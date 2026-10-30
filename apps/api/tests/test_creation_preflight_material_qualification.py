"""CreationRequest prepares the same qualified material pairs it can confirm.

Real SQLite, member tokens, qualification, search, preflight and confirmation;
no model inference, media process, running app server or user data is involved.
"""
from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import app.main as main
from app.local_members import LocalMemberService
from app.production import MaterialAsset, MaterialSegment, ProductionRepository
from app.schemas import CreationPlanPreviewRequest
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem

WORKSPACE = 'local'
GOAL = '制作一条两分钟接口演示视频'


@pytest.fixture
def qualification_state(tmp_path, monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main, "_creation_task_script_generator_for", forbidden)
    library = tmp_path / 'library'
    library.mkdir()
    repository = ProductionRepository(tmp_path / 'isolated.sqlite', library)
    members = LocalMemberService(repository.database_path)
    owner, token = members.whoami(None)
    sources = SourceRepository(repository.database_path)
    sources.create_knowledge_item_owned(KnowledgeItem(
        id='knowledge', workspace_id=WORKSPACE, content_type='note', title='接口说明',
        body='隔离测试中已保存的接口说明。', file_reference=None, authorization='authorized',
        ownership='user_owned', source_item_id=None, version='v1'), verified_user_id=owner.member_id)
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'source_repository', sources)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: WORKSPACE)
    # The plan needs a model availability snapshot, never model execution.
    monkeypatch.setattr(main, 'model_catalog', SimpleNamespace(list_statuses=lambda: []))

    def add_asset(asset_id, *, role, use_state='available', registered=True, mixed=False):
        path = library / (asset_id + '.mp4')
        path.write_bytes(b'isolated-stat-qualified-video')
        segment = MaterialSegment(id=asset_id + ('-zzz-qualified' if mixed else '-segment'), asset_id=asset_id,
            start_seconds=0, end_seconds=2, description=GOAL, usage_role=role,
            suggested_usage_role='a_roll', usage_role_confidence=.95,
            review_status='raw', user_tags=['original-tag'])
        segments = [segment]
        if mixed:
            segments.extend(MaterialSegment(id=asset_id + f'-suggestion-{index}', asset_id=asset_id,
                start_seconds=2, end_seconds=4, description=GOAL, usage_role='unassigned',
                suggested_usage_role='a_roll', usage_role_confidence=.95, review_status='raw')
                for index in range(9))
        asset = MaterialAsset(id=asset_id, workspace_id=WORKSPACE, file_name=path.name,
            path=str(path), size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime), duration_seconds=4,
            # Keep the real role on the segment. Do not turn a suggestion into a user choice.
            usage_role='unassigned', review_status='raw', analysis_status='ready',
            use_state=use_state, segments=segments, owner_user_id=owner.member_id, visibility='private')
        repository._save_asset(asset)
        if registered:
            with repository._connect() as conn:
                repository._claim_workspace_media_reference(conn, workspace_id=WORKSPACE,
                    reference=repository.workspace_media_reference_for_asset(WORKSPACE, asset_id))
        return asset

    good_b = add_asset('valid-b-roll', role='b_roll')
    good_a = add_asset('valid-a-roll', role='a_roll')
    # More than the eight-result limit: post-filtering alone must not let these
    # consume the whole search window before qualified assets are considered.
    # Unassigned suggestions are valid under the current policy. Registration,
    # not the old role threshold, makes these real manual-only sources.
    bad = [add_asset(f'suggested-only-{index}', role='unassigned', registered=False) for index in range(9)]
    bad.append(add_asset('not-for-automation', role='b_roll', use_state='not_for_automation'))
    bad.append(add_asset('unregistered-source', role='b_roll', registered=False))
    app = FastAPI()
    for path, handler in [
        ('/api/creation-requests', next(route.endpoint for route in main.app.routes if route.name == "create_creation_request")),
        ('/api/creation-requests/{request_id}/windows', next(route.endpoint for route in main.app.routes if route.name == "preview_creation_request_window")),
        ('/api/creation-requests/{request_id}/windows/{window_id}/confirm', next(route.endpoint for route in main.app.routes if route.name == "confirm_creation_request_window")),
    ]:
        app.add_api_route(path, handler, methods=['POST'])

    # A small test route supplies the existing automation scope argument. The
    # preflight itself and its search/qualification are the actual implementation.
    @app.post('/scoped-preflight')
    def scoped_preflight(request: Request, payload: CreationPlanPreviewRequest):
        return asdict(main._creation_plan_preflight(request, WORKSPACE, payload, automation_scope={
            'knowledge_ids': ['knowledge'], 'connector_ids': [],
            'material_asset_ids': [asset.id for asset in bad],
        }))

    return SimpleNamespace(repository=repository, client=TestClient(app, headers={'X-Local-Member-Token': token}),
        owner=owner, good_a=good_a, good_b=good_b, bad=bad)


def material_rows(repository):
    with repository._connect() as conn:
        return [tuple(row) for row in conn.execute('SELECT * FROM material_assets ORDER BY id')]


