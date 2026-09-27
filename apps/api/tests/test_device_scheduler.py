from __future__ import annotations

import multiprocessing
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.database_migrations import PRODUCTION_SCHEMA_VERSION, apply_production_migrations
from app.device_scheduler import (
    DeviceScheduler,
    ProcessIdentity,
    RecoveryBlocked,
    SchedulePolicy,
    SessionHeartbeat,
    SourceRef,
    SubmissionIntent,
    VerifiedSession,
    VerifiedWork,
    Window,
)


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class Owners:
    def __init__(self):
        self.revoked = False
        self.references: tuple[SourceRef, ...] = ()

    def verify_submission(self, intent, actor):
        if actor != "service" and actor not in {"member-a", "member-b", "main"}:
            raise PermissionError("unknown member")
        if intent.workspace_id == "private-b" and actor not in {"member-b", "main", "service"}:
            raise PermissionError("wrong workspace")
        if self.revoked:
            raise PermissionError("sharing revoked")
        kind = "background" if actor == "service" else ("preview_dependency" if intent.owner_type == "preview" else "normal_make")
        return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id, intent.input_digest,
                            intent.unit_key, None if actor == "service" else actor, kind,
                            {"cpu_threads": 2}, self.references)

    def check_current(self, request, conn):
        return "stale" if self.revoked else "ready"

    def can_manage(self, request, actor):
        return actor == "main" or actor == request.member_id

    def can_wake(self, request, actor):
        return actor in {"main", "member-a"} and request.workspace_id == "private-a"

    def verify_session(self, heartbeat, actor):
        if actor not in {"member-a", "member-b"}:
            raise PermissionError("no session")
        return VerifiedSession(heartbeat.session_id, heartbeat.workspace_id, actor)

    def can_view_detail(self, request, actor):
        return actor == request.member_id

    def can_view_device_summary(self, actor):
        return actor in {"main", "member-a", "member-b"}


class Resources:
    admit_value = True
    release_value = True
    continue_value = True

    def admit(self, request):
        return self.admit_value

    def continue_unit(self, grant):
        return self.continue_value

    def released(self, grant):
        return self.release_value


class Processes:
    def __init__(self):
        self.outcome = "released"

    def current(self):
        return ProcessIdentity(os.getpid(), f"test-{os.getpid()}", "test-boot")

    def recovery_state(self, holder, children):
        if children:
            return "active" if any(_pid_alive(child.pid) for child in children) else self.outcome
        return self.outcome

    def children_released(self, children):
        return not any(_pid_alive(child.pid) for child in children)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _db(path):
    with sqlite3.connect(path) as conn:
        apply_production_migrations(conn)
        assert conn.execute("SELECT max(version) FROM production_schema_migrations").fetchone()[0] == PRODUCTION_SCHEMA_VERSION


def _intent(owner_id, *, workspace="private-a", owner_type="job_run", unit="narration", policy=None):
    return SubmissionIntent(workspace, owner_type, owner_id, "sha256:one", unit, policy or SchedulePolicy())


def _scheduler(tmp_path, *, db_name="business.sqlite", lock_name="device.lock", clock=None,
               owners=None, resources=None, processes=None, activity=False):
    database = tmp_path / db_name
    _db(database)
    return DeviceScheduler(database, tmp_path / lock_name, owners=owners or Owners(),
                           resources=resources or Resources(), processes=processes or Processes(),
                           clock=clock or Clock(datetime(2026, 9, 27, 12, tzinfo=timezone.utc)),
                           activity_source_reliable=activity)


def _complete(scheduler, grant):
    return scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                   checkpoint_write=lambda conn, current: current.unit_key)


def _holder_process(database, lock_path, ready, release):
    sched = DeviceScheduler(database, lock_path, owners=Owners(), resources=Resources(), processes=Processes())
    try:
        grant = sched.pick_and_grant()
        ready.put(grant is not None)
        release.wait(10)
        if grant:
            _complete(sched, grant)
    finally:
        sched.close()


