from __future__ import annotations

import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.creation_execution import CreationExecution, revision_digest
from app.production import CreationPlan, CreationPlanVoiceReference, CreationTask, ProductionRepository
from app.artifact_store import ArtifactStore
from app.config import Settings


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
    assert len(repo.list_job_runs("local", task_id)) == 1
    assert len(scheduled) == 2  # atomic JobRun claim ensures only one worker executes


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
            "aspect_ratio": "9:16"}
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
        assert client.get("/api/production/materials/intake").json()["bound_to_current_workspace"] is False
        bound = client.post("/api/production/materials/intake/bind")
        assert bound.status_code == 200
        assert bound.json() == {"bound_to_current_workspace": True, "available": True, "scan_interval_seconds": 60}
        assert "library" not in bound.text


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
