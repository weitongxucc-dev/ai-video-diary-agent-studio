"""Query-only calibration and real SQLite permit binding; no model execution."""
from dataclasses import asdict, replace
import json
import sqlite3
from types import SimpleNamespace

import psutil
import pytest

from app.device_scheduler import Grant, ProcessIdentity, RecoveryBlocked
from app.granted_subprocess import (
    GrantedCommandResourceLimit, GrantedCommandResourceUnavailable, _check_scene_runtime,
    run_granted_command,
)
from app.host_resource_probes import (
    HostBudget, HostProcessProbe, HostResourceProbe, HostResourceRefusal,
    MaterialVisualQueryBudget, material_visual_query_resources,
)
from app.member_permits import HostBudgetUncalibrated, host_budget_from_env, material_visual_query_budget_from_env
from app.model_runtime import CHINESE_CLIP_VIT_B16
from app.visual_semantics import visual_feature_version
from tests.test_member_permits import _HOST_ENV


def _budget():
    # Explicit synthetic probe values, never production defaults or calibration evidence.
    return MaterialVisualQueryBudget(model_id=CHINESE_CLIP_VIT_B16.id,
        model_version=visual_feature_version(CHINESE_CLIP_VIT_B16.id), max_query_chars=120,
        max_timeout_seconds=2., max_stdout_bytes=64, max_stderr_bytes=128,
        max_output_file_bytes=512, max_threads=1, max_process_rss_bytes=1000,
        max_process_threads=4, runtime_sample_seconds=.1,
        additional_memory_bytes=2000, additional_disk_bytes=1000)


def _environment():
    return {"DEVICE_MATERIAL_VISUAL_QUERY_" + name.upper(): str(value)
            for name, value in asdict(_budget()).items()}


def test_budget_absence_does_not_borrow_or_change_other_host_gates():
    assert material_visual_query_budget_from_env(_HOST_ENV) is None
    before = host_budget_from_env(_HOST_ENV)
    configured = host_budget_from_env(_HOST_ENV, material_visual_query=_budget())
    assert replace(configured, material_visual_query=None) == before
    assert configured.material_visual_query.execution_limits.max_stderr_bytes == 128


@pytest.mark.parametrize("field", list(asdict(_budget())))
def test_partial_query_calibration_requires_each_explicit_value(field):
    env = _environment()
    name = "DEVICE_MATERIAL_VISUAL_QUERY_" + field.upper()
    del env[name]
    with pytest.raises(HostBudgetUncalibrated, match=name):
        material_visual_query_budget_from_env(env)


@pytest.mark.parametrize("field,value", [
    ("MODEL_ID", "unregistered-model"), ("MODEL_VERSION", "old-revision"),
    ("MAX_TIMEOUT_SECONDS", "nan"), ("RUNTIME_SAMPLE_SECONDS", "0.3"),
    ("MAX_QUERY_CHARS", "121"), ("MAX_THREADS", "5"),
    ("ADDITIONAL_MEMORY_BYTES", "999"), ("ADDITIONAL_DISK_BYTES", "511"),
    ("MAX_STDOUT_BYTES", "-1"),
])
def test_invalid_model_or_unbounded_calibration_is_explicit(field, value):
    with pytest.raises(HostBudgetUncalibrated, match="素材画面查询预算无效"):
        material_visual_query_budget_from_env(_environment() | {"DEVICE_MATERIAL_VISUAL_QUERY_" + field: value})


def test_calibration_parses_exact_execution_limits_and_seals_complete_profile():
    budget = material_visual_query_budget_from_env(_environment())
    assert budget == _budget()
    resources = material_visual_query_resources(budget, query_chars=12)
    assert resources["execution_limits"] == asdict(budget.execution_limits)
    assert resources["budget_digest"] == budget.digest
    assert replace(budget, max_process_rss_bytes=999).digest != budget.digest
    with pytest.raises(ValueError):
        material_visual_query_resources(budget, query_chars=121)


