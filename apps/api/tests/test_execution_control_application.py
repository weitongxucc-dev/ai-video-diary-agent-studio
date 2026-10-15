"""One metadata-only public execution-control flow; no stage dispatch or media work."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
from types import GeneratorType, SimpleNamespace

import psutil
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation.contracts import JobRun, TaskEvent
from app.creation_execution import CreationExecutionActor, PERSON_EDIT_KIND
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, MontageBudget, NarrationBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess, creation_job_registration
from app.production import CreationPlanVoiceReference
from app.task_audio import TASK_MEDIA_KIND
from _media_preview_test_fixtures import register_synthetic_profiles
from test_creation_execution import _seed


def test_public_execution_control_reuses_queue_and_preserves_query_boundaries(tmp_path, monkeypatch):
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local')
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), '执行控制 normal 合成成员')
    person, voice, *_ = register_synthetic_profiles(repo, workspace_id='local', member_id=member.member_id,
        person_id='person_1', voice_id='voice_1', person_version='v1', voice_version='v1', language='zh',
        model_id='declared-control-voice', person_authorization_id='person_auth_1', voice_authorization_id='voice_auth_1',
        voice_usage_scopes=('narration',))
    plan = repo.get_creation_plan('local', 'plan_local')
    plan.presenter_share_target = 0
    plan.selected_voice = CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version, voice.authorization.id, voice.authorization.version, 'zh-CN', voice.model_id)
    plan.owner_user_id = member.member_id
    task = repo.get_creation_task('local', task_id)
    task.owner_user_id = member.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(asdict(plan), default=str), plan.id))
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'creation_execution', main._new_creation_execution(repo))
    # Exact original narration-application values, with no resource admission/dispatch here.
    budget=NarrationBudget(1000,10,1_000_000,300,8_000_000,1,90,1024**3,16,.1,1024**3,1_000_000)
    montage_budget=MontageBudget(10,20,1_000_000,1_000_000,30,1_000_000)
    host_budget=HostBudget(100,1,100*1024**3,1,10**12,10_000_000,narration=budget,montage=montage_budget)
    swap=psutil.swap_memory();disk=psutil.disk_io_counters();disk_devices=psutil.disk_io_counters(perdisk=True)
    monkeypatch.setattr(psutil,'cpu_percent',lambda *args,**kwargs:0.0)
    monkeypatch.setattr(psutil,'swap_memory',lambda:swap._replace(used=0))
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:disk_devices if kwargs.get('perdisk') else disk)
    resources=HostResourceProbe(repo.database_path,disk_paths=(tmp_path,),budget=host_budget)
    registration=creation_job_registration(repo,render_action=main._render_creation_stage_with_grant,
        render_is_valid=main._creation_stage_valid_with_grant,verify_identity=main._verify_creation_identity,
        narration_budget=budget,montage_budget=montage_budget)
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'device.lock',
        device_access=MemberDeviceAccess(members,identity_verifier=main._verify_creation_identity),
        registrations={'creation_job':registration},resources=resources,processes=HostProcessProbe())
    monkeypatch.setattr(main,'device_runtime',runtime)
    control=main._creation_execution_control()
    assert control.repository is control.execution.repository is control.render_stage.repository is repo
    assert control.execution is main.creation_execution and control.verify_identity is main._verify_creation_identity
    client=TestClient(main.app,headers={'X-Local-Member-Token':token})
    actor=CreationExecutionActor('local',member.member_id)
    url='/api/creation-tasks/'+task_id
    evidence={}
    try:
        preview=client.get(url+'/execution/preview')
        assert preview.status_code==200 and preview.json()['requirements']['can_continue'],preview.text
        request={'script_id':script_id,'input_digest':preview.json()['input_digest'],'requirements_confirmed':True}
        first=client.post(url+'/execution/start',json=request)
        assert first.status_code==202,first.text
        first_job=first.json()['job']['id']
        duplicate=client.post(url+'/execution/start',json=request)
        assert duplicate.status_code==202 and duplicate.json()['job']['id']==first_job,duplicate.text
        rows=repo.list_creation_job_request_states('local',first_job)
        assert len(rows)==1 and isinstance(rows[0],sqlite3.Row) and set(rows[0].keys())=={'id','state'}
        request_id=rows[0]['id']
        assert rows[0]['state']=='waiting'
        assert repo.list_creation_job_request_states('other-workspace',first_job)==[]
        wait=repo.creation_execution_wait_row('local',member.member_id,first_job,request['input_digest'])
        assert isinstance(wait,sqlite3.Row) and set(wait.keys())=={'unit_key','wait_reason','updated_at'}
        assert wait['unit_key'].startswith('narration:')
        for args in [('other-workspace',member.member_id,first_job,request['input_digest']),
                     ('local','other-member',first_job,request['input_digest']),('local',member.member_id,first_job,'other-digest')]:
            assert repo.creation_execution_wait_row(*args) is None
        assert [r['id'] for r in repo.active_creation_request_ids('local',first_job)]==[request_id]
        owner_row=repo.creation_job_for_device_request_row('local',request_id)
        assert isinstance(owner_row,sqlite3.Row) and owner_row['owner_id']==first_job
        assert repo.creation_job_for_device_request_row('other-workspace',request_id) is None
        assert repo.creation_job_for_device_request_row('local','missing-request') is None
        assert main._creation_job_for_request('local',request_id).id==first_job
        bound=client.get(url+'/execution',params={'job_id':first_job})
        assert bound.status_code==200 and bound.json()['job']['id']==first_job,bound.text
        # Explicit projection fixture: interrupted durable job plus original historical running event.
        with repo._connect() as conn:
            conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=?",('local',first_job))
        repo.append_task_event(TaskEvent(id='declared-control-running-history',workspace_id='local',task_id=task_id,
            job_id=first_job,event_type='creation_execution_stage',payload={'stage':'narration','status':'running'}))
        interrupted=client.get(url+'/execution',params={'job_id':first_job})
        assert interrupted.status_code==200 and interrupted.json()['job']['status']=='interrupted',interrupted.text
        assert interrupted.json()['stages'][-1]['status']=='running'
        main._recover_creation_executions()
        assert len(repo.list_creation_job_request_states('local',first_job))==1
        local=repo.create_job_run(JobRun(id='declared-local-edit-job',workspace_id='local',task_id=task_id,
            kind=PERSON_EDIT_KIND,idempotency_key='declared-local-edit-job',input_digest=request['input_digest']))
        repo.append_task_event(TaskEvent(id=f'event_{local.id}_start',workspace_id='local',task_id=task_id,job_id=local.id,
            event_type='declared_local_start',payload={**actor.identity_payload(),'input_digest':local.input_digest}))
        local_query=client.get(url+'/execution',params={'job_id':local.id})
        assert local_query.status_code==409 and '身份或任务不可核验' in local_query.text,local_query.text
        cancelled=client.post(url+'/execution/cancel')
        assert cancelled.status_code==200 and cancelled.json()['job']['status']=='cancelled',cancelled.text
        assert repo.list_creation_job_request_states('local',first_job)[0]['state']=='cancelled'
        assert not repo.active_creation_request_ids('local',first_job)
        assert repo.list_cancelled_creation_execution_requests()==[]
        main._recover_creation_executions()
        resumed=client.post(url+'/execution/resume')
        assert resumed.status_code==202 and resumed.json()['job']['id']!=first_job,resumed.text
        second_job=resumed.json()['job']['id']
        assert client.post(url+'/execution/resume').json()['job']['id']==second_job
        assert len(repo.list_creation_job_request_states('local',second_job))==1
        assert client.get(url+'/execution',params={'job_id':second_job}).status_code==200
        assert client.get(url+'/execution',params={'job_id':first_job}).status_code==409
        main._recover_creation_executions()
        assert len(repo.list_creation_job_request_states('local',second_job))==1
        assert client.post(url+'/execution/cancel').status_code==200
        assert repo.get_job_run('local',second_job).status=='cancelled'
        assert repo.list_creation_job_request_states('local',second_job)[0]['state']=='cancelled'
        # Finite read-port fixtures below are not submitted work or worker-success substitutes.
        kind='declared-control-read-probe'
        for index in range(2):
            repo.create_job_run(JobRun(id=f'declared-read-{index}',workspace_id='local',task_id=task_id,kind=kind,
                idempotency_key=f'declared-read-{index}',input_digest='declared-read'))
        decoded=[]
        original_decoder=repo._job_run_from_row
        def observe_decoder(row):
            decoded.append(row['id'])
            return original_decoder(row)
        with monkeypatch.context() as observe:
            observe.setattr(repo,'_job_run_from_row',observe_decoder)
            snapshot=repo.list_pending_task_media_jobs(kind)
            assert isinstance(snapshot,GeneratorType) and decoded==[]
            first_decoded=next(snapshot)
            assert decoded==[first_decoded.id]
            repo.create_job_run(JobRun(id='declared-read-after-query',workspace_id='local',task_id=task_id,kind=kind,
                idempotency_key='declared-read-after-query',input_digest='declared-read'))
            remainder=list(snapshot)
            assert {first_decoded.id,*[j.id for j in remainder]}=={'declared-read-0','declared-read-1'}
        assert list(repo.list_pending_task_media_jobs(TASK_MEDIA_KIND))==[]
        with repo._connect() as conn:
            source=dict(conn.execute('SELECT * FROM device_work_requests WHERE id=?',(request_id,)).fetchone())
            for index in range(2):
                probe={**source,'id':f'declared-read-request-{index}','owner_id':'declared-read-0',
                    'input_digest':'declared-read','unit_key':f'declared-read-{index}',
                    'state':'waiting','created_at':f'2026-01-0{index+1}T00:00:00+00:00',
                    'updated_at':f'2026-01-0{index+1}T00:00:00+00:00'}
                columns=','.join(probe)
                conn.execute(f'INSERT INTO device_work_requests ({columns}) VALUES ({",".join("?" for _ in probe)})',tuple(probe.values()))
        latest=repo.creation_execution_wait_row('local',member.member_id,'declared-read-0','declared-read')
        assert latest['unit_key']=='declared-read-1'
        assert repo.list_cancelled_creation_execution_requests()==[]
        with repo._connect() as conn:
            conn.execute("DELETE FROM device_work_requests WHERE id IN ('declared-read-request-0','declared-read-request-1')")
            request_rows=[dict(r) for r in conn.execute('SELECT id,owner_id,state FROM device_work_requests').fetchall()]
            permits=[dict(r) for r in conn.execute('SELECT state,children_json FROM device_permit').fetchall()]
        assert len(request_rows)==2 and all(r['state']=='cancelled' for r in request_rows)
        assert not repo.list_person_renders('local',task_id) and not repo.list_transform_runs('local',task_id)
        assert not repo.list_timeline_versions('local',task_id)
        assert all(json.loads(row['children_json'])==[] for row in permits)
        evidence={'preview':preview.json(),'first':first.json(),'duplicate':duplicate.json(),'bound':bound.json(),
            'declared_interrupted_projection':interrupted.json(),'local_kind_query_status':local_query.status_code,
            'cancelled':cancelled.json(),'resumed':resumed.json(),'request_rows':request_rows,'permits':permits,
            'lazy_decoder_ids':decoded,'ordered_wait_fixture':dict(latest),'jobs':[asdict(j) for j in repo.list_job_runs('local',task_id)],
            'narration_budget':asdict(budget),'host_budget':asdict(host_budget),
            'OS_fixture':{'cpu_percent':0,'swap_used_bytes':0,'actual_swap_before':swap.used,'disk_io_baseline':disk._asdict(),
                'per_device_disk_io_baseline':{name:value._asdict() for name,value in disk_devices.items()}},
            'projection_and_query_fixtures':'Explicit interrupted/history event, rejected local kind and two read-only ordering probe rows; no real worker interruption, alternate actor or submitted probe work claimed.',
            'stage_dispatch_model_media_native':0,'business_guard_substitutes':0}
        destination=os.environ.get('EXECUTION_CONTROL_EVIDENCE_DIR')
        if destination:Path(destination,'NORMAL_SUMMARY.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str)+'\n')
    finally:
        assert runtime.stop(1)
