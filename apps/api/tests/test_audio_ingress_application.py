"""Two finite audio journeys with synthetic files, actual stores/receipts and original grants."""
from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import wave

import pytest
from fastapi.testclient import TestClient

from app import main
from app.artifact_store import ArtifactStore
from app.audio_timeline import narration_audio_timeline
from app.creation.audio_ingress_use_case import AudioIngressRejected, AudioIngressUseCase
from app.creation.contracts import ArtifactRecord, JobRun, TimelineVersion, TransformRun
from app.host_resource_probes import HostProcessProbe
from app.local_members import LocalMemberService
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_to_payload
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.production import NarrationArtifactRecord, ProductionRepository
from app.creation.contracts import NarrationSegment
from app.task_audio import TASK_MEDIA_CONTEXT
from test_cpu_audio_budget import audio_budget
from test_task_media_grants import case


def pcm_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 3200)
    return buffer.getvalue()


def audio_state(tmp_path, monkeypatch):
    repo, actor, state, factory, build, media = case(tmp_path)
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    assert member.member_id == actor.member_id
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path/'outputs'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_output_path=str(tmp_path/'outputs'), video_inbox_path=str(repo.video_library_path),
        temporary_root_path=str(tmp_path/'temporary')))
    # These are the existing named fixture caps, not a production calibration.
    for name, value in asdict(audio_budget()).items():
        monkeypatch.setenv('DEVICE_CPU_AUDIO_'+name.upper(),str(value))
    media.facts = main._task_media_facts
    media.audio_ingress = main._audio_ingress_use_case()
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    client = TestClient(main.app, headers={'X-Local-Member-Token':token})
    errors, executions = [], []
    execute = media.audio_ingress.execute
    def observed(job,event,who,context):
        assert TASK_MEDIA_CONTEXT.get() is None
        assert who == actor and context.grant.owner_id == job.id
        executions.append((event['operation'],job.id,context.grant.request_id))
        try:
            return execute(job,event,who,context)
        except AudioIngressRejected as exc:
            errors.append((exc.status_code,exc.detail))
            raise
    monkeypatch.setattr(media.audio_ingress,'execute',observed)
    return repo,actor,media,runtime,client,errors,executions


def confirmed_script(repo, *, montage=False, cache_key=None):
    task = repo.get_creation_task('local','task')
    stamp = utc_now()
    script = CreationTaskScript('script','local','task','fixture-script','fixture-topic','输入文案','zh-CN',
        [CreationTaskScriptSection('section','段落','合成配音','合成画面',[])],
        selection_status='confirmed', created_at=stamp,updated_at=stamp,confirmed_at=stamp)
    task.stage='montage';task.confirmed_script_id=script.id;task.narration_cache_key=cache_key
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',(json.dumps(asdict(task),default=str),task.id))
        conn.execute('INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)',
            (script.id,'local',task.id,script.run_id,json.dumps(asdict(script),default=str),stamp.isoformat()))
    return task,script


def settled(client, runtime, receipt):
    assert runtime.run_once()
    result=client.get(receipt['result_url'])
    assert result.status_code==200, result.text
    return result.json()


