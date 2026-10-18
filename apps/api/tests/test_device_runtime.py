from __future__ import annotations

import os
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from app.database_migrations import apply_production_migrations
from app.device_runtime import (
    OwnerRegistration,
    RuntimeUnavailable,
    build_device_runtime,
)
from app.device_scheduler import (
    DeviceScheduler,
    ProcessIdentity,
    RecoveryBlocked,
    StaleGrantInput,
    SchedulePolicy,
    SubmissionIntent,
    VerifiedSession,
    VerifiedWork,
)
from app.host_memory import HostMemoryMonitor, MemoryPolicy, MemorySample
from app.host_resource_probes import HostBudget, HostResourceProbe, NarrationBudget, RuntimeCpuPolicy


class Owner:
    def verify_submission(self, intent, actor):
        if actor != "member":
            raise PermissionError("unknown member")
        return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id,
                            intent.input_digest, intent.unit_key, "member", "normal_make", {})

    def check_current(self, request, conn):
        return "ready"

    def can_manage(self, request, actor):
        return actor == "member"

    def can_wake(self, request, actor):
        return False

    def can_view_detail(self, request, actor):
        return actor == "member"

    def verify_retry(self, request, actor, reason, conn):
        if actor != "member":
            raise PermissionError("unknown member")
        return actor


class Access:
    def verify_session(self, heartbeat, actor):
        if actor != "member":
            raise PermissionError("unknown member")
        return VerifiedSession(heartbeat.session_id, heartbeat.workspace_id, actor)

    def can_view_device_summary(self, actor):
        return actor == "member"


class Resources:
    def __init__(self, admit=True):
        self.admit_value = admit

    def admit(self, request):
        from app.host_resource_probes import HostResourceDecision
        return HostResourceDecision(self.admit_value, dict(request.resources), {})

    def continue_unit(self, grant):
        return True

    def released(self, grant):
        return True


class Processes:
    def current(self):
        return ProcessIdentity(os.getpid(), "test-start", "test-boot")

    def children_released(self, children):
        return True

    def recovery_state(self, holder, children):
        return "released"


class CompletingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, grant, scheduler):
        self.calls.append(grant)
        scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                checkpoint_write=lambda conn, current: current.owner_id)


def _database(tmp_path):
    database = tmp_path / "business.sqlite"
    with sqlite3.connect(database) as conn:
        # ProductionRepository initializes this legacy table before migrations.
        conn.execute("CREATE TABLE material_ingestion_files (workspace_id TEXT, file_path TEXT)")
        apply_production_migrations(conn)
    return database


def _intent(owner_id, owner_type="job_run"):
    return SubmissionIntent("private-a", owner_type, owner_id, "sha256:input", "narration", SchedulePolicy())


def _runtime(tmp_path, *, database=None, executor=None, resources=None, registrations=None, host_observer=None):
    database = database or _database(tmp_path)
    executor = executor or CompletingExecutor()
    registrations = registrations or {"job_run": OwnerRegistration(Owner(), executor)}
    return build_device_runtime(
        database_path=database, device_lock_path=tmp_path / "device.lock",
        device_access=Access(), registrations=registrations,
        resources=resources or Resources(), processes=Processes(),
        poll_interval_seconds=0.01,
        host_observer=host_observer,
    )


