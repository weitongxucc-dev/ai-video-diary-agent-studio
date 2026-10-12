from datetime import datetime, timezone
from fastapi import HTTPException
import pytest
import app.main as main
from app.creation_execution import CreationExecutionActor
from app.production import ProductionRepository, VideoTemplate
from app.source_repository import SourceRepository
from fastapi.testclient import TestClient
from test_production_foundation import _repository, _seed_creation_plan_facts, _missing_model_catalog, plan_owner as plan_owner


@pytest.fixture
def automation_state(tmp_path,monkeypatch,plan_owner):
    production=_repository(tmp_path)
    sources=SourceRepository(production.database_path)
    actor=CreationExecutionActor('local',plan_owner[0].member_id)
    _seed_creation_plan_facts(production,sources,workspace_id=actor.workspace_id,verified_owner_user_id=actor.member_id)
    template=production.save_video_template(VideoTemplate(id='template-automation',workspace_id='local',version=1,name='真实测试模板',origin='user',settings={'voice':{'profile_id':'voice-local','version':'v1'},'presenter_share_target':0,'lipsync_quality':'fast'}),verified_owner_user_id=actor.member_id)
    monkeypatch.setattr(main,'production_repo',production)
    monkeypatch.setattr(main,'source_repository',sources)
    monkeypatch.setattr(main,'model_catalog',_missing_model_catalog(tmp_path))
    monkeypatch.setattr(main.creation_execution,'repository',production)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    client=TestClient(main.app,headers=plan_owner[1])
    monkeypatch.setattr(main,'automation_runtime',None)
    monkeypatch.setattr(main,'_topic_route_snapshot',lambda ws:{'availability':'unconfigured'})
    asset=next(item for item in production.list_assets('local',viewer_user_id=actor.member_id) if item.file_name=='local.mp4')
    for segment in asset.segments:
        segment.description='梳理真实信息源'
    production._save_asset(asset)
    payload={'name':'夜间整理','goal':'梳理真实信息源','template_id':template.id,'template_version':template.version,'trigger':{'cadence':'once','timezone':'Asia/Shanghai','start_local':'2026-10-10T00:00:00'},'source_scope':{'connector_ids':['connector-local'],'knowledge_ids':['knowledge-local'],'material_asset_ids':[asset.id]}}
    return production,template,client,actor,payload


def create_and_due(client,actor,payload):
    created=client.post('/api/automations',json=payload)
    assert created.status_code==200,created.text
    main._automation_runtime().service.tick(now=datetime(2026,10,10,14,tzinfo=timezone.utc))
    response=client.get(f"/api/automations/{created.json()['id']}/runs")
    assert response.status_code==200,response.text
    return created.json(),response.json()['items'][0]


def test_actual_router_fixed_inputs_missing_model_persist_and_restart(automation_state,monkeypatch):
    production,template,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    assert run['state']=='blocked' and run['reason_code']=='capability_unavailable'
    assert run['task_id'] is None and run['input_snapshot']['source_run_ids']==[]
    assert 'actor' not in definition
    runtime=main._automation_runtime()
    runtime.service.tick(now=datetime(2026,10,10,1,tzinfo=timezone.utc))
    monkeypatch.setattr(main,'automation_runtime',None)
    reopened=client.get(f"/api/automations/{definition['id']}/runs").json()['items']
    assert len(reopened)==1 and reopened[0]['id']==run['id']
    assert client.get('/api/automations/options').json()['driver']['running'] is False
    main.start_automation_due_driver()
    assert main._automation_runtime().thread.is_alive()
    main.stop_automation_due_driver()
    assert not runtime.thread


