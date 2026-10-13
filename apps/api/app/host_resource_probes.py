"""Measured, fail-closed host probes for bounded CPU-only device units.

The caller supplies limits established for this machine. Model/GPU residency
is deliberately unsupported until an owner can measure and release it.
"""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

import psutil

from app.device_scheduler import Grant, ProcessIdentity, RecoveryBlocked, RecoveryState, WorkRequest
from app.platform_browser.base import PUBLISH_OPERATION_TIMEOUT_SECONDS


logger = logging.getLogger(__name__)


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
    max_process_threads: int

    def __post_init__(self) -> None:
        if (not math.isfinite(self.max_duration_seconds) or self.max_duration_seconds <= 0
                or not math.isfinite(self.max_pixel_seconds) or self.max_pixel_seconds <= 0
                or not math.isfinite(self.max_timeout_seconds) or self.max_timeout_seconds <= 0):
            raise ValueError("Render duration and timeout budgets must be finite and positive")
        if any(type(value) is not int or value < 0 for value in (
            self.max_width_px, self.max_height_px, self.max_input_bytes,
            self.max_clip_count, self.max_overlay_count, self.max_output_bytes, self.max_threads,
            self.additional_memory_bytes, self.additional_disk_bytes, self.max_process_threads,
        )) or any(value == 0 for value in (
            self.max_width_px, self.max_height_px, self.max_input_bytes,
            self.max_clip_count, self.max_output_bytes, self.max_threads,
            self.additional_memory_bytes, self.additional_disk_bytes, self.max_process_threads,
        )):
            raise ValueError("Render limits must be explicit nonnegative integers")
        if self.max_process_threads < self.max_threads:
            raise ValueError("Render process thread cap must cover the requested encoder threads")


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
class PresenterBudget:
    max_section_count: int
    max_input_bytes: int
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
                    raise ValueError("Presenter timing limits must be explicitly positive")
            elif type(value) is not int or value <= 0:
                raise ValueError("Presenter limits must be explicit positive integers")
        if self.additional_memory_bytes < self.max_process_rss_bytes or self.runtime_sample_seconds > 0.25:
            raise ValueError("Presenter monitor needs reserved process memory and bounded sampling")


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
class CpuAudioBudget:
    max_input_count: int
    max_input_bytes: int
    max_source_duration_seconds: float
    max_output_duration_seconds: float
    max_sample_rate: int
    max_channels: int
    max_output_bytes: int
    max_threads: int
    max_timeout_seconds: float
    max_process_rss_bytes: int
    max_process_threads: int
    runtime_sample_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int

    def __post_init__(self):
        for name, value in vars(self).items():
            if name.endswith("seconds"):
                if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                    raise ValueError("CPU audio timing limits must be explicitly positive")
            elif type(value) is not int or value <= 0:
                raise ValueError("CPU audio limits must be explicitly positive integers")
        if self.additional_memory_bytes < self.max_process_rss_bytes or self.runtime_sample_seconds > .25:
            raise ValueError("CPU audio needs reserved process memory and bounded sampling")


@dataclass(frozen=True)
class MediaScriptBudget:
    """Device-calibrated single-process Python/Pillow envelope; no defaults."""

    max_script_bytes: int
    max_parameters_bytes: int
    max_input_count: int
    max_input_bytes: int
    max_output_count: int
    max_width_px: int
    max_height_px: int
    max_output_pixels: int
    max_output_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_threads: int
    max_timeout_seconds: float
    max_process_rss_bytes: int
    max_process_threads: int
    runtime_sample_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int

    def __post_init__(self):
        for name, value in vars(self).items():
            if name.endswith("seconds"):
                if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                    raise ValueError("Media script timing limits must be explicitly positive")
            elif type(value) is not int or value <= 0:
                raise ValueError("Media script limits must be explicitly positive integers")
        if self.additional_memory_bytes < self.max_process_rss_bytes or self.runtime_sample_seconds > .25:
            raise ValueError("Media script needs reserved process memory and bounded sampling")


