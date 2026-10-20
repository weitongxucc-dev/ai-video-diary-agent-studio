from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier, Thread

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
from app.host_resource_probes import HostCapacity, HostProcessProbe
from app.local_members import LocalMemberService
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import (CreationPlan, CreationPlanVoiceReference, CreationTask,
                            MaterialAsset, MaterialSegment, NarrationArtifactRecord, ProductionRepository,
                            CreationPlanPersonReference, TimelineVersion)
from app.artifact_store import ArtifactStore
from app.config import Settings
from app.narration import NarrationGenerator
from app.voice_clone import Qwen3TTSSubprocessProvider
from app.host_resource_probes import HostBudget, HostResourceProbe, CpuRenderBudget, NarrationBudget, MontageBudget
from app.video_jobs import VideoJobRunner
from _media_preview_test_fixtures import (
    register_synthetic_profiles, register_synthetic_video_asset, synthetic_wav_bytes,
    write_synthetic_video,
)


def _seed(tmp_path: Path, *, workspace_id: str = "workspace_a", confirmed: bool = True, voice: bool = True, task_suffix: str = ""):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(database_path=tmp_path / "isolated.sqlite3", video_library_path=library)
    task_id = f"task_{workspace_id}{task_suffix}"
    plan = CreationPlan(
        id=f"plan_{workspace_id}{task_suffix}", workspace_id=workspace_id,
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
    script_id = f"script_{workspace_id}{task_suffix}"
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


def test_creation_owner_uses_canonical_plan_visibility_for_legacy_payload(tmp_path):
    repo, task_id, script_id, _digest = _seed(tmp_path)
    task = repo.get_creation_task("workspace_a", task_id)
    with repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_plans WHERE id=?", (task.plan_id,)).fetchone()
        payload = json.loads(row["payload"])
        payload.pop("visibility")
        conn.execute("UPDATE creation_plans SET payload=?, visibility='shared' WHERE id=?",
                     (json.dumps(payload), task.plan_id))
    script = repo.get_creation_task_script("workspace_a", script_id)
    plan = repo.get_creation_plan("workspace_a", task.plan_id)
    actor = CreationExecutionActor("workspace_a", "member-a")
    job = _execution(repo).start("workspace_a", task_id, script_id, revision_digest(script, plan), actor=actor)
    owner = CreationJobOwnerVerifier(repo)
    with repo._connect() as conn:
        facts = owner._facts(conn, "workspace_a", job.id)
        assert facts[-1].visibility == plan.visibility == "shared"
        assert owner._check_facts(*facts, "narration", actor.member_id) == "ready"
        conn.execute("UPDATE creation_plans SET visibility='private' WHERE id=?", (task.plan_id,))
        changed = owner._facts(conn, "workspace_a", job.id)
        assert owner._check_facts(*changed, "narration", actor.member_id) == "stale"


def _execution(repo, actions=None, render_ready=lambda _w, _t: True):
    actions = actions or {}
    return CreationExecution(
        repo,
        run_narration=actions.get("narration", lambda _w, _t: None),
        run_montage_plan=actions.get("montage_plan", lambda _w, _t: None),
        run_render=actions.get("render", lambda _w, _t: None),
        render_is_valid=render_ready,
    )


def _render_version_fixture(tmp_path):
    """Persisted render inputs are real synthetic media with an owned profile."""
    from app.member_permits import MemberDeviceAccess

    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    members = LocalMemberService(repo.database_path)
    member = members.add_member("render version fixture")
    actor = CreationExecutionActor("local", member.member_id)
    person, voice, _avatar_authorization, _voice_path, _image_path = register_synthetic_profiles(
        repo, workspace_id="local", member_id=member.member_id,
        person_id="person_1", voice_id="voice_1", person_version="v1", voice_version="v1",
        language="zh", model_id="test",
    )
    plan = repo.get_creation_plan("local", "plan_local")
    plan.selected_person = CreationPlanPersonReference(person.id, person.version)
    plan.selected_voice = CreationPlanVoiceReference(
        voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version,
        voice.authorization.id, voice.authorization.version, "zh-CN", "test",
    )
    plan.owner_user_id = member.member_id
    task = repo.get_creation_task("local", task_id)
    task.owner_user_id = member.member_id
    with repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(plan), default=str), "local", plan.id))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(task), default=str), "local", task.id))
    audio, video = tmp_path / "library/audio.wav", tmp_path / "library/source.mp4"
    audio.write_bytes(synthetic_wav_bytes())
    write_synthetic_video(video, seconds=36)
    repo._save_asset(MaterialAsset(
        id="asset_1", workspace_id="local", file_name=video.name, path=str(video),
        size_bytes=video.stat().st_size,
        modified_at=datetime.fromtimestamp(video.stat().st_mtime, timezone.utc),
        duration_seconds=36, usage_role="b_roll", review_status="approved", analysis_status="ready",
        media_type="video", owner_user_id=member.member_id,
        segments=[MaterialSegment(id="segment_1", asset_id="asset_1", start_seconds=0,
                                  end_seconds=1, description="synthetic fixture", review_status="approved")],
    ))
    source_reference = repo.workspace_media_reference_for_asset("local", "asset_1")
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(
            conn, workspace_id="local", reference=source_reference
        )
    repo.save_narration_artifact(NarrationArtifactRecord(
        id="narration_cache_1", workspace_id="local", task_id=task_id,
        script_id=script_id, voice_profile_id="voice_1", cache_key="cache_1",
        audio_path=str(audio), duration_seconds=1, sample_rate=24000, model_id="fixture",
    ))
    task = repo.get_creation_task("local", task_id)
    task.narration_cache_key = "cache_1"
    task.stage = "montage"
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), "local", task_id))
    montage = CreationTaskMontagePlan(
        id="montage_1", workspace_id="local", task_id=task_id, script_id=script_id,
        script_title="confirmed fixture", status="ready",
        cues=[NarrationCue("cue_1", "section_1", "fixture", 1)],
        clips=[MontageClip("clip_1", "cue_1", "asset_1", "segment_1", "workspace://source.mp4",
                           0, 1, 0, 1, 1, ["confirmed fixture"], False)],
        duration_seconds=1, low_confidence_clip_count=0, narration_audio_path="narration://artifact/cache_1",
    )
    repo.save_creation_task_montage_plan("local", task_id, montage.id, creation_task_montage_plan_to_payload(montage))
    version = repo.create_timeline_version(TimelineVersion(
        "timeline_base", "local", task_id, None, 1,
        {"montage": repo.get_creation_task_montage_plan("local", task_id)}, None))
    execution = _execution(repo, render_ready=lambda workspace, task: repo.get_creation_task(workspace, task).output_ready)
    owner = CreationJobOwnerVerifier(repo, cpu_render_limits=CpuRenderLimits(8_000_000, 1, 60),
                                    verify_identity=MemberDeviceAccess(members).verify_identity)
    scheduler = DeviceScheduler(repo.database_path, tmp_path / "device.lock", owners=owner,
        resources=SimpleNamespace(admit=lambda _request: True, continue_unit=lambda _grant: True,
                                  released=lambda _grant: True), processes=HostProcessProbe())
    persisted_script = repo.get_creation_task_script("local", script_id)
    persisted_plan = repo.get_creation_plan("local", plan.id)
    return (repo, task_id, script_id, revision_digest(persisted_script, persisted_plan),
            actor, execution, owner, scheduler, version, members)


