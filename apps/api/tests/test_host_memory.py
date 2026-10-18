"""Native-pressure semantics and unified recovery counterexamples, without applying load."""
import ctypes
from dataclasses import asdict
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app import host_memory as memory
from app.granted_subprocess import GrantedCommandMemoryPressure, GrantedCommandResourceUnavailable, run_granted_command
from app.host_memory import HostMemoryMonitor, MemoryDecision, MemoryPolicy, MemorySample, validate_observation_cadence
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, HostResourceRefusal, SceneRuntimeLimits
from app.member_permits import build_host_probes, host_budget_from_env, memory_policy_from_env, HostBudgetUncalibrated
from tests.test_member_permits import _HOST_ENV


class World:
    def __init__(self, platform="darwin"):
        self.now, self.pressure, self.available, self.commit, self.swap = 0., "normal", 1000, 1000, 0
        self.platform = platform
        self.policy = MemoryPolicy(platform, 5., 2., "synthetic://policy-not-production", 100 if platform == "win32" else None)
        self.monitor = HostMemoryMonitor(self.policy, sampler=self.sample, clock=lambda: self.now)

    def sample(self):
        return MemorySample(self.platform, self.now, "synthetic", self.pressure, self.available,
            self.commit if self.platform == "win32" else None,
            {"pressure": {"value": self.pressure, "source": "synthetic-native-boundary", "unit": "state"},
             "swap_used_bytes": {"value": self.swap, "unit": "bytes", "source": "synthetic", "diagnostic_only": True}})

    def check(self, additional=0):
        return self.monitor.check(system_reserve_bytes=100, additional_memory_bytes=additional, additional_commit_bytes=additional)

    def stabilize(self, additional=0):
        for _ in range(6):
            result = self.check(additional)
            self.now += 1
        return result


@pytest.mark.parametrize("pressure", ["normal", "warning"])
def test_sufficient_stable_memory_with_large_swap_is_not_rejected(pressure):
    world = World()
    world.swap = 2**50
    world.pressure = pressure
    assert world.stabilize(additional=500).verdict is True
    world.now += 1
    assert world.check().verdict is True


def test_low_swap_is_not_safety_when_headroom_or_os_pressure_is_bad():
    world = World()
    assert world.stabilize().verdict is True
    world.swap, world.available = 0, 99
    assert world.check().reason == "memory_headroom_insufficient"
    world.available, world.pressure = 1000, "critical"
    assert world.check().reason == "memory_pressure_critical"


def test_critical_is_immediate_and_recovery_requires_continuous_window():
    world = World()
    assert world.stabilize().verdict is True
    world.pressure = "critical"
    assert world.check().verdict is False
    world.now += 1
    world.pressure = "warning"
    assert world.check().reason == "memory_recovery_stabilizing"
    world.now += 1
    assert world.check().verdict is False
    world.now += 3  # Missing observations reset the window, even when memory looks normal.
    assert world.check().stable_elapsed_seconds == 0
    assert world.stabilize().verdict is True


def test_startup_increment_is_not_reserved_again_after_allocation():
    world = World()
    assert world.stabilize(additional=500).verdict is True
    world.available = 150
    assert world.check().verdict is True  # Running reserve is 100, not 100+500 again.
    assert world.check(additional=500).reason == "memory_task_budget_insufficient"


def test_unaffordable_large_task_cannot_reset_small_tasks_global_recovery():
    world = World()
    world.available = 3000
    for _ in range(7):
        large = world.monitor.check(system_reserve_bytes=1000, additional_memory_bytes=9000)
        small = world.monitor.check(system_reserve_bytes=1000)
        world.now += 1.1  # Production-shaped 1s poll + 0.1s probe, not a tight loop.
    assert small.verdict is True
    assert large.verdict is False and large.reason == "memory_task_budget_insufficient"


def test_profile_must_cover_real_poll_plus_probe_before_runtime_binding(tmp_path):
    policy = World().policy
    validate_observation_cadence(policy, device_poll_interval_seconds=1., probe_seconds=.1)
    budget = host_budget_from_env(_HOST_ENV, memory_policy=policy)
    with pytest.raises(HostBudgetUncalibrated, match="观测节奏"):
        build_host_probes(database_path=tmp_path / "test.sqlite", disk_paths=(tmp_path,), budget=budget)
    bad = MemoryPolicy("darwin", 5., .25, "synthetic://too-short-for-production-poll")
    with pytest.raises(ValueError, match="poll"):
        validate_observation_cadence(bad, device_poll_interval_seconds=1., probe_seconds=.1)


@pytest.mark.parametrize("pressure", ["normal", "warning"])
def test_realistic_poll_cadence_can_complete_recovery_without_tight_sampling(pressure):
    world = World()
    world.pressure = pressure
    for _ in range(7):
        decision = world.check()
        world.now += 1.2  # 1s poll + probe and overhead, an explicit synthetic timing trace.
    assert decision.verdict is True and decision.stable_elapsed_seconds >= 5


