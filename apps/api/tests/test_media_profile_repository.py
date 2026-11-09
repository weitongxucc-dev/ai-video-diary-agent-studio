from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import json
import wave
from PIL import Image

import pytest

from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncService,
    PersonProfile,
    ProfileReference,
    PronunciationEntry,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile as VersionedVoiceProfile,
)
from app.models import Episode, EpisodeStatus, ScriptPackage, ScriptSection
from app.narration import NarrationGenerator
from app.production import MaterialAsset, ProductionProfile, ProductionRepository, VoiceProfile
from app.voice_clone import VoiceCloneResult
from app.local_members import LocalMemberService
from _media_preview_test_fixtures import synthetic_wav_bytes


def _repository(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "library"
    library.mkdir()
    repository = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3", video_library_path=library,
        duration_reader=lambda _path: 10.0, scene_reader=lambda _path, _duration: [],
    )
    members = LocalMemberService(repository.database_path)
    owner, _token = members.whoami(None)
    repository.fixture_owner_id = owner.member_id
    approved = library / "approved"
    approved.mkdir()
    for name in ("voice-a.wav", "legacy.wav"):
        with wave.open(str(approved / name), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000)
        path = approved / name
        repository._save_asset(MaterialAsset(id="voice-a-source" if name == "voice-a.wav" else "legacy-source",
            workspace_id="workspace_a", file_name=name, path=str(path), size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime), media_type="audio", duration_seconds=1,
            owner_user_id=owner.member_id))
        with repository._connect() as conn:
            repository._claim_workspace_media_reference(conn, workspace_id="workspace_a", reference=f"workspace://approved/{name}")
    avatar = approved / "avatar.png"
    Image.new("RGB", (2, 2), (100, 100, 100)).save(avatar)
    repository._save_asset(MaterialAsset(id="avatar", workspace_id="workspace_a", file_name=avatar.name,
        path=str(avatar), size_bytes=avatar.stat().st_size, modified_at=datetime.fromtimestamp(avatar.stat().st_mtime),
        media_type="image", owner_user_id=owner.member_id))
    return repository


def _authorization(
    workspace_id: str,
    content_type: str,
    scope: str | tuple[str, ...],
) -> Authorization:
    return Authorization(
        id=f"{content_type}-authorization",
        version="v1",
        rights_holder="权利人",
        content_types=(content_type,),
        usage_scopes=(scope,) if isinstance(scope, str) else scope,
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        evidence_reference="consent://record/1",
        status="active",
        workspace_id=workspace_id,
    )


def _media_profiles(workspace_id: str, owner_user_id: str | None = None) -> tuple[PersonProfile, VersionedVoiceProfile]:
    person = PersonProfile(
        id="person_a",
        workspace_id=workspace_id,
        display_name="主播 A",
        version="person-v1",
        avatar_reference="workspace://approved/avatar.png",
        background_capabilities=("studio",),
        shot_capabilities=("medium",),
        authorization=_authorization(workspace_id, "person_likeness", "avatar_lipsync"),
        reference=ProfileReference(asset_id="avatar"),
        owner_user_id=owner_user_id,
    )
    voice = VersionedVoiceProfile(
        id="voice_a",
        workspace_id=workspace_id,
        person_profile_id=person.id,
        person_version=person.version,
        version="voice-v1",
        language="zh-en",
        reference_audio_path="workspace://approved/voice-a.wav",
        reference_text="主播 A 的声音参考文字。",
        model_id="qwen3-tts-v1",
        authorization=_authorization(workspace_id, "voice", ("narration", "avatar_lipsync")),
        reference=ProfileReference(asset_id="voice-a-source"), display_name="主播声音",
        owner_user_id=owner_user_id,
    )
    return person, voice


def _episode(workspace_id: str) -> Episode:
    return Episode(
        id="episode_a",
        day_number=1,
        episode_date=date(2026, 8, 27),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id=workspace_id,
        script=ScriptPackage(
            id="script_a",
            hook="开头。",
            sections=[ScriptSection("body", "正文", 8, "Codex 处理中文 English 文案。")],
            title_options=[],
            cover_copy="",
            publish_copy="",
            comment_prompt="",
        ),
    )


