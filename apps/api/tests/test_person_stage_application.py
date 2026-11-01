"""One full/local person-stage normal in the explicit synthetic provider domain."""
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from threading import Event
import time
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.avatar_lipsync import SubprocessAvatarAndLipSyncProvider
from app.creation_execution import CreationStageGrantContext, revision_digest
from app.creation.contracts import EditIntent, EditProposal
from app.creation.editing.person_preview_use_case import PersonPreviewUseCase
from app.creation.person_stage_use_case import PersonStageUseCase, PersonStageRejected
from app.device_scheduler import DeviceScheduler
from app.granted_subprocess import GrantedCommandCancelled
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostBudget,HostProcessProbe,HostResourceProbe,PresenterBudget,MontageBudget,CpuRenderBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess,creation_job_registration
from app.montage import CreationTaskMontagePlan,MontageClip,NarrationCue,creation_task_montage_plan_to_payload
from app.creation.contracts import CreationPlanPersonReference, CreationPlanVoiceReference, NarrationSegment
from app.production import NarrationArtifactRecord, PersonRender
from app.video_jobs import VideoJobRunner
from test_creation_execution import _seed
from _media_preview_test_fixtures import register_synthetic_profiles,synthetic_wav_bytes,write_synthetic_video
from test_device_scheduler import Owners, Resources, Processes, _intent
from test_metadata_edit_use_case import editorial_rows
from test_timeline_restore_application import restore_state


SYNTHETIC_PERSON_WORKER='''import json,shutil,sys
from pathlib import Path
request=json.loads(Path(sys.argv[sys.argv.index('--request')+1]).read_text())
output=Path(request['output_path']);output.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(Path(__file__).with_name('synthetic-person.mp4'),output)
Path(sys.argv[sys.argv.index('--response')+1]).write_text(json.dumps(dict(schema_version=1,status='succeeded',artifact_path=str(output),duration_seconds=2.0,alignment_evidence='synthetic finite protocol output; no model or perceptual lipsync evidence')))
'''


def _file_fact(path):
    path=Path(path);stat=path.stat()
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':stat.st_size,
        'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns,'inode':stat.st_ino}


