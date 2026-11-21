from types import SimpleNamespace

import pytest

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
        get_asset = lambda self, *_: asset
        workspace_media_reference_for_asset = lambda self, *_: "workspace://clips/asset-1.mp4"
        resolve_clip_media_reference = lambda self, *_: "/controlled/clips/asset-1.mp4"
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