def test_versioned_media_profiles_survive_restart_and_resolve_task_inputs(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)
    repository.profile_records.save_pronunciation_entry(
        PronunciationEntry(
            id="workspace-term",
            workspace_id="workspace_a",
            term="Codex",
            language="en",
            target_pronunciation="KO-deks",
            scope="workspace",
            scope_reference=None,
            version="v1",
        )
    )
    repository.profile_records.save_pronunciation_entry(
        PronunciationEntry(
            id="person-term",
            workspace_id="workspace_a",
            term="API",
            language="en",
            target_pronunciation="A-P-I",
            scope="person",
            scope_reference=person.id,
            version="v1",
        )
    )
    repository.profile_records.save_pronunciation_entry(
        PronunciationEntry(
            id="task-term",
            workspace_id="workspace_a",
            term="AI 视频日记",
            language="zh",
            target_pronunciation="AI shi pin ri ji",
            scope="task",
            scope_reference="episode_a",
            version="v1",
        )
    )

    reloaded = ProductionRepository(
        database_path=repository.database_path,
        video_library_path=repository.video_library_path,
        duration_reader=lambda _path: 10.0,
        scene_reader=lambda _path, _duration: [],
    )
    reloaded.fixture_owner_id = repository.fixture_owner_id
    inputs = reloaded.profile_inputs.resolve_narration_inputs(
        workspace_id="workspace_a",
        episode_id="episode_a",
        voice_profile_id=voice.id,
        voice_version=voice.version,
    )

    assert inputs.voice == voice
    assert {entry.id for entry in inputs.pronunciation_entries} == {
        "workspace-term",
        "person-term",
        "task-term",
    }
    assert reloaded.profile_records.get_person_profile("workspace_a", person.id, person.version) == person
    assert reloaded.profile_records.get_pronunciation_entry("workspace_a", "task-term", "v1").term == "AI 视频日记"


def test_versioned_media_profiles_are_workspace_scoped(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)

    with pytest.raises(KeyError):
        repository.profile_records.get_person_profile("workspace_b", person.id, person.version)
    with pytest.raises(KeyError):
        repository.profile_records.get_versioned_voice_profile("workspace_b", voice.id, voice.version)
    with pytest.raises(KeyError):
        repository.get_authorization(
            "workspace_b", person.authorization.id, person.authorization.version
        )
    with pytest.raises(KeyError):
        repository.save_versioned_voice_profile(
            replace(
                voice,
                workspace_id="workspace_b",
                authorization=replace(voice.authorization, workspace_id="workspace_b"),
            ),
            verified_owner_user_id=repository.fixture_owner_id,
        )


def test_legacy_voice_profiles_remain_readable_and_migrate_only_explicitly(tmp_path):
    repository = _repository(tmp_path)
    legacy = repository.save_voice_profile(
        VoiceProfile(
            id="legacy_voice",
            workspace_id="workspace_a",
            name="旧声音",
            reference_audio_path="workspace://approved/legacy.wav",
            reference_text="旧声音参考文字",
            consent_confirmed=True,
        )
    )
    person, _voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)

    assert repository.get_voice_profile("workspace_a", legacy.id) == legacy
    with pytest.raises(KeyError):
        repository.profile_records.get_versioned_voice_profile("workspace_a", legacy.id)

    migrated = repository.migrate_legacy_voice_profile(
        workspace_id="workspace_a",
        legacy_voice_profile_id=legacy.id,
        person_profile_id=person.id,
        person_version=person.version,
        version="legacy-v1",
        language="zh",
        authorization=_authorization("workspace_a", "voice", "narration"),
        verified_owner_user_id=repository.fixture_owner_id)

    assert repository.get_voice_profile("workspace_a", legacy.id) == legacy
    assert repository.profile_records.get_versioned_voice_profile("workspace_a", legacy.id, "legacy-v1") == migrated

    raw_legacy = repository.save_voice_profile(
        VoiceProfile(
            id="raw_legacy_voice",
            workspace_id="workspace_a",
            name="原始路径旧声音",
            reference_audio_path="/approved/raw-legacy.wav",
            reference_text="旧声音参考文字",
            consent_confirmed=True,
        )
    )
    with pytest.raises(ValueError, match="workspace://"):
        repository.migrate_legacy_voice_profile(
            workspace_id="workspace_a",
            legacy_voice_profile_id=raw_legacy.id,
            person_profile_id=person.id,
            person_version=person.version,
            version="raw-legacy-v1",
            language="zh",
            authorization=_authorization("workspace_a", "voice", "narration"),
            verified_owner_user_id=repository.fixture_owner_id)