@pytest.mark.parametrize('change', ['none', 'timeline', 'cancel', 'revoked'])
def test_person_edit_checkpoint_keeps_identity_checks_outside_writer(tmp_path, monkeypatch, change):
    state = restore_state(tmp_path)
    provider = SimpleNamespace(model_id='declared-person-input')
    preview = PersonPreviewUseCase(state.repo, state.case, provider, provider)
    case = PersonStageUseCase(state.repo, state.case, preview, None, None)
    # Substitute the generated-render boundary; exercise real editing and SQLite,
    # without running or claiming media/model qualification.
    render = PersonRender('declared-render', 'ws', state.task.id,
        'person', 'v1', 'fixture-voice', 'fixture-v1', 'fixed-segment', 'person:person:v1',
        provider.model_id, 'natural', 'neutral', hashlib.sha256(b'declared-person').hexdigest(), 'ready',
        output_path=str(state.source), duration_seconds=4, alignment_evidence='declared input fixture')
    state.case.picture_renderer = SimpleNamespace(person_path=lambda current: Path(current.output_path))
    monkeypatch.setattr(case, 'generate_record', lambda *args, **kwargs: render)
    payload = dict(clip_id='clip', cue_id='cue', narration_segment_id='fixed-segment',
        person_profile_id='person', person_version='v1', expected_asset_id='asset',
        expected_segment_id='segment', expected_person_render_id=None, motion='natural',
        expression='neutral', base_version_id=state.base.id,
        expected_audio_reference=state.segments[0].audio_reference,
        source_start_seconds=0, source_end_seconds=4)
    intent = state.repo.edit_records.create_edit_intent(EditIntent('person-intent', 'ws', state.task.id,
        '换人物', 'montage', [], None, 'person', {}, {}, 1, 'interpreted'))
    proposal = state.repo.edit_records.create_edit_proposal(EditProposal('person-proposal', 'ws', state.task.id,
        intent.id, '换人物', ['clip:clip'], {'operation_type':'ChangePersonSegment', 'payload':payload},
        [], {}, []))

    class IdentityOwners(Owners):
        def check_current(self, request, conn):
            # This independent writer models a slow remote boundary's requirement
            # that ordinary editing can still write while permission is observed.
            with sqlite3.connect(state.repo.database_path, timeout=0) as writer:
                writer.execute('BEGIN IMMEDIATE')
            return 'unauthorized' if self.revoked else 'ready'

        def check_local(self, request, conn, observed):
            assert conn.in_transaction
            return 'unauthorized' if self.revoked else 'ready'

    owners = IdentityOwners()
    scheduler = DeviceScheduler(state.repo.database_path, tmp_path/'checkpoint.lock',
        owners=owners, resources=Resources(), processes=Processes())
    scheduler.submit(_intent('person-edit', workspace='ws'), 'member-a')
    grant = scheduler.pick_and_grant()
    cancellation = Event()
    context = CreationStageGrantContext(scheduler, grant, time.monotonic()+30, 1024, 1, cancellation)
    commit = case.prepare_edit('ws', state.task.id, context, proposal.id)
    context.check()
    if change == 'timeline':
        state.case.save(state.task, main.metadata_edit_command(state.task, state.identity, 'ChangeCrop',
            {'base_version_id':state.base.id, 'clip_id':'clip', 'fit_mode':'fit', 'crop_x':.3, 'crop_y':.7}))
    elif change == 'cancel':
        cancellation.set()
    elif change == 'revoked':
        owners.revoked = True
    before = editorial_rows(state.repo)
    complete = lambda: scheduler.complete_unit(grant, checkpoint_database_path=state.repo.database_path,
        checkpoint_write=lambda conn, current: commit(conn).id)
    if change == 'none':
        complete()
        current = state.repo.get_creation_task_montage_plan('ws', state.task.id)
        assert current['clips'][0]['person_render_id'] == render.id
        assert state.repo.edit_records.get_edit_proposal('ws', proposal.id).status == 'confirmed'
    else:
        expected = {'timeline':PersonStageRejected, 'cancel':GrantedCommandCancelled, 'revoked':PermissionError}[change]
        with pytest.raises(expected):
            complete()
        assert editorial_rows(state.repo) == before
        scheduler.complete_unit(grant, checkpoint_database_path=state.repo.database_path,
            checkpoint_write=lambda conn, current: None, cancelled=True)


