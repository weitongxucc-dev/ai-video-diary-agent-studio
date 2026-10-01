from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.canvas import canvas_size
from app.creation_execution import (
    CpuRenderLimits, CreationExecution, CreationExecutionActor, CreationJobOwnerVerifier,
    CreationJobUnitExecutor,
    revision_digest, submit_creation_stage,
)
from app.device_scheduler import DeviceScheduler, ProcessIdentity
from app.host_resource_probes import HostProcessProbe
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import (CreationPlan, CreationPlanVoiceReference, CreationTask,
                            MaterialAsset, MaterialSegment, NarrationArtifactRecord, ProductionRepository,
                            TimelineVersion)
from app.artifact_store import ArtifactStore
from app.config import Settings
from app.video_jobs import VideoJobRunner


def _seed(tmp_path: Path, *, workspace_id: str = "workspace_a", confirmed: bool = True, voice: bool = True):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(database_path=tmp_path / "isolated.sqlite3", video_library_path=library)
    task_id = f"task_{workspace_id}"
    plan = CreationPlan(
        id=f"plan_{workspace_id}", workspace_id=workspace_id,
        goal="一条测试视频", name="测试", target_count=1, language="zh-CN",
        aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[],
        selected_voice=CreationPlanVoiceReference(
            profile_id="voice_1", version="v1", person_profile_id="person_1", person_version="v1",
            person_authorization_id="person_auth_1", person_authorization_version="v1",
            authorization_id="voice_auth_1", authorization_version="v1", language="zh-CN", model_id="test",
        ) if voice else None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[],
        blockers=[], preflight_fingerprint="test",
    )
    task = CreationTask(id=task_id, workspace_id=workspace_id, plan_id=plan.id,
                        name="测试", scenario="knowledge_explanation")
    repo.confirm_creation_plan(plan, task)
    now = datetime.now(timezone.utc).isoformat()
    script_id = f"script_{workspace_id}"
    payload = {
        "id": script_id, "workspace_id": workspace_id, "task_id": task_id,
        "run_id": f"run_{workspace_id}", "topic_id": f"topic_{workspace_id}",
        "title": "已确认文案", "language": "zh-CN",
        "sections": [{"id": "section_1", "title": "正文", "content": "真实待制作文案",
                      "visual_prompt": "现场", "evidence_reference_ids": ["source_1"]}],
        "selection_status": "confirmed" if confirmed else "draft",
        "created_at": now, "updated_at": now,
        "confirmed_at": now if confirmed else None,
    }
    with sqlite3.connect(repo.database_path) as conn:
        conn.execute(
            "INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
            (script_id, workspace_id, task_id, payload["run_id"], json.dumps(payload), now),
        )
        if confirmed:
            task.confirmed_script_id = script_id
            task.stage = "narration"
            conn.execute(
                "UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
                (json.dumps(asdict(task), default=str), now, workspace_id, task_id),
            )
    script = repo.get_creation_task_script(workspace_id, script_id)
    persisted_plan = repo.get_creation_plan(workspace_id, plan.id)
    return repo, task_id, script_id, revision_digest(script, persisted_plan)


def _execution(repo, actions=None, render_ready=lambda _w, _t: True):
    actions = actions or {}
    return CreationExecution(
        repo,
        run_narration=actions.get("narration", lambda _w, _t: None),
        run_montage_plan=actions.get("montage_plan", lambda _w, _t: None),
        run_render=actions.get("render", lambda _w, _t: None),
        render_is_valid=render_ready,
    )


