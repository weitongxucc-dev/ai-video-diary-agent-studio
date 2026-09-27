"""A saved desktop recording is not the current narration until edit confirmation."""

import io
import json
import shutil
import sqlite3
import subprocess
import wave
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.artifact_store import ArtifactStore
from app.audio_timeline import AudioEditRequest, apply_audio_edit, narration_audio_timeline
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import (CreationPlan, CreationPlanVoiceReference, CreationTask,
                            NarrationArtifactRecord, ProductionRepository, TimelineVersion)


def wav_bytes(seconds: float = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * round(16_000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def recording_task(tmp_path, monkeypatch):
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
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(effective_temporary_root_path=tmp_path / "temp"))
    monkeypatch.setattr(main_module, "creation_execution_mode", "legacy")
    return repo, task_id, version.id, TestClient(main_module.app)


def test_recording_upload_preview_and_confirm_replace_one_narration_track(recording_task):
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
    _repo, task_id, _version, client = recording_task
    monkeypatch.setattr(main_module, "creation_execution_mode", "scheduled")
    response = client.post(f"/api/creation-tasks/{task_id}/audio/recordings",
                           files={"audio": ("voice.wav", wav_bytes(), "audio/wav")})
    assert response.status_code == 503


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
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "other_workspace")
    assert client.get(route).status_code == 404
    assert client.get(first["audio_url"]).status_code == 404


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_browser_webm_recording_keeps_audio_container_and_reloads(recording_task, tmp_path):
    _repo, task_id, _version, client = recording_task
    media = tmp_path / "microphone.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "libopus", str(media)], check=True, capture_output=True)
    uploaded = client.post(f"/api/creation-tasks/{task_id}/audio/recordings",
                           files={"audio": (media.name, media.read_bytes(), "audio/webm")})
    assert uploaded.status_code == 200, uploaded.text
    audio = client.get(uploaded.json()["audio_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/webm"
    assert audio.content == media.read_bytes()
