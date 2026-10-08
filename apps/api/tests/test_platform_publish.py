"""Publishing HTTP contract tests use only fake identity/browser and memory jobs."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform_auth import PlatformAuthService
from app.platform_publish.routes import build_platform_publish_router


class _Driver:
    async def goto(self, url): pass
    async def upload(self, selector, path): pass
    async def fill(self, selector, value): pass
    async def click(self, selector): pass
    async def read_text(self, selector):
        return {"[data-e2e='creator-avatar']": "avatar",
                "[data-e2e='creator-name']": "creator-001"}.get(selector, "")


class _Sessions:
    def __init__(self): self.driver = _Driver()
    def open(self, *args): return self.driver
    def invoke(self, _workspace, _user, _platform, action): return asyncio.run(action(self.driver))


class _Connection:
    def execute(self, *_args, **_kwargs): return self
    def fetchone(self): return None


class _Repository:
    database_path = "/tmp/isolated-test.sqlite3"
    def __init__(self): self.jobs = {}; self.events = []
    @contextmanager
    def _connect(self): yield _Connection()
    def create_job_run(self, job, *, connection=None):
        self.jobs[job.id] = job
        return job
    def append_task_event(self, event, *, connection=None):
        self.events.append(event)
        return event
    def get_job_run_by_idempotency_key(self, _workspace, key):
        return next((job for job in self.jobs.values() if job.idempotency_key == key), None)
    def get_job_run(self, _workspace, job_id): return self.jobs[job_id]
    def list_job_runs(self, _workspace, task_id):
        return [job for job in self.jobs.values() if job.task_id == task_id]
    def list_task_events(self, _workspace, task_id):
        return [event for event in self.events if event.task_id == task_id]


class _Scheduler:
    def __init__(self): self.intents = []
    def submit(self, intent, actor): self.intents.append((intent, actor))


class _Runtime:
    def __init__(self): self.registrations = {"platform_publish": object()}; self.scheduler = _Scheduler(); self.wakes = 0
    def wake(self): self.wakes += 1


def _client(tmp_path):
    repository = _Repository()
    sessions = _Sessions()
    auth = PlatformAuthService(tmp_path / "metadata.sqlite3", tmp_path / "profiles", session_manager=sessions)
    auth.verify_current("ws-1", "member-1", "douyin")
    runtime = _Runtime()
    task = SimpleNamespace(id="task-1", output_ready=True, current_timeline_version_id="timeline-7")

    def output(_workspace, _task, _user):
        return {"task": task, "timeline_version_id": "timeline-7", "logical_ref": "artifact:montage:current",
                "path": "/isolated/final.mp4", "sha256": "a" * 64}

    app = FastAPI()
    app.include_router(build_platform_publish_router(
        repository, workspace_for=lambda _request: "ws-1",
        content_reader=lambda _request, _workspace: ("member-1", False),
        actor_for=lambda _request, workspace: SimpleNamespace(workspace_id=workspace, member_id="member-1"),
        auth_service=auth, task_for_user=lambda _workspace, _task, _user: task,
        publishable_output_for=output, runtime_for=lambda: runtime,
    ))
    return TestClient(app), repository, runtime


def _payload(**changes):
    value = {"platform": "douyin", "account_ref": "creator-001", "timeline_version_id": "timeline-7",
             "title": "已确认标题", "description": "完整发布说明", "topics": ["记录"],
             "idempotency_key": "client-request-0001", "confirmed": True}
    value.update(changes)
    return value


def test_publish_requires_explicit_confirmation_and_current_version(tmp_path):
    client, repository, runtime = _client(tmp_path)
    path = "/api/publishing/tasks/task-1/publish"
    response = client.post(path, json=_payload(confirmed=False))
    assert response.status_code == 409
    assert repository.jobs == {} and runtime.scheduler.intents == []

    response = client.post(path, json=_payload(timeline_version_id="old-version"))
    assert response.status_code == 409
    assert repository.jobs == {} and runtime.scheduler.intents == []


def test_confirmed_publish_becomes_one_persistent_job_and_queue_intent(tmp_path):
    client, repository, runtime = _client(tmp_path)
    path = "/api/publishing/tasks/task-1/publish"
    response = client.post(path, json=_payload())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["task_id"] == "task-1"
    assert len(repository.jobs) == len(runtime.scheduler.intents) == 1
    prepared = next(event for event in repository.events if event.event_type == "platform_publish_prepared")
    assert prepared.payload["account_ref"] == "creator-001"
    assert prepared.payload["timeline_version_id"] == "timeline-7"
    assert prepared.payload["confirmed"] is True

    repeated = client.post(path, json=_payload())
    assert repeated.status_code == 202
    assert repeated.json()["id"] == body["id"]
    assert len(repository.jobs) == len(runtime.scheduler.intents) == 1
    assert runtime.wakes == 1
