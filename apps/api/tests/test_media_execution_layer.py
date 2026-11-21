from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from app.creation.contracts import CreationTask, JobRun, TaskEvent, TimelineVersion
from app.creation.plan_contracts import CreationPlan
from app.media_execution import (
    FFMPEG_OPERATION,
    EditPlan,
    MediaExecutionCancelled,
    MediaExecutionLayer,
    MediaOperation,
    TaskMediaExecutionDelegate,
    build_media_execution_entrypoint,
)
from app.media_execution.adapter import FfmpegAdapter, PreparedCommand
from app.media_execution.capabilities import ToolDependency, detect_dependencies
from app.media_execution.contracts import MediaExecutionError
from app.media_execution.worker import FfmpegWorker
from app.production import ProductionRepository
from app.task_audio import TaskMediaJobs


def _case(tmp_path: Path):
    repo = ProductionRepository(tmp_path / "media.sqlite3", tmp_path / "library")
    plan = CreationPlan(
        id="plan", workspace_id="local", goal="媒体执行", name="媒体执行", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=2,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="test",
    )
    task = CreationTask("task", "local", plan.id, "媒体执行", "knowledge_explanation", owner_user_id="member")
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    base = TimelineVersion("base", "local", "task", None, 1, {"montage": {"clips": []}}, None)
    repo.timeline_records.create_timeline_version(base)
    return repo, base


def _make_media(root: Path) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    first, second, audio = root / "first.mp4", root / "second.mp4", root / "voice.wav"
    for target, color, frequency in ((first, "red", 440), (second, "blue", 660)):
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=10",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=8000",
            "-t", "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(target),
        ], check=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=330:sample_rate=8000", "-t", "1", str(audio),
    ], check=True)
    return first, second, audio


def test_repository_exposes_its_connection_factory_for_composed_services(tmp_path):
    repo, _ = _case(tmp_path)

    assert repo.connection_factory.__func__ is repo._connect.__func__
    with repo.connection_factory() as connection:
        assert connection.row_factory is not None
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_real_media_chain_persists_job_artifacts_timeline_and_request_log(tmp_path):
    repo, base = _case(tmp_path)
    first, second, audio = _make_media(tmp_path / "inputs")
    plan = EditPlan(
        "local", "task", base.id, "req-real-chain", (
            MediaOperation("probe", (str(first),), "probe.json"),
            MediaOperation("look", (str(first),), "frame.png", {"frame_time": 0.2, "width": 160, "height": 120}),
            MediaOperation("cut", (str(first),), "cut.mp4", {"start": 0, "duration": 0.4}),
            MediaOperation("join", ("$previous", str(second)), "joined.mp4"),
            MediaOperation("audio", ("$previous", str(audio)), "mixed.mp4"),
            MediaOperation("fit", ("$previous",), "fit.mp4", {"width": 180, "height": 320}),
            MediaOperation("export", ("$previous",), "final.mp4", {"width": 180, "height": 320}),
        ),
    )
    result = MediaExecutionLayer(repo, tmp_path / "outputs", connection_factory=repo._connect).submit(plan)
    assert result["status"] == "succeeded"
    assert len(result["output_refs"]) == 7
    assert repo.job_records.get_job_run("local", result["job_id"]).status == "succeeded"
    projection = MediaExecutionLayer(repo, tmp_path / "outputs", connection_factory=repo._connect).read("local", result["job_id"])
    assert projection.request_id == "req-real-chain"
    assert projection.timeline_version_id == result["timeline_version_id"]
    version = repo.timeline_records.get_timeline_version("local", result["timeline_version_id"])
    assert version.parent_version_id == base.id
    assert version.snapshot["media_execution"]["request_id"] == "req-real-chain"
    log_path = Path(result["log_path"])
    assert log_path.exists()
    assert all(json.loads(line)["request_id"] == "req-real-chain" for line in log_path.read_text().splitlines())
    assert repo.execution_records.list_artifact_records("local", "task")