class BaseObservations:
    """Virtual one-second native observations; wall-clock waits stay small."""
    def __init__(self, tmp_path, monkeypatch):
        self.now = 0.
        self.condition = threading.Condition()
        self.monitor = HostMemoryMonitor(MemoryPolicy("darwin", 10, 2, "synthetic-observer-cadence"),
            sampler=self.sample, clock=lambda: self.now)
        import app.host_resource_probes as probes
        monkeypatch.setattr(probes, "time", SimpleNamespace(monotonic=lambda: self.now, sleep=self.advance))
        monkeypatch.setattr(probes, "sys", SimpleNamespace(platform="darwin"))
        monkeypatch.setattr(probes.psutil, "cpu_percent", lambda interval: self.advance(interval) or 0)
        monkeypatch.setattr(probes.psutil, "disk_io_counters", lambda **_: {
            "disk0": SimpleNamespace(read_bytes=int(self.now * 10), write_bytes=0)})
        monkeypatch.setattr(probes.psutil, "disk_partitions", lambda **_: [])
        monkeypatch.setattr(probes.psutil, "disk_usage", lambda _: SimpleNamespace(free=10**9))
        narration = NarrationBudget(80, 1, 1024, 15, 1024, 1, 120, 1000, 64, .1, 1000, 100,
            RuntimeCpuPolicy("darwin_background", "synthetic-base-observation"))
        self.probe = HostResourceProbe(tmp_path / "business.sqlite", disk_paths=(tmp_path,),
            budget=HostBudget(91, 100, None, 100, 10**9, 10**9, narration=narration), memory_monitor=self.monitor)
        self.resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
            "text_chars": 17, "section_count": 1, "reference_bytes": 100, "duration_seconds": 15,
            "max_output_bytes": 1024, "threads": 1, "timeout_seconds": 120}

    def advance(self, seconds):
        with self.condition:
            self.now += seconds
            self.condition.notify_all()

    def sample(self):
        with self.condition:
            self.now += 1
            self.condition.notify_all()
            return MemorySample("darwin", self.now, "synthetic", "normal", 10000, None, {})

    def wait_past(self, observation):
        with self.condition:
            assert self.condition.wait_for(lambda: self.now > observation, timeout=1)


@pytest.mark.parametrize("unit_count", [1, 2])
def test_base_observation_completes_both_windows_despite_slow_owner_checks(tmp_path, monkeypatch, unit_count):
    observations = BaseObservations(tmp_path, monkeypatch)
    checking, release_owner, finished = (threading.Event() for _ in range(3))
    class SlowOwner(Owner):
        def verify_submission(self, intent, actor):
            trusted = super().verify_submission(intent, actor)
            from dataclasses import replace
            return replace(trusted, resources=observations.resources)
        def check_current(self, request, conn):
            assert observations.probe.observing  # Observer starts before dispatch.
            if request.owner_id == "slow":
                checking.set()
                assert release_owner.wait(2)
            return "ready"
    class Executor(CompletingExecutor):
        def execute(self, grant, scheduler):
            super().execute(grant, scheduler)
            if len(self.calls) == unit_count:
                finished.set()
    executor = Executor()
    runtime = _runtime(tmp_path, resources=observations.probe, host_observer=observations.probe,
        registrations={"job_run": OwnerRegistration(SlowOwner(), executor)})
    for name in ("slow", "second")[:unit_count]:
        runtime.scheduler.submit(_intent(name), "member")
    try:
        runtime.start()
        assert checking.wait(1)
        observations.wait_past(11)  # Full 10s/2s recovery while the owner call is still blocked.
        assert not executor.calls
        release_owner.set()
        assert finished.wait(2)
        assert len(executor.calls) == unit_count and runtime.last_error is None
        check = observations.probe.admit(SimpleNamespace(id="read-probe", resources=observations.resources))
        assert check.verdict is True and check.checks['memory_policy']['stable_elapsed_seconds'] >= 10
        assert check.checks['disk_io_bytes_per_second']['window_complete']
        assert check.checks['disk_io_bytes_per_second']['window_seconds'] >= 3
    finally:
        release_owner.set()
        assert runtime.stop(2)
    assert not observations.probe.observing