@pytest.mark.parametrize("second_database", [False, True])
def test_two_processes_share_one_host_permit_even_with_different_business_databases(tmp_path, second_database):
    first = _scheduler(tmp_path)
    first.submit(_intent("first"), "member-a")
    if second_database:
        first.submit(_intent("left-pending"), "member-a")
    second = _scheduler(tmp_path, db_name="other.sqlite" if second_database else "business.sqlite")
    if second_database:
        second.submit(_intent("second"), "member-b")
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Event()
    worker = context.Process(target=_holder_process, args=(first.database_path, first.lock_path, ready, release))
    worker.start()
    try:
        assert ready.get(timeout=10) is True
        assert second.pick_and_grant() is None
    finally:
        release.set()
        worker.join(10)
        assert worker.exitcode == 0
    if second_database:
        with pytest.raises(RecoveryBlocked, match="未处理"):
            second.pick_and_grant()  # first DB's untouched request must be checked


def test_fencing_checkpoint_atomicity_and_different_database_rejection(tmp_path):
    scheduler = _scheduler(tmp_path)
    scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    assert grant
    with pytest.raises(PermissionError, match="不匹配"):
        scheduler.assert_grant(replace(grant, input_digest="sha256:forged"))
    with pytest.raises(ValueError, match="同一 Production"):
        scheduler.complete_unit(grant, checkpoint_database_path=tmp_path / "other.sqlite",
                                checkpoint_write=lambda conn, current: None)
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE checkpoint_test (value TEXT)")

    def fails(conn, current):
        conn.execute("INSERT INTO checkpoint_test VALUES ('partial')")
        raise RuntimeError("checkpoint interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path, checkpoint_write=fails)
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM checkpoint_test").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "held"
    scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                            checkpoint_write=lambda conn, current: conn.execute("INSERT INTO checkpoint_test VALUES ('valid')") and "valid")
    scheduler.submit(_intent("next"), "member-a")
    new = scheduler.pick_and_grant()
    assert new and new.generation > grant.generation
    with pytest.raises(PermissionError, match="失效"):
        scheduler.assert_grant(grant)
    _complete(scheduler, new)


def test_live_or_unknown_child_blocks_recovery(tmp_path):
    processes = Processes()
    scheduler = _scheduler(tmp_path, processes=processes)
    scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        scheduler.register_child(grant, ProcessIdentity(child.pid, "child-start", "test-boot"))
        with pytest.raises(RecoveryBlocked, match="子进程"):
            _complete(scheduler, grant)
        scheduler.close()  # simulate parent loss; durable ledger remains held
        replacement = _scheduler(tmp_path, processes=processes)
        with pytest.raises(RecoveryBlocked, match="子进程"):
            replacement.pick_and_grant()
        child.terminate()
        child.wait(timeout=5)
        processes.outcome = "unknown"
        with pytest.raises(RecoveryBlocked, match="子进程"):
            replacement.pick_and_grant()
        processes.outcome = "released"
        resumed = replacement.pick_and_grant()
        assert resumed and resumed.generation > grant.generation
        _complete(replacement, resumed)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_member_rotation_bursts_and_aged_material(tmp_path):
    clock = Clock(datetime(2026, 9, 27, 12, tzinfo=timezone.utc))
    scheduler = _scheduler(tmp_path, clock=clock)
    scheduler.submit(_intent("a-1"), "member-a")
    scheduler.submit(_intent("a-2"), "member-a")
    scheduler.submit(_intent("b-1"), "member-b")
    first = scheduler.pick_and_grant()
    assert first and scheduler._get(first.request_id).member_id == "member-a"
    _complete(scheduler, first)
    second = scheduler.pick_and_grant()
    assert second and scheduler._get(second.request_id).member_id == "member-b"
    _complete(scheduler, second)
    third = scheduler.pick_and_grant()
    _complete(scheduler, third)

    for index in range(4):
        scheduler.submit(_intent(f"preview-{index}", owner_type="preview"), "member-a")
    scheduler.submit(_intent("normal"), "member-b")
    picked = []
    for _ in range(4):
        grant = scheduler.pick_and_grant()
        picked.append(scheduler._get(grant.request_id).work_class)
        _complete(scheduler, grant)
    assert picked == ["preview_dependency"] * 3 + ["normal_make"]
    remaining_preview = scheduler.pick_and_grant()
    assert remaining_preview and scheduler._get(remaining_preview.request_id).work_class == "preview_dependency"
    _complete(scheduler, remaining_preview)

    scheduler.submit(_intent("material", owner_type="material_asset"), "service")
    for index in range(3):
        scheduler.submit(_intent(f"normal-{index}"), "member-a")
    picked = []
    for _ in range(4):
        grant = scheduler.pick_and_grant()
        picked.append(scheduler._get(grant.request_id).work_class)
        _complete(scheduler, grant)
    assert "background" in picked

    aged_dir = tmp_path / "aged"
    aged_dir.mkdir()
    scheduler = _scheduler(aged_dir, clock=clock)
    aged = scheduler.submit(_intent("old-material", owner_type="material_asset"), "service")
    scheduler.submit(_intent("new-preview", owner_type="preview"), "member-a")
    ahead = scheduler.pick_and_grant()
    assert ahead and scheduler._get(ahead.request_id).work_class == "preview_dependency"
    _complete(scheduler, ahead)
    clock.advance(minutes=31)
    newer = scheduler.submit(_intent("new-material", owner_type="material_asset"), "service")
    scheduler.submit(_intent("another-preview", owner_type="preview"), "member-a")
    grant = scheduler.pick_and_grant()
    assert grant and grant.request_id == aged.id
    _complete(scheduler, grant)
    assert scheduler._get(newer.id).state == "waiting"