def test_public_import_recording_mp4_and_original_fixed_receipt(tmp_path, monkeypatch, granted_media):
    repo,actor,media,runtime,client,errors,executions = audio_state(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='同一仓储'):
        AudioIngressUseCase(object(),media.audio_ingress.task_audio,tmp_path/'unused')
    data=pcm_bytes()
    rejected=client.post('/api/creation-tasks/task/audio/import',files={'audio':('empty.wav',b'','audio/wav')})
    assert rejected.status_code==422 and rejected.json()['detail']=='上传音频为空'
    first=client.post('/api/creation-tasks/task/audio/import',files={'audio':('input.wav',data,'audio/wav')})
    assert first.status_code==202, first.text
    original=first.json()
    _,start=media.read('local',original['job_id'])
    assert start['input_facts']['base_version_id'] is None
    assert start['arguments']['audio']['sha256']==hashlib.sha256(data).hexdigest()
    duplicate=client.post('/api/creation-tasks/task/audio/import',files={'audio':('input.wav',data,'audio/wav')})
    assert duplicate.status_code==202 and duplicate.json()['job_id']==original['job_id']
    imported=settled(client,runtime,original)
    assert imported['status']=='succeeded' and imported['result']=={
        'source_ref':repo.list_artifact_records('local','task')[0].logical_ref,'name':'input.wav','duration_ms':200}
    fixed=repo.get_task_event('local','event_'+original['job_id']+'_result').payload
    assert client.get(original['result_url']).json()['result']==fixed['result']
    # The old recorded rejection becomes a finite domain fact, then the same persistent label.
    bad=client.post('/api/creation-tasks/task/audio/recordings',files={'audio':('bad-stage.wav',data,'audio/wav')})
    assert bad.status_code==202, bad.text
    failed=settled(client,runtime,bad.json())
    assert failed['status']=='failed' and failed['failure_reason']=='HTTPException' and failed['result'] is None
    assert errors==[(409,'请先确认当前任务文案，再保存电脑录音')]
    task,script=confirmed_script(repo)
    source=tmp_path/'fixture-source.wav';source.write_bytes(data)
    mp4=tmp_path/'fixture-recording.mp4'
    # Generate only this owned synthetic input via the existing persisted fixture grant.
    with granted_media([source]) as context:
        command=['ffmpeg','-v','error','-filter_threads','1','-threads','1','-i',str(source),
            '-c:a','aac','-threads:a','1',str(mp4)]
        encoded=context.run(command,output_paths=(mp4,))
        assert encoded.returncode==0, encoded.stderr
    recording=client.post('/api/creation-tasks/task/audio/recordings',
        files={'audio':('recording.mp4',mp4.read_bytes(),'audio/mp4')})
    assert recording.status_code==202, recording.text
    recorded=settled(client,runtime,recording.json())
    assert recorded['status']=='succeeded', recorded
    result=recorded['result'];assert result['applied'] is False and result['duration_ms']==200
    artifact=repo.get_artifact_record('local',result['artifact_id'])
    assert artifact.kind=='task_recording' and artifact.logical_ref.endswith('.mp4')
    assert artifact.sha256==hashlib.sha256(mp4.read_bytes()).hexdigest()
    verified=repo.get_task_event('local','event_'+artifact.producer_job_id+'_verified').payload['outputs']
    assert [v['reference'] for v in verified]==[artifact.logical_ref]
    assert repo.get_creation_task('local','task').narration_cache_key is None
    assert repo.get_creation_task('local','task').current_timeline_version_id is None
    reopened=ProductionRepository(repo.database_path,repo.video_library_path)
    assert reopened.get_task_event('local','event_'+artifact.producer_job_id+'_result').payload['result']==result
    assert len(executions)==3 and all(request_id!=job_id for _,job_id,request_id in executions)
    assert not list((tmp_path/'temporary').rglob('*.part'))
    assert TASK_MEDIA_CONTEXT.get() is None
    assert runtime.stop(1)


def seed_narration(repo, media, tmp_path):
    task,script=confirmed_script(repo,cache_key='fixed-recorded')
    digest=repo.confirmed_creation_task_script_digest('local',task.id,script.id)
    data=pcm_bytes();source=tmp_path/'seed.wav';source.write_bytes(data)
    store=media.audio_ingress.task_audio.artifact_store
    ref='artifacts/task/fixture-voice.wav';publication=store.publish_file(ref,source)
    job=repo.create_job_run(JobRun('fixture-source-job','local','task','fixture_input','fixture-source-input',publication.sha256))
    running=repo.claim_job_run('local',job.id)
    artifact=repo.create_artifact_record(ArtifactRecord('fixture-source-artifact','local','task','task_recording',ref,
        publication.sha256,publication.size_bytes,job.id,[]))
    repo.lineage_records.create_transform_run(TransformRun('fixture-source-transform','local','task',job.id,'task_recording',publication.sha256,
        [],[ref],'synthetic-fixture-input/v1',{'duration_ms':200,'sample_rate':16000,'channels':1,'script_id':script.id,'script_digest':digest},'succeeded',running.started_at,utc_now()))
    repo.finish_job_run('local',job.id,status='succeeded',output_refs=[ref])
    path=store.resolve_file(ref)
    narration=NarrationArtifactRecord('fixed-recorded','local',script.id,None,'fixed-recorded',str(path),.2,16000,None,
        task_id=task.id,source_kind='recorded',source_artifact_ref=ref,source_sha256=artifact.sha256,source_script_digest=digest)
    repo.save_narration_artifact(narration)
    segment=NarrationSegment('segment','local',task.id,script.id,'section','合成配音',ref,.2,16000,None,None,None,'zh','',
        'ready',source_kind='recorded',source_artifact_ref=ref,source_sha256=artifact.sha256,source_script_digest=digest)
    repo.save_narration_segments('local',task.id,script.id,[segment])
    graph=narration_audio_timeline(ref,.2)
    plan=CreationTaskMontagePlan('montage','local',task.id,script.id,'输入文案','ready',[],[],.2,0,
        narration_audio_path=str(path),audio_timeline=graph)
    payload=creation_task_montage_plan_to_payload(plan)
    repo.save_creation_task_montage_plan('local',task.id,plan.id,payload)
    version=repo.create_timeline_version(TimelineVersion('base','local',task.id,None,1,{'montage':payload,
        'narration_segments':[asdict(segment)],'narration_cache_key':narration.cache_key},None))
    return version,graph,path


