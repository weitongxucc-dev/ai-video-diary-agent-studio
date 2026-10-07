import shutil
import sqlite3
import sys
import threading
import time
import wave
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import psutil

from app.granted_subprocess import (
    GrantedCommandCancelled,
    GrantedCommandOutputLimit,
    GrantedCommandResourceLimit,
    GrantedCommandResourceUnavailable,
    GrantedCommandTimeout,
    run_granted_command,
)
from app.host_resource_probes import (
    CREATION_RENDER_CPU_THREADS_MAX,
    CREATION_RENDER_SOURCE_DURATION_S_MAX,
    CpuRenderBudget,
    HostBudget,
    HostCapacity,
    HostProcessProbe,
    HostResourceProbe,
    HostResourceRefusal,
    HostResourceDecision,
    NarrationBudget,
    ResourceAdmissionRejected,
    ResourceSamplingUnavailable,
    SceneWindowBudget,
    admit_resource,
    resource_kinds,
    sample_host_capacity,
)
from app.database_migrations import apply_production_migrations
from app.device_scheduler import DeviceScheduler, Grant, SchedulePolicy, SubmissionIntent, VerifiedWork


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


def test_creation_permission_poll_is_independent_of_rss_sampling(tmp_path, monkeypatch):
    from app.host_resource_probes import SceneRuntimeLimits
    import app.granted_subprocess as commands

    grant = Grant("request", "workspace", "creation_job", "job", "narration:digest",
                  "a" * 64, 1, 1, "token", tmp_path / "isolated.sqlite")
    permission_samples, rss_samples = [], []
    class TimedScheduler(RecordingScheduler):
        def assert_grant(self, current):
            assert current == grant
            permission_samples.append(time.monotonic())

    scheduler = TimedScheduler()
    scheduler.resources = HostResourceProbe(tmp_path / "isolated.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 1, 2**63, 1024))
    monkeypatch.setattr(scheduler.resources, "scene_runtime_limits", lambda _grant:
        SceneRuntimeLimits(1024 ** 3, 16, 1, 2**63, 0.05))
    sample = commands._check_scene_runtime
    def sampled(*args):
        rss_samples.append(time.monotonic())
        sample(*args)
    monkeypatch.setattr(commands, "_check_scene_runtime", sampled)
    result = run_granted_command(scheduler, grant, [sys.executable, "-c", "import time; time.sleep(1.3)"],
        timeout_seconds=3, max_stdout_bytes=100, max_stderr_bytes=100)
    assert result.returncode == 0
    assert len(rss_samples) >= 5
    assert len(permission_samples) == 5  # prelaunch/register/exec, then immediate + one 1s poll
    assert permission_samples[-1] - permission_samples[-2] >= 1
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_real_host_sample_admits_only_known_bounded_cpu_units(tmp_path):
    probe = HostResourceProbe(
        tmp_path / "production.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(100, 0, 2**63, 0, 2**63, 1024, 0.02),
    )
    assert probe.admit(SimpleNamespace(resources={"kind": "material_hash", "size_bytes": 10})) is True
    assert probe.admit(SimpleNamespace(resources={"kind": "material_probe_meta", "size_bytes": 10})) is True
    assert probe.admit(SimpleNamespace(resources={"kind": "creation_render", "size_bytes": 10})) is None
    assert probe.admit(SimpleNamespace(resources={"kind": "material_hash", "size_bytes": 1025})) is False


def _render_resources():
    return {"kind": "creation_render", "execution_mode": "cpu_ffmpeg",
            "duration_seconds": 0.2, "width_px": 160, "height_px": 120,
            "input_bytes": 1600, "clip_count": 1, "overlay_count": 0,
            "max_output_bytes": 1_000_000, "threads": 1, "timeout_seconds": 3.0}


def _render_budget():
    return CpuRenderBudget(
        max_duration_seconds=1, max_pixel_seconds=100_000,
        max_width_px=320, max_height_px=240, max_input_bytes=10_000,
        max_clip_count=2, max_overlay_count=4,
        max_output_bytes=1_000_000, max_threads=1, max_timeout_seconds=5,
        additional_memory_bytes=1, additional_disk_bytes=1,
    )


def test_cpu_render_requires_explicit_budget_and_complete_bounded_contract(tmp_path):
    base = dict(max_cpu_percent=100, min_available_memory_bytes=0,
                max_swap_used_bytes=2**63, min_free_disk_bytes=0,
                max_disk_io_bytes_per_second=2**63, max_unit_input_bytes=1024,
                sample_seconds=0.02)
    unknown = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                budget=HostBudget(**base))
    resources = _render_resources()
    assert unknown.admit(SimpleNamespace(resources=resources)) is None
    measured = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                 budget=HostBudget(**base, cpu_render=_render_budget()))
    assert measured.admit(SimpleNamespace(resources=resources)) is True
    assert measured.admit(SimpleNamespace(resources={**resources, "execution_mode": "gpu"})) is None
    assert measured.admit(SimpleNamespace(resources={k: v for k, v in resources.items()
                                                     if k != "overlay_count"})) is None
    assert measured.admit(SimpleNamespace(resources={**resources, "duration_seconds": 2})) is False
    assert measured.admit(SimpleNamespace(resources={**resources, "overlay_count": 5})) is False


