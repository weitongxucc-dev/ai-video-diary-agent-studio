import copy
import hashlib
import json
import os
import sqlite3
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.avatar_lipsync import Authorization
from app.local_adoption import main
from app.production import (
    MaterialAsset, MaterialSegment, ProductionRepository, VersionedVoiceProfile,
    _local_adoption_digest, _versioned_voice_profile_payload,
)

OWNER = '00000000-0000-0000-0000-000000000001'


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / 'media'
    root.mkdir()
    repo = ProductionRepository(tmp_path / 'test.sqlite', root)
    now = datetime.now(timezone.utc)
    authorization = Authorization(id='consent', version='v1', rights_holder='private-name',
        workspace_id='local', content_types=('voice',), usage_scopes=('narration',),
        effective_from=now-timedelta(days=1), expires_at=None,
        evidence_reference='private-consent-evidence', status='active')
    repo.save_authorization(authorization)
    for index in range(12):
        path = root / f'{index}.mp4'
        path.write_bytes(b'isolated-no-decoder-input')
        repo._save_asset(MaterialAsset(id=f'asset_{index}', workspace_id='local', path=str(path),
            file_name=path.name, size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            visibility='shared', duration_seconds=2, analysis_status='failed',
            analysis_message='private-analysis', usage_role='exclude' if index == 1 else 'b_roll',
            use_state='excluded' if index == 1 else 'available', user_tags=['private-tag'],
            segments=[MaterialSegment(id=f'segment_{index}', asset_id=f'asset_{index}',
                start_seconds=0, end_seconds=2, usage_role='exclude', transcript='private-transcript')]))
    wav_path = root / 'original.wav'
    with wave.open(str(wav_path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\x00\x00' * 32000)
    voice = VersionedVoiceProfile(id='voice_cc', workspace_id='local', version='v1', language='zh',
        reference_audio_path='workspace://original.wav', reference_text='private-reference-text',
        authorization=authorization, owner_user_id=None, visibility='shared')
    with repo._connect() as conn:
        conn.execute("INSERT INTO material_ingestion_binding(id,workspace_id,library_path) VALUES (1,'local',?)", (str(root),))
        conn.execute("INSERT INTO local_members(member_id,name,role,created_at) VALUES ('other','other','member',?)", (now.isoformat(),))
        conn.execute("INSERT INTO versioned_voice_profiles VALUES ('local','voice_cc','v1',?,?)",
                     (json.dumps(_versioned_voice_profile_payload(voice)), now.isoformat()))
        conn.execute("INSERT INTO workspace_media_references VALUES ('workspace://original.wav','local',?)", (now.isoformat(),))
        conn.execute("INSERT INTO workspace_media_references VALUES ('workspace://0.mp4','local',?)", (now.isoformat(),))
        # A pre-existing standalone legacy database need not have the new table.
        conn.execute('DROP TABLE local_adoption_receipts')
    return repo, wav_path


def plan(repo, **overrides):
    kwargs = dict(member_id=OWNER, operation_id='approved-operation',
        asset_ids=[f'asset_{i}' for i in range(12)], voice_id='voice_cc', voice_version='v1',
        new_voice_version='v2', authorization_basis='属于我，允许接入并保持私有')
    kwargs.update(overrides)
    return ProductionRepository.plan_local_adoption(repo.database_path, repo.video_library_path, **kwargs)


def apply(repo, manifest, **overrides):
    kwargs = dict(member_id=OWNER, manifest=manifest, manifest_sha256=_local_adoption_digest(manifest))
    kwargs.update(overrides)
    return ProductionRepository.apply_local_adoption(repo.database_path, repo.video_library_path, **kwargs)


def dump(repo):
    with repo._connect() as conn:
        return tuple(conn.iterdump())


def test_dry_run_is_read_only_and_manifest_has_no_private_bodies(legacy, monkeypatch):
    repo, _ = legacy
    before = dump(repo)
    raw_before = hashlib.sha256(repo.database_path.read_bytes()).hexdigest()
    def forbidden(*args, **kwargs):
        raise AssertionError('must not initialize Repository')
    monkeypatch.setattr(ProductionRepository, '__init__', forbidden)
    manifest = plan(repo)
    assert dump(repo) == before
    assert hashlib.sha256(repo.database_path.read_bytes()).hexdigest() == raw_before
    text = json.dumps(manifest)
    for secret in ('private-analysis', 'private-tag', 'private-transcript', 'private-reference-text',
                   'private-consent-evidence', 'private-name', 'payload"', 'token"'):
        assert secret not in text
    assert len(manifest['assets']) == 12


def test_adopts_exact_rows_preserves_v1_and_qualifies_real_wav(legacy):
    repo, wav_path = legacy
    before = dump(repo)
    with repo._connect() as conn:
        originals = {row['id']: dict(row) for row in conn.execute('SELECT * FROM material_assets')}
        original_voice = dict(conn.execute('SELECT * FROM versioned_voice_profiles').fetchone())
        untouched_tables = {name: list(conn.execute(f'SELECT * FROM {name}')) for name in
            ('media_authorizations', 'person_profiles', 'voice_profiles', 'production_profiles', 'local_members')}
        untouched_reference = dict(conn.execute("SELECT * FROM workspace_media_references WHERE reference='workspace://0.mp4'").fetchone())
    original_wav = wav_path.read_bytes()
    original_stat = wav_path.stat()
    manifest = plan(repo)
    result = apply(repo, manifest)
    with repo._connect() as conn:
        assert dict(conn.execute("SELECT * FROM versioned_voice_profiles WHERE version='v1'").fetchone()) == original_voice
        for row in conn.execute("SELECT * FROM material_assets WHERE id LIKE 'asset_%'"):
            original = originals[row['id']]
            expected = json.loads(original['payload'])
            expected.update(owner_user_id=OWNER, visibility='private')
            assert json.loads(row['payload']) == expected
            assert row['visibility'] == 'private'
            assert row['updated_at'] == original['updated_at']
        for name, rows in untouched_tables.items():
            assert list(conn.execute(f'SELECT * FROM {name}')) == rows
        assert dict(conn.execute("SELECT * FROM workspace_media_references WHERE reference='workspace://0.mp4'").fetchone()) == untouched_reference
        assert conn.execute('SELECT COUNT(*) FROM workspace_media_references').fetchone()[0] == 13
        receipt = conn.execute('SELECT * FROM local_adoption_receipts').fetchone()
        assert receipt['member_id'] == OWNER
        snapshot = json.loads(receipt['manifest_json'])
        assert snapshot['before']['voice'] == original_voice
        assert snapshot['before']['assets'] == [originals[f'asset_{i}'] for i in sorted(range(12), key=lambda x: f'asset_{x}')]
    voice = repo.get_versioned_voice_profile('local', 'voice_cc', 'v2')
    assert voice.owner_user_id == OWNER and voice.visibility == 'private'
    assert voice.authorization.id == 'consent'
    reference = repo.profile_reference_input(voice, viewer_user_id=OWNER)
    assert reference.asset_id == result['voice_source_asset_id']
    assert reference.source_path == str(wav_path) and reference.source_duration_seconds == 2
    assert not reference.needs_derivation
    with pytest.raises(PermissionError):
        repo.profile_reference_input(voice, viewer_user_id='other')
    assert wav_path.read_bytes() == original_wav
    assert wav_path.stat() == original_stat
    assert dump(repo) != before
    after = dump(repo)
    assert apply(repo, manifest) == result
    assert dump(repo) == after
    changed = copy.deepcopy(manifest)
    changed['authorization_basis'] += 'changed'
    with pytest.raises(ValueError, match='operation_conflict'):
        apply(repo, changed)


@pytest.mark.parametrize('change', ['payload', 'visibility', 'mtime', 'size', 'replacement', 'member',
                                   'cross_space', 'foreign_owner', 'authorization', 'root', 'version', 'voice_excluded', 'encoded_foreign', 'outside_root'])
def test_changed_inputs_reject_atomically(legacy, change):
    repo, wav_path = legacy
    manifest = plan(repo)
    with repo._connect() as conn:
        if change in ('payload', 'foreign_owner'):
            row = conn.execute("SELECT payload FROM material_assets WHERE id='asset_11'").fetchone()
            payload = json.loads(row['payload'])
            payload['user_tags'] = ['later-edit']
            if change == 'foreign_owner':
                payload['owner_user_id'] = 'other'
            conn.execute("UPDATE material_assets SET payload=? WHERE id='asset_11'", (json.dumps(payload),))
        elif change == 'voice_excluded':
            row = conn.execute('SELECT payload FROM versioned_voice_profiles').fetchone()
            payload = json.loads(row['payload']); payload['use_state'] = 'excluded'
            conn.execute('UPDATE versioned_voice_profiles SET payload=?', (json.dumps(payload),))
        elif change == 'encoded_foreign':
            conn.execute("INSERT INTO workspace_media_references VALUES ('workspace://%31%31.mp4','foreign','now')")
        elif change == 'outside_root':
            outside = repo.database_path.parent / 'outside.mp4'
            outside.write_bytes(b'not-in-authorized-root')
            row = conn.execute("SELECT payload FROM material_assets WHERE id='asset_11'").fetchone()
            payload = json.loads(row['payload']); payload['path'] = str(outside)
            conn.execute("UPDATE material_assets SET file_path=?,payload=? WHERE id='asset_11'", (str(outside), json.dumps(payload)))
        elif change == 'visibility':
            conn.execute("UPDATE material_assets SET visibility='private' WHERE id='asset_11'")
        elif change == 'member':
            conn.execute('DELETE FROM local_members WHERE member_id=?', (OWNER,))
        elif change == 'cross_space':
            conn.execute("INSERT INTO workspace_media_references VALUES ('workspace://11.mp4','foreign','now')")
        elif change == 'authorization':
            row = conn.execute('SELECT payload FROM media_authorizations').fetchone()
            payload = json.loads(row['payload']); payload['status'] = 'revoked'
            conn.execute('UPDATE media_authorizations SET payload=?', (json.dumps(payload),))
        elif change == 'root':
            conn.execute("UPDATE material_ingestion_binding SET workspace_id='foreign'")
        elif change == 'version':
            conn.execute("INSERT INTO versioned_voice_profiles SELECT workspace_id,id,'v2',payload,'later' FROM versioned_voice_profiles")
    if change == 'mtime':
        stat = wav_path.stat(); os.utime(wav_path, ns=(stat.st_atime_ns, stat.st_mtime_ns+1))
    elif change == 'size':
        with wav_path.open('ab') as output:
            output.write(b'x')
    elif change == 'replacement':
        path = repo.video_library_path / '11.mp4'
        stat = path.stat(); content = path.read_bytes(); path.unlink(); path.write_bytes(content)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    before = dump(repo)
    with pytest.raises(ValueError):
        apply(repo, manifest)
    assert dump(repo) == before


def test_transaction_rolls_back_late_failure_including_receipt_migration(legacy):
    repo, _ = legacy
    manifest = plan(repo)
    with repo._connect() as conn:
        conn.execute("CREATE TRIGGER reject_v2 BEFORE INSERT ON versioned_voice_profiles WHEN NEW.version='v2' BEGIN SELECT RAISE(ABORT,'isolated failure'); END")
    before = dump(repo)
    with pytest.raises(sqlite3.IntegrityError):
        apply(repo, manifest)
    assert dump(repo) == before


def test_rejects_wrong_member_space_digest_and_database(legacy, tmp_path):
    repo, _ = legacy
    manifest = plan(repo)
    before = dump(repo)
    for altered in ({**manifest, 'workspace_id': 'foreign'}, {**manifest, 'member_id': 'other'}):
        with pytest.raises(ValueError, match='target_mismatch'):
            apply(repo, altered)
    with pytest.raises(ValueError, match='digest_mismatch'):
        apply(repo, manifest, manifest_sha256='wrong')
    with pytest.raises(ValueError, match='existing_owner_required'):
        plan(repo, member_id='other')
    other_db = tmp_path / 'different.sqlite'
    other_db.write_bytes(repo.database_path.read_bytes())
    with pytest.raises(ValueError, match='database_changed'):
        ProductionRepository.apply_local_adoption(other_db, repo.video_library_path, member_id=OWNER,
            manifest=manifest, manifest_sha256=_local_adoption_digest(manifest))
    assert dump(repo) == before


def test_cli_private_manifest_and_safe_output(legacy, tmp_path, capsys):
    repo, _ = legacy
    path = tmp_path / 'review.json'
    args = ['--database', str(repo.database_path), '--media-root', str(repo.video_library_path),
            '--member-id', OWNER, '--manifest', str(path)]
    dry = ['dry-run', *args, '--operation-id', 'cli-operation', '--voice-id', 'voice_cc',
           '--voice-version', 'v1', '--new-voice-version', 'v2',
           '--authorization-basis', '属于我，允许接入并保持私有']
    for index in range(12):
        dry.extend(['--asset-id', f'asset_{index}'])
    before = dump(repo)
    assert main(dry) == 0
    output = json.loads(capsys.readouterr().out)
    assert str(tmp_path) not in json.dumps(output)
    assert path.stat().st_mode & 0o777 == 0o600
    assert dump(repo) == before
    assert main(['apply', *args, '--manifest-sha256', output['manifest_sha256']]) == 0
    applied = capsys.readouterr().out
    assert str(tmp_path) not in applied and 'private-reference-text' not in applied
    assert main(dry) == 1
    assert str(tmp_path) not in capsys.readouterr().err


def test_late_file_change_rolls_back_entire_adoption(legacy, monkeypatch):
    import app.production as module
    repo, wav_path = legacy
    manifest = plan(repo)
    original_revision = module._local_adoption_file_revision
    calls = 0
    def change_at_final_check(path):
        nonlocal calls
        calls += 1
        # During apply: 12 material checks + WAV before and after PCM read.
        # Then 12 materials and WAV are checked immediately before commit.
        if calls == 27:
            stat = wav_path.stat()
            os.utime(wav_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
        return original_revision(path)
    monkeypatch.setattr(module, '_local_adoption_file_revision', change_at_final_check)
    before = dump(repo)
    with pytest.raises(ValueError, match='file_changed'):
        apply(repo, manifest)
    assert dump(repo) == before
