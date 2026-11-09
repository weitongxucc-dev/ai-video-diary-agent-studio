"""Persist real window receipts through the ordinary task conversation finish."""
from types import SimpleNamespace
from uuid import UUID, uuid4
import json

from fastapi import Request
import pytest

import app.main as main
from app.assistant.task_turn_use_case import TaskAssistantTurnUseCase
from app.conversation_repository import ConversationRepository
from app.schemas import CreationTaskScriptWindowStartRequest
from test_creation_task_script_grounding import (
    api_state as api_state, agent_payload, draft, reviewed, set_agent_reviewer,  # noqa: F401
)


@pytest.fixture(autouse=True)
def window_turn_metadata_boundary(request, monkeypatch):
    if request.node.originalname == 'test_tool_receipt_survives_model_end_before_final_response':
        from test_production_foundation import edit_adoption_no_execution
        forbidden = edit_adoption_no_execution(monkeypatch)
        monkeypatch.setattr(main, '_creation_task_script_generator_for', forbidden)


def test_window_receipt_finishes_conversation_without_full_script_projection(api_state, monkeypatch):
    state = api_state
    conversations = ConversationRepository(state.repository.database_path)
    member = state.owner.member_id
    conversation = conversations.create('local', member, scope_kind='task', scope_id='task')
    message = '查询原文案窗口保存结果'
    conversation = conversations.save_draft(conversation['id'], 'local', member, message, conversation['draft_version'])
    turn, _ = conversations.create_turn_idempotent(conversation['id'], 'local', member,
        user_message=message, client_request_id=str(uuid4()), expected_draft_version=conversation['draft_version'],
        request_context={'kind': 'task', 'task_id': 'task', 'stage': 'script', 'plan_id': 'plan'})
    conversations.task_turn_source(turn['id'], 'local', member, 'task')
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, '_goal_service', lambda: SimpleNamespace(register_turn=lambda _: None,
        record_script_adoption=lambda *args, **kwargs: pytest.fail('reading a saved window cannot adopt it')))
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    monkeypatch.setattr(TaskAssistantTurnUseCase, 'action_proofs', lambda *args, **kwargs: [])
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [
        (b'x-local-member-token', state.client.headers['X-Local-Member-Token'].encode())]})
    case = main._script_draft_use_case('local', validate_inputs=lambda ws, plan, refs:
        main._validate_script_input_sources(ws, plan, refs, member))
    payload = CreationTaskScriptWindowStartRequest(**agent_payload(state), request_id=uuid4())
    saved = case.start_window('local', 'task', payload, member_id=member)
    preview = {'kind': 'script_window_request', 'operation': 'start', 'task_id': 'task',
        'request_id': str(payload.request_id),
        'status': 'unknown', 'submission_effect': 'unknown'}
    monkeypatch.setattr(state.repository.script_records, 'get_creation_task_script', lambda *args, **kwargs:
        pytest.fail('window turn response must not hydrate whole script'))
    result = main._task_turn_http_adapter().finish('task', request, SimpleNamespace(
        conversation_id=UUID(conversation['id']), expected_draft_version=conversation['draft_version']),
        turn['id'], '已经完成', preview, 'script')
    assert result.script_draft is None
    assert result.script_window_request.status == 'saved'
    assert result.script_window_request.script_id == saved['script_id']
    assert '不代表完整稿已采用' in result.reply
    persisted = conversations.get_turn(turn['id'])
    assert persisted['result']['response']['script_window_request']['request_id'] == str(payload.request_id)
    assert len(calls) == 1


def test_new_consumed_input_does_not_inherit_previous_window_projection(tmp_path):
    conversations = ConversationRepository(tmp_path / 'input-window.sqlite')
    chat = conversations.create('w', 'm', scope_kind='task', scope_id='t')
    conversations.save_draft(chat['id'], 'w', 'm', '原要求', 0)
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'w', 'm',
        '原要求', 'first', 1, request_context={'kind': 'task', 'task_id': 't'})
    window = {'kind': 'script_window_request', 'operation': 'start', 'task_id': 't',
        'request_id': str(uuid4()), 'status': 'unknown', 'submission_effect': 'unknown'}
    assert conversations.record_script_window_request(turn['id'], 'w', 'other', 't', window) is False
    assert conversations.record_script_window_request(turn['id'], 'w', 'm', 't', window)
    conversations.save_draft(chat['id'], 'w', 'm', '新的要求', 2)
    conversations.enqueue_input(chat['id'], 'w', 'm', target_turn_id=turn['id'],
        client_request_id='second', message='新的要求', mode='steer',
        expected_draft_version=3, expected_reference_version=0,
        request_context={'kind': 'task', 'task_id': 't'})
    conversations.transition_input(chat['id'], 'w', 'm', 'second', 'consumed')
    assert conversations.record_script_window_request(turn['id'], 'w', 'm', 't', window) is False
    conversations.finish_turn(turn['id'], 'failed', result={'kind': 'failure'})
    saved = conversations.get_turn(turn['id'])
    assert saved['result'].get('response', {}).get('script_window_request') is None
    assert [event['request'] for event in saved['events']
            if event['type'] == 'script_window_request_recorded'] == [window]


