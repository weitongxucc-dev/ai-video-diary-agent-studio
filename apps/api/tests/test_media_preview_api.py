from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.avatar_lipsync import AvatarPreviewService, UnavailableAvatarAndLipSyncProvider
from app.creation_execution import CreationExecutionActor
from app.narration import VoicePreviewService
from app.production import MaterialAsset, ProductionRepository
from app.voice_clone import UnavailableVoiceCloneProvider, VoiceCloneResult
from _media_preview_test_fixtures import (
    build_preview_runtime,
    register_synthetic_profiles,
    synthetic_wav_bytes,
)


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
        grant = _kwargs.get("grant_context")
        assert grant is not None and grant.grant.owner_type == "media_preview"
        assert grant.grant.unit_key.startswith("voice:")
        output = Path(output_path)
        if self.writes_output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(synthetic_wav_bytes())
        return VoiceCloneResult(str(output), 1.0, 24000)


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
    voice_service = VoicePreviewService(output_root, provider)
    avatar_service = AvatarPreviewService(output_root, UnavailableAvatarAndLipSyncProvider())
    monkeypatch.setattr(main_module, "voice_preview_service", voice_service)
    monkeypatch.setattr(
        main_module,
        "avatar_preview_service",
        avatar_service,
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )
    # 真实成员服务：内容以真实身份入库，读取也用同一可信身份。
    members = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", members)
    owner_member, owner_token = members.whoami(None)
    person, voice, avatar_authorization, reference, _image = register_synthetic_profiles(
        repository, workspace_id="workspace-a", member_id=owner_member.member_id,
        person_id="person-a", voice_id="voice-a",
    )
    runtime_harness = build_preview_runtime(
        repository, output_root=output_root, voice_service=voice_service,
        avatar_service=avatar_service, member_id=owner_member.member_id,
        workspace_id="workspace-a",
    )
    headers = {"X-Local-Member-Token": owner_token}

    def permit_actor(request, workspace_id):
        member = members.resolve_member(request.headers.get("x-local-member-token"))
        assert member.member_id == owner_member.member_id
        return CreationExecutionActor(workspace_id, member.member_id)

    monkeypatch.setattr(main_module, "_creation_runtime", lambda: runtime_harness.runtime)
    monkeypatch.setattr(main_module, "_require_permit_actor", permit_actor)
    try:
        yield (
            TestClient(main_module.app), repository, provider, person, voice,
            avatar_authorization, reference, output_root,
            SimpleNamespace(owner_token=owner_token, owner_member_id=owner_member.member_id,
                            headers=headers, runtime=runtime_harness.runtime,
                            resources=runtime_harness.resources, voice_service=voice_service),
        )
    finally:
        assert runtime_harness.close()


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
        "authorization_id": "person-a-avatar-auth",
        "authorization_version": "v1",
        "model_id": "avatar-model-v1",
        "retry": retry,
    }


def test_materials_expose_workspace_reference_without_absolute_submission(preview_client):
    client, repository, *_rest = preview_client
    identity = _rest[-1]
    from PIL import Image

    headers = identity.headers
    video = repository.video_library_path / "approved" / "demo.png"
    video.parent.mkdir(exist_ok=True)
    Image.new("RGB", (16, 16), color=(24, 80, 140)).save(video, format="PNG")
    stat = video.stat()
    repository._save_asset(MaterialAsset(
        id="demo-image", workspace_id="workspace-a", file_name=video.name,
        path=str(video), size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime),
        media_type="image", owner_user_id=identity.owner_member_id,
    ))

    response = client.get("/api/production/materials", headers=headers)

    assert response.status_code == 200
    asset = next(item for item in response.json()["assets"] if item["file_name"] == "demo.png")
    assert asset["workspace_reference"] == "workspace://approved/demo.png"
    assert asset["path"] != asset["workspace_reference"]


