from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
import pytest
import app.main as main
from app.llm_route_snapshot import WorkspaceLlmRouteSnapshot
from app.creation_execution import CreationExecutionActor
from app.creation.video_template_records import VideoTemplate
from app.production import ProductionRepository
from app.source_repository import SourceRepository
from fastapi.testclient import TestClient
from test_production_foundation import _repository, _seed_creation_plan_facts, _missing_model_catalog, plan_owner as plan_owner


@pytest.fixture
def automation_state(tmp_path,monkeypatch,plan_owner):
    production=_repository(tmp_path)
    sources=SourceRepository(production.database_path)
    actor=CreationExecutionActor('local',plan_owner[0].member_id)
    _seed_creation_plan_facts(production,sources,workspace_id=actor.workspace_id,verified_owner_user_id=actor.member_id)
    template=production.video_templates.save_video_template(VideoTemplate(id='template-automation',workspace_id='local',version=1,name='真实测试模板',origin='user',settings={'voice':{'profile_id':'voice-local','version':'v1'},'presenter_share_target':0,'lipsync_quality':'fast'}),verified_owner_user_id=actor.member_id)
    monkeypatch.setattr(main,'production_repo',production)
    monkeypatch.setattr(main,'source_repository',sources)
    monkeypatch.setattr(main,'model_catalog',_missing_model_catalog(tmp_path))
    monkeypatch.setattr(main.creation_execution,'repository',production)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    client=TestClient(main.app,headers=plan_owner[1])
    monkeypatch.setattr(main,'automation_runtime',None)
    monkeypatch.setattr(WorkspaceLlmRouteSnapshot,"snapshot",lambda adapter, ws:{'availability':'unconfigured'})
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
    assert fresh.task_records.get_creation_task(actor.workspace_id,task_id).plan_id==plan_id


def test_atomic_confirm_rollback_does_not_leave_partial_plan_or_run_binding(automation_state,monkeypatch):
    production,template,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    insert=production.plan_records.insert_confirmed_creation_plan
    def interrupted(conn,plan,task):
        insert(conn,plan,task)
        raise ValueError('crash before binding')
    monkeypatch.setattr(production.plan_records,'insert_confirmed_creation_plan',interrupted)
    assert client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp}).status_code==409
    with production._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_plans').fetchone()[0]==0
        assert conn.execute('SELECT task_id FROM automation_runs WHERE id=?',(run['id'],)).fetchone()[0] is None
    monkeypatch.setattr(production.plan_records,'insert_confirmed_creation_plan',insert)
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
        main._creation_plan_preflight_http_adapter().preflight(request,actor.workspace_id,CreationPlanPreviewRequest(goal='真实目标',selected_knowledge={'knowledge_item_id':'knowledge-local','version':'v1'}),automation_scope=payload['source_scope'])
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
    ordinary=production.material_search.search_material_segments(actor.workspace_id,query=payload['goal'],usage_roles=['b_roll'],limit=8,viewer_user_id=actor.member_id)
    assert ordinary and all(match.asset_ref!=f'asset:{selected.id}' for match in ordinary)
    scoped=production.material_search.search_material_segments(actor.workspace_id,query=payload['goal'],usage_roles=['b_roll'],limit=8,asset_ids=[selected.id],viewer_user_id=actor.member_id)
    assert scoped and all(match.asset_ref==f'asset:{selected.id}' for match in scoped)
    assert production.material_search.search_material_segments(actor.workspace_id,usage_roles=['b_roll'],asset_ids=[],viewer_user_id=actor.member_id)==[]
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
        response=client.post(f'/api/creation-tasks/{task_id}/{suffix}',
            json={'request_id': str(uuid4())} if suffix == 'scripts/generate' else {})
        assert response.status_code==409,response.text
        assert response.json()['detail']['error']==('automation_paused' if restriction=='paused' else 'outside_window')
    external.assert_not_called()
    assert production.job_records.list_job_runs(actor.workspace_id,task_id)==[]
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
    original=build_platform_publish_router(production,workspace_for=lambda request:actor.workspace_id,content_reader=lambda request,ws:(current_actor[0].member_id,False),actor_for=lambda request,ws:current_actor[0],auth_service=SimpleNamespace(sessions=SimpleNamespace(invoke=external)),task_for_user=lambda ws,id,user:production.task_records.get_creation_task(ws,id),publishable_output_for=external,runtime_for=lambda:None)
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
    assert production.job_records.list_job_runs(actor.workspace_id,task_id)==[]


