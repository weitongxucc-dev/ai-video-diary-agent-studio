from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncResult,
    AvatarPreviewService,
    PersonProfile,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile,
)
from app.narration import VoicePreviewService
from app.production import ProductionRepository
from app.voice_clone import UnavailableVoiceCloneProvider, VoiceCloneResult


class _VoiceProvider:
    model_id = "fixture-voice-runtime-v1"
    supported_languages = ("zh-en",)
    ready = True
    message = "fixture ready"

    def __init__(self, *, writes_output: bool = True) -> None:
        self.writes_output = writes_output
        self.calls = 0
        self.reference_paths: list[Path] = []

    def generate(self, *, output_path, reference_audio_path, **_kwargs):
        self.calls += 1
        self.reference_paths.append(Path(reference_audio_path))
        output = Path(output_path)
        if self.writes_output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fixture audio")
        return VoiceCloneResult(str(output), 1.0, 24000)


def _authorization(
    workspace_id: str,
    authorization_id: str,
    content_type: str,
    scopes: tuple[str, ...],
) -> Authorization:
    return Authorization(
        id=authorization_id,
        version="v1",
        rights_holder="权利人",
        content_types=(content_type,),
        usage_scopes=scopes,
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        evidence_reference="consent://preview-fixture",
        status="active",
        workspace_id=workspace_id,
    )


def _save_media_profiles(repository: ProductionRepository, workspace_id: str) -> tuple[PersonProfile, VoiceProfile, Authorization, Path]:
    reference = repository.video_library_path / "approved" / f"{workspace_id}-voice.wav"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"voice reference")
    person = PersonProfile(
        id="person-a",
        workspace_id=workspace_id,
        display_name="主播 A",
        version="person-v1",
        avatar_reference="asset://avatar/person-a",
        background_capabilities=("studio",),
        shot_capabilities=("medium",),
        authorization=_authorization(
            workspace_id, "person-auth", "person_likeness", ("avatar_lipsync",)
        ),
    )
    voice = VoiceProfile(
        id="voice-a",
        workspace_id=workspace_id,
        person_profile_id=person.id,
        person_version=person.version,
        version="voice-v1",
        language="zh-en",
        reference_audio_path=f"workspace://approved/{reference.name}",
        reference_text="声音参考文字",
        model_id="voice-model-v1",
        authorization=_authorization(
            workspace_id, "voice-auth", "voice", ("narration", "avatar_lipsync")
        ),
    )
    avatar_authorization = _authorization(
        workspace_id, "avatar-auth", "avatar_video", ("avatar_lipsync",)
    )
    repository.save_person_profile(person)
    repository.save_versioned_voice_profile(voice)
    repository.save_authorization(avatar_authorization)
    return person, voice, avatar_authorization, reference


@pytest.fixture
def preview_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    library = tmp_path / "library"
    library.mkdir()
    output_root = tmp_path / "outputs"
    repository = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 10.0,
        scene_reader=lambda _path, _duration: [],
    )
    provider = _VoiceProvider()
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(video_inbox_path=library, video_output_path=output_root),
    )
    monkeypatch.setattr(
        main_module,
        "voice_preview_service",
        VoicePreviewService(output_root, provider),
    )
    monkeypatch.setattr(
        main_module,
        "avatar_preview_service",
        AvatarPreviewService(output_root, UnavailableAvatarAndLipSyncProvider()),
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )
    person, voice, avatar_authorization, reference = _save_media_profiles(repository, "workspace-a")
    return (
        TestClient(main_module.app),
        repository,
        provider,
        person,
        voice,
        avatar_authorization,
        reference,
        output_root,
    )


def _voice_payload(*, preview_text: str = "声音预览", retry: bool = False) -> dict[str, object]:
    return {
        "voice_profile_id": "voice-a",
        "voice_version": "voice-v1",
        "preview_text": preview_text,
        "retry": retry,
    }


def _avatar_payload(voice_identity: str, *, retry: bool = False) -> dict[str, object]:
    return {
        "person_profile_id": "person-a",
        "person_version": "person-v1",
        "voice_profile_id": "voice-a",
        "voice_version": "voice-v1",
        "voice_preview_identity": voice_identity,
        "motion": "natural",
        "expression": "neutral",
        "authorization_id": "avatar-auth",
        "authorization_version": "v1",
        "model_id": "avatar-model-v1",
        "retry": retry,
    }


