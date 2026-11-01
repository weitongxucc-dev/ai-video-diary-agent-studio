"""A saved desktop recording is not the current narration until edit confirmation."""

import io
import json
import shutil
import subprocess
import wave
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.artifact_store import ArtifactStore
from app.creation_execution import (CpuRenderLimits, CreationExecution,
                                    CreationExecutionActor, CreationJobOwnerVerifier,
                                    cpu_render_facts, next_creation_intent, revision_digest)
from app.audio_timeline import AudioEditRequest, apply_audio_edit, narration_audio_timeline
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import MaterialAsset, MaterialSegment, NarrationArtifactRecord, ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import (
    CreationPlanVoiceReference,
    CreationTask,
    SubtitleCue,
    SubtitleTrack,
    TimelineVersion,
)


def wav_bytes(seconds: float = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * round(16_000 * seconds))
    return buffer.getvalue()


def _synthetic_audio_duration(path):
    if path.suffix == ".webm":
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-threads", "1",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(result.stdout.strip())
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


@pytest.fixture
def recording_task(tmp_path, monkeypatch):
    repo = ProductionRepository(database_path=tmp_path / "production.sqlite3", video_library_path=tmp_path / "library")
    from app.local_members import LocalMemberService
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    workspace_id, task_id, script_id = "local", "task_recording", "script_recording"
    plan = CreationPlan(
        id="plan_recording", workspace_id=workspace_id, goal="一条视频", name="录音测试", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=1,
        knowledge_references=[], source_references=[],
        selected_voice=CreationPlanVoiceReference(
            profile_id="voice_1", version="v1", person_profile_id="person_1", person_version="v1",
            person_authorization_id="person_auth_1", person_authorization_version="v1",
            authorization_id="voice_auth_1", authorization_version="v1", language="zh-CN", model_id="test"),
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="test",
    )
    task = CreationTask(id=task_id, workspace_id=workspace_id, plan_id=plan.id,
                        name="录音测试", scenario="knowledge_explanation")
    task.owner_user_id = member.member_id
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=member.member_id)
    from _media_preview_test_fixtures import register_synthetic_video_asset
    asset, source_reference = register_synthetic_video_asset(repo, workspace_id=workspace_id, member_id=member.member_id,
        path=repo.video_library_path / "source.mp4", asset_id="asset_1", seconds=1)
    asset.segments[0].id = "segment_1"
    repo._save_asset(asset)
    now = datetime.now(timezone.utc).isoformat()
    script = {"id": script_id, "workspace_id": workspace_id, "task_id": task_id,
              "run_id": "run_recording", "topic_id": "topic_recording", "title": "录音文案",
              "language": "zh-CN", "sections": [{"id": "section_1", "title": "正文",
              "content": "真实待制作文案", "visual_prompt": "现场", "evidence_reference_ids": ["source_1"]}],
              "selection_status": "confirmed", "created_at": now, "updated_at": now, "confirmed_at": now}
    old_audio = tmp_path / "old.wav"
    old_audio.write_bytes(wav_bytes())
    repo.save_narration_artifact(NarrationArtifactRecord(
        id="narration_old", workspace_id=workspace_id, task_id=task_id, script_id=script_id,
        voice_profile_id="voice_1", cache_key="old_voice", audio_path=str(old_audio),
        duration_seconds=1, sample_rate=16_000, model_id="test"))
    task = repo.task_records.get_creation_task(workspace_id, task_id)
    task.confirmed_script_id = script_id
    task.narration_cache_key = "old_voice"
    task.montage_plan_id = "montage_recording"
    task.stage = "montage"
    task.stage_status = "waiting_for_user"
    montage = CreationTaskMontagePlan(
        id=task.montage_plan_id, workspace_id=workspace_id, task_id=task_id,
        script_id=script_id, script_title="录音文案", status="rendered",
        cues=[NarrationCue("cue_1", "section_1", "真实待制作文案", 1.0)],
        clips=[MontageClip("clip_1", "cue_1", "asset_1", "segment_1", source_reference,
                           0, 1, 0, 1, 1, ["已确认镜头"], False)],
        duration_seconds=1, low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/old_voice",
        output_path="artifacts/task_recording/old.mp4",
        subtitle_path="artifacts/task_recording/old.srt",
    )
    montage_payload = creation_task_montage_plan_to_payload(montage)
    with repo._connect() as conn:
        conn.execute("INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
                     (script_id, workspace_id, task_id, script["run_id"], json.dumps(script), now))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), workspace_id, task_id))
        conn.execute("INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) VALUES (?,?,?,?,?)",
                     (montage.id, workspace_id, task_id, json.dumps(montage_payload), now))
    version = repo.timeline_records.create_timeline_version(TimelineVersion(
        id="timeline_recording_base", workspace_id=workspace_id, task_id=task_id,
        parent_version_id=None, sequence=1, snapshot={"montage": montage_payload},
        created_by_operation_id=None))
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", main_module._new_creation_execution(repo))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
    from dataclasses import replace
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings, temporary_root_path=str(tmp_path / "temp"),
        video_inbox_path=str(repo.video_library_path), video_output_path=str(tmp_path / "artifacts")))
    monkeypatch.setattr(main_module, "creation_execution_mode", "controlled")
    monkeypatch.setattr(main_module, "local_members_service", members)
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    configure_synthetic_media_budgets(monkeypatch, main_module)
    from test_montage_pipeline import _granted_domain_preview_adapter
    client = TestClient(main_module.app, headers={"x-local-member-token": token})
    state = SimpleNamespace(production=repo, member=member, members=members, task=task, workspace_id=workspace_id, client=client)
    runtime = _granted_domain_preview_adapter(state, monkeypatch, facts_override=main_module._task_media_facts)
    client.media_runtime = runtime
    yield repo, task_id, version.id, client
    assert runtime.stop(1)



