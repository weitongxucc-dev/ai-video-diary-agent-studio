"""Real HTTP/use-case writes on isolated storage with a controlled review provider."""
from dataclasses import asdict
from uuid import uuid4
import json

import httpx
import pytest

import app.main as main
from app.creation.script_routes import build_script_sections_router
from test_creation_task_script_grounding import (
    api_state as api_state, agent_payload, draft, reviewed, saved_row_draft, set_agent_reviewer,
    FACT, OpenAICompatibleCreationTaskScriptGenerator, _review_tool_response,
    set_api_provider,
)


def test_patch_http_preserves_unread_sections_and_replays_original_receipt(api_state, monkeypatch):
    state = api_state
    original = saved_row_draft(state)
    state.client.app.include_router(build_script_sections_router(
        readable_task=main._readable_creation_task,
        read_sections=state.repository.script_records.read_creation_task_script_sections,
        patch_sections=main._patch_creation_script_sections,
    ))
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    section = asdict(original.sections[-1])
    section['visual_prompt'] = '展示本机素材用途设置界面'
    body = {'request_id': str(uuid4()), 'expected_updated_at': original.updated_at.isoformat(),
            'sections': [section]}
    url = f'/api/creation-tasks/task/scripts/{original.id}/sections'
    response = state.client.patch(url, json=body)
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    saved = state.repository.script_records.get_creation_task_script('local', original.id)
    assert len(saved.sections) == 25
    assert saved.sections[:-1] == original.sections[:-1]
    assert saved.sections[-1].visual_prompt == section['visual_prompt']
    replay = state.client.patch(url, json=body)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert len(calls) == 1
    stale = state.client.patch(url, json={**body, 'request_id': str(uuid4())})
    assert stale.status_code == 409, stale.text
    assert len(calls) == 1
    missing_version = dict(body)
    del missing_version['expected_updated_at']
    assert state.client.patch(url, json=missing_version).status_code == 422
    assert state.client.patch(url.replace('row-script', 'missing'), json=body).status_code == 404
    assert len(calls) == 1


def test_legacy_confirmation_rechecks_current_input_before_adoption(api_state, monkeypatch):
    state = api_state
    calls = []
    set_api_provider(monkeypatch, draft(), reviewed(draft()), calls)
    generated = state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert generated.status_code == 200, generated.text
    script = state.repository.script_records.list_creation_task_scripts('local', 'task')[0]
    turn_id = str(uuid4())
    checks = []
    def current_origin(task_id, request, actual_turn_id, *, draft_only=False):
        checks.append((task_id, actual_turn_id, draft_only))
        return {'input_id': 'original' if len(checks) == 1 else 'superseded'}
    monkeypatch.setattr(main, '_assistant_creation_adoption_scope', current_origin)
    state.client.app.add_api_route('/api/creation-tasks/{task_id}/scripts/{script_id}/confirm',
        main.confirm_creation_task_script, methods=['POST'])
    body = {'title': script.title, 'sections': [asdict(section) for section in script.sections],
            'expected_updated_at': script.updated_at.isoformat(),
            'adoption_kind': 'delegated_system', 'assistant_turn_id': turn_id}
    result = state.client.post(f'/api/creation-tasks/task/scripts/{script.id}/confirm', json=body)
    assert result.status_code == 409, result.text
    assert '原输入已变化' in result.json()['detail']
    assert checks == [('task', turn_id, False), ('task', turn_id, False)]
    assert state.repository.script_records.get_creation_task_script('local', script.id) == script
    assert state.repository.task_records.get_creation_task('local', 'task').confirmed_script_id is None
    assert len(calls) == 2  # The unchanged candidate does not run another review.


