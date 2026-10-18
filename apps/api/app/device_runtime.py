"""One dispatch loop for verified device work; host probes are injected.

This module supplies no production resource or process probe. The application
must provide real implementations before enabling heavy-work routes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Protocol
from urllib.parse import quote

from app.device_scheduler import (
    DeviceScheduler,
    Grant,
    OwnerVerifier,
    ProcessProbe,
    RecoveryBlocked,
    ResourceProbe,
    ResourceAdmissionWrite,
    RetryReason,
    SessionHeartbeat,
    SubmissionIntent,
    VerifiedSession,
    VerifiedWork,
    Verdict,
    WorkRequest,
)
if TYPE_CHECKING:
    from app.host_resource_probes import HostResourceProbe


logger = logging.getLogger(__name__)


class RuntimeUnavailable(RuntimeError):
    """A required host or business adapter is absent, or the driver stopped."""


class BusinessOwner(Protocol):
    """One owner type's business facts and permissions, supplied by its writer."""

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork: ...
    def check_current(self, request: WorkRequest, conn: sqlite3.Connection) -> Verdict: ...
    def can_manage(self, request: WorkRequest, actor: object) -> bool: ...
    def can_wake(self, request: WorkRequest, actor: object) -> bool: ...
    def can_view_detail(self, request: WorkRequest, actor: object) -> bool: ...
    def verify_retry(self, request: WorkRequest, actor: object, reason: RetryReason,
                     conn: sqlite3.Connection) -> str: ...


class DeviceAccess(Protocol):
    """Session and anonymous device-view rights, separate from owner details."""

    def verify_session(self, heartbeat: SessionHeartbeat, actor: object) -> VerifiedSession: ...
    def can_view_device_summary(self, actor: object) -> bool: ...


class UnitExecutor(Protocol):
    """Run exactly one grant and commit, defer, or fail it before returning."""

    def execute(self, grant: Grant, scheduler: DeviceScheduler) -> None: ...


@dataclass(frozen=True)
class OwnerRegistration:
    owner: BusinessOwner
    executor: UnitExecutor


class OwnerRouter(OwnerVerifier):
    """Route per-owner checks without inventing fallback identity or access."""

    def __init__(self, registrations: Mapping[str, OwnerRegistration], access: DeviceAccess) -> None:
        self.registrations = dict(registrations)
        self.access = access

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork:
        registration = self.registrations.get(intent.owner_type)
        if registration is None:
            raise RuntimeUnavailable("执行对象没有已注册的验证者和执行器")
        trusted = registration.owner.verify_submission(intent, actor)
        if trusted.owner_type != intent.owner_type:
            raise PermissionError("执行对象类型与注册验证者不匹配")
        return trusted

    def check_current(self, request: WorkRequest, conn: sqlite3.Connection) -> Verdict:
        registration = self.registrations.get(request.owner_type)
        if registration is None:
            return "unknown"
        return registration.owner.check_current(request, conn)

    def can_manage(self, request: WorkRequest, actor: object) -> bool:
        registration = self.registrations.get(request.owner_type)
        return registration is not None and registration.owner.can_manage(request, actor)

    def can_wake(self, request: WorkRequest, actor: object) -> bool:
        registration = self.registrations.get(request.owner_type)
        return registration is not None and registration.owner.can_wake(request, actor)

    def can_view_detail(self, request: WorkRequest, actor: object) -> bool:
        registration = self.registrations.get(request.owner_type)
        return registration is not None and registration.owner.can_view_detail(request, actor)

    def verify_retry(self, request: WorkRequest, actor: object, reason: RetryReason,
                     conn: sqlite3.Connection) -> str:
        registration = self.registrations.get(request.owner_type)
        if registration is None:
            raise RuntimeUnavailable("执行对象没有已注册的验证者和执行器")
        return registration.owner.verify_retry(request, actor, reason, conn)

    def verify_session(self, heartbeat: SessionHeartbeat, actor: object) -> VerifiedSession:
        return self.access.verify_session(heartbeat, actor)

    def can_view_device_summary(self, actor: object) -> bool:
        return self.access.can_view_device_summary(actor)