def test_cpu_render_simulated_memory_pressure_blocks(tmp_path, monkeypatch):
    budget = HostBudget(100, 1, 2**63, 0, 2**63, 1024, 0.02,
                        cpu_render=_render_budget())
    probe = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,), budget=budget)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=0))
    assert probe.admit(SimpleNamespace(resources=_render_resources())) is False


def _scene_resources():
    return {"kind": "material_scene_window", "source_codec": "mpeg4",
            "source_width_px": 160, "source_height_px": 90,
            "source_frame_rate_fps": 5.0, "size_bytes": 1_500_000,
            "scan_seconds": 15.0, "output_sample_fps": 2,
            "output_max_width_px": 320, "output_max_frames": 30,
            "ffmpeg_threads": 1, "timeout_seconds": 12.0}


def _scene_budget():
    return SceneWindowBudget(
        allowed_source_codecs=("mpeg4",), max_source_bytes=3_000_000,
        max_source_width_px=160, max_source_height_px=90,
        max_source_frame_rate_fps=120,
        max_scan_seconds=15, max_output_sample_fps=2,
        max_output_width_px=320, max_output_frames=30,
        max_threads=1, max_timeout_seconds=12,
        max_process_rss_bytes=64 * 1024 * 1024, max_process_threads=8,
        runtime_sample_seconds=0.05,
        additional_memory_bytes=128 * 1024 * 1024, additional_disk_bytes=1,
    )


def test_scene_window_requires_calibrated_input_decode_and_hard_timeout(tmp_path):
    base = dict(max_cpu_percent=100, min_available_memory_bytes=1,
                max_swap_used_bytes=2**63, min_free_disk_bytes=1,
                max_disk_io_bytes_per_second=2**63, max_unit_input_bytes=1024,
                sample_seconds=0.02)
    resources = _scene_resources()
    unknown = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                budget=HostBudget(**base))
    assert unknown.admit(SimpleNamespace(resources=resources)) is None
    measured = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                 budget=HostBudget(**base, scene_window=_scene_budget()))
    assert measured.admit(SimpleNamespace(resources=resources)) is True
    assert measured.admit(SimpleNamespace(resources={**resources, "source_codec": "av1"})) is None
    assert measured.admit(SimpleNamespace(resources={**resources, "source_frame_rate_fps": None})) is None
    assert measured.admit(SimpleNamespace(resources={**resources, "source_width_px": 3840})) is False
    assert measured.admit(SimpleNamespace(resources={**resources, "source_frame_rate_fps": 240})) is False
    assert measured.admit(SimpleNamespace(resources={**resources, "scan_seconds": 16})) is False
    assert measured.admit(SimpleNamespace(resources={**resources, "output_max_frames": 20})) is None
    assert measured.admit(SimpleNamespace(resources={**resources, "timeout_seconds": 20})) is False


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