def test_scheduled_creation_stage_is_deduplicated_and_waits_for_safe_executor(tmp_path):
    repo, task_id, script_id, digest = _seed(tmp_path)
    execution = _execution(repo)
    actor = CreationExecutionActor("workspace_a", "member-a")
    job = execution.start(actor.workspace_id, task_id, script_id, digest, member_id=actor.member_id)
    owner = CreationJobOwnerVerifier(repo)
    probe = SimpleNamespace(admit=lambda _request: True, continue_unit=lambda _grant: True,
                            released=lambda _grant: True)
    processes = SimpleNamespace(current=lambda: ProcessIdentity(1234, "test", "boot"),
                                children_released=lambda _children: True,
                                recovery_state=lambda _holder, _children: "released")
    scheduler = DeviceScheduler(repo.database_path, tmp_path / "device.lock",
                                owners=owner, resources=probe, processes=processes)
    first = submit_creation_stage(execution, scheduler, job, actor)
    second = submit_creation_stage(execution, scheduler, job, actor)
    assert first == second
    assert scheduler.pick_and_grant() is None
    assert scheduler.view_for(first, actor)["wait_reason"] == "dependency"
    with pytest.raises(PermissionError, match="身份"):
        submit_creation_stage(execution, scheduler, job, CreationExecutionActor("workspace_b", "member-a"))
    with pytest.raises(PermissionError, match="身份"):
        submit_creation_stage(execution, scheduler, job, CreationExecutionActor("workspace_a", ""))
    with pytest.raises(PermissionError, match="不可提交"):
        submit_creation_stage(execution, scheduler, job, CreationExecutionActor("workspace_a", "member-b"))
    with pytest.raises(PermissionError, match="创建者"):
        execution.start("workspace_a", task_id, script_id, digest, member_id="member-b")
    scheduler.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg tools unavailable")