class DeviceRuntime:
    """One driver thread; failures retain the grant for recovery inspection."""

    def __init__(self, scheduler: DeviceScheduler, registrations: Mapping[str, OwnerRegistration],
                 *, poll_interval_seconds: float, host_observer: HostResourceProbe | None = None) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("调度轮询间隔必须为正数")
        self.scheduler = scheduler
        self.registrations = dict(registrations)
        self.poll_interval_seconds = poll_interval_seconds
        self.host_observer = host_observer
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._dispatch_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._last_error: Exception | None = None

    @property
    def last_error(self) -> Exception | None:
        with self._lifecycle_lock:
            return self._last_error

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> bool:
        """Dispatch at most one unit; useful before start and in isolated tests."""
        with self._dispatch_lock:
            if self._stop.is_set() or self._paused.is_set():
                return False
            if self._thread is not None and self._thread.is_alive() and threading.current_thread() is not self._thread:
                raise RuntimeUnavailable("后台调度驱动已经运行")
            try:
                grant = self.scheduler.pick_and_grant()
                if grant is None:
                    return False
                if self._stop.is_set():
                    raise RecoveryBlocked("停止信号已到，获准单元未执行并保留许可供核查")
                registration = self.registrations.get(grant.owner_type)
                if registration is None:
                    raise RecoveryBlocked("获准对象缺少执行器，许可保留供核查")
                self.scheduler.assert_grant(grant)
                if self._stop.is_set():
                    raise RecoveryBlocked("停止信号已到，获准单元未执行并保留许可供核查")
                registration.executor.execute(grant, self.scheduler)
                self.scheduler.assert_released(grant)
                return True
            except Exception as exc:
                # Pause shares this lock: publish a retained-permit fault before
                # the dispatch boundary can be observed as quiescent.
                with self._lifecycle_lock:
                    self._last_error = exc
                    self._stop.set()
                raise

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                dispatched = self.run_once()
            except Exception:
                logger.exception("Device dispatch stopped; inspect the permit before resuming")
                return
            if not dispatched:
                self._wake.wait(self.poll_interval_seconds)
                self._wake.clear()

    def start(self) -> None:
        """Start once; repeat calls while running do not launch another driver."""
        with self._lifecycle_lock:
            if self._closed or self._last_error is not None:
                raise RuntimeUnavailable("调度驱动已停止，须重新构造并核查许可")
            if self._thread is not None and self._thread.is_alive():
                return
            if self._dispatch_lock.locked():
                raise RuntimeUnavailable("同步派发尚未结束，不能启动后台调度驱动")
            self._stop.clear()
            if self.host_observer is not None:
                self.host_observer.start_observing(interval_seconds=self.poll_interval_seconds)
            self._thread = threading.Thread(target=self._run, name="device-dispatch", daemon=True)
            try:
                self._thread.start()
            except (RuntimeError, OSError):
                self._thread = None
                self._stop.set()
                if self.host_observer is not None and not self.host_observer.stop_observing(self.poll_interval_seconds):
                    raise RuntimeUnavailable("派发启动失败，基础资源观察线程尚未停止")
                raise

    def wake(self) -> None:
        """Wake the existing driver; never start or duplicate one."""
        if not self._stop.is_set():
            self._wake.set()

    def pause(self, timeout_seconds: float) -> bool:
        """Quiesce between units without closing or touching a held grant."""
        if timeout_seconds < 0:
            raise ValueError("暂停等待时间不能为负数")
        if not self._dispatch_lock.acquire(timeout=timeout_seconds):
            return False
        try:
            with self._lifecycle_lock:
                if self._closed or self._last_error is not None:
                    raise RuntimeUnavailable("调度驱动已停止，须重新构造并核查许可")
                self._paused.set()
        finally:
            self._dispatch_lock.release()
        return True

    def resume(self) -> None:
        """Resume the same owner and thread, preserving queued requests."""
        with self._lifecycle_lock:
            if self._closed or self._last_error is not None:
                raise RuntimeUnavailable("调度驱动已停止，须重新构造并核查许可")
            self._paused.clear()
            self._wake.set()

    def stop(self, timeout_seconds: float) -> bool:
        """Return false if a unit is still running; leave its permit untouched."""
        if timeout_seconds < 0:
            raise ValueError("停止等待时间不能为负数")
        deadline = time.monotonic() + timeout_seconds
        with self._lifecycle_lock:
            self._closed = True
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                raise RuntimeUnavailable("调度驱动不能在执行线程中等待自身停止")
            thread.join(max(0, deadline - time.monotonic()))
            if thread.is_alive():
                return False
        if not self._dispatch_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            return False
        try:
            if self.host_observer is not None and not self.host_observer.stop_observing(max(0, deadline - time.monotonic())):
                return False
            self.scheduler.close()
        finally:
            self._dispatch_lock.release()
        return True


def build_device_runtime(
    *,
    database_path: str | Path,
    device_lock_path: str | Path,
    device_access: DeviceAccess,
    registrations: Mapping[str, OwnerRegistration],
    resources: ResourceProbe,
    processes: ProcessProbe,
    activity_source_reliable: bool = False,
    poll_interval_seconds: float = 1.0,
    admission_recorder: ResourceAdmissionWrite | None = None,
    host_observer: HostResourceProbe | None = None,
) -> DeviceRuntime:
    """Build only from explicit trusted adapters; never supply permissive probes."""
    if not database_path or not device_lock_path or device_access is None or resources is None or processes is None:
        raise RuntimeUnavailable("缺少全机锁路径、设备权限或真实资源/进程探针")
    if registrations is None:
        raise RuntimeUnavailable("没有完整注册的业务验证者和执行器")
    registered = dict(registrations)
    if not registered or any(not owner_type or item.owner is None or item.executor is None
                             for owner_type, item in registered.items()):
        raise RuntimeUnavailable("没有完整注册的业务验证者和执行器")
    lock_path = Path(device_lock_path).expanduser()
    if not lock_path.is_absolute():
        raise RuntimeUnavailable("全机锁路径必须显式配置为稳定绝对路径")
    database = Path(database_path).expanduser().resolve()
    if not database.is_file():
        raise RuntimeUnavailable("Production SQLite 尚未初始化")
    try:
        with sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True) as conn:
            if conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone() is None:
                raise RuntimeUnavailable("Production SQLite 缺少设备许可表")
            conn.execute("SELECT id FROM device_work_requests LIMIT 0")
    except sqlite3.Error as exc:
        raise RuntimeUnavailable("Production SQLite 调度表不可用") from exc
    try:
        scheduler = DeviceScheduler(database, lock_path, owners=OwnerRouter(registered, device_access),
                                    resources=resources, processes=processes,
                                    admission_recorder=admission_recorder,
                                    activity_source_reliable=activity_source_reliable)
    except ValueError as exc:
        raise RuntimeUnavailable(str(exc)) from exc
    return DeviceRuntime(scheduler, registered, poll_interval_seconds=poll_interval_seconds,
                         host_observer=host_observer)
