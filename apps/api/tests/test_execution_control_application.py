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
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, MontageBudget, NarrationBudget, CpuAudioBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess, creation_job_registration
from app.creation.contracts import CreationPlanVoiceReference
from app.task_audio import TASK_MEDIA_KIND
from _media_preview_test_fixtures import register_synthetic_profiles
from test_creation_execution import _seed
import pytest


def _bounded_preview_case(tmp_path):
    from dataclasses import replace
    from app.creation.script_contracts import CreationTaskScriptSection
    from app.creation_execution import CreationExecution
    from app.creation.execution_control import CreationExecutionControl
    repo, task_id, script_id, _ = _seed(tmp_path)
    script = replace(repo.script_records.get_creation_task_script('workspace_a', script_id), sections=[
        CreationTaskScriptSection(f'part-{index}', f'段{index}', f'正文{index}🙂', '', []) for index in range(1, 46)])
    with repo._connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('BEGIN')
        payload = repo.script_records._script_payload(conn, script)
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?', (payload, script_id))
    def no_media(*args, **kwargs):
        raise AssertionError('this representative must not dispatch media')
    execution = CreationExecution(repo, run_montage_plan=no_media, run_render=no_media, render_is_valid=no_media)
    control = CreationExecutionControl(repo, execution, None, no_media)
    return repo, task_id, script_id, execution, control


def test_bounded_execution_preview_keeps_one_snapshot_during_a_tail_commit(tmp_path, monkeypatch):
    from app.creation_execution import revision_digest
    repo, task_id, script_id, execution, control = _bounded_preview_case(tmp_path)
    old_script = repo.script_records.get_creation_task_script('workspace_a', script_id)
    old_plan = repo.plan_records.get_creation_plan('workspace_a', 'plan_workspace_a')
    expected = revision_digest(old_script, old_plan)
    original = repo.script_records.iter_execution_script_sections
    def concurrent_tail_change(connection, workspace_id, selected_script_id):
        for index, section in enumerate(original(connection, workspace_id, selected_script_id)):
            if index == 0:
                with repo._connect() as writer:
                    row = writer.execute('SELECT section_json FROM creation_task_script_sections WHERE section_id=?', ('part-45',)).fetchone()
                    changed = json.loads(row['section_json'])
                    changed['content'] = '并发修改末段'
                    writer.execute('UPDATE creation_task_script_sections SET section_json=? WHERE section_id=?', (json.dumps(changed), 'part-45'))
                    row = writer.execute('SELECT payload FROM creation_task_scripts WHERE id=?', (script_id,)).fetchone()
                    meta = json.loads(row['payload'])
                    meta['updated_at'] = '2026-10-27T01:02:03+00:00'
                    writer.execute('UPDATE creation_task_scripts SET payload=?,updated_at=? WHERE id=?', (json.dumps(meta), meta['updated_at'], script_id))
            yield section
    def no_full_getter(*args, **kwargs):
        raise AssertionError('preview must not hydrate the complete script or use another plan connection')
    with monkeypatch.context() as patch:
        patch.setattr(repo.script_records, 'iter_execution_script_sections', concurrent_tail_change)
        patch.setattr(repo.script_records, 'get_creation_task_script', no_full_getter)
        patch.setattr(repo.plan_records, 'get_creation_plan', no_full_getter)
        patch.setattr(execution, 'validate_input', no_full_getter)
        preview = control.preview('workspace_a', task_id)
    assert preview['input_digest'] == expected
    assert preview['script_updated_at'] == old_script.updated_at.isoformat()
    assert preview['script_sections_total_count'] == 45 and preview['script_sections_loaded'] is False
    assert 'script_sections' not in preview
    latest = control.preview('workspace_a', task_id)
    assert latest['input_digest'] != expected
    assert latest['input_digest'] == revision_digest(repo.script_records.get_creation_task_script('workspace_a', script_id), old_plan)
    assert latest['script_updated_at'] == '2026-10-27T01:02:03+00:00'
    with pytest.raises(ValueError, match='文案或制作要求已变化'):
        execution.validate_input('workspace_a', repo.task_records.get_creation_task('workspace_a', task_id), script_id, expected)


def test_bounded_execution_preview_replays_the_original_job_and_keeps_full_validation(tmp_path):
    from app.creation_execution import revision_digest
    repo, task_id, script_id, execution, control = _bounded_preview_case(tmp_path)
    task = repo.task_records.get_creation_task('workspace_a', task_id)
    old_digest = revision_digest(repo.script_records.get_creation_task_script('workspace_a', script_id), repo.plan_records.get_creation_plan('workspace_a', task.plan_id))
    original = execution.start('workspace_a', task_id, script_id, old_digest)
    original_event = repo.execution_records.get_task_event('workspace_a', f'event_{original.id}_start')
    preview = control.preview('workspace_a', task_id)
    replay = execution.start('workspace_a', task_id, preview['script_id'], preview['input_digest'])
    assert replay.id == original.id and replay.input_digest == old_digest
    assert repo.execution_records.get_task_event('workspace_a', f'event_{original.id}_start') == original_event
    assert len(execution.jobs('workspace_a', task_id)) == 1
    script, plan = execution.validate_input('workspace_a', task, script_id, old_digest)
    assert len(script.sections) == 45 and script.sections[-1].id == 'part-45'
    assert plan.id == task.plan_id
    with repo._connect() as conn:
        conn.execute('BEGIN')
        assert repo.execution_inputs.read_creation_execution_snapshot('workspace_a', task_id, connection=conn).input_digest == old_digest
        assert conn.in_transaction  # caller owns its transaction
    with repo._connect() as conn:
        with pytest.raises(ValueError, match='已开启的事务'):
            repo.execution_inputs.read_creation_execution_snapshot('workspace_a', task_id, connection=conn)
    with repo._connect() as conn:
        row = conn.execute('SELECT payload FROM creation_plans WHERE id=?', (task.plan_id,)).fetchone()
        payload = json.loads(row['payload'])
        payload['duration_target_seconds'] += 1
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(payload), task.plan_id))
    with pytest.raises(ValueError, match='文案或制作要求已变化'):
        execution.validate_input('workspace_a', task, script_id, old_digest)