def _prepare_first_recording_adoption(repo: ProductionRepository, task_id: str) -> None:
    task = repo.task_records.get_creation_task("local", task_id)
    task.stage = "narration"
    task.stage_status = "not_started"
    task.status = "running"
    task.recovery_point = "narration"
    task.narration_cache_key = None
    task.montage_plan_id = None
    task.current_timeline_version_id = None
    task.output_ready = False
    plan = repo.plan_records.get_creation_plan("local", task.plan_id)
    plan.selected_voice = None
    plan.presenter_share_target = 0
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), "local", task_id))
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(plan), default=str), "local", plan.id))
        conn.execute("DELETE FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=?",
                     ("local", task_id))


def test_initial_recording_requires_explicit_adoption_then_persists_narration_for_continuation(recording_task, tmp_path):
    repo, task_id, _old_version_id, client = recording_task
    authorized_voice = repo.plan_records.get_creation_plan("local", "plan_recording").selected_voice
    generated_preview = client.get(f"/api/creation-tasks/{task_id}/execution/preview")
    assert generated_preview.status_code == 200, generated_preview.text
    assert generated_preview.json()["requirements"]["presenter_share_target"] == repo.plan_records.get_creation_plan(
        "local", "plan_recording").presenter_share_target
    assert generated_preview.json()["requirements"]["can_continue"] is True
    assert generated_preview.json()["narration_source"]["source_kind"] == "generated"
    _prepare_first_recording_adoption(repo, task_id)
    route = f"/api/creation-tasks/{task_id}/audio/recordings"
    saved_response = client.post(route, files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["applied"] is False
    assert repo.task_records.get_creation_task("local", task_id).narration_cache_key is None
    assert repo.task_records.get_creation_task("local", task_id).stage == "narration"
    assert client.get(route).json()["items"][0]["applied"] is False

    adopted = client.post(route + "/adopt", json={
        "source_ref": saved["source_ref"],
        "script_id": "script_recording",
        "source_section_id": "section_1",
    })
    assert adopted.status_code == 200, adopted.text
    body = adopted.json()
    assert body["source_kind"] == "recorded"
    assert body["model_id"] is None and body["voice_profile_id"] is None
    assert body["task"]["stage"] == "montage"
    task = repo.task_records.get_creation_task("local", task_id)
    assert task.narration_cache_key == body["audio_reference"].removeprefix("narration://artifact/")
    assert task.montage_plan_id is None and task.current_timeline_version_id is None
    saved_artifact = repo.get_narration_artifact("local", task.narration_cache_key)
    assert saved_artifact is not None and saved_artifact.source_artifact_ref == saved["source_ref"]
    assert saved_artifact.source_sha256 == next(
        item.sha256 for item in repo.execution_records.list_artifact_records("local", task_id)
        if item.logical_ref == saved["source_ref"]
    )
    segment = repo.narration_records.list_narration_segments("local", task_id)[0]
    assert (segment.source_kind, segment.source_section_id, segment.text) == (
        "recorded", "section_1", "真实待制作文案")
    assert segment.model_id is None and segment.voice_profile_id is None and segment.voice_version is None
    assert client.get(route).json()["items"][0]["applied"] is True

    execution_preview = client.get(f"/api/creation-tasks/{task_id}/execution/preview")
    assert execution_preview.status_code == 200, execution_preview.text
    preview_body = execution_preview.json()
    assert preview_body["narration_source"] == {
        "source_kind": "recorded",
        "source_artifact_ref": saved["source_ref"],
        "duration_seconds": pytest.approx(1),
    }
    assert preview_body["requirements"]["can_continue"] is True
    assert preview_body["requirements"]["continuation_blocker"] is None

    plan = repo.plan_records.get_creation_plan("local", task.plan_id)
    plan.presenter_share_target = 20
    plan.selected_voice = authorized_voice
    with repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(plan), default=str), "local", plan.id))
    identity_blocked_preview = client.get(f"/api/creation-tasks/{task_id}/execution/preview")
    assert identity_blocked_preview.status_code == 200, identity_blocked_preview.text
    assert identity_blocked_preview.json()["requirements"]["can_continue"] is False
    assert "当前配音是本人录音" in identity_blocked_preview.json()["requirements"][
        "continuation_blocker"]

    plan.presenter_share_target = 0
    plan.selected_voice = None
    with repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(plan), default=str), "local", plan.id))

    restarted = ProductionRepository(repo.database_path, repo.video_library_path)
    reloaded = restarted.get_narration_artifact("local", task.narration_cache_key)
    assert reloaded == saved_artifact
    assert restarted.narration_records.list_narration_segments("local", task_id) == [segment]
    current_script = restarted.script_records.get_creation_task_script("local", "script_recording")
    current_plan = restarted.plan_records.get_creation_plan("local", "plan_recording")
    execution = CreationExecution(
        restarted,
        run_narration=lambda _workspace, _task: pytest.fail("录音采用后不应再次生成配音"),
        run_montage_plan=lambda _workspace, _task: None,
        run_render=lambda _workspace, _task: None,
        render_is_valid=lambda _workspace, _task: False,
    )
    actor = CreationExecutionActor("local", "member-recorded-test")
    job = execution.start("local", task_id, current_script.id,
                          revision_digest(current_script, current_plan), actor=actor)
    assert job.status == "queued"
    start_event = restarted.execution_records.get_task_event("local", f"event_{job.id}_start")
    assert start_event.payload["script_id"] == current_script.id
    owner = CreationJobOwnerVerifier(restarted)
    scheduler = SimpleNamespace(owners=owner)
    intent = next_creation_intent(restarted, scheduler, job)
    assert intent.unit_key == "montage_plan"
    verified = owner.verify_submission(intent, actor)
    assert verified.owner_id == job.id and verified.unit_key == "montage_plan"

    visual_path = repo.video_library_path / "recorded-input.mp4"
    visual_path.parent.mkdir(parents=True, exist_ok=True)
    visual_path.write_bytes(b"synthetic-video-bytes-not-a-playable-video")
    asset = MaterialAsset(
        id="asset_recorded", workspace_id="local", file_name=visual_path.name,
        path=str(visual_path), size_bytes=visual_path.stat().st_size,
        modified_at=datetime.fromtimestamp(visual_path.stat().st_mtime, timezone.utc),
        duration_seconds=1, usage_role="b_roll", review_status="approved",
        analysis_status="ready",
        segments=[MaterialSegment(
            id="segment_recorded", asset_id="asset_recorded", start_seconds=0,
            end_seconds=1, usage_role="b_roll", review_status="approved",
        )],
    )
    restarted._save_asset(asset)
    material_ref = restarted.workspace_media_reference_for_asset("local", asset.id)
    montage = CreationTaskMontagePlan(
        id="montage_recorded", workspace_id="local", task_id=task_id,
        script_id=current_script.id, script_title=current_script.title, status="ready",
        cues=[NarrationCue("cue_recorded", "section_1", current_script.sections[0].content, 1)],
        clips=[MontageClip("clip_recorded", "cue_recorded", asset.id, "segment_recorded",
                           material_ref, 0, 1, 0, 1, 1, ["synthetic confirmed shot"], False)],
        duration_seconds=1, low_confidence_clip_count=0,
        narration_audio_path=body["audio_reference"],
    )
    restarted.montage_plan_adoption.save_creation_task_montage_plan(
        "local", task_id, montage.id, creation_task_montage_plan_to_payload(montage),
    )
    with restarted._connect() as conn:
        digest, resources = cpu_render_facts(
            restarted, conn, restarted.task_records.get_creation_task("local", task_id),
            current_plan, CpuRenderLimits(1024 * 1024, 1, 30),
        )
    assert digest.startswith("render:")
    assert resources["kind"] == "creation_render"

    with repo._connect() as conn:
        row = conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
            ("local", current_script.id),
        ).fetchone()
        script_payload = json.loads(row["payload"])
        script_payload["sections"][0]["content"] = "之后改过的确认文案"
        conn.execute(
            "UPDATE creation_task_scripts SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(script_payload), "local", current_script.id),
        )
    assert client.get(route).json()["items"] == []
    stale_preview = client.get(f"/api/creation-tasks/{task_id}/execution/preview")
    assert stale_preview.status_code == 200, stale_preview.text
    assert stale_preview.json()["narration_source"] is None
    assert stale_preview.json()["requirements"]["can_continue"] is False


