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
from app.creation_execution import revision_digest
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, MontageBudget, NarrationBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess, creation_job_registration
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.narration import NarrationGenerator
from app.production import CreationPlanPersonReference, CreationPlanVoiceReference, SubtitleCue, SubtitleTrack
from app.voice_clone import Qwen3TTSSubprocessProvider
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
    assert not {'narration','narration_edit'}&set(registration.executor.stage_actions)
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
