import json
import subprocess
import wave
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncRequest,
    AvatarAndLipSyncService,
    PersonProfile,
    SubprocessAvatarAndLipSyncProvider,
    LatentSync16Provider,
    LatentSyncAvatarAndLipSyncProvider,
    select_lipsync_provider,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile,
    avatar_lipsync_cache_key,
    avatar_lipsync_provider_from_settings,
    _parse_avatar_result,
    _probe_avatar_output,
)
from app.config import Settings
from app.lipsync_models import LATENTSYNC_15_MODEL_ID, LATENTSYNC_16_MODEL_ID, planned_lipsync_model_id


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
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * (16000 * 8))
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


def test_high_and_low_use_distinct_models_without_fallback(tmp_path):
    settings = replace(
        Settings(),
        avatar_model_runtime_python_path=str(tmp_path / "missing-low-runtime"),
        avatar_model_root_path=str(tmp_path / "missing-low-root"),
        latentsync_runtime_python_path=str(tmp_path / "missing-high-runtime"),
        latentsync_root_path=str(tmp_path / "missing-high-root"),
        latentsync_pose_root_path=str(tmp_path / "missing-pose"),
    )
    high = avatar_lipsync_provider_from_settings(settings, quality="high")
    low = avatar_lipsync_provider_from_settings(settings, quality="fast")
    assert isinstance(high, LatentSync16Provider)
    assert high.model_id == LATENTSYNC_16_MODEL_ID != low.model_id
    assert high.ready is False and low.ready is False
    assert "LatentSync 1.6" in high.message
    historical = SimpleNamespace(lipsync_quality="high", lipsync_model_id=None, model_snapshot=[])
    old_id = planned_lipsync_model_id(historical, high.model_id)
    assert old_id == LATENTSYNC_15_MODEL_ID
    legacy = select_lipsync_provider(high, old_id)
    assert isinstance(legacy, LatentSyncAvatarAndLipSyncProvider)
    assert legacy.model_id == old_id
    new = SimpleNamespace(**{**vars(historical), "lipsync_model_id": LATENTSYNC_16_MODEL_ID})
    assert select_lipsync_provider(high, planned_lipsync_model_id(new, high.model_id)) is high
    request = _request(tmp_path)
    high_request = replace(request, model_id=high.model_id)
    low_request = replace(request, model_id=low.model_id)
    assert avatar_lipsync_cache_key(high_request) != avatar_lipsync_cache_key(low_request)
    result = AvatarAndLipSyncService(tmp_path / "high", high).render(high_request)
    assert result.status == "unavailable"
    assert not (tmp_path / "high").exists()


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


@pytest.mark.parametrize(
    ("quality", "model_id", "language", "reaches_worker"),
    [
        ("high", LATENTSYNC_16_MODEL_ID, "en", True),
        ("high", LATENTSYNC_16_MODEL_ID, "zh-en", False),
        ("high", LATENTSYNC_15_MODEL_ID, "en", False),
        ("fast", None, "en", False),
    ],
)
def test_lipsync_worker_language_matches_the_pinned_model(
    tmp_path, monkeypatch, quality, model_id, language, reaches_worker,
):
    settings = Settings(
        avatar_model_runtime_python_path=str(tmp_path / "fast-python"),
        avatar_model_root_path=str(tmp_path / "fast-root"),
        latentsync_runtime_python_path=str(tmp_path / "legacy-python"),
        latentsync_root_path=str(tmp_path / "legacy-root"),
        latentsync_pose_root_path=str(tmp_path / "pose"),
    )
    default = avatar_lipsync_provider_from_settings(settings, quality=quality)
    provider = select_lipsync_provider(default, model_id or default.model_id)
    monkeypatch.setattr(type(provider), "ready", property(lambda _: True))
    worker_requests = []

    def runner(command):
        request_path = Path(command[command.index("--request") + 1])
        worker_requests.append(json.loads(request_path.read_text(encoding="utf-8")))
        response_path = Path(command[command.index("--response") + 1])
        response_path.write_text(
            json.dumps({"schema_version": 1, "status": "provider_rejected", "reason": "test worker"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(provider, "_runner", runner)
    request = replace(_request(tmp_path), model_id=provider.model_id, language=language)
    result = AvatarAndLipSyncService(tmp_path / "outputs", provider).render(request)

    if reaches_worker:
        assert result.status == "provider_rejected"
        assert [(item["model_id"], item["language"]) for item in worker_requests] == [(model_id, language)]
    else:
        assert result.failure_code == "language_unavailable"
        assert worker_requests == []


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
    }, expected_output=avatar_video, audio_path=avatar_video, cache_key="test")
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
    }, expected_output=output, audio_path=avatar_video, cache_key="test")
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