def test_cpu_render_counts_repeated_source_once_and_preserves_all_clips(tmp_path, monkeypatch):
    repo, task_id, _, _, _, _, owner, scheduler, base, _ = _render_version_fixture(tmp_path)
    try:
        montage = repo.get_creation_task_montage_plan("local", task_id)
        clip = montage["clips"][0]
        montage["clips"] = [
            {**clip, "id": f"clip_{index}", "timeline_start_seconds": index,
             "timeline_end_seconds": index + 1}
            for index in range(21)
        ]
        montage["duration_seconds"] = 21
        repo.save_creation_task_montage_plan("local", task_id, "montage_1", montage)
        repo.create_timeline_version(replace(
            base, id="timeline_repeated_source", parent_version_id=base.id, sequence=2,
            snapshot={"montage": montage}))
        task = repo.get_creation_task("local", task_id)
        plan = repo.get_creation_plan("local", task.plan_id)
        manifests = []
        original_dumps = json.dumps

        def capture_manifest(value, *args, **kwargs):
            if isinstance(value, dict) and "sources" in value and "plan" in value:
                manifests.append(value)
            return original_dumps(value, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(json, "dumps", capture_manifest)
            with repo._connect() as conn:
                unit_key, resources = owner.render_facts(conn, task, plan)
        manifest, = manifests
        audio = tmp_path / "library/audio.wav"
        video = tmp_path / "library/source.mp4"
        assert [fact["path"] for fact in manifest["sources"]] == [str(audio), str(video)]
        assert resources["input_bytes"] == audio.stat().st_size + video.stat().st_size
        assert resources["clip_count"] == len(manifest["plan"]["clips"]) == 21
        assert manifest["plan"]["clips"] == montage["clips"]
        legacy_manifest = {**manifest, "sources": [manifest["sources"][0]] + [manifest["sources"][1]] * 21}
        legacy_digest = hashlib.sha256(original_dumps(
            legacy_manifest, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        assert unit_key != f"render:{legacy_digest}"
    finally:
        scheduler.close()


@pytest.mark.parametrize("mutation", ["version", "source", "revoked"])
def test_confirmed_crop_requeues_only_render_and_invalidates_prior_grant(tmp_path, mutation):
    from app.production import EditIntent, EditProposal, EditOperation

    repo, task_id, script_id, digest, actor, execution, owner, scheduler, base, members = _render_version_fixture(tmp_path)
    try:
        job = execution.start("local", task_id, script_id, digest, actor=actor)
        # Persisted completed-job fixture; no fabricated render/audio acceptance.
        repo.claim_job_run("local", job.id)
        repo.finish_job_run("local", job.id, status="succeeded")
        original = repo.get_creation_task_montage_plan("local", task_id)
        original.update(status="rendered", output_path="artifacts/fixture-old.mp4")
        repo.save_creation_task_montage_plan("local", task_id, "montage_1", original)
        assert execution.retry("local", task_id, member_id=actor.member_id).id == job.id
        edited = json.loads(json.dumps(original))
        edited.update(status="ready", output_path=None, subtitle_path=None)
        edited["clips"][0]["crop_x"] = 0
        intent = repo.create_edit_intent(EditIntent("intent_crop", "local", task_id, "crop", "review",
            ["clip_1"], None, "ChangeCrop", {}, {}, 1, "parsed"))
        proposal = repo.create_edit_proposal(EditProposal("proposal_crop", "local", task_id, intent.id,
            "crop", ["clip_1"], {"operation_type": "ChangeCrop"}, [], {}, []))
        version = TimelineVersion("timeline_crop", "local", task_id, base.id, 2, {"montage": edited}, "operation_crop")
        now = datetime.now(timezone.utc)
        repo.confirm_edit_proposal("local", task_id, proposal.id,
            operation=EditOperation("operation_crop", "local", task_id, proposal.id, "ChangeCrop",
                                    {"clip_id": "clip_1", "fit_mode": "fill", "crop_x": 0, "crop_y": 0.5},
                                    base.id, version.id, "applied", created_at=now, applied_at=now),
            result_version=version, montage_plan_id="montage_1", montage_payload=edited)
        resumed = execution.retry("local", task_id, member_id=actor.member_id)
        assert resumed.id != job.id and resumed.input_digest == job.input_digest
        assert execution.retry("local", task_id, member_id=actor.member_id).id == resumed.id
        request_id = submit_creation_stage(execution, scheduler, resumed, actor)
        assert submit_creation_stage(execution, scheduler, resumed, actor) == request_id
        grant = scheduler.pick_and_grant()
        assert grant is not None and grant.request_id == request_id and grant.unit_key.startswith("render:")
        scheduler.assert_grant(grant)
        task = repo.get_creation_task("local", task_id)
        assert task.narration_cache_key == "cache_1" and task.montage_plan_id == "montage_1"
        assert not task.output_ready and task.current_timeline_version_id == version.id
        assert repo.get_edit_proposal("local", proposal.id).status == "confirmed"
        if mutation == "version":
            repo.create_timeline_version(replace(version, id="timeline_concurrent", parent_version_id=version.id, sequence=3))
        elif mutation == "source":
            (tmp_path / "library/source.mp4").write_bytes(b"changed-source")
        else:
            members.remove_member(actor.member_id)
        with pytest.raises(PermissionError, match="失效"):
            scheduler.assert_grant(grant)
        scheduler.defer_unit(grant, reason="authorization_lost" if mutation == "revoked" else "input_stale")
        scheduler.assert_released(grant)
        with repo._connect() as conn:
            requests = conn.execute("SELECT unit_key FROM device_work_requests WHERE owner_id=?", (resumed.id,)).fetchall()
            assert len(requests) == 1 and requests[0]["unit_key"].startswith("render:")
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    finally:
        scheduler.close()


def test_render_version_plan_publication_rolls_back_and_preserves_concurrent_crop(tmp_path):
    repo, task_id, _, _, _, _, _, scheduler, base, _ = _render_version_fixture(tmp_path)
    try:
        original = repo.get_creation_task_montage_plan("local", task_id)
        old_output = {**original, "status": "rendered", "output_path": "artifacts/old-render.mp4"}
        # A version conflict after the plan write must roll back both changes.
        conflict = replace(base, id="timeline_output", sequence=3, parent_version_id=base.id)
        with pytest.raises(ValueError, match="连续递增"):
            with repo._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                repo.save_creation_task_montage_plan("local", task_id, "montage_1", old_output,
                    connection=conn, expected_current_version_id=base.id)
                repo.create_timeline_version(conflict, connection=conn, expected_current_version_id=base.id)
        assert repo.get_creation_task_montage_plan("local", task_id) == original
        concurrent = replace(base, id="timeline_concurrent", sequence=2, parent_version_id=base.id)
        repo.create_timeline_version(concurrent)
        for status in ("rendered", "failed"):
            with pytest.raises(ValueError, match="不能覆盖"):
                repo.save_creation_task_montage_plan("local", task_id, "montage_1", {**old_output, "status": status},
                    expected_current_version_id=base.id)
        with pytest.raises(ValueError, match="不能发布"):
            repo.create_timeline_version(replace(conflict, parent_version_id=base.id), expected_current_version_id=base.id)
        assert repo.get_creation_task("local", task_id).current_timeline_version_id == concurrent.id
        assert repo.get_creation_task_montage_plan("local", task_id) == original
        # Successful compare-and-publish reuses the same transaction and current parent.
        output = replace(conflict, parent_version_id=concurrent.id)
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repo.save_creation_task_montage_plan("local", task_id, "montage_1", old_output,
                connection=conn, expected_current_version_id=concurrent.id)
            repo.create_timeline_version(output, connection=conn, expected_current_version_id=concurrent.id)
        assert repo.get_creation_task("local", task_id).current_timeline_version_id == output.id
        assert repo.get_creation_task_montage_plan("local", task_id)["output_path"] == old_output["output_path"]
    finally:
        scheduler.close()


def test_render_grant_accepts_own_publication_but_not_copied_version_or_subtitle_mutation(tmp_path):
    from app.production import ArtifactRecord, JobRun, TransformRun, SubtitleTrack, SubtitleCue

    repo, task_id, script_id, digest, actor, execution, _, scheduler, base, _ = _render_version_fixture(tmp_path)
    try:
        track = SubtitleTrack("input_track", "local", task_id, base.id, "zh-CN", None,
            {"enabled": True}, [SubtitleCue("cue", None, "original fixture", 0, 1)], "timeline", None,
            "artifacts/fixture/input.srt", "transform_fixture_subtitle", "valid")
        repo.create_subtitle_track(track)
        job = execution.start("local", task_id, script_id, digest, actor=actor)
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        scheduler.assert_grant(grant)
        # Actual persistent producer/transform lineage with explicitly synthetic
        # artifact records. This tests input fencing, not playable-media validity.
        inner = repo.create_job_run(JobRun("job_fixture_render", "local", task_id,
            "creation_task_montage_render", "fixture-inner", "fixture-inner-digest"))
        repo.claim_job_run("local", inner.id)
        refs = ["artifacts/fixture/video.mp4", "artifacts/fixture/subtitles.srt"]
        for index, ref in enumerate(refs):
            repo.create_artifact_record(ArtifactRecord(f"fixture_artifact_{index}", "local", task_id,
                "rendered_video" if index == 0 else "subtitle_track", ref, "0" * 64, 1, inner.id, []))
        repo.finish_job_run("local", inner.id, status="succeeded", output_refs=refs)
        transform = repo.create_transform_run(TransformRun(f"transform_{inner.id}", "local", task_id,
            inner.id, "montage_render", inner.input_digest, [], refs, "fixture", {}, "succeeded",
            datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc)))
        rendered = repo.get_creation_task_montage_plan("local", task_id)
        rendered.update(status="rendered", output_path=refs[0], subtitle_path=refs[1])
        published = TimelineVersion(f"timeline_{inner.id}", "local", task_id, base.id, 2,
            {"montage": rendered, "output_refs": refs, "transform_run_id": transform.id,
             "creation_render_request_id": request_id, "render_input_digest": inner.input_digest,
             "render_input_timeline_version_id": base.id}, None)
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repo.save_creation_task_montage_plan("local", task_id, "montage_1", rendered,
                connection=conn, expected_current_version_id=base.id)
            repo.create_timeline_version(published, connection=conn, expected_current_version_id=base.id)
        scheduler.assert_grant(grant)
        # New output tracks are not retroactively inputs to their own render.
        repo.create_subtitle_track(replace(track, id="output_track", timeline_version_id=published.id))
        scheduler.assert_grant(grant)
        copied = replace(published, id="timeline_independent", parent_version_id=published.id, sequence=3)
        repo.create_timeline_version(copied)
        with pytest.raises(PermissionError, match="失效"):
            scheduler.assert_grant(grant)
        with repo._connect() as conn:
            task = repo._require_creation_task(conn, "local", task_id)
            task.current_timeline_version_id = published.id
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                         (json.dumps(asdict(task), default=str), "local", task_id))
        scheduler.assert_grant(grant)
        # Even a same-version subtitle change cannot retain the old input permit.
        with repo._connect() as conn:
            row = conn.execute("SELECT payload_json FROM subtitle_tracks WHERE id=?", (track.id,)).fetchone()
            subtitle = json.loads(row[0])
            subtitle["cues"][0]["text"] = "changed fixture"
            conn.execute("UPDATE subtitle_tracks SET payload_json=? WHERE id=?", (json.dumps(subtitle), track.id))
        with pytest.raises(PermissionError, match="失效"):
            scheduler.assert_grant(grant)
        scheduler.defer_unit(grant, reason="input_stale")
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


def test_failed_inner_render_does_not_overwrite_concurrently_confirmed_crop(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.creation_execution import CpuRenderGrantContext
    from app.production import EditIntent, EditProposal, EditOperation

    repo, task_id, script_id, digest, actor, execution, _, scheduler, base, _ = _render_version_fixture(tmp_path)
    try:
        outer = execution.start("local", task_id, script_id, digest, actor=actor)
        submit_creation_stage(execution, scheduler, outer, actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        original = repo.get_creation_task_montage_plan("local", task_id)
        cropped = json.loads(json.dumps(original))
        cropped["clips"][0]["crop_x"] = 0
        proposal = repo.create_edit_proposal(EditProposal("concurrent_proposal", "local", task_id,
            repo.create_edit_intent(EditIntent("concurrent_intent", "local", task_id, "crop", "montage",
                ["clip_1"], None, "ChangeCrop", {}, {}, 1, "parsed")).id,
            "crop", ["clip_1"], {"operation_type": "ChangeCrop"}, [], {}, []))
        version = replace(base, id="timeline_concurrent_crop", parent_version_id=base.id,
                          sequence=2, snapshot={"montage": cropped}, created_by_operation_id="concurrent_operation")

        def fail_after_crop(inner, _render):
            repo.create_job_run(inner)
            repo.claim_job_run("local", inner.id)
            now = datetime.now(timezone.utc)
            repo.confirm_edit_proposal("local", task_id, proposal.id,
                operation=EditOperation("concurrent_operation", "local", task_id, proposal.id, "ChangeCrop",
                    {"clip_id": "clip_1", "crop_x": 0}, base.id, version.id, "applied", created_at=now, applied_at=now),
                result_version=version, montage_plan_id="montage_1", montage_payload=cropped)
            return repo.finish_job_run("local", inner.id, status="failed",
                failure_reason="synthetic executor failed after concurrent crop", impact="no video", repair_action="fixture")

        monkeypatch.setattr(main_module, "production_repo", repo)
        monkeypatch.setattr(main_module, "video_job_runner", SimpleNamespace(run=fail_after_crop))
        context = CpuRenderGrantContext(scheduler, grant, time.monotonic() + 60, 8_000_000, 1)
        with pytest.raises(HTTPException) as error:
            main_module._render_creation_task_montage_for_workspace("local", task_id, render_context=context)
        assert error.value.status_code == 500
        assert repo.get_creation_task("local", task_id).current_timeline_version_id == version.id
        current = repo.get_creation_task_montage_plan("local", task_id)
        assert current["status"] == "ready" and current["clips"][0]["crop_x"] == 0
        assert current["output_path"] is None
        assert not any(e.event_type == "render_succeeded" for e in repo.list_task_events("local", task_id))
        scheduler.defer_unit(grant, reason="input_stale")
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


@pytest.mark.parametrize("publication_state", ["normal", "crash", "missing_input_version", "legacy_defaults"])
def test_actual_main_render_publication_and_completion_preserve_own_grant(tmp_path, monkeypatch, publication_state):
    """Real main/repository/permit completion; explicit media/quality substitutes."""
    repo, task_id, script_id, digest, actor, execution, _, scheduler, base, _ = _render_version_fixture(tmp_path)
    if publication_state == "legacy_defaults":
        legacy = repo.get_creation_task_montage_plan("local", task_id)
        for key in ("aspect_ratio", "presenter_share_target", "graphic_overlays", "audio_timeline",
                    "narration_linkage_created_audio_timeline"):
            legacy.pop(key)
        for clip in legacy["clips"]:
            for key in ("person_render_id", "fit_mode", "crop_x", "crop_y", "visual_effects",
                        "transition_in_seconds", "fade_envelope_rate", "fade_envelope_offset_frames",
                        "fade_envelope_duration_frames"):
                clip.pop(key)
        for cue in legacy["cues"]:
            for key in ("narration_segment_id", "subtitle_emphasis", "subtitle_style", "subtitle_emphasis_ranges"):
                cue.pop(key)
        with repo._connect() as conn:
            conn.execute("UPDATE creation_task_montage_plans SET payload=? WHERE task_id=?",
                         (json.dumps(legacy), task_id))
        base = repo.create_timeline_version(replace(base, id="timeline_legacy", parent_version_id=base.id,
            sequence=2, snapshot=dict(base.snapshot, montage=legacy)))
    try:
        monkeypatch.setattr("app.creation_execution.sample_host_capacity", lambda: HostCapacity(
            cpu_count=8, cpu_threads_ceiling=7, available_memory_bytes=8 * 1024 ** 3, load_avg_1m=0.5))
        monkeypatch.setattr(main_module, "production_repo", repo)
        monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
        monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(repo))
        monkeypatch.setattr(main_module, "settings", SimpleNamespace(
            video_output_path=str(tmp_path / "output"), video_inbox_path=str(tmp_path / "library"),
            effective_temporary_root_path=tmp_path / "temp", native_portrait_worker_path=None))
        renders, media_checks, quality_requests = [], [], []

        def synthetic_media(_workspace, plan, _audio, *, job_staging_path, render_context, subtitle_tracks):
            render_context.check()
            renders.append(render_context.grant.request_id)
            # Neither these marker bytes nor the substituted validator are
            # presented as FFmpeg, playable video, Qwen or quality acceptance.
            (job_staging_path / "preview.mp4").write_bytes(b"synthetic-render-output")
            (job_staging_path / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nfixture\n")
            return replace(plan, status="rendered")

        def synthetic_validate(path, *, scheduler, grant, **kwargs):
            scheduler.assert_grant(grant)
            assert path.read_bytes() == b"synthetic-render-output"
            media_checks.append(grant.request_id)

        monkeypatch.setattr(main_module, "_render_montage_with_resolved_clip_sources", synthetic_media)
        monkeypatch.setattr(main_module, "_validate_rendered_media", synthetic_validate)
        monkeypatch.setattr(main_module, "_start_creation_task_quality_check", lambda w, t: quality_requests.append((w, t)))
        if publication_state == "missing_input_version":
            task = repo.get_creation_task("local", task_id)
            task.current_timeline_version_id = None
            with repo._connect() as conn:
                conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                    (json.dumps(asdict(task), default=str), "local", task_id))
        job = execution.start("local", task_id, script_id, digest, actor=actor)
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        executor = CreationJobUnitExecutor(repo,
            render_action=lambda w, t, context: main_module._render_creation_task_montage_for_workspace(w, t, render_context=context),
            render_is_valid=lambda w, t, context: main_module._creation_render_is_valid(w, t, render_context=context))
        if publication_state == "crash":
            checkpoint = executor._checkpoint

            def crash(conn, current, **kwargs):
                if kwargs["status"] == "succeeded":
                    raise RuntimeError("crash after inner publication")
                return checkpoint(conn, current, **kwargs)

            executor._checkpoint = crash
            with pytest.raises(RuntimeError, match="crash after inner publication"):
                executor.execute(grant, scheduler)
            assert repo.get_job_run("local", job.id).status == "running"
            scheduler.assert_grant(grant)
            executor._checkpoint = checkpoint
        executor.execute(grant, scheduler)
        scheduler.assert_released(grant)
        finished = repo.get_job_run("local", job.id)
        if publication_state == "missing_input_version":
            assert finished.status == "failed"
            assert "完整性与媒体校验" in finished.failure_reason
            assert renders == [request_id] and media_checks == []
            return
        assert finished.status == "succeeded", finished.failure_reason
        published_task = repo.get_creation_task("local", task_id)
        version = repo.get_timeline_version("local", published_task.current_timeline_version_id)
        assert version.parent_version_id == base.id
        assert version.snapshot["render_input_timeline_version_id"] == base.id
        assert version.snapshot["creation_render_request_id"] == request_id
        tracks = repo.list_subtitle_tracks("local", task_id, timeline_version_id=version.id)
        assert len(tracks) == 1 and tracks[0].status == "valid"
        inner = [j for j in repo.list_job_runs("local", task_id) if j.kind == "creation_task_montage_render"]
        assert len(inner) == 1 and inner[0].status == "succeeded"
        assert renders == [request_id]
        validations = 2 if publication_state == "crash" else 1
        assert media_checks == [request_id] * validations
        assert quality_requests == [("local", task_id)]  # reuse the persisted quality request on replay
        execution.render_is_valid = lambda w, t: main_module._creation_render_is_valid(w, t, verify_media=False)
        assert execution.retry("local", task_id, member_id=actor.member_id).id == job.id
        assert submit_creation_stage(execution, scheduler, repo.get_job_run("local", job.id), actor) is None
        with repo._connect() as conn:
            assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (request_id,)).fetchone()[0] == "done"
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    finally:
        scheduler.close()


@pytest.mark.parametrize("failure", ["http_detail", "runtime_error"])
def test_granted_render_failure_reads_actual_reason_and_preserves_upstream(tmp_path, monkeypatch, failure):
    from fastapi import HTTPException

    repo, task_id, script_id, digest, actor, execution, _, scheduler, base, _ = _render_version_fixture(tmp_path)
    reason = "运行中可用内存低于安全线，本次制作已停止。" if failure == "http_detail" else "actual render worker failed"
    task_before = repo.get_creation_task("local", task_id)
    montage_before = repo.get_creation_task_montage_plan("local", task_id)
    narration_before = repo.get_narration_artifact("local", task_before.narration_cache_key)
    try:
        monkeypatch.setattr("app.creation_execution.sample_host_capacity", lambda: HostCapacity(
            cpu_count=8, cpu_threads_ceiling=7, available_memory_bytes=8 * 1024 ** 3, load_avg_1m=.5))
        monkeypatch.setattr(main_module, "production_repo", repo)
        monkeypatch.setattr(main_module, "creation_execution", execution)

        def failed_render(_workspace, _task, _context):
            if failure == "http_detail":
                raise HTTPException(status_code=500, detail={"error": "montage_render_failed", "reason": reason,
                    "impact": "当前任务仍没有可播放 MP4。", "repair_action": "释放资源后沿原任务继续制作。"})
            raise RuntimeError(f"  {reason}  ")

        job = execution.start("local", task_id, script_id, digest, actor=actor)
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None and grant.request_id == request_id
        executor = CreationJobUnitExecutor(repo, render_action=failed_render,
            render_is_valid=lambda _w, _t, _context: False)
        executor.execute(grant, scheduler)
        scheduler.assert_released(grant)
        persisted = repo.get_job_run("local", job.id)
        assert persisted.status == "failed" and persisted.failure_reason == reason
        assert persisted.progress != 100 and persisted.output_refs == []
        status = main_module._creation_execution_status("local", task_id)
        assert status["job"]["status"] == "failed" and status["job"]["failure_reason"] == reason
        assert status["video_ready"] is False
        task_after = repo.get_creation_task("local", task_id)
        assert task_after.confirmed_script_id == script_id
        assert task_after.current_timeline_version_id == base.id
        assert task_after.narration_cache_key == task_before.narration_cache_key
        assert repo.get_creation_task_montage_plan("local", task_id) == montage_before
        assert repo.get_narration_artifact("local", task_after.narration_cache_key) == narration_before
        assert Path(narration_before.audio_path).is_file()
        with repo._connect() as conn:
            assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (request_id,)).fetchone()[0] == "failed"
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    finally:
        scheduler.close()