@pytest.mark.parametrize('table,field,value', [
    ('creation_tasks', 'confirmed_script_id', None),
    ('creation_task_scripts', 'task_id', 'another-task'),
    ('creation_task_scripts', 'selection_status', 'draft'),
    ('creation_plans', 'creation_task_id', 'another-task'),
    ('creation_plans', 'status', 'draft'),
])
def test_bounded_execution_preview_preserves_metadata_relationship_guards(tmp_path, table, field, value):
    from app.creation.execution_control import ExecutionControlRejected
    repo, task_id, script_id, _, control = _bounded_preview_case(tmp_path)
    with repo._connect() as conn:
        row = conn.execute(f'SELECT payload FROM {table}').fetchone()
        payload = json.loads(row['payload'])
        payload[field] = value
        conn.execute(f'UPDATE {table} SET payload=?', (json.dumps(payload),))
    with pytest.raises(ExecutionControlRejected) as error:
        control.preview('workspace_a', task_id)
    assert error.value.status_code == 409


def _declared_job_control_profiles(repo, member_id, monkeypatch):
    """Persist real owner/source/authorization facts; media inspection is a declared metadata port."""
    from datetime import datetime, timezone
    from app.materials.contracts import MaterialAsset
    from app.profiles.contracts import Authorization, PersonProfile, VoiceProfile, ProfileReference
    import app.materials.source_files as source_files

    sources = {}
    assets = {}
    for kind, filename, media_type in (("person", "person.png", "image"), ("voice", "voice.mp3", "audio")):
        path = repo.video_library_path / "declared-control" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("declared metadata bytes for " + kind).encode())
        stat = path.stat()
        asset = repo._save_asset(MaterialAsset(id="declared-" + kind, workspace_id="local", file_name=filename,
            path=str(path), size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime),
            media_type=media_type, duration_seconds=2 if kind == "voice" else None, owner_user_id=member_id))
        sources[path] = media_type
        assets[kind] = asset
    def declared_type(path):
        assert Path(path) in sources
        return sources[Path(path)]
    monkeypatch.setattr(source_files, '_probe_file_media_type', declared_type)
    def authorization(identifier, content_types, scopes):
        return Authorization(identifier, 'v1', 'declared member', content_types, scopes,
            datetime(2025,1,1,tzinfo=timezone.utc), None, 'fixture://declared-control/' + identifier, 'active', 'local')
    person = repo.save_person_profile(PersonProfile('person_1','local','declared person','v1',
        repo.workspace_media_reference_for_asset('local',assets['person'].id), ('studio',), ('medium',),
        authorization('person_auth_1',('person_likeness','avatar_video'),('avatar_lipsync',)),
        ProfileReference(asset_id=assets['person'].id)), verified_owner_user_id=member_id)
    voice = repo.save_versioned_voice_profile(VoiceProfile(id='voice_1',workspace_id='local',version='v1',language='zh',
        reference_audio_path=repo.workspace_media_reference_for_asset('local',assets['voice'].id),
        reference_text='declared reference', person_profile_id=person.id, person_version=person.version,
        model_id='declared-control-voice', display_name='declared voice', authorization=authorization('voice_auth_1',('voice',),('narration',)),
        reference=ProfileReference(asset_id=assets['voice'].id)), verified_owner_user_id=member_id)
    return person, voice


