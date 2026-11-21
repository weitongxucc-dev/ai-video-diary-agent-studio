from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import MediaExecutionError, MediaOperation


@dataclass(frozen=True)
class PreparedCommand:
    operation: str
    command: tuple[str, ...]
    output_path: Path
    input_paths: tuple[Path, ...]
    logical_kind: str


class FfmpegAdapter:
    """Translate EditPlan operations into argv without exposing raw flags."""

    def __init__(self, ffmpeg: str, ffprobe: str) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def prepare(self, operation: MediaOperation, output_path: Path) -> PreparedCommand:
        name = operation.operation.lower()
        inputs = tuple(Path(item).expanduser() for item in operation.inputs)
        if name == "look" and operation.options.get("frame_time") is not None:
            if len(inputs) != 1:
                raise MediaExecutionError("frame_input_count", "抽帧没有生成输出。", "为 look 提供一个真实媒体输入。")
            timestamp = float(operation.options.get("frame_time", 0))
            filters = []
            if operation.options.get("width") and operation.options.get("height"):
                filters.append(f"scale={int(operation.options['width'])}:{int(operation.options['height'])}")
            command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp),
                       "-i", str(inputs[0]), "-frames:v", "1"]
            if filters:
                command.extend(["-vf", ",".join(filters)])
            command.append(str(output_path))
            return PreparedCommand(name, tuple(command), output_path, inputs, "media_file")
        if name in {"probe", "look"}:
            if len(inputs) != 1:
                raise MediaExecutionError("probe_input_count", "没有生成探测结果。", "为 probe 提供一个真实媒体输入。")
            command = (self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(inputs[0]))
            return PreparedCommand(name, command, output_path, inputs, "probe_json")
        if not inputs:
            raise MediaExecutionError("media_input_missing", "媒体作业没有可执行输入。", "为媒体操作提供真实输入文件。")
        if name in {"cut", "trim"}:
            start = float(operation.options.get("start", 0))
            duration = float(operation.options["duration"])
            command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(inputs[0]),
                       "-t", str(duration), "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output_path))
        elif name in {"fit", "aspect_ratio", "export"}:
            width = int(operation.options.get("width", 1080))
            height = int(operation.options.get("height", 1920))
            codec = str(operation.options.get("video_codec", "libx264"))
            audio = str(operation.options.get("audio_codec", "aac"))
            vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(inputs[0]), "-vf", vf,
                       "-c:v", codec, "-c:a", audio, "-movflags", "+faststart", str(output_path))
        elif name in {"audio", "mix_audio"}:
            if len(inputs) != 2:
                raise MediaExecutionError("audio_input_count", "音频操作没有生成输出。", "为 audio 提供一个视频和一个音频输入。")
            command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(inputs[0]), "-i", str(inputs[1]),
                       "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(output_path))
        elif name in {"caption", "subtitles"}:
            subtitle = operation.options.get("subtitle_path")
            if not subtitle:
                raise MediaExecutionError("subtitle_input_missing", "字幕操作没有生成输出。", "为 caption 提供 subtitle_path。")
            escaped = str(Path(str(subtitle)).expanduser()).replace("\\", "\\\\").replace(":", "\\:")
            command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(inputs[0]),
                       "-vf", f"subtitles={escaped}", "-c:v", "libx264", "-c:a", "aac", str(output_path))
        elif name == "transcode":
            command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(inputs[0]),
                       "-c:v", str(operation.options.get("video_codec", "libx264")),
                       "-c:a", str(operation.options.get("audio_codec", "aac")), str(output_path))
        else:
            raise MediaExecutionError("unsupported_media_operation", f"未执行媒体操作 {operation.operation}。",
                                      "改用已登记的媒体能力，或先为该能力提供适配器。")
        return PreparedCommand(name, command, output_path, inputs, "media_file")

    def concat(self, inputs: tuple[Path, ...], output_path: Path, manifest_path: Path) -> PreparedCommand:
        if len(inputs) < 2:
            raise MediaExecutionError("join_input_count", "拼接至少需要两个媒体输入。", "为 join 提供两个或更多输入文件。")
        manifest_path.write_text("".join(f"file '{path.as_posix().replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n" for path in inputs), encoding="utf-8")
        command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                   "-i", str(manifest_path), "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output_path))
        return PreparedCommand("join", command, output_path, inputs, "media_file")

    @staticmethod
    def parse_probe(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MediaExecutionError("probe_invalid_json", "FFprobe 返回内容无法解析。", "检查输入媒体并重新执行 probe。") from exc
        streams = value.get("streams")
        if not isinstance(streams, list):
            raise MediaExecutionError("probe_missing_streams", "FFprobe 没有返回媒体流信息。", "确认输入是可读取的媒体文件。")
        return value
