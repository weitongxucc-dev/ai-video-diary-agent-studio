"""Real SQLite ownership receipts; no provider, worker or media execution."""
from dataclasses import replace
from uuid import uuid4

import pytest

from app.assistant.goal_versions import observation_source_is_owned, observation_turn_origin
from app.conversation_repository import ConversationRepository
from app.creation.contracts import JobRun, TaskEvent
from test_typed_assistant_edits_api import metadata_http_state as metadata_http_state  # noqa: F401


def live_delivery(state, monkeypatch, *, source_patch=None, locator_digest=None, with_input=False):
    production = state.repo
    conversations = ConversationRepository(production.database_path)
    ws, member = state.task.workspace_id, state.member.member_id
    chat = conversations.create(ws, member, 'task', state.task.id)
    chat = conversations.save_draft(chat['id'], ws, member, '输出后检查当前成片', chat['draft_version'])
    expected = {'timeline_version_id': state.base.id, 'timeline_sequence': state.base.sequence}
    context = {'kind': 'task', 'task_id': state.task.id, 'target_snapshot': expected}
    turn, _ = conversations.create_turn_idempotent(chat['id'], ws, member, chat['draft_text'],
        str(uuid4()), chat['draft_version'], context)
    input_source = {}
    if with_input:
        current = conversations.get(chat['id'], ws, member)
        draft = conversations.save_draft(chat['id'], ws, member, '补充真实输入规格', current['draft_version'])
        input_id = str(uuid4())
        conversations.enqueue_input(chat['id'], ws, member, target_turn_id=turn['id'], client_request_id=input_id,
            message=draft['draft_text'], mode='steer', expected_draft_version=draft['draft_version'],
            expected_reference_version=0, request_context=context)
        conversations.transition_input(chat['id'], ws, member, input_id, 'consumed')
        input_source = {'input_id': input_id, 'input_turn_id': turn['id']}
    job = production.job_records.create_job_run(JobRun('declared-render', ws, state.task.id,
        'creation_task_execution', 'declared-render', 'a' * 64))
    production.job_records.claim_job_run(ws, job.id)
    production.job_records.finish_job_run(ws, job.id, status='succeeded')
    source = {'workspace_id': ws, 'task_id': state.task.id, 'member_id': member,
        'conversation_id': chat['id'], 'assistant_turn_id': turn['id'],
        'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': state.base.sequence,
        'action': 'resume', **input_source}
    production.execution_records.append_task_event(TaskEvent('declared-render-locator', ws, state.task.id,
        job.id, 'assistant_execution_located', {'job_id': job.id,
            'input_digest': locator_digest or job.input_digest, 'source': {**source, **(source_patch or {})}}))
    monkeypatch.setattr(production, 'goal_render_request_row', lambda *_: {'owner_id': job.id, 'state': 'done'})
    delivered = replace(state.base, id='declared-delivery', parent_version_id=state.base.id,
        sequence=state.base.sequence + 1, snapshot={'creation_render_request_id': 'declared-publication',
            'render_input_timeline_version_id': state.base.id})
    production.timeline_records.create_timeline_version(delivered)
    task = production.task_records.get_creation_task(ws, state.task.id)
    return conversations, turn, task, delivered, source, expected


@pytest.mark.parametrize('with_input', [False, True])
def test_live_turn_can_observe_its_own_delivery_before_goal_registration(metadata_http_state, monkeypatch, with_input):
    state = metadata_http_state
    chats, turn, task, version, source, expected = live_delivery(state, monkeypatch, with_input=with_input)
    assert chats.get_task_goal(turn['id']) is None
    origin = observation_turn_origin(state.repo, chats, task, state.member.member_id, turn['id'], version)
    assert origin['conversation_id'] == source['conversation_id']
    assert origin['context']['target_snapshot'] == expected
    assert chats.task_turn_source(turn['id'], task.workspace_id, state.member.member_id,
        task.id)['context']['target_snapshot'] == expected, 'observation must not advance editing authority'
    observed_source = {**source, 'expected_timeline_version_id': version.id, 'expected_timeline_sequence': version.sequence}
    start = {'operation': 'output_observation', 'result_kind': 'observation'}
    assert observation_source_is_owned(state.repo, chats, task, observed_source, expected, start)
    foreign = replace(version, id='unrelated-edit', parent_version_id=version.id, sequence=version.sequence + 1, snapshot={})
    state.repo.timeline_records.create_timeline_version(foreign)
    with pytest.raises(ValueError, match='版本已失效'):
        observation_turn_origin(state.repo, chats, state.repo.task_records.get_creation_task(task.workspace_id, task.id),
            state.member.member_id, turn['id'], foreign)


@pytest.mark.parametrize('patch', [
    {'member_id': 'another-member'}, {'conversation_id': 'another-conversation'},
    {'assistant_turn_id': str(uuid4())}, {'input_id': str(uuid4()), 'input_turn_id': str(uuid4())},
    {'expected_timeline_version_id': 'another-version'}, {'expected_timeline_sequence': 999}, {'action': 'cancel'},
])
def test_delivery_cannot_borrow_another_origin(metadata_http_state, monkeypatch, patch):
    state = metadata_http_state
    chats, turn, task, version, _, _ = live_delivery(state, monkeypatch, source_patch=patch)
    with pytest.raises(ValueError, match='版本已失效'):
        observation_turn_origin(state.repo, chats, task, state.member.member_id, turn['id'], version)


def test_delivery_cannot_borrow_another_render_digest(metadata_http_state, monkeypatch):
    state = metadata_http_state
    chats, turn, task, version, _, _ = live_delivery(state, monkeypatch, locator_digest='b' * 64)
    with pytest.raises(ValueError, match='版本已失效'):
        observation_turn_origin(state.repo, chats, task, state.member.member_id, turn['id'], version)