def test_voice_preview_success_reuse_safe_output_and_workspace_isolation(preview_client):
    client, repository, provider, _person, _voice, _authorization, reference, _output, identity = preview_client
    headers = identity.headers

    first = client.post("/api/media/previews/voice", json=_voice_payload(), headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "queued"
    assert first.json()["attempt"] == 1
    assert first.json()["output_url"] is None
    assert provider.calls == 0
    assert identity.runtime.run_once()
    completed = repository.get_media_preview_run("workspace-a", "voice", first.json()["identity"])
    assert completed.status == "succeeded" and completed.output_path
    second = client.post("/api/media/previews/voice", json=_voice_payload(), headers=headers)
    listed = client.get("/api/media/previews?preview_type=voice", headers=headers)

    assert second.json()["status"] == "succeeded"
    assert second.json()["output_url"]
    assert second.json()["id"] == first.json()["id"]
    assert provider.calls == 1
    assert provider.reference_paths == [reference.resolve()]
    assert listed.json()["total_count"] == 1
    assert client.get(second.json()["output_url"], headers=headers).status_code == 200
    other_workspace = {**headers, "x-test-workspace": "workspace-b"}
    assert client.get("/api/media/previews", headers=other_workspace).json()["items"] == []
    assert client.get(
        f"/api/media/previews/voice/{first.json()['identity']}/attempts",
        headers=other_workspace,
    ).status_code == 404


def test_voice_preview_unavailable_failure_retry_and_attempt_order(preview_client, monkeypatch):
    client, repository, provider, *_rest, output_root, identity = preview_client
    headers = identity.headers
    monkeypatch.setattr(
        main_module,
        "voice_preview_service",
        VoicePreviewService(output_root, UnavailableVoiceCloneProvider("missing runtime")),
    )
    unavailable = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="unavailable"), headers=headers
    )
    monkeypatch.setattr(main_module, "voice_preview_service", VoicePreviewService(output_root, provider))
    provider.writes_output = False
    failed = client.post("/api/media/previews/voice", json=_voice_payload(preview_text="retry"), headers=headers)
    assert failed.json()["status"] == "queued" and provider.calls == 0
    assert identity.runtime.run_once()
    unchanged = client.post("/api/media/previews/voice", json=_voice_payload(preview_text="retry"), headers=headers)
    provider.writes_output = True
    retried = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="retry", retry=True), headers=headers
    )
    assert retried.json()["status"] == "queued" and retried.json()["attempt"] == 2
    assert identity.runtime.run_once()
    attempts = client.get(
        f"/api/media/previews/voice/{failed.json()['identity']}/attempts", headers=headers
    )
    listed = client.get("/api/media/previews?preview_type=voice", headers=headers)

    assert (unavailable.status_code, unavailable.json()["status"], unavailable.json()["reason"]) == (
        200,
        "unavailable",
        "capability_unavailable",
    )
    assert unchanged.json()["id"] == failed.json()["id"]
    assert (unchanged.json()["status"], unchanged.json()["attempt"]) == ("failed", 1)
    assert [(item["status"], item["attempt"]) for item in attempts.json()["items"]] == [
        ("succeeded", 2), ("failed", 1),
    ]
    retried_final = repository.get_media_preview_run("workspace-a", "voice", retried.json()["identity"])
    assert (retried_final.status, retried_final.attempt) == ("succeeded", 2)
    assert [item["attempt"] for item in attempts.json()["items"]] == [2, 1]
    assert [item["attempt"] for item in listed.json()["items"] if item["identity"] == failed.json()["identity"]] == [2]


def test_avatar_preview_requires_same_workspace_succeeded_voice_and_rejects_client_audio_path(
    preview_client,
):
    client, _repository, provider, _person, _voice, _authorization, _reference, _output, identity_context = preview_client
    headers = identity_context.headers
    voice = client.post("/api/media/previews/voice", json=_voice_payload(preview_text="avatar"), headers=headers)
    assert voice.json()["status"] == "queued" and provider.calls == 0
    assert identity_context.runtime.run_once()
    identity = voice.json()["identity"]
    forged_audio = client.post(
        "/api/media/previews/avatar-lipsync",
        json={**_avatar_payload(identity), "audio_path": "/tmp/forged.wav"},
        headers=headers,
    )
    cross_workspace = client.post(
        "/api/media/previews/avatar-lipsync",
        headers={**headers, "x-test-workspace": "workspace-b"},
        json=_avatar_payload(identity),
    )
    avatar = client.post("/api/media/previews/avatar-lipsync", json=_avatar_payload(identity), headers=headers)
    provider.writes_output = False
    failed_voice = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="blocked"), headers=headers
    )
    assert failed_voice.json()["status"] == "queued"
    assert identity_context.runtime.run_once()
    not_succeeded = client.post(
        "/api/media/previews/avatar-lipsync",
        json=_avatar_payload(failed_voice.json()["identity"]),
        headers=headers,
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


def test_cancelled_voice_preview_never_calls_provider(preview_client):
    client, repository, provider, *_rest, identity = preview_client
    headers = identity.headers
    queued = client.post(
        "/api/media/previews/voice", json=_voice_payload(preview_text="取消预览"), headers=headers
    )

    assert (queued.status_code, queued.json()["status"]) == (200, "queued")
    assert provider.calls == 0
    cancelled = client.post(
        f"/api/media/previews/voice/{queued.json()['identity']}/cancel", headers=headers
    )

    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")
    assert identity.runtime.run_once() is False
    persisted = repository.get_media_preview_run("workspace-a", "voice", queued.json()["identity"])
    assert persisted.status == "cancelled" and provider.calls == 0