@pytest.mark.parametrize("stop", [None, "revoked", "cancelled", "output", "rss", "timeout", "commit_unknown", "input", "render_revoked", "render_commit_unknown"])
def test_confirmed_script_only_runs_all_queue_stages_and_restores_original_account(tmp_path, monkeypatch, stop):
    """Real scheduler/SQL/media, synthetic TTS worker; no model or cloud claim."""
    import sys
    from uuid import uuid4
    from app.auth import AuthPrincipal

    workspace, device = (str(uuid4()) for _ in range(2))
    repo, task_id, script_id, _digest = _seed(tmp_path, workspace_id=workspace)
    members = LocalMemberService(repo.database_path)
    member, member_token = members.join(members.create_invite_code(), "队列镜头确认成员")
    user = member.member_id
    assert members.resolve_member(member_token) == member
    monkeypatch.setattr(main_module, "local_members_service", members)
    plan = repo.get_creation_plan(workspace, f"plan_{workspace}")
    plan.presenter_share_target = 0
    person, voice, _avatar_authorization, _voice_path, _image_path = register_synthetic_profiles(
        repo, workspace_id=workspace, member_id=user,
        person_id="person_1", voice_id="voice_1", person_version="v1", voice_version="v1",
        language="zh", model_id=plan.selected_voice.model_id,
        person_authorization_id="person_auth_1", voice_authorization_id="voice_auth_1",
        voice_usage_scopes=("narration",),
    )
    plan.selected_person = CreationPlanPersonReference(person.id, person.version)
    plan.selected_voice = CreationPlanVoiceReference(
        voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version,
        voice.authorization.id, voice.authorization.version, "zh-CN", voice.model_id,
    )
    plan.owner_user_id = user
    task = repo.get_creation_task(workspace, task_id)
    task.owner_user_id = user
    with repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(plan), default=str), workspace, plan.id))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), workspace, task_id))
        if stop is None:
            # Exercise actual granted FFmpeg concatenation, not just one-file copy.
            script_payload = json.loads(conn.execute("SELECT payload FROM creation_task_scripts WHERE id=?", (script_id,)).fetchone()[0])
            script_payload["sections"].append({**script_payload["sections"][0], "id": "section_2"})
            conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id=?", (json.dumps(script_payload), script_id))
    digest = revision_digest(repo.get_creation_task_script(workspace, script_id), plan)
    asset, _source_reference = register_synthetic_video_asset(
        repo, workspace_id=workspace, member_id=user,
        path=tmp_path / "library/source.mp4", asset_id="asset", seconds=36,
    )
    assert [segment.id for _asset, segment in repo.automatic_material_segments(
        workspace, viewer_user_id=user
    )] == [f"asset-segment-{index}" for index in range(3)]
    monkeypatch.setattr(repo, "resolve_creation_task_narration_inputs",
                        lambda **_kwargs: SimpleNamespace(voice=voice, pronunciation_entries=()))
    model = tmp_path / "model"
    model.mkdir()
    (model / "manifest.json").write_text("{}")
    worker = tmp_path / "synthetic_tts.py"
    worker.write_text('''import sys,json,wave,os
from pathlib import Path
r=json.loads(Path(sys.argv[sys.argv.index('--request')+1]).read_text())
assert 'DEVICE_PROOF' not in os.environ and 'AI_VIDEO_DIARY_LLM_KEY' not in os.environ
assert Path(os.environ['HOME']).parent.name == '.cache'
assert Path(os.environ['XDG_CONFIG_HOME']).parent == Path(os.environ['HOME']).parent
p=Path(r['output_path'])
with wave.open(str(p),'wb') as w:
 w.setparams((1,2,24000,0,'NONE','not compressed')); w.writeframes(b'\\0\\0'*24000)
Path(sys.argv[sys.argv.index('--response')+1]).write_text(json.dumps(dict(schema_version=1,model_id=r['model_id'],audio_path=str(p),duration_seconds=1,sample_rate=24000)))
''')
    generator = NarrationGenerator(tmp_path / "audio", Qwen3TTSSubprocessProvider(
        runtime_python_path=sys.executable, worker_path=worker, model_path=model))
    marker = tmp_path / "worker_started"
    if stop in {"revoked", "cancelled", "rss", "timeout"}:
        worker.write_text(worker.read_text() + f"\nPath({str(marker)!r}).write_text('started')\n" + (
            "import subprocess,time\np=subprocess.Popen([sys.executable,'-c','import time; held=bytearray(80000000); time.sleep(5)']); p.wait()\n"
            if stop == "rss" else "import time; time.sleep(5)\n"))
    narration = NarrationBudget(1000, 10, 1_000_000, 300, 8_000_000, 1, 90,
        1024 ** 3, 16, 0.1, 1024 ** 3, 1_000_000)
    if stop == "rss":
        narration = replace(narration, max_process_rss_bytes=40 * 1024 ** 2)
    elif stop == "output":
        narration = replace(narration, max_output_bytes=8000)
    elif stop == "timeout":
        narration = replace(narration, max_timeout_seconds=1)
    elif stop == "input":
        narration = replace(narration, max_text_chars=1)
    montage = MontageBudget(10, 20, 1_000_000, 1_000_000, 30, 1_000_000)
    # Match the existing synthetic media profile, not the production host budget.
    render = CpuRenderBudget(300, 400_000_000, 1920, 1920, 10_000_000, 20, 0,
                            8_000_000, 1, 90, 512 * 1024 ** 2, 64 * 1024 ** 2, 16)
    budget = HostBudget(100, 1, 100 * 1024 ** 3, 1, 10 ** 12, 10_000_000,
                        cpu_render=render, narration=narration, montage=montage)
    actor = CreationExecutionActor.account(workspace, device, user)
    state = {"identity": "ready", "montage_attempts": 0}
    identity_calls = []

    def verify(original):
        identity_calls.append(original)
        assert original == actor
        return state["identity"]

    owner = CreationJobOwnerVerifier(repo, cpu_render_limits=CpuRenderLimits(8_000_000, 1, 90),
        narration_budget=narration, montage_budget=montage, verify_identity=verify)
    resources = HostResourceProbe(repo.database_path, disk_paths=(tmp_path,), budget=budget)
    # Deterministic ambient load; preserve the probe's real sampling interval.
    def ambient_cpu_percent(*, interval):
        time.sleep(interval)
        return 0

    monkeypatch.setattr("app.host_resource_probes.psutil.cpu_percent", ambient_cpu_percent)
    monkeypatch.setattr("app.creation_execution.sample_host_capacity", lambda **_kwargs:
        HostCapacity(cpu_count=8, cpu_threads_ceiling=7, available_memory_bytes=8 * 1024 ** 3, load_avg_1m=0))
    monkeypatch.setattr("app.montage.sample_host_capacity", lambda **_kwargs:
        HostCapacity(cpu_count=8, cpu_threads_ceiling=7, available_memory_bytes=8 * 1024 ** 3, load_avg_1m=0))
    scheduler = DeviceScheduler(repo.database_path, tmp_path / "device.lock", owners=owner,
        resources=resources, processes=HostProcessProbe())
    children = []
    register_child = scheduler.register_child
    def registered(grant, child):
        register_child(grant, child)
        children.append(child)
    monkeypatch.setattr(scheduler, "register_child", registered)
    execution = _execution(repo)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", execution)
    monkeypatch.setattr(main_module, "narration_generator", generator)
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(repo))
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path / "outputs"),
        video_inbox_path=str(tmp_path / "library"), effective_temporary_root_path=tmp_path / "temp",
        native_portrait_worker_path=None))
    principal = AuthPrincipal(user_id=user, workspace_id=workspace, access_token="ephemeral-fixture-token")
    def authenticate(request):
        token = request.headers.get("X-Local-Member-Token")
        if token:
            assert members.resolve_member(token).member_id == principal.user_id == actor.user_id
        return principal

    account_context = SimpleNamespace(enabled=True, authenticate=authenticate,
        is_onboarding_complete=lambda *_args: True)
    monkeypatch.setattr(main_module, "auth_context", account_context)
    monkeypatch.setattr(main_module, "_verify_creation_identity", verify)
    monkeypatch.setattr(main_module, "trusted_device_credential", lambda: SimpleNamespace(device_id=device))
    runtime = SimpleNamespace(scheduler=scheduler, registrations={"creation_job": SimpleNamespace(owner=owner)},
        last_error=None, wake=lambda: None)
    monkeypatch.setattr(main_module, "device_runtime", runtime)
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(submit=lambda *_args:
        pytest.fail("product cannot use diagnostic execute/thread fallback")))
    calls = []
    render_calls = []

    def narration_action(workspace, current_task, *, grant_context):
        calls.append("narration")
        result = main_module._narration_stage_use_case(repo, narration_budget=narration).generate(
            workspace, current_task, grant_context=grant_context)
        if stop == "commit_unknown":
            state["identity"] = "unknown"
        return result

    def montage_action(workspace, current_task, *, grant_context):
        calls.append("montage_plan")
        state["montage_attempts"] += 1
        if state["montage_attempts"] == 1:
            raise ValueError("synthetic first montage failure")
        return main_module._montage_stage_use_case(repo).generate(workspace, current_task, grant_context=grant_context)

    def render_action(*args):
        render_calls.append("render")
        result = main_module._render_creation_stage_with_grant(*args)
        if stop == "render_commit_unknown":
            state["identity"] = "unknown"
        return result

    executor = CreationJobUnitExecutor(repo, render_action=render_action,
        render_is_valid=main_module._creation_stage_valid_with_grant,
        narration_stage=SimpleNamespace(generate=narration_action),
        montage_stage=SimpleNamespace(generate=montage_action))
    from app.device_runtime import OwnerRegistration, OwnerRouter
    from app.member_permits import MemberDeviceAccess
    registration = OwnerRegistration(owner, executor)
    scheduler.owners = OwnerRouter({"creation_job": registration},
        MemberDeviceAccess(main_module.local_members_service, identity_verifier=verify))
    runtime.registrations = {"creation_job": registration}
    client = TestClient(main_module.app)
    payload = {"script_id": script_id, "input_digest": digest, "requirements_confirmed": True}
    base = f"/api/creation-tasks/{task_id}/execution"
    try:
        assert not repo.get_creation_task(workspace, task_id).narration_cache_key
        state["identity"] = "unknown"
        assert client.post(base + "/start", json=payload).status_code == 503
        assert not repo.list_job_runs(workspace, task_id)
        state["identity"] = "ready"
        first = client.post(base + "/start", json=payload)
        second = client.post(base + "/start", json=payload)
        assert first.status_code == second.status_code == 202, (first.json(), second.json())
        job_id = first.json()["job"]["id"]
        assert second.json()["job"]["id"] == job_id
        assert calls == []
        assert first.json()["current_stage"] == "narration"
        # A restart reads the original persisted account even if no login exists.
        monkeypatch.setattr(main_module, "auth_context", SimpleNamespace(enabled=True))
        main_module._recover_creation_executions()
        state["identity"] = "unknown"
        assert scheduler.pick_and_grant() is None
        assert calls == []
        state["identity"] = "ready"
        with repo._connect() as conn:
            requests = conn.execute("SELECT id FROM device_work_requests WHERE owner_id=?", (job_id,)).fetchall()
        assert len(requests) == 1
        request_id = requests[0][0]
        grant = scheduler.pick_and_grant()
        deadline = time.monotonic() + resources.io_window_seconds + 1
        while grant is None and stop != "input" and time.monotonic() < deadline:
            with repo._connect() as conn:
                waiting = conn.execute("SELECT state,wait_reason FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            assert tuple(waiting) == ("blocked", "resource_unknown")
            assert state["identity"] == "ready"
            assert repo.get_job_run(workspace, job_id).status == "queued"
            assert calls == []
            time.sleep(0.1)
            grant = scheduler.pick_and_grant()
        if stop == "input":
            assert grant is None
            assert calls == []
            assert repo.get_job_run(workspace, job_id).status == "queued"
            with repo._connect() as conn:
                assert conn.execute("SELECT wait_reason FROM device_work_requests WHERE owner_id=?", (job_id,)).fetchone()[0] == "resource_busy"
            assert main_module._creation_execution_status(workspace, task_id)["waiting_reason"] == "本机资源检查尚未通过，正在等待满足制作条件。"
            return
        assert grant is not None
        assert grant.request_id == request_id and grant.unit_key.startswith("narration:")
        assert resources.io_window_seconds == 3.0
        assert resources._admission_io[-1][0] - resources._admission_io[0][0] >= resources.io_window_seconds
        stopper = None
        stop_errors = []
        if stop in {"revoked", "cancelled"}:
            def interrupt_worker():
                try:
                    deadline = time.monotonic() + 3
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert marker.exists()
                    state["identity"] = "unauthorized"
                    if stop == "cancelled":
                        monkeypatch.setattr(main_module, "auth_context", SimpleNamespace(enabled=True,
                            authenticate=lambda _request: principal, is_onboarding_complete=lambda *_args: True))
                        assert client.post(base + "/cancel").status_code == 200
                except Exception as exc:
                    stop_errors.append(exc)
            stopper = Thread(target=interrupt_worker)
            stopper.start()
        executor.execute(grant, scheduler)
        if stopper is not None:
            stopper.join(timeout=4)
            assert not stopper.is_alive()
            assert stop_errors == []
        scheduler.assert_released(grant)
        assert scheduler.processes.children_released(tuple(children)) is True
        if stop in {"revoked", "cancelled", "output", "rss", "timeout", "commit_unknown"}:
            assert repo.get_job_run(workspace, job_id).status == (
                "cancelled" if stop == "cancelled" else "interrupted" if stop in {"revoked", "commit_unknown"} else "failed")
            assert calls == ["narration"]
            assert not any(event.payload.get("status") == "completed" for event in repo.list_task_events(workspace, task_id))
            assert scheduler.pick_and_grant() is None
            return
        grant = scheduler.pick_and_grant()
        assert grant.unit_key.startswith("montage_plan:")
        executor.execute(grant, scheduler)
        scheduler.assert_released(grant)
        assert repo.get_job_run(workspace, job_id).status == "failed"
        assert repo.get_creation_task(workspace, task_id).narration_cache_key
        retry = execution.retry(workspace, task_id, member_id=actor.member_id)
        submit_creation_stage(execution, scheduler, retry, actor)
        monkeypatch.setattr(main_module, "auth_context", account_context)
        for stage in ("montage_plan", "render"):
            grant = scheduler.pick_and_grant()
            with repo._connect() as conn:
                queued = [dict(row) for row in conn.execute(
                    "SELECT id,unit_key,state,wait_reason FROM device_work_requests WHERE owner_id=?", (retry.id,))]
            assert grant is not None and grant.unit_key.startswith(stage + ":"), (
                queued, repo.get_job_run(workspace, retry.id).failure_reason,
                repo.get_creation_task_montage_plan(workspace, task_id).get("status"),
                repo.get_creation_task_montage_plan(workspace, task_id).get("low_confidence_clip_count"))
            if stage == "render" and stop == "render_revoked":
                state["identity"] = "unauthorized"
            executor.execute(grant, scheduler)
            scheduler.assert_released(grant)
            if stage == "render" and stop in {"render_revoked", "render_commit_unknown"}:
                assert repo.get_job_run(workspace, retry.id).status != "succeeded"
                assert not any(event.payload.get("stage") == "render" and event.payload.get("status") == "completed"
                               for event in repo.list_task_events(workspace, task_id))
                assert scheduler.processes.children_released(tuple(children)) is True
                assert render_calls == ([] if stop == "render_revoked" else ["render"])
                return
            if stage == "montage_plan":
                try:
                    montage = repo.get_creation_task_montage_plan(workspace, task_id)
                except KeyError:
                    pytest.fail(repo.get_job_run(workspace, retry.id).failure_reason)
                assert "未运行视觉语义模型" in montage["message"]
                assert montage["status"] == "waiting_for_user"
                pending = [clip["id"] for clip in montage["clips"] if clip["needs_confirmation"]]
                assert len(pending) == montage["low_confidence_clip_count"] > 0
                assert repo.get_creation_task(workspace, task_id).stage_status == "waiting_for_user"
                assert repo.get_job_run(workspace, retry.id).status == "queued"
                assert repo.get_job_run(workspace, retry.id).failure_reason == "waiting_for_user"
                assert repo.get_job_run(workspace, retry.id).finished_at is None
                assert render_calls == []
                assert scheduler.pick_and_grant() is None
                with repo._connect() as conn:
                    requests_before = [dict(row) for row in conn.execute(
                        "SELECT id,unit_key,state FROM device_work_requests WHERE owner_id=? ORDER BY id", (retry.id,))]
                assert not any(row["unit_key"].startswith("render:") for row in requests_before)
                waiting = client.post(base + "/resume")
                assert waiting.status_code == 202, waiting.json()
                assert waiting.json()["job"]["id"] == retry.id
                assert waiting.json()["job"]["status"] == "queued"
                assert calls == ["narration", "montage_plan", "montage_plan"]
                with repo._connect() as conn:
                    assert [dict(row) for row in conn.execute(
                        "SELECT id,unit_key,state FROM device_work_requests WHERE owner_id=? ORDER BY id", (retry.id,))] == requests_before
                for clip_id in pending:
                    before = repo.get_creation_task_montage_plan(workspace, task_id)["clips"]
                    preview = client.post(f"/api/creation-tasks/{task_id}/edits/exact/preview",
                        headers={"X-Local-Member-Token": member_token},
                        json={"operation_type": "ConfirmClip", "clip_id": clip_id})
                    assert preview.status_code == 200, preview.json()
                    proposal = preview.json()["proposal"]
                    assert proposal["preview"]["before"]["detail"] == "待确认"
                    assert proposal["preview"]["after"]["detail"] == "已确认"
                    assert repo.get_creation_task_montage_plan(workspace, task_id)["clips"] == before
                    confirmed = client.post(f"/api/creation-tasks/{task_id}/edits/{proposal['id']}/confirm",
                        headers={"X-Local-Member-Token": member_token})
                    assert confirmed.status_code == 200, confirmed.json()
                    after = repo.get_creation_task_montage_plan(workspace, task_id)["clips"]
                    expected = [{**clip, "needs_confirmation": False} if clip["id"] == clip_id else clip for clip in before]
                    assert after == expected
                    assert render_calls == []
                    assert scheduler.pick_and_grant() is None
                confirmed_plan = repo.get_creation_task_montage_plan(workspace, task_id)
                assert confirmed_plan["status"] == "ready"
                assert confirmed_plan["low_confidence_clip_count"] == 0
                resumed = client.post(base + "/resume")
                assert resumed.status_code == 202, resumed.json()
                assert resumed.json()["job"]["id"] == retry.id
                assert resumed.json()["job"]["status"] == "queued"
                assert calls == ["narration", "montage_plan", "montage_plan"]
        finished = repo.get_job_run(workspace, retry.id)
        assert finished.status == "succeeded", finished.failure_reason
        assert calls == ["narration", "montage_plan", "montage_plan"]
        events = repo.list_task_events(workspace, task_id)
        assert any(event.payload.get("stage") == "render" and event.payload.get("status") == "completed" for event in events)
        start = repo.get_task_event(workspace, f"event_{retry.id}_start")
        assert CreationExecutionActor.from_event(workspace, start.payload) == actor
        assert "ephemeral-fixture-token" not in json.dumps(start.payload)
        assert len(identity_calls) > 10
        result = repo.get_creation_task_montage_plan(workspace, task_id)
        assert main_module.artifact_store.resolve_file(result["output_path"]).is_file()
    finally:
        scheduler.close()


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
    with pytest.raises(PermissionError, match="原身份不匹配"):
        submit_creation_stage(execution, scheduler, job, CreationExecutionActor("workspace_a", "member-b"))
    with pytest.raises(PermissionError, match="创建者"):
        execution.start("workspace_a", task_id, script_id, digest, member_id="member-b")
    scheduler.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg tools unavailable")
@pytest.mark.parametrize("source_change", [False, True])
def test_existing_voice_and_pure_material_montage_submit_bounded_render(tmp_path, monkeypatch, source_change):
    # The admission gate keeps its real request/limit checks; only the ambient host
    # sample is pinned so a busy machine cannot decide whether this test passes.
    pinned_capacity = HostCapacity(cpu_count=8, cpu_threads_ceiling=7,
                                   available_memory_bytes=8 * 1024 ** 3, load_avg_1m=0.5)
    monkeypatch.setattr("app.creation_execution.sample_host_capacity",
                        lambda **_kwargs: pinned_capacity)
    monkeypatch.setattr("app.montage.sample_host_capacity",
                        lambda **_kwargs: pinned_capacity)
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
        size_bytes=video.stat().st_size,
        modified_at=datetime.fromtimestamp(video.stat().st_mtime, timezone.utc),
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
    # The real montage stage persists its input version before requesting render.
    # Recovery must seal that version, not an absent timeline identifier.
    repo.create_timeline_version(TimelineVersion(
        "timeline_render_input", task.workspace_id, task_id, None, 1,
        {"montage": creation_task_montage_plan_to_payload(montage)}, None))
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
        assert repo.get_job_run(task.workspace_id, job.id).status == "failed"
        job = execution.retry(task.workspace_id, task_id, member_id=actor.member_id)
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


def _permit_scheduler(repo, tmp_path):
    """整机许可队列 + 真实本机名册；许可身份从成员行来，不从字符串来。"""
    owner = CreationJobOwnerVerifier(repo)
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=owner,
        resources=SimpleNamespace(admit=lambda _request: True, continue_unit=lambda _grant: True,
                                  released=lambda _grant: True),
        processes=SimpleNamespace(current=lambda: ProcessIdentity(1234, "test", "boot"),
                                  children_released=lambda _children: True,
                                  recovery_state=lambda _holder, _children: "released"),
    )
    members = LocalMemberService(repo.database_path)
    seeded_owner = members.whoami(None)[0]
    return scheduler, members, seeded_owner