@pytest.fixture
def person_application(tmp_path,monkeypatch):
    repo,task_id,script_id,_=_seed(tmp_path,workspace_id='local')
    members=LocalMemberService(repo.database_path)
    member,token=members.join(members.create_invite_code(),'人物 normal 合成成员')
    runtime_root=tmp_path/'runtime'
    for name in ('scripts/inference.py','models/musetalkV15/unet.pth'):
        marker=runtime_root/name;marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('synthetic fixture marker; no model')
    worker=tmp_path/'synthetic_person_worker.py';worker.write_text(SYNTHETIC_PERSON_WORKER)
    synthetic_video=write_synthetic_video(tmp_path/'synthetic-person.mp4',seconds=2)
    provider=SubprocessAvatarAndLipSyncProvider(runtime_path=sys.executable,worker_path=worker,runtime_root=runtime_root,
        model_id='synthetic-fast-person-protocol',supported_languages=('zh','en'))
    person,voice,*_=register_synthetic_profiles(repo,workspace_id='local',member_id=member.member_id,
        person_id='person_1',voice_id='voice_1',person_version='v1',voice_version='v1',language='zh',model_id='synthetic-recorded-narration',
        person_authorization_id='person_auth_1',voice_authorization_id='voice_auth_1')
    plan=repo.plan_records.get_creation_plan('local','plan_local');plan.presenter_share_target=100;plan.lipsync_quality='fast'
    plan.selected_person=CreationPlanPersonReference(person.id,person.version)
    plan.selected_voice=CreationPlanVoiceReference(voice.id,voice.version,person.id,person.version,person.authorization.id,
        person.authorization.version,voice.authorization.id,voice.authorization.version,'zh-CN',voice.model_id)
    plan.owner_user_id=member.member_id
    task=repo.task_records.get_creation_task('local',task_id);task.owner_user_id=member.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?',(json.dumps(asdict(plan),default=str),plan.id))
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',(json.dumps(asdict(task),default=str),task.id))
        payload=json.loads(conn.execute('SELECT payload FROM creation_task_scripts WHERE id=?',(script_id,)).fetchone()[0])
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?',(json.dumps(payload),script_id))
    script=repo.script_records.get_creation_task_script('local',script_id);repo.start_creation_task_narration('local',task_id)
    sections=[]
    for index,section in enumerate(script.sections):
        audio=tmp_path/f'declared-narration-{index}.wav';audio.write_bytes(synthetic_wav_bytes(2))
        cache=hashlib.sha256(f'declared-person-narration-{index}'.encode()).hexdigest()
        record=repo.save_narration_artifact(NarrationArtifactRecord(id='narration-'+str(index),workspace_id='local',task_id=task_id,
            script_id=script_id,voice_profile_id=voice.id,cache_key=cache,audio_path=str(audio),duration_seconds=2,sample_rate=8000,
            model_id='declared-synthetic-narration',language='zh',pronunciation_version='fixture-rules'))
        sections.append(NarrationSegment(id='normal-person-narration-'+str(index),workspace_id='local',task_id=task_id,
            script_id=script_id,source_section_id=section.id,text=section.content,audio_reference=repo.narration_artifact_reference(cache),
            duration_seconds=2,sample_rate=8000,model_id=record.model_id,voice_profile_id=voice.id,voice_version=voice.version,
            language='zh',pronunciation_version=record.pronunciation_version,status='ready'))
    combined=tmp_path/'declared-combined.wav';combined.write_bytes(synthetic_wav_bytes(2))
    combined_record=NarrationArtifactRecord(id='normal-person-combined',workspace_id='local',task_id=task_id,script_id=script_id,
        voice_profile_id=voice.id,cache_key=hashlib.sha256(b'declared-person-combined').hexdigest(),audio_path=str(combined),
        duration_seconds=2,sample_rate=8000,model_id='declared-synthetic-narration',language='zh',pronunciation_version='fixture-rules')
    repo.narration_completion.complete_creation_task_narration(combined_record,segments=sections)
    monkeypatch.setattr(main,'auth_context',SimpleNamespace(enabled=False))
    monkeypatch.setattr(main,'local_members_service',members);monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'avatar_lipsync_provider',provider)
    monkeypatch.setattr(main,'artifact_store',ArtifactStore(tmp_path/'artifacts'))
    monkeypatch.setattr(main,'video_job_runner',VideoJobRunner(repo.job_records))
    monkeypatch.setattr(main,'settings',replace(main.settings,video_output_path=str(tmp_path/'outputs'),video_inbox_path=str(tmp_path/'library'),
        temporary_root_path=str(tmp_path/'temporary'),native_portrait_worker_path=None,hermes_radar_path=None,obsidian_vault_path=None))
    monkeypatch.setattr(main,'creation_execution',main._new_creation_execution(repo))
    gib=1024**3
    # Exact original host-probe PresenterBudget, including its one-section cap.
    budget=PresenterBudget(1,8*1024**2,20,4*1024**2,1,90,3*gib//2,16,.1,2*gib,32*1024**2)
    montage_budget=MontageBudget(10,20,1_000_000,1_000_000,30,1_000_000)
    render=CpuRenderBudget(300,400_000_000,1920,1920,10_000_000,20,0,8_000_000,1,90,512*1024**2,64*1024**2,16)
    host_budget=HostBudget(91,gib,16*gib,gib,10**9,128*1024**2,presenter=budget,presenter_cpu_render=render,cpu_render=render,montage=montage_budget)
    swap=psutil.swap_memory();disk=psutil.disk_io_counters();devices=psutil.disk_io_counters(perdisk=True)
    monkeypatch.setattr(psutil,'cpu_percent',lambda *args,**kwargs:0.0)
    monkeypatch.setattr(psutil,'swap_memory',lambda:swap._replace(used=0))
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:devices if kwargs.get('perdisk') else disk)
    resources=HostResourceProbe(repo.database_path,disk_paths=(tmp_path,),budget=host_budget)
    case=main._person_stage_use_case(repo)
    registration=creation_job_registration(repo,render_action=main._render_creation_stage_with_grant,render_is_valid=main._creation_stage_valid_with_grant,
        verify_identity=main._verify_creation_identity,presenter_budget=budget,presenter_cpu_render_budget=render,cpu_render_budget=render,
        montage_budget=montage_budget,presenter_models=main._presenter_model_ids(),resolve_presenter_output=case.metadata.picture_renderer.person_path,
        person_stage=case)
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'device.lock',
        device_access=MemberDeviceAccess(members,identity_verifier=main._verify_creation_identity),
        registrations={'creation_job':registration},resources=resources,processes=HostProcessProbe())
    monkeypatch.setattr(main,'device_runtime',runtime)
    client=TestClient(main.app,headers={'X-Local-Member-Token':token})
    assert registration.executor.person_stage is case
    assert case.repository is case.metadata.repository is case.person_preview.repository is repo
    assert case.person_preview.metadata is case.metadata and case.person_preview.provider('fast') is provider
    assert case.settings is main.settings and not hasattr(registration.executor,'stage_actions')
    state=SimpleNamespace(repo=repo,task_id=task_id,script_id=script_id,plan=plan,member=member,sections=sections,combined=combined_record,
        case=case,runtime=runtime,client=client,provider=provider,worker=worker,synthetic_video=synthetic_video,budget=budget,host_budget=host_budget,
        fixture_OS={'cpu_percent':0,'swap_used_bytes':0,'actual_swap_before':swap.used,'aggregate_disk':disk._asdict(),
            'per_device_disk':{name:value._asdict() for name,value in devices.items()}})
    yield state
    assert runtime.stop(1)


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,reason='native media tools unavailable')
def test_full_person_segment_then_selected_regeneration_preserves_early_child(person_application):
    s=person_application;repo=s.repo;url='/api/creation-tasks/'+s.task_id
    narration_before=[asdict(item) for item in repo.narration_records.list_narration_segments('local',s.task_id,s.script_id)]
    narration_files={item.id:_file_fact(repo.resolve_authorized_narration_audio_reference('local',item.audio_reference)) for item in s.sections}
    request={'script_id':s.script_id,'input_digest':revision_digest(repo.script_records.get_creation_task_script('local',s.script_id),s.plan),'requirements_confirmed':True}
    first=s.client.post(url+'/execution/start',json=request);assert first.status_code==202,first.text
    assert first.json()['current_stage']=='person_render'
    assert s.client.post(url+'/execution/start',json=request).json()['job']['id']==first.json()['job']['id']
    full_job=first.json()['job']['id'];deadline=time.monotonic()+4
    while not s.runtime.run_once():
        assert time.monotonic()<deadline,s.runtime.last_error
        time.sleep(.1)
    renders=repo.list_person_renders('local',s.task_id)
    assert len(renders)==1 and all(r.status=='ready' and r.artifact_ref and r.alignment_evidence for r in renders)
    by_segment={r.narration_segment_id:r for r in renders}
    initial_files={r.id:_file_fact(s.case.metadata.picture_renderer.person_path(r)) for r in renders}
    initial_child_jobs=[job for job in repo.job_records.list_job_runs('local',s.task_id) if job.kind=='person_render']
    assert len(initial_child_jobs)==1 and all(job.status=='succeeded' for job in initial_child_jobs)
    event=repo.execution_records.get_task_event('local',f'event_{full_job}_person_render_queued')
    assert set(event.payload['output'].split(','))=={r.id for r in renders}
    stopped=s.client.post(url+'/execution/cancel');assert stopped.status_code==200,stopped.text
    cues=[NarrationCue('cue-'+item.id,item.source_section_id,item.text,2,item.id) for item in s.sections]
    cue=cues[0];original=by_segment[cue.narration_segment_id]
    clips=[MontageClip('clip-person-'+str(i),cue.id,'person:'+original.id,cue.narration_segment_id,
        'person-render://'+original.id,float(i),float(i+1),float(i),float(i+1),1,['合成人物 normal'],False,
        person_render_id=original.id,role='a_roll') for i in range(2)]
    montage=CreationTaskMontagePlan('normal-person-montage','local',s.task_id,s.script_id,'合成 normal','ready',cues,clips,2,0,
        narration_audio_path=repo.narration_artifact_reference(s.combined.cache_key))
    payload=creation_task_montage_plan_to_payload(montage);repo.montage_plan_adoption.save_creation_task_montage_plan('local',s.task_id,montage.id,payload)
    base=main._ensure_montage_base_version('local',repo.task_records.get_creation_task('local',s.task_id),montage)
    # Actual queued render is the existing rendered-only preview prerequisite, not a fixture status rewrite.
    render_queued=s.client.post(url+'/execution/resume');assert render_queued.status_code==202,render_queued.text
    assert render_queued.json()['current_stage']=='render',render_queued.text
    assert s.runtime.run_once(),s.runtime.last_error
    rendered_job=repo.job_records.get_job_run('local',render_queued.json()['job']['id'])
    assert rendered_job.status=='succeeded',asdict(rendered_job)
    rendered_task=repo.task_records.get_creation_task('local',s.task_id)
    payload=repo.get_creation_task_montage_plan('local',s.task_id)
    assert payload['status']=='rendered' and payload['output_path']
    rendered_file=_file_fact(main.artifact_store.resolve_file(payload['output_path']))
    base=repo.timeline_records.get_timeline_version('local',rendered_task.current_timeline_version_id)
    preview=s.client.post(url+'/edits/preview',json={'raw_command':'这里只重做口型，其他都不要动','context_stage':'review',
        'selected_object_refs':['clip:'+clips[0].id],'playhead_ms':500})
    assert preview.status_code==200,preview.text
    proposal=preview.json()['proposal'];assert proposal['operation']['operation_type']=='RegeneratePersonRender'
    confirm_url=url+'/edits/'+proposal['id']+'/confirm'
    queued=s.client.post(confirm_url,json={});assert queued.status_code==202,queued.text
    assert s.client.post(confirm_url,json={}).json()==queued.json()
    assert s.runtime.run_once(),s.runtime.last_error
    job=repo.job_records.get_job_run('local',queued.json()['job_id']);assert job.status=='succeeded',asdict(job)
    current=repo.task_records.get_creation_task('local',s.task_id);assert current.current_timeline_version_id!=base.id
    changed=repo.get_creation_task_montage_plan('local',s.task_id)
    assert changed['clips'][0]['person_render_id']!=clips[0].person_render_id
    assert changed['clips'][1]==payload['clips'][1] and changed['cues']==payload['cues']
    assert [asdict(item) for item in repo.narration_records.list_narration_segments('local',s.task_id,s.script_id)]==narration_before
    assert all(_file_fact(fact['path'])==fact for fact in [*initial_files.values(),*narration_files.values()])
    assert _file_fact(rendered_file['path'])==rendered_file
    final_renders=repo.list_person_renders('local',s.task_id);assert len(final_renders)==2
    replacement=repo.get_person_render('local',s.task_id,changed['clips'][0]['person_render_id'])
    assert replacement.status=='ready' and replacement.cache_key!=by_segment[s.sections[0].id].cache_key
    child_jobs=[j for j in repo.job_records.list_job_runs('local',s.task_id) if j.kind=='person_render']
    assert len(child_jobs)==2 and all(j.status=='succeeded' for j in child_jobs)
    replay_started=time.monotonic()
    assert s.client.post(confirm_url,json={}).status_code==409
    render_query=s.client.get(url+'/person-renders');assert render_query.status_code==200,render_query.text
    assert render_query.json()['total_count']==2
    local_job_query=s.client.get(url+'/execution',params={'job_id':job.id})
    assert local_job_query.status_code==409,local_job_query.text
    task_query=s.client.get(url);assert task_query.status_code==200,task_query.text
    assert task_query.json()['task']['current_timeline_version_id']==current.current_timeline_version_id
    assert not s.runtime.run_once()
    with repo._connect() as conn:
        permit=dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
        operations=[dict(r) for r in conn.execute('SELECT id,proposal_id,result_version_id,status FROM edit_operations WHERE task_id=?',(s.task_id,))]
    assert permit['state']=='free' and len(operations)==1 and operations[0]['status']=='applied'
    assert operations[0]['proposal_id']==proposal['id'] and operations[0]['result_version_id']==current.current_timeline_version_id
    assert not hasattr(main,'_person_stage_with_grant') and not hasattr(main,'_person_edit_stage_with_grant')
    flow={'declared_narration':'one actual registered synthetic WAV/combined/profile; no narration provider or stage executed',
        'narration_before':narration_before,'narration_files':narration_files,'full_request':request,'full_queued':first.json(),
        'full_stage_event':asdict(event),'initial_renders':[asdict(r) for r in renders],'initial_files':initial_files,
        'initial_child_jobs':[asdict(j) for j in initial_child_jobs],'public_stop_after_person':stopped.json(),
        'declared_intermediate_fixture':'one actual ready PersonRender legally split into two adjacent clips of one cue via original source_range; no second independent render claimed',
        'actual_render_prerequisite':{'queued':render_queued.json(),'job':asdict(rendered_job),'base_version':asdict(base),'file':rendered_file},
        'preview':preview.json(),'queued':queued.json(),'local_job':asdict(job),'replacement':asdict(replacement),
        'final_renders':[asdict(r) for r in final_renders],'child_jobs':[asdict(j) for j in child_jobs],'montage_after':changed,
        'operations':operations,'render_query':render_query.json(),'task_query':task_query.json(),
        'local_job_query':{'status_code':local_job_query.status_code,'body':local_job_query.json()},
        'permit':permit,'replay_started_monotonic':replay_started,'database_path':str(repo.database_path),
        'worker':_file_fact(s.worker),'synthetic_video':_file_fact(s.synthetic_video),'presenter_budget':asdict(s.budget),
        'host_budget':asdict(s.host_budget),'fixture_OS':s.fixture_OS,
        'business_qualification_provider_render_probe_runner_checkpoint_mocks':0,
        'external_fixture':'fast original SubprocessAvatar provider with isolated synthetic worker and marker files; finite valid MP4 copy, no actual model/lipsync quality claim',
        'production_auth_GUI_calibration_high_model_full_inflight_PGID':'UNVERIFIED'}
    destination=os.environ.get('PERSON_STAGE_EVIDENCE_DIR')
    if destination:(Path(destination)/'NORMAL_FLOW.json').write_text(json.dumps(flow,ensure_ascii=False,indent=2,default=str)+'\n')