def test_window_idle_expired_session_and_permission_projection(tmp_path):
    clock = Clock(datetime(2026, 9, 27, 12, 59, tzinfo=timezone.utc))  # 20:59 Shanghai
    owners = Owners()
    owners.references = (SourceRef("shared-owner", "material_asset", "asset-1", "v1"),)
    scheduler = _scheduler(tmp_path, clock=clock, owners=owners, activity=True)
    scheduled = scheduler.submit(_intent("night", policy=SchedulePolicy("scheduled", clock.value,
                                Window("Asia/Shanghai", "22:00", "07:00"))), "member-a")
    assert scheduled.source_refs[0].workspace_id == "shared-owner"
    assert scheduler.pick_and_grant() is None
    with pytest.raises(PermissionError):
        scheduler.submit(_intent("foreign", workspace="private-b"), "member-a")
    clock.advance(hours=2)
    scheduler.heartbeat_session(SessionHeartbeat("session-a", "private-a", playing=True), "member-a")
    idle = scheduler.submit(_intent("idle", policy=SchedulePolicy("idle")), "member-a")
    assert scheduler.view_for(idle.id, "main") == {"state": "waiting", "wait_reason": None, "device_busy": False}
    assert scheduler.list_queue_for("main")["mine"] == []
    assert "night" not in str(scheduler.list_queue_for("main"))
    assert scheduler.change_policy(idle.id, SchedulePolicy("idle"), "main") == {
        "state": "waiting", "wait_reason": None, "device_busy": False}
    with pytest.raises(PermissionError):
        scheduler.view_for(idle.id, "stranger")
    owners.revoked = True
    assert scheduler.pick_and_grant() is None
    owners.revoked = False
    first = scheduler.pick_and_grant()
    assert first and first.request_id == scheduled.id
    _complete(scheduler, first)
    assert scheduler.pick_and_grant() is None  # active playback blocks idle
    clock.advance(seconds=31)
    resumed = scheduler.pick_and_grant()
    assert resumed and resumed.request_id == idle.id
    _complete(scheduler, resumed)


def test_cross_midnight_window_closes_at_next_day_seven(tmp_path):
    clock = Clock(datetime(2026, 9, 27, 15, tzinfo=timezone.utc))  # 23:00 Shanghai
    scheduler = _scheduler(tmp_path, clock=clock)
    policy = SchedulePolicy("scheduled", clock.value, Window("Asia/Shanghai", "22:00", "07:00"))
    first = scheduler.submit(_intent("night-1", policy=policy), "member-a")
    grant = scheduler.pick_and_grant()
    assert grant and grant.request_id == first.id
    _complete(scheduler, grant)
    clock.advance(hours=8)  # 07:00 Shanghai, window ended
    second = scheduler.submit(_intent("night-2", policy=policy), "member-a")
    assert scheduler.pick_and_grant() is None
    assert scheduler._get(second.id).wait_reason == "outside_window"
    clock.advance(hours=15)  # 22:00 Shanghai next day
    resumed = scheduler.pick_and_grant()
    assert resumed and resumed.request_id == second.id
    _complete(scheduler, resumed)


