import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.schemas import TaskMediaJobResponse
from test_task_media_grants import case


def test_202_fixed_result_route_replay_success_and_cancel(tmp_path, monkeypatch):
    repo, actor, state, factory, build, media = case(tmp_path)
    runtime = build(media)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args: actor)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'local')
    from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_to_payload
    from app.audio_timeline import AudioTimeline
    plan = CreationTaskMontagePlan(id='wire-plan',workspace_id='local',task_id='task',script_id='script',
        script_title='wire fixture',status='ready',cues=[],clips=[],duration_seconds=3,low_confidence_clip_count=0,
        audio_timeline=AudioTimeline(tracks=[]))
    monkeypatch.setattr(repo,'get_creation_task_montage_plan',lambda *args:creation_task_montage_plan_to_payload(plan))
    client = TestClient(main.app)
    payload = {'base_version_id': None, 'start_seconds': 0, 'end_seconds': 1, 'solo_track_ids': []}
    first = client.post('/api/creation-tasks/task/audio/listen', json=payload)
    assert first.status_code == 202, first.text
    receipt = TaskMediaJobResponse.model_validate(first.json())
    assert receipt.result is None and receipt.device_request_id
    assert receipt.operation == 'audio_listen' and receipt.target_refs == []
    assert receipt.input_digest and receipt.created_at
    assert receipt.result_url == f'/api/creation-tasks/task/media-jobs/{receipt.job_id}'
    replay = client.post('/api/creation-tasks/task/audio/listen', json=payload)
    assert replay.json()['job_id'] == receipt.job_id
    assert state['calls'] == 0
    assert runtime.run_once()
    done = client.get(receipt.result_url)
    assert done.status_code == 200 and done.json()['status'] == 'succeeded'
    assert done.json()['result']['audio_url'] == '/saved-output'
    second = client.post('/api/creation-tasks/task/audio/listen', json={**payload, 'end_seconds': 2}).json()
    cancelled = client.post(second['result_url']+'/cancel')
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert cancelled.json()['result'] is None
    assert not runtime.run_once()
    runtime.stop(0)


def test_result_read_cannot_cross_member_task_or_changed_input(tmp_path, monkeypatch):
    from dataclasses import replace
    repo, actor, state, factory, build, media = case(tmp_path)
    runtime = build(media)
    job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
    runtime.run_once()
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args: actor)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'local')
    client = TestClient(main.app)
    url = f'/api/creation-tasks/task/media-jobs/{job.id}'
    assert client.get(url.replace('/task/', '/other/')).status_code == 404
    state['revision'] = 2
    stale = client.get(url).json()
    assert stale['status'] == 'stale' and stale['result'] is None
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args: replace(actor, member_id='other'))
    assert client.get(url).status_code == 404
    runtime.stop(0)


def _resource_api_case(tmp_path, monkeypatch):
    from test_task_media_grants import _resource_admission_case
    values = _resource_admission_case(tmp_path, monkeypatch)
    repo, actor, _state, _, _, _media, runtime, _samples = values
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, '_creation_runtime', lambda:runtime)
    monkeypatch.setattr(main, 'device_runtime', runtime)
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args:actor)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request:'local')
    return values, TestClient(main.app)


def test_resource_wait_get_list_missing_cancel_stale_and_actor_scope(tmp_path, monkeypatch):
    from dataclasses import replace
    (repo, actor, state, _, _, media, runtime, _), client = _resource_api_case(tmp_path, monkeypatch)
    try:
        job = media.submit(runtime, actor, 'task', 'visual_edit_preview', {}, 'edit_preview')
        url = f'/api/creation-tasks/task/media-jobs/{job.id}'
        assert client.get(url).json()['resource_wait'] is None
        assert not runtime.run_once()
        pending = TaskMediaJobResponse.model_validate(client.get(url).json())
        assert pending.status == 'queued' and pending.waiting_reason == 'resource_busy'
        assert pending.resource_wait.context == 'pending_request'
        assert pending.resource_wait.job_id == job.id and pending.resource_wait.input_digest == job.input_digest
        assert pending.resource_wait.last_grant_generation is None
        page = client.get('/api/creation-tasks/task/media-jobs').json()
        assert page['items'][0]['resource_wait']['observation_id'] == pending.resource_wait.observation_id
        assert 'PRIVATE' not in client.get(url).text and str(tmp_path) not in client.get(url).text
        state['revision'] += 1
        second = media.submit(runtime, actor, 'task', 'visual_edit_preview', {}, 'edit_preview')
        assert second.id != job.id
        assert client.get(f'/api/creation-tasks/task/media-jobs/{second.id}').json()['resource_wait'] is None
        stale = client.get(url).json()
        assert stale['status'] == 'stale' and stale['resource_wait']['context'] == 'history'
        cancelled = client.post(url+'/cancel').json()
        assert cancelled['status'] == 'cancelled' and cancelled['failure_reason'] == 'user_cancelled'
        assert cancelled['resource_wait']['context'] == 'history'
        assert cancelled['resource_wait']['checked_at'] == pending.resource_wait.checked_at.isoformat().replace('+00:00','Z')
        monkeypatch.setattr(main, '_require_permit_actor', lambda *args:replace(actor,member_id='other'))
        assert client.get(url).status_code == 404
    finally:
        runtime.stop(0)


