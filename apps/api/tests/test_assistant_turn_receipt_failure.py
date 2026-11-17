"""Rejected final receipts must not erase invocation or committed business facts."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.assistant import task_turn_use_case as task
from app.assistant.turn_admission_use_case import TurnRejected, ReadWorkspace, CleanText
from app.conversation_repository import ConversationRepository
from app.creation.media_result_use_case import MediaResultRejected
from app.assistant.trace import project_turn_trace
from test_creation_task_script_grounding import api_state as api_state  # noqa: F401


@pytest.mark.parametrize("kind", ["edit_request_status", "media_job"])
def test_rejected_final_receipt_persists_failed_turn_and_sdk_evidence(tmp_path, kind):
    repo = ConversationRepository(tmp_path / 'turn.sqlite')
    chat = repo.create('w', 'm', scope_kind='task', scope_id='t')
    repo.save_draft(chat['id'], 'w', 'm', 'import then render', 0)
    turn, _ = repo.create_turn_idempotent(chat['id'], 'w', 'm', 'import then render',
        str(uuid4()), 1, request_context={'kind': 'task', 'task_id': 't'})
    original = {'kind': 'script_window_request', 'operation': 'start', 'task_id': 't',
                'request_id': str(uuid4()), 'status': 'unknown', 'submission_effect': 'unknown'}
    assert repo.record_script_window_request(turn['id'], 'w', 'm', 't', original)
    before = repo.get_turn(turn['id'])['result']
    runtime = {'usage_status': 'known', 'provider_usage': {'input': 7, 'output': 2, 'totalTokens': 9},
               'trace_events': [{'type': 'turn_start', 'event_id': 'sdk-turn', 'at_ms': 1}]}
    preview = {'kind': kind, 'related_media_job': {'job_id': 'foreign'},
               'media_job': {'job_id': 'foreign'}, **runtime}
    case = task.TaskAssistantTurnUseCase(conversations_for=lambda: repo, window_case_for=None,
        read_event=None, read_intent=None, list_events=None)
    operation = case.finish('t', SimpleNamespace(conversation_id=chat['id']), turn['id'],
                            'model reply', preview, 'montage')
    checkpoint = next(operation)
    with pytest.raises(TurnRejected) as rejected:
        while True:
            if isinstance(checkpoint, ReadWorkspace):
                value = 'w'
            elif isinstance(checkpoint, (CleanText, task.SafeText)):
                value = checkpoint.value
            elif isinstance(checkpoint, task.ResolveEditRequest):
                value = SimpleNamespace(status='unknown')
            elif isinstance(checkpoint, (task.RelatedMedia, task.SelectMediaFinish)):
                checkpoint = operation.throw(MediaResultRejected(502, 'foreign goal receipt'))
                continue
            else:
                pytest.fail(f'unexpected checkpoint {checkpoint}')
            checkpoint = operation.send(value)
    assert rejected.value.status_code == 502
    saved = ConversationRepository(repo.database_path).get_turn(turn['id'])
    assert saved['status'] == 'failed'
    assert saved['result']['reason'] == 'final_receipt_rejected'
    assert saved['result']['response'] == before['response']
    assert saved['result']['provider_usage'] == runtime['provider_usage']
    assert saved['result']['trace_events'] == runtime['trace_events']
    assert 'foreign' not in str(saved['result']['response'])
    trace = project_turn_trace(saved)
    assert trace['usage_status'] == 'known'
    assert trace['provider_usage']['totalTokens'] == 9
    assert trace['event_count'] >= 1


def test_json_task_turn_records_live_events_and_fails_final_projection(api_state, monkeypatch):
    import app.main as main
    from app.assistant.task_turn_routes import TaskTurnHTTPAdapter

    state = api_state
    repo = ConversationRepository(state.repository.database_path)
    member = state.owner.member_id
    chat = repo.create('local', member, scope_kind='task', scope_id='task')
    chat = repo.save_draft(chat['id'], 'local', member, 'import then render', 0)
    monkeypatch.setattr(main, 'conversation_repo', repo)
    turn_ids = []

    def run(*args, **kwargs):
        turn_id = kwargs['turn_id']
        turn_ids.append(turn_id)
        kwargs['on_event']({'type': 'tool_start', 'tool_id': 1, 'label': 'read task',
                            'arguments': {'secret': 'must not persist'}})
        kwargs['on_event']({'type': 'tool_end', 'tool_id': 1, 'label': 'read task',
                            'ok': True, 'summary': 'done'})
        live = ConversationRepository(repo.database_path).get_turn(turn_id)
        assert live['status'] == 'running'
        assert [item['type'] for item in live['events']] == ['tool_start', 'tool_end']
        assert 'must not persist' not in str(live['events'])
        return 'model reply', {'kind': 'edit_request_status', 'related_media_job': {'job_id': 'foreign'},
            'usage_status': 'known', 'provider_usage': {'input': 5, 'totalTokens': 5},
            'trace_events': [{'type': 'turn_start', 'event_id': 'sdk', 'at_ms': 1}]}

    original_answer = TaskTurnHTTPAdapter._answer

    def answer(self, checkpoint, request, on_event, response_model):
        if isinstance(checkpoint, task.ResolveEditRequest):
            return SimpleNamespace(status='unknown')
        if isinstance(checkpoint, task.RelatedMedia):
            raise MediaResultRejected(502, 'foreign goal receipt')
        return original_answer(self, checkpoint, request, on_event, response_model)

    monkeypatch.setattr(main, 'run_task_turn', run)
    monkeypatch.setattr(TaskTurnHTTPAdapter, '_answer', answer)
    state.client.app.add_api_route('/api/creation-tasks/{task_id}/assistant/turn',
        main._task_turn_http_adapter().task_turn, methods=['POST'])
    response = state.client.post('/api/creation-tasks/task/assistant/turn', json={
        'conversation_id': chat['id'], 'client_request_id': str(uuid4()),
        'expected_draft_version': chat['draft_version'], 'message': 'import then render',
        'context_stage': 'script', 'selected_object_refs': []})
    assert response.status_code == 502, response.text
    saved = ConversationRepository(repo.database_path).get_turn(turn_ids[0])
    assert saved['status'] == 'failed'
    trace = project_turn_trace(saved)
    assert trace['provider_usage'] == {'input': 5, 'totalTokens': 5}
    assert trace['tool_counts'] == {'read task': 1}