@pytest.mark.parametrize('empty_query_result', [False, True], ids=['query', 'fallback'])
def test_prepare_confirm_freezes_only_actual_qualified_pairs(qualification_state, monkeypatch, empty_query_result):
    state = qualification_state
    repository = state.repository
    before = material_rows(repository)
    expected = {(state.good_a.id, state.good_a.segments[0].id), (state.good_b.id, state.good_b.segments[0].id)}
    assert {(asset.id, segment.id) for asset, segment in repository.automatic_material_segments(
        WORKSPACE, viewer_user_id=state.owner.member_id)} == expected
    manual = repository.material_search.search_material_segments(WORKSPACE, query=GOAL,
        usage_roles=['a_roll', 'b_roll'], limit=100, viewer_user_id=state.owner.member_id)
    # These really remain manually searchable; test data must exhibit the gap.
    assert any(item.asset_ref == 'asset:suggested-only-0' for item in manual)
    assert any(item.asset_ref == 'asset:not-for-automation' for item in manual)
    suggested_asset_ids = {f'suggested-only-{index}' for index in range(9)}
    assert suggested_asset_ids <= {item.asset_ref.removeprefix('asset:') for item in manual}
    automatic_asset_ids = {asset.id for asset, _ in repository.automatic_material_segments(
        WORKSPACE, viewer_user_id=state.owner.member_id)}
    assert suggested_asset_ids.isdisjoint(automatic_asset_ids)
    ordinary_limited = repository.material_search.search_material_segments(WORKSPACE, query=GOAL,
        asset_ids=[state.good_b.id, *sorted(suggested_asset_ids)], usage_roles=['a_roll', 'b_roll'], limit=8,
        viewer_user_id=state.owner.member_id)
    assert len(ordinary_limited) == 8
    assert all(item.segment_ref != 'segment:' + state.good_b.segments[0].id for item in ordinary_limited)
    scoped_matches = repository.material_search.search_material_segments(WORKSPACE, query=GOAL,
        asset_ids=[state.good_a.id, state.good_b.id], eligible_segment_refs=expected,
        usage_roles=['a_roll', 'b_roll'], limit=8, viewer_user_id=state.owner.member_id)
    assert {(item.asset_ref.removeprefix('asset:'), item.segment_ref.removeprefix('segment:'))
            for item in scoped_matches} == expected
    if empty_query_result:
        original_search = repository.material_search.search_material_segments
        def no_query_hits(workspace_id, **kwargs):
            if kwargs.get('query'):
                return []
            return original_search(workspace_id, **kwargs)
        monkeypatch.setattr(repository.material_search, "search_material_segments", no_query_hits)
    created = state.client.post('/api/creation-requests', json={
        'request_key': str(uuid4()), 'goal': GOAL, 'target_count': 1, 'output_relation': 'single',
        'duration_target_seconds': 120, 'presenter_share_target': 0,
    })
    assert created.status_code == 200, created.text
    saved = created.json()
    base = '/api/creation-requests/' + saved['id']
    prepared = state.client.post(base + '/windows', json={
        'expected_revision': saved['revision'], 'goals': [{'goal': GOAL}],
    })
    assert prepared.status_code == 200, prepared.text
    proposed = prepared.json()
    window = proposed['current_window']
    plan = window['entries'][0]['plan']
    assert repository.task_records.list_creation_tasks(WORKSPACE) == []
    confirmed = state.client.post(base + '/windows/' + window['id'] + '/confirm', json={
        'expected_revision': proposed['revision'], 'input_fingerprint': window['input_fingerprint'],
    })
    assert confirmed.status_code == 200, confirmed.text
    pairs = {(item['asset_ref'].removeprefix('asset:'), item['segment_ref'].removeprefix('segment:'))
             for item in plan['material_matches']}
    assert pairs == expected, plan['material_matches']
    assert plan['approved_asset_count'] == 2 and plan['approved_clip_count'] == 1
    assert not any(item['blocks_confirmation'] for item in plan['blockers'])
    assert confirmed.json()['confirmed_count'] == 1
    with repository._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM creation_plans').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM creation_tasks').fetchone()[0] == 1
    task = repository.task_records.list_creation_tasks(WORKSPACE)[0]
    persisted = repository.plan_records.get_creation_plan(WORKSPACE, task.plan_id)
    assert {(item.asset_ref.removeprefix('asset:'), item.segment_ref.removeprefix('segment:'))
            for item in persisted.material_matches} == expected
    assert material_rows(repository) == before


def test_scope_with_only_unqualified_materials_has_no_candidates_and_existing_blocker(qualification_state):
    state = qualification_state
    before = material_rows(state.repository)
    result = state.client.post('/scoped-preflight', json={
        'goal': GOAL, 'duration_target_seconds': 120, 'presenter_share_target': 0,
    })
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan['material_matches'] == []
    assert plan['approved_asset_count'] == 0 and plan['approved_clip_count'] == 0
    assert any(item['reason'] == 'approved_material_clips_missing' for item in plan['blockers'])
    assert state.repository.task_records.list_creation_tasks(WORKSPACE) == []
    assert material_rows(state.repository) == before
