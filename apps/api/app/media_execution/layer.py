from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from app.creation.contracts import ArtifactRecord, JobRun, TaskEvent, TimelineVersion

from .capabilities import dependency_payload, detect_dependencies
from .contracts import (
    EditPlan,
    MediaExecutionCancelled,
    MediaExecutionError,
    MediaJob,
    MediaProcessReleaseUnverified,
)
from .worker import FfmpegWorker


class MediaExecutionLayer:
    """Owns EditPlan -> MediaJob -> Worker -> TimelineVersion publication."""

    def __init__(self, repository, output_root: str | Path, *, worker: FfmpegWorker | None = None,
                 connection_factory: Callable[[], sqlite3.Connection] | None = None) -> None:
        self.repository = repository
        self._connect = connection_factory
        if self._connect is None:
            raise ValueError("媒体执行入口需要显式注入 ProductionRepository 的连接工厂")
        self.output_root = Path(output_root).expanduser()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.dependencies = detect_dependencies()
        self.worker = worker
        self._cancel_lock = threading.Lock()
        self._cancel_events: dict[tuple[str, str], threading.Event] = {}

    def capabilities(self) -> dict[str, object]:
        from .capabilities import capability_catalog
        return {"dependencies": dependency_payload(self.dependencies), "capabilities": capability_catalog(self.dependencies)}

    def read(self, workspace_id: str, job_id: str) -> MediaJob:
        with self._connect() as conn:
            conn.execute("BEGIN")
            job = self.repository.job_records.get_job_run(workspace_id, job_id, connection=conn)
            if job.kind != "media_execution":
                raise KeyError(job_id)
            start = self.repository.execution_records.get_task_event(
                workspace_id, f"event_{job_id}_media_start", connection=conn)
            suffix = {"succeeded": "result", "failed": "failure", "cancelled": "cancelled"}.get(job.status)
            result = None
            if suffix is not None:
                result = self.repository.execution_records.get_task_event(
                    workspace_id, f"event_{job_id}_media_{suffix}", connection=conn)
        return MediaJob(id=job.id, workspace_id=job.workspace_id, task_id=job.task_id,
                        request_id=str(start.payload["request_id"]), base_version_id=str(start.payload["plan"]["base_version_id"]),
                        status=job.status, attempt=job.attempt, output_refs=tuple(job.output_refs),
                        timeline_version_id=result.payload.get("timeline_version_id") if result and result.event_type == "media_execution_completed" else None,
                        log_path=result.payload.get("log_path") if result else None,
                        failure_reason=job.failure_reason, impact=job.impact, repair_action=job.repair_action)

    def submit(self, plan: EditPlan | dict, *, idempotency_suffix: str | None = None,
               cancel_event: threading.Event | None = None) -> dict[str, object]:
        plan = plan if isinstance(plan, EditPlan) else EditPlan.from_value(plan)
        digest = hashlib.sha256(json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        idempotency_key = f"media:{plan.task_id}:{plan.request_id}{idempotency_suffix or ''}"
        existing = self.repository.job_records.get_job_run_by_idempotency_key(plan.workspace_id, idempotency_key)
        if existing is not None:
            if existing.kind != "media_execution" or existing.task_id != plan.task_id or existing.input_digest != digest:
                raise ValueError("幂等键已绑定到不同的媒体输入")
            return self._existing_result(existing, plan)
        self._assert_plan_current(plan)
        job_id = f"media_{uuid4().hex}"
        job = JobRun(id=job_id, workspace_id=plan.workspace_id, task_id=plan.task_id,
                     kind="media_execution", idempotency_key=idempotency_key, input_digest=digest)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            persisted = self.repository.job_records.create_job_run(job, connection=conn)
            if persisted.id == job_id:
                self.repository.execution_records.append_task_event(TaskEvent(
                    id=f"event_{job_id}_media_start", workspace_id=plan.workspace_id, task_id=plan.task_id, job_id=job_id,
                    event_type="media_execution_submitted", payload={"request_id": plan.request_id, "plan": plan.as_dict(),
                    "dependencies": dependency_payload(self.dependencies)}, created_at=job.created_at), connection=conn)
        if persisted.id != job_id:
            return self._existing_result(persisted, plan)
        return self._run(job_id, plan, cancel_event=cancel_event)

    def _existing_result(self, job: JobRun, plan: EditPlan) -> dict[str, object]:
        """Return the durable receipt for a repeated idempotent submission."""
        result = self.read(job.workspace_id, job.id)
        return {
            "job_id": result.id,
            "status": result.status,
            "request_id": result.request_id or plan.request_id,
            "timeline_version_id": result.timeline_version_id,
            "output_refs": list(result.output_refs),
            "log_path": result.log_path,
            "failure_reason": result.failure_reason,
            "impact": result.impact,
            "repair_action": result.repair_action,
        }

    def cancel(self, job_id: str, workspace_id: str | None = None) -> dict[str, object]:
        workspace = self._job_workspace(job_id, workspace_id)
        with self._cancel_lock:
            current = self.read(workspace, job_id)
            if current.status in {"succeeded", "failed", "cancelled"}:
                return self._job_receipt(current)
            event = self._cancel_events.get((workspace, job_id))
            if event is not None:
                event.set()
                return {"job_id": job_id, "status": "cancellation_requested", "request_id": current.request_id}
            if current.status != "queued":
                raise MediaProcessReleaseUnverified()
            start = self.repository.execution_records.get_task_event(workspace, f"event_{job_id}_media_start")
            return self._cancelled(job_id, EditPlan.from_value(start.payload["plan"]), queued_only=True)

    @staticmethod
    def _job_receipt(job: MediaJob) -> dict[str, object]:
        receipt = asdict(job)
        receipt["job_id"] = receipt.pop("id")
        return receipt

    def resume(self, job_id: str, workspace_id: str | None = None) -> dict[str, object]:
        job = self.repository.job_records.get_job_run(self._job_workspace(job_id, workspace_id), job_id)
        if job.kind != "media_execution":
            raise ValueError("不是曜核媒体作业")
        event = self.repository.execution_records.get_task_event(job.workspace_id, f"event_{job_id}_media_start")
        return self._run(job_id, EditPlan.from_value(event.payload["plan"]))

    def retry(self, job_id: str, workspace_id: str | None = None) -> dict[str, object]:
        job = self.repository.job_records.get_job_run(self._job_workspace(job_id, workspace_id), job_id)
        if job.status not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("只有失败、取消或中断的媒体作业可以重试")
        event = self.repository.execution_records.get_task_event(job.workspace_id, f"event_{job_id}_media_start")
        plan = EditPlan.from_value(event.payload["plan"])
        retry_plan = EditPlan(plan.workspace_id, plan.task_id, plan.base_version_id, plan.request_id,
                              plan.operations, member_id=plan.member_id, metadata=plan.metadata)
        return self.submit(retry_plan, idempotency_suffix=f":retry:{uuid4().hex}")

    def _run(self, job_id: str, plan: EditPlan,
             cancel_event: threading.Event | None = None) -> dict[str, object]:
        key = (plan.workspace_id, job_id)
        cancel_event = cancel_event or threading.Event()
        with self._cancel_lock:
            if key in self._cancel_events:
                raise ValueError("媒体作业已有执行者")
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self.repository.job_records.get_job_run(*key, connection=conn)
                if job.status != "cancelled":
                    self.repository.job_records.claim_job_run(*key, connection=conn)
            if job.status == "cancelled":
                return self._job_receipt(self.read(*key))
            self._cancel_events[key] = cancel_event
        try:
            worker = self.worker or FfmpegWorker(self.dependencies)
            result = worker.execute(
                plan, job_id, self.output_root, cancel_event=cancel_event,
                progress=lambda value: self.repository.job_records.update_job_run_progress(*key, value),
            )
            with self._cancel_lock:
                if cancel_event.is_set():
                    raise MediaExecutionCancelled()
                return self._publish(job_id, plan, result)
        except MediaExecutionCancelled:
            with self._cancel_lock:
                return self._cancelled(job_id, plan)
        except MediaProcessReleaseUnverified:
            # Without proof of release, preserve the running state and refuse retries.
            raise
        except MediaExecutionError as exc:
            return self._fail(job_id, plan, exc)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
            return self._fail(job_id, plan, MediaExecutionError("media_execution_failed", str(exc) or type(exc).__name__, "修复首个失败原因后沿原请求重试。"))
        finally:
            with self._cancel_lock:
                del self._cancel_events[key]

    def _publish(self, job_id: str, plan: EditPlan, result: dict[str, object]) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self.repository.task_records.get_creation_task(plan.workspace_id, plan.task_id, connection=conn)
            if task.current_timeline_version_id != plan.base_version_id:
                raise MediaExecutionError("timeline_version_conflict", "当前时间线已变化，媒体输出未发布。", "读取当前时间线后重新生成 EditPlan。")
            base = self.repository.timeline_records.get_timeline_version(plan.workspace_id, plan.base_version_id, connection=conn)
            outputs = result["outputs"]
            refs: list[str] = []
            for index, item in enumerate(outputs, start=1):
                path = Path(item["path"])
                logical_ref = f"media/{job_id}/{index}-{path.name}"
                refs.append(logical_ref)
                artifact = ArtifactRecord(id=f"artifact_{job_id}_{index}", workspace_id=plan.workspace_id,
                    task_id=plan.task_id, kind="media_output", logical_ref=logical_ref,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size,
                    producer_job_id=job_id, input_refs=tuple(operation for operation in [plan.base_version_id]))
                self.repository.execution_records.create_artifact_record(artifact, connection=conn)
            sequence = self.repository.timeline_records.next_timeline_sequence(plan.workspace_id, plan.task_id, connection=conn)
            snapshot = dict(base.snapshot)
            snapshot["media_execution"] = {"job_id": job_id, "request_id": plan.request_id,
                "outputs": refs, "log_path": result["log_path"], "operations": [item.as_dict() for item in plan.operations]}
            version = TimelineVersion(id=f"timeline_media_{job_id}", workspace_id=plan.workspace_id, task_id=plan.task_id,
                parent_version_id=base.id, sequence=sequence, snapshot=snapshot, created_by_operation_id=job_id)
            self.repository.timeline_records.create_timeline_version(version, connection=conn,
                expected_current_version_id=plan.base_version_id)
            self.repository.execution_records.append_task_event(TaskEvent(
                id=f"event_{job_id}_media_result", workspace_id=plan.workspace_id, task_id=plan.task_id, job_id=job_id,
                event_type="media_execution_completed", payload={"request_id": plan.request_id, "outputs": refs,
                    "timeline_version_id": version.id, "log_path": result["log_path"]}), connection=conn)
            self.repository.job_records.finish_job_run(plan.workspace_id, job_id, status="succeeded", output_refs=refs, connection=conn)
        return {"job_id": job_id, "status": "succeeded", "request_id": plan.request_id,
                "timeline_version_id": version.id, "output_refs": refs, "log_path": result["log_path"],
                "failure_reason": None, "impact": None, "repair_action": None}

    def _fail(self, job_id: str, plan: EditPlan, error: MediaExecutionError) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            log_path = str(self.output_root / job_id / f"request-{plan.request_id}.jsonl")
            self.repository.execution_records.append_task_event(TaskEvent(
                id=f"event_{job_id}_media_failure", workspace_id=plan.workspace_id, task_id=plan.task_id, job_id=job_id,
                event_type="media_execution_failed", payload={"request_id": plan.request_id, "reason": error.reason,
                    "impact": error.impact, "repair_action": error.repair_action, "log_path": log_path}), connection=conn)
            self.repository.job_records.finish_job_run(plan.workspace_id, job_id, status="failed",
                failure_reason=error.reason, impact=error.impact, repair_action=error.repair_action, connection=conn)
        return {"job_id": job_id, "status": "failed", "request_id": plan.request_id,
                "failure_reason": error.reason, "impact": error.impact, "repair_action": error.repair_action,
                "log_path": str(self.output_root / job_id / f"request-{plan.request_id}.jsonl")}

    def _cancelled(self, job_id: str, plan: EditPlan, *, queued_only: bool = False) -> dict[str, object]:
        error = MediaExecutionCancelled()
        log_path = self.output_root / job_id / f"request-{plan.request_id}.jsonl"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.repository.job_records.get_job_run(plan.workspace_id, job_id, connection=conn)
            if current.status not in {"succeeded", "failed", "cancelled"}:
                if queued_only and current.status != "queued":
                    raise MediaProcessReleaseUnverified()
                self.repository.job_records.finish_job_run(plan.workspace_id, job_id, status="cancelled",
                    failure_reason=error.reason, impact=error.impact, repair_action=error.repair_action, connection=conn)
                self.repository.execution_records.append_task_event(TaskEvent(
                    id=f"event_{job_id}_media_cancelled", workspace_id=plan.workspace_id,
                    task_id=plan.task_id, job_id=job_id, event_type="media_execution_cancelled",
                    payload={"request_id": plan.request_id, "base_version_id": plan.base_version_id,
                        "status": "cancelled", "reason": error.reason, "impact": error.impact,
                        "repair_action": error.repair_action, "log_path": str(log_path) if log_path.is_file() else None},
                ), connection=conn)
        return self._job_receipt(self.read(plan.workspace_id, job_id))

    def _assert_plan_current(self, plan: EditPlan) -> None:
        task = self.repository.task_records.get_creation_task(plan.workspace_id, plan.task_id)
        if task.current_timeline_version_id != plan.base_version_id:
            raise MediaExecutionError("timeline_version_conflict", "当前时间线已变化，未创建媒体作业。", "读取当前时间线后重新生成 EditPlan。")

    def _job_workspace(self, job_id: str, workspace_id: str | None = None) -> str:
        if workspace_id is not None:
            self.repository.job_records.get_job_run(workspace_id, job_id)
            return workspace_id
        for candidate_workspace_id in ("local",):
            try:
                self.repository.job_records.get_job_run(candidate_workspace_id, job_id)
                return candidate_workspace_id
            except KeyError:
                continue
        raise KeyError(job_id)
