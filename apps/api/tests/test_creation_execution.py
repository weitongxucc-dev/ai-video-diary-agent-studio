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
from app.assistant.task_business_adapter import TaskBusinessAdapter
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

from app.host_resource_probes import HostBudget, HostResourceProbe, CpuRenderBudget, NarrationBudget, MontageBudget
from app.video_jobs import VideoJobRunner
from _media_preview_test_fixtures import (
    register_synthetic_profiles, register_synthetic_video_asset, synthetic_wav_bytes,
    write_synthetic_video,
)


def _seed(tmp_path: Path, *, workspace_id: str = "workspace_a", confirmed: bool = True, voice: bool = True, task_suffix: str = "", duration_seconds: int = 30):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(database_path=tmp_path / "isolated.sqlite3", video_library_path=library)
    task_id = f"task_{workspace_id}{task_suffix}"
    plan = CreationPlan(
        id=f"plan_{workspace_id}{task_suffix}", workspace_id=workspace_id,
        goal="一条测试视频", name="测试", target_count=1, language="zh-CN",
        aspect_ratio="9:16", duration_target_seconds=duration_seconds,
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
    from app.production import CreationTaskScript, CreationTaskScriptSection
    seeded_script = CreationTaskScript(**{key: value for key, value in payload.items()
        if key not in {'sections', 'created_at', 'updated_at', 'confirmed_at'}},
        sections=[CreationTaskScriptSection(**section) for section in payload['sections']],
        created_at=datetime.fromisoformat(now), updated_at=datetime.fromisoformat(now),
        confirmed_at=datetime.fromisoformat(now) if confirmed else None)
    with sqlite3.connect(repo.database_path) as conn:
        conn.execute('BEGIN')
        conn.execute(
            "INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
            (script_id, workspace_id, task_id, payload["run_id"], repo._script_payload(conn, seeded_script), now),
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


def _qualify_start_voice(repo, task_id, member_id, workspace_id='local'):
    register_synthetic_profiles(repo, workspace_id=workspace_id, member_id=member_id,
        person_id='person_1', voice_id='voice_1', person_version='v1', voice_version='v1', language='zh', model_id='test',
        person_authorization_id='person_auth_1', voice_authorization_id='voice_auth_1')
    with repo._connect() as conn:
        task=repo.get_creation_task(workspace_id,task_id)
        task.owner_user_id=member_id
        conn.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(task),default=str),workspace_id,task_id))


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
    _qualify_start_voice(repo,task_id,actor.member_id,workspace_id='workspace_a')
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
        last_error=None, running=True, wake=lambda: None)
    monkeypatch.setattr(main_module, "device_runtime", runtime)
    monkeypatch.setattr(execution, "execute", lambda *_args:
        pytest.fail("product cannot use diagnostic execute/thread fallback"))
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


def test_legacy_crop_undo_refuses_to_overwrite_later_production(tmp_path):
    from app.creation.contracts import EditIntent,EditProposal
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase,TimelineRestoreCommand,TimelineOutputQualification
    repo,task_id,script_id,_=_seed(tmp_path,workspace_id='local')
    with repo._connect() as conn:
        row=conn.execute('SELECT payload FROM creation_tasks WHERE id=?',(task_id,)).fetchone()
        task=json.loads(row[0]);task['stage']='montage'
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',(json.dumps(task),task_id))
    plan=CreationTaskMontagePlan('legacy','local',task_id,script_id,'legacy','ready',
        [NarrationCue('cue','section_1','original',1)],
        [MontageClip('clip','cue','asset','segment','workspace://original.mp4',0,1,0,1,1,[],False)],1,0)
    payload=creation_task_montage_plan_to_payload(plan)
    repo.save_creation_task_montage_plan('local',task_id,plan.id,payload)
    base=repo.create_timeline_version(TimelineVersion('base','local',task_id,None,1,{'montage':payload},None))
    edits=MetadataEditUseCase(repo,ArtifactStore(tmp_path/'artifacts'),output_root=tmp_path/'restore',audio_service=None)
    intent=repo.create_edit_intent(EditIntent('crop-intent','local',task_id,'crop','montage',[],None,'crop',{}, {},1,'interpreted'))
    proposal=repo.create_edit_proposal(EditProposal('crop-proposal','local',task_id,intent.id,'crop',['clip:clip'],
        {'operation_type':'ChangeCrop','payload':{}},[],{},[]))
    edited=edits.persist_montage_edit(workspace_id='local',task=repo.get_creation_task('local',task_id),proposal=proposal,
        base_version=base,updated_plan=replace(plan,clips=[replace(plan.clips[0],crop_x=.2)]),
        operation_type='ChangeCrop',operation_payload={'clip_id':'clip','crop_x':.2})
    restore=TimelineRestoreUseCase(repo,edits)
    prepared=restore.prepare_restore(TimelineRestoreCommand('local',edited.task,False))
    # A successful no-op production version leaves the complete edit result unchanged.
    changed=repo.get_timeline_version('local',edited.task.current_timeline_version_id)
    snapshot=json.loads(json.dumps(changed.snapshot,default=str))
    snapshot['stage_segments']={'section_2':'later-real-publication'}
    later=TimelineVersion('later-production','local',task_id,changed.id,changed.sequence+1,snapshot,None)
    repo.create_timeline_version(later,expected_current_version_id=changed.id)
    with pytest.raises(ValueError,match='后来成果'):
        restore.prepare_restore(TimelineRestoreCommand('local',repo.get_creation_task('local',task_id),False))
    # A preparation made before the later result must also be fenced at commit.
    with pytest.raises(ValueError):
        restore.apply_restore(prepared,TimelineOutputQualification(prepared,False))
    assert repo.get_creation_task('local',task_id).current_timeline_version_id==later.id
    assert repo.get_creation_task_montage_plan('local',task_id)['clips'][0]['crop_x']==.2
    assert len(repo.list_edit_operations('local',task_id))==1
    # The refused production result is retained above. A separate task checks
    # A/B -> undo B -> undo A without unrelated production facts.
    clean_root=tmp_path/'continuous';clean_root.mkdir()
    clean_repo,clean_task,clean_script,_=_seed(clean_root,workspace_id='local')
    with clean_repo._connect() as conn:
        row=conn.execute('SELECT payload FROM creation_tasks WHERE id=?',(clean_task,)).fetchone()
        value=json.loads(row[0]);value['stage']='montage'
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',(json.dumps(value),clean_task))
    clean_plan=replace(plan,task_id=clean_task,script_id=clean_script)
    clean_payload=creation_task_montage_plan_to_payload(clean_plan)
    clean_repo.save_creation_task_montage_plan('local',clean_task,clean_plan.id,clean_payload)
    clean_base=clean_repo.create_timeline_version(TimelineVersion('clean-base','local',clean_task,None,1,{'montage':clean_payload},None))
    clean_edits=MetadataEditUseCase(clean_repo,ArtifactStore(clean_root/'artifacts'),output_root=clean_root/'restore',audio_service=None)
    for name,position in [('A',.2),('B',.3)]:
        intent=clean_repo.create_edit_intent(EditIntent(f'intent-{name}','local',clean_task,'crop','montage',[],None,'crop',{}, {},1,'interpreted'))
        proposal=clean_repo.create_edit_proposal(EditProposal(f'proposal-{name}','local',clean_task,intent.id,'crop',['clip:clip'],
            {'operation_type':'ChangeCrop','payload':{}},[],{},[]))
        outcome=clean_edits.persist_montage_edit(workspace_id='local',task=clean_repo.get_creation_task('local',clean_task),
            proposal=proposal,base_version=clean_base,updated_plan=replace(clean_plan,clips=[replace(clean_plan.clips[0],crop_x=position)]),
            operation_type='ChangeCrop',operation_payload={'clip_id':'clip','crop_x':position})
        clean_base=clean_repo.get_timeline_version('local',outcome.task.current_timeline_version_id)
    clean_restore=TimelineRestoreUseCase(clean_repo,clean_edits)
    for position in (.2,.5):
        command=TimelineRestoreCommand('local',clean_repo.get_creation_task('local',clean_task),False)
        prepared=clean_restore.prepare_restore(command)
        restored=clean_restore.apply_restore(prepared,TimelineOutputQualification(prepared,False))
        assert restored.montage.clips[0].crop_x==position