def test_public_execution_control_reuses_queue_and_preserves_query_boundaries(tmp_path, monkeypatch):
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local')
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), '执行控制 normal 合成成员')
    person, voice = _declared_job_control_profiles(repo, member.member_id, monkeypatch)
    plan = repo.plan_records.get_creation_plan('local', 'plan_local')
    plan.presenter_share_target = 0
    plan.selected_voice = CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version, voice.authorization.id, voice.authorization.version, 'zh-CN', voice.model_id)
    plan.owner_user_id = member.member_id
    task = repo.task_records.get_creation_task('local', task_id)
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
    budget=NarrationBudget(1000,10,1_000_000,300,8_000_000,1,90,1024**3,16,.1,1024**3,1_000_000,
        input_scope='single_call_experiment_v1')
    audio_budget=CpuAudioBudget(2,32*1024**2,30,30,48000,2,3*1024**2,1,30,1024,16,.1,2048,1024)
    montage_budget=MontageBudget(10,20,1_000_000,1_000_000,30,1_000_000)
    host_budget=HostBudget(100,1,100*1024**3,1,10**12,10_000_000,narration=budget,montage=montage_budget)
    swap=psutil.swap_memory();disk=psutil.disk_io_counters();disk_devices=psutil.disk_io_counters(perdisk=True)
    monkeypatch.setattr(psutil,'cpu_percent',lambda *args,**kwargs:0.0)
    monkeypatch.setattr(psutil,'swap_memory',lambda:swap._replace(used=0))
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:disk_devices if kwargs.get('perdisk') else disk)
    resources=HostResourceProbe(repo.database_path,disk_paths=(tmp_path,),budget=host_budget)
    registration=creation_job_registration(repo,render_action=main._render_creation_stage_with_grant,
        render_is_valid=main._creation_stage_valid_with_grant,verify_identity=main._verify_creation_identity,
        narration_budget=budget,narration_audio_budget=audio_budget,montage_budget=montage_budget)
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'device.lock',
        device_access=MemberDeviceAccess(members,identity_verifier=main._verify_creation_identity),
        registrations={'creation_job':registration},resources=resources,processes=HostProcessProbe())
    monkeypatch.setattr(main,'device_runtime',runtime)
    # Only supply the assembled runtime; this HTTP representative never starts its driver.
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(runtime,'start',lambda:pytest.fail('HTTP representative must not start a driver'))
    control=main._creation_execution_control()
    registration.owner.segments = registration.executor.segments = control.segments
    assert control.repository is control.execution.repository is control.render_stage.repository is repo
    assert control.execution is main.creation_execution and control.verify_identity is main._verify_creation_identity
    client=TestClient(main.app,headers={'X-Local-Member-Token':token})
    actor=CreationExecutionActor('local',member.member_id)
    url='/api/creation-tasks/'+task_id
    evidence={}
    try:
        with monkeypatch.context() as readonly:
            readonly.setattr(main, '_creation_runtime', lambda: pytest.fail('GET must not start runtime'))
            preview=client.get(url+'/execution/preview')
        assert preview.status_code==200 and preview.json()['requirements']['can_continue'],preview.text
        request={'script_id':script_id,'input_digest':preview.json()['input_digest'],'requirements_confirmed':True}
        first=client.post(url+'/execution/start',json=request)
        assert first.status_code==202,first.text
        first_job=first.json()['job']['id']
        duplicate=client.post(url+'/execution/start',json=request)
        assert duplicate.status_code==202 and duplicate.json()['job']['id']==first_job,duplicate.text
        rows=repo.job_records.list_creation_job_request_states('local',first_job)
        assert len(rows)==1 and isinstance(rows[0],sqlite3.Row) and set(rows[0].keys())=={'id','state','unit_key','wait_reason','last_failure_reason'}
        request_id=rows[0]['id']
        assert rows[0]['state']=='waiting'
        assert repo.job_records.list_creation_job_request_states('other-workspace',first_job)==[]
        wait=repo.job_records.creation_execution_wait_row('local',member.member_id,first_job,request['input_digest'])
        assert isinstance(wait,sqlite3.Row) and set(wait.keys())=={'unit_key','wait_reason','updated_at'}
        assert wait['unit_key'].startswith('narration:')
        for args in [('other-workspace',member.member_id,first_job,request['input_digest']),
                     ('local','other-member',first_job,request['input_digest']),('local',member.member_id,first_job,'other-digest')]:
            assert repo.job_records.creation_execution_wait_row(*args) is None
        assert [r['id'] for r in repo.job_records.active_creation_request_ids('local',first_job)]==[request_id]
        owner_row=repo.job_records.creation_job_for_device_request_row('local',request_id)
        assert isinstance(owner_row,sqlite3.Row) and owner_row['owner_id']==first_job
        assert repo.job_records.creation_job_for_device_request_row('other-workspace',request_id) is None
        assert repo.job_records.creation_job_for_device_request_row('local','missing-request') is None
        assert main._creation_execution_control().job_for_request('local',request_id).id==first_job
        bound=client.get(url+'/execution',params={'job_id':first_job})
        assert bound.status_code==200 and bound.json()['job']['id']==first_job,bound.text
        # Keep the current original Job and immutable start receipt. A new trusted
        # task owner may read the task, but cannot adopt the former actor's Job.
        other_member, other_token = members.join(members.create_invite_code(), '后续任务成员')
        original_task = repo.task_records.get_creation_task('local', task_id)
        changed_owner = asdict(original_task)
        changed_owner['owner_user_id'] = other_member.member_id
        with repo._connect() as conn:
            conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
                (json.dumps(changed_owner, default=str), task_id))
        try:
            assert main.creation_execution.latest('local', task_id).id == first_job
            identity_query = client.get(url+'/execution', params={'job_id':first_job},
                headers={'X-Local-Member-Token':other_token})
            assert identity_query.status_code == 409 and '身份或任务不可核验' in identity_query.text, identity_query.text
        finally:
            with repo._connect() as conn:
                conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
                    (json.dumps(asdict(original_task), default=str), task_id))
        # Explicit projection fixture: interrupted durable job plus original historical running event.
        with repo._connect() as conn:
            conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=?",('local',first_job))
        repo.execution_records.append_task_event(TaskEvent(id='declared-control-running-history',workspace_id='local',task_id=task_id,
            job_id=first_job,event_type='creation_execution_stage',payload={'stage':'narration','status':'running'}))
        interrupted=client.get(url+'/execution',params={'job_id':first_job})
        assert interrupted.status_code==200 and interrupted.json()['job']['status']=='interrupted',interrupted.text
        assert interrupted.json()['stages'][-1]['status']=='running'
        main._recover_creation_executions()
        assert len(repo.job_records.list_creation_job_request_states('local',first_job))==1
        local=repo.job_records.create_job_run(JobRun(id='declared-local-edit-job',workspace_id='local',task_id=task_id,
            kind=PERSON_EDIT_KIND,idempotency_key='declared-local-edit-job',input_digest=request['input_digest']))
        repo.execution_records.append_task_event(TaskEvent(id=f'event_{local.id}_start',workspace_id='local',task_id=task_id,job_id=local.id,
            event_type='declared_local_start',payload={**actor.identity_payload(),'input_digest':local.input_digest}))
        local_query=client.get(url+'/execution',params={'job_id':local.id})
        assert local_query.status_code==409 and '原制作作业已被新的制作请求替代' in local_query.text,local_query.text
        with monkeypatch.context() as cancelling:
            cancelling.setattr(main, '_creation_runtime', lambda: pytest.fail('cancel must not start runtime'))
            cancelled=client.post(url+'/execution/cancel')
        assert cancelled.status_code==200 and cancelled.json()['job']['status']=='cancelled',cancelled.text
        assert repo.job_records.list_creation_job_request_states('local',first_job)[0]['state']=='cancelled'
        assert not repo.job_records.active_creation_request_ids('local',first_job)
        assert repo.job_records.list_cancelled_creation_execution_requests()==[]
        main._recover_creation_executions()
        resumed=client.post(url+'/execution/resume')
        assert resumed.status_code==202 and resumed.json()['job']['id']!=first_job,resumed.text
        second_job=resumed.json()['job']['id']
        assert client.post(url+'/execution/resume').json()['job']['id']==second_job
        assert len(repo.job_records.list_creation_job_request_states('local',second_job))==1
        assert client.get(url+'/execution',params={'job_id':second_job}).status_code==200
        assert client.get(url+'/execution',params={'job_id':first_job}).status_code==409
        main._recover_creation_executions()
        assert len(repo.job_records.list_creation_job_request_states('local',second_job))==1
        assert client.post(url+'/execution/cancel').status_code==200
        assert repo.job_records.get_job_run('local',second_job).status=='cancelled'
        assert repo.job_records.list_creation_job_request_states('local',second_job)[0]['state']=='cancelled'
        # Finite read-port fixtures below are not submitted work or worker-success substitutes.
        kind='declared-control-read-probe'
        for index in range(2):
            repo.job_records.create_job_run(JobRun(id=f'declared-read-{index}',workspace_id='local',task_id=task_id,kind=kind,
                idempotency_key=f'declared-read-{index}',input_digest='declared-read'))
        decoded=[]
        original_decoder=repo.job_records.job_run_from_row
        def observe_decoder(row):
            decoded.append(row['id'])
            return original_decoder(row)
        with monkeypatch.context() as observe:
            observe.setattr(repo.job_records,'job_run_from_row',observe_decoder)
            snapshot=repo.job_records.list_pending_task_media_jobs(kind)
            assert isinstance(snapshot,GeneratorType) and decoded==[]
            first_decoded=next(snapshot)
            assert decoded==[first_decoded.id]
            repo.job_records.create_job_run(JobRun(id='declared-read-after-query',workspace_id='local',task_id=task_id,kind=kind,
                idempotency_key='declared-read-after-query',input_digest='declared-read'))
            remainder=list(snapshot)
            assert {first_decoded.id,*[j.id for j in remainder]}=={'declared-read-0','declared-read-1'}
        assert list(repo.job_records.list_pending_task_media_jobs(TASK_MEDIA_KIND))==[]
        with repo._connect() as conn:
            source=dict(conn.execute('SELECT * FROM device_work_requests WHERE id=?',(request_id,)).fetchone())
            for index in range(2):
                probe={**source,'id':f'declared-read-request-{index}','owner_id':'declared-read-0',
                    'input_digest':'declared-read','unit_key':f'declared-read-{index}',
                    'state':'waiting','created_at':f'2026-01-0{index+1}T00:00:00+00:00',
                    'updated_at':f'2026-01-0{index+1}T00:00:00+00:00'}
                columns=','.join(probe)
                conn.execute(f'INSERT INTO device_work_requests ({columns}) VALUES ({",".join("?" for _ in probe)})',tuple(probe.values()))
        latest=repo.job_records.creation_execution_wait_row('local',member.member_id,'declared-read-0','declared-read')
        assert latest['unit_key']=='declared-read-1'
        assert repo.job_records.list_cancelled_creation_execution_requests()==[]
        with repo._connect() as conn:
            conn.execute("DELETE FROM device_work_requests WHERE id IN ('declared-read-request-0','declared-read-request-1')")
            request_rows=[dict(r) for r in conn.execute('SELECT id,owner_id,state FROM device_work_requests').fetchall()]
            permits=[dict(r) for r in conn.execute('SELECT state,children_json FROM device_permit').fetchall()]
        assert len(request_rows)==2 and all(r['state']=='cancelled' for r in request_rows)
        assert not repo.person_renders.list_person_renders('local',task_id) and not repo.lineage_records.list_transform_runs('local',task_id)
        assert not repo.timeline_records.list_timeline_versions('local',task_id)
        assert all(json.loads(row['children_json'])==[] for row in permits)
        evidence={'preview':preview.json(),'first':first.json(),'duplicate':duplicate.json(),'bound':bound.json(),
            'declared_interrupted_projection':interrupted.json(),'local_kind_query_status':local_query.status_code,
            'current_original_job_identity_rejection':identity_query.json(),
            'cancelled':cancelled.json(),'resumed':resumed.json(),'request_rows':request_rows,'permits':permits,
            'lazy_decoder_ids':decoded,'ordered_wait_fixture':dict(latest),'jobs':[asdict(j) for j in repo.job_records.list_job_runs('local',task_id)],
            'narration_budget':asdict(budget),'host_budget':asdict(host_budget),
            'OS_fixture':{'cpu_percent':0,'swap_used_bytes':0,'actual_swap_before':swap.used,'disk_io_baseline':disk._asdict(),
                'per_device_disk_io_baseline':{name:value._asdict() for name,value in disk_devices.items()}},
            'projection_and_query_fixtures':'Explicit interrupted/history event, rejected local kind and two read-only ordering probe rows; no real worker interruption, alternate actor or submitted probe work claimed.',
            'stage_dispatch_model_media_native':0,'business_guard_substitutes':0}
        destination=os.environ.get('EXECUTION_CONTROL_EVIDENCE_DIR')
        if destination:Path(destination,'NORMAL_SUMMARY.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str)+'\n')
    finally:
        assert runtime.stop(1)


@pytest.mark.parametrize('change', ['tail', 'authorization'])
def test_bounded_narration_start_source_seal_rolls_back_preparation_race(tmp_path, monkeypatch, change):
    from dataclasses import replace
    from datetime import datetime, timezone
    from app.creation.contracts import JobRun
    from app.creation.narration_pcm import prepare_narration_source
    from app.device_scheduler import RecoveryBlocked
    from test_creation_execution import _bounded_narration_start_case
    case = _bounded_narration_start_case(tmp_path, monkeypatch)
    repo = case.repo
    snapshot = repo.execution_inputs.read_creation_execution_snapshot('local', case.task_id)
    case.owner.task_authorizations.ensure(snapshot.task, case.actor)
    original_seal = repo.seal_creation_narration_source
    observed = []
    def seal(job, prepared, **kwargs):
        conn = kwargs['connection']
        assert conn.in_transaction
        assert conn.execute('SELECT 1 FROM job_runs WHERE id=?', (job.id,)).fetchone() is not None
        observed.append(job.id)
        return original_seal(job, prepared, **kwargs)
    monkeypatch.setattr(repo, 'seal_creation_narration_source', seal)
    with prepare_narration_source(repo, snapshot.task, snapshot.plan, case.actor) as prepared:
        assert prepared['adoption_revision_digest'] == case.digest
        with repo._connect() as writer:
            if change == 'tail':
                row = writer.execute('SELECT section_json FROM creation_task_script_sections WHERE section_id=?', ('section_45',)).fetchone()
                value = json.loads(row['section_json']); value['content'] = '准备期间尾段发生变化'
                writer.execute('UPDATE creation_task_script_sections SET section_json=? WHERE section_id=?',
                    (json.dumps(value), 'section_45'))
            else:
                authorization = repo.task_authorization_records.get_task_authorization('local', case.task_id, connection=writer)
                repo.task_authorization_records.save_task_authorization(replace(authorization, revision=authorization.revision+1,
                    revoked_at=datetime.now(timezone.utc)), expected_revision=authorization.revision, connection=writer)
        with pytest.raises((ValueError, RecoveryBlocked), match='变化'):
            repo.creation_execution_jobs.create_creation_execution_job(JobRun('race-job', 'local', case.task_id, 'creation_task_execution',
                'race-job-key', case.digest), start_payload={'script_id': case.script_id, 'plan_id': snapshot.plan.id,
                    'input_digest': case.digest, **case.actor.identity_payload()}, source_preparation=prepared)
    assert observed == ['race-job']
    with repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type IN "
            "('creation_execution_started','creation_narration_source_bound')").fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == 0