def test_version_changes_preserve_history_and_invalidate_narration_identity(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)
    with pytest.raises(ValueError, match="新版本"):
        repository.save_versioned_voice_profile(
            replace(voice, model_id="qwen3-tts-v2"),
            verified_owner_user_id=repository.fixture_owner_id,
        )

    avatar_v2 = repository.video_library_path / "approved" / "avatar-v2.png"
    Image.new("RGB", (2, 2), (150, 150, 150)).save(avatar_v2)
    repository._save_asset(MaterialAsset(id="avatar-v2", workspace_id="workspace_a", file_name=avatar_v2.name,
        path=str(avatar_v2), size_bytes=avatar_v2.stat().st_size,
        modified_at=datetime.fromtimestamp(avatar_v2.stat().st_mtime), media_type="image",
        owner_user_id=repository.fixture_owner_id))
    person_v2 = replace(person, version="person-v2", avatar_reference="workspace://approved/avatar-v2.png",
                        reference=ProfileReference(asset_id="avatar-v2"))
    person_v2 = replace(person_v2, owner_user_id=repository.fixture_owner_id)
    repository.save_person_profile(person_v2, verified_owner_user_id=repository.fixture_owner_id)
    voice_v2 = replace(
        voice,
        version="voice-v2",
        person_version=person_v2.version,
        model_id="qwen3-tts-v2",
    )
    repository.save_versioned_voice_profile(voice_v2, verified_owner_user_id=repository.fixture_owner_id)
    pronunciation_v1 = PronunciationEntry(
        id="term",
        workspace_id="workspace_a",
        term="Codex",
        language="en",
        target_pronunciation="KO-deks",
        scope="workspace",
        scope_reference=None,
        version="v1",
    )
    pronunciation_v2 = replace(pronunciation_v1, version="v2", target_pronunciation="KOH-deks")
    repository.profile_records.save_pronunciation_entry(pronunciation_v1)
    repository.profile_records.save_pronunciation_entry(pronunciation_v2)

    class Provider:
        model_id = "runtime-v1"
        ready = True
        message = "ready"

        def generate(self, **_kwargs):
            raise AssertionError("cache-key test must not synthesize")

    generator = NarrationGenerator(tmp_path / "outputs", Provider())
    episode = _episode("workspace_a")
    profile = ProductionProfile(workspace_id="workspace_a")
    old_inputs = repository.profile_inputs.resolve_narration_inputs(
        workspace_id="workspace_a",
        episode_id=episode.id,
        voice_profile_id=voice.id,
        voice_version=voice.version,
    )
    new_inputs = repository.profile_inputs.resolve_narration_inputs(
        workspace_id="workspace_a",
        episode_id=episode.id,
        voice_profile_id=voice_v2.id,
        voice_version=voice_v2.version,
    )

    assert generator.cache_key(
        episode=episode,
        profile=profile,
        voice_identity=old_inputs.voice,
        pronunciation_entries=old_inputs.pronunciation_entries,
    ) != generator.cache_key(
        episode=episode,
        profile=profile,
        voice_identity=new_inputs.voice,
        pronunciation_entries=new_inputs.pronunciation_entries,
    )
    assert repository.profile_records.get_versioned_voice_profile("workspace_a", voice.id, voice.version) == voice
    reloaded = ProductionRepository(
        database_path=repository.database_path,
        video_library_path=repository.video_library_path,
        duration_reader=lambda _path: 10.0,
        scene_reader=lambda _path, _duration: [],
    )
    reloaded.fixture_owner_id = repository.fixture_owner_id
    assert reloaded.profile_records.list_person_profiles("workspace_a") == [person_v2]
    assert reloaded.profile_records.list_person_profile_versions("workspace_a", person.id) == [person_v2, person]
    assert reloaded.profile_records.list_versioned_voice_profiles("workspace_a") == [voice_v2]
    assert reloaded.profile_records.list_versioned_voice_profile_versions("workspace_a", voice.id) == [voice_v2, voice]
    assert reloaded.list_authorizations("workspace_a") == [person.authorization, voice.authorization]
    assert reloaded.list_authorization_versions(
        "workspace_a", voice.authorization.id
    ) == [voice.authorization]
    assert reloaded.profile_records.list_pronunciation_entries("workspace_a") == [pronunciation_v2]
    assert reloaded.profile_records.list_pronunciation_entry_versions("workspace_a", "term") == [
        pronunciation_v2,
        pronunciation_v1,
    ]