@pytest.mark.parametrize("source_change", [False, True])
def test_existing_voice_and_pure_material_montage_submit_bounded_render(tmp_path, monkeypatch, source_change):
    repo, task_id, script_id, digest = _seed(tmp_path)
    audio = tmp_path / "voice.wav"
    video = tmp_path / "library" / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(audio)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x240:r=25:d=1", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True)
    repo._save_asset(MaterialAsset(
        id="asset_1", workspace_id="workspace_a", file_name=video.name, path=str(video),
        size_bytes=video.stat().st_size, modified_at=datetime.now(timezone.utc),
        duration_seconds=1, usage_role="b_roll", review_status="approved", analysis_status="ready",
        segments=[MaterialSegment(id="segment_1", asset_id="asset_1", start_seconds=0,
                                  end_seconds=1, description="蓝色画面", review_status="approved")],
    ))
    repo.save_narration_artifact(NarrationArtifactRecord(
        id="narration_cache_1", workspace_id="workspace_a", task_id=task_id,
        script_id=script_id, voice_profile_id="voice_1", cache_key="cache_1",
        audio_path=str(audio), duration_seconds=1, sample_rate=44100, model_id="test",
    ))
    task = repo.get_creation_task("workspace_a", task_id)
    task.narration_cache_key = "cache_1"
    task.montage_plan_id = "montage_1"
    task.stage = "montage"
    task.stage_status = "waiting_for_user"
    montage = CreationTaskMontagePlan(
        id=task.montage_plan_id, workspace_id=task.workspace_id, task_id=task_id,
        script_id=script_id, script_title="已确认文案", status="ready",
        cues=[NarrationCue("cue_1", "section_1", "真实待制作文案", 1.0)],
        clips=[MontageClip("clip_1", "cue_1", "asset_1", "segment_1", "workspace://source.mp4",
                           0, 1, 0, 1, 1, ["已确认镜头"], False)],
        duration_seconds=1, low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/cache_1",
    )
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), task.workspace_id, task_id))
        conn.execute("INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) "
                     "VALUES (?,?,?,?,?)",
                     (montage.id, task.workspace_id, task_id,
                      json.dumps(creation_task_montage_plan_to_payload(montage)), datetime.now(timezone.utc).isoformat()))
    execution = _execution(repo)
    actor = CreationExecutionActor(task.workspace_id, "member-a")
    job = execution.start(task.workspace_id, task_id, script_id, digest, member_id=actor.member_id)
    owner = CreationJobOwnerVerifier(repo, cpu_render_limits=CpuRenderLimits(8_000_000, 1, 90))
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=owner,
        resources=SimpleNamespace(admit=lambda _request: True, continue_unit=lambda _grant: True,
                                  released=lambda _grant: True),
        processes=HostProcessProbe(),
    )
    try:
        portrait_payload = creation_task_montage_plan_to_payload(montage)
        portrait_payload["clips"][0]["visual_effects"] = {"beauty_strength": 0.3}
        with repo._connect() as conn:
            conn.execute("UPDATE creation_task_montage_plans SET payload=? WHERE workspace_id=? AND task_id=?",
                         (json.dumps(portrait_payload), task.workspace_id, task_id))
        with pytest.raises(ValueError, match="人像效果"):
            submit_creation_stage(execution, scheduler, job, actor)
        with repo._connect() as conn:
            assert conn.execute("SELECT count(*) FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == 0
            conn.execute("UPDATE creation_task_montage_plans SET payload=? WHERE workspace_id=? AND task_id=?",
                         (json.dumps(creation_task_montage_plan_to_payload(montage)), task.workspace_id, task_id))
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        with repo._connect() as conn:
            row = conn.execute("SELECT unit_key,resources_json FROM device_work_requests WHERE id=?",
                               (request_id,)).fetchone()
        resources = json.loads(row["resources_json"])
        assert row["unit_key"].startswith("render:")
        assert resources["kind"] == "creation_render"
        assert resources["duration_seconds"] == 1
        assert resources["clip_count"] == 1
        assert resources["threads"] == 1
        assert resources["timeout_seconds"] == 90
        assert resources["overlay_count"] == 0
        assert resources["max_output_bytes"] == 8_000_000
        assert (resources["width_px"], resources["height_px"]) == canvas_size("9:16")
        assert resources["input_bytes"] > 0
        assert submit_creation_stage(execution, scheduler, job, actor) == request_id
        grant = scheduler.pick_and_grant()
        assert grant is not None
        original_claim = repo.claim_job_run

        def cancel_before_claim(workspace_id, job_id):
            execution.cancel(workspace_id, task_id, member_id=actor.member_id)
            return original_claim(workspace_id, job_id)

        monkeypatch.setattr(repo, "claim_job_run", cancel_before_claim)
        CreationJobUnitExecutor(
            repo, render_action=lambda *_args: pytest.fail("cancelled job must not render"),
            render_is_valid=lambda *_args: pytest.fail("cancelled job must not validate output"),
        ).execute(grant, scheduler)
        monkeypatch.setattr(repo, "claim_job_run", original_claim)
        scheduler.assert_released(grant)
        assert repo.get_job_run(task.workspace_id, job.id).status == "cancelled"
        with repo._connect() as conn:
            assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (request_id,)).fetchone()[0] == "cancelled"

        failed_attempt = execution.retry(task.workspace_id, task_id, member_id=actor.member_id)
        failure_request_id = submit_creation_stage(execution, scheduler, failed_attempt, actor)
        failure_grant = scheduler.pick_and_grant()
        assert failure_grant is not None and failure_grant.request_id == failure_request_id
        CreationJobUnitExecutor(
            repo, render_action=lambda *_args: (_ for _ in ()).throw(ValueError("synthetic render failure")),
            render_is_valid=lambda *_args: pytest.fail("failed render cannot validate output"),
        ).execute(failure_grant, scheduler)
        scheduler.assert_released(failure_grant)
        assert repo.get_job_run(task.workspace_id, failed_attempt.id).status == "failed"
        with repo._connect() as conn:
            assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (failure_request_id,)).fetchone()[0] == "failed"
        resumed = execution.retry(task.workspace_id, task_id, member_id=actor.member_id)
        retry_request_id = submit_creation_stage(execution, scheduler, resumed, actor)
        retry_grant = scheduler.pick_and_grant()
        assert retry_grant is not None and retry_grant.request_id == retry_request_id

        output_store = ArtifactStore(tmp_path / "artifacts")
        monkeypatch.setattr(main_module, "production_repo", repo)
        monkeypatch.setattr(main_module, "artifact_store", output_store)
        monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(repo))
        monkeypatch.setattr(main_module, "settings", SimpleNamespace(
            video_output_path=str(tmp_path / "outputs"), video_inbox_path=str(tmp_path / "library"),
            effective_temporary_root_path=tmp_path / "temp", native_portrait_worker_path=None))
        render_calls = []
        original_render = main_module._render_montage_with_resolved_clip_sources

        def counted_render(*args, **kwargs):
            render_calls.append(1)
            return original_render(*args, **kwargs)

        monkeypatch.setattr(main_module, "_render_montage_with_resolved_clip_sources", counted_render)
        retry_executor = CreationJobUnitExecutor(
            repo,
            render_action=lambda workspace_id, current_task_id, context: (
                main_module._render_creation_task_montage_for_workspace(
                    workspace_id, current_task_id, render_context=context)),
            render_is_valid=lambda workspace_id, current_task_id, context: (
                main_module._creation_render_is_valid(
                    workspace_id, current_task_id, render_context=context)),
        )
        real_checkpoint = retry_executor._checkpoint
        crash_once = {"pending": True}

        def crash_after_inner(conn, current_grant, *, status, output=None, reason=None):
            if status == "succeeded" and crash_once["pending"]:
                crash_once["pending"] = False
                raise RuntimeError("simulated crash after inner publication")
            return real_checkpoint(conn, current_grant, status=status, output=output, reason=reason)

        retry_executor._checkpoint = crash_after_inner
        with pytest.raises(RuntimeError, match="simulated crash"):
            retry_executor.execute(retry_grant, scheduler)
        assert render_calls == [1]
        published_task = repo.get_creation_task(task.workspace_id, task_id)
        published_version = repo.get_timeline_version(task.workspace_id, published_task.current_timeline_version_id)
        inner_jobs = [item for item in repo.list_job_runs(task.workspace_id, task_id)
                      if item.kind == "creation_task_montage_render"]
        assert len(inner_jobs) == 1 and inner_jobs[0].status == "succeeded"

        # A same-length write with restored mtime is still a new local source
        # identity. Do not resume the old grant after this branch changes it.
        if source_change:
            source_stat = video.stat()
            source_bytes = video.read_bytes()
            time.sleep(0.01)
            changed = bytearray(source_bytes)
            changed[-1] ^= 1
            video.write_bytes(changed)
            os.utime(video, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            changed_stat = video.stat()
            assert changed_stat.st_size == source_stat.st_size
            assert changed_stat.st_mtime_ns == source_stat.st_mtime_ns
            assert changed_stat.st_ino == source_stat.st_ino
            assert changed_stat.st_ctime_ns != source_stat.st_ctime_ns
            with pytest.raises(PermissionError, match="失效"):
                retry_executor.execute(retry_grant, scheduler)
            assert render_calls == [1]
            scheduler.defer_unit(retry_grant, reason="input_stale")
            scheduler.assert_released(retry_grant)
            return

        # An independently edited timeline version also invalidates the grant.
        repo.create_timeline_version(TimelineVersion(
            id="timeline_independent_edit", workspace_id=task.workspace_id, task_id=task_id,
            parent_version_id=published_version.id, sequence=published_version.sequence + 1,
            snapshot=published_version.snapshot, created_by_operation_id="operation_independent_edit",
        ))
        with pytest.raises(PermissionError, match="失效"):
            retry_executor.execute(retry_grant, scheduler)
        assert render_calls == [1]
        restored_task = repo.get_creation_task(task.workspace_id, task_id)
        restored_task.current_timeline_version_id = published_version.id
        with repo._connect() as conn:
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                         (json.dumps(asdict(restored_task), default=str), task.workspace_id, task_id))

        retry_executor._checkpoint = real_checkpoint
        retry_executor.execute(retry_grant, scheduler)
        assert render_calls == [1], "outer recovery must reuse its published inner render"
        scheduler.assert_released(retry_grant)
        finished = repo.get_job_run(task.workspace_id, resumed.id)
        assert finished.status == "succeeded", finished.failure_reason
        assert submit_creation_stage(execution, scheduler, repo.get_job_run(task.workspace_id, resumed.id), actor) is None
        with repo._connect() as conn:
            assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (retry_request_id,)).fetchone()[0] == "done"
            event = conn.execute("SELECT payload_json FROM task_events WHERE id=?",
                                 (f"event_{resumed.id}_render_succeeded",)).fetchone()
        output_identity = json.loads(event["payload_json"])["output"]
        assert output_identity.startswith("timeline_job_")
        assert output_identity.endswith("/preview.mp4")
        output_ref = repo.get_creation_task_montage_plan(task.workspace_id, task_id)["output_path"]
        assert output_store.resolve_file(output_ref).is_file()
    finally:
        scheduler.close()


