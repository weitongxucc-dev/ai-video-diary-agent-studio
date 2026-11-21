"""Adapt the public TaskMediaJobs execution boundary to media execution."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import EditPlan, MediaExecutionError

FFMPEG_OPERATION = "ffmpeg_execution"


@dataclass(frozen=True)
class DelegateReceipt:
    status: str
    request_id: str
    base_version_id: str
    timeline_version_id: str | None = None
    output_refs: tuple[str, ...] = ()
    log_path: str | None = None
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    job_id: str | None = None
    media_job_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "request_id": self.request_id,
            "base_version_id": self.base_version_id,
            "timeline_version_id": self.timeline_version_id,
            "output_refs": list(self.output_refs),
            "log_path": self.log_path,
            "failure_reason": self.failure_reason,
            "impact": self.impact,
            "repair_action": self.repair_action,
            "job_id": self.job_id,
            "media_job_id": self.media_job_id or self.job_id,
        }


class TaskMediaExecutionDelegate:
    """Adapt one sealed TaskMediaJobs event to MediaExecutionEntrypoint."""

    def __init__(self, entrypoint) -> None:
        self.entrypoint = entrypoint

    @staticmethod
    def handles(operation: str, arguments: Mapping[str, Any]) -> bool:
        return operation == FFMPEG_OPERATION and isinstance(arguments.get("edit_plan"), Mapping)

    def plan_from_event(self, event: Mapping[str, Any], actor_identity: Mapping[str, Any], *, job=None) -> EditPlan:
        arguments = event.get("arguments")
        input_facts = event.get("input_facts")
        if not isinstance(arguments, Mapping) or not isinstance(input_facts, Mapping):
            raise TypeError("媒体 delegate 事件缺少 arguments 或 input_facts")
        raw_plan = arguments.get("edit_plan")
        if not isinstance(raw_plan, Mapping):
            raise TypeError("媒体 delegate 缺少封存 EditPlan")
        plan = EditPlan.from_value(raw_plan)
        expected_version = input_facts.get("base_version_id")
        if expected_version != plan.base_version_id:
            raise ValueError("媒体 delegate 的基础时间线与封存输入不一致")
        event_workspace = event.get("workspace_id") or self._job_value(job, "workspace_id")
        event_task = event.get("task_id") or self._job_value(job, "task_id")
        if plan.workspace_id != event_workspace or plan.task_id != event_task:
            raise ValueError("媒体 delegate 的任务归属与封存事件不一致")
        if plan.member_id is not None and plan.member_id != actor_identity.get("member_id"):
            raise PermissionError("媒体 delegate 的成员身份与封存事件不一致")
        return plan

    def submit(self, job, event: Mapping[str, Any], actor_identity: Mapping[str, Any]) -> dict[str, Any]:
        plan = self.plan_from_event(event, actor_identity, job=job)
        result = self.entrypoint.submit(plan)
        return self._receipt(job, plan, result)

    def resume(self, job, event: Mapping[str, Any], actor_identity: Mapping[str, Any]) -> dict[str, Any]:
        plan = self.plan_from_event(event, actor_identity, job=job)
        result = self.entrypoint.resume(self._job_value(job, "id"), plan.workspace_id)
        return self._receipt(job, plan, result)

    def retry(self, job, event: Mapping[str, Any], actor_identity: Mapping[str, Any]) -> dict[str, Any]:
        plan = self.plan_from_event(event, actor_identity, job=job)
        result = self.entrypoint.retry(self._job_value(job, "id"), plan.workspace_id)
        return self._receipt(job, plan, result)

    def cancel(self, job, event: Mapping[str, Any], actor_identity: Mapping[str, Any]) -> dict[str, Any]:
        plan = self.plan_from_event(event, actor_identity, job=job)
        result = self.entrypoint.cancel(job["id"], plan.workspace_id)
        return self._receipt(job, plan, result)

    @staticmethod
    def _receipt(job, plan: EditPlan, result: Mapping[str, Any]) -> dict[str, Any]:
        status = str(result.get("status", "failed"))
        receipt = DelegateReceipt(
            status=status,
            request_id=plan.request_id,
            base_version_id=plan.base_version_id,
            timeline_version_id=result.get("timeline_version_id"),
            output_refs=tuple(str(item) for item in result.get("output_refs", ())),
            log_path=result.get("log_path"),
            failure_reason=result.get("failure_reason"),
            impact=result.get("impact"),
            repair_action=result.get("repair_action"),
            job_id=result.get("job_id") or (job.get("id") if isinstance(job, Mapping) else None),
            media_job_id=result.get("media_job_id") or result.get("job_id"),
        )
        if status == "failed" and not receipt.failure_reason:
            raise MediaExecutionError("delegate_failure_missing_reason", "媒体 delegate 没有返回可信失败原因。", "查询原媒体作业并补齐失败回执。")
        if status == "succeeded" and (not receipt.output_refs or not receipt.timeline_version_id):
            raise MediaExecutionError("delegate_success_missing_receipt", "媒体 delegate 的成功回执缺少版本或产物。", "查询原媒体作业，不把不完整结果发布到任务。")
        return receipt.as_dict()

    @staticmethod
    def _job_value(job, field: str):
        if isinstance(job, Mapping):
            return job.get(field)
        return getattr(job, field, None)