def test_actual_plan_confirm_same_transaction_replay_and_input_mismatch(automation_state):
    production,template,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    preview=client.post(f"/api/automations/runs/{run['id']}/preview")
    assert preview.status_code==200,preview.text
    assert preview.json()['approved_asset_count']==1
    assert preview.json()['confirmable'],preview.json()['blockers']
    fp=preview.json()['preflight_fingerprint']
    confirmed=client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp})
    assert confirmed.status_code==200,confirmed.text
    task_id=confirmed.json()['creation_task']['id']
    plan_id=confirmed.json()['id']
    repeated=client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp})
    assert repeated.status_code==200 and repeated.json()['creation_task']['id']==task_id
    assert client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':'0'*64}).status_code==409
    with production._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_tasks WHERE id=?',(task_id,)).fetchone()[0]==1
        persisted=conn.execute('SELECT plan_id,task_id FROM automation_runs WHERE id=?',(run['id'],)).fetchone()
    assert tuple(persisted)==(plan_id,task_id)
    fresh=ProductionRepository(production.database_path,production.video_library_path)
    assert fresh.get_creation_task(actor.workspace_id,task_id).plan_id==plan_id


def test_atomic_confirm_rollback_does_not_leave_partial_plan_or_run_binding(automation_state,monkeypatch):
    production,template,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    insert=production._insert_confirmed_creation_plan
    def interrupted(conn,plan,task):
        insert(conn,plan,task)
        raise ValueError('crash before binding')
    monkeypatch.setattr(production,'_insert_confirmed_creation_plan',interrupted)
    assert client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp}).status_code==409
    with production._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_plans').fetchone()[0]==0
        assert conn.execute('SELECT task_id FROM automation_runs WHERE id=?',(run['id'],)).fetchone()[0] is None
    monkeypatch.setattr(production,'_insert_confirmed_creation_plan',insert)
    assert client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp}).status_code==200


def test_paused_definition_no_next_occurrence_or_confirmation_and_cas(automation_state):
    _,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    paused=client.patch(f"/api/automations/{definition['id']}/pause",json={'paused':True,'expected_revision':definition['revision']})
    assert paused.status_code==200
    assert client.post(f"/api/automations/runs/{run['id']}/preview").status_code==409
    assert client.patch(f"/api/automations/{definition['id']}/pause",json={'paused':False,'expected_revision':definition['revision']}).status_code==409
    current=paused.json()
    assert client.patch(f"/api/automations/{definition['id']}/pause",json={'paused':False,'expected_revision':current['revision']}).status_code==200


def test_empty_scope_stays_empty_and_foreign_fixed_knowledge_rejected(automation_state):
    _,_,client,actor,payload=automation_state
    payload['source_scope']={'connector_ids':[],'knowledge_ids':[],'material_asset_ids':[]}
    definition,run=create_and_due(client,actor,payload)
    preview=client.post(f"/api/automations/runs/{run['id']}/preview")
    assert preview.status_code==200
    assert preview.json()['knowledge_references']==[] and preview.json()['source_references']==[]
    from app.schemas import CreationPlanPreviewRequest
    from starlette.requests import Request
    request=Request({'type':'http','headers':[(b'x-local-member-token',client.headers['x-local-member-token'].encode())]})
    with pytest.raises(HTTPException) as error:
        main._creation_plan_preflight(request,actor.workspace_id,CreationPlanPreviewRequest(goal='真实目标',selected_knowledge={'knowledge_item_id':'knowledge-local','version':'v1'}),automation_scope=payload['source_scope'])
    assert error.value.status_code==409


def test_run_identity_rechecks_current_permission(automation_state,monkeypatch):
    _,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    monkeypatch.setattr(main,'_verify_creation_identity',lambda actor:'unauthorized')
    assert client.get('/api/automations').status_code==403
    assert client.post(f"/api/automations/runs/{run['id']}/preview").status_code==403


