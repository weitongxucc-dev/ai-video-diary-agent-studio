from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.candidate_query_routes import build_candidate_query_router
from app.assistant.candidate_reads import goal_candidate_scope, read_goal_candidates
from app.assistant.task_business_errors import TaskBusinessRejected


def test_route_reads_use_member_identity_and_never_accept_client_scope():
    calls=[]
    case=SimpleNamespace(search=lambda workspace,**args:calls.append((workspace,args)) or {'schema':'candidate-discovery/v1'},
        read=lambda workspace,**args:calls.append((workspace,args)) or {'schema':'candidate-readback/v1'})
    app=FastAPI()
    def task_for(request,task_id):
        if task_id!='own':raise HTTPException(404)
    app.include_router(build_candidate_query_router(query_for=lambda:case,
        workspace_for=lambda request:'workspace',reader_for=lambda request,workspace:('member',False),task_for=task_for))
    client=TestClient(app)
    assert client.get('/api/creation-candidates/search?q=声音&limit_per_domain=2&scope=other').status_code==200
    assert calls[-1]==('workspace',{'query':'声音','user_id':'member','local_diagnostic':False,'limit_per_domain':2,'task_id':None})
    assert client.get('/api/creation-candidates/voice/voice-id?version=v2&task_id=own').status_code==200
    assert calls[-1][1]['version']=='v2'
    count=len(calls)
    assert client.get('/api/creation-candidates/search?q=x&task_id=other').status_code==404
    assert client.get('/api/creation-candidates/search?q=x&limit_per_domain=100').status_code==422
    assert client.get('/api/creation-candidates/voice/voice-id').status_code==422
    assert len(calls)==count


def test_goal_scope_uses_frozen_authorization_versions_and_selected_private_references():
    production=SimpleNamespace(task_authorization_records=SimpleNamespace(get_task_authorization=lambda *a:
        SimpleNamespace(resource_scope={'voices':['voice:v1'],'persons':[]})))
    authorizations=SimpleNamespace(material_asset_ids=lambda *a:{'asset'})
    actor=SimpleNamespace(workspace_id='workspace',member_id='member',user_id=None)
    sealed={'source_revisions':{'knowledge':[['entry','v3','hash','available','shared','owner']]},
        'selected_references':[{'kind':'person','profile_id':'person','version':'v2'}],
        'profile_reference':{'kind':'voice','profile_id':'voice','version':'v1'}}
    scope=goal_candidate_scope(production,authorizations,actor,'task',sealed)
    assert scope=={'material':[{'asset_id':'asset'}],
        'knowledge':[{'knowledge_item_id':'entry','knowledge_version':'v3'}],
        'person':[{'profile_id':'person','version':'v2'}], 'voice':[{'profile_id':'voice','version':'v1'}]}
    calls=[]
    case=SimpleNamespace(search=lambda workspace,**args:calls.append(args) or {})
    read_goal_candidates(case,production,authorizations,actor,'task',sealed,'search_creation_candidates',{'query':'声音'})
    assert calls[0]['scope']==scope and calls[0]['user_id']=='member' and calls[0]['task_id']=='task'
    with pytest.raises(TaskBusinessRejected):
        read_goal_candidates(case,production,authorizations,actor,'task',sealed,'search_creation_candidates',{'task_id':'other'})
    assert len(calls)==1


def test_goal_with_no_domain_references_does_not_become_unrestricted():
    production=SimpleNamespace(task_authorization_records=SimpleNamespace(get_task_authorization=lambda *a:
        SimpleNamespace(resource_scope={})))
    authorizations=SimpleNamespace(material_asset_ids=lambda *a:set())
    actor=SimpleNamespace(workspace_id='workspace',member_id='member',user_id=None)
    assert goal_candidate_scope(production,authorizations,actor,'task',{})=={
        'material':[],'knowledge':[],'person':[],'voice':[]}


def test_selected_segment_discovery_and_readback_do_not_expand_to_whole_asset(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.test_candidate_query import seed
    from app.materials.candidate_query import CrossDomainCandidateQuery
    repo,sources,task,asset,_,_=seed(tmp_path)
    original=asset.segments[0]
    asset.segments.append(replace(original,id='unselected',description='camera other scene'))
    asset=repo._save_asset(asset)
    original=asset.segments[0]
    actor=SimpleNamespace(workspace_id='workspace_a',member_id='member',user_id=None)
    authorizations=SimpleNamespace(material_asset_ids=lambda *a:set())
    sealed={'selected_references':[{'kind':'material','selection':{
        'asset_id':asset.id,'scope':'segment','segment_id':original.id,
        'expected_updated_at':asset.updated_at.isoformat(),
        'expected_segment_updated_at':original.updated_at.isoformat()}}]}
    monkeypatch.setattr(repo.task_authorization_records, 'get_task_authorization',
        lambda *a: SimpleNamespace(resource_scope={}))
    scope=goal_candidate_scope(repo,authorizations,actor,task,sealed)
    case=CrossDomainCandidateQuery(repo,sources)
    found=case.search('workspace_a',query='camera',user_id='member',scope=scope)
    read=case.read('workspace_a',domain='material',object_id=asset.id,
        version=asset.updated_at.isoformat(),user_id='member',scope=scope)
    assert [c['readback']['segment_id'] for c in found['domains']['material']['candidates']]==[original.id]
    assert [c['readback']['segment_id'] for c in read['candidates']]==[original.id]
