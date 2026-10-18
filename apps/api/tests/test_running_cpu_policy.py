"""Narration CPU experiment: cumulative endpoints, no model or production profile."""
from dataclasses import asdict, replace
import json
import sys
from types import SimpleNamespace

import pytest

from app.device_scheduler import Grant
from app.host_memory import MemoryDecision, MemorySample
from app.host_resource_probes import (
    HostBudget, HostResourceDecision, HostResourceProbe, HostResourceRefusal, NarrationBudget, RuntimeCpuPolicy,
    _RunningCpuWindow,
)
from app.member_permits import HostBudgetUncalibrated, build_host_probes, creation_stage_budgets_from_env


POLICY = RuntimeCpuPolicy(1.0, 2.0, "synthetic-test-evidence")


def test_cpu_refusal_keeps_fixed_readable_reason_with_other_failed_checks():
    checks = {"cpu_percent": {"actual": 95, "maximum": 91, "passed": False}}
    for reason, message in (("cpu_policy_unavailable", "配音持续CPU策略尚未准备"),
                            ("cpu_admission_missing", "当前许可没有匹配的有效CPU准入端点"),
                            ("cpu_sustained_overload", "完整运行窗口的CPU占用超出原阈值")):
        assert message in str(HostResourceRefusal(HostResourceDecision(False, {}, checks, reason)))


def endpoint(now, busy=0, idle=0, **changes):
    return {"user": busy, "system": 0.0, "nice": 0.0, "idle": idle,
        "logical_cpu_count": 8, "observed_monotonic": now,
        "read_started_monotonic": now, "read_seconds": 0.0, **changes}


def test_complete_cumulative_window_distinguishes_short_peak_and_sustained_load():
    window = _RunningCpuWindow(POLICY, endpoint(0))
    passed, reason, initial = window.observe(endpoint(.1, .8), 91)
    assert passed and reason is None and initial["state"] == "collecting"
    passed, reason, whole = window.observe(endpoint(1.05, 3.52, 5.28), 91)
    assert passed and whole["actual"] == pytest.approx(40) and whole["window_seconds"] == 1.05
    passed, reason, overloaded = window.observe(endpoint(2.15, 12.32, 5.28), 91)
    assert not passed and reason == "cpu_sustained_overload" and overloaded["actual"] == pytest.approx(100)


def test_zero_counters_accumulate_without_renewing_initial_permission():
    window = _RunningCpuWindow(POLICY, endpoint(0))
    for now in (.5, 1.5, 2.5):
        passed, _, check = window.observe(endpoint(now), 91)
        assert passed and check["state"] == "collecting" and check["actual"] is None
        assert check["valid_until_monotonic"] == 3.0
    passed, reason, check = window.observe(endpoint(3.0), 91)
    assert passed is None and reason == "cpu_counters_not_advancing"
    assert window.observe(endpoint(3.1, 1, 10), 91)[0] is None  # Cannot re-arm by later counts.


def test_zero_counters_after_valid_window_never_become_zero_percent_or_extend_permission():
    window = _RunningCpuWindow(POLICY, endpoint(0))
    assert window.observe(endpoint(1, 1, 7), 91)[0] is True
    for now in (1.5, 2.5):
        passed, _, check = window.observe(endpoint(now, 1, 7), 91)
        assert passed and check["actual"] is None and check["valid_until_monotonic"] == 3
    assert window.observe(endpoint(3, 1, 7), 91)[:2] == (None, "cpu_counters_not_advancing")