def test_scope_is_filtered_before_high_scoring_other_assets_truncate_result(automation_state):
    from copy import deepcopy
    production,_,client,actor,payload=automation_state
    selected=production.get_asset(actor.workspace_id,payload['source_scope']['material_asset_ids'][0])
    for segment in selected.segments:
        segment.description='所选普通镜头'
        segment.system_tags=[]
        segment.user_tags=[]
    production._save_asset(selected)
    for index in range(10):
        other=deepcopy(selected)
        other.id=f'outside-{index}'
        from shutil import copyfile
        other.path=str(production.video_library_path/f'outside-{index}.mp4')
        other.file_name=f'outside-{index}.mp4'
        copyfile(selected.path,other.path)
        for segment in other.segments:
            segment.id=f'{other.id}-{segment.id}'
            segment.description=payload['goal']
            segment.system_tags=[payload['goal']]
        production._save_asset(other)
    ordinary=production.search_material_segments(actor.workspace_id,query=payload['goal'],usage_roles=['b_roll'],limit=8,viewer_user_id=actor.member_id)
    assert ordinary and all(match.asset_ref!=f'asset:{selected.id}' for match in ordinary)
    scoped=production.search_material_segments(actor.workspace_id,query=payload['goal'],usage_roles=['b_roll'],limit=8,asset_ids=[selected.id],viewer_user_id=actor.member_id)
    assert scoped and all(match.asset_ref==f'asset:{selected.id}' for match in scoped)
    assert production.search_material_segments(actor.workspace_id,usage_roles=['b_roll'],asset_ids=[],viewer_user_id=actor.member_id)==[]
    definition,run=create_and_due(client,actor,payload)
    preview=client.post(f"/api/automations/runs/{run['id']}/preview").json()
    assert preview['approved_asset_count']==1
    assert preview['material_matches'] and all(match['asset_ref']==f'asset:{selected.id}' for match in preview['material_matches'])


def test_confirmed_run_projects_current_missing_model_without_fake_execution(automation_state):
    production,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    assert client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp}).status_code==200
    main._automation_runtime().service.tick()
    projected=main._automation_runtime().service.get_run(actor,run['id'])
    assert projected['state']=='blocked' and projected['stage']=='topic' and projected['reason_code']=='capability_unavailable'
    assert projected['confirmation']['plan']['fingerprint']==fp
    assert projected['execution']['job_ids']==[] and projected['execution']['output_refs']==[]


def test_changed_real_material_version_blocks_run_until_explicit_input_retry(automation_state):
    production,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    asset=production.get_asset(actor.workspace_id,payload['source_scope']['material_asset_ids'][0])
    asset.user_tags=['用户已修改真实标签']
    production._save_asset(asset)
    assert client.post(f"/api/automations/runs/{run['id']}/preview").status_code==409
    blocked=main._automation_runtime().service.get_run(actor,run['id'])
    assert blocked['reason_code']=='inputs_changed' and blocked['task_id'] is None
    assert client.post(f"/api/automations/runs/{run['id']}/retry").status_code==200
    assert client.post(f"/api/automations/runs/{run['id']}/preview").status_code==200


@pytest.mark.parametrize('restriction',['paused','window'])
def test_real_topic_script_and_make_routes_block_before_any_generation(automation_state,monkeypatch,restriction):
    import app.automation_service as service_module
    production,_,client,actor,payload=automation_state
    if restriction=='window':
        payload['trigger'].update(start_local='2026-10-10T22:00:00',window_start='22:00',window_end='07:00')
        monkeypatch.setattr(service_module,'utc_now',lambda:datetime(2026,10,10,14,1,tzinfo=timezone.utc))
        import app.automation_runtime as runtime_module
        monkeypatch.setattr(runtime_module,'utc_now',lambda:datetime(2026,10,10,14,1,tzinfo=timezone.utc))
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    result=client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp})
    assert result.status_code==200,result.text
    task_id=result.json()['creation_task']['id']
    if restriction=='paused':
        assert client.patch(f"/api/automations/{definition['id']}/pause",json={'paused':True,'expected_revision':definition['revision']}).status_code==200
    else:
        monkeypatch.setattr(service_module,'utc_now',lambda:datetime(2026,10,10,23,tzinfo=timezone.utc))
    external=__import__('unittest.mock',fromlist=['Mock']).Mock(side_effect=AssertionError('must not call model'))
    monkeypatch.setattr(main,'_creation_task_topic_generator_for',external)
    monkeypatch.setattr(main,'_creation_task_script_generator_for',external)
    for suffix in ['topics/generate','scripts/generate','execution/resume']:
        response=client.post(f'/api/creation-tasks/{task_id}/{suffix}',json={})
        assert response.status_code==409,response.text
        assert response.json()['detail']['error']==('automation_paused' if restriction=='paused' else 'outside_window')
    external.assert_not_called()
    assert production.list_job_runs(actor.workspace_id,task_id)==[]
    assert main._automation_runtime().service.get_run(actor,run['id'])['state']=='blocked'


