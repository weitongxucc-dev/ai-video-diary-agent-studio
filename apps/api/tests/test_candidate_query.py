"""Four-domain metadata discovery on temporary owner stores; no models or services."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from app.materials.candidate_query import CrossDomainCandidateQuery
from app.materials.contracts import MaterialAsset, MaterialSegment, material_usage_eligibility
from app.media_observations import SegmentUnderstanding
from app.source_repository import SourceRepository
from app.source_types import KnowledgeItem
from tests.test_profile_feedback import seed as profile_seed


def seed(tmp_path):
    repo, task, script, grant, voice = profile_seed(tmp_path)
    person = repo.profile_records.get_person_profile('workspace_a','person_1')
    with repo._connect() as conn:
        repo.profile_records._save_person_profile(conn,replace(person,version='v2',display_name='camera person'))
        repo.profile_records.save_received_voice(conn,replace(voice,version='v2',display_name='camera voice'))
    source = tmp_path / 'camera.mp4'
    source.write_bytes(b'synthetic opaque source metadata, no decoder')
    stat = source.stat()
    asset = repo._save_asset(MaterialAsset('asset','workspace_a','camera.mp4',str(source),stat.st_size,
        datetime.fromtimestamp(stat.st_mtime,timezone.utc),duration_seconds=4,usage_role='b_roll',
        analysis_status='ready',owner_user_id='member',segments=[MaterialSegment('segment','asset',0,4,
            description='camera scene',version=3,source_revision={'fixture':'revision-1'})]))
    sources=SourceRepository(tmp_path / 'sources.sqlite')
    knowledge=sources.create_knowledge_item_owned(KnowledgeItem('knowledge','workspace_a','text',
        'camera notes','camera technique',None,'owned','owned',None,'1'),verified_user_id='member')
    return repo,sources,task,asset,knowledge,grant


def search(repo,sources,**kwargs):
    return CrossDomainCandidateQuery(repo,sources).search('workspace_a',query='camera',user_id='member',**kwargs)


def test_four_domains_discovered_and_independently_bounded_without_promoting_permission(tmp_path):
    repo,sources,task,asset,_,_=seed(tmp_path)
    result=search(repo,sources,task_id=task,limit_per_domain=1)
    assert result['schema']=='candidate-discovery/v1'
    assert set(result['domains'])=={'material','knowledge','person','voice'}
    assert all(domain['status']=='matched' and domain['returned_count']==1 for domain in result['domains'].values())
    assert result['domains']['material']['candidates'][0]['readback']['segment_version']==3
    assert result['domains']['material']['candidates'][0]['readback']['version']==asset.updated_at.isoformat()
    voice=result['domains']['voice']['candidates'][0]
    assert voice['source_revision']['profile_version']=='v2'
    assert voice['feedback']['status']=='not_used'
    assert all(c['status']=='candidate' for domain in result['domains'].values() for c in domain['candidates'])
    assert result['domains']['knowledge']['candidates'][0]['authorization']['status']=='needs_confirmation'
    assert 'private transcript' not in json.dumps(result) and '/private/' not in json.dumps(result)


def test_hidden_members_and_workspaces_are_filtered_before_candidates_and_counts(tmp_path):
    repo,sources,_,_,_,_=seed(tmp_path)
    for domain in CrossDomainCandidateQuery(repo,sources).search('workspace_a',query='camera',user_id='other')['domains'].values():
        assert domain['candidates']==[] and domain['total_count']==0
    result=CrossDomainCandidateQuery(repo,sources).search('foreign',query='camera',user_id='member')
    assert all(domain['candidates']==[] for domain in result['domains'].values())


def test_explicit_goal_scope_never_searches_missing_domains_or_newer_profile_versions(tmp_path):
    repo,sources,task,_,_,_=seed(tmp_path)
    result=search(repo,sources,scope={})
    assert all(d['status']=='not_in_goal_scope' for d in result['domains'].values())
    query=CrossDomainCandidateQuery(repo,sources)
    scope={'person':[{'profile_id':'person_1','version':'v1'}]}
    result=query.search('workspace_a',query='person',user_id='member',task_id=task,scope=scope)
    assert result['domains']['person']['candidates'][0]['source_revision']['profile_version']=='v1'
    assert all(result['domains'][kind]['status']=='not_in_goal_scope' for kind in ('material','knowledge','voice'))
    with pytest.raises(KeyError):
        query.read('workspace_a',domain='person',object_id='person_1',version='v2',user_id='member',scope=scope)
    malformed=search(repo,sources,scope={'voice':[{'profile_id':'voice_1'}]})
    assert malformed['domains']['voice']['status']=='unknown'


def test_standalone_fixed_profile_and_knowledge_readback_need_no_material_asset(tmp_path):
    repo,sources,task,_,_,_=seed(tmp_path)
    query=CrossDomainCandidateQuery(repo,sources)
    for domain,identity,version in [('voice','voice_1','v1'),('person','person_1','v1'),('knowledge','knowledge','1')]:
        result=query.read('workspace_a',domain=domain,object_id=identity,version=version,user_id='member',task_id=task)
        assert result['schema']=='candidate-readback/v1'
        assert result['bundle']['schema']=='candidate-bundle/v1'
        assert len(result['candidates'])==1
    with pytest.raises(KeyError):
        query.read('workspace_a',domain='voice',object_id='voice_1',version='missing',user_id='member')


def test_material_readback_and_goal_segment_scope_do_not_rebind_changed_versions(tmp_path):
    repo,sources,_,asset,_,_=seed(tmp_path)
    query=CrossDomainCandidateQuery(repo,sources)
    scope={'material':[{'asset_id':'asset','segment_id':'segment','segment_version':3,
        'source_revision':{'fixture':'revision-1'}}]}
    assert search(repo,sources,scope=scope)['domains']['material']['returned_count']==1
    assert query.read('workspace_a',domain='material',object_id='asset',version=asset.updated_at.isoformat(),
        user_id='member',scope=scope)['bundle']['candidates'][0]['readback']['segment_version']==3
    scope['material'][0]['segment_version']=2
    assert search(repo,sources,scope=scope)['domains']['material']['status']=='unavailable'
    changed=repo.get_asset('workspace_a','asset')
    changed.user_description='a later edit'
    repo._save_asset(changed)
    with pytest.raises(KeyError):
        query.read('workspace_a',domain='material',object_id='asset',version=asset.updated_at.isoformat(),user_id='member')


def test_similar_product_candidates_preserve_original_nameplate_evidence(tmp_path, record_property):
    repo, sources, _, template, _, _ = seed(tmp_path)
    workspace = 'workspace_a'
    query = CrossDomainCandidateQuery(repo, sources)
    target_model = 'ZX-410'
    assets = {}
    evidence_ids = {}
    for identity, model, version, frame_time in (
        ('target', target_model, 3, 1.5), ('lookalike', 'ZX-401', 5, 2.5),
        ('excluded', target_model, 7, 3.5),
    ):
        source = tmp_path / f'{identity}.mp4'
        source.write_bytes(f'synthetic nameplate fixture: {identity}'.encode())
        stat = source.stat()
        revision = {'fixture': f'{identity}-revision'}
        segment = MaterialSegment(f'{identity}-clip', identity, frame_time - 0.5, frame_time + 0.5,
            description='Identical bench controller setup and panel demonstration',
            version=version, source_revision=revision,
            understanding=SegmentUnderstanding('ready', sample_times=[frame_time], ocr_status='ready',
                ocr_frames=[{'id': f'{identity}-frame', 'timestamp_seconds': frame_time, 'width': 64, 'height': 64,
                    'observations': [{'text': model, 'confidence': 1.0, 'box': [0, 0, 0.5, 0.5],
                                        'retrieval_eligible': True}]}]))
        asset = repo._save_asset(replace(template, id=identity, file_name=source.name, path=str(source),
            size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc), segments=[segment]))
        assets[identity] = asset
        evidence, _ = repo.material_data_governance.record_analysis_outputs(workspace, asset,
            analysis_version='synthetic-ocr-v1', source_revision=revision, source_digest=None,
            model_or_rule='synthetic-fixture', visual_index_status='ready', visual_index_version='fixture-v1',
            visual_index_coverage={})
        evidence_ids[identity] = next(item.id for item in evidence if item.segment_id == segment.id)

    excluded = repo.update_asset_use_state(workspace, 'excluded', 'excluded')
    result = query.search(workspace, query=f'{target_model} bench controller', user_id='member')
    hits = {item['asset_ref']: item for item in result['domains']['material']['candidates']}
    assert 'asset:target' in hits
    assert 'asset:excluded' not in hits
    record_property('discovery_matches', ', '.join(f"{ref} ({hit['details']['match_tier']})" for ref, hit in hits.items()))

    for identity, original_model in (('target', target_model), ('lookalike', 'ZX-401')):
        asset = assets[identity]
        segment = asset.segments[0]
        hit = hits.get(f'asset:{identity}')
        locator = hit['readback'] if hit else {
            'domain': 'material', 'object_id': asset.id, 'version': asset.updated_at.isoformat()}
        fixed = query.read(workspace, domain=locator['domain'], object_id=locator['object_id'],
            version=locator['version'], user_id='member')
        assert len(fixed['candidates']) == 1
        # The lexical search may recall a near match; its fixed candidate and evidence must stay its own.
        for candidate in (fixed['candidates'][0], hit):
            if candidate is None:
                continue
            assert candidate['status'] == 'candidate'
            assert candidate['authorization']['purpose'] == 'manual'
            assert candidate['asset_ref'] == f'asset:{identity}'
            assert candidate['ref'] == f'segment:{segment.id}'
            assert candidate['source_revision'] == segment.source_revision
            assert set(candidate['evidence']['ref_ids']).intersection(evidence_ids.values()) == {evidence_ids[identity]}
            locator = candidate['readback']
            assert (locator['object_id'], locator['version'], locator['segment_id'], locator['segment_version']) == (
                identity, asset.updated_at.isoformat(), segment.id, segment.version)
            assert locator['source_revision'] == segment.source_revision
        evidence = repo.material_assets.read_evidence(workspace, locator['object_id'], member_id='member',
            expected_updated_at=locator['version'], start_seconds=segment.start_seconds, end_seconds=segment.end_seconds)
        observation, = evidence['items']
        assert evidence['clock'] == 'original_source'
        assert (observation['id'], observation['start_seconds'], observation['end_seconds']) == (
            segment.id, segment.start_seconds, segment.end_seconds)
        frame, = observation['visual']['ocr_frames']
        assert (frame['timestamp_seconds'], frame['observations'][0]['text']) == (segment.start_seconds + 0.5, original_model)

    with pytest.raises(KeyError):
        query.read(workspace, domain='material', object_id=excluded.id,
            version=assets['excluded'].updated_at.isoformat(), user_id='member')
    current = query.read(workspace, domain='material', object_id=excluded.id,
        version=excluded.updated_at.isoformat(), user_id='member')
    assert current['candidates'] == []
    assert material_usage_eligibility(excluded, excluded.segments[0], purpose='automatic').status == 'restricted'


@pytest.mark.parametrize('change', ['reference_only', 'excluded', 'segment_excluded', 'source_range'])
def test_material_discovery_drops_ranked_candidate_changed_during_readback(tmp_path, monkeypatch, change):
    repo, sources, _, asset, _, _ = seed(tmp_path)
    workspace = 'workspace_a'
    original_search = repo.material_search.search_material_segments

    def search_then_change(*args, **kwargs):
        result = original_search(*args, **kwargs)
        assert len(result['matches']) == 1
        if change in {'reference_only', 'excluded'}:
            repo.update_asset_use_state(workspace, asset.id, change)
        elif change == 'segment_excluded':
            segment = asset.segments[0]
            repo.update_segment(workspace, asset.id, segment.id, usage_role='exclude',
                review_status=segment.review_status, description=segment.description, transcript=segment.transcript)
        else:
            current = repo.get_asset(workspace, asset.id)
            current.segments = [replace(current.segments[0], start_seconds=1)]
            repo._save_asset(current)
        return result

    monkeypatch.setattr(repo.material_search, 'search_material_segments', search_then_change)
    material = search(repo, sources)['domains']['material']
    assert material['candidates'] == []
    assert material['status'] == 'unknown'
    # The source remains available to the material library for management.
    assert repo.list_assets(workspace, viewer_user_id='member')[0].id == asset.id


def test_material_fixed_readback_does_not_mix_a_concurrent_asset_revision(tmp_path, monkeypatch):
    repo, sources, _, asset, _, _ = seed(tmp_path)
    workspace = 'workspace_a'
    version = asset.updated_at.isoformat()
    original_search = repo.material_search.search_material_segments

    def search_then_change(*args, **kwargs):
        result = original_search(*args, **kwargs)
        assert len(result['matches']) == 1
        current = repo.get_asset(workspace, asset.id)
        current.user_description = 'new description on a newer revision'
        repo._save_asset(current)
        return result

    monkeypatch.setattr(repo.material_search, 'search_material_segments', search_then_change)
    result = CrossDomainCandidateQuery(repo, sources).read(
        workspace, domain='material', object_id=asset.id, version=version, user_id='member')
    assert result['candidates'] == []
    assert result['status'] == 'unavailable'


def test_knowledge_historical_version_and_current_visibility_remain_owned(tmp_path):
    repo,sources,_,_,_,_=seed(tmp_path)
    sources.edit_knowledge_item('workspace_a','knowledge',expected_version='1',title='new notes',
        body='new contents',verified_user_id='member')
    scope={'knowledge':[{'knowledge_item_id':'knowledge','knowledge_version':'1'}]}
    result=search(repo,sources,scope=scope)['domains']['knowledge']
    assert result['returned_count']==1 and result['candidates'][0]['source_revision']['knowledge_version']=='1'
    sources.set_knowledge_item_use_state('workspace_a','knowledge','excluded')
    assert search(repo,sources,scope=scope)['domains']['knowledge']['status']=='unavailable'
    with pytest.raises(KeyError):
        CrossDomainCandidateQuery(repo,sources).read('workspace_a',domain='knowledge',object_id='knowledge',
            version='1',user_id='member')


def test_limit_counts_revocation_and_empty_unavailable_unknown_are_distinct(tmp_path):
    repo,sources,_,_,_,grant=seed(tmp_path)
    voice=repo.profile_records.get_versioned_voice_profile('workspace_a','voice_1')
    with repo._connect() as conn:
        for i in range(3):
            repo.profile_records.save_received_voice(conn,replace(voice,id=f'voice_extra_{i}'))
    domain=search(repo,sources,limit_per_domain=2)['domains']['voice']
    assert domain['total_count']==4 and domain['returned_count']==2 and domain['truncated'] is True
    repo.save_authorization(replace(grant,version='a2',status='revoked'))
    candidate=search(repo,sources)['domains']['voice']['candidates'][0]
    assert candidate['status']=='candidate' and candidate['authorization']['status']=='unavailable'
    empty=CrossDomainCandidateQuery(repo,sources).search('workspace_a',query='no-such-query',user_id='member')
    assert all(d['status']=='empty' for d in empty['domains'].values())
    assert CrossDomainCandidateQuery(repo,None).search('workspace_a',query='camera',user_id='member')['domains']['knowledge']['status']=='unavailable'


def test_task_scope_and_query_validation_apply_before_any_domain_output(tmp_path):
    repo,sources,task,_,_,_=seed(tmp_path)
    query=CrossDomainCandidateQuery(repo,sources)
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET visibility='private',payload=json_set(payload,'$.owner_user_id','other') WHERE id=?",(task,))
    with pytest.raises(KeyError):
        search(repo,sources,task_id=task)
    for args in ({'query':''},{'query':'x'*121},{'limit_per_domain':True},{'limit_per_domain':21},{'user_id':None},{'scope':{'voices':[]}}):
        options={'query':'camera','user_id':'member',**args}
        with pytest.raises(ValueError):
            query.search('workspace_a',**options)


def test_fixed_knowledge_claims_stay_at_requested_version_and_bounded(tmp_path):
    from app.materials.data_governance import KnowledgeClaim, SemanticFieldDefinition
    repo,sources,_,_,_,_=seed(tmp_path)
    repo.material_data_governance.upsert_field_definition(SemanticFieldDefinition(
        'field','workspace_a','camera.mode','Camera mode','text'))
    for version in ('1','2'):
        for i in range(3):
            repo.record_knowledge_claim(KnowledgeClaim(f'claim-{version}-{i}','workspace_a','knowledge',
                version,'camera.mode','camera',evidence_refs=({'source_ref':'owned-source'},),asserted_by='member'))
    candidates=search(repo,sources,limit_per_domain=1)['domains']['knowledge']['candidates']
    claims=candidates[0]['details']['claims_bundle']['candidates']
    assert len(claims)==1 and claims[0]['source_revision']['knowledge_version']=='1'
    assert candidates[0]['details']['claims_truncated'] is True
    fixed=CrossDomainCandidateQuery(repo,sources).read('workspace_a',domain='knowledge',
        object_id='knowledge',version='1',user_id='member')
    assert all(c['source_revision']['knowledge_version']=='1' for c in fixed['bundle']['candidates'])


def test_knowledge_cross_workspace_grant_and_revocation_use_original_owner(tmp_path):
    repo,sources,_,_,_,_=seed(tmp_path)
    query=CrossDomainCandidateQuery(repo,sources)
    grant=sources.grant_knowledge_item('workspace_a','knowledge','target',user_id='member')
    result=query.search('target',query='camera',user_id='recipient')
    assert result['domains']['knowledge']['returned_count']==1
    assert query.read('target',domain='knowledge',object_id='knowledge',version='1',
        user_id='recipient')['candidates'][0]['source_revision']['knowledge_version']=='1'
    sources.revoke_knowledge_grant('workspace_a','knowledge',grant.id,user_id='member')
    assert query.search('target',query='camera',user_id='recipient')['domains']['knowledge']['status']=='empty'
    with pytest.raises(KeyError):
        query.read('target',domain='knowledge',object_id='knowledge',version='1',user_id='recipient')


def test_search_locators_round_trip_including_unassigned_material(tmp_path):
    repo,sources,_,asset,_,_=seed(tmp_path)
    asset.usage_role='unassigned'
    repo._save_asset(asset)
    query=CrossDomainCandidateQuery(repo,sources)
    for domain in search(repo,sources)['domains'].values():
        for candidate in domain['candidates']:
            locator=candidate['readback']
            result=query.read('workspace_a',domain=locator['domain'],object_id=locator['object_id'],
                version=locator['version'],user_id='member')
            assert result['candidates'] and result['version']==locator['version']
