"""POSIX subprocess gate: durable child registration precedes exec ACK."""

from __future__ import annotations

import os
import json
import logging
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
_QWEN_PHASE = re.compile(rb"QWEN_PHASE (?:worker_started|importing_runtime|loading_weights|weights_loaded|inference_started|inference_returned|audio_written)")
_QWEN_MEMORY = re.compile(rb"QWEN_MEMORY phase=(?:cache_disabled|weights_loaded|inference_started|inference_returned|audio_written) active_bytes=[0-9]{1,20} cache_bytes=[0-9]{1,20} peak_bytes=[0-9]{1,20}")


def _record_command_diagnostics(grant, child, diagnostic, *, failure=None):
    """Keep only fixed worker markers from the already bounded pipe buffers."""
    if not isinstance(grant, Grant):
        return
    markers = {stream: [line.decode("ascii") for line in diagnostic[stream].splitlines()
        if _QWEN_PHASE.fullmatch(line) or _QWEN_MEMORY.fullmatch(line)]
        for stream in ("stdout", "stderr")}
    if failure is None and diagnostic["returncode"] == 0 and not any(markers.values()):
        return
    payload = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_id": grant.request_id, "workspace_id": grant.workspace_id,
        "owner_type": grant.owner_type, "owner_id": grant.owner_id,
        "attempt_number": grant.attempt_number, "permit_generation": grant.generation,
        "child": {"pid": child.pid, "start_token": child.start_token, "boot_id": child.boot_id} if child else None,
        "failure_type": type(failure).__name__ if failure is not None else None,
        "qwen_markers": markers,
        **{key: diagnostic[key] for key in ("stdout_truncated", "stderr_truncated", "exec_ack",
            "group_released", "before_termination_returncode", "returncode")}}
    if isinstance(failure, HostResourceRefusal):
        payload["decision"] = failure.decision.diagnostic_payload()
    if isinstance(failure, GrantedCommandResourceLimit):
        payload["process_resource_sample"] = failure.resource_sample
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
    pass


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
                         limits: SceneRuntimeLimits) -> None:
    try:
        if process.poll() is None and f"{psutil.Process(process.pid).create_time():.6f}" != child.start_token:
            raise GrantedCommandResourceUnavailable("Child identity changed during sampling")
        processes = []
        for current in psutil.process_iter():
            try:
                if os.getpgid(current.pid) == child.pid:
                    processes.append(current)
            except ProcessLookupError:
                continue
            except PermissionError:
                # An unrelated process cannot be in our own accessible group.
                continue
        rss = 0
        threads = 0
        for current in processes:
            try:
                rss += current.memory_info().rss
                threads += current.num_threads()
            except psutil.NoSuchProcess as exc:
                if current.pid == process.pid and process.poll() is None:
                    # macOS can remove the pid just before waitpid exposes its
                    # exit. Reap only this registered Popen; unknown/live stays refused.
                    try:
                        process.wait(timeout=.01)
                    except subprocess.TimeoutExpired:
                        raise GrantedCommandResourceUnavailable("Scene child disappeared during sampling") from exc
        if rss > limits.max_process_rss_bytes or threads > limits.max_process_threads:
            raise GrantedCommandResourceLimit(rss=rss, threads=threads, limits=limits)
        if limits.memory_monitor is None:
            raise GrantedCommandResourceUnavailable("Native memory policy is unavailable")
        memory = limits.memory_monitor.check(system_reserve_bytes=limits.min_host_available_memory_bytes,
                                              additional_commit_bytes=0)
        if memory.verdict is not True:
            raise GrantedCommandMemoryPressure(memory)
    except psutil.NoSuchProcess as exc:
        if process.poll() is None:
            try:
                process.wait(timeout=.01)
            except subprocess.TimeoutExpired:
                raise GrantedCommandResourceUnavailable("Scene process resource sample unavailable") from exc
    except (psutil.Error, OSError) as exc:
        raise GrantedCommandResourceUnavailable("Scene process resource sample unavailable") from exc


def _terminate_and_verify(process: subprocess.Popen[bytes], child: ProcessIdentity | None,
                          probe: HostProcessProbe) -> None:
    try:
        if process.poll() is None or (child and probe.children_released((child,)) is not True):
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
            while child and probe.children_released((child,)) is not True and time.monotonic() < group_deadline:
                time.sleep(0.01)
            if child and probe.children_released((child,)) is not True:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except (OSError, PermissionError) as exc:
        raise RecoveryBlocked("子进程组无法停止，设备许可须保留") from exc
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired as exc:
        raise RecoveryBlocked("子进程未退出，设备许可须保留") from exc
    if child is not None and probe.children_released((child,)) is not True:
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
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    child: ProcessIdentity | None = None
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
    exec_ack = False
    entrypoint = [sys.executable]
    if not getattr(sys, "frozen", False):
        entrypoint.append(str(Path(__file__).resolve().parents[1] / "server.py"))
    try:
        process = subprocess.Popen(
            [*entrypoint, "--grant-exec", str(read_fd), *argv],
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

        def check_running_unit():
            nonlocal next_permission_sample
            if isinstance(grant, Grant) and (grant.owner_type == "creation_job" or model_unit or quality_unit) and time.monotonic() >= next_permission_sample:
                scheduler.assert_grant(grant)
                next_permission_sample = time.monotonic() + 1.0
            if max_output_file_bytes is not None:
                output_size = 0
                for target in output_paths:
                    try:
                        output_size += Path(target).stat().st_size
                    except FileNotFoundError:
                        continue  # atomic temporary/final rename during worker output
                if output_size > max_output_file_bytes:
                    raise GrantedCommandOutputLimit("Generated files exceeded the stage output budget")

        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                check_running_unit()
                if cancel_event is not None and cancel_event.is_set():
                    raise GrantedCommandCancelled("Command cancelled")
                if scene_limits is not None and time.monotonic() >= next_scene_sample:
                    _check_scene_runtime(process, child, scene_limits)
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
                _check_scene_runtime(process, child, scene_limits)
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
            _terminate_and_verify(process, child, probe)
            raise GrantedCommandError("Command left a live process-group descendant")
        check_running_unit()
        _record_command_diagnostics(grant, child, {
            **{name: bytes(value) for name, value in outputs.items()},
            "stdout_truncated": False, "stderr_truncated": False,
            "exec_ack": exec_ack, "group_released": True,
            "before_termination_returncode": process.returncode, "returncode": process.returncode,
        })
        return GrantedCommandResult(process.returncode, bytes(outputs["stdout"]), bytes(outputs["stderr"]))
    except BaseException as exc:
        before_termination_returncode = process.poll() if process is not None else None
        group_released = True if process is None else None
        try:
            if process is not None:
                _terminate_and_verify(process, child, probe)
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
            }
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
