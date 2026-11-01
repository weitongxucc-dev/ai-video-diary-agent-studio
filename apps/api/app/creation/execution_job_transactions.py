"""Fixed-input creation Job registration, source binding and ungranted rejection."""
from __future__ import annotations

from collections.abc import Callable

from app.creation.contracts import TaskEvent as _TaskEvent
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore


class CreationExecutionJobTransactions:
    def __init__(self, *, connection_factory, job_records: JobRunRecordStore,
                 execution_records: ExecutionRecordStore, seal_creation_narration_source: Callable,
                 bind_execution_budget_scope: Callable) -> None:
        self._connect = connection_factory
        self.job_records = job_records
        self.execution_records = execution_records
        self.seal_creation_narration_source = seal_creation_narration_source
        self.bind_execution_budget_scope = bind_execution_budget_scope

    def create_creation_execution_job(self, run, *, start_payload, event_type="creation_execution_started",
                                      write_guard=None, execution_source=None, source_preparation=None, budget_scope_actor=None):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            current=self.job_records.create_job_run(run,connection=conn)
            event_id=f"event_{current.id}_start"
            try:
                event=self.execution_records.get_task_event(run.workspace_id,event_id,connection=conn)
            except KeyError:
                if source_preparation is not None:
                    source_id = self.seal_creation_narration_source(current, source_preparation,
                        input_digest=run.input_digest, origin_event_id=event_id, connection=conn)
                    start_payload = {**start_payload, 'source_binding_event_id': source_id}
                event=self.execution_records.append_task_event(_TaskEvent(event_id,run.workspace_id,run.task_id,current.id,
                    event_type,start_payload,created_at=current.created_at),connection=conn)
            if {key:value for key,value in event.payload.items() if key != 'source_binding_event_id'} != {
                    key:value for key,value in start_payload.items() if key != 'source_binding_event_id'}:
                raise PermissionError("已有制作任务的原输入与身份不同")
            policy = start_payload.get('execution_budget_policy')
            if policy is not None and policy.get('policy_kind') == 'scope':
                if budget_scope_actor is None:
                    raise PermissionError('授权范围绑定需要本次可信执行身份')
                self.bind_execution_budget_scope(run.workspace_id, current.id, budget_scope_actor,
                    scope_id=policy['scope_id'], expected_revision=policy['revision'], connection=conn)
            if execution_source is not None:
                self.bind_creation_execution_source(current,execution_source,connection=conn)
            return current

    def bind_creation_execution_source(self, job, source, *, connection=None, write_guard=None):
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.bind_creation_execution_source(job,source,connection=conn,write_guard=write_guard)
        if write_guard is not None:
            write_guard(connection)
        event_id=f"event_assistant_execution_locator_{source['assistant_turn_id']}"+(f"_{source['input_id']}" if source.get('input_id') else "")
        try:
            event=self.execution_records.get_task_event(job.workspace_id,event_id,connection=connection)
        except KeyError:
            return self.execution_records.append_task_event(_TaskEvent(event_id,job.workspace_id,job.task_id,job.id,
                "assistant_execution_located",{"job_id":job.id,"input_digest":job.input_digest,"source":source}),connection=connection)
        if event.job_id!=job.id or event.payload['source']!=source:
            raise ValueError("原助手输入已绑定不同制作作业")
        return event

    def reject_creation_execution_admission(self, workspace_id, job_id, request_id, input_digest, *,
                                           event, reason, impact, repair_action, connection):
        """Reject one ungranted fixed-input unit without pretending it executed."""
        if not connection.in_transaction:
            raise ValueError('准入拒绝须与原请求处于同一事务')
        row=connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?',(workspace_id,job_id)).fetchone()
        request=connection.execute('SELECT * FROM device_work_requests WHERE workspace_id=? AND id=?',
            (workspace_id,request_id)).fetchone()
        if (row is None or request is None or row['kind']!='creation_task_execution'
                or row['status'] not in {'queued','interrupted'} or row['input_digest']!=input_digest
                or request['owner_type']!='creation_job' or request['owner_id']!=job_id
                or request['input_digest']!=input_digest or request['state'] not in {'waiting','blocked'}
                or (event.workspace_id,event.task_id,event.job_id)!=(workspace_id,row['task_id'],job_id)
                or event.payload.get('device_request_id')!=request_id or event.payload.get('input_digest')!=input_digest
                or event.payload.get('unit_key')!=request['unit_key']):
            raise PermissionError('固定输入拒绝与原请求、作业或输入摘要不一致')
        if connection.execute("SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
                "AND owner_id=? AND state IN ('granted','yield_requested')",(workspace_id,job_id)).fetchone():
            raise PermissionError('原作业仍有执行许可，不能按未准入结束')
        self.execution_records.append_task_event(event,connection=connection)
        now=event.created_at.isoformat()
        connection.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,finished_at=?,updated_at=? "
            "WHERE workspace_id=? AND id=?",(reason,impact,repair_action,now,now,workspace_id,job_id))
        return self.job_records.job_run_from_row(connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?',
            (workspace_id,job_id)).fetchone())
