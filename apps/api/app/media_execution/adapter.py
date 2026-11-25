from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
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


def is_bound_video_cut(operation: MediaOperation) -> bool:
    return (operation.operation.lower() in {"cut", "trim"}
            and bool(operation.inputs) and "asset_id" in operation.options)


def video_frame_intervals(media: dict) -> list[tuple[int, Fraction, Fraction]]:
    """Read display intervals from decoded frames in the first real video stream."""
    if not media.get("frames"):
        return []
    try:
        time_base = Fraction(media["streams"][0]["time_base"])
        intervals = []
        for frame in media["frames"]:
            pts = int(frame["best_effort_timestamp"])
            # Older FFprobe reports the decoded frame duration as pkt_duration.
            duration = int(frame.get("duration", frame.get("pkt_duration")))
            if time_base <= 0 or duration <= 0 or (intervals and pts <= intervals[-1][0]):
                raise ValueError("invalid frame presentation clock")
            intervals.append((pts, pts * time_base, (pts + duration) * time_base))
        return intervals
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise MediaExecutionError("media_cut_duration_unverified", "无法核验视频帧的呈现区间，未发布。",
                                  "检查源片或编码工具能否提供有效的帧时间戳和时长。") from exc


def _rounded_frame_period(stream: dict) -> tuple[Fraction, Fraction] | None:
    """The two tick lengths that can represent one declared frame period."""
    time_base = Fraction(stream["time_base"])
    try:
        frame_rate = Fraction(stream.get("avg_frame_rate", "0"))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if frame_rate <= 0:
        return None
    period_ticks = 1 / (frame_rate * time_base)
    shorter = period_ticks.numerator // period_ticks.denominator
    if shorter == 0 or period_ticks.denominator == 1:
        return None
    return shorter * time_base, (shorter + 1) * time_base


