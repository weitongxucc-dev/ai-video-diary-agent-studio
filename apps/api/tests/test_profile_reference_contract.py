"""Isolated persistence/qualification; no real provider, user media or GUI."""
import io
import json
import subprocess
import wave
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from app.avatar_lipsync import (
    Authorization, PersonProfile, ProfileReference, VoiceProfile,
    profile_use_failure, _authorization_failure, AvatarAndLipSyncRequest,
)
from app.production import ProductionRepository, MaterialAsset, MaterialSegment, ProductionProfile
from app.narration import VoicePreviewService, NarrationGenerator
from app.creation_execution import presenter_inputs
from app.voice_clone import VoiceCloneError, VoiceCloneResult


@pytest.fixture
def repository(tmp_path):
    return ProductionRepository(database_path=tmp_path / 'db.sqlite3', video_library_path=tmp_path / 'library',
                                duration_reader=lambda _: 2, scene_reader=lambda *_: [])


def wav_bytes():
    target = io.BytesIO()
    with wave.open(target, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\0\0' * 16000)
    return target.getvalue()


def voice(repository, **changes):
    ref, _, _ = repository.import_voice_reference_wav('ws', wav_bytes(), verified_owner_user_id='member')
    return replace(VoiceProfile(id='voice', workspace_id='ws', display_name='独立声音', version='v1',
                                language='zh', reference_audio_path=ref, reference_text='合成参考',
                                reference=ProfileReference()), **changes)


def save_voice(repository, **changes):
    return repository.save_versioned_voice_profile(voice(repository, **changes), verified_owner_user_id='member')


def image_asset(repository):
    root = repository.video_library_path
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'portrait.png'
    Image.new('RGB', (2, 2), (100, 100, 100)).save(path)
    return repository._save_asset(MaterialAsset(id='portrait', workspace_id='ws', file_name=path.name,
        path=str(path), size_bytes=path.stat().st_size, modified_at=datetime.fromtimestamp(path.stat().st_mtime), media_type='image',
        owner_user_id='member'))


def person(repository, asset):
    return PersonProfile(id='person', workspace_id='ws', display_name='合成人物', version='v1',
        avatar_reference=repository.workspace_media_reference_for_asset('ws', asset.id),
        background_capabilities=(), shot_capabilities=(), reference=ProfileReference(asset_id=asset.id))


def test_independent_wav_registration_restart_and_default_pins_version(repository):
    saved = save_voice(repository)
    assert not saved.person_profile_id and not saved.model_id and saved.authorization is None
    assert saved.use_state == 'available' and saved.owner_user_id == 'member'
    repository.set_default_versioned_voice('ws', saved.id, saved.version, viewer_user_id='member')
    v2 = repository.save_versioned_voice_profile(replace(saved, version='v2', display_name='改名'), verified_owner_user_id='member')
    reopened = ProductionRepository(database_path=repository.database_path, video_library_path=repository.video_library_path)
    assert reopened.get_default_versioned_voice('ws') == saved
    assert reopened.get_versioned_voice_profile('ws', saved.id) == v2
    assert reopened.resolve_narration_inputs(workspace_id='ws', episode_id='task', voice_profile_id=saved.id,
                                            voice_version='v1').voice.version == 'v1'
    assert reopened.resolve_voice_reference(saved, viewer_user_id='member').read_bytes() == wav_bytes()
    with pytest.raises(KeyError):
        reopened.get_versioned_voice_profile('other', saved.id)
    with pytest.raises(ValueError):
        reopened.set_default_versioned_voice('ws', saved.id, '', viewer_user_id='member')


def test_immutable_version_and_owner_cannot_be_replaced(repository):
    saved = save_voice(repository)
    with pytest.raises(ValueError, match='不可|版本'):
        repository.save_versioned_voice_profile(replace(saved, display_name='覆盖'), verified_owner_user_id='member')
    with pytest.raises(PermissionError):
        repository.save_versioned_voice_profile(replace(saved, version='v2'), verified_owner_user_id='intruder')
    assert repository.get_versioned_voice_profile('ws', 'voice', 'v1') == saved


def test_person_reference_and_optional_person_pair(repository):
    asset = image_asset(repository)
    saved_person = repository.save_person_profile(person(repository, asset), verified_owner_user_id='member')
    independent = save_voice(repository)
    assert repository.resolve_profile_reference(saved_person, viewer_user_id='member')[0].name == 'portrait.png'
    linked = repository.save_versioned_voice_profile(replace(independent, version='v2',
        person_profile_id=saved_person.id, person_version=saved_person.version), verified_owner_user_id='member')
    assert linked.person_version == 'v1'
    with pytest.raises(ValueError, match='同时'):
        repository.save_versioned_voice_profile(replace(independent, version='v3', person_profile_id='person'),
                                               verified_owner_user_id='member')
    with pytest.raises(KeyError):
        repository.save_versioned_voice_profile(replace(independent, version='v3', person_profile_id='foreign', person_version='v1'),
                                               verified_owner_user_id='member')


@pytest.mark.parametrize('reference', [ProfileReference(start_seconds=-1, end_seconds=1),
    ProfileReference(start_seconds=0), ProfileReference(start_seconds=0, end_seconds=3),
    ProfileReference(segment_id='unknown'), ProfileReference(start_seconds=float('nan'), end_seconds=1)])
def test_invalid_reference_range_is_not_registered(repository, reference):
    with pytest.raises(ValueError):
        repository.save_versioned_voice_profile(voice(repository, reference=reference), verified_owner_user_id='member')
    assert repository.list_versioned_voice_profiles('ws') == []


def test_person_segment_range_persists_without_a_model_and_never_uses_whole_file(repository):
    root = repository.video_library_path
    root.mkdir(parents=True)
    path = root / 'synthetic.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=16x16:r=2', '-t', '2',
                    '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(path)],
                   check=True, timeout=10, capture_output=True)
    asset = repository._save_asset(MaterialAsset(id='video', workspace_id='ws', file_name=path.name,
        path=str(path), size_bytes=path.stat().st_size, modified_at=datetime.fromtimestamp(path.stat().st_mtime), duration_seconds=2,
        owner_user_id='member', segments=[MaterialSegment(id='segment', asset_id='video', start_seconds=0, end_seconds=2)]))
    profile = replace(person(repository, asset), reference=ProfileReference(asset_id='video', segment_id='segment',
                      start_seconds=.25, end_seconds=1.5))
    saved = repository.save_person_profile(profile, verified_owner_user_id='member')
    assert repository.get_person_profile('ws', 'person', 'v1') == saved
    assert repository.resolve_profile_reference(saved, viewer_user_id='member', for_generation=False)[1:] == (.25, 1.5)
    with pytest.raises(ValueError, match='精确派生'):
        repository.resolve_profile_reference(saved, viewer_user_id='member')
    with pytest.raises(ValueError, match='超出'):
        repository.save_person_profile(replace(saved, version='v2', reference=replace(saved.reference, end_seconds=3)),
                                       verified_owner_user_id='member')