def test_fresh_short_avatar_is_rejected_then_full_length_retry_succeeds(tmp_path, avatar_video):
    request = _request(tmp_path)
    short = tmp_path / "short.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(avatar_video), "-t", "1",
        "-c:v", "libx264", "-c:a", "aac", str(short),
    ], check=True)
    calls = []

    def runner(command):
        payload = json.loads(Path(command[command.index("--request") + 1]).read_text())
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        source = short if not calls else avatar_video
        output.write_bytes(source.read_bytes())
        calls.append(output)
        Path(command[command.index("--response") + 1]).write_text(json.dumps({
            "schema_version": 1, "status": "succeeded", "artifact_path": str(output),
            "duration_seconds": 1.0 if len(calls) == 1 else 8.0,
            "alignment_evidence": "worker evidence",
        }))

    service = AvatarAndLipSyncService(tmp_path / "outputs", _configured_provider(tmp_path, runner))
    rejected = service.render(request)
    assert rejected.failure_code == "output_invalid"
    assert rejected.artifact_path is None
    assert not calls[0].exists()
    retried = service.render(request)
    assert retried.status == "succeeded"
    assert retried.duration_seconds == 8.0
    assert len(calls) == 2
    assert short.is_file() and avatar_video.is_file()


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


def test_high_quality_uses_configured_packaged_worker(tmp_path):
    worker = tmp_path / "resources" / "latentsync_worker.py"
    settings = Settings(latentsync_runtime_python_path="/test/python",
                        latentsync_root_path="/test/models",
                        latentsync_pose_root_path="/test/pose",
                        latentsync_worker_path=str(worker))
    provider = avatar_lipsync_provider_from_settings(settings, quality="high")
    assert isinstance(provider, LatentSync16Provider)
    assert provider.worker_path == worker.with_name("latentsync_16_worker.py")
    assert select_lipsync_provider(provider, LATENTSYNC_15_MODEL_ID).worker_path == worker


def test_presenter_provider_and_cache_timing_use_the_same_grant_runner(tmp_path):
    """Synthetic protocol replies; no model, ffprobe, or media decoding."""
    import json
    from types import SimpleNamespace
    from app.granted_subprocess import GrantedCommandResult
    request = _request(tmp_path)
    runtime = tmp_path / "runtime"
    (runtime / "scripts").mkdir(parents=True)
    (runtime / "models/musetalkV15").mkdir(parents=True)
    (runtime / "scripts/inference.py").touch()
    (runtime / "models/musetalkV15/unet.pth").touch()
    worker = tmp_path / "worker.py"
    worker.touch()
    calls = []
    checks = []
    def run(command, **options):
        calls.append((command, options))
        if command[0] == "ffprobe":
            if command[command.index("-of")+1] != "json":
                return GrantedCommandResult(0, b"8\n", b"")
            return GrantedCommandResult(0, json.dumps({"format": {"duration": 8}, "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 64, "height": 64,
                 "duration": 8, "avg_frame_rate": "25/1"},
                {"codec_type": "audio", "codec_name": "aac", "duration": 8}]}).encode(), b"")
        payload = json.loads(Path(command[command.index("--request")+1]).read_text())
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"synthetic provider output")
        Path(command[command.index("--response")+1]).write_text(json.dumps({
            "schema_version": 1, "status": "succeeded", "artifact_path": str(output),
            "duration_seconds": 8, "alignment_evidence": "synthetic; not media evidence"}))
        assert options["output_paths"] == (output,)
        assert options["env"]["OMP_NUM_THREADS"] == "1"
        return GrantedCommandResult(0, b"", b"")
    grant = SimpleNamespace(check=lambda: checks.append(True), run=run, threads=1, max_output_bytes=4096)
    provider = SubprocessAvatarAndLipSyncProvider(runtime_path=worker, worker_path=worker,
        runtime_root=runtime, model_id=request.model_id, supported_languages=(request.language,),
        runner=lambda command: pytest.fail("granted production must not use diagnostic runner"))
    service = AvatarAndLipSyncService(tmp_path / "output", provider)
    result = service.render(request, grant_context=grant)
    assert result.status == "succeeded" and result.duration_seconds == 8
    assert len(calls) == 3  # Worker plus input and output ffprobe protocol.
    cached = service.render(request, grant_context=grant)
    assert cached.status == "succeeded" and len(calls) == 5
    assert all(command[0] == "ffprobe" for command, _ in calls[3:])
    revoked = replace(request, authorization=replace(request.authorization, status="revoked"))
    assert service.render(revoked, grant_context=grant).status == "authorization_denied"
    assert len(calls) == 5 and len(checks) >= 5