def test_published_media_output_remains_current_after_advancing_task_version(tmp_path):
    repo, base = _case(tmp_path)
    output = tmp_path / "inputs" / "source.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"media")
    plan = EditPlan("local", "task", base.id, "req-readable-output", (MediaOperation("export", (str(output),), "final.mp4"),))
    result = MediaExecutionLayer(repo, tmp_path / "outputs", worker=type("Worker", (), {
        "execute": lambda self, plan, job_id, output_root, *, progress=None: {
            "outputs": [{"path": str(output), "operation": "export", "output_name": "final.mp4", "summary": {"size_bytes": 5}}],
            "log_path": str(output.with_suffix(".jsonl")), "request_id": plan.request_id,
        },
    })(), connection_factory=repo._connect).submit(plan)
    outer_job = JobRun("outer-job", "local", "task", "task_media", "outer-key", "outer-digest")
    event = {"operation": "ffmpeg_execution", "result_kind": "media_output", "arguments": {"edit_plan": plan.as_dict()}}
    repo.job_records.create_job_run(outer_job)
    repo.job_records.claim_job_run("local", "outer-job")
    repo.job_records.finish_job_run("local", "outer-job", status="succeeded")
    repo.execution_records.append_task_event(TaskEvent(
        "event_outer-job_result", "local", "task", "outer-job", "task_media_result", {"result": result}
    ))
    media = TaskMediaJobs(repo, facts=lambda *args: ({}, {}), verify_identity=lambda actor: "ready")
    assert media._check_media_output_result(outer_job, event) == "ready"


def test_failure_is_persisted_and_retry_uses_original_request_id(tmp_path):
    repo, base = _case(tmp_path)
    missing = tmp_path / "missing.mp4"
    plan = EditPlan("local", "task", base.id, "req-failure", (MediaOperation("cut", (str(missing),), "out.mp4", {"duration": 1}),))
    failed = MediaExecutionLayer(repo, tmp_path / "outputs", connection_factory=repo._connect).submit(plan)
    assert failed["status"] == "failed"
    job = repo.job_records.get_job_run("local", failed["job_id"])
    assert job.failure_reason in {"media_command_failed", "media_output_missing"}
    assert job.repair_action


def test_capability_registry_keeps_unimplemented_external_tools_explicit(tmp_path):
    repo, _ = _case(tmp_path)
    layer = MediaExecutionLayer(repo, tmp_path / "outputs", connection_factory=repo._connect)
    values = {item["name"]: item for item in layer.capabilities()["capabilities"]}
    assert values["probe"]["implementation"] == "曜核媒体适配器"
    assert values["multicam"]["implementation"] == "registered_pending_adapter"
    assert values["multicam"]["status"] == "missing"


def test_retry_creates_a_new_job_but_keeps_the_original_request_id(tmp_path):
    repo, base = _case(tmp_path)

    class Worker:
        attempts = 0

        def execute(self, plan, job_id, output_root, *, progress=None, cancel_event=None):
            self.attempts += 1
            if self.attempts == 1:
                raise MediaExecutionError("temporary_media_failure", "没有发布新版本。", "沿原请求重试。")
            path = output_root / job_id / "final.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"media")
            return {"outputs": [{"path": str(path), "operation": "export", "output_name": "final.mp4", "summary": {"size_bytes": 5}}],
                    "log_path": str(path.with_suffix(".jsonl")), "request_id": plan.request_id}

    layer = MediaExecutionLayer(repo, tmp_path / "outputs", worker=Worker(), connection_factory=repo._connect)
    plan = EditPlan("local", "task", base.id, "req-retry", (MediaOperation("export", ("input.mp4",), "final.mp4"),))
    failed = layer.submit(plan)
    retried = layer.retry(failed["job_id"])
    assert failed["status"] == "failed"
    assert retried["status"] == "succeeded"
    assert retried["request_id"] == "req-retry"
    assert retried["job_id"] != failed["job_id"]


def test_repeated_submit_reuses_the_persisted_idempotent_job(tmp_path):
    repo, base = _case(tmp_path)

    class Worker:
        calls = 0

        def execute(self, plan, job_id, output_root, *, progress=None, cancel_event=None):
            self.calls += 1
            path = output_root / job_id / "final.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"media")
            return {"outputs": [{"path": str(path), "operation": "export", "output_name": "final.mp4", "summary": {"size_bytes": 5}}],
                    "log_path": str(path.with_suffix(".jsonl")), "request_id": plan.request_id}

    worker = Worker()
    layer = MediaExecutionLayer(repo, tmp_path / "outputs", worker=worker, connection_factory=repo._connect)
    plan = EditPlan("local", "task", base.id, "req-idempotent", (MediaOperation("export", ("input.mp4",), "final.mp4"),))

    first = layer.submit(plan)
    second = layer.submit(plan)

    assert first["status"] == "succeeded"
    assert second == first
    assert worker.calls == 1
    assert len(repo.job_records.list_job_runs("local", "task")) == 1


def test_caption_adapter_is_business_level_and_keeps_ffmpeg_flags_internal(tmp_path):
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:00,400\n你好\n", encoding="utf-8")
    prepared = FfmpegAdapter("ffmpeg", "ffprobe").prepare(
        MediaOperation("caption", ("input.mp4",), "captioned.mp4", {"subtitle_path": str(subtitle)}),
        tmp_path / "captioned.mp4",
    )
    assert prepared.operation == "caption"
    assert "subtitles=" in " ".join(prepared.command)


