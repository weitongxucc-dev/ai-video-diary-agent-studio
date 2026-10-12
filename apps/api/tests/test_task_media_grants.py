"""Persistent task-media work uses the sole device grant and isolated stores."""
from dataclasses import replace
import json
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
            resources=resources or Resources(), processes=Processes())
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
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
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
    with pytest.raises(PermissionError): render_audio_mix(None, 1, None, tmp_path/'out.wav')
    with pytest.raises(PermissionError): render_stretched_audio(tmp_path/'none',0,1000,1,1000,tmp_path/'out.wav')
    with pytest.raises(TaskMediaRequired): probe_audio_source(tmp_path/'none')
    with pytest.raises(PermissionError): audio_waveform(tmp_path/'none',tmp_path/'cache')
    with pytest.raises(PermissionError): detect_silence_cuts(tmp_path/'none',1)
    with pytest.raises(PermissionError): recognize_subtitle_timing(None,tmp_path/'none','zh-CN',tmp_path)


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
        wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000); wav.writeframes(b'\0\0'*2*4800)
    repo, actor, state, factory, build, media = case(tmp_path)
    original_facts=media.facts
    def facts(*args):
        data, resources=original_facts(*args)
        spec={'path':str(source),'duration_seconds':None,'sample_rate':None,'channels':None}
        if dimension == 'saved_metadata': spec['sample_rate']=24000
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
    import subprocess
    import wave
    import shutil
    from app.artifact_store import ArtifactStore
    from app.host_resource_probes import HostProcessProbe, HostCapacity
    from app.montage import MontageRenderer, CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
    from app.task_audio import TaskAudio
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required for synthetic picture verification')
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=25:d=1','-pix_fmt','yuv420p',str(source)],
                   capture_output=True,check=True,timeout=5)
    voice=tmp_path/'voice.wav'
    with wave.open(str(voice),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*24000)
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
    runtime=build(media);runtime.scheduler.processes=HostProcessProbe()
    job=media.submit(runtime,actor,'task','visual_edit_preview',{},'edit_preview')
    assert runtime.run_once()
    saved=repo.get_job_run('local',job.id)
    assert saved.status == 'succeeded', saved.failure_reason
    assert state['picture_calls'] == 1
    assert store.resolve_file(saved.output_refs[0]).stat().st_size > 0
    assert repo.list_transform_runs('local','task')[0].parameters['duration_ms'] == 1000
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
    import wave
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
    def transform(value):
        attempts['count']+=1
        if attempts['count']==1:
            raise ValueError('synthetic crash after immutable artifact receipt')
        return original_transform(value)
    monkeypatch.setattr(repo,'create_transform_run',transform)
    media.execute_operation=operation
    runtime=build(media);runtime.scheduler.processes=HostProcessProbe()
    job=media.submit(runtime,actor,'task','visual_edit_preview',{},'edit_preview')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status=='failed' and state['render_calls']==1
    with repo._connect() as conn:
        request_id=conn.execute('SELECT id FROM device_work_requests').fetchone()[0]
    runtime.scheduler.retry_existing(request_id,actor,reason='failed')
    assert runtime.run_once()
    assert repo.get_job_run('local',job.id).status=='succeeded' and state['render_calls']==1
    assert len(repo.list_artifact_records('local','task'))==1
    assert len(repo.list_transform_runs('local','task'))==1
    runtime.stop(0)