def test_initial_recording_adoption_rejects_ambiguous_or_stale_script(recording_task):
    repo, task_id, _old_version_id, client = recording_task
    _prepare_first_recording_adoption(repo, task_id)
    route = f"/api/creation-tasks/{task_id}/audio/recordings"
    script = repo.script_records.get_creation_task_script("local", "script_recording")
    with repo._connect() as conn:
        payload = json.loads(conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
            ("local", script.id),
        ).fetchone()[0])
        payload["sections"].append({"id": "section_2", "title": "第二段", "content": "不能推断", "visual_prompt": "", "evidence_reference_ids": []})
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(payload), "local", script.id))
    saved_response = client.post(route, files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    response = client.post(route + "/adopt", json={
        "source_ref": saved["source_ref"], "script_id": script.id,
        "source_section_id": "section_1",
    })
    assert response.status_code == 409
    assert repo.task_records.get_creation_task("local", task_id).narration_cache_key is None
    assert repo.task_records.get_creation_task("local", task_id).stage == "narration"


def test_initial_recording_rejects_script_content_changed_after_save(recording_task):
    repo, task_id, _old_version_id, client = recording_task
    _prepare_first_recording_adoption(repo, task_id)
    route = f"/api/creation-tasks/{task_id}/audio/recordings"
    saved_response = client.post(route, files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    script = repo.script_records.get_creation_task_script("local", "script_recording")
    with repo._connect() as conn:
        payload = json.loads(conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
            ("local", script.id),
        ).fetchone()[0])
        payload["sections"][0]["content"] = "已修改的文案正文"
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(payload), "local", script.id))
    response = client.post(route + "/adopt", json={
        "source_ref": saved["source_ref"], "script_id": script.id,
        "source_section_id": "section_1",
    })
    assert response.status_code == 409
    assert "确认文案已变化" in response.text
    assert repo.task_records.get_creation_task("local", task_id).narration_cache_key is None
    assert repo.task_records.get_creation_task("local", task_id).stage == "narration"