def test_http_start_requires_explicit_confirmation_and_is_idempotent(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    scheduler, members, owner_member = _permit_scheduler(repo, tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", _execution(repo))
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "device_runtime", SimpleNamespace(
        scheduler=scheduler, registrations={"creation_job": SimpleNamespace(owner=CreationJobOwnerVerifier(repo))},
        last_error=None, wake=lambda: None,
    ))
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(
        submit=lambda *_args: pytest.fail("许可已接通，制作作业不得退回旧的本机线程")))
    client = TestClient(main_module.app,
                        headers={"X-Local-Member-Token": members.whoami(None)[1]})
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
    assert first.json()["status_updated_at"] is not None
    assert len(repo.list_job_runs("local", task_id)) == 1
    # 两次点击只产生一条持久许可请求，身份是名册里的真实成员行。
    with repo._connect() as conn:
        requests = conn.execute(
            "SELECT member_id FROM device_work_requests WHERE owner_type='creation_job'",
        ).fetchall()
    assert [row["member_id"] for row in requests] == [owner_member.member_id]
    scheduler.close()


def test_http_start_refuses_an_identity_it_cannot_verify(tmp_path, monkeypatch):
    """没有可核验身份时不给许可，也不退回旧线程硬跑。"""
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    scheduler, members, _owner = _permit_scheduler(repo, tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", _execution(repo))
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "device_runtime", SimpleNamespace(
        scheduler=scheduler, registrations={"creation_job": SimpleNamespace(owner=CreationJobOwnerVerifier(repo))},
        last_error=None, wake=lambda: None,
    ))
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(
        submit=lambda *_args: pytest.fail("身份不可核验时不得放行制作作业")))
    base = f"/api/creation-tasks/{task_id}/execution"
    payload = {"script_id": script_id, "input_digest": digest, "requirements_confirmed": True}
    forged = TestClient(main_module.app, headers={"X-Local-Member-Token": "not-a-real-token"})
    assert forged.post(base + "/start", json=payload).status_code == 401
    assert not repo.list_job_runs("local", task_id)
    # 另一位真实成员发起时，许可挂的是他自己的成员行，不是 owner 的，也不是一个字符串。
    second, second_token = members.join(members.create_invite_code(), "第二位")
    client = TestClient(main_module.app, headers={"X-Local-Member-Token": second_token})
    assert client.post(base + "/start", json=payload).status_code == 202
    with repo._connect() as conn:
        rows = conn.execute(
            "SELECT member_id FROM device_work_requests WHERE owner_type='creation_job'",
        ).fetchall()
    assert [row["member_id"] for row in rows] == [second.member_id]
    scheduler.close()


def test_scheduled_http_start_uses_one_persisted_device_request_and_reports_wait(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    execution = _execution(repo)
    scheduler, members, seeded_owner = _permit_scheduler(repo, tmp_path)
    task = repo.get_creation_task("local", task_id)
    task.owner_user_id = seeded_owner.member_id
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(task), default=str), "local", task_id))
    owner = CreationJobOwnerVerifier(repo)
    wake_calls = []
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", execution)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "creation_execution_mode", "scheduled")
    monkeypatch.setattr(main_module, "device_runtime", SimpleNamespace(
        scheduler=scheduler, registrations={"creation_job": SimpleNamespace(owner=owner)}, last_error=None,
        running=True, wake=lambda: wake_calls.append(True),
    ))
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(
        submit=lambda *_args: pytest.fail("scheduled request must not enter legacy worker"),
    ))
    client = TestClient(main_module.app,
                        headers={"X-Local-Member-Token": members.whoami(None)[1]})
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
    assert execution.cancel("local", task_id, member_id=seeded_owner.member_id).status == "cancelled"
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


