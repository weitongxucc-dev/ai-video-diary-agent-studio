"""Actual source-reference and synthetic maintenance raw-reader consumers."""
from app.local_adoption_maintenance import LocalAdoptionMaintenance, _local_adoption_digest, _local_adoption_plan
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import wave

from PIL import Image
import pytest

from app.production import ProductionRepository, MaterialAsset
from app.profiles.contracts import Authorization, PersonProfile, ProfileReference, VoiceProfile
from app.profiles.records import ProfileRecordStore
from app.profiles.authorizations import MediaAuthorizationStore
from test_local_adoption import legacy as legacy, plan, OWNER


def reference_state(tmp_path, kind):
    library = tmp_path / 'library'
    library.mkdir()
    repo = ProductionRepository(tmp_path / 'profiles.sqlite3', library)
    path = library / ('portrait.png' if kind == 'person' else 'voice.wav')
    if kind == 'person':
        Image.new('RGB', (2, 2), (30, 40, 50)).save(path)
    else:
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\0\0' * 16000)
    stat = path.stat()
    asset = MaterialAsset('source', 'ws', path.name, str(path), stat.st_size,
        datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        media_type='image' if kind == 'person' else 'audio', duration_seconds=None if kind == 'person' else 2,
        owner_user_id='member', visibility='private')
    repo._save_asset(asset)
    reference = repo.workspace_media_reference_for_asset('ws', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='ws', reference=reference)
    now = datetime.now(timezone.utc)
    auth = Authorization('consent', 'v1', 'fixture', ('person_likeness', 'voice'),
        ('avatar_lipsync', 'narration'), now - timedelta(days=1), None, 'fixture://consent', 'active', 'ws')
    repo.save_authorization(auth)
    shared = dict(authorization=auth, reference=ProfileReference(asset_id=asset.id),
        owner_user_id='member', visibility='private')
    profile = (PersonProfile('profile', 'ws', 'fixture', 'v1', reference, (), (), **shared)
        if kind == 'person' else VoiceProfile(id='profile', workspace_id='ws', version='v1',
            language='zh', reference_audio_path=reference, reference_text='fixture', **shared))
    return repo, profile, path


@pytest.mark.parametrize('kind', ['person', 'voice'])
def test_source_reference_calls_raw_reader_preserves_fallback_and_current_permissions(tmp_path, monkeypatch, kind):
    repo, profile, path = reference_state(tmp_path, kind)
    reader = repo._profile_records._latest_profile_payload_row
    calls = []
    def observed(conn, workspace_id, profile_id, *, person):
        row = reader(conn, workspace_id, profile_id, person=person)
        calls.append((conn, workspace_id, profile_id, person, row))
        return row
    monkeypatch.setattr(repo._profile_records, '_latest_profile_payload_row', observed)
    def forbidden():
        pytest.fail('raw reader/decoder must use caller conn')
    monkeypatch.setattr(repo._profile_records, '_connect', forbidden)
    monkeypatch.setattr(repo._media_authorizations, '_connect', forbidden)
    resolved = repo._profile_reference_source(profile, viewer_user_id='member', local_diagnostic=False,
        for_generation=True, probe_media=False)
    assert resolved == (path.resolve(), None, None)
    assert calls[-1][1:4] == ('ws', profile.id, kind == 'person') and calls[-1][4] is None
    save = repo._profile_records._save_person_profile if kind == 'person' else repo._profile_records._save_versioned_voice_profile
    table = 'person_profiles' if kind == 'person' else 'versioned_voice_profiles'
    with repo._connect() as conn:
        save(conn, profile)
        save(conn, replace(profile, version='v2', owner_user_id='other'))
        conn.execute(f'UPDATE {table} SET created_at=?', ('2000-01-01T00:00:00+00:00',))
    with pytest.raises(PermissionError, match='档案不属于当前成员'):
        repo._profile_reference_source(profile, viewer_user_id='member', local_diagnostic=False,
            for_generation=False, probe_media=False)
    assert isinstance(calls[-1][4], sqlite3.Row)
    assert json.loads(calls[-1][4]['payload'])['version'] == 'v2'