def test_job_request_reads_keep_distinct_state_filters_rows_and_materialized_pending(tmp_path, monkeypatch):
    import socket
    import subprocess
    import threading
    import wave
    from datetime import datetime, timezone
    from PIL import Image
    from app.production import ProductionRepository
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationTask

    def forbidden(*args, **kwargs):
        raise AssertionError('job request metadata forbids execution, media and networking')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn'):
        monkeypatch.setattr(main, name, forbidden)
    repo = ProductionRepository(tmp_path / 'read-facts.sqlite3', tmp_path / 'library',
        duration_reader=forbidden, scene_reader=forbidden)
    def task(workspace):
        plan = CreationPlan('plan-' + workspace, workspace, 'fixed goal', 'fixed plan', 1, 'zh-CN', '9:16', 4,
            [], [], None, [], 0, 0, 'metadata', [], [], [], 'read-fixture')
        value = CreationTask('task-' + workspace, workspace, plan.id, 'fixed task', 'knowledge_explanation')
        repo.plan_confirmation.confirm_creation_plan(plan, value, verified_owner_user_id='metadata-member')
        return value
    local, foreign = task('metadata-ws'), task('foreign-ws')
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    def job(identity, current, status='queued'):
        value = JobRun(identity, current.workspace_id, current.id, 'creation_task_execution', identity, 'a' * 64,
            created_at=stamp, updated_at=stamp)
        repo.job_records.create_job_run(value)
        if status != 'queued':
            repo.job_records.claim_job_run(current.workspace_id, identity)
            repo.job_records.finish_job_run(current.workspace_id, identity, status=status,
                failure_reason='metadata state', impact='no execution', repair_action='review declared state')
        return value
    queued = job('pending-b', local)
    interrupted = job('pending-a', local, 'interrupted')
    cancelled = job('cancelled-local', local, 'cancelled')
    cancelled_foreign = job('cancelled-foreign', foreign, 'cancelled')
    def request(conn, identity, owner, state, day, *, member='metadata-member', digest=None, owner_type='creation_job'):
        time = f'2024-01-0{day}T00:00:00+00:00'
        conn.execute('INSERT INTO device_work_requests(id,workspace_id,member_id,owner_type,owner_id,input_digest,'
            'unit_key,source_refs_json,work_class,policy,first_ready_at_utc,state,resources_json,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (identity, owner.workspace_id, member, owner_type, owner.id,
            digest or owner.input_digest, 'unit-' + identity, '[]', 'normal_make', 'asap', time, state, '{}', time, time))
    with repo._connect() as conn:
        request(conn, 'z-wait', queued, 'waiting', 1)
        request(conn, 'a-wait', queued, 'waiting', 1)
        request(conn, 'yield-current', queued, 'yield_requested', 2)
        request(conn, 'different-member', queued, 'waiting', 3, member='other-member')
        request(conn, 'different-digest', queued, 'waiting', 4, digest='b' * 64)
        request(conn, 'different-owner-type', queued, 'waiting', 5, owner_type='task_media')
        request(conn, 'cancel-granted', cancelled, 'granted', 1)
        request(conn, 'cancel-yield', cancelled, 'yield_requested', 2)
        request(conn, 'foreign-blocked', cancelled_foreign, 'blocked', 1)
        request(conn, 'orphan-owner', JobRun('missing-job', local.workspace_id, local.id, 'metadata', 'orphan', 'c' * 64), 'waiting', 1)
    connections, active = [], []
    class ObservedConnection(sqlite3.Connection):
        def __enter__(self):
            active.append(self)
            return super().__enter__()
        def __exit__(self, *args):
            try:
                return super().__exit__(*args)
            finally:
                active.remove(self)
        def execute(self, sql, parameters=()):
            self.statements.append(sql)
            return super().execute(sql, parameters)
    def connect():
        conn = sqlite3.connect(repo.database_path, factory=ObservedConnection)
        conn.row_factory = sqlite3.Row
        conn.statements = []
        connections.append(conn)
        return conn
    monkeypatch.setattr(repo, '_connect', connect)
    records = repo.job_records
    rows = records.list_creation_job_request_states(local.workspace_id, queued.id)
    assert all(isinstance(row, sqlite3.Row) for row in rows)
    assert [row['id'] for row in rows] == ['a-wait', 'z-wait', 'yield-current', 'different-member', 'different-digest']
    assert records.list_creation_job_request_states(foreign.workspace_id, queued.id) == []
    wait = records.creation_execution_wait_row(local.workspace_id, 'metadata-member', queued.id, queued.input_digest)
    assert isinstance(wait, sqlite3.Row) and wait['unit_key'] == 'unit-yield-current'
    assert records.creation_execution_wait_row(local.workspace_id, 'missing-member', queued.id, queued.input_digest) is None
    assert records.creation_execution_wait_row(local.workspace_id, 'metadata-member', queued.id, 'missing-digest') is None
    assert records.creation_execution_wait_row(foreign.workspace_id, 'metadata-member', queued.id, queued.input_digest) is None
    assert {row['id'] for row in records.active_creation_request_ids(local.workspace_id, queued.id)} == {
        'a-wait', 'z-wait', 'different-member', 'different-digest'}
    cancellations = records.list_cancelled_creation_execution_requests()
    assert {(row['id'], row['workspace_id']) for row in cancellations} == {
        ('cancel-granted', local.workspace_id), ('foreign-blocked', foreign.workspace_id)}
    owner = records.creation_job_for_device_request_row(local.workspace_id, 'orphan-owner')
    assert isinstance(owner, sqlite3.Row) and owner.keys() == ['owner_id'] and owner['owner_id'] == 'missing-job'
    assert records.creation_job_for_device_request_row(foreign.workspace_id, 'orphan-owner') is None
    assert records.creation_job_for_device_request_row(local.workspace_id, 'different-owner-type') is None
    assert records.creation_job_for_device_request_row(local.workspace_id, 'missing-request') is None
    decoded = []
    decoder = records.job_run_from_row
    def decode_after_reader(row):
        assert active == []
        decoded.append(row['id'])
        return decoder(row)
    with monkeypatch.context() as patch:
        patch.setattr(records, 'job_run_from_row', decode_after_reader)
        pending = records.list_pending_creation_executions()
    assert isinstance(pending, list) and all(type(value) is JobRun for value in pending)
    assert [value.id for value in pending] == decoded == [interrupted.id, queued.id]
    assert len(connections) > 1 and active == []
    assert not any(sql.strip().upper().startswith('BEGIN') for conn in connections for sql in conn.statements)


