from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import Event, Lock, Thread

import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import JobRun
from app.task_runtime import JobFailure, JobResult
from app.video_jobs import VideoJobRunner


class FakeJobStateStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.runs: dict[tuple[str, str], JobRun] = {}
        self.idempotency: dict[tuple[str, str], tuple[str, str]] = {}

    def create_job_run(self, run: JobRun) -> JobRun:
        with self._lock:
            idempotency_key = (run.workspace_id, run.idempotency_key)
            existing_key = self.idempotency.get(idempotency_key)
            if existing_key is not None:
                existing = self.runs[existing_key]
                if (
                    existing.task_id != run.task_id
                    or existing.kind != run.kind
                    or existing.input_digest != run.input_digest
                ):
                    raise ValueError("幂等键已绑定到不同的任务输入")
                return existing
            key = (run.workspace_id, run.id)
            self.runs[key] = run
            self.idempotency[idempotency_key] = key
            return run

    def get_job_run(self, workspace_id: str, job_id: str) -> JobRun:
        return self.runs[(workspace_id, job_id)]

    def list_job_runs(self, workspace_id: str, task_id: str) -> list[JobRun]:
        return [
            run
            for (run_workspace_id, _), run in self.runs.items()
            if run_workspace_id == workspace_id and run.task_id == task_id
        ]

    def claim_job_run(self, workspace_id: str, job_id: str) -> JobRun:
        with self._lock:
            key = (workspace_id, job_id)
            run = self.runs[key]
            if run.status not in {"queued", "interrupted"}:
                raise ValueError("任务运行记录当前不可领取")
            claimed = replace(
                run,
                status="running",
                attempt=run.attempt + 1,
                started_at=datetime.now(UTC),
                finished_at=None,
            )
            self.runs[key] = claimed
            return claimed

    def update_job_run_progress(
        self, workspace_id: str, job_id: str, progress: int
    ) -> JobRun:
        with self._lock:
            key = (workspace_id, job_id)
            run = self.runs[key]
            if run.status != "running" or progress < run.progress or progress > 100:
                raise ValueError("任务进度无效")
            updated = replace(run, progress=progress, updated_at=datetime.now(UTC))
            self.runs[key] = updated
            return updated

    def finish_job_run(
        self,
        workspace_id: str,
        job_id: str,
        *,
        status: str,
        output_refs: list[str] | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
    ) -> JobRun:
        with self._lock:
            key = (workspace_id, job_id)
            run = self.runs[key]
            if run.status != "running" and not (
                status == "cancelled" and run.status in {"queued", "interrupted"}
            ):
                raise ValueError("任务运行记录当前不可结束")
            finished = replace(
                run,
                status=status,
                progress=100 if status == "succeeded" else run.progress,
                output_refs=output_refs or [],
                failure_reason=failure_reason,
                impact=impact,
                repair_action=repair_action,
                finished_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.runs[key] = finished
            return finished

    def interrupt_running_job_runs(self, workspace_id: str | None = None) -> int:
        changed = 0
        with self._lock:
            for key, run in list(self.runs.items()):
                if run.status != "running":
                    continue
                if workspace_id is not None and run.workspace_id != workspace_id:
                    continue
                self.runs[key] = replace(
                    run,
                    status="interrupted",
                    failure_reason="process_interrupted",
                    impact="本次执行未产生可信的成功结果。",
                    repair_action="重新领取该任务并从已持久化输入恢复。",
                )
                changed += 1
        return changed


def _run(*, job_id: str = "job_1", idempotency_key: str = "render:v1") -> JobRun:
    return JobRun(
        id=job_id,
        workspace_id="workspace_a",
        task_id="task_1",
        kind="timeline_render",
        idempotency_key=idempotency_key,
        input_digest="sha256:input",
    )


def test_video_job_runner_claims_once_reports_progress_and_reuses_success():
    store = FakeJobStateStore()
    runner = VideoJobRunner(store)
    calls: list[str] = []

    def render(context):
        calls.append(context.job_id)
        context.report_progress(40)
        return JobResult(("artifacts/task_1/preview.mp4",))

    first = runner.run(_run(), render)
    duplicate = runner.run(_run(job_id="job_duplicate"), render)

    assert calls == ["job_1"]
    assert first == duplicate
    assert first.status == "succeeded"
    assert first.progress == 100
    assert first.attempt == 1
    assert first.output_refs == ["artifacts/task_1/preview.mp4"]


def test_video_job_runner_interrupts_then_resumes_with_next_attempt():
    store = FakeJobStateStore()
    runner = VideoJobRunner(store)
    store.create_job_run(_run())
    store.claim_job_run("workspace_a", "job_1")

    assert runner.recover_after_restart() == 1
    recovered = runner.resume(
        "workspace_a", "job_1", lambda context: JobResult(("outputs/final.mp4",))
    )

    assert recovered.status == "succeeded"
    assert recovered.attempt == 2
    assert recovered.output_refs == ["outputs/final.mp4"]


def test_video_job_runner_persists_cancellation_and_first_real_failure():
    store = FakeJobStateStore()
    runner = VideoJobRunner(store)
    started = Event()
    release = Event()
    outcomes: list[JobRun] = []

    def wait_for_cancellation(context):
        started.set()
        release.wait(timeout=2)
        context.raise_if_cancelled()
        return JobResult()

    worker = Thread(
        target=lambda: outcomes.append(
            runner.run(
                _run(job_id="cancelled", idempotency_key="cancel:v1"),
                wait_for_cancellation,
            )
        )
    )
    worker.start()
    assert started.wait(timeout=2)

    cancelled = runner.cancel("workspace_a", "cancelled")
    release.set()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert outcomes == [cancelled]
    assert cancelled.status == "cancelled"
    assert cancelled.failure_reason == "user_cancelled"

    def fail(context):
        context.report_progress(25)
        raise JobFailure(
            "ffmpeg exited with code 1",
            "预览产物不可用。",
            "修正输入编码后重新提交。",
        )

    failed = runner.run(_run(job_id="failed", idempotency_key="fail:v1"), fail)
    repeated = runner.resume("workspace_a", "failed", lambda context: JobResult())

    assert repeated == failed
    assert failed.status == "failed"
    assert failed.progress == 25
    assert failed.failure_reason == "ffmpeg exited with code 1"
    assert failed.impact == "预览产物不可用。"
    assert failed.repair_action == "修正输入编码后重新提交。"


def test_artifact_store_atomically_publishes_digest_and_logical_reference(tmp_path):
    store = ArtifactStore(tmp_path / "workspace-files")
    content = b"real rendered bytes"

    published = store.publish_bytes("artifacts/task_1/preview.mp4", content)
    repeated = store.publish_bytes("artifacts/task_1/preview.mp4", content)
    recovered = store.describe("artifacts/task_1/preview.mp4")

    assert published == repeated == recovered
    assert published.logical_ref == "artifacts/task_1/preview.mp4"
    assert published.sha256 == "f3ce7163ff8025392d4a39ea3828d29148ea42ca33e77a1ff7f8e4b81eaf87f8"
    assert published.size_bytes == len(content)
    assert (tmp_path / "workspace-files" / published.logical_ref).read_bytes() == content
    assert list((tmp_path / "workspace-files").rglob("*.tmp")) == []


def test_artifact_store_rejects_escape_and_logical_ref_overwrite(tmp_path):
    store = ArtifactStore(tmp_path / "workspace-files")

    with pytest.raises(ValueError, match="受控目录"):
        store.publish_bytes("../outside.mp4", b"outside")

    store.publish_bytes("outputs/final.mp4", b"version one")
    with pytest.raises(FileExistsError, match="不同内容"):
        store.publish_bytes("outputs/final.mp4", b"version two")

    assert (tmp_path / "workspace-files" / "outputs/final.mp4").read_bytes() == b"version one"


def test_artifact_store_does_not_recreate_disconnected_root_and_recovers(tmp_path):
    root = tmp_path / "workspace-files"
    disconnected = tmp_path / "disconnected"
    store = ArtifactStore(root)
    store.publish_bytes("outputs/existing.mp4", b"original")
    root.rename(disconnected)

    with pytest.raises(OSError, match="断开或被替换"):
        store.publish_bytes("outputs/new.mp4", b"new")
    assert not root.exists()
    assert (disconnected / "outputs/existing.mp4").read_bytes() == b"original"

    root.mkdir()
    (root / "outputs").mkdir()
    (root / "outputs/existing.mp4").write_bytes(b"different storage")
    with pytest.raises(OSError, match="断开或被替换"):
        store.resolve_file("outputs/existing.mp4")
    with pytest.raises(OSError, match="断开或被替换"):
        store.publish_bytes("outputs/new.mp4", b"new")
    root.rename(tmp_path / "replacement")
    disconnected.rename(root)
    store.publish_bytes("outputs/new.mp4", b"new")
    assert store.resolve_file("outputs/existing.mp4").read_bytes() == b"original"


def test_artifact_store_refuses_root_swapped_during_render_and_preserves_replacement(tmp_path):
    root = tmp_path / "workspace-files"
    store = ArtifactStore(root)

    def write_then_swap(target):
        target.write(b"rendered")
        relative = next(root.rglob("*.tmp")).relative_to(root)
        root.rename(tmp_path / "detached")
        replacement = root / relative
        replacement.parent.mkdir(parents=True)
        replacement.write_bytes(b"belongs to replacement storage")

    with pytest.raises(OSError, match="断开或被替换"):
        store.publish("outputs/new.mp4", write_then_swap)
    assert not (root / "outputs/new.mp4").exists()
    assert next(root.rglob("*.tmp")).read_bytes() == b"belongs to replacement storage"