def test_base_observer_stays_on_during_pause_and_failed_stop_then_joins(tmp_path, monkeypatch):
    observations = BaseObservations(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    class Executor(CompletingExecutor):
        def execute(self, grant, scheduler):
            entered.set()
            assert release.wait(2)
            super().execute(grant, scheduler)
    runtime = _runtime(tmp_path, executor=Executor(), host_observer=observations.probe)
    try:
        runtime.start()
        thread = observations.probe._observer_thread
        runtime.start()
        assert observations.probe._observer_thread is thread
        assert runtime.pause(1)
        observations.wait_past(observations.now)
        assert observations.probe.observing
        runtime.scheduler.submit(_intent("blocked-executor"), "member")
        runtime.resume()
        assert entered.wait(1)
        assert not runtime.stop(.01)
        observations.wait_past(observations.now)
        assert observations.probe.observing
    finally:
        release.set()
        assert runtime.stop(2)
    assert not observations.probe.observing and not thread.is_alive()


def test_dispatch_start_failure_stops_the_existing_base_observer(tmp_path, monkeypatch):
    observations = BaseObservations(tmp_path, monkeypatch)
    runtime = _runtime(tmp_path, host_observer=observations.probe)
    original = threading.Thread.start
    def fail_dispatch(thread):
        if thread.name == "device-dispatch":
            raise RuntimeError("synthetic thread startup failure")
        return original(thread)
    monkeypatch.setattr(threading.Thread, "start", fail_dispatch)
    with pytest.raises(RuntimeError, match="startup failure"):
        runtime.start()
    assert not observations.probe.observing and not runtime.running
    assert runtime.stop(1)


def test_factory_requires_explicit_probes_lock_and_initialized_database(tmp_path):
    database = _database(tmp_path)
    registration = {"job_run": OwnerRegistration(Owner(), CompletingExecutor())}
    kwargs = dict(database_path=database, device_lock_path=tmp_path / "device.lock",
                  device_access=Access(), registrations=registration,
                  resources=Resources(), processes=Processes())
    with pytest.raises(RuntimeUnavailable, match="探针"):
        build_device_runtime(**{**kwargs, "resources": None})
    with pytest.raises(RuntimeUnavailable, match="探针"):
        build_device_runtime(**{**kwargs, "processes": None})
    with pytest.raises(RuntimeUnavailable, match="锁路径"):
        build_device_runtime(**{**kwargs, "device_lock_path": "relative.lock"})
    with pytest.raises(RuntimeUnavailable, match="验证者"):
        build_device_runtime(**{**kwargs, "registrations": {}})
    with pytest.raises(RuntimeUnavailable, match="未初始化"):
        build_device_runtime(**{**kwargs, "database_path": tmp_path / "missing.sqlite"})


def test_driver_dispatches_one_registered_unit_and_rejects_unknown_owner(tmp_path):
    database = _database(tmp_path)
    unknown_writer = DeviceScheduler(database, tmp_path / "device.lock", owners=Owner(),
                                     resources=Resources(), processes=Processes())
    unknown = unknown_writer.submit(_intent("unhandled", owner_type="unregistered"), "member")
    executor = CompletingExecutor()
    runtime = _runtime(tmp_path, database=database, executor=executor)
    with pytest.raises(RuntimeUnavailable, match="没有已注册"):
        runtime.scheduler.submit(_intent("new-unhandled", owner_type="unregistered"), "member")
    known = runtime.scheduler.submit(_intent("known"), "member")
    assert runtime.run_once() is True
    assert len(executor.calls) == 1
    assert (executor.calls[0].owner_type, executor.calls[0].owner_id) == ("job_run", "known")
    assert runtime.scheduler._get(known.id).state == "done"
    assert runtime.scheduler._get(unknown.id).wait_reason == "owner_unknown"
    assert runtime.run_once() is False
    assert runtime.stop(0.1) is True


@pytest.mark.parametrize("failure", ["raised", "unreleased"])
def test_executor_failure_keeps_permit_and_exposes_fault(tmp_path, failure):
    class BrokenExecutor:
        def execute(self, grant, scheduler):
            if failure == "raised":
                raise RuntimeError("worker failed")

    runtime = _runtime(tmp_path, executor=BrokenExecutor())
    request = runtime.scheduler.submit(_intent("job"), "member")
    with pytest.raises((RuntimeError, RecoveryBlocked)):
        runtime.run_once()
    with sqlite3.connect(runtime.scheduler.database_path) as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "held"
    assert runtime.scheduler._get(request.id).state == "granted"
    assert isinstance(runtime.last_error, (RuntimeError, RecoveryBlocked))
    with pytest.raises(RuntimeUnavailable, match="已停止"):
        runtime.pause(0.1)
    assert runtime.stop(0.1) is True  # closes this process's fd, preserves the held ledger


def test_start_is_idempotent_and_stop_timeout_never_releases_active_unit(tmp_path):
    class BlockingExecutor:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.calls = []

        def execute(self, grant, scheduler):
            self.calls.append(grant)
            self.entered.set()
            assert self.release.wait(3)
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                    checkpoint_write=lambda conn, current: current.owner_id)

    executor = BlockingExecutor()
    runtime = _runtime(tmp_path, executor=executor)
    runtime.scheduler.submit(_intent("first"), "member")
    second = runtime.scheduler.submit(_intent("second"), "member")
    runtime.start()
    runtime.start()
    assert executor.entered.wait(2)
    for _ in range(5):
        runtime.wake()
    assert runtime.stop(0.01) is False
    with sqlite3.connect(runtime.scheduler.database_path) as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "held"
    competing = _runtime(tmp_path, database=runtime.scheduler.database_path)
    assert competing.scheduler.pick_and_grant() is None
    runtime.wake()  # a wake racing stop must not start the second unit
    executor.release.set()
    assert runtime.stop(2) is True
    assert len(executor.calls) == 1
    assert runtime.scheduler._get(second.id).state == "waiting"
    with pytest.raises(RuntimeUnavailable, match="已停止"):
        runtime.start()