def test_draft_and_wrong_workspace_cannot_start(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, confirmed=False)
    execution = _execution(repo)
    with pytest.raises(ValueError, match="确认当前文案"):
        execution.start("workspace_a", task_id, script_id, digest)
    assert not repo.list_job_runs("workspace_a", task_id)
    with pytest.raises(KeyError):
        execution.start("workspace_b", task_id, script_id, digest)


def test_missing_authorized_voice_blocks_start_before_job_creation(tmp_path):
    repo, task_id, script_id, digest = _seed(tmp_path, voice=False)
    with pytest.raises(ValueError, match="选择已授权声音"):
        _execution(repo).start("workspace_a", task_id, script_id, digest)
    assert repo.list_job_runs("workspace_a", task_id) == []


def test_duplicate_start_and_restart_execute_one_persisted_job(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    monkeypatch.setattr(repo, "get_creation_task_montage_plan", lambda *_: {})
    calls = []
    execution = _execution(repo, {
        "narration": lambda *_: calls.append("narration"),
        "montage_plan": lambda *_: calls.append("montage_plan"),
        "render": lambda *_: calls.append("render"),
    })
    first = execution.start("workspace_a", task_id, script_id, digest)
    second = execution.start("workspace_a", task_id, script_id, digest)
    assert first.id == second.id
    assert len(execution.jobs("workspace_a", task_id)) == 1
    restarted = _execution(repo, execution.actions)
    assert restarted.execute("workspace_a", first.id).status == "succeeded"
    assert restarted.execute("workspace_a", first.id).status == "succeeded"
    assert calls == ["narration", "montage_plan", "render"]


def test_failure_resume_reuses_valid_narration(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    audio = tmp_path / "narration.wav"
    attempts = []
    monkeypatch.setattr(repo, "get_creation_task_montage_plan", lambda *_: {})
    monkeypatch.setattr(repo, "get_narration_artifact", lambda *_: SimpleNamespace(
        task_id=task_id, script_id=script_id, audio_path=str(audio)))

    def narration(workspace_id, current_task_id):
        attempts.append("narration")
        audio.write_bytes(b"synthetic audio")
        task = repo.get_creation_task(workspace_id, current_task_id)
        task.narration_cache_key = "synthetic_cache"
        with sqlite3.connect(repo.database_path) as conn:
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                         (json.dumps(asdict(task), default=str), workspace_id, current_task_id))

    def montage(*_):
        attempts.append("montage_plan")
        if attempts.count("montage_plan") == 1:
            raise ValueError("素材暂不可用")

    execution = _execution(repo, {"narration": narration, "montage_plan": montage,
                                  "render": lambda *_: attempts.append("render")})
    first = execution.start("workspace_a", task_id, script_id, digest)
    assert execution.execute("workspace_a", first.id).status == "failed"
    assert "素材暂不可用" in execution.latest("workspace_a", task_id).failure_reason
    resumed = execution.retry("workspace_a", task_id)
    assert resumed.id != first.id
    assert execution.execute("workspace_a", resumed.id).status == "succeeded"
    assert attempts == ["narration", "montage_plan", "montage_plan", "render"]


def test_cancel_at_boundary_and_revision_change_blocks_resume(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    monkeypatch.setattr(repo, "get_creation_task_montage_plan", lambda *_: {})
    calls = []
    execution = _execution(repo, {"narration": lambda *_: calls.append("narration")})
    first = execution.start("workspace_a", task_id, script_id, digest)
    assert execution.cancel("workspace_a", task_id).status == "cancelled"
    assert execution.execute("workspace_a", first.id).status == "cancelled"
    assert calls == []
    with sqlite3.connect(repo.database_path) as conn:
        row = conn.execute("SELECT payload FROM creation_task_scripts WHERE id=?", (script_id,)).fetchone()
        payload = json.loads(row[0])
        payload["sections"][0]["content"] = "修改后的文案"
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id=?", (json.dumps(payload), script_id))
    with pytest.raises(ValueError, match="变化"):
        execution.retry("workspace_a", task_id)


def test_http_start_requires_explicit_confirmation_and_is_idempotent(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    scheduled = []
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", _execution(repo))
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(
        submit=lambda *args: scheduled.append(args)))
    client = TestClient(main_module.app)
    base = f"/api/creation-tasks/{task_id}/execution"
    preview = client.get(base + "/preview")
    assert preview.status_code == 200
    assert preview.json()["input_digest"] == digest
    rejected = client.post(base + "/start", json={
        "script_id": script_id, "input_digest": digest, "requirements_confirmed": False,
    })
    assert rejected.status_code == 409
    assert not repo.list_job_runs("local", task_id)
    payload = {"script_id": script_id, "input_digest": digest,
               "requirements_confirmed": True}
    first = client.post(base + "/start", json=payload)
    second = client.post(base + "/start", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert first.json()["current_stage"] is None
    assert first.json()["waiting_reason"] is None
    assert first.json()["status_updated_at"] is not None
    assert len(repo.list_job_runs("local", task_id)) == 1
    assert len(scheduled) == 2  # atomic JobRun claim ensures only one worker executes


def test_scheduled_http_start_uses_one_persisted_device_request_and_reports_wait(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    execution = _execution(repo)
    owner = CreationJobOwnerVerifier(repo)
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=owner,
        resources=SimpleNamespace(admit=lambda _request: True, continue_unit=lambda _grant: True,
                                  released=lambda _grant: True),
        processes=SimpleNamespace(current=lambda: ProcessIdentity(1234, "test", "boot"),
                                  children_released=lambda _children: True,
                                  recovery_state=lambda _holder, _children: "released"),
    )
    wake_calls = []
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", execution)
    monkeypatch.setattr(main_module, "creation_execution_mode", "scheduled")
    monkeypatch.setattr(main_module, "device_runtime", SimpleNamespace(
        scheduler=scheduler, registrations={"creation_job": owner}, last_error=None,
        wake=lambda: wake_calls.append(True),
    ))
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(
        submit=lambda *_args: pytest.fail("scheduled request must not enter legacy worker"),
    ))
    client = TestClient(main_module.app)
    route = f"/api/creation-tasks/{task_id}/execution"
    payload = {"script_id": script_id, "input_digest": digest, "requirements_confirmed": True}
    first = client.post(route + "/start", json=payload)
    second = client.post(route + "/start", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert first.json()["current_stage"] == "narration"
    assert first.json()["waiting_reason"] is None
    assert first.json()["status_updated_at"] is not None
    with repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == 1
    assert len(wake_calls) == 2
    main_module._recover_creation_executions()
    with repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == 1
    assert scheduler.pick_and_grant() is None
    waiting = client.get(route)
    assert waiting.status_code == 200
    assert waiting.json()["waiting_reason"] == "等待前一步或受许可执行能力就绪。"
    assert waiting.json()["current_stage"] == "narration"
    # Reproduce a crash between durable JobRun cancellation and device-request
    # cancellation. Recovery must finish the latter without resubmitting work.
    assert execution.cancel("local", task_id, member_id="local-owner").status == "cancelled"
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == "blocked"
    main_module._recover_creation_executions()
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == "cancelled"
    stopped = client.post(route + "/cancel")
    assert stopped.status_code == 200
    assert stopped.json()["waiting_reason"] is None
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == "cancelled"
    resumed = client.post(route + "/resume")
    assert resumed.status_code == 202
    assert resumed.json()["job"]["id"] != first.json()["job"]["id"]
    assert resumed.json()["job"]["status"] == "queued"
    with repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM device_work_requests WHERE owner_type='creation_job'").fetchone()[0] == 2
    scheduler.close()


def test_failed_new_narration_never_reuses_leftover_old_audio(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    old_audio = tmp_path / "old.wav"
    old_audio.write_bytes(b"old voice")
    task = repo.get_creation_task("workspace_a", task_id)
    task.narration_cache_key = "old_voice_cache"
    with sqlite3.connect(repo.database_path) as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), "workspace_a", task_id))
    monkeypatch.setattr(repo, "get_narration_artifact", lambda *_: SimpleNamespace(
        task_id=task_id, script_id=script_id, audio_path=str(old_audio)))
    attempts = []

    def failing_narration(*_):
        attempts.append("called")
        raise ValueError("新声音暂不可用")

    execution = _execution(repo, {"narration": failing_narration})
    first = execution.start("workspace_a", task_id, script_id, digest)
    assert execution.execute("workspace_a", first.id).status == "failed"
    resumed = execution.retry("workspace_a", task_id)
    assert execution.execute("workspace_a", resumed.id).status == "failed"
    assert attempts == ["called", "called"]


def test_video_ready_requires_current_version_registered_hash_and_probe(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "artifacts")
    published = store.publish_bytes("task/preview.mp4", b"invalid media")
    task = SimpleNamespace(current_timeline_version_id="version_1", confirmed_script_id="script_1")
    plan = {"status": "rendered", "script_id": "script_1", "output_path": published.logical_ref,
            "aspect_ratio": "9:16", "duration_seconds": 1.0}
    record = SimpleNamespace(logical_ref=published.logical_ref, kind="rendered_video",
                             producer_job_id="render_1", sha256=published.sha256,
                             size_bytes=published.size_bytes)
    current_version = SimpleNamespace(task_id="task_1", snapshot={"output_refs": [published.logical_ref]})
    repository = SimpleNamespace(
        get_creation_task=lambda *_: task,
        get_creation_task_montage_plan=lambda *_: plan,
        get_timeline_version=lambda *_: current_version,
        list_artifact_records=lambda *_: [record],
        get_job_run=lambda *_: SimpleNamespace(status="succeeded"),
    )
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "artifact_store", store)
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")
    monkeypatch.setattr(main_module, "_validate_rendered_media", lambda *_args, **_kwargs: None)
    assert main_module._creation_render_is_valid("workspace_a", "task_1")
    current_version.snapshot["output_refs"] = []
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")
    current_version.snapshot["output_refs"] = [published.logical_ref]
    store.resolve_file(published.logical_ref).write_bytes(b"corrupt")
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")


