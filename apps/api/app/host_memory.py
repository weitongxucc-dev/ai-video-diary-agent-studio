"""Native memory facts and one admission/running policy; no guessed production profile.

Darwin sysctl exports dispatch flags (1/2/4), not XNU's internal 0/1/2/3 enum:
https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c
Windows notifications have a middle state; commit pages are not working set:
https://learn.microsoft.com/windows/win32/api/memoryapi/nf-memoryapi-creatememoryresourcenotification
https://learn.microsoft.com/windows/win32/api/psapi/ns-psapi-performance_information
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import math
import sys
import threading
import time

import psutil


@dataclass(frozen=True)
class MemoryPolicy:
    platform: str
    recovery_stable_seconds: float
    max_sample_gap_seconds: float
    evidence_reference: str
    min_commit_headroom_bytes: int | None = None

    def __post_init__(self):
        if self.platform not in {"darwin", "win32"} or not self.evidence_reference.strip():
            raise ValueError("Memory policy needs a supported platform and reviewed evidence reference")
        if any(type(value) not in {int, float} or not math.isfinite(value) or value <= 0
               for value in (self.recovery_stable_seconds, self.max_sample_gap_seconds)):
            raise ValueError("Memory recovery and observation windows must be explicitly positive")
        if self.max_sample_gap_seconds > self.recovery_stable_seconds:
            raise ValueError("A recovery window must contain continuous observations")
        if self.platform == "win32" and (type(self.min_commit_headroom_bytes) is not int or self.min_commit_headroom_bytes <= 0):
            raise ValueError("Windows memory policy needs a separately calibrated commit reserve")
        if self.platform == "darwin" and self.min_commit_headroom_bytes is not None:
            raise ValueError("Windows commit reserve is not a Darwin physical-memory metric")


def validate_observation_cadence(policy: MemoryPolicy, *, device_poll_interval_seconds: float,
                                 probe_seconds: float) -> None:
    """Reject a profile that cannot span even one configured production poll/probe."""
    if any(type(value) not in {int, float} or not math.isfinite(value) or value <= 0
           for value in (device_poll_interval_seconds, probe_seconds)):
        raise ValueError("Memory recovery needs the actual device poll and probe intervals")
    if policy.max_sample_gap_seconds <= device_poll_interval_seconds + probe_seconds:
        raise ValueError("Memory max sample gap must exceed device poll + probe time; calibration must also cover measured overhead")


@dataclass(frozen=True)
class MemorySample:
    platform: str
    observed_monotonic: float
    observed_at_utc: str
    pressure: str
    available_bytes: int | None
    commit_headroom_bytes: int | None
    facts: dict[str, dict]


@dataclass(frozen=True)
class MemoryDecision:
    verdict: bool | None
    reason: str | None
    sample: MemorySample
    required_available_bytes: int
    required_commit_headroom_bytes: int | None
    stable_elapsed_seconds: float
    recovery_required_seconds: float | None = None
    max_sample_gap_seconds: float | None = None
    policy_evidence_reference: str | None = None
    system_reserve_bytes: int | None = None
    additional_memory_bytes: int | None = None

    def payload(self):
        return asdict(self)


@lru_cache(maxsize=1)
def _darwin_library():
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    lib.sysctlbyname.restype = ctypes.c_int
    lib.mach_host_self.restype = ctypes.c_uint
    lib.host_statistics64.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    lib.host_statistics64.restype = ctypes.c_int
    lib.host_page_size.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    lib.host_page_size.restype = ctypes.c_int
    return lib, lib.mach_host_self()


class _VMStatisticsPrefix(ctypes.Structure):
    # Public mach/vm_statistics.h prefix through compressor_page_count (rev1).
    _fields_ = [(name, ctypes.c_uint) for name in ("free_count", "active_count", "inactive_count", "wire_count")] + [
        (name, ctypes.c_uint64) for name in ("zero_fill_count", "reactivations", "pageins", "pageouts", "faults",
            "cow_faults", "lookups", "hits", "purges")] + [
        ("purgeable_count", ctypes.c_uint), ("speculative_count", ctypes.c_uint)] + [
        (name, ctypes.c_uint64) for name in ("decompressions", "compressions", "swapins", "swapouts")] + [
        ("compressor_page_count", ctypes.c_uint)]


def _darwin_facts():
    lib, host = _darwin_library()
    raw = ctypes.c_uint()
    size = ctypes.c_size_t(ctypes.sizeof(raw))
    if lib.sysctlbyname(b"kern.memorystatus_vm_pressure_level", ctypes.byref(raw), ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "native memory pressure sysctl unavailable")
    pressure = {1: "normal", 2: "warning", 4: "critical"}.get(raw.value, "unknown")
    facts = {"pressure": {"value": pressure, "native_value": raw.value, "unit": "dispatch_flags",
        "source": "sysctlbyname(kern.memorystatus_vm_pressure_level)"}}
    # Bounded buffer accepts newer struct revisions without guessing their layout.
    buffer = (ctypes.c_uint * 256)()
    count = ctypes.c_uint(256)
    page_size = ctypes.c_uint()
    if (lib.host_statistics64(host, 4, buffer, ctypes.byref(count)) == 0
            and count.value * 4 >= ctypes.sizeof(_VMStatisticsPrefix)
            and lib.host_page_size(host, ctypes.byref(page_size)) == 0 and page_size.value > 0):
        stats = _VMStatisticsPrefix.from_buffer(buffer)
        facts["compressed_bytes"] = {"value": stats.compressor_page_count * page_size.value,
            "unit": "bytes", "source": "host_statistics64.compressor_page_count * host_page_size"}
        for name in ("pageins", "pageouts", "swapins", "swapouts"):
            facts[name] = {"value": getattr(stats, name), "unit": "pages_since_boot",
                "source": f"host_statistics64.{name}", "page_size_bytes": page_size.value}
    else:
        facts["paging"] = {"value": None, "unit": "pages_since_boot", "source": "host_statistics64",
                           "unavailable": "native VM statistics unavailable"}
    available = psutil.virtual_memory().available
    facts["available_bytes"] = {"value": available, "unit": "bytes", "source": "psutil.darwin: free + inactive"}
    return pressure, available, None, facts


def _windows_facts():
    # WinDLL is intentionally accessed only on Windows. No POSIX process claim.
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMemoryResourceNotification.argtypes = [ctypes.c_int]
    kernel.CreateMemoryResourceNotification.restype = wintypes.HANDLE
    kernel.QueryMemoryResourceNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.QueryMemoryResourceNotification.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    states = []
    for kind in (0, 1):
        handle = kernel.CreateMemoryResourceNotification(kind)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Windows memory notification unavailable")
        try:
            value = wintypes.BOOL()
            if not kernel.QueryMemoryResourceNotification(handle, ctypes.byref(value)):
                raise OSError(ctypes.get_last_error(), "Windows memory notification query failed")
            states.append(bool(value.value))
        finally:
            kernel.CloseHandle(handle)
    low, high = states
    pressure = "unknown" if low and high else "critical" if low else "normal" if high else "warning"
    class PerformanceInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
            "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable", "SystemCache",
            "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")] + [
            (name, wintypes.DWORD) for name in ("HandleCount", "ProcessCount", "ThreadCount")]
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetPerformanceInfo.argtypes = [ctypes.POINTER(PerformanceInfo), wintypes.DWORD]
    psapi.GetPerformanceInfo.restype = wintypes.BOOL
    info = PerformanceInfo()
    info.cb = ctypes.sizeof(info)
    if (not psapi.GetPerformanceInfo(ctypes.byref(info), info.cb) or not info.PageSize
            or not info.PhysicalTotal or info.PhysicalAvailable > info.PhysicalTotal or not info.CommitLimit):
        raise OSError(ctypes.get_last_error(), "Windows physical/commit facts unavailable")
    available = info.PhysicalAvailable * info.PageSize
    commit = (info.CommitLimit - info.CommitTotal) * info.PageSize if info.CommitLimit >= info.CommitTotal else None
    facts = {"pressure": {"value": pressure, "unit": "notification_state", "source": "QueryMemoryResourceNotification",
        "low_memory": low, "high_memory": high},
        "available_bytes": {"value": available, "unit": "bytes", "source": "GetPerformanceInfo.PhysicalAvailable * PageSize"},
        "commit_headroom_bytes": {"value": commit, "unit": "bytes", "source": "GetPerformanceInfo.(CommitLimit-CommitTotal) * PageSize"},
        "commit_total_bytes": {"value": info.CommitTotal * info.PageSize, "unit": "bytes", "source": "GetPerformanceInfo.CommitTotal * PageSize"},
        "commit_limit_bytes": {"value": info.CommitLimit * info.PageSize, "unit": "bytes", "source": "GetPerformanceInfo.CommitLimit * PageSize"},
        "paging": {"value": None, "unit": "pages_per_second", "source": "not_integrated", "unavailable": "psutil Windows sin/sout zero is not a paging measurement"},
        "compressed_bytes": {"value": None, "unit": "bytes", "source": "not_integrated"}}
    return pressure, available, commit, facts


def sample_host_memory() -> MemorySample:
    facts = {}
    pressure, available, commit = "unknown", None, None
    try:
        if sys.platform == "darwin":
            pressure, available, commit, facts = _darwin_facts()
        elif sys.platform == "win32":
            pressure, available, commit, facts = _windows_facts()
        else:
            facts["pressure"] = {"value": None, "unit": "state", "source": sys.platform, "unavailable": "native platform pressure adapter not implemented"}
    except (OSError, ValueError, AttributeError, ctypes.ArgumentError, psutil.Error) as exc:
        facts["pressure"] = {"value": None, "unit": "state", "source": sys.platform, "unavailable": str(exc)}
    try:
        facts["swap_used_bytes"] = {"value": psutil.swap_memory().used, "unit": "bytes", "source": "psutil.swap_memory.used", "diagnostic_only": True}
    except (OSError, psutil.Error) as exc:
        facts["swap_used_bytes"] = {"value": None, "unit": "bytes", "source": "psutil.swap_memory.used", "diagnostic_only": True, "unavailable": str(exc)}
    return MemorySample(sys.platform, time.monotonic(), datetime.now(timezone.utc).isoformat(), pressure, available, commit, facts)


class _RusageInfoV0(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_ubyte * 16)] + [(name, ctypes.c_uint64) for name in (
        "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size",
        "resident_size", "phys_footprint", "proc_start_abstime", "proc_exit_abstime")]


def process_memory_facts(pid: int) -> dict[str, dict]:
    """Separate process accounting; neither RSS nor footprint substitutes OS pressure."""
    facts = {}
    try:
        rss = psutil.Process(pid).memory_info().rss
        key = "working_set_bytes" if sys.platform == "win32" else "resident_set_bytes"
        facts[key] = {"value": rss, "unit": "bytes", "source": "psutil.Process.memory_info.rss"}
    except (OSError, psutil.Error) as exc:
        facts["resident_set_bytes"] = {"value": None, "unit": "bytes", "source": "psutil", "unavailable": str(exc)}
    facts["physical_footprint_bytes"] = {"value": None, "unit": "bytes", "source": "platform_metric_not_available"}
    if sys.platform == "win32":
        facts["private_commit_bytes"] = {"value": None, "unit": "bytes", "source": "not_integrated"}
    if sys.platform == "darwin":
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            lib.proc_pid_rusage.restype = ctypes.c_int
            info = _RusageInfoV0()
            if lib.proc_pid_rusage(pid, 0, ctypes.byref(info)) != 0:
                raise OSError(ctypes.get_errno(), "process footprint unavailable")
            facts["physical_footprint_bytes"] = {"value": info.phys_footprint, "unit": "bytes",
                                                  "source": "proc_pid_rusage(RUSAGE_INFO_V0).ri_phys_footprint"}
        except (OSError, AttributeError) as exc:
            facts["physical_footprint_bytes"]["unavailable"] = str(exc)
    return facts


class HostMemoryMonitor:
    """One device-wide recovery window shared by admission and registered children."""
    def __init__(self, policy: MemoryPolicy | None, *, sampler=sample_host_memory, clock=time.monotonic):
        self.policy, self.sampler, self.clock = policy, sampler, clock
        self._healthy_since = self._last_observed = None
        self._lock = threading.Lock()

    def check(self, *, system_reserve_bytes: int, additional_memory_bytes: int = 0,
              additional_commit_bytes: int | None = None) -> MemoryDecision:
        if (type(system_reserve_bytes) is not int or system_reserve_bytes <= 0
                or type(additional_memory_bytes) is not int or additional_memory_bytes < 0
                or (additional_commit_bytes is not None
                    and (type(additional_commit_bytes) is not int or additional_commit_bytes < 0))):
            raise ValueError("Memory checks require explicit positive system headroom and nonnegative new demand")
        required_available_bytes = system_reserve_bytes + additional_memory_bytes
        sample = self.sampler()
        with self._lock:
            now = sample.observed_monotonic
            policy = self.policy
            commit_required = (policy.min_commit_headroom_bytes + additional_commit_bytes
                               if policy is not None and policy.platform == "win32" and additional_commit_bytes is not None else None)
            def result(verdict, reason, elapsed):
                return MemoryDecision(verdict, reason, sample, required_available_bytes, commit_required, elapsed,
                    policy.recovery_stable_seconds if policy else None,
                    policy.max_sample_gap_seconds if policy else None,
                    policy.evidence_reference if policy else None, system_reserve_bytes, additional_memory_bytes)
            def refuse(verdict, reason):
                self._healthy_since = None
                self._last_observed = now
                return result(verdict, reason, 0.)
            if sample.pressure == "critical":
                return refuse(False, "memory_pressure_critical")
            if sample.available_bytes is not None and sample.available_bytes < system_reserve_bytes:
                return refuse(False, "memory_headroom_insufficient")
            if policy is None:
                return refuse(None, "memory_policy_unavailable")
            if sample.platform != policy.platform or sample.pressure not in {"normal", "warning"} or sample.available_bytes is None:
                return refuse(None, "memory_measurement_unavailable")
            if sample.platform == "win32":
                if sample.commit_headroom_bytes is None:
                    return refuse(None, "memory_commit_measurement_unavailable")
                if sample.commit_headroom_bytes < policy.min_commit_headroom_bytes:
                    return refuse(False, "memory_commit_headroom_insufficient")
            current = self.clock()
            if now > current or current - now > policy.max_sample_gap_seconds:
                return refuse(None, "memory_observation_stale")
            gap = self._last_observed is not None and now - self._last_observed > policy.max_sample_gap_seconds
            if self._last_observed is None or now < self._last_observed or gap:
                self._healthy_since = now
            if self._healthy_since is None:
                self._healthy_since = now
            self._last_observed = now
            elapsed = now - self._healthy_since
            # Queue-specific shortages must never clear device-wide recovery.
            if sample.available_bytes < required_available_bytes:
                return result(False, "memory_task_budget_insufficient", elapsed)
            if sample.platform == "win32":
                if commit_required is None:
                    return result(None, "memory_commit_budget_unavailable", elapsed)
                if sample.commit_headroom_bytes < commit_required:
                    return result(False, "memory_task_commit_budget_insufficient", elapsed)
            if elapsed < policy.recovery_stable_seconds:
                if gap:
                    return result(None, "memory_observation_gap", elapsed)
                return result(False, "memory_recovery_stabilizing", elapsed)
            return result(True, None, elapsed)
