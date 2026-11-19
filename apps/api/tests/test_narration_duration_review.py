"""Measured duration adoption keeps every paid audio unit and the original job."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.creation.contracts import CreationPlanPersonReference
from app.creation.execution_control import CreationExecutionControl
from app.creation.plan_contracts import CreationPlanPresenterSelection
from app.creation_execution import revision_digest, submit_creation_stage
from test_creation_execution import _narration_units_case


@pytest.mark.parametrize("scoped_budget", [False, True], ids=["legacy-budget", "task-budget"])
def test_overflow_keeps_the_final_wav_and_reuses_the_original_job(tmp_path, monkeypatch, scoped_budget):
    case = _narration_units_case(tmp_path, section_count=3, duration_seconds=2,
        local_policy=scoped_budget, start_job=False)
    task = case.repo.task_records.get_creation_task("local", case.task_id)
    plan = case.repo.plan_records.get_creation_plan("local", task.plan_id)
    script = case.repo.script_records.get_creation_task_script("local", case.script_id)
    plan.selected_person = CreationPlanPersonReference("person_1", "v1")
    plan.presenter_share_target = 20
    plan.presenter_selection = CreationPlanPresenterSelection(script.id, script.updated_at,
        "section_1", plan.selected_voice.profile_id, plan.selected_voice.version)
    with case.repo._connect() as conn:
        case.repo.plan_records.write_plan_payload(conn, "local", plan)
    case.job = case.execution.start("local", task.id, script.id, revision_digest(script, plan), actor=case.actor)
    case.owner.segments = case.executor.segments = SimpleNamespace(waiting_for_visual_decision=lambda _job: False)
    initial_binding = case.repo.creation_execution_jobs.creation_narration_source_binding("local", case.job.id)
    start_event_id = f"event_{case.job.id}_start"
    initial_start = case.repo.execution_records.get_task_event("local", start_event_id)

    def access(task_id, request, *, write=False):
        return case.repo.task_records.get_creation_task("local", task_id), case.actor.member_id

    monkeypatch.setattr(main, "production_repo", case.repo)
    monkeypatch.setattr(main, "_exact_edit_access", access)
    monkeypatch.setattr(main, "_request_permit_actor", lambda _request, _workspace_id: case.actor)
    client = TestClient(main.app)
    path = f"/api/creation-tasks/{case.task_id}/narration/duration"
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    for ordinal in range(1, 4):
        grant = case.scheduler.pick_and_grant()
        assert grant.unit_key.startswith(f"narration:segment:v1:section_{ordinal}:")
        case.executor.execute(grant, case.scheduler)
        assert case.scheduler._get(grant.request_id).state == "done"
        progress = client.get(path).json()
        assert progress["completed_sections"] == ordinal
        assert progress["measured_seconds"] == ordinal
        assert progress["target_seconds"] == 2
        assert bool(progress["review_id"]) == (ordinal == 3)
    assert case.scheduler.pick_and_grant() is None
    job = case.repo.job_records.get_job_run("local", case.job.id)
    assert job.status == "interrupted"
    task = case.repo.task_records.get_creation_task("local", case.task_id)
    assert task.stage == "narration" and task.narration_cache_key is None
    assert task.stage_reason == job.failure_reason

    original_segments = case.repo.narration_records.list_narration_segments("local", case.task_id, case.script_id)
    original_paths = [case.repo.narration_artifacts.resolve_authorized_narration_audio_reference(
        "local", segment.audio_reference) for segment in original_segments]
    original_audio = [Path(p).read_bytes() for p in original_paths]
    original_requests = [case.scheduler._get(item[0]) for item in case.generated]
    original_usage = case.repo.narration_units.creation_narration_usage("local", case.job.id)
    assert original_usage["consumption_known"] and not original_usage["unsettled"]
    assert original_usage["used_output_bytes"] == sum(len(wav) for wav in original_audio)

    wrong = {"request_id": str(uuid4()), "review_id": progress["review_id"] + "-stale"}
    assert client.put(path, json=wrong).status_code == 409
    command = {"request_id": str(uuid4()), "review_id": progress["review_id"]}
    saved = client.put(path, json=command)
    assert saved.status_code == 200, saved.text
    assert saved.json()["job_id"] == case.job.id
    assert saved.json()["duration_target_seconds"] == 3
    assert client.put(path, json=command).json() == saved.json()
    assert client.put(path, json={**wrong, "request_id": command["request_id"]}).status_code == 409
    assert client.get(path).json()["review_id"] is None
    assert case.repo.narration_units.creation_narration_usage("local", case.job.id) == original_usage
    assert [case.scheduler._get(item[0]) for item in case.generated] == original_requests
    assert case.repo.narration_records.list_narration_segments("local", case.task_id, case.script_id) == original_segments
    assert [Path(p).read_bytes() for p in original_paths] == original_audio
    assert case.repo.execution_records.get_task_event("local", start_event_id) == initial_start

    with pytest.raises(ValueError):
        case.execution.validate_input_snapshot("local", case.task_id, case.script_id, case.job.input_digest)
    control = CreationExecutionControl(case.repo, case.execution, SimpleNamespace(valid=lambda *_args, **_kw: False),
        lambda _identity: "ready")
    runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda: None,
        registrations={"creation_job": SimpleNamespace(owner=case.owner, executor=case.executor)})
    resumed = control.resume("local", case.task_id, case.actor, runtime, verify_media=False)
    assert resumed["job"]["id"] == case.job.id
    job = case.repo.job_records.get_job_run("local", case.job.id)
    rebound = case.repo.creation_execution_jobs.creation_narration_source_binding("local", job.id)
    assert rebound.payload["sha256"] == initial_binding.payload["sha256"]
    assert rebound.payload["previous_source_binding_event_id"] == initial_binding.id
    assert rebound.payload["business_input_digest"] == job.input_digest
    assert job.id == case.job.id and job.input_digest != case.job.input_digest
    merge = case.scheduler.pick_and_grant()
    assert merge.unit_key.startswith("narration:merge:v1:")
    assert merge.input_digest == job.input_digest
    case.executor.execute(merge, case.scheduler)
    assert case.scheduler._get(merge.request_id).state == "done"
    adopted = case.repo.task_records.get_creation_task("local", case.task_id)
    full_audio = case.repo.narration_artifacts.get_narration_artifact("local", adopted.narration_cache_key)
    assert full_audio.duration_seconds == 3
    assert len(case.generated) == 3
    assert [case.scheduler._get(item[0]) for item in case.generated] == original_requests
    assert len(case.execution.jobs("local", case.task_id)) == 1
    assert client.put(path, json=command).json() == saved.json()