def test_raw_material_qualification_agrees_for_search_preflight_and_edit(tmp_path,monkeypatch):
    from app.production import material_segment_eligible
    repo,task_id,_script,_=_seed(tmp_path,workspace_id='local')
    asset,reference=register_synthetic_video_asset(repo,workspace_id='local',member_id='owner',
        path=tmp_path/'library/raw.mp4',asset_id='raw_material',seconds=2)
    asset=repo.update_asset_review('local',asset.id,review_status='raw',usage_role='b_roll')
    original=asset.segments[0]
    repo.update_segment('local',asset.id,original.id,description=original.description,transcript=original.transcript,
        review_status='raw',usage_role='b_roll')
    def matches():
        return repo.search_material_segments('local',asset_ids=[asset.id],usage_roles=['b_roll'],viewer_user_id='owner')
    asset=repo.get_asset('local',asset.id);segment=asset.segments[0]
    assert matches() and repo.automatic_material_segments('local',viewer_user_id='owner')
    assert material_segment_eligible(asset,segment,'local')
    clip=MontageClip('manual','cue',asset.id,segment.id,reference,0,1,0,1,1,[],False)
    assert repo.resolve_edit_clip_source('local',task_id,clip,{},user_id='owner',local_diagnostic=False).asset_id==asset.id
    assert repo.get_asset('local',asset.id).review_status=='raw'
    repo.update_asset_use_state('local',asset.id,'not_for_automation')
    assert matches() and not repo.automatic_material_segments('local',viewer_user_id='owner')
    monkeypatch.setattr(main_module,'production_repo',repo)
    monkeypatch.setattr(main_module,'current_workspace_id',lambda request:'local')
    monkeypatch.setattr(main_module,'_content_reader',lambda request,workspace_id:('owner',False))
    client=TestClient(main_module.app)
    automatic=client.get('/api/production/materials/search',params={'purpose':'automatic','include_eligibility':True})
    assert automatic.status_code==200,automatic.text
    receipt=automatic.json()
    assert receipt['matches']==[] and receipt['qualification_message'] and receipt['total_count']==0 and not receipt['truncated']
    manual=client.get('/api/production/materials/search')
    assert manual.status_code==200 and isinstance(manual.json(),list) and manual.json()[0]['asset_ref']==f'asset:{asset.id}'
    assert repo.resolve_edit_clip_source('local',task_id,clip,{},user_id='owner',local_diagnostic=False).asset_id==asset.id
    with pytest.raises(PermissionError):
        repo.resolve_edit_clip_source('local',task_id,clip,{},user_id='stranger',local_diagnostic=False)
    Path(asset.path).unlink()
    assert not matches() and not repo.automatic_material_segments('local',viewer_user_id='owner')


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
    host=TaskBusinessAdapter(business)
    binding={'workspace_id':'local','member_id':member,'task_id':task_id,'conversation_id':chat['id'],
        'turn_id':claim['turn_id'],'goal_revision':str(goal.get('revision',0)),'input_id':goal.get('source_input_id'),
        'input_turn_id':goal.get('source_input_turn_id'),'task_version':{'timeline_version_id':None,'timeline_sequence':0}}
    def call(op,args):
        return host(op,args,call_id='transport-only',tool_call_id='tool-only',source_binding=binding,
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
    _qualify_start_voice(repo,task_id,actor.member_id)
    source={'workspace_id':'local','task_id':task_id,'member_id':actor.member_id,
        'assistant_turn_id':'trusted-turn','conversation_id':'trusted-conversation','action':'start'}
    def refused(conn):
        assert conn.in_transaction
        raise TaskBusinessRejected('任务授权已到期')
    with pytest.raises(TaskBusinessRejected):
        execution.start('local',task_id,script_id,digest,actor=actor,write_guard=refused,execution_source=source)
    assert repo.list_job_runs('local',task_id)==[]
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_narration_source_bound'").fetchone()[0]==0
    calls=[]
    def allowed(conn):
        assert conn.in_transaction
        calls.append(True)
    job=execution.start('local',task_id,script_id,digest,actor=actor,write_guard=allowed,execution_source=source)
    assert execution.start('local',task_id,script_id,digest,actor=actor,write_guard=allowed,execution_source=source).id==job.id
    located=repo.get_task_event('local','event_assistant_execution_locator_trusted-turn')
    assert located.job_id==job.id and located.payload['source']==source
    assert calls==[True,True] and len(repo.list_job_runs('local',task_id))==1
    binding=repo.creation_narration_source_binding('local',job.id)
    assert binding.payload['basis']=='current_bytes_at_adoption'
    assert binding.payload['business_input_digest']==digest


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
    return business,TaskBusinessAdapter(business),binding,claim,script_id,digest


def test_public_background_turn_forwards_trusted_preflight_binding(tmp_path,monkeypatch):
    from app import assistant_pi as pi
    from app.assistant import turn as turn_module
    business,host,binding,claim,_,_=_seed_background_business(tmp_path)
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
        assert kwargs['business_handler'] is host and kwargs['source_binding']==binding
        value=json.loads(message)
        if 'factMode' not in value:
            raise pi.AssistantStopped('stop before any model')
        kind=value['factMode'];observed.append(kind)
        result=host('read_creator_memory' if kind=='memory' else 'read_task_workspace',{},
            call_id='declared-preflight',tool_call_id=None,source_binding=binding,
            cancel_event=__import__('threading').Event(),deadline_monotonic=time.monotonic()+10)
        assert result['ok']
        return subprocess.CompletedProcess(cmd,0,json.dumps({'type':'diary_preflight_result','kind':kind,
            'ok':True,'value':result['value']}),'')
    monkeypatch.setattr(pi,'_run_pi_streaming',process)
    with pytest.raises(pi.AssistantStopped,match='stop before any model'):
        pi.run_task_turn(main_module.Settings(),workspace_id='local',task_id=binding['task_id'],
            conversation_id=binding['conversation_id'],message='继续完成',stage='narration',selected_refs=[],playhead_ms=None,
            caller_user_id=binding['member_id'],turn_id=claim['turn_id'],business_handler=host,source_binding=binding)
    assert observed==['memory','task']


def test_background_input_consumption_inherits_scope_and_fences_old_calls(tmp_path,monkeypatch):
    from threading import Event
    from uuid import uuid4
    business,host,binding,claim,_,_=_seed_background_business(tmp_path)
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
        return host('read_creator_memory',{},call_id='read',tool_call_id='tool',source_binding=source,
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
    business,host,binding,claim,script_id,digest=_seed_background_business(tmp_path)
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
    result=host('start_creation_execution',{'payload':{'script_id':script_id,'input_digest':digest,'requirements_confirmed':True}},
        call_id='start',tool_call_id='tool',source_binding=binding,cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert result['ok'] is False and result['failure'].get('submission_effect')=='unknown',result
    jobs=business.production.list_job_runs('local',binding['task_id'])
    assert len(jobs)==1 and result['failure']['job_id']==jobs[0].id
    assert result['failure']['query_tool']=='read_creation_execution'


def test_background_safe_point_uses_host_binding_and_keeps_old_tool_origin(tmp_path):
    import sys
    from uuid import uuid4
    from app import assistant_pi as pi
    business,host,binding,claim,_,_=_seed_background_business(tmp_path)
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
        return host(operation,arguments,**kwargs)
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
    business,host,binding,claim,_,digest=_seed_background_business(tmp_path)
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
        return host('read_creation_execution',{'job_id':job.id},call_id='reconcile-original',tool_call_id='read',
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
    business,host,binding,_,_,_=_seed_background_business(tmp_path)
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
    response=host('observe_task_output',{'payload':payload.model_dump(mode='json',exclude_unset=True)},
        call_id='old-refusal',tool_call_id='observe',source_binding=binding,cancel_event=Event(),deadline_monotonic=time.monotonic()+10)
    assert response['ok'] is False and response['failure']['status_code']==409
    assert response['failure']['error']==rejection['error'] and response['failure']['submission_effect']=='rejected'



def _queued_creation_admission_case(tmp_path, *, static):
    from app.host_resource_probes import HostResourceDecision,NarrationBudget
    from app.device_scheduler import SubmissionIntent,SchedulePolicy
    repo,task_id,script_id,digest=_seed(tmp_path,workspace_id='local')
    actor=CreationExecutionActor('local','original-member');execution=_execution(repo)
    register_synthetic_profiles(repo,workspace_id='local',member_id=actor.member_id,
        person_id='person_1',voice_id='voice_1',person_version='v1',voice_version='v1',language='zh',model_id='test',
        person_authorization_id='person_auth_1',voice_authorization_id='voice_auth_1')
    with repo._connect() as conn:
        for table in ('creation_tasks', 'creation_plans'):
            row = conn.execute(f'SELECT id,payload FROM {table} WHERE workspace_id=?', ('local',)).fetchone()
            owned = json.loads(row['payload']); owned['owner_user_id'] = actor.member_id
            conn.execute(f'UPDATE {table} SET payload=? WHERE id=?', (json.dumps(owned), row['id']))
    task = repo.get_creation_task('local', task_id)
    digest = revision_digest(repo.get_creation_task_script('local', script_id), repo.get_creation_plan('local', task.plan_id))
    job=execution.start('local',task_id,script_id,digest,actor=actor)
    budget=NarrationBudget(374,3,1000000,30,3145728,2,300,1024,16,.1,2048,1024)
    owner=CreationJobOwnerVerifier(repo,narration_budget=budget,verify_identity=lambda current:'ready' if current==actor else 'unauthorized')
    # Fixed stage facts and host observation are declared ports; the real owner,
    # scheduler, process identity and SQLite transactions run without a model.
    resources={'kind':'creation_narration','execution_mode':'isolated_qwen3_tts',
        'text_chars':547 if static else 300,'section_count':3,'reference_bytes':627574,
        'duration_seconds':30,'max_output_bytes':3145728,'threads':2,'timeout_seconds':300.0}
    key='narration:'+'b'*64
    owner.stage_facts=lambda *_:(key,dict(resources))
    owner.narration_unit_facts=lambda *args,**kwargs:owner.stage_facts()
    calls=[]
    def admit(request):
        calls.append(request.id)
        return HostResourceDecision(False,dict(request.resources),
            {'text_chars':{'actual':547,'maximum':374,'passed':False}} if static else
            {'available_memory_bytes':{'actual':5,'minimum':10,'passed':False}},
            'declared_resources_exceed_budget' if static else 'host_memory_pressure')
    scheduler=DeviceScheduler(repo.database_path,tmp_path/'static-admission.lock',owners=owner,
        resources=SimpleNamespace(admit=admit,continue_unit=lambda _:True,released=lambda _:True),
        processes=HostProcessProbe(),admission_recorder=owner.record_admission)
    request=scheduler.submit(SubmissionIntent('local','creation_job',job.id,digest,key,SchedulePolicy()),actor)
    return repo,task_id,execution,owner,scheduler,request,actor,calls


@pytest.mark.parametrize('static',[True,False])
def test_creation_admission_static_refusal_is_persistent_but_pressure_keeps_waiting(tmp_path,static):
    from app.creation.execution_control import CreationExecutionControl,ExecutionControlRejected
    repo,task_id,execution,owner,scheduler,request,actor,calls=_queued_creation_admission_case(tmp_path,static=static)
    task=asdict(repo.get_creation_task('local',task_id))
    assert scheduler.pick_and_grant() is None
    first=repo.get_job_run('local',request.owner_id)
    assert scheduler.pick_and_grant() is None
    current=scheduler._get(request.id)
    assert first.attempt==0 and first.started_at is None and current.last_grant_generation is None
    assert asdict(repo.get_creation_task('local',task_id))==task
    control=CreationExecutionControl(repo,execution,SimpleNamespace(valid=lambda *args,**kwargs:False),lambda _:'ready')
    status=control.read_bound('local',task_id,request.owner_id,actor,verify_media=False)
    if static:
        assert current.state=='failed' and current.wait_reason=='declared_resources_exceed_budget'
        assert first.status=='failed' and calls==[request.id]
        receipt=status['input_rejection']
        assert receipt['job_id']==request.owner_id and receipt['device_request_id']==request.id
        assert receipt['input_digest']==request.input_digest and receipt['failed_limits']==[
            {'field':'text_chars','actual':547,'maximum':374}]
        assert status['current_stage']=='narration' and '547' in first.failure_reason
        stored=repo.get_task_event('local',receipt['observation_id'])
        with repo._connect() as conn:
            assert owner.record_admission(conn,request,SimpleNamespace(verdict=False,
                reason='declared_resources_exceed_budget',declared=request.resources),datetime.now(timezone.utc))=='input_rejected'
        assert repo.get_task_event('local',stored.id)==stored
        def must_not_render(*args, **kwargs):
            raise AssertionError('admission recovery must not execute media')
        executor=CreationJobUnitExecutor(repo,render_action=must_not_render,render_is_valid=must_not_render)
        runtime=SimpleNamespace(registrations={'creation_job':SimpleNamespace(owner=owner,executor=executor)},
            scheduler=scheduler,wake=lambda:None)
        with pytest.raises(ExecutionControlRejected) as same_input:
            control.resume('local',task_id,actor,runtime,verify_media=False)
        assert same_input.value.detail['job_id']==request.owner_id
        assert len(repo.list_job_runs('local',task_id))==1
        from fastapi.encoders import jsonable_encoder
        assert jsonable_encoder(status)['input_rejection']['failed_limits']==receipt['failed_limits']
        # An explicitly changed unit declaration may use the formal continue
        # entry; the old refusal survives and no admission/model is run here.
        changed={**request.resources,'text_chars':300}
        owner.stage_facts=lambda *_:('narration:'+'c'*64,changed)
        resumed=control.resume('local',task_id,actor,runtime,verify_media=False)
        assert resumed['job']['status']=='queued' and resumed['job']['id']!=request.owner_id
        assert repo.get_job_run('local',request.owner_id).status=='failed'
        assert repo.get_task_event('local',stored.id)==stored and calls==[request.id]
    else:
        assert current.state=='blocked' and current.wait_reason=='resource_busy'
        assert first.status=='queued' and calls==[request.id,request.id]
        assert status['input_rejection'] is None


def test_creation_static_refusal_rolls_back_with_request_transaction(tmp_path,monkeypatch):
    repo,task_id,_execution,_owner,scheduler,request,_actor,_calls=_queued_creation_admission_case(tmp_path,static=True)
    def failed_request_write(*args,**kwargs): raise RuntimeError('request write failed')
    monkeypatch.setattr(scheduler,'_record_event',failed_request_write)
    with pytest.raises(RuntimeError,match='request write failed'):
        scheduler.pick_and_grant()
    assert repo.get_job_run('local',request.owner_id).status=='queued'
    assert scheduler._get(request.id).state=='waiting'
    assert not [event for event in repo.list_task_events('local',task_id)
        if event.payload.get('failure_kind')=='input_budget_exceeded']
from app.voice_clone import Qwen3TTSSubprocessProvider


def _narration_units_case(tmp_path, *, section_count=2, audio_input_count=10, seconds=1, pcm_inprocess=True, local_policy=False, duration_seconds=30, start_job=True):
    from app.creation.narration_stage_use_case import NarrationStageUseCase
    from app.host_resource_probes import CpuAudioBudget, HostResourceDecision
    from app.narration import NarrationArtifact
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local', duration_seconds=duration_seconds)
    actor = CreationExecutionActor('local', 'original-member')
    register_synthetic_profiles(repo, workspace_id='local', member_id=actor.member_id,
        person_id='person_1', voice_id='voice_1', person_version='v1', voice_version='v1', language='zh', model_id='test',
        person_authorization_id='person_auth_1', voice_authorization_id='voice_auth_1')
    with repo._connect() as conn:
        for table in ('creation_tasks', 'creation_plans'):
            row = conn.execute(f'SELECT id,payload FROM {table} WHERE workspace_id=?', ('local',)).fetchone()
            owned = json.loads(row['payload']); owned['owner_user_id'] = actor.member_id
            conn.execute(f'UPDATE {table} SET payload=? WHERE id=?', (json.dumps(owned), row['id']))
        row = conn.execute('SELECT payload FROM creation_task_scripts WHERE id=?', (script_id,)).fetchone()
        from app.production import CreationTaskScriptSection
        seeded = repo._creation_task_script_from_payload(json.loads(row['payload']), connection=conn)
        seeded.sections = [CreationTaskScriptSection(**{**asdict(seeded.sections[0]),
            'id': f'section_{ordinal}', 'content': '原文' * 130}) for ordinal in range(1, section_count + 1)]
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?', (repo._script_payload(conn, seeded), script_id))
    task = repo.get_creation_task('local', task_id)
    script = repo.get_creation_task_script('local', script_id)
    plan = repo.get_creation_plan('local', task.plan_id)
    execution = _execution(repo)
    if local_policy:
        from app.creation.task_budget_use_case import TaskBudgetUseCase
        repo.authorize_execution_budget_scope('local', task_id, actor, scope_id='isolated-local-goal',
            expected_revision=-1, request_id='trusted-local-delegation', source_ref='isolated-test:local-goal',
            target_task_ids=[task_id], limits={'execution_seconds': None, 'generated_bytes': None},
            policy_kind='local_goal', authorization_guard=lambda conn: None)
        execution.budget_policy_for = TaskBudgetUseCase(repo, scope_for=lambda ws, tid, current:
            {'scope_id': 'isolated-local-goal', 'revision': 0}).local_start_policy
    job = execution.start('local', task_id, script_id, revision_digest(script, plan), actor=actor) if start_job else None
    budget = NarrationBudget(374, 3, 1_000_000, 30, 3*1024*1024, 1, 300, 1024, 16, .1, 2048, 1024,
        input_scope='single_call_experiment_v1')
    audio = CpuAudioBudget(audio_input_count, 32*1024*1024, 30, 30, 48000, 2, 3*1024*1024, 1, 30, 1024, 16, .1, 2048, 1024)
    montage = MontageBudget(10, 20, 1_000_000, 1_000_000, 30, 1_000_000)
    owner = CreationJobOwnerVerifier(repo, narration_budget=budget, narration_audio_budget=audio,
        montage_budget=montage, verify_identity=lambda current: 'ready' if current == actor else 'unauthorized')
    releases = {'ready': True}
    resources = SimpleNamespace(admit=lambda request: HostResourceDecision(True, dict(request.resources), {}, None),
        continue_unit=lambda _: True, released=lambda _: releases['ready'])
    scheduler = DeviceScheduler(repo.database_path, tmp_path/'units.lock', owners=owner, resources=resources, processes=HostProcessProbe())
    generated = []
    merged = []
    class Generator:
        def generate_for_creation_task(self, **kwargs):
            context = kwargs['grant_context']; context.check()
            generated.append((context.grant.request_id, kwargs['segment_id'], context.max_output_bytes,
                context.deadline_monotonic-context.started_monotonic))
            key = hashlib.sha256(kwargs['segment_id'].encode()).hexdigest()
            if kwargs.get('generation_identity'):
                key = hashlib.sha256((key+kwargs['generation_identity']).encode()).hexdigest()
            path = tmp_path/f'{key}.wav'
            if not path.exists():
                import wave
                with wave.open(str(path),'wb') as stream:
                    stream.setparams((1,2,24000,0,'NONE','not compressed'))
                    stream.writeframes(b'\x01\x00'*(24000*seconds))
                time.sleep(.002)
            from app.narration import _pronunciation_dictionary, _pronunciation_version
            pronunciation = _pronunciation_version(_pronunciation_dictionary(entries=kwargs['pronunciation_entries'],
                workspace_id='local', person_profile_id=kwargs['voice_identity'].person_profile_id, task_id=task_id))
            return NarrationArtifact(task_id=task_id, script_id=script_id, audio_path=str(path), duration_seconds=seconds,
                sample_rate=24000, model_id='test', voice_profile_id=kwargs['voice_identity'].id, cache_key=key, language='zh',
                pronunciation_version=pronunciation)
    class Stage(NarrationStageUseCase):
        def prepare_merge(self,*args,**kwargs):
            if not pcm_inprocess:
                return super().prepare_merge(*args,**kwargs)
            from unittest.mock import patch
            from model_workers.pcm_assembly import execute as assemble
            def worker(current_scheduler,grant,command,**options):
                request=current_scheduler._get(grant.request_id)
                merged.append([item['reference'].removeprefix('narration://artifact/') for item in request.resources['inputs']])
                manifest=json.loads(Path(command[-1]).read_text())
                receipt=assemble(manifest,{'generated_bytes':0,'work_created':False})
                return SimpleNamespace(returncode=0,stdout=json.dumps(receipt),stderr='')
            with patch('app.granted_subprocess.run_granted_command',worker):
                return super().prepare_merge(*args,**kwargs)
    stage = Stage(repo,Generator(),None,budget)
    executor = CreationJobUnitExecutor(repo, render_action=lambda *_: None, render_is_valid=lambda *_: False, narration_stage=stage)
    return SimpleNamespace(repo=repo, task_id=task_id, script_id=script_id, job=job, actor=actor, owner=owner,
        execution=execution, scheduler=scheduler, executor=executor, resources=resources, stage=stage,
        generated=generated, merged=merged, releases=releases)


def _assert_wav_reads_outside_writer(monkeypatch,case):
    import wave
    connections=[]
    reads=[]
    def tracked_connect(open_connection):
        def connect():
            conn=open_connection()
            connections.append(conn)
            return conn
        return connect
    monkeypatch.setattr(case.repo,'_connect',tracked_connect(case.repo._connect))
    monkeypatch.setattr(case.scheduler,'_connect',tracked_connect(case.scheduler._connect))
    original=wave.open
    def checked(file,mode=None):
        if mode is None or mode in {'r','rb'}:
            for conn in connections:
                try:
                    active=conn.in_transaction
                except sqlite3.ProgrammingError:
                    continue  # The scheduler explicitly closed this connection.
                if active:
                    import traceback
                    pytest.fail('WAV格式读取持有写事务: '+str(file)+' '+str(traceback.extract_stack()[-6:]))
            reads.append(str(file))
        return original(file,mode)
    monkeypatch.setattr(wave,'open',checked)
    return reads


@pytest.mark.parametrize('corrupt_header',[False,True,'seal_only','commit_rollback','pending_pair'])
def test_pcm_original_job_appends_within_parent_and_keeps_unknown_unpublished(tmp_path,monkeypatch,corrupt_header):
    from model_workers.pcm_assembly import execute as assemble
    from app.device_scheduler import RecoveryBlocked
    from app.creation_execution import next_creation_intent
    case=_narration_units_case(tmp_path,section_count=3,audio_input_count=2,seconds=10,pcm_inprocess=False)
    wav_reads=_assert_wav_reads_outside_writer(monkeypatch,case)
    seen=[]
    def worker(scheduler,grant,command,**kwargs):
        request=scheduler._get(grant.request_id)
        with case.repo._connect() as conn:
            baseline=case.owner.pcm_assembly_baseline(request,conn)
            watcher=case.owner.capture_local(request,conn)
        assert baseline==request.resources['pcm_assembly']
        assert request.resources['input_count']==2
        assert command[-3]=='--pcm-assembly-worker' and command[-2]=='--manifest'
        manifest=json.loads(Path(command[-1]).read_text())
        assert manifest['unit_id']==f'{grant.request_id}:{grant.generation}'
        receipt=assemble(manifest,{'generated_bytes':0,'work_created':False})
        seen.append(receipt)
        with case.repo._connect() as conn:
            assert watcher(conn)=='ready'
        if corrupt_header is True and receipt['finalized']:
            with Path(receipt['work_path']).open('r+b') as stream:
                stream.write(b'BAD!')
        if corrupt_header=='seal_only' and receipt['finalized']:
            Path(manifest['receipt_path']).unlink()
            raise OSError('模拟seal完成后进程回执返回前退出')
        if corrupt_header=='pending_pair' and receipt['finalized']:
            for path in (Path(manifest['receipt_path']),Path(manifest['work_path']+'.sealed.json')):
                os.link(path,path.with_name('.'+path.name+'.pending'))
            raise OSError('模拟发布正式名后删除pending前退出')
        return SimpleNamespace(returncode=0,stdout=json.dumps(receipt),stderr='')
    monkeypatch.setattr('app.granted_subprocess.run_granted_command',worker)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    for _ in range(3):
        grant=case.scheduler.pick_and_grant()
        assert grant is not None,case.repo.get_job_run('local',case.job.id).failure_reason
        case.executor.execute(grant,case.scheduler)
    original_usage=case.repo.creation_narration_usage('local',case.job.id)
    first=case.scheduler.pick_and_grant()
    assert first is not None, (case.repo.get_job_run('local',case.job.id).failure_reason,
        [dict(row) for row in case.repo.list_creation_job_request_states('local',case.job.id)])
    case.executor.execute(first,case.scheduler)
    partial=case.repo.creation_narration_merge_checkpoint('local',case.job.id)
    assert partial is not None,case.repo.get_job_run('local',case.job.id).failure_reason
    assert partial['section_ids']==['section_1','section_2'] and 'output' not in partial
    assert partial['pcm']['generated_bytes']==960044
    assert case.repo.get_creation_task('local',case.task_id).narration_cache_key is None
    second=case.scheduler.pick_and_grant()
    original_commit=case.repo.complete_creation_task_narration
    if corrupt_header=='commit_rollback':
        def rollback(*args,**kwargs):
            original_commit(*args,**kwargs)
            raise RuntimeError('模拟最终登记事务回滚')
        monkeypatch.setattr(case.repo,'complete_creation_task_narration',rollback)
    if corrupt_header:
        with pytest.raises(RecoveryBlocked):
            case.executor.execute(second,case.scheduler)
        assert case.repo.get_creation_task('local',case.task_id).narration_cache_key is None
        usage=case.repo.creation_narration_usage('local',case.job.id)
        assert usage['unsettled'] and usage['used_output_bytes']==original_usage['used_output_bytes']+960044
        if corrupt_header in {'seal_only','commit_rollback','pending_pair'}:
            monkeypatch.setattr(case.repo,'complete_creation_task_narration',original_commit)
            from app.creation.execution_control import CreationExecutionControl
            executor=CreationJobUnitExecutor(case.repo,render_action=lambda *_:None,render_is_valid=lambda *_:False,
                narration_stage=case.stage)
            control=CreationExecutionControl(case.repo,case.execution,SimpleNamespace(valid=lambda *_:False),lambda _:'ready')
            control.recover(SimpleNamespace(scheduler=case.scheduler,registrations={
                'creation_job':SimpleNamespace(owner=case.owner,executor=executor)},wake=lambda:None))
            assert len(seen)==2
            recovered=case.repo.creation_narration_usage('local',case.job.id)
            assert not recovered['unsettled'] and recovered['used_output_bytes']==2880184
            assert case.repo.get_creation_task('local',case.task_id).narration_cache_key
            assert case.scheduler._get(second.request_id).state=='done'
        else:
            with pytest.raises(RecoveryBlocked):
                next_creation_intent(case.repo,case.scheduler,case.job)
    else:
        case.executor.execute(second,case.scheduler)
        current=case.repo.get_creation_task('local',case.task_id)
        assert current.stage=='montage' and current.narration_cache_key
        usage=case.repo.creation_narration_usage('local',case.job.id)
        assert not usage['unsettled'] and usage['used_output_bytes']==2880184<3*1024*1024
        assert seen[0]['file_identity']==seen[1]['file_identity']
        assert seen[1]['previous_pcm_bytes']==seen[0]['pcm_bytes']
        assert seen[1]['generated_bytes']==480008
        assert len(case.execution.jobs('local',case.task_id))==1
    assert wav_reads


@pytest.mark.parametrize('interrupt_final',[False,True,'dependency_changed','source_changed'])
def test_regenerate_narration_keeps_original_job_and_budget_through_pcm_adoption(tmp_path,monkeypatch,interrupt_final):
    from model_workers.pcm_assembly import execute as assemble
    from app.creation.contracts import EditIntent,EditProposal,JobRun,TaskEvent
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation_execution import NARRATION_EDIT_KIND,narration_edit_digest
    from app.device_scheduler import SubmissionIntent,SchedulePolicy
    case=_narration_units_case(tmp_path,section_count=3,audio_input_count=2,pcm_inprocess=False)
    wav_reads=_assert_wav_reads_outside_writer(monkeypatch,case)
    def worker(_scheduler,_grant,command,**_kwargs):
        manifest=json.loads(Path(command[-1]).read_text())
        receipt=assemble(manifest,{'generated_bytes':0,'work_created':False})
        if interrupt_final in (True, 'source_changed') and manifest['job_id']!=case.job.id and receipt['finalized']:
            Path(manifest['receipt_path']).unlink()
            raise OSError('模拟局部配音seal后登记前退出')
        return SimpleNamespace(returncode=0,stdout=json.dumps(receipt),stderr='')
    monkeypatch.setattr('app.granted_subprocess.run_granted_command',worker)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    for _ in range(5):
        case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    for row in case.repo.list_creation_job_request_states('local',case.job.id):
        if row['state'] in {'waiting','blocked'}:
            case.scheduler.cancel(row['id'],case.actor)
    task=case.repo.get_creation_task('local',case.task_id)
    segments=case.repo.list_narration_segments('local',task.id,case.script_id)
    source=tmp_path/'library'/'opaque.mp4';source.write_bytes(b'fixed metadata-only test source')
    asset=MaterialAsset(id='edit-material',workspace_id='local',file_name=source.name,path=str(source),
        size_bytes=source.stat().st_size,modified_at=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc),
        duration_seconds=10,analysis_status='ready',usage_role='b_roll',owner_user_id=case.actor.member_id,
        segments=[MaterialSegment('edit-material-segment','edit-material',0,10,usage_role='b_roll')])
    case.repo._save_asset(asset)
    reference=case.repo.workspace_media_reference_for_asset('local',asset.id)
    cues=[NarrationCue('edit-cue-'+str(i),item.source_section_id,item.text,1,narration_segment_id=item.id)
        for i,item in enumerate(segments)]
    clips=[MontageClip('edit-clip-'+str(i),cue.id,asset.id,asset.segments[0].id,reference,i,i+1,i,i+1,1,[],False)
        for i,cue in enumerate(cues)]
    montage=CreationTaskMontagePlan('edit-montage','local',task.id,case.script_id,'fixture','ready',cues,clips,3,0,
        narration_audio_path=case.repo.narration_artifact_reference(task.narration_cache_key))
    payload=creation_task_montage_plan_to_payload(montage)
    case.repo.save_creation_task_montage_plan('local',task.id,montage.id,payload)
    base=case.repo.create_timeline_version(TimelineVersion('edit-base','local',task.id,None,1,
        {'montage':payload,'narration_cache_key':task.narration_cache_key,'subtitle_track_ids':[]},None))
    task=case.repo.get_creation_task('local',task.id)
    case.stage.metadata=MetadataEditUseCase(case.repo,ArtifactStore(tmp_path/'outputs'),output_root=tmp_path/'outputs',audio_service=None)
    intent=case.repo.create_edit_intent(EditIntent('regenerate-intent','local',task.id,'重新配音','narration',[],None,
        'regenerate_narration',{}, {},1,'interpreted'))
    proposal=case.repo.create_edit_proposal(EditProposal('regenerate-proposal','local',task.id,intent.id,'重新生成首段配音',
        ['narration:'+segments[0].id],{'operation_type':'RegenerateNarrationSegment','payload':{
            'base_version_id':base.id,'narration_segment_id':segments[0].id,
            'expected_audio_reference':segments[0].audio_reference}},[],{},[]))
    from app.creation.execution_control import CreationExecutionControl
    control=CreationExecutionControl(case.repo,case.execution,SimpleNamespace(valid=lambda *_:False),lambda _:'ready')
    runtime=SimpleNamespace(scheduler=case.scheduler,registrations={
        'creation_job':SimpleNamespace(owner=case.owner,executor=case.executor)},wake=lambda:None)
    from functools import partial
    from app.conversation_repository import ConversationRepository
    from app.creation.editing.exact_preview_use_case import validate_edit_origin
    conversations = ConversationRepository(case.repo.database_path)
    source = {'source_kind': 'interactive', 'workspace_id': 'local', 'task_id': task.id, 'member_id': case.actor.member_id,
        'actor_identity': case.actor.identity_payload(), 'request_id': proposal.id,
        'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence}
    if interrupt_final in (True, 'source_changed'):
        source['source_kind'] = 'assistant'
        chat = conversations.create('local', case.actor.member_id, 'task', task.id)
        conversations.save_draft(chat['id'], 'local', case.actor.member_id, '重新配音', 0)
        turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', case.actor.member_id,
            '重新配音', 'origin-request', 1, {'kind': 'task', 'task_id': task.id})
        source.update(conversations.task_turn_source(turn['id'], 'local', case.actor.member_id, task.id))
        source['assistant_turn_id'] = turn['id']
    verifier = partial(validate_edit_origin, conversations)
    case.owner.source_verifier = case.executor.source_verifier = verifier
    job,queued=control.submit_narration_edit('local',task,proposal,case.actor,runtime,source=source)
    binding=case.repo.creation_narration_source_binding('local',job.id)
    assert binding.payload['business_input_digest']==job.input_digest
    assert binding.payload['basis']=='current_bytes_at_adoption'
    if interrupt_final is True:
        conversations.finish_turn(turn['id'], 'completed', '已排队', {})
        goal = {'workspace_id': 'local', 'task_id': task.id, 'member_id': case.actor.member_id,
            'original_turn_id': turn['id'], 'current_turn_id': turn['id'], 'status': 'waiting_result',
            'original_instruction': '重新配音'}
        with conversations._connect() as conn:
            conversations._write_goal(conn, {'id': turn['id']}, goal)
    if interrupt_final == 'dependency_changed':
        from app.device_scheduler import RecoveryBlocked
        _replace_fixed_voice_file(SimpleNamespace(**{**vars(case),'job':job}))
        original_audio=case.repo.resolve_authorized_narration_audio_reference('local',segments[1].audio_reference)
        original_audio.write_bytes(original_audio.read_bytes())
        with pytest.raises(RecoveryBlocked,match='其他产物或依赖已变化'):
            control.submit_narration_edit('local',task,proposal,case.actor,runtime,source=source)
        assert case.scheduler._get(queued.id).state=='waiting'
        assert case.repo.creation_narration_source_binding('local',job.id)==binding
        return
    if not interrupt_final:
        _replace_fixed_voice_file(SimpleNamespace(**{**vars(case),'job':job}))
        same,revalidated=control.submit_narration_edit('local',task,proposal,case.actor,runtime,source=source)
        assert same.id==job.id and revalidated.id!=queued.id
        assert revalidated.first_ready_at_utc==queued.first_ready_at_utc
        assert case.repo.creation_narration_source_binding('local',job.id)==binding
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    assert case.repo.get_narration_segment('local',task.id,segments[0].id).audio_reference==segments[0].audio_reference
    assert case.repo.narration_edit_generated('local',job.id)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    if interrupt_final:
        from app.device_scheduler import RecoveryBlocked
        from app.creation.execution_control import CreationExecutionControl
        with pytest.raises(RecoveryBlocked):
            case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
        control=CreationExecutionControl(case.repo,case.execution,SimpleNamespace(valid=lambda *_:False),lambda _:'ready')
        if interrupt_final == 'source_changed':
            conversations.finish_turn(turn['id'], 'stopped', '原目标已停止', {})
            assert case.executor.recover_pcm_unit(job, case.actor, case.scheduler)
            current = case.repo.get_creation_task('local', task.id)
            assert current.current_timeline_version_id == base.id
            assert case.repo.get_job_run('local', job.id).status == 'interrupted'
            assert case.repo.list_edit_operations('local', task.id) == []
            usage = case.repo.creation_narration_usage('local', job.id)
            assert not usage['unsettled'] and usage['used_output_bytes'] == 192096
            reconciled = [event for event in case.repo.list_task_events('local', task.id)
                if event.job_id == job.id and event.event_type == 'narration_edit_result_reconciled']
            assert len(reconciled) == 1 and reconciled[0].payload['publication_blocked']
            assert reconciled[0].payload['pcm']['finalized']
            assert case.repo.narration_edit_generated('local', job.id)
            return
        control.recover(SimpleNamespace(scheduler=case.scheduler,registrations={
            'creation_job':SimpleNamespace(owner=case.owner,executor=case.executor)},wake=lambda:None))
    else:
        case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    current=case.repo.get_creation_task('local',task.id)
    assert current.current_timeline_version_id!=base.id
    assert case.repo.get_job_run('local',job.id).status=='succeeded'
    assert case.repo.get_narration_segment('local',task.id,segments[0].id).audio_reference!=segments[0].audio_reference
    assert case.repo.get_narration_segment('local',task.id,segments[1].id)==segments[1]
    usage=case.repo.creation_narration_usage('local',job.id)
    assert not usage['unsettled'] and usage['used_output_bytes']==192096
    assert len(case.repo.list_edit_operations('local',task.id))==1
    assert wav_reads


def _segment_units_case(tmp_path, monkeypatch):
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    case = _narration_units_case(tmp_path)
    asset, reference = register_synthetic_video_asset(case.repo,workspace_id='local',member_id=case.actor.member_id,
        path=tmp_path/'library/section.mp4',asset_id='section_video',seconds=2)
    class Renderer:
        def render(self, plan, audio_path, **kwargs):
            assert kwargs['scheduler']._get(kwargs['grant'].request_id).resources['kind']=='creation_render'
            assert Path(audio_path).is_file()
            target = Path(kwargs['job_staging_path']); target.mkdir(parents=True,exist_ok=True)
            (target/'preview.mp4').write_bytes(b'controlled-render-port')
            (target/'subtitles.srt').write_text(plan.cues[0].text)
            return plan
    service = SegmentProductionUseCase(case.repo,narration_stage=case.stage,renderer=Renderer(),
        artifact_store=ArtifactStore(tmp_path/'artifacts'),output_root=tmp_path/'temporary')
    def section_plan(task,section_id,audio,owner,*,connection=None,text=None):
        assert connection is None or not connection.in_transaction
        text = text or '原文'
        plan = CreationTaskMontagePlan(id=f'stage_{section_id}',workspace_id='local',task_id=task.id,
            script_id=case.script_id,script_title='section',status='ready',
            cues=[NarrationCue(f'cue_{section_id}',section_id,text,audio.duration_seconds,
                narration_segment_id=case.stage.segment_id(task.id,case.script_id,section_id))],
            clips=[MontageClip(f'clip_{section_id}',f'cue_{section_id}',asset.id,asset.segments[0].id,reference,0,audio.duration_seconds,
                0,audio.duration_seconds,1,['controlled port'],False)],duration_seconds=audio.duration_seconds,
            low_confidence_clip_count=0,created_at=task.created_at,updated_at=task.created_at,
            narration_audio_path=case.repo.narration_artifact_reference(audio.cache_key))
        paths = [Path(audio.audio_path),Path(asset.path)]
        sources = [{'path':str(path),'size':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns,
            'ctime_ns':path.stat().st_ctime_ns} for path in paths]
        with case.repo.creation_execution_read() as conn:
            snapshot = case.repo.read_creation_execution_snapshot('local', task.id, connection=conn)
            checksum = hashlib.sha256()
            for _ in service.planning_inputs(snapshot, section_id, connection=conn, checksum=checksum):
                pass
        return plan,sources,creation_task_montage_plan_to_payload(plan),checksum.hexdigest()
    monkeypatch.setattr(service,'section_plan',section_plan)
    case.owner.cpu_render_limits = CpuRenderLimits(1_000_000,1,30)
    case.owner.segments = case.executor.segments = service
    case.segments = service
    return case


def test_segment_video_checkpoint_precedes_remaining_narration_and_retains_version(tmp_path,monkeypatch):
    case = _segment_units_case(tmp_path,monkeypatch)
    wav_reads=_assert_wav_reads_outside_writer(monkeypatch,case)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    first = case.scheduler.pick_and_grant()
    case.executor.execute(first,case.scheduler)
    second = case.scheduler.pick_and_grant()
    assert second.unit_key.startswith('segment_video:section_1:')
    case.executor.execute(second,case.scheduler)
    context = case.segments.context('local',case.task_id,'section_1',case.actor)
    assert context['video']['kind']=='video'
    assert case.segments.video_path('local',case.task_id,'section_1',context['segment_version'],case.actor).is_file()
    segments = case.repo.list_narration_segments('local',case.task_id,case.script_id)
    assert [item.status for item in segments]==['ready','pending']
    assert case.repo.get_creation_task('local',case.task_id).narration_cache_key is None
    third = case.scheduler.pick_and_grant()
    assert third.unit_key.startswith('narration:segment:v1:section_2:')
    case.executor.execute(third,case.scheduler)
    assert case.segments.context('local',case.task_id,'section_1',case.actor)['video']==context['video']
    assert case.repo.task_budget_projection('local',case.task_id)['actual_cost']['amount'] is None
    for prefix in ('segment_video:section_2:','narration:merge:v1:'):
        grant=case.scheduler.pick_and_grant()
        assert grant.unit_key.startswith(prefix)
        case.executor.execute(grant,case.scheduler)
    assert case.repo.get_creation_task('local',case.task_id).narration_cache_key is not None
    assert wav_reads


def _submit_section_revision(case):
    from app.schemas import NarrationRevisionRequest
    from app.creation.contracts import TaskEvent
    current = case.segments.context('local',case.task_id,'section_1',case.actor)
    text = '原文更紧凑'
    payload = {'request_id':'revision_1','input_id':'turn_revision','section_id':'section_1','script_id':case.script_id,
        'expected_segment_version':current['segment_version'],'base_version_id':current['base_version_id'],
        'dependency_digest':current['dependency_digest'],
        'source_text_sha256':current['source_text_sha256'],'replacement_text_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'replacement_text':text,'protected_rules':[{'id':'selling_point','kind':'exact_text','source_text':'原文',
            'required_text':'原文','source_reference':'source_1'}], 'semantic_review_ref':'review_revision','adoption_mode':'apply'}
    review = {key:value for key,value in payload.items() if key not in {'replacement_text','semantic_review_ref','adoption_mode'}}
    review.update(conclusion='preserved',findings=[{'rule_id':'selling_point','before':'原文','after':'原文',
        'assessment':'preserved','reason':'controlled Pi comparison receipt'}],authorized_locked_section_ids=[],
        source={'assistant_turn_id':'turn_revision','input_id':'turn_revision'},evidence_kind='pi_semantic_comparison')
    case.repo.save_semantic_review(TaskEvent('review_revision','local',case.task_id,None,'narration_semantic_review',review),write_guard=lambda conn:None)
    runtime = SimpleNamespace(scheduler=case.scheduler,wake=lambda:None,registrations={'creation_job':SimpleNamespace(owner=case.owner,executor=case.executor)})
    result = case.segments.submit_revision('local',case.task_id,case.actor,NarrationRevisionRequest.model_validate(payload),runtime=runtime)
    return current,result,runtime


@pytest.mark.parametrize('revalidate_source',[False,True])
def test_segment_rewrite_publishes_only_target_and_replays_original_request(tmp_path,monkeypatch,revalidate_source):
    case = _segment_units_case(tmp_path,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    before,request,runtime = _submit_section_revision(case)
    revision_job=case.repo.get_job_run('local',request['job_id'])
    binding=case.repo.creation_narration_source_binding('local',revision_job.id)
    assert binding.payload['business_input_digest']==revision_job.input_digest
    assert binding.id!=case.repo.creation_narration_source_binding('local',case.job.id).id
    if revalidate_source:
        _replace_fixed_voice_file(SimpleNamespace(**{**vars(case),'job':revision_job}))
        resumed=case.segments.resume_request('local',case.task_id,'revision_1',case.actor,runtime=runtime)
        assert resumed['job_id']==revision_job.id
        assert case.repo.creation_narration_source_binding('local',revision_job.id)==binding
    audio = case.scheduler.pick_and_grant()
    assert audio.unit_key.startswith('segment_revision:')
    case.executor.execute(audio,case.scheduler)
    video = case.scheduler.pick_and_grant()
    assert video is not None, (case.repo.get_job_run('local', request['job_id']).failure_reason, case.repo.list_creation_job_request_states('local',request['job_id']))
    assert video.unit_key.startswith('segment_video:section_1:')
    case.executor.execute(video,case.scheduler)
    receipt = case.segments.read_request('local',case.task_id,'revision_1',case.actor)
    assert receipt['job_id']==request['job_id'] and receipt['status']=='succeeded', case.repo.get_job_run('local',request['job_id']).failure_reason
    current = case.segments.context('local',case.task_id,'section_1',case.actor)
    assert current['text']=='原文更紧凑' and current['segment_version']!=before['segment_version']
    assert case.repo.get_creation_task_script('local',case.script_id).sections[0].content=='原文'*130
    assert case.repo.list_narration_segments('local',case.task_id,case.script_id)[1].status=='pending'
    assert case.segments.video_path('local',case.task_id,'section_1',before['segment_version'],case.actor).is_file()
    assert case.segments.list_segments('local',case.task_id,case.actor)['revision_requests']['items'][0]['status']=='succeeded'
    if revalidate_source:
        from app.creation_execution import migrate_creation_narration_request
        from app.device_scheduler import RecoveryBlocked
        # The initial production has already run; changing its physical source
        # cannot borrow the separate, never-granted rewrite's revalidation.
        with pytest.raises(RecoveryBlocked,match='已有授予'):
            migrate_creation_narration_request(case.repo,case.scheduler,case.job,case.actor)
        assert case.scheduler.pick_and_grant() is None
        return
    for prefix in ('narration:segment:v1:section_2:','segment_video:section_2:','narration:merge:v1:'):
        grant = case.scheduler.pick_and_grant()
        assert grant.unit_key.startswith(prefix)
        case.executor.execute(grant,case.scheduler)
    assert case.repo.get_creation_task('local',case.task_id).narration_cache_key is not None
    assert case.repo.effective_creation_script('local',case.task_id).sections[0].content=='原文更紧凑'


def test_segment_video_failure_resumes_same_unit_without_regenerating_audio(tmp_path,monkeypatch):
    case = _segment_units_case(tmp_path,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    before,request,runtime = _submit_section_revision(case)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    original = case.segments.renderer.render
    def fail(*args,**kwargs): raise ValueError('controlled render failure')
    monkeypatch.setattr(case.segments.renderer,'render',fail)
    video = case.scheduler.pick_and_grant()
    case.executor.execute(video,case.scheduler)
    assert case.segments.read_request('local',case.task_id,'revision_1',case.actor)['status']=='failed'
    assert case.segments.context('local',case.task_id,'section_1',case.actor)['segment_version']==before['segment_version']
    generated = list(case.generated)
    monkeypatch.setattr(case.segments.renderer,'render',original)
    resumed = case.segments.resume_request('local',case.task_id,'revision_1',case.actor,runtime=runtime)
    assert resumed['job_id']==request['job_id']
    grant = case.scheduler.pick_and_grant()
    assert grant.request_id==video.request_id
    case.executor.execute(grant,case.scheduler)
    assert case.generated==generated
    assert case.segments.read_request('local',case.task_id,'revision_1',case.actor)['status']=='succeeded'


@pytest.mark.parametrize('full_timeline',[False,True])
def test_section_undo_redo_preserves_later_other_section_publication(tmp_path,monkeypatch,full_timeline):
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase,TimelineRestoreCommand,TimelineOutputQualification
    from app.creation.montage_stage_use_case import MontageStageUseCase
    from app.montage import creation_task_montage_plan_from_payload
    case = _segment_units_case(tmp_path,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    for _ in range(2): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    before,request,_runtime = _submit_section_revision(case)
    for _ in range(5): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    later = case.repo.stage_segment_state('local',case.task_id,'section_2')
    later_audio = case.repo.list_narration_segments('local',case.task_id,case.script_id)[1]
    edits = MetadataEditUseCase(case.repo,case.segments.artifact_store,output_root=tmp_path/'restore',audio_service=None)
    if full_timeline:
        task = case.repo.get_creation_task('local',case.task_id)
        template = creation_task_montage_plan_from_payload(case.repo.stage_segment_state('local',case.task_id,'section_1')['montage'])
        full = MontageStageUseCase.include_adopted_sections(SimpleNamespace(repository=case.repo),task,
            replace(template,id='full_montage',narration_audio_path=case.repo.narration_artifact_reference(case.repo.get_creation_task('local',case.task_id).narration_cache_key)))
        assert {cue.source_section_id for cue in full.cues}=={'section_1','section_2'}
        case.repo.save_creation_task_montage_plan('local',case.task_id,full.id,creation_task_montage_plan_to_payload(full))
        edits.ensure_montage_base('local',case.repo.get_creation_task('local',case.task_id),full)
    restore = TimelineRestoreUseCase(case.repo,edits)
    for redo,text in [(False,'原文'*130),(True,'原文更紧凑')]:
        task = case.repo.get_creation_task('local',case.task_id)
        prepared = restore.prepare_restore(TimelineRestoreCommand('local',task,redo))
        actual=case.repo.section_dependency_snapshot('local',case.task_id,'section_1')
        if json.dumps(actual,sort_keys=True,default=str)!=json.dumps(prepared.section_dependencies,sort_keys=True,default=str):
            pytest.fail(str({'redo':redo,'audio_actual':actual['timeline'].get('audio_timeline'),
                'audio_expected':prepared.section_dependencies['timeline'].get('audio_timeline')}))
        result = restore.apply_restore(prepared,TimelineOutputQualification(prepared,False))
        assert (result.montage is not None)==full_timeline
        assert case.repo.effective_creation_script('local',case.task_id).sections[0].content==text
        assert case.repo.stage_segment_state('local',case.task_id,'section_2')==later
        assert case.repo.list_narration_segments('local',case.task_id,case.script_id)[1]==later_audio
        assert result.task.output_ready is False and result.task.narration_cache_key is None
        if full_timeline:
            assert {cue.source_section_id for cue in result.montage.cues}=={'section_1','section_2'}
            voices=result.montage.audio_timeline.tracks[0].clips
            assert voices[1].source_ref==later_audio.audio_reference
    assert case.segments.video_path('local',case.task_id,'section_1',before['segment_version'],case.actor).is_file()


def test_named_section_restore_keeps_later_user_edit_and_replays_original_request(tmp_path,monkeypatch):
    from app.creation.editing.metadata_use_case import MetadataEditUseCase,metadata_edit_command
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase,TimelineRestoreCommand
    from app.creation.montage_stage_use_case import MontageStageUseCase
    from app.montage import creation_task_montage_plan_from_payload
    case=_segment_units_case(tmp_path,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    for _ in range(5): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    for row in case.repo.list_creation_job_request_states('local',case.job.id):
        if row['state'] in {'waiting','blocked'}: case.scheduler.cancel(row['id'],case.actor)
    edits=MetadataEditUseCase(case.repo,case.segments.artifact_store,output_root=tmp_path/'restore',audio_service=None)
    task=case.repo.get_creation_task('local',case.task_id)
    template=creation_task_montage_plan_from_payload(case.repo.stage_segment_state('local',case.task_id,'section_1')['montage'])
    full=MontageStageUseCase.include_adopted_sections(SimpleNamespace(repository=case.repo),task,
        replace(template,id='full_before',narration_audio_path=case.repo.narration_artifact_reference(case.repo.get_creation_task('local',case.task_id).narration_cache_key)))
    case.repo.save_creation_task_montage_plan('local',case.task_id,full.id,creation_task_montage_plan_to_payload(full))
    edits.ensure_montage_base('local',task,full)
    _before,request,_runtime=_submit_section_revision(case)
    for _ in range(2): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    task=case.repo.get_creation_task('local',case.task_id)
    a=case.repo.get_edit_operation('local',case.repo.get_timeline_version('local',task.current_timeline_version_id).created_by_operation_id)
    current=creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local',case.task_id))
    second_id=current.clips[1].id
    actor_identity={'member_id':case.actor.member_id,'local_diagnostic':False}
    b=edits.save(task,metadata_edit_command(task,actor_identity,'AdjustVisualEffects',
        {'base_version_id':task.current_timeline_version_id,'clip_ids':[second_id],'effects':{'exposure':.1}}))
    assert b.operation.id != a.id
    assert case.repo.stage_segment_state('local',case.task_id,'section_2')['current_validity']=='invalidated'
    restore=TimelineRestoreUseCase(case.repo,edits)
    seen=case.repo.get_timeline_version('local',b.task.current_timeline_version_id)
    arguments=dict(actor=case.actor,request_id='undo-earlier-body',target_operation_id=a.id,
        expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    undone=restore.execute(TimelineRestoreCommand('local',b.task,False),**arguments)
    assert case.repo.effective_creation_script('local',case.task_id).sections[0].content=='原文'*130
    assert next(clip for clip in undone.montage.clips if clip.id==second_id).visual_effects['exposure']==.1
    assert case.repo.stage_segment_state('local',case.task_id,'section_2')['current_validity']=='invalidated'
    receipt=restore.read_request('local',case.task_id,arguments['request_id'],case.actor)
    assert receipt['status']=='applied' and receipt['target_operation_id']==a.id
    replay=restore.execute(TimelineRestoreCommand('local',b.task,False),**arguments)
    assert replay.operation.id==undone.operation.id and replay.restore_request==receipt
    page=restore.history('local',case.task_id,case.actor,limit=2)
    assert len(page['items'])==2 and page['truncated'] and page['items'][0]['operation_id']==undone.operation.id
    following=restore.history('local',case.task_id,case.actor,limit=2,before_sequence=page['next_before_sequence'])
    assert not {item['id'] for item in page['items']} & {item['id'] for item in following['items']}
    seen=case.repo.get_timeline_version('local',undone.task.current_timeline_version_id)
    redone=restore.execute(TimelineRestoreCommand('local',undone.task,True),actor=case.actor,
        request_id='redo-earlier-body',target_operation_id=a.id,
        expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    assert redone.operation.payload['target_undo_operation_id']==undone.operation.id
    assert next(clip for clip in redone.montage.clips if clip.id==second_id).visual_effects['exposure']==.1
    seen=case.repo.get_timeline_version('local',redone.task.current_timeline_version_id)
    undo_b=restore.execute(TimelineRestoreCommand('local',redone.task,False),actor=case.actor,
        request_id='undo-earlier-color',target_operation_id=b.operation.id,
        expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    from app.visual_effects import VisualEffects
    assert VisualEffects.model_validate(next(clip for clip in undo_b.montage.clips if clip.id==second_id).visual_effects).exposure==0
    assert case.repo.effective_creation_script('local',case.task_id).sections[0].content=='原文更紧凑'
    assert case.segments.context('local',case.task_id,'section_2',case.actor)['adopted']
    seen=case.repo.get_timeline_version('local',undo_b.task.current_timeline_version_id)
    redone=restore.execute(TimelineRestoreCommand('local',undo_b.task,True),actor=case.actor,
        request_id='redo-earlier-color',target_operation_id=b.operation.id,
        expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    assert redone.operation.payload['target_undo_operation_id']==undo_b.operation.id
    assert next(clip for clip in redone.montage.clips if clip.id==second_id).visual_effects['exposure']==.1
    changed=edits.save(redone.task,metadata_edit_command(redone.task,actor_identity,'AdjustVisualEffects',
        {'base_version_id':redone.task.current_timeline_version_id,'clip_ids':[redone.montage.clips[0].id],
            'effects':{'exposure':.2}}))
    seen=case.repo.get_timeline_version('local',changed.task.current_timeline_version_id)
    with pytest.raises(ValueError,match='依赖|冲突'):
        restore.execute(TimelineRestoreCommand('local',changed.task,False),actor=case.actor,
            request_id='conflicting-undo-body',target_operation_id=a.id,
            expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    assert restore.read_request('local',case.task_id,'conflicting-undo-body',case.actor)['status']=='refused'
    assert case.repo.get_creation_task('local',case.task_id).current_timeline_version_id==seen.id


def test_section_restore_uses_actual_before_crop_and_subtitles_without_write_lock_io(tmp_path,monkeypatch):
    from app.creation.contracts import SubtitleTrack,SubtitleCue
    from app.creation.editing.metadata_use_case import MetadataEditUseCase,default_subtitle_style,execution_subtitle_track,metadata_edit_command
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase,TimelineRestoreCommand,TimelineOutputQualification
    from app.creation.montage_stage_use_case import MontageStageUseCase
    from app.montage import creation_task_montage_plan_from_payload
    from app.subtitle_tracks import render_srt
    case=_segment_units_case(tmp_path,monkeypatch)
    publish=case.segments.artifact_store.publish_bytes
    prepared_files=[]
    def no_writer():
        with sqlite3.connect(case.repo.database_path,timeout=.02) as conn:
            conn.execute('BEGIN IMMEDIATE');conn.rollback()
    def checked_publish(reference,content):
        no_writer();prepared_files.append(reference)
        return publish(reference,content)
    monkeypatch.setattr(case.segments.artifact_store,'publish_bytes',checked_publish)
    original_scan=Path.rglob
    def checked_scan(path,pattern):
        if 'segment-staging' in path.parts: no_writer()
        return original_scan(path,pattern)
    monkeypatch.setattr(Path,'rglob',checked_scan)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    for _ in range(5): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    for row in case.repo.list_creation_job_request_states('local',case.job.id):
        if row['state'] in {'waiting','blocked'}: case.scheduler.cancel(row['id'],case.actor)
    edits=MetadataEditUseCase(case.repo,case.segments.artifact_store,output_root=tmp_path/'restore',audio_service=None)
    task=case.repo.get_creation_task('local',case.task_id)
    template=creation_task_montage_plan_from_payload(case.repo.stage_segment_state('local',case.task_id,'section_1')['montage'])
    full=MontageStageUseCase.include_adopted_sections(SimpleNamespace(repository=case.repo),task,
        replace(template,id='full_before',narration_audio_path=case.repo.narration_artifact_reference(case.repo.get_creation_task('local',case.task_id).narration_cache_key)))
    full.clips[0].crop_x=.23
    case.repo.save_creation_task_montage_plan('local',case.task_id,full.id,creation_task_montage_plan_to_payload(full))
    base=edits.ensure_montage_base('local',case.repo.get_creation_task('local',case.task_id),full)
    track=SubtitleTrack('before_source','local',case.task_id,base.id,'zh-CN',None,default_subtitle_style(),
        [SubtitleCue(full.cues[0].id,None,'尚未修改字幕',.1,.85),SubtitleCue(full.cues[1].id,None,'第二段原字幕',1,2)],
        'timeline',None,None,None,'unavailable')
    reference,transform=edits.publish_subtitle_text(workspace_id='local',task_id=case.task_id,proposal_id='before_subtitle',
        kind='subtitle_cue_update',input_refs=[f'timeline:{base.id}'],content=render_srt(execution_subtitle_track(track)),
        engine='subtitle-srt/v1',parameters={})
    case.repo.create_subtitle_track(replace(track,artifact_ref=reference,transform_id=transform,status='valid'))
    # A real subtitle edit invalidates P before a later body revision produces Q.
    edits.save(case.repo.get_creation_task('local',case.task_id),metadata_edit_command(
        case.repo.get_creation_task('local',case.task_id),
        {'member_id':case.actor.member_id,'local_diagnostic':False},'UpdateSubtitleCue',
        {'base_version_id':base.id,'cue_id':full.cues[0].id,'text':'修订前人工字幕'}))
    old_publication=case.repo.stage_segment_state('local',case.task_id,'section_1')
    assert old_publication['current_validity']=='invalidated'
    translation=replace(track,id='before_translation',language='en-US',source_language='zh-CN',generation_method='translation',
        timeline_version_id=case.repo.get_creation_task('local',case.task_id).current_timeline_version_id,
        cues=[SubtitleCue('en_first',full.cues[0].id,'Original first',.1,.85),
            SubtitleCue('en_second',full.cues[1].id,'Original second',1,2)])
    translated_ref,translated_transform=edits.publish_subtitle_text(workspace_id='local',task_id=case.task_id,
        proposal_id='before_translation',kind='subtitle_cue_update',input_refs=[f'timeline:{base.id}'],
        content=render_srt(execution_subtitle_track(translation)),engine='subtitle-srt/v1',parameters={})
    case.repo.create_subtitle_track(replace(translation,artifact_ref=translated_ref,transform_id=translated_transform,status='valid'))
    before,request,_runtime=_submit_section_revision(case)
    for _ in range(2): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    assert case.repo.get_job_run('local',request['job_id']).status=='succeeded'
    current=case.repo.get_creation_task('local',case.task_id)
    revised=case.repo.get_timeline_version('local',current.current_timeline_version_id)
    assert next(item for item in case.repo.list_subtitle_tracks('local',case.task_id,timeline_version_id=revised.id)
        if item.language=='zh-CN').cues[0].text=='原文更紧凑'
    payload=json.loads(json.dumps(revised.snapshot,default=str))
    payload['montage']['clips'][1]['crop_y']=.31
    later=TimelineVersion('later_other_section','local',case.task_id,revised.id,revised.sequence+1,payload,None)
    case.repo.create_timeline_version(later,expected_current_version_id=revised.id)
    rows=case.repo.list_subtitle_tracks('local',case.task_id,timeline_version_id=revised.id)
    source_track=next(item for item in rows if item.language=='zh-CN')
    later_track=replace(source_track,id='later_source',timeline_version_id=later.id,
        cues=[replace(cue,text='第二段后来字幕') if cue.id==full.cues[1].id else cue for cue in source_track.cues])
    case.repo.create_subtitle_track(later_track)
    invalid_translation=replace(next(item for item in rows if item.language=='en-US'),id='later_translation',
        timeline_version_id=later.id,status='unavailable',artifact_ref=None,transform_id=None,
        failure_reason='第二段译文尚待复核',impact='后续片段没有通过语义检查',repair_action='只复核第二段译文后再生成字幕')
    case.repo.create_subtitle_track(invalid_translation)
    restore=TimelineRestoreUseCase(case.repo,edits)
    for redo in (False,True):
        prepared=restore.prepare_restore(TimelineRestoreCommand('local',case.repo.get_creation_task('local',case.task_id),redo))
        actual=case.repo.section_dependency_snapshot('local',case.task_id,'section_1')
        assert json.dumps(actual,sort_keys=True,default=str)==json.dumps(prepared.section_dependencies,sort_keys=True,default=str), (
            redo,actual['subtitles'],prepared.section_dependencies['subtitles'])
        outcome=restore.apply_restore(prepared,TimelineOutputQualification(prepared,False))
        assert case.repo.stage_segment_state('local',case.task_id,'section_1')['current_validity']==('current' if redo else 'invalidated')
        assert case.segments.context('local',case.task_id,'section_1',case.actor)['adopted'] is redo
        assert outcome.montage.clips[0].crop_x==(.5 if redo else .23)
        assert outcome.montage.clips[1].crop_y==.31
        tracks=case.repo.list_subtitle_tracks('local',case.task_id,timeline_version_id=outcome.task.current_timeline_version_id)
        source_track=next(item for item in tracks if item.language=='zh-CN')
        assert source_track.cues[0].text==('原文更紧凑' if redo else '修订前人工字幕')
        assert source_track.cues[1].text=='第二段后来字幕'
        assert source_track.status=='valid' and case.segments.artifact_store.resolve_file(source_track.artifact_ref).is_file()
        translated=next(item for item in tracks if item.language=='en-US')
        assert translated.status=='unavailable' and translated.artifact_ref is None and translated.transform_id is None
        assert (translated.failure_reason,translated.impact,translated.repair_action)==(
            invalid_translation.failure_reason,invalid_translation.impact,invalid_translation.repair_action)
    assert prepared_files


@pytest.mark.parametrize('changed',['unrelated_timeline','target_audio','input'])
def test_segment_revision_checks_actual_target_dependencies_and_semantic_input(tmp_path,monkeypatch,changed):
    from app.creation.contracts import TaskEvent
    from app.schemas import NarrationRevisionRequest
    case = _segment_units_case(tmp_path,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    _before,request,_runtime = _submit_section_revision(case)
    event = case.repo.get_task_event('local',f'event_{request["job_id"]}_start')
    command = NarrationRevisionRequest.model_validate(event.payload['command'])
    queued=case.repo.list_creation_job_request_states('local',request['job_id'])[0]
    with case.repo._connect() as conn:
        running_check=case.segments.capture_local(case.scheduler._get(queued['id']),conn,case.owner)
    if changed=='unrelated_timeline':
        current = case.repo.get_creation_task('local',case.task_id)
        base = case.repo.get_timeline_version('local',current.current_timeline_version_id)
        case.repo.create_timeline_version(TimelineVersion('unrelated_version','local',case.task_id,base.id,
            base.sequence+1,base.snapshot,None),expected_current_version_id=base.id)
    elif changed=='target_audio':
        segments=case.repo.list_narration_segments('local',case.task_id,case.script_id)
        case.repo.save_narration_segments('local',case.task_id,case.script_id,
            [replace(row,pronunciation_version='changed') if row.source_section_id=='section_1' else row for row in segments])
    else:
        review = case.repo.get_task_event('local','review_revision')
        case.repo.save_semantic_review(TaskEvent('different_input','local',case.task_id,None,'narration_semantic_review',
            {**review.payload,'input_id':'another_input'}),write_guard=lambda conn:None)
        command = command.model_copy(update={'semantic_review_ref':'different_input'})
    if changed=='unrelated_timeline':
        case.segments.verify_revision('local',case.task_id,case.actor,command)
        with case.repo._connect() as conn:
            assert running_check(conn)=='ready'
    else:
        with pytest.raises(ValueError):
            case.segments.verify_revision('local',case.task_id,case.actor,command)
        if changed=='target_audio':
            with case.repo._connect() as conn:
                assert running_check(conn)=='stale'


@pytest.mark.parametrize('original,replacement,expected',[
    ('售价120元','售价20元','rejected'),('售价20元','售价120元','rejected'),('售价20元','仍为20元','passed')])
def test_revision_number_rule_binds_whole_original_value(original,replacement,expected):
    from app.creation.editing.narration_preview_use_case import inspect_narration_revision
    from app.schemas import NarrationProtectedRule
    rule = NarrationProtectedRule(id='price',kind='number',source_text=original,required_text='20',source_reference='price')
    assert inspect_narration_revision(original,replacement,[rule])['status']==expected


def test_segment_revision_http_adapter_checks_trusted_source_before_atomic_effect(tmp_path,monkeypatch):
    from starlette.requests import Request
    from app.creation.contracts import TaskEvent
    case = _segment_units_case(tmp_path,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    _before,original,runtime = _submit_section_revision(case)
    command = case.repo.get_task_event('local',f'event_{original["job_id"]}_start').payload['command']
    review = case.repo.get_task_event('local','review_revision')
    origin = {'conversation_id':'conversation_revision','input_id':None}
    source = {**review.payload['source'],'conversation_id':origin['conversation_id']}
    case.repo.save_semantic_review(TaskEvent('http_review','local',case.task_id,None,'narration_semantic_review',
        {**review.payload,'source':source}),write_guard=lambda conn:None)
    payload = {**command,'request_id':'http_revision','semantic_review_ref':'http_review','assistant_turn_id':'turn_revision'}
    checks,effects = [],[]
    current_origin = dict(origin)
    def current(conn,turn_id,ws,member_id,task_id):
        assert conn.in_transaction and (turn_id,ws,member_id,task_id)==('turn_revision','local',case.actor.member_id,case.task_id)
        checks.append(('source',id(conn)))
        return current_origin
    verify = case.segments.verify_revision
    def verified(*args,**kwargs):
        result = verify(*args,**kwargs)
        if kwargs.get('connection') is not None:
            checks.append(('version',id(kwargs['connection'])))
        return result
    def effect(*args,connection,**kwargs):
        assert checks[-2:]==[('source',id(connection)),('version',id(connection))]
        assert connection.in_transaction
        effects.append(kwargs['request_id'])
    monkeypatch.setattr(main_module,'production_repo',case.repo)
    monkeypatch.setattr(main_module,'conversation_repo',SimpleNamespace(_task_turn_source=current,record_input_business_effect=effect))
    monkeypatch.setattr(main_module,'current_workspace_id',lambda _request:'local')
    monkeypatch.setattr(main_module,'_require_automation_task_open',lambda *_args:None)
    monkeypatch.setattr(main_module,'_require_permit_actor',lambda *_args:case.actor)
    monkeypatch.setattr(main_module,'_assistant_creation_adoption_scope',lambda *_args,**_kwargs:origin)
    monkeypatch.setattr(main_module,'_segment_production_use_case',lambda:case.segments)
    monkeypatch.setattr(main_module,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(case.segments,'verify_revision',verified)
    request = Request({'type':'http','method':'POST','path':'/','headers':[]})
    result = main_module.revise_creation_task_segment(case.task_id,'section_1',payload,request)
    assert result['request_id']=='http_revision' and effects==['http_revision']
    with pytest.raises(main_module.HTTPException) as extra:
        main_module.revise_creation_task_segment(case.task_id,'section_1',{**payload,'request_id':'extra','untrusted':True},request)
    assert extra.value.status_code==409
    case.repo.save_semantic_review(TaskEvent('other_turn_review','local',case.task_id,None,'narration_semantic_review',
        {**review.payload,'source':{**source,'assistant_turn_id':'other_turn'}}),write_guard=lambda conn:None)
    with pytest.raises(main_module.HTTPException) as mismatched:
        main_module.revise_creation_task_segment(case.task_id,'section_1',
            {**payload,'request_id':'other_source','semantic_review_ref':'other_turn_review'},request)
    assert mismatched.value.status_code==409
    current_origin['input_id']='new_input'
    with pytest.raises(main_module.HTTPException) as stale:
        main_module.revise_creation_task_segment(case.task_id,'section_1',{**payload,'request_id':'stale_input'},request)
    assert stale.value.status_code==409 and effects==['http_revision']
    assert case.repo.find_segment_revision_request('local',case.task_id,case.actor.member_id,'stale_input') is None


def test_narration_units_commit_release_restore_and_merge_on_original_job(tmp_path):
    case = _narration_units_case(tmp_path)
    first = submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    request = case.scheduler._get(first)
    assert request.resources['section_count'] == 1 and request.resources['text_chars'] < 374
    case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    segments = case.repo.list_narration_segments('local', case.task_id, case.script_id)
    assert [item.status for item in segments] == ['ready', 'pending']
    assert case.repo.get_creation_task('local', case.task_id).narration_cache_key is None
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_seconds'] > 0 and usage['used_output_bytes'] > 0 and not usage['unsettled']
    assert case.scheduler._get(first).state == 'done'
    # A fresh coordinator and executor consume persisted usage and only the next section.
    case.scheduler = DeviceScheduler(case.repo.database_path, tmp_path/'units.lock', owners=case.owner,
        resources=case.resources, processes=HostProcessProbe())
    case.executor = CreationJobUnitExecutor(case.repo, render_action=lambda *_: None,
        render_is_valid=lambda *_: False, narration_stage=case.stage)
    second = case.scheduler.pick_and_grant()
    assert second.unit_key.startswith('narration:segment:v1:section_2:')
    case.executor.execute(second, case.scheduler)
    assert case.generated[1][2] == 3*1024*1024 - usage['used_output_bytes']
    assert case.generated[1][3] < 300 - usage['used_seconds'] + .01
    merge = case.scheduler.pick_and_grant()
    assert merge.unit_key.startswith('narration:merge:v1:')
    assert case.scheduler._get(merge.request_id).resources['kind'] == 'task_audio'
    case.executor.execute(merge, case.scheduler)
    current = case.repo.get_creation_task('local', case.task_id)
    assert current.stage == 'montage' and current.narration_cache_key is not None
    assert len(case.execution.jobs('local', case.task_id)) == 1
    final = case.repo.creation_narration_usage('local', case.job.id)
    assert not final['unsettled'] and final['used_seconds'] > usage['used_seconds']
    assert final['used_output_bytes'] == sum(Path(item.audio_path).stat().st_size for item in [
        case.repo.get_narration_artifact('local', current.narration_cache_key), *[
            case.repo.get_narration_artifact('local', s.audio_reference.removeprefix('narration://artifact/'))
            for s in case.repo.list_narration_segments('local', case.task_id, case.script_id)]])
    with case.repo._connect() as conn:
        queued = conn.execute("SELECT unit_key FROM device_work_requests WHERE state='waiting'").fetchall()
    assert len(queued) == 1 and queued[0]['unit_key'].startswith('montage_plan:')


def _queued_narration_voice_selection_case(tmp_path):
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from uuid import uuid4
    case = _narration_units_case(tmp_path)
    register_synthetic_profiles(case.repo, workspace_id='local', member_id=case.actor.member_id,
        person_id='person_2', voice_id='voice_2', voice_version='v1', language='zh', model_id='test')
    task = case.repo.get_creation_task('local', case.task_id)
    def verify(identity):
        assert identity == TaskAuthorizationUseCase.identity(case.actor)
        # A real writer can enter while the external identity port is observed.
        with case.repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        return 'ready'
    case.authorizations = TaskAuthorizationUseCase(case.repo, verify)
    case.original_authorization = case.authorizations.ensure(task, case.actor)
    case.owner.task_authorizations = case.authorizations
    case.request_id = submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    plan = case.repo.get_creation_plan('local', task.plan_id)
    case.control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    case.runtime = SimpleNamespace(scheduler=case.scheduler,
        registrations={'creation_job': SimpleNamespace(owner=case.owner,executor=case.executor)}, wake=lambda: None, last_error=None)
    case.selection = dict(workspace_id='local', task_id=case.task_id, member_id=case.actor.member_id,
        request_id=str(uuid4()), voice_profile_id='voice_2', voice_version='v1',
        expected_plan_updated_at=plan.updated_at, expected_task_updated_at=task.updated_at)
    return case


def test_queued_voice_selection_rebinds_original_job_and_request_once_without_budget_reset(tmp_path):
    case = _queued_narration_voice_selection_case(tmp_path)
    start = case.repo.get_task_event('local', f'event_{case.job.id}_start')
    usage = case.repo.creation_narration_usage('local', case.job.id)
    old = case.scheduler._get(case.request_id)
    receipt = case.control.select_narration_voice(runtime=case.runtime, actor=case.actor, **case.selection)
    authorization = case.repo.get_task_authorization('local', case.task_id)
    assert authorization.resource_scope == {**case.original_authorization.resource_scope, 'voices': ['voice_2:v1']}
    assert replace(authorization, resource_scope=case.original_authorization.resource_scope,
        revision=case.original_authorization.revision, last_checked_at=case.original_authorization.last_checked_at,
        last_verdict=case.original_authorization.last_verdict) == case.original_authorization
    job = case.repo.get_job_run('local', case.job.id)
    assert job.input_digest != case.job.input_digest and job.status == 'queued' and job.attempt == 0
    assert job.idempotency_key == case.job.idempotency_key and job.created_at == case.job.created_at
    assert case.repo.get_task_event('local', start.id) == start
    effective = case.repo.creation_execution_input('local', job.id)
    source = case.repo.creation_narration_source_binding('local', job.id)
    assert effective == {**start.payload, 'input_digest': job.input_digest, 'source_binding_event_id': source.id}
    assert source.id != start.payload['source_binding_event_id']
    assert source.payload['voice_id'] == 'voice_2' and source.payload['basis'] == 'current_bytes_at_adoption'
    assert case.repo.creation_narration_usage('local', job.id) == usage
    replaced = case.scheduler._get(old.id)
    assert replaced.state == 'cancelled' and replaced.wait_reason.startswith('replaced_by:')
    new = case.scheduler._get(replaced.wait_reason.split(':', 1)[1])
    assert new.owner_id == job.id and new.member_id == old.member_id and new.input_digest == job.input_digest
    assert new.first_ready_at_utc == old.first_ready_at_utc and new.resources['voice_profile_id'] == 'voice_2'
    assert new.resources['script_id'] == case.script_id
    assert new.resources['parent_max_output_bytes'] == old.resources['parent_max_output_bytes']
    assert new.resources['parent_timeout_seconds'] == old.resources['parent_timeout_seconds']
    with case.repo._connect() as conn:
        assert case.owner.check_current(old, conn) == 'stale'
        assert case.owner.check_current(new, conn) == 'ready'
        assert conn.execute("SELECT COUNT(*) FROM device_work_requests WHERE state IN ('waiting','blocked')").fetchone()[0] == 1
    grant = case.scheduler.pick_and_grant()
    assert grant.request_id == new.id
    case.executor.execute(grant, case.scheduler)
    segment = case.repo.list_narration_segments('local', case.task_id, case.script_id)[0]
    assert segment.status == 'ready' and segment.voice_profile_id == 'voice_2'
    after = case.repo.creation_narration_usage('local', job.id)
    assert after['used_output_bytes'] > 0 and not after['unsettled']
    assert after['budget']['parent_max_output_bytes'] == new.resources['parent_max_output_bytes']
    with case.repo._connect() as conn:
        count = conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0]
    assert case.control.select_narration_voice(runtime=case.runtime, actor=case.actor, **case.selection) == receipt
    assert case.repo.creation_narration_usage('local', job.id) == after
    assert len(case.execution.jobs('local', case.task_id)) == 1
    with case.repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == count
    assert case.control.read_bound('local', case.task_id, job.id, case.actor, verify_media=False)['job']['id'] == job.id


@pytest.mark.parametrize('blocked', ['granted', 'stale_version', 'unknown', 'replacement_failure', 'authorization_changed'])
def test_queued_voice_selection_refuses_races_or_unknown_and_rolls_back_all_writes(tmp_path, monkeypatch, blocked):
    from app.creation.contracts import TaskEvent
    from app.device_scheduler import RecoveryBlocked
    import sqlite3
    case = _queued_narration_voice_selection_case(tmp_path)
    if blocked == 'granted':
        assert case.scheduler.pick_and_grant().request_id == case.request_id
    elif blocked == 'stale_version':
        case.selection['expected_plan_updated_at'] = '2020-01-01T00:00:00+00:00'
    elif blocked == 'unknown':
        active = f'event_{case.job.id}_narration_started_unknown'
        case.repo.append_task_event(TaskEvent(active, 'local', case.task_id, case.job.id,
            'creation_narration_unit_started', {'usage_id': 'unknown-original-unit'}))
        case.repo.append_task_event(TaskEvent(f'event_{case.job.id}_narration_budget', 'local', case.task_id, case.job.id,
            'creation_narration_budget', {'active_event_id': active, 'settled_event_id': None,
                'parent_timeout_seconds': 300, 'parent_max_output_bytes': 3*1024*1024}))
    elif blocked == 'replacement_failure':
        with case.repo._connect() as conn:
            conn.execute("CREATE TRIGGER fail_voice_replacement BEFORE INSERT ON device_work_requests "
                "BEGIN SELECT RAISE(ABORT,'replacement failure'); END")
    tables = ('creation_tasks', 'creation_plans', 'creation_task_scripts', 'job_runs', 'task_authorizations',
        'task_events', 'device_work_requests', 'device_request_events', 'device_permit')
    def snapshot():
        with case.repo._connect() as conn:
            return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in tables}
    before = snapshot()
    if blocked == 'authorization_changed':
        select = case.repo.select_creation_task_narration_voice
        def revoke_between_observation_and_commit(**kwargs):
            nonlocal before
            current = case.repo.get_task_authorization('local', case.task_id)
            case.repo.save_task_authorization(replace(current, revision=current.revision + 1,
                revoked_at=datetime.now(timezone.utc), last_verdict='unauthorized'), expected_revision=current.revision)
            before = snapshot()  # This independent revocation must survive the failed selection.
            return select(**kwargs)
        monkeypatch.setattr(case.repo, 'select_creation_task_narration_voice', revoke_between_observation_and_commit)
    with pytest.raises((ValueError, RecoveryBlocked, sqlite3.IntegrityError)):
        case.control.select_narration_voice(runtime=case.runtime, actor=case.actor, **case.selection)
    assert snapshot() == before
    assert case.repo.get_job_run('local', case.job.id).input_digest == case.job.input_digest
    assert len(case.execution.jobs('local', case.task_id)) == 1


def test_narration_three_inputs_checkpoint_failed_merge_and_resume_on_original_job(tmp_path, monkeypatch):
    from app.creation.execution_control import CreationExecutionControl
    case = _narration_units_case(tmp_path, section_count=3, audio_input_count=2)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(3):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    sections = case.repo.list_narration_segments('local', case.task_id, case.script_id)
    section_keys = [s.audio_reference.removeprefix('narration://artifact/') for s in sections]
    first = case.scheduler.pick_and_grant()
    declared = case.scheduler._get(first.request_id).resources
    assert declared['input_count'] == 2 and not declared['merge_final']
    case.executor.execute(first, case.scheduler)
    checkpoint = case.repo.creation_narration_merge_checkpoint('local', case.job.id)
    partial_path=Path(checkpoint['pcm']['work_path'])
    assert checkpoint['section_ids'] == ['section_1', 'section_2'] and checkpoint['pcm']['pcm_bytes']/48000 == 2
    task = case.repo.get_creation_task('local', case.task_id)
    assert task.stage == 'narration' and task.narration_cache_key is None
    consumed = case.repo.creation_narration_usage('local', case.job.id)
    assert consumed['used_output_bytes'] == 3*48044 + partial_path.stat().st_size

    # A fresh coordinator consumes the saved prefix; a known final-unit failure retains it.
    case.scheduler = DeviceScheduler(case.repo.database_path, tmp_path/'units.lock', owners=case.owner,
        resources=case.resources, processes=HostProcessProbe())
    case.executor = CreationJobUnitExecutor(case.repo, render_action=lambda *_: None,
        render_is_valid=lambda *_: False, narration_stage=case.stage)
    final = case.scheduler.pick_and_grant()
    resources = case.scheduler._get(final.request_id).resources
    assert resources['merge_final'] and resources['input_count'] == 2
    assert [item['reference'] for item in resources['inputs']] == [str(partial_path), sections[2].audio_reference]
    original=case.stage.prepare_merge
    def fail_final(*args,**kwargs):
        raise RuntimeError('injected final merge failure before output')
    monkeypatch.setattr(case.stage,'prepare_merge',fail_final)
    case.executor.execute(final, case.scheduler)
    assert case.repo.get_job_run('local', case.job.id).status == 'failed'
    assert case.repo.creation_narration_merge_checkpoint('local', case.job.id) == checkpoint
    failed_usage = case.repo.creation_narration_usage('local', case.job.id)
    assert failed_usage['used_output_bytes'] == consumed['used_output_bytes'] and not failed_usage['unsettled']
    monkeypatch.setattr(case.stage,'prepare_merge',original)
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={'creation_job': SimpleNamespace(owner=case.owner,executor=case.executor)}, wake=lambda: None)
    control.resume('local', case.task_id, case.actor, runtime, verify_media=False)
    retried = case.scheduler.pick_and_grant()
    assert retried.request_id == final.request_id and retried.owner_id == case.job.id
    case.executor.execute(retried, case.scheduler)
    task = case.repo.get_creation_task('local', case.task_id)
    output = case.repo.get_narration_artifact('local', task.narration_cache_key)
    assert task.stage == 'montage' and output.duration_seconds == 3
    assert case.merged == [section_keys[:2], [str(partial_path), section_keys[2]]]
    assert len(case.generated) == 3 and len(case.execution.jobs('local', case.task_id)) == 1
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_seconds'] > failed_usage['used_seconds'] and not usage['unsettled']
    assert usage['used_output_bytes'] == consumed['used_output_bytes'] + 48008
    assert case.repo.list_narration_segments('local', case.task_id, case.script_id) == sections


def test_narration_partial_merge_exhausts_parent_without_refund_or_reset(tmp_path):
    from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
    case = _narration_units_case(tmp_path, section_count=3, audio_input_count=2)
    cap = 3*48044 + 96044
    case.owner.narration_budget = replace(case.owner.narration_budget, max_output_bytes=cap)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(4):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    checkpoint = case.repo.creation_narration_merge_checkpoint('local', case.job.id)
    assert checkpoint['section_ids'] == ['section_1', 'section_2']
    partial_path=Path(checkpoint['pcm']['work_path'])
    assert partial_path.is_file()
    assert case.repo.get_job_run('local', case.job.id).status == 'failed'
    task = case.repo.get_creation_task('local', case.task_id)
    assert task.stage == 'narration' and task.narration_cache_key is None and not task.stage_retryable
    assert all(s.status == 'ready' for s in case.repo.list_narration_segments('local', case.task_id, case.script_id))
    # Removing a charged intermediate cannot refund the original cumulative allowance.
    partial_path.unlink()
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_output_bytes'] == cap and not usage['unsettled']
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={'creation_job': SimpleNamespace(owner=case.owner,executor=case.executor)}, wake=lambda: None)
    with pytest.raises(ExecutionControlRejected, match='不能|核对'):
        control.resume('local', case.task_id, case.actor, runtime, verify_media=False)
    assert len(case.execution.jobs('local', case.task_id)) == 1 and case.scheduler.pick_and_grant() is None


@pytest.mark.parametrize('changed', ['source', 'partial'])
def test_narration_merge_checkpoint_rejects_changed_actual_dependency(tmp_path, changed):
    from app.device_scheduler import RecoveryBlocked
    case = _narration_units_case(tmp_path, section_count=3, audio_input_count=2)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(4):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    checkpoint = case.repo.creation_narration_merge_checkpoint('local', case.job.id)
    if changed=='partial':
        Path(checkpoint['pcm']['work_path']).write_bytes(b'changed dependency')
        request=case.scheduler._get(next(row['id'] for row in case.repo.list_creation_job_request_states('local',case.job.id) if row['state']=='waiting'))
        with case.repo._connect() as conn:
            watcher=case.owner.capture_local(request,conn)
            assert watcher(conn)=='stale'
    else:
        artifact=case.repo.get_narration_artifact('local',checkpoint['input_references'][0].removeprefix('narration://artifact/'))
        Path(artifact.audio_path).write_bytes(b'changed dependency')
        with case.repo._connect() as conn:
            job,task,_event,script,plan=case.owner._facts(conn,'local',case.job.id)
            with pytest.raises(RecoveryBlocked,match='检查点'):
                case.owner.narration_unit_facts(conn,job,task,script,plan)
    assert len(case.generated) == 3 and len(case.merged) == 1
    assert not case.repo.creation_narration_usage('local', case.job.id)['unsettled']


def test_narration_unit_rollback_and_explicit_resume_preserve_consumed_allowance(tmp_path, monkeypatch):
    from app.creation.execution_control import CreationExecutionControl
    case = _narration_units_case(tmp_path)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    grant = case.scheduler.pick_and_grant()
    original_commit = case.repo.commit_creation_narration_segment
    def refused(*args, **kwargs):
        original_commit(*args, **kwargs)
        raise RuntimeError('injected segment commit failure')
    monkeypatch.setattr(case.repo, 'commit_creation_narration_segment', refused)
    case.executor.execute(grant, case.scheduler)
    assert case.scheduler._get(grant.request_id).state == 'failed'
    assert all(s.status == 'pending' for s in case.repo.list_narration_segments('local', case.task_id, case.script_id))
    consumed = case.repo.creation_narration_usage('local', case.job.id)
    assert consumed['used_output_bytes'] > 0 and not consumed['unsettled']
    monkeypatch.setattr(case.repo, 'commit_creation_narration_segment', original_commit)
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={'creation_job': SimpleNamespace(owner=case.owner,executor=case.executor)}, wake=lambda: None)
    control.resume('local', case.task_id, case.actor, runtime, verify_media=False)
    retried = case.scheduler.pick_and_grant()
    assert retried.request_id == grant.request_id and retried.attempt_number == 2
    case.executor.execute(retried, case.scheduler)
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.generated[-1][2] == 3*1024*1024-consumed['used_output_bytes']
    after = case.repo.creation_narration_usage('local', case.job.id)
    assert after['used_output_bytes'] == consumed['used_output_bytes']  # Retry reused the already charged file.
    assert after['used_seconds'] > consumed['used_seconds']


def test_narration_legacy_static_refusal_migrates_idempotently_without_new_job(tmp_path):
    from app.creation_execution import creation_stage_facts, migrate_creation_narration_request
    from app.device_scheduler import SubmissionIntent, SchedulePolicy
    from app.host_resource_probes import HostResourceDecision
    case = _narration_units_case(tmp_path)
    task = case.repo.get_creation_task('local', case.task_id)
    script = case.repo.get_creation_task_script('local', case.script_id)
    plan = case.repo.get_creation_plan('local', task.plan_id)
    key, resources = creation_stage_facts(case.repo, task, script, plan, 'narration', case.owner.narration_budget, None)
    request = case.scheduler.submit(SubmissionIntent('local', 'creation_job', case.job.id, case.job.input_digest, key, SchedulePolicy()), case.actor)
    decision = HostResourceDecision(False, resources, {'text_chars': {'actual': resources['text_chars'], 'maximum': 374, 'passed': False}},
        'declared_resources_exceed_budget')
    with case.scheduler._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert case.owner.record_admission(conn, request, decision, datetime.now(timezone.utc)) == 'input_rejected'
        conn.execute("UPDATE device_work_requests SET state='failed',last_failure_reason='declared_resources_exceed_budget' WHERE id=?", (request.id,))
    replacement = migrate_creation_narration_request(case.repo, case.scheduler, case.repo.get_job_run('local', case.job.id), case.actor)
    repeated = migrate_creation_narration_request(case.repo, case.scheduler, case.repo.get_job_run('local', case.job.id), case.actor)
    assert replacement.id == repeated.id and replacement.first_ready_at_utc == request.first_ready_at_utc
    assert case.scheduler._get(request.id).wait_reason == f'replaced_by:{replacement.id}'
    assert case.repo.get_job_run('local', case.job.id).status == 'queued'
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert any(e.payload.get('failure_kind') == 'input_budget_exceeded' for e in case.repo.list_task_events('local', case.task_id))


def test_narration_quit_settles_then_cached_resume_does_not_charge_output_twice(tmp_path, monkeypatch):
    from datetime import timedelta
    case = _narration_units_case(tmp_path)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    original = case.stage.prepare_segment
    def quit_after_prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        case.executor.request_stop()
        return result
    monkeypatch.setattr(case.stage, 'prepare_segment', quit_after_prepare)
    grant = case.scheduler.pick_and_grant()
    case.executor.execute(grant, case.scheduler)
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['consumption_known'] and usage['used_output_bytes'] > 0 and usage['used_seconds'] > 0
    assert case.scheduler._get(grant.request_id).state == 'blocked'
    assert case.repo.get_job_run('local', case.job.id).status == 'interrupted'
    monkeypatch.setattr(case.stage, 'prepare_segment', original)
    case.scheduler = DeviceScheduler(case.repo.database_path, tmp_path/'units.lock', owners=case.owner,
        resources=case.resources, processes=HostProcessProbe(), clock=lambda: datetime.now(timezone.utc)+timedelta(seconds=2))
    case.executor = CreationJobUnitExecutor(case.repo, render_action=lambda *_: None,
        render_is_valid=lambda *_: False, narration_stage=case.stage)
    next_grant = case.scheduler.pick_and_grant()
    assert next_grant.request_id == grant.request_id
    case.executor.execute(next_grant, case.scheduler)
    after = case.repo.creation_narration_usage('local', case.job.id)
    assert after['used_output_bytes'] == usage['used_output_bytes']
    assert after['used_seconds'] > usage['used_seconds'] and after['consumption_known']
    assert case.repo.list_narration_segments('local', case.task_id, case.script_id)[0].status == 'ready'


def test_narration_unknown_usage_blocks_redispatch_without_reset(tmp_path, monkeypatch):
    from datetime import timedelta
    from app.device_scheduler import RecoveryBlocked
    from app.creation_execution import next_creation_intent
    case = _narration_units_case(tmp_path)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    grant = case.scheduler.pick_and_grant()
    def unknown(*args, **kwargs):
        kwargs['grant_context'].narration_output_usage[0] = None
        raise RecoveryBlocked('simulated unknown output accounting')
    monkeypatch.setattr(case.stage, 'prepare_segment', unknown)
    with pytest.raises(RecoveryBlocked, match='unknown output'):
        case.executor.execute(grant, case.scheduler)
    # This port proves release only; it does not invent a usage settlement.
    case.scheduler.defer_unit(grant, reason='worker_interrupted', retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=1),
        owner_database_path=case.repo.database_path, owner_write=lambda conn, current: case.executor._write_interrupted(
            conn, current, scheduler=case.scheduler, stage='narration', reason='process_interrupted'))
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert not usage['consumption_known'] and len(usage['unsettled']) == 1
    with pytest.raises(RecoveryBlocked, match='消耗未知'):
        next_creation_intent(case.repo, case.scheduler, case.repo.get_job_run('local', case.job.id))
    case.scheduler.clock = lambda: datetime.now(timezone.utc)+timedelta(seconds=2)
    assert case.scheduler.pick_and_grant() is None
    assert len(case.execution.jobs('local', case.task_id)) == 1


def test_narration_explicit_scope_parser_preserves_numeric_caps(tmp_path):
    from app.member_permits import creation_stage_budgets_from_env
    from app.creation_execution import next_creation_intent
    case = _narration_units_case(tmp_path)
    expected = case.owner.narration_budget
    env = {'DEVICE_NARRATION_'+key.upper(): str(value) for key, value in vars(expected).items()
        if key not in {'running_cpu_policy', 'input_scope'}}
    parsed, montage = creation_stage_budgets_from_env(env)
    assert parsed.input_scope == 'legacy_stage' and parsed.max_text_chars == 374 and montage is None
    case.owner.narration_budget = parsed
    with pytest.raises(ValueError, match='明确执行范围'):
        next_creation_intent(case.repo, case.scheduler, case.job)
    env['DEVICE_NARRATION_INPUT_SCOPE'] = 'single_call_experiment_v1'
    parsed, _ = creation_stage_budgets_from_env(env)
    assert parsed == expected and parsed.max_timeout_seconds == 300 and parsed.max_output_bytes == 3*1024*1024


def test_narration_exhausted_parent_keeps_ready_segment_and_cannot_reset(tmp_path):
    from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
    case = _narration_units_case(tmp_path)
    one_output = 48044
    case.owner.narration_budget = replace(case.owner.narration_budget, max_output_bytes=one_output)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    grant = case.scheduler.pick_and_grant()
    case.executor.execute(grant, case.scheduler)
    assert case.repo.get_job_run('local', case.job.id).status == 'failed'
    assert [s.status for s in case.repo.list_narration_segments('local', case.task_id, case.script_id)] == ['ready', 'pending']
    assert case.repo.get_creation_task('local', case.task_id).narration_cache_key is None
    assert case.repo.creation_narration_usage('local', case.job.id)['used_output_bytes'] == one_output
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={'creation_job': SimpleNamespace(owner=case.owner,executor=case.executor)}, wake=lambda: None)
    with pytest.raises(ExecutionControlRejected, match='不能|核对'):
        control.resume('local', case.task_id, case.actor, runtime, verify_media=False)
    assert len(case.execution.jobs('local', case.task_id)) == 1 and case.scheduler.pick_and_grant() is None


@pytest.mark.parametrize('unknown', [None, 'release', 'output'])
def test_narration_real_memory_pressure_settles_known_usage_and_retains_unknown(tmp_path, monkeypatch, unknown):
    from datetime import timedelta
    from app.device_runtime import DeviceRuntime, OwnerRegistration
    from app.device_scheduler import RecoveryBlocked
    from app.granted_subprocess import GrantedCommandMemoryPressure
    from app.host_memory import MemoryDecision, MemorySample
    case = _narration_units_case(tmp_path)
    request_id = submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    sample = MemorySample('darwin', time.monotonic(), datetime.now(timezone.utc).isoformat(), 'critical', 16, None, {})
    failure = GrantedCommandMemoryPressure(MemoryDecision(False, 'memory_pressure_critical', sample, 32, None, 0))
    original_prepare = case.stage.prepare_segment
    partial = tmp_path/'pressure-partial.wav'
    def pressure(*args, **kwargs):
        partial.write_bytes(synthetic_wav_bytes(seconds=1))
        kwargs['grant_context'].narration_output_usage[0] = None if unknown == 'output' else partial.stat().st_size
        # These ports report known output and release separately; the exception class is production's real type.
        case.releases['ready'] = unknown != 'release'
        raise failure
    monkeypatch.setattr(case.stage, 'prepare_segment', pressure)
    runtime = DeviceRuntime(case.scheduler, {'creation_job': OwnerRegistration(case.owner, case.executor)}, poll_interval_seconds=.1)
    if unknown is not None:
        with pytest.raises(RecoveryBlocked):
            runtime.run_once()
        usage = case.repo.creation_narration_usage('local', case.job.id)
        assert not usage['consumption_known'] and len(usage['unsettled']) == 1
        assert runtime.last_error is not None
        assert case.scheduler._get(request_id).state == 'granted'
    else:
        assert runtime.run_once() is True and runtime.last_error is None
        usage = case.repo.creation_narration_usage('local', case.job.id)
        assert usage['consumption_known'] and not usage['unsettled']
        assert usage['used_output_bytes'] == partial.stat().st_size and usage['used_seconds'] > 0
        assert case.repo.get_job_run('local', case.job.id).status == 'interrupted'
        assert case.scheduler._get(request_id).state == 'blocked'
        assert case.scheduler._get(request_id).wait_reason == 'resource_pressure'
        monkeypatch.setattr(case.stage, 'prepare_segment', original_prepare)
        case.scheduler.clock = lambda: datetime.now(timezone.utc)+timedelta(seconds=31)
        assert runtime.run_once() is True and runtime.last_error is None
        assert case.scheduler._get(request_id).state == 'done'
    assert len(case.execution.jobs('local', case.task_id)) == 1
    case.scheduler.close()


def _native_operations_case(tmp_path):
    from app.creation.native_task_operations import NativeTaskOperations
    from app.host_resource_probes import NativeCommandBudget
    case = _narration_units_case(tmp_path)
    budget = NativeCommandBudget(1024*1024,1024*1024,32,65536,65536,1,5,128*1024*1024,16,.1,128*1024*1024,1024*1024)
    case.native = NativeTaskOperations(case.repo,ArtifactStore(tmp_path/'native-artifacts'),tmp_path/'native-work',budget=budget)
    case.owner.native_case = case.executor.native_case = case.native
    case.runtime = SimpleNamespace(scheduler=case.scheduler,wake=lambda:None,
        registrations={'creation_job':SimpleNamespace(owner=case.owner,executor=case.executor)})
    case.origin = {'task_id':case.task_id,'conversation_id':'conversation_native','assistant_turn_id':'turn_native',
        'input_id':'input_native','session_id':'session_native','sdk_round':1}
    def call(operation,arguments,call_id='file_1'):
        return case.native.dispatch(operation,arguments,actor=case.actor,origin=case.origin,write_guard=lambda conn:None,
            runtime=case.runtime,call_id=call_id)
    case.call = call
    return case


def test_native_cas_unknown_replay_and_real_output_registration(tmp_path):
    import base64
    from app.device_scheduler import RecoveryBlocked
    case = _native_operations_case(tmp_path)
    path = '/task/work/note.json'
    args = {'action':'write','path':path,'expected_sha256':None,'content_base64':base64.b64encode(b'{"v":1}').decode()}
    first = case.call('native_task_file',args)
    assert case.call('native_task_file',args)==first
    with pytest.raises(ValueError,match='变化'):
        case.call('native_task_file',{**args,'expected_sha256':'0'*64},'bad_cas')
    bad = case.repo.get_job_run_by_idempotency_key('local',f'native:{case.task_id}:{case.actor.member_id}:bad_cas')
    assert bad.status=='failed' and case.repo.native_operation_result('local',bad.id)['submission_effect']=='not_submitted'
    root = case.native.task_root(case.actor,case.task_id)
    (root/'work'/'image.txt').write_bytes(b'\x89PNG\r\n\x1a\n'+b'\x00'*30)
    with pytest.raises(PermissionError,match='媒体'):
        case.call('native_task_file',{'action':'read','path':'/task/work/image.txt'})
    changed = case.call('native_task_file',{**args,'expected_sha256':first['sha256'],
        'content_base64':base64.b64encode(b'{"v":2}').decode()},'file_2')
    stale = case.call('register_task_artifacts',{'request_id':'register_stale','execution_job_id':first['job_id'],
        'files':[{'path':path,'role':'notes','expected_sha256':changed['sha256']}]})
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    assert case.repo.get_job_run('local',stale['job_id']).status=='failed'
    registered = case.call('register_task_artifacts',{'request_id':'register_current','execution_job_id':changed['job_id'],
        'files':[{'path':path,'role':'notes','expected_sha256':changed['sha256']}]})
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    result = case.call('read_task_command',{'job_id':registered['job_id']})
    assert result['status']=='succeeded' and result['release_state']=='released',result
    artifact = result['artifacts'][0]
    assert artifact['sha256']==changed['sha256'] and artifact['adopted'] is False
    assert case.native.artifact_path(case.actor,case.task_id,artifact['artifact_id']).read_bytes()==b'{"v":2}'
    # A started operation with no result cannot mutate again on a repeated request.
    pending,_ = case.repo.create_native_operation(case.actor,case.origin,'unknown_write','native_task_file',
        {**args,'path':'/task/work/unknown'}, {},write_guard=lambda conn:None)
    case.repo.claim_job_run('local',pending.id)
    with pytest.raises(RecoveryBlocked):
        case.call('native_task_file',{**args,'path':'/task/work/unknown'},'unknown_write')
    assert not (root/'work'/'unknown').exists()


def test_native_queued_script_change_never_launches_and_origin_cancel_drains_all(tmp_path,monkeypatch):
    import base64
    case = _native_operations_case(tmp_path)
    case.call('native_task_file',{'action':'write','path':'/task/work/run.py','expected_sha256':None,
        'content_base64':base64.b64encode(b'print(1)').decode()})
    submitted = case.call('submit_task_command',{'request_id':'changed_script','command':'python run.py'})
    root = case.native.task_root(case.actor,case.task_id)
    (root/'work'/'run.py').write_text('print(2)')
    def unexpected(*args,**kwargs): raise AssertionError('changed queued content must not launch')
    monkeypatch.setattr('app.granted_subprocess.run_granted_command',unexpected)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    result = case.call('read_task_command',{'job_id':submitted['job_id']})
    assert result['status']=='failed' and result['process_state']=='not_started' and result['release_state']=='released'
    for index in range(105):
        case.repo.create_native_operation(case.actor,case.origin,f'cancel_{index}','submit_task_command',
            {'request_id':f'cancel_{index}','command':'not launched'}, {},write_guard=lambda conn:None)
    settled=case.native.cancel_origin(case.actor,{'task_id':case.task_id,'turn_id':'turn_native','session_id':'session_native'},
        case.runtime,deadline_monotonic=time.monotonic()+10)
    assert settled['cancellation_complete'] and settled['processed_count']==105 and settled['total_count']==0,settled


def _replace_fixed_voice_file(case, *, changed=False):
    source = case.repo.creation_narration_source_binding('local', case.job.id)
    path = Path(source.payload['narration_reference']['reference']['source_path'])
    before = path.stat()
    content = bytearray(path.read_bytes())
    if changed:
        content[-1] ^= 1
    replacement = path.with_name(path.stem + '-replacement.wav')
    replacement.write_bytes(content)
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)
    assert path.stat().st_ino != before.st_ino
    return source, path


@pytest.mark.parametrize('legacy', [False, True, 'device_only'])
def test_unstarted_narration_revalidates_original_sealed_bytes_without_reset(tmp_path, monkeypatch, legacy):
    from app.creation_execution import creation_stage_facts, migrate_creation_narration_request
    from app.device_scheduler import SubmissionIntent, SchedulePolicy
    case = _narration_units_case(tmp_path)
    if legacy:
        task = case.repo.get_creation_task('local', case.task_id)
        script = case.repo.get_creation_task_script('local', case.script_id)
        plan = case.repo.get_creation_plan('local', task.plan_id)
        key, _ = creation_stage_facts(case.repo, task, script, plan, 'narration', case.owner.narration_budget, None)
        old = case.scheduler.submit(SubmissionIntent('local', 'creation_job', case.job.id,
            case.job.input_digest, key, SchedulePolicy()), case.actor)
    else:
        old = case.scheduler._get(submit_creation_stage(case.execution, case.scheduler, case.job, case.actor))
    start = case.repo.get_task_event('local', f'event_{case.job.id}_start')
    usage = case.repo.creation_narration_usage('local', case.job.id)
    if legacy == 'device_only':
        source = case.repo.creation_narration_source_binding('local',case.job.id)
        previous = json.loads(json.dumps(source.payload))
        previous['narration_reference']['reference']['device'] -= 4
        task = case.repo.get_creation_task('local',case.task_id)
        script = case.repo.get_creation_task_script('local',case.script_id)
        plan = case.repo.get_creation_plan('local',task.plan_id)
        old_key,_ = creation_stage_facts(case.repo,task,script,plan,'narration',case.owner.narration_budget,None,
            comparison_reference=previous['narration_reference'])
        # Simulate the persisted pre-remount snapshot; only st_dev differs, and
        # the original host-produced SHA is retained unchanged.
        with case.repo._connect() as conn:
            conn.execute('UPDATE task_events SET payload_json=? WHERE id=?',(json.dumps(previous),source.id))
            conn.execute('UPDATE device_work_requests SET unit_key=? WHERE id=?',(old_key,old.id))
        source = case.repo.creation_narration_source_binding('local',case.job.id)
        path = Path(source.payload['narration_reference']['reference']['source_path'])
    else:
        source, path = _replace_fixed_voice_file(case)
    _assert_wav_reads_outside_writer(monkeypatch, case)
    new = migrate_creation_narration_request(case.repo, case.scheduler, case.job, case.actor)
    assert new.id != old.id and new.owner_id == old.owner_id and new.input_digest == old.input_digest
    assert new.first_ready_at_utc == old.first_ready_at_utc and new.policy == old.policy
    assert (new.eligible_since_utc, new.eligible_wait_seconds) == (old.eligible_since_utc, old.eligible_wait_seconds)
    assert new.resources['parent_max_output_bytes'] == old.resources.get('parent_max_output_bytes', old.resources['max_output_bytes'])
    assert new.resources['parent_timeout_seconds'] == old.resources.get('parent_timeout_seconds', old.resources['timeout_seconds'])
    assert case.repo.get_job_run('local', case.job.id).idempotency_key == case.job.idempotency_key
    assert case.repo.get_task_event('local', start.id) == start
    assert case.repo.creation_narration_source_binding('local', case.job.id) == source
    assert case.repo.creation_narration_usage('local', case.job.id) == usage
    assert source.payload['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert case.repo.get_task_event('local', f'event_{new.id}_source_revalidated').payload['source_binding_event_id'] == source.id
    assert migrate_creation_narration_request(case.repo, case.scheduler, case.repo.get_job_run('local', case.job.id), case.actor).id == new.id
    assert submit_creation_stage(case.execution, case.scheduler, case.repo.get_job_run('local', case.job.id), case.actor) == new.id
    with case.repo._connect() as conn:
        assert case.owner.check_current(new, conn) == 'ready'
    grant = case.scheduler.pick_and_grant()
    assert grant.request_id == new.id
    case.executor.execute(grant, case.scheduler)
    assert case.repo.list_narration_segments('local', case.task_id, case.script_id)[0].status == 'ready'
    assert len(case.execution.jobs('local', case.task_id)) == 1


@pytest.mark.parametrize('failure', ['changed_bytes', 'missing_anchor', 'granted', 'unknown'])
def test_unstarted_narration_revalidation_refuses_without_new_baseline(tmp_path, failure):
    from app.creation_execution import migrate_creation_narration_request
    from app.device_scheduler import RecoveryBlocked
    case = _narration_units_case(tmp_path)
    old = case.scheduler._get(submit_creation_stage(case.execution, case.scheduler, case.job, case.actor))
    source, _ = _replace_fixed_voice_file(case, changed=failure == 'changed_bytes')
    if failure == 'missing_anchor':
        with case.repo._connect() as conn:
            start = case.repo.get_task_event('local', f'event_{case.job.id}_start', connection=conn)
            payload = dict(start.payload); payload.pop('source_binding_event_id')
            conn.execute('UPDATE task_events SET payload_json=? WHERE id=?', (json.dumps(payload), start.id))
        # Original start replay cannot turn the unrelated saved source event into
        # historical proof, even though the file currently has exactly its bytes.
        assert case.execution.start('local', case.task_id, case.script_id, case.job.input_digest, actor=case.actor).id == case.job.id
    elif failure in {'granted', 'unknown'}:
        with case.scheduler._connect() as conn:
            conn.execute("UPDATE device_work_requests SET last_grant_generation=7,state=? WHERE id=?",
                ('granted' if failure == 'granted' else 'blocked', old.id))
    reason = {'changed_bytes': 'original_source_content_changed', 'missing_anchor': 'original_source_content_proof_missing',
        'granted': '已有授予', 'unknown': '已有授予'}[failure]
    with pytest.raises(RecoveryBlocked, match=reason):
        migrate_creation_narration_request(case.repo, case.scheduler, case.job, case.actor)
    with case.repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_narration_source_bound'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_narration_source_revalidated'").fetchone()[0] == 0
    assert case.repo.get_task_event('local', source.id) == source
    assert case.repo.get_job_run('local', case.job.id) == case.job


def test_unstarted_source_revalidation_revocation_during_prepare_keeps_original_request(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.creation_execution import migrate_creation_narration_request
    import app.creation.narration_pcm as sources
    case = _queued_narration_voice_selection_case(tmp_path)
    source, _ = _replace_fixed_voice_file(case)
    original = sources.prepare_narration_source
    @contextmanager
    def revoke(*args, **kwargs):
        with original(*args, **kwargs) as prepared:
            current = case.repo.get_task_authorization('local', case.task_id)
            case.repo.save_task_authorization(replace(current, revision=current.revision+1,
                revoked_at=datetime.now(timezone.utc)), expected_revision=current.revision)
            yield prepared
    monkeypatch.setattr(sources, 'prepare_narration_source', revoke)
    with pytest.raises(PermissionError, match='授权不可核实'):
        migrate_creation_narration_request(case.repo, case.scheduler, case.job, case.actor)
    assert case.scheduler._get(case.request_id).state == 'waiting'
    assert case.repo.get_job_run('local', case.job.id) == case.job
    assert case.repo.creation_narration_source_binding('local', case.job.id) == source
    with case.repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == 1


def test_new_voice_adoption_reuses_only_exact_received_sampling_sha(tmp_path):
    case = _queued_narration_voice_selection_case(tmp_path)
    service = case.repo.voice_sampling
    sample = service.create('local', case.actor.member_id, request_id='sample-for-adoption', display_name='本人采样')
    received = service.receive('local', case.actor.member_id, sample.id, request_id='sample-receive',
        text_version=sample.text_version, purpose=sample.purpose, source_device='import',
        content=synthetic_wav_bytes(), model_id='test')
    voice = case.repo.get_versioned_voice_profile('local', received.voice_profile_id, received.voice_version)
    proof = service.received_source_evidence(voice)
    assert proof['sha256'] == received.audio_sha256
    assert service.received_source_evidence(replace(voice, version='other-version')) is None
    assert service.received_source_evidence(replace(voice, owner_user_id='other-member')) is None
    assert service.received_source_evidence(replace(voice, reference=replace(voice.reference, asset_id='other-source'))) is None
    selection = {**case.selection, 'voice_profile_id': voice.id, 'voice_version': voice.version}
    case.control.select_narration_voice(runtime=case.runtime, actor=case.actor, **selection)
    binding = case.repo.creation_narration_source_binding('local', case.job.id)
    assert binding.payload['basis'] == 'prior_sampling_receipt'
    assert binding.payload['sampling_receipt'] == proof
    assert binding.payload['sha256'] == received.audio_sha256
    assert binding.payload['actor'] == case.actor.identity_payload()


def test_unsealed_original_job_without_device_request_cannot_resume_as_fresh_input(tmp_path):
    from app.device_scheduler import RecoveryBlocked
    case = _narration_units_case(tmp_path)
    with case.repo._connect() as conn:
        start = case.repo.get_task_event('local',f'event_{case.job.id}_start',connection=conn)
        payload = dict(start.payload); payload.pop('source_binding_event_id')
        conn.execute('UPDATE task_events SET payload_json=? WHERE id=?',(json.dumps(payload),start.id))
    # Idempotent start remains a read of the original request. First admission
    # and an old failed request's retry must not establish a fresh byte baseline.
    assert case.execution.start('local',case.task_id,case.script_id,case.job.input_digest,actor=case.actor).id == case.job.id
    with pytest.raises(RecoveryBlocked,match='original_source_content_proof_missing'):
        submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    with case.repo._connect() as conn:
        conn.execute("UPDATE job_runs SET status='failed' WHERE id=?",(case.job.id,))
    with pytest.raises(RecoveryBlocked,match='original_source_content_proof_missing'):
        case.execution.retry('local',case.task_id,member_id=case.actor.member_id)
    assert len(case.execution.jobs('local',case.task_id)) == 1
    with case.repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_narration_source_bound'").fetchone()[0] == 1


def _account_voice_selection_case(tmp_path, monkeypatch):
    from uuid import uuid4
    from fastapi import FastAPI
    from app.auth import AuthPrincipal
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.host_resource_probes import CpuAudioBudget, HostResourceDecision
    workspace, device, user = (str(uuid4()) for _ in range(3))
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id=workspace)
    actor = CreationExecutionActor.account(workspace, device, user)
    _qualify_start_voice(repo, task_id, user, workspace_id=workspace)
    register_synthetic_profiles(repo, workspace_id=workspace, member_id=user,
        person_id='person_2', voice_id='voice_2', voice_version='v1', language='zh', model_id='test')
    with repo._connect() as conn:
        row = conn.execute('SELECT id,payload FROM creation_plans WHERE workspace_id=?', (workspace,)).fetchone()
        plan_payload = json.loads(row['payload']); plan_payload['owner_user_id'] = user
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(plan_payload), row['id']))
    task = repo.get_creation_task(workspace, task_id)
    calls = []
    def verify(identity):
        assert identity == TaskAuthorizationUseCase.identity(actor)
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE'); conn.rollback()
        calls.append(identity)
        return 'ready'
    authorizations = TaskAuthorizationUseCase(repo, verify)
    authorization = authorizations.ensure(task, actor)
    execution = _execution(repo)
    script = repo.get_creation_task_script(workspace, script_id)
    plan = repo.get_creation_plan(workspace, task.plan_id)
    job = execution.start(workspace, task_id, script_id, revision_digest(script, plan), actor=actor)
    narration = NarrationBudget(374, 3, 1_000_000, 30, 3*1024*1024, 1, 300, 1024, 16, .1, 2048, 1024,
        input_scope='single_call_experiment_v1')
    audio = CpuAudioBudget(2, 32*1024*1024, 30, 30, 48000, 2, 3*1024*1024, 1, 30, 1024, 16, .1, 2048, 1024)
    owner = CreationJobOwnerVerifier(repo, narration_budget=narration, narration_audio_budget=audio,
        task_authorizations=authorizations)
    resources = SimpleNamespace(admit=lambda request: HostResourceDecision(True, dict(request.resources), {}, None))
    scheduler = DeviceScheduler(repo.database_path, tmp_path/'account-selection.lock', owners=owner,
        resources=resources, processes=HostProcessProbe())
    request_id = submit_creation_stage(execution, scheduler, job, actor)
    control = CreationExecutionControl(repo, execution, None, lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=scheduler, registrations={'creation_job':SimpleNamespace(owner=owner)},
        last_error=None, wake=lambda:None)
    selection = dict(workspace_id=workspace, task_id=task_id, member_id=user, request_id=str(uuid4()),
        voice_profile_id='voice_2', voice_version='v1', expected_plan_updated_at=plan.updated_at,
        expected_task_updated_at=task.updated_at)
    principal = AuthPrincipal(user_id=user, workspace_id=workspace)
    monkeypatch.setattr(main_module, 'production_repo', repo)
    monkeypatch.setattr(main_module, 'creation_execution', execution)
    monkeypatch.setattr(main_module, '_creation_execution_control', lambda:control)
    monkeypatch.setattr(main_module, 'device_runtime', runtime)
    monkeypatch.setattr(main_module, 'auth_context', SimpleNamespace(enabled=True))
    monkeypatch.setattr(main_module, 'current_workspace_id', lambda request:request.state.principal.workspace_id)
    monkeypatch.setattr(main_module, 'trusted_device_credential', lambda:SimpleNamespace(device_id=device))
    monkeypatch.setattr(main_module, '_verify_creation_identity', lambda _:pytest.fail('选声适配器不能额外调用执行许可'))
    app = FastAPI()
    @app.middleware('http')
    async def authenticated(request, call_next):
        request.state.principal = principal
        return await call_next(request)
    app.add_api_route('/api/creation-tasks/{task_id}/narration/voice', main_module.select_creation_task_narration_voice,
        methods=['POST'])
    client = TestClient(app)
    return SimpleNamespace(repo=repo, actor=actor, job=job, task_id=task_id, script_id=script_id,
        owner=owner, scheduler=scheduler, execution=execution, control=control, runtime=runtime,
        request_id=request_id, selection=selection, client=client, authorization=authorization, identity_calls=calls)


def _voice_selection_snapshot(case):
    with case.repo._connect() as conn:
        return {table:[tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in ('creation_tasks','creation_plans','job_runs','task_events','task_authorizations',
                'device_work_requests','device_request_events','device_permit')}


def test_account_voice_http_uses_content_owner_and_full_current_actor_then_replays(tmp_path, monkeypatch):
    from app.schemas import CreationTaskNarrationVoiceSelectionRequest
    from uuid import uuid4
    case = _account_voice_selection_case(tmp_path, monkeypatch)
    body = CreationTaskNarrationVoiceSelectionRequest.model_validate({key:value for key,value in case.selection.items()
        if key not in {'workspace_id','task_id','member_id'}}).model_dump(mode='json')
    old = case.scheduler._get(case.request_id)
    start = case.repo.get_task_event(case.actor.workspace_id,f'event_{case.job.id}_start')
    usage = case.repo.creation_narration_usage(case.actor.workspace_id,case.job.id)
    route = f'/api/creation-tasks/{case.task_id}/narration/voice'
    before = _voice_selection_snapshot(case)
    monkeypatch.setattr(main_module,'trusted_device_credential',lambda:SimpleNamespace(device_id=str(uuid4())))
    refused = case.client.post(route,json=body)
    assert refused.status_code == 403 and '声音选择不属于原作业成员' in refused.text
    assert _voice_selection_snapshot(case) == before
    monkeypatch.setattr(main_module,'trusted_device_credential',lambda:SimpleNamespace(device_id=case.actor.device_id))
    checks_before = len(case.identity_calls)
    response = case.client.post(route,json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['member_id'] == case.actor.user_id != case.actor.member_id
    assert receipt['selected_voice']['profile_id'] == 'voice_2'
    assert case.repo.get_task_event(case.actor.workspace_id,start.id) == start
    updated = case.repo.get_job_run(case.actor.workspace_id,case.job.id)
    assert updated.id == case.job.id and updated.idempotency_key == case.job.idempotency_key and updated.attempt == 0
    source = case.repo.creation_narration_source_binding(case.actor.workspace_id,case.job.id)
    assert source.payload['actor'] == case.actor.identity_payload()
    new = case.scheduler._get(case.scheduler._get(old.id).wait_reason.split(':',1)[1])
    assert new.member_id == case.actor.member_id and new.first_ready_at_utc == old.first_ready_at_utc
    assert new.resources['parent_timeout_seconds'] == old.resources['parent_timeout_seconds']
    assert new.resources['parent_max_output_bytes'] == old.resources['parent_max_output_bytes']
    assert case.repo.creation_narration_usage(case.actor.workspace_id,case.job.id) == usage
    assert len(case.identity_calls) > checks_before
    checks_after = len(case.identity_calls)
    before = _voice_selection_snapshot(case)
    monkeypatch.setattr(main_module,'_request_permit_actor',lambda *_:pytest.fail('成功原请求重放不解析执行actor'))
    assert case.client.post(route,json=body).json() == receipt
    changed = case.client.post(route,json={**body,'voice_profile_id':'voice_1'})
    assert changed.status_code == 409
    assert _voice_selection_snapshot(case) == before
    assert len(case.identity_calls) == checks_after
    case.scheduler.close()


@pytest.mark.parametrize('different', ['device','user','workspace','missing'])
def test_account_queued_voice_selection_refuses_current_actor_mismatch(tmp_path, monkeypatch, different):
    from uuid import uuid4
    from app.creation.task_authorization import TaskAuthorizationDenied
    case = _account_voice_selection_case(tmp_path, monkeypatch)
    actor = case.actor
    current = None if different == 'missing' else CreationExecutionActor.account(
        str(uuid4()) if different == 'workspace' else actor.workspace_id,
        str(uuid4()) if different == 'device' else actor.device_id,
        str(uuid4()) if different == 'user' else actor.user_id)
    before = _voice_selection_snapshot(case)
    with pytest.raises(TaskAuthorizationDenied,match='声音选择不属于原作业成员'):
        case.control.select_narration_voice(runtime=case.runtime,actor=current,**case.selection)
    assert _voice_selection_snapshot(case) == before
    case.scheduler.close()


def test_account_queued_voice_selection_rechecks_original_actor_in_writer(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.creation.task_authorization import TaskAuthorizationDenied
    case = _account_voice_selection_case(tmp_path, monkeypatch)
    select = case.repo.select_creation_task_narration_voice
    before = None
    def changed_original(**inputs):
        nonlocal before
        other = CreationExecutionActor.account(case.actor.workspace_id,str(uuid4()),case.actor.user_id)
        start = case.repo.get_task_event(case.actor.workspace_id,f'event_{case.job.id}_start')
        with case.repo._connect() as conn:
            conn.execute('UPDATE task_events SET payload_json=? WHERE id=?',
                (json.dumps({**start.payload,**other.identity_payload()}),start.id))
        before = _voice_selection_snapshot(case)
        return select(**inputs)
    monkeypatch.setattr(case.repo,'select_creation_task_narration_voice',changed_original)
    with pytest.raises(TaskAuthorizationDenied,match='声音选择不属于原作业成员'):
        case.control.select_narration_voice(runtime=case.runtime,actor=case.actor,**case.selection)
    assert _voice_selection_snapshot(case) == before
    case.scheduler.close()


def _visual_decision_case(tmp_path, monkeypatch):
    case = _segment_units_case(tmp_path,monkeypatch)
    asset = case.repo.get_asset('local','section_video')
    asset.segments[0].end_seconds = asset.duration_seconds
    case.repo._save_asset(asset)
    return case


def _require_section_visual_decision(case, monkeypatch):
    from app.montage import creation_task_montage_plan_to_payload
    original = case.segments.section_plan
    def unconfirmed(*args, **kwargs):
        plan, sources, _, planning_dependency = original(*args, **kwargs)
        plan.clips[0].needs_confirmation = True
        plan.low_confidence_clip_count = 1
        plan.status = 'waiting_for_user'
        return plan, sources, creation_task_montage_plan_to_payload(plan), planning_dependency
    monkeypatch.setattr(case.segments, 'section_plan', unconfirmed)


@pytest.mark.parametrize('revision', [False, True])
def test_segment_visual_decision_keeps_audio_usage_and_resumes_original_job(tmp_path, monkeypatch, revision):
    from types import SimpleNamespace
    from app.creation_execution import next_creation_intent
    case = _visual_decision_case(tmp_path, monkeypatch)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda:None,
        registrations={'creation_job':SimpleNamespace(owner=case.owner)})
    if revision:
        for _ in range(2):
            case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
        before, result, runtime = _submit_section_revision(case)
        job = case.repo.get_job_run('local', result['job_id'])
    else:
        job = case.job
    _require_section_visual_decision(case, monkeypatch)
    grant = case.scheduler.pick_and_grant()
    assert grant.owner_id == job.id, (grant.unit_key,case.repo.list_creation_job_request_states('local',job.id))
    case.executor.execute(grant, case.scheduler)
    updated = case.repo.get_job_run('local', job.id)
    assert updated.status == 'queued', updated.failure_reason
    assert case.scheduler._get(grant.request_id).state == 'done'
    view = case.segments.read_visual_decision('local', case.task_id, job.id, 'section_1', case.actor)
    assert view['status'] == 'waiting_for_user'
    assert view['audio']['persisted'] and view['usage']['settled']
    assert view['usage']['used_output_bytes'] > 0
    assert all(row['state'] == 'done' for row in case.repo.list_creation_job_request_states('local',job.id))
    if revision:
        case.segments.resume_request('local',case.task_id,'revision_1',case.actor,runtime=runtime)
        assert case.segments.context('local',case.task_id,'section_1',case.actor)['text'] == before['text']
        monkeypatch.setattr(case.stage,'prepare_revision',lambda *a,**kw:pytest.fail('audio regenerated'))
    else:
        assert next_creation_intent(case.repo,case.scheduler,updated) is None
        segments = case.repo.list_narration_segments('local',case.task_id,case.script_id)
        assert segments[0].status == 'ready'
        monkeypatch.setattr(case.stage,'prepare_segment',lambda *a,**kw:pytest.fail('audio regenerated'))
    with pytest.raises(ValueError):
        case.segments.adopt_visual_decision('local',case.task_id,job.id,'section_1',case.actor,
            decision_digest=view['decision_digest'],confirmed_clip_ids=[],runtime=runtime)
    adopted = case.segments.adopt_visual_decision('local',case.task_id,job.id,'section_1',case.actor,
        decision_digest=view['decision_digest'],confirmed_clip_ids=view['required_confirmation_ids'],runtime=runtime)
    replay = case.segments.adopt_visual_decision('local',case.task_id,job.id,'section_1',case.actor,
        decision_digest=view['decision_digest'],confirmed_clip_ids=view['required_confirmation_ids'],runtime=runtime)
    assert replay['replayed'] and replay['device_request_id'] == adopted['device_request_id']
    assert adopted['usage'] == view['usage']
    video = case.scheduler.pick_and_grant()
    assert video.owner_id == job.id and video.unit_key.startswith('segment_video:')
    assert video.request_id == adopted['device_request_id']
    case.executor.execute(video,case.scheduler)
    assert case.scheduler._get(video.request_id).state == 'done'


@pytest.mark.parametrize('cancel', [False, True])
def test_segment_visual_decision_submit_gap_and_cancel_preserve_checkpoint(tmp_path, monkeypatch, cancel):
    from types import SimpleNamespace
    case = _visual_decision_case(tmp_path, monkeypatch)
    _require_section_visual_decision(case,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    view = case.segments.read_visual_decision('local',case.task_id,case.job.id,'section_1',case.actor)
    submit = case.scheduler.submit
    def interrupted(*args, **kwargs):
        if cancel:
            case.repo.finish_job_run('local',case.job.id,status='cancelled',failure_reason='user_cancelled',
                impact='保留段落音频检查点。',repair_action='需要时从原任务继续。')
            return submit(*args,**kwargs)
        raise RuntimeError('after durable adoption, before queue')
    monkeypatch.setattr(case.scheduler,'submit',interrupted)
    runtime = SimpleNamespace(scheduler=case.scheduler,wake=lambda:None,
        registrations={'creation_job':SimpleNamespace(owner=case.owner)})
    with pytest.raises((RuntimeError,PermissionError,ValueError)):
        case.segments.adopt_visual_decision('local',case.task_id,case.job.id,'section_1',case.actor,
            decision_digest=view['decision_digest'],confirmed_clip_ids=view['required_confirmation_ids'],runtime=runtime)
    after = case.segments.read_visual_decision('local',case.task_id,case.job.id,'section_1',case.actor)
    assert after['usage'] == view['usage'] and after['audio'] == view['audio']
    assert after['device_request_id'] is None
    monkeypatch.setattr(case.scheduler,'submit',submit)
    if cancel:
        assert after['status'] == 'stale'
        with pytest.raises(ValueError):
            case.segments.adopt_visual_decision('local',case.task_id,case.job.id,'section_1',case.actor,
                decision_digest=view['decision_digest'],confirmed_clip_ids=view['required_confirmation_ids'],runtime=runtime)
        assert case.scheduler.pick_and_grant() is None
    else:
        assert after['status'] == 'adopted' and after['recovery']['action'] == 'submit_video'
        result = case.segments.adopt_visual_decision('local',case.task_id,case.job.id,'section_1',case.actor,
            decision_digest=view['decision_digest'],confirmed_clip_ids=view['required_confirmation_ids'],runtime=runtime)
        assert result['replayed'] and result['device_request_id']


@pytest.mark.parametrize('blocker', ['source_changed', 'write_guard'])
def test_segment_visual_decision_rejects_stale_or_revoked_adoption(tmp_path, monkeypatch, blocker):
    case = _visual_decision_case(tmp_path, monkeypatch)
    _require_section_visual_decision(case, monkeypatch)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    view = case.segments.read_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor)
    decision = case.segments.visual_decision(case.job, 'section_1')
    runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda: None,
        registrations={'creation_job': SimpleNamespace(owner=case.owner)})
    if blocker == 'source_changed':
        source = Path(decision.payload['sources'][0]['path'])
        source.write_bytes(source.read_bytes() + b'changed')
    def denied(conn):
        assert conn.in_transaction
        raise PermissionError('authorization revoked')
    with pytest.raises((ValueError, PermissionError)):
        case.segments.adopt_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor,
            decision_digest=view['decision_digest'], confirmed_clip_ids=view['required_confirmation_ids'],
            runtime=runtime, write_guard=denied if blocker == 'write_guard' else None)
    after = case.segments.read_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor)
    assert after['status'] == ('stale' if blocker == 'source_changed' else 'waiting_for_user')
    assert after['audio'] == view['audio'] and after['usage'] == view['usage']
    assert after['device_request_id'] is None
    assert case.segments.visual_decision(case.job, 'section_1') == decision
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', case.segments.video_preparation_id(case.job, 'section_1'))
    assert case.scheduler.pick_and_grant() is None


def _edit_source_boundary_case(tmp_path, monkeypatch, kind):
    from app.conversation_repository import ConversationRepository
    from app.creation.contracts import EditIntent, EditProposal
    from app.creation.editing.exact_preview_use_case import validate_edit_origin
    from app.device_scheduler import SubmissionIntent, SchedulePolicy
    case = _narration_units_case(tmp_path)
    conversations = ConversationRepository(case.repo.database_path)
    chat = conversations.create('local', case.actor.member_id, 'task', case.task_id)
    conversations.save_draft(chat['id'], 'local', case.actor.member_id, '修改原作品', 0)
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', case.actor.member_id,
        '修改原作品', 'edit-source', 1, {'kind': 'task', 'task_id': case.task_id})
    source = {**conversations.task_turn_source(turn['id'], 'local', case.actor.member_id, case.task_id),
        'workspace_id': 'local', 'task_id': case.task_id, 'member_id': case.actor.member_id,
        'actor_identity': case.actor.identity_payload(), 'assistant_turn_id': turn['id']}
    intent = case.repo.create_edit_intent(EditIntent('source-intent', 'local', case.task_id,
        '修改原作品', 'person', [], None, 'edit', {}, {}, 1, 'interpreted'))
    proposal = case.repo.create_edit_proposal(EditProposal('source-proposal', 'local', case.task_id,
        intent.id, '修改原作品', ['clip:original'], {'operation_type': 'RegeneratePersonRender',
            'payload': {'person_profile_id': 'person', 'person_version': 'v1'}}, [], {}, []))
    with case.repo._connect() as conn:
        conn.execute('UPDATE job_runs SET kind=?,status=? WHERE id=?', (kind, 'running', case.job.id))
        event = case.repo.get_task_event('local', f'event_{case.job.id}_start', connection=conn)
        payload = {**event.payload, 'source': source, 'edit_proposal_id': proposal.id}
        conn.execute('UPDATE task_events SET payload_json=? WHERE id=?',
            (json.dumps(payload), event.id))
    # Resource/proposal eligibility is covered by the existing real edit integration;
    # these boundary cases isolate persisted source changes with real SQLite/turn facts.
    key = ('person_edit:' if kind == 'creation_task_person_edit' else 'narration_edit:') + 'source'
    resources = {'kind': 'edit_source_boundary'}
    monkeypatch.setattr(case.owner, '_check_facts', lambda *a: 'ready')
    monkeypatch.setattr(case.owner, 'edit_facts', lambda *a, **kw: (key, resources))
    monkeypatch.setattr(case.owner, 'narration_edit_facts', lambda *a, **kw: (key, resources))
    connections = []
    def verifier(source, actor, *, connection=None):
        connections.append(connection)
        validate_edit_origin(conversations, source, actor, connection=connection)
    case.owner.source_verifier = case.executor.source_verifier = verifier
    request = SimpleNamespace(workspace_id='local', owner_id=case.job.id, owner_type='creation_job',
        member_id=case.actor.member_id, source_refs=(), unit_key=key, input_digest=case.job.input_digest,
        work_class='preview_dependency' if kind == 'creation_task_narration_edit' else 'normal_make', resources=resources)
    submission = SubmissionIntent('local', 'creation_job', case.job.id, case.job.input_digest, key, SchedulePolicy())
    return SimpleNamespace(**vars(case), turn=turn, source=source, request=request,
        submission=submission, connections=connections)


@pytest.mark.parametrize('kind', ['creation_task_person_edit', 'creation_task_narration_edit'])
def test_edit_source_rechecked_in_submission_grant_and_success_transaction(tmp_path, monkeypatch, kind):
    case = _edit_source_boundary_case(tmp_path, monkeypatch, kind)
    checkpoint = (case.executor._person_edit_checkpoint if kind == 'creation_task_person_edit'
        else case.executor._narration_edit_checkpoint)
    grant = SimpleNamespace(workspace_id='local', owner_id=case.job.id, request_id='original-request',
        unit_key=case.request.unit_key, attempt_number=1)
    with case.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert case.owner.verify_submission(case.submission, case.actor, connection=conn)
        assert case.owner.check_current(case.request, conn) == 'ready'
        captured = case.owner.capture_local(case.request, conn)
        assert captured(conn) == 'ready'
        conn.execute("UPDATE assistant_conversation_turns SET status='stopped' WHERE id=?", (case.turn['id'],))
        with pytest.raises((PermissionError, ValueError)):
            case.owner.verify_submission(case.submission, case.actor, connection=conn)
        assert case.owner.check_current(case.request, conn) == 'stale'
        assert captured(conn) == 'stale'
        with pytest.raises((PermissionError, ValueError)):
            checkpoint(conn, grant, 'succeeded', commit=lambda _: pytest.fail('stale source published'))
        assert all(current is conn and current.in_transaction for current in case.connections)
        # Terminal receipts and original reads do not require renewed source permission.
        terminal = 'cancelled' if kind == 'creation_task_person_edit' else 'failed'
        ref = checkpoint(conn, grant, terminal, reason='source_unavailable')
        assert case.repo.get_task_event('local', ref, connection=conn).payload['status'] == terminal
    original = case.repo.get_task_event('local', f'event_{case.job.id}_start')
    assert original.payload['source'] == case.source
    assert case.repo.list_edit_operations('local', case.task_id) == []


@pytest.mark.parametrize('kind,blocker', [
    ('creation_task_person_edit', 'missing'),
    ('creation_task_narration_edit', 'malformed'),
    ('creation_task_person_edit', 'not_configured'),
    ('creation_task_narration_edit', 'wrong_actor'),
])
def test_edit_source_unverifiable_jobs_cannot_be_submitted(tmp_path, monkeypatch, kind, blocker):
    case = _edit_source_boundary_case(tmp_path, monkeypatch, kind)
    with case.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        event = case.repo.get_task_event('local', f'event_{case.job.id}_start', connection=conn)
        payload = event.payload
        if blocker == 'missing':
            payload.pop('source')
        elif blocker == 'malformed':
            payload['source'] = ['not a source']
        elif blocker == 'wrong_actor':
            payload['source']['actor_identity'] = {**case.actor.identity_payload(), 'device_id': 'another-device'}
        else:
            case.owner.source_verifier = None
        conn.execute('UPDATE task_events SET payload_json=? WHERE id=?', (json.dumps(payload), event.id))
        with pytest.raises(PermissionError):
            case.owner.verify_submission(case.submission, case.actor, connection=conn)
        assert case.owner.check_current(case.request, conn) == 'stale'
    assert case.repo.get_job_run('local', case.job.id).status == 'running'


def _legacy_generated_segment_case(tmp_path, monkeypatch):
    from app.narration import NarrationArtifact, NarrationGenerator, _pronunciation_dictionary, _pronunciation_version
    case = _segment_units_case(tmp_path, monkeypatch)
    _require_section_visual_decision(case, monkeypatch)
    canonical = NarrationGenerator(tmp_path/'legacy-output', SimpleNamespace(model_id='test'))
    case.stage.generator.output_root = canonical.output_root
    output = {}
    def legacy_generate(**kwargs):
        reference = case.repo.profile_reference_input(kwargs['voice_identity'], viewer_user_id=case.actor.member_id)
        dictionary = _pronunciation_dictionary(entries=kwargs['pronunciation_entries'], workspace_id='local',
            person_profile_id=kwargs['voice_identity'].person_profile_id, task_id=case.task_id)
        cache = canonical.creation_task_cache_key(workspace_id='local', task_id=case.task_id,
            script_id=case.script_id, narration_text=kwargs['narration_text'].strip(),
            voice_identity=kwargs['voice_identity'], pronunciation_dictionary=dictionary,
            segment_id=kwargs['segment_id'], language='zh',
            generation_identity=hashlib.sha256(json.dumps([None, reference.identity]).encode()).hexdigest())
        path = canonical.output_root/'creation-tasks'/'local'/case.task_id/f"segment_{kwargs['segment_id']}_{cache[:12]}.wav"
        path.parent.mkdir(parents=True)
        import wave
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x01\x00'*24000)
        response = {'schema_version': 1, 'model_id': 'test', 'audio_path': str(path.resolve()),
            'duration_seconds': 1.0, 'sample_rate': 24000}
        kwargs['grant_context'].narration_output_usage[0] = path.stat().st_size + len(json.dumps(response).encode())
        output.update(path=path, cache_key=cache)
        return NarrationArtifact(task_id=case.task_id, script_id=case.script_id, audio_path=str(path),
            duration_seconds=1.0, sample_rate=24000, model_id='test', voice_profile_id='voice_1',
            cache_key=cache, language='zh', pronunciation_version=_pronunciation_version(dictionary))
    monkeypatch.setattr(case.stage.generator, 'generate_for_creation_task', legacy_generate)
    prepare_video = case.segments.prepare_video
    monkeypatch.setattr(case.segments, 'prepare_video', lambda *a, **kw: (_ for _ in ()).throw(
        ValueError('legacy visual decision failed before audio checkpoint')))
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    grant = case.scheduler.pick_and_grant()
    case.executor.execute(grant, case.scheduler)
    assert case.repo.get_job_run('local', case.job.id).status == 'failed'
    assert case.scheduler._get(grant.request_id).state == 'failed'
    settled = case.repo.get_task_event('local', f'event_{case.job.id}_narration_settled_{grant.generation}')
    assert settled.payload['output_file'] is None and settled.payload['output_bytes'] > 0
    assert case.repo.get_narration_artifact('local', output['cache_key']) is None
    monkeypatch.setattr(case.segments, 'prepare_video', prepare_video)
    monkeypatch.setattr(case.stage.generator, 'generate_for_creation_task', lambda **kw: pytest.fail('provider fallback'))
    from app.creation.execution_control import CreationExecutionControl
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={
        'creation_job': SimpleNamespace(owner=case.owner, executor=case.executor)}, wake=lambda: None)
    return SimpleNamespace(**vars(case), output=output, grant=grant, settled=settled, control=control, runtime=runtime)


@pytest.mark.parametrize('mismatch', [None, 'output', 'source'])
def test_resume_original_legacy_audio_recovers_metadata_without_provider_or_new_charge(tmp_path, monkeypatch, mismatch):
    from app.device_scheduler import RecoveryBlocked
    case = _legacy_generated_segment_case(tmp_path, monkeypatch)
    before = case.repo.creation_narration_usage('local', case.job.id)
    if mismatch == 'output':
        path = case.output['path']
        path.write_bytes(path.read_bytes() + b'not_original')
    elif mismatch == 'source':
        inputs = case.repo.resolve_creation_task_narration_inputs(workspace_id='local', task_id=case.task_id, voice_profile_id='voice_1', voice_version='v1')
        source = case.repo.profile_reference_input(inputs.voice, viewer_user_id=case.actor.member_id)
        Path(source.source_path).write_bytes(Path(source.source_path).read_bytes() + b'not_original')
    def guard(conn):
        assert conn.in_transaction
    if mismatch:
        from app.creation.execution_control import ExecutionControlRejected
        with pytest.raises(ExecutionControlRejected):
            case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False, write_guard=guard)
        assert case.repo.get_narration_artifact('local', case.output['cache_key']) is None
        assert all(row.status == 'pending' for row in case.repo.list_narration_segments('local', case.task_id, case.script_id))
    else:
        case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False, write_guard=guard)
        result = case.segments.read_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor)
        assert result['status'] == 'waiting_for_user' and result['audio']['persisted']
        assert case.repo.get_creation_task('local', case.task_id).stage_status == 'waiting_for_user'
        record = case.repo.get_narration_artifact('local', case.output['cache_key'])
        assert record.sample_rate == 24000 and record.duration_seconds == 1.0
        decision = case.segments.visual_decision(case.repo.get_job_run('local', case.job.id), 'section_1')
        case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False, write_guard=guard)
        assert case.segments.visual_decision(case.repo.get_job_run('local', case.job.id), 'section_1') == decision
        assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.repo.creation_narration_usage('local', case.job.id) == before
    assert case.repo.get_task_event('local', case.settled.id) == case.settled
    assert case.scheduler._get(case.grant.request_id).state == 'failed'
    assert case.scheduler.pick_and_grant() is None


