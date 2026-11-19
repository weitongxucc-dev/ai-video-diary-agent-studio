"""Reopen a settled production for an explicitly requested current-timeline output."""
from datetime import datetime
import hashlib
import json
import re

from app.creation.contracts import TaskEvent
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent


def _pending_render_event(job, actor, version_id, script_id, unit_key, generation=None, **lineage):
    from app.creation.render_generation import render_generation_fields
    identity = hashlib.sha256(json.dumps([version_id, unit_key]).encode()).hexdigest()
    return TaskEvent(id=f'event_{job.id}_render_requested_{identity}', workspace_id=job.workspace_id,
        task_id=job.task_id, job_id=job.id, event_type='creation_execution_stage',
        payload={'stage': 'render', 'status': 'pending', 'input_timeline_version_id': version_id, 'script_id': script_id,
            'unit_key': unit_key, 'input_digest': job.input_digest, 'execution_purpose': 'render_current',
            'start_event_id': f'event_{job.id}_start', **actor.identity_payload(),
            **render_generation_fields(generation), **lineage})


def _settled_presenter_attempt(repository, render_stage, conn, task, request_row):
    from app.creation.render_stage_use_case import render_input_digest_for, render_input_for_version
    from app.montage import creation_task_montage_plan_from_payload

    if not task.current_timeline_version_id:
        raise RecoveryBlocked('原人物成片缺少已确认时间线')
    version = repository.timeline_records.get_timeline_version(task.workspace_id,
        task.current_timeline_version_id, connection=conn)
    montage = creation_task_montage_plan_from_payload(
        repository.get_creation_task_montage_plan(task.workspace_id, task.id, connection=conn))
    if (montage.status != 'failed' or montage.output_path or montage.subtitle_path
            or not any(clip.person_render_id for clip in montage.clips)):
        raise RecoveryBlocked('原人物成片没有明确失败的已确认故事板')
    tracks = render_stage.metadata.current_subtitle_tracks(
        task.workspace_id, task, current_version=version, montage=montage, connection=conn)
    digest = render_input_digest_for(render_input_for_version(montage, version, tracks, bind_timeline_version=True))
    inner = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? "
        "AND kind='creation_task_montage_render' AND input_digest=? ORDER BY created_at DESC,id DESC LIMIT 1",
        (task.workspace_id, task.id, digest)).fetchone()
    if inner is None or inner['status'] != 'failed' or not inner['finished_at']:
        raise RecoveryBlocked('原人物内层渲染未明确失败，不能启动新尝试')
    if not (datetime.fromisoformat(request_row['created_at']) <= datetime.fromisoformat(inner['created_at'])
            <= datetime.fromisoformat(inner['finished_at']) <= datetime.fromisoformat(request_row['updated_at'])):
        raise RecoveryBlocked('失败的内层渲染不属于原设备尝试')
    transform = repository.lineage_records.get_transform_run(task.workspace_id, f"transform_{inner['id']}", connection=conn)
    if (transform.task_id != task.id or transform.job_id != inner['id']
            or transform.transform_type != 'montage_render' or transform.input_digest != digest
            or transform.status != 'failed' or transform.output_refs or json.loads(inner['output_refs_json'])
            or conn.execute("SELECT 1 FROM artifact_records WHERE workspace_id=? AND producer_job_id=? LIMIT 1",
                (task.workspace_id, inner['id'])).fetchone() is not None):
        raise RecoveryBlocked('原人物渲染产物或结算不明确，不能重复制作')
    return version.id, inner['id'], inner['failure_reason']


