from types import SimpleNamespace

import pytest

from app.creation.contracts import CreationTask, JobRun, TaskEvent, TimelineVersion
from app.creation.execution_control import CreationExecutionControl, _duration_consistency
from app.creation.plan_contracts import CreationPlan
from app.creation_execution import CreationExecution
from app.job_progress import summarize_job_progress
from app.models import utc_now
from app.production import ProductionRepository
from app.task_audio import TASK_MEDIA_KIND


def test_duration_consistency_compares_current_timeline_to_plan_target():
    task = SimpleNamespace(
        workspace_id="local",
        id="task-1",
        current_timeline_version_id="timeline-2",
    )
    plan = SimpleNamespace(duration_target_seconds=151, source_media=None)
    version = SimpleNamespace(
        id="timeline-2",
        snapshot={"montage": {"duration_seconds": 162.73333333333332}},
    )
    repository = SimpleNamespace(
        timeline_records=SimpleNamespace(get_timeline_version=lambda *_: version),
        execution_records=SimpleNamespace(list_task_events=lambda *_: []),
    )

    result = _duration_consistency(repository, task, plan)

    assert result == {
        "status": "mismatch",
        "original_target_seconds": None,
        "target_seconds": 151.0,
        "target_range_seconds": None,
        "timeline_seconds": 162.733333,
        "difference_seconds": 11.733333,
        "tolerance_seconds": 1 / 30,
        "timeline_version_id": "timeline-2",
        "target_adjusted": False,
        "adjustment_event_id": None,
    }


def test_duration_consistency_marks_explicit_target_adjustment():
    task = SimpleNamespace(
        workspace_id="local",
        id="task-1",
        current_timeline_version_id="timeline-2",
    )
    plan = SimpleNamespace(duration_target_seconds=162.73333333333332, source_media=None)
    version = SimpleNamespace(
        id="timeline-2",
        snapshot={"montage": {"duration_seconds": 162.73333333333332}},
    )
    adoption = SimpleNamespace(
        id="duration-adoption-1",
        event_type="creation_narration_duration_adopted",
        payload={"previous_target_seconds": 151},
    )
    repository = SimpleNamespace(
        timeline_records=SimpleNamespace(get_timeline_version=lambda *_: version),
        execution_records=SimpleNamespace(list_task_events=lambda *_: [adoption]),
    )

    result = _duration_consistency(repository, task, plan)

    assert result["status"] == "adjusted"
    assert result["original_target_seconds"] == 151.0
    assert result["target_adjusted"] is True
    assert result["adjustment_event_id"] == "duration-adoption-1"


@pytest.mark.parametrize(("seconds", "status"), [
    (30.5, "aligned"),
    (31.1, "mismatch"),
])
def test_duration_consistency_uses_agreed_range(seconds, status):
    task = SimpleNamespace(workspace_id="local", id="task", current_timeline_version_id="timeline")
    plan = SimpleNamespace(
        duration_target_seconds=30, duration_mode="approximate", duration_range_seconds=(29, 31), source_media=None,
    )
    version = SimpleNamespace(id="timeline", snapshot={"montage": {"duration_seconds": seconds}})
    repository = SimpleNamespace(
        timeline_records=SimpleNamespace(get_timeline_version=lambda *_: version),
        execution_records=SimpleNamespace(list_task_events=lambda *_: []),
    )

    result = _duration_consistency(repository, task, plan)

    assert result["status"] == status
    assert result["target_range_seconds"] == (29, 31)
    assert result["tolerance_seconds"] is None


def test_duration_consistency_uses_edited_clock_for_original_video():
    task = SimpleNamespace(workspace_id="local", id="task", current_timeline_version_id="timeline")
    plan = SimpleNamespace(duration_target_seconds=30, source_media={"asset_id": "original-video"})
    version = SimpleNamespace(id="timeline", snapshot={"montage": {"duration_seconds": 28, "script_id": None}})
    repository = SimpleNamespace(
        timeline_records=SimpleNamespace(get_timeline_version=lambda *_: version),
        execution_records=SimpleNamespace(list_task_events=lambda *_: []),
    )

    result = _duration_consistency(repository, task, plan)

    assert result["status"] == "aligned"
    assert result["target_seconds"] == result["timeline_seconds"] == 28


