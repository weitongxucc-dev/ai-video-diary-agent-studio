"""One explicit application flow; external prepare/run are disclosed substitutes."""
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

import app.main as main
import app.media_script_jobs as scripts
from app.media_script import MeasuredScriptOutput
from test_media_script_api import (
    state as legacy_state,
    material_edit_state,
    person_render_edit_state,
)


@pytest.fixture
def script_application(legacy_state, monkeypatch):
    state = legacy_state
    monkeypatch.setattr(main.settings, 'native_ocr_worker_path', '', raising=False)
    media = state.runtime.registrations['creation_job'].owner.task_media
    media.media_script = main._media_script_use_case(state.production)
    assert media.media_script.repository is state.production
    assert media.media_script.recipe_tools.repository is state.production
    state.port_calls = []
    state.prepared_calls = []

    def prepare(*, sealed, input_paths, job_directory, limits):
        assert main.TASK_MEDIA_CONTEXT.get() is None
        job_directory.mkdir()
        (job_directory / 'out').mkdir()
        state.prepared_calls.append({'digest': sealed.digest, 'inputs': sorted(input_paths),
            'directory': str(job_directory), 'timeout': limits.timeout_seconds})
        return SimpleNamespace(sealed=sealed, directory=job_directory, limits=limits)

    def run(*, prepared, scheduler, grant, cancel_event):
        assert main.TASK_MEDIA_CONTEXT.get() is None
        scheduler.assert_grant(grant)
        job, event = media.read(grant.workspace_id, grant.owner_id)
        assert event['resources']['kind'] == 'media_script'
        assert event['resources']['script_digest'] == prepared.sealed.digest
        assert grant.input_digest != prepared.sealed.digest
        output, = prepared.sealed.requested_outputs
        path = prepared.directory / 'out' / output.name
        Image.new('RGBA', (16, 16), (255, 0, 0, 128)).save(path)
        content = path.read_bytes()
        state.port_calls.append({'job_id': job.id, 'operation': event['operation'],
            'digest': prepared.sealed.digest, 'output_sha256': hashlib.sha256(content).hexdigest(),
            'ambient': None, 'grant_request_id': grant.request_id})
        return (MeasuredScriptOutput(output.name, hashlib.sha256(content).hexdigest(), len(content), 16, 16),)

    monkeypatch.setattr(scripts, 'prepare_media_script', prepare)
    monkeypatch.setattr(scripts, 'run_media_script', run)
    state.media = media
    return state


