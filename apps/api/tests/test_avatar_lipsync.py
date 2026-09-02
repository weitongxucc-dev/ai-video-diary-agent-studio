import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncRequest,
    AvatarAndLipSyncService,
    PersonProfile,
    SubprocessAvatarAndLipSyncProvider,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile,
    avatar_lipsync_cache_key,
)


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


def test_avatar_lipsync_succeeds_only_after_valid_provider_output(tmp_path):
    request = _request(tmp_path)

    def success_runner(command):
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"valid media")
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

    result = AvatarAndLipSyncService(
        tmp_path / "outputs", _configured_provider(tmp_path, success_runner)
    ).render(request)

    assert result.status == "succeeded"
    assert Path(result.artifact_path or "").is_file()


def test_avatar_lipsync_can_create_an_immutable_regeneration_output(tmp_path):
    request = _request(tmp_path)
    calls: list[Path] = []

    def success_runner(command):
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"regenerated media")
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