def test_large_windows_commit_demand_does_not_reset_small_tasks_recovery():
    world = World("win32")
    world.commit = 3000
    for _ in range(7):
        large = world.monitor.check(system_reserve_bytes=100, additional_commit_bytes=9000)
        small = world.monitor.check(system_reserve_bytes=100, additional_commit_bytes=0)
        world.now += 1.2
    assert large.reason == "memory_task_commit_budget_insufficient"
    assert small.verdict is True


def test_unknown_does_not_become_zero_or_normal_and_resets_recovery():
    world = World()
    world.stabilize()
    world.pressure = "unknown"
    assert world.check().verdict is None
    world.pressure = "normal"
    assert world.check().reason == "memory_recovery_stabilizing"
    world.available = None
    assert world.check().verdict is None
    world.monitor.policy = None
    world.available = 1000
    assert world.check().reason == "memory_policy_unavailable"


def test_sampling_and_window_writes_are_serialized_across_background_and_live_checks():
    world = World()
    first_entered, release_first, second_entered = (threading.Event() for _ in range(3))
    calls = []
    def sample():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        world.now += 1
        return world.sample()
    world.monitor.sampler = sample
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(world.check)
        assert first_entered.wait(1)
        second = pool.submit(world.check)
        try:
            assert not second_entered.wait(.05)  # The second sampler cannot overtake a blocked first read.
        finally:
            release_first.set()
        before, after = first.result(timeout=1), second.result(timeout=1)
    assert before.sample.observed_monotonic == 1 and after.sample.observed_monotonic == 2
    assert after.stable_elapsed_seconds == 1


@pytest.mark.parametrize("failure", ["critical", "unknown", "sampler_exception"])
def test_background_failure_resets_healthy_window_and_keeps_observing(failure):
    world = World()
    assert world.stabilize().verdict is True
    sampled = threading.Event()
    def failed_sample():
        sampled.set()
        if failure == "sampler_exception":
            raise OSError("synthetic native sampler failure")
        world.pressure = failure
        return world.sample()
    world.monitor.sampler = failed_sample
    world.monitor.start_observing(system_reserve_bytes=100, interval_seconds=.01)
    try:
        assert sampled.wait(1) and world.monitor.observing
        stopped = world.check()
        assert stopped.reason == ("memory_pressure_critical" if failure == "critical" else "memory_measurement_unavailable")
        assert stopped.stable_elapsed_seconds == 0
        if failure == "sampler_exception":
            assert stopped.sample.available_bytes is None
    finally:
        assert world.monitor.stop_observing(1)
    world.monitor.sampler = world.sample
    world.now += 1
    world.pressure = "normal"
    assert world.check().reason == "memory_recovery_stabilizing"
    assert not world.monitor.observing


def test_windows_commit_is_distinct_from_available_physical_memory():
    world = World("win32")
    world.commit = 150
    assert world.check(additional=100).reason == "memory_task_commit_budget_insufficient"
    assert world.stabilize().verdict is True
    world.commit = None
    assert world.check().reason == "memory_commit_measurement_unavailable"


def test_windows_does_not_infer_commit_demand_from_physical_peak():
    world = World("win32")
    assert world.monitor.check(system_reserve_bytes=100, additional_memory_bytes=400).reason == "memory_commit_budget_unavailable"
    world.commit = 200
    result = world.monitor.check(system_reserve_bytes=100, additional_memory_bytes=400, additional_commit_bytes=150)
    assert result.reason == "memory_task_commit_budget_insufficient"
    assert result.required_commit_headroom_bytes == 250


def test_stale_and_wrong_platform_facts_cannot_pass():
    world = World()
    sample = world.sample()
    world.monitor.sampler = lambda: sample
    world.now = 3
    assert world.check().reason == "memory_observation_stale"
    world.monitor.sampler = lambda: MemorySample("win32", 3., "fixture", "normal", 1000, 1000, {})
    assert world.check().reason == "memory_measurement_unavailable"


@pytest.mark.parametrize("raw", [None, "", "not-a-number", "-1", "1", str(2**50)])
def test_old_swap_configuration_is_optional_diagnostic_only(raw):
    env = {name: value for name, value in _HOST_ENV.items() if name != "DEVICE_HOST_MAX_SWAP_USED_BYTES"}
    if raw is not None:
        env["DEVICE_HOST_MAX_SWAP_USED_BYTES"] = raw
    budget = host_budget_from_env(env)
    assert budget.min_available_memory_bytes == int(_HOST_ENV["DEVICE_HOST_MIN_AVAILABLE_MEMORY_BYTES"])
    assert budget.memory_policy is None


def test_memory_policy_has_no_guessed_production_window():
    assert memory_policy_from_env({}) is None
    policy = World().policy
    assert memory_policy_from_env({"DEVICE_HOST_MEMORY_POLICY_JSON": json.dumps(asdict(policy))}) == policy
    for config in ("{}", "null", json.dumps(asdict(policy) | {"recovery_stable_seconds": 0}),
                   json.dumps(asdict(policy) | {"evidence_reference": ""})):
        with pytest.raises(HostBudgetUncalibrated):
            memory_policy_from_env({"DEVICE_HOST_MEMORY_POLICY_JSON": config})


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback
    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("raw,expected", [(0, "unknown"), (1, "normal"), (2, "warning"), (4, "critical"), (3, "unknown")])