def test_entrypoint_requires_explicit_connection_boundary(tmp_path):
    repo, _ = _case(tmp_path)
    entrypoint = build_media_execution_entrypoint(
        repository=repo, output_root=tmp_path / "outputs", connection_factory=repo._connect)
    assert entrypoint.capabilities()["dependencies"]["ffmpeg"]["status"] == "available"


def test_missing_caption_dependency_is_persisted_as_unavailable(tmp_path):
    repo, base = _case(tmp_path)
    layer = MediaExecutionLayer(repo, tmp_path / "outputs", connection_factory=repo._connect)
    caption_file = tmp_path / "captions.srt"
    caption_file.write_text("1\n00:00:00,000 --> 00:00:00,400\n你好\n", encoding="utf-8")
    plan = EditPlan("local", "task", base.id, "req-caption-unavailable", (
        MediaOperation("caption", (str(tmp_path / "input.mp4"),), "captioned.mp4", {"subtitle_path": str(caption_file)}),
    ))
    result = layer.submit(plan)
    dependencies = layer.capabilities()["dependencies"]["ffmpeg"]["features"]
    if "subtitles" not in dependencies and "drawtext" not in dependencies:
        assert result["status"] == "failed"
        assert result["failure_reason"] == "caption_dependency_missing"


def test_task_media_delegate_preserves_success_and_input_version(tmp_path):
    class EntryPoint:
        def submit(self, plan):
            return {"status": "succeeded", "job_id": "media-job", "request_id": plan.request_id,
                    "timeline_version_id": "next", "output_refs": ["media/job/final.mp4"], "log_path": "job.jsonl"}

    event = {"workspace_id": "local", "task_id": "task", "arguments": {"edit_plan": {
        "workspace_id": "local", "task_id": "task", "base_version_id": "base", "request_id": "req",
        "member_id": "member", "operations": [{"operation": "export", "inputs": ["input.mp4"], "output_name": "final.mp4"}],
    }}, "input_facts": {"base_version_id": "base"}}
    delegate = TaskMediaExecutionDelegate(EntryPoint())
    result = delegate.submit({"id": "outer-job"}, event, {"member_id": "member"})
    assert result["status"] == "succeeded"
    assert result["base_version_id"] == "base"
    assert result["output_refs"] == ["media/job/final.mp4"]
    assert delegate.handles(FFMPEG_OPERATION, event["arguments"])


def test_task_media_delegate_persists_caption_dependency_failure_without_success_fields():
    class EntryPoint:
        def submit(self, plan):
            return {"status": "failed", "request_id": plan.request_id, "failure_reason": "caption_dependency_missing",
                    "impact": "字幕未生成", "repair_action": "安装字幕滤镜后重试", "log_path": "failed.jsonl"}

    event = {"workspace_id": "local", "task_id": "task", "arguments": {"edit_plan": {
        "workspace_id": "local", "task_id": "task", "base_version_id": "base", "request_id": "req-caption",
        "operations": [{"operation": "caption", "inputs": ["input.mp4"], "output_name": "captioned.mp4",
                         "options": {"subtitle_path": "captions.srt"}}],
    }}, "input_facts": {"base_version_id": "base"}}
    result = TaskMediaExecutionDelegate(EntryPoint()).submit({"id": "outer-job"}, event, {})
    assert result["status"] == "failed"
    assert result["failure_reason"] == "caption_dependency_missing"
    assert result["timeline_version_id"] is None
    assert result["output_refs"] == []


def test_task_media_delegate_rejects_failure_without_reason_and_resume_keeps_base_version():
    class EntryPoint:
        def resume(self, job_id, workspace_id):
            return {"status": "failed", "request_id": "req", "failure_reason": "timeline_version_conflict",
                    "impact": "未发布", "repair_action": "重新读取版本"}

    event = {"workspace_id": "local", "task_id": "task", "arguments": {"edit_plan": {
        "workspace_id": "local", "task_id": "task", "base_version_id": "base", "request_id": "req",
        "operations": [{"operation": "export", "inputs": ["input.mp4"], "output_name": "final.mp4"}],
    }}, "input_facts": {"base_version_id": "base"}}
    result = TaskMediaExecutionDelegate(EntryPoint()).resume({"id": "outer-job"}, event, {})
    assert result["failure_reason"] == "timeline_version_conflict"
    assert result["base_version_id"] == "base"