def _render_validity_fixture(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "artifacts")
    published = store.publish_bytes("task/preview.mp4", b"invalid media")
    task = SimpleNamespace(current_timeline_version_id="version_1", confirmed_script_id="script_1")
    plan = creation_task_montage_plan_to_payload(CreationTaskMontagePlan(
        id="montage_validity", workspace_id="workspace_a", task_id="task_1",
        script_id="script_1", script_title="fixture", status="rendered",
        cues=[], clips=[], duration_seconds=1, low_confidence_clip_count=0,
        output_path=published.logical_ref))
    record = SimpleNamespace(logical_ref=published.logical_ref, kind="rendered_video",
                             producer_job_id="render_1", sha256=published.sha256,
                             size_bytes=published.size_bytes)
    current_version = SimpleNamespace(task_id="task_1", snapshot={
        "output_refs": [published.logical_ref], "transform_run_id": "transform_render_1"})
    transform = SimpleNamespace(task_id="task_1", job_id="render_1", status="succeeded",
                                output_refs=[published.logical_ref], input_digest="render-input")
    repository = SimpleNamespace(
        get_creation_task=lambda *_: task,
        get_creation_task_montage_plan=lambda *_: plan,
        get_timeline_version=lambda *_: current_version,
        list_artifact_records=lambda *_: [record],
        get_job_run=lambda *_: SimpleNamespace(status="succeeded", task_id="task_1",
            output_refs=[published.logical_ref], input_digest="render-input"),
        get_transform_run=lambda *_: transform,
    )
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "artifact_store", store)
    monkeypatch.setattr(main_module, "_validate_montage_material_use", lambda *_: None)
    return store, published, current_version, transform


def test_video_ready_requires_current_version_registered_hash_and_probe(tmp_path, monkeypatch):
    store, published, current_version, transform = _render_validity_fixture(tmp_path, monkeypatch)
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")
    monkeypatch.setattr(main_module, "_validate_rendered_media", lambda *_args, **_kwargs: None)
    assert main_module._creation_render_is_valid("workspace_a", "task_1")
    transform.status = "failed"
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")
    transform.status = "succeeded"
    current_version.snapshot["output_refs"] = []
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")
    current_version.snapshot["output_refs"] = [published.logical_ref]
    store.resolve_file(published.logical_ref).write_bytes(b"corrupt")
    assert not main_module._creation_render_is_valid("workspace_a", "task_1")


def test_render_validity_requires_lineage_without_media(tmp_path, monkeypatch):
    store, published, current_version, transform = _render_validity_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "_validate_rendered_media", lambda *_args, **_kwargs: pytest.fail("metadata-only validation must not probe"))
    def valid():
        return main_module._creation_render_is_valid("workspace_a", "task_1", verify_media=False)
    assert valid()
    current_version.snapshot.pop("transform_run_id")
    assert not valid()
    current_version.snapshot["transform_run_id"] = "transform_render_1"
    transform.status = "failed"
    assert not valid()
    transform.status = "succeeded"
    current_version.snapshot["output_refs"] = []
    assert not valid()
    current_version.snapshot["output_refs"] = [published.logical_ref]
    store.resolve_file(published.logical_ref).write_bytes(b"x" * published.size_bytes)
    assert not valid()


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


@pytest.mark.parametrize("scenario", ["single", "changed_input", "newer_execution", "newer_input_and_execution"])
def test_recovered_interrupted_narration_resets_stage_before_admission(tmp_path, scenario):
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.contracts import JobRun, TaskEvent
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    scheduler, _members, member = _permit_scheduler(repo, tmp_path)
    execution = _execution(repo)
    actor = CreationExecutionActor("local", member.member_id)
    job = execution.start("local", task_id, script_id, digest, actor=actor)
    request_id = submit_creation_stage(execution, scheduler, job, actor)
    repo.claim_job_run("local", job.id)
    repo.start_creation_task_narration("local", task_id)
    # The old startup recovery only changed JobRun, leaving this stage running.
    repo.interrupt_running_job_runs()
    assert repo.get_creation_task("local", task_id).stage_status == "running"
    if scenario in {"changed_input", "newer_input_and_execution"}:
        script = repo.get_creation_task_script("local", script_id)
        script = replace(script, sections=[replace(script.sections[0], content="较新采用的文案")])
        with repo._connect() as conn:
            conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id=?",
                (json.dumps(asdict(script), default=str), script.id))
    if scenario in {"newer_execution", "newer_input_and_execution"}:
        newer_digest = revision_digest(repo.get_creation_task_script("local", script_id),
            repo.get_creation_plan("local", repo.get_creation_task("local", task_id).plan_id))
        newer = repo.create_job_run(JobRun("newer-execution", "local", task_id,
            "creation_task_execution", "newer-execution-input", newer_digest))
        repo.append_task_event(TaskEvent(f"event_{newer.id}_start", "local", task_id, newer.id,
            "creation_execution_started", {"script_id":script_id,
                "plan_id":repo.get_creation_task("local", task_id).plan_id,
                "input_digest":newer_digest, "member_id":member.member_id}))
        repo.claim_job_run("local", newer.id)
        if repo.get_creation_task("local", task_id).stage_status != "running":
            repo.start_creation_task_narration("local", task_id)
    current_task = asdict(repo.get_creation_task("local", task_id))
    control = CreationExecutionControl(repo, execution, None, lambda original: "ready")
    runtime = SimpleNamespace(scheduler=scheduler,
        registrations={"creation_job": SimpleNamespace(owner=scheduler.owners)}, wake=lambda: None)
    control.recover(runtime)
    recovered = repo.get_creation_task("local", task_id)
    if scenario == "single":
        assert recovered.stage_status == "not_started" and recovered.stage_reason == "process_interrupted"
    else:
        assert asdict(recovered) == current_task
    assert repo.get_job_run("local", job.id).status == "interrupted"
    with repo._connect() as conn:
        assert conn.execute("SELECT id FROM device_work_requests WHERE owner_id=?", (job.id,)).fetchall()[0][0] == request_id
        assert conn.execute("SELECT count(*) FROM device_work_requests WHERE owner_id=?", (job.id,)).fetchone()[0] == 1
    control.recover(runtime)  # Same receipt and request, no duplicate recovery write.
    if scenario == "single":
        # The real stage gate now permits re-entry; no voice model is called.
        assert repo.start_creation_task_narration("local", task_id).stage_status == "running"
    else:
        assert asdict(repo.get_creation_task("local", task_id)) == current_task
    scheduler.close()


def test_lifespan_recovers_pending_job_and_binds_only_current_workspace(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    scheduler, members, owner_member = _permit_scheduler(repo, tmp_path)
    execution = _execution(repo)
    # 能被复核的作业必须带着真实成员身份落库，否则开机时就该拒绝重新申请许可。
    queued = execution.start("local", task_id, script_id, digest,
                              member_id=owner_member.member_id)
    submitted = []
    worker = SimpleNamespace(
        submit=lambda *args: submitted.append(args), shutdown=lambda **_kwargs: None,
    )
    runtime_starts = []
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "quality_check_repo", main_module.QualityCheckRepository(repo.database_path))
    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(repo))
    monkeypatch.setattr(main_module, "creation_execution", execution)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "execution_worker", worker)
    monkeypatch.setattr(main_module, "material_ingestion", None)
    monkeypatch.setattr(main_module, "device_runtime", SimpleNamespace(
        scheduler=scheduler, registrations={"creation_job": SimpleNamespace(owner=CreationJobOwnerVerifier(repo))},
        last_error=None, running=False, wake=lambda: None, start=lambda: runtime_starts.append("start"),
        stop=lambda **_kwargs: True,
    ))
    with TestClient(main_module.app) as client:
        # 开机恢复重新申请的是整机许可，不是绕开身份和主机门禁的旧线程。
        assert not submitted
        assert runtime_starts == ["start"]
        with repo._connect() as conn:
            recovered = conn.execute(
                "SELECT member_id, owner_id FROM device_work_requests WHERE owner_type='creation_job'",
            ).fetchall()
        assert [(row["member_id"], row["owner_id"]) for row in recovered] == [
            (owner_member.member_id, queued.id),
        ]
        scheduler.close()
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
    repo.bind_material_ingestion("local", expected_directory=str(library))
    for name in ("one.mp4", "two.mp4"):
        repo.confirm_material_copy_complete(
            "local", relative_path=name,
            expected_revision=repo.material_copy_revision((library / name).stat()),
            confirmation_source="manual",
        )
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


