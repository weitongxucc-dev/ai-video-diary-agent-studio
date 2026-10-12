"""A long movie's selected-clip preview remains a silent five-second unit."""
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.host_resource_probes import CpuRenderBudget, HostProcessProbe, HostCapacity, HostBudget, HostResourceProbe
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import MaterialAsset, MaterialSegment, TimelineVersion
from test_task_media_grants import case


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='FFmpeg required for bounded clip verification')
def test_change_crop_on_long_task_only_renders_selected_silent_window(tmp_path,monkeypatch):
    repo,actor,state,factory,build,media=case(tmp_path)
    library=tmp_path/'library';library.mkdir(exist_ok=True)
    source=library/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=30:d=6',
        '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True,timeout=5)
    asset=MaterialAsset(id='asset',workspace_id='local',file_name=source.name,path=str(source),
        size_bytes=source.stat().st_size,modified_at=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc),
        duration_seconds=6,usage_role='b_roll',review_status='approved',analysis_status='ready',media_type='video',
        owner_user_id=actor.member_id,segments=[MaterialSegment(id='segment',asset_id='asset',start_seconds=0,end_seconds=6,
            description='synthetic frame',review_status='approved',usage_role='b_roll')])
    repo._save_asset(asset)
    reference=repo.workspace_media_reference_for_asset('local','asset')
    with repo._connect() as conn:repo._claim_workspace_media_reference(conn,workspace_id='local',reference=reference)
    clips=[MontageClip('clip','cue','asset','segment',reference,0,6,0,6,1,[],False),
           MontageClip('other','othercue','asset','segment',reference,0,6,6,27,1,[],False)]
    plan=CreationTaskMontagePlan(id='montage',workspace_id='local',task_id='task',script_id='script',script_title='long fixture',
        status='ready',cues=[NarrationCue('cue','section','first',6),NarrationCue('othercue','section2','other',21)],
        clips=clips,duration_seconds=27,low_confidence_clip_count=0)
    with repo._connect() as conn:
        task=repo.get_creation_task('local','task');task.montage_plan_id='montage'
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id='local' AND id='task'",(json.dumps(asdict(task),default=str),))
        conn.execute("INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) VALUES (?,?,?,?,?)",
            ('montage','local','task',json.dumps(creation_task_montage_plan_to_payload(plan)),task.updated_at.isoformat()))
    repo.create_timeline_version(TimelineVersion('timeline','local','task',None,1,{'montage':creation_task_montage_plan_to_payload(plan)},None))
    budget=CpuRenderBudget(max_duration_seconds=20.401,max_pixel_seconds=1920*1920*20.401,max_width_px=1920,max_height_px=1920,
        max_input_bytes=10000000,max_clip_count=64,max_overlay_count=16,max_output_bytes=2000000,max_threads=1,max_timeout_seconds=30,
        additional_memory_bytes=1,additional_disk_bytes=1)
    monkeypatch.setattr(main,'cpu_render_budget_from_env',lambda:budget)
    # _task_media_facts imports the calibration reader at its boundary.
    monkeypatch.setattr('app.member_permits.cpu_render_budget_from_env',lambda:budget)
    monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'artifact_store',ArtifactStore(tmp_path/'output'))
    monkeypatch.setattr(main,'settings',SimpleNamespace(effective_temporary_root_path=tmp_path/'temp'))
    monkeypatch.setattr(main,'_require_permit_actor',lambda *args:actor)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    media.facts=main._task_media_facts;media.execute_operation=main._execute_task_media_operation
    media.validate_result=main._validate_task_media_result
    runtime=build(media);runtime.scheduler.processes=HostProcessProbe()
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    client=TestClient(main.app)
    payload={'operation_type':'ChangeCrop','clip_id':'clip','fit_mode':'fit','crop_x':.5,'crop_y':.5}
    queued=client.post('/api/creation-tasks/task/edits/exact/preview',json=payload)
    assert queued.status_code==202,queued.text
    _,event=media.read('local',queued.json()['job_id'])
    assert event['resources']['duration_seconds']==5 and event['resources']['clip_count']==1
    assert event['resources']['overlay_count']==0 and event['resources']['silent'] is True
    assert len(event['input_facts']['source'])==4 and 'montage' not in event['input_facts']
    assert queued.json()['target_refs']==['clip:clip']
    assert runtime.run_once()
    done=client.get(queued.json()['result_url']).json()
    assert done['status']=='succeeded',done
    records=[record for record in repo.list_artifact_records('local','task') if record.producer_job_id==queued.json()['job_id']]
    assert len(records)==2
    for record in records:
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,width,height:format=duration',
            '-of','json',str(main.artifact_store.resolve_file(record.logical_ref))],capture_output=True,text=True,check=True,timeout=5)
        metadata=json.loads(probe.stdout)
        assert float(metadata['format']['duration'])<=5.1
        assert [stream['codec_type'] for stream in metadata['streams']]==['video']
    assert client.post('/api/creation-tasks/task/edits/exact/preview',json=payload).json()['job_id']==queued.json()['job_id']
    assert not runtime.run_once()
    count_before=len(repo.list_job_runs('local','task'))
    def no_runtime():
        raise AssertionError('metadata proposal must not construct the device runtime')
    monkeypatch.setattr(main,'_creation_runtime',no_runtime)
    metadata=client.post('/api/creation-tasks/task/edits/exact/preview',json={
        'operation_type':'SetClipLock','clip_id':'clip','locked':True})
    assert metadata.status_code==200,metadata.text
    assert len(repo.list_job_runs('local','task'))==count_before
    # Budget refusal concerns this declared window, independently of the 27s parent.
    host=HostResourceProbe(tmp_path/'budget.sqlite',disk_paths=(tmp_path,),budget=HostBudget(max_cpu_percent=100,
        min_available_memory_bytes=0,max_swap_used_bytes=10**12,min_free_disk_bytes=0,max_disk_io_bytes_per_second=10**12,
        max_unit_input_bytes=10**12,cpu_render=CpuRenderBudget(**{**asdict(budget),'max_duration_seconds':4})))
    assert host._sample(event['resources']).verdict is False
    runtime.stop(0)
