import json
import os
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
from tests.host_memory_boundary import IsolatedMemoryMonitor, isolated_runtime_limits


@pytest.fixture(autouse=True)
def isolated_native_pressure_boundary(monkeypatch):
    # This file isolates CPU/IO/child-group behavior. Native policy/recovery has
    # its own counterexamples in test_host_memory.py; never read the developer's
    # changing OS warning state to decide these unrelated checks.
    monkeypatch.setattr("app.host_resource_probes.HostMemoryMonitor", lambda *_: IsolatedMemoryMonitor())

from app.host_resource_probes import MediaScriptBudget
from app.host_resource_probes import RuntimeCpuPolicy
from app.device_scheduler import RecoveryBlocked

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
from app.platform_browser.base import PUBLISH_OPERATION_TIMEOUT_SECONDS
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


@pytest.fixture(params=["source", "simulated_frozen"])
def grant_entry(request, tmp_path, monkeypatch):
    if request.param == "source":
        return
    # Exercise the real fixed server entry through a standalone executable.
    # This is a simulated frozen launcher, not a PyInstaller acceptance result.
    import app.granted_subprocess as commands

    server = Path(__file__).resolve().parents[1] / "server.py"
    executable = tmp_path / "simulated-api-server"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import builtins, os, runpy, sys\n"
        f"assert os.environ['_PYI_ARCHIVE_FILE'] == {str(executable)!r}\n"
        f"assert os.environ['_PYI_APPLICATION_HOME_DIR'] == {str(tmp_path/'host-runtime')!r}\n"
        "assert os.environ['_PYI_PARENT_PROCESS_LEVEL'] == '1'\n"
        "original_import = builtins.__import__\n"
        "def guarded_import(name, *args, **kwargs):\n"
        "    if name == 'uvicorn' or name == 'app' or name.startswith('app.'):\n"
        "        raise AssertionError('Grant child must not load API/recovery')\n"
        "    return original_import(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded_import\n"
        "sys.frozen = True\n"
        f"runpy.run_path({str(server)!r}, run_name='__main__')\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv('_PYI_ARCHIVE_FILE',str(executable))
    monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR',str(tmp_path/'host-runtime'))
    monkeypatch.setenv('_PYI_PARENT_PROCESS_LEVEL','1')
    monkeypatch.setattr(commands, "sys", SimpleNamespace(
        executable=str(executable), frozen=True, platform=sys.platform,
    ))


def test_frozen_worker_context_is_reused_without_reaching_target(tmp_path,monkeypatch,grant_entry):
    scheduler=RecordingScheduler()
    monkeypatch.setenv('UNRELATED_HOST_SECRET','synthetic-secret')
    environment={'PATH':os.environ['PATH'],'TMPDIR':str(tmp_path),'SAFE_TARGET_VALUE':'retained',
        '_PYI_APPLICATION_HOME_DIR':'untrusted-caller-value','PYINSTALLER_RESET_ENVIRONMENT':'1'}
    result=run(scheduler,[sys.executable,'-c',
        "import json,os; print(json.dumps({'safe':os.environ.get('SAFE_TARGET_VALUE'),"
        "'secret':os.environ.get('UNRELATED_HOST_SECRET'),"
        "'bootloader':[key for key in os.environ if key.startswith(('_PYI_','PYINSTALLER_'))]}))"],env=environment)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'safe':'retained','secret':None,'bootloader':[]}
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_task_file_count_failure_keeps_actual_limit_and_release_receipt(tmp_path):
    scheduler=RecordingScheduler()
    output=tmp_path/'task-output';output.mkdir()
    for index in range(4): (output/str(index)).write_bytes(b'x')
    with pytest.raises(GrantedCommandOutputLimit) as stopped:
        run(scheduler,[sys.executable,'-c','import time;time.sleep(5)'],output_paths=(output,),
            max_output_file_bytes=1024,max_output_files=3)
    expected={'budget_kind':'file_count','actual_count':4,'maximum_count':3,'output_root_index':0}
    assert stopped.value.file_sample==expected
    assert stopped.value.command_diagnostics['output_file_sample']==expected
    assert stopped.value.command_diagnostics['group_released'] is True


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
        isolated_runtime_limits(1024 ** 3, 16, 1, 2**63, 0.05))
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
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={"kind": "material_hash", "size_bytes": 10})).verdict is True
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={"kind": "material_probe_meta", "size_bytes": 10})).verdict is True
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={"kind": "creation_render", "size_bytes": 10})).verdict is None
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={"kind": "material_hash", "size_bytes": 1025})).verdict is False


def test_platform_publish_uses_real_size_finite_browser_deadline_and_shared_host_budget(tmp_path, monkeypatch):
    probe = HostResourceProbe(
        tmp_path / "isolated.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(100, 100, 0, 200, 1000, 4096, 0.01),
    )
    counters = iter((SimpleNamespace(read_bytes=10, write_bytes=20),
                     SimpleNamespace(read_bytes=10, write_bytes=20)))
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **_kwargs: next(counters))
    monkeypatch.setattr(psutil, "cpu_percent", lambda **_kwargs: 1.0)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=10000))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(psutil, "disk_usage", lambda _path: SimpleNamespace(free=10000))
    ticks = iter((10.0, 11.0))
    monkeypatch.setattr("app.host_resource_probes.time.monotonic", lambda: next(ticks))

    resources = {"kind": "platform_publish", "size_bytes": 512,
                 "timeout_seconds": PUBLISH_OPERATION_TIMEOUT_SECONDS,
                 "browser_channel": "chrome"}
    decision = probe._sample(resources)
    assert decision.verdict is True
    assert decision.checks["size_bytes"] == {"actual": 512, "maximum": 4096, "passed": True}
    assert decision.checks["timeout_seconds"]["passed"] is True
    assert decision.checks[f"disk_free_bytes:{tmp_path}"]["minimum"] == 200

    too_large = probe._sample({**resources, "size_bytes": 4097})
    assert too_large.verdict is False and too_large.reason == "declared_resources_exceed_budget"
    too_slow = probe._sample({**resources, "timeout_seconds": PUBLISH_OPERATION_TIMEOUT_SECONDS + 0.01})
    assert too_slow.verdict is False and too_slow.reason == "declared_resources_exceed_budget"
    missing_deadline = probe._sample({key: value for key, value in resources.items()
                                      if key != "timeout_seconds"})
    assert missing_deadline.verdict is None and missing_deadline.reason == "platform_publish_timeout_invalid"


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
        additional_memory_bytes=128*1024**2, additional_disk_bytes=64*1024**2,
        max_process_threads=16,  # Finite synthetic process/disk profile, not a machine default.
        running_cpu_policy=RuntimeCpuPolicy("darwin_nice", "synthetic-render-test-evidence", nice=5),
    )


def test_cpu_render_requires_explicit_budget_and_complete_bounded_contract(tmp_path):
    base = dict(max_cpu_percent=100, min_available_memory_bytes=0,
                max_swap_used_bytes=2**63, min_free_disk_bytes=0,
                max_disk_io_bytes_per_second=2**63, max_unit_input_bytes=1024,
                sample_seconds=0.02)
    unknown = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                budget=HostBudget(**base))
    resources = _render_resources()
    assert unknown.admit(SimpleNamespace(id="fixture-request", resources=resources)).verdict is None
    measured = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                 budget=HostBudget(**base, cpu_render=_render_budget()))
    assert measured.admit(SimpleNamespace(id="fixture-request", resources=resources)).verdict is True
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "execution_mode": "gpu"})).verdict is None
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={k: v for k, v in resources.items()
                                                     if k != "overlay_count"})).verdict is None
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "duration_seconds": 2})).verdict is False
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "overlay_count": 5})).verdict is False


@pytest.mark.parametrize("running", [False, True])
def test_render_running_memory_is_host_headroom_with_explicit_own_process_limits(tmp_path, monkeypatch, running):
    render = replace(_render_budget(), additional_memory_bytes=2*1024**3, max_process_threads=16)
    probe = HostResourceProbe(tmp_path/"db",disk_paths=(tmp_path,),budget=HostBudget(
        100,1024**3,2**63,0,2**63,1024,sample_seconds=.01,cpu_render=render))
    resources = _render_resources()
    monkeypatch.setattr(psutil,"virtual_memory",lambda:SimpleNamespace(available=2*1024**3))
    decision = probe._sample(resources,running_render=running)
    assert decision.checks["available_memory_bytes"]["minimum"] == (1 if running else 3)*1024**3
    assert decision.verdict is running
    monkeypatch.setattr(probe,"_grant_resources",lambda _:resources)
    limits = probe.scene_runtime_limits(SimpleNamespace())
    assert limits.max_process_rss_bytes == render.additional_memory_bytes
    assert limits.max_process_threads == 16 and limits.min_host_available_memory_bytes == 1024**3
    assert limits.sample_seconds == .01
    assert limits.collect_cpu and limits.cpu_policy == render.running_cpu_policy
    grant = Grant("request", "workspace", "creation_job", "job", "render:digest", "a"*64, 1, 1, "token", probe.database_path)
    assert probe.continue_unit(grant) is True


