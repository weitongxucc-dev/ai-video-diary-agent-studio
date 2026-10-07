"""Measured, fail-closed host probes for bounded CPU-only device units.

The caller supplies limits established for this machine. Model/GPU residency
is deliberately unsupported until an owner can measure and release it.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

import psutil

from app.device_scheduler import Grant, ProcessIdentity, RecoveryBlocked, RecoveryState, WorkRequest


@dataclass(frozen=True)
class CpuRenderBudget:
    """Device-calibrated limits for the explicitly bounded FFmpeg CPU path."""

    max_duration_seconds: float
    max_pixel_seconds: float
    max_width_px: int
    max_height_px: int
    max_input_bytes: int
    max_clip_count: int
    max_overlay_count: int
    max_output_bytes: int
    max_threads: int
    max_timeout_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int

    def __post_init__(self) -> None:
        if (not math.isfinite(self.max_duration_seconds) or self.max_duration_seconds <= 0
                or not math.isfinite(self.max_pixel_seconds) or self.max_pixel_seconds <= 0
                or not math.isfinite(self.max_timeout_seconds) or self.max_timeout_seconds <= 0):
            raise ValueError("Render duration and timeout budgets must be finite and positive")
        if any(type(value) is not int or value < 0 for value in (
            self.max_width_px, self.max_height_px, self.max_input_bytes,
            self.max_clip_count, self.max_overlay_count, self.max_output_bytes, self.max_threads,
            self.additional_memory_bytes, self.additional_disk_bytes,
        )) or any(value == 0 for value in (
            self.max_width_px, self.max_height_px, self.max_input_bytes,
            self.max_clip_count, self.max_output_bytes, self.max_threads,
            self.additional_memory_bytes, self.additional_disk_bytes,
        )):
            raise ValueError("Render limits must be explicit nonnegative integers")


@dataclass(frozen=True)
class SceneWindowBudget:
    """Calibrated cap for one timed FFmpeg source-window decode."""

    allowed_source_codecs: tuple[str, ...]
    max_source_bytes: int
    max_source_width_px: int
    max_source_height_px: int
    max_source_frame_rate_fps: float
    max_scan_seconds: float
    max_output_sample_fps: float
    max_output_width_px: int
    max_output_frames: int
    max_threads: int
    max_timeout_seconds: float
    max_process_rss_bytes: int
    max_process_threads: int
    runtime_sample_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int

    def __post_init__(self) -> None:
        rates = (self.max_source_frame_rate_fps, self.max_scan_seconds,
                 self.max_output_sample_fps, self.max_timeout_seconds,
                 self.runtime_sample_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in rates):
            raise ValueError("Scene timing and decode budgets must be finite and positive")
        integers = (self.max_source_bytes, self.max_source_width_px, self.max_source_height_px,
                    self.max_output_width_px, self.max_output_frames, self.max_threads,
                    self.max_process_rss_bytes, self.max_process_threads,
                    self.additional_memory_bytes, self.additional_disk_bytes)
        if (any(type(value) is not int or value <= 0 for value in integers)
                or not self.allowed_source_codecs
                or any(not isinstance(codec, str) or not codec or codec != codec.lower()
                       for codec in self.allowed_source_codecs)):
            raise ValueError("Scene codec and size budgets must be explicit and positive")
        if (self.additional_memory_bytes < self.max_process_rss_bytes
                or self.runtime_sample_seconds > 0.25):
            raise ValueError("Scene process monitor needs reserved memory and a bounded sample interval")


@dataclass(frozen=True)
class SceneRuntimeLimits:
    max_process_rss_bytes: int
    max_process_threads: int
    min_host_available_memory_bytes: int
    max_host_swap_used_bytes: int
    sample_seconds: float


@dataclass(frozen=True)
class NarrationBudget:
    max_text_chars: int
    max_section_count: int
    max_reference_bytes: int
    max_duration_seconds: float
    max_output_bytes: int
    max_threads: int
    max_timeout_seconds: float
    max_process_rss_bytes: int
    max_process_threads: int
    runtime_sample_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if name in {"max_duration_seconds", "max_timeout_seconds", "runtime_sample_seconds"}:
                if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                    raise ValueError("Narration timing limits must be explicit positive values")
            elif type(value) is not int or value <= 0:
                raise ValueError("Narration limits must be explicit positive integers")
        if (self.additional_memory_bytes < self.max_process_rss_bytes
                or self.runtime_sample_seconds > 0.25):
            raise ValueError("Narration monitor needs reserved process memory and a bounded sample interval")


@dataclass(frozen=True)
class MontageBudget:
    max_section_count: int
    max_asset_count: int
    max_input_bytes: int
    max_output_bytes: int
    max_timeout_seconds: float
    additional_memory_bytes: int

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if name == "max_timeout_seconds":
                if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                    raise ValueError("Montage timing limit must be explicitly positive")
            elif type(value) is not int or value <= 0:
                raise ValueError("Montage limits must be explicit positive integers")


@dataclass(frozen=True)
class HostBudget:
    max_cpu_percent: float
    min_available_memory_bytes: int
    max_swap_used_bytes: int
    min_free_disk_bytes: int
    max_disk_io_bytes_per_second: int
    max_unit_input_bytes: int
    sample_seconds: float = 0.1
    cpu_render: CpuRenderBudget | None = None
    scene_window: SceneWindowBudget | None = None
    narration: NarrationBudget | None = None
    montage: MontageBudget | None = None

    def __post_init__(self) -> None:
        if not (0 < self.max_cpu_percent <= 100 and self.sample_seconds > 0):
            raise ValueError("CPU budget and sample interval must be positive")
        if any(value < 0 for value in (
            self.min_available_memory_bytes, self.max_swap_used_bytes,
            self.min_free_disk_bytes, self.max_disk_io_bytes_per_second,
            self.max_unit_input_bytes,
        )):
            raise ValueError("Host budget cannot contain negative limits")
        if self.scene_window is not None and (
            self.min_available_memory_bytes == 0 or self.min_free_disk_bytes == 0
        ):
            raise ValueError("Scene decode requires explicit host memory and disk headroom")


# Per-kind static caps. These are the device-calibrated upper bounds a declared
# resource kind may request; the live sampled ceiling (see sample_host_capacity)
# can only lower them, never raise them.
CREATION_RENDER_SOURCE_DURATION_S_MAX = 3600.0
CREATION_RENDER_INPUT_COUNT_MAX = 64
CREATION_RENDER_CPU_THREADS_MAX = 16
CREATION_RENDER_WALL_CLOCK_S_MAX = 7200.0
MAT_SCENE_PROBE_CPU_THREADS_MAX = 8
MAT_SCENE_PROBE_WALL_CLOCK_S_MAX = 3600.0
MAT_SCENE_PROBE_MAX_SOURCE_DURATION_S_MAX = 1800.0
MAT_SCENE_PROBE_SAMPLING_FPS_MAX = 30.0


class ResourceSamplingUnavailable(RuntimeError):
    """Real host sampling could not complete; no budget is fabricated."""


class ResourceAdmissionRejected(ValueError):
    """A declared resource request was rejected; the reason is user-readable."""


@dataclass(frozen=True)
class ResourceField:
    """One declared input dimension with its inclusive value range."""

    name: str
    value_type: type  # int or float
    min_value: int | float
    max_value: int | float


@dataclass(frozen=True)
class ResourceKindSpec:
    """Declared dimensions and per-item caps for one resource kind."""

    name: str
    fields: tuple[ResourceField, ...]


@dataclass(frozen=True)
class HostCapacity:
    """A conservative snapshot of the live host, derived from real sampling."""

    cpu_count: int
    cpu_threads_ceiling: int
    available_memory_bytes: int
    load_avg_1m: float | None


@dataclass(frozen=True)
class ResourceAdmission:
    """The deterministic budget handed back for a legal declared request."""

    kind: str
    granted: dict[str, int | float]
    cpu_threads_ceiling: int
    available_memory_bytes: int
    load_avg_1m: float | None


_RESOURCE_KINDS: Mapping[str, ResourceKindSpec] = {
    "creation_render": ResourceKindSpec("creation_render", (
        ResourceField("source_duration_s", float, 0.0, CREATION_RENDER_SOURCE_DURATION_S_MAX),
        ResourceField("input_count", int, 1, CREATION_RENDER_INPUT_COUNT_MAX),
        ResourceField("cpu_threads", int, 1, CREATION_RENDER_CPU_THREADS_MAX),
        ResourceField("wall_clock_s", float, 0.0, CREATION_RENDER_WALL_CLOCK_S_MAX),
    )),
    "mat_scene_probe": ResourceKindSpec("mat_scene_probe", (
        ResourceField("cpu_threads", int, 1, MAT_SCENE_PROBE_CPU_THREADS_MAX),
        ResourceField("wall_clock_s", float, 0.0, MAT_SCENE_PROBE_WALL_CLOCK_S_MAX),
        ResourceField("max_source_duration_s", float, 0.0, MAT_SCENE_PROBE_MAX_SOURCE_DURATION_S_MAX),
        ResourceField("sampling_fps", float, 0.0, MAT_SCENE_PROBE_SAMPLING_FPS_MAX),
    )),
}


def resource_kinds() -> Mapping[str, ResourceKindSpec]:
    """The declared resource kind registry (creation_render, mat_scene_probe)."""
    return _RESOURCE_KINDS


def sample_host_capacity(*, thread_margin: int = 1) -> HostCapacity:
    """Sample the real host and derive a conservative thread ceiling.

    ``cpu_threads_ceiling`` is ``cpu_count - thread_margin`` (minimum 1), leaving
    headroom for the host itself. Sampling that cannot complete raises
    :class:`ResourceSamplingUnavailable` rather than fabricating a budget.
    """
    if thread_margin < 0:
        raise ValueError("thread margin must be nonnegative")
    try:
        cores = psutil.cpu_count(logical=True)
        memory = psutil.virtual_memory()
    except (OSError, psutil.Error, AttributeError) as exc:
        raise ResourceSamplingUnavailable("host CPU/memory sampling is unavailable") from exc
    if not isinstance(cores, int) or cores <= 0 or memory is None:
        raise ResourceSamplingUnavailable("host CPU/memory sampling returned no usable values")
    try:
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    except (OSError, AttributeError):
        load = None
    return HostCapacity(
        cpu_count=cores,
        cpu_threads_ceiling=max(1, cores - thread_margin),
        available_memory_bytes=int(memory.available),
        load_avg_1m=load,
    )


def admit_resource(kind: str, request: Mapping[str, object],
                   *, capacity: HostCapacity) -> ResourceAdmission:
    """Admit a declared resource request; reject with a readable reason otherwise.

    A legal request returns a deterministic :class:`ResourceAdmission`. Nothing
    is silently truncated: an undeclared field, an over-limit value, a type
    mismatch, or a missing field raises :class:`ResourceAdmissionRejected`.
    """
    spec = _RESOURCE_KINDS.get(kind)
    if spec is None:
        raise ResourceAdmissionRejected(f"unknown resource kind: {kind!r}")
    declared = {field.name for field in spec.fields}
    unknown = sorted(set(request) - declared)
    if unknown:
        raise ResourceAdmissionRejected(f"undeclared field(s): {', '.join(unknown)}")
    granted: dict[str, int | float] = {}
    for field in spec.fields:
        if field.name not in request:
            raise ResourceAdmissionRejected(f"missing required field: {field.name}")
        value = request[field.name]
        if field.value_type is int:
            if type(value) is not int:
                raise ResourceAdmissionRejected(
                    f"field {field.name} must be an integer, got {type(value).__name__}")
        else:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ResourceAdmissionRejected(
                    f"field {field.name} must be a finite number, got {value!r}")
        if not (field.min_value <= value <= field.max_value):
            raise ResourceAdmissionRejected(
                f"field {field.name}={value} outside [{field.min_value}, {field.max_value}]")
        granted[field.name] = value
    threads = int(granted["cpu_threads"])
    if threads > capacity.cpu_threads_ceiling:
        raise ResourceAdmissionRejected(
            f"cpu_threads={threads} exceeds sampled host ceiling {capacity.cpu_threads_ceiling}")
    if capacity.load_avg_1m is not None and capacity.load_avg_1m >= capacity.cpu_count:
        raise ResourceAdmissionRejected(
            f"host load average {capacity.load_avg_1m:.2f} saturates {capacity.cpu_count} cores")
    return ResourceAdmission(
        kind=kind,
        granted=granted,
        cpu_threads_ceiling=capacity.cpu_threads_ceiling,
        available_memory_bytes=capacity.available_memory_bytes,
        load_avg_1m=capacity.load_avg_1m,
    )


@dataclass(frozen=True)
class HostResourceDecision:
    """One probe call's verdict and evidence; never a cached readiness fact."""

    verdict: bool | None
    declared: dict[str, object]
    checks: dict[str, dict[str, object]]
    reason: str | None = None
    observed_interval_seconds: float | None = None