def test_terminal_conversation_uses_same_resource_history_without_changing_cancel_status(tmp_path, monkeypatch):
    import json
    from dataclasses import asdict
    from types import SimpleNamespace
    from app.conversation_repository import ConversationRepository
    (repo, actor, _, _, _, media, runtime, _), client = _resource_api_case(tmp_path, monkeypatch)
    conversations = ConversationRepository(repo.database_path)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, '_verify_creation_identity', lambda *args:'ready')
    monkeypatch.setattr(main, '_goal_service', lambda:SimpleNamespace(observe_business_result=lambda *args:False))
    # Fixture ownership belongs to the same synthetic member; no user/app database.
    task = repo.get_creation_task('local','task')
    task.owner_user_id = actor.member_id
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id='local' AND id='task'",
                     (json.dumps(asdict(task),default=str),))
    conversation = conversations.create('local',actor.member_id,'task','task')
    draft = conversations.save_draft(conversation['id'], 'local', actor.member_id,
        'fixture original instruction', conversation['draft_version'])
    turn, _ = conversations.create_turn_idempotent(conversation['id'],'local',actor.member_id,
        'fixture original instruction','fixture-request',draft['draft_version'],
        {'kind':'task','task_id':'task','target_snapshot':{'timeline_version_id':None,'timeline_sequence':0}})
    source = {'workspace_id':'local','task_id':'task','member_id':actor.member_id,
        'actor_identity':actor.identity_payload(), 'assistant_turn_id':turn['id'],
        'conversation_id':conversation['id'],'expected_timeline_version_id':None,'expected_timeline_sequence':0}
    try:
        job = media.submit(runtime, actor, 'task', 'visual_edit_preview', {'_typed_source':source}, 'edit_preview')
        assert not runtime.run_once()
        before = client.get(f'/api/creation-tasks/task/media-jobs/{job.id}').json()['resource_wait']
        conversations.finish_turn(turn['id'],'completed',result={'kind':'task_turn','response':{}})
        client.post(f'/api/creation-tasks/task/media-jobs/{job.id}/cancel')
        candidate = next(item for item in repo.assistant_business_candidates() if item['id']==job.id)
        assert main._project_assistant_business_candidate(candidate)
        saved = conversations.get_turn(turn['id'])
        assert saved['status'] == 'completed' and saved['result']['continuation']['status'] == 'cancelled'
        projected = saved['result']['response']['media_job']
        assert projected['status'] == 'cancelled' and projected['failure_reason'] == 'user_cancelled'
        assert projected['resource_wait']['context'] == 'history'
        assert projected['resource_wait']['observation_id'] == before['observation_id']
        assert projected['resource_wait']['checked_at'] == before['checked_at']
    finally:
        runtime.stop(0)


