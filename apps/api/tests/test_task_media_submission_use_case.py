from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.creation.media_result_use_case import MediaResultUseCase
from app.creation_execution import CreationExecutionActor
from app.task_audio import TaskMediaJobs, media_digest
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
    assert result["result"]["output_urls"] == [
        "/api/creation-tasks/task-1/media-jobs/outer-job/outputs/1"]


def test_public_media_output_download_requires_registered_timeline_and_artifact(tmp_path):
    import hashlib

    from app.creation.media_result_use_case import MediaResultRejected

    outer = SimpleNamespace(
        id='outer-job', workspace_id='local', task_id='task-1', input_digest='digest',
        status='succeeded', created_at=datetime.now(UTC), failure_reason=None, repair_action=None,
    )
    ref = 'media/inner-job/1-final.mp4'
    start = {
        'member_id': 'member-1', 'operation': 'ffmpeg_execution', 'result_kind': 'media_output',
        'input_digest': 'digest', 'input_facts': {'base_version_id': 'timeline-1'},
        'arguments': {'edit_plan': {'request_id': 'request-1'}},
    }
    inner = {'job_id': 'inner-job', 'request_id': 'request-1', 'base_version_id': 'timeline-1',
             'timeline_version_id': 'timeline-2', 'output_refs': [ref]}
    output = tmp_path / 'final.mp4'
    output.write_bytes(b'video')

    class Media:
        def read(self, workspace_id, job_id):
            return outer, start

        def authorization_verdict(self, *_args, **_kwargs):
            return 'ready'

        def check(self, *_args, **_kwargs):
            return 'ready'

        def resource_admission_receipt(self, *_args, **_kwargs):
            return None

    class Events:
        def get_task_event(self, workspace_id, event_id):
            return SimpleNamespace(payload={'result': inner})

        def list_artifact_records(self, workspace_id, task_id):
            return [SimpleNamespace(logical_ref=ref, producer_job_id='inner-job', kind='media_output', size_bytes=5,
                                    sha256=hashlib.sha256(b'video').hexdigest())]

    class Timeline:
        def get_timeline_version(self, workspace_id, version_id):
            return SimpleNamespace(task_id='task-1', snapshot={'media_execution': {'job_id': 'inner-job', 'outputs': [ref]}})

    class Store:
        def resolve_file(self, logical_ref):
            assert logical_ref == ref
            return output

    class Repository:
        execution_records = Events()
        timeline_records = Timeline()

    class Outputs:
        artifact_store = Store()

        def published_outputs_current(self, _job):
            return True

    class Scheduler:
        def task_media_result_request(self, *_args):
            return None, {}

    use_case = MediaResultUseCase(Repository(), Outputs())
    actor = CreationExecutionActor('local', 'member-1')
    assert use_case.output_path(Media(), 'task-1', 'outer-job', 1, actor, Scheduler()) == output
    output.write_bytes(b'other')
    with pytest.raises(MediaResultRejected, match='登记摘要不一致') as changed:
        use_case.output_path(Media(), 'task-1', 'outer-job', 1, actor, Scheduler())
    assert changed.value.status_code == 409

    class MissingArtifacts(Events):
        def list_artifact_records(self, workspace_id, task_id):
            return []

    Repository.execution_records = MissingArtifacts()
    with pytest.raises(MediaResultRejected, match='尚未登记') as missing:
        use_case.output_path(Media(), 'task-1', 'outer-job', 1, actor, Scheduler())
    assert missing.value.status_code == 409


def test_public_media_output_download_rejects_cancelled_job_and_cross_workspace():
    from app.creation.media_result_use_case import MediaResultRejected

    job = SimpleNamespace(id='cancelled-job', workspace_id='local', task_id='task-1', input_digest='digest', status='cancelled',
                          created_at=datetime.now(UTC), failure_reason='media_cancelled', repair_action='重试原请求')
    event = {'member_id': 'member-1', 'operation': 'ffmpeg_execution', 'result_kind': 'media_output',
             'input_facts': {'base_version_id': 'timeline-1'}, 'arguments': {'edit_plan': {'request_id': 'request-1'}}}

    class Media:
        def read(self, workspace_id, job_id):
            return job, event

        def authorization_verdict(self, *_args, **_kwargs):
            return 'ready'

        def check(self, *_args, **_kwargs):
            return 'ready'

        def resource_admission_receipt(self, *_args, **_kwargs):
            return None

    class Outputs:
        def published_outputs_current(self, _job):
            return False

    class Scheduler:
        def task_media_result_request(self, *_args):
            return None, {}

    use_case = MediaResultUseCase(SimpleNamespace(), Outputs())
    with pytest.raises(MediaResultRejected, match='尚无可预览') as cancelled:
        use_case.output_path(Media(), 'task-1', 'cancelled-job', 1,
                             CreationExecutionActor('local', 'member-1'), Scheduler())
    assert cancelled.value.status_code == 409

    event['member_id'] = 'other-member'
    with pytest.raises(MediaResultRejected) as foreign:
        use_case.output_path(Media(), 'task-1', 'cancelled-job', 1,
                             CreationExecutionActor('local', 'member-1'), Scheduler())
    assert foreign.value.status_code == 404


