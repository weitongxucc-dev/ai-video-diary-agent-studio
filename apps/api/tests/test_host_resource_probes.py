import shutil
import sqlite3
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.granted_subprocess import (
    GrantedCommandCancelled,
    GrantedCommandOutputLimit,
    GrantedCommandTimeout,
    run_granted_command,
)
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe
from app.database_migrations import apply_production_migrations
from app.device_scheduler import DeviceScheduler, SchedulePolicy, SubmissionIntent, VerifiedWork


class RecordingScheduler:
    def __init__(self, *, reject_registration=False, cancel_on_registration=None):
        self.processes = HostProcessProbe()
        self.children = []
        self.reject_registration = reject_registration
        self.cancel_on_registration = cancel_on_registration

    def assert_grant(self, grant):
        assert grant == "isolated-grant"

    def register_child(self, grant, child):
        self.assert_grant(grant)
        if self.reject_registration:
            raise RuntimeError("ledger unavailable")
        self.children.append(child)
        if self.cancel_on_registration is not None:
            self.cancel_on_registration.set()


def run(scheduler, argv, **options):
    return run_granted_command(
        scheduler, "isolated-grant", argv,
        timeout_seconds=options.pop("timeout_seconds", 2),
        max_stdout_bytes=options.pop("max_stdout_bytes", 4096),
        max_stderr_bytes=options.pop("max_stderr_bytes", 4096),
        **options,
    )


def test_real_host_sample_admits_only_known_bounded_cpu_units(tmp_path):
    probe = HostResourceProbe(
        tmp_path / "production.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(100, 0, 2**63, 0, 2**63, 1024, 0.02),
    )
    assert probe.admit(SimpleNamespace(resources={"kind": "material_hash", "size_bytes": 10})) is True
    assert probe.admit(SimpleNamespace(resources={"kind": "material_probe_meta", "size_bytes": 10})) is True
    assert probe.admit(SimpleNamespace(resources={"kind": "creation_render", "size_bytes": 10})) is None
    assert probe.admit(SimpleNamespace(resources={"kind": "material_hash", "size_bytes": 1025})) is False


def test_registration_ack_precedes_real_exec(tmp_path):
    marker = tmp_path / "must-not-exist"
    scheduler = RecordingScheduler(reject_registration=True)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        run(scheduler, [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])
    assert not marker.exists()


def test_cancel_before_or_during_registration_never_execs_marker(tmp_path):
    marker = tmp_path / "must-not-exist"
    command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    cancelled = threading.Event()
    cancelled.set()
    scheduler = RecordingScheduler()
    with pytest.raises(GrantedCommandCancelled):
        run(scheduler, command, cancel_event=cancelled)
    assert not scheduler.children and not marker.exists()

    cancelled.clear()
    scheduler = RecordingScheduler(cancel_on_registration=cancelled)
    with pytest.raises(GrantedCommandCancelled):
        run(scheduler, command, cancel_event=cancelled)
    assert scheduler.children and not marker.exists()
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_cancel_after_output_eof_still_stops_live_process():
    scheduler = RecordingScheduler()
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(GrantedCommandCancelled):
            run(scheduler, [sys.executable, "-c",
                            "import os,time; os.close(1); os.close(2); time.sleep(5)"],
                timeout_seconds=1.5, cancel_event=cancelled)
    finally:
        timer.cancel()
    assert time.monotonic() - start < 1
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_real_command_timeout_cancel_output_limit_and_release():
    scheduler = RecordingScheduler()
    with pytest.raises(GrantedCommandTimeout):
        run(scheduler, [sys.executable, "-c", "import time; time.sleep(5)"], timeout_seconds=0.25)
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(GrantedCommandCancelled):
        run(scheduler, [sys.executable, "-c", "import time; time.sleep(5)"], cancel_event=cancelled)
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True

    with pytest.raises(GrantedCommandOutputLimit):
        run(scheduler, [sys.executable, "-c", "print('x' * 2000)"], max_stdout_bytes=32)
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_timeout_stops_real_grandchild_in_same_process_group():
    scheduler = RecordingScheduler()
    spawn = ("import subprocess,sys,time; "
             "subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']); "
             "time.sleep(5)")
    with pytest.raises(GrantedCommandTimeout):
        run(scheduler, [sys.executable, "-c", spawn], timeout_seconds=0.25)
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_real_ffprobe_fixture_is_bounded_and_child_released(tmp_path):
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")
    source = Path(tmp_path / "short.wav")
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 800)
    scheduler = RecordingScheduler()
    result = run(scheduler, [ffprobe, "-v", "error", "-show_entries", "format=duration",
                             "-of", "json", str(source)])
    assert result.returncode == 0
    assert b'"duration"' in result.stdout
    assert scheduler.children
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_real_scheduler_registers_child_before_exec_and_commits_after_release(tmp_path):
    class Owner:
        def verify_submission(self, intent, actor):
            assert actor == "service"
            return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id,
                                intent.input_digest, intent.unit_key, None, "background",
                                {"kind": "material_hash", "size_bytes": 1},
                                stage_input_digest="a" * 64)

        def check_current(self, request, conn):
            return "ready"

    database = tmp_path / "production.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE material_ingestion_files (
            workspace_id TEXT NOT NULL, file_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            ctime_ns INTEGER NOT NULL DEFAULT 0, inode INTEGER NOT NULL DEFAULT 0,
            observed_at REAL NOT NULL, completed_digest TEXT, attempted_digest TEXT,
            state TEXT NOT NULL, PRIMARY KEY (workspace_id, file_path))""")
        apply_production_migrations(conn)
    resources = HostResourceProbe(
        database, disk_paths=(tmp_path,),
        budget=HostBudget(100, 0, 2**63, 0, 2**63, 1024, 0.02),
    )
    scheduler = DeviceScheduler(database, tmp_path / "device.lock", owners=Owner(),
                                resources=resources, processes=HostProcessProbe())
    try:
        scheduler.submit(SubmissionIntent("workspace", "material_asset", "asset", "a" * 64,
                                          "hash:v1:" + "a" * 64, SchedulePolicy()), "service")
        grant = scheduler.pick_and_grant()
        assert grant is not None
        result = run_granted_command(
            scheduler, grant, [sys.executable, "-c", "print('registered')"],
            timeout_seconds=3, max_stdout_bytes=100, max_stderr_bytes=100,
        )
        assert result.stdout == b"registered\n"
        with sqlite3.connect(database) as conn:
            children = conn.execute("SELECT children_json FROM device_permit WHERE id=1").fetchone()[0]
        assert '"pid"' in children
        scheduler.complete_unit(grant, checkpoint_database_path=database,
                                checkpoint_write=lambda conn, current: "fixture-checkpoint")
        scheduler.assert_released(grant)
    finally:
        scheduler.close()
