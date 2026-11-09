"""Public registration/selection with isolated SQLite and real synthetic PNG/PCM."""
from dataclasses import replace
from datetime import datetime, timezone
import wave

from PIL import Image
import pytest

from app import production
from app.creation.contracts import CreationPlanPersonReference, CreationPlanVoiceReference
from app.materials.contracts import MaterialAsset
from app.profiles.contracts import Authorization, PersonProfile, ProfileReference, VoiceProfile
from app.profiles.registration import ProfileRegistrationUseCase
from app.source_policy import content_owner


def case(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    repo = production.ProductionRepository(
        tmp_path / "db.sqlite3", library,
        duration_reader=lambda _: 2.0, scene_reader=lambda *_: [],
    )
    portrait = library / "portrait.png"
    Image.new("RGB", (2, 2), (100, 80, 60)).save(portrait)
    audio = library / "reference.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 16000)
    references = {}
    for identity, path, media_type in (("portrait", portrait, "image"), ("audio", audio, "audio")):
        repo._save_asset(MaterialAsset(
            id=identity, workspace_id="ws", file_name=path.name, path=str(path),
            size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            media_type=media_type, duration_seconds=2.0 if media_type == "audio" else None,
            owner_user_id="member",
        ))
        references[identity] = repo.workspace_media_reference_for_asset("ws", identity)
        with repo._connect() as conn:
            repo._claim_workspace_media_reference(conn, workspace_id="ws", reference=references[identity])
    person = PersonProfile(
        id="person", workspace_id="ws", display_name="合成人物", version="v1",
        avatar_reference=references["portrait"], background_capabilities=(), shot_capabilities=(),
        reference=ProfileReference(), owner_user_id="untrusted-input",
    )
    voice = VoiceProfile(
        id="voice", workspace_id="ws", version="v1", language="zh",
        reference_audio_path=references["audio"], reference_text="合成参考",
        display_name="独立声音", reference=ProfileReference(), owner_user_id="untrusted-input",
    )
    return repo, person, voice


def test_registration_and_fixed_selections_survive_new_versions_and_restart(tmp_path):
    repo, person, voice = case(tmp_path)
    assert isinstance(repo._profile_registration, ProfileRegistrationUseCase)
    assert not hasattr(production, "MediaAuthorityError")
    assert production._CreationPlanPersonReference is CreationPlanPersonReference
    assert production._CreationPlanVoiceReference is CreationPlanVoiceReference
    assert production.content_owner is content_owner
    saved_person = repo.save_person_profile(person, verified_owner_user_id="member")
    saved_voice = repo.save_versioned_voice_profile(voice, verified_owner_user_id="member")
    assert saved_person.owner_user_id == saved_voice.owner_user_id == "member"
    assert saved_person.reference.asset_id == "portrait"
    assert saved_voice.reference.asset_id == "audio"
    assert not saved_voice.person_profile_id and not saved_voice.model_id
    assert saved_voice.authorization is None
    assert repo.set_default_versioned_voice("ws", "voice", "v1", viewer_user_id="member") == saved_voice
    with pytest.raises(ValueError, match="版本"):
        repo.save_versioned_voice_profile(replace(saved_voice, display_name="覆盖"), verified_owner_user_id="member")
    linked = repo.save_versioned_voice_profile(
        replace(saved_voice, version="v2", person_profile_id="person", person_version="v1"),
        verified_owner_user_id="member",
    )
    repo.save_person_profile(replace(saved_person, version="v2", display_name="新版"), verified_owner_user_id="member")
    reopened = production.ProductionRepository(repo.database_path, repo.video_library_path)
    assert reopened.get_default_versioned_voice("ws") == saved_voice
    assert reopened.profile_records.get_versioned_voice_profile("ws", "voice") == linked
    selected_person = reopened.creation_plan_person_reference("ws", "person", "v1", viewer_user_id="member")
    selected_voice = reopened.creation_plan_voice_reference("ws", "voice", "v1", viewer_user_id="member")
    assert selected_person == CreationPlanPersonReference("person", "v1")
    assert selected_voice.version == "v1" and selected_voice.person_profile_id == ""
    source = reopened.profile_reference_input(saved_voice, viewer_user_id="member")
    assert source.profile_version == "v1" and source.asset_id == "audio"
    assert source.kind == "voice_audio" and source.source_duration_seconds == 2.0
    assert not source.needs_derivation
    # The existing API reference presenter still consumes this private delegate.
    assert reopened._profile_reference_source(
        saved_person, viewer_user_id="member", local_diagnostic=False,
        for_generation=False, probe_media=False,
    )[0].name == "portrait.png"


def test_legacy_mapping_is_explicit_preserves_original_and_checks_language_first(tmp_path):
    repo, person, voice = case(tmp_path)
    saved_person = repo.save_person_profile(person, verified_owner_user_id="member")
    legacy = repo.save_voice_profile(production.VoiceProfile(
        id="legacy", workspace_id="ws", name="旧声音", reference_text=voice.reference_text,
        reference_audio_path=voice.reference_audio_path, consent_confirmed=True,
    ))
    with pytest.raises(KeyError):
        repo.profile_records.get_versioned_voice_profile("ws", legacy.id)
    authorization = Authorization(
        id="voice-grant", version="v1", rights_holder="合成夹具",
        content_types=("voice",), usage_scopes=("narration",),
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc), expires_at=None,
        evidence_reference="consent://synthetic/1", status="active", workspace_id="ws",
    )
    arguments = dict(
        workspace_id="ws", person_profile_id=saved_person.id, person_version=saved_person.version,
        version="mapped-v1", authorization=authorization, verified_owner_user_id="member",
    )
    with pytest.raises(ValueError, match="配音语言"):
        repo.migrate_legacy_voice_profile(legacy_voice_profile_id="absent", language="invalid", **arguments)
    mapped = repo.migrate_legacy_voice_profile(legacy_voice_profile_id=legacy.id, language="zh", **arguments)
    assert repo.get_voice_profile("ws", legacy.id) == legacy
    assert repo.profile_records.get_versioned_voice_profile("ws", legacy.id, "mapped-v1") == mapped
    assert mapped.authorization == authorization
    assert mapped.reference.asset_id == "audio" and mapped.person_version == "v1"
