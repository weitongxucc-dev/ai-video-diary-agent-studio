"""Adopt fixed task knowledge and preserve its original atomic receipt."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json

from app.creation.contracts import CreationTask, TaskEvent
from app.models import utc_now
from app.source_policy import content_allows_use, knowledge_item_readable as _content_readable


@dataclass(frozen=True)
class KnowledgeAdoptionCommand:
    request_id: str
    expected_plan_updated_at: datetime
    expected_task_updated_at: datetime
    references: tuple[tuple[str, str], ...]


class TaskKnowledgeAdoptionUseCase:
    def __init__(self, connection_factory, *, require_task, plans, tasks, events,
                 has_open_job, count_scripts, revision_value):
        self._connect = connection_factory
        self.require_task = require_task
        self.plans, self.tasks, self.events = plans, tasks, events
        self.has_open_job, self.count_scripts = has_open_job, count_scripts
        self.revision_value = revision_value

    @staticmethod
    def _event_id(workspace_id: str, member_id: str, request_id: str) -> str:
        return 'event_knowledge_adoption_' + hashlib.sha256(json.dumps([workspace_id, member_id, request_id]).encode()).hexdigest()

    @staticmethod
    def _owner(task: CreationTask, member_id: str) -> None:
        if not member_id or not _content_readable(user_id=member_id, local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task.id)
        if task.owner_user_id and task.owner_user_id != member_id:
            raise PermissionError('当前成员不能更改此任务的资料。')

    def read_receipt(self, workspace_id: str, task_id: str, request_id: str, *, member_id: str) -> dict[str, object]:
        with self._connect() as conn:
            task = self.require_task(conn, workspace_id, task_id)
            self._owner(task, member_id)
            row = self.events.knowledge_adoption_receipt(
                conn, workspace_id, self._event_id(workspace_id, member_id, request_id)
            )
            if row is None or row['task_id'] != task_id:
                raise KeyError(request_id)
            return json.loads(row['payload_json'])['receipt']

    def adopt(self, workspace_id: str, task_id: str, member_id: str, command: KnowledgeAdoptionCommand, *, read_reference) -> dict[str, object]:
        """Append fixed inputs and their immutable receipt under one write lock."""
        from app.creation.plan_contracts import CreationPlanKnowledgeReference
        if not 1 <= len(command.references) <= 64 or len(set(command.references)) != len(command.references):
            raise ValueError('一次采用需包含1至64份不同的固定资料版本。')
        if any((not identity.strip() or not version.strip() for identity, version in command.references)):
            raise ValueError('资料采用必须包含实际ID和固定版本。')

        def resolve_references() -> list[CreationPlanKnowledgeReference]:
            references = []
            for identity, version in command.references:
                item = read_reference(workspace_id, identity, version, user_id=member_id)
                if not content_allows_use(item.use_state, 'reference') or item.use_state == 'not_for_automation':
                    raise ValueError('所选资料当前不允许引用，没有追加到任务。')
                references.append(CreationPlanKnowledgeReference(item.id, item.version, item.title, item.authorization, item.ownership))
            return references
        request_id = command.request_id
        expected_plan_updated_at = command.expected_plan_updated_at
        expected_task_updated_at = command.expected_task_updated_at
        requested_references = command.references
        if not request_id.strip():
            raise ValueError('采用资料需要稳定请求身份。')
        fingerprint = hashlib.sha256(json.dumps({'task_id': task_id, 'member_id': member_id, 'references': requested_references}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        event_id = self._event_id(workspace_id, member_id, request_id)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = self.require_task(conn, workspace_id, task_id)
            self._owner(task, member_id)
            previous = self.events.knowledge_adoption_request(conn, event_id)
            if previous:
                payload = json.loads(previous['payload_json'])
                if previous['task_id'] != task_id or payload['fingerprint'] != fingerprint:
                    raise ValueError('此资料采用请求已用于不同任务或资料，未重复采用。')
                return payload['receipt']
            plan = self.plans.read_knowledge_adoption_plan(conn, workspace_id, task.plan_id)
            if self.revision_value(plan.updated_at) != self.revision_value(expected_plan_updated_at) or self.revision_value(task.updated_at) != self.revision_value(expected_task_updated_at):
                raise ValueError('方案或任务版本已变化，请读取当前版本后重新采用资料。')
            resolved = resolve_references()
            existing = {item.id: item for item in plan.knowledge_references}
            added = []
            for item in resolved:
                if item.id in existing:
                    if existing[item.id] != item:
                        raise ValueError('任务已固定了此资料的另一版本或授权；本入口只追加，不覆盖旧引用。')
                else:
                    existing[item.id] = item
                    added.append(item)
            if added:
                if task.stage_status == 'running' or self.has_open_job(conn, workspace_id, task_id):
                    raise ValueError('此任务仍有未结束的制作或生成作业；先等作业结算或取消，再采用资料。')
                if task.stage not in {'topic', 'script'} or task.confirmed_script_id or task.narration_cache_key or task.montage_plan_id or task.current_timeline_version_id or task.output_ready:
                    raise ValueError('此任务已有采用稿或下游结果，需要明确文案修订及影响范围；本次未追加或改动原结果。')
                if len(existing) + len(plan.source_references) > 64:
                    raise ValueError('文案输入最多64份资料，请只采用本次需要的固定资料。')
            before = [asdict(item) for item in plan.knowledge_references]
            previous_status = task.stage_status
            now = utc_now()
            if added:
                plan.knowledge_references.extend(added)
                plan.updated_at = task.updated_at = now
                if task.stage == 'script':
                    task.stage_status, task.status, task.recovery_point = ('not_started', 'running', 'script')
                    task.stage_reason = task.stage_impact = task.stage_repair_action = task.stage_retryable = None
                self.plans.write_knowledge_adopted_plan(conn, workspace_id, plan)
                self.tasks.write_knowledge_adopted_task(conn, workspace_id, task)
            retained = self.count_scripts(conn, workspace_id, task_id)
            receipt = {"request_id": request_id, "task_id": task_id, "plan_id": plan.id,
                "status": "adopted" if added else "unchanged", "recorded_at": now.isoformat(),
                "added_references": [asdict(item) for item in added], "previous_references": before,
                "knowledge_references": [asdict(item) for item in plan.knowledge_references],
                "plan_updated_at": self.revision_value(plan.updated_at), "task_updated_at": self.revision_value(task.updated_at),
                "retained_script_count": retained, "previous_stage_status": previous_status,
                "next_step": "generate_new_script" if added and task.stage == "script" else "unchanged" if not added else "continue_topic"}
            self.events.append_task_event(TaskEvent(event_id, workspace_id, task_id, None, 'task_knowledge_adopted', {'fingerprint': fingerprint, 'member_id': member_id, 'receipt': receipt}), connection=conn)
            return receipt

    def inputs_current(self, conn, workspace_id, task_id, plan_id, input_references):
        if not self.events.task_knowledge_was_adopted(conn, workspace_id, task_id):
            return True
        planned = self.plans.knowledge_reference_keys(conn, workspace_id, plan_id)
        bound = {(item["object_id"], item["version_or_identity"]) for item in input_references
                 if item.get("kind") == "knowledge"}
        return planned.issubset(bound)
