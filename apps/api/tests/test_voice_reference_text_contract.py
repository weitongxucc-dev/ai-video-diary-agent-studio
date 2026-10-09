from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.avatar_lipsync import ProfileReference, VoiceProfile
from app.local_members import LocalMemberService
from app.narration import VoicePreviewService
from app.production import ProductionRepository
from app.source_repository import SourceRepository
from app.voice_clone import VoiceCloneError
from _media_preview_test_fixtures import synthetic_wav_bytes


class _NoCallProvider:
    model_id = "controlled-no-call-provider"
    ready = True
    supported_languages = ("zh",)
    supports_pronunciation_dictionary = True
    calls = 0

    def generate(self, **_kwargs):
        self.calls += 1
        raise VoiceCloneError("provider must not be called without reference text")


@pytest.fixture
def registered_voice_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    library = tmp_path / "library"
    library.mkdir()
    database_path = tmp_path / "voices.sqlite3"
    repository = ProductionRepository(database_path=database_path, video_library_path=library)
    members = LocalMemberService(database_path)
    owner, token = members.whoami(None)
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "source_repository", SourceRepository(database_path))
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "voice-workspace")
    reference_path, _, _ = repository.import_voice_reference_wav(
        "voice-workspace", synthetic_wav_bytes(), verified_owner_user_id=owner.member_id
    )
    absolute_path = repository.resolve_workspace_media_reference("voice-workspace", reference_path)
    asset = repository._asset_by_path("voice-workspace", str(absolute_path))
    client = TestClient(main_module.app, headers={"X-Local-Member-Token": token})
    return SimpleNamespace(
        repository=repository,
        members=members,
        owner=owner,
        client=client,
        reference_path=reference_path,
        asset=asset,
    )


@pytest.mark.parametrize(
    ("voice_id", "reference_text"),
    [
        ("voice-omitted-text", None),
        ("voice-blank-text", "   "),
    ],
)
def test_voice_reference_can_be_registered_without_transcript_and_read_back(
    registered_voice_environment, voice_id: str, reference_text: str | None
) -> None:
    env = registered_voice_environment
    payload = {
        "id": voice_id,
        "version": "voice-v1",
        "language": "zh",
        "display_name": "合成声音参考",
        "reference_audio_path": env.reference_path,
        "reference": {"asset_id": env.asset.id},
    }
    if reference_text is not None:
        payload["reference_text"] = reference_text

    saved = env.client.post("/api/media/voice-profiles", json=payload)

    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == "voice-v1"
    assert saved.json()["reference_audio_path"] == env.reference_path
    assert saved.json()["reference_text"] == (reference_text or "")
    reread = env.client.get(f"/api/media/voice-profiles/{voice_id}?version=voice-v1")
    assert reread.status_code == 200, reread.text
    assert reread.json()["version"] == "voice-v1"
    assert reread.json()["reference_text"] == (reference_text or "")
    persisted = env.repository.get_versioned_voice_profile(
        "voice-workspace", voice_id, "voice-v1"
    )
    assert persisted.reference_audio_path == env.reference_path
    assert persisted.reference_text == (reference_text or "")


def test_missing_reference_text_blocks_real_preview_before_provider_and_preserves_profile(
    registered_voice_environment, tmp_path: Path
) -> None:
    env = registered_voice_environment
    voice = env.repository.save_versioned_voice_profile(
        VoiceProfile(
            id="voice-needs-transcript",
            workspace_id="voice-workspace",
            display_name="尚未转写的合成声音",
            version="voice-v1",
            language="zh",
            reference_audio_path=env.reference_path,
            reference_text="",
            model_id="controlled-no-call-provider",
            reference=ProfileReference(asset_id=env.asset.id),
        ),
        verified_owner_user_id=env.owner.member_id,
    )
    provider = _NoCallProvider()
    service = VoicePreviewService(tmp_path / "outputs", provider)

    result = service.request(
        repository=env.repository,
        workspace_id="voice-workspace",
        voice_profile_id=voice.id,
        voice_version=voice.version,
        preview_text="一条合成预览文案",
        viewer_user_id=env.owner.member_id,
    )

    assert result.status == "blocked"
    assert result.reason == "reference_text_missing"
    assert result.output_path is None
    assert provider.calls == 0
    reread = env.repository.get_versioned_voice_profile(
        "voice-workspace", voice.id, voice.version
    )
    assert reread == voice
    assert Path(env.repository.resolve_workspace_media_reference(
        "voice-workspace", reread.reference_audio_path
    )).is_file()
