"""The narration-edit grant may materialize its exact voice reference."""

import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.avatar_lipsync import ProfileReference, VoiceProfile
from app.production import ProductionRepository


def _wav_bytes() -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 16000)
    return target.getvalue()


class _Context:
    def __init__(self, *, workspace_id: str = "ws", owner_type: str = "creation_job",
                 unit_key: str = "narration_edit:job"):
        self.grant = SimpleNamespace(workspace_id=workspace_id, owner_type=owner_type,
                                     unit_key=unit_key)
        self.checks = 0

    def check(self):
        self.checks += 1


@pytest.fixture
def voice_repository(tmp_path):
    repository = ProductionRepository(database_path=tmp_path / "db.sqlite3",
                                      video_library_path=tmp_path / "library",
                                      duration_reader=lambda _: 2, scene_reader=lambda *_: [])
    path, _, _ = repository.import_voice_reference_wav(
        "ws", _wav_bytes(), verified_owner_user_id="member")
    voice = repository.save_versioned_voice_profile(
        VoiceProfile(id="voice", workspace_id="ws", display_name="测试声音", version="v1",
                     language="zh", reference_audio_path=path, reference_text="测试文本",
                     reference=ProfileReference()),
        verified_owner_user_id="member")
    return repository, voice


def test_narration_edit_grant_materializes_voice_reference_without_decoding(
    voice_repository, monkeypatch, tmp_path
):
    repository, voice = voice_repository
    context = _Context()
    calls = []

    def materialize(source, *, output_root, grant_context, recheck):
        recheck()
        calls.append((source, output_root, grant_context))
        return SimpleNamespace(path=Path(source.source_path), source=source, derived=False,
                               duration_seconds=2)

    monkeypatch.setattr("app.profile_reference_media.materialize_reference", materialize)
    artifact = repository.materialize_profile_reference(voice, viewer_user_id="member",
                                                        grant_context=context)

    assert artifact.source.profile_id == voice.id
    assert artifact.source.profile_version == voice.version
    assert calls == [(artifact.source, tmp_path / "library" / ".profile_references", context)]
    assert context.checks == 2


@pytest.mark.parametrize(
    "grant",
    [
        {"owner_type": "media_preview", "unit_key": "avatar_lipsync:preview"},
        {"unit_key": "person_render:job"},
        {"workspace_id": "other"},
    ],
)
def test_narration_edit_reference_rejects_wrong_grant_before_materialization(
    voice_repository, monkeypatch, grant
):
    repository, voice = voice_repository
    context = _Context(**grant)
    calls = []
    monkeypatch.setattr("app.profile_reference_media.materialize_reference",
                        lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(PermissionError):
        repository.materialize_profile_reference(voice, viewer_user_id="member",
                                                  grant_context=context)

    assert calls == []
