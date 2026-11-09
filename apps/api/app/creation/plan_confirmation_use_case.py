"""Ordinary and reply-bound plan confirmation with their original transactions."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, replace

from app.creation.contracts import CreationTask as _CreationTask, TaskEvent as _TaskEvent
from app.creation.plan_contracts import CreationPlan, _validate_plan_material_matches
from app.creation.script_contracts import (
    _validated_assistant_reply_reference, _assistant_reply_request_event_id,
)
from app.persistence_json import _json_object
from app.source_policy import content_owner


def _creation_plan_conversion_intent(plan: CreationPlan) -> dict[str, object]:
    value = asdict(plan)
    for key in ("id", "creation_task_id", "owner_user_id", "created_at", "confirmed_at", "updated_at"):
        value.pop(key, None)
    return value


def _creation_task_conversion_intent(task: _CreationTask) -> dict[str, object]:
    value = asdict(task)
    for key in ("id", "plan_id", "owner_user_id", "created_at", "updated_at"):
        value.pop(key, None)
    return value



class CreationPlanConfirmationUseCase:
    def __init__(self, connection_factory, *, records, read_task, execution_records):
        self._connect = connection_factory
        self.records = records
        self._read_task = read_task
        self.execution_records = execution_records

    def confirm_creation_plan(
        self,
        plan: CreationPlan,
        task: _CreationTask,
        *,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
        reply_reference: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> tuple[CreationPlan, _CreationTask]:
        if reply_reference is not None or request_id is not None:
            if reply_reference is None or request_id is None:
                raise ValueError("对话回复来源绑定需要稳定请求身份")
            return self.confirm_creation_plan_from_reply(
                plan,
                task,
                reply_reference=reply_reference,
                request_id=request_id,
                verified_owner_user_id=verified_owner_user_id,
                local_diagnostic=local_diagnostic,
            )
        plan, task = self.prepare_confirmed_creation_plan(
            plan, task, verified_owner_user_id=verified_owner_user_id,
            local_diagnostic=local_diagnostic,
        )
        with self._connect() as conn:
            self.records.insert_confirmed_creation_plan(conn, plan, task)
        return self.records.get_creation_plan(plan.workspace_id, plan.id), self._read_task(task.workspace_id, task.id)


    def confirm_creation_plan_from_reply(
        self,
        plan: CreationPlan,
        task: _CreationTask,
        *,
        reply_reference: dict[str, str],
        request_id: str,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> tuple[CreationPlan, _CreationTask]:
        reference = _validated_assistant_reply_reference(reply_reference)
        if not request_id.strip():
            raise ValueError("确认方案请求身份不能为空")
        plan, task = self.prepare_confirmed_creation_plan(
            plan, task, verified_owner_user_id=verified_owner_user_id,
            local_diagnostic=local_diagnostic,
        )
        fingerprint_payload = {
            "workspace_id": plan.workspace_id,
            "owner_user_id": task.owner_user_id,
            "reply_reference": reference,
            "plan": _creation_plan_conversion_intent(plan),
            "task": _creation_task_conversion_intent(task),
        }
        request_fingerprint = hashlib.sha256(
            _json_object(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        event_id = _assistant_reply_request_event_id(
            "assistant_reply_plan_confirmed", plan.workspace_id, request_id,
        )
        replay_ids: tuple[str, str] | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM task_events WHERE id = ?", (event_id,),
            ).fetchone()
            if existing is not None:
                event = self.execution_records.task_event_from_row(existing)
                if (
                    event.event_type != "assistant_reply_plan_confirmed"
                    or event.workspace_id != plan.workspace_id
                    or event.payload.get("request_fingerprint") != request_fingerprint
                ):
                    raise ValueError("方案请求身份已用于不同的对话或方案输入")
                replay_ids = (str(event.payload["plan_id"]), str(event.payload["task_id"]))
            else:
                self.records.insert_confirmed_creation_plan(conn, plan, task)
                self.execution_records.append_task_event(
                    _TaskEvent(
                        id=event_id,
                        workspace_id=plan.workspace_id,
                        task_id=task.id,
                        job_id=None,
                        event_type="assistant_reply_plan_confirmed",
                        payload={
                            "request_id": request_id,
                            "request_fingerprint": request_fingerprint,
                            "reply_reference": reference,
                            "reply_owner_user_id": task.owner_user_id,
                            "plan_id": plan.id,
                            "task_id": task.id,
                        },
                    ),
                    connection=conn,
                )
        if replay_ids is not None:
            return (
                self.records.get_creation_plan(plan.workspace_id, replay_ids[0]),
                self._read_task(plan.workspace_id, replay_ids[1]),
            )
        return self.records.get_creation_plan(plan.workspace_id, plan.id), self._read_task(task.workspace_id, task.id)


    @staticmethod
    def prepare_confirmed_creation_plan(
        plan: CreationPlan,
        task: _CreationTask,
        *,
        verified_owner_user_id: str | None,
        local_diagnostic: bool,
    ) -> tuple[CreationPlan, _CreationTask]:
        if not plan.workspace_id.strip() or not task.workspace_id.strip():
            raise ValueError("创作方案和任务必须属于工作区")
        if plan.workspace_id != task.workspace_id or plan.id != task.plan_id:
            raise ValueError("创作任务必须关联同一工作区的创作方案")
        if not plan.goal.strip() or not plan.name.strip():
            raise ValueError("创作方案必须包含目标和名称")
        if not 1 <= plan.target_count <= 50:
            raise ValueError("创作方案成片数必须在 1 到 50 之间")
        if any(blocker.blocks_confirmation for blocker in plan.blockers):
            raise ValueError("当前预检存在阻断选题阶段的事实缺口")
        if plan.approved_clip_count > 0 and not plan.material_matches:
            raise ValueError("新创作方案必须固化本次实际命中的素材片段")
        _validate_plan_material_matches(plan.material_matches)
        # 上传者只认已验证身份。dataclass 里的 owner_user_id 是调用方能自填的
        # 字段，新建时一律以这里解析出的身份覆盖：自填的归属不参与判断，
        # 没有可验证身份就不算任何成员的私有内容。
        owner = content_owner(verified_owner_user_id)
        if owner is None and not local_diagnostic:
            # 认证范围里解析不出身份：不算任何成员的私有内容，也不采信输入值。
            plan = replace(plan, owner_user_id=None)
            task = replace(task, owner_user_id=None)
        elif owner:
            plan = replace(plan, owner_user_id=owner)
            task = replace(task, owner_user_id=owner)
        return plan, task
