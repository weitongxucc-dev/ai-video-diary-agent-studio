"""One real selection preview: original audio, changed picture/caption, no apply."""
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import shutil

import pytest

import app.main as main
from app.task_audio import TASK_MEDIA_CONTEXT
from test_visual_preview_application import visual_application as visual_application, _inspect
from test_montage_pipeline import person_render_edit_state as person_render_edit_state, permitted_media_state as permitted_media_state


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None, reason='native media tools unavailable')
def test_compound_selection_renders_real_audio_caption_pair(visual_application, granted_media, tmp_path):
    state = visual_application
    state.media.compound_selection = main._compound_edit_use_case(state.production)
    case = state.media.compound_selection
    assert case.picture_renderer.audio is case.montage_edits.audio_service
    assert case.picture_renderer.metadata is case.montage_edits
    assert case.montage_edits.audio_service.picture_renderer.__self__ is case.picture_renderer
    before_task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    before_montage = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    original = state.client.post(state.base_url + '/edits/preview?draft=true', json={
        'raw_command': '当前镜头亮一点，同时配音片段1音量降低3分贝，字幕字号改为64',
        'context_stage': 'montage', 'selected_object_refs': ['clip:' + state.visual_clip.id]})
    assert original.status_code == 200, original.text
    original_proposal = original.json()['proposal']
    assert len(original_proposal['operation']['payload']['steps']) == 3
    stored_original = asdict(state.production.edit_records.get_edit_proposal(state.workspace_id, original_proposal['id']))
    payload = {'included_step_indices': [0, 2]}
    route = state.base_url + '/edits/' + original_proposal['id'] + '/selection/preview'
    queued = state.client.post(route, json=payload)
    assert queued.status_code == 202 and queued.json()['result'] is None, queued.text
    job_id = queued.json()['job_id']
    assert state.runtime.run_once()
    completed = state.client.get(queued.json()['result_url'])
    assert completed.status_code == 200 and completed.json()['status'] == 'succeeded', completed.text
    job, event = state.media.read(state.workspace_id, job_id)
    assert event['operation'] == 'compound_selection_preview'
    frozen_proposal = event['input_facts']['compound_selection_proposal']
    assert {k: v for k, v in frozen_proposal.items() if k not in {'created_at', 'updated_at'}} == {
        k: v for k, v in stored_original.items() if k not in {'created_at', 'updated_at'}}
    for key in ('created_at', 'updated_at'):
        assert datetime.fromisoformat(frozen_proposal[key]) == stored_original[key]
    assert '_typed_source' not in event['arguments']
    result = completed.json()['result']
    proposal, intent = result['proposal'], result['intent']
    data = proposal['operation']['payload']
    assert [item['enabled'] for item in data['steps']] == [True, False, True]
    assert data['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == 0
    assert intent['raw_command'] == original.json()['intent']['raw_command']
    assert 'request_source' not in intent['constraints']
    records = [r for r in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id) if r.producer_job_id == job_id]
    assert len(records) == 2 and {r.kind for r in records} == {'compound_preview'}
    side_records = {case.montage_edits.audio_service._transform_for(state.workspace_id, state.task.id, r).parameters['side']: r for r in records}
    assert set(side_records) == {'before', 'after'}
    files = {side: main.artifact_store.resolve_file(r.logical_ref) for side, r in side_records.items()}
    directory = tmp_path / 'frames'
    directory.mkdir()
    with granted_media(files.values(), kind='creation_render') as context:
        visuals = {side: _inspect(context, path, directory, side) for side, path in files.items()}
    for side, visual in visuals.items():
        assert (visual['video']['width'], visual['video']['height']) == (1080, 1920)
        assert abs(float(visual['probe']['format']['duration']) - 7) < .1
        assert any(s['codec_type'] == 'audio' for s in visual['probe']['streams'])
        assert visual['frames']['middle']['white_pixels'] > 5
        assert proposal['preview'][side]['ref'] == side_records[side].logical_ref
    assert visuals['after']['frames']['middle']['white_pixels'] > visuals['before']['frames']['middle']['white_pixels']
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == before_task.current_timeline_version_id
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id) == before_montage
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, original_proposal['id']).status == 'proposed'
    assert state.client.get(queued.json()['result_url']).json() == completed.json()
    replay = state.client.post(route, json=payload)
    assert replay.status_code == 202 and replay.json()['job_id'] == job_id, replay.text
    assert not state.runtime.run_once()
    assert 'compound_selection_preview' not in main._TASK_MEDIA_HANDLERS
    assert not hasattr(next(route.endpoint for route in main.app.routes if route.name == 'preview_compound_selection'), '__wrapped__')
    assert TASK_MEDIA_CONTEXT.get() is None
    with state.production._connect() as conn:
        permit = dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
    assert permit['state'] == 'free'
    evidence = {'queued': queued.json(), 'completed': completed.json(), 'replay': replay.json(),
        'original': original.json(), 'start_event': event, 'records': [asdict(r) for r in records],
        'visuals': visuals, 'task_before': asdict(before_task), 'montage_before': before_montage,
        'database_path': str(state.production.database_path), 'permit': permit,
        'factory': 'main._compound_edit_use_case', 'resource_budget': asdict(state.runtime.scheduler.resources.budget),
        'fixture_domain': 'existing permitted_media_state plus visual_application; original native and all product bodies restored',
        'fixture_OS_inputs': {'swap_used_bytes': 0, 'global_cpu_percent': 0,
            'disk_io_counter': state.fixture_disk_io_baseline._asdict(), 'actual_swap_before': state.actual_swap_before_fixture},
        'production_auth_host_profile_GUI_full_inflight_PGID': 'UNVERIFIED'}
    destination = os.environ.get('COMPOUND_PREVIEW_EVIDENCE_DIR')
    if destination:
        (Path(destination) / 'NORMAL_FLOW.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + '\n')
