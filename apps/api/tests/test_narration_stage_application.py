"""One bounded full-narration to selected-segment regeneration normal."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
import wave

import psutil
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation_execution import CreationStageGrantContext, revision_digest
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, MontageBudget, NarrationBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess, creation_job_registration
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.narration import NarrationArtifact, NarrationGenerator, _pronunciation_version
from app.production import CreationPlanPersonReference, CreationPlanVoiceReference, NarrationArtifactRecord, NarrationSegment, PronunciationEntry, SubtitleCue, SubtitleTrack
from app.voice_clone import Qwen3TTSSubprocessProvider
from app.creation.narration_stage_use_case import NarrationStageUseCase
from app.audio_timeline import narration_audio_timeline
from app.subtitle_schedule import narration_schedule
from test_creation_execution import _seed
from _media_preview_test_fixtures import register_synthetic_profiles, register_synthetic_video_asset


# Exact original finite worker from test_creation_execution; no model execution.
SYNTHETIC_TTS_WORKER = '''import sys,json,wave,os
from pathlib import Path
r=json.loads(Path(sys.argv[sys.argv.index('--request')+1]).read_text())
assert 'DEVICE_PROOF' not in os.environ and 'AI_VIDEO_DIARY_LLM_KEY' not in os.environ
assert Path(os.environ['HOME']).parent.name == '.cache'
assert Path(os.environ['XDG_CONFIG_HOME']).parent == Path(os.environ['HOME']).parent
p=Path(r['output_path'])
with wave.open(str(p),'wb') as w:
 w.setparams((1,2,24000,0,'NONE','not compressed')); w.writeframes(b'\\0\\0'*24000)
Path(sys.argv[sys.argv.index('--response')+1]).write_text(json.dumps(dict(schema_version=1,model_id=r['model_id'],audio_path=str(p),duration_seconds=1,sample_rate=24000)))
'''


def _file_fact(path):
    path=Path(path);stat=path.stat()
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':stat.st_size,
        'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns,'inode':stat.st_ino}


@pytest.fixture
def narration_preparation(tmp_path):
    task=SimpleNamespace(id='task',workspace_id='workspace',plan_id='plan',stage='narration',confirmed_script_id='script',
        owner_user_id='member',narration_cache_key=None)
    voice=SimpleNamespace(id='voice',version='v1',reference_audio_path='workspace://reference.wav',person_profile_id=None)
    plan=SimpleNamespace(creation_task_id=task.id,duration_target_seconds=30,
        selected_voice=SimpleNamespace(profile_id=voice.id,version=voice.version))
    script=SimpleNamespace(id='script',selection_status='confirmed',language='en-US',sections=[
        SimpleNamespace(id='section-1',content='First section.'),SimpleNamespace(id='section-2',content='Second section.')])
    job=SimpleNamespace(id='original-job',workspace_id='workspace',task_id=task.id,
        kind='creation_task_execution',input_digest='original-input')
    start=SimpleNamespace(workspace_id='workspace',task_id=task.id,job_id=job.id,
        payload={'script_id':script.id,'input_digest':job.input_digest})
    inputs=SimpleNamespace(voice=voice,pronunciation_entries=(
        PronunciationEntry('entry','workspace','term','en','first','workspace',None,'v1'),))
    segments={};records={};calls=[];target_reads=[]
    def read_task(*args):
        target_reads.append(args)
        return task
    def no_write(*args,**kwargs):
        pytest.fail('preparation must leave the business checkpoint to its owner')
    repository=SimpleNamespace(
        get_job_run=lambda _ws,_job:job,get_task_event=lambda _ws,_event:start,
        creation_execution_input=lambda *_:start.payload,
        get_creation_task=read_task,get_creation_plan=lambda *_:plan,get_creation_task_script=lambda *_:script,
        effective_creation_script=lambda *_:script,
        resolve_creation_task_narration_inputs=lambda **_:inputs,
        resolve_workspace_media_reference=lambda *args,**kwargs:tmp_path/'reference.wav',
        get_narration_segment=lambda _ws,_task,identity:segments[identity],
        list_narration_segments=lambda *_:list(reversed(list(segments.values()))),
        narration_artifact_reference=lambda key:'narration://artifact/'+key,
        resolve_authorized_narration_audio_reference=lambda _ws,ref:Path(records[ref.rsplit('/',1)[1]].audio_path),
        get_narration_artifact=lambda _ws,key:records.get(key),
        start_creation_task_narration=no_write,save_narration_artifact=no_write,
        save_narration_segments=no_write,complete_creation_task_narration=no_write,fail_creation_task_stage=no_write)
    def artifact(key):
        path=tmp_path/(key+'.wav');path.write_bytes(b'controlled-output')
        return NarrationArtifact(str(path),1.,24000,'fixture-model',task_id=task.id,script_id=script.id,
            voice_profile_id=voice.id,cache_key=key,language='en',
            pronunciation_version=_pronunciation_version(tuple(entry.task_input() for entry in inputs.pronunciation_entries)))
    def generate(**kwargs):
        calls.append(('segment',kwargs))
        return artifact(kwargs['segment_id'])
    use_case=NarrationStageUseCase(repository,SimpleNamespace(generate_for_creation_task=generate),None,None)
    def resources():
        return {'execution_mode':'cpu_pcm_wav_assembly','script_id':script.id,'merge_source_digest':'fixed-digest',
            'merge_final':True,'inputs':[{'reference':'narration://artifact/'+use_case.segment_id(task.id,script.id,section.id)}
                for section in script.sections]}
    from contextlib import nullcontext
    repository._connect=lambda:nullcontext(SimpleNamespace(execute=lambda *_:SimpleNamespace(fetchone=lambda:
        {'resources_json':json.dumps(resources())})))
    for section in script.sections:
        identity=use_case.segment_id(task.id,script.id,section.id)
        segments[identity]=NarrationSegment(identity,'workspace',task.id,script.id,section.id,section.content,
            None,None,None,None,voice.id,voice.version,'en','', 'pending')
    def context(unit_key,limit=128):
        value=CreationStageGrantContext(SimpleNamespace(assert_grant=lambda _:None),
            SimpleNamespace(owner_type='creation_job',unit_key=unit_key,workspace_id='workspace',
                owner_id=job.id,input_digest=job.input_digest,request_id='fixture-request'),time.monotonic()+30,limit,1)
        def assemble(declared):
            calls.append(('merge',{'grant_context':value,'inputs':declared['inputs']}))
            output=artifact('combined')
            return {'finalized':True,'source_digest':declared['merge_source_digest'],
                'pcm_bytes':96000,'work_path':output.audio_path,'generated_bytes':Path(output.audio_path).stat().st_size}
        object.__setattr__(value,'run_pcm_assembly',assemble)
        return value
    def ready():
        for section in script.sections:
            pending=segments[use_case.segment_id(task.id,script.id,section.id)]
            generated=artifact(pending.id)
            record=NarrationArtifactRecord(id='artifact-'+pending.id,workspace_id='workspace',task_id=task.id,
                script_id=script.id,voice_profile_id=voice.id,cache_key=generated.cache_key,audio_path=generated.audio_path,
                duration_seconds=generated.duration_seconds,sample_rate=generated.sample_rate,model_id=generated.model_id,
                language=generated.language,pronunciation_version=generated.pronunciation_version)
            records[record.cache_key]=record
            segments[pending.id]=replace(pending,status='ready',audio_reference=repository.narration_artifact_reference(record.cache_key),
                duration_seconds=record.duration_seconds,sample_rate=record.sample_rate,model_id=record.model_id,
                pronunciation_version=record.pronunciation_version)
    return SimpleNamespace(case=use_case,task=task,script=script,voice=voice,segments=segments,
        records=records,calls=calls,context=context,ready=ready,inputs=inputs,target_reads=target_reads,
        merge_inputs=lambda:['narration://artifact/'+use_case.segment_id(task.id,script.id,section.id)
            for section in script.sections])


def test_prepare_segment_keeps_context_and_pending_identity_without_checkpoint_write(narration_preparation):
    s=narration_preparation;section=s.script.sections[0]
    pending=s.segments[s.case.segment_id(s.task.id,s.script.id,section.id)]
    context=s.context('narration:segment:v1:'+section.id+':digest')
    record,segment,output_bytes=s.case.prepare_segment('workspace',s.task.id,
        script_id=s.script.id,section_id=section.id,grant_context=context)
    assert len(s.calls)==1 and s.calls[0][0]=='segment'
    assert s.calls[0][1]['grant_context'] is context
    assert s.calls[0][1]['narration_text']==section.content and s.calls[0][1]['language']=='en'
    assert segment.id==pending.id and segment.created_at==pending.created_at and segment.status=='ready'
    assert segment.audio_reference=='narration://artifact/'+record.cache_key
    assert output_bytes==Path(record.audio_path).stat().st_size
    assert s.segments[pending.id]==pending and s.records=={} and s.task.narration_cache_key is None


def test_prepare_merge_orders_saved_ready_segments_without_publishing(narration_preparation):
    s=narration_preparation;s.ready()
    before=dict(s.segments);before_records=dict(s.records)
    context=s.context('narration:merge:v1:digest')
    record,output_bytes,receipt=s.case.prepare_merge('workspace',s.task.id,script_id=s.script.id,
        input_references=s.merge_inputs(),grant_context=context)
    assert len(s.calls)==1 and s.calls[0][0]=='merge'
    assert s.calls[0][1]['grant_context'] is context
    assert [item['reference'] for item in s.calls[0][1]['inputs']]==s.merge_inputs()
    assert receipt['finalized']
    assert output_bytes==Path(record.audio_path).stat().st_size
    assert s.segments==before and s.records==before_records and s.task.narration_cache_key is None


@pytest.mark.parametrize('target',[('other-workspace','task','script'),('workspace','other-task','script'),
                                  ('workspace','task','other-script')])
def test_prepare_merge_rejects_target_outside_original_grant_before_target_read(narration_preparation,target):
    s=narration_preparation;s.ready()
    with pytest.raises(PermissionError):
        s.case.prepare_merge(target[0],target[1],script_id=target[2],
            input_references=s.merge_inputs(),grant_context=s.context('narration:merge:v1:digest'))
    assert s.target_reads==[] and s.calls==[]


@pytest.mark.parametrize('stale',['all','mixed'])
def test_prepare_merge_rejects_old_or_mixed_fixed_pronunciation_input(narration_preparation,stale):
    s=narration_preparation;s.ready()
    s.inputs.pronunciation_entries=(replace(s.inputs.pronunciation_entries[0],version='v2',target_pronunciation='second'),)
    if stale=='mixed':
        current=_pronunciation_version(tuple(entry.task_input() for entry in s.inputs.pronunciation_entries))
        identity=next(iter(s.segments));segment=s.segments[identity]
        s.segments[identity]=replace(segment,pronunciation_version=current)
        key=segment.audio_reference.rsplit('/',1)[1]
        s.records[key]=replace(s.records[key],pronunciation_version=current)
    with pytest.raises(ValueError):
        s.case.prepare_merge('workspace',s.task.id,script_id=s.script.id,
            input_references=s.merge_inputs(),grant_context=s.context('narration:merge:v1:digest'))
    assert s.calls==[] and s.task.narration_cache_key is None


@pytest.mark.parametrize('invalid',['wrong-unit','changed-script','ready-segment','incomplete-merge','stale-voice','exhausted-output'])
def test_narration_preparation_rejects_unbound_or_unusable_work(narration_preparation,invalid):
    s=narration_preparation;section=s.script.sections[0]
    context=s.context('narration:segment:v1:'+section.id+':digest',limit=1 if invalid=='exhausted-output' else 128)
    script_id='old-script' if invalid=='changed-script' else s.script.id
    if invalid=='wrong-unit':
        context=s.context('narration:legacy-stage')
    if invalid in {'ready-segment','stale-voice'}:
        s.ready()
    if invalid=='incomplete-merge':
        s.segments.pop(next(iter(s.segments)))
    if invalid=='stale-voice':
        s.voice.version='v2'
    with pytest.raises((PermissionError,ValueError)):
        if invalid in {'incomplete-merge','stale-voice'}:
            s.case.prepare_merge('workspace',s.task.id,script_id=script_id,
                input_references=s.merge_inputs(),grant_context=s.context('narration:merge:v1:digest'))
        else:
            s.case.prepare_segment('workspace',s.task.id,script_id=script_id,section_id=section.id,grant_context=context)
    assert len(s.calls)==(1 if invalid=='exhausted-output' else 0)
    assert s.task.narration_cache_key is None
    with pytest.raises(PermissionError):
        s.case.generate('workspace',s.task.id,grant_context=s.context('narration:segment:v1:'+section.id+':digest'))


def _metadata_retime_plan(durations, caption_durations=None):
    """Frozen narration/shot metadata only; no audio or media tool is invoked."""
    cues, clips, segments = [], [], []
    cursor = 0.
    for index, duration in enumerate(durations):
        segment_id = f"audio-{index}"
        cue_id = f"cue-{len(cues)}"
        for part in (caption_durations[index] if caption_durations else [duration]):
            cues.append(NarrationCue(f"cue-{len(cues)}", f"section-{index}", f"caption {len(cues)}", part, segment_id))
        clips.append(MontageClip(f"scene-{index}", cue_id, "asset", "source", "workspace://metadata-source.mp4",
            0., duration, cursor, round(cursor + duration, 3), 1., ["metadata fixture"], False))
        segments.append(SimpleNamespace(id=segment_id, duration_seconds=duration, status="ready"))
        cursor = round(cursor + duration, 3)
    plan = CreationTaskMontagePlan("metadata-plan", "workspace", "task", "script", "Fixed segments",
        "ready", cues, clips, cursor, 0, narration_audio_path="narration://artifact/original")
    repository = SimpleNamespace(get_asset=lambda *_: SimpleNamespace(segments=[SimpleNamespace(id="source", end_seconds=100.)]))
    return NarrationStageUseCase(repository, None, None, None), plan, segments


def test_local_retime_preserves_semantic_shots_manual_split_and_other_caption_groups():
    case, plan, segments = _metadata_retime_plan([7.68, 5.36, 6.64, 5.68],
        [[3.238, .376, 3.464, .602], [3.496, 1.864], [2.639, 4.001], [2.882, 2.798]])
    original = plan.clips[0]
    plan.clips[:1] = [replace(original, timeline_end_seconds=3.84, source_end_seconds=3.84, manually_selected=True),
        replace(original, id="manual-second-half", timeline_start_seconds=3.84, source_start_seconds=3.84, manually_selected=True)]
    snapshot = creation_task_montage_plan_to_payload(plan)
    segments[0] = SimpleNamespace(id=segments[0].id, duration_seconds=8.68, status="ready")
    updated = case.retime_for_segments("workspace", plan, segments, "narration://artifact/replaced-one-segment")
    assert [clip.id for clip in updated.clips] == [clip.id for clip in plan.clips]
    assert [(clip.timeline_start_seconds, clip.timeline_end_seconds) for clip in updated.clips] == [
        (0, 4.34), (4.34, 8.68), (8.68, 14.04), (14.04, 20.68), (20.68, 26.36)]
    assert all(clip.manually_selected for clip in updated.clips[:2])
    assert [(cue.id, cue.text, cue.narration_segment_id) for cue in updated.cues] == [
        (cue.id, cue.text, cue.narration_segment_id) for cue in plan.cues]
    assert [cue.duration_seconds for cue in updated.cues[4:]] == [cue.duration_seconds for cue in plan.cues[4:]]
    assert sum(cue.duration_seconds for cue in updated.cues[:4]) == pytest.approx(8.68)
    assert narration_schedule(updated.cues, updated.duration_seconds)[4].start_seconds == pytest.approx(8.68)
    assert creation_task_montage_plan_to_payload(plan) == snapshot


def test_local_retime_maps_a_manual_shot_across_two_narration_segments():
    case, plan, segments = _metadata_retime_plan([7.68, 5.36, 6.64, 5.68])
    first, second = plan.clips[:2]
    plan.clips[:2] = [replace(first, timeline_end_seconds=9., source_end_seconds=9., manually_selected=True),
        replace(second, timeline_start_seconds=9., source_end_seconds=4.04, manually_selected=True)]
    segments[0] = SimpleNamespace(id=segments[0].id, duration_seconds=8.68, status="ready")
    updated = case.retime_for_segments("workspace", plan, segments, "narration://artifact/replaced")
    assert updated.clips[0].timeline_end_seconds == 10.
    assert (updated.clips[1].timeline_start_seconds, updated.clips[1].timeline_end_seconds) == (10., 14.04)
    assert [clip.cue_id for clip in updated.clips] == [clip.cue_id for clip in plan.clips]


def test_local_retime_keeps_manual_silent_tail_outside_narration_coverage():
    case, plan, segments = _metadata_retime_plan([3.84, 5.52, 5.04, 5.44, 5.36])
    plan.clips[-1] = replace(plan.clips[-1], source_start_seconds=12., source_end_seconds=17.36)
    tail = replace(plan.clips[-1], id="clip_8944d73f572e", timeline_start_seconds=25.2,
        timeline_end_seconds=30., source_start_seconds=7.2, source_end_seconds=12., manually_selected=True)
    plan.clips.append(tail)
    plan.duration_seconds = 30.
    plan.audio_timeline = narration_audio_timeline(plan.narration_audio_path, 25.2)
    snapshot = creation_task_montage_plan_to_payload(plan)
    segments[0] = SimpleNamespace(id=segments[0].id, duration_seconds=4.84, status="ready")
    updated = case.retime_for_segments("workspace", plan, segments, "narration://artifact/replaced")
    result = updated.clips[-1]
    assert (result.id, result.cue_id, result.manually_selected) == (tail.id, tail.cue_id, True)
    assert (result.timeline_start_seconds, result.timeline_end_seconds) == (26.2, 31.)
    assert (result.source_start_seconds, result.source_end_seconds) == (7.2, 12.)
    assert updated.duration_seconds == 31.
    assert narration_schedule(updated.cues, updated.duration_seconds)[-1].end_seconds == pytest.approx(26.2)
    assert updated.audio_timeline.tracks[0].clips[0].source_end_ms == 26200
    assert creation_task_montage_plan_to_payload(plan) == snapshot


@pytest.mark.parametrize("guard", ["locked", "source_too_short", "outside_clock", "too_short_to_keep_boundary"])
def test_retime_rejects_expansion_that_cannot_preserve_the_existing_shot(guard):
    case, plan, segments = _metadata_retime_plan([7.68, 5.36])
    if guard == "locked":
        plan.clips[0].locked = True
        message = "已锁定镜头"
    elif guard == "source_too_short":
        case.repository.get_asset = lambda *_: SimpleNamespace(segments=[SimpleNamespace(id="source", end_seconds=7.68)])
        message = "超过当前镜头可用范围"
    elif guard == "outside_clock":
        plan.clips[0].timeline_start_seconds = -1
        message = "镜头边界不在原配音时间轴内"
    else:
        message = "新配音时长不足以保留当前镜头边界"
    snapshot = creation_task_montage_plan_to_payload(plan)
    segments[0] = SimpleNamespace(id=segments[0].id,
        duration_seconds=.0001 if guard == "too_short_to_keep_boundary" else 8.68, status="ready")
    with pytest.raises(ValueError, match=message):
        case.retime_for_segments("workspace", plan, segments, "narration://artifact/replaced")
    assert creation_task_montage_plan_to_payload(plan) == snapshot


@pytest.fixture
def narration_application(tmp_path,monkeypatch):
    repo,task_id,script_id,_=_seed(tmp_path,workspace_id='local')
    members=LocalMemberService(repo.database_path)
    member,token=members.join(members.create_invite_code(),'配音 normal 合成成员')
    model=tmp_path/'model';model.mkdir();(model/'manifest.json').write_text('{}')
    worker=tmp_path/'synthetic_tts.py';worker.write_text(SYNTHETIC_TTS_WORKER)
    provider=Qwen3TTSSubprocessProvider(runtime_python_path=sys.executable,worker_path=worker,model_path=model)
    generator=NarrationGenerator(tmp_path/'audio',provider)
    person,voice,*_=register_synthetic_profiles(repo,workspace_id='local',member_id=member.member_id,
        person_id='person_1',voice_id='voice_1',person_version='v1',voice_version='v1',language='zh',
        model_id=provider.model_id,person_authorization_id='person_auth_1',voice_authorization_id='voice_auth_1',
        voice_usage_scopes=('narration',))
    plan=repo.get_creation_plan('local','plan_local');plan.presenter_share_target=0
    plan.selected_person=CreationPlanPersonReference(person.id,person.version)
    plan.selected_voice=CreationPlanVoiceReference(voice.id,voice.version,person.id,person.version,
        person.authorization.id,person.authorization.version,voice.authorization.id,voice.authorization.version,'zh-CN',voice.model_id)
    plan.owner_user_id=member.member_id
    task=repo.get_creation_task('local',task_id);task.owner_user_id=member.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?',(json.dumps(asdict(plan),default=str),plan.id))
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',(json.dumps(asdict(task),default=str),task.id))
        payload=json.loads(conn.execute('SELECT payload FROM creation_task_scripts WHERE id=?',(script_id,)).fetchone()[0])
        payload['sections'].append({**payload['sections'][0],'id':'section_2'})
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?',(json.dumps(payload),script_id))
    asset,_=register_synthetic_video_asset(repo,workspace_id='local',member_id=member.member_id,
        path=tmp_path/'library/source.mp4',asset_id='asset',seconds=36)
    monkeypatch.setattr(main,'auth_context',SimpleNamespace(enabled=False))
    monkeypatch.setattr(main,'local_members_service',members)
    monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'narration_generator',generator)
    monkeypatch.setattr(main,'artifact_store',ArtifactStore(tmp_path/'artifacts'))
    monkeypatch.setattr(main,'settings',SimpleNamespace(video_output_path=str(tmp_path/'outputs'),
        video_inbox_path=str(tmp_path/'library'),effective_temporary_root_path=tmp_path/'temporary',native_portrait_worker_path=None))
    execution=main._new_creation_execution(repo);monkeypatch.setattr(main,'creation_execution',execution)
    budget=NarrationBudget(1000,10,1_000_000,300,8_000_000,1,90,1024**3,16,.1,1024**3,1_000_000)
    montage_budget=MontageBudget(10,20,1_000_000,1_000_000,30,1_000_000)
    host_budget=HostBudget(100,1,100*1024**3,1,10**12,10_000_000,narration=budget,montage=montage_budget)
    swap=psutil.swap_memory();disk=psutil.disk_io_counters();disk_devices=psutil.disk_io_counters(perdisk=True)
    monkeypatch.setattr(psutil,'cpu_percent',lambda *args,**kwargs:0.0)
    monkeypatch.setattr(psutil,'swap_memory',lambda:swap._replace(used=0))
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:disk_devices if kwargs.get('perdisk') else disk)
    resources=HostResourceProbe(repo.database_path,disk_paths=(tmp_path,),budget=host_budget)
    case=main._narration_stage_use_case(repo,narration_budget=budget)
    registration=creation_job_registration(repo,render_action=main._render_creation_stage_with_grant,
        render_is_valid=main._creation_stage_valid_with_grant,verify_identity=main._verify_creation_identity,
        narration_budget=budget,montage_budget=montage_budget,narration_stage=case)
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'device.lock',
        device_access=MemberDeviceAccess(members,identity_verifier=main._verify_creation_identity),
        registrations={'creation_job':registration},resources=resources,processes=HostProcessProbe())
    monkeypatch.setattr(main,'device_runtime',runtime)
    client=TestClient(main.app,headers={'X-Local-Member-Token':token})
    assert registration.owner.narration_budget is case.narration_budget is budget
    assert registration.executor.narration_stage is case
    assert not hasattr(registration.executor,'stage_actions')
    state=SimpleNamespace(repo=repo,task_id=task_id,script_id=script_id,plan=plan,member=member,generator=generator,
        runtime=runtime,case=case,client=client,asset=asset,worker=worker,budget=budget,host_budget=host_budget,
        fixture_OS={'cpu_percent':0,'swap_used_bytes':0,'actual_swap_before':swap.used,'disk_io_baseline':disk._asdict(),
            'per_device_disk_io_baseline':{name:value._asdict() for name,value in disk_devices.items()}})
    yield state
    assert runtime.stop(1)


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,reason='native media tools unavailable')
def test_full_two_section_narration_then_local_regeneration_uses_concrete_owner(narration_application):
    s=narration_application;repo=s.repo;url='/api/creation-tasks/'+s.task_id
    script=repo.get_creation_task_script('local',s.script_id)
    request={'script_id':script.id,'input_digest':revision_digest(script,s.plan),'requirements_confirmed':True}
    first=s.client.post(url+'/execution/start',json=request)
    assert first.status_code==202,first.text
    assert s.client.post(url+'/execution/start',json=request).json()['job']['id']==first.json()['job']['id']
    full_job=first.json()['job']['id']
    deadline=time.monotonic()+4
    while not s.runtime.run_once():
        assert time.monotonic()<deadline,s.runtime.last_error
        time.sleep(.1)
    task=repo.get_creation_task('local',s.task_id)
    assert task.stage=='montage' and task.narration_cache_key
    sections=repo.list_narration_segments('local',s.task_id,s.script_id)
    assert len(sections)==2 and all(item.status=='ready' for item in sections)
    combined=repo.get_narration_artifact('local',task.narration_cache_key)
    initial_files={item.id:_file_fact(repo.resolve_authorized_narration_audio_reference('local',item.audio_reference)) for item in sections}
    for fact in [*initial_files.values(),_file_fact(combined.audio_path)]:
        with wave.open(fact['path']) as stream:assert stream.getnchannels()==1 and stream.getframerate()==24000
    event=repo.get_task_event('local',f'event_{full_job}_narration_queued')
    assert event.payload['output']==task.narration_cache_key
    assert s.client.get(url+'/execution',params={'job_id':full_job}).status_code==200
    # Stop the queued downstream stage at the public boundary; only narration is this normal's execution target.
    stopped=s.client.post(url+'/execution/cancel');assert stopped.status_code==200,stopped.text
    # Declared intermediate fixture: a minimal montage/base using the actual generated segments and a registered source.
    cues=[NarrationCue('cue-'+item.id,item.source_section_id,item.text,item.duration_seconds,item.id) for item in sections]
    clips=[MontageClip('clip-'+str(i),cue.id,s.asset.id,s.asset.segments[i].id,
        repo.workspace_media_reference_for_asset('local',s.asset.id),s.asset.segments[i].start_seconds,
        s.asset.segments[i].start_seconds+cue.duration_seconds,float(i),float(i)+cue.duration_seconds,1,['合成 normal'],False)
        for i,cue in enumerate(cues)]
    montage=CreationTaskMontagePlan('normal-montage','local',s.task_id,s.script_id,'合成 normal','ready',cues,clips,2,0,
        narration_audio_path=repo.narration_artifact_reference(combined.cache_key))
    repo.save_creation_task_montage_plan('local',s.task_id,montage.id,creation_task_montage_plan_to_payload(montage))
    base=main._ensure_montage_base_version('local',repo.get_creation_task('local',s.task_id),montage)
    caption_ref,caption_transform=s.case.metadata.publish_subtitle_text(workspace_id='local',task_id=s.task_id,
        proposal_id='normal-fixture-caption',kind='subtitle_fixture',input_refs=['timeline:'+base.id],
        content='1\n00:00:00,000 --> 00:00:01,000\n配音 normal\n',engine='fixture-subtitle-text',parameters={'language':'zh-CN'})
    repo.create_subtitle_track(SubtitleTrack('normal-caption','local',s.task_id,base.id,'zh-CN',None,{},
        [SubtitleCue('subtitle-'+item.id,item.id,item.text,float(i),float(i+1)) for i,item in enumerate(sections)],
        'timeline',None,caption_ref,caption_transform,'valid'))
    target=sections[0];untouched=sections[1]
    preview=s.client.post(url+'/edits/preview',json={'raw_command':'这句重新配音','context_stage':'narration',
        'selected_object_refs':['narration:'+target.id]})
    assert preview.status_code==200,preview.text
    proposal=preview.json()['proposal'];assert proposal['operation']['operation_type']=='RegenerateNarrationSegment'
    confirm_url=url+'/edits/'+proposal['id']+'/confirm'
    queued=s.client.post(confirm_url,json={});assert queued.status_code==202,queued.text
    assert s.client.post(confirm_url,json={}).json()==queued.json()
    assert s.runtime.run_once(),s.runtime.last_error
    job=repo.get_job_run('local',queued.json()['job_id']);assert job.status=='succeeded',asdict(job)
    current=repo.get_creation_task('local',s.task_id);assert current.current_timeline_version_id!=base.id
    updated=repo.list_narration_segments('local',s.task_id,s.script_id)
    changed=next(item for item in updated if item.id==target.id)
    assert changed.audio_reference!=target.audio_reference
    assert next(item for item in updated if item.id==untouched.id)==untouched
    assert _file_fact(repo.resolve_authorized_narration_audio_reference('local',untouched.audio_reference))==initial_files[untouched.id]
    assert all(repo.get_narration_artifact('local',item.audio_reference.removeprefix('narration://artifact/')) for item in updated)
    new_combined=repo.get_narration_artifact('local',current.narration_cache_key)
    assert current.narration_cache_key!=combined.cache_key
    changed_montage=repo.get_creation_task_montage_plan('local',s.task_id)
    assert changed_montage['narration_audio_path']==repo.narration_artifact_reference(new_combined.cache_key)
    tracks=repo.list_subtitle_tracks('local',s.task_id,timeline_version_id=current.current_timeline_version_id)
    assert len(tracks)==1 and tracks[0].status=='unavailable' and tracks[0].artifact_ref is None
    final_files={item.id:_file_fact(repo.resolve_authorized_narration_audio_reference('local',item.audio_reference)) for item in updated}
    replay_started=time.monotonic()
    assert s.client.post(confirm_url,json={}).status_code==409
    queried=s.client.get(url+'/execution',params={'job_id':full_job});assert queried.status_code==200
    assert not s.runtime.run_once()
    with repo._connect() as conn:
        permit=dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
        operations=[dict(row) for row in conn.execute('SELECT id,proposal_id,result_version_id,status FROM edit_operations WHERE task_id=?',(s.task_id,))]
    assert permit['state']=='free' and len(operations)==1 and operations[0]['status']=='applied'
    assert operations[0]['proposal_id']==proposal['id'] and operations[0]['result_version_id']==current.current_timeline_version_id
    assert all(_file_fact(fact['path'])==fact for fact in final_files.values())
    assert all(not hasattr(main,name) for name in ('_narration_stage_with_grant','_narration_edit_stage_with_grant',
        '_generate_narration_edit_with_grant','_retime_montage_for_narration_segments'))
    flow={'full_request':request,'full_queued':first.json(),'full_stage_event':asdict(event),'public_stop_after_narration':stopped.json(),
        'segments_before':[asdict(item) for item in sections],'combined_before':asdict(combined),'initial_files':initial_files,
        'declared_intermediate_fixture':'registered source + minimal two-cue montage/base/subtitle; downstream montage/render not executed',
        'preview':preview.json(),'local_queued':queued.json(),'local_job':asdict(job),'segments_after':[asdict(item) for item in updated],
        'combined_after':asdict(new_combined),'montage_after':changed_montage,'tracks_after':[asdict(item) for item in tracks],
        'final_files':final_files,'operations':operations,'permit':permit,'replay_started_monotonic':replay_started,
        'database_path':str(repo.database_path),'worker':_file_fact(s.worker),'narration_budget':asdict(s.budget),'host_budget':asdict(s.host_budget),
        'fixture_OS':s.fixture_OS,'business_input_generator_checkpoint_mocks':0,'identity':'actual local roster/token/permit_actor/current verifier; auth disabled synthetic local workspace',
        'actual_body':'repository/ProfileRecordStore/qualification/NarrationGenerator/Qwen subprocess protocol/concat/probe/concrete owner/editorial and job checkpoint',
        'model_GUI_production_auth_resources_full_inflight_PGID':'UNVERIFIED; only original synthetic worker and finite fixture budgets/OS ports'}
    destination=os.environ.get('NARRATION_STAGE_EVIDENCE_DIR')
    if destination:(Path(destination)/'NORMAL_FLOW.json').write_text(json.dumps(flow,ensure_ascii=False,indent=2,default=str)+'\n')
