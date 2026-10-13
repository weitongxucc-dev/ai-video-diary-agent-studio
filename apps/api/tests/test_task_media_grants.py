"""Persistent task-media work uses the sole device grant and isolated stores."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier, CreationJobUnitExecutor
from app.device_runtime import OwnerRegistration, build_device_runtime
from app.device_scheduler import RecoveryBlocked
from app.host_resource_probes import HostResourceDecision, HostResourceRefusal
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess
from app.production import CreationPlan, CreationTask, ProductionRepository
from app.task_audio import TASK_MEDIA_CONTEXT, TaskMediaJobs, TaskMediaRequired
from test_device_runtime import Resources, Processes


def case(tmp_path, action=None, validate=None, resources=None):
    repo = ProductionRepository(tmp_path / 'media.sqlite', tmp_path / 'library')
    plan = CreationPlan(id='plan', workspace_id='local', goal='test', name='test', target_count=1,
        language='zh-CN', aspect_ratio='9:16', duration_target_seconds=120,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint='test')
    repo.confirm_creation_plan(plan, CreationTask(id='task', workspace_id='local', plan_id='plan', name='test', scenario='knowledge_explanation'))
    members = LocalMemberService(repo.database_path)
    actor = CreationExecutionActor('local', members.whoami(None)[0].member_id)
    access = MemberDeviceAccess(members)
    state = {'revision': 1, 'calls': 0, 'checks': 0}
    def execute(job, event, actor):
        assert TASK_MEDIA_CONTEXT.get()[0].grant.owner_id == job.id
        state['calls'] += 1
        if action:
            return action(state)
        return {'audio_url': '/saved-output'}
    def check_output(*args):
        state['checks'] += 1
        if validate:
            validate(state)
    def factory():
        return TaskMediaJobs(repo, facts=lambda *args: ({'base_version_id': None, 'revision': state['revision']},
            {'kind': 'task_audio', 'execution_mode': 'cpu_ffmpeg', 'timeout_seconds': 30, 'max_output_bytes': 65536, 'threads': 1, 'input_policy': 'probe_then_bounded_decode',
             'source_duration_limit_seconds':120, 'source_sample_rate_limit':48000, 'source_channels_limit':8,
             'output_duration_limit_seconds':120, 'output_sample_rate_limit':48000, 'output_channels_limit':8, 'inputs': []}),
            execute_operation=execute, validate_result=check_output, verify_identity=access.verify_identity)
    def runtime(media):
        return build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path/'device.lock',
            device_access=access, registrations={'creation_job': OwnerRegistration(
                CreationJobOwnerVerifier(repo, verify_identity=access.verify_identity, task_media=media),
                CreationJobUnitExecutor(repo, render_action=None, render_is_valid=None, task_media=media))},
            resources=resources or Resources(), processes=Processes(), admission_recorder=media.record_admission)
    media = factory()
    return repo, actor, state, factory, runtime, media


def test_submission_replay_is_persistent_and_does_not_start_work(tmp_path):
    repo, actor, state, factory, build, media = case(tmp_path)
    runtime = build(media)
    job = media.submit(runtime, actor, 'task', 'audio_listen', {'payload': {'start': 0}}, 'audio_listen')
    again = factory().submit(runtime, actor, 'task', 'audio_listen', {'payload': {'start': 0}}, 'audio_listen')
    assert again.id == job.id
    assert state['calls'] == 0 and repo.get_job_run('local', job.id).status == 'queued'
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 1
    runtime.stop(0)
    rebuilt = build(factory())
    assert rebuilt.run_once()
    assert repo.get_job_run('local', job.id).status == 'succeeded'
    assert state['calls'] == 1
    assert media.read('local', job.id)[1]['arguments'] == {'payload': {'start': 0}}
    assert repo.get_task_event('local', f'event_{job.id}_result').payload['result']['audio_url'] == '/saved-output'
    rebuilt.stop(0)


@pytest.mark.parametrize('change', ['input', 'identity', 'budget'])
def test_waiting_units_do_not_run_after_input_identity_or_resource_rejection(tmp_path, change):
    resources = Resources(admit=change != 'budget')
    repo, actor, state, factory, build, media = case(tmp_path, resources=resources)
    runtime = build(media)
    job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
    if change == 'input':
        state['revision'] = 2
    elif change == 'identity':
        media.verify_identity = lambda actor: 'unauthorized'
    assert not runtime.run_once()
    assert state['calls'] == 0 and repo.get_job_run('local', job.id).status == 'queued'
    runtime.stop(0)


def test_cross_member_replay_is_not_authorized(tmp_path):
    repo, actor, state, factory, build, media = case(tmp_path)
    runtime = build(media)
    job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
    assert media.check(*media.read('local', job.id), replace(actor, member_id='some-other-member')) == 'unauthorized'
    runtime.stop(0)


def test_cancel_before_grant_never_calls_media(tmp_path):
    repo, actor, state, factory, build, media = case(tmp_path)
    runtime = build(media)
    job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
    with repo._connect() as conn:
        request_id = conn.execute('SELECT id FROM device_work_requests').fetchone()[0]
    runtime.scheduler.cancel(request_id, actor)
    repo.finish_job_run('local', job.id, status='cancelled', failure_reason='user_cancelled', impact='unchanged', repair_action='resubmit')
    assert not runtime.run_once()
    assert state['calls'] == 0
    runtime.stop(0)


def test_post_receipt_pressure_revalidates_without_reexecuting(tmp_path):
    def pressure(state):
        if state['checks'] == 1:
            raise HostResourceRefusal(HostResourceDecision(False, {},
                {'disk_io_bytes_per_second': {'actual': 200, 'maximum': 100, 'passed': False}}, None))
    repo, actor, state, factory, build, media = case(tmp_path, validate=pressure)
    runtime = build(media)
    job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
    assert runtime.run_once()
    assert repo.get_job_run('local', job.id).status == 'interrupted'
    with repo._connect() as conn:
        conn.execute("UPDATE device_work_requests SET retry_not_before_utc=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    assert runtime.run_once()
    assert state['calls'] == 1 and state['checks'] == 2
    assert repo.get_job_run('local', job.id).status == 'succeeded'
    runtime.stop(0)


def test_unknown_release_blocks_and_retains_permit(tmp_path):
    resources = Resources()
    resources.released = lambda grant: None
    repo, actor, state, factory, build, media = case(tmp_path, resources=resources)
    runtime = build(media)
    media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
    with pytest.raises(RecoveryBlocked):
        runtime.run_once()
    with repo._connect() as conn:
        assert conn.execute('SELECT state FROM device_permit').fetchone()[0] != 'free'


def _resource_admission_case(tmp_path, monkeypatch):
    from itertools import count
    from types import SimpleNamespace
    import app.host_resource_probes as probes
    from test_host_resource_probes import _render_budget, _render_resources
    repo, actor, state, factory, build, media = case(tmp_path)
    resources = {**_render_resources(), 'private_payload':'PRIVATE_INPUT', 'secret':'PRIVATE_KEY'}
    media.facts = lambda *args: ({'base_version_id':None, 'revision':state['revision']}, resources)
    values = {'unknown':False, 'available':2801418240, 'swap':17957978112}
    ticks, io = count(10, .1), count(0, 4096)
    monkeypatch.setattr(probes.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(probes.psutil, 'cpu_percent', lambda **kwargs:21.4)
    def memory():
        if values['unknown']:
            raise OSError('PRIVATE_SOURCE_PATH')
        return SimpleNamespace(available=values['available'])
    monkeypatch.setattr(probes.psutil, 'virtual_memory', memory)
    monkeypatch.setattr(probes.psutil, 'swap_memory', lambda:SimpleNamespace(used=values['swap']))
    monkeypatch.setattr(probes.psutil, 'disk_io_counters', lambda **kwargs:SimpleNamespace(read_bytes=next(io), write_bytes=0))
    monkeypatch.setattr(probes.psutil, 'disk_usage', lambda path:SimpleNamespace(free=2**50))
    runtime = build(media)
    # Real admission implementation, synthetic envelope and platform observations in TMP only.
    runtime.scheduler.resources = probes.HostResourceProbe(repo.database_path, disk_paths=(tmp_path,),
        budget=probes.HostBudget(91, 1024**3, 16342122496, 1, 10**12, 10**12,
            cpu_render=replace(_render_budget(), additional_memory_bytes=2*1024**3)))
    return repo, actor, state, factory, build, media, runtime, values


def test_resource_unknown_then_refused_is_scoped_bounded_and_not_resampled_on_read(tmp_path, monkeypatch):
    import json
    repo, actor, state, _, _, media, runtime, values = _resource_admission_case(tmp_path, monkeypatch)
    try:
        job = media.submit(runtime, actor, 'task', 'visual_edit_preview', {}, 'edit_preview')
        job, start = media.read('local', job.id)
        values['unknown'] = True
        assert not runtime.run_once()
        with repo._connect() as conn:
            request_id = conn.execute('SELECT id FROM device_work_requests WHERE owner_id=?',(job.id,)).fetchone()[0]
        request = runtime.scheduler._get(request_id)
        unknown = media.resource_admission_receipt(job, start, actor, request, 'queued')
        assert unknown['observation_kind'] == 'first_unknown' and unknown['admitted'] is None
        assert unknown['context'] == 'pending_request' and unknown['last_grant_generation'] is None
        memory = next(item for item in unknown['checks'] if item['metric'] == 'available_memory_bytes')
        assert memory['actual'] is None and memory['minimum'] == 3*1024**3 and memory['passed'] is None
        values['unknown'] = False
        assert not runtime.run_once()
        request = runtime.scheduler._get(request.id)
        refused = media.resource_admission_receipt(job, start, actor, request, 'queued')
        assert refused['observation_kind'] == 'first_refused' and refused['admitted'] is False
        assert refused['device_request_id'] == request.id and refused['job_id'] == job.id
        assert refused['input_digest'] == job.input_digest and refused['base_version_id'] is None
        checks = {item['metric']:item for item in refused['checks']}
        assert checks['available_memory_bytes']['actual'] == 2801418240
        assert checks['swap_used_bytes']['actual'] == 17957978112
        assert checks['swap_used_bytes']['maximum'] == 16342122496
        assert len(checks) == 5 and state['calls'] == 0
        values['available'] = 1
        for _ in range(3):
            assert not runtime.run_once()
        repeated = media.resource_admission_receipt(job, start, actor, runtime.scheduler._get(request.id), 'queued')
        assert repeated == refused
        assert len([item for item in repo.list_task_events('local','task')
                    if item.event_type == 'task_media_resource_admission']) == 2
        for receipt in (unknown, refused):
            assert 'PRIVATE' not in json.dumps(receipt) and str(tmp_path) not in json.dumps(receipt)
        assert media.resource_admission_receipt(job, start, replace(actor, member_id='other'), request, 'queued') is None
        values['available'], values['swap'] = 4*1024**3, 0
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None
        history = media.resource_admission_receipt(job, start, actor, runtime.scheduler._get(request.id), 'queued')
        assert history['context'] == 'history' and history['last_grant_generation'] is None
        assert history['checked_at'] == refused['checked_at']
        runtime.scheduler.complete_unit(grant, checkpoint_database_path=repo.database_path,
                                       checkpoint_write=lambda conn, current:None)
    finally:
        runtime.stop(0)


def test_resource_receipt_write_failure_rolls_back_without_grant(tmp_path, monkeypatch):
    repo, actor, state, _, _, media, runtime, _ = _resource_admission_case(tmp_path, monkeypatch)
    job = media.submit(runtime, actor, 'task', 'visual_edit_preview', {}, 'edit_preview')
    original = repo.append_task_event
    def fail(event, *, connection=None):
        if event.event_type == 'task_media_resource_admission':
            assert connection is not None and connection.in_transaction
            original(event, connection=connection)
            raise RuntimeError('fixture persistence failure')
        return original(event, connection=connection)
    monkeypatch.setattr(repo, 'append_task_event', fail)
    try:
        with pytest.raises(RuntimeError, match='fixture persistence failure'):
            runtime.run_once()
        assert not [item for item in repo.list_task_events('local','task')
                    if item.event_type == 'task_media_resource_admission']
        with repo._connect() as conn:
            row = conn.execute('SELECT state,wait_reason FROM device_work_requests WHERE owner_id=?',(job.id,)).fetchone()
            assert tuple(row) == ('waiting',None)
            assert conn.execute('SELECT state FROM device_permit').fetchone()[0] == 'free'
        assert state['calls'] == 0
    finally:
        runtime.stop(0)


def test_resource_receipt_history_survives_cancel_retry_and_restart(tmp_path, monkeypatch):
    repo, actor, _, _, build, media, runtime, _ = _resource_admission_case(tmp_path, monkeypatch)
    try:
        job = media.submit(runtime, actor, 'task', 'visual_edit_preview', {}, 'edit_preview')
        job, start = media.read('local', job.id)
        assert not runtime.run_once()
        with repo._connect() as conn:
            request_id = conn.execute('SELECT id FROM device_work_requests WHERE owner_id=?',(job.id,)).fetchone()[0]
        first = media.resource_admission_receipt(job, start, actor, runtime.scheduler._get(request_id), 'queued')
        runtime.scheduler.cancel(request_id, actor)
        repo.finish_job_run('local', job.id, status='cancelled', failure_reason='user_cancelled',
                            impact='unchanged', repair_action='resubmit')
        cancelled = media.resource_admission_receipt(repo.get_job_run('local',job.id), start, actor,
                                                    runtime.scheduler._get(request_id), 'cancelled')
        assert cancelled['context'] == 'history' and cancelled['observation_id'] == first['observation_id']
        runtime.scheduler.retry_existing(request_id, actor, reason='cancelled')
        assert not runtime.run_once()
        second = media.resource_admission_receipt(repo.get_job_run('local',job.id), start, actor,
                                                 runtime.scheduler._get(request_id), 'interrupted')
        assert second['attempt_number'] == 2 and second['last_grant_generation'] is None
        assert second['observation_id'] != first['observation_id']
        runtime.stop(0)
        rebuilt = build(media)
        try:
            monkeypatch.setattr(media, 'facts', lambda *args: (_ for _ in ()).throw(AssertionError('no fact resample on history read')))
            history = media.resource_admission_receipt(repo.get_job_run('local',job.id), start, actor,
                                                      rebuilt.scheduler._get(request_id), 'stale')
            assert history['context'] == 'history' and history['checked_at'] == second['checked_at']
        finally:
            rebuilt.stop(0)
    finally:
        runtime.stop(0)


def test_resource_projection_keeps_unknown_disk_and_rejects_invalid_units():
    from app.schemas import ResourceAdmissionCheck
    from pydantic import ValidationError
    projected = TaskMediaJobs._admission_checks({
        'disk_free_bytes:/PRIVATE/A':{'actual':100,'minimum':50,'passed':True},
        'disk_free_bytes:/PRIVATE/B':{'minimum':50},
        'cpu_percent':{'actual':float('nan'),'maximum':91,'passed':False}})
    assert next(item for item in projected if item['metric']=='disk_free_bytes')['actual'] is None
    assert next(item for item in projected if item['metric']=='cpu_percent')['passed'] is None
    with pytest.raises(ValidationError, match='单位与指标'):
        ResourceAdmissionCheck(metric='swap_used_bytes',unit='percent',actual=1)


def test_real_short_audio_is_published_and_recovered_under_registered_grant(tmp_path, monkeypatch):
    import wave
    import shutil
    from app.artifact_store import ArtifactStore
    from app.host_resource_probes import HostProcessProbe
    from app.task_audio import TaskAudio
    if not shutil.which('ffprobe'):
        pytest.skip('FFprobe is required for this narrow synthetic media verification')
    source = tmp_path/'temporary'/'uploaded.wav'
    source.parent.mkdir()
    with wave.open(str(source), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b'\0\0'*4800)
    repo, actor, state, factory, build, media = case(tmp_path)
    store = ArtifactStore(tmp_path/'output')
    audio = TaskAudio(repo, store, source.parent)
    def produce(job, event, actor):
        record = audio.publish('local', 'task', kind='audio_source', parameters={'name': 'synthetic'}, input_refs=[],
            operation='upload', media_input={'staged_input':'uploaded.wav'})
        return {'source_ref':record.logical_ref, 'artifact_id':record.id}
    media.execute_operation = produce
    original_facts = media.facts
    def upload_facts(*args):
        facts, resources = original_facts(*args)
        resources["inputs"] = [{"path":str(source), "duration_seconds":None, "sample_rate":None, "channels":None}]
        return facts, resources
    media.facts = upload_facts
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    job = media.submit(runtime, actor, 'task', 'audio_import', {}, 'audio_source')
    assert runtime.run_once()
    saved = repo.get_job_run('local', job.id)
    assert saved.status == 'succeeded' and len(saved.output_refs) == 1
    record = repo.list_artifact_records('local','task')[0]
    with wave.open(str(store.resolve_file(record.logical_ref))) as wav:
        assert wav.getnframes() == 4800
    with repo._connect() as conn:
        permit = conn.execute('SELECT children_json,state FROM device_permit').fetchone()
        assert permit['state'] == 'free' and permit['children_json'] == '[]'
    assert repo.list_transform_runs('local','task')[0].parameters['duration_ms'] == 100
    runtime.stop(0)


def test_all_media_helpers_refuse_missing_grant_before_starting_subprocess(tmp_path):
    from app.audio_timeline import render_audio_mix, render_stretched_audio, probe_audio_source
    from app.audio_waveform import audio_waveform
    from app.silence_edits import detect_silence_cuts
    from app.subtitle_alignment import recognize_subtitle_timing
    from app.task_audio import TaskAudio
    from app.artifact_store import ArtifactStore
    repo, actor, state, factory, build, media = case(tmp_path)
    with pytest.raises(TaskMediaRequired):
        TaskAudio(repo, ArtifactStore(tmp_path/'out'), tmp_path/'temp').publish('local','task',kind='audio_preview',
            parameters={},input_refs=[],operation='audio_mix',media_input={})
    with pytest.raises(PermissionError):
        render_audio_mix(None, 1, None, tmp_path/'out.wav')
    with pytest.raises(PermissionError):
        render_stretched_audio(tmp_path/'none',0,1000,1,1000,tmp_path/'out.wav')
    with pytest.raises(TaskMediaRequired):
        probe_audio_source(tmp_path/'none')
    with pytest.raises(PermissionError):
        audio_waveform(tmp_path/'none',tmp_path/'cache')
    with pytest.raises(PermissionError):
        detect_silence_cuts(tmp_path/'none',1)
    with pytest.raises(PermissionError):
        recognize_subtitle_timing(None,tmp_path/'none','zh-CN',tmp_path)


@pytest.mark.parametrize('dimension', ['sample_rate', 'channels', 'duration_seconds', 'saved_metadata'])
def test_unknown_and_known_source_probe_rejects_before_dsp(tmp_path, monkeypatch, dimension):
    import wave
    import shutil
    import app.audio_timeline as timeline
    from app.host_resource_probes import HostProcessProbe
    if not shutil.which('ffprobe'):
        pytest.skip('FFprobe required for synthetic bounded probe')
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b'\0\0'*2*4800)
    repo, actor, state, factory, build, media = case(tmp_path)
    original_facts=media.facts
    def facts(*args):
        data, resources=original_facts(*args)
        spec={'path':str(source),'duration_seconds':None,'sample_rate':None,'channels':None}
        if dimension == 'saved_metadata':
            spec['sample_rate']=24000
        else:
            key={'sample_rate':'source_sample_rate_limit','channels':'source_channels_limit','duration_seconds':'source_duration_limit_seconds'}[dimension]
            resources[key]={'sample_rate':16000,'channels':1,'duration_seconds':.05}[dimension]
        resources['inputs']=[spec]
        return data,resources
    media.facts=facts
    dsp=[]
    original_command=timeline._run_granted_audio_command
    def command(*args,**kwargs):
        dsp.append(args[2][0])
        return original_command(*args,**kwargs)
    monkeypatch.setattr(timeline,'_run_granted_audio_command',command)
    def operation(job,event,actor):
        context=TASK_MEDIA_CONTEXT.get()[0]
        graph=timeline.narration_audio_timeline('workspace://source.wav',.1)
        timeline.render_audio_mix(graph,.1,lambda ref:source,tmp_path/'mixed.wav',
            scheduler=context.scheduler,grant=context.grant,cancel_event=context.cancel_event,
            deadline_monotonic=context.deadline_monotonic,max_output_bytes=context.max_output_bytes,threads=context.threads)
        return {}
    media.execute_operation=operation
    runtime=build(media)
    runtime.scheduler.processes=HostProcessProbe()
    job=media.submit(runtime,actor,'task','audio_listen',{},'audio_listen')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status == 'failed'
    assert dsp == ['ffprobe']
    assert not (tmp_path/'mixed.wav').exists()
    runtime.stop(0)


def test_picture_preview_uses_parent_grant_and_real_media_renderer(tmp_path, monkeypatch):
    import app.main as main
    import subprocess
    import wave
    import shutil
    from app.artifact_store import ArtifactStore
    from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, HostCapacity
    from app.montage import MontageRenderer, CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
    from app.task_audio import TaskAudio
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required for synthetic picture verification')
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=25:d=1','-pix_fmt','yuv420p',str(source)],
                   capture_output=True,check=True,timeout=5)
    voice=tmp_path/'voice.wav'
    with wave.open(str(voice),'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b'\0\0'*24000)
    plan=CreationTaskMontagePlan(id='montage',workspace_id='local',task_id='task',script_id='script',script_title='fixture',
        status='ready',cues=[NarrationCue('cue','section','',1)],
        clips=[MontageClip('clip','cue','asset','segment',str(source),0,1,0,1,1,[],False)],
        duration_seconds=1,low_confidence_clip_count=0,aspect_ratio='1:1')
    renderer=MontageRenderer(tmp_path/'render-output')
    with pytest.raises(PermissionError):
        renderer.render(plan,voice,job_staging_path=tmp_path/'ungranted')
    monkeypatch.setattr('app.montage.sample_host_capacity',lambda:HostCapacity(8,7,8*1024**3,0))
    repo,actor,state,factory,build,media=case(tmp_path)
    store=ArtifactStore(tmp_path/'output')
    def render(workspace,task_id,inputs,output,context):
        state['picture_calls']=state.get('picture_calls',0)+1
        renderer.render(plan,voice,job_staging_path=output.parent,subtitle_tracks=[],scheduler=context.scheduler,
            grant=context.grant,cancel_event=context.cancel_event,deadline_monotonic=context.deadline_monotonic,
            threads=context.threads,max_output_bytes=context.max_output_bytes)
        (output.parent/'preview.mp4').replace(output)
    audio=TaskAudio(repo,store,tmp_path/'temp',picture_renderer=render)
    original_facts=media.facts
    def facts(*args):
        current,resources=original_facts(*args)
        return current,{'kind':'creation_render','execution_mode':'cpu_ffmpeg','duration_seconds':1,
            'width_px':1080,'height_px':1080,'input_bytes':source.stat().st_size+voice.stat().st_size,
            'clip_count':1,'overlay_count':0,'max_output_bytes':2000000,'threads':1,'timeout_seconds':30}
    media.facts=facts
    def operation(job,event,actor):
        record=audio.publish('local','task',kind='color_preview',parameters={'side':'after'},input_refs=[],suffix='.mp4',
            operation='picture_preview',media_input={'plan':creation_task_montage_plan_to_payload(plan),'subtitle_tracks':[]})
        return {'video_url':'/synthetic-preview','artifact_id':record.id}
    media.execute_operation=operation
    configure_synthetic_media_budgets(monkeypatch, main)
    runtime=build(media)
    runtime.scheduler.processes=HostProcessProbe()
    runtime.scheduler.resources=HostResourceProbe(repo.database_path, disk_paths=(tmp_path,), budget=HostBudget(
        100, 0, 2**63, 0, 2**63, 32*1024**2, sample_seconds=.01,
        cpu_render=main.cpu_render_budget_from_env()))
    job=media.submit(runtime,actor,'task','visual_edit_preview',{},'edit_preview')
    assert runtime.run_once()
    saved=repo.get_job_run('local',job.id)
    assert saved.status == 'succeeded', saved.failure_reason
    assert state['picture_calls'] == 1
    assert store.resolve_file(saved.output_refs[0]).stat().st_size > 0
    assert repo.list_transform_runs('local','task')[0].parameters['duration_ms'] == 1000
    runtime.stop(0)


def _visual_scope_case(tmp_path, monkeypatch, *, fade_out=0):
    import json
    import subprocess
    from dataclasses import asdict
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    import app.main as main
    from app.artifact_store import ArtifactStore
    from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack
    from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe
    from app.montage import CreationTaskMontagePlan, MontageClip, MontageRenderer, NarrationCue, creation_task_montage_plan_to_payload
    from app.production import MaterialAsset, MaterialSegment, SubtitleCue, SubtitleTrack, TimelineVersion
    from app.task_audio import TaskAudio
    from _media_preview_test_fixtures import configure_synthetic_media_budgets, synthetic_wav_bytes
    configure_synthetic_media_budgets(monkeypatch, main)
    repo, actor, _state, _factory, build, media = case(tmp_path)
    repo.video_library_path.mkdir(exist_ok=True)
    source = repo.video_library_path/'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=64x64:r=30:d=6',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)],
        check=True, capture_output=True, timeout=5)
    voice = repo.video_library_path/'voice.wav'
    voice.write_bytes(synthetic_wav_bytes(8))
    references = []
    for asset_id, path, seconds, media_type in [('asset', source, 6, 'video'), ('voice', voice, 8, 'audio')]:
        repo._save_asset(MaterialAsset(id=asset_id, workspace_id='local', file_name=path.name, path=str(path),
            size_bytes=path.stat().st_size, modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
            duration_seconds=seconds, usage_role='b_roll', review_status='approved', analysis_status='ready',
            media_type=media_type, owner_user_id=actor.member_id,
            segments=[MaterialSegment(id=asset_id+'-segment', asset_id=asset_id, start_seconds=0, end_seconds=seconds,
                description='synthetic source', review_status='approved')]))
        reference = repo.workspace_media_reference_for_asset('local', asset_id)
        references.append(reference)
        with repo._connect() as conn:
            repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    clips, at = [], 0
    for index in range(21):
        length = 6 if index == 1 else 1
        clips.append(MontageClip(f'clip_{index}', f'cue_{index}', 'asset', 'asset-segment', references[0],
            0, length, at, at+length, 1, [], False, locked=index == 0,
            transition_in_seconds=.2 if index == 1 else 0,
            visual_effects={'fade_out_seconds':fade_out} if index == 1 else {}))
        at += length
    graph = AudioTimeline(tracks=[TimelineAudioTrack(id='voice', name='voice', role='narration', locked=True,
        clips=[TimelineAudioClip(id='voice-clip', name='voice', source_ref=references[1], source_start_ms=0,
            source_end_ms=8000, timeline_start_ms=0, locked=True, fade_in_ms=400, fade_out_ms=400)])])
    plan = CreationTaskMontagePlan('montage', 'local', 'task', 'script', 'scope fixture', 'ready',
        [NarrationCue(f'cue_{index}', f'section_{index}', '', clip.timeline_end_seconds-clip.timeline_start_seconds)
         for index, clip in enumerate(clips)], clips, at, 0, aspect_ratio='1:1', audio_timeline=graph, narration_audio_path=references[1],
        graphic_overlays=[{'kind':'text', 'id':'in-window', 'text':'fixture', 'start_seconds':2, 'end_seconds':3},
                          {'kind':'text', 'id':'outside', 'text':'outside', 'start_seconds':9, 'end_seconds':10}])
    payload = creation_task_montage_plan_to_payload(plan)
    with repo._connect() as conn:
        task = repo.get_creation_task('local', 'task')
        task.montage_plan_id = plan.id
        task.stage = 'montage'
        task.owner_user_id = actor.member_id
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id='local' AND id='task'",
            (json.dumps(asdict(task), default=str),))
    repo.save_creation_task_montage_plan('local', 'task', plan.id, payload)
    payload = repo.get_creation_task_montage_plan('local', 'task')
    repo.create_timeline_version(TimelineVersion('timeline', 'local', 'task', None, 1, {'montage':payload}, None))
    monkeypatch.setattr(main, 'clip_preview_budget_from_env', main.cpu_render_budget_from_env)
    store = ArtifactStore(tmp_path/'output')
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'settings', SimpleNamespace(
        video_output_path=str(tmp_path/'output'), effective_temporary_root_path=tmp_path/'temp'))
    subtitle_ref, transform_id = main._publish_subtitle_text(
        workspace_id='local', task_id='task', proposal_id='scope-fixture', kind='subtitle_text_edit',
        input_refs=['timeline:timeline'],
        content='1\n00:00:02,000 --> 00:00:03,000\ninside\n\n2\n00:00:09,000 --> 00:00:10,000\noutside\n',
        engine='synthetic-fixture', parameters={'timeline_version_id':'timeline'})
    track = SubtitleTrack('subtitles', 'local', 'task', 'timeline', 'en-US', None,
        {'font_family':'Arial', 'font_size':32, 'enabled':True},
        [SubtitleCue('inside', None, 'inside', 2, 3), SubtitleCue('outside', None, 'outside', 9, 10)],
        'timeline', None, subtitle_ref, transform_id, 'valid')
    repo.create_subtitle_track(track)
    service = TaskAudio(repo, store, tmp_path/'temp', picture_renderer=main._render_task_picture_input)
    monkeypatch.setattr(main, 'montage_renderer', MontageRenderer(tmp_path/'render'))
    monkeypatch.setattr(main, '_task_audio', lambda:service)
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args:actor)
    monkeypatch.setattr(main, '_assistant_member_id', lambda *args:actor.member_id)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request:'local')
    media.facts = main._task_media_facts
    media.execute_operation = main._execute_task_media_operation
    media.validate_result = main._validate_task_media_result
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    runtime.scheduler.resources = HostResourceProbe(repo.database_path, disk_paths=(tmp_path,), budget=HostBudget(
        100, 0, 2**63, 0, 2**63, 32*1024**2, sample_seconds=.01,
        cpu_render=main.cpu_render_budget_from_env(), clip_preview=main.clip_preview_budget_from_env(), cpu_audio=main.cpu_audio_budget_from_env()))
    monkeypatch.setattr(main, '_creation_runtime', lambda:runtime)
    return main, repo, actor, media, runtime, TestClient(main.app), payload, track, store


@pytest.mark.parametrize('route', ['visual', 'exact'])
def test_color_preview_on_21_shots_renders_only_representative_silent_window(tmp_path, monkeypatch, route):
    import json
    import subprocess
    main, repo, actor, media, runtime, client, original, _track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        payload = {'clip_ids':['clip_20', 'clip_1'], 'effects':{'exposure':.15}}
        if route == 'visual':
            path = '/api/creation-tasks/task/visual/preview'
            payload['base_version_id'] = 'timeline'
        else:
            path = '/api/creation-tasks/task/edits/exact/preview'
            payload.update(operation_type='AdjustVisualEffects', expected_timeline_version_id='timeline', expected_timeline_sequence=1)
        queued = client.post(path, json=payload)
        assert queued.status_code == 202, queued.text
        job_id = queued.json()['job_id']
        _job, event = media.read('local', job_id)
        resources = event['resources']
        assert event['arguments']['_media_kind'] == 'clip_preview'
        assert resources['duration_seconds'] == 5 and resources['clip_count'] == 1
        assert resources['silent'] is True and resources['overlay_count'] == 0 and 'audio_mix' not in resources
        assert event['input_facts']['visual_preview']['selected_clip_ids'] == ['clip_1', 'clip_20']
        assert event['input_facts']['clip']['id'] == 'clip_1'
        assert resources['input_bytes'] == repo.resolve_clip_media_reference('local', 'asset', original['clips'][1]['source_path']).stat().st_size
        assert runtime.run_once()
        done = client.get(queued.json()['result_url']).json()
        assert done['status'] == 'succeeded', done
        proposal = done['result']['proposal']
        assert set(proposal['operation']['payload']['expected_clips']) == {'clip_1', 'clip_20'}
        assert '代表镜头 2' in proposal['preview']['after']['detail']
        assert '静音画面' in proposal['preview']['after']['detail'] and '未覆盖相邻转场' in proposal['preview']['after']['detail']
        records = [record for record in repo.list_artifact_records('local', 'task') if record.producer_job_id == job_id]
        assert len(records) == 2
        for record in records:
            metadata = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                'format=duration:stream=codec_type', '-of', 'json', str(store.resolve_file(record.logical_ref))]))
            assert float(metadata['format']['duration']) == pytest.approx(5, abs=.05)
            assert [stream['codec_type'] for stream in metadata['streams']] == ['video']
        assert repo.get_creation_task_montage_plan('local', 'task') == original
        assert repo.get_creation_task('local', 'task').current_timeline_version_id == 'timeline'
        locked = client.post(path, json={**payload, 'clip_ids':['clip_0']})
        assert locked.status_code == 409 and '锁定' in locked.text
        asset = repo.get_asset('local', 'asset')
        repo._save_asset(replace(asset, use_state='excluded'))
        with pytest.raises(PermissionError, match='素材使用状态'):
            main._task_media_facts('local', 'task', event['operation'], event['arguments'], actor)
    finally:
        runtime.stop(0)


@pytest.mark.parametrize('existing_fade', [False, True])
def test_long_fade_preview_seals_one_stable_linked_window_and_renders_its_tail(tmp_path, monkeypatch, existing_fade):
    import json
    import subprocess
    from dataclasses import asdict
    from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(
        tmp_path, monkeypatch, fade_out=.4 if existing_fade else 0)
    try:
        patch = {'fade_out_seconds':0 if existing_fade else .4}
        queued = client.post('/api/creation-tasks/task/edits/exact/preview', json={
            'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':patch,
            'expected_timeline_version_id':'timeline', 'expected_timeline_sequence':1})
        assert queued.status_code == 202, queued.text
        job_id = queued.json()['job_id']
        _job, event = media.read('local', job_id)
        assert event['arguments']['_media_kind'] == 'picture_preview'
        assert event['resources']['clip_count'] == 1 and event['resources']['duration_seconds'] == 6
        assert event['resources']['overlay_count'] == 1 and event['resources']['audio_mix']['input_count'] == 1
        assert event['input_facts']['audio_mix_candidates'] == event['resources']['audio_mix_candidates']
        assert main._task_media_facts('local', 'task', event['operation'], event['arguments'], actor) == (
            event['input_facts'], event['resources'])
        plan = creation_task_montage_plan_from_payload(original)
        before_tracks = asdict(track)
        first = main._visual_preview_projection(plan, ['clip_1'], patch, [track])[2]
        second = main._visual_preview_projection(plan, ['clip_1'], patch, [track])[2]
        for (candidate, tracks), (again, again_tracks) in zip(first, second, strict=True):
            assert creation_task_montage_plan_to_payload(candidate) == creation_task_montage_plan_to_payload(again)
            assert [asdict(item) for item in tracks] == [asdict(item) for item in again_tracks]
            assert len(candidate.clips) == 1 and candidate.clips[0].id == 'clip_1'
            assert candidate.clips[0].timeline_start_seconds == 0 and candidate.clips[0].transition_in_seconds == 0
            audio = candidate.audio_timeline.tracks[0].clips[0]
            assert (audio.source_start_ms, audio.source_end_ms, audio.timeline_start_ms) == (1000, 7000, 0)
            assert (audio.envelope_offset_ms, audio.envelope_duration_ms) == (1000, 8000)
            assert [(item['id'], item['start_seconds'], item['end_seconds']) for item in candidate.graphic_overlays] == [('in-window', 1, 2)]
            assert [(cue.id, cue.start_seconds, cue.end_seconds) for cue in tracks[0].cues] == [('inside', 1, 2)]
        assert creation_task_montage_plan_to_payload(plan) == original and asdict(track) == before_tracks
        assert runtime.run_once()
        done = client.get(queued.json()['result_url']).json()
        assert done['status'] == 'succeeded', done
        assert '完整区间' in done['result']['proposal']['preview']['after']['detail']
        assert '未覆盖相邻转场' in done['result']['proposal']['preview']['after']['detail']
        inputs = [item for item in repo.list_task_events('local', 'task')
                  if item.job_id == job_id and item.event_type == 'task_media_input']
        assert len(inputs) == 2
        records = [record for record in repo.list_artifact_records('local', 'task') if record.producer_job_id == job_id]
        assert len(records) == 2
        for event_input in inputs:
            candidate = event_input.payload['media_input']['plan']
            assert len(candidate['clips']) == 1 and candidate['duration_seconds'] == 6
        for record in records:
            path = store.resolve_file(record.logical_ref)
            metadata = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                'format=duration:stream=codec_type', '-of', 'json', str(path)]))
            assert float(metadata['format']['duration']) == pytest.approx(6, abs=.05)
            assert {stream['codec_type'] for stream in metadata['streams']} == {'video', 'audio'}
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', 'scale=1:1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
            assert len(raw) == 180*3
            event_input = next(item for item in inputs if item.payload['parameters']['side'] ==
                next(transform.parameters['side'] for transform in repo.list_transform_runs('local', 'task')
                     if record.logical_ref in transform.output_refs))
            has_fade = event_input.payload['media_input']['plan']['clips'][0]['visual_effects'].get('fade_out_seconds', 0) > 0
            assert (max(raw[-3:]) < 8) if has_fade else (raw[-1] > 240)
        assert repo.get_creation_task_montage_plan('local', 'task') == original
        assert repo.get_creation_task('local', 'task').current_timeline_version_id == 'timeline'
    finally:
        runtime.stop(0)


def test_explicit_failed_retry_reuses_job_and_original_request(tmp_path):
    def action(state):
        if state['calls'] == 1:
            raise ValueError('synthetic known failure')
        return {'audio_url':'/saved-output'}
    repo,actor,state,factory,build,media=case(tmp_path,action=action)
    runtime=build(media)
    job=media.submit(runtime,actor,'task','audio_listen',{},'audio_listen')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status == 'failed'
    replay=media.submit(runtime,actor,'task','audio_listen',{},'audio_listen')
    assert replay.id == job.id and not runtime.run_once()
    with repo._connect() as conn:
        request_id=conn.execute('SELECT id FROM device_work_requests').fetchone()[0]
    runtime.scheduler.retry_existing(request_id,actor,reason='failed')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status == 'succeeded'
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 1
    runtime.stop(0)


def test_partial_artifact_receipt_reuses_frozen_candidate_after_explicit_retry(tmp_path, monkeypatch):
    import shutil
    from app.artifact_store import ArtifactStore
    from app.host_resource_probes import HostProcessProbe
    from app.task_audio import TaskAudio
    if not shutil.which('ffprobe'):
        pytest.skip('FFprobe required for synthetic receipt recovery')
    repo,actor,state,factory,build,media=case(tmp_path)
    import subprocess
    movie=tmp_path/'receipt-source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=1080x1080:r=30:d=0.1',
        '-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t','0.1','-c:v','libx264','-preset','ultrafast',
        '-pix_fmt','yuv420p','-c:a','aac',str(movie)],check=True,capture_output=True,timeout=5)
    def render(workspace,task_id,inputs,path,context):
        state['render_calls']=state.get('render_calls',0)+1
        shutil.copyfile(movie,path)
    audio=TaskAudio(repo,ArtifactStore(tmp_path/'output'),tmp_path/'temp',picture_renderer=render)
    original_facts=media.facts
    media.facts=lambda *args:(original_facts(*args)[0], {'kind':'creation_render','execution_mode':'cpu_ffmpeg',
        'threads':1,'max_output_bytes':65536,'timeout_seconds':30})
    def operation(job,event,actor):
        serial=str(state['render_calls'] if 'render_calls' in state else 0)
        # Fresh compiler IDs differ on the resumed invocation, but the render inputs are identical.
        inputs={'plan':{'id':'plan-'+serial,'duration_seconds':.1,'aspect_ratio':'1:1','cues':[{'id':'cue-'+serial}],
                       'clips':[{'id':'clip-'+serial,'cue_id':'cue-'+serial}]},'subtitle_tracks':[]}
        record=audio.publish('local','task',kind='audio_preview',parameters={'side':'after'},input_refs=[],
            operation='picture_preview',media_input=inputs)
        return {'artifact_id':record.id}
    original_transform=repo.create_transform_run
    attempts={'count':0}
    def transform(value, **kwargs):
        attempts['count']+=1
        if attempts['count']==1:
            raise ValueError('synthetic crash after immutable artifact receipt')
        return original_transform(value, **kwargs)
    monkeypatch.setattr(repo,'create_transform_run',transform)
    media.execute_operation=operation
    runtime=build(media)
    runtime.scheduler.processes=HostProcessProbe()
    job=media.submit(runtime,actor,'task','visual_edit_preview',{},'edit_preview')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status=='failed' and state['render_calls']==1
    assert repo.list_artifact_records('local','task') == []
    assert repo.list_transform_runs('local','task') == []
    with repo._connect() as conn:
        request_id=conn.execute('SELECT id FROM device_work_requests').fetchone()[0]
    runtime.scheduler.retry_existing(request_id,actor,reason='failed')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status=='succeeded' and state['render_calls']==1
    assert len(repo.list_artifact_records('local','task'))==1
    assert len(repo.list_transform_runs('local','task'))==1
    runtime.stop(0)