def test_revoked_authorization_changes_cache_identity_and_blocks_persisted_narration(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)
    episode = _episode("workspace_a")
    profile = ProductionProfile(workspace_id="workspace_a")

    class Provider:
        model_id = "runtime-v1"
        ready = True
        message = "ready"

        def generate(self, **_kwargs):
            raise AssertionError("revoked authorization must stop before synthesis")

    generator = NarrationGenerator(tmp_path / "outputs", Provider())
    before_revocation = repository.profile_inputs.resolve_narration_inputs(
        workspace_id="workspace_a",
        episode_id=episode.id,
        voice_profile_id=voice.id,
        voice_version=voice.version,
    )
    active_cache_key = generator.cache_key(
        episode=episode,
        profile=profile,
        voice_identity=before_revocation.voice,
    )
    revoked_authorization = replace(
        voice.authorization,
        version="v2",
        status="revoked",
    )
    repository.save_authorization(revoked_authorization)
    revoked_inputs = repository.profile_inputs.resolve_narration_inputs(
        workspace_id="workspace_a",
        episode_id=episode.id,
        voice_profile_id=voice.id,
        voice_version=voice.version,
    )

    assert repository.get_authorization(
        "workspace_a", voice.authorization.id, "v1"
    ) == voice.authorization
    assert revoked_inputs.voice.version == voice.version
    assert revoked_inputs.voice.authorization == revoked_authorization
    assert active_cache_key != generator.cache_key(
        episode=episode,
        profile=profile,
        voice_identity=revoked_inputs.voice,
    )
    with pytest.raises(ValueError, match="authorization_revoked"):
        generator.generate_from_repository(
            repository=repository,
            workspace_id="workspace_a",
            episode=episode,
            profile=profile,
            voice_profile_id=voice.id,
            voice_version=voice.version,
            viewer_user_id=repository.fixture_owner_id,
        )


