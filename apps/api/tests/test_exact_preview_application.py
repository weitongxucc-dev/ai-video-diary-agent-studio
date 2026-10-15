"""One typed picture preview with real audio/captions and its durable request journal."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from PIL import Image
import pytest

import app.main as main
from app.task_audio import TASK_MEDIA_CONTEXT, media_digest
from app.production import EXACT_EDIT_SOURCE
from app.creation_execution import CreationExecutionActor
from test_visual_preview_application import visual_application, _inspect
from test_montage_pipeline import person_render_edit_state, permitted_media_state


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None, reason='native media tools unavailable')
def test_typed_picture_exact_preview_preserves_source_and_request_journal(visual_application, granted_media, tmp_path):
    state = visual_application
    state.media.exact_preview = main._exact_preview_use_case(state.production)
    case = state.media.exact_preview
    assert case.metadata is case.subtitles.montage_edits is case.linked.montage_edits
    assert case.metadata.audio_service is case.audio_edit.audio is case.visual.audio
    assert case.metadata.picture_renderer is case.visual.renderer
    task_before = state.production.get_creation_task(state.workspace_id, state.task.id)
    montage_before = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    request_id = str(uuid4())
    graphic = {**state.visual_plan.graphic_overlays[0], 'x': .25}
    payload = {'request_id': request_id, 'expected_timeline_version_id': state.visual_base.id,
        'expected_timeline_sequence': state.visual_base.sequence, 'operation_type': 'EditGraphicOverlay',
        'overlay_action': 'update', 'overlay_id': graphic['id'], 'graphic_overlay': graphic}
    url = state.base_url + '/edits/exact/preview'
    queued = state.client.post(url, json=payload)
    assert queued.status_code == 202 and queued.json()['result'] is None, queued.text
    job_id = queued.json()['job_id']
    assert state.runtime.run_once()
    completed = state.client.get(queued.json()['result_url'])
    assert completed.status_code == 200 and completed.json()['status'] == 'succeeded', completed.text
    job, start = state.media.read(state.workspace_id, job_id)
    assert start['operation'] == 'exact_edit_preview'
    assert job.input_digest == start['input_digest'] == media_digest({k:v for k,v in start.items() if k != 'input_digest'})
    source = start['arguments']['_typed_source']
    assert (source['workspace_id'], source['task_id'], source['member_id'], source['request_id'],
        source['expected_timeline_version_id'], source['expected_timeline_sequence']) == (
        state.workspace_id, state.task.id, task_before.owner_user_id, request_id, state.visual_base.id, state.visual_base.sequence)
    assert source['source_facts'] == []
    assert source['action'] == {k:v for k,v in payload.items() if k not in {'request_id','expected_timeline_version_id','expected_timeline_sequence'}}
    result = completed.json()['result']
    saved_source = result['intent']['constraints']['request_source']
    expected_actor = CreationExecutionActor.from_event(state.workspace_id,start).identity_payload()
    assert saved_source == {**source, 'media_job_id': job_id, 'actor_identity': expected_actor}
    assert saved_source['request_id'] == request_id and saved_source['action'] == source['action']
    records = [r for r in state.production.list_artifact_records(state.workspace_id, state.task.id) if r.producer_job_id == job_id]
    assert len(records) == 2 and {r.kind for r in records} == {'graphic_preview'}
    sides = {case.metadata.audio_service._transform_for(state.workspace_id,state.task.id,r).parameters['graphic_overlays'][0]['x']: r for r in records}
    assert set(sides) == {.5, .25}
    files = {side:main.artifact_store.resolve_file(sides[x].logical_ref) for side,x in [('before',.5),('after',.25)]}
    directory = tmp_path / 'frames'; directory.mkdir()
    with granted_media(files.values(),kind='creation_render') as context:
        visuals = {side:_inspect(context,path,directory,side) for side,path in files.items()}
    for side,visual in visuals.items():
        assert (visual['video']['width'],visual['video']['height']) == (1080,1920)
        assert abs(float(visual['probe']['format']['duration']) - 7) < .1
        assert any(s['codec_type'] == 'audio' for s in visual['probe']['streams'])
        assert visual['frames']['middle']['white_pixels'] > 5
        with Image.open(visual['frames']['middle']['path']) as image:
            positions = [i % image.width for i,(r,g,b) in enumerate(image.convert('RGB').getdata()) if r > 190 and g < 80 and b < 80]
        assert positions
        visual['red_centroid_x'] = sum(positions) / len(positions)
    assert visuals['before']['red_centroid_x'] - visuals['after']['red_centroid_x'] > 80
    journal_url = state.base_url + '/assistant/edit-requests/' + request_id + '?phase=preview'
    journal = state.client.get(journal_url)
    assert journal.status_code == 200, journal.text
    assert journal.json()['status'] == 'recorded' and journal.json()['result_status_code'] == 202
    assert journal.json()['result']['job_id'] == job_id
    assert journal.json()['submitted_input'] == payload
    replay = state.client.post(url,json=payload)
    assert replay.status_code == 202 and replay.json() == queued.json(), replay.text
    assert not state.runtime.run_once()
    assert state.client.get(queued.json()['result_url']).json() == completed.json()
    assert state.production.get_creation_task(state.workspace_id,state.task.id).current_timeline_version_id == task_before.current_timeline_version_id
    assert state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id) == montage_before
    assert 'exact_edit_preview' not in main._TASK_MEDIA_HANDLERS
    assert not hasattr(main.preview_creation_task_exact_edit,'__wrapped__')
    assert TASK_MEDIA_CONTEXT.get() is None and EXACT_EDIT_SOURCE.get() is None
    with state.production._connect() as conn:
        permit = dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
    assert permit['state'] == 'free'
    flow = {'payload':payload,'queued':queued.json(),'completed':completed.json(),'journal':journal.json(),'replay':replay.json(),
        'start_event':start,'records':[asdict(r) for r in records],'visuals':visuals,'task_before':asdict(task_before),
        'montage_before':montage_before,'database_path':str(state.production.database_path),'permit':permit,
        'factory':'main._exact_preview_use_case','resource_budget':asdict(state.runtime.scheduler.resources.budget),
        'fixture_domain':'existing permitted_media_state and visual_application; actual product bodies/native restored',
        'fixture_OS_inputs':{'swap_used_bytes':0,'global_cpu_percent':0,'disk_io_counter':state.fixture_disk_io_baseline._asdict(),
            'actual_swap_before':state.actual_swap_before_fixture},
        'production_auth_host_profile_GUI_full_inflight_PGID':'UNVERIFIED'}
    destination = os.environ.get('EXACT_PREVIEW_EVIDENCE_DIR')
    if destination:
        (Path(destination)/'NORMAL_FLOW.json').write_text(json.dumps(flow,ensure_ascii=False,indent=2,default=str)+'\n')