@pytest.mark.parametrize('blocked', [None, 'guard', 'incomplete'])
def test_completed_narration_resumes_current_montage_not_historical_failed_tts(tmp_path, monkeypatch, blocked):
    from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
    case = _narration_units_case(tmp_path)
    def refused(*args, **kwargs):
        raise ValueError('current montage audio mix budget refused')
    case.executor.montage_stage = SimpleNamespace(generate=refused)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    first = case.scheduler.pick_and_grant()
    case.executor.execute(first, case.scheduler)
    for _ in range(3):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    task = case.repo.get_creation_task('local', case.task_id)
    assert task.stage == 'montage' and task.stage_status == 'failed'
    assert case.repo.get_narration_artifact('local', task.narration_cache_key)
    before = case.repo.creation_narration_usage('local', case.job.id)
    rows = case.repo.list_creation_job_request_states('local', case.job.id)
    montage = next(row for row in rows if row['unit_key'].startswith('montage_plan:'))
    assert montage['state'] == 'failed'
    # Legacy audio recovery preserves its original failed request as audit history.
    with case.repo._connect() as conn:
        conn.execute("UPDATE device_work_requests SET state='failed' WHERE id=?", (first.request_id,))
        if blocked == 'incomplete':
            conn.execute("UPDATE narration_segments SET status='pending' WHERE task_id=? AND source_section_id='section_2'", (case.task_id,))
    monkeypatch.setattr(case.executor, 'recover_generated_segment', lambda *a, **kw: pytest.fail('historical TTS recovery chosen'))
    monkeypatch.setattr(case.stage.generator, 'generate_for_creation_task', lambda **kw: pytest.fail('TTS invoked'))
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={
        'creation_job': SimpleNamespace(owner=case.owner, executor=case.executor)}, wake=lambda: None)
    observed = []
    def guard(conn):
        assert conn.in_transaction
        observed.append(conn)
        if blocked == 'guard':
            raise PermissionError('original source revoked')
    if blocked:
        with pytest.raises(ExecutionControlRejected):
            control.resume('local', case.task_id, case.actor, runtime, verify_media=False, write_guard=guard)
        assert case.scheduler._get(montage['id']).state == 'failed'
        assert case.repo.get_job_run('local', case.job.id).status == 'failed'
    else:
        control.resume('local', case.task_id, case.actor, runtime, verify_media=False, write_guard=guard)
        current = case.scheduler._get(montage['id'])
        assert current.state == 'waiting' and current.attempt_number == 2
        assert case.repo.get_job_run('local', case.job.id).status == 'interrupted'
        assert observed and len(case.execution.jobs('local', case.task_id)) == 1
    assert case.scheduler._get(first.request_id).state == 'failed'
    assert case.repo.creation_narration_usage('local', case.job.id) == before