def test_isolated_cpu_ffmpeg_render_uses_measured_admission_and_real_grant(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is not installed")
    audio = tmp_path / "input.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 800)
    resources = {**_render_resources(), "input_bytes": audio.stat().st_size}

    class Owner:
        def verify_submission(self, intent, actor):
            assert actor == "member"
            return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id,
                                intent.input_digest, intent.unit_key, "member", "normal_make",
                                resources)

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
    probe = HostResourceProbe(
        database, disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 1, 2**63, 1024, 0.02,
                          cpu_render=_render_budget()),
    )
    scheduler = DeviceScheduler(database, tmp_path / "device.lock", owners=Owner(),
                                resources=probe, processes=HostProcessProbe())
    video = tmp_path / "output.mp4"
    try:
        scheduler.submit(SubmissionIntent("workspace", "creation_job", "job", "digest",
                                          "render", SchedulePolicy()), "member")
        grant = scheduler.pick_and_grant()
        assert grant is not None
        result = run_granted_command(
            scheduler, grant,
            [ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "color=s=160x120:r=10",
             "-i", str(audio), "-t", "0.2", "-c:v", "libx264", "-threads", "1",
             "-filter_threads", "1", "-c:a", "aac", "-fs", "1000000", "-y", str(video)],
            timeout_seconds=3, max_stdout_bytes=1024, max_stderr_bytes=4096,
        )
        assert result.returncode == 0 and 0 < video.stat().st_size <= 1_000_000
        scheduler.complete_unit(grant, checkpoint_database_path=database,
                                checkpoint_write=lambda conn, current: video.name)
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


@pytest.mark.parametrize("unavailable", [False, True])
def test_scene_process_monitor_stops_real_child_after_output_eof(tmp_path, monkeypatch, unavailable):
    resources = _scene_resources()

    class Owner:
        def verify_submission(self, intent, actor):
            return VerifiedWork(intent.workspace_id, intent.owner_type, intent.owner_id,
                                intent.input_digest, intent.unit_key, None, "background",
                                resources, stage_input_digest="b" * 64)

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
    scene_budget = replace(_scene_budget(), max_process_rss_bytes=24 * 1024 * 1024,
                           additional_memory_bytes=24 * 1024 * 1024)
    probe = HostResourceProbe(
        database, disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 1, 2**63, 1024, 0.02,
                          scene_window=scene_budget),
    )
    scheduler = DeviceScheduler(database, tmp_path / "device.lock", owners=Owner(),
                                resources=probe, processes=HostProcessProbe())
    try:
        scheduler.submit(SubmissionIntent("workspace", "material_asset", "asset", "a" * 64,
                                          "scene-window:v1:" + "b" * 64,
                                          SchedulePolicy()), "service")
        grant = scheduler.pick_and_grant()
        assert grant is not None
        if unavailable:
            def missing_memory(process):
                raise psutil.AccessDenied(pid=process.pid)
            monkeypatch.setattr(psutil.Process, "memory_info", missing_memory)
        start = time.monotonic()
        expected = GrantedCommandResourceUnavailable if unavailable else GrantedCommandResourceLimit
        with pytest.raises(expected):
            run_granted_command(
                scheduler, grant,
                [sys.executable, "-c", "import os,time; os.close(1); os.close(2); "
                 "held=bytearray(80_000_000); time.sleep(5)"],
                timeout_seconds=3, max_stdout_bytes=100, max_stderr_bytes=100,
            )
        assert time.monotonic() - start < 2
        if unavailable:
            scheduler.defer_unit(grant, reason="resource_pressure",
                                 retry_not_before_utc=datetime.now(timezone.utc) + timedelta(minutes=1))
        else:
            scheduler.fail_unit(grant, reason="scene_resource_limit",
                                failure_write=lambda conn, current: None)
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


