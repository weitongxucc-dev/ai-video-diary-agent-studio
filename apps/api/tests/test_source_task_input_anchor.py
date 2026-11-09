"""Original full source inputs survive unknown outcomes without creating work."""
from dataclasses import asdict
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.assistant.source_task_binding import SourceTaskBinding
from app.conversation_repository import ConversationRepository
from test_source_task_use_case import source_case as source_case  # noqa: F401


@pytest.fixture
def anchor_case(source_case):  # noqa: F811 (shared isolated source/member fixture)
    case = source_case
    conversations = ConversationRepository(case.repo.database_path)
    member = case.actor.member_id
    chat = conversations.create('local', member, 'home', 'home')
    message = '请把这个原片保留原声继续剪辑。'
    draft = conversations.save_draft(chat['id'], 'local', member, message, 0)
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', member, message, str(uuid4()),
        draft['draft_version'], request_context={'kind': 'home',
            'target_snapshot': {'material_selection': {'asset_id': case.asset.id}}})
    body = json.loads(json.dumps(asdict(case.command), default=lambda value: value.isoformat()))
    return SimpleNamespace(case=case, repo=conversations, binding=SourceTaskBinding(conversations),
        turn=turn, chat=chat, member=member, body=body)


def test_original_full_source_input_survives_stopped_turn_and_reopened_database(anchor_case):
    state = anchor_case
    source, _guard = state.binding.prepare(state.turn['id'], None, state.case.actor,
        state.body['request_id'], state.body)
    assert source['workspace_id'] == 'local'
    assert state.binding.prepare(state.turn['id'], None, state.case.actor,
        state.body['request_id'], state.body)[0] == source
    events = state.repo.get_turn(state.turn['id'])['events']
    assert len([event for event in events if event.get('type') == 'source_task_input']) == 1
    state.repo.finish_turn(state.turn['id'], 'failed', '宿主退出，原请求结果尚未确定。')
    state.case.path.unlink()  # Reading the anchor must not re-probe the old file.
    reopened = ConversationRepository(state.repo.database_path)
    assert reopened.read_source_task_input('local', state.member, state.body['request_id']) == {
        'source': source, 'payload': state.body}
    assert reopened.read_source_task_input('other-workspace', state.member, state.body['request_id']) is None
    assert reopened.read_source_task_input('local', 'other-member', state.body['request_id']) is None
    assert reopened.read_source_task_input('local', state.member, str(uuid4())) is None
    with state.case.repo._connect() as connection:
        assert connection.execute('SELECT count(*) FROM creation_tasks').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 0


def test_same_source_uuid_cannot_replace_full_parameters_or_stale_origin(anchor_case, monkeypatch):
    state = anchor_case
    source, guard = state.binding.prepare(state.turn['id'], None, state.case.actor,
        state.body['request_id'], state.body)
    with pytest.raises(ValueError, match='完整参数'):
        state.binding.prepare(state.turn['id'], None, state.case.actor,
            state.body['request_id'], {**state.body, 'source_end_seconds': 8})
    before = state.repo.get_turn(state.turn['id'])['events']
    with pytest.raises(ValueError, match='当前输入'):
        state.repo.bind_source_task_input(source, state.body,
            verify_source=lambda connection: {**source, 'input_id': 'another-input'})
    assert state.repo.get_turn(state.turn['id'])['events'] == before
    with state.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        with monkeypatch.context() as patch:
            patch.setattr(state.repo, '_connect', lambda: pytest.fail('source guard must reuse the original transaction'))
            guard(connection)
