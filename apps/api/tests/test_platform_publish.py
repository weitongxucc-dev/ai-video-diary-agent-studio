"""Publishing HTTP contract tests use only fake identity/browser and memory jobs."""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.platform_auth import PlatformAuthService
from app.creation_execution import CreationExecutionActor
from app.device_runtime import build_device_runtime
from app.device_scheduler import ProcessIdentity, VerifiedSession
from app.platform_publish.queue import platform_publish_registration, recover_platform_publish_jobs
from app.platform_publish.routes import build_platform_publish_router
from app.production import ProductionRepository, _json_payload
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask, TimelineVersion


class _Driver:
    async def goto(self, url): pass
    async def upload(self, selector, path): pass
    async def fill(self, selector, value): pass
    async def click(self, selector): pass
    async def read_text(self, selector):
        return {"[data-e2e='creator-avatar']": "avatar",
                "[data-e2e='creator-uid']": "creator-001",
                "[data-e2e='publish-success']": "发布成功",
                "[data-e2e='publish-result-id']": "12345678901234567890"}.get(selector, "")


class _Sessions:
    def __init__(self): self.driver = _Driver()
    def open(self, *args): return self.driver
    def invoke(self, _workspace, _user, _platform, action, *, timeout=30):
        assert timeout == 30
        return asyncio.run(action(self.driver))
    def close_all(self): pass


class _Connection:
    def execute(self, *_args, **_kwargs): return self
    def fetchone(self): return None


class _Repository:
    database_path = "/tmp/isolated-test.sqlite3"
    def __init__(self):
        self.jobs = {}
        self.events = []
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
    def __init__(self):
        self.registrations = {"platform_publish": object()}
        self.scheduler = _Scheduler()
        self.wakes = 0
    def wake(self): self.wakes += 1


class _HostResources:
    def admit(self, request):
        from app.host_resource_probes import HostResourceDecision
        return HostResourceDecision(True, dict(request.resources), {})
    def continue_unit(self, grant): return True
    def released(self, grant): return True


class _ProcessFacts:
    def current(self): return ProcessIdentity(os.getpid(), "fake-start", "fake-boot")
    def children_released(self, children): return True
    def recovery_state(self, holder, children): return "released"


class _DeviceAccess:
    def verify_session(self, heartbeat, actor):
        return VerifiedSession(heartbeat.session_id, heartbeat.workspace_id, actor.member_id)
    def can_view_device_summary(self, actor): return True