def _capacity(*, ceiling=8, cpu_count=16, memory=2**33, load=0.5):
    return HostCapacity(cpu_count=cpu_count, cpu_threads_ceiling=ceiling,
                        available_memory_bytes=memory, load_avg_1m=load)


def _render_declared():
    return {"source_duration_s": 12.5, "input_count": 3, "cpu_threads": 4, "wall_clock_s": 120.0}


def test_registry_declares_expected_kinds_and_fields():
    kinds = resource_kinds()
    assert {"creation_render", "mat_scene_probe"} <= set(kinds)
    render = {field.name: field for field in kinds["creation_render"].fields}
    assert set(render) == {"source_duration_s", "input_count", "cpu_threads", "wall_clock_s"}
    assert render["cpu_threads"].value_type is int
    assert render["input_count"].value_type is int
    assert render["source_duration_s"].value_type is float
    assert render["wall_clock_s"].value_type is float
    scene = {field.name: field for field in kinds["mat_scene_probe"].fields}
    # Cross-package contract: MAT's SceneProbeBudget(cpu_threads, max_source_duration_s, sampling_fps).
    assert {"cpu_threads", "max_source_duration_s", "sampling_fps"} <= set(scene)
    assert scene["cpu_threads"].value_type is int
    assert scene["max_source_duration_s"].value_type is float
    assert scene["sampling_fps"].value_type is float


def test_admit_resource_legal_returns_deterministic_budget():
    capacity = _capacity()
    first = admit_resource("creation_render", _render_declared(), capacity=capacity)
    second = admit_resource("creation_render", _render_declared(), capacity=capacity)
    assert first == second
    assert first.granted == _render_declared()
    assert first.kind == "creation_render"
    assert first.cpu_threads_ceiling == capacity.cpu_threads_ceiling
    assert first.available_memory_bytes == capacity.available_memory_bytes


def test_admit_resource_mat_scene_probe_legal():
    request = {"cpu_threads": 4, "wall_clock_s": 300.0,
               "max_source_duration_s": 600.0, "sampling_fps": 2.0}
    admission = admit_resource("mat_scene_probe", request, capacity=_capacity())
    assert admission.granted == request


def test_admit_resource_rejects_undeclared_field():
    with pytest.raises(ResourceAdmissionRejected, match="undeclared field"):
        admit_resource("creation_render", {**_render_declared(), "bogus_field": 1},
                       capacity=_capacity())
    with pytest.raises(ResourceAdmissionRejected, match="unknown resource kind"):
        admit_resource("no_such_kind", _render_declared(), capacity=_capacity())
    with pytest.raises(ResourceAdmissionRejected, match="missing required field"):
        admit_resource("creation_render", {"source_duration_s": 1.0}, capacity=_capacity())


def test_admit_resource_rejects_over_limit():
    with pytest.raises(ResourceAdmissionRejected, match="source_duration_s"):
        admit_resource("creation_render",
                       {**_render_declared(),
                        "source_duration_s": CREATION_RENDER_SOURCE_DURATION_S_MAX + 1},
                       capacity=_capacity())
    with pytest.raises(ResourceAdmissionRejected, match="cpu_threads"):
        admit_resource("creation_render",
                       {**_render_declared(), "cpu_threads": CREATION_RENDER_CPU_THREADS_MAX + 1},
                       capacity=_capacity())
    # A sampled ceiling below the static cap still rejects conservatively.
    with pytest.raises(ResourceAdmissionRejected, match="sampled host ceiling"):
        admit_resource("creation_render", _render_declared(), capacity=_capacity(ceiling=2))


def test_admit_resource_rejects_type_mismatch():
    with pytest.raises(ResourceAdmissionRejected, match="integer"):
        admit_resource("creation_render", {**_render_declared(), "cpu_threads": 4.0},
                       capacity=_capacity())
    with pytest.raises(ResourceAdmissionRejected, match="finite number"):
        admit_resource("creation_render", {**_render_declared(), "wall_clock_s": "120"},
                       capacity=_capacity())
    with pytest.raises(ResourceAdmissionRejected, match="finite number"):
        admit_resource("creation_render", {**_render_declared(), "wall_clock_s": float("nan")},
                       capacity=_capacity())


