"""POSIX subprocess gate: durable child registration precedes exec ACK."""

from __future__ import annotations

import os
import json
import logging
import math
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from pathlib import Path

import psutil

from app.device_scheduler import DeviceScheduler, Grant, ProcessIdentity, RecoveryBlocked
from app.host_resource_probes import HostProcessProbe, HostResourceProbe, SceneRuntimeLimits, HostResourceDecision, HostResourceRefusal


logger = logging.getLogger(__name__)
_PERMISSION_SAMPLE_SECONDS = 1.0
_QWEN_PHASE = re.compile(rb"QWEN_PHASE (?:worker_started|importing_runtime|loading_weights|weights_loaded|inference_started|inference_returned|audio_written)")
_QWEN_MEMORY = re.compile(rb"QWEN_MEMORY phase=(?:cache_disabled|weights_loaded|inference_started|inference_returned|audio_written) active_bytes=[0-9]{1,20} cache_bytes=[0-9]{1,20} peak_bytes=[0-9]{1,20}")
_CPU_POLICY = re.compile(rb"GRANT_CPU_POLICY mode=(?:darwin_background state=(?:verified background=1 errno=0|failed errno=[0-9]{1,10})|darwin_nice state=(?:verified nice=(?:[1-9]|1[0-9]|20) background=0 errno=0|failed errno=[0-9]{1,10}))")


def _record_command_diagnostics(grant, child, diagnostic, *, failure=None):
    """Keep only fixed worker markers from the already bounded pipe buffers."""
    if not isinstance(grant, Grant):
        return
    markers = {stream: [line.decode("ascii") for line in diagnostic[stream].splitlines()
        if _QWEN_PHASE.fullmatch(line) or _QWEN_MEMORY.fullmatch(line)]
        for stream in ("stdout", "stderr")}
    cpu_policy = [line.decode("ascii") for line in diagnostic["stderr"].splitlines() if _CPU_POLICY.fullmatch(line)]
    if failure is None and diagnostic["returncode"] == 0 and not any(markers.values()) and not cpu_policy:
        return
    payload = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_id": grant.request_id, "workspace_id": grant.workspace_id,
        "owner_type": grant.owner_type, "owner_id": grant.owner_id,
        "attempt_number": grant.attempt_number, "permit_generation": grant.generation,
        "child": {"pid": child.pid, "start_token": child.start_token, "boot_id": child.boot_id} if child else None,
        "failure_type": type(failure).__name__ if failure is not None else None,
        "qwen_markers": markers,
        "cpu_policy_markers": cpu_policy,
        **{key: diagnostic[key] for key in ("stdout_truncated", "stderr_truncated", "exec_ack",
            "group_released", "before_termination_returncode", "returncode")}}
    if isinstance(failure, HostResourceRefusal):
        payload["decision"] = failure.decision.diagnostic_payload()
    if isinstance(failure, GrantedCommandResourceLimit):
        payload["process_resource_sample"] = failure.resource_sample
    business = getattr(failure, "business_failure", None)
    if isinstance(business, dict) and business.get("source") == "owner_current" and business.get("verdict") in {"unknown", "unauthorized", "dependency"}:
        payload["business_failure"] = {"source": business["source"], "verdict": business["verdict"]}
        verification_id = business.get("verification_id")
        if isinstance(verification_id, str) and re.fullmatch(r"[0-9a-f]{32}", verification_id):
            payload["business_failure"]["verification_id"] = verification_id
    if diagnostic.get("output_file_sample") is not None:
        payload["output_file_sample"] = diagnostic["output_file_sample"]
    if diagnostic.get("process_cpu_observation") is not None:
        payload["process_cpu_observation"] = diagnostic["process_cpu_observation"]
    if diagnostic.get("release_observations"):
        payload["release_observations"] = diagnostic["release_observations"]
    if diagnostic.get("process_resource_observation") is not None:
        payload["process_resource_observation"] = diagnostic["process_resource_observation"]
    # These bounded execution receipts must reach the existing API stderr log,
    # including successful worker runs when application INFO logging is disabled.
    logger.warning("Granted command diagnostic: %s", json.dumps(payload, ensure_ascii=False))