@pytest.mark.parametrize("failure", ["headroom","memory_unknown","scope_unknown"])
def test_render_runtime_keeps_other_host_and_unknown_gates(tmp_path, monkeypatch, failure):
    render = replace(_render_budget(),additional_memory_bytes=2*1024**3)
    probe = HostResourceProbe(tmp_path/"db",disk_paths=(tmp_path,),budget=HostBudget(
        91,1024**3,1024,0,2**63,1024,sample_seconds=.01,cpu_render=render))
    monkeypatch.setattr(probe,"_grant_resources",lambda _:None if failure=="scope_unknown" else _render_resources())
    def memory():
        if failure=="memory_unknown":
            raise OSError("synthetic unknown sample")
        return SimpleNamespace(available=1024**3-1 if failure=="headroom" else 2*1024**3)
    monkeypatch.setattr(psutil,"virtual_memory",memory)
    monkeypatch.setattr(psutil,"cpu_percent",lambda **kwargs:pytest.fail("Running render reused admission CPU gate"))
    monkeypatch.setattr(psutil,"swap_memory",lambda:SimpleNamespace(used=0))
    grant = Grant("request", "workspace", "creation_job", "job", "render:digest", "a"*64, 1, 1, "token", probe.database_path)
    with pytest.raises(HostResourceRefusal):
        probe.continue_unit(grant)


@pytest.mark.parametrize("rss,threads,accepted", [(1024,2,True),(1025,2,False),(1024,3,False)])
def test_render_process_group_rss_and_total_threads_use_actual_cap(monkeypatch,rss,threads,accepted):
    from app.granted_subprocess import _check_scene_runtime
    from app.device_scheduler import ProcessIdentity
    from app.host_resource_probes import SceneRuntimeLimits
    child=ProcessIdentity(100,'1.000000','synthetic-boot')
    current=SimpleNamespace(pid=100,create_time=lambda:1,memory_info=lambda:SimpleNamespace(rss=rss),num_threads=lambda:threads)
    monkeypatch.setattr(psutil,'Process',lambda _:current)
    monkeypatch.setattr(psutil,'process_iter',lambda:[current])
    monkeypatch.setattr(os,'getpgid',lambda _:100)
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=10000))
    monkeypatch.setattr(psutil,'swap_memory',lambda:SimpleNamespace(used=0))
    process=SimpleNamespace(pid=100,poll=lambda:None)
    limits=isolated_runtime_limits(1024,2,100,100,.1)
    observation={"latest":None,"observed_peak_rss":None,"observed_peak_threads":None}
    if accepted:
        monkeypatch.setattr(psutil,'process_iter',lambda:[])
        _check_scene_runtime(process,child,limits,observation)
        assert observation["last_attempt"]["state"] == "unknown"
        assert observation["latest"] is observation["observed_peak_rss"] is None
        monkeypatch.setattr(psutil,'process_iter',lambda:[current])
        _check_scene_runtime(process,child,limits,observation)
    else:
        with pytest.raises(GrantedCommandResourceLimit):
            _check_scene_runtime(process,child,limits,observation)
    assert observation["latest"]["rss_bytes"] == rss
    assert observation["latest"]["threads"] == threads
    assert observation["observed_peak_rss"]["rss_bytes"] == rss
    assert observation["observed_peak_threads"]["threads"] == threads
    if accepted:
        current.memory_info=lambda:SimpleNamespace(rss=512)
        current.num_threads=lambda:1
        _check_scene_runtime(process,child,limits,observation)
        assert observation["latest"]["rss_bytes"] == 512
        assert observation["observed_peak_rss"]["rss_bytes"] == rss
        assert observation["observed_peak_threads"]["threads"] == threads
        assert observation["observed_peak_rss"]["observed_monotonic"] <= observation["latest"]["observed_monotonic"]
        before=observation["latest"]
        monkeypatch.setattr(psutil,'process_iter',lambda:[current,SimpleNamespace(pid=101)])
        def group(pid):
            if pid == 101:
                raise ProcessLookupError()
            return 100
        monkeypatch.setattr(os,'getpgid',group)
        _check_scene_runtime(process,child,limits,observation)
        assert observation["last_attempt"]["sample_complete"] is False
        assert observation["last_attempt"]["reason"] == "process_disappeared_during_enumeration"
        assert observation["latest"]["rss_bytes"] == before["rss_bytes"]
        assert observation["latest"]["threads"] == before["threads"]
        assert observation["latest"]["is_lower_bound"] is True
        assert observation["latest"]["sample_complete"] is False
        assert observation["observed_peak_rss"]["rss_bytes"] == rss
        def missing_threads():
            raise psutil.NoSuchProcess(101)
        disappearing=SimpleNamespace(pid=101,memory_info=lambda:SimpleNamespace(rss=256),num_threads=missing_threads)
        monkeypatch.setattr(psutil,'process_iter',lambda:[current,disappearing])
        monkeypatch.setattr(os,'getpgid',lambda _:100)
        _check_scene_runtime(process,child,limits,observation)
        assert observation["latest"]["rss_bytes"] == 768
        assert observation["latest"]["threads"] == 1
        assert observation["latest"]["rss_members"] == 2 and observation["latest"]["thread_members"] == 1
        assert observation["latest"]["sample_complete"] is False


def test_cpu_render_simulated_memory_pressure_blocks(tmp_path, monkeypatch):
    budget = HostBudget(100, 1, 2**63, 0, 2**63, 1024, 0.02,
                        cpu_render=_render_budget())
    probe = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,), budget=budget)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=0))
    assert probe.admit(SimpleNamespace(id="fixture-request", resources=_render_resources())).verdict is False


@pytest.mark.parametrize("failure", [
    "cpu_percent", "available_memory_bytes",
    "disk_free_bytes", "disk_io_bytes_per_second", "memory_unknown", None,
])
def test_admission_logs_same_observation_without_resampling(
    tmp_path, monkeypatch, caplog, failure,
):
    import app.host_resource_probes as probes

    values = {"cpu_percent": 75, "available_memory_bytes": 100,
              "swap_used_bytes": 50, "disk_free_bytes": 200,
              "disk_io_bytes_per_second": 100}
    if failure in values:
        values[failure] += -1 if failure in {"available_memory_bytes", "disk_free_bytes"} else 1
    calls = []
    counters = iter([SimpleNamespace(read_bytes=0, write_bytes=0),
                     SimpleNamespace(read_bytes=values["disk_io_bytes_per_second"], write_bytes=0)])
    def io():
        calls.append("io")
        return next(counters)
    def memory():
        calls.append("memory")
        if failure == "memory_unknown":
            raise OSError("fixture memory unavailable")
        return SimpleNamespace(available=values["available_memory_bytes"])
    ticks = iter([10.0, 11.0])
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", io)
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: values["cpu_percent"])
    monkeypatch.setattr(psutil, "virtual_memory", memory)
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=values["swap_used_bytes"]))
    monkeypatch.setattr(psutil, "disk_usage", lambda path: SimpleNamespace(free=values["disk_free_bytes"]))
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
                              budget=HostBudget(75, 100, 50, 200, 100, 1024))
    observations = []
    sample = probe._sample
    def observe(resources):
        observations.append(sample(resources))
        return observations[-1]
    monkeypatch.setattr(probe, "_sample", observe)
    request = SimpleNamespace(id="request-admission94", resources={
        "kind": "material_hash", "size_bytes": 10,
        "text": "PRIVATE_USER_BODY", "api_key": "PRIVATE_KEY",
        "token": "PRIVATE_TOKEN", "private_numeric": 8675309,
    })
    with caplog.at_level("WARNING", logger=probes.__name__):
        admission = probe.admit(request)
        verdict = admission.verdict
    assert len(observations) == 1
    assert calls == ["io", "io", "memory"]
    decision = observations[0]
    assert admission is decision
    assert verdict is decision.verdict
    if failure is None:
        assert verdict is True
        assert not caplog.records
        return
    assert len(caplog.records) == 1
    record = caplog.records[0]
    payload = json.loads(record.args[0])
    assert payload["request_id"] == request.id
    assert payload["declared"] == {"size_bytes": 10}
    assert payload["checks"] == decision.checks
    assert payload["reason"] == decision.reason
    assert payload["observed_interval_seconds"] == 1
    assert "PRIVATE" not in record.getMessage()
    assert "8675309" not in record.getMessage()
    if failure == "memory_unknown":
        assert verdict is None and payload["verdict"] is None
        assert payload["reason"] == "available_memory_bytes: OSError: fixture memory unavailable"
        assert "actual" not in payload["checks"]["available_memory_bytes"]
    else:
        assert verdict is False and payload["verdict"] is False
        key = f"disk_free_bytes:{tmp_path}" if failure == "disk_free_bytes" else failure
        check = payload["checks"][key]
        assert check["actual"] == values[failure] and check["passed"] is False
        boundary = "minimum" if failure in {"available_memory_bytes", "disk_free_bytes"} else "maximum"
        assert check[boundary] == values[failure] + (1 if boundary == "minimum" else -1)


