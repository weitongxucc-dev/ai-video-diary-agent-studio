"""Real source adoption and conversation scope; no model or media execution."""
import copy
from dataclasses import asdict, replace
import json
from uuid import uuid4

import pytest

from app.assistant.source_task_binding import SourceTaskBinding
from app.assistant_goal_continuation import GoalContinuationService
from app.conversation_repository import ConversationRepository
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from test_source_task_use_case import source_case as source_case  # noqa: F401


@pytest.fixture
def goal_case(source_case):  # noqa: F811 (shared metadata-only source fixture)
    case = source_case
    case.authorizations = TaskAuthorizationUseCase(case.repo, lambda identity: case.verify(
        CreationExecutionActor.from_event(identity['workspace_id'], identity)))
    case.conversations = ConversationRepository(case.repo.database_path,
        task_goal_input_authorized=case.authorizations.adopted_goal_input_valid)
    member = case.actor.member_id
    case.chat = case.conversations.create('local', member, 'home', 'home')
    case.message = '把当前选中的原视频保留原声，继续制作成片。'
    case.home_context = {'kind': 'home', 'target_snapshot': {'material_selection': {'asset_id': case.asset.id}}}
    draft = case.conversations.save_draft(case.chat['id'], 'local', member, case.message, 0)
    case.turn, _ = case.conversations.create_turn_idempotent(case.chat['id'], 'local', member,
        case.message, str(uuid4()), draft['draft_version'], request_context=case.home_context)
    case.source, guard = SourceTaskBinding(case.conversations).prepare(
        case.turn['id'], None, case.actor, case.command.request_id,
        json.loads(json.dumps(asdict(case.command), default=lambda value: value.isoformat())))
    case.receipt = case.use_case.create(case.command, case.actor, source=case.source, write_guard=guard)
    case.task = case.repo.task_records.get_creation_task('local', case.receipt['task_id'])
    case.plan = case.repo.plan_records.get_creation_plan('local', case.task.plan_id)
    case.event, = [event for event in case.repo.execution_records.list_task_events('local', case.task.id)
        if event.event_type == 'source_task_created']
    case.calls = []
    def run(claim, fresh):
        origin = case.conversations.task_turn_source(claim['turn_id'], 'local', member, case.task.id)
        case.calls.append((claim, origin))
        case.conversations.finish_turn(claim['turn_id'], 'completed', '本测试只核接续边界，没有运行模型或制作。')
    case.service = GoalContinuationService(case.conversations, verify_current_goal=lambda goal, event: {
        'status': 'ready', 'task_version': goal['target_snapshot']}, run_goal_turn=run)
    return case


def complete(case):
    case.conversations.finish_turn(case.turn['id'], 'completed', '原片任务已保存。',
        result={'kind': 'source_task', 'receipt': case.receipt})


def register(case):
    return case.service.register_source_task(case.turn['id'], 'local', case.actor.member_id,
        case.event, case.task, case.plan)


def bind(case, roots):
    for root in roots:
        case.authorizations.bind_business_context(case.task, case.actor,
            case.conversations.get_task_goal(root), memory={})


def human(case, message, context=None):
    member = case.actor.member_id
    chat = case.conversations.get(case.chat['id'], 'local', member)
    draft = case.conversations.save_draft(chat['id'], 'local', member, message, chat['draft_version'])
    turn, _ = case.conversations.create_turn_idempotent(chat['id'], 'local', member, message,
        str(uuid4()), draft['draft_version'], request_context=context or {'kind': 'home'})
    return turn


def test_source_adoption_continues_once_without_a_creation_request_or_fake_message(goal_case):
    case = goal_case
    complete(case)
    roots = register(case)
    assert len(roots) == 1 and register(case) == roots
    goal = case.conversations.get_task_goal(roots[0])
    assert goal['creation_adoption'] == {'kind': 'source_task', 'event_id': case.event.id,
        'request_id': case.receipt['request_id'], 'plan_id': case.plan.id,
        'input_fingerprint': case.event.payload['input_digest'], 'source': case.source, 'turn_id': case.turn['id']}
    assert 'window_id' not in goal['creation_adoption']
    event, = case.conversations.get_turn(roots[0])['events']
    assert event['source_task_event_id'] == case.event.id and 'window_id' not in event
    bind(case, roots)
    assert case.service.reconcile() == 1 and case.service.reconcile() == 0
    claim, origin = case.calls[0]
    assert claim['goal']['task_id'] == case.task.id and origin['raw_command'] == case.message
    assert claim['request_context']['stage'] == 'montage'
    with case.repo._connect() as connection:
        assert connection.execute('SELECT count(*) FROM creation_requests').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM creation_request_windows').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM creation_task_scripts').fetchone()[0] == 0
        messages = [row[0] for row in connection.execute('SELECT user_message FROM assistant_conversation_turns WHERE conversation_id=?', (case.chat['id'],)) if row[0]]
    assert messages == [case.message]


