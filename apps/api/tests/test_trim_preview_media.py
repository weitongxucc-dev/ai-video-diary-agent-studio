"""A TrimClip proposal must expose playable media from the requested source interval."""

import json
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main as main_module
from app.artifact_store import ArtifactStore
from app.montage import CreationTaskMontagePlan, MontageClip, MontageRenderer, NarrationCue, creation_task_montage_plan_to_payload
from app.production import (
    CreationPlan, CreationTask, MaterialAsset, MaterialSegment,
    MaterialSegmentMatch, NarrationArtifactRecord, ProductionRepository,
)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg is required for the real-media preview check",
)
def test_trim_preview_is_playable_and_uses_before_and_after_source_ranges(tmp_path, monkeypatch):
    video = tmp_path / "two-colors.mp4"
    voice = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=1.2",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=1.2",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=2.4",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        str(voice),
    ], check=True, capture_output=True)

    workspace_id, task_id = "workspace-a", "task-trim-media"
    repository = ProductionRepository(tmp_path / "production.sqlite", tmp_path)
    asset = MaterialAsset(
        id="asset-trim", workspace_id=workspace_id, file_name=video.name, path=str(video),
        size_bytes=video.stat().st_size, modified_at=main_module.utc_now(), duration_seconds=2.4,
        usage_role="b_roll", review_status="approved", analysis_status="ready",
        segments=[MaterialSegment(
            id="segment-trim", asset_id="asset-trim", start_seconds=0, end_seconds=2.4,
            description="red then blue", review_status="approved",
        )],
    )
    repository._save_asset(asset)
    plan = CreationPlan(
        id="plan-trim", workspace_id=workspace_id, goal="预览裁剪", name="裁剪测试", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=1,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=1, approved_clip_count=1,
        material_rule_summary="已审核素材", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="f" * 64,
        material_matches=[MaterialSegmentMatch(
            asset_ref="asset:asset-trim", segment_ref="segment:segment-trim", score=1,
            reasons=["test"],
        )],
    )
    task = CreationTask(
        id=task_id, workspace_id=workspace_id, plan_id=plan.id, name=plan.name,
        scenario=plan.goal, stage="montage", stage_status="waiting_for_user",
        status="waiting_for_user", progress=70, recovery_point="montage",
    )
    repository.confirm_creation_plan(plan, task)
    repository.save_narration_artifact(NarrationArtifactRecord(
        id="voice-trim", workspace_id=workspace_id, script_id="script-trim", voice_profile_id=None,
        cache_key="voice-trim", audio_path=str(voice), duration_seconds=1, sample_rate=44100,
        model_id="test-sine", task_id=task_id,
    ))
    montage = CreationTaskMontagePlan(
        id="montage-trim", workspace_id=workspace_id, task_id=task_id, script_id="script-trim",
        script_title="裁剪测试", status="ready",
        cues=[NarrationCue("cue-trim", "section-trim", "裁剪", 1)],
        clips=[MontageClip(
            id="clip-trim", cue_id="cue-trim", asset_id=asset.id, segment_id=asset.segments[0].id,
            source_path=repository.workspace_media_reference_for_asset(workspace_id, asset.id),
            source_start_seconds=0, source_end_seconds=1, timeline_start_seconds=0,
            timeline_end_seconds=1, match_score=1, match_reasons=["test"], needs_confirmation=False,
        )],
        duration_seconds=1, low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/voice-trim",
    )
    repository.save_creation_task_montage_plan(
        workspace_id, task_id, montage.id, creation_task_montage_plan_to_payload(montage),
    )
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "output"))
    monkeypatch.setattr(main_module, "montage_renderer", MontageRenderer(tmp_path / "output"))
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(
        video_inbox_path=str(tmp_path), effective_temporary_root_path=tmp_path / "temp",
    ))
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    monkeypatch.setattr(
        repository, "resolve_authorized_narration_audio_reference", lambda _workspace_id, _reference: voice,
    )

    client = TestClient(main_module.app)
    route = f"/api/creation-tasks/{task_id}/edits/exact/preview"
    initial = client.post(route, json={
        "operation_type": "TrimClip", "clip_id": "clip-trim", "source_start_seconds": 1.2,
    })
    assert initial.status_code == 409
    assert initial.json()["detail"]["error"] == "timeline_version_required"
    assert repository.get_creation_task(workspace_id, task_id).current_timeline_version_id is None
    assert repository.list_edit_proposals(workspace_id, task_id) == []
    assert repository.list_job_runs(workspace_id, task_id) == []
    initialized = client.post(f"/api/creation-tasks/{task_id}/timeline/initialize")
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["version_id"] == repository.get_creation_task(workspace_id, task_id).current_timeline_version_id
    assert repository.get_creation_task_montage_plan(workspace_id, task_id)["clips"][0]["source_start_seconds"] == 0
    assert len(repository.list_timeline_versions(workspace_id, task_id)) == 1

    original_resolve = repository.resolve_clip_media_reference
    resolve_calls = 0

    def unavailable_after(_workspace_id, _asset_id, _source_path):
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 2:
            raise ValueError("裁剪后源片段不可用")
        return original_resolve(_workspace_id, _asset_id, _source_path)

    with monkeypatch.context() as missing_after:
        missing_after.setattr(repository, "resolve_clip_media_reference", unavailable_after)
        incomplete = client.post(route, json={
            "operation_type": "TrimClip", "clip_id": "clip-trim", "source_start_seconds": 1.2,
        })
    assert incomplete.status_code == 422
    assert repository.list_edit_proposals(workspace_id, task_id) == []
    assert repository.list_job_runs(workspace_id, task_id) == []

    with monkeypatch.context() as no_media_work:
        no_media_work.setattr(
            main_module.subprocess, "run",
            lambda *_args, **_kwargs: pytest.fail("TrimClip preview must not launch FFmpeg"),
        )
        preview = client.post(route, json={
            "operation_type": "TrimClip", "clip_id": "clip-trim", "source_start_seconds": 1.2,
        })
    assert preview.status_code == 200, preview.text
    version_after_preview = repository.get_creation_task(workspace_id, task_id).current_timeline_version_id
    assert version_after_preview is not None
    for side, dominant, start, end in (("before", "red", 0, 1), ("after", "blue", 1.2, 2.2)):
        url = preview.json()["proposal"]["preview"][side]["video_url"]
        fragment = urlsplit(url)
        assert fragment.fragment == f"t={start:.3f},{end:.3f}"
        media = client.get(fragment.path)
        assert media.status_code == 200 and media.headers["content-type"].startswith("video/mp4")
        output = tmp_path / f"{side}.mp4"
        output.write_bytes(media.content)
        probe = json.loads(subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
            "-of", "json", str(output),
        ], check=True, capture_output=True, text=True).stdout)
        assert {stream["codec_type"] for stream in probe["streams"]} == {"video", "audio"}
        assert float(probe["format"]["duration"]) == pytest.approx(2.4, abs=0.15)
        frame = tmp_path / f"{side}.png"
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-ss", str(start + .5), "-i", str(output),
            "-frames:v", "1", str(frame),
        ], check=True, capture_output=True)
        with Image.open(frame) as image:
            red, _, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        assert (red > blue) if dominant == "red" else (blue > red)

    assert repository.get_creation_task(workspace_id, task_id).current_timeline_version_id == version_after_preview
    assert repository.list_edit_operations(workspace_id, task_id) == []
    assert repository.list_job_runs(workspace_id, task_id) == []
    assert len(repository.list_timeline_versions(workspace_id, task_id)) == 1
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "another-workspace")
    assert client.get(preview.json()["proposal"]["preview"]["after"]["video_url"].split("#")[0]).status_code == 404