def _client(tmp_path, *, actor=None, workspace="ws-1", user_id="member-1"):
    repository = _Repository()
    sessions = _Sessions()
    auth = PlatformAuthService(tmp_path / "metadata.sqlite3", tmp_path / "profiles", session_manager=sessions)
    auth.verify_current(workspace, user_id, "douyin")
    runtime = _Runtime()
    task = SimpleNamespace(id="task-1", output_ready=True, current_timeline_version_id="timeline-7")

    def output(_workspace, _task, _user):
        return {"task": task, "timeline_version_id": "timeline-7", "logical_ref": "artifact:montage:current",
                "path": "/isolated/final.mp4", "sha256": "a" * 64, "size_bytes": 512}

    app = FastAPI()
    app.include_router(build_platform_publish_router(
        repository, workspace_for=lambda _request: workspace,
        content_reader=lambda _request, _workspace: (user_id, False),
        actor_for=lambda _request, current_workspace: actor or CreationExecutionActor(
            current_workspace, user_id,
        ),
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
    assert prepared.payload["content_size_bytes"] == 512
    assert prepared.payload["confirmed"] is True

    repeated = client.post(path, json=_payload())
    assert repeated.status_code == 202
    assert repeated.json()["id"] == body["id"]
    assert len(repository.jobs) == len(runtime.scheduler.intents) == 1
    assert runtime.wakes == 1


def test_confirmed_publish_persists_round_trippable_account_identity(tmp_path):
    workspace = "00000000-0000-4000-8000-000000000001"
    device = "00000000-0000-4000-8000-000000000002"
    user = "00000000-0000-4000-8000-000000000003"
    actor = CreationExecutionActor.account(workspace, device, user)
    client, repository, _runtime = _client(
        tmp_path, actor=actor, workspace=workspace, user_id=user,
    )

    response = client.post("/api/publishing/tasks/task-1/publish", json=_payload())

    assert response.status_code == 202, response.text
    prepared = next(event for event in repository.events if event.event_type == "platform_publish_prepared")
    identity = prepared.payload["identity"]
    assert identity == {
        **actor.identity_payload(), "workspace_id": workspace,
    }
    assert CreationExecutionActor.from_event(identity["workspace_id"], identity) == actor


class _PersistentSessions(_Sessions):
    def __init__(self, *, publish_status="发布成功", receipt="12345678901234567890"):
        super().__init__()
        self.publish_status = publish_status
        self.driver.receipt = receipt
        self.driver.publish_status = publish_status
        self.driver.calls = []


class _PersistentDriver(_Driver):
    def __init__(self, *, publish_status="发布成功", receipt="12345678901234567890"):
        self.publish_status = publish_status
        self.receipt = receipt
        self.calls = []

    async def goto(self, url): self.calls.append(("goto", url))
    async def upload(self, selector, path): self.calls.append(("upload", selector, path))
    async def fill(self, selector, value): self.calls.append(("fill", selector, value))
    async def click(self, selector): self.calls.append(("click", selector))
    async def read_text(self, selector):
        self.calls.append(("read", selector))
        return {
            "[data-e2e='creator-avatar']": "avatar",
            "[data-e2e='creator-uid']": "creator-001",
            "[data-e2e='publish-success']": self.publish_status,
            "[data-e2e='publish-result-id']": self.receipt,
        }.get(selector, "")


def _persistent_runtime_fixture(tmp_path, *, publish_status="发布成功", receipt="12345678901234567890"):
    database = tmp_path / "production.sqlite3"
    output_root = tmp_path / "output"
    output_root.mkdir()
    video_path = output_root / "final.mp4"
    video_path.write_bytes(b"isolated-video-bytes")
    sha256 = hashlib.sha256(video_path.read_bytes()).hexdigest()
    ref = "artifact:montage:current"
    workspace, member, task_id, version_id = "local", "member-1", "task-1", "timeline-7"
    repository = ProductionRepository(database, tmp_path / "library")
    plan = CreationPlan(
        id="plan-1", workspace_id=workspace, goal="测试本地发布队列", name="测试发布",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="fixture", model_snapshot=[], confirmation_points=[],
        blockers=[], preflight_fingerprint="f" * 64,
    )
    task = CreationTask(id=task_id, workspace_id=workspace, plan_id=plan.id,
                        name=plan.name, scenario=plan.goal)
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=member)
    repository.create_timeline_version(TimelineVersion(
        id=version_id, workspace_id=workspace, task_id=task_id, parent_version_id=None,
        sequence=1, snapshot={"montage": {"output_path": ref}, "output_refs": [ref]},
        created_by_operation_id=None,
    ))
    current = repository.task_records.get_creation_task(workspace, task_id)
    from dataclasses import replace
    current = replace(current, output_ready=True)
    with repository._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
                     (_json_payload(current), current.updated_at.isoformat(), workspace, task_id))

    driver = _PersistentDriver(publish_status=publish_status, receipt=receipt)
    sessions = _Sessions()
    sessions.driver = driver
    sessions.invoke = lambda _ws, _user, _platform, action, *, timeout=30: asyncio.run(action(driver))
    auth = PlatformAuthService(database, tmp_path / "profiles", session_manager=sessions)
    auth.verify_current(workspace, member, "douyin")

    def output_for(ws, current_task_id, user_id):
        persisted_task = repository.task_records.get_creation_task(ws, current_task_id)
        persisted_version = repository.get_timeline_version(ws, version_id)
        assert user_id == member and persisted_task.output_ready
        assert persisted_task.current_timeline_version_id == version_id
        assert persisted_version.snapshot["montage"]["output_path"] == ref
        assert video_path.stat().st_size == len(b"isolated-video-bytes")
        return {"task": persisted_task, "timeline_version_id": version_id,
                "logical_ref": ref, "path": str(video_path), "sha256": sha256,
                "size_bytes": video_path.stat().st_size}

    verifier = platform_publish_registration(
        repository, auth, publishable_output_for=output_for,
        verify_identity=lambda actor: "ready",
        actor_from_event=lambda identity: CreationExecutionActor.from_event(
            identity["workspace_id"], identity,
        ),
    )
    runtime = build_device_runtime(
        database_path=database, device_lock_path=tmp_path / "device.lock",
        device_access=_DeviceAccess(), registrations={"platform_publish": verifier},
        resources=_HostResources(), processes=_ProcessFacts(), poll_interval_seconds=0.01,
    )
    app = FastAPI()
    app.include_router(build_platform_publish_router(
        repository, workspace_for=lambda _request: workspace,
        content_reader=lambda _request, _workspace: (member, False),
        actor_for=lambda _request, current_workspace: CreationExecutionActor(
            current_workspace, member,
        ), auth_service=auth,
        task_for_user=lambda ws, current_task_id, user_id: repository.task_records.get_creation_task(ws, current_task_id),
        publishable_output_for=output_for, runtime_for=lambda: runtime,
    ))
    return TestClient(app), repository, runtime, driver, auth


@pytest.mark.parametrize(("status", "receipt", "expected"), [
    ("发布成功", "12345678901234567890", "succeeded"),
    ("发布失败", "12345678901234567890", "interrupted"),
])
def test_real_repository_device_runtime_persists_result_and_never_replays_unknown(
    tmp_path, status, receipt, expected,
):
    client, repository, runtime, driver, auth = _persistent_runtime_fixture(
        tmp_path, publish_status=status, receipt=receipt,
    )
    response = client.post("/api/publishing/tasks/task-1/publish", json=_payload())
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    recover_platform_publish_jobs(
        repository, runtime,
        actor_from_event=lambda identity: CreationExecutionActor.from_event(
            identity["workspace_id"], identity,
        ),
        verify_identity=lambda actor: "ready",
    )
    assert runtime.run_once() is True

    persisted = ProductionRepository(repository.database_path, repository.video_library_path)
    job = persisted.job_records.get_job_run("local", job_id)
    assert job.status == expected
    events = [event for event in persisted.execution_records.list_task_events("local", "task-1")
              if event.job_id == job_id]
    assert any(event.event_type == "platform_publish_submission_started" for event in events)
    if expected == "succeeded":
        success = next(event for event in events if event.event_type == "platform_publish_succeeded")
        assert success.payload["remote_publish_id"] == receipt
        assert len([call for call in driver.calls if call[0] == "click"]) == 1
    else:
        assert any(event.event_type == "platform_publish_unknown" for event in events)
        before = len([call for call in driver.calls if call[0] == "click"])
        recover_platform_publish_jobs(
            persisted, runtime,
            actor_from_event=lambda identity: CreationExecutionActor.from_event(
                identity["workspace_id"], identity,
            ),
            verify_identity=lambda actor: "ready",
        )
        assert runtime.run_once() is False
        assert len([call for call in driver.calls if call[0] == "click"]) == before == 1
    runtime.stop(timeout_seconds=1)
    auth.sessions.close_all()
