import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncRequest,
    AvatarAndLipSyncService,
    PersonProfile,
    SubprocessAvatarAndLipSyncProvider,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile,
    avatar_lipsync_cache_key,
    _parse_avatar_result,
    _probe_avatar_output,
)


@pytest.fixture(scope="module")
def avatar_video(tmp_path_factory):
    output = tmp_path_factory.mktemp("avatar-media") / "valid.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:r=25",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output),
    ], check=True)
    return output


def _authorization(content_type: str, *, status: str = "active") -> Authorization:
    return Authorization(
        id=f"auth-{content_type}",
        version="v1",
        rights_holder="本人",
        content_types=(content_type,),
        usage_scopes=("avatar_lipsync",),
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        evidence_reference="consent://evidence/1",
        status=status,  # type: ignore[arg-type]
        workspace_id="workspace_a",
    )


def _request(tmp_path: Path) -> AvatarAndLipSyncRequest:
    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"audio")
    visual = tmp_path / "person.mp4"
    visual.write_bytes(b"visual")
    person = PersonProfile(
        id="person_a",
        workspace_id="workspace_a",
        display_name="A",
        version="person-v1",
        avatar_reference="asset://avatar/a",
        background_capabilities=("studio",),
        shot_capabilities=("medium",),
        authorization=_authorization("person_likeness"),
    )
    voice = VoiceProfile(
        id="voice_a",
        workspace_id="workspace_a",
        person_profile_id="person_a",
        person_version="person-v1",
        version="voice-v1",
        language="zh-en",
        reference_audio_path=str(audio),
        reference_text="参考文字",
        model_id="voice-model-v1",
        authorization=_authorization("voice"),
    )
    return AvatarAndLipSyncRequest(
        workspace_id="workspace_a",
        person=person,
        voice=voice,
        audio_path=str(audio),
        audio_cache_key="audio-cache-v1",
        language="zh-en",
        motion="natural",
        expression="neutral",
        authorization=_authorization("avatar_video"),
        model_id="avatar-model-v1",
        visual_path=str(visual),
    )


def test_avatar_lipsync_reports_unavailable_capability_and_versioned_cache(tmp_path):
    request = _request(tmp_path)
    service = AvatarAndLipSyncService(tmp_path / "outputs", UnavailableAvatarAndLipSyncProvider())

    result = service.render(request)

    assert result.status == "unavailable"
    assert result.failure_code == "capability_unavailable"
    assert result.artifact_path is None
    assert result.failure is not None
    assert (
        result.failure.reason,
        result.failure.impact,
        result.failure.repair_action,
        result.failure.retryable,
    ) == (
        "capability_unavailable",
        "人物与口型步骤未完成，不能生成或复用人物视频。",
        "人物口型能力尚未配置。",
        True,
    )
    assert avatar_lipsync_cache_key(request) != avatar_lipsync_cache_key(
        replace(request, person=replace(request.person, version="person-v2"))
    )
    assert avatar_lipsync_cache_key(request) != avatar_lipsync_cache_key(
        replace(request, voice=replace(request.voice, version="voice-v2"))
    )
    assert avatar_lipsync_cache_key(request) != avatar_lipsync_cache_key(
        replace(request, audio_cache_key="audio-cache-pronunciation-v2")
    )
    assert avatar_lipsync_cache_key(request) != avatar_lipsync_cache_key(
        replace(request, model_id="avatar-model-v2")
    )
    assert avatar_lipsync_cache_key(request) != avatar_lipsync_cache_key(
        replace(request, language="en")
    )


def test_avatar_lipsync_stops_unauthorized_request_before_provider(tmp_path):
    request = _request(tmp_path)
    unauthorized = replace(request, authorization=replace(request.authorization, status="revoked"))
    result = AvatarAndLipSyncService(
        tmp_path / "outputs", UnavailableAvatarAndLipSyncProvider()
    ).render(unauthorized)

    assert result.status == "authorization_denied"
    assert result.failure_code == "authorization_revoked"
    assert result.failure is not None
    assert result.failure.retryable is False


def test_avatar_lipsync_reports_missing_provider_output(tmp_path):
    request = _request(tmp_path)
    runtime = tmp_path / "runtime"
    worker = tmp_path / "worker.py"
    runtime.write_text("runtime", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    runtime_root = tmp_path / "musetalk"
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    (runtime_root / "scripts" / "inference.py").write_text("", encoding="utf-8")
    (runtime_root / "models" / "musetalkV15").mkdir(parents=True, exist_ok=True)
    (runtime_root / "models" / "musetalkV15" / "unet.pth").write_bytes(b"model")

    def runner(command):
        response_path = Path(command[command.index("--response") + 1])
        output_path = Path(command[command.index("--request") + 1])
        request_payload = json.loads(output_path.read_text(encoding="utf-8"))
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "succeeded",
                    "artifact_path": request_payload["output_path"],
                    "duration_seconds": 8.0,
                    "alignment_evidence": "phoneme alignment v1",
                }
            ),
            encoding="utf-8",
        )

    provider = SubprocessAvatarAndLipSyncProvider(
        runtime_path=runtime,
        worker_path=worker,
        runtime_root=runtime_root,
        model_id="avatar-model-v1",
        supported_languages=("zh", "en", "zh-en"),
        runner=runner,
    )
    result = AvatarAndLipSyncService(tmp_path / "outputs", provider).render(request)

    assert result.status == "output_missing"
    assert result.failure_code == "output_missing"
    assert result.failure is not None
    assert result.failure.retryable is True


