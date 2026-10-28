"""Post-publication render pressure, with synthetic receipts and an isolated driver.

No FFmpeg, model, service or real media is run. Only media/fact measurement and
ambient resource observations are seams; roster, jobs, versions, permit, lock,
release fencing and the background dispatch loop use their production code.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.creation_execution as creation
from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier, CreationJobUnitExecutor
from app.device_runtime import OwnerRegistration, build_device_runtime
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent
from app.host_resource_probes import HostCapacity, HostProcessProbe, HostResourceDecision, HostResourceRefusal
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess
from app.creation.contracts import TimelineVersion
from test_creation_execution import _execution, _seed


def wait_for(predicate, diagnostic=lambda: None):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"isolated driver did not reach its bounded checkpoint: {diagnostic()}")


@pytest.fixture
def render_case(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    members = LocalMemberService(repo.database_path)
    member = members.add_member("render pressure fixture")
    actor = CreationExecutionActor("local", member.member_id)
    access = MemberDeviceAccess(members)
    owner = CreationJobOwnerVerifier(repo, verify_identity=access.verify_identity)
    resource_facts = {"kind": "creation_render", "execution_mode": "cpu_ffmpeg", "duration_seconds": 1,
                      "clip_count": 1, "threads": 1, "timeout_seconds": 600, "max_output_bytes": 1024}
    # Persisted measurement seam; _check_facts still checks the actual script,
    # plan, original member and revision on every submission/current read.
    monkeypatch.setattr(owner, "render_facts", lambda *args, **kwargs: ("render:fixture", resource_facts))
    monkeypatch.setattr(creation, "sample_host_capacity", lambda: HostCapacity(
        cpu_count=8, cpu_threads_ceiling=7, available_memory_bytes=10**10, load_avg_1m=0))
    state = {"render_calls": 0, "validation_calls": 0, "failure": True, "released": True, "children_released": True,
             "grants": [], "now": datetime.now(timezone.utc), "decision": HostResourceDecision(False, {},
                 {"disk_io_bytes_per_second": {"actual": 200, "maximum": 100, "passed": False}}, None)}
    receipt = tmp_path / "synthetic-render-receipt.dat"

    def render(workspace_id, current_task_id, context):
        state["render_calls"] += 1
        state["grants"].append(context.grant)
        receipt.write_bytes(b"synthetic render receipt, not media")
        plan = {"id": "fixture_montage", "workspace_id": workspace_id, "task_id": current_task_id,
                "script_id": script_id, "status": "rendered", "output_path": str(receipt)}
        task = repo.get_creation_task(workspace_id, current_task_id)
        previous = repo.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        version = TimelineVersion(id=f"fixture-output-{state['render_calls']}", workspace_id=workspace_id,
            task_id=current_task_id, parent_version_id=task.current_timeline_version_id,
            sequence=previous.sequence + 1 if previous else 1, created_by_operation_id=None,
            snapshot={"output_refs": [str(receipt)], "creation_render_request_id": context.grant.request_id})
        with repo._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) VALUES (?,?,?,?,?)",
                         (plan["id"], workspace_id, current_task_id, json.dumps(plan), state["now"].isoformat()))
            repo.create_timeline_version(version, connection=conn)

    def validate(workspace_id, current_task_id, context):
        state["validation_calls"] += 1
        if state["failure"]:
            state["failure"] = False
            if state.get("worker_error"):
                error = HostResourceRefusal(state["decision"])
                error.command_diagnostics = {"before_termination_returncode": -6, "stderr": b"fixture worker failed"}
                raise error
            raise HostResourceRefusal(state["decision"])
        return receipt.is_file() and not state.get("invalid_output")

    executor = CreationJobUnitExecutor(repo, render_action=render, render_is_valid=validate)
    execution = _execution(repo)
    resources = SimpleNamespace(admit=lambda request: True, continue_unit=lambda grant: True,
                                released=lambda grant: state["released"])
    processes = HostProcessProbe()
    original_children_released = processes.children_released
    monkeypatch.setattr(processes, "children_released", lambda children:
                        original_children_released(children) if state["children_released"] else False)
    runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path / "device.lock",
        device_access=access, registrations={"creation_job": OwnerRegistration(owner, executor)},
        resources=resources, processes=processes, poll_interval_seconds=0.01)
    runtime.scheduler.clock = lambda: state["now"]
    job = execution.start("local", task_id, script_id, digest, actor=actor)
    request = runtime.scheduler.submit(SubmissionIntent("local", "creation_job", job.id, digest,
                                                       "render:fixture", SchedulePolicy()), actor)

    def snapshot():
        with repo._connect() as conn:
            queued = dict(conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request.id,)).fetchone())
            permit = dict(conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone())
        return queued, permit, repo.job_records.get_job_run("local", job.id)

    yield SimpleNamespace(repo=repo, task_id=task_id, runtime=runtime, state=state, receipt=receipt,
                          request=request, job=job, snapshot=snapshot, executor=executor, members=members, member=member,
                          script_id=script_id)
    # Tests retain unknown/unreleased permits deliberately. Fixture cleanup is
    # separate from the assertion and proves its synthetic unit has no children.
    runtime._stop.set()
    if runtime._thread:
        runtime._thread.join(timeout=3)
    _request, permit, _job = snapshot()
    if permit["state"] == "held":
        state["released"] = True
        state["children_released"] = True
        runtime.scheduler.fail_unit(runtime.scheduler._last_issued_grant, reason="fixture_cleanup",
            failure_write=lambda conn, grant: executor._checkpoint(conn, grant, status="failed", reason="fixture_cleanup"))
    assert runtime.stop(timeout_seconds=3)


def test_post_publication_pressure_keeps_driver_and_reuses_output(render_case):
    case = render_case
    case.runtime.start()
    wait_for(lambda: case.runtime.last_error is not None or case.snapshot()[2].status in {"interrupted", "failed"},
             lambda: {key: case.snapshot()[0][key] for key in ("state", "wait_reason", "unit_key")})
    request, permit, job = case.snapshot()
    assert job.status != "failed", job.failure_reason
    assert case.receipt.is_file() and case.state["render_calls"] == 1
    assert case.runtime.last_error is None
    assert case.runtime.running
    assert request["state"] == "blocked" and request["wait_reason"] in {"resource_pressure", "retry_not_before"}
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM device_request_events WHERE request_id=? AND event_type='deferred' AND reason='resource_pressure'", (case.request.id,)).fetchone()[0] == 1
    assert permit["state"] == "free" and job.status == "interrupted"
    output_version = case.repo.get_creation_task("local", case.task_id).current_timeline_version_id
    case.state["now"] += timedelta(seconds=31)
    case.runtime.wake()
    wait_for(lambda: case.runtime.last_error is not None or case.snapshot()[0]["state"] == "done")
    request, permit, job = case.snapshot()
    assert case.runtime.last_error is None and case.runtime.running
    assert request["state"] == "done" and permit["state"] == "free" and job.status == "succeeded"
    assert case.state["render_calls"] == 1 and case.state["validation_calls"] == 2
    assert case.repo.get_creation_task("local", case.task_id).current_timeline_version_id == output_version


@pytest.mark.parametrize("failure", ["unknown", "declaration", "worker_error", "resources_unreleased", "children_unreleased"])
def test_unproven_or_nonrecoverable_conditions_keep_driver_blocked(render_case, failure):
    case = render_case
    if failure == "unknown":
        case.state["decision"] = HostResourceDecision(None, {}, {}, "disk_io_before_unavailable")
    elif failure == "declaration":
        case.state["decision"] = HostResourceDecision(False, {}, {}, "render_integer_fields_invalid")
    elif failure == "worker_error":
        case.state["worker_error"] = True
    elif failure == "resources_unreleased":
        case.state["released"] = False
    else:
        case.state["children_released"] = False
    case.runtime.start()
    wait_for(lambda: case.runtime.last_error is not None)
    assert isinstance(case.runtime.last_error, RecoveryBlocked)
    wait_for(lambda: not case.runtime.running)
    request, permit, job = case.snapshot()
    assert request["state"] == "granted" and permit["state"] == "held" and job.status == "running"
    assert case.state["render_calls"] == 1 and case.receipt.is_file()
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM device_request_events WHERE request_id=? AND event_type='deferred'", (case.request.id,)).fetchone()[0] == 0


def test_invalid_retained_output_fails_without_rendering_again(render_case):
    case = render_case
    case.runtime.start()
    wait_for(lambda: case.runtime.last_error is not None or case.snapshot()[2].status == "interrupted")
    assert case.runtime.last_error is None
    case.state["invalid_output"] = True
    case.state["now"] += timedelta(seconds=31)
    case.runtime.wake()
    wait_for(lambda: case.runtime.last_error is not None or case.snapshot()[2].status == "failed")
    request, permit, job = case.snapshot()
    assert case.runtime.last_error is None and case.runtime.running
    assert request["state"] == "failed" and permit["state"] == "free" and job.status == "failed"
    assert "未重复渲染" in job.failure_reason and case.state["render_calls"] == 1


def test_changed_confirmed_input_prevents_regrant(render_case):
    case = render_case
    case.runtime.start()
    wait_for(lambda: case.runtime.last_error is not None or case.snapshot()[2].status == "interrupted")
    assert case.runtime.last_error is None
    with case.repo._connect() as conn:
        script = json.loads(conn.execute("SELECT payload FROM creation_task_scripts WHERE id=?", (case.script_id,)).fetchone()[0])
        script["sections"][0]["content"] = "changed confirmed fixture input"
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id=?", (json.dumps(script), case.script_id))
    case.state["now"] += timedelta(seconds=31)
    case.runtime.wake()
    wait_for(lambda: case.runtime.last_error is not None or case.snapshot()[0]["wait_reason"] == "input_stale")
    request, permit, job = case.snapshot()
    assert case.runtime.last_error is None and case.runtime.running
    assert request["state"] == "blocked" and permit["state"] == "free" and job.status == "interrupted"
    assert case.state["render_calls"] == 1 and case.state["validation_calls"] == 1