@pytest.mark.parametrize("sample,reason", [
    (endpoint(.5, -1, 1), "cpu_counters_regressed"),
    (endpoint(-.1), "cpu_counters_regressed"),
    (endpoint(2.01, 1, 7), "cpu_observation_gap"),
    (endpoint(.5, 1, 7, logical_cpu_count=4), "cpu_identity_changed"),
    (endpoint(.5, 1, 7, read_seconds=2.01), "cpu_observation_gap"),
])
def test_invalid_window_latches_unknown(sample, reason):
    window = _RunningCpuWindow(POLICY, endpoint(0))
    assert window.observe(sample, 91)[:2] == (None, reason)
    assert window.observe(endpoint(3, 1, 7), 91)[:2] == (None, reason)


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
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def cpu_percent(**kwargs):
        clock[0] += kwargs["interval"]
        return instant_cpu[0]
    monkeypatch.setattr(module.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(module, "_cpu_counter", lambda: endpoint(clock[0], clock[0] * 2, clock[0] * 6))
    monkeypatch.setattr(module.psutil, "disk_io_counters", lambda **kwargs: {"disk0": SimpleNamespace(read_bytes=0, write_bytes=0)})
    monkeypatch.setattr(module.psutil, "disk_usage", lambda path: SimpleNamespace(free=10**9))
    monkeypatch.setattr(value, "_io_evidence", lambda *args: ({"actual": 0 if io_passed[0] else 10**10,
        "maximum": 10**9, "passed": io_passed[0], "window_complete": True}, None))
    resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts", "text_chars": 17,
        "section_count": 1, "reference_bytes": 1024, "duration_seconds": 15,
        "max_output_bytes": 1024, "threads": 2, "timeout_seconds": 120}
    request = SimpleNamespace(id="request", workspace_id="workspace", owner_type="media_preview", owner_id="preview",
        unit_key="voice:digest", input_digest="a" * 64, attempt_number=1, last_grant_generation=None, resources=resources)
    grant = Grant(request.id, request.workspace_id, request.owner_type, request.owner_id, request.unit_key,
        request.input_digest, 1, 1, "PRIVATE_TOKEN", value.database_path)
    monkeypatch.setattr(value, "_grant_resources", lambda grant: resources)
    return SimpleNamespace(value=value, clock=clock, instant=instant_cpu, memory=memory, io=io_passed,
                           request=request, grant=grant, resources=resources)


def test_admission_gate_stays_instantaneous_but_running_uses_seeded_window(probe, caplog):
    probe.instant[0] = 100
    assert probe.value.admit(probe.request).verdict is False
    with pytest.raises(HostResourceRefusal, match="准入端点"):
        probe.value.continue_unit(probe.grant)
    probe.instant[0] = 40
    assert probe.value.admit(probe.request).verdict is True
    probe.instant[0] = 100
    assert probe.value.continue_unit(probe.grant)
    probe.clock[0] += 1
    assert probe.value.continue_unit(probe.grant)
    observations = [json.loads(record.args[0]) for record in caplog.records if record.msg == "Runtime CPU observation: %s"]
    assert observations[0]["cpu"]["state"] == "collecting"
    assert observations[-1]["cpu"]["actual"] == pytest.approx(25)
    assert observations[-1]["cpu"]["sample_gap_seconds"] == pytest.approx(1.1)
    assert observations[-1]["permit_generation"] == 1 and "PRIVATE_TOKEN" not in caplog.text


@pytest.mark.parametrize("problem", ["missing", "stale", "generation", "gap", "counter_missing"])
def test_missing_stale_or_reused_seed_cannot_start_or_rearm(probe, monkeypatch, problem):
    import app.host_resource_probes as module
    if problem != "missing":
        assert probe.value.admit(probe.request).verdict is True
    if problem == "stale":
        probe.clock[0] += 2.1
    elif problem == "generation":
        probe.value.continue_unit(probe.grant)
        probe.grant = replace(probe.grant, generation=2)
    elif problem == "gap":
        probe.value.continue_unit(probe.grant)
        probe.clock[0] += 2.1
    elif problem == "counter_missing":
        monkeypatch.setattr(module, "_cpu_counter", lambda: (_ for _ in ()).throw(OSError("unavailable")))
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.value.continue_unit(probe.grant)
    assert stopped.value.decision.verdict is None