def test_publishing_guard_preserves_read_history_and_checks_original_run_member(automation_state):
    from fastapi import FastAPI
    from unittest.mock import Mock
    from types import SimpleNamespace
    from app.automation_routes import build_automation_guarded_publish_router,guard_automation_task
    from app.platform_publish.routes import build_platform_publish_router
    production,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    confirmed=client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp})
    task_id=confirmed.json()['creation_task']['id']
    runtime=main._automation_runtime()
    current_actor=[actor]
    external=Mock(side_effect=AssertionError('must not access platform browser'))
    original=build_platform_publish_router(production,workspace_for=lambda request:actor.workspace_id,content_reader=lambda request,ws:(current_actor[0].member_id,False),actor_for=lambda request,ws:current_actor[0],auth_service=SimpleNamespace(sessions=SimpleNamespace(invoke=external)),task_for_user=lambda ws,id,user:production.get_creation_task(ws,id),publishable_output_for=external,runtime_for=lambda:None)
    app=FastAPI()
    app.include_router(build_automation_guarded_publish_router(original,guard=lambda request,id:guard_automation_task(runtime,actor.workspace_id,id,lambda:current_actor[0])))
    publishing=TestClient(app)
    runtime.service.pause(actor,definition['id'],True,definition['revision'])
    assert publishing.get(f'/api/publishing/tasks/{task_id}/jobs').status_code==200
    body={'platform':'douyin','account_ref':'chosen-account','timeline_version_id':'actual-version','title':'明确发布','description':'说明','topics':[],'idempotency_key':'explicit-key','confirmed':True}
    denied=publishing.post(f'/api/publishing/tasks/{task_id}/publish',json=body)
    assert denied.status_code==409 and denied.json()['detail']['error']=='automation_paused'
    other=main.local_members_service.add_member('另一个真实成员')
    current_actor[0]=CreationExecutionActor(actor.workspace_id,other.member_id)
    assert publishing.post(f'/api/publishing/tasks/{task_id}/publish',json=body).status_code==403
    external.assert_not_called()
    assert production.list_job_runs(actor.workspace_id,task_id)==[]


def test_publish_projection_distinguishes_queue_pause_and_actual_claim(automation_state):
    from app.production import JobRun
    production,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    result=client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp})
    task_id=result.json()['creation_task']['id']
    job=production.create_job_run(JobRun(id='queued-publish',workspace_id=actor.workspace_id,task_id=task_id,kind='platform_publish',idempotency_key='explicit-test-publish',input_digest='a'*64))
    runtime=main._automation_runtime()
    bound=runtime.service.get_run(actor,run['id'])
    queued=runtime.project_state(actor,bound)
    assert queued['state']=='blocked' and queued['reason_code']=='device_wait'
    runtime.service.pause(actor,definition['id'],True,definition['revision'])
    paused=runtime.project_state(actor,bound)
    assert paused['state']=='blocked' and paused['reason_code']=='automation_paused'
    # Persist a canonical claim fact; this checks projection only, never platform execution.
    production.claim_job_run(actor.workspace_id,job.id)
    running=runtime.project_state(actor,bound)
    assert running['state']=='running' and running['job_id']==job.id