@pytest.mark.parametrize('denied', [False, True])
def test_succeeded_job_rerenders_current_titles_without_reopening_narration(tmp_path, denied):
    from app.creation.contracts import TaskEvent
    from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
    from app.graphic_overlays import TextOverlay
    repo, task_id, script_id, digest, actor, execution, owner, scheduler, base, _members = _render_version_fixture(tmp_path)
    from app.host_resource_probes import HostResourceDecision
    scheduler.resources.admit = lambda request: HostResourceDecision(True, dict(request.resources), {}, None)
    job = execution.start('local', task_id, script_id, digest, actor=actor)
    original_request = submit_creation_stage(execution, scheduler, job, actor)
    old_output = TaskEvent(f'event_{job.id}_render_succeeded', 'local', task_id, job.id,
        'creation_execution_stage', {'stage': 'render', 'status': 'completed', 'output': 'timeline_old:preview.mp4'})
    repo.append_task_event(old_output)
    settled = TaskEvent(f'event_{job.id}_narration_settled_1', 'local', task_id, job.id,
        'creation_narration_unit_settled', {'usage_id': 'original-tts:1', 'elapsed_seconds': 12.5,
            'output_bytes': 48044, 'total_seconds': 12.5, 'total_output_bytes': 48044})
    repo.append_task_event(settled)
    repo.append_task_event(TaskEvent(f'event_{job.id}_narration_budget', 'local', task_id, job.id,
        'creation_narration_budget', {'parent_timeout_seconds': 300, 'parent_max_output_bytes': 3145728,
            'settled_event_id': settled.id, 'active_event_id': None}))
    with repo._connect() as conn:
        conn.execute("UPDATE job_runs SET status='succeeded' WHERE id=?", (job.id,))
        conn.execute("UPDATE device_work_requests SET state='done' WHERE id=?", (original_request,))
    montage = repo.get_creation_task_montage_plan('local', task_id)
    montage['graphic_overlays'] = [TextOverlay(id='opening', text='Opening title', start_seconds=0, end_seconds=.4).model_dump(),
        TextOverlay(id='credit', text='Closing credit', start_seconds=.5, end_seconds=1).model_dump()]
    repo.save_creation_task_montage_plan('local', task_id, montage['id'], montage)
    edited = repo.create_timeline_version(TimelineVersion('timeline_two_titles', 'local', task_id,
        base.id, 2, {'montage': repo.get_creation_task_montage_plan('local', task_id)}, 'two-title-edit'))
    audio = repo.get_narration_artifact('local', 'cache_1')
    audio_sha = hashlib.sha256(Path(audio.audio_path).read_bytes()).hexdigest()
    usage = repo.creation_narration_usage('local', job.id)
    control = CreationExecutionControl(repo, execution, SimpleNamespace(valid=lambda *_a, **_k: False), lambda _: 'ready')
    executor = CreationJobUnitExecutor(repo, render_action=lambda *_: pytest.fail('unexpected render'),
        render_is_valid=lambda *_: False)
    runtime = SimpleNamespace(scheduler=scheduler, registrations={
        'creation_job': SimpleNamespace(owner=owner, executor=executor)}, wake=lambda: None)
    guarded = []
    def guard(conn):
        assert conn.in_transaction
        guarded.append(True)
        if denied:
            raise PermissionError('original source revoked')
    if denied:
        with pytest.raises(ExecutionControlRejected) as rejected:
            control.resume('local', task_id, actor, runtime, verify_media=False, write_guard=guard)
        assert rejected.value.status_code == 403
        assert repo.get_job_run('local', job.id).status == 'succeeded'
        assert len(repo.list_creation_job_request_states('local', job.id)) == 1
        assert not any(event.payload.get('input_timeline_version_id') == edited.id
            for event in repo.list_task_events('local', task_id))
    else:
        control.resume('local', task_id, actor, runtime, verify_media=False, write_guard=guard)
        requests = repo.list_creation_job_request_states('local', job.id)
        fresh = next(row for row in requests if row['id'] != original_request)
        assert fresh['unit_key'].startswith('render:') and fresh['state'] == 'waiting'
        assert scheduler._get(fresh['id']).resources['overlay_count'] == 2
        assert repo.get_job_run('local', job.id).status == 'queued'
        control.resume('local', task_id, actor, runtime, verify_media=False, write_guard=guard)
        assert repo.list_creation_job_request_states('local', job.id) == requests
        grant = scheduler.pick_and_grant()
        assert grant.request_id == fresh['id']
        # The inner renderer is covered separately; publish metadata for the outer receipt contract.
        rendered = repo.get_creation_task_montage_plan('local', task_id)
        rendered.update(status='rendered', output_path='artifact://new-render/preview.mp4')
        repo.save_creation_task_montage_plan('local', task_id, rendered['id'], rendered)
        published = repo.create_timeline_version(TimelineVersion('timeline_new_render', 'local', task_id,
            edited.id, 3, {'montage': rendered, 'output_refs': [rendered['output_path']]}, None))
        with repo._connect() as conn:
            conn.execute("UPDATE job_runs SET status='running' WHERE id=?", (job.id,))
            executor._checkpoint(conn, grant, status='succeeded', stage='render',
                output=f'{published.id}:{rendered["output_path"]}')
        scheduler.defer_unit(grant, reason='input_stale')
        completed = repo.get_task_event('local', f'{old_output.id}_{fresh["id"]}')
        assert completed.payload['output'] == f'{published.id}:{rendered["output_path"]}'
        assert completed.payload['request_id'] == fresh['id']
        assert len(execution.jobs('local', task_id)) == 1
    assert guarded
    assert repo.get_task_event('local', old_output.id) == old_output
    assert repo.get_task_event('local', settled.id) == settled
    assert repo.creation_narration_usage('local', job.id) == usage
    assert repo.get_narration_artifact('local', 'cache_1') == audio
    assert hashlib.sha256(Path(audio.audio_path).read_bytes()).hexdigest() == audio_sha
    assert scheduler._get(original_request).state == 'done'


