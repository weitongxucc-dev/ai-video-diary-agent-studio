"""One real-media short/full visual flow in the explicitly bounded fixture domain."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import psutil

from PIL import Image
import pytest

import app.main as main
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.creation.contracts import SubtitleTrack, SubtitleCue
from app.task_audio import TaskAudio, TASK_MEDIA_CONTEXT
from test_montage_pipeline import person_render_edit_state, permitted_media_state
from _media_preview_test_fixtures import write_synthetic_video

REAL_AUDIO_FACTORY = main._task_audio
REAL_MEASURE = TaskAudio._measure_publication
from app.creation.output_observation_use_case import OutputObservationUseCase
REAL_CURRENT = OutputObservationUseCase.published_outputs_current


@pytest.fixture
def visual_application(permitted_media_state, monkeypatch):
    state = permitted_media_state
    # Restore every old domain-only seam; only its explicit data/caps/roster domain remains.
    monkeypatch.setattr(main, '_task_audio', REAL_AUDIO_FACTORY)
    monkeypatch.setattr(TaskAudio, '_measure_publication', REAL_MEASURE)
    monkeypatch.setattr(OutputObservationUseCase, 'published_outputs_current', REAL_CURRENT)
    state.client.post = state.domain_original_post
    monkeypatch.setattr(main,'settings',replace(main.settings,native_ocr_worker_path=None))
    original_swap = psutil.swap_memory
    state.actual_swap_before_fixture = original_swap().used
    # Control global OS pressure in the explicitly synthetic resource domain;
    # original declared budgets and native RSS/thread/deadline/PGID gates remain.
    monkeypatch.setattr(psutil, 'swap_memory', lambda: original_swap()._replace(used=0))
    state.fixture_disk_io_baseline = psutil.disk_io_counters()
    monkeypatch.setattr(psutil, 'disk_io_counters', lambda **kwargs: state.fixture_disk_io_baseline)
    monkeypatch.setattr(psutil, 'cpu_percent', lambda *args, **kwargs: 0.0)
    state.runtime = state.preview_runtime
    state.media = state.runtime.registrations['creation_job'].owner.task_media
    state.media.visual_preview = main._visual_preview_use_case(state.production)
    state.media.facts = main._task_media_facts
    clip = next(item for item in state.montage.clips if not item.person_render_id)
    asset = state.production.get_asset(state.workspace_id, clip.asset_id)
    write_synthetic_video(Path(asset.path), seconds=10)
    stat = Path(asset.path).stat()
    asset = replace(asset, size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        duration_seconds=10, segments=[replace(item, start_seconds=0, end_seconds=10) for item in asset.segments])
    state.production._save_asset(asset)
    clip = replace(clip, source_start_seconds=0, source_end_seconds=7,
        timeline_start_seconds=0, timeline_end_seconds=7, transition_in_seconds=0, visual_effects={}, locked=False)
    plan = replace(state.montage, clips=[clip], duration_seconds=7, status='ready',
        audio_timeline=None, graphic_overlays=[], output_path=None, subtitle_path=None)
    state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id, plan.id,
        creation_task_montage_plan_to_payload(plan))
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    base = main._ensure_montage_base_version(state.workspace_id, task, plan)
    state.base_url = '/api/creation-tasks/' + task.id
    buffer = io.BytesIO()
    Image.new('RGBA', (32, 32), (255, 0, 0, 255)).save(buffer, format='PNG')
    imported = state.client.post(state.base_url + '/graphics/import',
        files={'image': ('marker.png', buffer.getvalue(), 'image/png')})
    assert imported.status_code == 200, imported.text
    overlay = {'kind':'image','id':'visual-fixture-marker','name':'marker.png',
        'source_ref':imported.json()['source_ref'],'start_seconds':0,'end_seconds':2,
        'x':.5,'y':.5,'width':.15}
    plan = replace(plan, graphic_overlays=[overlay])
    state.production.save_creation_task_montage_plan(state.workspace_id, task.id, plan.id,
        creation_task_montage_plan_to_payload(plan))
    caption_ref, caption_transform = state.media.visual_preview.metadata.publish_subtitle_text(
        workspace_id=state.workspace_id, task_id=task.id, proposal_id='visual-fixture-caption',
        kind='subtitle_fixture', input_refs=['timeline:'+base.id],
        content='1\n00:00:00,200 --> 00:00:01,500\nTAIL DEMO\n', engine='fixture-subtitle-text',
        parameters={'language':'en-US'})
    track = state.production.subtitle_records.create_subtitle_track(SubtitleTrack(id='visual-fixture-caption',
        workspace_id=state.workspace_id, task_id=task.id, timeline_version_id=base.id,
        language='en-US', source_language=None, style={**main.metadata_default_subtitle_style(), 'enabled':True},
        cues=[SubtitleCue('visual-caption-one', None, 'TAIL DEMO', .2, 1.5)],
        generation_method='timeline', model_id=None, artifact_ref=caption_ref, transform_id=caption_transform, status='valid'))
    state.visual_plan, state.visual_clip, state.visual_base = plan, clip, base
    state.visual_track, state.visual_graphic = track, imported.json()
    audio = state.media.visual_preview.audio
    assert audio.picture_renderer.__self__ is state.media.visual_preview.renderer
    assert state.media.visual_preview.renderer.audio is audio
    assert state.media.visual_preview.metadata.audio_service is audio
    assert audio.picture_renderer.__module__ == 'app.media.picture_preview'
    return state


def _inspect(context, path, directory, label):
    info = context.run(['ffprobe','-v','error','-count_frames','-show_entries',
        'stream=codec_type,width,height,nb_read_frames:format=duration','-of','json',str(path)])
    document = json.loads(info.stdout)
    video = next(item for item in document['streams'] if item['codec_type']=='video')
    frames = {}
    for name, number in [('middle',21),('last',int(video['nb_read_frames'])-1)]:
        output = directory / (label+'-'+name+'.png')
        context.run(['ffmpeg','-v','error','-y','-i',str(path),'-vf',
            f'select=eq(n\\,{number}),scale=384:640','-frames:v','1',str(output)], output_paths=(output,))
        with Image.open(output) as image:
            rgb = list(image.convert('RGB').getdata())
        frames[name] = {'path':str(output),'frame_number':number,
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'red_pixels':sum(r>190 and g<80 and b<80 for r,g,b in rgb),
            'white_pixels':sum(min(r,g,b)>200 for r,g,b in rgb),
            'corner_rgb':rgb[0], 'mean_blue':sum(b for r,g,b in rgb)/len(rgb)}
    return {'media_path':str(path),'probe':document,'video':video,'frames':frames}


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None, reason='native media tools unavailable')
def test_short_and_full_tail_visual_preview_use_real_native_media(visual_application, granted_media, tmp_path):
    state = visual_application
    task_before = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    montage_before = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    outcomes = []
    for label,effects in [('short',{'exposure':-.5}),('full',{'fade_out_seconds':.4})]:
        payload = {'base_version_id':state.visual_base.id,'clip_ids':[state.visual_clip.id],'effects':effects}
        queued = state.client.post(state.base_url + '/visual/preview', json=payload)
        assert queued.status_code == 202, queued.text
        assert queued.json()['result'] is None
        job_id = queued.json()['job_id']
        assert state.runtime.run_once()
        completed = state.client.get(queued.json()['result_url'])
        assert completed.status_code == 200 and completed.json()['status']=='succeeded', completed.text
        job,event = state.media.read(state.workspace_id,job_id)
        assert event['operation']=='visual_edit_preview'
        assert ('_clip_preview' in event['arguments']) == (label=='short')
        assert TASK_MEDIA_CONTEXT.get() is None
        records = [item for item in state.production.execution_records.list_artifact_records(state.workspace_id,state.task.id)
                   if item.producer_job_id==job_id]
        assert len(records)==2 and {r.kind for r in records}=={'color_preview'}
        side_records = {}
        for record in records:
            transform = state.media.visual_preview.audio._transform_for(state.workspace_id,state.task.id,record)
            side_records[transform.parameters['side']] = record
        assert set(side_records)=={'before','after'}
        files = {side:main.artifact_store.resolve_file(record.logical_ref) for side,record in side_records.items()}
        with granted_media(files.values(),kind='creation_render') as context:
            directory = tmp_path / ('frames-'+label);directory.mkdir()
            visuals = {side:_inspect(context,path,directory,label+'-'+side) for side,path in files.items()}
        for visual in visuals.values():
            assert (visual['video']['width'],visual['video']['height']) == ((360,640) if label=='short' else (1080,1920))
            assert abs(float(visual['probe']['format']['duration'])-(5 if label=='short' else 7)) < .1
            assert any(s['codec_type']=='audio' for s in visual['probe']['streams']) == (label=='full')
        if label=='short':
            assert all(v['frames']['middle']['red_pixels']==0 for v in visuals.values())
            assert all(v['frames']['middle']['white_pixels']==0 for v in visuals.values())
            assert visuals['after']['frames']['middle']['mean_blue'] < visuals['before']['frames']['middle']['mean_blue']-20
        else:
            assert all(v['frames']['middle']['red_pixels']>10 for v in visuals.values())
            assert all(v['frames']['middle']['white_pixels']>5 for v in visuals.values())
            assert visuals['before']['frames']['last']['corner_rgb'][2] > 150
            assert max(visuals['after']['frames']['last']['corner_rgb']) < 20
        assert state.production.task_records.get_creation_task(state.workspace_id,state.task.id).current_timeline_version_id == task_before.current_timeline_version_id
        assert state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id) == montage_before
        replay = state.client.post(state.base_url+'/visual/preview',json=payload)
        assert replay.status_code==202 and replay.json()['job_id']==job_id
        assert not state.runtime.run_once()
        retry = state.client.post(state.base_url+'/media-jobs/'+job_id+'/retry')
        assert retry.status_code==409  # Original public policy: a successful current job cannot be retried.
        assert state.client.get(queued.json()['result_url']).json() == completed.json()
        outcomes.append({'label':label,'payload':payload,'queued':queued.json(),'result':completed.json(),
            'start_event':event,'replay':replay.json(),'successful_retry_refusal':retry.json(),
            'records':[asdict(r) for r in records],'visuals':visuals})
    assert not hasattr(main,'_TASK_MEDIA_HANDLERS')
    assert not hasattr(next(route.endpoint for route in main.app.routes if route.name == 'preview_creation_task_visual'),'__wrapped__')
    with state.production._connect() as conn:
        permit=dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
    assert permit['state']=='free'
    evidence={'outcomes':outcomes,'task_before':asdict(task_before),'montage_before':montage_before,
        'fixture_graphic':state.visual_graphic,'fixture_track':asdict(state.visual_track),
        'database_path':str(state.production.database_path),'permit':permit,
        'factory':'main._visual_preview_use_case','native_test_fixture_domain':True,
        'resource_type':type(state.runtime.scheduler.resources).__module__+'.'+type(state.runtime.scheduler.resources).__name__,
        'resource_budget':asdict(state.runtime.scheduler.resources.budget),
        'restored_real_seams':['facts','render','measure','current-reader','client.post'],
        'fixture_resource_metric':{'swap_used_bytes':0,'actual_before_fixture':state.actual_swap_before_fixture,
            'global_cpu_percent':0.0,
            'disk_io_counter':state.fixture_disk_io_baseline._asdict(),
            'scope':'this parent test process only; original helper budget/probe and all native limits unchanged'},
        'production_auth_host_profile_GUI_full_inflight_PGID':'UNVERIFIED'}
    destination=os.environ.get('VISUAL_PREVIEW_EVIDENCE_DIR')
    if destination:
        (Path(destination)/'NORMAL_FLOW.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str)+'\n')