def test_task_media_delegate_resume_and_retry_accept_persisted_jobrun():
    calls = []

    class EntryPoint:
        def resume(self, job_id, workspace_id):
            calls.append(("resume", job_id, workspace_id))
            return {"status": "failed", "request_id": "req", "failure_reason": "interrupted",
                    "impact": "未发布", "repair_action": "恢复后重试"}

        def retry(self, job_id, workspace_id):
            calls.append(("retry", job_id, workspace_id))
            return {"status": "failed", "request_id": "req", "failure_reason": "retry_failed",
                    "impact": "未发布", "repair_action": "修复后重试"}

    event = {"workspace_id": "local", "task_id": "task", "arguments": {"edit_plan": {
        "workspace_id": "local", "task_id": "task", "base_version_id": "base", "request_id": "req",
        "operations": [{"operation": "export", "inputs": ["input.mp4"], "output_name": "final.mp4"}],
    }}, "input_facts": {"base_version_id": "base"}}
    job = JobRun("outer-job", "local", "task", "task_media", "outer-key", "digest", status="interrupted")
    delegate = TaskMediaExecutionDelegate(EntryPoint())

    resumed = delegate.resume(job, event, {})
    retried = delegate.retry(job, event, {})

    assert resumed["failure_reason"] == "interrupted"
    assert retried["failure_reason"] == "retry_failed"
    assert calls == [("resume", "outer-job", "local"), ("retry", "outer-job", "local")]


def test_task_media_delegate_cancel_and_retry_keep_outer_input_version_and_request():
    class EntryPoint:
        def cancel(self, job_id, workspace_id):
            return {"status": "cancelled", "job_id": job_id, "request_id": "req",
                    "failure_reason": "media_cancelled", "impact": "未发布", "repair_action": "沿原请求重试",
                    "log_path": "cancelled.jsonl"}

        def retry(self, job_id, workspace_id):
            return {"status": "succeeded", "job_id": "new-job", "request_id": "req",
                    "timeline_version_id": "next", "output_refs": ["media/new/final.mp4"],
                    "log_path": "retry.jsonl"}

    event = {"workspace_id": "local", "task_id": "task", "arguments": {"edit_plan": {
        "workspace_id": "local", "task_id": "task", "base_version_id": "base", "request_id": "req",
        "operations": [{"operation": "export", "inputs": ["input.mp4"], "output_name": "final.mp4"}],
    }}, "input_facts": {"base_version_id": "base"}}
    delegate = TaskMediaExecutionDelegate(EntryPoint())

    cancelled = delegate.cancel({"id": "outer-job"}, event, {})
    retried = delegate.retry({"id": "outer-job"}, event, {})

    assert cancelled == {
        "status": "cancelled", "request_id": "req", "base_version_id": "base", "timeline_version_id": None,
        "output_refs": [], "log_path": "cancelled.jsonl", "failure_reason": "media_cancelled",
        "impact": "未发布", "repair_action": "沿原请求重试", "job_id": "outer-job",
    }
    assert retried["status"] == "succeeded"
    assert retried["job_id"] == "new-job"
    assert retried["base_version_id"] == "base"
    assert retried["request_id"] == "req"