@pytest.mark.parametrize("after_wait", ["recover", "io_gap", "revoked", "cancelled", "unknown", "unreleased", "write_failure", "model_error", "checkpoint_pressure", "ordinary_error", "user_cancel", "application_quit"])
def test_resource_pressure_defers_original_job_and_revalidates_before_regrant(tmp_path, monkeypatch, after_wait):
    """Real roster/permit/driver; explicit resource-pressure fixture, no model."""
    from datetime import timedelta
    from app.device_runtime import OwnerRegistration, build_device_runtime
    from app.device_scheduler import RecoveryBlocked
    from app.host_resource_probes import HostResourceDecision, HostResourceRefusal
    from app.member_permits import MemberDeviceAccess
    from _media_preview_test_fixtures import register_synthetic_profiles
    from app.production import CreationPlanPersonReference

    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    members = LocalMemberService(repo.database_path)
    member = members.add_member("bounded fixture member")
    actor = CreationExecutionActor("local", member.member_id)
    access = MemberDeviceAccess(members)
    person, voice, _avatar_auth, reference, _image_path = register_synthetic_profiles(
        repo, workspace_id="local", member_id=member.member_id,
        person_id="person_1", voice_id="voice_1", person_version="v1", voice_version="v1",
        language="zh", model_id="test", person_authorization_id="person_auth_1",
        voice_authorization_id="voice_auth_1", voice_usage_scopes=("narration",),
    )
    plan = repo.get_creation_plan("local", "plan_local")
    plan.selected_person = CreationPlanPersonReference(person.id, person.version)
    plan.selected_voice = CreationPlanVoiceReference(
        voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version,
        voice.authorization.id, voice.authorization.version, "zh-CN", "test",
    )
    plan.owner_user_id = member.member_id
    task = repo.get_creation_task("local", task_id)
    task.owner_user_id = member.member_id
    with repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(plan), default=str), "local", plan.id))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), "local", task_id))
    digest = revision_digest(repo.get_creation_task_script("local", script_id), plan)
    narration = NarrationBudget(1000, 10, 1000000, 30, 100000, 1, 10, 1024, 16, 0.1, 1024, 1)
    owner = CreationJobOwnerVerifier(repo, narration_budget=narration, verify_identity=access.verify_identity)
    # Only host pressure is substituted. Identity, inputs, SQLite, process and lock checks are real.
    resources = SimpleNamespace(admit=lambda request: HostResourceDecision(
        request.resources.get("kind") == "creation_narration", dict(request.resources), {}),
        continue_unit=lambda grant: True, released=lambda grant: True)
    unknown_reason = {"unknown":"disk_io_before_unavailable", "io_gap":"io_observation_gap"}.get(after_wait)
    failure = HostResourceRefusal(HostResourceDecision(None if unknown_reason else False,
        {}, {} if unknown_reason else {"disk_io_bytes_per_second": {"actual": 200, "maximum": 100, "passed": False}},
        unknown_reason))
    if after_wait == "model_error":
        failure.command_diagnostics = {"before_termination_returncode": -6, "stderr": b"synthetic model abort"}
    attempts = []
    grants = []
    def action(workspace, current_task, context):
        attempts.append(context.grant.generation)
        grants.append(context.grant)
        repo.start_creation_task_narration(workspace, current_task)
        if after_wait == "application_quit":
            executor.request_stop()
            context.check_execution_limits()
        if len(attempts) == 1 and after_wait != "checkpoint_pressure":
            if after_wait == "ordinary_error":
                raise RuntimeError("synthetic narration failed")
            if after_wait == "user_cancel":
                from app.granted_subprocess import GrantedCommandCancelled
                execution.cancel(workspace, current_task, member_id=member.member_id)
                raise GrantedCommandCancelled("user cancelled")
            if after_wait == "unreleased":
                monkeypatch.setattr(context.scheduler.processes, "children_released", lambda children: False)
            raise failure
        audio = tmp_path / "library/narration.wav"
        audio.write_bytes(synthetic_wav_bytes())
        record = NarrationArtifactRecord(id="pressure_audio", workspace_id=workspace, task_id=current_task,
            script_id=script_id, voice_profile_id="voice_1", cache_key="pressure_cache",
            audio_path=str(audio), duration_seconds=0.01, sample_rate=24000, model_id="synthetic")
        if after_wait == "checkpoint_pressure":
            repo.complete_creation_task_narration(record)
            raise failure
        if after_wait in {"ordinary_error", "user_cancel"}:
            repo.complete_creation_task_narration(record)
            return
        repo.save_narration_artifact(record)
        task = repo.get_creation_task(workspace, current_task)
        task.narration_cache_key = record.cache_key
        with repo._connect() as conn:
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?", (json.dumps(asdict(task), default=str), workspace, current_task))
    executor = CreationJobUnitExecutor(repo, render_action=lambda *args: pytest.fail("not render"),
        render_is_valid=lambda *args: False,
        narration_stage=SimpleNamespace(generate=lambda workspace, current_task, *, grant_context:
            action(workspace, current_task, grant_context)))
    runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path / "device.lock",
        device_access=access, registrations={"creation_job": OwnerRegistration(owner, executor, executor.record_stopped)},
        resources=resources, processes=HostProcessProbe(), poll_interval_seconds=0.01)
    execution = _execution(repo)
    job = execution.start("local", task_id, script_id, digest, actor=actor)
    request_id = submit_creation_stage(execution, runtime.scheduler, job, actor)
    with repo._connect() as conn:
        first_ready = conn.execute("SELECT first_ready_at_utc FROM device_work_requests WHERE id=?", (request_id,)).fetchone()[0]
    rolled_back = []
    if after_wait == "write_failure":
        original_append = repo.append_task_event
        def fail_pressure_receipt(event, **kwargs):
            if event.id.startswith(f"event_{job.id}_pressure_"):
                raise OSError("fixture receipt storage failure")
            with repo._connect() as conn:
                rolled_back.append((conn.execute("SELECT state FROM device_permit").fetchone()[0],
                    conn.execute("SELECT status FROM job_runs WHERE id=?", (job.id,)).fetchone()[0],
                    repo.get_creation_task("local", task_id).stage_status))
            return original_append(event, **kwargs)
        monkeypatch.setattr(repo, "append_task_event", fail_pressure_receipt)
    try:
        if after_wait in {"unknown", "unreleased", "write_failure"}:
            with pytest.raises(OSError if after_wait == "write_failure" else RecoveryBlocked):
                runtime.run_once()
            assert runtime.last_error is not None
            assert repo.get_job_run("local", job.id).status == "interrupted"
            assert repo.get_creation_task("local", task_id).stage_status == "not_started"
            stopped = repo.get_task_event("local", f"event_{job.id}_stopped_{grants[-1].generation}")
            assert stopped.payload["permit_retained"] is True
            if after_wait == "write_failure":
                assert rolled_back == [("held", "running", "running")]
                with pytest.raises(KeyError):
                    repo.get_task_event("local", f"event_{job.id}_pressure_{grants[-1].generation}")
            with repo._connect() as conn:
                assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "held"
                assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (request_id,)).fetchone()[0] == "granted"
            monkeypatch.setattr(runtime.scheduler.processes, "children_released", lambda children: True)
            grant = grants[-1]
            runtime.scheduler.complete_unit(grant, checkpoint_database_path=repo.database_path,
                checkpoint_write=lambda conn, current: None, cancelled=True)
            return
        assert runtime.run_once() is True and runtime.last_error is None
        if after_wait == "application_quit":
            interrupted = repo.get_job_run("local", job.id)
            assert interrupted.status == "interrupted" and interrupted.failure_reason == "application_quit"
            assert repo.get_creation_task("local", task_id).stage_status == "not_started"
            with repo._connect() as conn:
                row = conn.execute("SELECT state,wait_reason FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
                assert tuple(row) == ("blocked", "worker_interrupted")
            runtime.scheduler.assert_released(grants[-1])
            return
        if after_wait in {"ordinary_error", "user_cancel"}:
            failed_task = repo.get_creation_task("local", task_id)
            assert failed_task.stage_status == ("failed" if after_wait == "ordinary_error" else "not_started")
            assert failed_task.stage_reason == ("synthetic narration failed" if after_wait == "ordinary_error" else "user_cancelled")
            assert failed_task.recovery_point == "narration"
            assert failed_task.confirmed_script_id == script_id
            assert failed_task.narration_cache_key is None
            with repo._connect() as conn:
                assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "free"
            runtime.scheduler.assert_released(grants[-1])
            resumed = execution.retry("local", task_id, member_id=member.member_id)
            assert resumed.id != job.id and resumed.input_digest == job.input_digest
            submit_creation_stage(execution, runtime.scheduler, resumed, actor)
            assert runtime.run_once() is True
            assert repo.get_job_run("local", resumed.id).status == "queued"
            recovered = repo.get_creation_task("local", task_id)
            assert recovered.narration_cache_key == "pressure_cache" and recovered.stage == "montage"
            assert attempts == [1, 2]
            runtime.scheduler.assert_released(grants[-1])
            return
        if after_wait == "model_error":
            assert repo.get_job_run("local", job.id).status == "failed"
            assert repo.get_job_run("local", job.id).failure_reason == "synthetic model abort"
            assert repo.get_creation_task("local", task_id).stage_status == "failed"
            assert repo.get_creation_task("local", task_id).stage_reason == "synthetic model abort"
            assert runtime.run_once() is False
            return
        assert attempts == [1] and repo.get_job_run("local", job.id).status == "interrupted"
        assert repo.get_creation_task("local", task_id).stage_status == "not_started"
        if after_wait == "checkpoint_pressure":
            assert repo.get_creation_task("local", task_id).stage == "montage"
        with repo._connect() as conn:
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            assert row["state"] == "blocked" and row["wait_reason"] == "resource_pressure"
            assert row["first_ready_at_utc"] == first_ready
            retry_at = datetime.fromisoformat(row["retry_not_before_utc"])
            assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "free"
        if after_wait == "io_gap":
            event = repo.get_task_event("local", f"event_{job.id}_pressure_{grants[-1].generation}")
            assert event.payload["decision"]["reason"] == "io_observation_gap"
            assert repo.get_creation_task("local", task_id).stage_reason == "resource_pressure"
        assert runtime.run_once() is False  # Cannot cold-start every polling tick.
        if after_wait == "revoked":
            members.remove_member(member.member_id)
        elif after_wait == "cancelled":
            execution.cancel("local", task_id, member_id=member.member_id)
            runtime.scheduler.cancel(request_id, actor)
        monkeypatch.setattr(runtime.scheduler, "_now", lambda: retry_at + timedelta(seconds=1))
        assert runtime.run_once() is (after_wait in {"recover", "io_gap", "checkpoint_pressure"})
        assert attempts == ([1, 2] if after_wait in {"recover", "io_gap"} else [1])
        if after_wait in {"recover", "io_gap", "checkpoint_pressure"}:
            assert repo.get_job_run("local", job.id).status == "queued"
            assert repo.get_creation_task("local", task_id).narration_cache_key == "pressure_cache"
            assert all(event.payload.get("stage") != "render" for event in repo.list_task_events("local", task_id))
        with repo._connect() as conn:
            assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "free"
    finally:
        assert runtime.stop(2) is True


def test_task_authorization_survives_unknown_and_restart_but_not_expiry_or_revocation(tmp_path):
    from datetime import timedelta
    from app.creation.task_authorization import TaskAuthorizationUseCase
    repo, task_id, _, _ = _seed(tmp_path, workspace_id="local", voice=False)
    task = repo.get_creation_task("local", task_id)
    actor = CreationExecutionActor("local", "original-member")
    now = [datetime.now(timezone.utc)]
    verdict = ["ready"]
    calls = []
    def verify(identity):
        calls.append(identity)
        return verdict[0]
    service = TaskAuthorizationUseCase(repo, verify, clock=lambda: now[0])
    issued = service.ensure(task, actor)
    verdict[0] = "unknown"
    service.refresh(issued)
    unchanged = repo.get_task_authorization("local", task_id)
    assert unchanged.expires_at == issued.expires_at
    service.last_error = RuntimeError("observation unavailable")
    assert service.status("local", task_id) == "valid"
    restarted = TaskAuthorizationUseCase(repo, verify, clock=lambda: now[0])
    before = len(calls)
    assert restarted.ensure(task, actor) == unchanged
    assert len(calls) == before
    with pytest.raises(PermissionError, match="不能切换"):
        restarted.ensure(task, CreationExecutionActor("local", "another-member"))
    now[0] = issued.expires_at + timedelta(seconds=1)
    assert restarted.status("local", task_id) == "expired"
    verdict[0] = "ready"
    restarted.refresh(unchanged)
    renewed = repo.get_task_authorization("local", task_id)
    assert renewed.expires_at > now[0]
    verdict[0] = "unauthorized"
    restarted.refresh(renewed)
    revoked = repo.get_task_authorization("local", task_id)
    verdict[0] = "ready"
    restarted.refresh(renewed)  # Late ready cannot overwrite the authoritative revocation.
    assert repo.get_task_authorization("local", task_id) == revoked
    assert restarted.status("local", task_id) == "revoked"


def test_task_authorization_keeps_selected_assets_and_accepts_only_own_derived_output(tmp_path):
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.production import ArtifactRecord, JobRun, MaterialSegmentMatch
    repo, task_id, _, _ = _seed(tmp_path, workspace_id="local", voice=False)
    task = repo.get_creation_task("local", task_id)
    actor = CreationExecutionActor("local", "original-member")
    for asset_id in ("selected", "unselected"):
        path = repo.video_library_path / f"{asset_id}.mp4"
        path.write_bytes(b"metadata-only fixture")
        repo._save_asset(MaterialAsset(asset_id, "local", path.name, str(path), path.stat().st_size,
            datetime.now(timezone.utc), visibility="shared"))
    plan = repo.get_creation_plan("local", task.plan_id)
    plan.material_matches = [MaterialSegmentMatch("asset:selected", "segment:selected", 1, ["explicit selection"])]
    with repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(plan), default=str), "local", plan.id))
    service = TaskAuthorizationUseCase(repo, lambda _: "ready")
    authorization = service.ensure(task, actor)
    assert authorization.resource_scope["assets"] == ["selected"]
    source = repo.workspace_media_reference_for_asset("local", "selected")
    job = repo.create_job_run(JobRun("derived-job", "local", task_id, "creation_task_execution", "derived", "a"*64))
    repo.create_artifact_record(ArtifactRecord("derived-artifact", "local", task_id, "rendered_video",
        "artifacts/own-output.mp4", "a"*64, 1, job.id, [source]))
    assert service.verdict(actor, task_id, "task_media", references=["artifacts/own-output.mp4"]) == "ready"
    assert service.verdict(actor, task_id, "task_media", references=[repo.workspace_media_reference_for_asset("local", "unselected")]) == "unauthorized"
    assert service.verdict(actor, task_id, "task_media", references=["workspace://invented-output.mp4"]) == "unauthorized"
    with repo._connect() as conn:
        value = json.loads(conn.execute("SELECT payload FROM creation_plans WHERE id=?", (plan.id,)).fetchone()[0])
        value["goal"] = "a different goal"
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(value),plan.id))
    assert service.status("local", task_id) == "outside_scope"


def test_restart_keeps_revoked_task_paused_and_recovers_other_valid_task(tmp_path):
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.task_authorization import TaskAuthorizationUseCase
    repo, revoked_task, revoked_script, revoked_digest = _seed(tmp_path, workspace_id="local")
    _, valid_task, valid_script, valid_digest = _seed(tmp_path, workspace_id="local", task_suffix="_valid")
    scheduler, _, member = _permit_scheduler(repo, tmp_path)
    register_synthetic_profiles(repo, workspace_id="local", member_id=member.member_id,
        person_id="person_1", voice_id="voice_1", person_version="v1", voice_version="v1",
        language="zh", model_id="test", person_authorization_id="person_auth_1",
        voice_authorization_id="voice_auth_1", voice_usage_scopes=("narration",))
    actor = CreationExecutionActor("local", member.member_id)
    service = TaskAuthorizationUseCase(repo, lambda _: "ready")
    scheduler.owners.task_authorizations = service
    execution = _execution(repo)
    revoked_job = execution.start("local", revoked_task, revoked_script, revoked_digest, actor=actor)
    valid_job = execution.start("local", valid_task, valid_script, valid_digest, actor=actor)
    revoked = service.ensure(repo.get_creation_task("local", revoked_task), actor)
    service.ensure(repo.get_creation_task("local", valid_task), actor)
    repo.save_task_authorization(replace(revoked,revision=revoked.revision+1,
        revoked_at=datetime.now(timezone.utc),last_verdict="unauthorized"),expected_revision=revoked.revision)
    runtime = SimpleNamespace(scheduler=scheduler,
        registrations={"creation_job":SimpleNamespace(owner=scheduler.owners)},wake=lambda:None)
    try:
        CreationExecutionControl(repo,execution,None,lambda _: "ready").recover(runtime)
        with repo._connect() as conn:
            queued = [row[0] for row in conn.execute("SELECT owner_id FROM device_work_requests")]
        assert queued == [valid_job.id]
        assert repo.get_job_run("local",revoked_job.id).status == "queued"
        assert service.status("local",revoked_task) == "revoked"
        assert service.status("local",valid_task) == "valid"
    finally:
        scheduler.close()


def test_scope_refusal_does_not_stop_other_task_authorization_observation(tmp_path):
    from datetime import timedelta
    from app.creation.contracts import JobRun
    from app.creation.task_authorization import TaskAuthorizationUseCase
    repo, denied_task, _, _ = _seed(tmp_path,workspace_id="local")  # Missing voice version is a real resource refusal.
    _, valid_task, _, _ = _seed(tmp_path,workspace_id="local",voice=False,task_suffix="_valid")
    actor = CreationExecutionActor("local","original-member")
    now = [datetime.now(timezone.utc)]
    verdict = ["unknown"]
    service = TaskAuthorizationUseCase(repo,lambda _:verdict[0],clock=lambda:now[0])
    denied = service.ensure(repo.get_creation_task("local",denied_task),actor)
    now[0] += timedelta(seconds=1)
    service.ensure(repo.get_creation_task("local",valid_task),actor)
    for task_id in (denied_task,valid_task):
        repo.create_job_run(JobRun("job_"+task_id,"local",task_id,"creation_task_execution","observe_"+task_id,"a"*64))
    now[0] += timedelta(minutes=1,seconds=1)
    verdict[0] = "ready"
    service.start()
    try:
        for _ in range(100):
            if repo.get_task_authorization("local",valid_task).issued_at is not None:
                break
            time.sleep(.01)
        refused = repo.get_task_authorization("local",denied_task)
        valid = repo.get_task_authorization("local",valid_task)
        assert refused.expires_at == denied.expires_at and refused.issued_at is None
        assert refused.last_verdict.startswith("scope_refused: 原任务声音参考不可用")
        assert refused.last_checked_at == now[0]
        receipt = repo.get_task_event("local",f"event_{refused.id}_{refused.revision}")
        assert receipt.payload["verdict"] == refused.last_verdict
        assert valid.issued_at == now[0] and valid.expires_at > now[0]
        assert service.last_error is None and service._thread.is_alive()
    finally:
        assert service.stop(2)


