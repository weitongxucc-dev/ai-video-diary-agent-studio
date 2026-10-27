from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncResult,
    AvatarPreviewService,
    PersonProfile,
    PronunciationEntry,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile,
)
from app.narration import VoicePreviewService
from app.production import ProductionRepository
from app.voice_clone import UnavailableVoiceCloneProvider, VoiceCloneResult
from app.local_members import LocalMemberService
from _media_preview_test_fixtures import (
    PreviewRuntimeHarness,
    build_preview_runtime,
    register_synthetic_profiles,
    synthetic_wav_bytes,
)

_ACTIVE_HARNESSES: list[PreviewRuntimeHarness] = []


@pytest.fixture(autouse=True)
def _release_test_preview_runtimes():
    _ACTIVE_HARNESSES.clear()
    yield
    while _ACTIVE_HARNESSES:
        assert _ACTIVE_HARNESSES.pop().close()


def _repository(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "library"
    library.mkdir()
    return ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 10.0,
        scene_reader=lambda _path, _duration: [],
    )


def _profiles(
    repository: ProductionRepository,
    workspace_id: str = "workspace_a",
) -> tuple[PersonProfile, VoiceProfile, Path]:
    members = LocalMemberService(repository.database_path.with_name("preview-members.sqlite3"))
    member, token = members.whoami(None)
    person, voice, avatar_authorization, reference, _image = register_synthetic_profiles(
        repository, workspace_id=workspace_id, member_id=member.member_id,
    )
    repository._preview_member_id = member.member_id
    repository._preview_member_token = token
    repository._preview_members = members
    repository._preview_avatar_authorization = avatar_authorization
    return person, voice, reference


def _save_profiles(repository: ProductionRepository, person: PersonProfile, voice: VoiceProfile) -> Authorization:
    voice_provider = FixtureVoiceProvider()
    avatar_provider = FixtureAvatarProvider()
    voice_service = VoicePreviewService(repository.video_library_path.parent / "outputs", voice_provider)
    avatar_service = AvatarPreviewService(repository.video_library_path.parent / "outputs", avatar_provider)
    repository._preview_voice_provider = voice_provider
    repository._preview_avatar_provider = avatar_provider
    repository._preview_voice_service = voice_service
    repository._preview_avatar_service = avatar_service
    repository._preview_harness = build_preview_runtime(
        repository, output_root=repository.video_library_path.parent / "outputs",
        voice_service=voice_service, avatar_service=avatar_service,
        member_id=repository._preview_member_id, workspace_id=person.workspace_id,
    )
    _ACTIVE_HARNESSES.append(repository._preview_harness)
    return repository._preview_avatar_authorization


def _voice_request(
    repository, *, preview_text="预览", retry=False, provider=None, complete=True,
    voice_profile_id="voice_a", voice_version="voice-v1",
):
    harness = repository._preview_harness
    service = repository._preview_voice_service
    if provider is not None:
        service.provider = provider
    params = {
        "voice_profile_id": voice_profile_id, "voice_version": voice_version,
        "preview_text": preview_text, "retry": retry,
    }
    queued = harness.prepare(service, params)
    if complete and queued.status == "queued" and harness.complete_once():
        return repository.preview_records.get_media_preview_run(harness.actor.workspace_id, "voice", queued.identity)
    return queued


def _avatar_request(repository, *, audio, provider=None, retry=False, complete=True, audio_path=None):
    harness = repository._preview_harness
    service = repository._preview_avatar_service
    if provider is not None:
        service.provider = provider
    person = repository.get_person_profile(harness.actor.workspace_id, "person_a")
    voice = repository.get_versioned_voice_profile(harness.actor.workspace_id, "voice_a", "voice-v1")
    params = {
        "person_id": person.id, "person_version": person.version,
        "voice_profile_id": voice.id, "voice_version": voice.version,
        "audio_path": str(audio_path or audio.output_path), "audio_cache_key": audio.identity,
        "language": audio.language, "motion": "natural", "expression": "neutral",
        "authorization_id": repository._preview_avatar_authorization.id,
        "authorization_version": repository._preview_avatar_authorization.version,
        "model_id": service.provider.model_id, "retry": retry,
    }
    queued = harness.prepare(service, params)
    if complete and queued.status == "queued" and harness.complete_once():
        return repository.preview_records.get_media_preview_run(harness.actor.workspace_id, "avatar_lipsync", queued.identity)
    return queued