@pytest.mark.parametrize('state', ['reference_only', 'excluded', 'not_for_automation'])
def test_profile_and_source_restrictions_are_separate_and_reread(repository, state):
    saved = save_voice(repository)
    asset = repository._asset_by_path('ws', str(repository.resolve_workspace_media_reference('ws', saved.reference_audio_path)))
    asset.use_state = state
    repository._save_asset(asset)
    assert repository.get_versioned_voice_profile('ws', 'voice').use_state == 'available'
    with pytest.raises(ValueError, match='来源'):
        repository.resolve_voice_reference(saved, viewer_user_id='member')
    asset.use_state = 'available'
    repository._save_asset(asset)
    repository.save_versioned_voice_profile(replace(saved, version='v2', use_state=state), verified_owner_user_id='member')
    with pytest.raises(ValueError, match=state):
        repository.resolve_voice_reference(saved, viewer_user_id='member')


def test_shared_source_revocation_foreign_owner_and_lost_file(repository):
    saved = save_voice(repository, visibility='shared')
    path = repository.resolve_workspace_media_reference('ws', saved.reference_audio_path)
    asset = repository._asset_by_path('ws', str(path))
    # Shared profile never widens the underlying private source.
    with pytest.raises(PermissionError):
        repository.resolve_voice_reference(saved, viewer_user_id='other')
    asset.visibility = 'shared'
    repository._save_asset(asset)
    repository.set_asset_visibility('ws', asset.id, 'shared')
    assert repository.resolve_voice_reference(saved, viewer_user_id='other') == path
    repository.set_asset_visibility('ws', asset.id, 'private')
    with pytest.raises(PermissionError):
        repository.resolve_voice_reference(saved, viewer_user_id='other')
    path.unlink()
    assert repository.get_versioned_voice_profile('ws', 'voice') == saved
    with pytest.raises(ValueError, match='失联'):
        repository.resolve_voice_reference(saved, viewer_user_id='member')


def test_source_identity_escape_and_invalid_wav_stop_registration(repository):
    reference, _, _ = repository.import_voice_reference_wav('ws', wav_bytes(), verified_owner_user_id='member')
    with pytest.raises(PermissionError):
        repository.save_versioned_voice_profile(replace(voice(repository), reference_audio_path=reference),
                                               verified_owner_user_id='other')
    with pytest.raises(ValueError):
        repository.save_versioned_voice_profile(voice(repository, reference_audio_path='workspace://../outside.wav'),
                                               verified_owner_user_id='member')
    with pytest.raises(ValueError):
        repository.import_voice_reference_wav('ws', b'not wav', verified_owner_user_id='member')
    wrong_asset = image_asset(repository)
    with pytest.raises(ValueError, match='不一致'):
        repository.save_versioned_voice_profile(voice(repository, reference=ProfileReference(asset_id=wrong_asset.id)),
                                               verified_owner_user_id='member')


