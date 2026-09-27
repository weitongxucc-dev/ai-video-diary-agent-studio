"""POSIX subprocess gate: durable child registration precedes exec ACK."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Sequence

from app.device_scheduler import DeviceScheduler, Grant, ProcessIdentity, RecoveryBlocked
from app.host_resource_probes import HostProcessProbe


_TRAMPOLINE = """
import os, sys
gate = int(sys.argv[1])
try:
    approved = os.read(gate, 1)
finally:
    os.close(gate)
if approved != b'G':
    sys.exit(126)
try:
    os.execvp(sys.argv[2], sys.argv[2:])
except OSError as exc:
    print(f'exec failed: {exc}', file=sys.stderr)
    sys.exit(127)
"""


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
) -> GrantedCommandResult:
    """Run one command with group cleanup; nonzero exit is returned to caller.

    The trampoline blocks on a private pipe. Only a successfully registered
    exact child identity receives the byte permitting exec. The grant remains
    held by the caller until its own checkpoint/defer/fail transaction.
    """
    if os.name != "posix" or sys.platform not in {"darwin", "linux"}:
        raise RuntimeError("Grant-gated subprocess supports macOS and Linux only")
    if (not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
            or timeout_seconds <= 0 or max_stdout_bytes < 0 or max_stderr_bytes < 0):
        raise ValueError("Invalid bounded subprocess command or limits")
    probe = scheduler.processes
    if not isinstance(probe, HostProcessProbe):
        raise RuntimeError("Grant-gated subprocess requires HostProcessProbe")
    scheduler.assert_grant(grant)
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    child: ProcessIdentity | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", _TRAMPOLINE, str(read_fd), *argv],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, pass_fds=(read_fd,),
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
        os.write(write_fd, b"G")
        os.close(write_fd)
        write_fd = -1

        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if cancel_event is not None and cancel_event.is_set():
                    raise GrantedCommandCancelled("Command cancelled")
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
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise GrantedCommandTimeout("Command exceeded its unit deadline") from exc
        if probe.children_released((child,)) is not True:
            _terminate_and_verify(process, child, probe)
            raise GrantedCommandError("Command left a live process-group descendant")
        return GrantedCommandResult(process.returncode, bytes(outputs["stdout"]), bytes(outputs["stderr"]))
    except BaseException:
        if process is not None:
            _terminate_and_verify(process, child, probe)
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