def test_publish_projection_distinguishes_queue_pause_and_actual_claim(automation_state):
    from app.creation.contracts import JobRun
    production,_,client,actor,payload=automation_state
    definition,run=create_and_due(client,actor,payload)
    fp=client.post(f"/api/automations/runs/{run['id']}/preview").json()['preflight_fingerprint']
    result=client.post(f"/api/automations/runs/{run['id']}/confirm",json={'preflight_fingerprint':fp})
    task_id=result.json()['creation_task']['id']
    job=production.job_records.create_job_run(JobRun(id='queued-publish',workspace_id=actor.workspace_id,task_id=task_id,kind='platform_publish',idempotency_key='explicit-test-publish',input_digest='a'*64))
    runtime=main._automation_runtime()
    bound=runtime.service.get_run(actor,run['id'])
    queued=runtime.project_state(actor,bound)
    assert queued['state']=='blocked' and queued['reason_code']=='device_wait'
    runtime.service.pause(actor,definition['id'],True,definition['revision'])
    paused=runtime.project_state(actor,bound)
    assert paused['state']=='blocked' and paused['reason_code']=='automation_paused'
    # Persist a canonical claim fact; this checks projection only, never platform execution.
    production.job_records.claim_job_run(actor.workspace_id,job.id)
    running=runtime.project_state(actor,bound)
    assert running['state']=='running' and running['job_id']==job.id