def test_granted_avatar_cancellation_is_not_converted_to_provider_failure(tmp_path):
    from types import SimpleNamespace
    from app.granted_subprocess import GrantedCommandCancelled
    request = _request(tmp_path)
    provider = SubprocessAvatarAndLipSyncProvider(runtime_path=tmp_path / "worker",
        worker_path=tmp_path / "worker", runtime_root=tmp_path, model_id=request.model_id)
    def cancelled():
        raise GrantedCommandCancelled("cancelled")
    with pytest.raises(GrantedCommandCancelled):
        AvatarAndLipSyncService(tmp_path / "output", provider).render(request,
            grant_context=SimpleNamespace(check=cancelled))


@pytest.mark.parametrize("quality", ["fast", "high"])
def test_granted_person_provider_preserves_actual_fast_and_high_argv(tmp_path, quality):
    from types import SimpleNamespace
    from app.avatar_lipsync import LatentSyncAvatarAndLipSyncProvider
    from app.granted_subprocess import GrantedCommandResult
    calls = []
    context = SimpleNamespace(check=lambda: None, threads=1,
        run=lambda command, **options: calls.append((command, options)) or GrantedCommandResult(0, b"", b""))
    kwargs = dict(runtime_path=tmp_path / "python", worker_path=tmp_path / "worker.py",
                  runtime_root=tmp_path / "runtime", model_id="synthetic")
    provider = (LatentSyncAvatarAndLipSyncProvider(pose_root=tmp_path / "pose", **kwargs)
                if quality == "high" else SubprocessAvatarAndLipSyncProvider(**kwargs))
    argv = [str(tmp_path / "python"), str(tmp_path / "worker.py"), "--request", "request", "--response", "response",
            "--runtime-root", str(tmp_path / "runtime")]
    output = tmp_path / "output.mp4"
    provider._run_command(argv, grant_context=context, output_paths=(output,))
    assert calls[0][0] == (argv + ["--pose-root", str((tmp_path / "pose").resolve())] if quality == "high" else argv)
    assert calls[0][1]["output_paths"] == (output,)
    assert "timeout" not in calls[0][1]  # The Grant owns the remaining unit deadline.
    assert calls[0][1]["env"]["HF_HUB_OFFLINE"] == "1"


def test_latentsync_16_stages_a_dependency_without_symlink_privileges(tmp_path, monkeypatch):
    import errno

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "model_workers"))
    import latentsync_16_worker

    source = tmp_path / "upstream.pt"
    destination = tmp_path / "stage.pt"
    source.write_bytes(b"model dependency")

    def cross_volume(*_args):
        raise OSError(errno.EXDEV, "cross-volume link")

    monkeypatch.setattr(latentsync_16_worker.os, "link", cross_volume)
    latentsync_16_worker._stage_file(source, destination)
    assert destination.read_bytes() == source.read_bytes()