def test_real_upload_uses_measured_source_specs_and_fixed_receipt(tmp_path, monkeypatch):
    import io
    import wave
    import shutil
    from dataclasses import asdict
    from types import SimpleNamespace
    from app.artifact_store import ArtifactStore
    from app.host_resource_probes import HostProcessProbe
    from test_cpu_audio_budget import audio_budget
    if not shutil.which('ffprobe'):
        pytest.skip('FFprobe required for synthetic audio verification')
    repo, actor, state, factory, build, media = case(tmp_path)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path/'output'))
    monkeypatch.setattr(main, 'settings', SimpleNamespace(effective_temporary_root_path=tmp_path/'temporary'))
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args: actor)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'local')
    for field, value in asdict(audio_budget()).items():
        monkeypatch.setenv('DEVICE_CPU_AUDIO_'+field.upper(), str(value))
    media.facts = main._task_media_facts
    media.execute_operation = main._execute_task_media_operation
    media.validate_result = main._validate_task_media_result
    media.audio_ingress = main._audio_ingress_use_case()
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    buffer = io.BytesIO()
    with wave.open(buffer,'wb') as source:
        source.setnchannels(1)
        source.setsampwidth(2)
        source.setframerate(24000)
        source.writeframes(b'\0\0'*2400)
    client = TestClient(main.app)
    response = client.post('/api/creation-tasks/task/audio/import', files={'audio':('synthetic.wav',buffer.getvalue(),'audio/wav')})
    assert response.status_code == 202, response.text
    receipt = response.json()
    duplicate = client.post('/api/creation-tasks/task/audio/import', files={'audio':('synthetic.wav',buffer.getvalue(),'audio/wav')})
    assert duplicate.status_code == 202 and duplicate.json()['job_id'] == receipt['job_id']
    assert len(repo.list_job_runs('local','task')) == 1
    assert not list((tmp_path/'temporary').rglob('*.part'))
    rejected = client.post('/api/creation-tasks/task/audio/import', files={'audio':('empty.wav',b'','audio/wav')})
    assert rejected.status_code == 422 and not list((tmp_path/'temporary').rglob('*.part'))
    before_files = set((tmp_path/'temporary').rglob('*.wav'))
    original_submit = media.submit
    def reject_submit(*args, **kwargs):
        raise PermissionError('synthetic submit rejection')
    monkeypatch.setattr(media,'submit',reject_submit)
    rejected = client.post('/api/creation-tasks/task/audio/import', files={'audio':('rejected.wav',buffer.getvalue()+b'x','audio/wav')})
    assert rejected.status_code == 403
    assert set((tmp_path/'temporary').rglob('*.wav')) == before_files
    monkeypatch.setattr(media,'submit',original_submit)
    _, event = media.read('local', receipt['job_id'])
    assert event['resources']['input_policy'] == 'probe_then_bounded_decode'
    assert event['resources']['inputs'][0]['sample_rate'] is None
    assert event['resources']['inputs'][0]['channels'] is None
    assert runtime.run_once()
    result = client.get(receipt['result_url']).json()
    assert result['status'] == 'succeeded', result
    assert result['result']['duration_ms'] == 100
    transform = repo.list_transform_runs('local','task')[0]
    assert transform.parameters['sample_rate'] == 24000 and transform.parameters['channels'] == 1
    assert client.get('/api/creation-tasks/task/media-jobs').json()['items'][0]['job_id'] == receipt['job_id']
    # The imported source survives later timeline edits; drafts stay synchronous and do no DSP.
    import json
    from app.audio_timeline import narration_audio_timeline
    from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_to_payload
    from app.production import TimelineVersion
    graph = narration_audio_timeline(result['result']['source_ref'], .1)
    plan = CreationTaskMontagePlan(id='montage',workspace_id='local',task_id='task',script_id='script',
        script_title='fixture',status='ready',cues=[],clips=[],duration_seconds=.1,low_confidence_clip_count=0,
        audio_timeline=graph)
    with repo._connect() as conn:
        task = repo.get_creation_task('local','task')
        task.montage_plan_id='montage'
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id='local' AND id='task'",(json.dumps(asdict(task),default=str),))
        conn.execute("INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) VALUES (?,?,?,?,?)",
            ('montage','local','task',json.dumps(creation_task_montage_plan_to_payload(plan)),task.updated_at.isoformat()))
    repo.create_timeline_version(TimelineVersion('timeline','local','task',None,1,
        {'montage':creation_task_montage_plan_to_payload(plan)},None))
    before = len(repo.list_job_runs('local','task'))
    draft = client.post('/api/creation-tasks/task/audio/preview?draft=true', json={
        'base_version_id':'timeline','action':'update','target_id':graph.tracks[0].clips[0].id,'values':{'gain_db':-3}})
    assert draft.status_code == 200, draft.text
    assert draft.json()['proposal']['operation']['operation_type'] == 'EditAudioTimeline'
    assert len(repo.list_job_runs('local','task')) == before
    listen = client.post('/api/creation-tasks/task/audio/listen',json={
        'base_version_id':'timeline','start_seconds':0,'end_seconds':.1,'solo_track_ids':[]})
    assert listen.status_code == 202, listen.text
    assert runtime.run_once()
    listened = client.get(listen.json()['result_url']).json()
    assert listened['status'] == 'succeeded', listened
    played = client.get(listened['result']['audio_url'])
    assert played.status_code == 200 and played.content[:4] == b'RIFF'
    with wave.open(io.BytesIO(played.content)) as output:
        assert output.getframerate() == 48000 and output.getnchannels() == 2
    from fastapi import HTTPException
    from uuid import uuid4
    turn={'status':'running','calls':0}
    selected=f"audio:{graph.tracks[0].clips[0].id}"
    def bind(request, task, payload):
        if payload.assistant_turn_id is not None:
            turn['calls']+=1
            if turn['status'] != 'running':
                raise HTTPException(status_code=409,detail='completed public turn')
            return payload.model_copy(update={'raw_command':'当前配音音量降低3分贝'})
        return payload
    monkeypatch.setattr(main,'_bind_assistant_edit_instruction',bind)
    queued=client.post('/api/creation-tasks/task/edits/preview',json={'assistant_turn_id':str(uuid4()),
        'raw_command':'model text','context_stage':'montage','selected_object_refs':[selected]})
    assert queued.status_code == 202, queued.text
    turn['status']='completed'
    assert runtime.run_once()
    assert client.get(queued.json()['result_url']).json()['status']=='succeeded'
    assert queued.json()['target_refs']==[selected]
    assert turn['calls']==2  # Public submission validation only; no running-turn replay.
    _, frozen=media.read('local',queued.json()['job_id'])
    assert frozen['arguments']['payload']['raw_command']=='当前配音音量降低3分贝'
    # A fresh public call using a completed turn remains rejected.
    assert client.post('/api/creation-tasks/task/edits/preview',json=frozen['arguments']['payload']).status_code==409
    # A changed committed output cannot be called success or regenerated by replay/retry.
    listened_job=repo.get_job_run('local',listen.json()['job_id'])
    damaged=main.artifact_store.resolve_file(listened_job.output_refs[0])
    with damaged.open('ab') as output:
        output.write(b'changed')
    invalid=client.get(listen.json()['result_url']).json()
    assert invalid['status']=='failed' and invalid['failure_reason']=='result_invalid' and invalid['result'] is None
    replay=client.post('/api/creation-tasks/task/audio/listen',json={
        'base_version_id':'timeline','start_seconds':0,'end_seconds':.1,'solo_track_ids':[]})
    assert replay.json()['job_id']==listened_job.id and replay.json()['result'] is None
    assert client.post(listen.json()['result_url']+'/retry').status_code==202
    assert runtime.run_once()
    assert repo.get_job_run('local',listened_job.id).status=='failed'
    assert main.artifact_store.resolve_file(listened_job.output_refs[0]).stat().st_size==damaged.stat().st_size
    runtime.stop(0)


def test_list_is_bounded_member_filtered_and_restores_same_jobs(tmp_path, monkeypatch):
    from dataclasses import replace
    repo, actor, state, factory, build, media = case(tmp_path)
    runtime = build(media)
    own = [media.submit(runtime, actor, 'task','audio_listen', {'position':i},'audio_listen') for i in range(3)]
    other = replace(actor, member_id='other')
    media.verify_identity = lambda actor: 'ready'
    media.submit(runtime, other, 'task','audio_listen', {'position':99},'audio_listen')
    monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(main,'_require_permit_actor',lambda *args:actor)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    client=TestClient(main.app)
    first=client.get('/api/creation-tasks/task/media-jobs?limit=2').json()
    assert len(first['items']) == 2 and first['next_cursor']
    second=client.get('/api/creation-tasks/task/media-jobs',params={'cursor':first['next_cursor'],'limit':2}).json()
    assert len(second['items']) == 1 and second['next_cursor'] is None
    assert {item['job_id'] for item in first['items']+second['items']} == {job.id for job in own}
    assert client.get('/api/creation-tasks/task/media-jobs?limit=41').status_code == 422
    runtime.stop(0)