def test_admit_resource_rejects_when_host_load_saturated():
    request = {"cpu_threads": 2, "wall_clock_s": 60.0,
               "max_source_duration_s": 60.0, "sampling_fps": 2.0}
    with pytest.raises(ResourceAdmissionRejected, match="saturates"):
        admit_resource("mat_scene_probe", request, capacity=_capacity(load=16.0, cpu_count=16))


def test_sample_host_capacity_real_derives_conservative_ceiling():
    capacity = sample_host_capacity()
    assert capacity.cpu_count >= 1
    assert 1 <= capacity.cpu_threads_ceiling <= capacity.cpu_count
    assert capacity.available_memory_bytes >= 0


def test_sample_host_capacity_unavailable_fails_explicitly(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: None)
    with pytest.raises(ResourceSamplingUnavailable):
        sample_host_capacity()


@pytest.mark.parametrize("metric,actual,threshold", [
    ("cpu_percent", 76, 75),
    ("available_memory_bytes", 99, 100),
    ("swap_used_bytes", 51, 50),
    ("disk_free_bytes", 199, 200),
    ("disk_io_bytes_per_second", 101, 100),
])
def test_runtime_refusal_contains_the_same_samples_and_unchanged_thresholds(
    tmp_path, monkeypatch, metric, actual, threshold,
):
    import app.host_resource_probes as probes
    values = {"cpu_percent": 75, "available_memory_bytes": 100, "swap_used_bytes": 50,
              "disk_free_bytes": 200, "disk_io_bytes_per_second": 100}
    values[metric] = actual
    calls = []
    counters = iter([SimpleNamespace(read_bytes=0, write_bytes=0),
                     SimpleNamespace(read_bytes=values["disk_io_bytes_per_second"], write_bytes=0)])
    def io():
        calls.append("io")
        return next(counters)
    ticks = iter([10.0, 11.0])
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", io)
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: values["cpu_percent"])
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=values["available_memory_bytes"]))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=values["swap_used_bytes"]))
    monkeypatch.setattr(psutil, "disk_usage", lambda path: SimpleNamespace(free=values["disk_free_bytes"]))
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,), budget=HostBudget(75, 100, 50, 200, 100, 1024))
    monkeypatch.setattr(probe, "_grant_resources", lambda grant: {"kind": "material_hash", "size_bytes": 10})
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.continue_unit("fixture-grant")
    decision = stopped.value.decision
    key = f"disk_free_bytes:{tmp_path}" if metric == "disk_free_bytes" else metric
    failed = decision.checks[key]
    assert decision.verdict is False and failed["passed"] is False
    assert failed["actual"] == actual
    assert failed["minimum" if metric in {"available_memory_bytes", "disk_free_bytes"} else "maximum"] == threshold
    assert all(check["passed"] for name, check in decision.checks.items() if name != key)
    assert decision.observed_interval_seconds == 1
    assert calls == ["io", "io"]  # No second observation to explain the first refusal.
    label = {"cpu_percent": "CPU占用", "available_memory_bytes": "可用内存", "swap_used_bytes": "交换空间已用量",
             "disk_free_bytes": "目标磁盘可用空间", "disk_io_bytes_per_second": "磁盘读写速率"}[metric]
    assert label in str(stopped.value) and str(actual) in str(stopped.value)