def test_materials_expose_workspace_reference_without_absolute_submission(preview_client):
    client, repository, *_rest = preview_client
    video = repository.video_library_path / "approved" / "demo.mp4"
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(b"media")
    repository.scan_library("workspace-a")

    response = client.get("/api/production/materials")

    assert response.status_code == 200
    asset = next(item for item in response.json()["assets"] if item["file_name"] == "demo.mp4")
    assert asset["workspace_reference"] == "workspace://approved/demo.mp4"
    assert asset["path"] != asset["workspace_reference"]


def test_voice_preview_success_reuse_safe_output_and_workspace_isolation(preview_client):
    client, _repository, provider, _person, _voice, _authorization, reference, _output = preview_client

    first = client.post("/api/media/previews/voice", json=_voice_payload())
    second = client.post("/api/media/previews/voice", json=_voice_payload())
    listed = client.get("/api/media/previews?preview_type=voice")

    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert first.json()["attempt"] == 1
    assert first.json()["output_url"]
    assert second.json()["id"] == first.json()["id"]
    assert provider.calls == 1
    assert provider.reference_paths == [reference.resolve()]
    assert listed.json()["total_count"] == 1
    assert client.get(first.json()["output_url"]).status_code == 200
    other_workspace = {"x-test-workspace": "workspace-b"}
    assert client.get("/api/media/previews", headers=other_workspace).json()["items"] == []
    assert client.get(
        f"/api/media/previews/voice/{first.json()['identity']}/attempts",
        headers=other_workspace,
    ).status_code == 404


def test_voice_preview_unavailable_failure_retry_and_attempt_order(preview_client, monkeypatch):
    client, repository, provider, *_rest, output_root = preview_client
    monkeypatch.setattr(
        main_module,
        "voice_preview_service",
        VoicePreviewService(output_root, UnavailableVoiceCloneProvider("missing runtime")),
    )
    unavailable = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="unavailable")
    )
    monkeypatch.setattr(main_module, "voice_preview_service", VoicePreviewService(output_root, provider))
    provider.writes_output = False
    failed = client.post("/api/media/previews/voice", json=_voice_payload(preview_text="retry"))
    unchanged = client.post("/api/media/previews/voice", json=_voice_payload(preview_text="retry"))
    provider.writes_output = True
    retried = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="retry", retry=True)
    )
    attempts = client.get(
        f"/api/media/previews/voice/{failed.json()['identity']}/attempts"
    )
    listed = client.get("/api/media/previews?preview_type=voice")

    assert (unavailable.status_code, unavailable.json()["status"], unavailable.json()["reason"]) == (
        200,
        "unavailable",
        "capability_unavailable",
    )
    assert (failed.json()["status"], failed.json()["attempt"]) == ("failed", 1)
    assert unchanged.json()["id"] == failed.json()["id"]
    assert (retried.json()["status"], retried.json()["attempt"]) == ("succeeded", 2)
    assert [item["attempt"] for item in attempts.json()["items"]] == [2, 1]
    assert [item["attempt"] for item in listed.json()["items"] if item["identity"] == failed.json()["identity"]] == [2]


def test_avatar_preview_requires_same_workspace_succeeded_voice_and_rejects_client_audio_path(
    preview_client,
):
    client, _repository, provider, _person, _voice, _authorization, _reference, _output = preview_client
    voice = client.post("/api/media/previews/voice", json=_voice_payload(preview_text="avatar"))
    identity = voice.json()["identity"]
    forged_audio = client.post(
        "/api/media/previews/avatar-lipsync",
        json={**_avatar_payload(identity), "audio_path": "/tmp/forged.wav"},
    )
    cross_workspace = client.post(
        "/api/media/previews/avatar-lipsync",
        headers={"x-test-workspace": "workspace-b"},
        json=_avatar_payload(identity),
    )
    avatar = client.post("/api/media/previews/avatar-lipsync", json=_avatar_payload(identity))
    provider.writes_output = False
    failed_voice = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="blocked")
    )
    not_succeeded = client.post(
        "/api/media/previews/avatar-lipsync",
        json=_avatar_payload(failed_voice.json()["identity"]),
    )

    assert forged_audio.status_code == 422
    assert cross_workspace.status_code == 404
    assert (avatar.status_code, avatar.json()["status"], avatar.json()["reason"]) == (
        200,
        "unavailable",
        "capability_unavailable",
    )
    assert not_succeeded.status_code == 409
    assert not_succeeded.json()["detail"]["error"] == "voice_preview_not_succeeded"
