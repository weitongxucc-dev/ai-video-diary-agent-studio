from __future__ import annotations

import os
import signal
import subprocess
import threading
import sys
import time
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

import psutil
from app.device_scheduler import ProcessIdentity
from app.host_resource_probes import HostProcessProbe
from types import MappingProxyType
from typing import Callable

class AssistantStopped(RuntimeError):
    """The current conversation turn's Pi child was stopped by its owner."""

class AssistantProcessReleaseError(RuntimeError):
    """Owned process identity/release is unproven; never acknowledge a stop."""


@dataclass
class _OwnedGroup:
    identity: ProcessIdentity
    probe: HostProcessProbe
    lock: threading.Lock = field(default_factory=threading.Lock)
    released: bool = False


_groups: WeakKeyDictionary = WeakKeyDictionary()
_groups_guard = threading.RLock()


def _require_process_group_platform() -> None:
    if os.name != "posix" or sys.platform not in {"darwin", "linux"}:
        raise AssistantProcessReleaseError("当前平台无法核验助手进程组释放，未启动新的助手子进程；仅支持 macOS/Linux 的独占进程组。")


def _capture_process_group(process: subprocess.Popen[str]) -> _OwnedGroup | None:
    """Capture the child before any parent wait/reap can lose its identity."""
    _require_process_group_platform()
    with _groups_guard:
        existing = _groups.get(process)
        if existing is not None:
            return existing
        try:
            # Popen alone is not authority to signal an arbitrary/parent group.
            if process.poll() is not None:
                raise psutil.NoSuchProcess(process.pid)
            if os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid:
                raise AssistantProcessReleaseError("助手子进程不是本轮独占 session/group，未发送组停止信号。")
            probe = HostProcessProbe()
            identity = probe.identity(process.pid)
            if psutil.Process(process.pid).ppid() != os.getpid():
                raise AssistantProcessReleaseError("助手进程已不属于当前宿主，未发送停止信号。")
        except (psutil.NoSuchProcess, ProcessLookupError):
            # A short command may exit before capture. Only definite group
            # absence permits release; surviving descendants remain unknown.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return None
            except OSError as error:
                raise AssistantProcessReleaseError("助手进程身份已不可读，无法核验进程组是否释放；未确认停止。") from error
            raise AssistantProcessReleaseError("助手父进程已退出，原进程组身份未登记且仍存在；未确认停止，未发送未知组信号。")
        except (OSError, psutil.Error, RuntimeError) as error:
            if isinstance(error, AssistantProcessReleaseError):
                raise
            raise AssistantProcessReleaseError("助手进程身份无法核验，未确认停止；请查看本轮本机进程诊断。") from error
        group = _OwnedGroup(identity, probe)
        _groups[process] = group
        return group


def _group_state(group: _OwnedGroup) -> bool:
    released = group.probe.children_released((group.identity,))
    if released is None:
        raise AssistantProcessReleaseError("助手进程组身份已变化或释放状态未知，未确认停止；未向未知或复用的进程组发送信号。")
    return released


def _process_group_released(process: subprocess.Popen[str]) -> bool:
    group = _capture_process_group(process)
    if group is None:
        return True
    with group.lock:
        if group.released:
            return True
        released = _group_state(group)
        if released:
            group.released = True
        return released


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    group = _capture_process_group(process)
    if group is None:
        process.wait(timeout=3)
        return
    # The request thread and stdout runner can observe the same stop. Serialize
    # their signals and revalidate before each one, including after leader exit.
    with group.lock:
        if group.released:
            return
        for sig, wait_seconds in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
            if _group_state(group):
                process.wait(timeout=3)
                group.released = True
                return
            try:
                os.killpg(group.identity.pid, sig)
            except ProcessLookupError:
                pass
            except OSError as error:
                raise AssistantProcessReleaseError("助手进程组停止信号失败，未确认停止；请检查本机进程权限。") from error
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                process.poll()  # Reap only this Popen, then also inspect PGID.
                # macOS metadata can be temporarily unreadable while the
                # leader exits. Poll without signalling during that interval;
                # any next signal still requires a definite matching identity.
                if group.probe.children_released((group.identity,)) is True:
                    process.wait(timeout=3)
                    group.released = True
                    return
                time.sleep(.01)
        raise AssistantProcessReleaseError("助手进程组在 TERM/KILL 后仍未释放，未确认停止；本轮停止标记保留。")


class TurnControl:
    """One owner for host preflight/Pi process stop markers and child handles."""
    def __init__(self, terminate: Callable[[subprocess.Popen[str]], None]) -> None:
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._events: dict[str, threading.Event] = {}
        self._guard = threading.RLock()
        self._terminate = terminate

    @property
    def stop_events(self):
        return MappingProxyType(self._events)

    def register(self, turn_id: str) -> None:
        with self._guard:
            self._events[turn_id] = threading.Event()

    def forget(self, turn_id: str) -> None:
        with self._guard:
            process = self._processes.get(turn_id)
        if process is not None and not _process_group_released(process):
            self._terminate(process)
        with self._guard:
            self._events.pop(turn_id, None)
            self._processes.pop(turn_id, None)

    def request_stop(self, turn_id: str) -> bool:
        with self._guard:
            event = self._events.get(turn_id)
            if event is None:
                return False
            process = self._processes.get(turn_id)
            if process is None and event.is_set():
                return False
            event.set()
            if process is None:
                return True
        self._terminate(process)
        return True

    def stop_all(self) -> list[str]:
        with self._guard:
            active_ids = list(self._events)
        for turn_id in active_ids:
            self.request_stop(turn_id)
        return active_ids

    def is_stopped(self, turn_id: str) -> bool:
        with self._guard:
            event = self._events.get(turn_id)
            return event is not None and event.is_set()

    def begin(self, turn_id: str) -> threading.Event:
        with self._guard:
            event = self._events.setdefault(turn_id, threading.Event())
            if event.is_set():
                raise AssistantStopped("assistant turn stopped before Pi started")
            return event

    def bind(self, turn_id: str, process: subprocess.Popen[str], event: threading.Event) -> None:
        with self._guard:
            self._processes[turn_id] = process
            try:
                _capture_process_group(process)
            except AssistantProcessReleaseError:
                event.set()
                raise
            if event.is_set():
                self._terminate(process)

    def launch(self, turn_id: str, event: threading.Event, launch_process) -> subprocess.Popen[str]:
        # Stop acknowledgement and creation/registration share this boundary.
        # A stop between begin() and Popen must never acknowledge then spawn.
        with self._guard:
            if event.is_set():
                raise AssistantStopped("assistant turn stopped before Pi started")
            process = launch_process()
            self.bind(turn_id, process, event)
            return process

    def release(self, turn_id: str, process: subprocess.Popen[str], keep: bool) -> None:
        if not _process_group_released(process):
            self._terminate(process)
        with self._guard:
            if self._processes.get(turn_id) is process:
                self._processes.pop(turn_id, None)
            stopped = self._events.get(turn_id)
            if not keep and not (stopped is not None and stopped.is_set()):
                self._events.pop(turn_id, None)