def test_pause_between_grant_and_execute_leaves_admitted_unit_untouched(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    completed = threading.Event()
    class ObservedExecutor(CompletingExecutor):
        def execute(self, grant, scheduler):
            super().execute(grant, scheduler)
            completed.set()
    executor = ObservedExecutor()
    runtime = _runtime(tmp_path, executor=executor)
    runtime.scheduler.submit(_intent("first"), "member")
    pick = runtime.scheduler.pick_and_grant

    def admitted_then_wait():
        grant = pick()
        if grant is not None:
            entered.set()
            assert release.wait(3)
        return grant

    monkeypatch.setattr(runtime.scheduler, "pick_and_grant", admitted_then_wait)
    runtime.start()
    original_thread = runtime._thread
    try:
        assert entered.wait(2)
        assert runtime.pause(0.01) is False
        assert not runtime._stop.is_set() and not runtime._paused.is_set() and not runtime._closed
        with sqlite3.connect(runtime.scheduler.database_path) as conn:
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "held"
        release.set()
        assert completed.wait(2)
        assert runtime.stop(2)
        assert len(executor.calls) == 1
        assert runtime.last_error is None and runtime._thread is original_thread
    finally:
        release.set()
        runtime.stop(2)


def test_idle_pause_resume_uses_same_driver_scheduler_and_waiting_request(tmp_path):
    completed = threading.Event()
    class ObservedExecutor(CompletingExecutor):
        def execute(self, grant, scheduler):
            super().execute(grant, scheduler)
            completed.set()
    executor = ObservedExecutor()
    runtime = _runtime(tmp_path, executor=executor)
    runtime.start()
    thread, scheduler = runtime._thread, runtime.scheduler
    try:
        assert runtime.pause(1)
        request = scheduler.submit(_intent("after-pause"), "member")
        assert runtime.run_once() is False
        assert scheduler._get(request.id).state == "waiting"
        assert not runtime._stop.is_set() and not runtime._closed
        runtime.resume()
        assert completed.wait(2)
        assert runtime._thread is thread and runtime.scheduler is scheduler
        assert len(executor.calls) == 1 and scheduler._get(request.id).state == "done"
    finally:
        assert runtime.stop(2)
    with pytest.raises(RuntimeUnavailable, match="已停止"):
        runtime.resume()


def test_background_executor_error_is_visible_and_stops_before_next_unit(tmp_path):
    class RaisingExecutor:
        def execute(self, grant, scheduler):
            raise RuntimeError("worker failed")

    runtime = _runtime(tmp_path, executor=RaisingExecutor())
    first = runtime.scheduler.submit(_intent("first"), "member")
    second = runtime.scheduler.submit(_intent("second"), "member")
    runtime.start()
    runtime._thread.join(2)
    assert isinstance(runtime.last_error, RuntimeError)
    assert runtime.running is False
    assert runtime.scheduler._get(first.id).state == "granted"
    assert runtime.scheduler._get(second.id).state == "waiting"
    with pytest.raises(RuntimeUnavailable, match="已停止"):
        runtime.start()
    assert runtime.stop(0.1) is True


def test_pause_rejects_admitted_fault_before_background_catch(tmp_path):
    exception_returned, allow_background_catch = threading.Event(), threading.Event()
    fault = RuntimeError("admitted failure without checkpoint or permit release")
    class RaisingExecutor:
        def execute(self, grant, scheduler):
            scheduler.assert_grant(grant)
            raise fault
    runtime = _runtime(tmp_path, executor=RaisingExecutor())
    request = runtime.scheduler.submit(_intent("admitted-failure"), "member")
    original_run_once = runtime.run_once
    def hold_exception():
        try:
            return original_run_once()
        except RuntimeError:
            exception_returned.set()
            assert allow_background_catch.wait(3)
            raise
    runtime.run_once = hold_exception
    runtime.start()
    try:
        assert exception_returned.wait(2)
        assert not runtime._dispatch_lock.locked()
        assert runtime.last_error is fault and runtime._stop.is_set()
        assert not runtime._closed and not runtime._paused.is_set()
        ledger = runtime.scheduler.ledger_path.read_bytes()
        fd = runtime.scheduler._held_fd
        inode = os.fstat(fd).st_ino
        with pytest.raises(RuntimeUnavailable, match="已停止"):
            runtime.pause(0.1)
        with sqlite3.connect(runtime.scheduler.database_path) as conn:
            assert conn.execute("SELECT state,request_id FROM device_permit WHERE id=1").fetchone() == ("held", request.id)
        assert runtime.scheduler._get(request.id).state == "granted"
        assert runtime.scheduler.ledger_path.read_bytes() == ledger
        assert runtime.scheduler._held_fd == fd and os.fstat(fd).st_ino == inode
        assert not runtime._paused.is_set()
    finally:
        allow_background_catch.set()
        runtime._thread.join(2)
        assert not runtime.running
        assert runtime.stop(2)


def test_driver_rejects_freed_permit_with_unfinished_request_row(tmp_path):
    class CorruptingExecutor:
        def execute(self, grant, scheduler):
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                    checkpoint_write=lambda conn, current: current.owner_id)
            with sqlite3.connect(scheduler.database_path) as conn:
                conn.execute("UPDATE device_work_requests SET state='granted' WHERE id=?", (grant.request_id,))

    runtime = _runtime(tmp_path, executor=CorruptingExecutor())
    request = runtime.scheduler.submit(_intent("job"), "member")
    with pytest.raises(RecoveryBlocked, match="尚未提交"):
        runtime.run_once()
    assert runtime.scheduler._get(request.id).state == "granted"
    assert runtime.stop(0.1) is True