def test_expired_or_revoked_authorization_never_upgrades_historical_reference(repository):
    saved = save_voice(repository)
    auth = Authorization(id='restriction', version='v1', rights_holder='holder', content_types=('voice',),
        usage_scopes=('narration',), effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        expires_at=None, evidence_reference='test://proof', status='revoked', workspace_id='ws')
    restricted = repository.save_versioned_voice_profile(replace(saved, version='v2', authorization=auth),
                                                        verified_owner_user_id='member')
    repository.save_authorization(replace(auth, version='v2', status='active'))
    resolved = repository.resolve_narration_inputs(workspace_id='ws', episode_id='task', voice_profile_id='voice', voice_version='v2')
    assert resolved.voice.authorization.status == 'revoked'
    with pytest.raises(ValueError, match='revoked'):
        repository.resolve_voice_reference(restricted, viewer_user_id='member')
    expired = replace(restricted, authorization=replace(auth, status='active', expires_at=datetime.now(timezone.utc)-timedelta(days=1)))
    assert profile_use_failure(expired, content_type='voice', usage_scope='narration') == 'authorization_expired'
    pending = replace(saved, authorization=replace(auth, status='pending', rights_holder='', evidence_reference=''))
    assert profile_use_failure(pending, content_type='voice', usage_scope='narration') is None


def test_legacy_payload_is_read_without_write_or_assumed_private_owner(repository):
    saved = save_voice(repository)
    with repository._connect() as conn:
        raw = json.loads(conn.execute('SELECT payload FROM versioned_voice_profiles').fetchone()['payload'])
        for key in ('reference', 'use_state', 'owner_user_id', 'visibility', 'display_name', 'model_id', 'person_profile_id', 'person_version'):
            raw.pop(key)
        before = json.dumps(raw, ensure_ascii=False)
        conn.execute('UPDATE versioned_voice_profiles SET payload=?', (before,))
    old = repository.get_versioned_voice_profile('ws', 'voice')
    assert old.reference is None and old.owner_user_id is None
    assert old.model_id == '' and old.person_profile_id == ''
    with pytest.raises(PermissionError):
        repository.resolve_voice_reference(old, viewer_user_id='member')
    with repository._connect() as conn:
        assert conn.execute('SELECT payload FROM versioned_voice_profiles').fetchone()['payload'] == before


def test_zero_percent_presenter_never_queries_person_or_model():
    class Trap:
        def __getattr__(self, name):
            raise AssertionError(name)
    assert presenter_inputs(Trap(), Trap(), Trap(), SimpleNamespace(presenter_share_target=0), Trap()) == []