def test_host_task_business_reads_sealed_memory_and_fences_late_cancellation(tmp_path):
    from threading import Event
    from uuid import uuid4
    from app.assistant.task_business import TaskBusiness
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.conversation_repository import ConversationRepository
    from app.production import JobRun, TaskEvent
    repo,task_id,_,_=_seed(tmp_path,workspace_id='local',voice=False)
    conversations=ConversationRepository(repo.database_path)
    member='original-member';actor=CreationExecutionActor('local',member)
    chat=conversations.create('local',member,'task',task_id)
    chat=conversations.save_draft(chat['id'],'local',member,'完成原视频并检查输出',chat['draft_version'])
    context={'kind':'task','task_id':task_id,'stage':'narration','selected_object_refs':[],
             'target_snapshot':{'timeline_version_id':None,'timeline_sequence':0}}
    turn,_=conversations.create_turn_idempotent(chat['id'],'local',member,chat['draft_text'],str(uuid4()),chat['draft_version'],request_context=context)
    conversations.finish_turn(turn['id'],'completed','已安排',result={'kind':'task_turn',
        'response':{'media_job':{'job_id':'declared-existing-source','status':'queued'}}})
    goal=conversations.register_task_goal(turn['id'])
    authorizations=TaskAuthorizationUseCase(repo,lambda _: 'ready')
    task=repo.get_creation_task('local',task_id)
    authorizations.bind_business_context(task,actor,goal,memory={'revision':3,'banned_words':['私有偏好快照']})
    event={'type':'business_result','event_id':str(uuid4()),'task_id':task_id,'status':'succeeded','phase':'preview','result_kind':'graphic_source'}
    conversations.project_business_result(turn['id'],'local',member,event,response_patch={},message='原结果已核实')
    candidate=conversations.goal_event_candidates()[0]
    claim=conversations.claim_goal_event(candidate,{'timeline_version_id':None,'timeline_sequence':0},max_rounds=6)
    goal=claim['goal']
    control=CreationExecutionControl(repo,_execution(repo),None,lambda _: 'ready')
    job=repo.create_job_run(JobRun('original-job','local',task_id,'creation_task_execution','original-job','a'*64))
    repo.append_task_event(TaskEvent('event_original-job_start','local',task_id,job.id,'creation_execution_started',
        {'member_id':member,'script_id':'script_local','plan_id':'plan_local','input_digest':'a'*64}))
    repo.bind_creation_execution_source(job,{'assistant_turn_id':claim['turn_id'],'conversation_id':chat['id'],
        'input_id':goal.get('source_input_id'),'input_turn_id':goal.get('source_input_turn_id')})
    def revoke_before_owner_write():
        current=repo.get_task_authorization('local',task_id)
        repo.save_task_authorization(replace(current,revision=current.revision+1,
            revoked_at=datetime.now(timezone.utc),last_verdict='unauthorized'),expected_revision=current.revision)
        return None
    ports={key:(lambda *args,**kwargs:None) for key in ('workspace_facts','script_context','script_case','script_facts','generate_topics',
        'topic_facts','edit_capabilities','script_capabilities','tool_capabilities','material_facts','observation_facts',
        'observe_output','request_result','media_result','record_adoption')}
    business=TaskBusiness(repo,conversations,authorizations,runtime=revoke_before_owner_write,execution=control,**ports)
    binding={'workspace_id':'local','member_id':member,'task_id':task_id,'conversation_id':chat['id'],
        'turn_id':claim['turn_id'],'goal_revision':str(goal.get('revision',0)),'input_id':goal.get('source_input_id'),
        'input_turn_id':goal.get('source_input_turn_id'),'task_version':{'timeline_version_id':None,'timeline_sequence':0}}
    def call(op,args):
        return business(op,args,call_id='transport-only',tool_call_id='tool-only',source_binding=binding,
            cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert call('read_creator_memory',{})=={'ok':True,'value':{'revision':3,'banned_words':['私有偏好快照']}}
    wrong=call('start_creation_execution',{'task_id':task_id,'assistant_turn_id':str(uuid4()),'payload':{}})
    assert wrong['ok'] is False and wrong['failure']['status_code']==403
    late=call('cancel_creation_execution',{'task_id':task_id,'payload':{}})
    assert late['ok'] is False and late['failure']['status_code']==403
    assert repo.get_job_run('local',job.id).status=='queued'
    with repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM task_events WHERE event_type LIKE 'task_business_invok%'").fetchone()[0]==0


def test_creation_job_and_original_goal_source_share_the_guarded_owner_transaction(tmp_path):
    from app.assistant.task_business import TaskBusinessRejected
    repo,task_id,script_id,digest=_seed(tmp_path,workspace_id='local')
    execution=_execution(repo);actor=CreationExecutionActor('local','original-member')
    source={'workspace_id':'local','task_id':task_id,'member_id':actor.member_id,
        'assistant_turn_id':'trusted-turn','conversation_id':'trusted-conversation','action':'start'}
    def refused(conn):
        assert conn.in_transaction
        raise TaskBusinessRejected('任务授权已到期')
    with pytest.raises(TaskBusinessRejected):
        execution.start('local',task_id,script_id,digest,actor=actor,write_guard=refused,execution_source=source)
    assert repo.list_job_runs('local',task_id)==[]
    calls=[]
    def allowed(conn):
        assert conn.in_transaction
        calls.append(True)
    job=execution.start('local',task_id,script_id,digest,actor=actor,write_guard=allowed,execution_source=source)
    assert execution.start('local',task_id,script_id,digest,actor=actor,write_guard=allowed,execution_source=source).id==job.id
    located=repo.get_task_event('local','event_assistant_execution_locator_trusted-turn')
    assert located.job_id==job.id and located.payload['source']==source
    assert calls==[True,True] and len(repo.list_job_runs('local',task_id))==1


def _seed_background_business(tmp_path):
    from uuid import uuid4
    from app.assistant.task_business import TaskBusiness
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.conversation_repository import ConversationRepository
    repo,task_id,script_id,digest=_seed(tmp_path,workspace_id='local',voice=False)
    conversations=ConversationRepository(repo.database_path);member='original-member'
    chat=conversations.create('local',member,'task',task_id)
    chat=conversations.save_draft(chat['id'],'local',member,'完成原视频并检查输出',chat['draft_version'])
    context={'kind':'task','task_id':task_id,'stage':'narration','selected_object_refs':[],
        'target_snapshot':{'timeline_version_id':None,'timeline_sequence':0}}
    turn,_=conversations.create_turn_idempotent(chat['id'],'local',member,chat['draft_text'],str(uuid4()),chat['draft_version'],request_context=context)
    conversations.finish_turn(turn['id'],'completed','已安排',result={'kind':'task_turn',
        'response':{'media_job':{'job_id':'declared-source','status':'queued'}}})
    goal=conversations.register_task_goal(turn['id'])
    authorization=TaskAuthorizationUseCase(repo,lambda _: 'ready')
    conversations.task_goal_input_authorized=authorization.adopted_goal_input_valid
    authorization.bind_business_context(repo.get_creation_task('local',task_id),CreationExecutionActor('local',member),goal,memory={'revision':3})
    conversations.project_business_result(turn['id'],'local',member,{'type':'business_result','event_id':str(uuid4()),
        'task_id':task_id,'status':'succeeded','phase':'preview','result_kind':'graphic_source'},response_patch={},message='原结果已核实')
    claim=conversations.claim_goal_event(conversations.goal_event_candidates()[0],
        {'timeline_version_id':None,'timeline_sequence':0},max_rounds=6)
    goal=claim['goal']
    ports={key:(lambda *args,**kwargs:None) for key in ('workspace_facts','script_context','script_case','script_facts','generate_topics',
        'topic_facts','edit_capabilities','script_capabilities','tool_capabilities','material_facts','observation_facts',
        'observe_output','request_result','media_result','record_adoption')}
    business=TaskBusiness(repo,conversations,authorization,runtime=lambda:None,
        execution=CreationExecutionControl(repo,_execution(repo),None,lambda _: 'ready'),**ports)
    binding={'workspace_id':'local','member_id':member,'task_id':task_id,'conversation_id':chat['id'],
        'turn_id':claim['turn_id'],'goal_revision':str(goal.get('revision',0)),'input_id':goal.get('source_input_id'),
        'input_turn_id':goal.get('source_input_turn_id'),'task_version':{'timeline_version_id':None,'timeline_sequence':0}}
    return business,binding,claim,script_id,digest


def test_public_background_turn_forwards_trusted_preflight_binding(tmp_path,monkeypatch):
    from app import assistant_pi as pi
    from app.assistant import turn as turn_module
    business,binding,claim,_,_=_seed_background_business(tmp_path)
    bridge=tmp_path/'host.mjs';bridge.touch()
    monkeypatch.setattr(pi,'pi_runtime_checks',lambda:[])
    monkeypatch.setattr(pi,'_pi_executable',lambda:'declared-pi')
    monkeypatch.setattr(pi,'_configured_model',lambda _:('unused','unused','unused'))
    monkeypatch.setattr(pi,'_agent_root',lambda *_:tmp_path)
    monkeypatch.setattr(turn_module,'runtime_bundle',lambda _:tmp_path)
    monkeypatch.setattr(turn_module,'prepare_runtime',lambda **kwargs:(tmp_path,
        {'PATH':os.environ['PATH'],'AI_VIDEO_DIARY_AGENT_PI_MODULE':'declared-module',
         'AI_VIDEO_DIARY_AGENT_TASK_ID':binding['task_id']},kwargs['tool_names'],'isolated-session'))
    business.workspace_facts=lambda ws,task:{'task_id':task.id,'selected_refs':[]}
    observed=[]
    def process(cmd,*,message,**kwargs):
        assert kwargs['business_handler'] is business and kwargs['source_binding']==binding
        value=json.loads(message)
        if 'factMode' not in value:
            raise pi.AssistantStopped('stop before any model')
        kind=value['factMode'];observed.append(kind)
        result=business('read_creator_memory' if kind=='memory' else 'read_task_workspace',{},
            call_id='declared-preflight',tool_call_id=None,source_binding=binding,
            cancel_event=__import__('threading').Event(),deadline_monotonic=time.monotonic()+10)
        assert result['ok']
        return subprocess.CompletedProcess(cmd,0,json.dumps({'type':'diary_preflight_result','kind':kind,
            'ok':True,'value':result['value']}),'')
    monkeypatch.setattr(pi,'_run_pi_streaming',process)
    with pytest.raises(pi.AssistantStopped,match='stop before any model'):
        pi.run_task_turn(main_module.Settings(),workspace_id='local',task_id=binding['task_id'],
            conversation_id=binding['conversation_id'],message='继续完成',stage='narration',selected_refs=[],playhead_ms=None,
            caller_user_id=binding['member_id'],turn_id=claim['turn_id'],business_handler=business,source_binding=binding)
    assert observed==['memory','task']


def test_background_input_consumption_inherits_scope_and_fences_old_calls(tmp_path,monkeypatch):
    from threading import Event
    from uuid import uuid4
    business,binding,claim,_,_=_seed_background_business(tmp_path)
    conversations=business.conversations;delivery=business.input_delivery(binding)
    def enqueue(context,message='增加结尾要求'):
        chat=conversations.get(binding['conversation_id'],'local',binding['member_id'])
        chat=conversations.save_draft(chat['id'],'local',binding['member_id'],message,chat['draft_version'])
        item,_=conversations.enqueue_input(chat['id'],'local',binding['member_id'],target_turn_id=claim['turn_id'],
            client_request_id=str(uuid4()),message=message,mode='steer',expected_draft_version=chat['draft_version'],
            expected_reference_version=chat['reference_version'],request_context=context)
        assert delivery('next',mode='any')['input_id']==item['input_id']
        return item
    context={**claim['request_context'],'selected_reference':None}
    first=enqueue(context)
    adopted=delivery('consume',first['input_id'],cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    current={**binding,**adopted['_business_binding']}
    assert current['input_id']==first['input_id'] and current['goal_revision']!=binding['goal_revision']
    assert conversations.list_inputs(binding['conversation_id'],'local',binding['member_id'])[0]['status']=='consumed'
    def read(source):
        return business('read_creator_memory',{},call_id='read',tool_call_id='tool',source_binding=source,
            cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert not read(binding)['ok']
    assert read(current)=={'ok':True,'value':{'revision':3}}
    goal=conversations.get_task_goal(claim['turn_id'])
    before=business.production.get_task_event('local',business.authorizations.business_context_id(claim['goal'])).payload
    after=business.production.get_task_event('local',business.authorizations.business_context_id(goal)).payload
    assert before['authorization_id']==after['authorization_id'] and before['identity']==after['identity']
    assert before['source_revisions']==after['source_revisions']
    for invalid_context in ({**context,'task_id':'another-task'},
            {**context,'knowledge_reference':{'knowledge_item_id':'new-private','version':'v1'}},
            {**context,'target_snapshot':{'timeline_version_id':'stale','timeline_sequence':5}}):
        refused=enqueue(invalid_context)
        assert delivery('consume',refused['input_id'],cancel_event=Event(),deadline_monotonic=time.monotonic()+10) is None
        receipt=conversations.list_inputs(binding['conversation_id'],'local',binding['member_id'])[-1]
        assert receipt['status']=='rejected' and receipt['reason']
        assert conversations.get_task_goal(claim['turn_id'])['revision']==goal['revision']
    from app.assistant.task_business import TaskBusinessRejected
    original_inherit=business.authorizations.inherit_business_context
    def expired_after_seal(*args,**kwargs):
        original_inherit(*args,**kwargs)
        raise TaskBusinessRejected('原任务授权在消费前已到期')
    with monkeypatch.context() as patch:
        patch.setattr(business.authorizations,'inherit_business_context',expired_after_seal)
        rolled_back=enqueue(context)
        assert delivery('consume',rolled_back['input_id'],cancel_event=Event(),deadline_monotonic=time.monotonic()+10) is None
    assert conversations.get_task_goal(claim['turn_id'])['revision']==goal['revision']
    with pytest.raises(KeyError):
        business.production.get_task_event('local',business.authorizations.business_context_id(
            {**goal,'revision':goal['revision']+1,'source_input_id':rolled_back['input_id']}))
    cancelled=enqueue(context);stop=Event();stop.set()
    assert delivery('consume',cancelled['input_id'],cancel_event=stop,deadline_monotonic=time.monotonic()+10) is None
    assert conversations.get_task_goal(claim['turn_id'])['source_input_id']==first['input_id']
    business.finish(claim,'已处理追加输入')
    assert claim['goal']['source_input_id']==first['input_id']
    assert conversations.list_inputs(binding['conversation_id'],'local',binding['member_id'])[0]['status']=='completed'
    assert conversations.settle_goal_round(claim) is not None


def test_observation_reconciles_original_before_version_and_marks_unknown(tmp_path,monkeypatch):
    from uuid import uuid4
    from app.creation.output_observation_use_case import OutputObservationUseCase,OutputObservationRejected
    from app.schemas import CreationTaskObservationRequest
    repo,task_id,_,_=_seed(tmp_path,workspace_id='local',voice=False)
    task=repo.get_creation_task('local',task_id);actor=CreationExecutionActor('local','original-member')
    case=OutputObservationUseCase(repo,None,SimpleNamespace(repository=repo),tmp_path,None)
    payload=CreationTaskObservationRequest(expected_timeline_version_id=None,expected_timeline_sequence=0,
        target={'kind':'final'},request_id=uuid4())
    event_id,_=repo.claim_edit_request(task,actor.member_id,payload.request_id,'observation',payload.model_dump(mode='json',exclude_unset=True))
    receipt={'job_id':'original-observation','status':'queued'}
    repo.save_edit_request_result(task,event_id,status_code=202,response=receipt)
    later=replace(task,current_timeline_version_id='later-version')
    assert case.submit(None,None,later,actor,payload,{},receipt_for=None)==receipt
    monkeypatch.setattr(main_module,'_exact_edit_access',lambda *_args,**_kwargs:(later,actor.member_id))
    monkeypatch.setattr(main_module,'_require_permit_actor',lambda *_args:actor)
    monkeypatch.setattr(main_module,'_output_observation_use_case',lambda:case)
    assert main_module.observe_creation_task_output(task_id,None,payload).status_code==202
    other=payload.model_copy(update={'sample_times_seconds':[0.2]})
    with pytest.raises(OutputObservationRejected) as conflict:
        case.submit(None,None,later,actor,other,{},receipt_for=None)
    assert conflict.value.status_code==409
    unknown=payload.model_copy(update={'request_id':uuid4()})
    class FailedQueue:
        def submit(self,*args,**kwargs):
            raise ValueError('queue failed after claim')
    with pytest.raises(OutputObservationRejected) as failure:
        case.submit(None,FailedQueue(),task,actor,unknown,{},receipt_for=None)
    assert failure.value.detail['submission_effect']=='unknown'
    assert failure.value.detail['request_id']==str(unknown.request_id)
    assert failure.value.detail['query_tool']=='read_task_observation_request'
    for exception,status in ((PermissionError('source denied'),403),(ValueError('bad source'),422),(KeyError('gone'),422)):
        monkeypatch.setattr(case,'original_request',lambda *_args:None)
        def prepare(*args): raise exception
        monkeypatch.setattr(main_module,'_prepare_observation_request',prepare)
        with pytest.raises(main_module.HTTPException) as error:
            main_module.observe_creation_task_output(task_id,None,payload)
        assert error.value.status_code==status


def test_execution_projection_failure_reports_original_committed_job(tmp_path,monkeypatch):
    from threading import Event
    business,binding,claim,script_id,digest=_seed_background_business(tmp_path)
    control=business.execution
    def start(ws,task_id,script_id,digest,actor,runtime,**kwargs):
        # Declare the control result through the actual atomic job/source owner;
        # this case tests failed projection, independent of media prerequisites.
        from app.creation.contracts import JobRun
        job=business.production.create_creation_execution_job(JobRun('persisted-job',ws,task_id,
            'creation_task_execution','persisted-job',digest),
            start_payload={'script_id':script_id,'plan_id':'plan_local','input_digest':digest,**actor.identity_payload()},
            write_guard=kwargs['write_guard'],execution_source=kwargs['execution_source'])
        return {'job':asdict(job)}
    monkeypatch.setattr(control,'start',start)
    def failed_projection(*args,**kwargs): raise ValueError('projection unavailable')
    monkeypatch.setattr(business.conversations,'record_task_execution',failed_projection)
    result=business('start_creation_execution',{'payload':{'script_id':script_id,'input_digest':digest,'requirements_confirmed':True}},
        call_id='start',tool_call_id='tool',source_binding=binding,cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert result['ok'] is False and result['failure'].get('submission_effect')=='unknown',result
    jobs=business.production.list_job_runs('local',binding['task_id'])
    assert len(jobs)==1 and result['failure']['job_id']==jobs[0].id
    assert result['failure']['query_tool']=='read_creation_execution'


def test_background_safe_point_uses_host_binding_and_keeps_old_tool_origin(tmp_path):
    import sys
    from uuid import uuid4
    from app import assistant_pi as pi
    business,binding,claim,_,_=_seed_background_business(tmp_path)
    repo=business.conversations
    chat=repo.get(binding['conversation_id'],'local',binding['member_id'])
    chat=repo.save_draft(chat['id'],'local',binding['member_id'],'补充结尾',chat['draft_version'])
    item,_=repo.enqueue_input(chat['id'],'local',binding['member_id'],target_turn_id=claim['turn_id'],
        client_request_id=str(uuid4()),message='补充结尾',mode='steer',expected_draft_version=chat['draft_version'],
        expected_reference_version=chat['reference_version'],request_context={**claim['request_context'],'selected_reference':None})
    delivery=business.input_delivery(binding);observed=[]
    def input_handler(*args,**kwargs):
        result=delivery(*args,**kwargs)
        if args[0]=='consume':
            result['environment']['AI_VIDEO_DIARY_AGENT_INPUT_ID']='untrusted-environment-value'
        return result
    def callback(operation,arguments,**kwargs):
        observed.append((kwargs['tool_call_id'],kwargs['source_binding']['input_id']))
        return business(operation,arguments,**kwargs)
    script='''import json,sys
json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value),flush=True)
send({'type':'tool_execution_start','toolName':'read_creator_memory','toolCallId':'old-tool'})
send({'type':'diary_input_checkpoint','checkpoint_id':1,'phase':'next','mode':'any'})
item=json.loads(sys.stdin.readline())['result']
send({'type':'diary_input_checkpoint','checkpoint_id':2,'phase':'consume','input_id':item['input_id']})
adopted=json.loads(sys.stdin.readline())['result']
assert '_business_binding' not in adopted
send({'type':'tool_execution_start','toolName':'read_creator_memory','toolCallId':'new-tool'})
for tool in ['old-tool','new-tool']:
 send({'type':'diary_business_call','call_id':tool,'tool_call_id':tool,'operation':'read_creator_memory','arguments':{}})
answers=[json.loads(sys.stdin.readline()),json.loads(sys.stdin.readline())]
assert answers[0]['ok'] is False and answers[1]['ok'] is True
'''
    pi.register_turn_control(claim['turn_id'])
    result=pi._run_pi_streaming([sys.executable,'-u','-c',script],message='{"controlEnabled":true,"businessEnabled":true}\n',
        env=os.environ.copy(),cwd=tmp_path,timeout_seconds=5,on_event=lambda _:None,turn_id=claim['turn_id'],
        input_handler=input_handler,business_handler=callback,source_binding=binding)
    assert result.returncode==0,result.stderr
    assert observed==[('old-tool',None),('new-tool',item['input_id'])]


@pytest.mark.parametrize('interruption',['quit','restart'])
def test_adopted_input_and_original_job_survive_turn_interruption(tmp_path,interruption):
    from fastapi.encoders import jsonable_encoder
    from threading import Event
    from uuid import uuid4
    from app.conversation_repository import ConversationRepository
    from app.creation.contracts import JobRun
    business,binding,claim,_,digest=_seed_background_business(tmp_path)
    business.execution.render_stage=SimpleNamespace(valid=lambda *args,**kwargs:False)
    conversations=business.conversations;production=business.production
    chat=conversations.get(binding['conversation_id'],'local',binding['member_id'])
    chat=conversations.save_draft(chat['id'],'local',binding['member_id'],'补充结尾并继续原制作',chat['draft_version'])
    item,_=conversations.enqueue_input(chat['id'],'local',binding['member_id'],target_turn_id=claim['turn_id'],
        client_request_id=str(uuid4()),message=chat['draft_text'],mode='steer',expected_draft_version=chat['draft_version'],
        expected_reference_version=chat['reference_version'],request_context={**claim['request_context'],'selected_reference':None})
    delivery=business.input_delivery(binding)
    delivery('next',mode='any')
    consumed=delivery('consume',item['input_id'],cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    current={**binding,**consumed['_business_binding']}
    source={**{key:binding[key] for key in ('workspace_id','member_id','task_id','conversation_id')},
        'assistant_turn_id':claim['turn_id'],'action':'start','input_id':item['input_id'],'input_turn_id':claim['turn_id']}
    job=production.create_creation_execution_job(JobRun('job_adopted_input','local',binding['task_id'],
        'creation_task_execution','adopted-input-job',digest),start_payload={'script_id':'script_local','plan_id':'plan_local',
        'input_digest':digest,**CreationExecutionActor('local',binding['member_id']).identity_payload()},
        write_guard=lambda conn:business.check(current,Event(),time.monotonic()+10,connection=conn),execution_source=source)
    assert conversations.record_task_execution(claim['turn_id'],'local',binding['member_id'],binding['task_id'],
        {'task_id':binding['task_id'],'job':jsonable_encoder(job)},current_source=True,input_id=item['input_id'])
    if interruption=='quit':
        conversations.finish_turn(claim['turn_id'],'failed',result={'reason':'api_shutdown'})
        settled=conversations.settle_goal_round(claim)  # Deliberately the pre-consumption claim.
        assert settled['status']=='waiting_result' and settled['source_input_id']==item['input_id']
    restarted=ConversationRepository(production.database_path,
        task_goal_input_authorized=business.authorizations.adopted_goal_input_valid)
    restarted.fail_interrupted_turns()
    assert restarted.list_inputs(binding['conversation_id'],'local',binding['member_id'])[0]['status']=='consumed'
    assert restarted.get_task_goal(claim['turn_id'])['status'] in {'running','waiting_result'}
    business.conversations=restarted
    def call(source_binding):
        return business('read_creation_execution',{'job_id':job.id},call_id='reconcile-original',tool_call_id='read',
            source_binding=source_binding,cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert not call(binding)['ok']  # The prior tool still owns the old input/revision.
    actual=business.read_execution(restarted.get_task_goal(claim['turn_id']),CreationExecutionActor('local',binding['member_id']),job_id=job.id)
    assert actual['job']['id']==job.id
    event={'type':'business_result','event_id':'event_original_job_reconciled','task_id':binding['task_id'],
        'phase':'execution','status':'queued','job_id':job.id,'input_id':item['input_id']}
    assert restarted.project_business_result(claim['turn_id'],'local',binding['member_id'],event,
        response_patch={'execution_status':jsonable_encoder(actual)},message='已核对原作业')
    candidate=restarted.goal_event_candidates()[0]
    resumed=restarted.claim_goal_event(candidate,{'timeline_version_id':None,'timeline_sequence':0},max_rounds=6)
    assert resumed is not None and resumed['goal']['source_input_id']==item['input_id']
    resumed_binding={**current,'turn_id':resumed['turn_id']}
    assert call(resumed_binding)['value']['job']['id']==job.id
    authorization=production.get_task_authorization('local',binding['task_id'])
    production.save_task_authorization(replace(authorization,revision=authorization.revision+1,
        revoked_at=datetime.now(timezone.utc),last_verdict='unauthorized'),expected_revision=authorization.revision)
    denied=call(resumed_binding)
    assert denied['ok'] is False and denied['failure']['status_code']==403
    with production._connect() as conn:
        assert not business.authorizations.adopted_goal_input_valid(resumed['goal'],connection=conn)


def test_original_observation_409_stays_rejected_for_http_and_host(tmp_path,monkeypatch):
    from threading import Event
    from uuid import uuid4
    from app.creation.output_observation_use_case import OutputObservationUseCase,OutputObservationRejected
    from app.schemas import CreationTaskObservationRequest
    business,binding,_,_,_=_seed_background_business(tmp_path)
    repo=business.production;task=repo.get_creation_task('local',binding['task_id'])
    actor=CreationExecutionActor('local',binding['member_id'])
    case=OutputObservationUseCase(repo,None,SimpleNamespace(repository=repo),tmp_path,None)
    payload=CreationTaskObservationRequest(expected_timeline_version_id=None,expected_timeline_sequence=0,
        target={'kind':'final'},request_id=uuid4(),assistant_turn_id=binding['turn_id'])
    event_id,_=repo.claim_edit_request(task,actor.member_id,payload.request_id,'observation',payload.model_dump(mode='json',exclude_unset=True))
    rejection={'error':'original_observation_refused','reason':'原输入被拒绝','submission_effect':'rejected'}
    repo.save_edit_request_result(task,event_id,status_code=409,response={'detail':rejection})
    def must_not_submit(*args,**kwargs): raise AssertionError('must replay original refusal before any version or media work')
    monkeypatch.setattr(case,'version',must_not_submit)
    with pytest.raises(OutputObservationRejected) as original:
        case.submit(None,None,task,actor,payload,{},receipt_for=None)
    assert original.value.status_code==409 and original.value.detail==rejection
    monkeypatch.setattr(main_module,'_exact_edit_access',lambda *_args,**_kwargs:(task,actor.member_id))
    monkeypatch.setattr(main_module,'_require_permit_actor',lambda *_args:actor)
    monkeypatch.setattr(main_module,'_output_observation_use_case',lambda:case)
    with pytest.raises(main_module.HTTPException) as http:
        main_module.observe_creation_task_output(task.id,None,payload)
    assert http.value.status_code==409 and http.value.detail==rejection
    response=business('observe_task_output',{'payload':payload.model_dump(mode='json',exclude_unset=True)},
        call_id='old-refusal',tool_call_id='observe',source_binding=binding,cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert response['ok'] is False and response['failure']['status_code']==409
    assert response['failure']['error']==rejection['error'] and response['failure']['submission_effect']=='rejected'
