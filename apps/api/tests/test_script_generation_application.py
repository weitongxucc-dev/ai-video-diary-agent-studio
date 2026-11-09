"""Source rejection creates no request;accepted requests retain their own terminal run."""
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.creation_execution import CreationExecutionActor
from test_creation_task_script_grounding import api_state as api_state


def test_generation_source_preflight_records_original_run_without_model(api_state, monkeypatch):
    state = api_state
    monkeypatch.setattr(main, "conversation_repo", ConversationRepository(state.repository.database_path))
    previous = state.sources.get_knowledge_item('local', 'fixture').use_state
    state.sources.set_knowledge_item_use_state('local', 'fixture', 'excluded')
    def forbidden(*args, **kwargs):
        pytest.fail('preflight rejection must not construct or invoke an Agent/provider')
    monkeypatch.setattr(main, '_creation_task_script_generator_for', forbidden)
    monkeypatch.setattr(main, '_run_script_generation_agent', forbidden)
    rejected = state.client.post('/api/creation-tasks/task/scripts/generate', json={'request_id': str(uuid4())})
    assert rejected.status_code == 409, rejected.text
    assert state.repository.script_records.latest_script_generation_run('local', 'task') is None
    state.sources.set_knowledge_item_use_state('local', 'fixture', previous)
    case = main._script_generation_intent('local')
    request_id = str(uuid4())
    accepted, created = case.start('local', 'task', request_id, CreationExecutionActor('local', state.owner.member_id))
    assert created
    state.sources.set_knowledge_item_use_state('local', 'fixture', 'excluded')
    result = case.execute('local', 'task', request_id, state.owner.member_id, run_agent=forbidden, script_case=None)
    assert result['request_id'] == request_id and result['run_id'] == accepted['run_id']
    assert result['turn_id'] == accepted['turn_id'] and result['status'] == 'failed', result
    run = state.repository.script_records.get_script_generation_run('local', accepted['run_id'])
    assert run.status == 'failed' and run.finished_at is not None
    assert run.reason == 'generation_not_completed'
    assert case.execute('local', 'task', request_id, state.owner.member_id, run_agent=forbidden, script_case=None) == result
    assert state.repository.script_records.list_creation_task_scripts('local', 'task') == []
    assert state.verifications == []
