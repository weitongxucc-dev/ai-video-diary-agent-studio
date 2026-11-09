"""Original segment revision request claims and persisted receipts."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from uuid import uuid4

from app.creation.contracts import JobRun as _JobRun, TaskEvent as _TaskEvent
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.creation.task_input_reads import require_creation_task


class SegmentRevisionTransactions:
    def __init__(self, *, connection_factory, job_records: JobRunRecordStore,
                 execution_records: ExecutionRecordStore, effective_script, stage_version,
                 seal_source, bind_scope) -> None:
        self._connect = connection_factory
        self.job_records = job_records
        self.execution_records = execution_records
        self._effective_script = effective_script
        self._stage_version = stage_version
        self._seal_source = seal_source
        self._bind_scope = bind_scope

    def find_segment_revision_request(self, workspace_id, task_id, member_id, request_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM task_events WHERE workspace_id=? AND task_id=? AND event_type='segment_revision_requested' "
                "AND json_extract(payload_json,'$.member_id')=? AND json_extract(payload_json,'$.command.request_id')=? LIMIT 1",
                (workspace_id, task_id, member_id, request_id)).fetchone()
        return self.execution_records.task_event_from_row(row) if row is not None else None

    def create_segment_revision_job(self, task, actor, command, rules_check, prepared, *, write_guard=None, budget_policy=None):
        encoded = json.dumps(command, ensure_ascii=False, sort_keys=True, default=str)
        input_digest = hashlib.sha256(encoded.encode()).hexdigest()
        run = _JobRun(id=f'job_{uuid4().hex}', workspace_id=task.workspace_id, task_id=task.id,
            kind='creation_task_segment_revision', input_digest=input_digest,
            idempotency_key=f'segment-revision:{task.id}:{actor.member_id}:{command["request_id"]}')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            current = require_creation_task(conn, task.workspace_id, task.id)
            if current.owner_user_id != (actor.user_id or actor.member_id):
                raise PermissionError('局部修改的任务归属已变化')
            script = self._effective_script(task.workspace_id, task.id, connection=conn)
            section = next((item for item in script.sections if item.id == command['section_id']), None)
            if (section is None or script.id != command['script_id']
                    or self._stage_version(task.workspace_id, task.id, section.id, connection=conn) != command['expected_segment_version']
                    or hashlib.sha256(section.content.encode()).hexdigest() != command['source_text_sha256']):
                raise ValueError('提交前段版本已变化，原采用成果保持不变')
            job = self.job_records.create_job_run(run, connection=conn)
            if job.id != run.id:
                return job  # The concurrent original commit owns its baseline.
            source_id = self._seal_source(job, prepared, input_digest=input_digest,
                origin_event_id=f'event_{job.id}_start', connection=conn)
            payload = {**actor.identity_payload(), 'command': command, 'rules_check': rules_check,
                'input_digest': input_digest, 'script_id': command['script_id'], 'plan_id': current.plan_id, 'source_binding_event_id': source_id,
                **({'execution_budget_policy': budget_policy} if budget_policy is not None else {})}
            self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_start', workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type='segment_revision_requested', payload=payload,
                created_at=job.created_at), connection=conn)
            if budget_policy is not None and budget_policy.get('policy_kind') == 'scope':
                self._bind_scope(job.workspace_id, job.id, actor, scope_id=budget_policy['scope_id'],
                    expected_revision=budget_policy['revision'], connection=conn)
        return job

    def list_segment_revision_requests(self, workspace_id, task_id, member_id, *, limit=30, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            args = (workspace_id, task_id, member_id)
            where = "workspace_id=? AND task_id=? AND event_type='segment_revision_requested' AND json_extract(payload_json,'$.member_id')=?"
            count = conn.execute('SELECT count(*) FROM task_events WHERE '+where, args).fetchone()[0]
            rows = conn.execute('SELECT * FROM task_events WHERE '+where+' ORDER BY created_at DESC LIMIT ?', (*args,limit)).fetchall()
            values = []
            for row in rows:
                event = self.execution_records.task_event_from_row(row)
                job = self.job_records.get_job_run(workspace_id, event.job_id, connection=conn)
                result = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type='segment_publication' ORDER BY created_at DESC LIMIT 1", (workspace_id,job.id)).fetchone()
                waiting = conn.execute("SELECT wait_reason,last_failure_reason FROM device_work_requests WHERE workspace_id=? "
                    "AND owner_type='creation_job' AND owner_id=? ORDER BY created_at DESC,id DESC LIMIT 1",(workspace_id,job.id)).fetchone()
                values.append({'request_id':event.payload['command']['request_id'], 'input_id':event.payload['command']['input_id'],
                    'section_id':event.payload['command']['section_id'], 'job_id':job.id, 'status':job.status,
                    'result':json.loads(result[0]) if result else None,
                    'waiting_reason':job.failure_reason or (waiting['wait_reason'] or waiting['last_failure_reason'] if waiting else 'queue_submission_pending')})
        return {'items':values,'total_count':count,'truncated':len(values)<count}

    def segment_revision_result(self, workspace_id, job_id):
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='segment_publication' ORDER BY created_at DESC LIMIT 1", (workspace_id, job_id)).fetchone()
        return json.loads(row['payload_json']) if row is not None else None
