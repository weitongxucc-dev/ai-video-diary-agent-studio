"""Bounded source-time windows and scene-cut facts for material intake."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


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


# ---------------------------------------------------------------------------
# Bounded scene probe (WIL-68): self-contained, budgeted, resumable analysis.
# This section is intentionally independent of the granted scene-window path
# above and of any device_* module; the SCHED wiring is a later package.
# ---------------------------------------------------------------------------

SCENE_PROBE_DEFAULT_CPU_THREADS = 1
SCENE_PROBE_DEFAULT_MAX_SOURCE_DURATION_S = 3600.0
SCENE_PROBE_DEFAULT_SAMPLING_FPS = 2.0
SCENE_PROBE_DEFAULT_SEGMENT_SECONDS = 14.0
SCENE_PROBE_DEFAULT_THRESHOLD = 0.25
SCENE_PROBE_WALL_CLOCK_MARGIN_S = 30.0
SCENE_PROBE_CHECKPOINT_MAX_BYTES = 64 * 1024


def _coerce_to_float(instance: object, *field_names: str) -> None:
    for name in field_names:
        value = getattr(instance, name)
        if type(value) is not float:
            object.__setattr__(instance, name, float(value))


@dataclass(frozen=True)
class SceneProbeBudget:
    """Conservative resource budget for one bounded scene probe.

    Field semantics are aligned with the SCHED resource registry:
    ``cpu_threads`` is the integer thread count the probe may use, and
    ``wall_clock_s`` (a derived property) is the wall-clock ceiling in seconds.
    """

    cpu_threads: int = SCENE_PROBE_DEFAULT_CPU_THREADS
    max_source_duration_s: float = SCENE_PROBE_DEFAULT_MAX_SOURCE_DURATION_S
    sampling_fps: float = SCENE_PROBE_DEFAULT_SAMPLING_FPS

    def __post_init__(self) -> None:
        if type(self.cpu_threads) is not int or self.cpu_threads < 1:
            raise ValueError("cpu_threads 必须是 ≥1 的整数")
        if (not math.isfinite(self.max_source_duration_s) or self.max_source_duration_s <= 0
                or not math.isfinite(self.sampling_fps) or self.sampling_fps <= 0):
            raise ValueError("场景探测预算的时长与采样率必须为有限正数")
        _coerce_to_float(self, "max_source_duration_s", "sampling_fps")

    @property
    def wall_clock_s(self) -> float:
        """Conservative wall-clock ceiling: at worst real-time decode plus margin."""
        return self.max_source_duration_s + SCENE_PROBE_WALL_CLOCK_MARGIN_S


@dataclass(frozen=True)
class SceneSegment:
    """One detected scene: half-open [start_seconds, end_seconds) with confidence."""

    start_seconds: float
    end_seconds: float
    confidence: float

    def __post_init__(self) -> None:
        if (any(not math.isfinite(value)
                for value in (self.start_seconds, self.end_seconds, self.confidence))
                or self.end_seconds <= self.start_seconds
                or not 0.0 <= self.confidence <= 1.0):
            raise ValueError("场景段起止时间或置信度无效")
        _coerce_to_float(self, "start_seconds", "end_seconds", "confidence")


@dataclass(frozen=True)
class SceneProbeSegment:
    """A bounded processing segment of a source window (half-open [start, end))."""

    index: int
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if (type(self.index) is not int or self.index < 0
                or any(not math.isfinite(value) for value in (self.start_seconds, self.end_seconds))
                or self.end_seconds <= self.start_seconds):
            raise ValueError("场景处理分段无效")
        _coerce_to_float(self, "start_seconds", "end_seconds")


def frame_difference(frame_a: bytes, frame_b: bytes) -> float:
    """Normalized mean absolute difference between two same-length frames, in [0, 1]."""
    if not isinstance(frame_a, bytes) or not isinstance(frame_b, bytes):
        raise ValueError("场景帧必须是字节串")
    if not frame_a or not frame_b or len(frame_a) != len(frame_b):
        raise ValueError("场景帧必须等长且非空")
    return sum(abs(a - b) for a, b in zip(frame_a, frame_b)) / (len(frame_a) * 255)


def _cut_confidence(difference: float, threshold: float) -> float:
    """Map a difference at/above threshold to a confidence in [0.5, 1.0]."""
    return round(0.5 + 0.5 * (difference - threshold) / (1.0 - threshold), 3)


def detect_scene_segments(
    frames: Sequence[bytes],
    *,
    window_start_seconds: float,
    sampling_fps: float,
    threshold: float = SCENE_PROBE_DEFAULT_THRESHOLD,
) -> tuple[SceneSegment, ...]:
    """Detect scene segments from frames sampled uniformly at ``sampling_fps``.

    Consecutive frames are differenced; a difference at or above ``threshold``
    marks a cut. Each returned segment carries a confidence in [0, 1] derived
    from the strength of the cuts that delimit it (window edges count as 1.0).
    """
    if not math.isfinite(window_start_seconds) or window_start_seconds < 0:
        raise ValueError("场景窗口起始时间无效")
    if not math.isfinite(sampling_fps) or sampling_fps <= 0:
        raise ValueError("场景采样率无效")
    if not math.isfinite(threshold) or not 0.0 < threshold < 1.0:
        raise ValueError("场景切分阈值必须在 (0, 1) 区间")
    for frame in frames:
        if not isinstance(frame, bytes) or not frame:
            raise ValueError("场景帧必须是非空字节串")

    if len(frames) < 2:
        if len(frames) == 1:
            end = round(window_start_seconds + 1.0 / sampling_fps, 3)
            return (SceneSegment(round(window_start_seconds, 3), end, 1.0),)
        return ()

    cut_strengths: dict[int, float] = {}
    for index in range(1, len(frames)):
        difference = frame_difference(frames[index - 1], frames[index])
        if difference >= threshold:
            cut_strengths[index] = _cut_confidence(difference, threshold)

    frame_period = 1.0 / sampling_fps
    last = len(frames) - 1
    edges = sorted({0, last} | set(cut_strengths))
    segments: list[SceneSegment] = []
    for left, right in zip(edges, edges[1:]):
        start = window_start_seconds + left * frame_period
        end = window_start_seconds + right * frame_period
        confidence = min(cut_strengths.get(left, 1.0), cut_strengths.get(right, 1.0))
        segments.append(SceneSegment(round(start, 3), round(end, 3), confidence))
    return tuple(segments)


def validate_scene_probe_window(
    window_start_seconds: float,
    window_end_seconds: float,
    budget: SceneProbeBudget,
) -> None:
    """Reject windows that exceed the budget with a readable reason."""
    if (not math.isfinite(window_start_seconds) or not math.isfinite(window_end_seconds)
            or window_start_seconds < 0 or window_end_seconds <= window_start_seconds):
        raise ValueError("源时间区间无效：起止必须为有限秒且 end > start ≥ 0")
    duration = window_end_seconds - window_start_seconds
    if duration > budget.max_source_duration_s:
        raise ValueError(
            f"源时间区间 {duration:.3f}s 超过预算上限 "
            f"{budget.max_source_duration_s:.3f}s，拒绝场景探测"
        )


def scene_probe_resource_spec(budget: SceneProbeBudget) -> dict[str, object]:
    """SCHED-registry-aligned resource declaration for one scene probe unit.

    ``cpu_threads`` (int) and ``wall_clock_s`` (float) match the registry field
    names and semantics; scheduling wiring is a later package.
    """
    return {"cpu_threads": budget.cpu_threads, "wall_clock_s": budget.wall_clock_s}


def scene_probe_segments(
    window_start_seconds: float,
    window_end_seconds: float,
    segment_seconds: float = SCENE_PROBE_DEFAULT_SEGMENT_SECONDS,
) -> tuple[SceneProbeSegment, ...]:
    """Split a source window into contiguous, idempotent processing segments."""
    if (not math.isfinite(window_start_seconds) or not math.isfinite(window_end_seconds)
            or window_start_seconds < 0 or window_end_seconds <= window_start_seconds):
        raise ValueError("源时间区间无效")
    if not math.isfinite(segment_seconds) or segment_seconds <= 0:
        raise ValueError("分段时长必须为有限正数")
    start = round(window_start_seconds, 6)
    window_end = round(window_end_seconds, 6)
    segments: list[SceneProbeSegment] = []
    index = 0
    while start < window_end:
        end = round(min(window_end, start + segment_seconds), 6)
        if end <= start:
            raise ValueError("分段时长低于场景定位精度")
        segments.append(SceneProbeSegment(index, start, end))
        start = end
        index += 1
    return tuple(segments)


@dataclass(frozen=True)
class CompletedSceneSegment:
    """A finished processing segment and its detected scenes."""

    index: int
    scenes: tuple[SceneSegment, ...]

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("已完成分段索引无效")


@dataclass(frozen=True)
class SceneProbeCheckpoint:
    """Resumable state for a bounded scene probe, persisted as canonical JSON."""

    window_start_seconds: float
    window_end_seconds: float
    segment_seconds: float
    sampling_fps: float
    threshold: float
    completed: tuple[CompletedSceneSegment, ...]

    def __post_init__(self) -> None:
        if (not math.isfinite(self.window_start_seconds)
                or not math.isfinite(self.window_end_seconds)
                or self.window_start_seconds < 0
                or self.window_end_seconds <= self.window_start_seconds):
            raise ValueError("检查点源时间区间无效")
        if not math.isfinite(self.segment_seconds) or self.segment_seconds <= 0:
            raise ValueError("检查点分段时长无效")
        if not math.isfinite(self.sampling_fps) or self.sampling_fps <= 0:
            raise ValueError("检查点采样率无效")
        if not math.isfinite(self.threshold) or not 0.0 < self.threshold < 1.0:
            raise ValueError("检查点阈值无效")
        indices = [segment.index for segment in self.completed]
        if len(set(indices)) != len(indices):
            raise ValueError("检查点已完成分段索引重复")
        if indices != sorted(indices):
            raise ValueError("检查点已完成分段必须按索引有序")
        _coerce_to_float(
            self,
            "window_start_seconds", "window_end_seconds", "segment_seconds",
            "sampling_fps", "threshold",
        )

    def has_segment(self, index: int) -> bool:
        return any(segment.index == index for segment in self.completed)

    def mark_complete(self, completed: CompletedSceneSegment) -> SceneProbeCheckpoint:
        if self.has_segment(completed.index):
            return self
        ordered = sorted(self.completed + (completed,), key=lambda segment: segment.index)
        return SceneProbeCheckpoint(
            self.window_start_seconds, self.window_end_seconds, self.segment_seconds,
            self.sampling_fps, self.threshold, tuple(ordered),
        )

    def all_scenes(self) -> tuple[SceneSegment, ...]:
        return tuple(scene for segment in self.completed for scene in segment.scenes)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> SceneProbeCheckpoint:
        if len(value.encode("utf-8")) > SCENE_PROBE_CHECKPOINT_MAX_BYTES:
            raise ValueError("场景探测检查点超出上限")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("场景探测检查点 JSON 无效") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "window_start_seconds", "window_end_seconds", "segment_seconds",
            "sampling_fps", "threshold", "completed",
        }:
            raise ValueError("场景探测检查点结构无效")
        checkpoint = cls(
            _parse_finite_number(payload["window_start_seconds"], "窗口起始"),
            _parse_finite_number(payload["window_end_seconds"], "窗口结束"),
            _parse_finite_number(payload["segment_seconds"], "分段时长"),
            _parse_finite_number(payload["sampling_fps"], "采样率"),
            _parse_finite_number(payload["threshold"], "阈值"),
            tuple(_parse_completed_segment(item) for item in payload["completed"]),
        )
        if checkpoint.to_json() != value:
            raise ValueError("场景探测检查点不是规范编码")
        return checkpoint


def _parse_finite_number(value: object, label: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError(f"检查点{label}无效")
    return float(value)


def _parse_scene_segment(value: object) -> SceneSegment:
    if not isinstance(value, dict) or set(value) != {"start_seconds", "end_seconds", "confidence"}:
        raise ValueError("检查点场景段结构无效")
    return SceneSegment(
        _parse_finite_number(value["start_seconds"], "场景段起始"),
        _parse_finite_number(value["end_seconds"], "场景段结束"),
        _parse_finite_number(value["confidence"], "场景段置信度"),
    )


def _parse_completed_segment(value: object) -> CompletedSceneSegment:
    if not isinstance(value, dict) or set(value) != {"index", "scenes"}:
        raise ValueError("检查点已完成分段结构无效")
    index = value["index"]
    if type(index) is not int:
        raise ValueError("检查点已完成分段索引无效")
    scenes = value["scenes"]
    if not isinstance(scenes, list):
        raise ValueError("检查点场景段列表无效")
    return CompletedSceneSegment(index, tuple(_parse_scene_segment(item) for item in scenes))


def load_scene_probe_checkpoint(path: Path) -> SceneProbeCheckpoint | None:
    """Load a checkpoint from disk, or return None when none exists yet."""
    try:
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return SceneProbeCheckpoint.from_json(value)


def write_scene_probe_checkpoint(path: Path, checkpoint: SceneProbeCheckpoint) -> None:
    """Atomically persist a checkpoint so a crash never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(checkpoint.to_json(), encoding="utf-8")
    os.replace(tmp, path)


