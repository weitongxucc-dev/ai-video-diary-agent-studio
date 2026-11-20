import json
from threading import Event

from app.content_identity_http import ContentIdentityHttpAdapter
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.assistant.workspace_save_notifications import WorkspaceSaveNotifications
from app.conversation_repository import ConversationRepository
from app.schemas import CreationTaskNarrationVoiceSelectionRequest
from test_narration_voice_selection_receipts import state as state, command


@pytest.fixture
def http_state(state, monkeypatch):
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'creation_execution', SimpleNamespace(latest=lambda *_:None))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'workspace')
    monkeypatch.setattr(ContentIdentityHttpAdapter, 'reader', lambda adapter, request, workspace: ('owner', False))
    monkeypatch.setattr(main, '_exact_edit_access', lambda task_id, request, **kwargs:
        (state.repo.task_records.get_creation_task('workspace', task_id), request.headers.get('X-Test-Member', 'owner')))
    conversations = ConversationRepository(state.repo.database_path)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    app = FastAPI()
    app.add_api_route('/api/creation-tasks/{task_id}/narration/voice', next(route.endpoint for route in main.app.routes if route.name == "select_creation_task_narration_voice"), methods=['POST'])
    app.add_api_route('/api/creation-tasks/{task_id}/narration/voice/requests/{request_id}',
        next(route.endpoint for route in main.app.routes if route.name == "get_creation_task_narration_voice_selection"), methods=['GET'])
    state.client, state.conversations = TestClient(app), conversations
    return state


def body(state, **changes):
    data = command(state, **changes)
    return CreationTaskNarrationVoiceSelectionRequest.model_validate({key: value for key, value in data.items()
        if key not in {'workspace_id', 'task_id', 'member_id'}}).model_dump(mode='json')


def test_voice_http_replay_and_query_return_original_receipt_not_head(http_state):
    state = http_state
    original = body(state)
    selected = state.client.post('/api/creation-tasks/task/narration/voice', json=original)
    assert selected.status_code == 200, selected.text
    later = state.client.post('/api/creation-tasks/task/narration/voice', json=body(state, voice_profile_id='voice-2'))
    assert later.status_code == 200, later.text
    replay = state.client.post('/api/creation-tasks/task/narration/voice', json=original)
    lookup = state.client.get('/api/creation-tasks/task/narration/voice/requests/' + original['request_id'])
    assert selected.json() == replay.json() == lookup.json()
    assert lookup.json()['selected_voice']['profile_id'] == 'voice-1'
    assert state.repo.plan_records.get_creation_plan('workspace', 'plan').selected_voice.profile_id == 'voice-2'
    assert state.client.get('/api/creation-tasks/task/narration/voice/requests/' + original['request_id'],
        headers={'X-Test-Member': 'another-member'}).status_code in {403, 404}


@pytest.mark.parametrize('instruction', ['使用当前候选声音继续制作。', '只讨论，不要制作。', '只写文案，不要开始。'])
def test_pi_voice_selection_uses_real_current_task_turn_and_rejects_draft_only(http_state, instruction):
    state = http_state
    chat = state.conversations.create('workspace', 'owner', 'task', 'task')
    saved = state.conversations.save_draft(chat['id'], 'workspace', 'owner', instruction, 0)
    turn, _ = state.conversations.create_turn_idempotent(chat['id'], 'workspace', 'owner', instruction,
        str(uuid4()), saved['draft_version'], request_context={'kind': 'task', 'task_id': 'task', 'stage': 'narration',
            'target_snapshot': {'timeline_version_id': None, 'timeline_sequence': 0}})
    original = body(state)
    response = state.client.post('/api/creation-tasks/task/narration/voice', json={**original, 'assistant_turn_id': turn['id']})
    allowed = instruction.startswith('使用')
    assert response.status_code == (200 if allowed else 409), response.text
    if allowed:
        assert response.json()['selected_voice']['profile_id'] == 'voice-1'
    else:
        assert state.repo.plan_records.get_creation_plan('workspace', 'plan').selected_voice is None


def test_pi_cannot_select_voice_with_another_tasks_turn(http_state):
    state = http_state
    chat = state.conversations.create('workspace', 'owner', 'task', 'other-task')
    saved = state.conversations.save_draft(chat['id'], 'workspace', 'owner', '选择声音并继续', 0)
    turn, _ = state.conversations.create_turn_idempotent(chat['id'], 'workspace', 'owner', saved['draft_text'],
        str(uuid4()), saved['draft_version'], request_context={'kind': 'task', 'task_id': 'other-task'})
    response = state.client.post('/api/creation-tasks/task/narration/voice', json={**body(state), 'assistant_turn_id': turn['id']})
    assert response.status_code == 409
    assert state.repo.plan_records.get_creation_plan('workspace', 'plan').selected_voice is None