def test_recording_upload_preview_and_confirm_replace_one_narration_track(recording_task, tmp_path):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    uploaded = client.post(route + "/audio/recordings",
                           files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert uploaded.status_code == 200, uploaded.text
    saved = uploaded.json()
    assert saved["applied"] is False and saved["duration_ms"] == 1000
    assert client.get(saved["audio_url"]).content == wav_bytes()
    assert client.get(route + "/audio/recordings").json()["items"][0]["source_ref"] == saved["source_ref"]
    assert client.get(route + "/audio/recordings").json()["items"][0]["applied"] is False
    assert repo.task_records.get_creation_task("local", task_id).current_timeline_version_id == version_id
    assert repo.get_creation_task_montage_plan("local", task_id)["audio_timeline"] is None

    preview = client.post(route + "/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]},
    })
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert proposal["preview"]["after"]["audio_url"] == saved["audio_url"]
    assert proposal["invalidated_refs"] == ["artifacts/task_recording/old.mp4", "artifacts/task_recording/old.srt"]
    assert repo.task_records.get_creation_task("local", task_id).current_timeline_version_id == version_id
    confirmed = client.post(route + f"/edits/{proposal['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    current = repo.task_records.get_creation_task("local", task_id)
    assert current.current_timeline_version_id != version_id
    montage = repo.get_creation_task_montage_plan("local", task_id)
    assert montage["audio_timeline"]["tracks"][0]["clips"][0]["source_ref"] == saved["source_ref"]
    assert montage["audio_timeline"]["tracks"][0]["clips"] == [montage["audio_timeline"]["tracks"][0]["clips"][0]]
    assert montage["output_path"] is None and montage["status"] == "ready"
    assert repo.task_records.get_creation_task("local", task_id).narration_cache_key == "old_voice"
    assert client.get(route + "/audio/recordings").json()["items"][0]["applied"] is True
    assert (tmp_path / "old.wav").read_bytes() == wav_bytes()
    assert client.get(saved["audio_url"]).content == wav_bytes()


def test_recording_rejects_wrong_version_duration_and_container(recording_task):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    bad = client.request("POST", route + "/audio/recordings", files={"audio": ("voice.webm", wav_bytes(), "audio/webm")})
    assert bad.status_code == 202, bad.text
    assert client.media_runtime.run_once()
    assert repo.job_records.get_job_run("local", bad.json()["job_id"]).status == "failed"
    uploaded = client.post(route + "/audio/recordings",
                           files={"audio": ("short.wav", wav_bytes(.5), "audio/wav")})
    assert uploaded.status_code == 200, uploaded.text
    source_ref = uploaded.json()["source_ref"]
    payload = {"base_version_id": version_id, "action": "replace_narration",
               "target_id": "audio-narration-main", "values": {"source_ref": source_ref}}
    invalid = client.request("POST", route + "/audio/preview", json=payload)
    assert invalid.status_code == 202
    assert client.media_runtime.run_once()
    assert repo.job_records.get_job_run("local", invalid.json()["job_id"]).status == "failed"
    assert client.post(route + "/audio/preview", json={**payload, "base_version_id": "old"}).status_code == 409
    assert repo.get_creation_task_montage_plan("local", task_id)["audio_timeline"] is None


def test_recording_stays_blocked_in_scheduled_mode(recording_task, monkeypatch):
    repo, task_id, version_id, client = recording_task
    saved = client.post(f"/api/creation-tasks/{task_id}/audio/recordings",
                        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")}).json()
    preview = client.post(f"/api/creation-tasks/{task_id}/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]}})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    monkeypatch.setattr(main_module, "creation_execution_mode", "scheduled")
    response = client.request("POST", f"/api/creation-tasks/{task_id}/audio/recordings", files={"audio": ("scheduled.wav", wav_bytes(.8), "audio/wav")})
    assert response.status_code == 202
    assert repo.job_records.get_job_run("local", response.json()["job_id"]).status == "queued"
    assert client.media_runtime.run_once()
    assert repo.job_records.get_job_run("local", response.json()["job_id"]).status == "succeeded"
    assert client.post(f"/api/creation-tasks/{task_id}/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]}}).status_code == 503
    assert client.post(f"/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm",
                       json={}).status_code == 503
    assert repo.task_records.get_creation_task("local", task_id).current_timeline_version_id == version_id


@pytest.mark.parametrize("change", ["track_locked", "clip_locked", "split", "moved", "same_source"])
def test_whole_narration_replacement_rejects_incompatible_audio_graph(change):
    timeline = narration_audio_timeline("narration://artifact/old_voice", 1)
    track = timeline.tracks[0]
    clip = track.clips[0]
    if change == "track_locked":
        track.locked = True
    elif change == "clip_locked":
        clip.locked = True
    elif change == "split":
        timeline = apply_audio_edit(timeline, AudioEditRequest(
            action="split", target_id=clip.id, values={"at_ms": 500}), 1000)
    elif change == "moved":
        clip.source_start_ms = 100
    source_ref = clip.source_ref if change == "same_source" else "artifacts/new_recording.wav"
    with pytest.raises(ValueError):
        apply_audio_edit(timeline, AudioEditRequest(
            action="replace_narration", target_id=clip.id,
            values={"source_ref": source_ref, "source_end_ms": 1000}), 1000)


def test_recording_preview_rejects_person_render_and_unowned_source(recording_task):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    uploaded = client.post(route + "/audio/recordings",
                           files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    source_ref = uploaded.json()["source_ref"]
    payload = {"base_version_id": version_id, "action": "replace_narration",
               "target_id": "audio-narration-main", "values": {"source_ref": source_ref}}
    assert client.post(route + "/audio/preview", json={
        **payload, "values": {"source_ref": "artifacts/other_task/voice.wav"}}).status_code == 422
    montage = repo.get_creation_task_montage_plan("local", task_id)
    montage["clips"][0]["person_render_id"] = "person_render_old_voice"
    with repo._connect() as conn:
        conn.execute("UPDATE creation_task_montage_plans SET payload=? WHERE workspace_id=? AND task_id=?",
                     (json.dumps(montage), "local", task_id))
    assert client.post(route + "/audio/preview", json=payload).status_code == 422
    assert repo.task_records.get_creation_task("local", task_id).current_timeline_version_id == version_id


def test_recording_proposal_cannot_apply_after_version_changes(recording_task):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    uploaded = client.post(route + "/audio/recordings",
                           files={"audio": ("voice.wav", wav_bytes(), "audio/wav")}).json()
    preview = client.post(route + "/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": uploaded["source_ref"]}})
    assert preview.status_code == 200, preview.text
    base = repo.timeline_records.get_timeline_version("local", version_id)
    repo.timeline_records.create_timeline_version(TimelineVersion(
        id="timeline_recording_other_edit", workspace_id="local", task_id=task_id,
        parent_version_id=version_id, sequence=base.sequence + 1, snapshot=base.snapshot,
        created_by_operation_id=None))
    confirmed = client.post(route + f"/edits/{preview.json()['proposal']['id']}/confirm", json={})
    assert confirmed.status_code == 409
    assert repo.get_creation_task_montage_plan("local", task_id)["audio_timeline"] is None


def test_recordings_list_excludes_incomplete_and_other_workspace(recording_task, monkeypatch):
    repo, task_id, _version, client = recording_task
    route = f"/api/creation-tasks/{task_id}/audio/recordings"
    first = client.post(route, files={"audio": ("first.wav", wav_bytes(), "audio/wav")}).json()
    second = client.post(route, files={"audio": ("second.wav", wav_bytes(), "audio/wav")}).json()
    assert [item["source_ref"] for item in client.get(route).json()["items"]] == [
        second["source_ref"], first["source_ref"]]
    transform_id = next(run.id for run in repo.lineage_records.list_transform_runs("local", task_id) if second["source_ref"] in run.output_refs)
    with repo._connect() as conn:
        conn.execute("UPDATE transform_runs SET status='failed' WHERE workspace_id=? AND id=?",
                     ("local", transform_id))
    assert [item["source_ref"] for item in client.get(route).json()["items"]] == [first["source_ref"]]
    main_module.artifact_store.resolve_file(first["source_ref"]).unlink()
    assert client.get(route).json()["items"] == []
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "other_workspace")
    assert client.get(route).status_code == 404
    assert client.get(first["audio_url"]).status_code == 404


def test_equal_size_recording_change_rejects_old_proposal(recording_task):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    saved = client.post(route + "/audio/recordings",
                        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")}).json()
    edit = {"base_version_id": version_id, "action": "replace_narration",
            "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]}}
    preview = client.post(route + "/audio/preview", json=edit)
    assert preview.status_code == 200, preview.text
    path = main_module.artifact_store.resolve_file(saved["source_ref"])
    changed = bytearray(path.read_bytes())
    changed[-1] ^= 1
    path.write_bytes(changed)
    assert path.stat().st_size == len(wav_bytes())
    assert client.post(route + f"/edits/{preview.json()['proposal']['id']}/confirm", json={}).status_code == 409
    changed_preview = client.request("POST", route + "/audio/preview", json=edit)
    assert changed_preview.status_code == 202
    assert client.media_runtime.run_once()
    assert repo.job_records.get_job_run("local", changed_preview.json()["job_id"]).status == "failed"
    assert repo.task_records.get_creation_task("local", task_id).current_timeline_version_id == version_id
    assert repo.get_creation_task_montage_plan("local", task_id)["output_path"] == "artifacts/task_recording/old.mp4"


def test_replacing_narration_preserves_subtitle_text_but_requires_review(recording_task):
    repo, task_id, version_id, client = recording_task
    original = repo.subtitle_records.create_subtitle_track(SubtitleTrack(
        id="caption_recording_old", workspace_id="local", task_id=task_id,
        timeline_version_id=version_id, language="zh-CN", source_language=None,
        style={"font_size": 40},
        cues=[SubtitleCue(id="caption_1", source_cue_id=None, text="用户改过的字幕", start_seconds=0,
                          end_seconds=1)], generation_method="timeline", model_id=None,
        artifact_ref="artifacts/task_recording/old.srt", transform_id="transform_caption_old",
        status="valid"))
    route = f"/api/creation-tasks/{task_id}"
    saved = client.post(route + "/audio/recordings",
                        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")}).json()
    preview = client.post(route + "/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]}})
    assert preview.status_code == 200, preview.text
    confirmed = client.post(route + f"/edits/{preview.json()['proposal']['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    current_version = repo.task_records.get_creation_task("local", task_id).current_timeline_version_id
    tracks = repo.subtitle_records.list_subtitle_tracks("local", task_id, timeline_version_id=current_version)
    assert len(tracks) == 1
    assert tracks[0].status == "unavailable"
    assert tracks[0].artifact_ref is None and tracks[0].transform_id is None
    assert tracks[0].cues == original.cues and tracks[0].style == original.style
    assert tracks[0].repair_action
    assert repo.subtitle_records.list_subtitle_tracks("local", task_id, timeline_version_id=version_id)[0].status == "valid"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_volume_edit_keeps_subtitle_track_valid(recording_task):
    repo, task_id, version_id, client = recording_task
    original = repo.subtitle_records.create_subtitle_track(SubtitleTrack(
        id="caption_recording_old", workspace_id="local", task_id=task_id,
        timeline_version_id=version_id, language="zh-CN", source_language=None,
        style={"font_size": 40},
        cues=[SubtitleCue(id="caption_1", source_cue_id=None, text="用户改过的字幕", start_seconds=0,
                          end_seconds=1)], generation_method="timeline", model_id=None,
        artifact_ref="artifacts/task_recording/old.srt", transform_id="transform_caption_old",
        status="valid"))
    preview = client.post(f"/api/creation-tasks/{task_id}/audio/preview", json={
        "base_version_id": version_id, "action": "update",
        "target_id": "audio-narration-main", "values": {"gain_db": -6}})
    assert preview.status_code == 200, preview.text
    confirmed = client.post(f"/api/creation-tasks/{task_id}/edits/{preview.json()['proposal']['id']}/confirm",
                            json={})
    assert confirmed.status_code == 200, confirmed.text
    current_version = repo.task_records.get_creation_task("local", task_id).current_timeline_version_id
    tracks = repo.subtitle_records.list_subtitle_tracks("local", task_id, timeline_version_id=current_version)
    assert len(tracks) == 1
    assert tracks[0].status == "valid"
    assert tracks[0].artifact_ref == original.artifact_ref and tracks[0].transform_id == original.transform_id
    assert tracks[0].cues == original.cues and tracks[0].style == original.style


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/ffprobe unavailable",
)
def test_browser_webm_recording_keeps_audio_container_and_reloads(recording_task, tmp_path):
    _repo, task_id, _version, client = recording_task
    media = tmp_path / "microphone.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:a", "libopus", str(media)],
                   check=True, capture_output=True, timeout=15)
    uploaded = client.post(f"/api/creation-tasks/{task_id}/audio/recordings",
                           files={"audio": (media.name, media.read_bytes(), "audio/webm")})
    assert uploaded.status_code == 200, uploaded.text
    audio = client.get(uploaded.json()["audio_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/webm"
    assert audio.content == media.read_bytes()


@pytest.fixture
def recording_metadata(tmp_path, monkeypatch):
    """Declared saved-recording facts; no waveform, upload, decoder or worker run."""
    import hashlib
    from dataclasses import replace
    from app.local_members import LocalMemberService
    from app.creation.contracts import JobRun, ArtifactRecord, TransformRun, TaskEvent
    from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
    from app.device_runtime import DeviceRuntime
    from app.task_audio import TaskAudio, TASK_MEDIA_KIND
    from app.models import utc_now
    import _media_preview_test_fixtures as helpers
    import test_creation_execution as creation_helpers
    def forbidden(*args, **kwargs):
        pytest.fail('recording adoption evidence forbids wave/provider/Popen/runtime/model/driver/media')
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(TaskAudio, 'probe', forbidden)
    monkeypatch.setattr(TaskAudio, 'publish', forbidden)
    monkeypatch.setattr(main_module.narration_generator, 'generate_for_creation_task', forbidden)
    monkeypatch.setattr(main_module.montage_renderer, 'render', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main_module, name, forbidden)
    for owner in (helpers, creation_helpers):
        for name in ('write_synthetic_video', 'register_synthetic_video_asset', 'register_synthetic_profiles', 'synthetic_wav_bytes'):
            monkeypatch.setattr(owner, name, forbidden)
    repo = ProductionRepository(tmp_path/'recording.sqlite3', tmp_path/'library')
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    plan = CreationPlan(id='plan_metadata',workspace_id='local',goal='declared recording',name='metadata',target_count=1,
        language='zh-CN',aspect_ratio='9:16',duration_target_seconds=1,knowledge_references=[],source_references=[],
        selected_voice=None,pronunciation_references=[],approved_asset_count=0,approved_clip_count=0,
        material_rule_summary='',model_snapshot=[],confirmation_points=[],blockers=[],preflight_fingerprint='declared')
    task = CreationTask('task_metadata','local',plan.id,'metadata recording','knowledge_explanation',owner_user_id=member.member_id)
    repo.plan_confirmation.confirm_creation_plan(plan,task,verified_owner_user_id=member.member_id)
    task = repo.task_records.get_creation_task('local',task.id)
    now = utc_now().isoformat()
    script = {'id':'script_metadata','workspace_id':'local','task_id':task.id,'run_id':'declared-run','topic_id':'declared-topic',
        'title':'declared script','language':'zh-CN','sections':[{'id':'section_1','title':'正文','content':'真实待制作文案',
            'visual_prompt':'','evidence_reference_ids':[]}], 'selection_status':'confirmed',
        'created_at':now,'updated_at':now,'confirmed_at':now}
    task.confirmed_script_id = script['id']
    task.stage, task.stage_status, task.recovery_point = 'narration','not_started','narration'
    script_record = CreationTaskScript(**{**script,
        'sections':[CreationTaskScriptSection(**part) for part in script['sections']],
        'created_at':datetime.fromisoformat(now),'updated_at':datetime.fromisoformat(now),'confirmed_at':datetime.fromisoformat(now)})
    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        stored = repo.script_records._script_payload(conn,script_record)
        conn.execute('INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)',
            (script['id'],'local',task.id,script['run_id'],stored,now))
        conn.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(task),default=str),'local',task.id))
    digest = repo.script_records.confirmed_creation_task_script_digest('local',task.id,script['id'])
    store = ArtifactStore(tmp_path/'artifacts')
    blob = store.publish_bytes('artifacts/declared-recording.webm', b'declared saved recording bytes; not media acceptance')
    job = repo.job_records.create_job_run(JobRun('declared-recording-job','local',task.id,TASK_MEDIA_KIND,'declared-recording',hashlib.sha256(b'declared').hexdigest()))
    running = repo.job_records.claim_job_run('local',job.id)
    source = repo.execution_records.create_artifact_record(ArtifactRecord('declared-recording-source','local',task.id,'task_recording',
        blob.logical_ref,blob.sha256,blob.size_bytes,job.id,[]))
    repo.lineage_records.create_transform_run(TransformRun('declared-recording-transform','local',task.id,job.id,
        'task_recording',job.input_digest,[],[source.logical_ref],'declared metadata',
        {'duration_ms':1000,'script_id':script['id'],'script_digest':digest,'montage_plan_id':None},
        'succeeded',running.started_at,utc_now()))
    stat = store.resolve_file(source.logical_ref).stat()
    repo.execution_records.append_task_event(TaskEvent('event_'+job.id+'_verified','local',task.id,job.id,'task_media_verified',
        {'outputs':[{'reference':source.logical_ref,'size_bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns}]}))
    repo.job_records.finish_job_run('local',job.id,status='succeeded',output_refs=[source.logical_ref])
    monkeypatch.setattr(main_module,'production_repo',repo)
    monkeypatch.setattr(main_module,'artifact_store',store)
    monkeypatch.setattr(main_module,'creation_execution',main_module._new_creation_execution(repo))
    monkeypatch.setattr(main_module,'local_members_service',members)
    monkeypatch.setattr(main_module,'auth_context',SimpleNamespace(enabled=False))
    monkeypatch.setattr(main_module,'current_workspace_id',lambda request:'local')
    monkeypatch.setattr(main_module,'settings',replace(main_module.settings,
        temporary_root_path=str(tmp_path/'temporary'),video_output_path=str(tmp_path/'output')))
    client = TestClient(main_module.app,headers={'x-local-member-token':token})
    return SimpleNamespace(repo=repo,task=task,source=source,store=store,client=client,script=script,
        route=f'/api/creation-tasks/{task.id}/audio/recordings',
        payload={'script_id':script['id'],'source_section_id':'section_1','source_ref':source.logical_ref})


def test_recording_metadata_adoption_persists_applied_and_continuation_identity(recording_metadata):
    from dataclasses import replace
    state = recording_metadata
    repo, task, client = state.repo, state.task, state.client
    before_jobs = [job.id for job in repo.job_records.list_job_runs('local',task.id)]
    with repo._connect() as conn:
        before_missing = list(conn.iterdump())
    missing = client.get(f'/api/creation-tasks/{task.id}/audio/artifacts/missing-artifact')
    assert missing.status_code == 404 and missing.json()['detail'] == '当前任务的音频不存在'
    with repo._connect() as conn:
        assert list(conn.iterdump()) == before_missing
    assert client.get(state.route).json()['items'][0]['applied'] is False
    adopted = client.post(state.route+'/adopt',json=state.payload)
    assert adopted.status_code==200,adopted.text
    body = adopted.json()
    assert body['source_kind']=='recorded' and body['model_id'] is None and body['voice_profile_id'] is None
    assert body['task']['stage']=='montage' and body['source_artifact_ref']==state.source.logical_ref
    assert client.get(state.route).json()['items'][0]['applied'] is True
    current = repo.task_records.get_creation_task('local',task.id)
    artifact = repo.get_narration_artifact('local',current.narration_cache_key)
    segment, = repo.narration_records.list_narration_segments('local',task.id)
    assert (segment.source_kind,segment.source_section_id,segment.text)==('recorded','section_1','真实待制作文案')
    assert segment.model_id is None and segment.voice_profile_id is None and segment.voice_version is None
    assert artifact.source_sha256==state.source.sha256 and artifact.source_script_digest==segment.source_script_digest
    reopened = ProductionRepository(repo.database_path,repo.video_library_path)
    assert reopened.get_narration_artifact('local',current.narration_cache_key)==artifact
    assert reopened.narration_records.list_narration_segments('local',task.id)==[segment]
    preview = client.get(f'/api/creation-tasks/{task.id}/execution/preview')
    assert preview.status_code==200,preview.text
    assert preview.json()['narration_source']=={'source_kind':'recorded','source_artifact_ref':state.source.logical_ref,'duration_seconds':1}
    assert preview.json()['requirements']['can_continue'] is True and preview.json()['requirements']['continuation_blocker'] is None
    plan = repo.plan_records.get_creation_plan('local',task.plan_id)
    with repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(replace(plan,presenter_share_target=20)),default=str),'local',plan.id))
    blocked = client.get(f'/api/creation-tasks/{task.id}/execution/preview')
    assert blocked.status_code==200 and blocked.json()['requirements']['can_continue'] is False
    assert '当前配音是本人录音' in blocked.json()['requirements']['continuation_blocker']
    assert [job.id for job in repo.job_records.list_job_runs('local',task.id)]==before_jobs
    assert current.current_timeline_version_id is None and repo.timeline_records.list_timeline_versions('local',task.id)==[]
    # The other original applied source is the current montage narration graph.
    montage = CreationTaskMontagePlan('declared-montage','local',task.id,task.confirmed_script_id,'metadata','ready',
        [],[],1,0,narration_audio_path=state.source.logical_ref,
        audio_timeline=narration_audio_timeline(state.source.logical_ref,1))
    repo.montage_plan_adoption.save_creation_task_montage_plan('local',task.id,montage.id,creation_task_montage_plan_to_payload(montage))
    assert client.get(state.route).json()['items'][0]['applied'] is True
    with repo._connect() as conn:
        current = repo.task_records.get_creation_task('local',task.id)
        current.narration_cache_key = None
        conn.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(current),default=str),'local',task.id))
    assert client.get(state.route).json()['items'][0]['applied'] is True


def test_recording_metadata_rejects_ambiguous_then_changed_script_without_partial_adoption(recording_metadata):
    state = recording_metadata
    repo, task, client = state.repo,state.task,state.client
    def write_script(value):
        from dataclasses import replace
        from app.creation.script_contracts import CreationTaskScriptSection
        original = repo.script_records.get_creation_task_script('local',state.script['id'])
        updated = replace(original,sections=[CreationTaskScriptSection(**part) for part in value['sections']])
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            stored = repo.script_records._script_payload(conn,updated)
            conn.execute('UPDATE creation_task_scripts SET payload=? WHERE workspace_id=? AND id=?',
                (stored,'local',state.script['id']))
    script = {**state.script,'sections':[*state.script['sections'],{**state.script['sections'][0],'id':'section_2'}]}
    write_script(script)
    ambiguous = client.post(state.route+'/adopt',json=state.payload)
    assert ambiguous.status_code==409 and '多个文案段' in ambiguous.json()['detail']
    write_script({**state.script,'sections':[{**state.script['sections'][0],'content':'已修改的文案正文'}]})
    changed = client.post(state.route+'/adopt',json=state.payload)
    assert changed.status_code==409
    current = repo.task_records.get_creation_task('local',task.id)
    assert current.stage=='narration' and current.narration_cache_key is None
    assert repo.narration_records.list_narration_segments('local',task.id)==[]
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM narration_artifacts WHERE workspace_id=?',('local',)).fetchone()[0]==0
    assert client.get(state.route).json()['items']==[]