@pytest.mark.parametrize('revision', [False, True])
def test_known_visual_planning_conflict_keeps_paid_audio_and_original_resume(tmp_path, monkeypatch, revision):
    from app.creation.duration_requirement import DurationConflict
    from app.creation_execution import next_creation_intent
    case = _visual_decision_case(tmp_path, monkeypatch)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    if revision:
        for _ in range(2):
            case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
        for row in case.repo.list_creation_job_request_states('local', case.job.id):
            if row['state'] in {'waiting', 'blocked'}:
                case.scheduler.cancel(row['id'], case.actor)
        before, receipt, runtime = _submit_section_revision(case)
        job = case.repo.get_job_run('local', receipt['job_id'])
    else:
        job = case.job
    original = case.segments.prepare_video
    def conflict(*args, **kwargs):
        raise DurationConflict('已付配音可用；当前合法画面不足以覆盖目标，请补充素材后恢复原请求')
    monkeypatch.setattr(case.segments, 'prepare_video', conflict)
    grant = case.scheduler.pick_and_grant()
    case.executor.execute(grant, case.scheduler)
    updated = case.repo.get_job_run('local', job.id)
    assert updated.status == 'queued' and updated.failure_reason == 'waiting_for_user', updated.failure_reason
    assert case.scheduler._get(grant.request_id).state == 'done'
    view = case.segments.read_visual_decision('local', case.task_id, job.id, 'section_1', case.actor)
    assert view['status'] == 'planning_conflict' and not view['can_adopt']
    assert view['candidate_clips'] == [] and view['audio']['persisted']
    assert view['audio']['artifact_reference'] == view['kept_audio_reference']
    assert {'adopted', 'request_id', 'revision_request_id', 'expected_segment_version', 'candidate_offset',
        'next_offset', 'confirmed_clip_ids', 'device_request_id'} <= view.keys()
    assert not view['adopted'] and view['next_offset'] is None and view['confirmed_clip_ids'] == []
    locators = case.segments.visual_decision_locators('local', case.task_id, case.actor, job_id=job.id)
    assert locators['total_count'] == len(locators['items']) == 1
    assert locators['items'][0]['decision_digest'] is None
    assert view['usage']['settled'] and view['usage']['used_output_bytes'] > 0
    assert case.scheduler.pick_and_grant() is None
    saved_usage = dict(view['usage'])
    monkeypatch.setattr(case.segments, 'prepare_video', original)
    _require_section_visual_decision(case, monkeypatch)
    if revision:
        monkeypatch.setattr(case.stage, 'prepare_revision', lambda *a, **k: pytest.fail('paid audio regenerated'))
        case.segments.resume_request('local', case.task_id, 'revision_1', case.actor, runtime=runtime)
    else:
        assert case.repo.list_narration_segments('local', case.task_id, case.script_id)[0].status == 'ready'
        monkeypatch.setattr(case.stage, 'prepare_segment', lambda *a, **k: pytest.fail('paid audio regenerated'))
        intent = next_creation_intent(case.repo, case.scheduler, updated)
        assert intent is None
    candidate = case.segments.read_visual_decision('local', case.task_id, job.id, 'section_1', case.actor)
    assert candidate['status'] == 'waiting_for_user'
    locators = case.segments.visual_decision_locators('local', case.task_id, case.actor, job_id=job.id)
    assert locators['total_count'] == len(locators['items']) == 1
    assert locators['items'][0]['decision_digest'] == candidate['decision_digest']
    case.segments.adopt_visual_decision('local', case.task_id, job.id, 'section_1', case.actor,
        decision_digest=candidate['decision_digest'], confirmed_clip_ids=candidate['required_confirmation_ids'],
        runtime=runtime if revision else SimpleNamespace(scheduler=case.scheduler, wake=lambda: None,
            registrations={'creation_job': SimpleNamespace(owner=case.owner)}))
    video = case.scheduler.pick_and_grant()
    assert video.owner_id == job.id and video.unit_key.startswith('segment_video:')
    assert case.segments.visual_usage(updated) == saved_usage
    case.scheduler.cancel(video.request_id, case.actor)
    case.scheduler.close()