def test_media_queue_summary_keeps_pending_waveforms_visible_with_execution_success(tmp_path):
    workspace, task_id = "local", "task"
    repo = ProductionRepository(tmp_path / "projection.sqlite3", tmp_path / "library")
    plan = CreationPlan(
        id="plan", workspace_id=workspace, goal="projection", name="projection", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=3,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="fixture",
    )
    task = CreationTask(task_id, workspace, plan.id, "projection", "knowledge_explanation")
    repo.plan_confirmation.confirm_creation_plan(plan, task)
    repo.timeline_records.create_timeline_version(TimelineVersion(
        "final", workspace, task_id, None, 1, {"montage": {"duration_seconds": 3}}, None,
    ))
    main_job = "finished-main"
    repo.job_records.create_job_run(JobRun(main_job, workspace, task_id, "creation_task_execution", main_job, main_job))
    repo.job_records.claim_job_run(workspace, main_job)
    repo.job_records.finish_job_run(workspace, main_job, status="succeeded")
    settled = "settled-waveform"
    repo.job_records.create_job_run(JobRun(settled, workspace, task_id, TASK_MEDIA_KIND, settled, settled))
    repo.job_records.claim_job_run(workspace, settled)
    repo.job_records.finish_job_run(workspace, settled, status="succeeded")
    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for job_id in ("history-waveform", "current-waveform"):
            repo.job_records.create_job_run(
                JobRun(job_id, workspace, task_id, TASK_MEDIA_KIND, job_id, job_id), connection=conn,
            )
            repo.execution_records.append_task_event(TaskEvent(
                f"event_{job_id}_start", workspace, task_id, job_id, "task_media_submitted",
                {"operation": "audio_waveform"},
            ), connection=conn)
        # Each history row differs from a matching unsettled request in one field.
        conn.executemany(
            """
            INSERT INTO device_work_requests
                (id, workspace_id, owner_type, owner_id, input_digest, unit_key, state,
                 source_refs_json, work_class, policy, first_ready_at_utc, resources_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'preview_dependency', 'asap',
                    '2026-01-01T00:00:00+00:00', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            [
                ("valid", workspace, "creation_job", "current-waveform", "current-waveform", "task_media:current-waveform", "blocked"),
                ("terminal", workspace, "creation_job", "history-waveform", "history-waveform", "task_media:history-waveform", "done"),
                ("digest", workspace, "creation_job", "history-waveform", "wrong", "task_media:history-waveform", "waiting"),
                ("workspace", "other", "creation_job", "history-waveform", "history-waveform", "task_media:history-waveform", "granted"),
                ("unit", workspace, "creation_job", "history-waveform", "history-waveform", "wrong-unit", "waiting"),
                ("owner", workspace, "other", "history-waveform", "history-waveform", "task_media:history-waveform", "waiting"),
            ],
        )
        before = list(conn.iterdump())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("status projection must not execute media or background work")

    execution = CreationExecution(repo, run_montage_plan=forbidden, run_render=forbidden, render_is_valid=forbidden)
    control = CreationExecutionControl(
        repo, execution, SimpleNamespace(valid=lambda *_args, **_kwargs: True), forbidden,
    )
    result = control.status(workspace, task_id, verify_media=False)

    assert result["job"]["id"] == main_job and result["job"]["status"] == "succeeded"
    assert result["video_ready"] is True
    assert result["current_stage"] is None and result["waiting_reason"] is None
    assert result["media_queue"] == {
        "status": "pending",
        "pending_count": 2,
        "pending_operations": ["audio_waveform"],
        "pending_with_device_request_count": 1,
        "pending_without_device_request_count": 1,
    }
    runs, missing = repo.job_records.list_task_job_progress_runs(workspace, task_id, TASK_MEDIA_KIND)
    items, current = summarize_job_progress(runs, now=utc_now(), missing_request_job_ids=missing)
    assert current.id == "current-waveform"
    assert [(item.id, item.status) for item in items if item.missing_device_request] == [
        ("history-waveform", "queued"),
    ]
    with repo._connect() as conn:
        assert list(conn.iterdump()) == before
        conn.execute("UPDATE device_work_requests SET state='done' WHERE id='valid'")
    runs, missing = repo.job_records.list_task_job_progress_runs(workspace, task_id, TASK_MEDIA_KIND)
    items, current = summarize_job_progress(runs, now=utc_now(), missing_request_job_ids=missing)
    assert current.id == settled
    assert {item.id for item in items if item.missing_device_request} == {"history-waveform", "current-waveform"}
