from types import SimpleNamespace

from app.materials.candidate_bundle import build_candidate_bundle
from app.materials.library_routes import MaterialLibraryHTTPAdapter


def test_candidate_bundle_projects_material_and_knowledge_without_promoting_unknowns():
    material = SimpleNamespace(
        asset_ref="asset:a1", segment_ref="segment:s1", score=0.72,
        evidence_ref_ids=["evidence-1"], needs_confirmation=True,
        time_range="0.0 – 2.0", reasons=["文字命中"], match_tier="text",
    )
    claim = SimpleNamespace(
        id="claim-1", knowledge_item_id="knowledge-1", knowledge_version="v2",
        evidence_refs=(), status="candidate", confidence=None, field_ref="model",
        value="待确认型号", unit=None, origin="model_inference",
    )
    bundle = build_candidate_bundle([material], [claim], knowledge_requested=True)
    assert bundle["schema"] == "candidate-bundle/v1"
    assert bundle["summary"] == {
        "total": 2, "material_candidates": 1, "knowledge_candidates": 1,
        "confirmed": 0, "needs_confirmation": 2,
    }
    first, second = bundle["candidates"]
    assert first["evidence"] == {"status": "available", "ref_ids": ["evidence-1"]}
    assert first["authorization"]["status"] == "needs_confirmation"
    assert first["source_revision"] == {"status": "unknown"}
    assert second["status"] == "candidate"
    assert second["source_revision"] == {"knowledge_version": "v2"}
    assert bundle["missing_domains"] == ["person", "voice"]


def test_candidate_bundle_keeps_confirmed_knowledge_distinct():
    claim = SimpleNamespace(
        id="claim-2", knowledge_item_id="knowledge-2", knowledge_version="v3",
        evidence_refs=({"source_ref": "page-1"},), status="confirmed", confidence=0.9,
        field_ref="capacity", value=10, unit="unit/min", origin="user_confirmation",
    )
    candidate = build_candidate_bundle(knowledge_claims=[claim])["candidates"][0]
    assert candidate["status"] == "confirmed"
    assert candidate["authorization"]["status"] == "confirmed"
    assert candidate["evidence"]["status"] == "available"


def test_material_candidate_projects_governed_asset_state_without_inventing_language():
    asset = SimpleNamespace(
        use_state="not_for_automation", visibility="private", owner_user_id="member-1",
        source_url="https://example.invalid/clip.mp4", media_type="video",
        metadata={"language": "zh"},
    )
    segment = SimpleNamespace(
        source_revision={"size_bytes": "42", "mtime_ns": "7"},
        analysis_version="analysis-1",
    )
    profile = SimpleNamespace(
        status="unavailable", metrics={"audio": {"status": "unavailable", "value": None}},
        profile_version="quality-v1", source_revision=segment.source_revision,
        unavailable_reasons=("没有真实质量测量器",),
    )
    match = SimpleNamespace(
        asset_ref="asset:a1", segment_ref="segment:s1", score=0.72,
        evidence_ref_ids=[], needs_confirmation=False,
        time_range="0.0 – 2.0", reasons=["文字命中"], match_tier="text",
    )
    candidate = build_candidate_bundle(
        [match], material_asset=asset, material_segment_for=lambda _item: segment,
        material_source_revision_for=lambda _item: segment.source_revision,
        material_quality_profile_for=lambda _item: profile,
    )["candidates"][0]
    assert candidate["quality"]["status"] == "unavailable"
    assert candidate["quality"]["metrics"]["audio"]["status"] == "unavailable"
    assert candidate["source_revision"] == segment.source_revision
    assert candidate["authorization"] == {
        "status": "not_for_automation", "use_state": "not_for_automation",
        "visibility": "private", "owner_known": True, "purpose": "manual",
    }
    assert candidate["language"] == {"status": "declared", "value": "zh"}
    assert candidate["details"]["analysis_version"] == "analysis-1"
    assert candidate["details"]["relevance_score"] == 0.72


