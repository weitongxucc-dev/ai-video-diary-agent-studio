"""Bounded source-time windows and scene-cut facts for material intake."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass


SCENE_LOGICAL_WINDOW_SECONDS = 14.0
SCENE_OVERLAP_SECONDS = 1.0
SCENE_SAMPLE_FPS = 2
SCENE_MAX_WIDTH = 320
SCENE_THRESHOLD = 0.35
SCENE_MAX_SAMPLED_FRAMES = 30
SCENE_TIMEOUT_SECONDS = 12.0
SCENE_STDOUT_LIMIT_BYTES = 4 * 1024
SCENE_STDERR_LIMIT_BYTES = 64 * 1024

_SHOWINFO_TIME = re.compile(rb"\bn:\s*\d+\b[^\n]*\bpts_time:([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class SceneWindow:
    start_seconds: float
    end_seconds: float
    scan_start_seconds: float

    @property
    def scan_seconds(self) -> float:
        return round(self.end_seconds - self.scan_start_seconds, 3)


@dataclass(frozen=True)
class SceneWindowFacts:
    window: SceneWindow
    boundaries_seconds: tuple[float, ...]

    def checkpoint_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_checkpoint_json(cls, value: str) -> SceneWindowFacts:
        if len(value.encode("utf-8")) > SCENE_STDERR_LIMIT_BYTES:
            raise ValueError("镜头检查点超出上限")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("镜头检查点 JSON 无效") from exc
        if not isinstance(payload, dict) or set(payload) != {"window", "boundaries_seconds"}:
            raise ValueError("镜头检查点结构无效")
        raw_window = payload["window"]
        if not isinstance(raw_window, dict) or set(raw_window) != {
            "start_seconds", "end_seconds", "scan_start_seconds",
        }:
            raise ValueError("镜头检查点窗口无效")
        window = SceneWindow(**raw_window)
        validate_scene_window(window)
        boundaries = payload["boundaries_seconds"]
        if (not isinstance(boundaries, list) or len(boundaries) > SCENE_MAX_SAMPLED_FRAMES
                or any(type(item) not in {int, float} or not math.isfinite(item)
                       or not window.start_seconds <= item < window.end_seconds
                       for item in boundaries)):
            raise ValueError("镜头检查点切点无效")
        facts = cls(window, tuple(boundaries))
        if tuple(sorted(set(boundaries))) != facts.boundaries_seconds or facts.checkpoint_json() != value:
            raise ValueError("镜头检查点不是规范编码")
        return facts


def validate_scene_window(window: SceneWindow) -> None:
    values = (window.start_seconds, window.end_seconds, window.scan_start_seconds)
    if (any(type(value) not in {int, float} or not math.isfinite(value) for value in values)
            or window.start_seconds < 0 or window.end_seconds <= window.start_seconds
            or window.scan_start_seconds < 0
            or window.scan_start_seconds > window.start_seconds
            or window.start_seconds - window.scan_start_seconds > SCENE_OVERLAP_SECONDS
            or window.end_seconds - window.start_seconds > SCENE_LOGICAL_WINDOW_SECONDS
            or window.scan_seconds > SCENE_LOGICAL_WINDOW_SECONDS + SCENE_OVERLAP_SECONDS):
        raise ValueError("镜头窗口超出执行预算")


def scene_windows(duration_seconds: float) -> tuple[SceneWindow, ...]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("素材时长无效")
    duration = round(duration_seconds, 3)
    if duration <= 0:
        raise ValueError("素材时长低于镜头定位精度")
    windows: list[SceneWindow] = []
    start = 0.0
    while start < duration:
        end = round(min(duration, start + SCENE_LOGICAL_WINDOW_SECONDS), 3)
        window = SceneWindow(start, end, round(max(0.0, start - SCENE_OVERLAP_SECONDS), 3))
        validate_scene_window(window)
        windows.append(window)
        start = end
    return tuple(windows)


def scene_unit_key(source_sha256: str, probe_result_ref: str,
                   video_stream_index: int, window: SceneWindow) -> str:
    validate_scene_window(window)
    if (not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", probe_result_ref)
            or type(video_stream_index) is not int or video_stream_index < 0):
        raise ValueError("镜头单元来源无效")
    stage_input = hashlib.sha256(json.dumps({
        "stage": "scene-window:v1", "source_sha256": source_sha256,
        "probe_result_ref": probe_result_ref, "video_stream_index": video_stream_index,
        "window": asdict(window), "sample_fps": SCENE_SAMPLE_FPS,
        "max_width": SCENE_MAX_WIDTH, "threshold": SCENE_THRESHOLD,
        "ffmpeg_threads": 1, "worker_contract": "ffmpeg-showinfo:v1",
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"scene-window:v1:{round(window.start_seconds * 1000)}:{stage_input}"


def scene_window_for_key(duration_seconds: float, source_sha256: str,
                         probe_result_ref: str, video_stream_index: int,
                         unit_key: str) -> SceneWindow | None:
    match = re.fullmatch(r"scene-window:v1:(0|[1-9][0-9]{0,8}):[0-9a-f]{64}", unit_key)
    if match is None:
        return None
    start_ms = int(match.group(1))
    if start_ms % round(SCENE_LOGICAL_WINDOW_SECONDS * 1000) != 0:
        return None
    duration = round(duration_seconds, 3)
    start = start_ms / 1000
    if start >= duration:
        return None
    window = SceneWindow(start, round(min(duration, start + SCENE_LOGICAL_WINDOW_SECONDS), 3),
                         round(max(0.0, start - SCENE_OVERLAP_SECONDS), 3))
    return window if scene_unit_key(source_sha256, probe_result_ref,
                                    video_stream_index, window) == unit_key else None


def scene_command(ffmpeg: str, source_path: str, video_stream_index: int,
                  window: SceneWindow) -> list[str]:
    validate_scene_window(window)
    if not ffmpeg or not source_path or type(video_stream_index) is not int or video_stream_index < 0:
        raise ValueError("镜头命令输入无效")
    return [
        ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info",
        "-threads", "1", "-filter_threads", "1",
        "-ss", f"{window.scan_start_seconds:.3f}",
        "-t", f"{window.scan_seconds:.3f}", "-i", source_path,
        "-map", f"0:{video_stream_index}", "-an", "-sn",
        "-vf", "fps=2,trim=end_frame=30,scale=320:-2,select='gt(scene,0.35)',showinfo",
        "-f", "null", "-",
    ]


def parse_scene_showinfo(stderr: bytes, window: SceneWindow) -> SceneWindowFacts:
    validate_scene_window(window)
    if len(stderr) > SCENE_STDERR_LIMIT_BYTES:
        raise ValueError("镜头分析输出超出上限")
    matches = _SHOWINFO_TIME.findall(stderr)
    if len(matches) > SCENE_MAX_SAMPLED_FRAMES:
        raise ValueError("镜头分析帧数超出上限")
    boundaries: set[float] = set()
    for raw_time in matches:
        relative = float(raw_time)
        if not math.isfinite(relative) or not 0 <= relative <= window.scan_seconds + 0.001:
            raise ValueError("镜头切点时间无效")
        absolute = round(window.scan_start_seconds + relative, 3)
        if window.start_seconds <= absolute < window.end_seconds:
            boundaries.add(absolute)
    return SceneWindowFacts(window, tuple(sorted(boundaries)))
