from __future__ import annotations

import multiprocessing
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic

import pytest

from app.database_migrations import PRODUCTION_SCHEMA_VERSION, _MIGRATIONS, apply_production_migrations
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
    VerifiedRetry,
    Window,
    material_stage_unit_key,
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
                            {"cpu_threads": 2}, self.references,
                            intent.unit_key.rsplit(":", 1)[-1]
                            if intent.owner_type == "material_asset" else None)

    def check_current(self, request, conn):
        return "unauthorized" if self.revoked else "ready"

    def capture_local(self, request, conn):
        return None

    def check_local(self, request, conn, observed):
        return self.check_current(request, conn)

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

    def verify_retry(self, request, actor, reason, conn):
        if actor not in {"main", "member-a"} or request.workspace_id != "private-a":
            raise PermissionError("cannot retry")
        if reason == "result_invalid":
            valid = conn.execute("SELECT valid FROM checkpoint_validity WHERE request_id=?", (request.id,)).fetchone()
            if valid is None or valid[0]:
                raise PermissionError("checkpoint still valid")
        def recheck(current):
            if reason == "result_invalid":
                valid = current.execute("SELECT valid FROM checkpoint_validity WHERE request_id=?", (request.id,)).fetchone()
                if valid is None or valid[0]:
                    raise PermissionError("checkpoint still valid")
        return VerifiedRetry(actor, recheck)


class Resources:
    admit_value = True
    release_value = True
    continue_value = True

    def admit(self, request):
        from app.host_resource_probes import HostResourceDecision
        return HostResourceDecision(self.admit_value, dict(request.resources), {})

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