def test_segment_unit_receipts_block_unknown_resume_and_keep_caller_atomicity(tmp_path, monkeypatch):
    import socket
    import subprocess
    import threading
    import wave
    from PIL import Image
    from app.production import ProductionRepository
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationTask
    from app.device_scheduler import RecoveryBlocked

    def forbidden(*args, **kwargs):
        raise AssertionError('segment unit metadata forbids execution, media and networking')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn'):
        monkeypatch.setattr(main, name, forbidden)
    repo = ProductionRepository(tmp_path / 'segment-unit.sqlite3', tmp_path / 'library',
        duration_reader=forbidden, scene_reader=forbidden)
    plan = CreationPlan('unit-plan', 'metadata-ws', 'fixed goal', 'fixed plan', 1, 'zh-CN', '9:16', 4,
        [], [], None, [], 0, 0, 'metadata', [], [], [], 'unit-fixture')
    task = CreationTask('unit-task', 'metadata-ws', plan.id, 'fixed task', 'knowledge_explanation')
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id='metadata-member')
    job = repo.job_records.create_job_run(JobRun('original-unit-job', task.workspace_id, task.id,
        'creation_task_segment_revision', 'original-unit-request', 'a' * 64))
    identity = SimpleNamespace(workspace_id=task.workspace_id, owner_id=job.id,
        request_id='metadata-unit-request', generation=1, unit_key='metadata-unit')
    units = repo.segment_units
    started = units.start_segment_unit(identity)
    assert started.id == job.id and started.status == 'running' and started.attempt == 1
    assert units.unsettled_segment_units(task.workspace_id, job.id)
    repo.job_records.finish_job_run(task.workspace_id, job.id, status='failed',
        failure_reason='declared interrupted work', impact='original output retained', repair_action='review original usage')
    failed = repo.job_records.get_job_run(task.workspace_id, job.id)
    with pytest.raises(RecoveryBlocked, match='用量未知'):
        units.start_segment_unit(identity)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='用量未核对'):
            units.resume_segment_job(task.workspace_id, job.id, connection=conn)
        assert conn.in_transaction
        conn.rollback()
    assert repo.job_records.get_job_run(task.workspace_id, job.id) == failed
    append = repo.execution_records.append_task_event
    def checkpoint_append_then_fail(event, **kwargs):
        value = append(event, **kwargs)
        if event.event_type == 'segment_execution_checkpoint':
            raise RuntimeError('segment checkpoint receipt tail')
        return value
    with monkeypatch.context() as patch:
        patch.setattr(repo.execution_records, 'append_task_event', checkpoint_append_then_fail)
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            units.settle_segment_unit(identity, elapsed_seconds=0.0, output_bytes=0, connection=conn)
            assert units.resume_segment_job(task.workspace_id, job.id, connection=conn) is None
            assert repo.job_records.get_job_run(task.workspace_id, job.id, connection=conn).status == 'interrupted'
            assert repo.job_records.get_job_run(task.workspace_id, job.id) == failed
            assert units.unsettled_segment_units(task.workspace_id, job.id)
            with pytest.raises(RuntimeError, match='segment checkpoint receipt tail'):
                units.checkpoint_segment_unit(identity, status='cancelled', connection=conn, reason='metadata cancellation')
            assert conn.in_transaction
            assert repo.job_records.get_job_run(task.workspace_id, job.id, connection=conn).status == 'cancelled'
            assert repo.execution_records.get_task_event(task.workspace_id,
                f'event_{job.id}_unit_checkpoint_1', connection=conn).payload['status'] == 'cancelled'
            conn.rollback()
    assert repo.job_records.get_job_run(task.workspace_id, job.id) == failed
    assert units.unsettled_segment_units(task.workspace_id, job.id)
    with repo._connect() as conn:
        for suffix in ('unit_settle_1', 'unit_checkpoint_1'):
            assert conn.execute('SELECT id FROM task_events WHERE id=?', (f'event_{job.id}_{suffix}',)).fetchone() is None
        assert conn.execute('SELECT id FROM task_events WHERE id=?', (f'event_{job.id}_unit_start_1',)).fetchone()
        conn.execute('BEGIN IMMEDIATE')
        units.settle_segment_unit(identity, elapsed_seconds=0.0, output_bytes=0, connection=conn)
        assert units.resume_segment_job(task.workspace_id, job.id, connection=conn) is None
        assert conn.in_transaction
    resumed = repo.job_records.get_job_run(task.workspace_id, job.id)
    assert resumed.id == job.id and resumed.status == 'interrupted' and resumed.attempt == failed.attempt
    assert resumed.failure_reason is None and resumed.finished_at is None
    assert not units.unsettled_segment_units(task.workspace_id, job.id)
    usage = repo.execution_records.get_task_event(task.workspace_id, f'event_{job.id}_unit_settle_1').payload
    assert usage['usage_id'] == 'metadata-unit-request:1' and usage['permit_generation'] == 1
    assert usage['elapsed_seconds'] == 0.0 and usage['output_bytes'] == 0
    assert usage['usage_source'] == 'local_execution_measurement'
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM job_runs WHERE workspace_id=? AND task_id=?',
            (task.workspace_id, task.id)).fetchone()[0] == 1