@dataclass(frozen=True)
class VideoCut:
    source_start: Fraction
    source_end: Fraction
    first_pts: int
    last_pts: int
    intervals: tuple[tuple[Fraction, Fraction], ...]

    @classmethod
    def from_source(cls, media: dict, start: float, duration: float) -> VideoCut:
        frames = video_frame_intervals(media)
        if not frames:
            raise cls.no_video_frames()
        try:
            origin = Fraction(media["format"]["start_time"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise MediaExecutionError("media_cut_duration_unverified", "无法核验原片的起始时钟，未发布。",
                                      "检查源片是否有可读取的媒体起始时间。") from exc
        # Resolve both edges at FFmpeg's microsecond precision. The sealed
        # duration is a float difference and can otherwise admit an end frame
        # whose start equals the requested end.
        start_offset = Fraction(str(start))
        end_offset = start_offset + Fraction(str(duration))
        source_start = origin + Fraction(round(start_offset * 1_000_000), 1_000_000)
        source_end = origin + Fraction(round(end_offset * 1_000_000), 1_000_000)
        rounded_period = _rounded_frame_period(media["streams"][0])
        selected = []
        for index, (pts, frame_start, frame_end) in enumerate(frames):
            if frame_start >= source_end:
                break
            # A following frame supersedes this one at its presentation time.
            if index + 1 < len(frames):
                next_start = frames[index + 1][1]
                # A coarse source time base can store a frame duration rounded
                # down and successive PTS spaced by the rounded-up period.
                if rounded_period == (frame_end - frame_start, next_start - frame_start):
                    frame_end = next_start
                else:
                    frame_end = min(frame_end, next_start)
            begin, end = max(source_start, frame_start), min(source_end, frame_end)
            if begin < end:
                selected.append((pts, begin - source_start, end - source_start))
        if not selected:
            raise cls.no_video_frames()
        if any(previous[2] != current[1] for previous, current in zip(selected, selected[1:])):
            raise MediaExecutionError("media_cut_presentation_gap", "区间内画面呈现不连续，当前 MP4 截取无法保持该空档。",
                                      "请选择画面连续的区间。")
        return cls(source_start, source_end, selected[0][0], selected[-1][0],
                   tuple((begin, end) for _, begin, end in selected))

    @staticmethod
    def no_video_frames() -> MediaExecutionError:
        return MediaExecutionError("media_cut_no_video_frames", "所选区间与源片的画面呈现区间没有交集，未发布。",
                                   "请选择包含源片画面的区间。")


def verify_video_cut(media: dict, cut: VideoCut) -> dict:
    actual = [(begin, end) for _, begin, end in video_frame_intervals(media)]
    try:
        actual_duration = float(media["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaExecutionError("media_cut_duration_unverified", "截取文件缺少可核验的容器时长，未发布。",
                                  "检查源片时间戳后查询原截取作业。") from exc
    # Allow two output ticks for the filter and muxer's timestamp rounding.
    same_intervals = len(actual) == len(cut.intervals) and all(
        abs(actual_start - start) <= 2 / 90000 and abs(actual_end - end) <= 2 / 90000
        for (actual_start, actual_end), (start, end) in zip(actual, cut.intervals)
    )
    if (not same_intervals or media["streams"][0].get("codec_name") != "h264"
            or not math.isclose(actual_duration, float(cut.source_end - cut.source_start), rel_tol=0, abs_tol=0.01)):
        raise MediaExecutionError("media_cut_range_mismatch", "截取文件的画面或时长与请求区间不一致，未发布。",
                                  "检查源片时间戳和截取范围后查询原作业。")
    return {"video_frames": len(actual), "duration_seconds": actual_duration,
            "video_start_seconds": float(actual[0][0]), "video_end_seconds": float(actual[-1][1])}


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
            command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                       "-i", str(inputs[0]), "-t", str(duration), "-c:v", "libx264", "-c:a", "aac",
                       "-movflags", "+faststart", str(output_path))
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

    def video_cut(self, operation: str, source: Path, output: Path, cut: VideoCut,
                  *, threads: int | None = None, input_limits: tuple[str, ...] = ()) -> PreparedCommand:
        # Keep the first intersecting frame, even if its PTS predates the cut.
        # Use the same absolute source clock for audio, whose samples must be trimmed.
        video = (f"trim=start_pts={cut.first_pts}:end_pts={cut.last_pts + 1},"
                 f"settb=1/90000,setpts=max(0\\,PTS-({cut.source_start})/TB)")
        audio = (f"atrim=start={round(cut.source_start * 1_000_000)}us:"
                 f"end={round(cut.source_end * 1_000_000)}us,asetpts=PTS-({cut.source_start})/TB")
        last_start, last_end = cut.intervals[-1]
        # Earlier packet durations are determined by successive PTS. Set the last
        # one explicitly to the proven intersection, including for a single frame.
        duration = f"setts=duration=if(eq(N\\,{len(cut.intervals) - 1})\\,({last_end - last_start})/TB\\,DURATION)"
        decode_limits = ("-threads", str(threads), "-filter_threads", str(threads)) if threads is not None else ()
        encode_limits = ("-threads:v", str(threads), "-threads:a", str(threads)) if threads is not None else ()
        command = (self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-copyts", *decode_limits,
                   *input_limits, "-i", str(source),
                   "-map", "0:V:0", "-map", "0:a:0?", "-vf", video, "-af", audio,
                   "-c:v", "libx264", "-bf:v", "0", "-fps_mode:v", "passthrough", "-enc_time_base:v", "1:90000",
                   "-bsf:v", duration, "-video_track_timescale", "90000", "-movie_timescale", "90000",
                   "-c:a", "aac", *encode_limits, "-movflags", "+faststart", str(output))
        return PreparedCommand(operation, command, output, (source,), "video_cut")

    def cut_probe_command(self, path: Path, *, threads: int | None = None) -> tuple[str, ...]:
        decode_limits = ("-threads", str(threads)) if threads is not None else ()
        return (self.ffprobe, "-v", "error", *decode_limits, "-select_streams", "V:0", "-show_frames",
                "-show_entries", "frame=best_effort_timestamp,duration,pkt_duration:"
                "stream=codec_name,time_base,avg_frame_rate:format=start_time,duration",
                "-of", "json=compact=1", str(path))

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