def test_unknown_resource_sample_never_reaches_executor(tmp_path):
    executor = CompletingExecutor()
    runtime = _runtime(tmp_path, executor=executor, resources=Resources(admit=None))
    request = runtime.scheduler.submit(_intent("job"), "member")
    assert runtime.run_once() is False
    assert executor.calls == []
    assert runtime.scheduler._get(request.id).wait_reason == "resource_unknown"
    assert runtime.stop(0.1) is True


def _post_grant_owner_change(tmp_path, verdict):
    """Change business input after a real grant, without forging its fences."""
    owner, executor, states, grants = Owner(), CompletingExecutor(), {}, []
    owner.check_current = lambda request, conn: states.get(request.owner_id, "ready")
    runtime = _runtime(tmp_path, registrations={"job_run": OwnerRegistration(owner, executor)})
    request = runtime.scheduler.submit(_intent("old-input"), "member")
    pick = runtime.scheduler.pick_and_grant
    def race():
        grant = pick()
        if grant is not None:
            grants.append(grant)
            if grant.request_id == request.id:
                states[grant.owner_id] = verdict
        return grant
    runtime.scheduler.pick_and_grant = race
    return runtime, request, executor, states, grants


def test_explicit_stale_before_executor_releases_and_next_valid_unit_runs(tmp_path, caplog):
    runtime, request, executor, _, grants = _post_grant_owner_change(tmp_path, "stale")
    next_request = runtime.scheduler.submit(_intent("valid-input"), "member")
    assert runtime.run_once() is True
    stale = runtime.scheduler._get(request.id)
    assert (stale.state, stale.wait_reason) == ("blocked", "input_stale")
    assert executor.calls == [] and runtime.last_error is None and not runtime._stop.is_set()
    assert runtime.scheduler._held_fd is None
    with sqlite3.connect(runtime.scheduler.database_path) as conn:
        assert conn.execute("SELECT state,request_id,children_json FROM device_permit WHERE id=1").fetchone() == ("free", None, "[]")
        assert conn.execute("SELECT event_type,reason,prior_state FROM device_request_events WHERE request_id=? ORDER BY id DESC LIMIT 1",
            (request.id,)).fetchone() == ("deferred", "input_stale", "granted")
    decision = [json.loads(record.args[0]) for record in caplog.records if record.msg == "Device owner current check refused: %s"][-1]
    assert decision == {"request_id": request.id, "attempt_number": 1, "permit_generation": grants[0].generation, "verdict": "stale"}
    assert runtime.run_once() is True
    assert len(executor.calls) == 1 and executor.calls[0].request_id == next_request.id
    assert runtime.scheduler._get(next_request.id).state == "done"
    assert runtime.last_error is None and runtime.stop(.1)