def test_material_candidate_bundle_read_adapter_returns_revision_and_missing_domains():
    asset = SimpleNamespace(
        id="asset-1", visibility="shared", owner_user_id=None,
        updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
        modified_at=SimpleNamespace(isoformat=lambda: "2025-12-31T00:00:00+00:00"),
    )
    match = SimpleNamespace(
        asset_ref="asset:asset-1", segment_ref="segment:segment-1", score=0.4,
        evidence_ref_ids=[], needs_confirmation=True, time_range="0 – 2",
        reasons=["当前工作区已分析素材"], match_tier="structural",
    )
    records = SimpleNamespace(get_asset=lambda _workspace, _asset_id: asset)
    search = SimpleNamespace(search_material_segments=lambda *_args, **_kwargs: [match])
    adapter = MaterialLibraryHTTPAdapter(
        records_for=lambda: records, search_for=lambda: search,
        workspace_for=lambda _request: "workspace-1", reader_for=lambda _request, _workspace: ("member-1", False),
        visible=lambda *_args: True, write_guard=lambda _request: None,
        library_root=lambda: ".", asset_response=lambda value: value,
    )
    result = adapter.candidate_bundle(SimpleNamespace(), "asset-1")
    assert result["schema"] == "candidate-bundle/v1"
    assert result["candidates"][0]["source_revision"]["asset_updated_at"].startswith("2026-")
    assert result["missing_domains"] == ["knowledge", "person", "voice"]


def test_material_candidate_bundle_adapter_reads_current_quality_profile_and_clip_revision():
    segment = SimpleNamespace(
        id="segment-1", source_revision={"size_bytes": "42", "mtime_ns": "7"},
        analysis_version="analysis-1",
    )
    asset = SimpleNamespace(
        id="asset-1", visibility="shared", owner_user_id=None, use_state="available",
        media_type="video", source_url="", segments=[segment],
        updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
        modified_at=SimpleNamespace(isoformat=lambda: "2025-12-31T00:00:00+00:00"),
    )
    match = SimpleNamespace(
        asset_ref="asset:asset-1", segment_ref="segment:segment-1", score=0.4,
        evidence_ref_ids=[], needs_confirmation=False, time_range="0 – 2",
        reasons=["当前工作区已分析素材"], match_tier="structural",
    )
    profile = SimpleNamespace(
        status="observed", metrics={"clarity": {"status": "observed", "value": 0.9}},
        profile_version="quality-v1", source_revision=segment.source_revision,
        unavailable_reasons=(),
    )
    records = SimpleNamespace(
        get_asset=lambda _workspace, _asset_id: asset,
        list_material_quality_profiles=lambda _workspace, _asset_id: [profile],
    )
    search = SimpleNamespace(search_material_segments=lambda *_args, **_kwargs: [match])
    adapter = MaterialLibraryHTTPAdapter(
        records_for=lambda: records, search_for=lambda: search,
        workspace_for=lambda _request: "workspace-1", reader_for=lambda _request, _workspace: ("member-1", False),
        visible=lambda *_args: True, write_guard=lambda _request: None,
        library_root=lambda: ".", asset_response=lambda value: value,
    )
    result = adapter.candidate_bundle(SimpleNamespace(), "asset-1")
    candidate = result["candidates"][0]
    assert candidate["quality"]["status"] == "observed"
    assert candidate["source_revision"] == segment.source_revision
    assert candidate["authorization"]["status"] == "available"


def test_material_candidate_bundle_reads_visible_knowledge_claims_when_requested():
    asset = SimpleNamespace(
        id="asset-1", visibility="shared", owner_user_id=None,
        updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
        modified_at=SimpleNamespace(isoformat=lambda: "2025-12-31T00:00:00+00:00"),
    )
    claim = SimpleNamespace(
        id="claim-1", knowledge_item_id="knowledge-1", knowledge_version="v2",
        evidence_refs=(), status="candidate", confidence=None, field_ref="model",
        value="待确认型号", unit=None, origin="model_inference",
    )
    records = SimpleNamespace(get_asset=lambda _workspace, _asset_id: asset)
    search = SimpleNamespace(search_material_segments=lambda *_args, **_kwargs: [])
    adapter = MaterialLibraryHTTPAdapter(
        records_for=lambda: records, search_for=lambda: search,
        workspace_for=lambda _request: "workspace-1", reader_for=lambda _request, _workspace: ("member-1", False),
        visible=lambda *_args: True, write_guard=lambda _request: None,
        library_root=lambda: ".", asset_response=lambda value: value,
        knowledge_claims_for=lambda *_args: [claim],
    )
    result = adapter.candidate_bundle(SimpleNamespace(), "asset-1", knowledge_item_id="knowledge-1")
    assert result["summary"]["knowledge_candidates"] == 1
    assert result["candidates"][0]["source_revision"] == {"knowledge_version": "v2"}
    assert result["missing_domains"] == ["person", "voice"]

