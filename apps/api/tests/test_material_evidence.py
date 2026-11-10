from dataclasses import replace

import pytest

from app.materials.assets import MaterialAssetStore
from app.media_observations import MaterialSpeechAnalysis, SubtitleSegment
from test_material_asset_store import database as asset_database, _asset, _insert


@pytest.fixture
def database(tmp_path):
    return asset_database.__wrapped__(tmp_path)


def test_source_evidence_pages_filter_before_transfer_and_keep_correction_separate(database):
    connect, traces = database
    original = _asset()
    original.segments[0].transcript = "人工纠正：最高可达，不是保证"
    original.speech_analysis = MaterialSpeechAnalysis("ready", "a" * 64, "local-model",
        [SubtitleSegment(i * 1000, (i + 1) * 1000, f"ASR {i}") for i in range(100)],
        word_observations=[SubtitleSegment(1500, 1800, "原识别词")], timing_granularity="word", audio_present=True)
    with connect() as conn:
        _insert(conn, original)
    store = MaterialAssetStore(connect)
    common = {"member_id": "owner-a", "expected_updated_at": original.updated_at.isoformat()}
    segment = store.read_evidence('workspace-a', 'asset', **common)["items"][0]
    assert segment['corrected_transcript'] == original.segments[0].transcript
    page = store.read_evidence('workspace-a', 'asset', kind='speech', start_seconds=1.2, end_seconds=4,
        offset=1, limit=1, **common)
    assert page['items'] == [{'source_index': 2, 'start_ms': 2000, 'end_ms': 3000, 'text': 'ASR 2'}]
    assert page['page'] == {'offset': 1, 'limit': 1, 'total_count': 3, 'has_more': True}
    words = store.read_evidence('workspace-a', 'asset', kind='words', **common)
    assert words['clock'] == 'original_source' and words['timing_granularity'] == 'word'
    assert words['items'][0]['text'] == '原识别词'  # Not silently replaced/aligned by the corrected sentence.
    assert any('LIMIT 1 OFFSET 1' in sql for sql in traces)
    assert not any('SELECT payload ' in sql for sql in traces)
    assert 'fixture://' not in str(page) and 'owner-a' not in str(page)


def test_evidence_checks_visibility_revision_and_exclusion(database):
    connect, _ = database
    original = _asset()
    with connect() as conn:
        _insert(conn, original)
    store = MaterialAssetStore(connect)
    for ws, member in [('workspace-a', 'another'), ('other-workspace', 'owner-a')]:
        with pytest.raises(KeyError):
            store.read_evidence(ws, 'asset', member_id=member)
    with pytest.raises(ValueError, match='版本'):
        store.read_evidence('workspace-a', 'asset', member_id='owner-a', expected_updated_at='stale')
    with connect() as conn:
        conn.execute('DELETE FROM material_assets')
        _insert(conn, replace(original, use_state='excluded', user_description='private withheld content'))
    result = store.read_evidence('workspace-a', 'asset', member_id='owner-a')
    assert result['content_withheld'] is True and 'private withheld content' not in str(result)


def test_background_evidence_keeps_fixed_goal_scope_and_classifies_revision_conflict(database):
    from types import SimpleNamespace
    from app.assistant.material_reads import read_goal_materials
    from app.assistant.task_business_errors import TaskBusinessRejected, TaskOperationRejected
    connect, _ = database
    with connect() as conn:
        _insert(conn, _asset())
    production = SimpleNamespace(material_assets=MaterialAssetStore(connect))
    scope = SimpleNamespace(material_asset_ids=lambda ws, task: {'asset'})
    actor = SimpleNamespace(workspace_id='workspace-a', user_id='owner-a', member_id='owner-a')
    def read(args):
        return read_goal_materials(production, scope, actor, 'task', 'read_material_evidence', args,
            material_facts=lambda value: value)
    with pytest.raises(TaskBusinessRejected):
        read({'asset_id': 'outside'})
    with pytest.raises(TaskOperationRejected) as conflict:
        read({'asset_id': 'asset', 'expected_updated_at': 'old'})
    assert conflict.value.kind == 'conflict'
    assert read({'asset_id': 'asset'})['asset_id'] == 'asset'


def test_frozen_material_reference_allows_reads_without_enrolling_library_and_rechecks_revision(database):
    from types import SimpleNamespace
    from app.assistant.material_reads import read_goal_materials
    from app.assistant.task_business_errors import TaskBusinessRejected, TaskOperationRejected
    connect, _ = database
    asset = _asset()
    with connect() as conn:
        _insert(conn, asset)
        _insert(conn, _asset('outside'))
    store = MaterialAssetStore(connect)
    production = SimpleNamespace(material_assets=store)
    authorization = SimpleNamespace(material_asset_ids=lambda ws, task: frozenset())
    actor = SimpleNamespace(workspace_id='workspace-a', user_id='owner-a', member_id='owner-a')
    sealed = {'selected_references': [{'kind': 'material', 'selection': {'scope': 'asset',
        'asset_id': asset.id, 'expected_updated_at': asset.updated_at.isoformat()}}]}
    def read(key):
        return read_goal_materials(production, authorization, actor, 'task', 'read_material_evidence',
            {'asset_id': key}, material_facts=lambda value: value, sealed=sealed)
    assert read('asset')['asset_id'] == 'asset'
    with pytest.raises(TaskBusinessRejected):
        read('outside')
    sealed['selected_references'][0]['selection']['expected_updated_at'] = '2020-01-01T00:00:00+00:00'
    with pytest.raises(TaskOperationRejected) as conflict:
        read('asset')
    assert conflict.value.kind == 'conflict'
    sealed['selected_references'][0]['selection']['expected_updated_at'] = asset.updated_at.isoformat()
    actor.user_id = 'revoked-reader'
    with pytest.raises(KeyError):
        read('asset')
