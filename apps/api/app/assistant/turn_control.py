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


@dataclass
class _OwnedCallback:
    thread: threading.Thread
    cancel: Callable[[], None]
    verify_release: Callable[[float], None] | None = None


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


def _terminate_process_group(process: subprocess.Popen[str], *, deadline_monotonic: float | None = None) -> None:
    total_deadline = time.monotonic() + 7 if deadline_monotonic is None else deadline_monotonic
    group = _capture_process_group(process)
    if group is None:
        process.wait(timeout=max(0, min(3, total_deadline-time.monotonic())))
        return
    if not group.lock.acquire(timeout=max(0, total_deadline-time.monotonic())):
        raise AssistantProcessReleaseError("助手进程组回收等待已到共同截止，未确认停止。")
    try:
        if group.released:
            return
        for sig, wait_seconds in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
            if time.monotonic() >= total_deadline:
                break
            if _group_state(group):
                process.wait(timeout=max(0, min(3, total_deadline-time.monotonic())))
                group.released = True
                return
            try:
                os.killpg(group.identity.pid, sig)
            except ProcessLookupError:
                pass
            except OSError as error:
                raise AssistantProcessReleaseError("助手进程组停止信号失败，未确认停止；请检查本机进程权限。") from error
            deadline = min(total_deadline, time.monotonic()+wait_seconds)
            while time.monotonic() < deadline:
                process.poll()
                if group.probe.children_released((group.identity,)) is True:
                    process.wait(timeout=max(0, min(3, total_deadline-time.monotonic())))
                    group.released = True
                    return
                time.sleep(min(.01, max(0, deadline-time.monotonic())))
        raise AssistantProcessReleaseError("助手进程组在共同截止前仍未释放，未确认停止；本轮停止标记保留。")
    finally:
        group.lock.release()


class TurnControl:
    """One owner for host preflight/Pi process stop markers and child handles."""
    def __init__(self, terminate: Callable[[subprocess.Popen[str]], None]) -> None:
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._events: dict[str, threading.Event] = {}
        self._guard = threading.RLock()
        self._terminate = terminate
        self._shutdown_requested = False
        self._callbacks: dict[str, _OwnedCallback] = {}
        self._stop_deadlines: dict[str, float] = {}

    def launch_callback(self, turn_id: str, thread: threading.Thread, cancel: Callable[[], None], *, verify_release=None) -> None:
        with self._guard:
            if self._shutdown_requested or self._events[turn_id].is_set():
                raise AssistantStopped("助手回合已停止，未启动宿主业务回调。")
            if turn_id in self._callbacks:
                raise AssistantProcessReleaseError("本轮宿主业务回调尚未确认释放。")
            self._callbacks[turn_id] = _OwnedCallback(thread, cancel,verify_release)
            thread.start()

    def _wait_callbacks(self, callbacks, deadline: float) -> None:
        pending = False
        for turn_id, owned in callbacks:
            owned.thread.join(timeout=max(0, deadline - time.monotonic()))
            if owned.thread.is_alive():
                pending = True
            else:
                if owned.verify_release is not None:
                    owned.verify_release(deadline)
                with self._guard:
                    if self._callbacks.get(turn_id) is owned:
                        self._callbacks.pop(turn_id)
        if pending:
            raise AssistantProcessReleaseError("宿主业务回调在共同截止前尚未返回，释放未确认；本轮登记保留。")

    @property
    def stop_events(self):
        return MappingProxyType(self._events)

    def register(self, turn_id: str) -> None:
        with self._guard:
            if self._shutdown_requested:
                raise AssistantStopped("应用正在退出，未接收新的助手执行")
            if turn_id in self._callbacks:
                raise AssistantProcessReleaseError("本轮宿主业务回调尚未确认释放，不能覆盖原执行登记。")
            self._events[turn_id] = threading.Event()

    def forget(self, turn_id: str) -> None:
        with self._guard:
            process = self._processes.get(turn_id)
            callbacks = [(turn_id, self._callbacks[turn_id])] if turn_id in self._callbacks else []
            deadline = self._stop_deadlines.get(turn_id, time.monotonic() + 7)
            for _, owned in callbacks:
                owned.cancel()
        if process is not None and not _process_group_released(process):
            self._terminate(process, deadline_monotonic=deadline)
        self._wait_callbacks(callbacks, deadline)
        with self._guard:
            self._events.pop(turn_id, None)
            self._processes.pop(turn_id, None)
            self._stop_deadlines.pop(turn_id, None)

    def request_stop(self, turn_id: str) -> bool:
        with self._guard:
            event = self._events.get(turn_id)
            if event is None:
                return False
            process = self._processes.get(turn_id)
            callbacks = [(turn_id, self._callbacks[turn_id])] if turn_id in self._callbacks else []
            if process is None and not callbacks and event.is_set():
                return False
            event.set()
            deadline = self._stop_deadlines.setdefault(turn_id, time.monotonic() + 7)
            for _, owned in callbacks:
                owned.cancel()
        if process is not None:
            self._terminate(process, deadline_monotonic=deadline)
        self._wait_callbacks(callbacks, deadline)
        return True

    def stop_all(self, *, deadline_monotonic: float | None = None) -> list[str]:
        deadline = time.monotonic() + 7 if deadline_monotonic is None else deadline_monotonic
        with self._guard:
            if deadline_monotonic is not None:
                self._shutdown_requested = True
            active_ids = list(self._events)
            for event in self._events.values():
                event.set()
            for turn_id in active_ids:
                self._stop_deadlines[turn_id] = min(deadline, self._stop_deadlines.get(turn_id, deadline))
            callbacks = tuple(self._callbacks.items())
            for _, owned in callbacks:
                owned.cancel()
            processes = tuple(self._processes.values())
        failures = []
        for process in processes:
            try:
                self._terminate(process, deadline_monotonic=deadline)
            except Exception as exc:
                failures.append(exc)
        try:
            self._wait_callbacks(callbacks, deadline)
        except AssistantProcessReleaseError as exc:
            failures.append(exc)
        if failures:
            raise AssistantProcessReleaseError("部分助手进程组或宿主业务回调尚未确认释放；所有在途回合已通知停止。") from failures[0]
        return active_ids

    def is_stopped(self, turn_id: str) -> bool:
        with self._guard:
            event = self._events.get(turn_id)
            return event is not None and event.is_set()

    def begin(self, turn_id: str) -> threading.Event:
        with self._guard:
            if self._shutdown_requested:
                raise AssistantStopped("应用正在退出，未启动助手进程")
            if turn_id in self._callbacks:
                raise AssistantProcessReleaseError("前一次宿主业务回调尚未确认释放，未启动新的助手进程。")
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
            if self._shutdown_requested or event.is_set():
                raise AssistantStopped("assistant turn stopped before Pi started")
            process = launch_process()
            self.bind(turn_id, process, event)
            return process

    def release(self, turn_id: str, process: subprocess.Popen[str], keep: bool) -> None:
        with self._guard:
            deadline = self._stop_deadlines.get(turn_id, time.monotonic() + 7)
            callbacks = [(turn_id, self._callbacks[turn_id])] if turn_id in self._callbacks else []
            for _, owned in callbacks:
                owned.cancel()
        if not _process_group_released(process):
            self._terminate(process, deadline_monotonic=deadline)
        self._wait_callbacks(callbacks, deadline)
        with self._guard:
            if self._processes.get(turn_id) is process:
                self._processes.pop(turn_id, None)
            stopped = self._events.get(turn_id)
            if not keep and not (stopped is not None and stopped.is_set()):
                self._events.pop(turn_id, None)