@pytest.mark.parametrize('terminal', ['failed', 'stopped', 'restart'])
def test_tool_receipt_survives_model_end_before_final_response(api_state, monkeypatch, terminal):
    from app.assistant.projection import _PiStreamProjector
    state = api_state
    conversations = ConversationRepository(state.repository.database_path)
    member = state.owner.member_id
    conversation = conversations.create('local', member, scope_kind='task', scope_id='task')
    conversation = conversations.save_draft(conversation['id'], 'local', member,
        '保存文案窗口', conversation['draft_version'])
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, '_request_permit_actor', lambda *args: None)
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    request_id = uuid4()
    seen = []
    def tool_then_interrupt(*args, **kwargs):
        case = main._script_draft_use_case('local', validate_inputs=lambda ws, plan, refs:
            main._validate_script_input_sources(ws, plan, refs, member))
        payload = CreationTaskScriptWindowStartRequest(**agent_payload(state), request_id=request_id)
        receipt = case.start_window('local', 'task', payload, member_id=member)
        event = _PiStreamProjector().project(json.dumps({'type': 'tool_execution_end',
            'toolName': 'start_script_window', 'toolCallId': 'window-call',
            'isError': False, 'result': {'details': {'kind': 'script_window_request',
                'operation': 'start', 'task_id': 'task', 'request_id': str(request_id), 'receipt': receipt,
                'invocation_input_id': kwargs['turn_id'], 'input_id': None, 'input_turn_id': None}}}))
        assert event.get('invocation_input_id') == kwargs['turn_id']
        binding = dict(workspace_id='local', member_id=member, task_id='task', turn_id=kwargs['turn_id'])
        assert main._task_turn_http_adapter().case().record_window_event({**event, 'invocation_input_id': str(uuid4())}, **binding) is False
        mismatched = {**event['script_window_request'], 'input_id': 'other-input', 'input_turn_id': kwargs['turn_id']}
        assert main._task_turn_http_adapter().case().record_window_event({**event, 'invocation_input_id': 'other-input',
            'script_window_request': mismatched}, **binding) is False
        assert conversations.get_turn(kwargs['turn_id'])['result'] is None
        kwargs['on_event'](event)
        kwargs['on_event'](event)  # Replayed tool events must not create a second locator.
        turn = conversations.get_turn(kwargs['turn_id'])
        assert turn['status'] == 'running'
        assert turn['result'] is not None, (event, conversations.task_turn_source(
            kwargs['turn_id'], 'local', member, 'task'))
        assert turn['result']['response']['script_window_request']['status'] == 'saved'
        assert not turn['result'].get('goal_business_actions')
        seen.append(turn['id'])
        if terminal == 'stopped':
            raise main.AssistantStopped('stopped after original tool')
        if terminal == 'restart':
            assert ConversationRepository(state.repository.database_path).fail_interrupted_turns() == 1
        raise main.AssistantUnavailable('model ended after original tool')
    monkeypatch.setattr(main, 'run_task_turn', tool_then_interrupt)
    state.client.app.add_api_route('/api/creation-tasks/{task_id}/assistant/turn',
        main._task_turn_http_adapter().task_turn, methods=['POST'])
    response = state.client.post('/api/creation-tasks/task/assistant/turn', json={
        'conversation_id': conversation['id'], 'client_request_id': str(uuid4()),
        'expected_draft_version': conversation['draft_version'], 'message': '保存文案窗口',
        'context_stage': 'script', 'selected_object_refs': []})
    assert response.status_code == (200 if terminal == 'stopped' else 503), response.text
    turn = conversations.get_turn(seen[0])
    assert turn['status'] == ('failed' if terminal == 'restart' else terminal)
    window = turn['result']['response']['script_window_request']
    assert window['request_id'] == str(request_id) and window['status'] == 'saved'
    assert len([event for event in turn['events'] if event['type'] == 'script_window_request_recorded']) == 1
    assert not turn['result'].get('goal_business_actions')
    assert state.repository.task_records.get_creation_task('local', 'task').confirmed_script_id is None
    if terminal == 'stopped':
        assert response.json()['script_window_request'] == window
    assert conversations.record_script_window_request(seen[0], 'local', member, 'task', window) is False
    assert conversations.get_turn(seen[0])['result'] == turn['result']
    assert len(calls) == 1
