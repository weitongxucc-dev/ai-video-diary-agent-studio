"""Reopen a settled production for an explicitly requested current-timeline output."""
import hashlib
import json

from app.creation.contracts import TaskEvent
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent


def rerender_current(execution, job, actor, scheduler, *, write_guard=None):
    from app.creation_execution import CreationExecutionActor, DEVICE_OWNER_TYPE, KIND
    if job.kind != KIND or job.status not in {'succeeded', 'failed'}:
        return False
    repository = execution.repository

    def facts(conn):
        current = repository.job_records.get_job_run(job.workspace_id, job.id, connection=conn)
        start = repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn)
        snapshot = execution.validate_input_snapshot(job.workspace_id, job.task_id,
            start.payload['script_id'], job.input_digest, connection=conn)
        return current, snapshot.task, start.payload, snapshot.plan

    owner = scheduler.owners
    if hasattr(owner, 'registrations'):
        owner = owner.registrations[DEVICE_OWNER_TYPE].owner
    with repository.execution_inputs.creation_execution_read() as reader:
        current, task, start, _plan = facts(reader)
    if current != job or actor != CreationExecutionActor.from_event(job.workspace_id, start):
        raise PermissionError('不能重新输出其他身份的原作业')
    if execution.render_is_valid(job.workspace_id, job.task_id):
        return False
    if repository.narration_units.creation_narration_usage(job.workspace_id, job.id)['unsettled']:
        raise RecoveryBlocked('原配音消耗尚未核实，不能重开输出')
    version_id = task.current_timeline_version_id
    if not version_id:
        raise ValueError('当前作品尚不能仅重新输出；配音或剪辑仍未就绪')
    progressive = owner.picture_fragments is not None and not _plan.presenter_share_target
    if job.status == 'failed':
        from app.creation.render_generation import current_render_generation
        previous = current_render_generation(repository, job, require_current_head=False)
        if (not progressive or previous is None
                or previous['input_timeline_version_id'] == version_id):
            return False
    if not progressive:
        with repository._connect() as reader:
            key, _resources = owner.render_facts(reader, task, _plan, actor=actor)
    with repository._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if write_guard is not None:
            write_guard(conn)
        generation = None
        current = job
        if progressive:
            from app.creation.render_generation import render_generation_event
            current, target, original, current_plan = facts(conn)
            if current != job or target.current_timeline_version_id != version_id:
                raise PermissionError('原完成作业或重新输出的时间线已变化')
            start_event = repository.execution_records.get_task_event(job.workspace_id,
                f'event_{job.id}_start', connection=conn)
            scope = render_generation_event(job, target, start_event, actor)
            current = repository.creation_execution_jobs.begin_creation_render_generation(job, scope,
                expected_timeline_version_id=version_id, connection=conn)
            generation = scope.payload
            key, _resources = owner.render_facts(conn, target, current_plan, actor=actor, job=current,
                prepare_assembly=True)
        from app.creation.render_generation import render_generation_fields
        intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id, job.input_digest, key, SchedulePolicy())
        identity = hashlib.sha256(json.dumps([version_id, intent.unit_key]).encode()).hexdigest()
        event = TaskEvent(id=f'event_{job.id}_render_requested_{identity}', workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type='creation_execution_stage',
            payload={'stage': 'render', 'status': 'pending', 'input_timeline_version_id': version_id,
                'unit_key': intent.unit_key, 'input_digest': job.input_digest,
                'execution_purpose': 'render_current', 'script_id': task.confirmed_script_id,
                'start_event_id': f'event_{job.id}_start', **actor.identity_payload(),
                **render_generation_fields(generation)})
        repository.creation_execution_jobs.reopen_creation_execution_for_render(current, event,
            expected_timeline_version_id=version_id, connection=conn)
        owner.verify_submission(intent, actor, connection=conn)
    return True