def _close_runtime(repository: ProductionRepository) -> None:
    harness = getattr(repository, "_preview_harness", None)
    if harness is not None:
        assert harness.close()


class FixtureVoiceProvider:
    model_id = "fixture-voice-runtime-v1"
    supported_languages = ("zh-en",)
    supports_pronunciation_dictionary = True
    ready = True
    message = "fixture ready"

    def __init__(self, *, output_mode: str = "expected") -> None:
        self.output_mode = output_mode
        self.calls = 0
        self.references: list[str] = []

    def generate_granted(self, *, grant_context, **kwargs):
        grant_context.check()
        return self.generate(grant_context=grant_context, **kwargs)

    def generate(self, *, output_path, reference_audio_path, **_kwargs):
        self.calls += 1
        self.references.append(str(reference_audio_path))
        grant = _kwargs.get("grant_context")
        assert grant is not None and grant.grant.owner_type == "media_preview"
        assert grant.grant.unit_key.startswith("voice:")
        output = Path(output_path)
        if self.output_mode == "outside":
            output = output.parent.parent.parent.parent / "outside.wav"
        if self.output_mode != "missing":
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(synthetic_wav_bytes())
        return VoiceCloneResult(str(output), 1.0, 24000)


class FixtureAvatarProvider:
    model_id = "avatar-model-v1"
    supported_languages = ("zh-en",)
    ready = True
    message = "fixture ready"

    def __init__(self, *, output_mode: str = "expected") -> None:
        self.output_mode = output_mode
        self.calls = 0

    def render(self, *, request, output_path, grant_context=None):
        self.calls += 1
        assert grant_context is not None and grant_context.grant.owner_type == "media_preview"
        assert grant_context.grant.unit_key.startswith("avatar_lipsync:")
        output = Path(output_path)
        if self.output_mode == "outside":
            output = output.parent.parent.parent.parent / "outside.mp4"
        if self.output_mode != "missing":
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\x00\x00\x00\x18ftypisomfixture")
        return AvatarAndLipSyncResult(
            status="succeeded",
            cache_key="fixture-provider-result",
            artifact_path=str(output),
            duration_seconds=1.0,
            alignment_evidence="fixture alignment only",
            failure_code=None,
            failure_impact=None,
            remediation_action=None,
        )


