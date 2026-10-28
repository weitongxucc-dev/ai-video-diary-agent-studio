"""Real DeviceRuntime handoff with controlled resources; no media/model process."""
import json

import pytest

import app.main as main_module
from app.creation_execution import CreationExecution, CpuRenderLimits, submit_creation_stage
from app.host_resource_probes import MontageBudget
from app.creation.contracts import TimelineVersion
from test_narration_edit_api import generator_api, narration_api  # noqa: F401
from test_narration_edit_execution import fixture  # noqa: F401


@pytest.fixture
def waiting_state(generator_api, monkeypatch):  # noqa: F811 (existing pytest fixture import)
    state = generator_api
    state.owner.montage_budget = MontageBudget(10, 10, 1000000, 1000000, 60, 1000000)
    state.owner.cpu_render_limits = CpuRenderLimits(1000000, 1, 60)
    monkeypatch.setattr(state.resources, "admit", lambda _: state.resources.allowed)
    monkeypatch.setattr(main_module, "_creation_render_is_valid", lambda *_, **__: False)
    montage = state.repo.get_creation_task_montage_plan("local", state.task.id)
    montage["status"] = "waiting_for_user"
    montage["clips"][0]["needs_confirmation"] = True
    montage["low_confidence_clip_count"] = 1
    state.repo.save_creation_task_montage_plan("local", state.task.id, montage["id"], montage)
    state.base = state.repo.timeline_records.create_timeline_version(TimelineVersion("waiting-base", "local", state.task.id,
        "full-base", 3, {"montage": montage}, None))
    with state.repo._connect() as connection:
        task = state.repo.task_records.get_creation_task("local", state.task.id)
        from dataclasses import asdict
        task.narration_cache_key = "old"
        task.montage_plan_id = None
        connection.execute("UPDATE creation_tasks SET payload=? WHERE id=?",
            (json.dumps(asdict(task), default=str), task.id))
        asset = state.repo.get_asset("local", "asset_1")
        state.repo._claim_workspace_media_reference(connection, workspace_id="local",
            reference=state.repo.workspace_media_reference_for_asset("local", asset.id))
    state.task = state.repo.task_records.get_creation_task("local", state.task.id)
    state.execution = CreationExecution(state.repo, run_narration=lambda *_: pytest.fail("不应重配音"),
        run_montage_plan=lambda *_: None, run_render=lambda *_: pytest.fail("未确认不应渲染"),
        render_is_valid=lambda *_: False)
    monkeypatch.setattr(main_module, "creation_execution", state.execution)
    state.normal_job = state.execution.start("local", state.task.id, "script",
        main_module.revision_digest(state.repo.get_creation_task_script("local", "script"), state.plan), actor=state.actor)
    state.montage_calls = []
    def produce_waiting(*args):
        state.montage_calls.append(args)
        state.repo.save_creation_task_montage_plan("local", state.task.id, montage["id"], montage)
        state.produced_plan = state.repo.get_creation_task_montage_plan("local", state.task.id)
    state.executor.stage_actions["montage_plan"] = produce_waiting
    # Force the completed montage stage through the actual persisted Grant.
    from app.device_scheduler import SchedulePolicy, SubmissionIntent
    key, _ = state.owner.stage_facts(state.task, state.repo.get_creation_task_script("local", "script"), state.plan, "montage_plan")
    state.request = state.runtime.scheduler.submit(SubmissionIntent("local", "creation_job", state.normal_job.id,
        state.normal_job.input_digest, key, SchedulePolicy()), state.actor)
    state.resources.allowed = True
    return state


def test_montage_waits_without_failure_or_render_then_confirm_and_continue_once(waiting_state):
    state = waiting_state
    before = state.repo.get_creation_task_montage_plan("local", state.task.id)
    assert state.runtime.run_once()
    assert state.repo.job_records.get_job_run("local", state.normal_job.id).status == "queued"
    assert state.runtime.scheduler._get(state.request.id).state == "done"
    assert state.repo.get_creation_task_montage_plan("local", state.task.id) == state.produced_plan
    assert state.produced_plan["clips"] == before["clips"]
    task = state.repo.task_records.get_creation_task("local", state.task.id)
    assert task.stage_status == "waiting_for_user" and task.narration_cache_key == "old"
    assert task.current_timeline_version_id == state.base.id
    assert submit_creation_stage(state.execution, state.runtime.scheduler,
        state.repo.job_records.get_job_run("local", state.normal_job.id), state.actor) is None
    assert not state.runtime.run_once()
    assert len(state.montage_calls) == 1
    status = state.client.get(f"/api/creation-tasks/{state.task.id}/execution")
    assert status.status_code == 200
    assert status.json()["waiting_reason"] and status.json()["current_stage"] == "montage_plan"
    preview = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/exact/preview", json={
        "operation_type": "ConfirmClip", "clip_id": "clip-1"})
    assert preview.status_code == 200, preview.text
    confirmed = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/{preview.json()['proposal']['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    assert state.repo.get_creation_task_montage_plan("local", state.task.id)["status"] == "ready"
    job = state.execution.retry("local", state.task.id, member_id=state.actor.member_id)
    assert job.id == state.normal_job.id
    render_id = submit_creation_stage(state.execution, state.runtime.scheduler, job, state.actor)
    assert state.runtime.scheduler._get(render_id).unit_key.startswith("render:")
    assert submit_creation_stage(state.execution, state.runtime.scheduler, job, state.actor) == render_id
    with state.repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM device_work_requests WHERE owner_id=? AND unit_key LIKE 'render:%'", (job.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"


@pytest.mark.parametrize("boundary", ["cancel", "revoked", "unknown"])
def test_waiting_handoff_preserves_cancel_revocation_and_unknown_gate(waiting_state, boundary):
    from app.device_scheduler import RecoveryBlocked
    state = waiting_state
    if boundary == "cancel":
        state.execution.cancel("local", state.task.id, member_id=state.actor.member_id)
        state.runtime.scheduler.cancel(state.request.id, state.actor)
        assert not state.runtime.run_once()
        assert state.repo.job_records.get_job_run("local", state.normal_job.id).status == "cancelled"
        assert not state.montage_calls
    elif boundary == "revoked":
        state.members.remove_member(state.actor.member_id)
        assert not state.runtime.run_once()
        assert not state.montage_calls
    else:
        state.resources.release = None
        try:
            with pytest.raises(RecoveryBlocked):
                state.runtime.run_once()
            with state.repo._connect() as connection:
                assert connection.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] != "free"
            assert state.repo.job_records.get_job_run("local", state.normal_job.id).status == "running"
        finally:
            state.resources.release = True
            state.resources.allowed = False
    task = state.repo.task_records.get_creation_task("local", state.task.id)
    assert task.current_timeline_version_id == state.base.id and task.narration_cache_key == "old"
    with state.repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM device_work_requests WHERE owner_id=? AND unit_key LIKE 'render:%'", (state.normal_job.id,)).fetchone()[0] == 0