def test_latentsync_16_mlx_requires_grant_and_uses_registered_offline_process(tmp_path):
    from app.lipsync_models import LatentSync16Runtime

    runtime = LatentSync16Runtime(tmp_path, tmp_path / "python", tmp_path / "worker.py", "mlx")
    catalog = SimpleNamespace(latentsync_runtime=runtime, verify=lambda _spec: SimpleNamespace(
        ready=True, model_path=str(tmp_path / "weights")))
    provider = LatentSync16Provider(catalog, None)
    command = ["python", "worker.py"]
    with pytest.raises(subprocess.SubprocessError, match="资源许可"):
        provider._run_command(command)

    calls = []
    context = SimpleNamespace(check=lambda: None, threads=2,
                              run=lambda argv, **kwargs: calls.append((argv, kwargs)) or
                              SimpleNamespace(returncode=0))
    output = tmp_path / "output.mp4"
    provider._run_command(command, grant_context=context, output_paths=(output,))
    argv, kwargs = calls[0]
    assert argv[:3] == ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)"]
    assert argv[-2:] == ["--backend", "mlx"]
    assert kwargs["output_paths"] == (output,)
    assert kwargs["env"]["OMP_NUM_THREADS"] == "2"


def test_latentsync_16_registered_still_prepares_a_loopable_video(tmp_path):
    from PIL import Image
    from app.profile_reference_media import ProfileReferenceInput

    photo = tmp_path / "portrait.jpg"
    Image.new("RGB", (1080, 1920), (180, 85, 100)).save(photo)
    original = photo.read_bytes()
    request = _request(tmp_path)
    reference = ProfileReferenceInput.from_source(
        workspace_id=request.workspace_id, profile_id=request.person.id,
        profile_version=request.person.version, asset_id="portrait", segment_id=None,
        source_path=photo, source_type="image", kind="person_visual",
        start_seconds=None, end_seconds=None, source_duration_seconds=None)
    request = replace(request, visual_path=str(photo), visual_reference_input=reference)
    provider = LatentSync16Provider.__new__(LatentSync16Provider)

    def run(argv, *, output_paths):
        assert len(output_paths) == 1 and output_paths[0] != photo
        return subprocess.run(argv, capture_output=True, timeout=10)

    loopable = provider._prepare_worker_visual(request, photo, tmp_path,
                                               SimpleNamespace(threads=2, run=run))
    output = tmp_path / "normalized.mp4"
    # Run the same fixed-frame normalization as the measured inference worker.
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-stream_loop", "-1",
        "-i", str(loopable), "-map", "0:v:0", "-an", "-vf", "fps=25", "-frames:v", "160",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(output)],
        check=True, timeout=15)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,nb_frames,r_frame_rate", "-of", "json", str(output)],
        check=True, capture_output=True, timeout=10)
    assert json.loads(probe.stdout)["streams"] == [
        {"width": 1080, "height": 1920, "r_frame_rate": "25/1", "nb_frames": "160"}
    ]
    assert photo.read_bytes() == original


@pytest.mark.parametrize("frames", [16, 32, 33])
def test_latentsync_16_mlx_mux_keeps_exact_frames_and_fractional_audio_tail(tmp_path, monkeypatch, frames):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "model_workers"))
    import latentsync_16_worker as worker

    video, audio, output = (tmp_path / name for name in ("source.mp4", "audio.wav", "output.mp4"))
    samples = frames * 640 + 272
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, samples, "NONE", "not compressed"))
        wav.writeframes(b"\x04\x00" * samples)
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    subprocess.run([*command, "-f", "lavfi", "-i", "testsrc2=s=64x64:r=30:d=2", "-c:v", "libx264",
                    str(video)], check=True)
    (tmp_path / "whisper").mkdir()
    (tmp_path / "whisper/tiny.pt").write_bytes(b"fixture")
    monkeypatch.setattr(worker.mlx, "check_runtime", lambda _runtime, _revision: None)
    monkeypatch.setattr(worker.mlx, "verify_weights", lambda *_args, **_kwargs: None)

    def emit_video(_runtime, _weights, staged, _speech, synced, _scratch, *, memory_limit):
        # Substitute only the inference output. Exercise the actual staging,
        # duration, mux, decode validation and publication around it.
        subprocess.run([*command, "-i", str(staged), "-map", "0:v:0", "-c", "copy", str(synced)], check=True)
        return 1234

    monkeypatch.setattr(worker.mlx, "render", emit_video)
    result = worker._render({
        "schema_version": 1, "model_id": LATENTSYNC_16_MODEL_ID, "language": "en",
        "motion": "natural", "expression": "neutral", "visual_path": str(video),
        "audio_path": str(audio), "output_path": str(output),
    }, tmp_path, tmp_path, "revision", "mlx")
    evidence = json.loads(result["alignment_evidence"])
    assert output.is_file()
    assert evidence["backend"] == "apple_mlx"
    assert evidence["rendered_frames"] == frames
    assert evidence["window_count"] == (frames + 15) // 16
    assert evidence["audio_conditioning_samples"] == samples
    assert evidence["peak_mlx_allocation_bytes"] == 1234
    assert evidence["audio_duration_seconds"] == pytest.approx(samples / 16000, abs=0.001)
    assert evidence["video_duration_seconds"] == pytest.approx(frames / 25, abs=0.001)
    with pytest.raises(worker.MediaTimingError):
        worker._mlx_media_evidence(output, frames - 1, samples / 16000)



