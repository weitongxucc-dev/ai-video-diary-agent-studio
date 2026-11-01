"""Actual by-path source and maintenance consumers of the released asset readers."""
from app.local_adoption_maintenance import LocalAdoptionMaintenance, _local_adoption_digest, _local_adoption_plan
from dataclasses import replace
import hashlib
import json
import sqlite3

import pytest

from app.materials.assets import MaterialAssetStore

from test_local_adoption import legacy as legacy, plan, OWNER
from test_profile_raw_reader_consumers import reference_state


READERS = ('_asset_payload_row_by_path', '_asset_file_path_rows_outside_workspace',
    '_asset_row_by_id', '_asset_file_path_existence_row', '_asset_id_existence_row')


def observe_readers(monkeypatch):
    calls = []
    for name in READERS:
        original = getattr(MaterialAssetStore, name)
        def observed(self, conn, *args, _name=name, _original=original):
            row = _original(self, conn, *args)
            calls.append((_name, self, conn, args, row))
            return row
        monkeypatch.setattr(MaterialAssetStore, name, observed)
    return calls


@pytest.mark.parametrize('kind', ['person', 'voice'])
def test_source_by_path_calls_released_reader_preserves_column_visibility_and_missing_fallback(tmp_path, monkeypatch, kind):
    repo, profile, path = reference_state(tmp_path, kind)
    profile = replace(profile, reference=None)
    calls = observe_readers(monkeypatch)
    monkeypatch.setattr(repo._material_assets, '_connect', lambda: pytest.fail('reader must use the caller conn'))
    def read(*, diagnostic=False):
        return repo._profile_reference_source(profile, viewer_user_id='member', local_diagnostic=diagnostic,
            for_generation=False, probe_media=False)
    assert read() == (path.resolve(), None, None)
    assert calls[-1][0] == '_asset_payload_row_by_path'
    assert calls[-1][3] == ('ws', str(path.resolve()))
    assert list(calls[-1][4].keys()) == ['payload', 'visibility']
    with repo._connect() as conn:
        data = json.loads(conn.execute('SELECT payload FROM material_assets').fetchone()[0])
        data.update(owner_user_id='other', visibility='private')
        raw = json.dumps(data, ensure_ascii=False, indent=2) + ' \n'
        conn.execute("UPDATE material_assets SET payload=?,visibility='shared'", (raw,))
    assert read() == (path.resolve(), None, None)
    assert calls[-1][4]['payload'] == raw and calls[-1][4]['visibility'] == 'shared'
    with repo._connect() as conn:
        data['visibility'] = 'shared'
        conn.execute("UPDATE material_assets SET payload=?,visibility='private'", (json.dumps(data),))
    with pytest.raises(PermissionError, match='来源素材不属于当前成员'):
        read()
    with repo._connect() as conn:
        conn.execute('DELETE FROM material_assets')
    if kind == 'person':
        with pytest.raises(ValueError, match='人物参考缺少稳定素材登记'):
            read()
    else:
        with pytest.raises(PermissionError, match='历史声音来源缺少成员归属'):
            read()
        assert read(diagnostic=True) == (path.resolve(), None, None)
    assert calls[-1][4] is None


