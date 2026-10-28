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
    script = replace(repo.get_creation_task_script('workspace_a', script_id), sections=[
        CreationTaskScriptSection(f'part-{index}', f'段{index}', f'正文{index}🙂', '', []) for index in range(1, 46)])
    with repo._connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('BEGIN')
        payload = repo._script_payload(conn, script)
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?', (payload, script_id))
    def no_media(*args, **kwargs):
        raise AssertionError('this representative must not dispatch media')
    execution = CreationExecution(repo, run_montage_plan=no_media, run_render=no_media, render_is_valid=no_media)
    control = CreationExecutionControl(repo, execution, None, no_media)
    return repo, task_id, script_id, execution, control


def test_bounded_execution_preview_keeps_one_snapshot_during_a_tail_commit(tmp_path, monkeypatch):
    from app.creation_execution import revision_digest
    repo, task_id, script_id, execution, control = _bounded_preview_case(tmp_path)
    old_script = repo.get_creation_task_script('workspace_a', script_id)
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
        patch.setattr(repo, 'get_creation_task_script', no_full_getter)
        patch.setattr(repo.plan_records, 'get_creation_plan', no_full_getter)
        patch.setattr(execution, 'validate_input', no_full_getter)
        preview = control.preview('workspace_a', task_id)
    assert preview['input_digest'] == expected
    assert preview['script_updated_at'] == old_script.updated_at.isoformat()
    assert preview['script_sections_total_count'] == 45 and preview['script_sections_loaded'] is False
    assert 'script_sections' not in preview
    latest = control.preview('workspace_a', task_id)
    assert latest['input_digest'] != expected
    assert latest['input_digest'] == revision_digest(repo.get_creation_task_script('workspace_a', script_id), old_plan)
    assert latest['script_updated_at'] == '2026-10-27T01:02:03+00:00'
    with pytest.raises(ValueError, match='文案或制作要求已变化'):
        execution.validate_input('workspace_a', repo.get_creation_task('workspace_a', task_id), script_id, expected)


def test_bounded_execution_preview_replays_the_original_job_and_keeps_full_validation(tmp_path):
    from app.creation_execution import revision_digest
    repo, task_id, script_id, execution, control = _bounded_preview_case(tmp_path)
    task = repo.get_creation_task('workspace_a', task_id)
    old_digest = revision_digest(repo.get_creation_task_script('workspace_a', script_id), repo.plan_records.get_creation_plan('workspace_a', task.plan_id))
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
        assert repo.read_creation_execution_snapshot('workspace_a', task_id, connection=conn).input_digest == old_digest
        assert conn.in_transaction  # caller owns its transaction
    with repo._connect() as conn:
        with pytest.raises(ValueError, match='已开启的事务'):
            repo.read_creation_execution_snapshot('workspace_a', task_id, connection=conn)
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
        rows=repo.list_creation_job_request_states('local',first_job)
        assert len(rows)==1 and isinstance(rows[0],sqlite3.Row) and set(rows[0].keys())=={'id','state','unit_key','wait_reason','last_failure_reason'}
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
        # Keep the current original Job and immutable start receipt. A new trusted
        # task owner may read the task, but cannot adopt the former actor's Job.
        other_member, other_token = members.join(members.create_invite_code(), '后续任务成员')
        original_task = repo.get_creation_task('local', task_id)
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
        assert len(repo.list_creation_job_request_states('local',first_job))==1
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
        assert repo.job_records.get_job_run('local',second_job).status=='cancelled'
        assert repo.list_creation_job_request_states('local',second_job)[0]['state']=='cancelled'
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
        latest=repo.creation_execution_wait_row('local',member.member_id,'declared-read-0','declared-read')
        assert latest['unit_key']=='declared-read-1'
        assert repo.list_cancelled_creation_execution_requests()==[]
        with repo._connect() as conn:
            conn.execute("DELETE FROM device_work_requests WHERE id IN ('declared-read-request-0','declared-read-request-1')")
            request_rows=[dict(r) for r in conn.execute('SELECT id,owner_id,state FROM device_work_requests').fetchall()]
            permits=[dict(r) for r in conn.execute('SELECT state,children_json FROM device_permit').fetchall()]
        assert len(request_rows)==2 and all(r['state']=='cancelled' for r in request_rows)
        assert not repo.list_person_renders('local',task_id) and not repo.lineage_records.list_transform_runs('local',task_id)
        assert not repo.list_timeline_versions('local',task_id)
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
    snapshot = repo.read_creation_execution_snapshot('local', case.task_id)
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
                authorization = repo.get_task_authorization('local', case.task_id, connection=writer)
                repo.save_task_authorization(replace(authorization, revision=authorization.revision+1,
                    revoked_at=datetime.now(timezone.utc)), expected_revision=authorization.revision, connection=writer)
        with pytest.raises((ValueError, RecoveryBlocked), match='变化'):
            repo.create_creation_execution_job(JobRun('race-job', 'local', case.task_id, 'creation_task_execution',
                'race-job-key', case.digest), start_payload={'script_id': case.script_id, 'plan_id': snapshot.plan.id,
                    'input_digest': case.digest, **case.actor.identity_payload()}, source_preparation=prepared)
    assert observed == ['race-job']
    with repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type IN "
            "('creation_execution_started','creation_narration_source_bound')").fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == 0
