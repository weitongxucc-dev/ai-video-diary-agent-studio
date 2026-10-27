"""One real registered output journey on original grants and isolated stores."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app import main
from app.artifact_store import ArtifactStore
from app.canvas import canvas_size
from app.creation.contracts import ArtifactRecord, JobRun, TimelineVersion, TransformRun
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.host_resource_probes import CpuRenderBudget, HostProcessProbe
from app.local_members import LocalMemberService
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, MontageClip, creation_task_montage_plan_to_payload
from app.production import MaterialAsset, MaterialSegment
from app.task_audio import TASK_MEDIA_CONTEXT
from test_task_media_grants import case


def test_public_actual_output_observation_and_fixed_request_replay(tmp_path, monkeypatch, granted_media):
    repo, actor, _state, _factory, build, media = case(tmp_path)
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    assert member.member_id == actor.member_id
    # This owned input starts with a real private owner, rather than the generic case's None.
    with repo._connect() as conn:
        for table in ('creation_tasks','creation_plans'):
            row=conn.execute('SELECT id,payload FROM '+table+' WHERE workspace_id=?',('local',)).fetchone()
            payload=json.loads(row['payload']);payload['owner_user_id']=actor.member_id
            if table=='creation_tasks':payload['stage']='review'
            conn.execute('UPDATE '+table+' SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    store = ArtifactStore(tmp_path/'outputs')
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_output_path=str(tmp_path/'outputs'), video_inbox_path=str(repo.video_library_path),
        temporary_root_path=str(tmp_path/'temporary'), native_ocr_worker_path=''))
    # Exact existing configure_synthetic_media_budgets render envelope; not a host default.
    render = CpuRenderBudget(max_duration_seconds=12, max_pixel_seconds=25_000_000, max_width_px=1920,
        max_height_px=1920, max_input_bytes=32*1024**2, max_clip_count=16, max_overlay_count=8,
        max_output_bytes=32*1024**2, max_threads=1, max_timeout_seconds=30,
        additional_memory_bytes=512*1024**2, additional_disk_bytes=64*1024**2, max_process_threads=16)
    for name, value in asdict(render).items():
        monkeypatch.setenv('DEVICE_CPU_RENDER_'+name.upper(), str(value))
    monkeypatch.delenv('TASK_OUTPUT_OBSERVATION_BUDGET_JSON', raising=False)
    media.facts = main._task_media_facts
    media.output_observation = main._output_observation_use_case()
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    # Original finite synthetic scratch reservation port used by granted_media(creation_render).
    runtime.scheduler.resources.budget = SimpleNamespace(cpu_render=render, presenter_cpu_render=None)
    def grant_resources(current):
        with repo._connect() as conn:
            row = conn.execute("SELECT resources_json FROM device_work_requests WHERE id=? AND workspace_id=? "
                "AND owner_id=? AND state IN ('granted','yield_requested')",
                (current.request_id, current.workspace_id, current.owner_id)).fetchone()
        return json.loads(row[0]) if row else None
    runtime.scheduler.resources._grant_resources = grant_resources
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    repo.video_library_path.mkdir(parents=True, exist_ok=True)
    original = repo.video_library_path/'owned-output-source.mp4'
    width, height = canvas_size('16:9')
    with granted_media(kind='creation_render') as context:
        encoded = context.run(['ffmpeg', '-v', 'error', '-filter_threads', '1', '-threads', '1',
            '-f', 'lavfi', '-i', f'color=c=red:s={width}x{height}:r=10:d=2',
            '-c:v', 'mpeg4', '-threads:v', '1', '-pix_fmt', 'yuv420p', str(original)], output_paths=(original,))
        assert encoded.returncode == 0, encoded.stderr
    stat = original.stat()
    asset = MaterialAsset(id='owned-asset', workspace_id='local', file_name=original.name,
        path=str(original), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc), duration_seconds=2,
        owner_user_id=actor.member_id, visibility='private', analysis_status='ready', usage_role='b_roll',
        segments=[MaterialSegment('owned-segment', 'owned-asset', 0, 2, usage_role='b_roll')])
    repo._save_asset(asset)
    source_ref = repo.workspace_media_reference_for_asset('local', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='local', reference=source_ref)
    publication = store.publish_file('artifacts/task/owned-final.mp4', original)
    producer = repo.create_job_run(JobRun('fixture-output-job', 'local', 'task', 'creation_task_montage_render',
        'owned-output-input', hashlib.sha256(original.read_bytes()).hexdigest()))
    running = repo.claim_job_run('local', producer.id)
    artifact = repo.create_artifact_record(ArtifactRecord('fixture-output-artifact', 'local', 'task', 'rendered_video',
        publication.logical_ref, publication.sha256, publication.size_bytes, producer.id, [source_ref]))
    repo.lineage_records.create_transform_run(TransformRun('fixture-output-transform', 'local', 'task', producer.id,
        'rendered_video', producer.input_digest, [source_ref], [publication.logical_ref], 'synthetic-fixture-input/v1',
        {'width':width,'height':height,'duration_ms':2000}, 'succeeded', running.started_at, utc_now()))
    repo.finish_job_run('local', producer.id, status='succeeded', output_refs=[publication.logical_ref])
    clip = MontageClip('clip-1', 'fixture-cue', asset.id, 'owned-segment', source_ref, 0, 2, 0, 2, 1, [], False)
    plan = CreationTaskMontagePlan('montage', 'local', 'task', 'fixture-script', 'owned output', 'rendered',
        [], [clip], 2, 0, aspect_ratio='16:9', output_path=publication.logical_ref)
    payload = creation_task_montage_plan_to_payload(plan)
    repo.save_creation_task_montage_plan('local', 'task', plan.id, payload)
    base = repo.create_timeline_version(TimelineVersion('v1', 'local', 'task', None, 1,
        {'montage':payload,'output_refs':[publication.logical_ref]}, None))
    assert type(media.output_observation) is OutputObservationUseCase
    assert runtime.registrations['creation_job'].owner.task_media is media
    calls=[];execute=media.output_observation.execute
    def observed(job,event,who,context):
        assert TASK_MEDIA_CONTEXT.get() is None and who==actor and context.grant.owner_id==job.id
        calls.append(job.id)
        return execute(job,event,who,context)
    monkeypatch.setattr(media.output_observation,'execute',observed)
    client=TestClient(main.app,headers={'X-Local-Member-Token':token})
    request={'request_id':str(uuid4()),'expected_timeline_version_id':base.id,
        'expected_timeline_sequence':base.sequence,'target':{'kind':'final'}}
    first=client.post('/api/creation-tasks/task/observations',json=request)
    assert first.status_code==202,first.text
    receipt=first.json();assert receipt['result'] is None and receipt['result_kind']=='observation'
    assert runtime.run_once()
    done=client.get(receipt['result_url']);assert done.status_code==200,done.text
    result=done.json();assert result['status']=='succeeded',result
    measured=result['result'];assert measured['measurement_scope']=='actual_output_bounded_frames'
    assert measured['output_role']=='composed_output' and measured['media']['width']==width and measured['media']['height']==height
    assert measured['output']['artifact_id']==artifact.id and measured['output']['sha256']==publication.sha256
    assert len(measured['samples'])==3 and all(s['object_refs']==['clip:clip-1'] for s in measured['samples'])
    assert measured['ocr_execution']['status']=='unavailable'
    assert {'visual_semantics','speech_and_listening','ocr'}=={v['scope'] for v in measured['unavailable']}
    assert not {'logical_ref','mtime_ns','ctime_ns'} & measured['output'].keys()
    replay=client.post('/api/creation-tasks/task/observations',json=request)
    assert replay.status_code==202 and replay.json()['job_id']==receipt['job_id']
    query=client.get('/api/creation-tasks/task/assistant/edit-requests/'+request['request_id']+'?phase=observation')
    assert query.status_code==200 and query.json()['result']['job_id']==receipt['job_id']
    assert client.get(receipt['result_url']).json()['result']==measured and calls==[receipt['job_id']]
    assert len(repo.list_job_runs('local','task'))==2 and len(repo.list_artifact_records('local','task'))==1
    assert repo.get_task_event('local','event_'+receipt['job_id']+'_verified').payload['outputs']==[]
    assert repo.get_creation_task('local','task').current_timeline_version_id==base.id
    assert main._creation_task_output_path('local','task',subtitle=False)==store.resolve_file(publication.logical_ref)
    assert main._quality_check_source('local','task')[3].sha256==publication.sha256
    assert not list((tmp_path/'temporary').glob('task-observation-*'))
    assert TASK_MEDIA_CONTEXT.get() is None and runtime.stop(1)
    client.close()