def test_revision_planning_resume_preserves_cancellation_during_preparation(tmp_path, monkeypatch):
    from app.creation.duration_requirement import DurationConflict
    case = _visual_decision_case(tmp_path, monkeypatch)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(2):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    for row in case.repo.list_creation_job_request_states('local', case.job.id):
        if row['state'] in {'waiting', 'blocked'}:
            case.scheduler.cancel(row['id'], case.actor)
    _, receipt, runtime = _submit_section_revision(case)
    job = case.repo.get_job_run('local', receipt['job_id'])
    prepare = case.segments.prepare_video
    def conflict(*args, **kwargs):
        raise DurationConflict('合法画面待补充，原配音保留')
    monkeypatch.setattr(case.segments, 'prepare_video', conflict)
    case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    usage = case.segments.visual_usage(job)
    def cancel_after_preparation(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        case.repo.finish_job_run('local', job.id, status='cancelled', failure_reason='user_cancelled',
            impact='用户暂停原请求', repair_action='保持取消，不自动恢复')
        return prepared
    monkeypatch.setattr(case.segments, 'prepare_video', cancel_after_preparation)
    with pytest.raises(ValueError, match='原段落作业已停止'):
        case.segments.resume_request('local', case.task_id, 'revision_1', case.actor, runtime=runtime)
    current = case.repo.get_job_run('local', job.id)
    assert current.status == 'cancelled' and current.failure_reason == 'user_cancelled'
    assert case.segments.visual_usage(current) == usage
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', case.segments.video_preparation_id(job, 'section_1'))
    assert all(row['state'] == 'done' for row in case.repo.list_creation_job_request_states('local', job.id))
    case.scheduler.close()


def test_pcm_three_declared_inputs_merge_once_without_new_job_or_audio(tmp_path):
    case = _narration_units_case(tmp_path, section_count=3, audio_input_count=3)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(3):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    merge = case.scheduler.pick_and_grant()
    declared = case.scheduler._get(merge.request_id).resources
    assert declared['input_count'] == len(declared['inputs']) == len(declared['pcm_sources']) == 3
    assert declared['merge_final'] and declared['merge_section_ids'] == ['section_1', 'section_2', 'section_3']
    case.executor.execute(merge, case.scheduler)
    task = case.repo.get_creation_task('local', case.task_id)
    assert case.repo.get_narration_artifact('local', task.narration_cache_key).duration_seconds == 3
    assert len(case.merged) == 1 and len(case.merged[0]) == 3 and len(case.generated) == 3
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_output_bytes'] == 3 * 48044 + 3 * 48000 + 44
    assert not usage['unsettled'] and len(case.execution.jobs('local', case.task_id)) == 1
    case.scheduler.close()


def test_pcm_sealed_two_input_unit_survives_larger_cap_and_prefix_uses_one_slot(tmp_path, monkeypatch):
    case = _narration_units_case(tmp_path, section_count=5, audio_input_count=2)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(5):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    first = case.scheduler.pick_and_grant()
    request = case.scheduler._get(first.request_id)
    assert request.resources['input_count'] == 2
    sealed = dict(request.resources)
    case.owner.narration_audio_budget = replace(case.owner.narration_audio_budget, max_input_count=3)
    with case.repo._connect() as conn:
        assert case.owner.pcm_assembly_baseline(request, conn) == sealed['pcm_assembly']
    assert case.scheduler._get(first.request_id).resources == sealed
    prepare = case.stage.prepare_merge
    def fail_before_output(*args, **kwargs):
        raise RuntimeError('known pre-output failure')
    monkeypatch.setattr(case.stage, 'prepare_merge', fail_before_output)
    case.executor.execute(first, case.scheduler)
    assert case.repo.get_job_run('local', case.job.id).status == 'failed'
    monkeypatch.setattr(case.stage, 'prepare_merge', prepare)
    case.scheduler.resume_creation_narration(first.request_id, resume_write=lambda conn, current:
        case.owner.resume_narration_request(conn, current, case.actor))
    retried = case.scheduler.pick_and_grant()
    assert retried.request_id == first.request_id
    assert case.scheduler._get(retried.request_id).resources == sealed
    case.executor.execute(retried, case.scheduler)
    following = case.scheduler.pick_and_grant()
    declared = case.scheduler._get(following.request_id).resources
    assert declared['input_count'] == len(declared['inputs']) == 3
    assert len(declared['pcm_sources']) == 2
    assert declared['merge_section_ids'] == ['section_1', 'section_2', 'section_3', 'section_4']
    assert not declared['merge_final']
    case.executor.execute(following, case.scheduler)
    last = case.scheduler.pick_and_grant()
    final = case.scheduler._get(last.request_id).resources
    assert final['input_count'] == 2 and final['merge_final']
    case.executor.execute(last, case.scheduler)
    task = case.repo.get_creation_task('local', case.task_id)
    assert case.repo.get_narration_artifact('local', task.narration_cache_key).duration_seconds == 5
    assert [len(unit) for unit in case.merged] == [2, 3, 2]
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_output_bytes'] == 5 * 48044 + 96044 + 96008 + 48008
    assert not usage['unsettled'] and len(case.generated) == 5
    case.scheduler.close()


def test_local_goal_twenty_sections_keep_finite_units_without_task_total_cap(tmp_path):
    case = _narration_units_case(tmp_path, section_count=20, audio_input_count=5, local_policy=True)
    start = case.repo.get_task_event('local', f'event_{case.job.id}_start')
    assert start.payload['execution_budget_policy']['policy_kind'] == 'scope'
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for _ in range(20):
        grant = case.scheduler.pick_and_grant()
        request = case.scheduler._get(grant.request_id)
        assert request.resources['section_count'] == 1
        assert request.resources['timeout_seconds'] <= case.owner.narration_budget.max_timeout_seconds
        assert request.resources['max_output_bytes'] <= case.owner.narration_budget.max_output_bytes
        assert request.resources['parent_timeout_seconds'] is None
        assert request.resources['parent_max_output_bytes'] is None
        case.executor.execute(grant, case.scheduler)
    while case.repo.get_creation_task('local', case.task_id).narration_cache_key is None:
        grant = case.scheduler.pick_and_grant()
        assert grant.unit_key.startswith('narration:merge:')
        assert case.scheduler._get(grant.request_id).resources['input_count'] <= 5
        case.executor.execute(grant, case.scheduler)
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['budget']['parent_timeout_seconds'] is None
    assert usage['budget']['parent_max_output_bytes'] is None
    assert usage['used_seconds'] > 0 and usage['used_output_bytes'] > 0 and not usage['unsettled']
    scope = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    assert scope['execution_seconds'] == pytest.approx(usage['used_seconds'])
    assert scope['generated_bytes'] == usage['used_output_bytes']
    assert scope['reserved_seconds'] == scope['reserved_bytes'] == 0
    assert scope['unit_count'] > 20
    assert len(case.generated) == 20
    assert all(item.status == 'ready' for item in case.repo.list_narration_segments('local', case.task_id, case.script_id))
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.repo.get_task_event('local', start.id) == start
    case.scheduler.close()


def test_scope_shrink_before_real_grant_start_rolls_back_started_and_pending_segments(tmp_path):
    from app.creation.execution_budget_scope import BudgetScopeExhausted
    case = _narration_units_case(tmp_path, local_policy=True)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    grant = case.scheduler.pick_and_grant()
    resources = case.scheduler._get(grant.request_id).resources
    case.repo.authorize_execution_budget_scope('local', case.task_id, case.actor,
        scope_id='isolated-local-goal', expected_revision=0, request_id='explicit-limit-after-grant',
        source_ref='isolated-test:smaller-explicit-limit', target_task_ids=[case.task_id],
        limits={'execution_seconds': resources['timeout_seconds'] - 1, 'generated_bytes': None},
        policy_kind='explicit_total', authorization_guard=lambda conn: None)
    with pytest.raises(BudgetScopeExhausted):
        case.executor.execute(grant, case.scheduler)
    assert case.repo.get_job_run('local', case.job.id).status == 'queued'
    assert not case.repo.creation_narration_usage('local', case.job.id)['unsettled']
    assert case.repo.creation_narration_usage('local', case.job.id)['budget'] is None
    assert case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']['unit_count'] == 0
    assert case.generated == []
    case.scheduler.close()


def test_legacy_tts_sealed_parent_declaration_keeps_actual_remaining_context(tmp_path, monkeypatch):
    case = _narration_units_case(tmp_path)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    used = case.repo.creation_narration_usage('local', case.job.id)
    grant = case.scheduler.pick_and_grant()
    original = case.scheduler._get(grant.request_id)
    assert original.resources['max_output_bytes'] == used['budget']['parent_max_output_bytes']
    assert original.resources['timeout_seconds'] == used['budget']['parent_timeout_seconds']
    assert used['used_output_bytes'] > 0
    prepare = case.stage.prepare_segment
    def bounded(*args, **kwargs):
        context = kwargs['grant_context']
        assert context.max_output_bytes == used['budget']['parent_max_output_bytes'] - used['used_output_bytes']
        return prepare(*args, **kwargs)
    monkeypatch.setattr(case.stage, 'prepare_segment', bounded)
    case.executor.execute(grant, case.scheduler)
    assert len(case.generated) == 2
    assert case.scheduler._get(original.id).resources == original.resources
    case.scheduler.close()


def test_experiment_narration_env_cannot_forge_prepared_receipts():
    from dataclasses import fields
    from app.member_permits import creation_stage_budgets_from_env, HostBudgetUncalibrated
    budget = NarrationBudget(374, 3, 1_000_000, 30, 3*1024*1024, 1, 300, 1024, 16, .1, 2048, 1024,
        input_scope='single_call_experiment_v1')
    metadata = {'running_cpu_policy', 'input_scope', 'preparation_backend_identity',
        'preparation_shape_digest', 'preparation_receipt_sha256'}
    env = {'DEVICE_NARRATION_' + item.name.upper(): str(getattr(budget, item.name))
        for item in fields(budget) if item.name not in metadata}
    env.update(DEVICE_NARRATION_INPUT_SCOPE='single_call_experiment_v1',
        DEVICE_NARRATION_PREPARATION_BACKEND_IDENTITY='a'*64,
        DEVICE_NARRATION_PREPARATION_SHAPE_DIGEST='b'*64,
        DEVICE_NARRATION_PREPARATION_RECEIPT_SHA256='c'*64)
    actual, _ = creation_stage_budgets_from_env(env)
    assert actual.input_scope == 'single_call_experiment_v1'
    assert actual.preparation_backend_identity is actual.preparation_shape_digest is actual.preparation_receipt_sha256 is None
    with pytest.raises(HostBudgetUncalibrated, match='准备回执'):
        creation_stage_budgets_from_env({**env, 'DEVICE_NARRATION_INPUT_SCOPE': 'prepared'})


def _bounded_narration_start_case(tmp_path, monkeypatch):
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation.execution_control import CreationExecutionControl
    case = _narration_units_case(tmp_path, section_count=45, start_job=False)
    repo = case.repo
    with repo._connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
    task = repo.get_creation_task('local', case.task_id)
    case.digest = revision_digest(repo.get_creation_task_script('local', case.script_id),
        repo.get_creation_plan('local', task.plan_id))
    case.owner.task_authorizations = TaskAuthorizationUseCase(repo, lambda identity: 'ready')
    case.owner.segments = SegmentProductionUseCase(repo, narration_stage=case.stage,
        renderer=None, artifact_store=None, output_root=tmp_path/'unused-output')
    case.control = CreationExecutionControl(repo, case.execution, SimpleNamespace(valid=lambda *a, **k: False),
        lambda actor: 'ready', segments=case.owner.segments)
    case.runtime = SimpleNamespace(registrations={'creation_job': SimpleNamespace(owner=case.owner, executor=case.executor)},
        scheduler=case.scheduler, wake=lambda: None)
    def no_full(*args, **kwargs):
        raise AssertionError('pending narration admission must not hydrate or collect complete script/audio text')
    for name in ('get_creation_task_script', '_creation_task_script_from_payload',
                 'effective_creation_script', 'list_narration_segments'):
        monkeypatch.setattr(repo, name, no_full)
    monkeypatch.setattr(case.execution, 'validate_input', no_full)
    return case


def test_bounded_narration_start_reenters_original_unit_and_checks_current(tmp_path, monkeypatch):
    case = _bounded_narration_start_case(tmp_path, monkeypatch)
    seen = []
    original = case.repo.iter_creation_narration_sections
    def scanned(*args, **kwargs):
        for section, segment in original(*args, **kwargs):
            seen.append(section.id)
            yield section, segment
    monkeypatch.setattr(case.repo, 'iter_creation_narration_sections', scanned)
    def start():
        return case.control.start('local', case.task_id, case.script_id, case.digest,
            case.actor, case.runtime, verify_media=False)
    first = start()
    job = case.repo.get_job_run('local', first['job']['id'])
    states = case.repo.list_creation_job_request_states('local', job.id)
    request = case.scheduler._get(states[0]['id'])
    assert request.unit_key.startswith('narration:segment:v1:section_1:')
    assert request.resources['section_count'] == 1 and request.resources['text_chars'] >= 260
    assert 'section_45' in seen
    assert start()['job']['id'] == job.id
    assert [row['id'] for row in case.repo.list_creation_job_request_states('local', job.id)] == [request.id]
    with case.repo.creation_execution_read() as conn:
        assert case.owner.check_current(request, conn) == 'ready'
        assert conn.in_transaction
        local = case.owner.capture_local(request, conn)
        assert local(conn) == 'ready'
    with case.scheduler._connect() as idle:
        assert case.owner.check_current(request, idle) == 'ready'
        assert not idle.in_transaction
        assert idle.execute('SELECT 1').fetchone()[0] == 1
    with case.repo._connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        row = writer.execute('SELECT section_json FROM creation_task_script_sections WHERE section_id=?', ('section_45',)).fetchone()
        changed = json.loads(row['section_json']); changed['content'] = '事务内未提交尾段'
        writer.execute('UPDATE creation_task_script_sections SET section_json=? WHERE section_id=?',
            (json.dumps(changed), 'section_45'))
        assert case.owner.check_current(request, writer) == 'stale'
        from app.device_scheduler import SubmissionIntent
        with pytest.raises(PermissionError, match='stale'):
            case.owner.verify_submission(SubmissionIntent('local', 'creation_job', job.id, case.digest,
                request.unit_key, request.policy), case.actor, connection=writer)
        assert writer.in_transaction
        writer.rollback()
        assert case.owner.check_current(request, writer) == 'ready'
    assert case.generated == [] and case.merged == []
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.repo.creation_narration_source_binding('local', job.id).payload['business_input_digest'] == case.digest


def test_bounded_narration_start_unknown_and_other_member_keep_original(tmp_path, monkeypatch):
    from app.creation.contracts import TaskEvent
    from app.creation.execution_control import ExecutionControlRejected
    from app.device_scheduler import RecoveryBlocked
    case = _bounded_narration_start_case(tmp_path, monkeypatch)
    result = case.control.start('local', case.task_id, case.script_id, case.digest,
        case.actor, case.runtime, verify_media=False)
    job = case.repo.get_job_run('local', result['job']['id'])
    original_requests = case.repo.list_creation_job_request_states('local', job.id)
    with pytest.raises(ExecutionControlRejected) as refused:
        case.control.start('local', case.task_id, case.script_id, case.digest,
            CreationExecutionActor('local', 'other-member'), case.runtime, verify_media=False)
    assert refused.value.status_code == 403
    pending = case.scheduler._get(original_requests[0]['id'])
    case.repo.append_task_event(TaskEvent('unsettled-original', 'local', case.task_id, job.id,
        'creation_narration_unit_started', {'usage_id': f'{pending.id}:9', 'request_id': pending.id,
            'permit_generation': 9, 'unit_key': pending.unit_key, 'segment_id': pending.resources['segment_id'],
            'section_id': pending.resources['section_id'], 'ordinal': 1, 'phase': 'segment',
            'remaining_seconds': 300, 'remaining_output_bytes': 3*1024*1024}))
    case.repo.append_task_event(TaskEvent(f'event_{job.id}_narration_budget', 'local', case.task_id, job.id,
        'creation_narration_budget', {'active_event_id': 'unsettled-original', 'settled_event_id': None,
            'parent_timeout_seconds': 300, 'parent_max_output_bytes': 3*1024*1024,
            'script_id': case.script_id, 'input_contract': 'narration_units_v1'}))
    with pytest.raises(RecoveryBlocked, match='未知'):
        submit_creation_stage(case.execution, case.scheduler, job, case.actor)
    request = case.scheduler._get(original_requests[0]['id'])
    with case.repo.creation_execution_read() as conn:
        assert case.owner.check_current(request, conn) == 'dependency'
    assert case.control.status('local', case.task_id, verify_media=False, actor=case.actor)['job']['id'] == job.id
    assert case.scheduler.pick_and_grant() is None
    assert case.scheduler._get(request.id).last_grant_generation is None
    assert [row['id'] for row in case.repo.list_creation_job_request_states('local', job.id)] == [request.id]
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.generated == [] and case.merged == []


def test_bounded_narration_start_effective_text_and_tail_audio_are_fully_checked(tmp_path, monkeypatch):
    from app.creation.contracts import NarrationSegment, TaskEvent
    from app.creation_execution import next_creation_intent
    case = _bounded_narration_start_case(tmp_path, monkeypatch)
    repo = case.repo
    with repo._connect() as conn:
        repo.append_task_event(TaskEvent('adopted-text', 'local', case.task_id, None, 'segment_publication',
            {'script_id': case.script_id, 'text': '采用后的正文', 'narration_reference': 'narration://artifact/prior'}), connection=conn)
        task = repo.get_creation_task('local', case.task_id, connection=conn)
        version = TimelineVersion(id='effective-text-version', workspace_id='local', task_id=case.task_id,
            parent_version_id=None, sequence=1, created_by_operation_id=None,
            snapshot={'stage_segments': {'section_1': 'adopted-text'}})
        repo.create_timeline_version(version, connection=conn)
        task.current_timeline_version_id = version.id
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    assert repo.read_creation_execution_snapshot('local', case.task_id).input_digest == case.digest
    result = case.control.start('local', case.task_id, case.script_id, case.digest,
        case.actor, case.runtime, verify_media=False)
    job = repo.get_job_run('local', result['job']['id'])
    request = case.scheduler._get(repo.list_creation_job_request_states('local', job.id)[0]['id'])
    inputs = repo.resolve_creation_task_narration_inputs(workspace_id='local', task_id=case.task_id,
        voice_profile_id=request.resources['voice_profile_id'], voice_version=request.resources['voice_version'])
    assert request.resources['text_chars'] == len('采用后的正文') + len(inputs.voice.reference_text)
    # Existing fixture reference bytes stand in for an already recorded artifact; no worker runs.
    record = NarrationArtifactRecord(id='tail-audio', workspace_id='local', task_id=case.task_id,
        script_id=case.script_id, voice_profile_id=request.resources['voice_profile_id'], cache_key='tail-cache',
        audio_path=request.resources['narration_reference']['reference']['source_path'],
        duration_seconds=1, sample_rate=24000, model_id='test',
        pronunciation_version=request.resources['pronunciation_version'])
    repo.save_narration_artifact(record)
    segment = NarrationSegment(id=case.stage.segment_id(case.task_id, case.script_id, 'section_45'),
        workspace_id='local', task_id=case.task_id, script_id=case.script_id, source_section_id='section_45',
        text='原文'*130, audio_reference=repo.narration_artifact_reference(record.cache_key), duration_seconds=1,
        sample_rate=24000, model_id='test', voice_profile_id=request.resources['voice_profile_id'],
        voice_version=request.resources['voice_version'], language='zh',
        pronunciation_version=request.resources['pronunciation_version'], status='ready')
    with repo._connect() as conn:
        values = asdict(segment)
        values['created_at'] = segment.created_at.isoformat()
        values['updated_at'] = segment.updated_at.isoformat()
        conn.execute('INSERT INTO narration_segments (' + ','.join(values) + ') VALUES ('
            + ','.join('?' for _ in values) + ')', tuple(values.values()))
    assert next_creation_intent(repo, case.scheduler, job).unit_key == request.unit_key
    for field, value in [('text', '错误尾段正文'), ('voice_version', 'wrong-voice'),
                         ('pronunciation_version', 'wrong-dictionary')]:
        with repo._connect() as conn:
            conn.execute(f'UPDATE narration_segments SET {field}=? WHERE id=?', (value, segment.id))
        with pytest.raises(ValueError, match='配音片段'):
            next_creation_intent(repo, case.scheduler, job)
        with repo.creation_execution_read() as conn:
            assert case.owner.check_current(request, conn) == 'dependency'
        with repo._connect() as conn:
            conn.execute(f'UPDATE narration_segments SET {field}=? WHERE id=?', (getattr(segment, field), segment.id))
    # Identity is immutable: construct an extra orphan row, without disabling its trigger.
    with repo._connect() as conn:
        orphan = {**values, 'id': 'orphan-audio', 'source_section_id': 'foreign-section'}
        conn.execute('INSERT INTO narration_segments (' + ','.join(orphan) + ') VALUES ('
            + ','.join('?' for _ in orphan) + ')', tuple(orphan.values()))
    with pytest.raises(ValueError, match='配音片段集'):
        next_creation_intent(repo, case.scheduler, job)
    with repo.creation_execution_read() as conn:
        assert case.owner.check_current(request, conn) == 'dependency'
    with repo._connect() as conn:
        conn.execute('DELETE FROM narration_segments WHERE id=?', ('orphan-audio',))
        prefix = {**values, 'id': case.stage.segment_id(case.task_id, case.script_id, 'section_1'),
            'source_section_id': 'section_1', 'text': '采用后的正文'}
        conn.execute('INSERT INTO narration_segments (' + ','.join(prefix) + ') VALUES ('
            + ','.join('?' for _ in prefix) + ')', tuple(prefix.values()))
        repo.append_task_event(TaskEvent(case.owner.segments.visual_decision_id(job, 'section_1'),
            'local', case.task_id, job.id, 'segment_visual_decision_required', {'section_id': 'section_1'}), connection=conn)
    assert next_creation_intent(repo, case.scheduler, job) is None  # Do not skip an earlier confirmation gate.
    repo.append_task_event(TaskEvent(case.owner.segments.video_preparation_id(job, 'section_1'),
        'local', case.task_id, job.id, 'segment_video_prepared', {'section_id': 'section_1'}))
    def video_facts(current_job, event, task, section_id, owner, **kwargs):
        assert current_job.id == job.id and section_id == 'section_1'
        return 'segment_video:section_1:prepared', {}
    monkeypatch.setattr(case.owner.segments, 'video_facts', video_facts)
    assert next_creation_intent(repo, case.scheduler, job).unit_key == 'segment_video:section_1:prepared'
    assert repo.read_creation_execution_snapshot('local', case.task_id).input_digest == case.digest
    assert len(repo.list_creation_job_request_states('local', job.id)) == 1
    assert case.generated == [] and case.merged == []


def test_owner_idle_observation_survives_reader_close_and_borrows_writer(tmp_path):
    case = _queued_narration_voice_selection_case(tmp_path)
    request = case.scheduler._get(case.request_id)
    with case.repo._connect() as idle:
        observed = case.owner.capture_local(request, idle)
        assert case.owner.owns_task_stage(idle, 'local', case.job.id, 'narration')
        assert not idle.in_transaction
        assert idle.execute('SELECT 1').fetchone()[0] == 1
    # capture_local has closed its own snapshot; the returned observer must use
    # the caller's later transaction and see its uncommitted owner changes.
    with case.repo._connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert observed(writer) == 'ready'
        writer.execute("UPDATE job_runs SET status='cancelled' WHERE id=?", (case.job.id,))
        assert observed(writer) == 'stale'
        assert not case.owner.owns_task_stage(writer, 'local', case.job.id, 'narration')
        assert writer.in_transaction
        writer.rollback()
        assert observed(writer) == 'ready'
    assert case.generated == [] and case.merged == []
    assert case.repo.get_job_run('local', case.job.id) == case.job
    case.scheduler.close()


def test_owner_first_authorization_precedes_submission_snapshot(tmp_path):
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import next_creation_intent
    case = _narration_units_case(tmp_path)
    with case.repo._connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
    case.owner.task_authorizations = TaskAuthorizationUseCase(case.repo, lambda identity: 'ready')
    assert case.repo.get_task_authorization('local', case.task_id) is None
    intent = next_creation_intent(case.repo, case.scheduler, case.job)
    with pytest.raises(PermissionError, match='输入摘要'):
        case.owner.verify_submission(replace(intent, input_digest='different-input'), case.actor)
    with pytest.raises(PermissionError, match='原身份'):
        case.owner.verify_submission(intent, CreationExecutionActor('local', 'other-member'))
    assert case.repo.get_task_authorization('local', case.task_id) is None
    verified = case.owner.verify_submission(intent, case.actor)
    assert verified.owner_id == case.job.id and verified.input_digest == case.job.input_digest
    assert case.repo.get_task_authorization('local', case.task_id) is not None
    assert case.generated == [] and case.merged == []
    case.scheduler.close()


@pytest.mark.parametrize('change_during_authorization', [False, True])
def test_owner_submission_rechecks_after_authorization_and_borrows_transaction(tmp_path, monkeypatch,
        change_during_authorization):
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import next_creation_intent
    case = _narration_units_case(tmp_path)
    intent = next_creation_intent(case.repo, case.scheduler, case.job)
    def verify(identity):
        assert identity == TaskAuthorizationUseCase.identity(case.actor)
        # External observation can write after preflight has closed its reader.
        with case.repo._connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            if change_during_authorization:
                row = writer.execute('SELECT section_json FROM creation_task_script_sections WHERE section_id=?',
                    ('section_2',)).fetchone()
                value = json.loads(row['section_json']); value['content'] = '授权观察期间尾段变化'
                writer.execute('UPDATE creation_task_script_sections SET section_json=? WHERE section_id=?',
                    (json.dumps(value), 'section_2'))
        return 'ready'
    case.owner.task_authorizations = TaskAuthorizationUseCase(case.repo, verify)
    if change_during_authorization:
        with pytest.raises(PermissionError, match='stale'):
            case.owner.verify_submission(intent, case.actor)
    else:
        verified = case.owner.verify_submission(intent, case.actor)
        def no_external_authorization(*args, **kwargs):
            raise AssertionError('borrowed transaction must only recheck current authorization')
        monkeypatch.setattr(case.owner.task_authorizations, 'ensure', no_external_authorization)
        with case.repo._connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            assert case.owner.verify_submission(intent, case.actor, connection=writer) == verified
            writer.execute("UPDATE job_runs SET status='cancelled' WHERE id=?", (case.job.id,))
            with pytest.raises(PermissionError, match='stale'):
                case.owner.verify_submission(intent, case.actor, connection=writer)
            assert writer.in_transaction
            writer.rollback()
        assert case.repo.get_job_run('local', case.job.id) == case.job
    assert case.repo.list_creation_job_request_states('local', case.job.id) == []
    assert case.generated == [] and case.merged == []
    case.scheduler.close()


def _ready_pcm_body_case(tmp_path, monkeypatch):
    """Already paid synthetic audio/SQL facts; no generator or worker executes."""
    import wave
    from app.creation.contracts import NarrationSegment, TaskEvent
    from app.narration import _pronunciation_dictionary, _pronunciation_version
    case = _narration_units_case(tmp_path, section_count=3, audio_input_count=2, pcm_inprocess=False)
    repo = case.repo
    task = repo.get_creation_task('local', case.task_id)
    script = repo.get_creation_task_script('local', case.script_id)
    plan = repo.get_creation_plan('local', task.plan_id)
    inputs = repo.resolve_creation_task_narration_inputs(workspace_id='local', task_id=task.id,
        voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
    pronunciation = _pronunciation_version(_pronunciation_dictionary(entries=inputs.pronunciation_entries,
        workspace_id='local', person_profile_id=inputs.voice.person_profile_id, task_id=task.id))
    segments = []
    for section in script.sections:
        path = tmp_path / (section.id + '.wav')
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x01\x00' * 24)
        key = 'ready-' + section.id
        record = NarrationArtifactRecord(id=key, workspace_id='local', task_id=task.id, script_id=script.id,
            voice_profile_id=inputs.voice.id, cache_key=key, audio_path=str(path), duration_seconds=.001,
            sample_rate=24000, model_id='test', language='zh', pronunciation_version=pronunciation)
        repo.save_narration_artifact(record)
        segments.append(NarrationSegment(id=case.stage.segment_id(task.id, script.id, section.id),
            workspace_id='local', task_id=task.id, script_id=script.id, source_section_id=section.id,
            text=section.content, audio_reference=repo.narration_artifact_reference(key), duration_seconds=.001,
            sample_rate=24000, model_id='test', voice_profile_id=inputs.voice.id, voice_version=inputs.voice.version,
            language='zh', pronunciation_version=pronunciation, status='ready'))
    repo.save_narration_segments('local', task.id, script.id, segments)
    effective = '采用正文 "quoted"\n\\ café 😺'
    with repo._connect() as conn:
        repo.append_task_event(TaskEvent('pcm-adopted-text', 'local', task.id, None, 'segment_publication',
            {'script_id': script.id, 'text': effective, 'narration_reference': segments[0].audio_reference}), connection=conn)
        version = TimelineVersion('pcm-effective-version', 'local', task.id, None, 1,
            {'stage_segments': {'section_1': 'pcm-adopted-text'}}, None)
        repo.create_timeline_version(version, connection=conn)
        task.current_timeline_version_id = version.id
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
        conn.execute('UPDATE narration_segments SET text=? WHERE id=?', (effective, segments[0].id))
    case.original_segments = [asdict(row) for row in repo.list_narration_segments('local', task.id, script.id)]
    case.sources = [str(tmp_path / (section.id + '.wav')) for section in script.sections]
    def forbid_worker(*args, **kwargs):
        pytest.fail('PCM body migration must not run a model, worker or media process')
    monkeypatch.setattr(case.stage.generator, 'generate_for_creation_task', forbid_worker)
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', forbid_worker)
    for name in ('get_creation_task_script', '_creation_task_script_from_payload',
                 'effective_creation_script', 'list_narration_segments'):
        original = getattr(repo, name)
        def bounded(*args, _original=original, **kwargs):
            current = repo.get_creation_task('local', task.id, connection=kwargs.get('connection'))
            if current.stage == 'narration':
                pytest.fail('PCM must not hydrate whole script/segment bodies')
            # Following montage is explicitly outside this migration.
            return _original(*args, **kwargs)
        monkeypatch.setattr(repo, name, bounded)
    return case


def _pcm_fixture_receipt(context, resources):
    """Finite sealed-file fixture, not the PCM worker or source concatenator."""
    import struct
    baseline = resources['pcm_assembly']
    work = Path(baseline['work_path'])
    work.parent.mkdir(parents=True, exist_ok=True)
    added = sum(item['pcm_frames'] * 2 for item in resources['pcm_sources'])
    size = baseline['committed_pcm_bytes'] + added
    data = b'\x01\x00' * (size // 2)
    header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', size+36, b'WAVE', b'fmt ',
        16, 1, 1, 24000, 48000, 2, 16, b'data', size)
    work.write_bytes(header + data)
    info = work.stat()
    now = datetime.now(timezone.utc).isoformat()
    receipt = {'version': 1, 'unit_id': f'{context.grant.request_id}:{context.grant.generation}',
        'job_id': context.grant.owner_id, 'source_digest': resources['merge_source_digest'],
        'work_path': str(work), 'previous_pcm_bytes': baseline['committed_pcm_bytes'],
        'pcm_bytes': size, 'appended_pcm_bytes': added, 'header_bytes_written': baseline['header_bytes_allowance'],
        'generated_bytes': added+baseline['header_bytes_allowance'], 'file_size': size+44,
        'source_count': len(resources['pcm_sources']) + int(baseline['committed_pcm_bytes'] > 0),
        'finalized': resources['merge_final'], 'format': {'channels': 1, 'sample_rate': 24000, 'sample_width': 2},
        'file_identity': {'device': info.st_dev, 'inode': info.st_ino},
        'pcm_sha256': hashlib.sha256(data).hexdigest(), 'started_at_utc': now, 'finished_at_utc': now,
        'elapsed_seconds': 0.0}
    Path(baseline['receipt_path']).write_text(json.dumps(receipt))
    if receipt['finalized']:
        Path(str(work)+'.sealed.json').write_text(json.dumps(receipt))
    context.narration_output_usage[0] += receipt['generated_bytes']
    return receipt


def test_pcm_body_stream_preserves_original_encoding_and_keys(tmp_path, monkeypatch):
    from app.creation import narration_pcm
    case = _ready_pcm_body_case(tmp_path, monkeypatch)
    preparation = case.owner.prepare_pcm_sources(case.job)
    with case.repo.creation_execution_read() as conn:
        snapshot = case.repo.read_creation_execution_snapshot('local', case.task_id, connection=conn)
        rows = [segment for _, segment in case.repo.iter_creation_narration_sections(
            snapshot.task, snapshot.script_header, connection=conn)]
        fields = {'script_id': case.script_id, 'voice': {'text': '中文\n"\\'}, 'source_files': [['a', 4]],
            'reference_input': {'at': rows[0].created_at}}
        for ordered in (rows[:1], rows, list(reversed(rows))):
            legacy = json.dumps({**fields, 'segments': [asdict(row) for row in ordered]}, sort_keys=True, default=str).encode()
            assert b''.join(narration_pcm.pcm_manifest_chunks(fields, iter(ordered))) == legacy
        args = (conn, case.job, snapshot.task, snapshot.script_header, snapshot.plan)
        actual = case.owner.narration_unit_facts(*args, pcm_preparation=preparation)
        def legacy_digest(fields, segments, *, resources=None):
            manifest = {**fields, 'segments': [asdict(row) for row in segments]}
            return hashlib.sha256(json.dumps(manifest if resources is None else [manifest, resources],
                sort_keys=True, default=str).encode()).hexdigest()
        with monkeypatch.context() as patch:
            patch.setattr(narration_pcm, 'pcm_manifest_digest', legacy_digest)
            assert case.owner.narration_unit_facts(*args, pcm_preparation=preparation) == actual
        receipt = {'finalized': True, 'source_digest': actual[1]['merge_source_digest'], 'work_path': 'fixture.wav', 'pcm_bytes': 144}
        # Edit adoption still passes a list, possibly with a replaced row.
        for ordered in (rows, [replace(rows[0], audio_reference='narration://artifact/replacement'), *rows[1:]]):
            expected = hashlib.sha256('\n'.join(row.audio_reference.removeprefix('narration://artifact/')
                for row in ordered).encode()).hexdigest()
            assert case.stage.pcm_record(snapshot.task, actual[1], receipt, iter(ordered)).cache_key == expected
            assert case.stage.pcm_record(snapshot.task, actual[1], receipt, ordered).cache_key == expected
    case.scheduler.close()


@pytest.mark.parametrize('outcome', ['normal', 'unknown', 'tail', 'source', 'revoked'])
def test_pcm_body_stream_commit_and_original_unknown_recovery(tmp_path, monkeypatch, outcome):
    from app.creation_execution import CreationStageGrantContext
    from app.device_scheduler import RecoveryBlocked
    case = _ready_pcm_body_case(tmp_path, monkeypatch)
    monkeypatch.setattr(CreationStageGrantContext, 'run_pcm_assembly', _pcm_fixture_receipt)
    submitted = submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    if outcome == 'normal':
        original = case.scheduler._get(submitted)
        case.owner.narration_audio_budget = replace(case.owner.narration_audio_budget, max_input_count=3)
        with monkeypatch.context() as patch:
            patch.setattr(case.owner, 'prepare_pcm_sources', lambda *a, **k: pytest.fail('reentry must use sealed sources'))
            assert submit_creation_stage(case.execution, case.scheduler, case.job, case.actor) == submitted
        assert case.scheduler._get(submitted).resources == original.resources
        assert len(case.repo.list_creation_job_request_states('local', case.job.id)) == 1
    first = case.scheduler.pick_and_grant()
    assert first is not None
    sealed = case.scheduler._get(first.request_id).resources
    case.executor.execute(first, case.scheduler)
    checkpoint = case.repo.creation_narration_merge_checkpoint('local', case.job.id)
    assert checkpoint['section_ids'] == ['section_1', 'section_2']
    assert checkpoint['source_digest'] == sealed['merge_source_digest']
    assert case.repo.get_creation_task('local', case.task_id).narration_cache_key is None
    final = case.scheduler.pick_and_grant()
    assert final is not None
    original_complete = case.repo.complete_creation_task_narration
    if outcome == 'revoked':
        # A legitimate downstream confirmation gate may produce no next unit;
        # narration adoption cannot rely on the next owner's authorization.
        monkeypatch.setattr('app.creation_execution.montage_waiting_for_confirmation',
            lambda repo, task: task.stage == 'montage')
    if outcome == 'unknown':
        def rollback(*args, **kwargs):
            original_complete(*args, **kwargs)
            raise RuntimeError('sealed fixture: lose final owner commit')
        monkeypatch.setattr(case.repo, 'complete_creation_task_narration', rollback)
    elif outcome in {'tail', 'source', 'revoked'}:
        # Mutate after outside observation inside the actual owner transaction.
        original_released = case.scheduler._released_for_commit
        injected = []
        def changed(conn, grant):
            result = original_released(conn, grant)
            if not injected and grant.request_id == final.request_id:
                injected.append(True)
                if outcome == 'tail':
                    conn.execute("UPDATE creation_task_script_sections SET section_json=json_set(section_json,'$.content',?) "
                        "WHERE script_id=? AND section_id='section_3'", ('changed tail', case.script_id))
                elif outcome == 'source':
                    path = Path(case.sources[-1]); path.write_bytes(path.read_bytes() + b'changed')
                else:
                    monkeypatch.setattr(case.owner, 'verify_identity', lambda actor: 'unauthorized')
            return result
        monkeypatch.setattr(case.scheduler, '_released_for_commit', changed)
    if outcome == 'normal':
        case.executor.execute(final, case.scheduler)
    else:
        with pytest.raises(RecoveryBlocked, match='原PCM结果未知'):
            case.executor.execute(final, case.scheduler)
        assert case.repo.get_creation_task('local', case.task_id).narration_cache_key is None
        usage = case.repo.creation_narration_usage('local', case.job.id)
        assert usage['unsettled'] and usage['used_output_bytes'] == checkpoint['pcm']['generated_bytes']
        if outcome == 'unknown':
            monkeypatch.setattr(case.repo, 'complete_creation_task_narration', original_complete)
            assert case.executor.recover_pcm_unit(case.repo.get_job_run('local', case.job.id), case.actor, case.scheduler)
    if outcome in {'normal', 'unknown'}:
        task = case.repo.get_creation_task('local', case.task_id)
        assert task.stage == 'montage' and task.narration_cache_key
        assert case.scheduler._get(final.request_id).state == 'done'
        usage = case.repo.creation_narration_usage('local', case.job.id)
        assert not usage['unsettled'] and usage['used_output_bytes'] == 196
        record = case.repo.get_narration_artifact('local', task.narration_cache_key)
        expected = hashlib.sha256('\n'.join('ready-section_'+str(i) for i in range(1, 4)).encode()).hexdigest()
        assert record.cache_key == expected and record.duration_seconds == .003
        assert [asdict(row) for row in case.repo.list_narration_segments('local', case.task_id, case.script_id)] == case.original_segments
    assert not case.generated and not case.merged
    assert len(case.execution.jobs('local', case.task_id)) == 1
    case.scheduler.close()


def _bounded_segment_video_case(tmp_path, monkeypatch):
    from app.creation.contracts import NarrationSegment
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.narration import _pronunciation_dictionary, _pronunciation_version
    case = _narration_units_case(tmp_path, section_count=23, duration_seconds=120)
    repo = case.repo
    task = repo.get_creation_task('local', case.task_id)
    script = repo.get_creation_task_script('local', case.script_id)
    plan = repo.get_creation_plan('local', task.plan_id)
    inputs = repo.resolve_creation_task_narration_inputs(workspace_id='local', task_id=task.id,
        voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
    pronunciation = _pronunciation_version(_pronunciation_dictionary(entries=inputs.pronunciation_entries,
        workspace_id='local', person_profile_id=inputs.voice.person_profile_id, task_id=task.id))
    register_synthetic_video_asset(repo, workspace_id='local', member_id=case.actor.member_id,
        path=tmp_path/'library/section.mp4', asset_id='section-video', seconds=120)
    rows = []
    for section, duration in [(script.sections[0], 2), (script.sections[-1], 30)]:
        path = tmp_path/(section.id+'.paid-audio'); path.write_bytes(b'already-paid-audio-metadata-fixture')
        record = NarrationArtifactRecord(id='paid-'+section.id, workspace_id='local', task_id=task.id,
            script_id=script.id, voice_profile_id=inputs.voice.id, cache_key='paid-'+section.id,
            audio_path=str(path), duration_seconds=duration, sample_rate=24000, model_id='test',
            language='zh', pronunciation_version=pronunciation)
        repo.save_narration_artifact(record)
        rows.append(NarrationSegment(id=case.stage.segment_id(task.id, script.id, section.id),
            workspace_id='local', task_id=task.id, script_id=script.id, source_section_id=section.id,
            text=section.content, audio_reference=repo.narration_artifact_reference(record.cache_key),
            duration_seconds=duration, sample_rate=24000, model_id='test', voice_profile_id=inputs.voice.id,
            voice_version=inputs.voice.version, language='zh', pronunciation_version=pronunciation, status='ready'))
    pending = [replace(rows[0], id=case.stage.segment_id(task.id, script.id, section.id),
        source_section_id=section.id, text=section.content, audio_reference=None,
        duration_seconds=None, sample_rate=None, status='pending') for section in script.sections[1:-1]]
    repo.save_narration_segments('local', task.id, script.id, [rows[0], *pending, rows[-1]])
    def no_execution(*args, **kwargs):
        pytest.fail('section planning must not execute a model, media worker or renderer')
    case.segments = SegmentProductionUseCase(repo, narration_stage=case.stage,
        renderer=SimpleNamespace(render=no_execution), artifact_store=ArtifactStore(tmp_path/'artifacts'),
        output_root=tmp_path/'unused-output')
    case.owner.segments = case.segments
    case.owner.cpu_render_limits = CpuRenderLimits(10_000_000, 1, 30)
    case.owner.task_authorizations = TaskAuthorizationUseCase(repo, lambda identity: 'ready')
    case.owner.task_authorizations.ensure(task, case.actor)
    case.runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda: None,
        registrations={'creation_job': SimpleNamespace(owner=case.owner)})
    def no_full(*args, **kwargs):
        pytest.fail('section planning/confirmation must not hydrate full script or all narration text')
    for name in ('get_creation_task_script', '_creation_task_script_from_payload',
                 'effective_creation_script', 'list_narration_segments'):
        monkeypatch.setattr(repo, name, no_full)
    monkeypatch.setattr(case.stage.generator, 'generate_for_creation_task', no_execution)
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', no_execution)
    case.paid_rows = rows
    return case


@pytest.mark.parametrize('legacy', [False, True])
def test_bounded_segment_video_plan_confirmation_and_original_reentry(tmp_path, monkeypatch, legacy):
    from app.creation_execution import next_creation_intent
    case = _bounded_segment_video_case(tmp_path, monkeypatch)
    assert next_creation_intent(case.repo, case.scheduler, case.job) is None
    waiting = case.repo.get_task_event('local', case.segments.visual_decision_id(case.job, 'section_1'))
    if legacy:
        original_key, original_digest = waiting.payload['unit_key'], waiting.payload['decision_digest']
        with case.repo._connect() as conn:
            conn.execute("UPDATE task_events SET payload_json=json_remove(payload_json,'$.planning_dependency_digest') WHERE id=?",
                (waiting.id,))
        waiting = case.repo.get_task_event('local', waiting.id)
        assert (waiting.payload['unit_key'], waiting.payload['decision_digest']) == (original_key, original_digest)
    window = waiting.payload['montage']['delivery_window']
    # Tail's 30 paid seconds constrain the global clock; equal text alone would
    # give the first section 5.2 seconds instead of the original 122-frame slot.
    assert window == {'section_id': 'section_1', 'start_seconds': 0, 'end_seconds': 122/30}
    assert case.segments.visual_decision_locators('local', case.task_id, case.actor)['items'][0]['section_id'] == 'section_1'
    view = case.segments.read_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor)
    assert view['status'] == 'waiting_for_user' and view['required_confirmation_ids']
    assert next_creation_intent(case.repo, case.scheduler, case.job) is None
    assert case.repo.get_task_event('local', waiting.id) == waiting
    result = case.segments.adopt_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor,
        decision_digest=view['decision_digest'], confirmed_clip_ids=view['required_confirmation_ids'], runtime=case.runtime)
    replay = case.segments.adopt_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor,
        decision_digest=view['decision_digest'], confirmed_clip_ids=view['required_confirmation_ids'], runtime=case.runtime)
    assert replay['replayed'] and result['device_request_id'] == replay['device_request_id']
    intent = next_creation_intent(case.repo, case.scheduler, case.job)
    assert case.scheduler.submit(intent, case.actor).id == result['device_request_id']
    assert len(case.repo.list_creation_job_request_states('local', case.job.id)) == 1
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.generated == [] and case.merged == []
    case.scheduler.close()