def run_scene_probe(
    *,
    window_start_seconds: float,
    window_end_seconds: float,
    budget: SceneProbeBudget,
    frame_loader: Callable[[SceneProbeSegment], Sequence[bytes]],
    checkpoint_path: str | Path,
    segment_seconds: float = SCENE_PROBE_DEFAULT_SEGMENT_SECONDS,
    threshold: float = SCENE_PROBE_DEFAULT_THRESHOLD,
) -> tuple[SceneSegment, ...]:
    """Run a bounded, resumable scene probe over a source window.

    The window is split into segments; each segment's frames are loaded via
    ``frame_loader``, differenced, and recorded in the checkpoint file after
    the segment completes. A crash mid-run leaves completed segments on disk,
    and a restart resumes from the checkpoint without reloading them.
    """
    window_start = float(round(window_start_seconds, 6))
    window_end = float(round(window_end_seconds, 6))
    validate_scene_probe_window(window_start, window_end, budget)
    segments = scene_probe_segments(window_start, window_end, segment_seconds)
    checkpoint_path = Path(checkpoint_path)

    existing = load_scene_probe_checkpoint(checkpoint_path)
    if existing is None:
        checkpoint = SceneProbeCheckpoint(
            window_start, window_end, segment_seconds, budget.sampling_fps, threshold, (),
        )
    else:
        if (existing.window_start_seconds != window_start
                or existing.window_end_seconds != window_end
                or existing.segment_seconds != segment_seconds
                or existing.sampling_fps != budget.sampling_fps
                or existing.threshold != threshold):
            raise ValueError("检查点与当前探测参数不匹配，拒绝续跑")
        checkpoint = existing

    results: list[CompletedSceneSegment] = list(checkpoint.completed)
    for segment in segments:
        if checkpoint.has_segment(segment.index):
            continue
        frames = frame_loader(segment)
        scenes = detect_scene_segments(
            frames,
            window_start_seconds=segment.start_seconds,
            sampling_fps=budget.sampling_fps,
            threshold=threshold,
        )
        completed = CompletedSceneSegment(segment.index, scenes)
        results.append(completed)
        checkpoint = checkpoint.mark_complete(completed)
        write_scene_probe_checkpoint(checkpoint_path, checkpoint)

    ordered = sorted(results, key=lambda segment: segment.index)
    return tuple(scene for segment in ordered for scene in segment.scenes)


@dataclass(frozen=True)
class MaterialSceneCandidate:
    workspace_id: str
    asset_id: str
    file_path: str
    source_sha256: str
    provisional_revision: str
    probe_result_ref: str
    video_stream_index: int
    video_codec: str
    video_width_px: int
    video_height_px: int
    source_frame_rate_fps: float | None
    duration_seconds: float
    size_bytes: int
    window: SceneWindow
    unit_key: str