@dataclass(frozen=True)
class ObservationBudget:
    """Measured local FFmpeg + Apple Vision envelope; no inferred defaults."""
    worker_sha256: str
    max_samples: int
    max_width_px: int
    max_height_px: int
    max_input_bytes: int
    max_source_pixels: int
    max_output_bytes: int
    max_threads: int
    max_timeout_seconds: float
    max_process_rss_bytes: int
    max_process_threads: int
    runtime_sample_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int

    def __post_init__(self):
        if (not isinstance(self.worker_sha256, str) or len(self.worker_sha256) != 64
                or any(value not in "0123456789abcdef" for value in self.worker_sha256)):
            raise ValueError("Output observation needs the measured OCR worker digest")
        for name, value in vars(self).items():
            if name == "worker_sha256":
                continue
            if name.endswith("seconds"):
                if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                    raise ValueError("Observation timings must be explicitly finite and positive")
            elif type(value) is not int or value <= 0:
                raise ValueError("Observation limits must be explicitly positive integers")
        if (self.max_samples > 3 or self.max_width_px > 1280 or self.max_height_px > 1280
                or self.additional_memory_bytes < self.max_process_rss_bytes or self.runtime_sample_seconds > .25):
            raise ValueError("Observation needs bounded frames, reserved process memory and bounded sampling")


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
    presenter: PresenterBudget | None = None
    presenter_cpu_render: CpuRenderBudget | None = None
    cpu_audio: CpuAudioBudget | None = None
    media_script: MediaScriptBudget | None = None
    observation: ObservationBudget | None = None

    def __post_init__(self) -> None:
        if not (0 < self.max_cpu_percent <= 100 and self.sample_seconds > 0):
            raise ValueError("CPU budget and sample interval must be positive")
        if any(value < 0 for value in (
            self.min_available_memory_bytes, self.max_swap_used_bytes,
            self.min_free_disk_bytes, self.max_disk_io_bytes_per_second,
            self.max_unit_input_bytes,
        )):
            raise ValueError("Host budget cannot contain negative limits")
        if (self.cpu_render is not None or self.presenter_cpu_render is not None) and self.sample_seconds > .25:
            raise ValueError("CPU render needs a bounded process sampling interval")
        if self.scene_window is not None and (
            self.min_available_memory_bytes == 0 or self.min_free_disk_bytes == 0
        ):
            raise ValueError("Scene decode requires explicit host memory and disk headroom")
        if self.media_script is not None and (
            self.min_available_memory_bytes == 0 or self.min_free_disk_bytes == 0
        ):
            raise ValueError("Media script requires explicit host memory and disk headroom")
        if self.observation is not None and (self.min_available_memory_bytes == 0 or self.min_free_disk_bytes == 0):
            raise ValueError("Output observation requires explicit host memory and disk headroom")


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
            "io_window_incomplete": "磁盘持续观察窗口尚未完成",
            "io_observation_gap": "运行中磁盘持续观察间隔超出窗口",
            "disk_devices_changed": "磁盘采样设备集合变化",
            "disk_io_before_unavailable": "采样前的磁盘读写计数不可读取",
            "disk_io_after_unavailable": "采样后的磁盘读写计数不可读取",
            "sample_interval_nonpositive": "磁盘负载采样时长无效",
            "disk_io_counters_regressed": "磁盘读写计数回退，无法确认当前负载",
            "render_budget_or_execution_mode_unavailable": "成片预算或执行模式不可用",
            "render_numeric_fields_invalid": "成片时长或阶段时限无效",
            "render_integer_fields_invalid": "成片输入的数量或大小字段无效",
            "render_required_fields_zero": "成片必需的尺寸、数量或线程为零",
            "audio_budget_or_execution_mode_unavailable": "CPU 音频预算尚未标定或执行模式不可用",
            "audio_resource_fields_invalid": "CPU 音频资源声明缺失或无效",
            "audio_probe_contract_missing": "CPU 音频缺少固定输入与先探测再解码约定",
            "stage_budget_unavailable": "当前阶段的专属设备预算尚未标定",
            "stage_execution_mode_unavailable": "当前阶段的执行模式不受支持",
            "media_script_budget_or_execution_mode_unavailable": "隔离脚本预算未标定或执行模式不可用",
            "media_script_digest_invalid": "隔离脚本的封存摘要无效",
            "media_script_resource_fields_invalid": "隔离脚本资源声明无效",
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

    @property
    def recoverable_pressure(self) -> bool:
        if self.decision.verdict is not False or self.decision.reason is not None:
            return False
        failed = [name for name, check in self.decision.checks.items() if check.get("passed") is False]
        return bool(failed) and all(name in {
            "cpu_percent", "available_memory_bytes", "swap_used_bytes", "disk_io_bytes_per_second",
        } or name.startswith("disk_free_bytes:") for name in failed)