@pytest.mark.parametrize("gate", ["memory", "io"])
def test_other_protections_do_not_wait_for_first_cpu_window(probe, gate):
    assert probe.value.admit(probe.request).verdict is True
    if gate == "memory":
        probe.memory[0] = "critical"
    else:
        probe.io[0] = False
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.value.continue_unit(probe.grant)
    assert stopped.value.decision.verdict is False
    if gate == "memory":
        assert stopped.value.decision.reason == "memory_pressure_critical"


def test_absent_running_policy_is_unavailable_without_old_cpu_fallback(probe):
    probe.value.budget = replace(probe.value.budget, narration=narration(None))
    assert probe.value.admit(probe.request).reason == "cpu_policy_unavailable"
    with pytest.raises(HostResourceRefusal) as stopped:
        probe.value.continue_unit(probe.grant)
    assert stopped.value.decision.reason == "cpu_policy_unavailable"


def test_policy_parser_and_driver_cadence_require_explicit_experiment(tmp_path):
    values = asdict(narration())
    values.pop("running_cpu_policy")
    environment = {"DEVICE_NARRATION_" + key.upper(): str(value) for key, value in values.items()}
    result, _ = creation_stage_budgets_from_env(environment)
    assert result.running_cpu_policy is None
    environment["DEVICE_NARRATION_RUNNING_CPU_POLICY_JSON"] = json.dumps(asdict(POLICY))
    result, _ = creation_stage_budgets_from_env(environment)
    assert result.running_cpu_policy == POLICY
    budget = HostBudget(91, 100, None, 100, 10**9, 10**9, narration=result)
    resources, _ = build_host_probes(database_path=tmp_path / "db", disk_paths=(tmp_path,), budget=budget,
        device_poll_interval_seconds=1)
    assert resources.budget.narration.running_cpu_policy == POLICY
    with pytest.raises(HostBudgetUncalibrated, match="driver"):
        build_host_probes(database_path=tmp_path / "db", disk_paths=(tmp_path,), budget=budget,
            device_poll_interval_seconds=2)
    environment["DEVICE_NARRATION_RUNNING_CPU_POLICY_JSON"] = '{"window_seconds":0}'
    with pytest.raises(HostBudgetUncalibrated):
        creation_stage_budgets_from_env(environment)


def test_light_child_cpu_diagnostics_are_bound_to_real_group_identity(tmp_path, monkeypatch, caplog):
    from app.granted_subprocess import run_granted_command
    from app.host_resource_probes import HostProcessProbe, SceneRuntimeLimits
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    grant = Grant("request", "workspace", "creation_job", "job", "narration:digest",
        "a" * 64, 1, 7, "PRIVATE_TOKEN", tmp_path / "test.sqlite")
    class Scheduler:
        processes = HostProcessProbe()
        resources = HostResourceProbe(grant.database_path, disk_paths=(tmp_path,),
            budget=HostBudget(100, 1, None, 1, 10**12, 10**9))
        def assert_grant(self, current):
            assert current == grant
        def register_child(self, current, child):
            self.child = child
    scheduler = Scheduler()
    monkeypatch.setattr(scheduler.resources, "scene_runtime_limits", lambda current:
        SceneRuntimeLimits(1024**3, 64, 1, None, .1, IsolatedMemoryMonitor(), collect_cpu=True))
    result = run_granted_command(scheduler, grant, [sys.executable, "-c", "import time; time.sleep(1.2)"],
        timeout_seconds=3, max_stdout_bytes=64, max_stderr_bytes=64)
    assert result.returncode == 0
    records = [json.loads(record.args[0]) for record in caplog.records if record.msg == "Granted process CPU observation: %s"]
    assert len(records) >= 2 and records[-1]["permit_generation"] == 7
    sample = records[-1]["process_cpu"]
    assert sample["interval_seconds"] >= 1 and sample["same_identity_cpu_seconds_delta"] >= 0
    assert sample["end"]["members"][0]["pid"] == scheduler.child.pid
    assert not sample["membership_changed"] and "PRIVATE_TOKEN" not in caplog.text