class HostResourceRefusal(RecoveryBlocked):
    """Carry the exact refusing observation through the existing failure path."""

    def __init__(self, decision: HostResourceDecision) -> None:
        self.decision = decision
        labels = {
            "cpu_percent": ("CPU占用", "%"),
            "available_memory_bytes": ("可用内存", "字节"),
            "swap_used_bytes": ("交换空间已用量", "字节"),
            "disk_io_bytes_per_second": ("磁盘读写速率", "字节/秒"),
            "size_bytes": ("输入大小", "字节"),
            "text_chars": ("配音文字数量", "字"),
            "section_count": ("段落数量", "段"),
            "reference_bytes": ("参考音频大小", "字节"),
            "duration_seconds": ("配音时长", "秒"),
            "max_output_bytes": ("输出大小", "字节"),
            "threads": ("线程数量", "个"),
            "timeout_seconds": ("阶段时限", "秒"),
            "asset_count": ("素材数量", "个"),
            "input_bytes": ("素材输入大小", "字节"),
        }
        reasons = {
            "disk_io_before_unavailable": "采样前的磁盘读写计数不可读取",
            "disk_io_after_unavailable": "采样后的磁盘读写计数不可读取",
            "sample_interval_nonpositive": "磁盘负载采样时长无效",
            "disk_io_counters_regressed": "磁盘读写计数回退，无法确认当前负载",
            "render_budget_or_execution_mode_unavailable": "成片预算或执行模式不可用",
            "render_numeric_fields_invalid": "成片时长或阶段时限无效",
            "render_integer_fields_invalid": "成片输入的数量或大小字段无效",
            "render_required_fields_zero": "成片必需的尺寸、数量或线程为零",
            "stage_budget_unavailable": "当前阶段的专属设备预算尚未标定",
            "stage_execution_mode_unavailable": "当前阶段的执行模式不受支持",
            "material_size_invalid": "素材输入大小无效",
            "scene_budget_or_source_codec_unavailable": "场景预算或源视频编码不可用",
            "scene_fields_invalid": "场景检测的输入声明无效",
            "scene_output_sampling_contract_invalid": "场景采样数量与输入时长不一致",
            "unsupported_resource_kind": "当前阶段的资源类型不受支持",
            "granted_resources_unreadable": "当前许可的阶段资源记录不可读取",
            "declared_resources_exceed_budget": "阶段输入超出已标定的设备预算",
        }
        details = []
        for metric, check in decision.checks.items():
            if check.get("passed") is not False:
                continue
            label, unit = (("目标磁盘可用空间", "字节") if metric.startswith("disk_free_bytes:")
                           else labels[metric])
            bound = (f"至少需要{check['minimum']}{unit}" if "minimum" in check else
                     f"上限{check['maximum']}{unit}")
            details.append(f"{label}为{check['actual']}{unit}，{bound}")
        if not details and decision.reason:
            metric = decision.reason.partition(":")[0]
            if metric == "disk_free_bytes":
                details.append("目标磁盘可用空间不可读取")
            elif metric in labels:
                details.append(f"{labels[metric][0]}不可读取")
            elif metric == "stage_field_invalid":
                details.append(f"{labels[decision.reason.split(':', 1)[1]][0]}声明无效")
            else:
                details.append(reasons[decision.reason])
        super().__init__("运行中资源未知或不足，须在安全边界让出：" + "；".join(details)
                         + "。请检查本机资源与该阶段预算后再试，当前任务保持。")