def test_unknown_resources_and_reused_request_age_do_not_fake_readiness(tmp_path):
    clock = Clock(datetime(2026, 9, 27, 12, tzinfo=timezone.utc))
    resources = Resources()
    resources.admit_value = None
    scheduler = _scheduler(tmp_path, clock=clock, resources=resources)
    intent = _intent("material", owner_type="material_asset")
    first = scheduler.submit(intent, "service")
    assert scheduler.pick_and_grant() is None
    clock.advance(minutes=31)
    again = scheduler.submit(intent, "service")
    assert again.id == first.id and again.first_ready_at_utc == first.first_ready_at_utc
    resources.admit_value = True
    grant = scheduler.pick_and_grant()
    assert grant and scheduler._get(grant.request_id).eligible_wait_seconds == 0
    resources.continue_value = None
    with pytest.raises(RecoveryBlocked):
        scheduler.assert_grant(grant)
    resources.continue_value = True
    _complete(scheduler, grant)


def test_material_hash_provisional_revision_then_real_sha_and_manual_wake_is_idempotent(tmp_path):
    scheduler = _scheduler(tmp_path)
    hash_unit = SubmissionIntent("private-a", "material_asset", "asset-1",
                                 "provisional:stat-and-bound-root", "material.hash:0", SchedulePolicy())
    with pytest.raises(KeyError):
        scheduler.wake_existing(hash_unit, "member-a")
    with pytest.raises(PermissionError):
        scheduler.submit(hash_unit, "member-a")
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = list(pool.map(lambda _: scheduler.submit(hash_unit, "service"), range(2)))
    assert requests[0].id == requests[1].id
    original = requests[0]
    with pytest.raises(PermissionError):
        scheduler.wake_existing(hash_unit, "member-b")
    assert scheduler.wake_existing(hash_unit, "member-a")["state"] == "waiting"
    after_wake = scheduler._get(original.id)
    assert (after_wake.member_id, after_wake.work_class, after_wake.first_ready_at_utc) == (
        None, "background", original.first_ready_at_utc)
    grant = scheduler.pick_and_grant()
    assert grant and grant.request_id == original.id
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE material_hash_checkpoint (asset_id TEXT PRIMARY KEY, digest TEXT NOT NULL)")
    real_sha = "a" * 64
    scheduler.complete_unit(
        grant, checkpoint_database_path=scheduler.database_path,
        checkpoint_write=lambda conn, current: conn.execute(
            "INSERT INTO material_hash_checkpoint VALUES ('asset-1', ?)", (real_sha,)
        ) and real_sha,
    )
    # If the process stops here, the next scan reads this durable digest and
    # idempotently submits the next unit; no cross-digest continuation is made.
    next_unit = SubmissionIntent("private-a", "material_asset", "asset-1",
                                 f"sha256:{real_sha}", "material.probe:0", SchedulePolicy())
    first_next = scheduler.submit(next_unit, "service")
    assert scheduler.submit(next_unit, "service").id == first_next.id
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT digest FROM material_hash_checkpoint").fetchone()[0] == real_sha
        assert conn.execute("SELECT count(*) FROM device_work_requests WHERE owner_id='asset-1'").fetchone()[0] == 2


def test_same_database_authorization_rechecked_before_execution_and_checkpoint(tmp_path):
    class DatabaseOwners(Owners):
        def check_current(self, request, conn):
            # The scheduler passes its exact permit transaction connection.
            allowed = conn.execute("SELECT allowed FROM source_grants WHERE id=1").fetchone()[0]
            return "ready" if allowed else "stale"

    owners = DatabaseOwners()
    scheduler = _scheduler(tmp_path, owners=owners)
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE source_grants (id INTEGER PRIMARY KEY, allowed INTEGER)")
        conn.execute("INSERT INTO source_grants VALUES (1, 1)")
        conn.execute("CREATE TABLE checkpoint_test (value TEXT)")
    scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    scheduler.assert_grant(grant)
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("UPDATE source_grants SET allowed=0 WHERE id=1")
    with pytest.raises(PermissionError, match="共享授权"):
        scheduler.assert_grant(grant)
    with pytest.raises(PermissionError, match="共享授权"):
        scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                checkpoint_write=lambda conn, current: conn.execute("INSERT INTO checkpoint_test VALUES ('invalid')") and "invalid")
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM checkpoint_test").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "held"
    # An unavailable cross-database authorization check must likewise refuse
    # execution; it is not a claim of atomic revocation across databases.
    owners.check_current = lambda request, conn: "unknown"
    with pytest.raises(PermissionError):
        scheduler.assert_grant(grant)
    scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                            checkpoint_write=lambda conn, current: None, cancelled=True)
