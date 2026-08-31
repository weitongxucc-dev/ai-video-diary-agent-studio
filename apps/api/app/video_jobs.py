from __future__ import annotations

from app.production import JobRun
from app.task_runtime import JobStateStore, JobWork, TaskRuntime


class VideoJobRunner:
    """Video-work entry point backed exclusively by persisted JobRun state."""

    def __init__(self, store: JobStateStore | None = None) -> None:
        self._runtime = TaskRuntime(store) if store is not None else None

    def run(self, run: JobRun, work: JobWork) -> JobRun:
        return self._require_runtime().run(run, work)

    def resume(self, workspace_id: str, job_id: str, work: JobWork) -> JobRun:
        return self._require_runtime().resume(workspace_id, job_id, work)

    def cancel(self, workspace_id: str, job_id: str) -> JobRun:
        return self._require_runtime().cancel(workspace_id, job_id)

    def recover_after_restart(self, workspace_id: str | None = None) -> int:
        return self._require_runtime().interrupt_running(workspace_id)

    def _require_runtime(self) -> TaskRuntime:
        if self._runtime is None:
            raise RuntimeError("VideoJobRunner 需要 JobStateStore")
        return self._runtime