@pytest.mark.parametrize('changed', ['event_id', 'receipt', 'source', 'plan_adoption', 'source_media_pair'])
def test_source_goal_requires_the_actual_persisted_adoption(goal_case, changed):
    case = goal_case
    complete(case)
    event, plan = case.event, case.plan
    if changed == 'event_id':
        event = replace(event, id='invented-source-event')
    elif changed == 'plan_adoption':
        plan = replace(plan, adoption={**plan.adoption, 'version': 'invented-version'})
    else:
        payload = copy.deepcopy(event.payload)
        if changed == 'source_media_pair':
            payload['receipt']['source_media']['asset_id'] = 'invented-asset'
            plan = replace(plan, source_media=payload['receipt']['source_media'])
        elif changed == 'receipt':
            payload['receipt']['task_id'] = 'invented-task'
        else:
            payload['source']['message_digest'] = 'forged-input'
        event = replace(event, payload=payload)
    with pytest.raises(ValueError, match='采用回执'):
        case.service.register_source_task(case.turn['id'], 'local', case.actor.member_id, event, case.task, plan)
    assert case.conversations.goal_event_candidates() == []


def test_an_additional_consumed_home_input_cannot_authorize_the_old_source(goal_case):
    case = goal_case
    member = case.actor.member_id
    chat = case.conversations.get(case.chat['id'], 'local', member)
    draft = case.conversations.save_draft(chat['id'], 'local', member, '先停止刚才的制作。', chat['draft_version'])
    input_id = str(uuid4())
    case.conversations.enqueue_input(chat['id'], 'local', member, target_turn_id=case.turn['id'],
        client_request_id=input_id, message=draft['draft_text'], mode='steer', expected_draft_version=draft['draft_version'],
        expected_reference_version=0, request_context=case.home_context)
    case.conversations.transition_input(chat['id'], 'local', member, input_id, 'consumed')
    complete(case)
    assert register(case) == [] and case.conversations.goal_event_candidates() == []


def test_persisted_source_change_invalidates_the_registered_goal_before_a_round(goal_case):
    case = goal_case
    complete(case)
    roots = register(case)
    bind(case, roots)
    with case.repo._connect() as connection:
        connection.execute("UPDATE task_events SET payload_json=json_set(payload_json,'$.source.message_digest','changed') WHERE id=?", (case.event.id,))
    assert case.service.reconcile() == 0 and case.calls == []
    assert case.conversations.get_task_goal(roots[0])['status'] == 'invalidated'


@pytest.mark.parametrize('outside', [False, True])
def test_home_resume_keeps_original_source_and_scope_without_inventing_a_window(goal_case, outside):
    case = goal_case
    complete(case)
    roots = register(case)
    bind(case, roots)
    authorization = case.repo.task_authorization_records.get_task_authorization('local', case.task.id)
    context = {'kind': 'home', 'target_snapshot': {'material_selection': {
        'asset_id': 'other-source' if outside else case.asset.id}}}
    turn = human(case, '继续刚才的原片任务，先核对原请求回执。', context)
    result = case.service.resume_creation_request('local', case.actor.member_id, turn['id'], None,
        case.receipt['request_id'], case.authorizations)
    assert result['status'] == ('blocked' if outside else 'resumed')
    current = case.conversations.get_task_goal(roots[0])
    assert case.repo.task_authorization_records.get_task_authorization('local', case.task.id) == authorization
    if not outside:
        resumed = current['creation_resume']
        assert resumed['kind'] == 'source_task' and resumed['source_task_event_id'] == case.event.id
        assert 'window_id' not in resumed
        assert case.service.resume_creation_request('local', case.actor.member_id, turn['id'], None,
            case.receipt['request_id'], case.authorizations)['status'] == 'already_running'
        case.conversations.finish_turn(turn['id'], 'completed', '已沿原任务恢复。')
        with case.conversations._connect() as connection:
            root = case.conversations._goal_root(connection, roots[0])
            assert case.conversations._goal_origin_current(connection, root)
        assert current['revision'] == 1
    else:
        assert 'creation_resume' not in current