class HostResourceProbe:
    """Admit measured material work and explicitly bounded CPU render only."""

    _SUPPORTED_KINDS = frozenset({"material_hash", "material_probe_meta"})

    def __init__(self, database_path: str | Path, *, disk_paths: tuple[str | Path, ...],
                 budget: HostBudget) -> None:
        if not disk_paths:
            raise ValueError("At least one material/output disk path is required")
        self.database_path = Path(database_path).expanduser().resolve()
        self.disk_paths = tuple(Path(path).expanduser().resolve() for path in disk_paths)
        self.budget = budget

    def _sample(self, resources: dict[str, object]) -> HostResourceDecision:
        checks: dict[str, dict[str, object]] = {}

        def decision(verdict: bool | None, reason: str | None = None,
                     interval: float | None = None) -> HostResourceDecision:
            return HostResourceDecision(verdict, dict(resources), checks, reason, interval)

        kind, size = resources.get("kind"), resources.get("size_bytes")
        render = self.budget.cpu_render if kind == "creation_render" else None
        if kind == "creation_render":
            if render is None or resources.get("execution_mode") != "cpu_ffmpeg":
                return decision(None, "render_budget_or_execution_mode_unavailable")
            numeric_fields = ("duration_seconds", "timeout_seconds")
            integer_fields = ("width_px", "height_px", "input_bytes", "clip_count",
                              "overlay_count", "max_output_bytes", "threads")
            if any(type(resources.get(name)) not in {int, float}
                   or not math.isfinite(resources[name]) or resources[name] <= 0
                   for name in numeric_fields):
                return decision(None, "render_numeric_fields_invalid")
            if any(type(resources.get(name)) is not int or resources[name] < 0
                   for name in integer_fields):
                return decision(None, "render_integer_fields_invalid")
            if any(resources[name] == 0 for name in
                   ("width_px", "height_px", "clip_count", "max_output_bytes", "threads")):
                return decision(None, "render_required_fields_zero")
            checks["render_budget"] = {"limits": asdict(render)}
            if (resources["duration_seconds"] > render.max_duration_seconds
                    or resources["width_px"] > render.max_width_px
                    or resources["height_px"] > render.max_height_px
                    or (resources["width_px"] * resources["height_px"]
                        * resources["duration_seconds"] > render.max_pixel_seconds)
                    or resources["input_bytes"] > render.max_input_bytes
                    or resources["clip_count"] > render.max_clip_count
                    or resources["overlay_count"] > render.max_overlay_count
                    or resources["max_output_bytes"] > render.max_output_bytes
                    or resources["threads"] > render.max_threads
                    or resources["timeout_seconds"] > render.max_timeout_seconds):
                return decision(False, "declared_resources_exceed_budget")
            required_memory = self.budget.min_available_memory_bytes + render.additional_memory_bytes
            required_disk = (self.budget.min_free_disk_bytes + render.additional_disk_bytes
                             + resources["max_output_bytes"])
        elif kind in {"creation_narration", "creation_montage_plan"}:
            stage = self.budget.narration if kind == "creation_narration" else self.budget.montage
            if stage is None:
                return decision(None, "stage_budget_unavailable")
            bounds = ({"text_chars": stage.max_text_chars, "section_count": stage.max_section_count,
                       "reference_bytes": stage.max_reference_bytes, "duration_seconds": stage.max_duration_seconds,
                       "max_output_bytes": stage.max_output_bytes, "threads": stage.max_threads,
                       "timeout_seconds": stage.max_timeout_seconds}
                      if kind == "creation_narration" else
                      {"section_count": stage.max_section_count, "asset_count": stage.max_asset_count,
                       "input_bytes": stage.max_input_bytes, "max_output_bytes": stage.max_output_bytes,
                       "timeout_seconds": stage.max_timeout_seconds})
            expected_mode = "isolated_qwen3_tts" if kind == "creation_narration" else "metadata_only"
            if resources.get("execution_mode") != expected_mode:
                return decision(None, "stage_execution_mode_unavailable")
            for name, limit in bounds.items():
                value = resources.get(name)
                expected_types = {int, float} if name in {"duration_seconds", "timeout_seconds"} else {int}
                if type(value) not in expected_types or not math.isfinite(value) or value < 0:
                    return decision(None, f"stage_field_invalid:{name}")
                checks[name] = {"actual": value, "maximum": limit, "passed": value <= limit}
                if value > limit:
                    return decision(False, "declared_resources_exceed_budget")
            required_memory = self.budget.min_available_memory_bytes + stage.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + stage.max_output_bytes
            if kind == "creation_narration":
                required_disk += stage.additional_disk_bytes
        elif kind in self._SUPPORTED_KINDS:
            if type(size) is not int or size < 0:
                return decision(None, "material_size_invalid")
            checks["size_bytes"] = {"actual": size, "maximum": self.budget.max_unit_input_bytes,
                                    "passed": size <= self.budget.max_unit_input_bytes}
            if size > self.budget.max_unit_input_bytes:
                return decision(False, "declared_resources_exceed_budget")
            required_memory = self.budget.min_available_memory_bytes
            required_disk = self.budget.min_free_disk_bytes
        elif kind == "material_scene_window":
            scene = self.budget.scene_window
            if scene is None or resources.get("source_codec") not in scene.allowed_source_codecs:
                return decision(None, "scene_budget_or_source_codec_unavailable")
            real_fields = ("source_frame_rate_fps", "scan_seconds", "output_sample_fps",
                           "timeout_seconds")
            int_fields = ("size_bytes", "source_width_px", "source_height_px",
                          "output_max_width_px", "output_max_frames", "ffmpeg_threads")
            if (any(type(resources.get(name)) not in {int, float}
                    or not math.isfinite(resources[name]) or resources[name] <= 0
                    for name in real_fields)
                    or any(type(resources.get(name)) is not int or resources[name] <= 0
                           for name in int_fields)):
                return decision(None, "scene_fields_invalid")
            checks["scene_budget"] = {"limits": asdict(scene)}
            if (resources["size_bytes"] > scene.max_source_bytes
                    or resources["source_width_px"] > scene.max_source_width_px
                    or resources["source_height_px"] > scene.max_source_height_px
                    # The reported rate screens calibrated profiles; it is not
                    # an upper bound on VFR or long-GOP decoded input frames.
                    or resources["source_frame_rate_fps"] > scene.max_source_frame_rate_fps
                    or resources["scan_seconds"] > scene.max_scan_seconds
                    or resources["output_sample_fps"] > scene.max_output_sample_fps
                    or resources["output_max_width_px"] > scene.max_output_width_px
                    or resources["output_max_frames"] > scene.max_output_frames
                    or resources["ffmpeg_threads"] > scene.max_threads
                    or resources["timeout_seconds"] > scene.max_timeout_seconds):
                return decision(False, "declared_resources_exceed_budget")
            if resources["output_sample_fps"] * resources["scan_seconds"] > resources["output_max_frames"]:
                return decision(None, "scene_output_sampling_contract_invalid")
            required_memory = self.budget.min_available_memory_bytes + scene.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + scene.additional_disk_bytes
        else:
            return decision(None, "unsupported_resource_kind")
        # Known thresholds remain available even when a later metric is unreadable.
        checks.update({
            "cpu_percent": {"maximum": self.budget.max_cpu_percent},
            "available_memory_bytes": {"minimum": required_memory},
            "swap_used_bytes": {"maximum": self.budget.max_swap_used_bytes},
            "disk_io_bytes_per_second": {"maximum": self.budget.max_disk_io_bytes_per_second},
            **{f"disk_free_bytes:{path}": {"minimum": required_disk} for path in self.disk_paths},
        })
        sampling = "disk_io_before"
        duration = None
        try:
            before = psutil.disk_io_counters()
            if before is None:
                return decision(None, "disk_io_before_unavailable")
            start = time.monotonic()
            sampling = "cpu_percent"
            cpu = psutil.cpu_percent(interval=self.budget.sample_seconds)
            checks["cpu_percent"] = {"actual": cpu, "maximum": self.budget.max_cpu_percent,
                                     "passed": cpu <= self.budget.max_cpu_percent}
            duration = time.monotonic() - start
            sampling = "disk_io_after"
            after = psutil.disk_io_counters()
            if after is None:
                return decision(None, "disk_io_after_unavailable", duration)
            if duration <= 0:
                return decision(None, "sample_interval_nonpositive", duration)
            io_bytes = (after.read_bytes - before.read_bytes +
                        after.write_bytes - before.write_bytes)
            if io_bytes < 0:
                checks["disk_io_bytes"] = {"actual": io_bytes, "before_read_bytes": before.read_bytes,
                    "before_write_bytes": before.write_bytes, "after_read_bytes": after.read_bytes,
                    "after_write_bytes": after.write_bytes}
                return decision(None, "disk_io_counters_regressed", duration)
            rate = io_bytes / duration
            checks["disk_io_bytes_per_second"] = {
                "actual": rate, "maximum": self.budget.max_disk_io_bytes_per_second,
                "passed": rate <= self.budget.max_disk_io_bytes_per_second,
                "delta_bytes": io_bytes,
            }
            sampling = "available_memory_bytes"
            memory = psutil.virtual_memory()
            checks[sampling] = {"actual": memory.available, "minimum": required_memory,
                                "passed": memory.available >= required_memory}
            sampling = "swap_used_bytes"
            swap = psutil.swap_memory()
            checks[sampling] = {"actual": swap.used, "maximum": self.budget.max_swap_used_bytes,
                                "passed": swap.used <= self.budget.max_swap_used_bytes}
            disks = []
            for path in self.disk_paths:
                sampling = f"disk_free_bytes:{path}"
                disk = psutil.disk_usage(str(path))
                disks.append(disk)
                checks[sampling] = {"actual": disk.free, "minimum": required_disk,
                                    "passed": disk.free >= required_disk}
        except (OSError, psutil.Error, ValueError) as exc:
            return decision(None, f"{sampling}: {type(exc).__name__}: {exc}", duration)
        # Exactly the existing host gates, evaluated from this observation only.
        return decision(cpu <= self.budget.max_cpu_percent
                        and memory.available >= required_memory
                        and swap.used <= self.budget.max_swap_used_bytes
                        and all(disk.free >= required_disk for disk in disks)
                        and io_bytes / duration <= self.budget.max_disk_io_bytes_per_second,
                        interval=duration)

    def admit(self, request: WorkRequest) -> bool | None:
        return self._sample(request.resources).verdict

    def _grant_resources(self, grant: Grant) -> dict[str, object] | None:
        if grant.database_path != self.database_path:
            return None
        try:
            with sqlite3.connect(f"file:{quote(str(self.database_path))}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    """SELECT resources_json FROM device_work_requests WHERE id=?
                    AND workspace_id=? AND owner_type=? AND owner_id=?
                    AND unit_key=? AND input_digest=? AND attempt_number=?
                    AND last_grant_generation=? AND state IN ('granted','yield_requested')""",
                    (grant.request_id, grant.workspace_id, grant.owner_type, grant.owner_id,
                     grant.unit_key, grant.input_digest, grant.attempt_number, grant.generation),
                ).fetchone()
            resources = json.loads(row[0]) if row else None
            return resources if isinstance(resources, dict) else None
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return None

    def continue_unit(self, grant: Grant) -> bool | None:
        resources = self._grant_resources(grant)
        decision = (self._sample(resources) if resources is not None else
                    HostResourceDecision(None, {}, {}, "granted_resources_unreadable"))
        if decision.verdict is not True:
            raise HostResourceRefusal(decision)
        return True

    def scene_runtime_limits(self, grant: Grant) -> SceneRuntimeLimits | None:
        resources = self._grant_resources(grant)
        if resources is None:
            raise RecoveryBlocked("获许可资源事实不可读取，不能执行子进程")
        if resources.get("kind") == "creation_narration":
            narration = self.budget.narration
            if narration is None:
                raise RecoveryBlocked("配音进程预算尚未标定")
            return SceneRuntimeLimits(
                narration.max_process_rss_bytes, narration.max_process_threads,
                self.budget.min_available_memory_bytes, self.budget.max_swap_used_bytes,
                narration.runtime_sample_seconds,
            )
        if resources.get("kind") != "material_scene_window":
            return None
        scene = self.budget.scene_window
        if scene is None:
            raise RecoveryBlocked("场景进程预算未配置，不能执行子进程")
        return SceneRuntimeLimits(
            scene.max_process_rss_bytes, scene.max_process_threads,
            self.budget.min_available_memory_bytes,
            self.budget.max_swap_used_bytes, scene.runtime_sample_seconds,
        )

    def released(self, grant: Grant) -> bool | None:
        resources = self._grant_resources(grant)
        # These kinds have no resident model/GPU allocation. Process descendants
        # are independently checked by DeviceScheduler before calling this.
        if resources is None:
            return None
        if resources.get("kind") in self._SUPPORTED_KINDS:
            return True
        if resources.get("kind") == "creation_narration" and resources.get("execution_mode") == "isolated_qwen3_tts":
            # The scheduler first proves every registered child group exited.
            # MLX exists only inside that group, never the API process.
            return True
        if resources.get("kind") == "creation_montage_plan" and resources.get("execution_mode") == "metadata_only":
            return True
        if (resources.get("kind") == "creation_render"
                and resources.get("execution_mode") == "cpu_ffmpeg"):
            return True
        if resources.get("kind") == "material_scene_window":
            return True
        return None


class HostProcessProbe:
    """Track exact PID/start/boot plus the POSIX process group owned by a child."""

    def __init__(self) -> None:
        if os.name != "posix" or sys.platform not in {"darwin", "linux"}:
            raise RuntimeError("Host process probe supports macOS and Linux only")

    @staticmethod
    def _boot_id() -> str:
        if sys.platform == "linux":
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"darwin:{psutil.boot_time():.0f}"

    def identity(self, pid: int) -> ProcessIdentity:
        return ProcessIdentity(pid, f"{psutil.Process(pid).create_time():.6f}", self._boot_id())

    def current(self) -> ProcessIdentity:
        return self.identity(os.getpid())

    def _state(self, child: ProcessIdentity, *, group: bool) -> RecoveryState:
        try:
            boot = self._boot_id()
            if not boot:
                return "unknown"
            if child.boot_id != boot:
                return "released"
            try:
                observed = self.identity(child.pid)
            except psutil.NoSuchProcess:
                observed = None
            if observed is not None and observed.start_token != child.start_token:
                return "unknown" if group else "released"
            if group:
                # Wrapper makes its PID the session and process-group ID before
                # registration. A surviving grandchild retains this group ID.
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    return "released"
                except PermissionError:
                    return "unknown"
                return "active"
            return "active" if observed is not None else "released"
        except (OSError, psutil.Error, ValueError):
            return "unknown"

    def children_released(self, children: tuple[ProcessIdentity, ...]) -> bool | None:
        states = tuple(self._state(child, group=True) for child in children)
        if "active" in states:
            return False
        if "unknown" in states:
            return None
        return True

    def recovery_state(self, holder: ProcessIdentity,
                       children: tuple[ProcessIdentity, ...]) -> RecoveryState:
        states = (self._state(holder, group=False),
                  *(self._state(child, group=True) for child in children))
        if "active" in states:
            return "active"
        if "unknown" in states:
            return "unknown"
        return "released"
