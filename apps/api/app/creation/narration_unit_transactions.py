"""Original narration-unit accounting, settlement and explicit job recovery."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import json
import math
import sqlite3

from app.creation.contracts import TaskEvent as _TaskEvent
from app.creation.execution_budget_scope import ExecutionBudgetScopes
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.creation.narration_segment_records import NarrationSegmentRecordStore, validate_narration_segment, narration_segment_values
from app.creation.section_inputs import SectionInputReader
from app.creation.task_input_reads import require_creation_task
from app.models import utc_now
from app.persistence_json import _json_payload


class NarrationUnitTransactions:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        execution_records: ExecutionRecordStore, job_records: JobRunRecordStore,
        narration_records: NarrationSegmentRecordStore, section_inputs: SectionInputReader,
        budget_scopes: ExecutionBudgetScopes, creation_execution_input: Callable,
        read_creation_execution_snapshot: Callable, fail_creation_task_stage: Callable,
    ) -> None:
        self._connect = connection_factory
        self.execution_records = execution_records
        self.job_records = job_records
        self.narration_records = narration_records
        self.section_inputs = section_inputs
        self.budget_scopes = budget_scopes
        self.creation_execution_input = creation_execution_input
        self.read_creation_execution_snapshot = read_creation_execution_snapshot
        self.fail_creation_task_stage = fail_creation_task_stage

    def creation_narration_usage(self, workspace_id, job_id, *, connection=None):
        """The budget checkpoint indexes receipts; only settlements own cumulative facts."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?',
                (workspace_id, f'event_{job_id}_narration_budget')).fetchone()
            budget = json.loads(row['payload_json']) if row is not None else None
            receipts = []
            for key in ('settled_event_id', 'active_event_id'):
                event_id = budget.get(key) if budget is not None else None
                receipt = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?',
                    (workspace_id, job_id, event_id)).fetchone() if event_id is not None else None
                if event_id is not None and receipt is None:
                    from app.device_scheduler import RecoveryBlocked
                    raise RecoveryBlocked('配音预算检查点的原消耗回执不存在，不能按零恢复')
                receipts.append(json.loads(receipt['payload_json']) if receipt is not None else None)
            scopes = self.budget_scopes
            try:
                binding = scopes.binding(conn, workspace_id, job_id)
            except KeyError:
                scope = None
            else:
                authorization = scopes.current(conn, workspace_id, binding.payload['scope_id'])
                scope = {'authorization': authorization.payload,
                    'usage': scopes.usage(conn, workspace_id, binding.payload['scope_id'])}
        settled, active = receipts
        return {'budget': budget, 'scope': scope, 'used_seconds': settled['total_seconds'] if settled is not None else 0,
                'used_output_bytes': settled['total_output_bytes'] if settled is not None else 0,
                'consumption_known': active is None, 'unsettled': [active] if active is not None else []}

    def start_creation_narration_unit(self, grant, resources, pending_segments, *, pending_segment_for=None):
        from app.device_scheduler import RecoveryBlocked
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            request = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            if (request is None or request['state'] != 'granted'
                    or request['last_grant_generation'] != grant.generation
                    or (request['workspace_id'], request['owner_id'], request['input_digest'], request['unit_key']) !=
                       (grant.workspace_id, grant.owner_id, grant.input_digest, grant.unit_key)
                    or json.loads(request['resources_json']) != resources):
                raise PermissionError('配音用量开始记录不属于当前许可')
            usage = self.creation_narration_usage(grant.workspace_id, grant.owner_id, connection=conn)
            if usage['unsettled']:
                raise RecoveryBlocked('原配音单元消耗尚未结算，须先核对原请求与进程回执')
            job = self.job_records.claim_job_run(grant.workspace_id, grant.owner_id, connection=conn)
            task = require_creation_task(conn, job.workspace_id, job.task_id)
            if (job.kind != 'creation_task_narration_edit' and task.stage != 'narration'
                    or task.confirmed_script_id != resources['script_id']):
                raise PermissionError('配音许可与当前文案阶段不一致')
            if usage['budget'] is None:
                budget = {key: resources[key] for key in ('parent_timeout_seconds', 'parent_max_output_bytes', 'script_id', 'input_contract')}
                if usage['scope'] is not None:
                    from app.creation.execution_budget import narration_limits
                    from types import SimpleNamespace
                    expected = narration_limits(usage, SimpleNamespace(
                        max_timeout_seconds=resources['timeout_seconds'], max_output_bytes=resources['max_output_bytes']))
                    if any(budget[key] != expected[key] for key in expected):
                        raise PermissionError('累计额度声明与可信任务策略不一致')
                    budget.update(policy_kind='scope', scope_id=usage['scope']['authorization']['scope_id'])
                elif budget['parent_timeout_seconds'] is None or budget['parent_max_output_bytes'] is None:
                    raise PermissionError('历史任务不能缺字段解除原封存预算')
                budget.update(settled_event_id=None, active_event_id=None)
                self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_narration_budget', workspace_id=job.workspace_id,
                    task_id=job.task_id, job_id=job.id, event_type='creation_narration_budget', payload=budget), connection=conn)
                usage['budget'] = budget
            budget = usage['budget']
            from app.creation.execution_budget import finite_unit_limit
            remaining_seconds = finite_unit_limit(resources['timeout_seconds'], None if budget['parent_timeout_seconds'] is None
                else budget['parent_timeout_seconds'] - usage['used_seconds'])
            remaining_bytes = finite_unit_limit(resources['max_output_bytes'], None if budget['parent_max_output_bytes'] is None
                else budget['parent_max_output_bytes'] - usage['used_output_bytes'])
            if remaining_seconds <= 0 or remaining_bytes <= 0:
                raise ValueError('原配音阶段累计额度已耗尽，不能重置额度重启')
            if job.kind == 'creation_task_narration_edit':
                pass  # Candidate generation does not replace applied segment facts.
            elif resources['narration_phase'] == 'merge':
                snapshot = self.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
                header = snapshot.script_header
                if snapshot.input_digest != grant.input_digest:
                    raise RecoveryBlocked('原PCM启动前固定文案或制作输入已变化')
                count = 0
                for _, segment in self.section_inputs.iter_creation_narration_sections(task, header, connection=conn):
                    if segment is None or segment.status != 'ready' or not segment.audio_reference:
                        raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
                    count += 1
                if count == 0 or count != header.sections_total_count:
                    raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
            elif pending_segment_for is not None:
                snapshot = self.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
                header = snapshot.script_header
                if snapshot.input_digest != grant.input_digest or header.selection_status != 'confirmed':
                    raise RecoveryBlocked('原单段启动前固定文案或制作输入已变化')
                count = conn.execute('SELECT COUNT(*) FROM narration_segments WHERE workspace_id=? AND task_id=? '
                    'AND script_id=?', (task.workspace_id, task.id, header.id)).fetchone()[0]
                if not header.sections_total_count or count not in (0, header.sections_total_count):
                    raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
                for section, segment in self.section_inputs.iter_creation_narration_sections(task, header, connection=conn):
                    if count:
                        if segment is None or segment.text != section.content:
                            raise RecoveryBlocked('原配音片段集合与采用正文不匹配，不能覆盖恢复')
                        validate_narration_segment(segment)
                        continue
                    segment = pending_segment_for(task, header, section, resources)
                    validate_narration_segment(segment)
                    if ((segment.workspace_id, segment.task_id, segment.script_id, segment.source_section_id, segment.text)
                            != (task.workspace_id, task.id, header.id, section.id, section.content)
                            or segment.status != 'pending'):
                        raise ValueError('初始配音分段必须属于当前唯一采用文案')
                    conn.execute('INSERT INTO narration_segments '
                        '(id,workspace_id,task_id,script_id,source_section_id,text,audio_reference,duration_seconds,sample_rate,'
                        'model_id,voice_profile_id,voice_version,language,pronunciation_version,status,created_at,updated_at,'
                        'source_kind,source_artifact_ref,source_sha256,source_script_digest) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', narration_segment_values(segment))
            else:
                current_segments = self.narration_records.list_narration_segments(task.workspace_id, task.id, task.confirmed_script_id, connection=conn)
                if not current_segments:
                    self.narration_records.save_narration_segments(task.workspace_id, task.id, task.confirmed_script_id, pending_segments, connection=conn)
                elif len(current_segments) != len(pending_segments):
                    raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
            if job.kind != 'creation_task_narration_edit':
                task.stage_status, task.status = 'running', 'running'
            task.stage_reason = task.stage_impact = task.stage_repair_action = task.stage_retryable = None
            task.updated_at = utc_now()
            conn.execute('UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?',
                (_json_payload(task), task.updated_at.isoformat(), task.workspace_id, task.id))
            payload = {'usage_id': f'{grant.request_id}:{grant.generation}', 'request_id': grant.request_id,
                'permit_generation': grant.generation, 'unit_key': grant.unit_key,
                'segment_id': resources.get('segment_id'), 'section_id': resources.get('section_id'),
                'ordinal': resources.get('ordinal'), 'phase': resources['narration_phase'],
                'remaining_seconds': remaining_seconds, 'remaining_output_bytes': remaining_bytes}
            start_id = f'event_{job.id}_narration_started_{grant.generation}'
            self.execution_records.append_task_event(_TaskEvent(id=start_id,
                workspace_id=job.workspace_id, task_id=job.task_id, job_id=job.id,
                event_type='creation_narration_unit_started', payload=payload), connection=conn)
            if usage['scope'] is not None:
                from app.creation_execution import CreationExecutionActor
                scopes = self.budget_scopes
                actor = CreationExecutionActor.from_event(job.workspace_id, self.creation_execution_input(
                    job.workspace_id, job.id, connection=conn))
                scopes.reserve(conn, job.workspace_id, job.id, actor, request_id=grant.request_id,
                    permit_generation=grant.generation, started_event_id=start_id,
                    receipt_id=f'event_{job.id}_narration_settled_{grant.generation}',
                    authorization_revision=usage['scope']['authorization']['revision'],
                    maximum={'execution_seconds': min(remaining_seconds, resources['timeout_seconds']),
                        'generated_bytes': min(remaining_bytes, resources['max_output_bytes'])})
            budget['active_event_id'] = start_id
            conn.execute('UPDATE task_events SET payload_json=? WHERE workspace_id=? AND id=?',
                (json.dumps(budget), job.workspace_id, f'event_{job.id}_narration_budget'))
            return job, remaining_seconds, remaining_bytes

    def previous_creation_narration_output(self, grant):
        with self._connect() as conn:
            row = conn.execute('SELECT permit_generation FROM device_request_events WHERE request_id=? '
                'AND permit_generation IS NOT NULL AND permit_generation<>? ORDER BY id DESC LIMIT 1',
                (grant.request_id, grant.generation)).fetchone()
            if row is None:
                return None
            receipt = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?',
                (grant.workspace_id, grant.owner_id, f"event_{grant.owner_id}_narration_settled_{row['permit_generation']}")).fetchone()
        return json.loads(receipt['payload_json']).get('output_file') if receipt is not None else None

    def settle_creation_narration_unit(self, grant, *, elapsed_seconds, output_bytes, connection, output_file=None):
        if (not connection.in_transaction or type(output_bytes) is not int or output_bytes < 0
                or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0):
            raise ValueError('配音消耗须以可核实的时间和字节数在许可事务内结算')
        usage_id = f'{grant.request_id}:{grant.generation}'
        start = connection.execute('SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?',
            (grant.workspace_id, grant.owner_id, f'event_{grant.owner_id}_narration_started_{grant.generation}')).fetchone()
        if start is None or json.loads(start['payload_json'])['usage_id'] != usage_id:
            raise ValueError('配音消耗缺少同一次许可的开始记录')
        event_id = f'event_{grant.owner_id}_narration_settled_{grant.generation}'
        previous = connection.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?',
            (grant.workspace_id, event_id)).fetchone()
        if previous is not None:
            recorded = json.loads(previous['payload_json'])
            if (recorded['elapsed_seconds'] != elapsed_seconds or recorded['output_bytes'] != output_bytes
                    or recorded.get('output_file') != output_file):
                raise ValueError('同一配音许可不能重复结算不同消耗')
            return event_id
        usage = self.creation_narration_usage(grant.workspace_id, grant.owner_id, connection=connection)
        if usage['budget']['active_event_id'] != f'event_{grant.owner_id}_narration_started_{grant.generation}':
            raise ValueError('只能结算原预算当前未完成的同一次配音许可')
        payload = {**json.loads(start['payload_json']), 'elapsed_seconds': elapsed_seconds, 'output_bytes': output_bytes,
            'output_file': output_file,
            'total_seconds': usage['used_seconds'] + elapsed_seconds,
            'total_output_bytes': usage['used_output_bytes'] + output_bytes}
        self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=grant.workspace_id,
            task_id=start['task_id'], job_id=grant.owner_id, event_type='creation_narration_unit_settled',
            payload=payload), connection=connection)
        if usage['scope'] is not None:
            self.budget_scopes.settle(connection, grant.workspace_id, grant.owner_id,
                request_id=grant.request_id, permit_generation=grant.generation, receipt_id=event_id)
        budget = {**usage['budget'], 'settled_event_id': event_id, 'active_event_id': None}
        connection.execute('UPDATE task_events SET payload_json=? WHERE workspace_id=? AND id=?',
            (json.dumps(budget), grant.workspace_id, f'event_{grant.owner_id}_narration_budget'))
        return event_id

    def exhaust_creation_narration_budget(self, grant, task_id, *, connection):
        reason = '原配音阶段累计额度已耗尽；已完成片段保留，未发布整体配音'
        now = utc_now()
        changed = connection.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,finished_at=?,updated_at=? "
            "WHERE workspace_id=? AND id=? AND task_id=? AND status='queued'",
            (reason, '已完成片段保留，不能重置父预算继续。', '核对原作业预算与消耗。', now.isoformat(), now.isoformat(),
             grant.workspace_id, grant.owner_id, task_id)).rowcount
        if changed != 1:
            raise PermissionError('原配音预算结束时作业状态已变化')
        self.fail_creation_task_stage(grant.workspace_id, task_id, stage='narration', reason=reason,
            impact='已完成片段保留，不能重置父预算继续。', repair_action='核对原作业预算与消耗。', retryable=False, connection=connection)
        self.execution_records.append_task_event(_TaskEvent(id=f'event_{grant.owner_id}_narration_exhausted', workspace_id=grant.workspace_id,
            task_id=task_id, job_id=grant.owner_id, event_type='creation_execution_stage',
            payload={'stage': 'narration', 'status': 'failed', 'reason': reason, 'retryable': False}), connection=connection)

    def resume_creation_narration_job(self, workspace_id, job_id, *, connection):
        usage = self.creation_narration_usage(workspace_id, job_id, connection=connection)
        if usage['budget'] is None or usage['unsettled']:
            raise ValueError('原配音消耗尚不可核实，不能重置作业继续')
        changed = connection.execute("UPDATE job_runs SET status='interrupted',failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=? AND kind='creation_task_execution' AND status IN ('failed','cancelled')",
            (utc_now().isoformat(), workspace_id, job_id)).rowcount
        if changed != 1:
            raise ValueError('原配音作业当前不可显式继续')