def test_raw_source_recipe_and_replay_use_explicit_owner(script_application):
    state = script_application
    before = state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    inputs = []
    for color in ('blue', 'green'):
        buffer = io.BytesIO()
        Image.new('RGBA', (16, 16), color).save(buffer, format='PNG')
        imported = state.client.post(state.base_url + '/graphics/import',
            files={'image': (color + '.png', buffer.getvalue(), 'image/png')})
        assert imported.status_code == 200, imported.text
        record = state.production.execution_records.get_artifact_record(state.workspace_id, imported.json()['artifact_id'])
        inputs.append({'name': 'input.png', 'source_ref': record.logical_ref,
            'expected_artifact_id': record.id, 'expected_sha256': record.sha256})
    raw_payload = {**state.payload, 'input_refs': [inputs[0]]}
    first = state.client.post(state.base_url + '/media-scripts/preview', json=raw_payload)
    assert first.status_code == 202, first.text
    assert first.json()['result'] is None and state.port_calls == []
    assert state.runtime.run_once()
    first_result = state.client.get(first.json()['result_url'])
    assert first_result.status_code == 200 and first_result.json()['status'] == 'succeeded', first_result.text
    registration = state.client.post(state.base_url + '/media-script-tools', json={
        'request_id': str(uuid4()), 'producer_job_id': first.json()['job_id'],
        'name': 'Explicit card fixture', 'description': 'Application flow with substituted external execution',
        'parameter_schema': {'type': 'object', 'properties': {'color': {'type': 'string',
            'maxLength': 16, 'enum': ['red', 'blue'], 'default': 'red'}},
            'required': [], 'additionalProperties': False}})
    assert registration.status_code == 200, registration.text
    tool = registration.json()
    assert tool['version'] == 1 and tool['active']
    recipe_payload = {'request_id': str(uuid4()),
        'expected_timeline_version_id': state.payload['expected_timeline_version_id'],
        'expected_timeline_sequence': state.payload['expected_timeline_sequence'],
        'tool_version': tool['version'], 'parameters': {'color': 'blue'}, 'input_refs': [inputs[1]]}
    recipe_url = state.base_url + '/media-script-tools/' + tool['tool_id'] + '/preview'
    second = state.client.post(recipe_url, json=recipe_payload)
    assert second.status_code == 202, second.text
    assert second.json()['job_id'] != first.json()['job_id'] and len(state.port_calls) == 1
    assert state.runtime.run_once()
    second_result = state.client.get(second.json()['result_url'])
    assert second_result.status_code == 200 and second_result.json()['status'] == 'succeeded', second_result.text
    receipt_evidence = []
    for queued, result, payload, url in ((first, first_result, raw_payload, state.base_url + '/media-scripts/preview'),
                                       (second, second_result, recipe_payload, recipe_url)):
        body = result.json()
        source = body['result']
        assert source['applied'] is False and (source['width'], source['height']) == (16, 16)
        image = state.client.get(state.base_url + '/graphics/source/' + source['artifact_id'])
        assert image.status_code == 200
        with Image.open(io.BytesIO(image.content)) as png:
            assert png.format == 'PNG' and png.size == (16, 16)
        records = [r for r in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id)
                   if r.producer_job_id == queued.json()['job_id']]
        assert {r.kind for r in records} == {'graphic_source', 'media_script_definition'}
        definition, = [r for r in records if r.kind == 'media_script_definition']
        document = json.loads(main.artifact_store.resolve_file(definition.logical_ref).read_bytes())
        assert document['input_slots'] == [{'name': 'input.png', 'kind': 'graphic_source_png'}]
        assert str(state.root) not in json.dumps(document)
        start = state.production.execution_records.get_task_event(state.workspace_id, 'event_' + queued.json()['job_id'] + '_start').payload
        assert document['script_digest'] == start['input_facts']['script_digest']
        assert document['parameters']['color'] == ('red' if queued is first else 'blue')
        replay = state.client.post(url, json=payload)
        assert replay.status_code == 202 and replay.json()['job_id'] == queued.json()['job_id']
        query = state.client.get(state.base_url + '/assistant/edit-requests/' + payload['request_id'] + '?phase=script')
        assert query.status_code == 200, query.text
        expected = {**payload, **({'tool_id': tool['tool_id']} if queued is second else {})}
        assert query.json()['submitted_input'] == expected
        assert state.client.get(queued.json()['result_url']).json() == body
        receipt_evidence.append({'job_id': queued.json()['job_id'], 'result': body,
            'query': query.json(), 'replay': replay.json(), 'definition': document,
            'files': [{'reference': r.logical_ref, 'sha256': r.sha256, 'size_bytes': r.size_bytes,
                       'path': str(main.artifact_store.resolve_file(r.logical_ref))} for r in records]})
    assert len(state.port_calls) == len(state.prepared_calls) == 2
    assert not state.runtime.run_once()
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == before
    assert not scripts.MEDIA_SCRIPT_ACTIONS.intersection(main._TASK_MEDIA_HANDLERS)
    assert not hasattr(main.preview_media_script, '__wrapped__')
    assert not hasattr(main.preview_media_script_tool, '__wrapped__')
    with state.production._connect() as conn:
        permit = dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
    assert permit['state'] == 'free'
    evidence = {'layer': 'application/real SQLite and files; original external prepare/run substitutes',
        'native_script_execution': 0, 'synthetic_descriptor': state.descriptor,
        'port_calls': state.port_calls, 'prepared_calls': state.prepared_calls,
        'receipts': receipt_evidence, 'input_refs': inputs, 'timeline_before_after': before,
        'permit': permit, 'factory': 'main._media_script_use_case',
        'owner': type(state.media.media_script).__module__ + '.' + type(state.media.media_script).__name__,
        'runtime': type(state.runtime).__module__ + '.' + type(state.runtime).__name__,
        'recipe_store': type(state.media.media_script.recipe_tools).__module__ + '.' + type(state.media.media_script.recipe_tools).__name__,
        'resources': type(state.runtime.scheduler.resources).__module__ + '.' + type(state.runtime.scheduler.resources).__name__}
    destination = os.environ.get('MEDIA_SCRIPT_EVIDENCE_DIR')
    if destination:
        (Path(destination) / 'NORMAL_FLOW.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + '\n')