def test_public_listen_and_both_waveforms_use_explicit_original_grant(tmp_path, monkeypatch):
    repo,actor,media,runtime,client,errors,executions=audio_state(tmp_path,monkeypatch)
    missing=client.get('/api/creation-tasks/task/narration/waveform')
    assert missing.status_code==404 and missing.json()['detail']=='当前任务还没有可读取的配音。'
    version,graph,path=seed_narration(repo,media,tmp_path)
    owner=media.audio_ingress
    media.audio_ingress=None
    unbound=client.post('/api/creation-tasks/task/audio/import',
        files={'audio':('unbound-owner.wav',pcm_bytes(),'audio/wav')})
    assert unbound.status_code==202,unbound.text
    refused=settled(client,runtime,unbound.json())
    assert refused['status']=='failed' and refused['failure_reason']=='ValueError' and refused['result'] is None
    assert not any(row.producer_job_id==unbound.json()['job_id'] for row in repo.list_artifact_records('local','task'))
    media.audio_ingress=owner
    endpoint='/api/creation-tasks/task/audio/listen'
    stale=client.post(endpoint,json={'base_version_id':'stale','start_seconds':0,'end_seconds':.2,'solo_track_ids':[]})
    assert stale.status_code==409 and stale.json()['detail']=='时间线已经变化，请刷新后试听'
    listened=client.post(endpoint,json={'base_version_id':version.id,'start_seconds':0,'end_seconds':.2,
        'solo_track_ids':[graph.tracks[0].id]})
    assert listened.status_code==202, listened.text
    result=settled(client,runtime,listened.json())
    assert result['status']=='succeeded',result
    playable=client.get(result['result']['audio_url']);assert playable.status_code==200
    with wave.open(io.BytesIO(playable.content)) as output:
        assert output.getframerate()==48000 and output.getnchannels()==2
    records_before=repo.list_artifact_records('local','task')
    waveforms=[]
    for url in ('/api/creation-tasks/task/audio/waveform/'+graph.tracks[0].clips[0].id,
                '/api/creation-tasks/task/narration/waveform'):
        submitted=client.get(url);assert submitted.status_code==202,submitted.text
        receipt=submitted.json();_,start=media.read('local',receipt['job_id'])
        assert start['input_facts']['base_version_id']==version.id
        waveform=settled(client,runtime,receipt)
        assert waveform['status']=='succeeded',waveform
        assert set(waveform['result'])=={'source_id','duration_seconds','seconds_per_peak','peaks'}
        assert waveform['result']['duration_seconds']==.2 and max(waveform['result']['peaks'])==0
        assert repo.get_task_event('local','event_'+receipt['job_id']+'_verified').payload['outputs']==[]
        assert repo.get_job_run('local',receipt['job_id']).output_refs==[]
        waveforms.append(waveform['result'])
        fixed=client.get(url).json();assert fixed['job_id']==receipt['job_id'] and fixed['result'] is None
        assert client.get(fixed['result_url']).json()['result']==waveform['result']
    assert waveforms[0]==waveforms[1]
    assert repo.list_artifact_records('local','task')==records_before
    assert len(list((tmp_path/'temporary'/'audio-waveforms'/'local').glob('*.json')))==1
    assert repo.get_creation_task('local','task').current_timeline_version_id==version.id
    assert errors==[] and len(executions)==3 and TASK_MEDIA_CONTEXT.get() is None
    assert runtime.stop(1)