@dataclass(frozen=True)
class GrantedCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class GrantedCommandError(RuntimeError):
    """The command failed after its process tree was confirmed released."""


class GrantedCommandTimeout(GrantedCommandError):
    pass


class GrantedCommandCancelled(GrantedCommandError):
    pass


class GrantedCommandOutputLimit(GrantedCommandError):
    def __init__(self, message: str, *, file_sample: dict[str, int] | None = None):
        self.file_sample = file_sample
        super().__init__(message)


class GrantedCommandResourceLimit(GrantedCommandError):
    """Measured process RSS or thread count exceeded the calibrated scene cap."""

    def __init__(self, *, rss: int, threads: int, limits: SceneRuntimeLimits):
        measured = {"rss_bytes": rss, "threads": threads}
        caps = {"rss_bytes": limits.max_process_rss_bytes,
                "threads": limits.max_process_threads}
        exceeded = [key for key in measured if measured[key] > caps[key]]
        self.resource_sample = {"measured": measured, "limits": caps, "exceeded": exceeded}
        self.resource_sample["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
        self.resource_sample["observed_monotonic"] = time.monotonic()
        labels = {"rss_bytes": "进程组内存（字节）", "threads": "进程组线程数"}
        detail = "；".join(f"{labels[key]} {measured[key]} > 上限 {caps[key]}" for key in exceeded)
        super().__init__(f"{detail}。请核对本次实测与场景预算后再运行。")


class GrantedCommandResourceUnavailable(GrantedCommandError):
    """Host pressure or an unavailable scene process sample stopped execution."""

    def __init__(self, message: str, *, memory_decision=None):
        self.memory_decision = memory_decision
        super().__init__(message)


class GrantedCommandMemoryPressure(GrantedCommandResourceUnavailable, HostResourceRefusal):
    """Existing creation and material executors both recognize a recoverable wait."""

    def __init__(self, memory):
        self.memory_decision = memory.payload()
        checks = {"memory_policy": {"passed": memory.verdict, "actual": memory.sample.pressure,
                                     **self.memory_decision}}
        HostResourceRefusal.__init__(self, HostResourceDecision(memory.verdict, {}, checks, memory.reason))


def _check_scene_runtime(process: subprocess.Popen[bytes], child: ProcessIdentity,
                         limits: SceneRuntimeLimits, resource_observation=None) -> dict | None:
    resource_sample = None
    sampled_members = 0
    observed_rss = observed_threads = rss_members = thread_members = 0
    enumeration_complete = True
    incomplete_reason = "no_complete_group_sample"
    def resource_snapshot(complete):
        return {"rss_bytes": observed_rss if rss_members else None,
            "threads": observed_threads if thread_members else None,
            "sample_complete": complete, "is_lower_bound": not complete,
            "rss_members": rss_members, "thread_members": thread_members,
            "observed_monotonic": time.monotonic(), "observed_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        if process.poll() is None and f"{psutil.Process(process.pid).create_time():.6f}" != child.start_token:
            raise GrantedCommandResourceUnavailable("Child identity changed during sampling")
        processes = []
        for current in psutil.process_iter():
            try:
                if os.getpgid(current.pid) == child.pid:
                    processes.append(current)
            except ProcessLookupError:
                enumeration_complete = False
                incomplete_reason = "process_disappeared_during_enumeration"
                continue
            except PermissionError:
                # An unrelated process cannot be in our own accessible group.
                enumeration_complete = False
                incomplete_reason = "process_identity_unreadable_during_enumeration"
                continue
        rss = 0
        threads = 0
        cpu_started = time.monotonic() if limits.collect_cpu else None
        cpu_members = []
        cpu_available = True
        for current in processes:
            try:
                member_rss = current.memory_info().rss
                rss += member_rss
                observed_rss += member_rss
                rss_members += 1
                member_threads = current.num_threads()
                threads += member_threads
                observed_threads += member_threads
                thread_members += 1
                sampled_members += 1
                if limits.collect_cpu:
                    try:
                        times = current.cpu_times()
                        if not all(math.isfinite(value) and value >= 0 for value in (times.user, times.system)):
                            raise ValueError("Invalid process CPU counters")
                        cpu_members.append({"pid": current.pid, "start_token": f"{current.create_time():.6f}",
                            "user": times.user, "system": times.system})
                    except (psutil.Error, OSError, ValueError):
                        cpu_available = False  # Diagnostic failure does not negate verified OS scheduling.
            except psutil.NoSuchProcess as exc:
                incomplete_reason = "process_disappeared_during_sample"
                if current.pid == process.pid and process.poll() is None:
                    # macOS can remove the pid just before waitpid exposes its
                    # exit. Reap only this registered Popen; unknown/live stays refused.
                    try:
                        process.wait(timeout=.01)
                    except subprocess.TimeoutExpired:
                        raise GrantedCommandResourceUnavailable("Scene child disappeared during sampling") from exc
        if resource_observation is not None and (rss_members or thread_members):
            complete = enumeration_complete and sampled_members == len(processes)
            resource_sample = resource_snapshot(complete)
        if rss > limits.max_process_rss_bytes or threads > limits.max_process_threads:
            raise GrantedCommandResourceLimit(rss=rss, threads=threads, limits=limits)
        if limits.memory_monitor is None:
            raise GrantedCommandResourceUnavailable("Native memory policy is unavailable")
        memory = limits.memory_monitor.check(system_reserve_bytes=limits.min_host_available_memory_bytes,
                                              additional_commit_bytes=0)
        if memory.verdict is not True:
            raise GrantedCommandMemoryPressure(memory)
        if limits.collect_cpu:
            try:
                cores = psutil.cpu_count(logical=True)
            except (psutil.Error, OSError):
                cores = None
            return {"read_started_monotonic": cpu_started, "observed_monotonic": time.monotonic(),
                    "logical_cpu_count": cores, "members": cpu_members,
                    "state": "ready" if cpu_available and cpu_members and type(cores) is int and cores > 0 else "unknown"}
    except psutil.NoSuchProcess as exc:
        if process.poll() is None:
            try:
                process.wait(timeout=.01)
            except subprocess.TimeoutExpired:
                raise GrantedCommandResourceUnavailable("Scene process resource sample unavailable") from exc
    except (psutil.Error, OSError) as exc:
        raise GrantedCommandResourceUnavailable("Scene process resource sample unavailable") from exc
    finally:
        if resource_observation is not None:
            if resource_sample is None and (rss_members or thread_members):
                resource_sample = resource_snapshot(False)
            if resource_sample is None:
                resource_observation["last_attempt"] = {"state": "unknown", "sample_complete": False, "reason": incomplete_reason,
                    "observed_monotonic": time.monotonic(), "observed_at_utc": datetime.now(timezone.utc).isoformat()}
            else:
                complete = resource_sample["sample_complete"]
                resource_observation["last_attempt"] = {"state": "ready" if complete else "unknown", "sample_complete": complete,
                    "observed_monotonic": resource_sample["observed_monotonic"],
                    "observed_at_utc": resource_sample["observed_at_utc"]}
                if not complete:
                    resource_observation["last_attempt"]["reason"] = incomplete_reason
                resource_observation["latest"] = resource_sample
                for metric, peak in (("rss_bytes", "observed_peak_rss"), ("threads", "observed_peak_threads")):
                    previous = resource_observation.get(peak)
                    if resource_sample[metric] is not None and (previous is None or resource_sample[metric] > previous[metric]):
                        resource_observation[peak] = {metric: resource_sample[metric], "sample_complete": complete,
                            "is_lower_bound": not complete,
                            "observed_members": resource_sample["rss_members" if metric == "rss_bytes" else "thread_members"],
                            "observed_monotonic": resource_sample["observed_monotonic"],
                            "observed_at_utc": resource_sample["observed_at_utc"]}


def _terminate_and_verify(process: subprocess.Popen[bytes], child: ProcessIdentity | None,
                          probe: HostProcessProbe, release_observations=None) -> None:
    def group_check(phase):
        released = probe.children_released((child,))
        if release_observations is not None:
            # Capture this check; never issue another probe for diagnostics.
            observed = {"released": released, "observed_monotonic": time.monotonic(),
                "group": getattr(probe, "last_group_observation", None)}
            release_observations[phase] = observed
            if released is None:
                release_observations.setdefault("first_unknown", {"phase": phase, **observed})
        return released
    phase = "TERM"
    try:
        if process.poll() is None or (child and group_check("before_term") is not True):
            if release_observations is not None:
                release_observations["signal_phase"] = phase
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            # Reaping the parent and the orphaned group is asynchronous on
            # macOS. Give the group a bounded chance to disappear before KILL.
            group_deadline = time.monotonic() + 0.25
            while child and group_check("after_term") is not True and time.monotonic() < group_deadline:
                time.sleep(0.01)
            if child and group_check("before_kill") is not True:
                phase = "KILL"
                if release_observations is not None:
                    release_observations["signal_phase"] = phase
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except (OSError, PermissionError) as exc:
        if release_observations is not None:
            release_observations["termination_error"] = {"phase": phase, "type": type(exc).__name__, "errno": exc.errno}
        raise RecoveryBlocked("子进程组无法停止，设备许可须保留") from exc
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired as exc:
        if release_observations is not None:
            release_observations["parent_wait_timeout"] = True
        raise RecoveryBlocked("子进程未退出，设备许可须保留") from exc
    if child is not None and group_check("final_after_parent_wait") is not True:
        raise RecoveryBlocked("子进程组释放状态未知，设备许可须保留")


def run_granted_command(
    scheduler: DeviceScheduler,
    grant: Grant,
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    cancel_event: threading.Event | None = None,
    env: dict[str, str] | None = None,
    output_paths: Sequence[str | Path] = (),
    max_output_file_bytes: int | None = None,
    resource_purpose: str | None = None,
) -> GrantedCommandResult:
    """Run one command with group cleanup; nonzero exit is returned to caller.

    The trampoline blocks on a private pipe. Only a successfully registered
    exact child identity receives the byte permitting exec. The grant remains
    held by the caller until its own checkpoint/defer/fail transaction.
    """
    if os.name != "posix" or sys.platform not in {"darwin", "linux"}:
        raise RuntimeError("Grant-gated subprocess supports macOS and Linux only")
    if (not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
            or timeout_seconds <= 0 or max_stdout_bytes < 0 or max_stderr_bytes < 0
            or (max_output_file_bytes is not None and max_output_file_bytes < 0)):
        raise ValueError("Invalid bounded subprocess command or limits")
    probe = scheduler.processes
    if not isinstance(probe, HostProcessProbe):
        raise RuntimeError("Grant-gated subprocess requires HostProcessProbe")
    if cancel_event is not None and cancel_event.is_set():
        raise GrantedCommandCancelled("Command cancelled before launch")
    scheduler.assert_grant(grant)
    # One frozen dependency view per command. The pipe/cancel watchdog checks
    # local versions and task expiry/revocation, never rebuilds the whole film.
    running_check = (scheduler.capture_running_check(grant)
        if isinstance(scheduler, DeviceScheduler) and isinstance(grant, Grant) and grant.owner_type == "creation_job"
        else lambda: scheduler.assert_grant(grant))
    if cancel_event is not None and cancel_event.is_set():
        raise GrantedCommandCancelled("Command cancelled before launch")
    resources = getattr(scheduler, "resources", None)
    scene_unit = (isinstance(grant, Grant) and grant.owner_type == "material_asset"
                  and grant.unit_key.startswith("scene-window:v1:"))
    model_unit = (isinstance(grant, Grant) and (
        (grant.owner_type == "creation_job" and grant.unit_key.startswith(("narration:", "person_render:", "person_edit:")))
        or (grant.owner_type == "media_preview" and grant.unit_key.startswith(("voice:", "avatar_lipsync:")))))
    quality_unit = isinstance(grant, Grant) and grant.owner_type == "quality_check"
    cover_unit = isinstance(grant, Grant) and grant.owner_type == "creation_job" and grant.unit_key.startswith("cover:")
    if (scene_unit or model_unit or quality_unit or cover_unit) and not isinstance(resources, HostResourceProbe):
        raise RecoveryBlocked("场景子进程缺少真实资源探针")
    if resource_purpose is not None and not isinstance(resources, HostResourceProbe):
        raise RecoveryBlocked("组合音频用途缺少真实资源探针")
    scene_limits = (resources.scene_runtime_limits(grant, purpose=resource_purpose) if resource_purpose is not None else
                    resources.scene_runtime_limits(grant)) if isinstance(resources, HostResourceProbe) else None
    if (scene_unit or model_unit or quality_unit or cover_unit) and scene_limits is None:
        raise RecoveryBlocked("场景子进程缺少运行中资源预算")
    cpu_policy = scene_limits.cpu_policy if scene_limits is not None else None
    if cpu_policy is None:
        child_entry = ["--grant-exec"]
    elif cpu_policy.mode == "darwin_background":
        child_entry = ["--grant-exec-background"]
    elif cpu_policy.mode == "darwin_nice":
        child_entry = ["--grant-exec-nice", str(cpu_policy.nice)]
    else:
        raise RecoveryBlocked("受许可子进程CPU调度策略不可识别")
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    child: ProcessIdentity | None = None
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
    exec_ack = False
    process_cpu_observation = None
    last_cpu_record = None
    release_observations = {}
    process_resource_observation = {"latest": None, "observed_peak_rss": None, "observed_peak_threads": None,
                                    "last_attempt": {"state": "unknown", "sample_complete": False, "reason": "not_sampled"}}
    entrypoint = [sys.executable]
    if not getattr(sys, "frozen", False):
        entrypoint.append(str(Path(__file__).resolve().parents[1] / "server.py"))
    try:
        process = subprocess.Popen(
            [*entrypoint, *child_entry, str(read_fd), *argv],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, pass_fds=(read_fd,),
            env=env,
        )
        os.close(read_fd)
        read_fd = -1
        deadline = time.monotonic() + timeout_seconds
        while os.getpgid(process.pid) != process.pid:
            if time.monotonic() >= deadline:
                raise GrantedCommandTimeout("Child session did not start before deadline")
            time.sleep(0.005)
        child = probe.identity(process.pid)
        scheduler.register_child(grant, child)
        scheduler.assert_grant(grant)
        if cancel_event is not None and cancel_event.is_set():
            raise GrantedCommandCancelled("Command cancelled before exec ACK")
        os.write(write_fd, b"G")
        exec_ack = True
        os.close(write_fd)
        write_fd = -1

        next_scene_sample = time.monotonic()
        next_permission_sample = time.monotonic()

        def sample_scene_runtime():
            nonlocal process_cpu_observation, last_cpu_record
            current = _check_scene_runtime(process, child, scene_limits, process_resource_observation)
            if current is None:
                return
            previous = last_cpu_record
            before = {(item["pid"], item["start_token"]): item for item in previous["members"]} if previous else {}
            after = {(item["pid"], item["start_token"]): item for item in current["members"]}
            common = before.keys() & after.keys()
            deltas = [after[key][metric] - before[key][metric] for key in common for metric in ("user", "system")]
            process_cpu_observation = {"start": previous, "end": current,
                "diagnostic_only": True,
                "membership_changed": previous is None or before.keys() != after.keys(),
                "same_identity_cpu_seconds_delta": sum(deltas) if previous and common
                    and previous["state"] == current["state"] == "ready" and all(value >= 0 for value in deltas) else None,
                "interval_seconds": current["observed_monotonic"] - previous["observed_monotonic"] if previous else None}
            if previous is None or current["observed_monotonic"] - previous["observed_monotonic"] >= _PERMISSION_SAMPLE_SECONDS:
                logger.warning("Granted process CPU observation: %s", json.dumps({
                    "request_id": grant.request_id, "attempt_number": grant.attempt_number,
                    "permit_generation": grant.generation, "process_cpu": process_cpu_observation,
                }, ensure_ascii=False))
                last_cpu_record = current

        def check_running_unit():
            nonlocal next_permission_sample
            if isinstance(grant, Grant) and (grant.owner_type == "creation_job" or model_unit or quality_unit) and time.monotonic() >= next_permission_sample:
                running_check()
                next_permission_sample = time.monotonic() + _PERMISSION_SAMPLE_SECONDS
            if max_output_file_bytes is not None:
                output_size = 0
                output_count = 0
                for target in output_paths:
                    try:
                        output_size += Path(target).stat().st_size
                        output_count += 1
                    except FileNotFoundError:
                        continue  # atomic temporary/final rename during worker output
                if output_size > max_output_file_bytes:
                    raise GrantedCommandOutputLimit("Generated files exceeded the stage output budget",
                        file_sample={"actual_bytes": output_size, "maximum_bytes": max_output_file_bytes,
                                     "output_count": output_count})

        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                check_running_unit()
                if cancel_event is not None and cancel_event.is_set():
                    raise GrantedCommandCancelled("Command cancelled")
                if scene_limits is not None and time.monotonic() >= next_scene_sample:
                    sample_scene_runtime()
                    next_scene_sample = time.monotonic() + scene_limits.sample_seconds
                if time.monotonic() >= deadline:
                    raise GrantedCommandTimeout("Command exceeded its unit deadline")
                for key, _ in selector.select(timeout=min(0.05, max(0, deadline - time.monotonic()))):
                    name = key.data
                    chunk = os.read(key.fileobj.fileno(), min(65536, limits[name] + 1 - len(outputs[name])))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        outputs[name].extend(chunk)
                        if len(outputs[name]) > limits[name]:
                            raise GrantedCommandOutputLimit(f"{name} exceeded its byte limit")
        while True:
            check_running_unit()
            if cancel_event is not None and cancel_event.is_set():
                raise GrantedCommandCancelled("Command cancelled")
            if scene_limits is not None and time.monotonic() >= next_scene_sample:
                sample_scene_runtime()
                next_scene_sample = time.monotonic() + scene_limits.sample_seconds
            if process.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GrantedCommandTimeout("Command exceeded its unit deadline")
            pause = min(0.05, remaining)
            if cancel_event is None:
                time.sleep(pause)
            else:
                cancel_event.wait(pause)
        if probe.children_released((child,)) is not True:
            _terminate_and_verify(process, child, probe, release_observations)
            raise GrantedCommandError("Command left a live process-group descendant")
        check_running_unit()
        _record_command_diagnostics(grant, child, {
            **{name: bytes(value) for name, value in outputs.items()},
            "stdout_truncated": False, "stderr_truncated": False,
            "exec_ack": exec_ack, "group_released": True,
            "before_termination_returncode": process.returncode, "returncode": process.returncode,
            "process_cpu_observation": process_cpu_observation,
            "process_resource_observation": process_resource_observation,
        })
        return GrantedCommandResult(process.returncode, bytes(outputs["stdout"]), bytes(outputs["stderr"]))
    except BaseException as exc:
        before_termination_returncode = process.poll() if process is not None else None
        group_released = True if process is None else None
        try:
            if process is not None:
                _terminate_and_verify(process, child, probe, release_observations)
                group_released = True
                # Drain only this released group's pipes, within the original caps.
                for name in outputs:
                    pipe = getattr(process, name)
                    if pipe is not None:
                        os.set_blocking(pipe.fileno(), False)
                        while len(outputs[name]) <= limits[name]:
                            try:
                                chunk = os.read(pipe.fileno(), min(65536, limits[name] + 1 - len(outputs[name])))
                            except BlockingIOError:
                                break
                            if not chunk:
                                break
                            outputs[name].extend(chunk)
        finally:
            exc.command_diagnostics = {
                "stdout": bytes(outputs["stdout"][:max_stdout_bytes]),
                "stderr": bytes(outputs["stderr"][:max_stderr_bytes]),
                "stdout_truncated": len(outputs["stdout"]) > max_stdout_bytes,
                "stderr_truncated": len(outputs["stderr"]) > max_stderr_bytes,
                "exec_ack": exec_ack, "group_released": group_released,
                "before_termination_returncode": before_termination_returncode,
                "returncode": process.returncode if process is not None else None,
                "process_cpu_observation": process_cpu_observation,
                "release_observations": release_observations,
                "process_resource_observation": process_resource_observation,
            }
            if isinstance(exc, GrantedCommandOutputLimit) and exc.file_sample is not None:
                exc.command_diagnostics["output_file_sample"] = exc.file_sample
            _record_command_diagnostics(grant, child, exc.command_diagnostics, failure=exc)
        raise
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