def test_latentsync_16_mlx_rejects_a_same_size_wrong_conversion(tmp_path, monkeypatch):
    import hashlib
    from model_workers import latentsync_16_mlx as mlx

    monkeypatch.setattr(mlx, "UNET_FILE", ("unet.safetensors", 4, hashlib.sha256(b"good").hexdigest()))
    monkeypatch.setattr(mlx, "RUNTIME_WEIGHTS", ())
    path = tmp_path / "unet.safetensors"
    path.write_bytes(b"bad!")
    with pytest.raises(ValueError, match="权重校验失败"):
        mlx.verify_weights(tmp_path, checksums=True)


def test_latentsync_16_calibration_identity_rejects_changed_runtime_weights(tmp_path, monkeypatch):
    import hashlib
    from app import lipsync_models

    worker = tmp_path / "latentsync_16_worker.py"
    python = tmp_path / "python"
    for path in (worker, python, *(tmp_path / name for name in (
            "latentsync_16_mlx.py", "latentsync_entry.py", "media_timing.py"))):
        path.write_bytes(path.name.encode())
    original = tmp_path / "latentsync_unet.pt"
    original.write_bytes(b"original")
    converted = tmp_path / "unet.safetensors"
    converted.write_bytes(b"converted")
    runtime = lipsync_models.LatentSync16Runtime(tmp_path, python, worker, "mlx")
    monkeypatch.setattr(lipsync_models.LatentSync16Runtime, "issue", lambda self: None)
    monkeypatch.setattr(lipsync_models.shutil, "which", lambda _name: str(python))
    monkeypatch.setattr(lipsync_models, "LATENTSYNC_16_DOWNLOADS",
                        ((original.name, 8, hashlib.sha256(b"original").hexdigest()),))
    monkeypatch.setattr(lipsync_models.mlx, "UNET_FILE",
                        (converted.name, 9, hashlib.sha256(b"converted").hexdigest()))
    monkeypatch.setattr(lipsync_models.mlx, "RUNTIME_WEIGHTS", ())

    identity = runtime.backend_identity(tmp_path)
    assert identity["model_id"] == LATENTSYNC_16_MODEL_ID
    assert identity["backend"] == "mlx"
    assert identity["runtime_root"] == str(tmp_path.resolve())
    assert identity["artifacts"][converted.name]["path"] == str(converted.resolve())
    converted.write_bytes(b"bad file!")
    with pytest.raises(ValueError, match="文件校验失败"):
        runtime.backend_identity(tmp_path)


def test_latentsync_16_runtime_retry_recovers_transient_native_probe_failure(tmp_path, monkeypatch):
    from app import lipsync_models

    runtime = lipsync_models.LatentSync16Runtime(tmp_path, tmp_path / "python", tmp_path / "worker.py", "mlx")
    replies = iter([
        SimpleNamespace(returncode=-11, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ])
    monkeypatch.setattr(lipsync_models.subprocess, "run", lambda *args, **kwargs: next(replies))
    assert "-11" in lipsync_models._runtime_issue(runtime, ())
    assert lipsync_models._runtime_issue(runtime, ()) is None
    assert lipsync_models._runtime_issue(runtime, ()) is None