@pytest.mark.parametrize('change', ['target', 'tail', 'measured', 'authorization'])
def test_bounded_segment_video_save_rechecks_original_transaction(tmp_path, monkeypatch, change):
    from app.creation_execution import next_creation_intent
    case = _bounded_segment_video_case(tmp_path, monkeypatch)
    prepare = case.segments.prepare_video
    def change_after_plan(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        with case.repo._connect() as conn:
            if change in {'target', 'tail'}:
                section_id = 'section_1' if change == 'target' else 'section_23'
                row = conn.execute('SELECT section_json FROM creation_task_script_sections WHERE section_id=?',
                    (section_id,)).fetchone()
                data = json.loads(row['section_json']); data['content'] = '规划后已改变的目标正文'
                conn.execute('UPDATE creation_task_script_sections SET section_json=? WHERE section_id=?',
                    (json.dumps(data), section_id))
            elif change == 'measured':
                conn.execute("UPDATE narration_segments SET duration_seconds=48 WHERE task_id=? AND source_section_id='section_23'",
                    (case.task_id,))
            else:
                current = case.repo.get_task_authorization('local', case.task_id, connection=conn)
                case.repo.save_task_authorization(replace(current, revision=current.revision+1,
                    revoked_at=datetime.now(timezone.utc)), expected_revision=current.revision, connection=conn)
        return prepared
    monkeypatch.setattr(case.segments, 'prepare_video', change_after_plan)
    with pytest.raises((ValueError, PermissionError)):
        next_creation_intent(case.repo, case.scheduler, case.job)
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', case.segments.visual_decision_id(case.job, 'section_1'))
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', case.segments.video_preparation_id(case.job, 'section_1'))
    assert case.repo.list_creation_job_request_states('local', case.job.id) == []
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert case.repo.get_narration_segment('local', case.task_id, case.paid_rows[0].id) == case.paid_rows[0]
    assert case.generated == [] and case.merged == []
    case.scheduler.close()


@pytest.mark.parametrize('boundary', ['planning', 'adoption'])
def test_bounded_segment_video_unknown_audio_keeps_original_request(tmp_path, monkeypatch, boundary):
    from app.creation.contracts import TaskEvent
    from app.creation_execution import next_creation_intent
    from app.device_scheduler import RecoveryBlocked
    case = _bounded_segment_video_case(tmp_path, monkeypatch)
    if boundary == 'adoption':
        assert next_creation_intent(case.repo, case.scheduler, case.job) is None
        waiting = case.repo.get_task_event('local', case.segments.visual_decision_id(case.job, 'section_1'))
    # A pending later narration unit retains its original request and unknown
    # receipt even though the first segment already has paid ready audio.
    case.owner.segments = None
    original = case.scheduler.submit(next_creation_intent(case.repo, case.scheduler, case.job), case.actor)
    case.owner.segments = case.segments
    case.repo.append_task_event(TaskEvent('unsettled-video-predecessor', 'local', case.task_id, case.job.id,
        'creation_narration_unit_started', {'usage_id': f'{original.id}:9', 'request_id': original.id,
            'permit_generation': 9, 'unit_key': original.unit_key, 'phase': 'segment',
            'remaining_seconds': 300, 'remaining_output_bytes': 3*1024*1024}))
    case.repo.append_task_event(TaskEvent(f'event_{case.job.id}_narration_budget', 'local', case.task_id, case.job.id,
        'creation_narration_budget', {'active_event_id': 'unsettled-video-predecessor', 'settled_event_id': None,
            'parent_timeout_seconds': 300, 'parent_max_output_bytes': 3*1024*1024,
            'script_id': case.script_id, 'input_contract': 'narration_units_v1'}))
    if boundary == 'adoption':
        view = case.segments.read_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor)
        assert view['status'] == 'waiting_for_user' and not view['usage']['settled']
    with pytest.raises(RecoveryBlocked, match='未知'):
        if boundary == 'planning':
            next_creation_intent(case.repo, case.scheduler, case.job)
        else:
            case.segments.adopt_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor,
                decision_digest=waiting.payload['decision_digest'],
                confirmed_clip_ids=[clip['id'] for clip in waiting.payload['montage']['clips'] if clip['needs_confirmation']],
                runtime=case.runtime)
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', case.segments.video_preparation_id(case.job, 'section_1'))
    if boundary == 'planning':
        with pytest.raises(KeyError):
            case.repo.get_task_event('local', case.segments.visual_decision_id(case.job, 'section_1'))
    else:
        assert case.repo.get_task_event('local', waiting.id) == waiting
    assert [row['id'] for row in case.repo.list_creation_job_request_states('local', case.job.id)] == [original.id]
    assert case.scheduler._get(original.id) == original
    assert case.repo.creation_narration_usage('local', case.job.id)['unsettled'][0]['request_id'] == original.id
    assert len(case.execution.jobs('local', case.task_id)) == 1
    assert not case.generated and not case.merged
    case.scheduler.close()