def test_concurrent_duplicate_start_returns_one_job(tmp_path):
    repo, task_id, script_id, digest = _seed(tmp_path)
    execution = _execution(repo)
    barrier = Barrier(12)

    def start_once(_index):
        barrier.wait()
        return execution.start("workspace_a", task_id, script_id, digest).id

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(start_once, range(12)))
    assert len(set(ids)) == 1
    assert len(execution.jobs("workspace_a", task_id)) == 1


def test_lifespan_recovers_pending_job_and_binds_only_current_workspace(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    execution = _execution(repo)
    queued = execution.start("local", task_id, script_id, digest)
    submitted = []
    worker = SimpleNamespace(
        submit=lambda *args: submitted.append(args), shutdown=lambda **_kwargs: None,
    )
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", execution)
    monkeypatch.setattr(main_module, "execution_worker", worker)
    monkeypatch.setattr(main_module, "material_ingestion", None)
    with TestClient(main_module.app) as client:
        assert submitted and submitted[0][1:] == ("local", queued.id)
        before = client.get("/api/production/materials/intake").json()
        assert before["bound_to_current_workspace"] is False
        assert before["binding_available"] is True
        assert before["directory_state"] == "ready"
        assert before["configured_directory"] == str((tmp_path / "library").resolve())
        assert before["bound_directory"] is None
        assert client.post("/api/production/materials/intake/bind", json={
            "expected_directory": str(tmp_path / "old-directory"),
        }).status_code == 409
        bound = client.post("/api/production/materials/intake/bind", json={
            "expected_directory": before["configured_directory"],
        })
        assert bound.status_code == 200
        assert bound.json()["bound_to_current_workspace"] is True
        assert bound.json()["bound_directory"] == before["configured_directory"]
        assert bound.json()["file_counts"] == {
            "observing": 0, "running": 0, "ready": 0, "failed": 0, "blocked_disk": 0,
        }
        foreign = main_module._material_binding_status("other_workspace", repo)
        assert foreign.configured_directory is None and foreign.bound_directory is None
        assert foreign.directory_state == "unknown"
        newer_directory = tmp_path / "new-library"
        newer_directory.mkdir()
        repo.video_library_path = newer_directory
        changed = client.get("/api/production/materials/intake").json()
        assert changed["directory_state"] == "changed"
        assert changed["configured_directory"] == str(newer_directory.resolve())
        assert changed["bound_directory"] == before["configured_directory"]
        assert client.post("/api/production/materials/intake/bind", json={
            "expected_directory": changed["configured_directory"],
        }).status_code == 409


def test_read_only_material_directory_refuses_binding_and_recovers(tmp_path):
    # A read-only volume still lists as a normal directory, so the intake would
    # otherwise accept a binding it can never fulfil and fail only at import time.
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir()
    repo = ProductionRepository(database_path=tmp_path / "isolated.sqlite3", video_library_path=library)
    assert main_module._material_binding_status("local", repo).directory_state == "ready"

    library.chmod(0o500)
    try:
        refused = main_module._material_binding_status("local", repo)
        assert refused.directory_state == "read_only"
        assert "不可写" in refused.directory_message
        assert refused.repair_action and "只读" in refused.repair_action
        with pytest.raises(ValueError, match="不可写"):
            repo.bind_material_ingestion("local", expected_directory=str(library))
    finally:
        library.chmod(0o755)

    recovered = main_module._material_binding_status("local", repo)
    assert recovered.directory_state == "ready"
    assert recovered.repair_action is None
    repo.bind_material_ingestion("local", expected_directory=str(library))
    assert main_module._material_binding_status("local", repo).bound_to_current_workspace is True

    library.chmod(0o500)
    try:
        assert main_module._material_binding_status("local", repo).directory_state == "read_only"
    finally:
        library.chmod(0o755)


def test_reload_startup_shutdown_do_not_leave_ingestion_or_execution_threads(tmp_path, monkeypatch):
    # Keep the full runtime reload inside a synthetic library and restore its
    # module globals afterwards; no provider or media operation is invoked.
    for name in (
        "settings repo source_repository model_catalog model_installer visual_semantic_provider "
        "voice_clone_provider avatar_lipsync_provider latentsync_provider production_repo "
        "montage_repo montage_planner montage_renderer artifact_store narration_generator "
        "voice_preview_service avatar_preview_service vault video_inbox video_processor "
        "video_pipeline draft_workspace source_tracker service workflow auth_context "
        "video_job_runner creation_execution execution_worker material_ingestion"
    ).split():
        monkeypatch.setattr(main_module, name, getattr(main_module, name))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    (tmp_path / "library").mkdir()
    isolated = Settings(
        database_path=str(tmp_path / "isolated.sqlite3"),
        video_inbox_path=str(tmp_path / "library"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        obsidian_vault_path=str(tmp_path / "vault"),
        model_root_path=str(tmp_path / "models"),
        temporary_root_path=str(tmp_path / "temporary"),
        hermes_radar_path=str(tmp_path / "radar"),
    )
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda _cls: isolated))
    monkeypatch.setattr(main_module, "execution_worker", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(main_module, "material_ingestion", None)

    def assert_running():
        service = main_module.material_ingestion
        worker = main_module.execution_worker
        assert service is not None and service._thread is not None and service._thread.is_alive()
        assert worker.submit(lambda: "ready").result(timeout=2) == "ready"
        return service, service._thread, worker

    def assert_stopped(service, thread, worker):
        assert service._thread is None and not thread.is_alive()
        with pytest.raises(RuntimeError, match="shutdown"):
            worker.submit(lambda: None)

    main_module._reload_runtime_dependencies()
    first_service, first_thread, first_worker = assert_running()
    with TestClient(main_module.app):
        assert main_module.material_ingestion is first_service
        assert main_module.execution_worker is first_worker
    assert_stopped(first_service, first_thread, first_worker)

    with TestClient(main_module.app):
        second_service, second_thread, second_worker = assert_running()
        main_module._reload_runtime_dependencies()
        assert_stopped(second_service, second_thread, second_worker)
        third_service, third_thread, third_worker = assert_running()
    assert_stopped(third_service, third_thread, third_worker)

    main_module.resume_persisted_creation_executions()
    fourth_service, fourth_thread, fourth_worker = assert_running()
    main_module.resume_persisted_creation_executions()
    assert main_module.material_ingestion is fourth_service
    assert main_module.execution_worker is fourth_worker
    main_module.stop_creation_execution_worker()
    main_module.stop_creation_execution_worker()
    assert_stopped(fourth_service, fourth_thread, fourth_worker)
    main_module.resume_persisted_creation_executions()
    fifth_service, fifth_thread, fifth_worker = assert_running()
    assert fifth_service is not fourth_service and fifth_worker is not fourth_worker
    main_module.stop_creation_execution_worker()
    assert_stopped(fifth_service, fifth_thread, fifth_worker)


def test_material_asset_tag_routes_are_atomic_and_scoped(tmp_path, monkeypatch):
    repo, _, _, _ = _seed(tmp_path, workspace_id="local")
    library = tmp_path / "library"
    (library / "one.mp4").write_bytes(b"synthetic")
    (library / "two.mp4").write_bytes(b"synthetic two")
    assets = repo.scan_library("local")
    monkeypatch.setattr(main_module, "production_repo", repo)
    client = TestClient(main_module.app)
    first = client.put(f"/api/production/materials/{assets[0].id}/tags", json={"user_tags": [" 访谈 ", "访谈"]})
    assert first.status_code == 200
    assert first.json()["user_tags"] == ["访谈"]
    batch = client.post("/api/production/materials/tags/batch", json={
        "asset_ids": [asset.id for asset in assets], "add": ["产品"], "remove": ["访谈"],
    })
    assert batch.status_code == 200
    assert [item["user_tags"] for item in batch.json()["assets"]] == [["产品"], ["产品"]]
    rejected = client.post("/api/production/materials/tags/batch", json={
        "asset_ids": [assets[0].id, "foreign"], "add": ["不可部分提交"], "remove": [],
    })
    assert rejected.status_code == 404
    assert repo.get_asset("local", assets[0].id).user_tags == ["产品"]