def test_admission_logs_declared_render_limit_without_host_sample(tmp_path, monkeypatch, caplog):
    import app.host_resource_probes as probes

    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
                              budget=HostBudget(100, 1, 50, 200, 100, 1024,
                                                cpu_render=_render_budget()))
    def no_host_sample(*args, **kwargs):
        pytest.fail("Declared refusal must not sample host resources")
    monkeypatch.setattr(psutil, "disk_io_counters", no_host_sample)
    request = SimpleNamespace(id="request-declared94", resources={
        **_render_resources(), "duration_seconds": 2, "text": "PRIVATE_BODY",
    })
    with caplog.at_level("WARNING", logger=probes.__name__):
        assert probe.admit(request).verdict is False
    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].args[0])
    assert payload["request_id"] == request.id
    assert payload["reason"] == "declared_resources_exceed_budget"
    assert payload["declared"]["duration_seconds"] == 2
    assert payload["checks"]["render_budget"]["limits"]["max_duration_seconds"] == 1
    assert payload["observed_interval_seconds"] is None
    assert "PRIVATE_BODY" not in caplog.text


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
    assert unknown.admit(SimpleNamespace(id="fixture-request", resources=resources)).verdict is None
    measured = HostResourceProbe(tmp_path / "db.sqlite", disk_paths=(tmp_path,),
                                 budget=HostBudget(**base, scene_window=_scene_budget()))
    assert measured.admit(SimpleNamespace(id="fixture-request", resources=resources)).verdict is True
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "source_codec": "av1"})).verdict is None
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "source_frame_rate_fps": None})).verdict is None
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "source_width_px": 3840})).verdict is False
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "source_frame_rate_fps": 240})).verdict is False
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "scan_seconds": 16})).verdict is False
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "output_max_frames": 20})).verdict is None
    assert measured.admit(SimpleNamespace(id="fixture-request", resources={**resources, "timeout_seconds": 20})).verdict is False


def test_registration_ack_precedes_real_exec(tmp_path, grant_entry):
    marker = tmp_path / "must-not-exist"
    scheduler = RecordingScheduler(reject_registration=True)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        run(scheduler, [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])
    assert not marker.exists()
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


@pytest.mark.parametrize("revoked", [False, True])
def test_grant_is_rechecked_after_registration_before_exec(tmp_path, grant_entry, revoked):
    marker = tmp_path / "executed"

    class CheckedScheduler(RecordingScheduler):
        def register_child(self, grant, child):
            assert not marker.exists()
            super().register_child(grant, child)
            time.sleep(0.05)
            assert not marker.exists()

        def assert_grant(self, grant):
            super().assert_grant(grant)
            if self.children:
                assert not marker.exists()
                if revoked:
                    raise PermissionError("grant revoked before exec ACK")

    scheduler = CheckedScheduler()
    command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    if revoked:
        with pytest.raises(PermissionError, match="grant revoked"):
            run(scheduler, command)
        assert not marker.exists()
    else:
        assert run(scheduler, command).returncode == 0
        assert marker.exists()
    assert len(scheduler.children) == 1
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


def test_cancel_before_or_during_registration_never_execs_marker(tmp_path, grant_entry):
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


def test_cancel_after_output_eof_still_stops_live_process(grant_entry):
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


def test_real_command_timeout_cancel_output_limit_and_release(grant_entry):
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


def test_timeout_stops_real_grandchild_in_same_process_group(grant_entry):
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


@pytest.mark.parametrize("refusal", ["rss_bytes", "threads", "unavailable"])
def test_scene_process_monitor_stops_real_child_after_output_eof(tmp_path, monkeypatch, refusal):
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
    unavailable = refusal == "unavailable"
    scene_budget = replace(_scene_budget(),
                           max_process_rss_bytes=(256 if refusal == "threads" else 24) * 1024 * 1024,
                           max_process_threads=1 if refusal == "threads" else 128,
                           additional_memory_bytes=(256 if refusal == "threads" else 24) * 1024 * 1024)
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
        with pytest.raises(expected) as caught:
            run_granted_command(
                scheduler, grant,
                [sys.executable, "-c", "import os,time,threading; os.close(1); os.close(2); "
                 "held=bytearray(80_000_000); "
                 "threading.Thread(target=lambda:time.sleep(5),daemon=True).start(); time.sleep(5)"],
                timeout_seconds=3, max_stdout_bytes=100, max_stderr_bytes=100,
            )
        assert time.monotonic() - start < 2
        assert caught.value.command_diagnostics["group_released"] is True
        if not unavailable:
            sample = caught.value.resource_sample
            assert sample["limits"] == {"rss_bytes": scene_budget.max_process_rss_bytes,
                                        "threads": scene_budget.max_process_threads}
            assert sample["exceeded"] == [refusal]
            assert sample["measured"][refusal] > sample["limits"][refusal]
            assert sample["measured"]["rss_bytes"] > 0
            assert sample["measured"]["threads"] >= 1
            assert str(sample["measured"][refusal]) in str(caught.value)
            assert str(sample["limits"][refusal]) in str(caught.value)
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


@pytest.mark.parametrize("kind,declared", [
    ("creation_render", _render_declared()),
    ("mat_scene_probe", {"cpu_threads": 2, "wall_clock_s": 60.0,
                         "max_source_duration_s": 60.0, "sampling_fps": 2.0}),
])
@pytest.mark.parametrize("load", [8.0, 8.11])
def test_admit_resource_keeps_high_historical_load_as_diagnostic(kind, declared, load):
    capacity = _capacity(load=load, cpu_count=8)
    admission = admit_resource(kind, declared, capacity=capacity)
    assert admission.granted == declared
    assert admission.cpu_threads_ceiling == capacity.cpu_threads_ceiling
    assert admission.load_avg_1m == load


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
    assert all(check.get("passed") is not False for name, check in decision.checks.items()
               if name not in {key, "memory_policy"})
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


@pytest.fixture
def narration_cpu_boundary(monkeypatch):
    # These tests isolate IO/memory/input caps; OS scheduling has its own tests.
    import app.host_resource_probes as probes
    monkeypatch.setattr(probes, "_cpu_counter", lambda: {
        "user": 1., "nice": 0., "system": 0., "idle": 7., "logical_cpu_count": 8,
        "observed_monotonic": 0., "read_started_monotonic": 0., "read_seconds": 0.})
    monkeypatch.setattr(probes.HostResourceProbe, "_runtime_cpu_observation", lambda *args:
        {"diagnostic_only": True, "state": "unknown", "actual": None})
    monkeypatch.setattr(probes, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(probes.time, "sleep", lambda _: None)
    return RuntimeCpuPolicy("darwin_background", "synthetic-cpu-boundary")


def test_exact_host_boundaries_and_narration_caps_preserve_admission(tmp_path, monkeypatch, narration_cpu_boundary):
    import app.host_resource_probes as probes
    narration = NarrationBudget(80, 1, 1048576, 15, 2097152, 1, 120,
                                2147483648, 128, 0.1, 2147483648, 8388608, narration_cpu_boundary)
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
    assert probe.admit(SimpleNamespace(id="fixture-request", resources=resources, last_grant_generation=None)).verdict is True
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={**resources, "text_chars": 81})).verdict is False
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={**resources, "reference_bytes": 1048577})).verdict is False
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={**resources, "execution_mode": "in_process"})).verdict is None
    assert probe.admit(SimpleNamespace(id="fixture-request", resources={**resources, "timeout_seconds": None})).verdict is None
    assert reads == ["cpu"]  # Declared rejection never becomes a ready host resample.