def test_revoked_authorization_blocks_explicit_historical_avatar_request(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)
    avatar_authorization = _authorization("workspace_a", "avatar_video", "avatar_lipsync")
    repository.save_authorization(avatar_authorization)
    audio = tmp_path / "narration.wav"
    audio.write_bytes(synthetic_wav_bytes())

    request_before_revocation = repository.profile_inputs.resolve_avatar_lipsync_request(
        workspace_id="workspace_a",
        person_id=person.id,
        person_version=person.version,
        voice_profile_id=voice.id,
        voice_version=voice.version,
        audio_path=str(audio),
        audio_cache_key="narration-cache-v1",
        language="zh-en",
        motion="natural",
        expression="neutral",
        authorization_id=avatar_authorization.id,
        authorization_version=avatar_authorization.version,
        model_id="avatar-model-v1",
        viewer_user_id=repository.fixture_owner_id,
    )
    repository.save_authorization(
        replace(person.authorization, version="v2", status="revoked")
    )
    repository.save_authorization(
        replace(voice.authorization, version="v2", status="revoked")
    )
    repository.save_authorization(
        replace(avatar_authorization, version="v2", status="revoked")
    )

    with pytest.raises(ValueError, match="authorization_revoked"):
        repository.profile_inputs.resolve_avatar_lipsync_request(
            workspace_id="workspace_a",
            person_id=person.id,
            person_version=person.version,
            voice_profile_id=voice.id,
            voice_version=voice.version,
            audio_path=str(audio),
            audio_cache_key="narration-cache-v1",
            language="zh-en",
            motion="natural",
            expression="neutral",
            authorization_id=avatar_authorization.id,
            authorization_version=avatar_authorization.version,
            model_id="avatar-model-v1",
            viewer_user_id=repository.fixture_owner_id,
        )
    request_after_revocation = replace(request_before_revocation,
        person=replace(person, authorization=repository.get_authorization("workspace_a", person.authorization.id)),
        voice=replace(voice, authorization=repository.get_authorization("workspace_a", voice.authorization.id)),
        authorization=repository.get_authorization("workspace_a", avatar_authorization.id))
    result = AvatarAndLipSyncService(
        tmp_path / "outputs", UnavailableAvatarAndLipSyncProvider()
    ).render(request_after_revocation)

    assert repository.profile_records.get_person_profile("workspace_a", person.id, person.version) == person
    assert request_after_revocation.person.version == person.version
    assert request_after_revocation.person.authorization.status == "revoked"
    assert request_after_revocation.voice.version == voice.version
    assert request_after_revocation.voice.authorization.status == "revoked"
    assert request_after_revocation.authorization.status == "revoked"
    assert request_before_revocation != request_after_revocation
    assert result.status == "authorization_denied"
    assert result.failure_code == "authorization_revoked"


def test_narration_generator_resolves_persisted_voice_language_and_dictionary(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    reference = repository.video_library_path / "approved" / "voice-a.wav"
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)
    repository.profile_records.save_pronunciation_entry(
        PronunciationEntry(
            id="term",
            workspace_id="workspace_a",
            term="Codex",
            language="en",
            target_pronunciation="KO-deks",
            scope="person",
            scope_reference=person.id,
            version="v1",
        )
    )
    captured: dict[str, object] = {}

    class Provider:
        model_id = "runtime-v1"
        ready = True
        message = "ready"
        supported_languages = ("zh", "en", "zh-en")
        supports_pronunciation_dictionary = True

        def generate(
            self,
            *,
            output_path,
            language,
            pronunciation_dictionary,
            reference_audio_path,
            **_kwargs,
        ):
            captured["language"] = language
            captured["dictionary"] = pronunciation_dictionary
            captured["reference_audio_path"] = reference_audio_path
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(synthetic_wav_bytes())
            return VoiceCloneResult(str(output), 8.0, 24000)

    artifact = NarrationGenerator(tmp_path / "outputs", Provider()).generate_from_repository(
        repository=repository,
        workspace_id="workspace_a",
        episode=_episode("workspace_a"),
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_profile_id=voice.id,
        voice_version=voice.version,
        viewer_user_id=repository.fixture_owner_id,
    )

    assert artifact.language == "zh-en"
    assert captured["reference_audio_path"] == reference.resolve()
    cache_inputs = repository.profile_inputs.resolve_narration_inputs(
        workspace_id="workspace_a", episode_id="episode_a",
        voice_profile_id=voice.id, voice_version=voice.version,
    )
    reference_input_identity = repository.profile_reference_input(
        cache_inputs.voice, viewer_user_id=repository.fixture_owner_id
    ).identity
    expected_cache_key = NarrationGenerator(
        tmp_path / "other-outputs", Provider()
    ).cache_key(
        episode=_episode("workspace_a"),
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=voice,
        pronunciation_entries=repository.profile_inputs.resolve_narration_inputs(
            workspace_id="workspace_a",
            episode_id="episode_a",
            voice_profile_id=voice.id,
            voice_version=voice.version,
        ).pronunciation_entries,
    )
    expected_cache_key = hashlib.sha256(
        json.dumps([expected_cache_key, reference_input_identity]).encode()
    ).hexdigest()
    assert artifact.cache_key == expected_cache_key
    assert captured["language"] == "zh-en"
    assert captured["dictionary"] == (
        {
            "id": "term",
            "version": "v1",
            "term": "Codex",
            "language": "en",
            "target_pronunciation": "KO-deks",
            "scope": "person",
            "scope_reference": "person_a",
        },
    )


