from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from app.production import JobRun


TerminalJobStatus = Literal["succeeded", "failed", "cancelled", "interrupted"]


class JobStateStore(Protocol):
    """The persistence boundary required by a job executor."""

    def create_job_run(self, run: JobRun) -> JobRun: ...

    def get_job_run(self, workspace_id: str, job_id: str) -> JobRun: ...

    def list_job_runs(self, workspace_id: str, task_id: str) -> list[JobRun]: ...

    def claim_job_run(self, workspace_id: str, job_id: str) -> JobRun: ...

    def update_job_run_progress(
        self, workspace_id: str, job_id: str, progress: int
    ) -> JobRun: ...

    def finish_job_run(
        self,
        workspace_id: str,
        job_id: str,
        *,
        status: TerminalJobStatus,
        output_refs: list[str] | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
    ) -> JobRun: ...

    def interrupt_running_job_runs(self, workspace_id: str | None = None) -> int: ...


@dataclass(frozen=True)
class JobResult:
    output_refs: tuple[str, ...] = ()


class JobFailure(RuntimeError):
    def __init__(self, reason: str, impact: str, repair_action: str) -> None:
        values = (reason, impact, repair_action)
        if any(not value.strip() for value in values):
            raise ValueError("任务失败必须包含原因、影响和唯一修复动作")
        super().__init__(reason)
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action


class JobCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class JobContext:
    store: JobStateStore
    workspace_id: str
    job_id: str

    def report_progress(self, progress: int) -> JobRun:
        self.raise_if_cancelled()
        return self.store.update_job_run_progress(
            self.workspace_id, self.job_id, progress
        )

    def raise_if_cancelled(self) -> None:
        if self.store.get_job_run(self.workspace_id, self.job_id).status == "cancelled":
            raise JobCancelled("任务已取消")


JobWork = Callable[[JobContext], JobResult]


class TaskRuntime:
    """Executes work while treating JobStateStore as the only state authority."""

    def __init__(self, store: JobStateStore) -> None:
        self._store = store

    def run(self, run: JobRun, work: JobWork) -> JobRun:
        persisted = self._store.create_job_run(run)
        return self.resume(persisted.workspace_id, persisted.id, work)

    def resume(self, workspace_id: str, job_id: str, work: JobWork) -> JobRun:
        current = self._store.get_job_run(workspace_id, job_id)
        if current.status in {"running", "succeeded", "failed", "cancelled"}:
            return current
        if current.status not in {"queued", "interrupted"}:
            raise ValueError(f"不支持的任务状态: {current.status}")

        try:
            claimed = self._store.claim_job_run(workspace_id, job_id)
        except ValueError:
            # An atomic claim may have been won by another executor. The store
            # remains authoritative, so this invocation must not run the work.
            current = self._store.get_job_run(workspace_id, job_id)
            if current.status in {"running", "succeeded", "failed", "cancelled"}:
                return current
            raise

        context = JobContext(self._store, claimed.workspace_id, claimed.id)
        try:
            result = work(context)
            if not isinstance(result, JobResult):
                raise TypeError("任务执行器必须返回 JobResult")
            context.raise_if_cancelled()
        except JobCancelled:
            current = self._store.get_job_run(workspace_id, job_id)
            if current.status == "cancelled":
                return current
            return self.cancel(workspace_id, job_id)
        except JobFailure as exc:
            return self._finish(
                workspace_id,
                job_id,
                status="failed",
                failure_reason=exc.reason,
                impact=exc.impact,
                repair_action=exc.repair_action,
            )
        except Exception as exc:
            reason = str(exc).strip() or type(exc).__name__
            return self._finish(
                workspace_id,
                job_id,
                status="failed",
                failure_reason=reason,
                impact="本次执行未产生可信的成功结果。",
                repair_action="修复首个失败原因后重新提交该任务。",
            )

        return self._finish(
            workspace_id,
            job_id,
            status="succeeded",
            output_refs=list(result.output_refs),
        )

    def cancel(
        self,
        workspace_id: str,
        job_id: str,
        *,
        reason: str = "user_cancelled",
        impact: str = "任务停止，未发布新产物。",
        repair_action: str = "需要时重新提交该任务。",
    ) -> JobRun:
        current = self._store.get_job_run(workspace_id, job_id)
        if current.status in {"succeeded", "failed", "cancelled"}:
            return current
        return self._finish(
            workspace_id,
            job_id,
            status="cancelled",
            failure_reason=reason,
            impact=impact,
            repair_action=repair_action,
        )

    def interrupt_running(self, workspace_id: str | None = None) -> int:
        return self._store.interrupt_running_job_runs(workspace_id)

    def get(self, workspace_id: str, job_id: str) -> JobRun:
        return self._store.get_job_run(workspace_id, job_id)

    def list(self, workspace_id: str, task_id: str) -> list[JobRun]:
        return self._store.list_job_runs(workspace_id, task_id)

    def _finish(
        self,
        workspace_id: str,
        job_id: str,
        *,
        status: TerminalJobStatus,
        output_refs: list[str] | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
    ) -> JobRun:
        try:
            return self._store.finish_job_run(
                workspace_id,
                job_id,
                status=status,
                output_refs=output_refs,
                failure_reason=failure_reason,
                impact=impact,
                repair_action=repair_action,
            )
        except ValueError:
            current = self._store.get_job_run(workspace_id, job_id)
            if current.status in {"succeeded", "failed", "cancelled", "interrupted"}:
                return current
            raise
