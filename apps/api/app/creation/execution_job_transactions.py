"""Fixed-input creation Job registration, source binding and ungranted rejection."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import sqlite3
from contextlib import nullcontext

from app.creation.contracts import JobRun as _JobRun, TaskEvent as _TaskEvent
from app.creation.task_input_reads import require_creation_task
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.models import utc_now


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

    def storyboard_regeneration_job(self, workspace_id: str, task_id: str, request_id: str) -> _JobRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND idempotency_key=?",
                (workspace_id, task_id, f"storyboard-regeneration:{task_id}:{request_id}")).fetchone()
        return self.job_records.job_run_from_row(row) if row is not None else None

    def read_storyboard_regeneration_receipt(self, workspace_id: str, task_id: str, request_id: str):
        """Read job status and its committed result from the same SQLite snapshot."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND idempotency_key=?",
                (workspace_id, task_id, f"storyboard-regeneration:{task_id}:{request_id}")).fetchone()
            if row is None:
                raise KeyError(request_id)
            job = self.job_records.job_run_from_row(row)
            start = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id, f"event_{job.id}_start")).fetchone()
            if start is None:
                raise ValueError("故事板原请求缺少持久输入记录")
            result = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id, f"event_{job.id}_storyboard_result")).fetchone()
            return job, json.loads(start["payload_json"]), json.loads(result["payload_json"]) if result else None

    def claim_storyboard_regeneration(self, run: _JobRun, payload: dict, *, verify_inputs) -> tuple[_JobRun, bool]:
        """Bind one explicit request, its original actor and JobRun atomically."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND idempotency_key=?",
                (run.workspace_id, run.idempotency_key)).fetchone()
            if row is not None:
                saved = self.job_records.job_run_from_row(row)
                event = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                    (run.workspace_id, f"event_{saved.id}_start")).fetchone()
                if event is None:
                    raise ValueError("故事板原请求缺少持久输入记录")
                original = json.loads(event["payload_json"])
                if original.get("member_id") != payload.get("member_id") or any(
                    original.get(key) != payload.get(key) for key in ("identity_kind", "device_id", "user_id")
                ):
                    raise PermissionError("故事板原请求属于其他制作身份")
                if original.get("request_inputs") != payload.get("request_inputs"):
                    raise ValueError("故事板请求身份已用于不同输入")
                return saved, False
            task = require_creation_task(conn, run.workspace_id, run.task_id)
            if conn.execute("SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('queued','running','interrupted') LIMIT 1",
                    (run.workspace_id, run.task_id)).fetchone():
                raise ValueError("原制作安排尚未结束；先沿现有取消入口停止并回读终态，再重新生成故事板")
            if conn.execute("SELECT 1 FROM device_work_requests r JOIN job_runs j ON j.id=r.owner_id "
                    "AND j.workspace_id=r.workspace_id WHERE j.workspace_id=? AND j.task_id=? "
                    "AND r.owner_type='creation_job' AND r.state IN ('waiting','blocked','granted','yield_requested') LIMIT 1",
                    (run.workspace_id, run.task_id)).fetchone():
                raise ValueError("原制作单元尚未释放，不能重新生成故事板")
            verify_inputs(conn, task)
            saved = self.job_records.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{saved.id}_start", workspace_id=saved.workspace_id,
                task_id=saved.task_id, job_id=saved.id, event_type="storyboard_regeneration_requested",
                payload=payload, created_at=saved.created_at), connection=conn)
        return saved, True

    def creation_execution_input(self, workspace_id, job_id, *, connection=None):
        """Read effective inputs while keeping the original start audit immutable."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            job = self.job_records.get_job_run(workspace_id, job_id, connection=conn)
            start = self.execution_records.get_task_event(workspace_id, f'event_{job_id}_start', connection=conn)
            if (start.task_id, start.job_id) != (job.task_id, job.id):
                raise ValueError('原制作开始记录不属于当前任务')
            payload = start.payload
            if payload['input_digest'] != job.input_digest:
                row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND job_id=? "
                    "AND event_type='creation_execution_input_rebound' AND json_extract(payload_json,'$.input_digest')=? "
                    "ORDER BY rowid DESC LIMIT 1", (workspace_id, job.task_id, job.id, job.input_digest)).fetchone()
                if row is None or json.loads(row['payload_json'])['start_event_id'] != start.id:
                    from app.device_scheduler import RecoveryBlocked
                    raise RecoveryBlocked('原作业有效输入缺少声音改选回执，不能继续')
                rebound = json.loads(row['payload_json'])
                payload = {**payload, 'input_digest': job.input_digest,
                    'source_binding_event_id': rebound.get('source_binding_event_id')}
            render = conn.execute("SELECT id,task_id,job_id,payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_execution_stage' AND json_extract(payload_json,'$.execution_purpose')='render_current' "
                "ORDER BY rowid DESC LIMIT 1", (workspace_id, job_id)).fetchone()
            if render is not None:
                current = json.loads(render['payload_json'])
                head, unit = current.get('input_timeline_version_id'), current.get('unit_key')
                identity = hashlib.sha256(json.dumps([head, unit]).encode()).hexdigest()
                if (render['task_id'] != job.task_id or render['job_id'] != job.id
                        or render['id'] != f'event_{job_id}_render_requested_{identity}'
                        or current.get('stage') != 'render' or current.get('status') != 'pending'
                        or current.get('start_event_id') != start.id
                        or not isinstance(head, str) or not head or not isinstance(unit, str) or not unit.startswith('render:')
                        or current.get('script_id') != payload.get('script_id')
                        or current.get('input_digest') != job.input_digest
                        or any(current.get(key) != payload.get(key) for key in ('member_id','identity_kind','device_id','user_id'))):
                    raise ValueError('重新输出事实与原作业身份、输入或成片单元不一致')
                payload = {**payload, 'operation': 'render_current', 'render_request_id': render['id'],
                    'render_unit_key': unit, 'render_input_timeline_version_id': head}
            return payload

    def creation_narration_source_binding(self, workspace_id, job_id, *, connection=None):
        from app.device_scheduler import RecoveryBlocked
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            job = self.job_records.get_job_run(workspace_id, job_id, connection=conn)
            inputs = self.creation_execution_input(workspace_id, job_id, connection=conn)
            source_id = inputs.get('source_binding_event_id')
            if source_id is None:
                raise RecoveryBlocked('original_source_content_proof_missing: 原作业未封存来源字节，须实际重新选择声音')
            event = self.execution_records.get_task_event(workspace_id, source_id, connection=conn)
            payload = event.payload
            if (event.event_type != 'creation_narration_source_bound' or event.task_id != job.task_id
                    or payload.get('version') != 1 or payload.get('business_input_digest') != job.input_digest
                    or payload.get('workspace_id') != workspace_id or payload.get('task_id') != job.task_id):
                raise RecoveryBlocked('原作业来源封存引用不可核实')
            origin = self.execution_records.get_task_event(workspace_id, payload['origin_event_id'], connection=conn)
            if origin.job_id != event.job_id or origin.payload.get('source_binding_event_id') != source_id:
                raise RecoveryBlocked('原作业来源缺少同事务采用关联')
            return event

    def creation_narration_reference(self, workspace_id, job_id, *, connection):
        inputs = self.creation_execution_input(workspace_id, job_id, connection=connection)
        if not inputs.get('source_binding_event_id'):
            return None  # Legacy started work retains its existing physical fences.
        source = self.creation_narration_source_binding(workspace_id, job_id, connection=connection)
        row = connection.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
            "AND event_type='creation_narration_source_revalidated' "
            "AND json_extract(payload_json,'$.source_binding_event_id')=? ORDER BY rowid DESC LIMIT 1",
            (workspace_id, job_id, source.id)).fetchone()
        return json.loads(row['payload_json'])['narration_reference'] if row else source.payload['narration_reference']

    def requeue_ungranted_narration_job(self, workspace_id, job_id, request_id, *, connection):
        """Only the verified aggregate-to-unit contract migration may revive this Job."""
        row = connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?', (workspace_id, job_id)).fetchone()
        if (row is None or row['kind'] != 'creation_task_execution' or row['attempt'] != 0
                or row['started_at'] is not None or row['status'] not in {'queued', 'interrupted', 'failed'}
                or (row['status'] == 'failed' and not connection.execute(
                    "SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? AND event_type='creation_execution_stage' "
                    "AND json_extract(payload_json,'$.failure_kind')='input_budget_exceeded' "
                    "AND json_extract(payload_json,'$.reason_code')='declared_resources_exceed_budget' "
                    "AND json_extract(payload_json,'$.device_request_id')=? LIMIT 1",
                    (workspace_id, job_id, request_id)).fetchone())):
            raise PermissionError('只有原未授予的配音契约拒绝可在原作业继续')
        connection.execute("UPDATE job_runs SET status='queued',failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=?", (utc_now().isoformat(), workspace_id, job_id))

    def reopen_creation_execution_for_render(self, run, event, *, expected_timeline_version_id, connection):
        """Reopen this completed creation only for its current, fixed render input."""
        if not connection.in_transaction:
            raise ValueError('重新输出必须使用原作业的提交事务')
        row = connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?',
            (run.workspace_id, run.id)).fetchone()
        task = require_creation_task(connection, run.workspace_id, run.task_id)
        if (row is None or self.job_records.job_run_from_row(row) != run
                or run.kind != 'creation_task_execution' or run.status != 'succeeded'
                or not expected_timeline_version_id or task.current_timeline_version_id != expected_timeline_version_id
                or (event.workspace_id, event.task_id, event.job_id) != (run.workspace_id, run.task_id, run.id)
                or event.event_type != 'creation_execution_stage' or event.payload.get('stage') != 'render'
                or event.payload.get('status') != 'pending'
                or event.payload.get('input_timeline_version_id') != expected_timeline_version_id
                or event.payload.get('execution_purpose') != 'render_current'
                or event.payload.get('script_id') != task.confirmed_script_id
                or event.payload.get('input_digest') != run.input_digest
                or event.payload.get('start_event_id') != f'event_{run.id}_start'
                or not event.payload.get('unit_key', '').startswith('render:')):
            raise PermissionError('原完成作业或重新输出的时间线已变化')
        connection.execute("UPDATE job_runs SET status='queued',progress=66,failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=?",
            (event.created_at.isoformat(), run.workspace_id, run.id))
        self.execution_records.append_task_event(event, connection=connection)
        return self.job_records.get_job_run(run.workspace_id, run.id, connection=connection)

    def interrupt_creation_execution(
        self, event: _TaskEvent, *, impact: str, repair_action: str,
        task_is_current: Callable[[sqlite3.Connection], bool],
        expected_free_permit_generation: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Save one owner's interrupted job, current stage and receipt together."""
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.interrupt_creation_execution(event, impact=impact,
                    repair_action=repair_action, task_is_current=task_is_current,
                    expected_free_permit_generation=expected_free_permit_generation,
                    connection=conn)
            return
        if expected_free_permit_generation is not None:
            permit = connection.execute("SELECT state,generation FROM device_permit WHERE id=1").fetchone()
            if permit is None or (permit["state"], permit["generation"]) != ("free", expected_free_permit_generation):
                raise ValueError("设备许可在制作恢复前已变化，原作业保持待核查")
        row = connection.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?",
                                 (event.workspace_id, event.job_id)).fetchone()
        if row is None or row["task_id"] != event.task_id:
            raise ValueError("中断回执不属于当前制作作业")
        if row["status"] not in {"queued", "running", "interrupted"}:
            return  # A stopped dispatcher cannot overwrite a committed terminal result.
        reason = event.payload["reason"]
        now = event.created_at.isoformat()
        connection.execute(
            "UPDATE job_runs SET status='interrupted',failure_reason=?,impact=?,repair_action=?,"
            "finished_at=?,updated_at=? WHERE workspace_id=? AND id=?",
            (reason, impact, repair_action, now, now, event.workspace_id, event.job_id))
        stage = event.payload["stage"]
        task_stage = "narration" if stage == "narration" else "montage"
        if (row["kind"] == "creation_task_execution"
                and stage in {"narration", "person_render", "montage_plan", "render"}
                and task_is_current(connection)):
            task_row = connection.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                          (event.workspace_id, event.task_id)).fetchone()
            payload = json.loads(task_row["payload"])
            if payload["stage"] == task_stage and payload["stage_status"] == "running":
                payload.update(stage_status="not_started", stage_reason=reason,
                    stage_impact=impact, stage_repair_action=repair_action, stage_retryable=True,
                    recovery_point=task_stage, updated_at=now)
                connection.execute("UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
                    (json.dumps(payload, ensure_ascii=False), now, event.workspace_id, event.task_id))
        self.execution_records.append_task_event(event, connection=connection)
