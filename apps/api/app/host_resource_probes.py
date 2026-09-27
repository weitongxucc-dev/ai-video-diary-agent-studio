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
from dataclasses import dataclass
from pathlib import Path
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

    def _sample(self, resources: dict[str, object]) -> bool | None:
        kind, size = resources.get("kind"), resources.get("size_bytes")
        render = self.budget.cpu_render if kind == "creation_render" else None
        if kind == "creation_render":
            if render is None or resources.get("execution_mode") != "cpu_ffmpeg":
                return None
            numeric_fields = ("duration_seconds", "timeout_seconds")
            integer_fields = ("width_px", "height_px", "input_bytes", "clip_count",
                              "overlay_count", "max_output_bytes", "threads")
            if any(type(resources.get(name)) not in {int, float}
                   or not math.isfinite(resources[name]) or resources[name] <= 0
                   for name in numeric_fields):
                return None
            if any(type(resources.get(name)) is not int or resources[name] < 0
                   for name in integer_fields):
                return None
            if any(resources[name] == 0 for name in
                   ("width_px", "height_px", "clip_count", "max_output_bytes", "threads")):
                return None
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
                return False
            required_memory = self.budget.min_available_memory_bytes + render.additional_memory_bytes
            required_disk = (self.budget.min_free_disk_bytes + render.additional_disk_bytes
                             + resources["max_output_bytes"])
        elif kind in self._SUPPORTED_KINDS:
            if type(size) is not int or size < 0:
                return None
            if size > self.budget.max_unit_input_bytes:
                return False
            required_memory = self.budget.min_available_memory_bytes
            required_disk = self.budget.min_free_disk_bytes
        elif kind == "material_scene_window":
            scene = self.budget.scene_window
            if scene is None or resources.get("source_codec") not in scene.allowed_source_codecs:
                return None
            real_fields = ("source_frame_rate_fps", "scan_seconds", "output_sample_fps",
                           "timeout_seconds")
            int_fields = ("size_bytes", "source_width_px", "source_height_px",
                          "output_max_width_px", "output_max_frames", "ffmpeg_threads")
            if (any(type(resources.get(name)) not in {int, float}
                    or not math.isfinite(resources[name]) or resources[name] <= 0
                    for name in real_fields)
                    or any(type(resources.get(name)) is not int or resources[name] <= 0
                           for name in int_fields)):
                return None
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
                return False
            if resources["output_sample_fps"] * resources["scan_seconds"] > resources["output_max_frames"]:
                return None
            required_memory = self.budget.min_available_memory_bytes + scene.additional_memory_bytes
            required_disk = self.budget.min_free_disk_bytes + scene.additional_disk_bytes
        else:
            return None
        try:
            before = psutil.disk_io_counters()
            if before is None:
                return None
            start = time.monotonic()
            cpu = psutil.cpu_percent(interval=self.budget.sample_seconds)
            duration = time.monotonic() - start
            after = psutil.disk_io_counters()
            if after is None or duration <= 0:
                return None
            io_bytes = (after.read_bytes - before.read_bytes +
                        after.write_bytes - before.write_bytes)
            if io_bytes < 0:
                return None
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disks = tuple(psutil.disk_usage(str(path)) for path in self.disk_paths)
        except (OSError, psutil.Error, ValueError):
            return None
        return (cpu <= self.budget.max_cpu_percent
                and memory.available >= required_memory
                and swap.used <= self.budget.max_swap_used_bytes
                and all(disk.free >= required_disk for disk in disks)
                and io_bytes / duration <= self.budget.max_disk_io_bytes_per_second)

    def admit(self, request: WorkRequest) -> bool | None:
        return self._sample(request.resources)

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
        return self._sample(resources) if resources is not None else None

    def scene_runtime_limits(self, grant: Grant) -> SceneRuntimeLimits | None:
        resources = self._grant_resources(grant)
        if resources is None:
            raise RecoveryBlocked("获许可资源事实不可读取，不能执行子进程")
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
