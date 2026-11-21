from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.creation.contracts import CreationTask, TimelineVersion
from app.creation.plan_contracts import CreationPlan
from app.media_execution import (
    FFMPEG_OPERATION,
    EditPlan,
    MediaExecutionLayer,
    MediaOperation,
    TaskMediaExecutionDelegate,
    build_media_execution_entrypoint,
)
from app.media_execution.adapter import FfmpegAdapter
from app.media_execution.contracts import MediaExecutionError
from app.production import ProductionRepository


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

        def execute(self, plan, job_id, output_root, *, progress=None):
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
