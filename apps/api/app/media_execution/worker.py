from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path

from .adapter import FfmpegAdapter
from .capabilities import ToolDependency
from .contracts import (
    EditPlan,
    MediaExecutionCancelled,
    MediaExecutionError,
    MediaProcessReleaseUnverified,
)


def _write_log(path: Path, identity: dict, event: str, **fields) -> None:
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({**identity, "event": event, "timestamp": time.time(), **fields},
                             ensure_ascii=False) + "\n")


class FfmpegWorker:
    def __init__(self, dependencies: dict[str, ToolDependency], *, timeout_seconds: float = 1800) -> None:
        self.dependencies = dependencies
        self.timeout_seconds = timeout_seconds
        missing = [name for name in ("ffmpeg", "ffprobe") if dependencies[name].status != "available"]
        if missing:
            raise MediaExecutionError("ffmpeg_dependency_unavailable", "媒体执行未开始。",
                                      f"修复并重新检测依赖: {', '.join(missing)}")
        self.adapter = FfmpegAdapter(dependencies["ffmpeg"].path, dependencies["ffprobe"].path)

    def execute(self, plan: EditPlan, job_id: str, output_root: Path,
                *, progress: Callable[[int], None] | None = None,
                cancel_event: threading.Event | None = None) -> dict[str, object]:
        job_root = output_root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        log_path = job_root / f"request-{plan.request_id}.jsonl"
        current_outputs: list[dict[str, object]] = []
        total = len(plan.operations)
        for index, operation in enumerate(plan.operations, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise MediaExecutionCancelled()
            if operation.operation.lower() in {"caption", "subtitles"}:
                available_filters = set(self.dependencies["ffmpeg"].features)
                if not ({"subtitles", "drawtext"} & available_filters):
                    raise MediaExecutionError(
                        "caption_dependency_missing",
                        "当前 FFmpeg 构建缺少字幕滤镜，未生成或发布字幕输出。",
                        "安装带 subtitles 或 drawtext 滤镜的 FFmpeg 构建后沿原请求重试。",
                    )
            is_probe = operation.operation.lower() == "probe" or (
                operation.operation.lower() == "look" and operation.options.get("frame_time") is None)
            output_name = operation.output_name or (f"step-{index}.json" if is_probe else (
                f"step-{index}.png" if operation.operation.lower() == "look" else f"step-{index}.mp4"))
            output_path = (job_root / output_name).resolve()
            if output_path.parent != job_root.resolve() and job_root.resolve() not in output_path.parents:
                raise MediaExecutionError("output_path_outside_job", "输出路径不在媒体作业目录内。", "使用相对输出名称重新提交媒体作业。")
            resolved_inputs = []
            for item in operation.inputs:
                if item in {"$previous", "$prev"}:
                    if not current_outputs:
                        raise MediaExecutionError("previous_output_missing", "媒体操作引用了不存在的前一步输出。", "调整 EditPlan 的操作顺序后重试。")
                    resolved_inputs.append(Path(current_outputs[-1]["path"]).resolve())
                else:
                    resolved_inputs.append(Path(item).expanduser().resolve())
            inputs = tuple(resolved_inputs)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path in inputs:
                raise MediaExecutionError("source_overwrite_refused", "原始输入未被覆盖，媒体作业未发布。", "为输出指定不同于输入的目标文件。")
            log = partial(_write_log, log_path, {
                "request_id": plan.request_id, "job_id": job_id,
                "operation": operation.operation, "index": index,
            })
            log("command_start", inputs=[str(item) for item in inputs], output=str(output_path))
            prepared = self.adapter.concat(inputs, output_path, job_root / f"step-{index}.concat.txt") if operation.operation.lower() == "join" else self.adapter.prepare(
                operation.__class__(operation=operation.operation, inputs=tuple(str(item) for item in inputs),
                                    output_name=operation.output_name, options=operation.options), output_path)
            try:
                completed = self._run_process(prepared.command, cancel_event, log)
            except MediaProcessReleaseUnverified as exc:
                # A log write failure must not turn an unverified release into a retryable failure.
                with suppress(OSError):
                    log("command_release_unverified", reason=exc.reason)
                raise
            except MediaExecutionError as exc:
                log("command_end", reason=exc.reason)
                raise
            log("command_end", returncode=completed.returncode, stderr=completed.stderr[-4000:])
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "ffmpeg 返回非零状态").strip()[-1000:]
                raise MediaExecutionError("media_command_failed", f"媒体操作 {operation.operation} 执行失败。",
                                          f"修复媒体输入或参数后沿原请求重试。详情: {detail}")
            if prepared.logical_kind == "probe_json":
                value = self.adapter.parse_probe(completed.stdout)
                output_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                summary = {"streams": len(value.get("streams", [])), "format": value.get("format", {})}
            else:
                if not output_path.exists() or output_path.stat().st_size == 0:
                    raise MediaExecutionError("media_output_missing", "FFmpeg 未产生可信输出。", "检查编码器和输出目录后沿原请求重试。")
                summary = {"size_bytes": output_path.stat().st_size}
                if prepared.logical_kind == "video_cut":
                    summary.update(self._validate_video_cut(
                        output_path, float(operation.options["duration"]), cancel_event, log))
            current_outputs.append({"path": str(output_path), "operation": operation.operation,
                                    "output_name": output_name, "summary": summary})
            if progress is not None:
                progress(int(index * 100 / total))
        return {"outputs": current_outputs, "log_path": str(log_path), "request_id": plan.request_id}

    def _validate_video_cut(self, path: Path, duration: float, cancel_event, log) -> dict:
        probe = self._run_process(self.adapter.cut_probe_command(path), cancel_event, log)
        if probe.returncode != 0:
            raise MediaExecutionError("media_cut_probe_failed", "截取文件的画面无法核验，未发布。",
                                      "检查源片和编码器后查询原截取作业。")
        media = self.adapter.parse_probe(probe.stdout)
        video = next(iter(media["streams"]), {})
        frame_count = str(video.get("nb_read_frames", ""))
        if not frame_count.isdecimal() or int(frame_count) == 0:
            raise MediaExecutionError("media_cut_no_video_frames", "所选区间没有可用视频帧，未发布截取文件。",
                                      "调整区间以包含源片画面后重新截取。")
        try:
            actual_duration = float(media["format"]["duration"])
            video_start = float(video["start_time"])
            video_end = video_start + float(video["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaExecutionError("media_cut_duration_unverified", "截取文件缺少可核验的画面时长，未发布。",
                                      "检查源片时间戳后查询原截取作业。") from exc
        # Allow container and audio-sample rounding, while checking the actual
        # video stream as well as the container clock.
        tolerance = 0.01
        if (video.get("codec_name") != "h264"
                or not math.isclose(actual_duration, duration, rel_tol=0, abs_tol=tolerance)
                or not (-tolerance <= video_start < video_end <= duration + tolerance)):
            raise MediaExecutionError("media_cut_range_mismatch", "截取文件的画面或时长与请求区间不一致，未发布。",
                                      "检查源片时间戳和截取范围后查询原作业。")
        facts = {"video_frames": int(frame_count), "duration_seconds": actual_duration,
                 "video_start_seconds": video_start, "video_end_seconds": video_end}
        log("video_cut_verified", **facts)
        return facts

    def _run_process(self, command: tuple[str, ...], cancel_event: threading.Event | None,
                     log: Callable[..., None]) -> subprocess.CompletedProcess[str]:
        if os.name != "posix":
            raise MediaExecutionError(
                "media_process_group_unsupported", "当前平台缺少媒体进程组管理，未启动媒体命令。",
                "请使用支持进程组清理的媒体运行环境。",
            )
        if cancel_event is not None and cancel_event.is_set():
            raise MediaExecutionCancelled()
        process_env = dict(os.environ)
        process_env.pop("FFREPORT", None)
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", env=process_env, start_new_session=True,
        )
        deadline = time.monotonic() + self.timeout_seconds
        try:
            log("process_started", pid=process.pid, process_group_id=process.pid)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise MediaExecutionCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MediaExecutionError(
                        "media_command_timeout", "媒体命令超时，未发布新版本。",
                        "检查输入规模或设备资源后沿原请求重试。",
                    )
                try:
                    stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            try:
                self._release_process_group(process)
            finally:
                process.stdout.close()
                process.stderr.close()

    @staticmethod
    def _release_process_group(process: subprocess.Popen[str]) -> None:
        # The group may outlive the leader, even after communicate() returns.
        try:
            for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 2.0)):
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, sig)
                deadline = time.monotonic() + grace
                while True:
                    process.poll()
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        process.wait(timeout=grace)
                        return
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MediaProcessReleaseUnverified() from exc
        raise MediaProcessReleaseUnverified()
