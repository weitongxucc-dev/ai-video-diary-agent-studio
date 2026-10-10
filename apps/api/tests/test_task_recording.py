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
from app import task_audio as task_audio_module
from app.artifact_store import ArtifactStore
from app.creation_execution import (CpuRenderLimits, CreationExecution,
                                    CreationExecutionActor, CreationJobOwnerVerifier,
                                    cpu_render_facts, next_creation_intent, revision_digest)
from app.audio_timeline import AudioEditRequest, apply_audio_edit, narration_audio_timeline
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import (CreationPlan, CreationPlanVoiceReference, CreationTask,
                            MaterialAsset, MaterialSegment,
                            NarrationArtifactRecord, ProductionRepository, SubtitleCue, SubtitleTrack, TimelineVersion)


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
    monkeypatch.setattr(task_audio_module, "probe_audio_source", _synthetic_audio_duration)
    repo = ProductionRepository(database_path=tmp_path / "production.sqlite3", video_library_path=tmp_path / "library")
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
    repo.confirm_creation_plan(plan, task)
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
    task = repo.get_creation_task(workspace_id, task_id)
    task.confirmed_script_id = script_id
    task.narration_cache_key = "old_voice"
    task.montage_plan_id = "montage_recording"
    task.stage = "montage"
    task.stage_status = "waiting_for_user"
    montage = CreationTaskMontagePlan(
        id=task.montage_plan_id, workspace_id=workspace_id, task_id=task_id,
        script_id=script_id, script_title="录音文案", status="rendered",
        cues=[NarrationCue("cue_1", "section_1", "真实待制作文案", 1.0)],
        clips=[MontageClip("clip_1", "cue_1", "asset_1", "segment_1", "workspace://source.mp4",
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
    version = repo.create_timeline_version(TimelineVersion(
        id="timeline_recording_base", workspace_id=workspace_id, task_id=task_id,
        parent_version_id=None, sequence=1, snapshot={"montage": montage_payload},
        created_by_operation_id=None))
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", main_module._new_creation_execution())
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(effective_temporary_root_path=tmp_path / "temp"))
    monkeypatch.setattr(main_module, "creation_execution_mode", "legacy")
    return repo, task_id, version.id, TestClient(main_module.app)


def _prepare_first_recording_adoption(repo: ProductionRepository, task_id: str) -> None:
    task = repo.get_creation_task("local", task_id)
    task.stage = "narration"
    task.stage_status = "not_started"
    task.status = "running"
    task.recovery_point = "narration"
    task.narration_cache_key = None
    task.montage_plan_id = None
    task.current_timeline_version_id = None
    task.output_ready = False
    plan = repo.get_creation_plan("local", task.plan_id)
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
    authorized_voice = repo.get_creation_plan("local", "plan_recording").selected_voice
    generated_preview = client.get(f"/api/creation-tasks/{task_id}/execution/preview")
    assert generated_preview.status_code == 200, generated_preview.text
    assert generated_preview.json()["requirements"]["presenter_share_target"] == repo.get_creation_plan(
        "local", "plan_recording").presenter_share_target
    assert generated_preview.json()["requirements"]["can_continue"] is True
    assert generated_preview.json()["narration_source"]["source_kind"] == "generated"
    _prepare_first_recording_adoption(repo, task_id)
    route = f"/api/creation-tasks/{task_id}/audio/recordings"
    saved_response = client.post(route, files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["applied"] is False
    assert repo.get_creation_task("local", task_id).narration_cache_key is None
    assert repo.get_creation_task("local", task_id).stage == "narration"
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
    task = repo.get_creation_task("local", task_id)
    assert task.narration_cache_key == body["audio_reference"].removeprefix("narration://artifact/")
    assert task.montage_plan_id is None and task.current_timeline_version_id is None
    saved_artifact = repo.get_narration_artifact("local", task.narration_cache_key)
    assert saved_artifact is not None and saved_artifact.source_artifact_ref == saved["source_ref"]
    assert saved_artifact.source_sha256 == next(
        item.sha256 for item in repo.list_artifact_records("local", task_id)
        if item.logical_ref == saved["source_ref"]
    )
    segment = repo.list_narration_segments("local", task_id)[0]
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

    plan = repo.get_creation_plan("local", task.plan_id)
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
    assert restarted.list_narration_segments("local", task_id) == [segment]
    current_script = restarted.get_creation_task_script("local", "script_recording")
    current_plan = restarted.get_creation_plan("local", "plan_recording")
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
    start_event = restarted.get_task_event("local", f"event_{job.id}_start")
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
    restarted.save_creation_task_montage_plan(
        "local", task_id, montage.id, creation_task_montage_plan_to_payload(montage),
    )
    with restarted._connect() as conn:
        digest, resources = cpu_render_facts(
            restarted, conn, restarted.get_creation_task("local", task_id),
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
    script = repo.get_creation_task_script("local", "script_recording")
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
    assert repo.get_creation_task("local", task_id).narration_cache_key is None
    assert repo.get_creation_task("local", task_id).stage == "narration"


def test_initial_recording_rejects_script_content_changed_after_save(recording_task):
    repo, task_id, _old_version_id, client = recording_task
    _prepare_first_recording_adoption(repo, task_id)
    route = f"/api/creation-tasks/{task_id}/audio/recordings"
    saved_response = client.post(route, files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    script = repo.get_creation_task_script("local", "script_recording")
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
    assert repo.get_creation_task("local", task_id).narration_cache_key is None
    assert repo.get_creation_task("local", task_id).stage == "narration"


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
    assert repo.get_creation_task("local", task_id).current_timeline_version_id == version_id
    assert repo.get_creation_task_montage_plan("local", task_id)["audio_timeline"] is None

    preview = client.post(route + "/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]},
    })
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert proposal["preview"]["after"]["audio_url"] == saved["audio_url"]
    assert proposal["invalidated_refs"] == ["artifacts/task_recording/old.mp4", "artifacts/task_recording/old.srt"]
    assert repo.get_creation_task("local", task_id).current_timeline_version_id == version_id
    confirmed = client.post(route + f"/edits/{proposal['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    current = repo.get_creation_task("local", task_id)
    assert current.current_timeline_version_id != version_id
    montage = repo.get_creation_task_montage_plan("local", task_id)
    assert montage["audio_timeline"]["tracks"][0]["clips"][0]["source_ref"] == saved["source_ref"]
    assert montage["audio_timeline"]["tracks"][0]["clips"] == [montage["audio_timeline"]["tracks"][0]["clips"][0]]
    assert montage["output_path"] is None and montage["status"] == "ready"
    assert repo.get_creation_task("local", task_id).narration_cache_key == "old_voice"
    assert client.get(route + "/audio/recordings").json()["items"][0]["applied"] is True
    assert (tmp_path / "old.wav").read_bytes() == wav_bytes()
    assert client.get(saved["audio_url"]).content == wav_bytes()


def test_recording_rejects_wrong_version_duration_and_container(recording_task):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    bad = client.post(route + "/audio/recordings",
                      files={"audio": ("voice.webm", wav_bytes(), "audio/webm")})
    assert bad.status_code == 422
    uploaded = client.post(route + "/audio/recordings",
                           files={"audio": ("short.wav", wav_bytes(.5), "audio/wav")})
    assert uploaded.status_code == 200, uploaded.text
    source_ref = uploaded.json()["source_ref"]
    payload = {"base_version_id": version_id, "action": "replace_narration",
               "target_id": "audio-narration-main", "values": {"source_ref": source_ref}}
    assert client.post(route + "/audio/preview", json=payload).status_code == 422
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
    response = client.post(f"/api/creation-tasks/{task_id}/audio/recordings",
                           files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert response.status_code == 503
    assert client.post(f"/api/creation-tasks/{task_id}/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": saved["source_ref"]}}).status_code == 503
    assert client.post(f"/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm",
                       json={}).status_code == 503
    assert repo.get_creation_task("local", task_id).current_timeline_version_id == version_id


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
    assert repo.get_creation_task("local", task_id).current_timeline_version_id == version_id


def test_recording_proposal_cannot_apply_after_version_changes(recording_task):
    repo, task_id, version_id, client = recording_task
    route = f"/api/creation-tasks/{task_id}"
    uploaded = client.post(route + "/audio/recordings",
                           files={"audio": ("voice.wav", wav_bytes(), "audio/wav")}).json()
    preview = client.post(route + "/audio/preview", json={
        "base_version_id": version_id, "action": "replace_narration",
        "target_id": "audio-narration-main", "values": {"source_ref": uploaded["source_ref"]}})
    assert preview.status_code == 200, preview.text
    base = repo.get_timeline_version("local", version_id)
    repo.create_timeline_version(TimelineVersion(
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
    transform_id = second["source_ref"].split("/")[-2].replace("job_", "transform_", 1)
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
    assert client.post(route + "/audio/preview", json=edit).status_code == 422
    assert repo.get_creation_task("local", task_id).current_timeline_version_id == version_id
    assert repo.get_creation_task_montage_plan("local", task_id)["output_path"] == "artifacts/task_recording/old.mp4"


def test_replacing_narration_preserves_subtitle_text_but_requires_review(recording_task):
    repo, task_id, version_id, client = recording_task
    original = repo.create_subtitle_track(SubtitleTrack(
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
    current_version = repo.get_creation_task("local", task_id).current_timeline_version_id
    tracks = repo.list_subtitle_tracks("local", task_id, timeline_version_id=current_version)
    assert len(tracks) == 1
    assert tracks[0].status == "unavailable"
    assert tracks[0].artifact_ref is None and tracks[0].transform_id is None
    assert tracks[0].cues == original.cues and tracks[0].style == original.style
    assert tracks[0].repair_action
    assert repo.list_subtitle_tracks("local", task_id, timeline_version_id=version_id)[0].status == "valid"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_volume_edit_keeps_subtitle_track_valid(recording_task):
    repo, task_id, version_id, client = recording_task
    original = repo.create_subtitle_track(SubtitleTrack(
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
    current_version = repo.get_creation_task("local", task_id).current_timeline_version_id
    tracks = repo.list_subtitle_tracks("local", task_id, timeline_version_id=current_version)
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