def continue_failed_presenter_render(execution, render_stage, job, actor, scheduler, *, write_guard=None):
    from app.creation_execution import CreationExecutionActor, DEVICE_OWNER_TYPE, KIND

    if job is None or job.kind != KIND or job.status != 'failed':
        return None
    repository = execution.repository
    owner = scheduler.owners
    if hasattr(owner, 'registrations'):
        owner = owner.registrations[DEVICE_OWNER_TYPE].owner

    def observe(conn):
        row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
            "AND event_type='creation_execution_stage' ORDER BY rowid DESC LIMIT 1",
            (job.workspace_id, job.id)).fetchone()
        stage = json.loads(row['payload_json']) if row else {}
        if (stage.get('stage'), stage.get('status')) != ('render', 'failed'):
            return None
        start = repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn)
        snapshot = execution.validate_input_snapshot(job.workspace_id, job.task_id,
            start.payload['script_id'], job.input_digest, connection=conn)
        if not snapshot.plan.presenter_share_target:
            return None
        current = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND kind=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1", (job.workspace_id, job.task_id, KIND)).fetchone()
        if (current is None or repository.job_records.job_run_from_row(current) != job
                or actor != CreationExecutionActor.from_event(job.workspace_id, start.payload)
                or snapshot.task.owner_user_id != (actor.user_id or actor.member_id)):
            raise PermissionError('只能继续原身份、原输入的失败人物成片')
        if (job.output_refs or snapshot.task.output_ready
                or repository.narration_units.creation_narration_usage(
                    job.workspace_id, job.id, connection=conn)['unsettled']
                or conn.execute("SELECT 1 FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
                    "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e "
                    "WHERE e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
                    "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",
                    (job.workspace_id, job.id)).fetchone() is not None
                or conn.execute("SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? "
                    "AND kind='creation_task_montage_render' AND status IN ('queued','running','interrupted') LIMIT 1",
                    (job.workspace_id, job.task_id)).fetchone() is not None):
            raise RecoveryBlocked('原人物成片已有产物或仍有未知执行，不能开启重试')
        prior = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? AND id=? "
            "AND owner_type='creation_job' AND owner_id=? AND input_digest=? AND member_id=? "
            "AND state='failed' AND last_failure_reason='creation_render_failed' AND checkpoint_ref IS NULL",
            (job.workspace_id, stage.get('request_id'), job.id, job.input_digest, actor.member_id)).fetchone()
        if (prior is None or prior['unit_key'] != stage.get('unit_key')
                or not re.fullmatch(r'render:[0-9a-f]{64}(?::retry:[0-9a-f]{32})?', prior['unit_key'])
                or json.loads(prior['resources_json']).get('kind') != 'creation_presenter_render'
                or conn.execute("SELECT 1 FROM device_request_events WHERE request_id=? AND event_type='failed' "
                    "AND reason='creation_render_failed' AND permit_generation=? AND attempt_number=?",
                    (prior['id'], prior['last_grant_generation'], prior['attempt_number'])).fetchone() is None):
            raise RecoveryBlocked('原人物成片缺少明确释放的失败许可')
        try:
            version_id, inner_id, failure_reason = _settled_presenter_attempt(repository, render_stage, conn, snapshot.task, prior)
            failed = scheduler._request(prior)
            unit_key, resources = owner.render_facts(conn, snapshot.task, snapshot.plan, actor=actor, retry_from=failed)
            if failure_reason in {
                    'Command exceeded its unit deadline', 'Child session did not start before deadline',
                    '渲染阶段已超出总时限', '制作阶段总时限已到'}:
                previous_total, current_total = failed.resources['timeout_seconds'], resources['timeout_seconds']
                previous_mix = failed.resources.get('audio_mix', {}).get('timeout_seconds', 0)
                current_mix = resources.get('audio_mix', {}).get('timeout_seconds', 0)
                if (current_total <= previous_total
                        and resources.get('picture_timeout_seconds', current_total)
                            <= failed.resources.get('picture_timeout_seconds', previous_total)
                        and min(current_total, current_mix) <= min(previous_total, previous_mix)):
                    raise RecoveryBlocked('原人物成片已耗尽许可时限，请先重新准备覆盖当前作品的 CPU 资源再继续')
        except KeyError as exc:
            raise RecoveryBlocked('原人物成片缺少完整结算记录') from exc
        except OSError as exc:
            raise RecoveryBlocked('原人物成片来源无法核验') from exc
        return failed, version_id, inner_id, unit_key, start.payload['script_id']

    with repository.execution_inputs.creation_execution_read() as reader:
        original = observe(reader)
    if original is None:
        return None
    failed, version_id, inner_id, unit_key, script_id = original
    intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id, job.input_digest, unit_key, failed.policy)

    def write(conn, predecessor, new_request_id):
        if predecessor != failed or observe(conn) != original:
            raise RecoveryBlocked('原人物成片失败、来源或资源已变化')
        repository.job_records.reopen_failed_job_run(job.workspace_id, job.id, expected_updated_at=job.updated_at,
            expected_attempt=job.attempt, connection=conn)
        event = _pending_render_event(job, actor, version_id, script_id, unit_key,
            previous_request_id=predecessor.id, previous_inner_job_id=inner_id, new_request_id=new_request_id)
        repository.execution_records.append_task_event(event, connection=conn)
        return owner.verify_submission(intent, actor, connection=conn)

    return scheduler.continue_failed_unit(failed.id, intent, actor,
        expected_failed_generation=failed.last_grant_generation, expected_free_generation=scheduler.reconcile_released(),
        owner_database_path=repository.database_path, owner_write=write, write_guard=write_guard)


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
        intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id, job.input_digest, key, SchedulePolicy())
        event = _pending_render_event(job, actor, version_id, task.confirmed_script_id, intent.unit_key, generation)
        repository.creation_execution_jobs.reopen_creation_execution_for_render(current, event,
            expected_timeline_version_id=version_id, connection=conn)
        owner.verify_submission(intent, actor, connection=conn)
    return True