def test_execution_job_state_transactions_keep_declared_requeue_render_and_caller_rollback(tmp_path, monkeypatch):
    import hashlib
    import socket
    import subprocess
    import threading
    import wave
    from PIL import Image
    from app.creation.contracts import CreationTask, TimelineVersion
    from app.creation.plan_contracts import CreationPlan
    from app.production import ProductionRepository

    def forbidden(*args, **kwargs):
        pytest.fail('job state metadata forbids media, network and execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn'):
        monkeypatch.setattr(main, name, forbidden)
    repo = ProductionRepository(tmp_path / 'job-state.sqlite3', tmp_path / 'library',
        duration_reader=forbidden, scene_reader=forbidden)
    plan = CreationPlan('state-plan', 'metadata-ws', 'Read declared Job transitions', 'Metadata plan',
        1, 'zh-CN', '9:16', 4, [], [], None, [], 0, 0, '', [], [], [], 'metadata-only')
    task = CreationTask('state-task', plan.workspace_id, plan.id, 'Metadata task', 'knowledge_explanation')
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id='metadata-member')
    head = TimelineVersion('declared-head', task.workspace_id, task.id, None, 1, {'metadata_only': True}, None)
    repo.timeline_records.create_timeline_version(head, expected_current_version_id=None)
    task = repo.task_records.get_creation_task(task.workspace_id, task.id)
    task.confirmed_script_id, task.stage, task.stage_status = 'declared-script', 'montage', 'running'
    with repo._connect() as connection:
        connection.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(task), default=str), task.workspace_id, task.id))
    job = repo.job_records.create_job_run(JobRun('original-state-job', task.workspace_id, task.id,
        'creation_task_execution', 'original-state-key', 'a' * 64))
    start = TaskEvent(f'event_{job.id}_start', task.workspace_id, task.id, job.id, 'creation_execution_started',
        {'input_digest': job.input_digest, 'script_id': task.confirmed_script_id, 'member_id': 'metadata-member',
            'identity_kind': 'device', 'device_id': 'metadata-device', 'user_id': None})
    repo.execution_records.append_task_event(start)
    # A refused, never-started Job declaration, without a device request or grant.
    with repo._connect() as connection:
        connection.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,finished_at=? "
            'WHERE workspace_id=? AND id=?', ('metadata refusal', 'no execution', 'review original input',
                start.created_at.isoformat(), task.workspace_id, job.id))
    states = repo.creation_execution_jobs
    with repo._connect() as connection:
        assert not connection.in_transaction
        with pytest.raises(PermissionError, match='原未授予'):
            states.requeue_ungranted_narration_job(task.workspace_id, job.id, 'declared-request', connection=connection)
        assert not connection.in_transaction
    refused = repo.job_records.get_job_run(task.workspace_id, job.id)
    assert refused.status == 'failed' and refused.attempt == 0 and refused.started_at is None
    rejection = TaskEvent('declared-rejection', task.workspace_id, task.id, job.id, 'creation_execution_stage',
        {'failure_kind': 'input_budget_exceeded', 'reason_code': 'declared_resources_exceed_budget',
            'device_request_id': 'declared-request'})
    repo.execution_records.append_task_event(rejection)
    with repo._connect() as connection:
        assert not connection.in_transaction
        states.requeue_ungranted_narration_job(task.workspace_id, job.id, 'declared-request', connection=connection)
        queued = repo.job_records.get_job_run(task.workspace_id, job.id, connection=connection)
        assert queued.status == 'queued' and queued.attempt == 0 and queued.started_at is None
        assert queued.failure_reason is queued.impact is queued.repair_action is queued.finished_at is None
        assert connection.in_transaction
        connection.commit()
    # Record helpers declare a completed state; no stage or worker is executed.
    repo.job_records.claim_job_run(task.workspace_id, job.id)
    repo.job_records.finish_job_run(task.workspace_id, job.id, status='succeeded')
    succeeded = repo.job_records.get_job_run(task.workspace_id, job.id)
    original_task = repo.task_records.get_creation_task(task.workspace_id, task.id)
    unit_key = 'render:metadata-fixed-head'
    identity = hashlib.sha256(json.dumps([head.id, unit_key]).encode()).hexdigest()
    render = TaskEvent(f'event_{job.id}_render_requested_{identity}', task.workspace_id, task.id, job.id,
        'creation_execution_stage', {**start.payload, 'stage': 'render', 'status': 'pending',
            'execution_purpose': 'render_current', 'input_timeline_version_id': head.id,
            'start_event_id': start.id, 'unit_key': unit_key})
    interrupted = TaskEvent('declared-interrupt', task.workspace_id, task.id, job.id, 'creation_execution_stage',
        {'stage': 'render', 'reason': 'metadata_tail_failure'})
    observed = []
    append = repo.execution_records.append_task_event
    def current(connection):
        observed.append(connection)
        assert connection.in_transaction
        return True
    def append_then_fail(event, **kwargs):
        saved = append(event, **kwargs)
        assert saved == interrupted
        raise RuntimeError('metadata interrupt tail after actual append')
    with repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        reopened = states.reopen_creation_execution_for_render(succeeded, render,
            expected_timeline_version_id=head.id, connection=connection)
        assert reopened.id == succeeded.id and reopened.status == 'queued' and reopened.progress == 66
        assert reopened.updated_at == render.created_at
        assert repo.execution_records.get_task_event(task.workspace_id, render.id, connection=connection) == render
        permit = connection.execute('SELECT state,generation FROM device_permit WHERE id=1').fetchone()
        assert permit['state'] == 'free'
        with pytest.raises(ValueError, match='设备许可在制作恢复前已变化'):
            states.interrupt_creation_execution(interrupted, impact='metadata impact', repair_action='review metadata',
                task_is_current=current, expected_free_permit_generation=permit['generation'] + 1, connection=connection)
        assert observed == []
        assert repo.job_records.get_job_run(task.workspace_id, job.id, connection=connection) == reopened
        with monkeypatch.context() as patch:
            patch.setattr(repo.execution_records, 'append_task_event', append_then_fail)
            with pytest.raises(RuntimeError, match='after actual append'):
                states.interrupt_creation_execution(interrupted, impact='metadata impact', repair_action='review metadata',
                    task_is_current=current, expected_free_permit_generation=permit['generation'], connection=connection)
        assert observed == [connection] and connection.in_transaction
        assert repo.job_records.get_job_run(task.workspace_id, job.id, connection=connection).status == 'interrupted'
        changed_task = repo.task_records.get_creation_task(task.workspace_id, task.id, connection=connection)
        assert changed_task.stage == 'montage' and changed_task.stage_status == 'not_started'
        assert changed_task.stage_retryable and changed_task.recovery_point == 'montage'
        assert repo.execution_records.get_task_event(task.workspace_id, interrupted.id, connection=connection) == interrupted
        assert repo.job_records.get_job_run(task.workspace_id, job.id) == succeeded
        assert repo.task_records.get_creation_task(task.workspace_id, task.id) == original_task
        connection.rollback()
        assert not connection.in_transaction and connection.execute('SELECT 1').fetchone()[0] == 1
        assert repo.job_records.get_job_run(task.workspace_id, job.id, connection=connection) == succeeded
    assert repo.task_records.get_creation_task(task.workspace_id, task.id) == original_task
    assert repo.execution_records.get_task_event(task.workspace_id, start.id) == start
    assert repo.execution_records.get_task_event(task.workspace_id, rejection.id) == rejection
    for event_id in (render.id, interrupted.id):
        with pytest.raises(KeyError):
            repo.execution_records.get_task_event(task.workspace_id, event_id)