def test_start_http_creates_partial_draft_without_an_existing_script(api_state, monkeypatch):
    state = api_state
    state.client.app.include_router(build_script_sections_router(
        readable_task=main._readable_creation_task,
        read_sections=state.repository.script_records.read_creation_task_script_sections,
        patch_sections=main._patch_creation_script_sections,
        start_window=main._start_creation_script_window,
        confirm_saved=main._confirm_saved_creation_script,
        read_request=main._read_creation_script_window_request,
    ))
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    body = {**agent_payload(state), 'request_id': str(uuid4())}
    url = '/api/creation-tasks/task/scripts/windows'
    response = state.client.post(url, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total_count'] == 1
    saved = state.repository.script_records.get_creation_task_script('local', result['script_id'])
    assert saved.draft_complete is False
    assert state.repository.task_records.get_creation_task('local', 'task').confirmed_script_id is None
    replay = state.client.post(url, json=body)
    assert replay.status_code == 200 and replay.json() == result
    assert len(calls) == 1
    assert state.client.post(url, json={**body, 'title': '换输入'}).status_code == 409
    assert state.client.post(url, json={**body, 'draft_complete': True}).status_code == 422
    context = state.client.get('/api/creation-tasks/task/assistant/script-context')
    assert context.status_code == 200, context.text
    assert context.json()['draft']['sections'] is None
    assert context.json()['draft']['draft_complete'] is False
    assert len(calls) == 1
    lookup = f"/api/creation-tasks/task/script-window-requests/{body['request_id']}?operation=start"
    assert state.client.get(lookup).json() == result
    confirmation = {'request_id': str(uuid4()), 'expected_updated_at': result['updated_at']}
    confirmed = state.client.post(f"/api/creation-tasks/task/scripts/{result['script_id']}/confirm-saved",
                                  json=confirmation)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['status'] == 'confirmed'
    assert len(calls) == 2
    saved = state.repository.script_records.get_creation_task_script('local', result['script_id'])
    assert saved.draft_complete is True and saved.selection_status == 'confirmed'
    lookup = (f"/api/creation-tasks/task/script-window-requests/{confirmation['request_id']}"
              f"?operation=confirm&script_id={result['script_id']}")
    assert state.client.get(lookup).json() == confirmed.json()
    assert len(calls) == 2


def test_45_section_target_through_http_windows_and_complete_adoption(api_state, monkeypatch):
    state = api_state
    with state.repository._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()
        plan = json.loads(row[0])
        plan['template_settings'] = {'script_section_count': 45}
        conn.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
    calls = []
    def review(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(payload)
        assert payload['requirements']['section_count'] == 45
        return _review_tool_response({'sections': [{'index': part['index'], 'supports': [{
            'candidate_quote_id': part['content'][0]['quote_id'],
            'source_quote_id': part['allowed_source_quote_ids']['evidence_1'][0],
        }]} for part in payload['script']['sections']]})
    checker = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(review))
    monkeypatch.setattr(main, '_creation_task_script_generator_for', lambda settings: checker)
    state.client.app.include_router(build_script_sections_router(
        readable_task=main._readable_creation_task,
        read_sections=state.repository.script_records.read_creation_task_script_sections,
        patch_sections=main._patch_creation_script_sections,
        start_window=main._start_creation_script_window,
        confirm_saved=main._confirm_saved_creation_script,
        read_request=main._read_creation_script_window_request,
    ))
    def sections(start, end):
        return [{'id': f'section-{index}', 'title': f'段落{index}', 'content': FACT,
            'visual_prompt': '素材用途画面', 'evidence_reference_ids': [state.refs[0]['reference_id']]}
            for index in range(start, end)]
    first = state.client.post('/api/creation-tasks/task/scripts/windows', json={
        'request_id': str(uuid4()), 'title': '45段目标', 'sections': sections(0, 20)})
    assert first.status_code == 200, first.text
    last = first.json()
    script_id = last['script_id']
    for start, end in ((20, 40), (40, 45)):
        response = state.client.patch(f'/api/creation-tasks/task/scripts/{script_id}/sections', json={
            'request_id': str(uuid4()), 'expected_updated_at': last['updated_at'], 'sections': sections(start, end)})
        assert response.status_code == 200, response.text
        last = response.json()
    assert last['total_count'] == 45 and len(calls) == 3
    confirmation = {'request_id': str(uuid4()), 'expected_updated_at': last['updated_at']}
    with monkeypatch.context() as bounded:
        bounded.setattr(state.repository.script_records, 'get_creation_task_script', lambda *args, **kwargs:
                       pytest.fail('full adoption must not load the whole script'))
        response = state.client.post(f'/api/creation-tasks/task/scripts/{script_id}/confirm-saved', json=confirmation)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'confirmed' and response.json()['total_count'] == 45
    assert len(calls) == 6
    assert state.repository.task_records.get_creation_task('local', 'task').confirmed_script_id == script_id
    receipt = state.client.get(f"/api/creation-tasks/task/script-window-requests/{confirmation['request_id']}",
                               params={'operation': 'confirm', 'script_id': script_id})
    assert receipt.status_code == 200 and receipt.json() == response.json()
    assert len(calls) == 6


def test_legacy_window_requirement_does_not_mark_model_connection_failed(api_state, monkeypatch):
    state = api_state
    with state.repository._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()
        plan = json.loads(row[0])
        plan['template_settings'] = {'script_section_count': 45}
        conn.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
    calls = []
    set_api_provider(monkeypatch, draft(), reviewed(draft()), calls)
    response = state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert response.status_code == 422, response.text
    assert 'window_required' in response.text
    assert not calls and not state.verifications
    run = state.repository.script_records.latest_script_generation_run('local', 'task')
    assert run.route_snapshot['availability'] == 'unverified'
