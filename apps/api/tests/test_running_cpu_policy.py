"""OS background scheduling and diagnostic CPU counters; never load a model."""
from dataclasses import asdict, replace
import json
import shutil
import sys
from types import SimpleNamespace

import psutil
import pytest

from app.device_scheduler import Grant
from app.host_memory import MemoryDecision, MemorySample
from app.host_resource_probes import (
    HostBudget, HostResourceProbe, HostResourceRefusal, NarrationBudget, RuntimeCpuPolicy,
)
from app.member_permits import HostBudgetUncalibrated, build_host_probes, creation_stage_budgets_from_env


POLICY = RuntimeCpuPolicy("darwin_background", "synthetic-test-evidence")


def endpoint(now, busy=0, idle=0, **changes):
    return {"user": busy, "system": 0., "nice": 0., "idle": idle,
        "logical_cpu_count": 8, "observed_monotonic": now,
        "read_started_monotonic": now, "read_seconds": 0., **changes}


def narration(policy=POLICY):
    return NarrationBudget(80, 1, 1024, 15, 1024, 2, 120, 1024, 64, .1, 1024, 1024, policy)


@pytest.fixture
def probe(tmp_path, monkeypatch):
    import app.host_resource_probes as module
    clock, instant_cpu, memory, io_passed = [10.0], [40.0], ["normal"], [True]
    class Monitor:
        def check(self, *, system_reserve_bytes, additional_memory_bytes=0, additional_commit_bytes=None):
            sample = MemorySample("darwin", clock[0], "synthetic", memory[0], 10**9, None, {})
            return MemoryDecision(memory[0] != "critical", "memory_pressure_critical" if memory[0] == "critical" else None,
                sample, system_reserve_bytes + additional_memory_bytes, None, 10)
    value = HostResourceProbe(tmp_path / "test.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(91, 100, None, 100, 10**9, 10**9, narration=narration()), memory_monitor=Monitor())
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def cpu_percent(**kwargs):
        clock[0] += kwargs["interval"]
        return instant_cpu[0]
    monkeypatch.setattr(module.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(module, "_cpu_counter", lambda: endpoint(clock[0], clock[0] * 8))
    monkeypatch.setattr(module.psutil, "disk_io_counters", lambda **kwargs: {"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)})
    monkeypatch.setattr(module.psutil, "disk_usage", lambda path: SimpleNamespace(free=10**9))
    monkeypatch.setattr(value, "_io_evidence", lambda *args: ({"actual": 0 if io_passed[0] else 10**10,
        "maximum": 10**9, "passed": io_passed[0], "window_complete": True}, None))
    resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts", "text_chars": 17,
        "section_count": 1, "reference_bytes": 1024, "duration_seconds": 15,
        "max_output_bytes": 1024, "threads": 2, "timeout_seconds": 120}
    request = SimpleNamespace(id="request", workspace_id="workspace", owner_type="media_preview", owner_id="preview",
        unit_key="voice:digest", input_digest="a" * 64, attempt_number=1, resources=resources)
    grant = Grant(request.id, request.workspace_id, request.owner_type, request.owner_id, request.unit_key,
        request.input_digest, 1, 1, "PRIVATE_TOKEN", value.database_path)
    monkeypatch.setattr(value, "_grant_resources", lambda grant: resources)
    return SimpleNamespace(value=value, clock=clock, instant=instant_cpu, memory=memory, io=io_passed,
                           request=request, grant=grant, resources=resources)


def test_91_only_limits_admission_while_full_cpu_is_runtime_diagnostic(probe, monkeypatch, caplog):
    import app.host_resource_probes as module
    probe.instant[0] = 100
    assert probe.value.admit(probe.request).verdict is False
    probe.instant[0] = 91
    assert probe.value.admit(probe.request).verdict is True
    monkeypatch.setattr(module.psutil, "cpu_percent", lambda **_: pytest.fail("Runtime used admission CPU veto"))
    assert probe.value.continue_unit(probe.grant)
    probe.clock[0] += 20  # No CPU seed, finite CPU window or expiry controls the grant.
    assert probe.value.continue_unit(probe.grant)
    records = [json.loads(record.args[0]) for record in caplog.records if record.msg == "Runtime CPU observation: %s"]
    assert records[0]["cpu"]["state"] == "unknown" and records[0]["cpu"]["actual"] is None
    assert records[-1]["cpu"]["actual"] == 100 and records[-1]["cpu"]["diagnostic_only"]
    assert records[-1]["cpu"]["interval_seconds"] == pytest.approx(20.1)
    assert records[-1]["permit_generation"] == 1 and "PRIVATE_TOKEN" not in caplog.text


@pytest.mark.parametrize("problem", ["unavailable", "stalled", "regressed", "cores", "generation"])
def test_cpu_unknown_never_becomes_idle_or_unknown_os_background_policy(probe, monkeypatch, caplog, problem):
    import app.host_resource_probes as module
    assert probe.value.continue_unit(probe.grant)
    previous = probe.value._running_cpu
    if problem == "unavailable":
        monkeypatch.setattr(module, "_cpu_counter", lambda: (_ for _ in ()).throw(OSError("unavailable")))
    elif problem == "generation":
        probe.grant = replace(probe.grant, generation=2)
    else:
        changes = {"user": previous["user"] - 1} if problem == "regressed" else (
            {"logical_cpu_count": 4} if problem == "cores" else {})
        monkeypatch.setattr(module, "_cpu_counter", lambda: {**previous,
            "observed_monotonic": probe.clock[0], **changes})
    assert probe.value.continue_unit(probe.grant)
    record = [json.loads(r.args[0]) for r in caplog.records if r.msg == "Runtime CPU observation: %s"][-1]
    assert record["cpu"]["state"] == "unknown" and record["cpu"]["actual"] is None


@pytest.mark.parametrize("gate", ["memory", "io"])
def test_other_protections_still_refuse_during_background_runtime(probe, gate):
    if gate == "memory":
        probe.memory[0] = "critical"
    else:
        probe.io[0] = False
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.value.continue_unit(probe.grant)
    assert stopped.value.decision.verdict is False
    assert stopped.value.recoverable_pressure
    if gate == "memory":
        assert stopped.value.decision.reason == "memory_pressure_critical"


def test_absent_policy_or_unsupported_platform_is_explicit(probe, monkeypatch):
    import app.host_resource_probes as module
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    assert probe.value.admit(probe.request).reason == "cpu_policy_platform_unsupported"
    probe.value.budget = replace(probe.value.budget, narration=narration(None))
    assert probe.value.admit(probe.request).reason == "cpu_policy_unavailable"
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.value.continue_unit(probe.grant)
    assert stopped.value.decision.reason == "cpu_policy_unavailable"


def test_policy_parser_requires_os_mechanism_and_rejects_old_window_schema(tmp_path):
    values = asdict(narration())
    values.pop("running_cpu_policy")
    environment = {"DEVICE_NARRATION_" + key.upper(): str(value) for key, value in values.items()}
    result, _ = creation_stage_budgets_from_env(environment)
    assert result.running_cpu_policy is None
    environment["DEVICE_NARRATION_RUNNING_CPU_POLICY_JSON"] = json.dumps(asdict(POLICY))
    result, _ = creation_stage_budgets_from_env(environment)
    assert result.running_cpu_policy == POLICY
    resources, _ = build_host_probes(database_path=tmp_path / "db", disk_paths=(tmp_path,),
        budget=HostBudget(91, 100, None, 100, 10**9, 10**9, narration=result), device_poll_interval_seconds=2)
    assert resources.budget.narration.running_cpu_policy == POLICY
    for invalid in ({"window_seconds": 1, "max_sample_gap_seconds": 2, "evidence_reference": "old"},
                    {"mode": "darwin_background", "evidence_reference": ""},
                    {"mode": "unverified", "evidence_reference": "other"}):
        environment["DEVICE_NARRATION_RUNNING_CPU_POLICY_JSON"] = json.dumps(invalid)
        with pytest.raises(HostBudgetUncalibrated):
            creation_stage_budgets_from_env(environment)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin background scheduling requires macOS")
@pytest.mark.parametrize("command", ["descendant", "ffmpeg"])
def test_real_narration_broker_sets_background_for_model_and_media_commands(tmp_path, monkeypatch, caplog, command):
    from app.granted_subprocess import run_granted_command
    from app.host_resource_probes import HostProcessProbe
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    grant = Grant("request", "workspace", "creation_job" if command == "descendant" else "media_preview",
        "owner", "narration:digest" if command == "descendant" else "voice:digest",
        "a" * 64, 1, 7, "PRIVATE_TOKEN", tmp_path / "test.sqlite")
    budget = replace(narration(), max_process_rss_bytes=1024**3, additional_memory_bytes=1024**3)
    class Scheduler:
        processes = HostProcessProbe()
        resources = HostResourceProbe(grant.database_path, disk_paths=(tmp_path,),
            budget=HostBudget(91, 1, None, 1, 10**12, 10**9, narration=budget),
            memory_monitor=IsolatedMemoryMonitor())
        def assert_grant(self, current):
            assert current == grant
        def register_child(self, current, child):
            self.child = child
    scheduler = Scheduler()
    monkeypatch.setattr(scheduler.resources, "_grant_resources", lambda _: {"kind": "creation_narration"})
    if command == "descendant":
        readback = "import ctypes,json,os; c=ctypes.CDLL(None,use_errno=True); ctypes.set_errno(0); b=c.getpriority(4,0); print(json.dumps({'background':b,'errno':ctypes.get_errno(),'pid':os.getpid(),'pgid':os.getpgrp()}))"
        code = "import subprocess,sys,time; " + readback + "; subprocess.run([sys.executable,'-c'," + repr(readback) + "],check=True); time.sleep(1.1)"
        argv = [sys.executable, "-c", code]
    else:
        executable = shutil.which("ffmpeg")
        if executable is None:
            pytest.skip("ffmpeg unavailable")
        argv = [executable, "-version"]
    result = run_granted_command(scheduler, grant, argv, timeout_seconds=4,
        max_stdout_bytes=8192, max_stderr_bytes=1024)
    assert result.returncode == 0
    assert b"GRANT_CPU_POLICY mode=darwin_background state=verified background=1 errno=0" in result.stderr
    assert scheduler.processes.children_released((scheduler.child,)) is True
    if command == "descendant":
        observations = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(observations) == 2
        assert all(row["background"] == 1 and row["errno"] == 0 and row["pgid"] == scheduler.child.pid for row in observations)
        assert scheduler.child.pid in {row["pid"] for row in observations}
        samples = [json.loads(r.args[0]) for r in caplog.records if r.msg == "Granted process CPU observation: %s"]
        assert samples[-1]["process_cpu"]["interval_seconds"] >= 1
    diagnostic = [json.loads(r.args[0]) for r in caplog.records if r.msg == "Granted command diagnostic: %s"][-1]
    assert diagnostic["permit_generation"] == 7 and diagnostic["cpu_policy_markers"]
    assert "PRIVATE_TOKEN" not in caplog.text


def test_process_cpu_counter_failure_does_not_hide_memory_or_thread_protection(tmp_path, monkeypatch):
    from app.granted_subprocess import _check_scene_runtime, GrantedCommandResourceLimit
    from app.host_resource_probes import SceneRuntimeLimits
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    child = SimpleNamespace(pid=999, start_token="1.000000")
    process = SimpleNamespace(pid=999, poll=lambda: None)
    current = SimpleNamespace(pid=999, create_time=lambda: 1., memory_info=lambda: SimpleNamespace(rss=10),
        num_threads=lambda: 2, cpu_times=lambda: (_ for _ in ()).throw(psutil.AccessDenied(999)))
    monkeypatch.setattr(psutil, "Process", lambda _: current)
    monkeypatch.setattr(psutil, "process_iter", lambda: [current])
    monkeypatch.setattr("app.granted_subprocess.os.getpgid", lambda _: 999)
    limits = SceneRuntimeLimits(100, 4, 1, None, .1, IsolatedMemoryMonitor(), collect_cpu=True)
    sample = _check_scene_runtime(process, child, limits)
    assert sample["state"] == "unknown" and sample["members"] == []
    with pytest.raises(GrantedCommandResourceLimit):
        _check_scene_runtime(process, child, replace(limits, max_process_threads=1))
