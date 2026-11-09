"""An immutable current-timeline render scope within the original production Job."""
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
import re

from app.creation.contracts import TaskEvent


def _generation_id(job_id, head):
    return hashlib.sha256(json.dumps([job_id, head]).encode()).hexdigest()


def render_generation_event(job, task, start_event, actor):
    """Build the scope event; the caller commits it with reopen and first submission."""
    from app.creation_execution import CreationExecutionActor
    if (not task.current_timeline_version_id or (task.workspace_id, task.id) != (job.workspace_id, job.task_id)
            or (start_event.workspace_id, start_event.task_id, start_event.job_id, start_event.id) != (
                job.workspace_id, job.task_id, job.id, f'event_{job.id}_start')
            or CreationExecutionActor.from_event(job.workspace_id, start_event.payload) != actor
            or actor.workspace_id != job.workspace_id):
        raise PermissionError('重新输出范围与原作业或可信成员不一致')
    identity = _generation_id(job.id, task.current_timeline_version_id)
    return TaskEvent(f'event_{job.id}_render_generation_{identity}', job.workspace_id, job.task_id, job.id,
        'creation_render_generation', {'version':1, 'render_generation_id':identity,
            'input_timeline_version_id':task.current_timeline_version_id, 'job_input_digest':job.input_digest,
            'start_event_id':start_event.id, 'actor_identity':actor.identity_payload()})


def current_render_generation(repository, job, *, connection=None, require_current_head=True):
    """No scope means the published legacy namespace; a changed HEAD never creates a new scope."""
    with repository._connect() if connection is None else nullcontext(connection) as conn:
        return read_render_generation(conn, job, repository.execution_records, require_current_head=require_current_head)


def read_render_generation(conn, job, execution_records, *, require_current_head=True):
    from app.creation_execution import CreationExecutionActor
    from app.creation.task_input_reads import require_creation_task
    with nullcontext(conn):
        row = conn.execute("SELECT id FROM task_events WHERE workspace_id=? AND job_id=? "
            "AND event_type='creation_render_generation' ORDER BY rowid DESC LIMIT 1",
            (job.workspace_id, job.id)).fetchone()
        if row is None:
            return None
        event = execution_records.get_task_event(job.workspace_id, row['id'], connection=conn)
        start = execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn)
        task = require_creation_task(conn, job.workspace_id, job.task_id)
        version = conn.execute('SELECT task_id FROM timeline_versions WHERE workspace_id=? AND id=?',
            (job.workspace_id, event.payload['input_timeline_version_id'])).fetchone()
        if version is None or version['task_id'] != job.task_id:
            raise ValueError('重新输出的原时间线不属于当前任务')
        if not require_current_head:
            task = replace(task, current_timeline_version_id=event.payload['input_timeline_version_id'])
        actor = CreationExecutionActor.from_event(job.workspace_id, start.payload)
        expected = render_generation_event(job, task, start, actor)
        if (event.workspace_id, event.task_id, event.job_id, event.event_type, event.id, event.payload) != (
                expected.workspace_id, expected.task_id, expected.job_id, expected.event_type, expected.id, expected.payload):
            raise ValueError('本次重新输出的时间线、原身份或范围已变化')
        return event.payload


def render_namespace(base_id, generation):
    return base_id if generation is None else f'{base_id}_render_{generation["render_generation_id"]}'


def render_generation_fields(generation):
    if generation is None:
        return {}
    return {'render_generation_id':generation['render_generation_id'],
            'render_input_timeline_version_id':generation['input_timeline_version_id']}


def render_resource_scope(scope):
    """Validate declaration shape only; the creation owner separately proves authority."""
    if scope is None:
        return {}
    if (set(scope) != {'render_generation_id', 'render_input_timeline_version_id'}
            or not isinstance(scope['render_generation_id'], str)
            or re.fullmatch('[0-9a-f]{64}', scope['render_generation_id']) is None
            or not isinstance(scope['render_input_timeline_version_id'], str)
            or not scope['render_input_timeline_version_id']):
        raise ValueError('重新输出资源范围声明不完整')
    return dict(scope)


def stored_render_scope(payload):
    if 'render_generation_id' not in payload:
        return None
    return render_resource_scope({key:payload[key] for key in
        ('render_generation_id', 'render_input_timeline_version_id')})
