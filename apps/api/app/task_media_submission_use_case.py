"""Domain-only media submission for a bound task asset.

The HTTP boundary accepts a small edit command.  This use case resolves the
asset through the production repository and creates the sealed EditPlan that
the existing TaskMediaJobs owner already schedules on the device.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.media_execution import FFMPEG_OPERATION, EditPlan, MediaOperation


class TaskMediaSubmissionRejected(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class BoundMediaSubmission:
    request_id: str
    base_version_id: str
    asset_id: str
    start_seconds: float
    end_seconds: float

    @classmethod
    def from_value(cls, value: object) -> "BoundMediaSubmission":
        if not isinstance(value, dict):
            raise TaskMediaSubmissionRejected(422, "媒体提交必须是对象")
        allowed = {"request_id", "base_version_id", "asset_id", "start_seconds", "end_seconds"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise TaskMediaSubmissionRejected(422, "媒体提交包含未支持的字段")
        missing = sorted(allowed - set(value))
        if missing:
            raise TaskMediaSubmissionRejected(422, "媒体提交缺少必要的领域字段")
        request_id = value["request_id"]
        base_version_id = value["base_version_id"]
        asset_id = value["asset_id"]
        if not isinstance(request_id, str) or not request_id.strip():
            raise TaskMediaSubmissionRejected(422, "request_id 无效")
        try:
            request_id = str(UUID(request_id))
        except (ValueError, AttributeError) as exc:
            raise TaskMediaSubmissionRejected(422, "request_id 必须是 UUID") from exc
        if not isinstance(base_version_id, str) or not base_version_id.strip():
            raise TaskMediaSubmissionRejected(422, "base_version_id 无效")
        if (not isinstance(asset_id, str) or not asset_id.strip() or Path(asset_id).is_absolute()
                or "/" in asset_id or "\\" in asset_id or asset_id.startswith("file:")):
            raise TaskMediaSubmissionRejected(422, "asset_id 必须是已登记的素材 ID")
        try:
            start = float(value["start_seconds"])
            end = float(value["end_seconds"])
        except (TypeError, ValueError) as exc:
            raise TaskMediaSubmissionRejected(422, "媒体时间范围无效") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise TaskMediaSubmissionRejected(422, "媒体时间范围必须为正向区间")
        return cls(request_id, base_version_id, asset_id, start, end)


class TaskMediaSubmissionUseCase:
    def __init__(self, repository, *, task_media, task_authorizations=None):
        self.repository = repository
        self.task_media = task_media
        self.task_authorizations = task_authorizations

    def submit(self, runtime, actor, task_id: str, value: object):
        command = BoundMediaSubmission.from_value(value)
        task = self.repository.task_records.get_creation_task(actor.workspace_id, task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise TaskMediaSubmissionRejected(403, "当前成员不能安排此任务的媒体处理")
        if task.current_timeline_version_id != command.base_version_id:
            raise TaskMediaSubmissionRejected(409, "媒体提交的基础时间线已变化，请重新读取当前作品")
        asset = self._bound_asset(task, command.asset_id)
        if self.task_authorizations is not None:
            verdict = self.task_authorizations.verdict(
                actor, task_id, "task_media", references={f"asset:{command.asset_id}"})
            if verdict != "ready":
                raise TaskMediaSubmissionRejected(403, "当前成员无权使用该作品绑定素材")
        if asset.duration_seconds is not None and command.end_seconds > asset.duration_seconds:
            raise TaskMediaSubmissionRejected(422, "媒体结束时间超出素材时长")
        try:
            reference = self.repository.workspace_media_reference_for_asset(
                actor.workspace_id, command.asset_id)
            source_path = self.repository.resolve_clip_media_reference(
                actor.workspace_id, command.asset_id, reference)
        except PermissionError as exc:
            raise TaskMediaSubmissionRejected(403, "当前成员无权使用该素材") from exc
        except (KeyError, ValueError, OSError) as exc:
            raise TaskMediaSubmissionRejected(409, "正式素材绑定已失效，请重新读取当前作品") from exc
        operation = MediaOperation(
            operation="cut",
            inputs=(str(source_path),),
            output_name="media-output.mp4",
            options={"start": command.start_seconds, "duration": command.end_seconds - command.start_seconds,
                     "asset_id": command.asset_id},
        )
        plan = EditPlan(
            workspace_id=actor.workspace_id,
            task_id=task_id,
            base_version_id=command.base_version_id,
            request_id=command.request_id,
            operations=(operation,),
            member_id=actor.member_id,
            metadata={"source_asset_ids": [command.asset_id], "source_references": [reference]},
        )
        existing = self._existing_submission(actor.workspace_id, task_id, command.request_id)
        if existing is not None:
            if existing["plan"] != plan.as_dict():
                raise TaskMediaSubmissionRejected(409, "request_id 已绑定到不同的媒体输入")
            return self.task_media.read(actor.workspace_id, existing["job_id"])[0]
        try:
            return self.task_media.submit(
                runtime, actor, task_id, FFMPEG_OPERATION, {"edit_plan": plan.as_dict()}, "media_output")
        except PermissionError as exc:
            raise TaskMediaSubmissionRejected(403, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise TaskMediaSubmissionRejected(409, str(exc)) from exc

    def _bound_asset(self, task, asset_id: str):
        try:
            timeline = self.repository.timeline_records.get_timeline_version(
                task.workspace_id, task.current_timeline_version_id)
        except (KeyError, ValueError) as exc:
            raise TaskMediaSubmissionRejected(409, "当前作品版本不可读取") from exc
        found = set()
        def walk(value):
            if isinstance(value, dict):
                if value.get("asset_id"):
                    found.add(value["asset_id"])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(timeline.snapshot)
        if asset_id not in found:
            raise TaskMediaSubmissionRejected(403, "素材未绑定到当前作品版本")
        try:
            return self.repository.get_asset(task.workspace_id, asset_id)
        except KeyError as exc:
            raise TaskMediaSubmissionRejected(409, "正式绑定素材已不存在") from exc

    def _existing_submission(self, workspace_id: str, task_id: str, request_id: str):
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT id, payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
                "AND event_type='task_media_submitted' ORDER BY created_at DESC",
                (workspace_id, task_id),
            ).fetchall()
        import json
        for row in rows:
            payload = json.loads(row["payload_json"])
            plan = payload.get("arguments", {}).get("edit_plan", {})
            if plan.get("request_id") == request_id:
                return {"job_id": row["id"].removeprefix("event_").removesuffix("_start"), "plan": plan}
        return None