def test_bounded_segment_planning_ports_keep_snapshot_and_transaction_owner(tmp_path, monkeypatch):
    from app.creation.contracts import TaskEvent
    case = _bounded_segment_video_case(tmp_path, monkeypatch)
    repo = case.repo
    with repo._connect() as setup:
        setup.execute('PRAGMA journal_mode=WAL')
    with repo.creation_execution_read() as reader:
        snapshot = repo.read_creation_execution_snapshot('local', case.task_id, connection=reader)
        inputs = repo.iter_creation_section_planning_inputs(snapshot.task, snapshot.script_header, connection=reader)
        first = next(inputs)
        with repo._connect() as writer:
            writer.execute("UPDATE creation_task_script_sections SET section_json=json_set(section_json,'$.content',?) "
                "WHERE script_id=? AND section_id='section_23'", ('更改后的尾段', case.script_id))
        tail = None
        for value in inputs:
            tail = value
        assert tail[0].content == first[0].content and tail[1] == 30
        assert reader.in_transaction
    with repo._connect() as borrowed:
        assert not borrowed.in_transaction
        version = repo.stage_section_version('local', case.task_id, 'section_1', connection=borrowed)
        assert version and not borrowed.in_transaction
        borrowed.execute('BEGIN IMMEDIATE')
        task = repo.get_creation_task('local', case.task_id, connection=borrowed)
        event = TaskEvent('planning-adopted', 'local', case.task_id, None, 'segment_publication', {
            'script_id': case.script_id, 'text': '已采用正文', 'narration_reference': 'narration://artifact/adopted',
            'montage': {'delivery_window': {'section_id': 'section_1', 'start_seconds': 0, 'end_seconds': 8}}})
        repo.append_task_event(event, connection=borrowed)
        version = TimelineVersion(id='planning-version', workspace_id='local', task_id=case.task_id,
            parent_version_id=None, sequence=1, created_by_operation_id=None,
            snapshot={'stage_segments': {'section_1': event.id}, 'stage_segment_invalidations': {
                'section_1': {'publication_id': event.id, 'reason': '保留旧失效原因'}}})
        repo.create_timeline_version(version, connection=borrowed)
        task.current_timeline_version_id = version.id
        borrowed.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
            (json.dumps(asdict(task), default=str), task.id))
        snapshot = repo.read_creation_execution_snapshot('local', case.task_id, connection=borrowed)
        current = repo.read_creation_section_input(snapshot.task, snapshot.script_header, 'section_1', connection=borrowed)
        assert current['section'].content == '已采用正文'
        assert current['adopted']['current_validity'] == 'invalidated'
        assert current['adopted']['invalidation_reason'] == '保留旧失效原因'
        assert repo.stage_section_version('local', case.task_id, 'section_1', connection=borrowed) == event.id
        timings = repo.iter_creation_section_planning_inputs(snapshot.task, snapshot.script_header, connection=borrowed)
        assert next(timings)[2] == event.payload['montage']['delivery_window']
        timings.close()
        assert borrowed.in_transaction
        borrowed.rollback()
        assert repo.stage_segment_state('local', case.task_id, 'section_1', connection=borrowed) is None
        assert not borrowed.in_transaction
    assert not case.generated and not case.merged
    case.scheduler.close()


@pytest.mark.parametrize('legacy', [False, True])
def test_bounded_segment_video_confirmation_rechecks_global_clock(tmp_path, monkeypatch, legacy):
    from app.creation.contracts import TaskEvent
    from app.creation_execution import next_creation_intent
    case = _bounded_segment_video_case(tmp_path, monkeypatch)
    assert next_creation_intent(case.repo, case.scheduler, case.job) is None
    waiting = case.repo.get_task_event('local', case.segments.visual_decision_id(case.job, 'section_1'))
    with case.repo._connect() as conn:
        if legacy:
            conn.execute("UPDATE task_events SET payload_json=json_remove(payload_json,'$.planning_dependency_digest') WHERE id=?", (waiting.id,))
            conn.execute("UPDATE narration_segments SET duration_seconds=48 WHERE task_id=? AND source_section_id='section_23'", (case.task_id,))
        else:
            publication = TaskEvent('other-adopted-clock', 'local', case.task_id, None, 'segment_publication', {
                'script_id': case.script_id, 'text': '改变后的另一段已采用正文', 'narration_reference': 'narration://artifact/older',
                'montage': {'delivery_window': {'section_id': 'section_2', 'start_seconds': 8, 'end_seconds': 14}}})
            case.repo.append_task_event(publication, connection=conn)
            task = case.repo.get_creation_task('local', case.task_id, connection=conn)
            version = TimelineVersion(id='other-clock-version', workspace_id='local', task_id=case.task_id,
                parent_version_id=None, sequence=1, created_by_operation_id=None,
                snapshot={'stage_segments': {'section_2': publication.id}})
            case.repo.create_timeline_version(version, connection=conn)
            task.current_timeline_version_id = version.id
            conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    waiting = case.repo.get_task_event('local', waiting.id)
    assert case.segments.read_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor)['status'] == 'stale'
    with pytest.raises(ValueError, match='全片时长'):
        case.segments.adopt_visual_decision('local', case.task_id, case.job.id, 'section_1', case.actor,
            decision_digest=waiting.payload['decision_digest'],
            confirmed_clip_ids=[clip['id'] for clip in waiting.payload['montage']['clips'] if clip['needs_confirmation']], runtime=case.runtime)
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', case.segments.video_preparation_id(case.job, 'section_1'))
    assert case.repo.get_task_event('local', waiting.id) == waiting
    assert case.repo.list_creation_job_request_states('local', case.job.id) == []
    assert not case.generated and not case.merged
    case.scheduler.close()


def test_bounded_segment_video_new_audio_preparation_precedes_its_original_commit(tmp_path, monkeypatch):
    case = _bounded_segment_video_case(tmp_path, monkeypatch)
    with case.repo._connect() as conn:
        conn.execute("UPDATE narration_segments SET status='pending',audio_reference=NULL,duration_seconds=NULL,sample_rate=NULL "
            "WHERE task_id=? AND source_section_id='section_1'", (case.task_id,))
    task = case.repo.get_creation_task('local', case.task_id)
    start = case.repo.get_task_event('local', f'event_{case.job.id}_start').payload
    audio = replace(case.repo.get_narration_artifact('local', 'paid-section_1'), id='not-committed', cache_key='not-committed')
    assert case.repo.get_narration_artifact('local', audio.cache_key) is None
    prepared = case.segments.prepare_video(case.job, start, task, 'section_1', audio, case.owner, text=case.paid_rows[0].text)
    with case.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        case.segments.persist_video_preparation_for_request(prepared, 'original-audio-request', connection=conn)
        assert conn.in_transaction
        assert case.repo.get_task_event('local', prepared.id, connection=conn).payload['unit_key'] == prepared.payload['unit_key']
        conn.rollback()
    assert case.repo.get_narration_artifact('local', audio.cache_key) is None
    with pytest.raises(KeyError):
        case.repo.get_task_event('local', prepared.id)
    assert not case.generated and not case.merged
    case.scheduler.close()


def _section_context_projection_case(tmp_path):
    """Persist section facts without running a renderer, narration worker or model."""
    from app.creation.contracts import ArtifactRecord, NarrationSegment, SubtitleCue, SubtitleTrack, TaskEvent
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    case = _narration_units_case(tmp_path, section_count=5)
    repo = case.repo
    case.segments = SegmentProductionUseCase(repo,narration_stage=None,renderer=None,
        artifact_store=None,output_root=tmp_path/'unused')
    clips, cues = [], []
    for index, (section, start, end) in enumerate([(1,0,1),(2,1,2),(3,2,2.5),(3,2.5,3),(4,3,4),(5,4,5)]):
        cue_id = f'cue_{index}'
        cues.append(asdict(NarrationCue(cue_id,f'section_{section}',
            'FORBIDDEN_CUE_BODY' if section==1 else f'段{section}正文',end-start)))
        clips.append(asdict(MontageClip(f'clip_{index}',cue_id,f'asset_{section}',f'segment_{section}',
            f'material://asset_{section}',0,end-start,start,end,1,
            ['FORBIDDEN_CLIP_BODY'] if section==1 else ['原匹配'],False,
            transition_in_seconds=.125 if index in (2,4) else 0)))
    case.montage = {'cues':cues,'clips':clips,'duration_seconds':5,'narration_audio_path':'narration://artifact/original',
        'graphic_overlays':[{'kind':'text','id':'near','text':'相关标题','start_seconds':1.75,'end_seconds':3.25},
            {'kind':'text','id':'far','text':'FORBIDDEN_OVERLAY_BODY','start_seconds':0,'end_seconds':.5}]}
    publications = {}
    for section in (2,3):
        ref = f'publication_{section}'
        publications[f'section_{section}'] = ref
        repo.create_artifact_record(ArtifactRecord(f'video_{section}','local',case.task_id,'video',
            f'video://artifact/{section}','a'*64,1,case.job.id,[]))
        local = {**case.montage,'cues':[cue for cue in cues if cue['source_section_id']==f'section_{section}'],
            'clips':[clip for clip in clips if clip['cue_id'] in {cue['id'] for cue in cues if cue['source_section_id']==f'section_{section}'}],
            'graphic_overlays':[]}
        repo.append_task_event(TaskEvent(ref,'local',case.task_id,case.job.id,'segment_publication',
            {'section_id':f'section_{section}','script_id':case.script_id,'text':f'已采用段{section}中文\\转义',
                'adopted':True,'video_artifact_id':f'video_{section}','duration_seconds':1,
                'narration_reference':f'narration://artifact/audio_{section}','montage':local}))
    case.snapshot = {'montage':case.montage,'stage_segments':publications,
        'stage_segment_invalidations':{'section_3':{'publication_id':'publication_3','reason':'changed_source'}},
        'unrelated_body':'FORBIDDEN_SNAPSHOT_BODY'}
    repo.create_timeline_version(TimelineVersion('projection_v1','local',case.task_id,None,1,case.snapshot,None),
        expected_current_version_id=None)
    with repo._connect() as conn:
        conn.execute("UPDATE creation_task_script_sections SET section_json=json_set(section_json,'$.content','FORBIDDEN_AUDIO_BODY') "
            "WHERE script_id=? AND section_id IN ('section_1','section_5')",(case.script_id,))
    audio = [NarrationSegment(f'audio_{i}','local',case.task_id,case.script_id,f'section_{i}',
        f'已采用段{i}中文\\转义' if i in (2,3) else ('原文'*130 if i==4 else 'FORBIDDEN_AUDIO_BODY'),
        f'narration://artifact/audio_{i}',1,24000,'test','voice_1','v1','zh','original','ready') for i in range(1,6)]
    repo.save_narration_segments('local',case.task_id,case.script_id,audio)
    for language in ('zh-CN','en-US'):
        repo.create_subtitle_track(SubtitleTrack(f'track_{language}','local',case.task_id,'projection_v1',language,
            None,{'enabled':True},[SubtitleCue(f'far_{language}','cue_0','FORBIDDEN_SUBTITLE_BODY',0,.5),
                SubtitleCue(f'overlap_{language}','cue_1','相邻重叠字幕',1.75,2.25),
                SubtitleCue(f'target_{language}','cue_2','目标字幕',2.25,2.5),
                SubtitleCue(f'linked_{language}','cue_3','来源命中不重叠',3.5,4)],
            'timeline',None,None,None,'unavailable','fixture_pending','no_file','generate_later'))
    return case


def test_section_context_projection_preserves_facts_without_unrelated_bodies(tmp_path,monkeypatch):
    from app.creation.section_timeline import section_timeline_dependencies
    from app.creation.segment_production_use_case import digest
    case = _section_context_projection_case(tmp_path)
    expected_timeline = section_timeline_dependencies(case.montage,'section_3')
    def forbidden(*args,**kwargs):
        raise AssertionError('whole document reader used')
    for name in ('effective_creation_script','get_creation_task_script','list_narration_segments','get_timeline_version','get_creation_plan'):
        monkeypatch.setattr(case.repo,name,forbidden)
    decode = json.loads
    def bounded(value,*args,**kwargs):
        assert 'FORBIDDEN_' not in value
        return decode(value,*args,**kwargs)
    monkeypatch.setattr(json,'loads',bounded)
    context = case.segments.context('local',case.task_id,'section_3',case.actor)
    dependencies = case.repo.section_dependency_snapshot('local',case.task_id,'section_3')
    assert dependencies['timeline']==expected_timeline
    assert context['dependency_digest']==digest(dependencies)
    assert context['ordinal']==3 and context['segment_version']=='publication_3'
    assert context['text']=='已采用段3中文\\转义'
    assert [item['id'] for item in context['adjacent_sections']]==['section_2','section_4']
    assert context['adjacent_sections'][0]['content']=='已采用段2中文\\转义'
    assert context['video'] is None and context['historical_video']['artifact_ref']=='video://artifact/3'
    assert context['adoption']['status']=='invalidated' and context['adoption']['current_timeline_sequence']==1
    assert [[cue['text'] for cue in track['cues']] for track in dependencies['subtitles']]==[
        ['相邻重叠字幕','目标字幕','来源命中不重叠']]*2
    assert len(dependencies['timeline']['transitions'])==2
    page = case.segments.list_segments('local',case.task_id,case.actor,offset=2,limit=1)
    assert page['segments']==[context] and page['total_count']==5 and page['truncated']
    assert case.segments.sources('local',case.task_id,'section_3',case.actor)['segment_version']=='publication_3'
    assert case.repo.stage_section_version('local',case.task_id,'section_3')=='publication_3'


def _section_projection_next_version(case, snapshot, *, connection=None, subtitle_change=False):
    if connection is None:
        with case.repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return _section_projection_next_version(case,snapshot,connection=conn,subtitle_change=subtitle_change)
    case.repo.create_timeline_version(TimelineVersion('projection_v2','local',case.task_id,'projection_v1',2,snapshot,None),
        connection=connection,expected_current_version_id='projection_v1')
    for track in case.repo.list_subtitle_tracks('local',case.task_id,timeline_version_id='projection_v1',connection=connection):
        cues = [replace(cue,text='changed overlap') if subtitle_change and cue.id=='overlap_zh-CN' else cue for cue in track.cues]
        case.repo.create_subtitle_track(replace(track,id=track.id+'_v2',timeline_version_id='projection_v2',cues=cues),connection=connection)


def test_section_dependency_projection_borrows_transaction_and_preserves_cas(tmp_path):
    from app.creation.segment_production_use_case import digest
    case = _section_context_projection_case(tmp_path)
    repo = case.repo
    from app.creation.contracts import TaskEvent
    repo.append_task_event(TaskEvent(case.segments.video_preparation_id(case.job,'section_3'),'local',case.task_id,
        case.job.id,'segment_video_prepared',{'montage':case.montage}))
    request = SimpleNamespace(workspace_id='local',owner_id=case.job.id,unit_key='segment_video:section_3:publication_3')
    before = repo.section_dependency_snapshot('local',case.task_id,'section_3')
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        check = case.segments.capture_local(request,conn,case.owner)
        snapshot = json.loads(json.dumps(case.snapshot))
        snapshot['montage']['cues'][0]['text']='unrelated later edit'
        _section_projection_next_version(case,snapshot,connection=conn)
        unchanged = repo.section_dependency_snapshot('local',case.task_id,'section_3',connection=conn)
        assert json.dumps(unchanged,sort_keys=True,default=str)==json.dumps(before,sort_keys=True,default=str)
        assert check(conn)=='ready'
        conn.execute("UPDATE creation_plans SET payload=json_set(payload,'$.aspect_ratio','16:9') WHERE workspace_id='local'")
        changed = repo.section_dependency_snapshot('local',case.task_id,'section_3',connection=conn)
        assert changed['aspect_ratio']=='16:9' and digest(changed)!=digest(before)
        assert case.segments.context('local',case.task_id,'section_3',case.actor,connection=conn)['dependency_digest']==digest(changed)
        assert check(conn)=='stale'
        assert conn.in_transaction
        conn.rollback()
    assert repo.section_dependency_snapshot('local',case.task_id,'section_3')==before
    # Exercise the durable restore CAS itself, stopping only after that guard.
    history={'section_id':'section_3','after_publication':'publication_3','before_publication':'missing_history'}
    arguments=dict(history=history,expected_dependencies=before,redo=False,
        changed_version_id='changed',restore_version_id='restored',timeline_change=None,job=case.job)
    with pytest.raises(KeyError,match='missing_history'):
        repo.restore_script_section('local',case.task_id,**arguments)
    _section_projection_next_version(case,case.snapshot,subtitle_change=True)
    with pytest.raises(ValueError,match='实际音画、字幕或来源依赖已变化'):
        repo.restore_script_section('local',case.task_id,**arguments)


@pytest.mark.parametrize('conflict',['cue_order','overlap'])
def test_section_dependency_projection_keeps_global_conflict_checks(tmp_path,conflict):
    case = _section_context_projection_case(tmp_path)
    snapshot = case.snapshot
    if conflict=='cue_order':
        snapshot['montage']['cues'][3],snapshot['montage']['cues'][4]=snapshot['montage']['cues'][4],snapshot['montage']['cues'][3]
    else:
        snapshot['montage']['clips'][0]['timeline_end_seconds']=2.25
    _section_projection_next_version(case,snapshot)
    with pytest.raises(ValueError,match='不连续|交叠'):
        case.repo.section_dependency_snapshot('local',case.task_id,'section_3')