def test_jobless_voice_selection_and_replay_do_not_require_device_execution_permission(http_state, monkeypatch):
    for name in ('_request_permit_actor','_require_permit_actor','_verify_creation_identity'):
        monkeypatch.setattr(main, name, lambda *_:pytest.fail('无Job的内容选声不依赖设备执行许可'))
    route = '/api/creation-tasks/task/narration/voice'
    original = body(http_state)
    first = http_state.client.post(route,json=original)
    assert first.status_code == 200, first.text
    assert http_state.client.post(route,json=original).json() == first.json()
    assert http_state.repo.plan_records.get_creation_plan('workspace','plan').selected_voice.profile_id == 'voice-1'



def test_voice_selection_guard_runs_in_real_transaction_before_mutation(http_state):
    from test_narration_voice_selection_receipts import snapshot
    before=snapshot(http_state.repo)
    def revoked(conn):
        assert conn.in_transaction
        raise PermissionError('原输入已撤销')
    with pytest.raises(PermissionError):
        main._creation_execution_control().select_narration_voice(runtime=None,write_guard=revoked,
            **command(http_state))
    assert snapshot(http_state.repo)==before


def test_committed_voice_receipt_reaches_the_original_sse_before_the_model_finishes(http_state, monkeypatch, request):
    from assistant_turn_control_fixtures import isolate_assistant_turn_control

    isolate_assistant_turn_control(monkeypatch, request)
    state = http_state
    app = state.client.app
    app.state.workspace_save_notifications = WorkspaceSaveNotifications()
    endpoint = '/api/creation-tasks/{task_id}/assistant/turn'
    app.add_api_route(endpoint, next(route.endpoint for route in main.app.routes if route.path == endpoint),
                      methods=['POST'])
    monkeypatch.setattr(main, '_workspace_settings_for', lambda _: SimpleNamespace(
        effective_llm_provider='controlled', effective_llm_model='fixture', effective_llm_api_key=None))
    monkeypatch.setattr(main, '_request_permit_actor', lambda *_: None)
    monkeypatch.setattr(main, 'start_assistant_continuation_driver', lambda: None)
    voice_path = '/api/creation-tasks/task/narration/voice'
    instruction = '为本作品选择指定声音。'
    chat = state.conversations.create('workspace', 'owner', 'task', 'task')
    draft = state.conversations.save_draft(chat['id'], 'workspace', 'owner', instruction, 0)
    voice = body(state)
    model_can_finish = Event()
    saved_frames = []

    def model(_settings, *, turn_id, on_event, **_):
        post = {**voice, 'assistant_turn_id': turn_id}
        rejected = state.client.post(voice_path, json={**post, 'expected_task_updated_at': '2020-01-01T00:00:00Z'})
        assert rejected.status_code == 409, rejected.text
        selected = state.client.post(voice_path, json=post)
        assert selected.status_code == 200, selected.text
        assert model_can_finish.wait(5), 'the SSE did not deliver the save while the model was active'
        on_event({'type':'text_delta','delta':'已采用指定声音。'})
        return '已采用指定声音。', None

    monkeypatch.setattr(main, 'run_task_turn', model)

    async def capture_stream(scope, receive, send):
        async def send_and_release(message):
            for line in message.get('body', b'').splitlines():
                if not line.startswith(b'data: '):
                    continue
                frame = json.loads(line[6:])
                if frame['type'] != 'workspace_saved':
                    continue
                saved_frames.append(frame)
                actual = state.repo.narration_voice_selection.get_creation_task_narration_voice_selection(
                    'workspace', 'task', voice['request_id'], member_id='owner')
                assert actual['status'] == 'selected'
                assert state.repo.plan_records.get_creation_plan('workspace', 'plan').selected_voice.profile_id == 'voice-1'
                model_can_finish.set()
            await send(message)

        await app(scope, receive, send_and_release)

    response = TestClient(capture_stream).post(endpoint.format(task_id='task'),
        headers={'Accept':'text/event-stream'}, json={
            'conversation_id':chat['id'], 'message':instruction, 'context_stage':'narration',
            'client_request_id':str(uuid4()), 'expected_draft_version':draft['draft_version'],
        })
    assert response.status_code == 200, response.text
    assert saved_frames == [{'type':'workspace_saved','status':'saved','task_id':'task',
                             'event_id':f"voice:{voice['request_id']}"}], response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[-1]['type'] == 'result', events
    assert events[-1]['result']['reply'] == '已采用指定声音。'