def _material_ingestion_table(conn):
    # ProductionRepository creates this legacy table before applying migrations.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS material_ingestion_files (
            workspace_id TEXT NOT NULL, file_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            ctime_ns INTEGER NOT NULL DEFAULT 0, inode INTEGER NOT NULL DEFAULT 0,
            observed_at REAL NOT NULL, completed_digest TEXT, attempted_digest TEXT,
            state TEXT NOT NULL, PRIMARY KEY (workspace_id, file_path))"""
    )


def _db(path):
    with sqlite3.connect(path) as conn:
        _material_ingestion_table(conn)
        apply_production_migrations(conn)
        assert conn.execute("SELECT max(version) FROM production_schema_migrations").fetchone()[0] == PRODUCTION_SCHEMA_VERSION


def _intent(owner_id, *, workspace="private-a", owner_type="job_run", unit="narration", policy=None):
    if owner_type == "material_asset" and unit == "narration":
        unit = material_stage_unit_key("hash:v1", "c" * 64)
    digest = "provisional:stat-and-bound-root" if unit.startswith("hash:v1:") else "sha256:one"
    return SubmissionIntent(workspace, owner_type, owner_id, digest, unit, policy or SchedulePolicy())


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


def _sqlite_connection_facts(conn):
    # Read the actual connection policy; never set a PRAGMA to make contention pass.
    return {"database": conn.execute("PRAGMA database_list").fetchone()[2],
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "busy_timeout_ms": conn.execute("PRAGMA busy_timeout").fetchone()[0],
            "isolation_level": conn.isolation_level}


def _trace_sqlite_commits(monkeypatch, owner):
    connect = owner._connect
    attempted, connections, statements = Event(), [], []

    def traced_connect():
        conn = connect()
        connections.append(_sqlite_connection_facts(conn))

        def trace(sql):
            normalized = sql.strip().upper()
            statements.append(normalized)
            if normalized == "COMMIT":
                attempted.set()

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(owner, "_connect", traced_connect)
    return attempted, connections, statements


def _sqlite_table_facts(repository, tables):
    with closing(repository._connect()) as conn:
        return {table: sorted([dict(row) for row in conn.execute(f"SELECT * FROM {table}")],
                              key=lambda row: json.dumps(row, sort_keys=True)) for table in tables}


@pytest.mark.parametrize("hold_until_timeout", [False, True], ids=["release_within_wait", "timeout_then_resume"])
def test_production_snapshot_contends_with_original_scheduler_checkpoint(tmp_path, monkeypatch, hold_until_timeout):
    from app.creation.contracts import JobRun, TaskEvent
    from test_creation_execution import _seed

    repository, task_id, _, _ = _seed(tmp_path, workspace_id="private-a", voice=False)
    scheduler = DeviceScheduler(repository.database_path, tmp_path / "device.lock", owners=Owners(),
                                resources=Resources(), processes=Processes())
    job = repository.create_job_run(JobRun("snapshot-job", "private-a", task_id, "snapshot-checkpoint",
                                           "snapshot-job", "sha256:one"))
    repository.claim_job_run(job.workspace_id, job.id)
    request = scheduler.submit(_intent(job.id), "member-a")
    grant = scheduler.pick_and_grant()
    assert grant is not None and grant.request_id == request.id
    event_id = "snapshot-checkpoint"
    tables = ("job_runs", "task_events", "device_work_requests", "device_request_events", "device_permit")
    before = _sqlite_table_facts(repository, tables)
    ledger_before = scheduler.ledger_path.read_bytes()
    attempted, connections, statements = _trace_sqlite_commits(monkeypatch, scheduler)

    def checkpoint(conn, current):
        assert conn.in_transaction and current.request_id == request.id
        repository.finish_job_run(job.workspace_id, job.id, status="succeeded", connection=conn)
        repository.execution_records.append_task_event(TaskEvent(event_id, job.workspace_id, task_id, job.id,
                                               "snapshot_checkpoint", {}), connection=conn)
        return event_id

    def complete():
        return scheduler.complete_unit(grant, checkpoint_database_path=repository.database_path,
                                       checkpoint_write=checkpoint)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with repository.creation_execution_read() as reader:
            snapshot = repository.read_creation_execution_snapshot(job.workspace_id, task_id, connection=reader)
            reader_facts = _sqlite_connection_facts(reader)
            assert snapshot.task.id == task_id and reader.in_transaction
            started = monotonic()
            pending = pool.submit(complete)
            assert attempted.wait(10), "writer did not reach its COMMIT attempt"
            assert not pending.done()
            if hold_until_timeout:
                with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                    pending.result(timeout=reader_facts["busy_timeout_ms"] / 1000 + 5)
                assert "ROLLBACK" in statements
                assert _sqlite_table_facts(repository, tables) == before
                assert scheduler.ledger_path.read_bytes() == ledger_before
                assert scheduler._get(request.id).state == "granted"
        elapsed = monotonic() - started  # Reader closes only after the observed attempt/failure.
        if not hold_until_timeout:
            assert pending.result(timeout=10) == event_id
    first_connections = list(connections)
    if hold_until_timeout:
        scheduler.assert_grant(grant)
        assert complete() == event_id  # Explicit resume of the original grant, without resubmission.
    scheduler.assert_released(grant)
    after = _sqlite_table_facts(repository, tables)
    assert [row["id"] for row in after["job_runs"]] == [job.id]
    assert after["job_runs"][0]["status"] == "succeeded"
    assert after["job_runs"][0]["attempt"] == before["job_runs"][0]["attempt"]
    assert [row["id"] for row in after["task_events"]] == [event_id]
    assert len(after["device_work_requests"]) == 1
    finished = after["device_work_requests"][0]
    assert (finished["id"], finished["state"], finished["checkpoint_ref"]) == (request.id, "done", event_id)
    original = before["device_work_requests"][0]
    assert (finished["attempt_number"], finished["last_grant_generation"]) == (original["attempt_number"], original["last_grant_generation"])
    assert after["device_request_events"] == before["device_request_events"]
    assert after["device_permit"][0]["state"] == "free"
    assert all(item["database"] == str(repository.database_path) for item in first_connections)
    print("SQLITE_LOCK_EVIDENCE " + json.dumps({"owner": "scheduler.complete_unit", "timeout": hold_until_timeout,
        "reader": reader_facts, "writer_connections": first_connections, "elapsed_seconds": elapsed,
        "request_id": request.id, "job_id": job.id, "one_checkpoint": True,
        "timeout_rollback_verified": hold_until_timeout}, sort_keys=True))


def test_preparation_dispatch_scope_preserves_other_requests_and_shared_permit(tmp_path):
    ordinary = _scheduler(tmp_path)
    old = ordinary.submit(_intent("old-work"), "member-a")
    other = ordinary.submit(_intent("other-plan", owner_type="device_preparation"), "member-a")
    current = ordinary.submit(_intent("current-plan", owner_type="device_preparation"), "member-a")
    def untouched():
        with ordinary._connect() as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT * FROM device_work_requests WHERE id IN (?,?) ORDER BY id", (old.id, other.id))]
    before = untouched()
    class PreparationOnly(Owners):
        def check_current(self, request, conn):
            assert (request.owner_type, request.owner_id) == ("device_preparation", "current-plan")
            return super().check_current(request, conn)
    preparation = DeviceScheduler(ordinary.database_path, ordinary.lock_path,
        owners=PreparationOnly(), resources=Resources(), processes=Processes(), clock=ordinary.clock,
        dispatch_owner=("device_preparation", "current-plan"))
    grant = preparation.pick_and_grant()
    assert grant is not None and grant.request_id == current.id
    try:
        assert untouched() == before
        assert ordinary.pick_and_grant() is None  # Scope does not bypass the machine lock.
    finally:
        _complete(preparation, grant)
    assert preparation.pick_and_grant() is None
    assert untouched() == before
    resumed = ordinary.pick_and_grant()
    assert resumed is not None and resumed.request_id in {old.id, other.id}
    _complete(ordinary, resumed)


@pytest.mark.parametrize("operation", ["cancel", "policy"])
def test_slow_management_identity_keeps_sqlite_writable(tmp_path, operation):
    entered, proceed = Event(), Event()
    class SlowManagement(Owners):
        def can_manage(self, request, actor):
            entered.set()
            assert proceed.wait(3)
            return super().can_manage(request, actor)
    scheduler = _scheduler(tmp_path, owners=SlowManagement())
    request = scheduler.submit(_intent("managed"), "member-a")
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE ordinary_edits(value TEXT)")
    def manage():
        return (scheduler.cancel(request.id, "member-a") if operation == "cancel"
                else scheduler.change_policy(request.id, SchedulePolicy(mode="idle"), "member-a"))
    def edit():
        with sqlite3.connect(scheduler.database_path, timeout=.25) as conn:
            conn.execute("INSERT INTO ordinary_edits VALUES ('saved')")
    with ThreadPoolExecutor(max_workers=2) as pool:
        managed = pool.submit(manage)
        try:
            assert entered.wait(2)
            pool.submit(edit).result(timeout=1)
        finally:
            proceed.set()
        managed.result(timeout=2)
    assert scheduler._get(request.id).state == ("cancelled" if operation == "cancel" else "waiting")


@pytest.mark.parametrize("operation", ["complete", "defer", "register_child", "retry"])
@pytest.mark.parametrize("mutation", [None, "source", "cancel"])
def test_owner_observation_before_atomic_write_keeps_edits_live_and_rechecks_input(tmp_path, operation, mutation):
    entered, proceed = Event(), Event()

    class SqlOwners(Owners):
        blocking = False

        def capture_local(self, request, conn):
            expected = conn.execute("SELECT version FROM owner_inputs").fetchone()[0]
            return lambda current: ("ready" if current.execute("SELECT version FROM owner_inputs").fetchone()[0] == expected else "stale")

        def check_current(self, request, conn):
            assert not conn.in_transaction
            if self.blocking:
                entered.set()
                assert proceed.wait(3)
            return "ready"

        def check_local(self, request, conn, observed):
            assert conn.in_transaction
            return observed(conn)

    owners = SqlOwners()
    scheduler = _scheduler(tmp_path, owners=owners)
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.executescript("CREATE TABLE owner_inputs(version INTEGER); INSERT INTO owner_inputs VALUES(1); CREATE TABLE ordinary_edits(value TEXT)")
    request = scheduler.submit(_intent("commit-observation"), "member-a")
    grant = scheduler.pick_and_grant()
    if operation == "retry":
        scheduler.fail_unit(grant, reason="fixture_failure", failure_write=lambda conn, current: None)
    owners.blocking = True
    writes = []

    def write_operation():
        if operation == "complete":
            return scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                checkpoint_write=lambda conn, current: writes.append("checkpoint") or "saved")
        if operation == "defer":
            return scheduler.defer_unit(grant, reason="resource_pressure",
                retry_not_before_utc=scheduler._now() + timedelta(seconds=30),
                owner_database_path=scheduler.database_path, owner_write=lambda conn, current: writes.append("deferred"))
        if operation == "retry":
            return scheduler.retry_existing(request.id, "member-a", reason="failed")
        return scheduler.register_child(grant, ProcessIdentity(999, "child-start", "child-boot"))

    def ordinary_edit():
        with sqlite3.connect(scheduler.database_path, timeout=.25) as conn:
            conn.execute("INSERT INTO ordinary_edits VALUES ('saved')")
            if mutation == "source":
                conn.execute("UPDATE owner_inputs SET version=2")
        if mutation == "cancel":
            scheduler.cancel(request.id, "member-a")

    with ThreadPoolExecutor(max_workers=2) as pool:
        committing = pool.submit(write_operation)
        try:
            assert entered.wait(2)
            pool.submit(ordinary_edit).result(timeout=1)
        finally:
            proceed.set()
        # A stale input can be recorded as a deferral, but cannot publish a
        # successful checkpoint, register a child, or reopen a terminal unit.
        refused = mutation == "cancel" and operation != "retry" or mutation == "source" and operation != "defer"
        if refused:
            with pytest.raises(PermissionError):
                committing.result(timeout=2)
            assert not writes
        else:
            committing.result(timeout=2)
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM ordinary_edits").fetchone()[0] == 1
    scheduler.close()


@pytest.mark.parametrize("phase,observation", [("owner", 1), ("owner", 2), ("resource", 1), ("resource", 2)])
def test_slow_admission_observations_leave_sqlite_available_for_ordinary_writes(tmp_path, phase, observation):
    entered, proceed = Event(), Event()

    class SlowOwners(Owners):
        calls = 0

        def check_current(self, request, conn):
            assert not conn.in_transaction
            self.calls += 1
            if phase == "owner" and self.calls == observation:
                entered.set()
                assert proceed.wait(3)
            return "ready"

        def check_local(self, request, conn, observed):
            assert conn.in_transaction
            return "ready"

    class SlowResources(Resources):
        calls = 0

        def admit(self, request):
            self.calls += 1
            if phase == "resource" and self.calls == observation:
                entered.set()
                assert proceed.wait(3)
            return super().admit(request)

    scheduler = _scheduler(tmp_path, owners=SlowOwners(), resources=SlowResources())
    scheduler.submit(_intent("ordinary-edit-during-observation"), "member-a")
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE ordinary_edits (value TEXT)")

    def write_edit():
        with sqlite3.connect(scheduler.database_path, timeout=.25) as conn:
            conn.execute("INSERT INTO ordinary_edits VALUES ('saved')")

    with ThreadPoolExecutor(max_workers=2) as pool:
        picking = pool.submit(scheduler.pick_and_grant)
        try:
            assert entered.wait(2)
            pool.submit(write_edit).result(timeout=1)
            assert not proceed.is_set()
        finally:
            proceed.set()
        assert picking.result(timeout=2) is not None
    scheduler.close()


@pytest.mark.parametrize("mutation,wait_reason", [("cancel", None), ("source", "input_stale"), ("revoke", "authorization_lost")])
def test_admission_rechecks_cancel_source_and_permission_after_observation(tmp_path, mutation, wait_reason):
    entered, proceed = Event(), Event()

    class ObservingOwners(Owners):
        def verdict(self, request, conn):
            row = conn.execute("SELECT digest,allowed FROM owner_inputs WHERE owner_id=?", (request.owner_id,)).fetchone()
            return "unauthorized" if not row[1] else "ready" if row[0] == request.input_digest else "stale"

        def check_current(self, request, conn):
            assert not conn.in_transaction
            observed = self.verdict(request, conn)
            entered.set()
            assert proceed.wait(3)
            return observed

        def check_local(self, request, conn, observed):
            return self.verdict(request, conn)

    scheduler = _scheduler(tmp_path, owners=ObservingOwners())
    request = scheduler.submit(_intent("observed-source"), "member-a")
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE owner_inputs (owner_id TEXT PRIMARY KEY,digest TEXT,allowed INTEGER)")
        conn.execute("INSERT INTO owner_inputs VALUES (?, ?, 1)", (request.owner_id, request.input_digest))
    with ThreadPoolExecutor(max_workers=1) as pool:
        picking = pool.submit(scheduler.pick_and_grant)
        try:
            assert entered.wait(2)
            if mutation == "cancel":
                scheduler.cancel(request.id, "member-a")
            else:
                with sqlite3.connect(scheduler.database_path, timeout=.25) as conn:
                    if mutation == "source":
                        conn.execute("UPDATE owner_inputs SET digest='sha256:new'")
                    else:
                        conn.execute("UPDATE owner_inputs SET allowed=0")
        finally:
            proceed.set()
        assert picking.result(timeout=2) is None
    with scheduler._connect() as conn:
        row = conn.execute("SELECT state,wait_reason FROM device_work_requests WHERE id=?", (request.id,)).fetchone()
        assert row[0] == ("cancelled" if mutation == "cancel" else "blocked")
        if wait_reason is not None:
            assert row[1] == wait_reason


def test_selected_candidate_gets_fresh_resources_after_a_later_owner_changes_host_state(tmp_path):
    class ChangingResources(Resources):
        busy = False

        def admit(self, request):
            from app.host_resource_probes import HostResourceDecision
            return HostResourceDecision(not (request.owner_id == "first" and self.busy), dict(request.resources), {})

    resources = ChangingResources()

    class ChangingOwners(Owners):
        def check_current(self, request, conn):
            if request.owner_id == "later":
                resources.busy = True
            return "ready"

        def check_local(self, request, conn, observed):
            return "ready"

    scheduler = _scheduler(tmp_path, owners=ChangingOwners(), resources=resources)
    first = scheduler.submit(_intent("first"), "member-a")
    scheduler.clock.advance(seconds=1)
    scheduler.submit(_intent("later"), "member-a")
    assert scheduler.pick_and_grant() is None
    with scheduler._connect() as conn:
        assert conn.execute("SELECT wait_reason FROM device_work_requests WHERE id=?", (first.id,)).fetchone()[0] == "resource_busy"
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    grant = scheduler.pick_and_grant()
    assert grant is not None and grant.owner_id == "later"
    _complete(scheduler, grant)


@pytest.mark.parametrize("reason", ["scheduled_time", "retry_not_before", "outside_window"])
def test_requests_not_due_skip_owner_and_resource_observation(tmp_path, reason):
    class NotCalledOwners(Owners):
        def check_current(self, request, conn):
            raise AssertionError("not-due work must not call the owner")

    class NotCalledResources(Resources):
        def admit(self, request):
            raise AssertionError("not-due work must not sample resources")

    clock = Clock(datetime(2026, 9, 27, 12, tzinfo=timezone.utc))
    policy = SchedulePolicy(not_before_utc=clock() + timedelta(minutes=1)) if reason == "scheduled_time" else (
        SchedulePolicy(window=Window("UTC", "13:00", "14:00")) if reason == "outside_window" else SchedulePolicy())
    scheduler = _scheduler(tmp_path, clock=clock, owners=NotCalledOwners(), resources=NotCalledResources())
    request = scheduler.submit(_intent("not-due", policy=policy), "member-a")
    if reason == "retry_not_before":
        with scheduler._connect() as conn:
            conn.execute("UPDATE device_work_requests SET retry_not_before_utc=? WHERE id=?", ((clock() + timedelta(minutes=1)).isoformat(), request.id))
    assert scheduler.pick_and_grant() is None
    with scheduler._connect() as conn:
        assert conn.execute("SELECT wait_reason FROM device_work_requests WHERE id=?", (request.id,)).fetchone()[0] == reason


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


def _completed_stale_history(scheduler, *, state="blocked", reason="input_stale",
                             generation=None, owner="creation_job", job_status="succeeded",
                             job_kind="creation_task_execution", job_workspace="private-a"):
    # Establish a genuinely free shared ledger first, then add never-granted history.
    scheduler.submit(_intent("prior-unit"), "member-a")
    granted = scheduler.pick_and_grant()
    _complete(scheduler, granted)
    request_id = scheduler.submit(_intent("historical-job", owner_type=owner), "member-a").id
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET state=?,wait_reason=?,last_grant_generation=? WHERE id=?",
                     (state, reason, generation, request_id))
        conn.execute("""INSERT INTO job_runs
            (id,workspace_id,task_id,kind,idempotency_key,input_digest,status,progress,attempt,
             output_refs_json,created_at,updated_at)
            VALUES ('historical-job',?,'synthetic-task',?,'historical-key','original',?,100,1,'[]','2026-10-12','2026-10-12')""",
                     (job_workspace, job_kind, job_status))
    return request_id


def _snapshot_old_rows(scheduler):
    with sqlite3.connect(scheduler.database_path) as conn:
        return (conn.execute("SELECT * FROM device_work_requests ORDER BY id").fetchall(),
                conn.execute("SELECT * FROM device_permit ORDER BY id").fetchall(),
                conn.execute("SELECT * FROM job_runs ORDER BY id").fetchall())


def test_completed_never_granted_stale_history_allows_handoff_without_old_row_changes(tmp_path):
    first = _scheduler(tmp_path)
    _completed_stale_history(first)
    before = _snapshot_old_rows(first)
    second = _scheduler(tmp_path, db_name="other.sqlite")
    second.submit(_intent("current-unit"), "member-a")
    granted = second.pick_and_grant()
    assert granted is not None
    assert _snapshot_old_rows(first) == before
    _complete(second, granted)
    assert _snapshot_old_rows(first) == before


@pytest.mark.parametrize("difference", [
    {"state":"waiting"}, {"state":"granted"}, {"state":"yield_requested"},
    {"reason":"resource_wait"}, {"reason":None}, {"generation":1}, {"generation":0},
    {"owner":"job_run"}, {"job_status":"failed"}, {"job_status":"cancelled"},
    {"job_status":"running"}, {"job_status":"queued"}, {"job_status":"interrupted"},
    {"job_kind":"task_media"}, {"job_workspace":"private-b"},
])
def test_handoff_exemption_requires_every_canonical_history_condition(tmp_path, difference):
    first = _scheduler(tmp_path)
    _completed_stale_history(first, **difference)
    before = _snapshot_old_rows(first)
    second = _scheduler(tmp_path, db_name="other.sqlite")
    second.submit(_intent("current-unit"), "member-a")
    with pytest.raises(RecoveryBlocked, match="未处理"):
        second.pick_and_grant()
    assert _snapshot_old_rows(first) == before


@pytest.mark.parametrize("missing", ["table", "job"])
def test_missing_canonical_job_facts_still_protect_old_pending_work(tmp_path, missing):
    first = _scheduler(tmp_path)
    _completed_stale_history(first)
    with sqlite3.connect(first.database_path) as conn:
        conn.execute("DROP TABLE job_runs" if missing == "table" else "DELETE FROM job_runs")
        pending_before = conn.execute("SELECT * FROM device_work_requests ORDER BY id").fetchall()
    second = _scheduler(tmp_path, db_name="other.sqlite")
    second.submit(_intent("current-unit"), "member-a")
    with pytest.raises(RecoveryBlocked, match="未处理"):
        second.pick_and_grant()
    with sqlite3.connect(first.database_path) as conn:
        assert conn.execute("SELECT * FROM device_work_requests ORDER BY id").fetchall() == pending_before


def test_eligible_history_does_not_hide_another_pending_request_or_nonfree_permit(tmp_path):
    first = _scheduler(tmp_path)
    _completed_stale_history(first)
    second = _scheduler(tmp_path, db_name="other.sqlite")
    second.submit(_intent("current-unit"), "member-a")
    waiting = first.submit(_intent("still-waiting"), "member-a")
    before = _snapshot_old_rows(first)
    with pytest.raises(RecoveryBlocked, match="未处理"):
        second.pick_and_grant()
    assert _snapshot_old_rows(first) == before
    with sqlite3.connect(first.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET state='done' WHERE id=?", (waiting.id,))
        conn.execute("UPDATE device_permit SET state='reconciling' WHERE id=1")
    before = _snapshot_old_rows(first)
    with pytest.raises(RecoveryBlocked, match="未处理"):
        second.pick_and_grant()
    assert _snapshot_old_rows(first) == before


def test_empty_pending_queue_keeps_handoff_even_without_job_table(tmp_path):
    first = _scheduler(tmp_path)
    first.submit(_intent("prior-unit"), "member-a")
    _complete(first, first.pick_and_grant())
    with sqlite3.connect(first.database_path) as conn:
        conn.execute("DROP TABLE job_runs")
    second = _scheduler(tmp_path, db_name="other.sqlite")
    second.submit(_intent("current-unit"), "member-a")
    granted = second.pick_and_grant()
    assert granted is not None
    _complete(second, granted)


def test_same_kind_second_request_waits_until_permit_released(tmp_path):
    # One host permit is held until a unit completes. A second request of the
    # same kind stays queued (pick_and_grant returns None) rather than gaining
    # a second license in flight; it is granted only after the first releases.
    scheduler = _scheduler(tmp_path)
    scheduler.submit(_intent("first"), "member-a")
    scheduler.submit(_intent("second"), "member-a")
    first = scheduler.pick_and_grant()
    assert first is not None
    assert scheduler.pick_and_grant() is None  # single permit still held
    _complete(scheduler, first)
    second = scheduler.pick_and_grant()
    assert second is not None and second.request_id != first.request_id
    _complete(scheduler, second)


def test_fencing_checkpoint_atomicity_and_different_database_rejection(tmp_path):
    scheduler = _scheduler(tmp_path)
    scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    assert grant
    with pytest.raises(PermissionError, match="不匹配"):
        scheduler.assert_grant(replace(grant, input_digest="sha256:forged"))
    with pytest.raises(PermissionError, match="不匹配"):
        scheduler.assert_grant(replace(grant, owner_type="material_asset"))
    with pytest.raises(PermissionError, match="不匹配"):
        scheduler.assert_grant(replace(grant, owner_id="forged-owner"))
    with pytest.raises(RecoveryBlocked):
        scheduler.assert_released(grant)
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
    scheduler.assert_released(grant)
    with pytest.raises(PermissionError):
        scheduler.assert_released(replace(grant, token="forged"))
    scheduler.submit(_intent("next"), "member-a")
    new = scheduler.pick_and_grant()
    assert new and new.generation > grant.generation
    with pytest.raises(PermissionError, match="失效"):
        scheduler.assert_grant(grant)
    _complete(scheduler, new)


@pytest.mark.parametrize("failure", ["owner_write", "ledger_finish"])
def test_defer_owner_transaction_and_post_commit_ledger_recovery(tmp_path, monkeypatch, failure):
    scheduler = _scheduler(tmp_path)
    scheduler.submit(_intent("defer-owner"), "member-a")
    grant = scheduler.pick_and_grant()
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE owner_receipt(value TEXT)")
    def write(conn, current):
        conn.execute("INSERT INTO owner_receipt VALUES ('interrupted')")
        if failure == "owner_write":
            raise OSError("receipt failed")
    if failure == "ledger_finish":
        def fail_finish(data):
            raise OSError("ledger finish failed")
        monkeypatch.setattr(scheduler, "_finish_host_release", fail_finish)
    with pytest.raises(OSError):
        scheduler.defer_unit(grant, reason="resource_pressure",
            retry_not_before_utc=scheduler._now() + timedelta(seconds=30),
            owner_database_path=scheduler.database_path, owner_write=write)
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_receipt").fetchone()[0] == (failure == "ledger_finish")
        assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == ("free" if failure == "ledger_finish" else "held")
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()[0] == ("blocked" if failure == "ledger_finish" else "granted")
    if failure == "ledger_finish":
        scheduler.record_owner_stop(grant, owner_database_path=scheduler.database_path,
            owner_write=lambda conn, current: pytest.fail("committed owner outcome must remain unchanged"))
    scheduler.close()
    replacement = _scheduler(tmp_path)
    assert replacement.reconcile_released() == grant.generation
    with sqlite3.connect(replacement.database_path) as conn:
        assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "free"
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()[0] == ("blocked" if failure == "ledger_finish" else "waiting")
        assert conn.execute("SELECT count(*) FROM owner_receipt").fetchone()[0] == (failure == "ledger_finish")
    replacement.close()


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


@pytest.mark.parametrize("old_state,preexisting_new", [("waiting", False), ("blocked", True)])
def test_unexecuted_probe_contract_replacement_preserves_age_and_audit(
    tmp_path, old_state, preexisting_new,
):
    class MaterialOwner(Owners):
        def verify_submission(self, intent, actor):
            if actor != "service":
                raise PermissionError("not discovery service")
            return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id,
                                intent.input_digest, intent.unit_key, None, "background",
                                {"kind": "material_probe_meta" if intent.unit_key.startswith("probe-meta:")
                                 else "material_probe_scene", "size_bytes": 512},
                                stage_input_digest=intent.unit_key.rsplit(":", 1)[-1])

        def check_current(self, request, conn):
            return "ready" if request.unit_key.startswith("probe-meta:") else "dependency"

    scheduler = _scheduler(tmp_path, owners=MaterialOwner())
    source_sha = "sha256:" + "a" * 64
    old_intent = SubmissionIntent("private-a", "material_asset", "asset", source_sha,
                                  material_stage_unit_key("probe-scene:v1", "b" * 64),
                                  SchedulePolicy())
    new_intent = SubmissionIntent("private-a", "material_asset", "asset", source_sha,
                                  material_stage_unit_key("probe-meta:v1", "c" * 64),
                                  SchedulePolicy())
    old = scheduler.submit(old_intent, "service")
    if old_state == "blocked":
        assert scheduler.pick_and_grant() is None
    existing = scheduler.submit(new_intent, "service") if preexisting_new else None
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET eligible_wait_seconds=77 WHERE id=?", (old.id,))
    replacement = scheduler.replace_unexecuted_request(
        old.id, new_intent, "service", "bounded_metadata_contract",
    )
    assert replacement.id == (existing.id if existing else replacement.id)
    assert replacement.first_ready_at_utc == old.first_ready_at_utc
    assert replacement.eligible_wait_seconds == 77
    assert scheduler.replace_unexecuted_request(old.id, new_intent, "service", "retry").id == replacement.id
    with sqlite3.connect(scheduler.database_path) as conn:
        old_state_row = conn.execute(
            "SELECT state, wait_reason FROM device_work_requests WHERE id=?", (old.id,),
        ).fetchone()
        event = conn.execute(
            "SELECT reason FROM device_request_events WHERE request_id=?", (old.id,),
        ).fetchone()
    assert old_state_row == ("cancelled", f"replaced_by:{replacement.id}")
    assert event and event[0] == f"superseded_by:{replacement.id}:bounded_metadata_contract"
    grant = scheduler.pick_and_grant()
    assert grant and grant.request_id == replacement.id
    _complete(scheduler, grant)


def test_probe_contract_replacement_rejects_any_prior_grant(tmp_path):
    class MaterialOwner(Owners):
        def verify_submission(self, intent, actor):
            return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id,
                                intent.input_digest, intent.unit_key, None, "background",
                                {"kind": "material_probe_meta", "size_bytes": 1},
                                stage_input_digest=intent.unit_key.rsplit(":", 1)[-1])

    scheduler = _scheduler(tmp_path, owners=MaterialOwner())
    old = scheduler.submit(
        SubmissionIntent("private-a", "material_asset", "asset", "sha256:" + "a" * 64,
                         material_stage_unit_key("probe-scene:v1", "b" * 64), SchedulePolicy()),
        "service",
    )
    grant = scheduler.pick_and_grant()
    assert grant is not None
    replacement = SubmissionIntent("private-a", "material_asset", "asset", "sha256:" + "a" * 64,
                                   material_stage_unit_key("probe-meta:v1", "c" * 64), SchedulePolicy())
    with pytest.raises(ValueError, match="尚未执行"):
        scheduler.replace_unexecuted_request(old.id, replacement, "service", "stage_upgrade")
    _complete(scheduler, grant)


def test_material_hash_provisional_revision_then_real_sha_and_manual_wake_is_idempotent(tmp_path):
    scheduler = _scheduler(tmp_path)
    hash_unit = SubmissionIntent("private-a", "material_asset", "asset-1",
                                 "provisional:stat-and-bound-root", material_stage_unit_key("hash:v1", "c" * 64), SchedulePolicy())
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
                                 f"sha256:{real_sha}", material_stage_unit_key("probe:v1", "b" * 64), SchedulePolicy())
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
            return "ready" if allowed else "unauthorized"

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


def test_v8_to_v10_preserves_requests_and_rebuilds_index(tmp_path):
    database = tmp_path / "old.sqlite"
    with sqlite3.connect(database) as conn:
        _material_ingestion_table(conn)
        conn.execute("CREATE TABLE production_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        for version, sql in _MIGRATIONS:
            if version == 9:
                break
            conn.executescript(sql)
            conn.execute("INSERT INTO production_schema_migrations(version) VALUES (?)", (version,))
        conn.execute(
            """INSERT INTO device_work_requests
            (id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key,
             source_refs_json, work_class, policy, window_json, first_ready_at_utc,
             eligible_since_utc, eligible_wait_seconds, state, resources_json, created_at, updated_at)
            VALUES ('old', 'private-a', 'member-a', 'job_run', 'job', 'sha:old', 'narration',
                    '[]', 'normal_make', 'asap', 'null', '2026-09-01T00:00:00+00:00',
                    '2026-09-02T00:00:00+00:00', 91, 'blocked', '{}',
                    '2026-09-01T00:00:00+00:00', '2026-09-02T00:00:00+00:00')"""
        )
        conn.commit()
        apply_production_migrations(conn)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM device_work_requests WHERE id='old'").fetchone()
        assert (row["member_id"], row["first_ready_at_utc"], row["eligible_wait_seconds"],
                row["attempt_number"], row["state"]) == (
                    "member-a", "2026-09-01T00:00:00+00:00", 91, 1, "blocked")
        assert conn.execute("SELECT max(version) FROM production_schema_migrations").fetchone()[0] == PRODUCTION_SCHEMA_VERSION
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_device_work_pending'").fetchone()


def test_v10_material_checkpoint_upgrade_is_idempotent_and_transactional(tmp_path):
    database = tmp_path / "production-v9.sqlite"
    with sqlite3.connect(database) as conn:
        _material_ingestion_table(conn)
        conn.execute("CREATE TABLE production_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        for version, sql in _MIGRATIONS:
            if version == 10:
                break
            conn.executescript(sql)
            conn.execute("INSERT INTO production_schema_migrations(version) VALUES (?)", (version,))
        conn.execute(
            """INSERT INTO material_ingestion_files
            (workspace_id, file_path, size_bytes, mtime_ns, observed_at, attempted_digest, state)
            VALUES ('private-a', '/library/a.mp4', 10, 123, 1, 'old-sha', 'waiting')"""
        )
        conn.commit()
        apply_production_migrations(conn)
        assert conn.execute("SELECT attempted_digest, provisional_revision FROM material_ingestion_files").fetchone() == (
            "old-sha", None)
        conn.execute("UPDATE material_ingestion_files SET provisional_revision='stat-v1' WHERE file_path='/library/a.mp4'")
        checkpoint = ("private-a", "asset-1", "old-sha", "stat-v1", "hash:v1:" + "a" * 64,
                      "a" * 64, None, "material_ingestion_files:old-sha", "2026-09-27T00:00:00+00:00")
        conn.execute("""INSERT INTO material_analysis_checkpoints
            (workspace_id, asset_id, source_sha256, provisional_revision, unit_key,
             stage_input_digest, result_json, result_ref, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", checkpoint)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""INSERT INTO material_analysis_checkpoints
                (workspace_id, asset_id, source_sha256, provisional_revision, unit_key,
                 stage_input_digest, result_json, result_ref, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", checkpoint)
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""INSERT INTO material_analysis_checkpoints
                (workspace_id, asset_id, source_sha256, provisional_revision, unit_key,
                 stage_input_digest, completed_at)
                VALUES ('private-a', 'asset-1', 'old-sha', 'stat-v1', 'empty-result',
                        'empty', '2026-09-27T00:00:00+00:00')""")
        conn.rollback()
        apply_production_migrations(conn)
        assert conn.execute("SELECT count(*) FROM material_analysis_checkpoints").fetchone()[0] == 1
        assert conn.execute("SELECT max(version) FROM production_schema_migrations").fetchone()[0] == PRODUCTION_SCHEMA_VERSION
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE material_ingestion_files SET attempted_digest='uncommitted' WHERE file_path='/library/a.mp4'")
        conn.execute("""INSERT INTO material_analysis_checkpoints
            (workspace_id, asset_id, source_sha256, provisional_revision, unit_key,
             stage_input_digest, result_ref, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("private-a", "asset-1", "uncommitted", "stat-v1",
             material_stage_unit_key("probe:v1", "b" * 64), "b" * 64,
             "temporary", "2026-09-27T00:01:00+00:00"))
        conn.rollback()
        assert conn.execute("SELECT attempted_digest FROM material_ingestion_files").fetchone()[0] == "old-sha"
        assert conn.execute("SELECT count(*) FROM material_analysis_checkpoints").fetchone()[0] == 1


def test_material_stage_digest_is_in_idempotency_key(tmp_path):
    scheduler = _scheduler(tmp_path)
    with pytest.raises(ValueError, match="阶段输入摘要"):
        scheduler.submit(SubmissionIntent("private-a", "material_asset", "asset", "provisional:stat",
                                          "hash:v1", SchedulePolicy()), "service")
    with pytest.raises(ValueError, match="阶段输入摘要"):
        scheduler.submit(SubmissionIntent("private-a", "material_asset", "asset", "sha256:one",
                                          "probe:v1", SchedulePolicy()), "service")
    first = SubmissionIntent("private-a", "material_asset", "asset", "sha256:one",
                             material_stage_unit_key("probe:v1", "a" * 64), SchedulePolicy())
    first_row = scheduler.submit(first, "service")
    _complete(scheduler, scheduler.pick_and_grant())
    assert scheduler.submit(first, "service").id == first_row.id
    assert scheduler._get(first_row.id).state == "done"
    changed = replace(first, unit_key=material_stage_unit_key("probe:v1", "b" * 64))
    assert scheduler.submit(changed, "service").id != first_row.id


def test_legacy_hash_request_is_blocked_on_execution_wake_retry_and_recovery(tmp_path):
    scheduler = _scheduler(tmp_path)
    normalized = _intent("asset", owner_type="material_asset")
    original = scheduler.submit(normalized, "service")
    grant = scheduler.pick_and_grant()
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET unit_key='hash:v1' WHERE id=?", (original.id,))
    legacy_grant = replace(grant, unit_key="hash:v1")
    with pytest.raises(PermissionError, match="旧素材"):
        scheduler.assert_grant(legacy_grant)
    legacy_intent = replace(normalized, unit_key="hash:v1")
    with pytest.raises(ValueError, match="旧素材"):
        scheduler.wake_existing(legacy_intent, "member-a")
    scheduler.close()
    replacement = _scheduler(tmp_path)
    assert replacement.pick_and_grant() is None
    blocked = replacement._get(original.id)
    assert (blocked.state, blocked.wait_reason) == ("blocked", "legacy_stage_digest_missing")
    with sqlite3.connect(replacement.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET state='cancelled' WHERE id=?", (original.id,))
    with pytest.raises(ValueError, match="旧素材"):
        replacement.retry_existing(original.id, "member-a", reason="cancelled")
    with sqlite3.connect(replacement.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET state='done' WHERE id=?", (original.id,))
    fresh = replacement.submit(normalized, "service")
    assert fresh.id != original.id and fresh.state == "waiting"
    changed_algorithm = replace(normalized, unit_key=material_stage_unit_key("hash:v1", "d" * 64))
    assert replacement.submit(changed_algorithm, "service").id not in {original.id, fresh.id}


def test_defer_releases_only_after_resources_and_rechecks_eligibility(tmp_path):
    clock = Clock(datetime(2026, 9, 27, 12, tzinfo=timezone.utc))
    owners, resources = Owners(), Resources()
    scheduler = _scheduler(tmp_path, clock=clock, owners=owners, resources=resources)
    request = scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    resources.release_value = None
    with pytest.raises(RecoveryBlocked):
        scheduler.defer_unit(grant, reason="authorization_lost")
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "held"
    resources.release_value = True
    owners.revoked = True
    scheduler.defer_unit(grant, reason="authorization_lost")
    assert scheduler._get(request.id).state == "blocked"
    assert scheduler.pick_and_grant() is None
    owners.revoked = False
    resumed = scheduler.pick_and_grant()
    assert resumed and resumed.generation > grant.generation
    with pytest.raises(PermissionError):
        scheduler.assert_grant(grant)
    _complete(scheduler, resumed)

    second = scheduler.submit(_intent("another"), "member-a")
    grant = scheduler.pick_and_grant()
    with pytest.raises(ValueError, match="未来"):
        scheduler.defer_unit(grant, reason="resource_pressure")
    scheduler.defer_unit(grant, reason="resource_pressure", retry_not_before_utc=clock.value + timedelta(minutes=2))
    assert scheduler.pick_and_grant() is None
    clock.advance(minutes=3)
    resumed = scheduler.pick_and_grant()
    assert resumed and resumed.request_id == second.id
    _complete(scheduler, resumed)


def test_failed_and_invalid_result_retry_requires_business_proof_and_keeps_audit(tmp_path):
    scheduler = _scheduler(tmp_path)
    request = scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("CREATE TABLE owner_failure (reason TEXT)")
        conn.execute("CREATE TABLE checkpoint_validity (request_id TEXT PRIMARY KEY, valid INTEGER NOT NULL)")
    def broken_write(conn, current):
        conn.execute("INSERT INTO owner_failure VALUES ('rolled back')")
        raise RuntimeError("owner write failed")

    with pytest.raises(RuntimeError):
        scheduler.fail_unit(grant, reason="model_failed", failure_write=broken_write)
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_failure").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "held"
    scheduler.fail_unit(grant, reason="model_failed",
                        failure_write=lambda conn, current: conn.execute("INSERT INTO owner_failure VALUES ('model_failed')"))
    assert scheduler._get(request.id).state == "failed"
    assert scheduler.submit(_intent("job"), "member-a").state == "failed"
    with pytest.raises(PermissionError):
        scheduler.retry_existing(request.id, "member-b", reason="failed")
    scheduler.retry_existing(request.id, "member-a", reason="failed")
    retry = scheduler._get(request.id)
    assert retry.attempt_number == 2 and retry.member_id == "member-a"
    assert retry.first_ready_at_utc == request.first_ready_at_utc
    with pytest.raises(PermissionError):
        scheduler.assert_grant(grant)
    second_grant = scheduler.pick_and_grant()
    assert second_grant.attempt_number == 2 and second_grant.generation > grant.generation
    _complete(scheduler, second_grant)
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("INSERT INTO checkpoint_validity VALUES (?, 1)", (request.id,))
    with pytest.raises(PermissionError, match="valid"):
        scheduler.retry_existing(request.id, "member-a", reason="result_invalid")
    with sqlite3.connect(scheduler.database_path) as conn:
        conn.execute("UPDATE checkpoint_validity SET valid=0 WHERE request_id=?", (request.id,))
    scheduler.retry_existing(request.id, "member-a", reason="result_invalid")
    assert scheduler._get(request.id).attempt_number == 3
    with sqlite3.connect(scheduler.database_path) as conn:
        events = conn.execute("SELECT event_type, reason, actor_id, prior_state FROM device_request_events WHERE request_id=? ORDER BY id", (request.id,)).fetchall()
    assert events == [("failed", "model_failed", None, "granted"),
                      ("retried", "failed", "member-a", "failed"),
                      ("retried", "result_invalid", "member-a", "done")]


@pytest.mark.parametrize("granted", [False, True])
def test_revoked_owner_can_cancel_with_only_actual_status_and_no_read_right(tmp_path, monkeypatch, granted):
    scheduler = _scheduler(tmp_path)
    original = scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant() if granted else None
    scheduler.owners.revoked = True
    monkeypatch.setattr(scheduler.owners, "can_view_detail", lambda *_: False)
    monkeypatch.setattr(scheduler.owners, "can_view_device_summary", lambda *_: False)
    try:
        with pytest.raises(PermissionError):
            scheduler.cancel(original.id, "member-b")
        result = scheduler.cancel(original.id, "member-a")
        assert result == {"id": original.id, "state": "yield_requested" if granted else "cancelled",
                          "wait_reason": "user_cancelled"}
        with pytest.raises(PermissionError):
            scheduler.view_for(original.id, "member-a")
        if grant is not None:
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                    checkpoint_write=lambda *_: None, cancelled=True)
            scheduler.assert_released(grant)
        with pytest.raises(PermissionError):
            scheduler.submit(_intent("other"), "member-a")
    finally:
        scheduler.close()


def test_cancelled_request_retries_only_by_explicit_authorized_call(tmp_path):
    scheduler = _scheduler(tmp_path)
    original = scheduler.submit(_intent("job"), "member-a")
    scheduler.cancel(original.id, "member-a")
    assert scheduler.submit(_intent("job"), "member-a").state == "cancelled"
    with pytest.raises(ValueError, match="终态"):
        scheduler.retry_existing(original.id, "member-a", reason="failed")
    scheduler.retry_existing(original.id, "member-a", reason="cancelled")
    reopened = scheduler._get(original.id)
    assert reopened.attempt_number == 2
    assert reopened.first_ready_at_utc == original.first_ready_at_utc
    assert reopened.member_id == original.member_id


def test_defer_keeps_permit_while_registered_child_is_alive(tmp_path):
    scheduler = _scheduler(tmp_path)
    scheduler.submit(_intent("job"), "member-a")
    grant = scheduler.pick_and_grant()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        scheduler.register_child(grant, ProcessIdentity(child.pid, "child-start", "test-boot"))
        with pytest.raises(RecoveryBlocked, match="子进程"):
            scheduler.defer_unit(grant, reason="worker_interrupted",
                                 retry_not_before_utc=datetime(2026, 9, 27, 12, 1, tzinfo=timezone.utc))
        with sqlite3.connect(scheduler.database_path) as conn:
            assert conn.execute("SELECT state FROM device_permit").fetchone()[0] == "held"
            assert conn.execute("SELECT count(*) FROM device_request_events").fetchone()[0] == 0
        child.terminate()
        child.wait(timeout=5)
        scheduler.defer_unit(grant, reason="worker_interrupted",
                             retry_not_before_utc=datetime(2026, 9, 27, 12, 1, tzinfo=timezone.utc))
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_scheduler_keeps_exact_host_refusal_and_checks_revocation_first(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.host_resource_probes import HostBudget, HostResourceProbe, HostResourceRefusal
    import app.host_resource_probes as probes
    from app.host_memory import HostMemoryMonitor, MemoryPolicy, MemorySample

    class MaterialOwners(Owners):
        def verify_submission(self, intent, actor):
            return replace(super().verify_submission(intent, actor),
                           resources={"kind": "material_hash", "size_bytes": 10})

    owners = MaterialOwners()
    available = [100]
    calls = []
    monkeypatch.setattr(probes.psutil, "disk_io_counters", lambda: SimpleNamespace(read_bytes=0, write_bytes=0))
    monkeypatch.setattr(probes.psutil, "cpu_percent", lambda interval: calls.append("cpu") or 12)
    monkeypatch.setattr(probes.psutil, "virtual_memory", lambda: SimpleNamespace(available=available[0]))
    monkeypatch.setattr(probes.psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(probes.psutil, "disk_usage", lambda path: SimpleNamespace(free=200))
    memory_clock = [0.]
    memory = HostMemoryMonitor(MemoryPolicy("darwin", 5., 2., "synthetic://scheduler-refusal"),
        clock=lambda: memory_clock[0], sampler=lambda: MemorySample("darwin", memory_clock[0],
            "2026-10-18T00:00:00+00:00", "normal", available[0], None, {}))
    for second in range(6):
        memory_clock[0] = float(second)
        memory.check(system_reserve_bytes=100)
    probe = HostResourceProbe(tmp_path / "business.sqlite", disk_paths=(tmp_path,),
                              budget=HostBudget(75, 100, 50, 200, 100, 1024), memory_monitor=memory)
    scheduler = _scheduler(tmp_path, owners=owners, resources=probe)
    scheduler.submit(_intent("bounded"), "member-a")
    grant = scheduler.pick_and_grant()
    assert grant is not None
    available[0] = 99
    with pytest.raises(HostResourceRefusal) as stopped:
        scheduler.assert_grant(grant)
    assert stopped.value.decision.verdict is False
    assert stopped.value.decision.checks["available_memory_bytes"] == {"actual": 99, "minimum": 100, "passed": False}
    assert len(calls) == 3  # Candidate, fresh final admission, then the precise refusing call.
    owners.revoked = True
    with pytest.raises(PermissionError):
        scheduler.assert_grant(grant)
    assert len(calls) == 3  # Resource diagnosis cannot bypass earlier continuous authorization.
    owners.revoked = False
    scheduler.fail_unit(grant, reason="fixture_host_refusal", failure_write=lambda conn, current: None)
    scheduler.assert_released(grant)
    assert scheduler._get(grant.request_id).state == "failed"
    with sqlite3.connect(scheduler.database_path) as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