@pytest.fixture
def bound(tmp_path):
    database = tmp_path / "permit.sqlite"
    grant = Grant("request", "workspace", "material_retrieval", "query", "visual-query:v1:input",
                  "a" * 64, 1, 1, "token", database)
    query = _budget()
    resources = material_visual_query_resources(query, query_chars=12)
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE device_work_requests (id TEXT,workspace_id TEXT,owner_type TEXT,
            owner_id TEXT,unit_key TEXT,input_digest TEXT,attempt_number INTEGER,
            last_grant_generation INTEGER,state TEXT,resources_json TEXT)""")
        conn.execute("INSERT INTO device_work_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
            (grant.request_id, grant.workspace_id, grant.owner_type, grant.owner_id, grant.unit_key,
             grant.input_digest, grant.attempt_number, grant.generation, "granted", json.dumps(resources)))
    probe = HostResourceProbe(database, disk_paths=(tmp_path,),
        budget=HostBudget(75, 100, 50, 200, 100, 1024, material_visual_query=query))
    return SimpleNamespace(query=query, resources=resources, grant=grant, probe=probe, tmp_path=tmp_path)


def _update(bound, resources):
    with sqlite3.connect(bound.grant.database_path) as conn:
        conn.execute("UPDATE device_work_requests SET resources_json=?", (json.dumps(resources),))


def test_missing_query_budget_refuses_before_sampling_or_spawn(bound, monkeypatch):
    s = bound
    s.probe.budget = replace(s.probe.budget, material_visual_query=None)
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **_: pytest.fail("unconfigured query sampled"))
    decision = s.probe._sample(s.resources)
    assert decision.verdict is None and decision.reason == "material_visual_query_budget_unavailable"
    monkeypatch.setattr("app.granted_subprocess.subprocess.Popen", lambda *_a, **_k: pytest.fail("unconfigured query spawned"))
    scheduler = SimpleNamespace(resources=s.probe, processes=HostProcessProbe(), assert_grant=lambda _: None)
    with pytest.raises(RecoveryBlocked):
        run_granted_command(scheduler, s.grant, ["never-launch"], timeout_seconds=2,
            max_stdout_bytes=64, max_stderr_bytes=128, resource_purpose="material_visual_query")


@pytest.mark.parametrize("change", ["model", "revision", "count", "chars", "limits", "boolean_alias", "purpose", "missing_digest", "rss_profile"])
def test_changed_declarations_and_profile_never_reach_sampling(bound, monkeypatch, change):
    s = bound
    resources = json.loads(json.dumps(s.resources))
    if change == "model":
        resources["model_id"] = "other-model"
    elif change == "revision":
        resources["model_version"] = "other-revision"
    elif change == "count":
        resources["text_count"] = 2
    elif change == "chars":
        resources["query_chars"] = 121
    elif change == "limits":
        resources["execution_limits"]["max_stdout_bytes"] += 1
    elif change == "boolean_alias":
        resources["execution_limits"]["threads"] = True
    elif change == "purpose":
        resources["audio_mix"] = {"kind": "task_audio"}
    elif change == "missing_digest":
        del resources["budget_digest"]
    else:
        s.probe.budget = replace(s.probe.budget, material_visual_query=replace(s.query, max_process_rss_bytes=999))
    _update(s, resources)
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **_: pytest.fail("invalid query sampled"))
    assert s.probe._sample(resources).verdict is False
    with pytest.raises(RecoveryBlocked):
        s.probe.scene_runtime_limits(s.grant, purpose="material_visual_query")


def test_runtime_purpose_and_current_grant_bind_exact_process_limits(bound):
    s = bound
    for purpose in (None, "material_visual_query"):
        limits = s.probe.scene_runtime_limits(s.grant, purpose=purpose)
        assert (limits.max_process_rss_bytes, limits.max_process_threads, limits.sample_seconds) == (1000, 4, .1)
        assert (limits.min_host_available_memory_bytes, limits.max_host_swap_used_bytes) == (100, 50)
    with pytest.raises(RecoveryBlocked):
        s.probe.scene_runtime_limits(s.grant, purpose="audio_mix")
    for grant in (replace(s.grant, generation=2), replace(s.grant, workspace_id="other"),
                  replace(s.grant, input_digest="b" * 64)):
        with pytest.raises(RecoveryBlocked):
            s.probe.scene_runtime_limits(grant, purpose="material_visual_query")
    _update(s, {"kind": "material_hash", "size_bytes": 10})
    with pytest.raises(RecoveryBlocked):
        s.probe.scene_runtime_limits(s.grant, purpose="material_visual_query")


def _sample_host(s, monkeypatch, **changes):
    values = dict(cpu=75, memory=2100, swap=50, disk=1200, io=100)
    values.update(changes)
    ticks = iter((0., 4.))
    counters = iter((SimpleNamespace(read_bytes=0, write_bytes=0),
                     SimpleNamespace(read_bytes=values["io"] * 4, write_bytes=0)))
    monkeypatch.setattr("app.host_resource_probes.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr(psutil, "disk_io_counters", lambda **_: {"disk": next(counters)})
    monkeypatch.setattr(psutil, "cpu_percent", lambda **_: values["cpu"])
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=values["memory"]))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=values["swap"]))
    monkeypatch.setattr(psutil, "disk_usage", lambda _: SimpleNamespace(free=values["disk"]))
    monkeypatch.setattr(psutil, "disk_partitions", lambda **_: [])


def test_admission_reserves_own_memory_and_disk_while_running_checks_host_headroom(bound, monkeypatch):
    s = bound
    _sample_host(s, monkeypatch)
    decision = s.probe._sample(s.resources)
    assert decision.verdict is True
    assert decision.checks["available_memory_bytes"]["minimum"] == 2100
    assert decision.checks[f"disk_free_bytes:{s.tmp_path}"]["minimum"] == 1200
    _sample_host(s, monkeypatch, memory=100)
    assert s.probe.continue_unit(s.grant) is True


@pytest.mark.parametrize("metric,value", [("cpu", 76), ("memory", 99), ("swap", 51), ("disk", 1199), ("io", 101)])
def test_query_keeps_existing_live_host_thresholds(bound, monkeypatch, metric, value):
    _sample_host(bound, monkeypatch, **{metric: value})
    with pytest.raises(HostResourceRefusal) as stopped:
        bound.probe.continue_unit(bound.grant)
    assert stopped.value.decision.verdict is False


def test_process_monitor_counts_the_whole_query_group(bound, monkeypatch):
    limits = bound.probe.scene_runtime_limits(bound.grant, purpose="material_visual_query")
    parent = SimpleNamespace(pid=101, create_time=lambda: 1., memory_info=lambda: SimpleNamespace(rss=600), num_threads=lambda: 2)
    child = SimpleNamespace(pid=102, memory_info=lambda: SimpleNamespace(rss=500), num_threads=lambda: 2)
    monkeypatch.setattr(psutil, "Process", lambda _: parent)
    monkeypatch.setattr(psutil, "process_iter", lambda: [parent, child])
    monkeypatch.setattr("app.granted_subprocess.os.getpgid", lambda _: 101)
    process = SimpleNamespace(pid=101, poll=lambda: None)
    identity = ProcessIdentity(101, "1.000000", "fixture")
    with pytest.raises(GrantedCommandResourceLimit):
        _check_scene_runtime(process, identity, limits)
    parent.memory_info = child.memory_info = lambda: SimpleNamespace(rss=100)
    child.num_threads = lambda: 3
    with pytest.raises(GrantedCommandResourceLimit):
        _check_scene_runtime(process, identity, limits)
    child.num_threads = lambda: 1
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=99))
    with pytest.raises(GrantedCommandResourceUnavailable):
        _check_scene_runtime(process, identity, limits)


def test_changed_budget_does_not_trap_proven_exited_query_group(bound):
    bound.probe.budget = replace(bound.probe.budget, material_visual_query=None)
    # HostResourceProbe owns no resident model; scheduler still must prove every child exited.
    assert bound.probe.released(bound.grant) is True