def test_metadata_plan_binding_one_transaction_replay_and_fresh_read(tmp_path, monkeypatch):
    """Declared metadata ports; real scoped stores and original confirmation flow."""
    from dataclasses import asdict
    from datetime import timedelta
    import hashlib
    import json
    import os
    from pathlib import Path
    import sqlite3

    from app.automation_runtime import AutomationRuntime
    from app.automation_service import AutomationBlocked, AutomationCreate
    from app.creation.plan_contracts import CreationPlan

    database = tmp_path / 'binding.sqlite3'
    production = ProductionRepository(database, tmp_path / 'media')
    sources = SourceRepository(database)
    actor = CreationExecutionActor('metadata-workspace', 'metadata-member')
    template = production.video_templates.save_video_template(VideoTemplate(
        id='template-metadata-binding', workspace_id=actor.workspace_id, version=1,
        name='声明的元数据模板', origin='user', settings={'language': 'zh-CN'}),
        verified_owner_user_id=actor.member_id)
    fingerprint = 'a' * 64
    preflights = []

    def preflight(request, workspace_id, payload, *, automation_scope):
        assert request is None and workspace_id == actor.workspace_id
        assert (payload.template_id, payload.template_version) == (template.id, template.version)
        assert automation_scope == {'connector_ids': [], 'knowledge_ids': [], 'material_asset_ids': []}
        preflights.append('declared_metadata_only')
        return CreationPlan(
            id='preflight-only', workspace_id=workspace_id, goal=payload.goal, name=payload.name,
            target_count=1, language='zh-CN', aspect_ratio='9:16', duration_target_seconds=30,
            knowledge_references=[], source_references=[], selected_voice=None,
            pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
            material_rule_summary='声明空素材范围；不调用模型', model_snapshot=[],
            confirmation_points=[], blockers=[], preflight_fingerprint=fingerprint)

    def runtime_for(repository, source_repository):
        return AutomationRuntime(repository, source_repository,
            verify_identity=lambda value: 'ready' if value == actor else 'forbidden',
            list_templates=lambda value: repository.video_templates.list_video_templates(
                value.workspace_id, viewer_user_id=value.member_id),
            preflight=preflight, preview_response=asdict,
            plan_response=lambda plan, task: {'plan': asdict(plan), 'task': asdict(task)},
            execution_status=lambda *args: None, sync_connector=lambda *args: None,
            inspect_topic_route=lambda workspace_id: {'availability': 'ready'},
            execution_available=lambda: False)

    runtime = runtime_for(production, sources)
    assert runtime.service.database_path == production.database_path
    now = datetime.now(timezone.utc)
    definition = runtime.service.create(actor, AutomationCreate(
        name='单事务元数据确认', goal='仅检验持久绑定，不生成内容',
        template_id=template.id, template_version=template.version,
        trigger={'cadence': 'once', 'timezone': 'UTC',
                 'start_local': (now - timedelta(seconds=1)).replace(tzinfo=None)},
        source_scope={'connector_ids': [], 'knowledge_ids': [], 'material_asset_ids': []}))
    runtime.service.tick(now=now)
    runs = runtime.service.runs(actor, definition['id'])
    assert len(runs) == 1 and runs[0]['state'] == 'waiting_review'
    run = runs[0]
    revision_before = run['revision']
    connections, ordering = [], []
    connect = sqlite3.connect

    def observe_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        item = {'database': str(args[0]), 'keywords': dict(kwargs), 'sql': []}
        connections.append(item)
        connection.set_trace_callback(item['sql'].append)
        return connection

    prepare = production.plan_confirmation.prepare_confirmed_creation_plan
    def observe_prepare(*args, **kwargs):
        value = prepare(*args, **kwargs)
        ordering.append('prepared_before_bind')
        return value

    monkeypatch.setattr(sqlite3, 'connect', observe_connect)
    monkeypatch.setattr(production.plan_confirmation, 'prepare_confirmed_creation_plan', observe_prepare)
    result = runtime.confirm(None, actor, run['id'], fingerprint)
    bound = runtime.service.get_run(actor, run['id'])
    plan_id = 'plan_' + hashlib.sha256(run['id'].encode()).hexdigest()[:24]
    task_id = 'task_' + hashlib.sha256(run['id'].encode()).hexdigest()[:24]
    assert (result['plan']['id'], result['task']['id']) == (plan_id, task_id)
    assert (bound['plan_id'], bound['task_id'], bound['revision']) == (plan_id, task_id, revision_before + 1)
    assert bound['confirmation']['plan']['fingerprint'] == fingerprint
    transactions = [item for item in connections if 'BEGIN IMMEDIATE' in item['sql']]
    assert len(transactions) == 1 and transactions[0]['keywords'] == {}
    sql = transactions[0]['sql']
    plan_insert = next(i for i, query in enumerate(sql) if query.lstrip().startswith('INSERT INTO creation_plans '))
    task_insert = next(i for i, query in enumerate(sql) if query.lstrip().startswith('INSERT INTO creation_tasks '))
    run_update = next(i for i, query in enumerate(sql) if query.lstrip().startswith('UPDATE automation_runs '))
    assert sql.index('BEGIN IMMEDIATE') < plan_insert < task_insert < run_update < sql.index('COMMIT')
    assert ordering == ['prepared_before_bind'] and len(preflights) == 1
    # Pause via the actual public owner: require_open now rejects, but a bound
    # replay still returns its durable result before mutable checks/preflight.
    runtime.service.pause(actor, definition['id'], True, definition['revision'])
    with pytest.raises(AutomationBlocked):
        runtime.service.require_open(bound)
    assert runtime.confirm(None, actor, run['id'], fingerprint) == result
    with pytest.raises(ValueError, match='不同输入'):
        runtime.confirm(None, actor, run['id'], 'b' * 64)
    reopened = ProductionRepository(database, production.video_library_path)
    fresh = runtime_for(reopened, SourceRepository(database))
    assert fresh.confirm(None, actor, run['id'], fingerprint) == result
    persisted = fresh.service.get_run(actor, run['id'])
    assert persisted['revision'] == revision_before + 1
    assert len(preflights) == 1 and ordering == ['prepared_before_bind']
    with sqlite3.connect(database) as connection:
        counts = {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('creation_plans', 'creation_tasks', 'automation_runs', 'job_runs',
                                'topic_generation_runs', 'script_generation_runs')}
    assert counts == {'creation_plans': 1, 'creation_tasks': 1, 'automation_runs': 1,
                      'job_runs': 0, 'topic_generation_runs': 0, 'script_generation_runs': 0}
    assert runtime.thread is fresh.thread is None
    destination = os.environ.get('AUTOMATION_PLAN_BINDING_EVIDENCE_DIR')
    if destination:
        Path(destination, 'NORMAL_SUMMARY.json').write_text(json.dumps({
            'run_id': run['id'], 'plan_id': plan_id, 'task_id': task_id,
            'original_default_connection_keywords': transactions[0]['keywords'],
            'one_actual_connection_sql': sql, 'counts': counts,
            'run_revision_before': revision_before, 'run_revision_after': persisted['revision'],
            'preflight_calls': len(preflights), 'preparation_calls': len(ordering),
            'duplicate_and_fresh_read_match': True, 'paused_public_guard_rejected': True,
            'different_fingerprint_rejected': True, 'runtime_threads': 0,
            'fixture_boundary': 'Real scoped SQLite/repositories/runtime/service; declared actor verifier and metadata preflight, empty real input scope. No production identity/model/media/resource acceptance.',
            'late_failure_critical_gate_executed': False,
        }, ensure_ascii=False, indent=2) + '\n')