@pytest.mark.parametrize("failure", ["before_missing", "after_missing", "regressed", "memory_unreadable"])
def test_unknown_runtime_sampling_records_exact_cause_without_ready_fallback(tmp_path, monkeypatch, failure):
    import app.host_resource_probes as probes
    counters = iter([None if failure == "before_missing" else SimpleNamespace(read_bytes=10, write_bytes=0),
                     None if failure == "after_missing" else SimpleNamespace(read_bytes=0 if failure == "regressed" else 10, write_bytes=0)])
    ticks = iter([10.0, 11.0])
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", lambda: next(counters))
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 12)
    def unreadable():
        raise OSError("fixture host memory read refused")
    monkeypatch.setattr(psutil, "virtual_memory", unreadable)
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,), budget=HostBudget(75, 100, 50, 200, 100, 1024))
    monkeypatch.setattr(probe, "_grant_resources", lambda grant: {"kind": "material_hash", "size_bytes": 10})
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.continue_unit("fixture-grant")
    decision = stopped.value.decision
    assert decision.verdict is None
    expected = {"before_missing": "disk_io_before_unavailable", "after_missing": "disk_io_after_unavailable",
                "regressed": "disk_io_counters_regressed", "memory_unreadable": "available_memory_bytes: OSError: fixture host memory read refused"}
    assert decision.reason == expected[failure]
    expected_text = {"before_missing": "采样前的磁盘读写计数不可读取", "after_missing": "采样后的磁盘读写计数不可读取",
                     "regressed": "磁盘读写计数回退", "memory_unreadable": "可用内存不可读取"}
    assert expected_text[failure] in str(stopped.value)
    if failure != "before_missing":
        assert decision.checks["cpu_percent"]["actual"] == 12
    if failure == "memory_unreadable":
        assert decision.checks["disk_io_bytes_per_second"]["actual"] == 0
        assert decision.checks["available_memory_bytes"] == {"minimum": 100}  # Never invent a value.


def test_runtime_declared_budget_missing_and_grant_fact_unreadable_are_explicit(tmp_path, monkeypatch):
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,), budget=HostBudget(75, 100, 50, 200, 100, 1024))
    resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts"}
    monkeypatch.setattr(probe, "_grant_resources", lambda grant: resources)
    with pytest.raises(HostResourceRefusal) as missing:
        probe.continue_unit("fixture-grant")
    assert missing.value.decision.verdict is None
    assert missing.value.decision.reason == "stage_budget_unavailable"
    assert missing.value.decision.checks == {}
    monkeypatch.setattr(probe, "_grant_resources", lambda grant: None)
    with pytest.raises(HostResourceRefusal) as unreadable:
        probe.continue_unit("fixture-grant")
    assert unreadable.value.decision.reason == "granted_resources_unreadable"


def test_exact_host_boundaries_and_narration_caps_preserve_admission(tmp_path, monkeypatch):
    import app.host_resource_probes as probes
    narration = NarrationBudget(80, 1, 1048576, 15, 2097152, 1, 120,
                                2147483648, 128, 0.1, 2147483648, 8388608)
    minimum_memory = 1610612736 + narration.additional_memory_bytes
    minimum_disk = 1073741824 + narration.additional_disk_bytes + narration.max_output_bytes
    reads = []
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: reads.append("cpu") or 75)
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **_kwargs: {"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)})
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=minimum_memory))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=50))
    monkeypatch.setattr(psutil, "disk_usage", lambda path: SimpleNamespace(free=minimum_disk))
    ticks = iter([10.0, 13.0])
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
                              budget=HostBudget(75, 1610612736, 50, 1073741824, 0, 2097152, narration=narration))
    resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
                 "text_chars": 80, "section_count": 1, "reference_bytes": 1048576,
                 "duration_seconds": 15, "max_output_bytes": 2097152, "threads": 1, "timeout_seconds": 120}
    assert probe.admit(SimpleNamespace(resources=resources)) is True
    assert probe.admit(SimpleNamespace(resources={**resources, "text_chars": 81})) is False
    assert probe.admit(SimpleNamespace(resources={**resources, "reference_bytes": 1048577})) is False
    assert probe.admit(SimpleNamespace(resources={**resources, "execution_mode": "in_process"})) is None
    assert probe.admit(SimpleNamespace(resources={**resources, "timeout_seconds": None})) is None
    assert reads == ["cpu"]  # Declared rejection never becomes a ready host resample.


