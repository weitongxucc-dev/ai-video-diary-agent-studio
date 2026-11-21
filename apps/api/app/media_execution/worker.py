from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from .adapter import FfmpegAdapter
from .capabilities import ToolDependency
from .contracts import EditPlan, MediaExecutionError


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
                *, progress: Callable[[int], None] | None = None) -> dict[str, object]:
        job_root = output_root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        log_path = job_root / f"request-{plan.request_id}.jsonl"
        current_outputs: list[dict[str, object]] = []
        total = len(plan.operations)
        for index, operation in enumerate(plan.operations, start=1):
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
            started = time.time()
            with log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"request_id": plan.request_id, "job_id": job_id, "event": "command_start",
                                      "operation": operation.operation, "index": index, "timestamp": started,
                                      "inputs": [str(item) for item in inputs], "output": str(output_path)}, ensure_ascii=False) + "\n")
            prepared = self.adapter.concat(inputs, output_path, job_root / f"step-{index}.concat.txt") if operation.operation.lower() == "join" else self.adapter.prepare(
                operation.__class__(operation=operation.operation, inputs=tuple(str(item) for item in inputs),
                                    output_name=operation.output_name, options=operation.options), output_path)
            try:
                process_env = dict(os.environ)
                process_env.pop("FFREPORT", None)
                completed = subprocess.run(prepared.command, capture_output=True, text=True, timeout=self.timeout_seconds,
                                           check=False, env=process_env)
            except subprocess.TimeoutExpired as exc:
                raise MediaExecutionError("media_command_timeout", "媒体命令超时，未发布新版本。", "检查输入规模或设备资源后沿原请求重试。") from exc
            finished = time.time()
            with log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"request_id": plan.request_id, "job_id": job_id, "event": "command_end",
                                      "operation": operation.operation, "index": index, "timestamp": finished,
                                      "returncode": completed.returncode, "stderr": completed.stderr[-4000:]}, ensure_ascii=False) + "\n")
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
            current_outputs.append({"path": str(output_path), "operation": operation.operation,
                                    "output_name": output_name, "summary": summary})
            if progress is not None:
                progress(int(index * 100 / total))
        return {"outputs": current_outputs, "log_path": str(log_path), "request_id": plan.request_id}
