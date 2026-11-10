"""Advance a read/execution fence only through proven changes of this goal."""
from app.assistant.task_business_errors import TaskBusinessRejected


def event_follows_owned_version(owner, goal, event):
    """An execution result may follow this same turn's already committed edit."""
    expected = goal['target_snapshot'].get('timeline_version_id')
    base = event.get('base_version_id')
    if base == expected:
        return True
    if base is None:
        return False
    try:
        version = owner.production.timeline_records.get_timeline_version(goal['workspace_id'], base)
    except KeyError:
        return False
    if version.task_id != goal['task_id']:
        return False
    chain = owner.selection_version_chain(goal, version)
    return chain is not None and expected in chain


def observation_turn_origin(production, conversations, task, member_id, turn_id, version):
    origin = conversations.task_turn_source(turn_id, task.workspace_id, member_id, task.id)
    snapshot = origin['context'].get('target_snapshot', {})
    current = {'timeline_version_id': task.current_timeline_version_id,
        'timeline_sequence': version.sequence if version else 0}
    expected = {key: snapshot.get(key) for key in current}
    if expected != current:
        goal = conversations.get_task_goal(turn_id)
        if goal is None or owned_version_transition(production, conversations, goal, expected, task) is None:
            raise ValueError('观察请求的助手轮次版本已失效')
    return origin


def observation_source_is_owned(production, conversations, task, source, expected, start):
    if (task.current_timeline_version_id is None or start is None
            or start.get('operation') != 'output_observation' or start.get('result_kind') != 'observation'):
        return False
    version = production.timeline_records.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
    if (source.get('expected_timeline_version_id'), source.get('expected_timeline_sequence')) != (version.id, version.sequence):
        return False
    goal = conversations.get_task_goal(source['assistant_turn_id'])
    if goal is None:
        return False
    from app.assistant_goal_continuation import GoalResultProjection
    return bool(GoalResultProjection(production, conversations).source_belongs_to_goal(goal, source)
        and owned_version_transition(production, conversations, goal, expected, task))


def owned_version_transition(production, conversations, goal, expected, task, *, connection=None):
    if expected is None or task.current_timeline_version_id is None:
        return None
    expected = {'timeline_version_id': expected.get('timeline_version_id'),
                'timeline_sequence': expected.get('timeline_sequence', 0)}
    version = production.timeline_records.get_timeline_version(task.workspace_id,
        task.current_timeline_version_id, connection=connection)
    current = {'timeline_version_id': version.id, 'timeline_sequence': version.sequence}
    if expected == current:
        return None
    from app.assistant_goal_continuation import GoalResultProjection
    chain = GoalResultProjection(production, conversations).selection_version_chain(goal, version)
    if chain is None or expected.get('timeline_version_id') not in chain:
        return None
    if expected['timeline_version_id'] is None:
        if expected['timeline_sequence'] != 0:
            return None
    else:
        basis = production.timeline_records.get_timeline_version(task.workspace_id,
            expected['timeline_version_id'], connection=connection)
        if basis.task_id != task.id or basis.sequence != expected.get('timeline_sequence'):
            return None
    return {'from': expected, 'to': current, 'scope': 'same_goal_owned_changes'}


def effective_goal_version(production, conversations, goal, expected, task, *, connection=None):
    version = production.timeline_records.get_timeline_version(task.workspace_id,
        task.current_timeline_version_id, connection=connection) if task.current_timeline_version_id else None
    current = {'timeline_version_id': task.current_timeline_version_id, 'timeline_sequence': version.sequence if version else 0}
    if expected is not None and expected != current:
        transition = owned_version_transition(production, conversations, goal, expected, task, connection=connection)
        if transition is None:
            raise TaskBusinessRejected('原目标的预期作品版本已变化', kind='conflict')
    return {**goal, 'target_snapshot': {**goal['target_snapshot'], **current}}
