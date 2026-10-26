"""Shared authorization scopes reference original execution receipts, never copy usage."""
from __future__ import annotations

import hashlib
import json
import math

from app.creation.contracts import TaskEvent


class BudgetScopeConflict(ValueError):
    pass


class BudgetScopeExhausted(ValueError):
    pass


class ExecutionBudgetScopes:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def install_indexes(connection):
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS task_events_execution_budget_receipt "
            "ON task_events(workspace_id, json_extract(payload_json,'$.receipt_id')) "
            "WHERE event_type='execution_budget_scope_reserved'")
        connection.execute("CREATE INDEX IF NOT EXISTS task_events_execution_budget_scope "
            "ON task_events(workspace_id, event_type, json_extract(payload_json,'$.scope_id'))")

    @staticmethod
    def _transaction(connection):
        if not connection.in_transaction:
            raise ValueError('预算授权、绑定和预留必须在所属写事务中执行')

    def _owner(self, connection, workspace_id, task_id, actor):
        task = self.repository._require_creation_task(connection, workspace_id, task_id)
        if actor.workspace_id != workspace_id or task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError('累计授权范围不属于当前成员')
        return task

    @staticmethod
    def _id(kind, workspace_id, key):
        digest = hashlib.sha256(json.dumps([workspace_id, key], sort_keys=True).encode()).hexdigest()
        return f'event_budget_scope_{kind}_{digest}'

    def _append(self, connection, event_id, workspace_id, task_id, job_id, event_type, payload):
        try:
            existing = self.repository.get_task_event(workspace_id, event_id, connection=connection)
        except KeyError:
            return self.repository.append_task_event(TaskEvent(event_id, workspace_id, task_id, job_id,
                event_type, payload), connection=connection)
        if (existing.task_id, existing.job_id, existing.event_type, existing.payload) != (task_id, job_id, event_type, payload):
            raise BudgetScopeConflict('原累计授权请求已绑定不同内容')
        return existing

    def current(self, connection, workspace_id, scope_id):
        row = connection.execute("SELECT * FROM task_events WHERE workspace_id=? "
            "AND event_type='execution_budget_scope_authorized' AND json_extract(payload_json,'$.scope_id')=? "
            "ORDER BY CAST(json_extract(payload_json,'$.revision') AS INTEGER) DESC LIMIT 1",
            (workspace_id, scope_id)).fetchone()
        if row is None:
            raise KeyError(scope_id)
        return self.repository._task_event_from_row(row)

    def authorize(self, connection, workspace_id, origin_task_id, actor, *, scope_id, expected_revision,
                  request_id, source_ref, target_task_ids, limits, policy_kind, authorization_guard, revoked=False):
        self._transaction(connection)
        self._owner(connection, workspace_id, origin_task_id, actor)
        if not scope_id or not request_id or not source_ref:
            raise ValueError('累计授权需要范围、原请求和可核验授权来源')
        if authorization_guard is None:
            raise PermissionError('累计授权需要可信入口在同一事务核验授权来源与本地执行资格')
        authorization_guard(connection)
        if policy_kind not in {'local_goal', 'explicit_total'} or set(limits) != {'execution_seconds', 'generated_bytes'}:
            raise ValueError('当前累计授权仅支持本地秒数与生成字节，不支持无账据的币种成本')
        for key, value in limits.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0 or key == 'generated_bytes' and not isinstance(value, int)):
                raise ValueError('显式总额必须为有限正额度，未设置须使用null')
        if policy_kind == 'explicit_total' and all(value is None for value in limits.values()):
            raise ValueError('明确累计总限至少需要一项有限额度')
        if policy_kind == 'local_goal' and any(value is not None for value in limits.values()):
            raise ValueError('明确累计总限应使用explicit_total策略')
        targets = sorted(set(target_task_ids))
        if not targets or origin_task_id not in targets:
            raise ValueError('累计授权必须包含原授权目标')
        for task_id in targets:
            self._owner(connection, workspace_id, task_id, actor)
        event_id = self._id('authorization', workspace_id, [scope_id, request_id])
        payload = {'scope_id': scope_id, 'revision': expected_revision + 1, 'source_ref': source_ref,
            'request_id': request_id, 'actor': actor.identity_payload(), 'target_task_ids': targets,
            'limits': limits, 'policy_kind': policy_kind, 'revoked': revoked}
        try:
            existing = self.repository.get_task_event(workspace_id, event_id, connection=connection)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.payload != payload or existing.task_id != origin_task_id:
                raise BudgetScopeConflict('累计授权幂等键内容冲突')
            return existing
        try:
            previous = self.current(connection, workspace_id, scope_id)
        except KeyError:
            if expected_revision != -1:
                raise BudgetScopeConflict('累计授权原版本不存在')
        else:
            if (previous.payload['revision'] != expected_revision or previous.task_id != origin_task_id
                    or previous.payload['actor'] != actor.identity_payload()):
                raise BudgetScopeConflict('累计授权版本或原授权成员已变化')
        return self._append(connection, event_id, workspace_id, origin_task_id, None,
            'execution_budget_scope_authorized', payload)

    def bind(self, connection, workspace_id, job_id, actor, *, scope_id, expected_revision):
        self._transaction(connection)
        job = self.repository.get_job_run(workspace_id, job_id, connection=connection)
        self._owner(connection, workspace_id, job.task_id, actor)
        event_id = self._id('binding', workspace_id, job_id)
        try:
            existing = self.repository.get_task_event(workspace_id, event_id, connection=connection)
        except KeyError:
            existing = None
        if existing is not None:
            if (existing.payload['scope_id'] != scope_id or existing.payload['revision'] != expected_revision
                    or existing.payload['actor'] != actor.identity_payload()):
                raise BudgetScopeConflict('原作业不能改绑授权范围或原授权版本')
            self._verify_input(connection, job, existing)
            return existing
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='device_work_requests'").fetchone() is not None and connection.execute(
                "SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_id=? LIMIT 1",
                (workspace_id, job_id)).fetchone():
            raise PermissionError('已封存执行请求不能自动改绑累计授权范围')
        if connection.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type IN ('creation_narration_unit_started','creation_narration_unit_settled',"
                "'task_execution_unit_started','task_execution_unit_settled') LIMIT 1",
                (workspace_id, job_id)).fetchone():
            raise PermissionError('已有历史用量不能静默迁入新授权范围，须保留原封存归属')
        current = self.current(connection, workspace_id, scope_id)
        policy = current.payload
        if (policy['revoked'] or policy['revision'] != expected_revision
                or policy['actor'] != actor.identity_payload() or job.task_id not in policy['target_task_ids']):
            raise PermissionError('作业不在当前累计授权范围或授权版本已失效')
        event_id = self._id('binding', workspace_id, job_id)
        payload = {'scope_id': scope_id, 'authorization_ref': current.id, 'revision': expected_revision,
            'actor': actor.identity_payload(), 'input_digest': job.input_digest}
        return self._append(connection, event_id, workspace_id, job.task_id, job.id,
            'execution_budget_scope_bound', payload)

    def _verify_input(self, connection, job, binding):
        if job.input_digest == binding.payload['input_digest']:
            return
        try:
            effective = self.repository.creation_execution_input(job.workspace_id, job.id, connection=connection)
        except KeyError as exc:
            raise PermissionError('原执行输入已变化且缺少原绑定版本回执') from exc
        if effective['input_digest'] != job.input_digest:
            raise PermissionError('原执行输入已变化，须先核验原绑定版本')

    def binding(self, connection, workspace_id, job_id):
        return self.repository.get_task_event(workspace_id, self._id('binding', workspace_id, job_id), connection=connection)

    def for_task(self, connection, workspace_id, task_id, actor):
        self._owner(connection, workspace_id, task_id, actor)
        rows = connection.execute("SELECT DISTINCT json_extract(e.payload_json,'$.scope_id') AS scope_id "
            "FROM task_events e JOIN job_runs j ON j.workspace_id=e.workspace_id AND j.id=e.job_id "
            "AND j.task_id=e.task_id WHERE e.workspace_id=? AND e.task_id=? "
            "AND e.event_type='execution_budget_scope_bound' ORDER BY scope_id",
            (workspace_id, task_id)).fetchall()
        scopes = []
        for row in rows:
            current = self.current(connection, workspace_id, row['scope_id'])
            if current.payload['actor'] != actor.identity_payload() or task_id not in current.payload['target_task_ids']:
                raise PermissionError('任务累计授权的当前主体或目标范围已变化')
            scopes.append({'authorization': current.payload, 'usage': self.usage(connection, workspace_id, row['scope_id'])})
        return scopes

    def usage(self, connection, workspace_id, scope_id):
        row = connection.execute("""
            SELECT COALESCE(SUM(CASE WHEN s.id IS NOT NULL
                THEN json_extract(s.payload_json,'$.elapsed_seconds') ELSE 0 END),0) AS execution_seconds,
                COALESCE(SUM(CASE WHEN s.id IS NOT NULL AND json_extract(s.payload_json,'$.output_bytes') IS NOT NULL
                THEN json_extract(s.payload_json,'$.output_bytes') ELSE 0 END),0) AS generated_bytes,
                COALESCE(SUM(CASE WHEN s.id IS NOT NULL THEN 0
                ELSE json_extract(r.payload_json,'$.maximum.execution_seconds') END),0) AS reserved_seconds,
                COALESCE(SUM(CASE WHEN s.id IS NOT NULL AND json_extract(s.payload_json,'$.output_bytes') IS NOT NULL
                THEN 0 ELSE json_extract(r.payload_json,'$.maximum.generated_bytes') END),0) AS reserved_bytes,
                COALESCE(SUM(CASE WHEN s.id IS NULL OR json_extract(s.payload_json,'$.output_bytes') IS NULL
                    THEN 1 ELSE 0 END),0) AS unknown_unit_count,
                COUNT(*) AS unit_count
            FROM task_events r LEFT JOIN task_events s ON s.workspace_id=r.workspace_id
                AND s.id=json_extract(r.payload_json,'$.receipt_id') AND s.job_id=r.job_id
                AND json_extract(s.payload_json,'$.usage_id')=json_extract(r.payload_json,'$.usage_id')
                AND s.event_type IN ('creation_narration_unit_settled','task_execution_unit_settled')
            WHERE r.workspace_id=? AND r.event_type='execution_budget_scope_reserved'
                AND json_extract(r.payload_json,'$.scope_id')=?
            """, (workspace_id, scope_id)).fetchone()
        return dict(row)

    def reserve(self, connection, workspace_id, job_id, actor, *, request_id, permit_generation,
                started_event_id, receipt_id, maximum, authorization_revision):
        self._transaction(connection)
        job = self.repository.get_job_run(workspace_id, job_id, connection=connection)
        self._owner(connection, workspace_id, job.task_id, actor)
        binding = self.binding(connection, workspace_id, job_id)
        current = self.current(connection, workspace_id, binding.payload['scope_id'])
        policy = current.payload
        self._verify_input(connection, job, binding)
        if (policy['revoked'] or policy['actor'] != actor.identity_payload()
                or job.task_id not in policy['target_task_ids']):
            raise PermissionError('当前作业累计授权已撤销或范围已变化')
        if set(maximum) != {'execution_seconds', 'generated_bytes'}:
            raise ValueError('累计占用需要本次有限执行上界')
        zero_media = False
        if maximum.get('generated_bytes') == 0:
            row = connection.execute("SELECT resources_json,unit_key FROM device_work_requests "
                "WHERE workspace_id=? AND owner_type='creation_job' AND owner_id=? AND id=?",
                (workspace_id, job_id, request_id)).fetchone()
            declared = json.loads(row['resources_json']) if row is not None else {}
            zero_media = (row is not None and row['unit_key'].startswith('render:delivery-adopt:v1:')
                and declared.get('kind') == 'creation_delivery_adoption'
                and declared.get('max_output_bytes') == 0 and declared.get('read_mode') == 'stream')
        for key, value in maximum.items():
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value < 0 or value == 0 and not (key == 'generated_bytes' and zero_media)
                    or key == 'generated_bytes' and not isinstance(value, int)):
                raise ValueError('单元最大占用须有限且为正，仅原许可的只读采用可声明零生成')
        started = self.repository.get_task_event(workspace_id, started_event_id, connection=connection)
        usage_id = f'{request_id}:{permit_generation}'
        if (started.job_id != job_id or started.payload.get('usage_id') != usage_id
                or started.event_type not in {'creation_narration_unit_started', 'task_execution_unit_started'}):
            raise PermissionError('累计预留必须关联当前作业原started回执')
        event_id = self._id('reservation', workspace_id, usage_id)
        payload = {'scope_id': policy['scope_id'], 'revision': authorization_revision,
            'input_digest': job.input_digest,
            'authorization_ref': current.id, 'binding_ref': binding.id, 'usage_id': usage_id,
            'started_event_id': started_event_id, 'receipt_id': receipt_id, 'maximum': maximum}
        try:
            existing = self.repository.get_task_event(workspace_id, event_id, connection=connection)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.job_id != job_id or {k: v for k, v in existing.payload.items() if k != 'authorization_ref'} != {
                    k: v for k, v in payload.items() if k != 'authorization_ref'}:
                raise BudgetScopeConflict('同一执行单元的原预留内容冲突')
            return existing
        if policy['revision'] != authorization_revision:
            raise BudgetScopeConflict('预留前累计授权版本已变化')
        measured = self.usage(connection, workspace_id, policy['scope_id'])
        for dimension, settled_key, reserved_key in (
                ('execution_seconds', 'execution_seconds', 'reserved_seconds'),
                ('generated_bytes', 'generated_bytes', 'reserved_bytes')):
            limit = policy['limits'][dimension]
            if limit is not None and measured[settled_key] + measured[reserved_key] + maximum[dimension] > limit:
                raise BudgetScopeExhausted('累计授权范围剩余量不足，原成果、消耗和未知占用均保留')
        return self._append(connection, event_id, workspace_id, job.task_id, job_id,
            'execution_budget_scope_reserved', payload)

    def settle(self, connection, workspace_id, job_id, *, request_id, permit_generation, receipt_id):
        self._transaction(connection)
        usage_id = f'{request_id}:{permit_generation}'
        reservation = self.repository.get_task_event(workspace_id,
            self._id('reservation', workspace_id, usage_id), connection=connection)
        receipt = self.repository.get_task_event(workspace_id, receipt_id, connection=connection)
        if (reservation.job_id != job_id or reservation.payload['receipt_id'] != receipt_id or receipt.job_id != job_id
                or receipt.payload.get('usage_id') != usage_id
                or receipt.event_type not in {'creation_narration_unit_settled', 'task_execution_unit_settled'}):
            raise PermissionError('累计结算必须引用同一作业、单元与原唯一回执')
        seconds, size = receipt.payload['elapsed_seconds'], receipt.payload['output_bytes']
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                or seconds < 0 or size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0)):
            raise ValueError('原结算用量不合法')
        return self.usage(connection, workspace_id, reservation.payload['scope_id'])