@pytest.mark.parametrize("kind, available, runtime_verdict", [
    ("creation_narration", 29 * 1024**3 // 10, True),
    ("creation_narration", 1024**3, True),
    ("creation_narration", 1024**3 - 1, False),
    ("creation_narration", None, None),
    ("creation_person_render", 29 * 1024**3 // 10, False),
])
def test_narration_reserves_memory_at_admission_but_runtime_keeps_system_floor(
    tmp_path, monkeypatch, kind, available, runtime_verdict, narration_cpu_boundary,
):
    from itertools import count
    import app.host_resource_probes as probes
    from app.host_resource_probes import PresenterBudget
    gib = 1024**3
    narration = NarrationBudget(128, 1, 8 * 1024**2, 20, 4 * 1024**2, 1, 90,
                                3 * gib // 2, 16, .1, 2 * gib, 32 * 1024**2, narration_cpu_boundary)
    presenter = PresenterBudget(1, 8 * 1024**2, 20, 4 * 1024**2, 1, 90,
                                3 * gib // 2, 16, .1, 2 * gib, 32 * 1024**2)
    budget = HostBudget(91, gib, 16 * gib, gib, 10**9, 128 * 1024**2,
                        narration=narration, presenter=presenter)
    probe = HostResourceProbe(tmp_path / "metadata.sqlite", disk_paths=(tmp_path,), budget=budget)
    resources = dict(kind=kind, execution_mode="isolated_qwen3_tts" if kind == "creation_narration" else "isolated_avatar_lipsync",
                     text_chars=100, section_count=1, reference_bytes=1024, input_bytes=1024,
                     duration_seconds=20, max_output_bytes=4 * 1024**2, threads=1, timeout_seconds=90)
    ticks = count(0, .1)
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **_kwargs:
                        {"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)})
    monkeypatch.setattr(psutil, "disk_partitions", lambda **_kwargs: [])
    monkeypatch.setattr(psutil, "cpu_percent", lambda **_kwargs: 10)
    def memory():
        if available is None:
            raise OSError("fixture memory unavailable")
        return SimpleNamespace(available=available)
    monkeypatch.setattr(psutil, "virtual_memory", memory)
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(psutil, "disk_usage", lambda _path: SimpleNamespace(free=10**12))
    assert probe.admit(SimpleNamespace(id="fixture-request", resources=resources)).verdict is (None if available is None else False)
    monkeypatch.setattr(probe, "_grant_resources", lambda _grant: resources)
    grant = SimpleNamespace(request_id="fixture-request", generation=1)
    if runtime_verdict is True:
        assert probe.continue_unit(grant) is True
    else:
        with pytest.raises(HostResourceRefusal) as stopped:
            probe.continue_unit(grant)
        decision = stopped.value.decision
        assert decision.verdict is runtime_verdict
        expected_floor = gib if kind == "creation_narration" else 3 * gib
        assert decision.checks["available_memory_bytes"]["minimum"] == expected_floor
        if available is None:
            assert "actual" not in decision.checks["available_memory_bytes"]
    limits = probe.scene_runtime_limits(grant)
    assert limits.min_host_available_memory_bytes == gib
    assert limits.max_process_rss_bytes == 3 * gib // 2
    assert limits.max_process_threads == 16


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


@pytest.mark.parametrize("release_unknown", [False, True])
def test_refusal_keeps_bounded_phase_output_after_real_group_release(tmp_path, monkeypatch, caplog, release_unknown):
    import app.granted_subprocess as commands
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
        isolated_runtime_limits(1024**3, 16, 1, 2**63, 0.1))
    scheduler.began = time.monotonic()
    if release_unknown:
        cleanup = commands._terminate_and_verify
        def unavailable_proof(*args):
            cleanup(*args)  # The test still cleans its real child.
            raise RecoveryBlocked("synthetic release proof unavailable")
        monkeypatch.setattr(commands, "_terminate_and_verify", unavailable_proof)
    with pytest.raises(RecoveryBlocked if release_unknown else HostResourceRefusal) as stopped:
        run_granted_command(scheduler, grant, [sys.executable, "-c",
            "import sys,time; print('PRIVATE_BODY',file=sys.stderr); print('QWEN_PHASE importing_runtime',file=sys.stderr,flush=True); time.sleep(5)"],
            timeout_seconds=2, max_stdout_bytes=64, max_stderr_bytes=64)
    if not release_unknown:
        assert stopped.value.decision is decision
        diagnostic = stopped.value.command_diagnostics
        assert diagnostic["exec_ack"] and diagnostic["group_released"]
        assert b"QWEN_PHASE importing_runtime" in diagnostic["stderr"]
        assert len(diagnostic["stderr"]) <= 64
    record = next(r for r in caplog.records if r.msg == "Granted command diagnostic: %s")
    payload = json.loads(record.args[0])
    assert payload["request_id"] == grant.request_id and payload["attempt_number"] == 1
    assert payload["permit_generation"] == 1 and payload["child"]["pid"] == scheduler.children[0].pid
    assert payload["decision"]["checks"]["disk_io_bytes_per_second"] == decision.checks["disk_io_bytes_per_second"]
    assert payload["qwen_markers"]["stderr"] == ["QWEN_PHASE importing_runtime"]
    assert payload["group_released"] is (None if release_unknown else True)
    release = payload["release_observations"]["final_after_parent_wait"]
    assert release["released"] is True
    assert release["group"]["registered"]["pid"] == payload["child"]["pid"]
    assert "PRIVATE_BODY" not in record.getMessage()
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


@pytest.mark.parametrize("returncode,with_markers", [(0, True), (3, True), (3, False)])
def test_successful_command_records_only_closed_qwen_diagnostics(tmp_path, monkeypatch, caplog, returncode, with_markers):
    grant = Grant("request", "workspace", "creation_job", "job", "narration:digest",
                  "a" * 64, 1, 7, "PRIVATE_TOKEN", tmp_path / "isolated.sqlite")
    class Scheduler(RecordingScheduler):
        def assert_grant(self, current):
            assert current == grant
    scheduler = Scheduler()
    scheduler.resources = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 1, 2**63, 1024))
    monkeypatch.setattr(scheduler.resources, "scene_runtime_limits", lambda current:
        isolated_runtime_limits(1024**3, 32, 1, 2**63, 0.1))
    code = "import sys; print('PRIVATE_REFERENCE'); print('QWEN_PHASE loading_weights PRIVATE_TEXT',file=sys.stderr); "
    if with_markers:
        code += ("print('QWEN_PHASE audio_written'); "
            "print('QWEN_MEMORY phase=audio_written active_bytes=12 cache_bytes=0 peak_bytes=42',file=sys.stderr); ")
    code += f"sys.exit({returncode})"
    result = run_granted_command(scheduler, grant, [sys.executable, "-c", code],
        timeout_seconds=2, max_stdout_bytes=256, max_stderr_bytes=256)
    assert result.returncode == returncode and b"PRIVATE_REFERENCE" in result.stdout
    record = next(r for r in caplog.records if r.msg == "Granted command diagnostic: %s")
    payload = json.loads(record.args[0])
    assert payload["returncode"] == returncode and payload["failure_type"] is None
    assert payload["permit_generation"] == 7 and payload["group_released"] is True
    assert payload["qwen_markers"] == ({"stdout": ["QWEN_PHASE audio_written"],
        "stderr": ["QWEN_MEMORY phase=audio_written active_bytes=12 cache_bytes=0 peak_bytes=42"]}
        if with_markers else {"stdout": [], "stderr": []})
    assert all(secret not in record.getMessage() for secret in ("PRIVATE_REFERENCE", "PRIVATE_TEXT", "PRIVATE_TOKEN"))


def test_resource_diagnostic_projection_excludes_text_paths_and_unapproved_fields(caplog):
    decision = HostResourceDecision(False, {"text": "PRIVATE_TEXT"}, {
        "cpu_percent": {"actual": 95, "maximum": 91, "passed": False, "raw": "PRIVATE_BODY"},
        "disk_free_bytes:/PRIVATE_PATH": {"actual": 50, "minimum": 100, "passed": False},
        "memory_policy": {"passed": False, "system_reserve_bytes": 100,
            "recovery_reset": {"reason":"memory_observation_gap", "source":"background_observer",
                "previous_observed_monotonic":38.0, "observed_monotonic":42.0, "interval_seconds":4.0,
                "pressure":"warning", "available_bytes":200, "raw":"PRIVATE_BODY"},
            "policy_evidence_reference": "PRIVATE_PATH", "sample": {
                "platform": "darwin", "observed_monotonic": 42.0,
                "observed_at_utc": "2026-10-18T05:15:28+00:00", "pressure": "critical",
                "available_bytes": 50, "facts": {"pressure": {"native_value": 4}, "private": "PRIVATE_TEXT"}}},
        "private": {"raw": "PRIVATE_TOKEN"},
    }, "memory_pressure_critical", 0.1)
    payload = decision.diagnostic_payload()
    assert payload["checks"]["memory_policy"]["recovery_reset"]["interval_seconds"] == 4.0
    assert payload["checks"]["memory_policy"]["recovery_reset"]["source"] == "background_observer"
    assert payload["checks"]["memory_policy"]["sample"]["native_pressure_value"] == 4
    assert payload["checks"]["memory_policy"]["sample"]["observed_at_utc"] == "2026-10-18T05:15:28+00:00"
    assert payload["checks"]["cpu_percent"] == {"actual": 95, "maximum": 91, "passed": False}
    assert payload["checks"]["disk_free_bytes"] == [{"actual": 50, "minimum": 100, "passed": False}]
    assert "PRIVATE" not in json.dumps(payload)
    from app.device_accounts import report_creation_identity_unknown
    from app.device_scheduler import DeviceScheduler, Grant
    from app.granted_subprocess import _record_command_diagnostics
    verdict = report_creation_identity_unknown(("PRIVATE_DEVICE", "PRIVATE_USER", "PRIVATE_WORKSPACE"),
        source="device_creation_permit", reason="transport_error", exception_type="ReadTimeout")
    assert isinstance(verdict, str) and verdict == "unknown"
    grant = Grant("correlation-request", "workspace", "creation_job", "job", "narration:input",
        "a" * 64, 1, 65, "PRIVATE_TOKEN", Path("/unused.sqlite"))
    with pytest.raises(PermissionError) as refused:
        DeviceScheduler._require_current(verdict, grant)
    _record_command_diagnostics(grant, None, {"stdout":b"", "stderr":b"",
        "stdout_truncated":False, "stderr_truncated":False, "exec_ack":False,
        "group_released":True, "before_termination_returncode":None, "returncode":None}, failure=refused.value)
    records = {record.msg:json.loads(record.args[0]) for record in caplog.records if record.msg in {
        "Creation identity verification unavailable: %s", "Device owner current check refused: %s", "Granted command diagnostic: %s"}}
    identity = records["Creation identity verification unavailable: %s"]
    owner = records["Device owner current check refused: %s"]
    command = records["Granted command diagnostic: %s"]
    assert identity["verification_id"] == owner["verification_id"] == command["business_failure"]["verification_id"]
    assert (owner["request_id"],owner["attempt_number"],owner["permit_generation"]) == (command["request_id"],command["attempt_number"],command["permit_generation"]) == (grant.request_id,1,65)
    assert "PRIVATE" not in json.dumps(records)


@pytest.mark.parametrize("scenario", ["short_peak", "sustained", "unknown", "counter_regressed", "observation_gap"])
def test_narration_io_uses_real_endpoint_window_and_finite_burst(tmp_path, monkeypatch, scenario, narration_cpu_boundary):
    import app.host_resource_probes as probes
    narration = NarrationBudget(80, 1, 1048576, 15, 2097152, 1, 120,
                                100, 128, 0.1, 100, 1, narration_cpu_boundary)
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
    first = probe._sample(resources, running_io=True, cpu_grant="isolated")
    if scenario == "unknown":
        assert first.verdict is None and first.reason == "disk_io_before_unavailable"
        assert not HostResourceRefusal(first).recoverable_pressure
        return
    assert first.verdict is True  # Known measured bytes below the finite 300B burst budget.
    second = probe._sample(resources, running_io=True, cpu_grant="isolated")
    if scenario == "observation_gap":
        assert second.verdict is None and second.reason == "io_observation_gap"
        return
    if scenario == "counter_regressed":
        assert second.verdict is None and second.reason == "disk_io_counters_regressed"
        return
    third = probe._sample(resources, running_io=True, cpu_grant="isolated")
    final = probe._sample(resources, running_io=True, cpu_grant="isolated")
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
    next_window = probe._sample(resources, running_io=True, cpu_grant="isolated")
    assert next_window.checks["disk_io_bytes_per_second"]["window_complete"]


def test_narration_admission_requires_complete_window_without_blocking(tmp_path, monkeypatch, narration_cpu_boundary):
    import app.host_resource_probes as probes
    narration = NarrationBudget(80, 1, 100, 15, 100, 1, 120, 100, 10, 0.1, 100, 1, narration_cpu_boundary)
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


@pytest.mark.parametrize("failure", ["before_missing", "after_missing", "read_error", "devices", "regressed", "gap"])
def test_base_io_failure_requires_new_real_endpoints(tmp_path, monkeypatch, failure):
    import app.host_resource_probes as probes
    clock = [0.]
    monkeypatch.setattr(probes, "time", SimpleNamespace(monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    def healthy(**_):
        return {"disk0": SimpleNamespace(read_bytes=int(clock[0] * 10), write_bytes=0)}
    monkeypatch.setattr(psutil, "disk_io_counters", healthy)
    monkeypatch.setattr(psutil, "disk_partitions", lambda **_: [])
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(75, 100, None, 100, 1000, 100))
    for second in range(4):
        clock[0] = float(second)
        evidence, reason = probe._observe_base()
    assert reason is None and evidence["window_complete"]
    clock[0] = 10. if failure == "gap" else 4.
    if failure == "read_error":
        monkeypatch.setattr(psutil, "disk_io_counters", lambda **_: (_ for _ in ()).throw(OSError("synthetic")))
    elif failure != "gap":
        snapshots = iter({
            "before_missing": [None],
            "after_missing": [healthy(), None],
            "devices": [healthy(), {"disk1": SimpleNamespace(read_bytes=40, write_bytes=0)}],
            "regressed": [{"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)}] * 2,
        }[failure])
        monkeypatch.setattr(psutil, "disk_io_counters", lambda **_: next(snapshots))
    evidence, reason = probe._observe_base()
    if failure == "gap":
        assert reason is None and not evidence["window_complete"]
        assert evidence["window_started_monotonic"] == 10.
    else:
        assert evidence is None and reason is not None and not probe._admission_io
    monkeypatch.setattr(psutil, "disk_io_counters", healthy)
    restart = clock[0] + 1
    clock[0] = restart
    evidence, reason = probe._observe_base()
    assert reason is None and not evidence["window_complete"]
    for step in (1, 2, 3):
        clock[0] = restart + step
        evidence, reason = probe._observe_base()
    assert reason is None and evidence["window_complete"]
    assert evidence["window_started_monotonic"] >= restart


def test_base_and_live_io_reads_share_ordered_lock(tmp_path, monkeypatch, narration_cpu_boundary):
    from concurrent.futures import ThreadPoolExecutor
    import app.host_resource_probes as probes
    clock, calls = [0.], []
    first_entered, release_first, next_entered = (threading.Event() for _ in range(3))
    monkeypatch.setattr(probes, "time", SimpleNamespace(monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    def disks(**_):
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(2)
        else:
            next_entered.set()
        return {"disk0": SimpleNamespace(read_bytes=int(clock[0] * 10), write_bytes=0)}
    monkeypatch.setattr(psutil, "disk_io_counters", disks)
    monkeypatch.setattr(psutil, "disk_partitions", lambda **_: [])
    monkeypatch.setattr(psutil, "disk_usage", lambda _: SimpleNamespace(free=10**9))
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: probes.time.sleep(interval) or 0)
    budget = NarrationBudget(80, 1, 100, 15, 100, 1, 120, 100, 10, .1, 100, 100, narration_cpu_boundary)
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(75, 100, None, 100, 1000, 100, narration=budget))
    resources = dict(kind="creation_narration", execution_mode="isolated_qwen3_tts", text_chars=17,
        section_count=1, reference_bytes=100, duration_seconds=15, max_output_bytes=100, threads=1, timeout_seconds=120)
    with ThreadPoolExecutor(max_workers=2) as pool:
        base = pool.submit(probe._observe_base)
        assert first_entered.wait(1)
        live = pool.submit(probe._sample, resources)
        try:
            assert not next_entered.wait(.05)
        finally:
            release_first.set()
        base_evidence, _ = base.result(timeout=1)
        current = live.result(timeout=1)
    assert base_evidence["sample_finished_monotonic"] <= current.checks["disk_io_bytes_per_second"]["sample_started_monotonic"]
    assert list(probe._admission_io) == sorted(probe._admission_io, key=lambda item: item[0])


@pytest.mark.parametrize("failure", ["gap", "unavailable"])
def test_base_observer_maintains_bound_running_io_and_preserves_real_faults(tmp_path, monkeypatch, narration_cpu_boundary, failure):
    from dataclasses import replace
    import app.host_resource_probes as probes
    clock = [0.]
    monkeypatch.setattr(probes, "time", SimpleNamespace(monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    def healthy(**_):
        return {"disk0": SimpleNamespace(read_bytes=int(clock[0] * 10), write_bytes=0)}
    monkeypatch.setattr(psutil, "disk_io_counters", healthy)
    monkeypatch.setattr(psutil, "disk_partitions", lambda **_: [])
    monkeypatch.setattr(psutil, "disk_usage", lambda _: SimpleNamespace(free=10**9))
    budget = NarrationBudget(80, 1, 100, 15, 100, 1, 120, 100, 10, .1, 100, 100, narration_cpu_boundary)
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(75, 100, None, 100, 1000, 100, narration=budget))
    resources = dict(kind="creation_narration", execution_mode="isolated_qwen3_tts", text_chars=17,
        section_count=1, reference_bytes=100, duration_seconds=15, max_output_bytes=100, threads=1, timeout_seconds=120)
    monkeypatch.setattr(probe, "_grant_resources", lambda _: resources)
    grant = Grant("request", "workspace", "creation_job", "job", "narration:digest",
        "a" * 64, 1, 1, "token", probe.database_path)
    for second in range(4):
        clock[0] = float(second)
        probe._observe_base()
    assert not probe._running_io and probe._running_io_key is None
    assert probe.continue_unit(grant)
    assert len(probe._running_io) == 2
    previous = list(probe._running_io)
    # Business preparation can exceed three seconds while the existing observer
    # keeps measuring; it must not become a fictitious IO observation gap.
    for _ in range(5):
        clock[0] += 1
        probe._observe_base()
    assert probe._running_io[-1][0] > previous[-1][0] + probe.io_window_seconds
    assert probe.continue_unit(grant)
    clock[0] += 1
    next_grant = replace(grant, generation=2)
    assert probe.continue_unit(next_grant)
    assert len(probe._running_io) == 2 and probe._running_io[0][0] > previous[-1][0]
    assert probe._running_io_key == (grant.request_id, 2)
    if failure == "gap":
        clock[0] += probe.io_window_seconds + 1
    else:
        monkeypatch.setattr(psutil, "disk_io_counters", lambda **_: None)
    probe._observe_base()
    monkeypatch.setattr(psutil, "disk_io_counters", healthy)
    clock[0] += 1
    probe._observe_base()  # A later healthy observation cannot erase the failure.
    with pytest.raises(HostResourceRefusal) as refused:
        probe.continue_unit(next_grant)
    assert refused.value.decision.reason == ("io_observation_gap" if failure == "gap" else "disk_io_before_unavailable")
    assert refused.value.recoverable_pressure is (failure == "gap")
    assert probe.continue_unit(replace(grant, generation=3))
    assert len(probe._running_io) == 2 and probe._running_io_key == (grant.request_id, 3)


def test_presenter_probe_keeps_dedicated_caps_unknown_and_measured_floor(tmp_path, monkeypatch):
    import app.host_resource_probes as probes
    from app.host_resource_probes import PresenterBudget
    presenter = PresenterBudget(1, 1024, 15, 4096, 1, 5, 8192, 8, .1, 8192, 4096)
    resources = {"kind": "creation_person_render", "execution_mode": "isolated_avatar_lipsync",
        "section_count": 1, "input_bytes": 1024, "duration_seconds": 15,
        "max_output_bytes": 4096, "threads": 1, "timeout_seconds": 5}
    probe = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,), budget=HostBudget(75, 100, 50, 200, 100, 1024))
    assert probe._sample(resources).reason == "stage_budget_unavailable"
    probe.budget = replace(probe.budget, presenter=presenter)
    over = probe._sample({**resources, "input_bytes": 1025})
    assert over.verdict is False and over.checks["input_bytes"] == {"actual": 1025, "maximum": 1024, "passed": False}
    assert probe._sample({**resources, "duration_seconds": None}).verdict is None
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 75)
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **kwargs: {"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)})
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=100+8192))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=50))
    monkeypatch.setattr(psutil, "disk_usage", lambda path: SimpleNamespace(free=200+4096+4096))
    monkeypatch.setattr(psutil, "disk_partitions", lambda **kwargs: [])
    ticks = iter([10., 13.])
    monkeypatch.setattr(probes.time, "monotonic", lambda: next(ticks))
    decision = probe._sample(resources)
    assert decision.verdict is True
    assert decision.checks["available_memory_bytes"]["minimum"] == 100+8192
    assert decision.checks[f"disk_free_bytes:{tmp_path}"]["minimum"] == 200+4096+4096
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **kwargs: None)
    unknown = probe._sample(resources)
    assert unknown.verdict is None and unknown.reason == "disk_io_before_unavailable"
    # A calibrated pure-material budget never stands in for the presenter combination budget.
    composition = {"kind": "creation_presenter_render", "execution_mode": "cpu_ffmpeg"}
    assert probe._sample(composition).reason == "render_budget_or_execution_mode_unavailable"