import pytest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.materials.library_routes import build_material_library_router
from app.materials.profile_candidates import ProfileCandidateReader
from app.profiles.contracts import Authorization, PersonProfile, VoiceProfile, ProfileReference
from app.source_policy import knowledge_item_readable


def profile_api():
    grant = Authorization('grant', 'a1', 'holder', ('voice', 'person_likeness'),
        ('narration', 'avatar_lipsync'), datetime.now(timezone.utc) - timedelta(days=1),
        None, '/private/evidence', 'active', 'workspace-1')
    person = PersonProfile('person-1', 'workspace-1', 'Person', 'p1', '/private/avatar', (), (),
        authorization=grant, reference=ProfileReference(asset_id='source-1'), owner_user_id='member-1')
    voice = VoiceProfile('voice-1', 'workspace-1', 'v1', 'zh', '/private/audio', 'private transcript',
        authorization=grant, reference=ProfileReference(asset_id='source-1'), owner_user_id='member-1')
    state = {'person': person, 'voice': voice, 'grant': grant, 'member': 'member-1'}
    def read(kind, ws, identity, version=None):
        fixed = person if kind == 'person' else voice
        if ws != fixed.workspace_id or identity != fixed.id or version not in {None, fixed.version}:
            raise KeyError(identity)
        return fixed if version else state[kind]
    repo = SimpleNamespace(
        profile_records=SimpleNamespace(get_person_profile=lambda *a: read('person', *a),
            get_versioned_voice_profile=lambda *a: read('voice', *a)),
        profile_readable=lambda p, user, diagnostic: knowledge_item_readable(user_id=user,
            local_diagnostic=diagnostic, visibility=p.visibility, owner_user_id=p.owner_user_id),
        current_profile_authorization=lambda p: state['grant'] if p.authorization else None)
    asset = SimpleNamespace(id='asset', visibility='shared', owner_user_id=None,
        updated_at=datetime.now(timezone.utc), modified_at=datetime.now(timezone.utc))
    adapter = MaterialLibraryHTTPAdapter(records_for=lambda: SimpleNamespace(get_asset=lambda *_: asset),
        search_for=lambda: SimpleNamespace(search_material_segments=lambda *a, **kw: []),
        workspace_for=lambda _: 'workspace-1', reader_for=lambda *_: (state['member'], False),
        visible=lambda *_: True, write_guard=lambda _: None, library_root=lambda: '.', asset_response=lambda a: a,
        profile_candidate_for=ProfileCandidateReader(repo))
    app = FastAPI()
    app.include_router(build_material_library_router(lambda: adapter, ('read_material_candidate_bundle',)))
    return TestClient(app), state


def test_selected_profiles_http_version_policy_and_no_private_media():
    client, state = profile_api()
    result = client.get('/api/production/materials/asset/candidate-bundle', params={
        'person_profile_id': 'person-1', 'person_version': 'p1',
        'voice_profile_id': 'voice-1', 'voice_version': 'v1'})
    assert result.status_code == 200
    bundle = result.json()
    assert bundle['missing_domains'] == ['knowledge']
    person, voice = bundle['candidates']
    assert person['source_revision']['profile_version'] == 'p1'
    assert voice['language'] == {'status': 'declared', 'value': 'zh'}
    assert all(p['status'] == 'candidate' and p['authorization']['status'] == 'policy_allowed'
               and p['details']['runtime_status'] == 'unknown' for p in (person, voice))
    assert '/private/' not in result.text and 'private transcript' not in result.text
    state['grant'] = replace(state['grant'], version='a2', status='revoked')
    revoked = client.get('/api/production/materials/asset/candidate-bundle',
        params={'voice_profile_id': 'voice-1', 'voice_version': 'v1'}).json()['candidates'][0]
    assert revoked['authorization']['status'] == 'unavailable'
    assert 'authorization_revoked' in revoked['authorization']['reasons']
    assert revoked['status'] == 'candidate'