def observed_maintenance_readers(monkeypatch):
    calls = []
    for cls, name in [(ProfileRecordStore, '_voice_version_row'),
            (ProfileRecordStore, '_latest_voice_version_row'),
            (ProfileRecordStore, '_voice_version_existence_row'),
            (MediaAuthorizationStore, '_authorization_history_rows')]:
        original = getattr(cls, name)
        def observed(self, conn, *args, _original=original, _name=name, **kwargs):
            result = _original(self, conn, *args, **kwargs)
            calls.append((_name, conn, args, result))
            return result
        monkeypatch.setattr(cls, name, observed)
    return calls


def test_maintenance_calls_released_readers_with_raw_row_order_and_digest(legacy, monkeypatch):
    repo, _ = legacy
    auth = repo.get_authorization('local', 'consent', 'v1')
    repo.save_authorization(replace(auth, version='v2'))
    repo.save_authorization(replace(auth, workspace_id='foreign'))
    with repo._connect() as conn:
        voice = dict(conn.execute('SELECT * FROM versioned_voice_profiles').fetchone())
        raw = json.dumps(json.loads(voice['payload']), ensure_ascii=False, indent=2) + ' \n'
        conn.execute('UPDATE versioned_voice_profiles SET payload=?', (raw,))
        conn.execute("UPDATE media_authorizations SET created_at='2000-01-01T00:00:00+00:00'")
        voice = dict(conn.execute('SELECT * FROM versioned_voice_profiles').fetchone())
        expected_history = [dict(row) for row in conn.execute(
            "SELECT * FROM media_authorizations WHERE workspace_id='local' AND id='consent' ORDER BY created_at,rowid")]
    calls = observed_maintenance_readers(monkeypatch)
    manifest = plan(repo)
    assert {item[0] for item in calls} == {'_voice_version_row', '_latest_voice_version_row',
        '_voice_version_existence_row', '_authorization_history_rows'}
    assert all(item[1] is calls[0][1] and item[2][0] == 'local' for item in calls)
    fixed = next(item[3] for item in calls if item[0] == '_voice_version_row')
    history = next(item[3] for item in calls if item[0] == '_authorization_history_rows')
    assert isinstance(fixed, sqlite3.Row) and fixed['payload'] == raw
    assert [row['version'] for row in history] == ['v1', 'v2']
    assert [dict(row) for row in history] == expected_history
    assert manifest['voice']['row_sha256'] == _local_adoption_digest(voice)
    assert manifest['voice']['payload_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert manifest['voice']['authorization_sha256'] == _local_adoption_digest(expected_history)


def test_maintenance_consumer_reads_uncommitted_caller_rows_without_commit(legacy, monkeypatch):
    repo, _ = legacy
    maintenance = LocalAdoptionMaintenance(repo.database_path, repo.video_library_path)
    original_manifest = plan(repo)
    calls = observed_maintenance_readers(monkeypatch)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        original_voice = conn.execute('SELECT payload FROM versioned_voice_profiles').fetchone()[0]
        changed_raw = ' \n' + original_voice + '\n '
        conn.execute('UPDATE versioned_voice_profiles SET payload=?', (changed_raw,))
        auth = maintenance._media_authorizations._get_authorization(conn, 'local', 'consent', 'v1')
        maintenance._media_authorizations._save_authorization(conn, 'local', replace(auth, version='v2'))
        def forbidden():
            pytest.fail('maintenance raw reader must not open a second connection')
        monkeypatch.setattr(maintenance._profile_records, '_connect', forbidden)
        monkeypatch.setattr(maintenance._media_authorizations, '_connect', forbidden)
        manifest, originals = _local_adoption_plan(maintenance, conn, member_id=OWNER, operation_id='approved-operation',
            asset_ids=[f'asset_{i}' for i in range(12)], voice_id='voice_cc', voice_version='v1',
            new_voice_version='v2', authorization_basis='属于我，允许接入并保持私有')
        assert all(item[1] is conn for item in calls)
        assert originals['voice']['payload'] == changed_raw
        assert [row['version'] for row in originals['authorizations']] == ['v1', 'v2']
        assert manifest['voice']['payload_sha256'] != original_manifest['voice']['payload_sha256']
        with sqlite3.connect(repo.database_path.resolve().as_uri() + '?mode=ro', uri=True) as observer:
            assert observer.execute('SELECT payload FROM versioned_voice_profiles').fetchone()[0] == original_voice
            assert observer.execute('SELECT count(*) FROM media_authorizations').fetchone()[0] == 1
        conn.rollback()
    assert plan(repo) == original_manifest