class Provider:
    model_id = 'controlled-test-provider'
    ready = True
    supported_languages = ('zh',)
    supports_pronunciation_dictionary = True
    message = 'test'
    calls = 0
    fail = False
    def generate(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise VoiceCloneError('controlled provider failure')
        output = kwargs['output_path']
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(wav_bytes())
        return VoiceCloneResult(str(output), 2, 8000)


def test_independent_narration_provider_failure_and_cached_preview_rechecks_source(repository, tmp_path):
    saved = save_voice(repository)
    provider = Provider()
    service = VoicePreviewService(tmp_path / 'output', provider)
    args = dict(repository=repository, workspace_id='ws', voice_profile_id=saved.id,
                preview_text='合成文字', viewer_user_id='member')
    first = service.request(**args)
    assert first.status == 'succeeded' and provider.calls == 1
    assert service.request(**args).id == first.id and provider.calls == 1
    asset = repository._asset_by_path('ws', str(repository.resolve_workspace_media_reference('ws', saved.reference_audio_path)))
    asset.use_state = 'excluded'
    repository._save_asset(asset)
    denied = service.request(**args)
    assert denied.status == 'blocked' and denied.output_path is None and provider.calls == 1
    asset.use_state = 'available'
    repository._save_asset(asset)
    provider.fail = True
    failed = service.request(**{**args, 'preview_text': '另一条合成文字'})
    assert failed.status == 'failed' and failed.output_path is None and provider.calls == 2


def test_unconfigured_model_does_not_block_saved_reference_or_fake_preview(repository, tmp_path):
    saved = save_voice(repository)
    provider = Provider()
    provider.ready = False
    result = VoicePreviewService(tmp_path / 'output', provider).request(repository=repository, workspace_id='ws',
        voice_profile_id=saved.id, preview_text='合成文字', viewer_user_id='member')
    assert repository.get_versioned_voice_profile('ws', 'voice') == saved
    assert result.status == 'unavailable' and result.reason == 'capability_unavailable'
    assert result.output_path is None and provider.calls == 0


def test_editing_segment_text_preserves_recognition_and_brand_is_not_global_gate(repository):
    asset = image_asset(repository)
    segment = MaterialSegment(id='seg', asset_id=asset.id, start_seconds=0, end_seconds=1,
        description='人工说明', transcript='旧字幕', system_tags=['识别事实'], user_tags=['我的标签'], usage_count=7)
    asset.segments = [segment]
    repository._save_asset(asset)
    updated = repository.update_segment('ws', asset.id, segment.id, description='', transcript='新字幕',
                                        review_status='raw', user_tags=['新标签'])
    actual = repository.get_asset('ws', asset.id).segments[0]
    assert actual.description == '' and actual.transcript == '新字幕' and actual.user_tags == ['新标签']
    assert actual.system_tags == ['识别事实'] and actual.usage_count == 7
    assert actual.start_seconds == segment.start_seconds and actual.end_seconds == segment.end_seconds
    assert 'brand_profile_missing' not in {item['code'] for item in repository.readiness('ws')['blockers']}


def test_independent_voice_is_consumed_by_task_narration_without_person(repository, tmp_path):
    saved = save_voice(repository)
    resolved = repository.resolve_voice_reference(saved, viewer_user_id='member')
    provider = Provider()
    result = NarrationGenerator(tmp_path / 'output', provider).generate_for_creation_task(
        workspace_id='ws', task_id='task', script_id='script', narration_text='测试', max_duration_seconds=10,
        voice_identity=saved, pronunciation_entries=(), resolved_reference_audio_path=resolved)
    assert result.voice_profile_id == saved.id and result.task_id == 'task'
    assert result.script_id == 'script' and result.duration_seconds == 2 and provider.calls == 1
    assert not saved.person_profile_id and saved.authorization is None


@pytest.mark.parametrize('auth_link', [dict(authorization_id='lost', authorization_version='v1'),
                                     dict(authorization_id='lost', authorization_version=None)])
def test_unmapped_historical_authorization_remains_readable_but_not_runnable(repository, auth_link):
    saved = save_voice(repository)
    with repository._connect() as conn:
        payload = json.loads(conn.execute('SELECT payload FROM versioned_voice_profiles').fetchone()['payload'])
        payload.update(auth_link)
        before = json.dumps(payload)
        conn.execute('UPDATE versioned_voice_profiles SET payload=?', (before,))
    read = repository.get_versioned_voice_profile('ws', 'voice')
    assert read.id == saved.id and read.reference_audio_path == saved.reference_audio_path
    assert read.reference_issue.startswith('historical_authorization_reference_')
    with pytest.raises(ValueError, match='重新定位'):
        repository.resolve_voice_reference(read, viewer_user_id='member')
    with repository._connect() as conn:
        assert conn.execute('SELECT payload FROM versioned_voice_profiles').fetchone()['payload'] == before


def test_reference_replaced_at_same_path_is_not_silently_the_same_version(repository):
    import os
    saved = save_voice(repository)
    path = repository.resolve_workspace_media_reference('ws', saved.reference_audio_path)
    stamp = path.stat().st_mtime
    path.write_bytes(wav_bytes())
    os.utime(path, (stamp + 10, stamp + 10))
    with pytest.raises(ValueError, match='已变化'):
        repository.resolve_voice_reference(saved, viewer_user_id='member')


def test_voice_reference_can_be_registered_before_transcript_but_not_generated(repository, tmp_path):
    saved = save_voice(repository, reference_text='')
    assert repository.get_versioned_voice_profile('ws', 'voice') == saved
    provider = Provider()
    result = VoicePreviewService(tmp_path / 'output', provider).request(repository=repository, workspace_id='ws',
        voice_profile_id='voice', preview_text='合成文字', viewer_user_id='member')
    assert result.status == 'blocked' and result.reason == 'reference_text_missing'
    assert result.output_path is None and provider.calls == 0


def test_new_registration_cannot_use_legacy_proof_shape_to_skip_reference_validation(repository):
    value = voice(repository)
    with pytest.raises(ValueError, match='来源引用'):
        repository.save_versioned_voice_profile(replace(value, reference=None), verified_owner_user_id='member')
    assert repository.list_versioned_voice_profiles('ws') == []


def test_readiness_does_not_count_a_lost_reference_as_usable(repository):
    saved = save_voice(repository)
    repository.resolve_workspace_media_reference('ws', saved.reference_audio_path).unlink()
    blockers = repository.readiness('ws', viewer_user_id='member')['blockers']
    assert any(item['code'] == 'voice_reference_unavailable' and '失联' in item['message'] for item in blockers)