def audio_resource_refusal(resources, audio):
    """Reuse one calibrated CPU-audio declaration check in previews and render."""
    if audio is None or resources.get("execution_mode") != "cpu_ffmpeg":
        return (None, "audio_budget_or_execution_mode_unavailable")
    fields = {"input_count": "max_input_count", "input_bytes": "max_input_bytes",
              "source_duration_limit_seconds": "max_source_duration_seconds",
              "output_duration_limit_seconds": "max_output_duration_seconds",
              "source_sample_rate_limit": "max_sample_rate", "source_channels_limit": "max_channels",
              "output_sample_rate_limit": "max_sample_rate", "output_channels_limit": "max_channels",
              "max_output_bytes": "max_output_bytes", "threads": "max_threads",
              "timeout_seconds": "max_timeout_seconds"}
    for name, limit in fields.items():
        value = resources.get(name)
        numeric = name.endswith("seconds")
        if (type(value) not in ({int, float} if numeric else {int})
                or not math.isfinite(value) or value < 0
                or (value == 0 and name not in {"input_count", "input_bytes"})):
            return (None, "audio_resource_fields_invalid")
        if value > getattr(audio, limit):
            return (False, "declared_resources_exceed_budget")
    if resources.get("input_policy") != "probe_then_bounded_decode" or not isinstance(resources.get("inputs"), list):
        return (None, "audio_probe_contract_missing")
    if "dynamic_input_count_limit" in resources or "dynamic_input_bytes_limit" in resources:
        for name, actual, cap in (("dynamic_input_count_limit", "input_count", audio.max_input_count),
                                  ("dynamic_input_bytes_limit", "input_bytes", audio.max_input_bytes)):
            value = resources.get(name)
            if type(value) is not int or not resources[actual] <= value <= cap:
                return (None, "audio_resource_fields_invalid")
    return None