@pytest.mark.parametrize("unit_key", ["person_render:input", "person_edit:input"])
def test_person_model_child_rejects_non_host_probe_before_exec(tmp_path, unit_key):
    grant = Grant("request", "local", "creation_job", "job", unit_key, "digest", 1, 1, "token", tmp_path / "db")
    scheduler = RecordingScheduler()
    scheduler.assert_grant = lambda current: None
    scheduler.resources = SimpleNamespace(scene_runtime_limits=lambda grant: None)
    marker = tmp_path / "must-not-exec"
    from app.device_scheduler import RecoveryBlocked
    with pytest.raises(RecoveryBlocked, match="真实资源探针"):
        run_granted_command(scheduler, grant, [sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            timeout_seconds=1, max_stdout_bytes=100, max_stderr_bytes=100)
    assert not marker.exists() and not scheduler.children


@pytest.mark.parametrize("unit_key", ["voice:input", "avatar_lipsync:input"])
def test_media_preview_child_requires_real_probe_and_declared_budget(tmp_path, unit_key):
    from app.device_scheduler import RecoveryBlocked
    grant = Grant("preview-request", "local", "media_preview", "preview", unit_key,
                  "digest", 1, 1, "token", tmp_path / "db")
    scheduler = RecordingScheduler()
    scheduler.assert_grant = lambda current: None
    scheduler.resources = SimpleNamespace(scene_runtime_limits=lambda current: None)
    marker = tmp_path / "must-not-exec"
    with pytest.raises(RecoveryBlocked, match="真实资源探针"):
        run_granted_command(scheduler, grant, [sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
                            timeout_seconds=1, max_stdout_bytes=100, max_stderr_bytes=100)
    assert not marker.exists() and not scheduler.children


@pytest.mark.parametrize("scenario", ["allowed", "register_refused", "cancel_before_exec", "revoked", "resource_limit", "timeout"])
def test_media_preview_registered_child_gate_and_group_release(tmp_path, monkeypatch, scenario):
    """Real POSIX children, controlled resource facts; no host capacity sampling/model."""
    from app.host_resource_probes import SceneRuntimeLimits
    import app.granted_subprocess as commands
    grant = Grant("preview-request", "local", "media_preview", "preview", "voice:input",
                  "digest", 1, 1, "token", tmp_path / "db")
    cancel = threading.Event()
    class PreviewScheduler(RecordingScheduler):
        samples = 0
        def assert_grant(self, current):
            assert current == grant
            self.samples += 1
            if scenario == "revoked" and self.samples >= 4:
                raise GrantedCommandCancelled("synthetic permit revoked")
        def register_child(self, current, child):
            self.assert_grant(current)
            if scenario == "register_refused":
                raise PermissionError("synthetic child registration refused")
            self.children.append(child)
            if scenario == "cancel_before_exec":
                cancel.set()
    scheduler = PreviewScheduler()
    scheduler.resources = HostResourceProbe(tmp_path / "db", disk_paths=(tmp_path,),
                                            budget=HostBudget(100,1,2**63,1,2**63,1024))
    monkeypatch.setattr(scheduler.resources, "scene_runtime_limits", lambda current: isolated_runtime_limits(10**9,16,1,2**63,0.05))
    def controlled_sample(*args):
        if scenario == "resource_limit":
            raise GrantedCommandResourceUnavailable("synthetic resource sample limit")
    monkeypatch.setattr(commands, "_check_scene_runtime", controlled_sample)
    marker = tmp_path / "executed"
    argv = [sys.executable, "-c", f"import time;open({str(marker)!r},'w').write('allowed');time.sleep({0 if scenario=='allowed' else 2})"]
    expected = {"register_refused":PermissionError,"cancel_before_exec":GrantedCommandCancelled,
                "revoked":GrantedCommandCancelled,"resource_limit":GrantedCommandResourceUnavailable,"timeout":GrantedCommandTimeout}
    if scenario == "allowed":
        assert run_granted_command(scheduler,grant,argv,timeout_seconds=1,max_stdout_bytes=100,max_stderr_bytes=100,cancel_event=cancel).returncode==0
        assert marker.read_text()=="allowed" and len(scheduler.children)==1
    else:
        with pytest.raises(expected[scenario]) as refusal:
            run_granted_command(scheduler,grant,argv,timeout_seconds=0.3,max_stdout_bytes=100,max_stderr_bytes=100,cancel_event=cancel)
        assert refusal.value.command_diagnostics["group_released"] is True
        if scenario in {"register_refused","cancel_before_exec"}:
            assert refusal.value.command_diagnostics["exec_ack"] is False
            assert not marker.exists()
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True


@pytest.mark.parametrize("boundary", ["files", "stdout", "stderr"])
def test_output_limit_captures_file_measurement_before_cleanup_and_separates_pipes(
    tmp_path, monkeypatch, caplog, grant_entry, boundary,
):
    import app.granted_subprocess as commands
    grant = Grant("request", "workspace", "creation_job", "job", "render:digest",
                  "a" * 64, 2, 7, "PRIVATE_TOKEN", tmp_path / "isolated.sqlite")
    class Scheduler(RecordingScheduler):
        def assert_grant(self, current):
            assert current == grant
    scheduler = Scheduler()
    first, second, missing = (tmp_path / name for name in ("PRIVATE_FIRST", "PRIVATE_SECOND", "PRIVATE_MISSING"))
    code = "import pathlib,sys,time; "
    if boundary == "files":
        code += f"pathlib.Path({str(first)!r}).write_bytes(b'x'*768); pathlib.Path({str(second)!r}).write_bytes(b'x'*512); "
        cleanup = commands._terminate_and_verify
        def change_files_after_termination(*args):
            cleanup(*args)
            first.write_bytes(b"x")
            second.unlink()
        monkeypatch.setattr(commands, "_terminate_and_verify", change_files_after_termination)
    else:
        code += f"print('PRIVATE_PIPE'*200,file=sys.{boundary},flush=True); "
    code += "time.sleep(5)"
    with pytest.raises(GrantedCommandOutputLimit) as stopped:
        run_granted_command(scheduler, grant, [sys.executable, "-c", code],
            timeout_seconds=2, max_stdout_bytes=64, max_stderr_bytes=64,
            output_paths=(first, second, missing), max_output_file_bytes=1024)
    error = stopped.value
    payload = json.loads(next(r.args[0] for r in caplog.records if r.msg == "Granted command diagnostic: %s"))
    if boundary == "files":
        expected = {"actual_bytes": 1280, "maximum_bytes": 1024, "output_count": 2}
        assert error.file_sample == expected
        assert error.command_diagnostics["output_file_sample"] == payload["output_file_sample"] == expected
        assert first.stat().st_size == 1 and not second.exists()  # Receipt predates cleanup.
    else:
        assert error.file_sample is None
        assert "output_file_sample" not in error.command_diagnostics and "output_file_sample" not in payload
    assert payload["request_id"] == grant.request_id and payload["attempt_number"] == 2
    assert payload["permit_generation"] == 7 and payload["child"]["pid"] == scheduler.children[0].pid
    assert payload["exec_ack"] and payload["group_released"]
    assert scheduler.processes.children_released(tuple(scheduler.children)) is True
    assert "PRIVATE" not in caplog.text and str(tmp_path) not in caplog.text



def _script_budget():
    # Explicit unit fixture, not device calibration or permission to run a job.
    return MediaScriptBudget(512,256,2,1024,2,32,32,2048,4096,1024,1024,1,2,
                             2048,4,.1,4096,8192)


def _script_resources():
    return dict(kind="media_script",execution_mode="isolated_python_pillow",script_digest="a"*64,
                script_bytes=128,parameters_bytes=32,input_count=1,input_bytes=512,
                output_count=1,width_px=16,height_px=16,output_pixels=256,
                max_output_bytes=1024,max_stdout_bytes=100,max_stderr_bytes=100,threads=1,timeout_seconds=1)


@pytest.mark.parametrize("change", [{"script_bytes":513},{"max_stdout_bytes":1025},
    {"input_bytes":1025},{"width_px":33},{"output_pixels":2049},{"timeout_seconds":2.1}])
def test_script_excess_is_refused_before_host_sampling(tmp_path, monkeypatch, change):
    probe=HostResourceProbe(tmp_path/'db',disk_paths=(tmp_path,),
        budget=HostBudget(50,100,100,100,1000,1000,media_script=_script_budget()))
    monkeypatch.setattr(psutil,"cpu_percent",lambda **kwargs:pytest.fail('invalid declaration sampled host'))
    decision=probe._sample(_script_resources() | change)
    assert decision.verdict is False and decision.reason=='declared_resources_exceed_budget'
    assert '阶段输入超出' in str(HostResourceRefusal(decision))


@pytest.mark.parametrize("change", [{"execution_mode":"cpu_ffmpeg"},{"script_digest":"wrong"},
    {"script_bytes":True},{"timeout_seconds":float('nan')},{"output_count":0}])
def test_invalid_script_contract_is_unknown_before_host_sampling(tmp_path, monkeypatch, change):
    probe=HostResourceProbe(tmp_path/'db',disk_paths=(tmp_path,),
        budget=HostBudget(50,100,100,100,1000,1000,media_script=_script_budget()))
    monkeypatch.setattr(psutil,"cpu_percent",lambda **kwargs:pytest.fail('invalid declaration sampled host'))
    decision=probe._sample(_script_resources() | change)
    assert decision.verdict is None
    assert '隔离脚本' in str(HostResourceRefusal(decision))


@pytest.fixture
def pcm_runner_case(tmp_path):
    import hashlib
    from app.device_runtime import OwnerRegistration, OwnerRouter
    from app.host_resource_probes import CpuAudioBudget
    work = tmp_path / 'pcm' / 'narration.wav'
    work.parent.mkdir(mode=0o700)
    source = tmp_path / 'next.wav'
    for path, frames in ((work,1000),(source,100)):
        with wave.open(str(path),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000)
            stream.writeframes(b'\0\0'*frames)
    work.chmod(0o600)
    info, source_info = work.stat(),source.stat()
    receipt = work.parent / 'receipt.json'
    baseline = {'work_path':str(work),'receipt_path':str(receipt),
        'file_identity':{'device':info.st_dev,'inode':info.st_ino},'committed_pcm_bytes':2000,
        'committed_pcm_sha256':hashlib.sha256(b'\0\0'*1000).hexdigest(),
        'max_new_bytes':208,'header_bytes_allowance':8,'absolute_max_output_bytes':4096}
    sources = [{'path':str(source),'size_bytes':source_info.st_size,'mtime_ns':source_info.st_mtime_ns,
        'ctime_ns':source_info.st_ctime_ns,'pcm_frames':100}]
    inputs = [{'reference':str(path),'size_bytes':path.stat().st_size,'duration_seconds':frames/24000,
        'sample_rate':24000,'channels':1,'uses':1} for path,frames in ((work,1000),(source,100))]
    resources = {'kind':'task_audio','execution_mode':'cpu_pcm_wav_assembly','pcm_assembly':baseline,
        'pcm_sources':sources,'merge_final':True,'merge_source_digest':'a'*64,'input_policy':'fixed_pcm_header',
        'inputs':inputs,'input_count':2,'input_bytes':sum(item['size_bytes'] for item in inputs),
        'source_duration_limit_seconds':1.,'output_duration_limit_seconds':1.,'source_sample_rate_limit':24000,
        'source_channels_limit':1,'output_sample_rate_limit':24000,'output_channels_limit':1,
        'max_output_bytes':4096,'threads':1,'timeout_seconds':3.}
    audio = CpuAudioBudget(2,4096,1.,1.,24000,1,4096,1,3.,256*1024*1024,16,.05,256*1024*1024,4096)
    class Owner:
        def verify_submission(self,intent,actor):
            assert actor=='synthetic-member'
            return VerifiedWork(intent.workspace_id,intent.owner_type,intent.owner_id,intent.input_digest,
                intent.unit_key,actor,'normal_make',resources)
        def check_current(self,request,conn): return 'ready'
        def capture_local(self,request,conn): return lambda current:'ready'
        def check_local(self,request,conn,observed): return observed(conn)
        def pcm_assembly_baseline(self,request,conn): return dict(baseline)
    database = tmp_path/'production.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE material_ingestion_files (workspace_id TEXT NOT NULL,file_path TEXT NOT NULL,'
            'size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,ctime_ns INTEGER NOT NULL DEFAULT 0,'
            'inode INTEGER NOT NULL DEFAULT 0,observed_at REAL NOT NULL,completed_digest TEXT,attempted_digest TEXT,'
            'state TEXT NOT NULL,PRIMARY KEY(workspace_id,file_path))')
        apply_production_migrations(conn)
    probe = HostResourceProbe(database,disk_paths=(tmp_path,),budget=HostBudget(100,1,2**63,1,2**63,4096,.02,cpu_audio=audio))
    router = OwnerRouter({'creation_job':OwnerRegistration(Owner(),None)},None)
    scheduler = DeviceScheduler(database,tmp_path/'device.lock',owners=router,resources=probe,processes=HostProcessProbe())
    scheduler.submit(SubmissionIntent('workspace','creation_job','job','a'*64,'narration:merge:v1:'+('b'*64),SchedulePolicy()),'synthetic-member')
    grant = scheduler.pick_and_grant()
    assert grant is not None
    manifest = {'version':1,'operation':'append','unit_id':f'{grant.request_id}:{grant.generation}',
        'job_id':'job','source_digest':'a'*64,**baseline,'expected_file_identity':baseline['file_identity'],
        'sources':sources,'final':True}
    del manifest['file_identity'],manifest['header_bytes_allowance']
    manifest_path = Path(str(receipt)+'.manifest.json')
    manifest_path.write_text(json.dumps(manifest))
    argv = [sys.executable,str(Path(__file__).resolve().parents[1]/'server.py'),'--pcm-assembly-worker','--manifest',str(manifest_path)]
    outputs = (work,receipt,Path(str(work)+'.sealed.json'))
    try:
        yield scheduler,grant,argv,outputs,resources,audio
    finally:
        scheduler.close()


def test_pcm_runner_executes_one_real_grant_and_preserves_committed_credit(pcm_runner_case):
    scheduler,grant,argv,outputs,resources,_ = pcm_runner_case
    result = run_granted_command(scheduler,grant,argv,timeout_seconds=3,max_stdout_bytes=16384,max_stderr_bytes=16384,
        output_paths=outputs,max_output_file_bytes=4096,resource_purpose='task_audio')
    assert result.returncode==0,result.stderr
    receipt = json.loads(outputs[1].read_text())
    assert receipt['unit_id']==f'{grant.request_id}:{grant.generation}'
    assert receipt['generated_bytes']==208 and receipt['file_size']==2244
    assert receipt['file_identity']==resources['pcm_assembly']['file_identity']
    assert json.loads(outputs[2].read_text())==receipt
    scheduler.complete_unit(grant,checkpoint_database_path=scheduler.database_path,checkpoint_write=lambda conn,current:'verified')
    scheduler.assert_released(grant)


@pytest.mark.parametrize('kind',['pcm_generated_bytes','pcm_metadata_bytes'])
def test_pcm_runner_caps_do_not_charge_or_truncate_the_committed_prefix(pcm_runner_case,kind):
    from app.granted_subprocess import _pcm_assembly_monitor
    from app.host_resource_probes import PCM_METADATA_MAX_BYTES,audio_resource_refusal
    scheduler,grant,argv,outputs,resources,audio = pcm_runner_case
    assert audio_resource_refusal({**resources,'threads':2},audio)==(False,'declared_resources_exceed_budget')
    assert audio_resource_refusal({**resources,'input_bytes':244},audio) is not None
    check,_ = _pcm_assembly_monitor(scheduler,grant,argv,outputs,4096)
    target = outputs[0] if kind=='pcm_generated_bytes' else outputs[1]
    if kind=='pcm_generated_bytes':
        with target.open('ab') as stream: stream.write(b'\0'*202)
    else:
        target.write_bytes(b'x'*(PCM_METADATA_MAX_BYTES+1))
    length = target.stat().st_size
    with pytest.raises(GrantedCommandOutputLimit) as failed: check()
    assert failed.value.file_sample['budget_kind']==kind
    assert target.stat().st_size==length  # Unknown tails and receipts are never repaired by the runner.


def test_pcm_runner_does_not_accept_declared_credit_without_owner_proof(pcm_runner_case):
    from app.granted_subprocess import _pcm_assembly_monitor
    scheduler,grant,argv,outputs,resources,_ = pcm_runner_case
    forged = json.loads(json.dumps(resources))
    forged['pcm_assembly']['max_new_bytes']=1000
    with scheduler._connect() as conn:
        conn.execute('UPDATE device_work_requests SET resources_json=? WHERE id=?',(json.dumps(forged),grant.request_id))
    with pytest.raises(RecoveryBlocked,match='基线'):
        _pcm_assembly_monitor(scheduler,grant,argv,outputs,4096)
    assert outputs[0].stat().st_size==2044 and not outputs[1].exists()


def test_pcm_runner_accepts_only_the_fixed_metadata_publication_link(pcm_runner_case,monkeypatch):
    from app.granted_subprocess import _pcm_assembly_monitor
    scheduler,grant,argv,outputs,_,_ = pcm_runner_case
    check,_ = _pcm_assembly_monitor(scheduler,grant,argv,outputs,4096)
    receipt = outputs[1]
    pending = receipt.with_name('.'+receipt.name+'.pending')
    pending.write_text('{}')
    os.link(pending,receipt)
    check()  # The fixed worker is between link and unlink.
    fstat = os.fstat
    samples = 0
    def unlink_between_fd_and_path_samples(fd):
        nonlocal samples
        info = fstat(fd)
        if (info.st_dev,info.st_ino) == (receipt.stat().st_dev,receipt.stat().st_ino):
            samples += 1
            if samples == 2:
                pending.unlink()
        return info
    monkeypatch.setattr(os,'fstat',unlink_between_fd_and_path_samples)
    check()  # Unlink after after=fstat but before canonical=lstat.
    assert samples == 3 and receipt.stat().st_nlink == 1 and not pending.exists()
    monkeypatch.setattr(os,'fstat',fstat)
    check()
    alias = receipt.with_name('unrecognized-alias')
    os.link(receipt,alias)
    with pytest.raises(RecoveryBlocked,match='元数据'):
        check()
    assert alias.stat().st_ino == receipt.stat().st_ino


def test_missing_script_budget_is_not_an_audio_or_render_permit(tmp_path):
    probe=HostResourceProbe(tmp_path/'db',disk_paths=(tmp_path,),budget=HostBudget(50,100,100,100,1000,1000))
    assert probe._sample(_script_resources()).verdict is None
    probe._grant_resources=lambda grant:_script_resources()
    with pytest.raises(RecoveryBlocked,match='脚本进程预算尚未标定'):
        probe.scene_runtime_limits(SimpleNamespace())


@pytest.mark.parametrize('pressure',['none','cpu','memory','swap','disk','io'])
def test_script_keeps_host_and_runtime_envelope(tmp_path, monkeypatch, pressure):
    from itertools import count
    probe=HostResourceProbe(tmp_path/'db',disk_paths=(tmp_path,),
        budget=HostBudget(50,100,100,100,1000,1000,media_script=_script_budget()))
    ticks=count(0,1.7)
    monkeypatch.setattr('app.host_resource_probes.time.monotonic',lambda:next(ticks))
    values=count(0,100000 if pressure=='io' else 0)
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:
        {'disk0':SimpleNamespace(read_bytes=next(values),write_bytes=0)})
    monkeypatch.setattr(psutil,'disk_partitions',lambda **kwargs:[])
    monkeypatch.setattr(psutil,'cpu_percent',lambda **kwargs:99 if pressure=='cpu' else 1)
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=1 if pressure=='memory' else 100000))
    monkeypatch.setattr(psutil,'swap_memory',lambda:SimpleNamespace(used=101 if pressure=='swap' else 0))
    monkeypatch.setattr(psutil,'disk_usage',lambda path:SimpleNamespace(free=1 if pressure=='disk' else 100000))
    decision=probe._sample(_script_resources(),running_io=True)
    assert decision.verdict is (True if pressure=='none' else False)
    assert decision.checks['available_memory_bytes']['minimum']==100
    assert decision.checks[f'disk_free_bytes:{tmp_path}']['minimum']==100+8192+1024
    probe._grant_resources=lambda grant:_script_resources()
    limits=probe.scene_runtime_limits(SimpleNamespace())
    assert (limits.max_process_rss_bytes,limits.max_process_threads,limits.sample_seconds)==(2048,4,.1)
    assert probe.released(SimpleNamespace()) is True  # Scheduler's group proof remains separate.