def test_resource_error_text_exposes_only_failed_checks_and_keeps_full_decision(tmp_path):
    decision = HostResourceDecision(False, {"kind": "material_hash", "size_bytes": 10}, {
        "cpu_percent": {"actual": 76, "maximum": 75, "passed": False},
        "swap_used_bytes": {"actual": 51, "maximum": 50, "passed": False},
        "available_memory_bytes": {"actual": 1000, "minimum": 100, "passed": True},
        f"disk_free_bytes:{tmp_path}": {"actual": 999, "minimum": 200, "passed": True},
    }, observed_interval_seconds=0.1)
    error = HostResourceRefusal(decision)
    assert "CPU占用为76%，上限75%" in str(error)
    assert "交换空间已用量为51字节，上限50字节" in str(error)
    assert "1000" not in str(error) and str(tmp_path) not in str(error)
    assert "material_hash" not in str(error) and "{" not in str(error)
    assert error.decision is decision  # Full evidence survives without another observation.
    unknown = HostResourceRefusal(HostResourceDecision(None, {}, {}, "stage_budget_unavailable"))
    assert "专属设备预算尚未标定" in str(unknown)
    disk = HostResourceRefusal(HostResourceDecision(None, {}, {}, f"disk_free_bytes:{tmp_path}: OSError: denied"))
    assert "目标磁盘可用空间不可读取" in str(disk) and str(tmp_path) not in str(disk)


def test_refusal_keeps_bounded_phase_output_after_real_group_release(tmp_path, monkeypatch):
    from app.host_resource_probes import SceneRuntimeLimits
    grant = Grant("request", "workspace", "creation_job", "job", "narration:digest",
                  "a" * 64, 1, 1, "token", tmp_path / "isolated.sqlite")
    decision = HostResourceDecision(False, {}, {
        "disk_io_bytes_per_second": {"actual": 200, "maximum": 100, "passed": False},
    })
    class RefusingScheduler(RecordingScheduler):
        def assert_grant(self, grant):
            if self.children and time.monotonic() - self.began > 0.2:
                raise HostResourceRefusal(decision)

    scheduler = RefusingScheduler()
    scheduler.resources = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 1, 2**63, 1024))
    monkeypatch.setattr(scheduler.resources, "scene_runtime_limits", lambda current:
        SceneRuntimeLimits(1024**3, 16, 1, 2**63, 0.1))
    scheduler.began = time.monotonic()
    with pytest.raises(HostResourceRefusal) as stopped:
        run_granted_command(scheduler, grant, [sys.executable, "-c",
            "import sys,time; print('QWEN_PHASE importing_runtime',file=sys.stderr,flush=True); time.sleep(5)"],
            timeout_seconds=2, max_stdout_bytes=64, max_stderr_bytes=64)
    assert stopped.value.decision is decision
    diagnostic = stopped.value.command_diagnostics
    assert diagnostic["exec_ack"] and diagnostic["group_released"]
    assert b"QWEN_PHASE importing_runtime" in diagnostic["stderr"]
    assert len(diagnostic["stderr"]) <= 64
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