def test_voice_preview_persists_idempotent_fixture_run_and_workspace_scope(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    provider = repository._preview_voice_provider
    first = _voice_request(repository, preview_text="这是 fixture 预览，不代表真实模型效果。")
    second = _voice_request(repository, preview_text="这是 fixture 预览，不代表真实模型效果。")

    assert first.status == "succeeded"
    assert first.attempt == 1
    assert first.device_request_id
    assert second == first
    assert provider.calls == 1
    assert provider.references == [str(_reference.resolve())]
    assert first.output_path and Path(first.output_path).is_file()
    assert repository.preview_records.get_media_preview_run("workspace_a", "voice", first.identity) == first
    assert repository.preview_records.get_media_preview_run("workspace_b", "voice", first.identity) is None
    assert repository.preview_records.list_media_preview_runs("workspace_a", "voice") == [first]
    assert repository.preview_records.list_media_preview_runs("workspace_b") == []
    reloaded = ProductionRepository(
        database_path=repository.database_path,
        video_library_path=repository.video_library_path,
        duration_reader=lambda _path: 10.0,
        scene_reader=lambda _path, _duration: [],
    )
    assert reloaded.preview_records.get_media_preview_run("workspace_a", "voice", first.identity) == first


def test_voice_preview_failure_requires_explicit_retry_and_preserves_attempt_history(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    provider = FixtureVoiceProvider(output_mode="missing")
    failed = _voice_request(repository, preview_text="重试预览", provider=provider)
    unchanged = _voice_request(repository, preview_text="重试预览", provider=provider)
    provider.output_mode = "expected"
    retried = _voice_request(repository, preview_text="重试预览", retry=True, provider=provider)

    assert (failed.status, failed.attempt) == ("failed", 1)
    assert unchanged == failed
    assert (retried.status, retried.attempt) == ("succeeded", 2)
    assert provider.calls == 2
    assert repository.preview_records.get_media_preview_run("workspace_a", "voice", failed.identity) == retried
    assert repository.preview_records.list_media_preview_attempts("workspace_a", "voice", failed.identity) == [
        retried,
        failed,
    ]


def test_running_voice_preview_is_returned_without_a_second_provider_call(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    provider = repository._preview_voice_provider
    pending = _voice_request(repository, preview_text="进行中预览", complete=False)
    duplicate = _voice_request(repository, preview_text="进行中预览", complete=False)

    assert pending.status == "queued"
    assert duplicate.id == pending.id and duplicate.device_request_id == pending.device_request_id
    assert provider.calls == 0
    assert repository._preview_harness.complete_once()
    assert repository.preview_records.get_media_preview_run("workspace_a", "voice", pending.identity).status == "succeeded"


def test_unknown_device_admission_keeps_preview_queued_until_granted(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    harness = repository._preview_harness
    provider = repository._preview_voice_provider
    harness.resources.admitted = None

    queued = _voice_request(repository, preview_text="等待设备资源", complete=False)

    assert queued.status == "queued" and queued.device_request_id
    assert harness.complete_once() is False
    assert harness.runtime.scheduler.view_for(queued.device_request_id, harness.actor)["wait_reason"] == "resource_unknown"
    assert provider.calls == 0
    harness.resources.admitted = True
    assert harness.complete_once()
    final = repository.preview_records.get_media_preview_run("workspace_a", "voice", queued.identity)
    assert final.status == "succeeded" and provider.calls == 1
    with repository._connect() as connection:
        permit = connection.execute("SELECT state, request_id FROM device_permit WHERE id=1").fetchone()
    assert (permit["state"], permit["request_id"]) == ("free", None)


def test_voice_preview_without_device_grant_does_not_call_provider(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    provider = repository._preview_voice_provider
    result = repository._preview_voice_service.request(
        repository=repository, workspace_id="workspace_a",
        viewer_user_id=repository._preview_member_id,
        voice_profile_id=voice.id, voice_version=voice.version,
        preview_text="没有设备许可不能运行",
    )

    assert (result.status, result.reason) == ("blocked", "device_grant_required")
    assert provider.calls == 0


def test_concurrent_first_voice_preview_claims_provider_once(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)

    class Provider(FixtureVoiceProvider):
        def __init__(self) -> None:
            super().__init__()
            self._calls_lock = threading.Lock()

        def generate(self, *, output_path, reference_audio_path, **kwargs):
            with self._calls_lock:
                self.calls += 1
            self.references.append(str(reference_audio_path))
            grant = kwargs.get("grant_context")
            assert grant is not None and grant.grant.owner_type == "media_preview"
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(synthetic_wav_bytes())
            return VoiceCloneResult(str(output), 1.0, 24000)

    provider = Provider()
    results: list[object] = []
    errors: list[BaseException] = []
    second_done = threading.Event()

    def request(*, signal_done: bool = False) -> None:
        try:
            results.append(_voice_request(repository, preview_text="并发预览", provider=provider, complete=False))
        except BaseException as exc:
            errors.append(exc)
        finally:
            if signal_done:
                second_done.set()

    first = threading.Thread(target=request)
    first.start()
    second = threading.Thread(target=request, kwargs={"signal_done": True})
    second.start()
    assert second_done.wait(timeout=3)
    first.join(timeout=3)
    second.join(timeout=3)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert provider.calls == 0
    assert len({record.device_request_id for record in results}) == 1
    assert repository._preview_harness.complete_once()
    final = repository.preview_records.get_media_preview_run("workspace_a", "voice", results[0].identity)
    assert final.status == "succeeded"
    assert provider.calls == 1
    assert sorted(record.status for record in results) == ["queued", "queued"]


def test_concurrent_retry_claims_exactly_one_next_voice_attempt(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)

    class Provider(FixtureVoiceProvider):
        def __init__(self) -> None:
            super().__init__(output_mode="missing")
            self._calls_lock = threading.Lock()

        def generate(self, *, output_path, reference_audio_path, **kwargs):
            with self._calls_lock:
                self.calls += 1
            self.references.append(str(reference_audio_path))
            grant = kwargs.get("grant_context")
            assert grant is not None and grant.grant.owner_type == "media_preview"
            if self.output_mode == "missing":
                return VoiceCloneResult(str(output_path), 1.0, 24000)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(synthetic_wav_bytes())
            return VoiceCloneResult(str(output), 1.0, 24000)

    provider = Provider()
    failed = _voice_request(repository, preview_text="并发重试", provider=provider)
    provider.output_mode = "expected"
    results: list[object] = []
    errors: list[BaseException] = []
    second_done = threading.Event()

    def retry(*, signal_done: bool = False) -> None:
        try:
            results.append(_voice_request(repository, preview_text="并发重试", provider=provider, retry=True, complete=False))
        except BaseException as exc:
            errors.append(exc)
        finally:
            if signal_done:
                second_done.set()

    first = threading.Thread(target=retry)
    first.start()
    second = threading.Thread(target=retry, kwargs={"signal_done": True})
    second.start()
    assert second_done.wait(timeout=3)
    first.join(timeout=3)
    second.join(timeout=3)
    assert provider.calls == 1
    assert len({record.device_request_id for record in results}) == 1
    assert repository._preview_harness.complete_once()
    final = repository.preview_records.get_media_preview_run("workspace_a", "voice", failed.identity)
    assert final.status == "succeeded"

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert provider.calls == 2
    assert sorted(record.status for record in results) == ["queued", "queued"]
    attempts = repository.preview_records.list_media_preview_attempts("workspace_a", "voice", failed.identity)
    assert [(record.attempt, record.status) for record in attempts] == [
        (2, "succeeded"),
        (1, "failed"),
    ]


def test_media_preview_attempt_migrates_existing_latest_record(tmp_path, monkeypatch):
    from test_profile_api_contract import preview_record_guards
    preview_record_guards(monkeypatch)
    database_path = tmp_path / "legacy.sqlite3"
    started_at = datetime(2026, 8, 28, tzinfo=timezone.utc)
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE media_preview_runs (
                id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                preview_type TEXT NOT NULL,
                identity TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id, preview_type, identity)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO media_preview_runs
                (id, workspace_id, preview_type, identity, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "workspace_a",
                "voice",
                "legacy-identity",
                json.dumps(
                    {
                        "id": "legacy-run",
                        "workspace_id": "workspace_a",
                        "preview_type": "voice",
                        "identity": "legacy-identity",
                        "status": "failed",
                        "capability_id": "voice_clone",
                        "model_id": "legacy-model",
                        "language": "zh",
                        "person_profile_id": "person_a",
                        "person_version": "person-v1",
                        "voice_profile_id": "voice_a",
                        "voice_version": "voice-v1",
                        "authorization_id": "voice-authorization",
                        "authorization_version": "v1",
                        "output_path": None,
                        "reason": "provider_failed",
                        "impact": "失败",
                        "repair_action": "修复后重试",
                        "started_at": started_at.isoformat(),
                        "finished_at": started_at.isoformat(),
                    }
                ),
                started_at.isoformat(),
            ),
        )
    library = tmp_path / "library"
    library.mkdir()

    repository = ProductionRepository(database_path=database_path, video_library_path=library)

    migrated = repository.preview_records.get_media_preview_run("workspace_a", "voice", "legacy-identity")
    assert migrated is not None
    assert (migrated.id, migrated.attempt, migrated.status) == ("legacy-run", 1, "failed")


def test_voice_preview_persists_unavailable_and_revoked_historical_versions(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    missing_provider = _voice_request(repository, provider=UnavailableVoiceCloneProvider())
    assert (missing_provider.status, missing_provider.reason) == ("unavailable", "capability_unavailable")

    active = _voice_request(repository, provider=FixtureVoiceProvider())
    repository.save_authorization(replace(voice.authorization, version="v2", status="revoked"))
    revoked = _voice_request(repository, provider=FixtureVoiceProvider())

    assert active.identity != revoked.identity
    assert (revoked.status, revoked.reason) == ("blocked", "authorization_revoked")
    assert repository.get_versioned_voice_profile("workspace_a", voice.id, voice.version) == voice


def test_voice_preview_preflights_language_and_pronunciation_before_provider(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)

    unsupported_language = replace(voice, version="voice-en", language="en")
    repository.save_versioned_voice_profile(
        unsupported_language, verified_owner_user_id=repository._preview_member_id
    )
    language_provider = FixtureVoiceProvider()
    language_result = _voice_request(
        repository, provider=language_provider, preview_text="English preview",
        voice_version=unsupported_language.version,
    )
    assert (language_result.status, language_result.reason, language_provider.calls) == (
        "unavailable",
        "language_unavailable",
        0,
    )
    assert language_result.impact and language_result.repair_action

    repository.save_pronunciation_entry(
        PronunciationEntry(
            id="pronunciation-a",
            workspace_id="workspace_a",
            term="Codex",
            language="en",
            target_pronunciation="KO-deks",
            scope="workspace",
            scope_reference=None,
            version="v1",
        )
    )

    class NoDictionaryProvider(FixtureVoiceProvider):
        supports_pronunciation_dictionary = False

    dictionary_provider = NoDictionaryProvider()
    dictionary_result = _voice_request(
        repository, provider=dictionary_provider, preview_text="词典预检"
    )
    assert (dictionary_result.status, dictionary_result.reason, dictionary_provider.calls) == (
        "unavailable",
        "pronunciation_dictionary_unavailable",
        0,
    )
    assert dictionary_result.impact and dictionary_result.repair_action


def test_workspace_media_reference_rejects_traversal_and_unapproved_absolute_paths(tmp_path):
    repository = _repository(tmp_path)
    approved = repository.video_library_path / "approved" / "voice.wav"
    approved.parent.mkdir()
    approved.write_bytes(synthetic_wav_bytes())

    assert repository.resolve_workspace_media_reference(
        "workspace_a", "workspace://approved/voice.wav"
    ) == approved.resolve()
    for reference in (
        "workspace://",
        "workspace://approved\\voice.wav",
        "workspace://approved/./voice.wav",
        "workspace://approved/../voice.wav",
        "workspace://approved/%2e%2e/voice.wav",
        str(approved),
        str(tmp_path / "outside.wav"),
    ):
        with pytest.raises(ValueError):
            repository.resolve_workspace_media_reference("workspace_a", reference)


@pytest.mark.parametrize(
    "reference_audio_path",
    ("workspace://approved/%2e%2e/outside.wav",),
)
def test_voice_preview_blocks_unapproved_reference_before_fixture_provider(
    tmp_path,
    reference_audio_path,
):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    invalid_voice = replace(
        voice,
        version="voice-invalid-reference",
        reference_audio_path=reference_audio_path,
    )
    provider = FixtureVoiceProvider()
    with pytest.raises(ValueError):
        repository.save_versioned_voice_profile(
            invalid_voice, verified_owner_user_id=repository._preview_member_id
        )
    assert provider.calls == 0


def test_preview_preflight_persists_missing_inputs_without_calling_fixture_provider(tmp_path):
    repository = _repository(tmp_path)
    person, voice, reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    successful_voice = _voice_request(repository, preview_text="有效声音来源")
    Path(successful_voice.output_path).unlink()
    avatar_provider = FixtureAvatarProvider()
    avatar_result = _avatar_request(repository, audio=successful_voice, provider=avatar_provider)
    reference.unlink()
    voice_provider = FixtureVoiceProvider()
    voice_result = _voice_request(repository, preview_text="参考失联", provider=voice_provider)

    assert (voice_result.status, voice_result.reason) == ("blocked", "reference_audio_invalid")
    assert voice_provider.calls == 0
    assert (avatar_result.status, avatar_result.reason, avatar_provider.calls) == (
        "failed",
        "audio_missing",
        0,
    )


@pytest.mark.parametrize("output_mode", ["missing", "outside"])
def test_voice_preview_rejects_missing_or_outside_fixture_output(tmp_path, output_mode):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)

    result = _voice_request(repository, provider=FixtureVoiceProvider(output_mode=output_mode))

    assert (result.status, result.reason) == ("failed", "output_invalid")


def test_avatar_preview_persists_unavailable_and_revoked_historical_versions(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    audio = _voice_request(repository, preview_text="人物预览来源")
    unavailable = _avatar_request(
        repository, audio=audio, provider=UnavailableAvatarAndLipSyncProvider()
    )
    assert (unavailable.status, unavailable.reason) == ("unavailable", "capability_unavailable")

    active_provider = FixtureAvatarProvider()
    active = _avatar_request(repository, audio=audio, provider=active_provider)
    repeated_active = _avatar_request(repository, audio=audio, provider=active_provider)
    repository.save_authorization(replace(person.authorization, version="v2", status="revoked"))
    revoked = _avatar_request(repository, audio=audio, provider=FixtureAvatarProvider())

    assert active.status == "succeeded"
    assert repeated_active == active
    assert active_provider.calls == 1
    assert active.identity != revoked.identity
    assert (revoked.status, revoked.reason) == ("blocked", "authorization_revoked")
    assert repository.get_person_profile("workspace_a", person.id, person.version) == person


def test_avatar_preview_failure_requires_explicit_retry(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    audio = _voice_request(repository, preview_text="人物预览失败重试来源")
    provider = FixtureAvatarProvider(output_mode="missing")
    failed = _avatar_request(repository, audio=audio, provider=provider)
    unchanged = _avatar_request(repository, audio=audio, provider=provider)
    provider.output_mode = "expected"
    retried = _avatar_request(repository, audio=audio, provider=provider, retry=True)

    assert (failed.status, failed.attempt) == ("failed", 1)
    assert unchanged == failed
    assert (retried.status, retried.attempt) == ("succeeded", 2)
    assert provider.calls == 2
    assert repository.preview_records.list_media_preview_attempts(
        "workspace_a", "avatar_lipsync", failed.identity
    ) == [retried, failed]


def test_avatar_preview_rejects_audio_outside_output_root_before_fixture_provider(tmp_path):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    audio = _voice_request(repository, preview_text="越界音频拒绝来源")
    outside_audio = tmp_path / "outside.wav"
    outside_audio.write_bytes(synthetic_wav_bytes())
    provider = FixtureAvatarProvider()

    result = _avatar_request(repository, audio=audio, provider=provider, audio_path=str(outside_audio))

    assert (result.status, result.reason, provider.calls) == (
        "failed",
        "audio_path_outside_output_root",
        0,
    )


@pytest.mark.parametrize("output_mode", ["missing", "outside"])
def test_avatar_preview_rejects_missing_or_outside_fixture_output(tmp_path, output_mode):
    repository = _repository(tmp_path)
    person, voice, _reference = _profiles(repository)
    _save_profiles(repository, person, voice)
    audio = _voice_request(repository, preview_text="人物预览输出校验来源")
    result = _avatar_request(
        repository, audio=audio, provider=FixtureAvatarProvider(output_mode=output_mode)
    )

    assert (result.status, result.reason) == ("failed", "output_invalid")