def _configured_provider(tmp_path, runner):
    runtime = tmp_path / "runtime"
    worker = tmp_path / "worker.py"
    runtime.write_text("runtime", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    runtime_root = tmp_path / "musetalk"
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    (runtime_root / "scripts" / "inference.py").write_text("", encoding="utf-8")
    (runtime_root / "models" / "musetalkV15").mkdir(parents=True, exist_ok=True)
    (runtime_root / "models" / "musetalkV15" / "unet.pth").write_bytes(b"model")
    return SubprocessAvatarAndLipSyncProvider(
        runtime_path=runtime,
        worker_path=worker,
        runtime_root=runtime_root,
        model_id="avatar-model-v1",
        supported_languages=("zh", "en", "zh-en"),
        runner=runner,
    )


def test_avatar_lipsync_reports_provider_process_and_invalid_response_failures(tmp_path):
    request = _request(tmp_path)

    def failed_runner(_command):
        raise subprocess.SubprocessError("provider stderr")

    failed = AvatarAndLipSyncService(
        tmp_path / "outputs", _configured_provider(tmp_path, failed_runner)
    ).render(request)
    assert failed.status == "failed"
    assert failed.failure_code == "provider_process_failed"
    assert "stderr" in (failed.remediation_action or "")

    def invalid_response_runner(command):
        response_path = Path(command[command.index("--response") + 1])
        response_path.write_text("not json", encoding="utf-8")

    invalid = AvatarAndLipSyncService(
        tmp_path / "outputs", _configured_provider(tmp_path, invalid_response_runner)
    ).render(request)
    assert invalid.status == "failed"
    assert invalid.failure_code == "provider_response_invalid"


def test_avatar_lipsync_succeeds_only_after_valid_provider_output(tmp_path, avatar_video):
    request = replace(_request(tmp_path), audio_path=str(avatar_video))
    calls = []

    def success_runner(command):
        calls.append(command)
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(avatar_video.read_bytes())
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "succeeded",
                    "artifact_path": str(output),
                    "duration_seconds": 8.0,
                    "alignment_evidence": "phoneme alignment v1",
                }
            ),
            encoding="utf-8",
        )

    service = AvatarAndLipSyncService(
        tmp_path / "outputs", _configured_provider(tmp_path, success_runner)
    )
    result = service.render(request)

    assert result.status == "succeeded"
    assert Path(result.artifact_path or "").is_file()
    cached = service.render(request)
    assert cached.status == "succeeded"
    assert cached.artifact_path == result.artifact_path
    assert cached.duration_seconds == result.duration_seconds == 8.0
    evidence = json.loads(cached.alignment_evidence)
    assert evidence["engine"] == "ffprobe"
    assert evidence["validation_scope"] == "media_timing_only_not_lip_sync_quality"
    assert len(calls) == 1


def test_avatar_lipsync_can_create_an_immutable_regeneration_output(tmp_path, avatar_video):
    request = _request(tmp_path)
    calls: list[Path] = []

    def success_runner(command):
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(avatar_video.read_bytes())
        calls.append(output)
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "succeeded",
                    "artifact_path": str(output),
                    "duration_seconds": 8.0,
                    "alignment_evidence": "phoneme alignment v1",
                }
            ),
            encoding="utf-8",
        )

    service = AvatarAndLipSyncService(
        tmp_path / "outputs", _configured_provider(tmp_path, success_runner)
    )
    original = service.render(request)
    regenerated = service.render(request, output_cache_key="regeneration-version-2")

    assert original.status == regenerated.status == "succeeded"
    assert original.artifact_path != regenerated.artifact_path
    assert regenerated.cache_key == "regeneration-version-2"
    assert calls == [Path(original.artifact_path or ""), Path(regenerated.artifact_path or "")]


def test_render_rejects_unsupported_language_before_provider_or_cache(tmp_path, monkeypatch):
    provider = _configured_provider(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError("provider called")))
    provider.supported_languages = ("zh",)
    request = _request(tmp_path)
    request = replace(request, language="en", voice=replace(request.voice, language="en"))
    monkeypatch.setattr("app.avatar_lipsync._probe_avatar_output", lambda _: (_ for _ in ()).throw(AssertionError("cache reused")))
    result = AvatarAndLipSyncService(tmp_path / "outputs", provider).render(request)
    assert result.status == "unavailable"
    assert result.failure_code == "language_unavailable"