@pytest.mark.parametrize("scenario", ["short_peak", "sustained", "unknown", "counter_regressed", "observation_gap"])
def test_narration_io_uses_real_endpoint_window_and_finite_burst(tmp_path, monkeypatch, scenario):
    import app.host_resource_probes as probes
    narration = NarrationBudget(80, 1, 1048576, 15, 2097152, 1, 120,
                                100, 128, 0.1, 100, 1)
    budget = HostBudget(75, 100, 50, 200, 100, 2097152, narration=narration)
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,), budget=budget)
    resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
                 "text_chars": 16, "section_count": 1, "reference_bytes": 100,
                 "duration_seconds": 15, "max_output_bytes": 2097152, "threads": 1, "timeout_seconds": 120}
    ticks = iter([0, 0.1, 10, 10.1] if scenario == "observation_gap" else
                 [0, 0.1, 1, 1.1, 2, 2.1, 3, 3.1, 4, 4.1])
    totals = [0, 150, 150, 150, 150, 150, 150, 150, 150, 150] if scenario == "short_peak" else [0, 10, 140, 150, 280, 290, 420, 430, 560, 570]
    if scenario == "counter_regressed":
        totals[2] = 1  # Regression across the polling gap must not disappear.
    counters = iter(totals)
    def disks(**options):
        assert options == {"perdisk": True}
        total = next(counters)
        if scenario == "unknown":
            return None
        return {"disk6": SimpleNamespace(read_bytes=total, write_bytes=0,
                 read_count=total, write_count=0, read_time=total * 2, write_time=0)}
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", disks)
    monkeypatch.setattr(psutil, "cpu_percent", lambda **kw: 10)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=1000))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(psutil, "disk_usage", lambda path: SimpleNamespace(free=10**9))
    monkeypatch.setattr(psutil, "disk_partitions", lambda **kw: [])
    first = probe._sample(resources, running_io=True)
    if scenario == "unknown":
        assert first.verdict is None and first.reason == "disk_io_before_unavailable"
        assert not HostResourceRefusal(first).recoverable_pressure
        return
    assert first.verdict is True  # Known measured bytes below the finite 300B burst budget.
    second = probe._sample(resources, running_io=True)
    if scenario == "observation_gap":
        assert second.verdict is None and second.reason == "io_observation_gap"
        return
    if scenario == "counter_regressed":
        assert second.verdict is None and second.reason == "disk_io_counters_regressed"
        return
    third = probe._sample(resources, running_io=True)
    final = probe._sample(resources, running_io=True)
    io = final.checks["disk_io_bytes_per_second"]
    assert io["window_complete"] and io["window_seconds"] >= 3
    assert io["delta_bytes"] == (0 if scenario == "short_peak" else 420)
    # This full 0.1-to-3.1 window includes polling-gap I/O, not isolated sample deltas.
    assert sum(device[direction]["delta"] for device in io["devices"].values()
               for direction in ("read_bytes", "write_bytes")) == io["delta_bytes"]
    assert io["devices"]["disk6"]["busy_time"] == "UNAVAILABLE"
    if scenario == "short_peak":
        assert all(d.verdict is True for d in [first, second, third, final])
        assert first.checks["disk_io_bytes_per_second"]["actual"] > 100
    else:
        assert final.verdict is False and HostResourceRefusal(final).recoverable_pressure
        assert io["actual"] > 100 and io["maximum"] == 100
    next_window = probe._sample(resources, running_io=True)
    assert next_window.checks["disk_io_bytes_per_second"]["window_complete"]


def test_narration_admission_requires_complete_window_without_blocking(tmp_path, monkeypatch):
    import app.host_resource_probes as probes
    narration = NarrationBudget(80, 1, 100, 15, 100, 1, 120, 100, 10, 0.1, 100, 1)
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(75, 0, 50, 0, 100, 100, narration=narration))
    resources = dict(kind="creation_narration", execution_mode="isolated_qwen3_tts", text_chars=1,
        section_count=1, reference_bytes=100, duration_seconds=15, max_output_bytes=100, threads=1, timeout_seconds=120)
    ticks = iter([0, 0.1, 1, 1.1, 3, 3.1, 34, 34.1, 35, 35.1, 37, 37.1])
    intervals = []
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **kw: {"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)})
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: intervals.append(interval) or 0)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=1000))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(psutil, "disk_usage", lambda path: SimpleNamespace(free=1000))
    monkeypatch.setattr(psutil, "disk_partitions", lambda **kw: [])
    for _ in range(2):
        decision = probe._sample(resources)
        assert decision.verdict is None and decision.reason == "io_window_incomplete"
    assert probe._sample(resources).verdict is True
    for _ in range(2):
        assert probe._sample(resources).reason == "io_window_incomplete"
    assert probe._sample(resources).verdict is True
    assert intervals == [0.1] * 6  # A 30s backoff must require a new complete 3s observation.