def test_script_reserves_peak_at_start_and_keeps_system_floor_while_running(tmp_path,monkeypatch):
    from itertools import count
    probe=HostResourceProbe(tmp_path/'db',disk_paths=(tmp_path,),
        budget=HostBudget(50,100,100,100,1000,1000,media_script=_script_budget()))
    ticks=count(0,1.7)
    monkeypatch.setattr('app.host_resource_probes.time.monotonic',lambda:next(ticks))
    monkeypatch.setattr(psutil,'disk_io_counters',lambda **kwargs:
        {'disk0':SimpleNamespace(read_bytes=0,write_bytes=0)})
    monkeypatch.setattr(psutil,'disk_partitions',lambda **kwargs:[])
    monkeypatch.setattr(psutil,'cpu_percent',lambda **kwargs:1)
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=200))
    monkeypatch.setattr(psutil,'swap_memory',lambda:SimpleNamespace(used=0))
    monkeypatch.setattr(psutil,'disk_usage',lambda path:SimpleNamespace(free=100000))
    start=probe._sample(_script_resources())
    assert start.verdict is False
    assert start.checks['available_memory_bytes']['minimum']==100+4096
    running=probe._sample(_script_resources(),running_io=True)
    assert running.verdict is True
    assert running.checks['available_memory_bytes']['minimum']==100
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=99))
    assert probe._sample(_script_resources(),running_io=True).verdict is False