def test_person_command_preserves_constraints_and_rejects_partial_execution():
    import pytest
    from app.avatar_lipsync import compile_person_edit_command
    assert compile_person_edit_command("这里只重做口型，其他都不要动") == "RegeneratePersonRender"
    assert compile_person_edit_command("这一段换成当前人物出镜") == "ChangePersonSegment"
    assert compile_person_edit_command("重新对齐口型，保留原声，表情不变") == "RegeneratePersonRender"
    for command in ("不要重做口型", "人物更自然", "重做口型，同时字幕变黄", "重做口型并且换成当前人物出镜", "保留配音", "口型不同步"):
        with pytest.raises(ValueError):
            compile_person_edit_command(command)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), True, 0, 1.0])
def test_avatar_result_rejects_invalid_or_incorrect_duration(avatar_video, duration):
    result = _parse_avatar_result({
        "schema_version": 1, "status": "succeeded", "artifact_path": str(avatar_video),
        "duration_seconds": duration, "alignment_evidence": "worker evidence",
    }, expected_output=avatar_video, cache_key="test")
    assert result.status == "failed"
    assert result.failure_code == "provider_evidence_invalid"
    assert result.artifact_path is None


@pytest.mark.parametrize("kind", ["header_only", "no_audio"])
def test_avatar_result_rejects_unusable_media(tmp_path, avatar_video, kind):
    output = tmp_path / "invalid.mp4"
    if kind == "header_only":
        output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"not a real video")
    else:
        subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(avatar_video), "-an", "-c:v", "copy", str(output),
        ], check=True)
    result = _parse_avatar_result({
        "schema_version": 1, "status": "succeeded", "artifact_path": str(output),
        "duration_seconds": 8.0, "alignment_evidence": "worker evidence",
    }, expected_output=output, cache_key="test")
    assert result.status == "failed"
    assert result.failure_code == "output_invalid"


def test_failed_avatar_output_cannot_be_reused_on_retry(tmp_path, avatar_video):
    request = _request(tmp_path)
    calls = []
    def runner(command):
        payload = json.loads(Path(command[command.index("--request") + 1]).read_text())
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(avatar_video.read_bytes())
        calls.append(output)
        Path(command[command.index("--response") + 1]).write_text(json.dumps({
            "schema_version": 1, "status": "succeeded", "artifact_path": str(output),
            "duration_seconds": 20.0, "alignment_evidence": "worker evidence",
        }))
    service = AvatarAndLipSyncService(tmp_path / "outputs", _configured_provider(tmp_path, runner))
    first = service.render(request)
    second = service.render(request)
    assert first.failure_code == second.failure_code == "provider_evidence_invalid"
    assert len(calls) == 2
    assert not calls[-1].exists()
    assert avatar_video.is_file()


@pytest.mark.parametrize("duration", ["nan", "inf"])
def test_avatar_cache_rejects_nonfinite_probe_duration(monkeypatch, avatar_video, duration):
    def probe(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps({
            "format": {"duration": duration},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 64, "height": 64},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }))
    monkeypatch.setattr("app.avatar_lipsync.subprocess.run", probe)
    assert _probe_avatar_output(avatar_video) is None


@pytest.mark.parametrize("picture_seconds,audio_seconds", [(0.3, 8), (8, 0.3), (1, 1)])
def test_truncated_avatar_cache_is_regenerated_against_input_audio(
    tmp_path, avatar_video, picture_seconds, audio_seconds,
):
    request = replace(_request(tmp_path), audio_path=str(avatar_video))
    calls = []
    def runner(command):
        payload = json.loads(Path(command[command.index("--request") + 1]).read_text())
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(avatar_video.read_bytes())
        calls.append(output)
        Path(command[command.index("--response") + 1]).write_text(json.dumps({
            "schema_version": 1, "status": "succeeded", "artifact_path": str(output),
            "duration_seconds": 8.0, "alignment_evidence": "worker evidence",
        }))
    service = AvatarAndLipSyncService(tmp_path / "outputs", _configured_provider(tmp_path, runner))
    original = service.render(request)
    assert original.status == "succeeded"
    cache = Path(original.artifact_path)
    # Simulate an interrupted/partial file left at the disposable cache path.
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=s=64x64:r=25:d={picture_seconds}", "-f", "lavfi", "-i",
        f"anullsrc=r=16000:cl=mono:d={audio_seconds}", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(cache),
    ], check=True)
    restored = service.render(request)
    assert restored.status == "succeeded"
    assert restored.duration_seconds == 8.0
    assert len(calls) == 2
    assert restored.artifact_path == original.artifact_path
    assert _probe_avatar_output(cache, audio_path=avatar_video)[0] == 8.0
