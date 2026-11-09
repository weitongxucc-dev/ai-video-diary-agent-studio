"""Source adoption is bound to a real consumed input and its writer recheck."""
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from app.assistant.source_task_binding import SourceTaskBinding
from app.conversation_repository import ConversationRepository
from app.creation.source_task_use_case import SourceTaskConflict
from app.creation.request_workflow_use_case import CreationRequestWorkflowRejected
from test_source_task_use_case import source_case as source_case  # noqa: F401


def command_payload(case):
    return {**asdict(case.command), "expected_asset_updated_at": case.command.expected_asset_updated_at.isoformat()}


def home_turn(case, message='保留原声制作'):
    conversations = ConversationRepository(case.repo.database_path)
    conversation = conversations.create('local', case.actor.member_id, 'home', 'home')
    saved = conversations.save_draft(conversation['id'], 'local', case.actor.member_id, message, 0)
    turn, created = conversations.create_turn_idempotent(conversation['id'], 'local', case.actor.member_id,
        message, str(uuid4()), saved['draft_version'], {'kind': 'home', 'target_snapshot': {
            'material_selection': {'scope': 'asset', 'asset_id': case.asset.id}}})
    assert created
    return conversations, turn


def test_source_adoption_records_actual_home_input(source_case):
    case = source_case
    conversations, turn = home_turn(case)
    source, guard = SourceTaskBinding(conversations).prepare(turn['id'], None, case.actor, case.command.request_id, command_payload(case))
    assert source['selected_asset_id'] == case.asset.id
    assert source['turn_id'] == turn['id'] and source['member_id'] == case.actor.member_id
    receipt = case.use_case.create(case.command, case.actor, source=source, write_guard=guard)
    plan = case.repo.plan_records.get_creation_plan('local', receipt['plan_id'])
    assert plan.adoption['kind'] == 'delegated_system'
    assert plan.adoption['authorization_source'] == source
    assert case.use_case.create(case.command, case.actor, source=source, write_guard=guard) == receipt
    with pytest.raises(SourceTaskConflict):
        case.use_case.create(case.command, case.actor)


@pytest.mark.parametrize('change', ['stopped', 'instruction', 'wrong_member', 'wrong_input', 'discussion'])
def test_source_adoption_never_uses_stale_or_unbound_input(source_case, change):
    case = source_case
    conversations, turn = home_turn(case, '先讨论一下怎么剪，不要开始制作' if change == 'discussion' else '保留原声制作')
    binding = SourceTaskBinding(conversations)
    if change in {'wrong_member', 'wrong_input', 'discussion'}:
        other = replace(case.actor, member_id='not-the-owner') if change == 'wrong_member' else case.actor
        with pytest.raises((SourceTaskConflict, CreationRequestWorkflowRejected)):
            binding.prepare(turn['id'], 'unknown-input' if change == 'wrong_input' else None, other, case.command.request_id, command_payload(case))
    else:
        source, guard = binding.prepare(turn['id'], None, case.actor, case.command.request_id, command_payload(case))
        def observe(actor):
            with case.repo._connect() as conn:
                if change == 'stopped':
                    conn.execute("UPDATE assistant_conversation_turns SET status='stopped' WHERE id=?", (turn['id'],))
                else:
                    conn.execute("UPDATE assistant_conversation_turns SET user_message='不要制作' WHERE id=?", (turn['id'],))
            return 'ready'
        case.use_case.verify_identity = observe
        with pytest.raises((SourceTaskConflict, CreationRequestWorkflowRejected)):
            case.use_case.create(case.command, case.actor, source=source, write_guard=guard)
    assert case.use_case.read(case.command.request_id, case.actor) is None
    assert not case.repo.task_records.list_creation_tasks('local')