class HostResourceProbe:
    """Admit measured material work and explicitly bounded CPU render only."""

    _SUPPORTED_KINDS = frozenset({"material_hash", "material_probe_meta", "platform_publish"})

    def __init__(self, database_path: str | Path, *, disk_paths: tuple[str | Path, ...],
                 budget: HostBudget, io_paths: Mapping[str, str | Path] | None = None) -> None:
        if not disk_paths:
            raise ValueError("At least one material/output disk path is required")
        self.database_path = Path(database_path).expanduser().resolve()
        self.disk_paths = tuple(Path(path).expanduser().resolve() for path in disk_paths)
        self.budget = budget
        # Measurement history only; permissions and readiness are always fresh.
        # A32.1: bounded initial narration observation, not hardware calibration.
        self.io_window_seconds = 3.0
        self._admission_io = deque()
        self._running_io = deque()
        self._running_io_key = None
        self.io_paths = {name: Path(path).resolve() for name, path in (io_paths or {}).items()}
        self.io_paths.update({f"output_{i}": path for i, path in enumerate(self.disk_paths)})

    def _io_evidence(self, before, after, start, end, history):
        if set(before) != set(after):
            return None, "disk_devices_changed"
        if history and start - history[-1][0] > self.io_window_seconds:
            history.clear()  # Backoff is not a long averaging window or readiness cache.
            if history is self._running_io:
                return None, "io_observation_gap"
        if history and set(history[-1][1]) != set(before):
            history.clear()
            return None, "disk_devices_changed"
        if history and any(before[key].read_bytes < history[-1][1][key].read_bytes
                           or before[key].write_bytes < history[-1][1][key].write_bytes for key in before):
            history.clear()
            return None, "disk_io_counters_regressed"
        if not history:
            history.append((start, before))
        history.append((end, after))
        while len(history) > 2 and history[1][0] <= end - self.io_window_seconds:
            history.popleft()
        anchor, initial = history[0]
        fields = ("read_bytes", "write_bytes", "read_count", "write_count", "read_time", "write_time", "busy_time")
        metrics = {}
        for device in before:
            metrics[device] = {}
            for field in fields:
                left, right = getattr(initial[device], field, None), getattr(after[device], field, None)
                if left is None or right is None:
                    metrics[device][field] = "UNAVAILABLE"
                    if field in {"read_bytes", "write_bytes"}:
                        return None, "disk_io_after_unavailable"
                elif right < left:
                    return None, "disk_io_counters_regressed"
                else:
                    metrics[device][field] = {"before": left, "after": right, "delta": right - left}
            for direction in ("read", "write"):
                count, duration = metrics[device][direction + "_count"], metrics[device][direction + "_time"]
                metrics[device][direction + "_request_mean_ms"] = (
                    duration["delta"] / count["delta"]
                    if isinstance(count, dict) and isinstance(duration, dict) and count["delta"] > 0
                    else "UNAVAILABLE")
        elapsed = end - anchor
        delta = sum(after[key].read_bytes + after[key].write_bytes - initial[key].read_bytes - initial[key].write_bytes for key in after)
        if delta < 0:
            history.clear()
            return None, "disk_io_counters_regressed"
        byte_budget = self.budget.max_disk_io_bytes_per_second * max(self.io_window_seconds, elapsed)
        mappings = {}
        try:
            partitions = psutil.disk_partitions(all=True)
            for name, path in self.io_paths.items():
                matches = [p for p in partitions if path.is_relative_to(Path(p.mountpoint))]
                partition = max(matches, key=lambda p: len(p.mountpoint)) if matches else None
                device = Path(partition.device).name if partition else "UNAVAILABLE"
                physical = re.match(r"^(disk\d+)(?:s\d+)*$", device)
                counter = physical.group(1) if physical else device
                mappings[name] = {"st_dev": path.stat().st_dev, "device": device,
                                  "counter": counter if counter in after else "UNAVAILABLE"}
        except (OSError, psutil.Error) as exc:
            mappings = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
        return {"actual": delta / elapsed, "maximum": self.budget.max_disk_io_bytes_per_second,
                "passed": delta <= byte_budget, "delta_bytes": delta,
                "window_seconds": elapsed, "required_window_seconds": self.io_window_seconds,
                "window_complete": elapsed >= self.io_window_seconds,
                "finite_byte_budget": byte_budget, "sample_started_monotonic": start,
                "sample_finished_monotonic": end, "sample_interval_seconds": end - start,
                "window_started_monotonic": anchor, "window_finished_monotonic": end,
                "devices": metrics, "path_devices": mappings,
                "busy_percent": "UNAVAILABLE: request times are not utilization",
                "process_io": "UNAVAILABLE: host counters cannot attribute a process"}, None

    def _sample(self, resources: dict[str, object], *, running_io: bool = False, running_render: bool = False) -> HostResourceDecision:
        checks: dict[str, dict[str, object]] = {}

        def decision(verdict: bool | None, reason: str | None = None,
                     interval: float | None = None) -> HostResourceDecision:
            return HostResourceDecision(verdict, dict(resources), checks, reason, interval)

        kind, size = resources.get("kind"), resources.get("size_bytes")
        render = (self.budget.presenter_cpu_render if kind == "creation_presenter_render"
                  else self.budget.cpu_render if kind == "creation_render" else None)
        if kind in {"creation_render", "creation_presenter_render"}:
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
            if "dynamic_clip_count_limit" in resources or "dynamic_overlay_count_limit" in resources:
                for name, actual, cap in (("dynamic_clip_count_limit", "clip_count", render.max_clip_count),
                                          ("dynamic_overlay_count_limit", "overlay_count", render.max_overlay_count)):
                    value = resources.get(name)
                    if type(value) is not int or not resources[actual] <= value <= cap:
                        return decision(None, "render_integer_fields_invalid")
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
            required_memory = self.budget.min_available_memory_bytes + (0 if running_render else render.additional_memory_bytes)
            required_disk = (self.budget.min_free_disk_bytes + render.additional_disk_bytes
                             + resources["max_output_bytes"])
            if "audio_mix" in resources:
                audio_resources = resources["audio_mix"]
                if not isinstance(audio_resources, dict) or audio_resources.get("kind") != "task_audio":
                    return decision(None, "audio_probe_contract_missing")
                refusal = audio_resource_refusal(audio_resources, self.budget.cpu_audio)
                if refusal is not None:
                    return decision(*refusal)
                checks["audio_budget"] = {"limits": asdict(self.budget.cpu_audio)}
                required_memory = self.budget.min_available_memory_bytes + (0 if running_render else max(
                    render.additional_memory_bytes, self.budget.cpu_audio.additional_memory_bytes))
                required_disk += self.budget.cpu_audio.additional_disk_bytes + audio_resources["max_output_bytes"]
        elif kind == "task_output_observation":
            observation = self.budget.observation
            if observation is None or resources.get("execution_mode") != "local_ffmpeg_apple_vision":
                return decision(None, "observation_budget_or_execution_mode_unavailable")
            if resources.get("worker_sha256") != observation.worker_sha256:
                return decision(False, "observation_worker_not_calibrated")
            bounds = {"sample_count": "max_samples", "width_px": "max_width_px", "height_px": "max_height_px",
                      "source_pixels": "max_source_pixels",
                      "input_bytes": "max_input_bytes", "max_output_bytes": "max_output_bytes",
                      "threads": "max_threads", "timeout_seconds": "max_timeout_seconds"}
            for name, cap in bounds.items():
                value = resources.get(name)
                if (type(value) not in ({int, float} if name == "timeout_seconds" else {int})
                        or not math.isfinite(value) or value <= 0):
                    return decision(None, "observation_resource_fields_invalid")
                if value > getattr(observation, cap):
                    return decision(False, "declared_resources_exceed_budget")
            checks["observation_budget"] = {"limits": asdict(observation)}
            required_memory = self.budget.min_available_memory_bytes + observation.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + observation.additional_disk_bytes + resources["max_output_bytes"]
        elif kind == "media_script":
            script = self.budget.media_script
            if script is None or resources.get("execution_mode") != "isolated_python_pillow":
                return decision(None, "media_script_budget_or_execution_mode_unavailable")
            digest = resources.get("script_digest")
            if (type(digest) is not str or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)):
                return decision(None, "media_script_digest_invalid")
            for name in ("script_bytes", "parameters_bytes", "input_count", "input_bytes",
                         "output_count", "width_px", "height_px", "output_pixels", "max_output_bytes",
                         "max_stdout_bytes", "max_stderr_bytes", "threads", "timeout_seconds"):
                value = resources.get(name)
                if (type(value) not in ({int, float} if name == "timeout_seconds" else {int})
                        or not math.isfinite(value) or value < 0
                        or (value == 0 and name not in {"input_count", "input_bytes", "max_stdout_bytes", "max_stderr_bytes"})):
                    return decision(None, "media_script_resource_fields_invalid")
                limit = getattr(script, name if name.startswith("max_") else "max_" + name)
                if value > limit:
                    checks["media_script_budget"] = {"limits": asdict(script)}
                    return decision(False, "declared_resources_exceed_budget")
            checks["media_script_budget"] = {"limits": asdict(script)}
            required_memory = self.budget.min_available_memory_bytes
            if not running_io:
                required_memory += script.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + script.additional_disk_bytes + resources["max_output_bytes"]
        elif kind == "task_audio":
            audio = self.budget.cpu_audio
            if audio is None or resources.get("execution_mode") != "cpu_ffmpeg":
                return decision(None, "audio_budget_or_execution_mode_unavailable")
            refusal = audio_resource_refusal(resources, audio)
            if refusal is not None:
                return decision(*refusal)
            checks["audio_budget"] = {"limits": asdict(audio)}
            required_memory = self.budget.min_available_memory_bytes + audio.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + audio.additional_disk_bytes + resources["max_output_bytes"]
        elif kind in {"creation_narration", "creation_person_render", "creation_montage_plan"}:
            stage = (self.budget.presenter if kind == "creation_person_render" else
                     self.budget.narration if kind == "creation_narration" else self.budget.montage)
            if stage is None:
                return decision(None, "stage_budget_unavailable")
            bounds = ({"text_chars": stage.max_text_chars, "section_count": stage.max_section_count,
                       "reference_bytes": stage.max_reference_bytes, "duration_seconds": stage.max_duration_seconds,
                       "max_output_bytes": stage.max_output_bytes, "threads": stage.max_threads,
                       "timeout_seconds": stage.max_timeout_seconds}
                      if kind == "creation_narration" else
                      {"section_count": stage.max_section_count, "input_bytes": stage.max_input_bytes,
                       "duration_seconds": stage.max_duration_seconds, "max_output_bytes": stage.max_output_bytes,
                       "threads": stage.max_threads, "timeout_seconds": stage.max_timeout_seconds}
                      if kind == "creation_person_render" else
                      {"section_count": stage.max_section_count, "asset_count": stage.max_asset_count,
                       "input_bytes": stage.max_input_bytes, "max_output_bytes": stage.max_output_bytes,
                       "timeout_seconds": stage.max_timeout_seconds})
            expected_mode = ("isolated_avatar_lipsync" if kind == "creation_person_render" else
                             "isolated_qwen3_tts" if kind == "creation_narration" else "metadata_only")
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
            required_memory = self.budget.min_available_memory_bytes
            if kind != "creation_narration" or not running_io:
                required_memory += stage.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + stage.max_output_bytes
            if kind in {"creation_narration", "creation_person_render"}:
                required_disk += stage.additional_disk_bytes
        elif kind == "platform_publish":
            timeout = resources.get("timeout_seconds")
            if type(size) is not int or size <= 0:
                return decision(None, "platform_publish_size_invalid")
            if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
                return decision(None, "platform_publish_timeout_invalid")
            checks["size_bytes"] = {
                "actual": size, "maximum": self.budget.max_unit_input_bytes,
                "passed": size <= self.budget.max_unit_input_bytes,
            }
            checks["timeout_seconds"] = {
                "actual": timeout, "maximum": PUBLISH_OPERATION_TIMEOUT_SECONDS,
                "passed": timeout <= PUBLISH_OPERATION_TIMEOUT_SECONDS,
            }
            if size > self.budget.max_unit_input_bytes or timeout > PUBLISH_OPERATION_TIMEOUT_SECONDS:
                return decision(False, "declared_resources_exceed_budget")
            required_memory = self.budget.min_available_memory_bytes
            required_disk = self.budget.min_free_disk_bytes
        elif kind in self._SUPPORTED_KINDS - {"platform_publish"}:
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
            sustained = kind in {"creation_narration", "creation_person_render", "media_script"}
            before = psutil.disk_io_counters(perdisk=True) if sustained else psutil.disk_io_counters()
            if before is None or (sustained and not before):
                return decision(None, "disk_io_before_unavailable")
            start = time.monotonic()
            sampling = "cpu_percent"
            cpu = psutil.cpu_percent(interval=self.budget.sample_seconds)
            checks["cpu_percent"] = {"actual": cpu, "maximum": self.budget.max_cpu_percent,
                                     "passed": cpu <= self.budget.max_cpu_percent}
            sampling = "disk_io_after"
            after = psutil.disk_io_counters(perdisk=True) if sustained else psutil.disk_io_counters()
            end = time.monotonic()
            duration = end - start
            if after is None or (sustained and not after):
                return decision(None, "disk_io_after_unavailable", duration)
            if duration <= 0:
                return decision(None, "sample_interval_nonpositive", duration)
            if sustained:
                evidence, unavailable = self._io_evidence(before, after, start, end,
                    self._running_io if running_io else self._admission_io)
                if unavailable:
                    return decision(None, unavailable, duration)
                checks["disk_io_bytes_per_second"] = evidence
            else:
                io_bytes = (after.read_bytes - before.read_bytes + after.write_bytes - before.write_bytes)
                if io_bytes < 0:
                    checks["disk_io_bytes"] = {"actual": io_bytes, "before_read_bytes": before.read_bytes,
                        "before_write_bytes": before.write_bytes, "after_read_bytes": after.read_bytes,
                        "after_write_bytes": after.write_bytes}
                    return decision(None, "disk_io_counters_regressed", duration)
                checks["disk_io_bytes_per_second"] = {
                    "actual": io_bytes / duration, "maximum": self.budget.max_disk_io_bytes_per_second,
                    "passed": io_bytes / duration <= self.budget.max_disk_io_bytes_per_second,
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
        passed = (cpu <= self.budget.max_cpu_percent
                        and memory.available >= required_memory
                        and swap.used <= self.budget.max_swap_used_bytes
                        and all(disk.free >= required_disk for disk in disks)
                        and checks["disk_io_bytes_per_second"]["passed"])
        if sustained and passed and not running_io and not evidence["window_complete"]:
            return decision(None, "io_window_incomplete", duration)
        return decision(passed, interval=duration)

    def admit(self, request: WorkRequest) -> HostResourceDecision:
        decision = self._sample(request.resources)
        if decision.verdict is not True:
            logger.warning("Device resource admission: %s", json.dumps({
                "request_id": request.id,
                "verdict": decision.verdict,
                # Only resource dimensions, never the full request or its content.
                "declared": {name: decision.declared[name] for name in (
                    "size_bytes", "duration_seconds", "timeout_seconds",
                    "width_px", "height_px", "input_bytes", "clip_count",
                    "overlay_count", "max_output_bytes", "threads", "text_chars",
                    "section_count", "reference_bytes", "source_frame_rate_fps",
                    "scan_seconds", "output_sample_fps", "source_width_px",
                    "source_height_px", "output_max_width_px", "output_max_frames",
                    "ffmpeg_threads",
                ) if type(decision.declared.get(name)) in {int, float}},
                "checks": decision.checks,
                "reason": decision.reason,
                "observed_interval_seconds": decision.observed_interval_seconds,
            }, ensure_ascii=False))
        return decision

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
        if (resources is not None and resources.get("kind") in {"creation_narration", "creation_person_render"}
                and (self.budget.presenter if resources["kind"] == "creation_person_render" else self.budget.narration) is not None):
            key = (grant.request_id, grant.generation)
            if key != self._running_io_key:
                self._running_io.clear()
                self._running_io_key = key
            decision = self._sample(resources, running_io=True)
        elif resources is not None and resources.get("kind") in {"creation_render", "creation_presenter_render"}:
            decision = self._sample(resources, running_render=True)
        else:
            decision = (self._sample(resources) if resources is not None else
                    HostResourceDecision(None, {}, {}, "granted_resources_unreadable"))
        if decision.verdict is not True:
            raise HostResourceRefusal(decision)
        return True

    def scene_runtime_limits(self, grant: Grant, *, purpose: str | None = None) -> SceneRuntimeLimits | None:
        resources = self._grant_resources(grant)
        if resources is None:
            raise RecoveryBlocked("获许可资源事实不可读取，不能执行子进程")
        if purpose is not None:
            if (purpose != "audio_mix" or resources.get("kind") not in {"creation_render", "creation_presenter_render"}
                    or not isinstance(resources.get("audio_mix"), dict)
                    or resources["audio_mix"].get("kind") != "task_audio"):
                raise RecoveryBlocked("当前许可未封存组合音频执行用途")
            refusal = audio_resource_refusal(resources["audio_mix"], self.budget.cpu_audio)
            if refusal is not None:
                raise RecoveryBlocked("组合音频子进程预算尚未标定或许可声明无效")
            resources = resources["audio_mix"]
        if resources.get("kind") in {"creation_render", "creation_presenter_render"}:
            render = self.budget.presenter_cpu_render if resources["kind"] == "creation_presenter_render" else self.budget.cpu_render
            if render is None or resources.get("execution_mode") != "cpu_ffmpeg":
                raise RecoveryBlocked("CPU 成片进程预算尚未标定或执行模式无效")
            return SceneRuntimeLimits(render.additional_memory_bytes, render.max_process_threads,
                self.budget.min_available_memory_bytes, self.budget.max_swap_used_bytes, self.budget.sample_seconds)
        if resources.get("kind") == "media_script":
            script = self.budget.media_script
            if script is None or resources.get("execution_mode") != "isolated_python_pillow":
                raise RecoveryBlocked("隔离脚本进程预算尚未标定或执行模式无效")
            return SceneRuntimeLimits(script.max_process_rss_bytes, script.max_process_threads,
                self.budget.min_available_memory_bytes, self.budget.max_swap_used_bytes, script.runtime_sample_seconds)
        if resources.get("kind") == "task_output_observation":
            observation = self.budget.observation
            if observation is None or resources.get("execution_mode") != "local_ffmpeg_apple_vision":
                raise RecoveryBlocked("本地输出 OCR 进程预算尚未标定")
            return SceneRuntimeLimits(observation.max_process_rss_bytes, observation.max_process_threads,
                self.budget.min_available_memory_bytes, self.budget.max_swap_used_bytes, observation.runtime_sample_seconds)
        if resources.get("kind") == "task_audio":
            audio = self.budget.cpu_audio
            if audio is None:
                raise RecoveryBlocked("CPU 音频进程预算尚未标定")
            return SceneRuntimeLimits(audio.max_process_rss_bytes, audio.max_process_threads,
                self.budget.min_available_memory_bytes, self.budget.max_swap_used_bytes, audio.runtime_sample_seconds)
        if resources.get("kind") in {"creation_narration", "creation_person_render"}:
            narration = (self.budget.presenter if resources["kind"] == "creation_person_render"
                         else self.budget.narration)
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
        # This probe owns no persistent browser context: Chrome is held by the
        # application-scoped session manager and closed on API shutdown. The
        # grant's registered process groups are independently checked by the
        # DeviceScheduler before this per-unit resource lease is released.
        if resources is None:
            return None
        if resources.get("kind") in self._SUPPORTED_KINDS:
            return True
        if (resources.get("kind") == "task_output_observation"
                and resources.get("execution_mode") == "local_ffmpeg_apple_vision"):
            return True  # Scheduler proves registered decode/OCR child groups exited.
        if resources.get("kind") == "creation_narration" and resources.get("execution_mode") == "isolated_qwen3_tts":
            # The scheduler first proves every registered child group exited.
            # MLX exists only inside that group, never the API process.
            return True
        if resources.get("kind") == "creation_person_render" and resources.get("execution_mode") == "isolated_avatar_lipsync":
            return True  # Resident model allocations live only in the proven-exited child group.
        if resources.get("kind") == "creation_montage_plan" and resources.get("execution_mode") == "metadata_only":
            return True
        if (resources.get("kind") in {"creation_render", "creation_presenter_render"}
                and resources.get("execution_mode") == "cpu_ffmpeg"):
            return True
        if resources.get("kind") == "task_audio" and resources.get("execution_mode") == "cpu_ffmpeg":
            return True
        if resources.get("kind") == "media_script" and resources.get("execution_mode") == "isolated_python_pillow":
            return True  # Existing scheduler first proves the registered single-process group exited.
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
            except (OSError, psutil.Error, RuntimeError) as exc:
                # macOS create_time reads kinfo_proc through sysctl. Its exact
                # short-read error can race exit; unrelated RuntimeError still
                # propagates, and holder handling retains the original rule.
                if isinstance(exc, RuntimeError) and (
                    not group or sys.platform != "darwin" or str(exc) != "sysctl() size mismatch"
                ):
                    raise
                if not group:
                    return "unknown"
                # Only the already registered PGID's definite absence proves
                # release. A surviving descendant, EPERM or any other error
                # keeps the metadata failure unknown.
                try:
                    os.killpg(child.pid, 0)
                except OSError as group_error:
                    if group_error.errno == errno.ESRCH:
                        return "released"
                return "unknown"
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