@pytest.mark.parametrize("verdict", ["unknown", "unauthorized", "dependency", "unexpected PRIVATE_INPUT"])
def test_other_owner_refusals_still_stop_with_held_permit(tmp_path, caplog, verdict):
    runtime, request, executor, _, _ = _post_grant_owner_change(tmp_path, verdict)
    with pytest.raises(PermissionError) as stopped:
        runtime.run_once()
    assert not isinstance(stopped.value, StaleGrantInput)
    assert runtime.last_error is stopped.value and runtime._stop.is_set()
    assert executor.calls == [] and runtime.scheduler._get(request.id).state == "granted"
    assert runtime.scheduler._held_fd is not None
    decision = [json.loads(record.args[0]) for record in caplog.records if record.msg == "Device owner current check refused: %s"][-1]
    assert decision["verdict"] == (verdict if verdict in {"unknown", "unauthorized", "dependency"} else "unknown")
    assert "PRIVATE_INPUT" not in caplog.text
    assert runtime.stop(.1)


@pytest.mark.parametrize("gate", ["child_active", "child_unknown", "resource_unknown"])
def test_stale_does_not_bypass_child_or_resource_release_proof(tmp_path, gate):
    runtime, request, executor, _, _ = _post_grant_owner_change(tmp_path, "stale")
    if gate == "resource_unknown":
        runtime.scheduler.resources.released = lambda grant: None
    else:
        runtime.scheduler.processes.children_released = lambda children: False if gate == "child_active" else None
    with pytest.raises(RecoveryBlocked):
        runtime.run_once()
    assert runtime.last_error is not None and runtime._stop.is_set()
    assert executor.calls == [] and runtime.scheduler._get(request.id).state == "granted"
    assert runtime.scheduler._held_fd is not None and runtime.stop(.1)


@pytest.mark.parametrize("second_verdict", ["ready", "unknown", "unauthorized", "dependency"])
def test_stale_defer_rechecks_owner_in_its_original_transaction(tmp_path, second_verdict):
    runtime, request, executor, states, _ = _post_grant_owner_change(tmp_path, "stale")
    defer = runtime.scheduler.defer_unit
    def changed(grant, **kwargs):
        states[grant.owner_id] = second_verdict
        return defer(grant, **kwargs)
    runtime.scheduler.defer_unit = changed
    with pytest.raises(ValueError, match="等待原因"):
        runtime.run_once()
    assert executor.calls == [] and runtime.scheduler._get(request.id).state == "granted"
    assert runtime.scheduler._held_fd is not None and runtime.last_error is not None and runtime.stop(.1)


def test_stale_business_input_cannot_hide_corrupted_permit(tmp_path):
    runtime, request, executor, _, _ = _post_grant_owner_change(tmp_path, "stale")
    pick = runtime.scheduler.pick_and_grant
    def corrupt():
        grant = pick()
        with sqlite3.connect(runtime.scheduler.database_path) as conn:
            conn.execute("UPDATE device_permit SET generation=generation+1 WHERE id=1")
        return grant
    runtime.scheduler.pick_and_grant = corrupt
    with pytest.raises(PermissionError) as stopped:
        runtime.run_once()
    assert not isinstance(stopped.value, StaleGrantInput)
    assert executor.calls == [] and runtime.scheduler._get(request.id).state == "granted"
    assert runtime.scheduler._held_fd is not None and runtime.last_error is stopped.value and runtime.stop(.1)


def test_stale_error_inside_executor_keeps_original_stop_boundary(tmp_path):
    class FailedExecutor:
        def execute(self, grant, scheduler):
            raise StaleGrantInput("executor failure")
    runtime = _runtime(tmp_path, executor=FailedExecutor())
    request = runtime.scheduler.submit(_intent("job"), "member")
    with pytest.raises(StaleGrantInput) as stopped:
        runtime.run_once()
    assert runtime.last_error is stopped.value and runtime._stop.is_set()
    assert runtime.scheduler._get(request.id).state == "granted"
    assert runtime.scheduler._held_fd is not None and runtime.stop(.1)