@pytest.mark.parametrize('params', [
    {'voice_profile_id': 'voice-1'}, {'person_version': 'p1'},
    {'voice_profile_id': '', 'voice_version': 'v1'}])
def test_profile_selection_requires_explicit_pair(params):
    client, _ = profile_api()
    assert client.get('/api/production/materials/asset/candidate-bundle', params=params).status_code == 422


@pytest.mark.parametrize('change', ['member', 'withdrawn', 'foreign', 'version'])
def test_profile_selection_never_exposes_unreadable_or_missing_versions(change):
    client, state = profile_api()
    params = {'voice_profile_id': 'voice-1', 'voice_version': 'v1'}
    if change == 'member':
        state['member'] = 'other-member'
    elif change == 'withdrawn':
        state['voice'] = replace(state['voice'], owner_user_id='other-member')
    elif change == 'foreign':
        state['voice'] = replace(state['voice'], workspace_id='other-workspace')
    else:
        params['voice_version'] = 'missing'
    assert client.get('/api/production/materials/asset/candidate-bundle', params=params).status_code == 404


def test_profile_reader_reopens_versioned_store_and_current_revocation(tmp_path):
    from app.production import ProductionRepository
    database = tmp_path / 'profiles.sqlite'
    repo = ProductionRepository(database, tmp_path / 'library')
    grant = Authorization('grant', 'a1', 'owner', ('voice',), ('narration',),
        datetime.now(timezone.utc) - timedelta(days=1), None, 'evidence:owned', 'active', 'workspace-1')
    repo.save_authorization(grant)
    voice = VoiceProfile('voice', 'workspace-1', 'v1', 'zh', 'media://private', 'private text',
        authorization=grant, reference=ProfileReference(asset_id='asset'), owner_user_id='member-1')
    # Registration-owner persistence with synthetic metadata only, never user data.
    with repo._connect() as conn:
        repo.profile_records.save_received_voice(conn, voice)
    reader = ProfileCandidateReader(ProductionRepository(database, tmp_path / 'library'))
    first = reader('workspace-1', 'voice', 'voice', 'v1', 'member-1')
    assert first['authorization']['status'] == 'policy_allowed'
    repo.save_authorization(replace(grant, version='a2', status='revoked'))
    second = reader('workspace-1', 'voice', 'voice', 'v1', 'member-1')
    assert second['source_revision']['profile_version'] == 'v1'
    assert second['authorization']['status'] == 'unavailable'
    assert second['authorization']['grants'][0]['version'] == 'a2'
    with pytest.raises(KeyError):
        reader('other-workspace', 'voice', 'voice', 'v1', 'member-1')
    with pytest.raises(KeyError):
        reader('workspace-1', 'voice', 'voice', 'v1', 'other-member')


@pytest.mark.parametrize('field,value,reason', [
    ('use_state', 'excluded', 'profile_excluded'),
    ('reference_issue', 'historical_authorization_reference_missing', 'historical_authorization_reference_missing')])
def test_profile_unavailable_policy_keeps_candidate_and_unknown_runtime(field, value, reason):
    client, state = profile_api()
    state['voice'] = replace(state['voice'], **{field: value})
    response = client.get('/api/production/materials/asset/candidate-bundle',
        params={'voice_profile_id': 'voice-1', 'voice_version': 'v1'})
    assert response.status_code == 200
    candidate = response.json()['candidates'][0]
    assert candidate['status'] == 'candidate'
    assert candidate['authorization']['status'] == 'unavailable'
    assert reason in candidate['authorization']['reasons']
    assert candidate['details']['runtime_status'] == 'unknown'
