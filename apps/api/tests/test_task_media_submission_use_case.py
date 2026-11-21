from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.creation.media_result_use_case import MediaResultUseCase
from app.creation_execution import CreationExecutionActor
from app.task_media_routes import build_task_media_router
from app.task_media_submission_use_case import (
    BoundMediaSubmission,
    TaskMediaSubmissionRejected,
    TaskMediaSubmissionUseCase,
)


def _command(**changes):
    value = {
        "request_id": "11111111-1111-4111-8111-111111111111",
        "base_version_id": "timeline-1",
        "asset_id": "asset-1",
        "start_seconds": 1,
        "end_seconds": 3,
    }
    value.update(changes)
    return value


def test_domain_input_rejects_paths_and_arbitrary_parameters():
    with pytest.raises(TaskMediaSubmissionRejected, match="未支持"):
        BoundMediaSubmission.from_value({**_command(), "source_path": "/tmp/input.mp4"})
    with pytest.raises(TaskMediaSubmissionRejected, match="素材 ID"):
        BoundMediaSubmission.from_value(_command(asset_id="/tmp/input.mp4"))


def test_submission_builds_controlled_plan_and_reuses_same_request():
    asset = SimpleNamespace(duration_seconds=10)
    timeline = SimpleNamespace(snapshot={"montage": {"clips": [{"asset_id": "asset-1"}]}})
    task = SimpleNamespace(id="task-1", workspace_id="local", owner_user_id=None,
                           current_timeline_version_id="timeline-1")

    class Repo:
        task_records = SimpleNamespace(get_creation_task=lambda *_: task)
        timeline_records = SimpleNamespace(get_timeline_version=lambda *_: timeline)
        def get_asset(self, *_):
            return asset
        def workspace_media_reference_for_asset(self, *_):
            return "workspace://clips/asset-1.mp4"
        def resolve_clip_media_reference(self, *_):
            return "/controlled/clips/asset-1.mp4"
        def __init__(self):
            self.events = []
        class Conn:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, *_): return self
            def fetchall(self): return []
        def _connect(self): return self.Conn()

    class Media:
        task_authorizations = None
        def __init__(self): self.plans = []
        def submit(self, runtime, actor, task_id, operation, arguments, result_kind):
            self.plans.append((operation, arguments, result_kind))
            return SimpleNamespace(id="job-1")
        def read(self, workspace_id, job_id):
            return SimpleNamespace(id=job_id), {"arguments": {}}

    repo, media = Repo(), Media()
    use_case = TaskMediaSubmissionUseCase(repo, task_media=media)
    actor = SimpleNamespace(workspace_id="local", member_id="member-1", user_id=None)
    first = use_case.submit(SimpleNamespace(), actor, "task-1", _command())
    assert first.id == "job-1"
    operation, arguments, result_kind = media.plans[0]
    plan = arguments["edit_plan"]
    assert operation == "ffmpeg_execution" and result_kind == "media_output"
    assert plan["operations"][0]["inputs"] == ["/controlled/clips/asset-1.mp4"]
    assert plan["operations"][0]["options"]["asset_id"] == "asset-1"


def test_formal_post_route_returns_receipt_and_domain_error():
    actor = SimpleNamespace(workspace_id="local", member_id="member-1", user_id=None)
    runtime = SimpleNamespace(registrations={"creation_job": SimpleNamespace(owner=SimpleNamespace(task_media="media"))})
    app = FastAPI()

    def submit_for():
        def submit(_task_id, _actor, payload):
            BoundMediaSubmission.from_value(payload)
            return SimpleNamespace(id="job-1"), runtime
        return submit

    app.include_router(build_task_media_router(
        workspace_for=lambda: (lambda _request: "local"),
        actor_for=lambda: (lambda _request, _workspace: actor),
        task_for=lambda: None, list_for=lambda: None, read_for=lambda: None,
        read_components_for=lambda: None, runtime_for=lambda: runtime, repository_for=lambda: None,
        receipt_for=lambda: (lambda _media, job, _actor, _runtime: {"job_id": job.id}),
        observations_for=lambda: None, submit_for=submit_for,
        route_names=("submit_task_media_job",),
    ))
    client = TestClient(app)
    response = client.post("/api/creation-tasks/task/media-jobs", json=_command())
    assert response.status_code == 202 and response.json() == {"job_id": "job-1"}
    rejected = client.post("/api/creation-tasks/task/media-jobs", json={**_command(), "source_path": "/tmp/x.mp4"})
    assert rejected.status_code == 422 and "未支持" in rejected.json()["detail"]


def test_public_media_output_receipt_reads_inner_timeline_and_artifact_facts():
    job = SimpleNamespace(
        id="outer-job", workspace_id="local", task_id="task-1", input_digest="digest",
        status="succeeded", created_at=datetime.now(UTC), failure_reason=None, repair_action=None,
    )
    start = {
        "member_id": "member-1", "operation": "ffmpeg_execution", "result_kind": "media_output",
        "input_digest": "digest", "input_facts": {"base_version_id": "timeline-1"},
        "arguments": {"edit_plan": {"request_id": "request-1"}},
    }
    inner = {
        "media_job_id": "inner-job", "request_id": "request-1", "base_version_id": "timeline-1",
        "timeline_version_id": "timeline-2", "output_refs": ["media/inner-job/1-final.mp4"],
        "log_path": "/tmp/request-request-1.jsonl",
    }

    class Media:
        def read(self, workspace_id, job_id):
            return job, start

        def authorization_verdict(self, *_args, **_kwargs):
            return "ready"

        def check(self, *_args, **_kwargs):
            return "ready"

        def resource_admission_receipt(self, *_args, **_kwargs):
            return None

    class Events:
        def get_task_event(self, workspace_id, event_id):
            return SimpleNamespace(payload={"result": inner})

    class Repository:
        execution_records = Events()

    class Outputs:
        def published_outputs_current(self, _job):
            return True

    class Scheduler:
        def task_media_result_request(self, *_args):
            return None, {}

    result = MediaResultUseCase(Repository(), Outputs()).read(
        Media(), "task-1", "outer-job", CreationExecutionActor("local", "member-1"), Scheduler())
    assert result["status"] == "succeeded"
    assert result["result_kind"] == "media_output"
    assert result["result_url"] == "/api/creation-tasks/task-1/media-jobs/outer-job"
    assert result["result"]["timeline_version_id"] == "timeline-2"
    assert result["result"]["output_refs"] == ["media/inner-job/1-final.mp4"]

