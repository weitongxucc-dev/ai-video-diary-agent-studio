"""A real render with a shorter unchanged voice and explicit caption/audio gaps."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation.render_stage_use_case import RenderStageUseCase
from app.creation_execution import revision_digest
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, MontageBudget, PresenterBudget, CpuRenderBudget
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.member_permits import MemberDeviceAccess, creation_job_registration
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.creation.contracts import CreationPlanVoiceReference, NarrationSegment, SubtitleCue, SubtitleTrack
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.video_jobs import VideoJobRunner
from _media_preview_test_fixtures import register_synthetic_profiles, register_synthetic_video_asset, synthetic_wav_bytes
from test_creation_execution import _seed


def file_fact(path):
    path = Path(path)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size}


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None, reason='real media tools required')
def test_public_render_publishes_physical_media_and_one_current_version(tmp_path, monkeypatch):
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local')
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), '成片 normal 合成成员')
    person, voice, *_ = register_synthetic_profiles(repo, workspace_id='local', member_id=member.member_id,
        person_id='person_1', voice_id='voice_1', person_version='v1', voice_version='v1', language='zh',
        model_id='declared-synthetic-narration', person_authorization_id='person_auth_1', voice_authorization_id='voice_auth_1',
        voice_usage_scopes=('narration',))
    plan = repo.plan_records.get_creation_plan('local', 'plan_local')
    plan.presenter_share_target = 0
    plan.duration_target_seconds = 15
    plan.selected_voice = CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version, voice.authorization.id, voice.authorization.version, 'zh-CN', voice.model_id)
    plan.owner_user_id = member.member_id
    task = repo.task_records.get_creation_task('local', task_id)
    task.owner_user_id = member.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(asdict(plan), default=str), plan.id))
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    script = repo.script_records.get_creation_task_script('local', script_id)
    repo.start_creation_task_narration('local', task_id)
    audio = tmp_path / 'declared-narration.wav'
    audio.write_bytes(synthetic_wav_bytes(2))
    cache = hashlib.sha256(b'declared-render-narration').hexdigest()
    record = NarrationArtifactRecord(id='declared-render-narration', workspace_id='local', task_id=task_id,
        script_id=script_id, voice_profile_id=voice.id, cache_key=cache, audio_path=str(audio), duration_seconds=2,
        sample_rate=8000, model_id=voice.model_id, language='zh', pronunciation_version='fixture-rules')
    segment = NarrationSegment(id='declared-render-section', workspace_id='local', task_id=task_id,
        script_id=script_id, source_section_id=script.sections[0].id, text=script.sections[0].content,
        audio_reference=repo.narration_artifacts.narration_artifact_reference(cache), duration_seconds=2, sample_rate=8000,
        model_id=voice.model_id, voice_profile_id=voice.id, voice_version=voice.version, language='zh',
        pronunciation_version='fixture-rules', status='ready')
    repo.narration_completion.complete_creation_task_narration(record, segments=[segment])
    asset, material_ref = register_synthetic_video_asset(repo, workspace_id='local', member_id=member.member_id,
        path=tmp_path / 'library/source.mp4', asset_id='declared-render-broll', seconds=21)
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'production_repo', repo)
    from app.quality_inspection import QualityCheckRepository
    monkeypatch.setattr(main, 'quality_check_repo', QualityCheckRepository(repo.database_path))
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'video_job_runner', VideoJobRunner(repo.job_records))
    monkeypatch.setattr(main, 'settings', replace(main.settings, video_output_path=str(tmp_path / 'outputs'),
        video_inbox_path=str(tmp_path / 'library'), temporary_root_path=str(tmp_path / 'temporary'),
        native_portrait_worker_path=None, hermes_radar_path=None, obsidian_vault_path=None))
    monkeypatch.setattr(main, 'creation_execution', main._new_creation_execution(repo))
    case = main._render_stage_use_case(repo)
    assert case.repository is case.metadata.repository is case.person_preview.repository is repo
    assert case.person_preview.metadata is case.metadata and case.video_job_runner is main.video_job_runner and case.settings is main.settings
    cue = NarrationCue('declared-render-cue', segment.source_section_id, segment.text, 2, segment.id, timeline_start_seconds=.5)
    clip = MontageClip('declared-render-clip', cue.id, asset.id, asset.segments[0].id, material_ref,
        0, 5, 0, 5, 1, ['声明的合成 B-roll'], False)
    montage = CreationTaskMontagePlan('declared-render-montage', 'local', task_id, script_id, '合成成片 normal',
        'ready', [cue], [clip, replace(clip, id='declared-middle-clip', segment_id=asset.segments[1].id,
            source_start_seconds=7, source_end_seconds=12, timeline_start_seconds=5, timeline_end_seconds=10),
            replace(clip, id='declared-tail-clip', segment_id=asset.segments[2].id, source_start_seconds=14,
                source_end_seconds=19, timeline_start_seconds=10, timeline_end_seconds=15)],
        15, 0, narration_audio_path=segment.audio_reference)
    from app.audio_timeline import narration_audio_timeline
    montage.audio_timeline = narration_audio_timeline(segment.audio_reference, 2)
    montage.audio_timeline.tracks[0].clips[0].timeline_start_ms = 500
    voice_hash = hashlib.sha256(audio.read_bytes()).hexdigest()
    repo.montage_plan_adoption.save_creation_task_montage_plan('local', task_id, montage.id, creation_task_montage_plan_to_payload(montage))
    base = case.metadata.ensure_montage_base('local', repo.task_records.get_creation_task('local', task_id), montage)
    caption_ref, caption_transform = case.metadata.publish_subtitle_text(workspace_id='local', task_id=task_id,
        proposal_id='declared-render-caption', kind='subtitle_fixture', input_refs=['timeline:' + base.id],
        content='1\n00:00:00,500 --> 00:00:02,500\n' + segment.text + '\n', engine='fixture-subtitle-text', parameters={'language': 'zh-CN'})
    repo.subtitle_records.create_subtitle_track(SubtitleTrack('declared-render-caption', 'local', task_id, base.id, 'zh-CN', None, {},
        [SubtitleCue('declared-caption-cue', cue.id, segment.text, .5, 2.5)], 'timeline', None, caption_ref, caption_transform, 'valid'))
    gib=1024**3
    # Exact values from the accepted person normal's actual queued render prerequisite.
    budget=PresenterBudget(1,8*1024**2,20,4*1024**2,1,90,3*gib//2,16,.1,2*gib,32*1024**2)
    montage_budget=MontageBudget(10,20,1_000_000,1_000_000,30,1_000_000)
    render=CpuRenderBudget(300,400_000_000,1920,1920,10_000_000,20,0,8_000_000,1,90,512*1024**2,64*1024**2,16)
    from test_host_resource_probes import _render_budget
    render = replace(render, running_cpu_policy=_render_budget().running_cpu_policy)
    from test_creation_audio_mix import audio_budget as calibrated_audio_budget
    audio_budget = replace(calibrated_audio_budget(), max_output_duration_seconds=15)
    host_budget=HostBudget(91,gib,16*gib,gib,10**9,128*1024**2,presenter=budget,presenter_cpu_render=render,cpu_render=render,montage=montage_budget,cpu_audio=audio_budget)
    monkeypatch.setattr(main, '_device_host_budget', lambda: host_budget)
    swap=psutil.swap_memory();disk=psutil.disk_io_counters();devices=psutil.disk_io_counters(perdisk=True)
    monkeypatch.setattr(psutil,'cpu_percent',lambda *args,**kwargs:0.0)
    monkeypatch.setattr(psutil,'swap_memory',lambda:swap._replace(used=0))
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:devices if kwargs.get('perdisk') else disk)
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    resources=HostResourceProbe(repo.database_path,disk_paths=(tmp_path,),budget=host_budget, memory_monitor=IsolatedMemoryMonitor())
    observations = {'renders': [], 'paired_publications': [], 'grant_validity': [], 'qc_triggers': []}
    original_render, original_publish = PicturePreviewRenderer.render_montage, repo.montage_plan_adoption.publish_creation_task_render
    from app.creation.quality_inspection_use_case import QualityInspectionUseCase
    original_valid, original_qc = RenderStageUseCase.valid_with_grant, QualityInspectionUseCase.start_for_render

    def observe_render(renderer, *args, **kwargs):
        observations['renders'].append({'request_id': kwargs['render_context'].grant.request_id})
        return original_render(renderer, *args, **kwargs)

    def observe_publish(*args, **kwargs):
        value = original_publish(*args, **kwargs)
        observations['paired_publications'].append({'plan_id': args[2], 'version_id': args[4].id, 'expected_version': kwargs['expected_current_version_id']})
        return value

    def observe_valid(owner, workspace_id, target_id, grant):
        value = original_valid(owner, workspace_id, target_id, grant)
        observations['grant_validity'].append({'request_id': grant.grant.request_id, 'valid': value})
        return value

    def observe_qc(service, runtime, workspace_id, target_id, job_id):
        observations['qc_triggers'].append([workspace_id, target_id, job_id])
        return original_qc(service, runtime, workspace_id, target_id, job_id)

    monkeypatch.setattr(PicturePreviewRenderer, 'render_montage', observe_render)
    monkeypatch.setattr(repo.montage_plan_adoption, 'publish_creation_task_render', observe_publish)
    monkeypatch.setattr(RenderStageUseCase, 'valid_with_grant', observe_valid)
    monkeypatch.setattr(QualityInspectionUseCase, 'start_for_render', observe_qc)
    registration=creation_job_registration(repo,render_action=main._render_creation_stage_with_grant,
        render_is_valid=main._creation_stage_valid_with_grant,verify_identity=main._verify_creation_identity,
        presenter_budget=budget,presenter_cpu_render_budget=render,cpu_render_budget=render,montage_budget=montage_budget,
        audio_mix_facts=lambda workspace, target, montage, *, actor: main._task_audio().mix_facts(
            workspace, target, montage.audio_timeline, montage.duration_seconds, actor=actor,
            normalize_voice_sources=montage.narration_linkage_created_audio_timeline, budget=audio_budget))
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'device.lock',
        device_access=MemberDeviceAccess(members,identity_verifier=main._verify_creation_identity),
        registrations={'creation_job':registration},resources=resources,processes=HostProcessProbe())
    quality_service = main._quality_inspection_use_case(repo)
    registration.executor.on_render_completed = lambda workspace, target, job: quality_service.start_for_render(runtime, workspace, target, job)
    monkeypatch.setattr(main, 'device_runtime', runtime)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    client=TestClient(main.app,headers={'X-Local-Member-Token':token})
    url='/api/creation-tasks/'+task_id
    destination=os.environ.get('RENDER_STAGE_EVIDENCE_DIR')
    try:
        if destination:
            Path(destination, 'RENDER_WINDOW_START').write_text(str(time.monotonic()))
        request={'script_id':script_id,'input_digest':revision_digest(script,plan),'requirements_confirmed':True}
        queued=client.post(url+'/execution/start',json=request)
        assert queued.status_code==202 and queued.json()['current_stage']=='render',queued.text
        duplicate=client.post(url+'/execution/start',json=request)
        assert duplicate.status_code==202 and duplicate.json()['job']['id']==queued.json()['job']['id'],duplicate.text
        assert not observations['renders']
        assert runtime.run_once(),runtime.last_error
        outer=repo.job_records.get_job_run('local',queued.json()['job']['id'])
        assert outer.status=='succeeded',asdict(outer)
        task=repo.task_records.get_creation_task('local',task_id)
        rendered=repo.get_creation_task_montage_plan('local',task_id)
        version=repo.timeline_records.get_timeline_version('local',task.current_timeline_version_id)
        child=next(j for j in repo.job_records.list_job_runs('local',task_id) if j.kind=='creation_task_montage_render')
        transform=repo.lineage_records.get_transform_run('local','transform_'+child.id)
        assert child.status==transform.status=='succeeded' and version.id=='timeline_'+child.id and version.parent_version_id==base.id
        assert task.output_ready and task.stage=='review' and rendered['status']=='rendered'
        assert version.snapshot['render_input_timeline_version_id']==base.id
        assert version.snapshot['creation_render_request_id']==observations['renders'][0]['request_id']
        assert version.snapshot['render_input_digest']==child.input_digest==transform.input_digest
        assert version.snapshot['render_input_subtitles_visible'] and version.snapshot['render_input_subtitle_tracks'][0]['cues'][0]['text']==segment.text
        assert child.output_refs==transform.output_refs==version.snapshot['output_refs']==[rendered['output_path'],rendered['subtitle_path']]
        artifacts=[a for a in repo.execution_records.list_artifact_records('local',task_id) if a.producer_job_id==child.id]
        assert {a.kind for a in artifacts}=={'rendered_video','subtitle_track'}
        facts={a.kind:file_fact(main.artifact_store.resolve_file(a.logical_ref)) for a in artifacts}
        assert all(a.sha256==facts[a.kind]['sha256'] and a.size_bytes==facts[a.kind]['size_bytes'] for a in artifacts)
        assert segment.text in Path(facts['subtitle_track']['path']).read_text()
        assert '00:00:00,500 --> 00:00:02,500' in Path(facts['subtitle_track']['path']).read_text()
        assert rendered['duration_seconds'] == 15
        assert rendered['cues'][0]['duration_seconds'] == 2
        assert rendered['audio_timeline']['tracks'][0]['clips'][0]['source_end_ms'] == 2000
        assert hashlib.sha256(audio.read_bytes()).hexdigest() == voice_hash
        edges=[e for e in repo.lineage_records.list_dependency_edges('local',task_id) if e.created_by_transform_id==transform.id]
        assert {(e.upstream_ref,e.downstream_ref) for e in edges}=={(u,d) for u in transform.input_refs for d in transform.output_refs}
        event=repo.execution_records.get_task_event('local',f'event_{outer.id}_render_succeeded')
        assert event.payload['stage']=='render' and event.payload['status']=='completed'
        assert event.payload['output']==version.id+':'+rendered['output_path']
        assert len(observations['renders'])==len(observations['paired_publications'])==len(observations['grant_validity'])==len(observations['qc_triggers'])==1
        assert observations['grant_validity'][0]['valid'] and observations['paired_publications'][0]['expected_version']==base.id
        # Wait only for the original QC bridge to settle so its native commands can be attributed.
        deadline=time.monotonic()+20
        quality=client.get(url+'/quality-check')
        while quality.status_code==200 and quality.json()['check_state']=='checking':
            assert time.monotonic()<deadline,quality.text
            time.sleep(.1)
            quality=client.get(url+'/quality-check')
        assert quality.status_code==200 and quality.json()['check_state'] in {'done','failed'},quality.text
        counts=(len(repo.job_records.list_job_runs('local',task_id)),len(repo.timeline_records.list_timeline_versions('local',task_id)),len(repo.lineage_records.list_transform_runs('local',task_id)))
        before=json.dumps(observations,sort_keys=True)
        queries=[client.get(url+'/execution',params={'job_id':outer.id}),client.get(url)]
        assert all(q.status_code==200 for q in queries),[q.text for q in queries]
        assert not runtime.run_once()
        assert json.dumps(observations,sort_keys=True)==before
        assert counts==(len(repo.job_records.list_job_runs('local',task_id)),len(repo.timeline_records.list_timeline_versions('local',task_id)),len(repo.lineage_records.list_transform_runs('local',task_id)))
        assert not repo.list_person_renders('local',task_id)
        if destination:
            Path(destination,'NORMAL_SUMMARY.json').write_text(json.dumps({'task':asdict(task),'outer_job':asdict(outer),
                'child_job':asdict(child),'transform':asdict(transform),'version':asdict(version),'base':asdict(base),
                'rendered':rendered,'artifacts':[asdict(a) for a in artifacts],'file_facts':facts,'edges':[asdict(e) for e in edges],
                'event':asdict(event),'observations':observations,'query_status':[q.status_code for q in queries],
                'quality_state':quality.json(),'counts_after_queries':counts,'render_budget':asdict(render),'host_budget':asdict(host_budget),
                'OS_fixture':{'cpu_percent':0,'swap_used_bytes':0,'actual_swap_before':swap.used,'disk_io_baseline':disk._asdict(),
                    'per_device_disk_io_baseline':{name:value._asdict() for name,value in devices.items()}},
                'source_fixtures':'declared authorized 2s WAV/section and ready B-roll montage/base/registered subtitle; one actual input-video ffmpeg setup',
                'model_person_narration_execution':0,'business_renderer_validator_substitutes':0,
            },ensure_ascii=False,indent=2,default=str)+'\n')
    finally:
        if destination:
            Path(destination,'RENDER_WINDOW_END').write_text(str(time.monotonic()))
        assert runtime.stop(1)