def test_media_timeout_preserves_original_public_receipt_without_output(tmp_path, monkeypatch):
    from app import main
    from app.creation.contracts import TimelineVersion
    from app.media_execution import FFMPEG_OPERATION, EditPlan, MediaOperation, TaskMediaExecutionDelegate
    from test_task_media_grants import case

    repo, actor, _state, _factory, build, media = case(tmp_path)
    base = repo.timeline_records.create_timeline_version(
        TimelineVersion("timeline-1", "local", "task", None, 1, {}, None))
    request_id = _command()["request_id"]
    plan = EditPlan("local", "task", base.id, request_id,
                    (MediaOperation("cut", ("unused-fixture-input.mp4",), "output.mp4"),),
                    member_id=actor.member_id)

    class TimeoutEntryPoint:
        def submit(self, submitted_plan, *, cancel_event):
            assert submitted_plan == plan
            return {"status": "failed", "request_id": request_id, "failure_reason": "media_command_timeout",
                    "impact": "未发布新版本。", "repair_action": "检查输入后沿原作业重试。"}

    fixture_facts = media.facts

    def bound_facts(*args):
        facts, resources = fixture_facts(*args)
        return {**facts, "base_version_id": base.id}, resources

    media.facts = bound_facts
    media.media_execution_delegate = TaskMediaExecutionDelegate(TimeoutEntryPoint())
    runtime = build(media)
    client = TestClient(main.app)  # In-process requests only; do not run application lifespan.
    try:
        monkeypatch.setattr(main, "production_repo", repo)
        monkeypatch.setattr(main, "current_workspace_id", lambda _request: actor.workspace_id)
        monkeypatch.setattr(main, "_require_permit_actor", lambda *_: actor)
        monkeypatch.setattr(main, "_media_result_read_components", lambda: (media, runtime.scheduler))

        job = media.submit(runtime, actor, "task", FFMPEG_OPERATION, {"edit_plan": plan.as_dict()}, "media_output")
        result_url = main._media_result_receipt(media, job, actor, runtime)["result_url"]
        assert runtime.run_once()

        response = client.get(result_url)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["job_id"] == job.id and receipt["result_url"] == result_url
        assert receipt["base_version_id"] == base.id
        assert receipt["status"] == "failed" and receipt["result"] is None
        assert client.get(f"{result_url}/outputs/1").status_code == 409
        assert not repo.execution_records.list_artifact_records("local", "task")
        assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == base.id
        assert receipt["failure_reason"] == "media_command_timeout"
    finally:
        client.close()
        runtime.stop(0)


def test_successful_media_output_stays_current_after_publishing_child_version():
    actor = CreationExecutionActor("local", "member-1")
    event = {
        "member_id": actor.member_id,
        "operation": "ffmpeg_execution",
        "result_kind": "media_output",
        "input_facts": {"base_version_id": "timeline-base"},
        "resources": {},
        "arguments": {"edit_plan": {"request_id": "request-1"}},
    }
    event["input_digest"] = media_digest(event)
    job = SimpleNamespace(id="outer-job", workspace_id="local", task_id="task-1",
                          input_digest=event["input_digest"])
    task = SimpleNamespace(owner_user_id=None, current_timeline_version_id="timeline-child")

    class Events:
        def get_task_event(self, workspace_id, event_id):
            assert workspace_id == "local" and event_id == "event_outer-job_result"
            return SimpleNamespace(payload={"result": {
                "status": "succeeded", "request_id": "request-1",
                "timeline_version_id": "timeline-child", "output_refs": ["media/child/1.mp4"],
            }})

    repository = SimpleNamespace(
        execution_records=Events(),
        task_records=SimpleNamespace(get_creation_task=lambda *_args: task),
    )
    media = TaskMediaJobs(repository, facts=lambda *_args: ({}, {}),
                          verify_identity=lambda _actor: "ready")

    assert media.check(job, event, actor) == "ready"
    task.current_timeline_version_id = "timeline-other"
    assert media.check(job, event, actor) == "stale"

