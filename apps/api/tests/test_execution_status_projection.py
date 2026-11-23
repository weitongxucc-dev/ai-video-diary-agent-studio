from types import SimpleNamespace

from app.creation.execution_control import _duration_consistency, _media_queue_summary
from app.task_audio import TASK_MEDIA_KIND


def test_duration_consistency_compares_current_timeline_to_plan_target():
    task = SimpleNamespace(
        workspace_id="local",
        id="task-1",
        current_timeline_version_id="timeline-2",
    )
    plan = SimpleNamespace(duration_target_seconds=151)
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
    plan = SimpleNamespace(duration_target_seconds=162.73333333333332)
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


def test_media_queue_summary_keeps_pending_waveforms_visible_with_execution_success():
    jobs = [
        SimpleNamespace(kind=TASK_MEDIA_KIND, status="queued", id="media-1"),
        SimpleNamespace(kind=TASK_MEDIA_KIND, status="succeeded", id="media-2"),
        SimpleNamespace(kind="creation_task_execution", status="succeeded", id="execution-1"),
    ]
    repository = SimpleNamespace(
        job_records=SimpleNamespace(list_job_runs=lambda *_: jobs),
        execution_records=SimpleNamespace(
            get_task_event=lambda *_: SimpleNamespace(payload={"operation": "audio_waveform"}),
        ),
    )

    result = _media_queue_summary(repository, "local", "task-1")

    assert result == {
        "status": "pending",
        "pending_count": 1,
        "pending_operations": ["audio_waveform"],
    }