@pytest.mark.parametrize(
    "reference_kind",
    (
        "traversal",
        "missing",
        "outside_absolute",
    ),
)
def test_persisted_narration_reference_fails_before_provider(tmp_path, reference_kind):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    invalid_voice = replace(
        voice,
        version="voice-invalid-reference",
        reference_audio_path={
            "traversal": "workspace://approved/%2e%2e/outside.wav",
            "missing": "workspace://approved/missing.wav",
            "outside_absolute": str(tmp_path / "outside.wav"),
        }[reference_kind],
    )
    calls = 0

    class Provider:
        model_id = "runtime-v1"
        ready = True
        message = "ready"

        def generate(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("invalid reference must stop before synthesis")

    if reference_kind != "missing":
        with pytest.raises(ValueError, match="媒体引用|workspace://"):
            repository.save_versioned_voice_profile(invalid_voice, verified_owner_user_id=repository.fixture_owner_id)
        assert calls == 0
        return

    invalid_voice = voice
    repository.save_versioned_voice_profile(invalid_voice, verified_owner_user_id=repository.fixture_owner_id)
    repository.resolve_workspace_media_reference("workspace_a", invalid_voice.reference_audio_path).unlink()
    with pytest.raises(ValueError, match="媒体引用|参考文件|workspace://"):
        NarrationGenerator(tmp_path / "outputs", Provider()).generate_from_repository(
            repository=repository,
            workspace_id="workspace_a",
            episode=_episode("workspace_a"),
            profile=ProductionProfile(workspace_id="workspace_a"),
            voice_profile_id=invalid_voice.id,
            voice_version=invalid_voice.version,
            viewer_user_id=repository.fixture_owner_id,
        )

    assert calls == 0


def test_avatar_request_is_resolved_from_workspace_scoped_versions(tmp_path):
    repository = _repository(tmp_path)
    person, voice = _media_profiles("workspace_a", repository.fixture_owner_id)
    repository.save_person_profile(person, verified_owner_user_id=repository.fixture_owner_id)
    repository.save_versioned_voice_profile(voice, verified_owner_user_id=repository.fixture_owner_id)
    avatar_authorization = _authorization("workspace_a", "avatar_video", "avatar_lipsync")
    repository.save_authorization(avatar_authorization)
    audio = tmp_path / "narration.wav"
    audio.write_bytes(synthetic_wav_bytes())

    result = AvatarAndLipSyncService(
        tmp_path / "outputs", UnavailableAvatarAndLipSyncProvider()
    ).render_from_repository(
        repository=repository,
        workspace_id="workspace_a",
        person_id=person.id,
        person_version=person.version,
        voice_profile_id=voice.id,
        voice_version=voice.version,
        audio_path=str(audio),
        audio_cache_key="narration-cache-v1",
        language="zh-en",
        motion="natural",
        expression="neutral",
        authorization_id=avatar_authorization.id,
        authorization_version=avatar_authorization.version,
        model_id="avatar-model-v1",
        viewer_user_id=repository.fixture_owner_id,
    )

    assert result.status == "unavailable"
    with pytest.raises(KeyError):
        repository.profile_inputs.resolve_avatar_lipsync_request(
            workspace_id="workspace_b",
            person_id=person.id,
            person_version=person.version,
            voice_profile_id=voice.id,
            voice_version=voice.version,
            audio_path=str(audio),
            audio_cache_key="narration-cache-v1",
            language="zh-en",
            motion="natural",
            expression="neutral",
            authorization_id=avatar_authorization.id,
            authorization_version=avatar_authorization.version,
            model_id="avatar-model-v1",
        )