@pytest.mark.parametrize('collision,expected', [
    ('foreign_path', 'local_adoption_source_other_workspace'),
    ('selected_foreign', 'local_adoption_selected_local_asset_missing'),
    ('registered_voice_path', 'local_adoption_voice_source_already_registered'),
    ('foreign_source_id', 'local_adoption_voice_source_id_exists'),
])
def test_maintenance_rejects_original_foreign_and_global_collisions(legacy, monkeypatch, collision, expected):
    repo, wav_path = legacy
    with repo._connect() as conn:
        row = dict(conn.execute("SELECT * FROM material_assets WHERE id='asset_0'").fetchone())
        if collision == 'selected_foreign':
            conn.execute("UPDATE material_assets SET workspace_id='foreign',visibility='private' WHERE id='asset_0'")
        else:
            source_id = 'voice_source_' + hashlib.sha256(b'approved-operation:voice_cc').hexdigest()[:32]
            row.update(id=source_id if collision == 'foreign_source_id' else 'collision',
                workspace_id='local' if collision == 'registered_voice_path' else 'foreign', visibility='private')
            if collision == 'registered_voice_path':
                row['file_path'] = str(wav_path)
            elif collision == 'foreign_source_id':
                row['file_path'] = str(repo.video_library_path / 'foreign-unused.mp4')
            conn.execute('INSERT INTO material_assets(id,workspace_id,file_path,payload,updated_at,visibility) VALUES (?,?,?,?,?,?)',
                tuple(row[key] for key in ('id','workspace_id','file_path','payload','updated_at','visibility')))
    calls = observe_readers(monkeypatch)
    with pytest.raises(ValueError, match=expected):
        plan(repo)
    assert calls and all(item[2] is calls[0][2] for item in calls)
    if collision == 'foreign_path':
        rows = next(item[4] for item in calls if item[0] == '_asset_file_path_rows_outside_workspace')
        assert [row['file_path'] for row in rows] == [row['file_path']]
    elif collision == 'selected_foreign':
        assert calls[0][0] == '_asset_row_by_id' and calls[0][3] == ('local', 'asset_0') and calls[0][4] is None
    elif collision == 'registered_voice_path':
        assert calls[-1][0] == '_asset_file_path_existence_row' and calls[-1][3] == (str(wav_path),)
        assert isinstance(calls[-1][4], sqlite3.Row)
    else:
        assert calls[-1][0] == '_asset_id_existence_row' and calls[-1][3] == (source_id,)
        assert isinstance(calls[-1][4], sqlite3.Row)


def test_maintenance_raw_rows_digests_use_uncommitted_caller_state_and_rollback(legacy, monkeypatch):
    repo, _ = legacy
    maintenance = LocalAdoptionMaintenance(repo.database_path, repo.video_library_path)
    original_manifest = plan(repo)
    calls = observe_readers(monkeypatch)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = dict(conn.execute("SELECT * FROM material_assets WHERE id='asset_0'").fetchone())
        raw = json.dumps(json.loads(old['payload']), ensure_ascii=False, indent=2) + ' \n'
        conn.execute("UPDATE material_assets SET payload=?,visibility='private' WHERE id='asset_0'", (raw,))
        expected = dict(conn.execute("SELECT * FROM material_assets WHERE id='asset_0'").fetchone())
        # Two duplicate, unrelated foreign paths must remain original unsorted Rows.
        for identity in ('z-foreign', 'a-foreign'):
            conn.execute('INSERT INTO material_assets(id,workspace_id,file_path,payload,updated_at,visibility) VALUES (?,?,?,?,?,?)',
                (identity, 'foreign-' + identity, str(repo.video_library_path / 'foreign-unused.mp4'), old['payload'], old['updated_at'], 'private'))
        expected_paths = [tuple(row) for row in conn.execute("SELECT file_path FROM material_assets WHERE workspace_id!='local'")]
        monkeypatch.setattr(maintenance._material_assets, '_connect', lambda: pytest.fail('must not open a second connection'))
        manifest, originals = _local_adoption_plan(maintenance, conn, member_id=OWNER, operation_id='approved-operation',
            asset_ids=[f'asset_{index}' for index in range(12)], voice_id='voice_cc', voice_version='v1',
            new_voice_version='v2', authorization_basis='属于我，允许接入并保持私有')
        assert {item[0] for item in calls} == set(READERS[1:])
        assert all(item[2] is conn for item in calls) and conn.in_transaction
        captured = next(item[4] for item in calls if item[0] == '_asset_row_by_id')
        assert dict(captured) == expected and captured['payload'] == raw
        foreign = next(item[4] for item in calls if item[0] == '_asset_file_path_rows_outside_workspace')
        assert [tuple(row) for row in foreign] == expected_paths and len(foreign) == 2
        assert originals['assets'][0] == expected
        asset = manifest['assets'][0]
        assert asset['row_sha256'] == _local_adoption_digest(expected)
        assert asset['payload_sha256'] == hashlib.sha256(raw.encode()).hexdigest() and asset['visibility'] == 'private'
        with sqlite3.connect(repo.database_path.resolve().as_uri() + '?mode=ro', uri=True) as observer:
            assert observer.execute("SELECT payload,visibility FROM material_assets WHERE id='asset_0'").fetchone() == (old['payload'],old['visibility'])
            assert observer.execute("SELECT count(*) FROM material_assets WHERE workspace_id!='local'").fetchone()[0] == 0
        conn.rollback()
    assert plan(repo) == original_manifest