def test_darwin_sysctl_uses_exported_dispatch_flags_not_internal_enum(monkeypatch, raw, expected):
    def sysctl(_name, output, *_args):
        output._obj.value = raw
        return 0
    lib = SimpleNamespace(sysctlbyname=sysctl, host_statistics64=lambda *_: 1)
    monkeypatch.setattr(memory, "_darwin_library", lambda: (lib, 1))
    monkeypatch.setattr(memory.psutil, "virtual_memory", lambda: SimpleNamespace(available=500))
    pressure, available, commit, facts = memory._darwin_facts()
    assert pressure == expected and available == 500 and commit is None
    assert facts["pressure"]["native_value"] == raw
    assert facts["paging"]["value"] is None  # Native paging failed, never fabricated zero.


@pytest.mark.parametrize("low,high,expected", [(False, True, "normal"), (False, False, "warning"),
                                              (True, False, "critical"), (True, True, "unknown")])
def test_windows_middle_notification_state_and_commit_pages(monkeypatch, low, high, expected):
    closed = []
    def query(handle, state):
        state._obj.value = low if handle == 100 else high
        return 1
    kernel = SimpleNamespace(CreateMemoryResourceNotification=NativeFunction(lambda kind: 100 + kind),
        QueryMemoryResourceNotification=NativeFunction(query), CloseHandle=NativeFunction(lambda h: closed.append(h) or 1))
    def performance(ptr, _size):
        value = ptr._obj
        value.CommitTotal, value.CommitLimit, value.PageSize, value.PhysicalAvailable, value.PhysicalTotal = 10, 30, 4096, 20, 100
        return 1
    psapi = SimpleNamespace(GetPerformanceInfo=NativeFunction(performance))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_: kernel if name == "kernel32" else psapi, raising=False)
    pressure, available, commit, facts = memory._windows_facts()
    assert pressure == expected and available == commit == 20 * 4096
    assert facts["paging"]["value"] is None
    assert closed == [100, 101]


def test_pressure_wait_is_recognized_by_both_existing_executor_families():
    world = World()
    world.pressure = "critical"
    stopped = GrantedCommandMemoryPressure(world.check())
    assert isinstance(stopped, HostResourceRefusal) and isinstance(stopped, GrantedCommandResourceUnavailable)
    assert stopped.recoverable_pressure and stopped.memory_decision["reason"] == "memory_pressure_critical"
    world.pressure = "unknown"
    assert GrantedCommandMemoryPressure(world.check()).recoverable_pressure


@pytest.mark.skipif(sys.platform != "darwin", reason="read-only native Darwin source verification")
def test_native_darwin_facts_are_readable_without_starting_a_load():
    sample = memory.sample_host_memory()
    assert sample.pressure in {"normal", "warning", "critical"}
    assert sample.facts["pressure"]["native_value"] in {1, 2, 4}
    assert sample.available_bytes > 0 and sample.facts["swap_used_bytes"]["diagnostic_only"]
    assert sample.facts["swapins"]["source"] == "host_statistics64.swapins"
    facts = memory.process_memory_facts(os.getpid())
    assert facts["physical_footprint_bytes"]["value"] > 0
    assert facts["resident_set_bytes"]["source"] != facts["physical_footprint_bytes"]["source"]


@pytest.mark.skipif(os.name != "posix", reason="Windows process-tree executor remains a separate implementation")
def test_synthetic_pressure_reaps_a_real_idle_child_group(tmp_path, monkeypatch):
    world = World()
    world.pressure = "critical"
    class Scheduler:
        processes = HostProcessProbe()
        children = []
        def assert_grant(self, grant):
            assert grant == "isolated-grant"
        def register_child(self, grant, child):
            self.assert_grant(grant)
            self.children.append(child)
    scheduler = Scheduler()
    scheduler.resources = HostResourceProbe(tmp_path / "not-used.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(75, 100, None, 100, 100, 100), memory_monitor=world.monitor)
    monkeypatch.setattr(scheduler.resources, "scene_runtime_limits", lambda _:
        SceneRuntimeLimits(1024**3, 32, 100, None, .01, world.monitor))
    # The only real child sleeps. Pressure is synthetic; no memory stress or model load.
    with pytest.raises(GrantedCommandMemoryPressure) as stopped:
        run_granted_command(scheduler, "isolated-grant", [sys.executable, "-c", "import time; time.sleep(10)"],
                            timeout_seconds=2, max_stdout_bytes=64, max_stderr_bytes=64)
    assert stopped.value.command_diagnostics["group_released"] is True
    assert scheduler.children and scheduler.processes.children_released(tuple(scheduler.children)) is True
