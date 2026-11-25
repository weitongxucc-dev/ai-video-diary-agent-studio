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
        version = conn.execute('SELECT task_id FROM timeline_versions WHERE workspace_id=? AND id=? AND candidate_id IS NULL',
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
    if generation is None:
        return base_id
    # Timeline and subtitle identities cross the existing 120-character save
    # and edit contracts. Hash the complete pair, retaining a readable prefix.
    identity = hashlib.sha256(json.dumps([base_id, generation['render_generation_id']]).encode()).hexdigest()
    return f'{base_id[:48]}_render_{identity}'


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


def adopted_render(repo, job, version_id, *, connection):
    """Locate an immutable adopted scope by result version, not by latest generation."""
    from app.creation_execution import CreationExecutionActor
    version = repo.timeline_records.get_timeline_version(job.workspace_id, version_id, connection=connection)
    facts = version.snapshot['audio_delivery']
    if (version.task_id != job.task_id or facts['job_id'] != job.id
            or facts['render_input']['timeline_version_id'] != version.parent_version_id
            or facts['origin_timeline_version_id'] != version.id or version.created_by_operation_id is not None):
        raise ValueError('历史交付版本不是原Job的采用结果')
    scope = stored_render_scope(facts)
    if scope is not None:
        parent = repo.timeline_records.get_timeline_version(job.workspace_id, version.parent_version_id, connection=connection)
        start = repo.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=connection)
        task = repo.task_records.get_creation_task(job.workspace_id, job.task_id, connection=connection)
        expected = render_generation_event(job, replace(task, current_timeline_version_id=parent.id), start,
            CreationExecutionActor.from_event(job.workspace_id, start.payload))
        if (parent.task_id != job.task_id or scope != render_generation_fields(expected.payload)
                or not _same_generation_event(repo, job, expected, connection)):
            raise ValueError('历史交付未绑定原输入时间线与代')
    event = repo.execution_records.get_task_event(job.workspace_id,
        render_namespace(f'event_{job.id}_delivery_adopted', scope), connection=connection)
    transform = repo.lineage_records.get_transform_run(job.workspace_id, version.snapshot['transform_run_id'], connection=connection)
    if (version.id != render_namespace(f'timeline_delivery_{job.id}', scope)
            or (transform.task_id, transform.job_id, transform.transform_type, transform.status) !=
                (job.task_id, job.id, 'montage_delivery', 'succeeded')
            or transform.input_digest != job.input_digest or transform.output_refs != version.snapshot['output_refs']
            or transform.parameters['delivery_manifest_digest'] != facts['manifest_digest']
            or stored_render_scope(transform.parameters) != scope
            or (event.task_id, event.job_id, event.event_type) != (job.task_id, job.id, 'render_succeeded')
            or event.payload != {'timeline_version_id': version.id, 'output_refs': version.snapshot['output_refs'],
                **(scope or {}), 'request_id': facts['request_id'], 'permit_generation': facts['permit_generation'],
                'delivery_manifest_digest': facts['manifest_digest']}):
        raise ValueError('历史交付缺少原采用回执')
    return version, facts, scope


def _same_generation_event(repo, job, expected, connection):
    observed = repo.execution_records.get_task_event(job.workspace_id, expected.id, connection=connection)
    return (observed.workspace_id, observed.task_id, observed.job_id, observed.event_type, observed.payload) == (
        expected.workspace_id, expected.task_id, expected.job_id, expected.event_type, expected.payload)


def latest_adopted_render(repo, job, *, connection):
    row = connection.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND job_id=? "
        "AND event_type='render_succeeded' ORDER BY rowid DESC LIMIT 1",
        (job.workspace_id, job.task_id, job.id)).fetchone()
    if row is not None:
        return adopted_render(repo, job, json.loads(row['payload_json'])['timeline_version_id'], connection=connection)


def adopted_manifest(repo, job, adoption, *, field, suffix, event_type, connection):
    _version, facts, scope = adoption
    event_id = facts[field]
    if event_id != render_namespace(f'event_{job.id}_{suffix}', scope):
        raise ValueError('历史清单身份不是原采用命名空间')
    event = repo.execution_records.get_task_event(job.workspace_id, event_id, connection=connection)
    from app.task_audio import media_digest
    payload = event.payload
    if ((event.task_id, event.job_id, event.event_type) != (job.task_id, job.id, event_type)
            or payload['job_id'] != job.id or payload['task_id'] != job.task_id
            or payload['input_digest'] != job.input_digest or stored_render_scope(payload) != scope
            or payload['digest'] != media_digest({k: v for k, v in payload.items() if k != 'digest'})):
        raise ValueError('历史清单内容或原范围不一致')
    return payload


def settled_render_producer(repo, job, *, transform, scope, kind, unit_key, measured_suffix, connection,
                            completing_in_transaction=False):
    """Historical reuse is never the live same-transaction granted/failed exception."""
    facts = transform.parameters.get('picture_fragment', transform.parameters)
    request_id, generation = facts['producer_request_id'], facts['producer_generation']
    start = repo.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=connection)
    row = connection.execute("SELECT resources_json FROM device_work_requests WHERE id=? AND workspace_id=? "
        "AND owner_type='creation_job' AND owner_id=? AND input_digest=? AND member_id=? "
        "AND unit_key=? AND last_grant_generation=? AND "
        "((state='done' AND checkpoint_ref IS NOT NULL) OR (state='granted' AND ?))",
        (request_id, job.workspace_id, job.id, job.input_digest, start.payload['member_id'], unit_key, generation,
         int(kind == 'creation_subtitle_sidecar' and completing_in_transaction and connection.in_transaction))).fetchone()
    if row is None:
        raise ValueError('历史父产物缺少明确完成的原设备请求')
    resources = json.loads(row['resources_json'])
    receipt = connection.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
        "AND job_id=? AND event_type='task_execution_unit_settled' AND json_extract(payload_json,'$.usage_id')=?",
        (job.workspace_id, job.task_id, job.id, f'{request_id}:{generation}')).fetchall()
    measured = repo.execution_records.get_task_event(job.workspace_id,
        f'event_{request_id}_{generation}_{measured_suffix}', connection=connection)
    if len(receipt) != 1:
        raise ValueError('历史父产物没有唯一原结算')
    receipt = json.loads(receipt[0]['payload_json'])
    if (resources['kind'] != kind or stored_render_scope(resources) != scope
            or stored_render_scope(transform.parameters) != scope
            or (receipt['request_id'], receipt['permit_generation'], receipt['unit_key']) != (request_id, generation, unit_key)
            or (measured.job_id, measured.task_id, measured.event_type) != (job.id, job.task_id,
                'picture_fragment_output_measured' if measured_suffix == 'picture_fragment_output' else 'audio_delivery_output_measured')
            or (measured.payload['request_id'], measured.payload['permit_generation']) != (request_id, generation)
            or measured.payload['consumption_known'] is not True
            or type(receipt['output_bytes']) is not int or receipt['output_bytes'] <= 0
            or type(receipt['elapsed_seconds']) not in {int, float} or not 0 <= receipt['elapsed_seconds'] < float('inf')
            or receipt['output_bytes'] != measured.payload['output_bytes']):
        raise ValueError('历史父产物原许可或真实计量不一致')
    return resources