def _wait_for_log(path: Path, event: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.is_file():
            for line in path.read_text().splitlines(keepends=True):
                if line.endswith("\n"):
                    item = json.loads(line)
                    if item["event"] == event:
                        return item
        time.sleep(0.02)
    pytest.fail(f"{path} 缺少 {event} 事件")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_cancel_releases_leader_and_child(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = """
import pathlib, signal, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
signal.signal(signal.SIGTERM, lambda *_: sys.exit(child.wait()))
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
child.wait()
"""
    dependencies = {name: ToolDependency(name, sys.executable, "available") for name in ("ffmpeg", "ffprobe")}
    worker = FfmpegWorker(dependencies, timeout_seconds=10)

    class ChildAdapter:
        def prepare(self, operation, output_path):
            command = (sys.executable, "-c", script, str(child_pid_file))
            return PreparedCommand(operation.operation, command, output_path, (), "media_file")

    worker.adapter = ChildAdapter()
    plan = EditPlan("local", "task", "base", "req-group", (MediaOperation("export", (), "final.mp4"),))
    cancel = Event()
    log_path = tmp_path / "group-job" / "request-req-group.jsonl"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.execute, plan, "group-job", tmp_path, cancel_event=cancel)
        try:
            started = _wait_for_log(log_path, "process_started")
            deadline = time.monotonic() + 5
            while not child_pid_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            child_pid = int(child_pid_file.read_text())
            assert os.getpgid(child_pid) == started["process_group_id"]
        finally:
            cancel.set()
        with pytest.raises(MediaExecutionCancelled):
            future.result(timeout=5)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(started["process_group_id"], 0)


class SlowFirstExport(FfmpegAdapter):
    def __init__(self, adapter):
        super().__init__(adapter.ffmpeg, adapter.ffprobe)
        self.attempts = 0

    def prepare(self, operation, output_path):
        command = super().prepare(operation, output_path)
        self.attempts += 1
        if self.attempts != 1:
            return command
        input_index = command.command.index("-i")
        return replace(command, command=(
            *command.command[:input_index], "-stream_loop", "20", "-re", *command.command[input_index:],
        ))


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_running_ffmpeg_cancel_persists_and_retry_starts_fresh(tmp_path):
    repo, base = _case(tmp_path)
    first, _, _ = _make_media(tmp_path / "inputs")
    worker = FfmpegWorker(detect_dependencies(), timeout_seconds=15)
    worker.adapter = SlowFirstExport(worker.adapter)
    outputs = tmp_path / "outputs"
    layer = MediaExecutionLayer(repo, outputs, worker=worker, connection_factory=repo._connect)
    plan = EditPlan("local", "task", base.id, "req-cancel", (
        MediaOperation("export", (str(first),), "final.mp4", {"width": 160, "height": 120}),
    ))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(layer.submit, plan)
        deadline = time.monotonic() + 5
        jobs = repo.job_records.list_job_runs("local", "task")
        while not jobs and time.monotonic() < deadline:
            time.sleep(0.02)
            jobs = repo.job_records.list_job_runs("local", "task")
        job_id = jobs[0].id
        log_path = outputs / job_id / "request-req-cancel.jsonl"
        started = _wait_for_log(log_path, "process_started")
        assert os.getpgid(started["pid"]) == started["process_group_id"]
        assert layer.cancel(job_id, "local")["status"] == "cancellation_requested"
        result = future.result(timeout=5)
    assert result["status"] == "cancelled"
    assert _wait_for_log(log_path, "command_end")["reason"] == "media_cancelled"
    with pytest.raises(ProcessLookupError):
        os.killpg(started["process_group_id"], 0)

    reopened = MediaExecutionLayer(repo, outputs, worker=worker, connection_factory=repo._connect)
    receipt = reopened.read("local", job_id)
    assert (receipt.status, receipt.request_id, receipt.log_path) == ("cancelled", plan.request_id, str(log_path))
    assert not receipt.output_refs and receipt.timeline_version_id is None
    assert reopened.cancel(job_id, "local")["status"] == "cancelled"
    assert repo.job_records.get_job_run("local", job_id).status == "cancelled"
    event = repo.execution_records.get_task_event("local", f"event_{job_id}_media_cancelled")
    assert event.payload["status"] == "cancelled"
    assert event.payload["base_version_id"] == base.id
    assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == base.id
    assert not repo.execution_records.list_artifact_records("local", "task")

    retried = reopened.retry(job_id, "local")
    assert retried["job_id"] != job_id and retried["request_id"] == plan.request_id
    assert retried["status"] == "succeeded"
    version = repo.timeline_records.get_timeline_version("local", retried["timeline_version_id"])
    assert version.parent_version_id == base.id
    assert reopened.read("local", job_id) == receipt
    assert worker.adapter.attempts == 2
    retry_log = Path(retried["log_path"])
    retry_started = _wait_for_log(retry_log, "process_started")
    assert retry_started["process_group_id"] != started["process_group_id"]
    assert (retry_log.parent / "final.mp4").stat().st_size > 0


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_ffmpeg_timeout_releases_process_and_preserves_failure_reason(tmp_path):
    repo, base = _case(tmp_path)
    first, _, _ = _make_media(tmp_path / "inputs")
    worker = FfmpegWorker(detect_dependencies(), timeout_seconds=0.5)
    worker.adapter = SlowFirstExport(worker.adapter)
    layer = MediaExecutionLayer(repo, tmp_path / "outputs", worker=worker, connection_factory=repo._connect)
    plan = EditPlan("local", "task", base.id, "req-timeout", (
        MediaOperation("export", (str(first),), "final.mp4", {"width": 160, "height": 120}),
    ))
    result = layer.submit(plan)
    assert result["status"] == "failed"
    assert layer.read("local", result["job_id"]).failure_reason == "media_command_timeout"
    log_path = Path(result["log_path"])
    started = _wait_for_log(log_path, "process_started")
    assert _wait_for_log(log_path, "command_end")["reason"] == "media_command_timeout"
    with pytest.raises(ProcessLookupError):
        os.killpg(started["process_group_id"], 0)
