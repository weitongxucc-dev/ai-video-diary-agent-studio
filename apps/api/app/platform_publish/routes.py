"""Authenticated routes for explicitly confirmed browser publishing jobs.

The application supplies its current identity, workspace and valid-output
resolver. This module stores publish attempts as existing JobRun + TaskEvent
records; it does not create a second job database or accept client file paths.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.device_scheduler import SchedulePolicy, SubmissionIntent
from app.platform_auth import CAPABILITIES, PlatformAuthService
from app.production import JobRun, TaskEvent


class PublishRequest(BaseModel):
    platform: str
    account_ref: str
    timeline_version_id: str
    title: str = Field(min_length=1, max_length=30)
    description: str = Field(max_length=1000)
    topics: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=128)
    confirmed: bool


def _json_hash(data: object) -> str:
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _actor_fields(actor: object) -> dict[str, str]:
    workspace_id = getattr(actor, "workspace_id", None)
    member_id = getattr(actor, "member_id", None)
    if not isinstance(workspace_id, str) or not isinstance(member_id, str) or not member_id:
        raise HTTPException(403, "缺少当前成员的可持久身份")
    values = {"workspace_id": workspace_id, "member_id": member_id}
    for key in ("device_id", "user_id"):
        value = getattr(actor, key, None)
        if value:
            values[key] = str(value)
    return values


def build_platform_publish_router(
    repository,
    *,
    workspace_for: Callable[[Request], str],
    content_reader: Callable[[Request, str], tuple[str | None, bool]],
    actor_for: Callable[[Request, str], object],
    auth_service: PlatformAuthService,
    task_for_user: Callable[[str, str, str], object],
    publishable_output_for: Callable[[str, str, str], dict[str, object]],
    runtime_for: Callable[[], object | None],
) -> APIRouter:
    router = APIRouter(prefix="/api/publishing", tags=["publishing"])

    def require_member(request: Request, workspace_id: str):
        user_id, local_diagnostic = content_reader(request, workspace_id)
        if not user_id or local_diagnostic:
            raise HTTPException(403, "发布账号和任务需要当前已验证成员身份")
        actor = actor_for(request, workspace_id)
        fields = _actor_fields(actor)
        if fields["workspace_id"] != workspace_id:
            raise HTTPException(403, "成员身份与当前工作区不匹配")
        actor_user_id = getattr(actor, "user_id", None)
        actor_has_device = getattr(actor, "device_id", None) is not None
        if ((actor_user_id and str(actor_user_id) != user_id)
                or (not actor_has_device and fields["member_id"] != user_id)):
            raise HTTPException(403, "成员身份与可信内容读取身份不一致")
        return user_id, actor, fields

    @router.get("/platforms")
    def list_publish_platforms():
        return {"items": [vars(capability) for capability in CAPABILITIES.values()]}

    @router.get("/accounts")
    def list_publish_accounts(request: Request):
        workspace_id = workspace_for(request)
        user_id, _actor, _fields = require_member(request, workspace_id)
        return {"items": [{"platform": account.platform, "account_ref": account.account_ref,
                           "verified_at": account.verified_at}
                          for account in auth_service.list_accounts(workspace_id, user_id)]}

    @router.post("/accounts/{platform}/connect")
    def connect_publish_account(request: Request, platform: str):
        workspace_id = workspace_for(request)
        user_id, _actor, _fields = require_member(request, workspace_id)
        try:
            return auth_service.connect(workspace_id, user_id, platform)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/accounts/{platform}/verify")
    def verify_publish_account(request: Request, platform: str):
        workspace_id = workspace_for(request)
        user_id, _actor, _fields = require_member(request, workspace_id)
        try:
            return auth_service.verify_current(workspace_id, user_id, platform)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/tasks/{task_id}/jobs")
    def list_publish_jobs(request: Request, task_id: str):
        workspace_id = workspace_for(request)
        user_id, _actor, _fields = require_member(request, workspace_id)
        try:
            task = task_for_user(workspace_id, task_id, user_id)
        except KeyError as exc:
            raise HTTPException(404, "创作任务不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, "当前成员无权查看此创作任务") from exc
        # publishable_output_for applies the repository's content visibility and
        # current artifact/version validity rules before exposing job history.
        jobs = [job for job in repository.list_job_runs(workspace_id, task_id)
                if job.kind == "platform_publish"]
        events = repository.list_task_events(workspace_id, task_id)
        event_by_job = {}
        for event in events:
            if event.job_id and event.event_type.startswith("platform_publish_"):
                event_by_job.setdefault(event.job_id, []).append(event)
        return {"items": [serialize_job(repository, job, known_events=event_by_job.get(job.id, []))
                           for job in jobs]}

    @router.post("/tasks/{task_id}/publish", status_code=202)
    def create_publish_job(request: Request, task_id: str, payload: PublishRequest):
        workspace_id = workspace_for(request)
        user_id, actor, actor_fields = require_member(request, workspace_id)
        if payload.confirmed is not True:
            raise HTTPException(409, "请先明确确认本账号、当前成片和完整发布内容")
        capability = CAPABILITIES.get(payload.platform)
        if capability is None or not capability.browser_publish:
            raise HTTPException(409, "该平台尚未接通发布")
        output = publishable_output_for(workspace_id, task_id, user_id)
        task = output.get("task")
        if (task is None or not getattr(task, "output_ready", False)
                or output.get("timeline_version_id") != payload.timeline_version_id
                or getattr(task, "current_timeline_version_id", None) != payload.timeline_version_id):
            raise HTTPException(409, "当前成片或版本已变化；请重新检查并确认")
        account = auth_service.get_account(workspace_id, user_id, payload.platform)
        if account is None or account.account_ref != payload.account_ref:
            raise HTTPException(409, "尚未核验所选发布账号")
        # The current live browser identity is checked immediately before the
        # durable job is created. It must be the exact account the user chose.
        if payload.platform != "douyin":
            raise HTTPException(409, "该平台尚未接通发布")
        from app.platform_browser import DouyinAdapter
        adapter = DouyinAdapter()
        try:
            login = auth_service.sessions.invoke(
                workspace_id, user_id, payload.platform,
                lambda driver: attach_and_login(adapter, driver),
            )
        except Exception as exc:
            raise HTTPException(409, f"无法读取当前浏览器登录状态：{type(exc).__name__}") from exc
        if not login.logged_in or login.account_ref != payload.account_ref:
            raise HTTPException(409, "当前浏览器账号与确认账号不一致；请核验后重新确认")

        path = output.get("path")
        logical_ref = output.get("logical_ref")
        content_sha256 = output.get("sha256")
        if (not isinstance(path, str) or not isinstance(logical_ref, str)
                or not isinstance(content_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)):
            raise HTTPException(409, "当前有效成片缺少可验证的服务端引用或摘要")
        topics = tuple(dict.fromkeys(topic.strip().lstrip("#") for topic in payload.topics if topic.strip()))
        if any(len(topic) > 16 for topic in topics):
            raise HTTPException(422, "单个话题不能超过 16 个字符")
        content = {"platform": payload.platform, "account_ref": payload.account_ref,
                   "task_id": task_id, "timeline_version_id": payload.timeline_version_id,
                   "logical_ref": logical_ref, "content_sha256": content_sha256,
                   "content_size_bytes": output.get("size_bytes"),
                   "title": payload.title.strip(), "description": payload.description.strip(),
                   "topics": topics}
        if type(content["content_size_bytes"]) is not int or content["content_size_bytes"] <= 0:
            raise HTTPException(409, "当前成片缺少可验证的真实文件大小")
        digest = _json_hash(content)
        existing = repository.get_job_run_by_idempotency_key(workspace_id, payload.idempotency_key)
        if existing is not None:
            if existing.task_id != task_id or existing.kind != "platform_publish" or existing.input_digest != digest:
                raise HTTPException(409, "幂等键已用于不同的发布内容")
            return serialize_job(repository, existing)

        # A different key must not repeat a prior non-terminal or successful
        # send for the same confirmed account, version and body.
        events = repository.list_task_events(workspace_id, task_id)
        for event in events:
            if event.event_type == "platform_publish_prepared" and event.payload.get("content_digest") == digest:
                previous = repository.get_job_run(workspace_id, event.job_id)
                if previous.status in {"queued", "running", "succeeded", "interrupted"}:
                    raise HTTPException(409, "相同内容已有发布作业；未知或处理中结果不能自动重发")
                if not any(e.event_type == "platform_publish_safe_retry" for e in events if e.job_id == previous.id):
                    raise HTTPException(409, "既有发布失败没有可安全重试的证据")

        runtime = runtime_for()
        if runtime is None or "platform_publish" not in runtime.registrations:
            raise HTTPException(503, "持久发布队列尚未完成应用接线")
        job_id = f"publish_{uuid4().hex}"
        now = datetime.now(UTC)
        job = JobRun(job_id, workspace_id, task_id, "platform_publish",
                     payload.idempotency_key, digest)
        prepared = TaskEvent(
            id=f"event_{job_id}_prepared", workspace_id=workspace_id, task_id=task_id,
            job_id=job_id, event_type="platform_publish_prepared",
            payload={**content, "content_digest": digest,
                     "identity": actor_fields, "profile_key": account.profile_key,
                     "confirmed": True, "confirmed_at": now.isoformat()}, created_at=now,
        )
        with repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repository.create_job_run(job, connection=conn)
            repository.append_task_event(prepared, connection=conn)
        intent = SubmissionIntent(
            workspace_id=workspace_id, owner_type="platform_publish", owner_id=job_id,
            input_digest=digest, unit_key=f"publish:{payload.timeline_version_id}:{digest}",
            policy=SchedulePolicy(mode="asap"),
        )
        try:
            runtime.scheduler.submit(intent, actor)
        except Exception as exc:
            # No browser side effect has started. Mark safe-to-retry only after
            # an explicit retry path; this route never silently retries queueing.
            current = repository.get_job_run(workspace_id, job_id)
            if current.status == "queued":
                claimed = repository.claim_job_run(workspace_id, job_id)
                failed = repository.finish_job_run(
                    workspace_id, claimed.id, status="failed",
                    failure_reason="publish_queue_unavailable",
                    impact="发布尚未提交到平台。",
                    repair_action="修复本地发布队列后由用户重新确认并提交。",
                )
                repository.append_task_event(TaskEvent(
                    id=f"event_{job_id}_queue_failure", workspace_id=workspace_id,
                    task_id=task_id, job_id=job_id, event_type="platform_publish_queue_failure",
                    payload={"detail": type(exc).__name__}, created_at=datetime.now(UTC),
                ))
                return serialize_job(repository, failed)
            raise HTTPException(503, "发布队列拒绝了此作业") from exc
        runtime.wake()
        return serialize_job(repository, repository.get_job_run(workspace_id, job_id))

    return router


def attach_and_login(adapter, driver):
    adapter._driver = driver
    return adapter.login()


def serialize_job(repository, job: JobRun, *, known_events=None) -> dict[str, object]:
    events = [event for event in repository.list_task_events(job.workspace_id, job.task_id)
              if event.job_id == job.id and event.event_type.startswith("platform_publish_")] if known_events is None else known_events
    with repository._connect() as conn:
        row = conn.execute(
            "SELECT state,wait_reason FROM device_work_requests WHERE workspace_id=? AND owner_type='platform_publish' AND owner_id=? ORDER BY created_at DESC LIMIT 1",
            (job.workspace_id, job.id),
        ).fetchone()
    return {"id": job.id, "task_id": job.task_id, "status": job.status,
            "progress": job.progress, "failure_reason": job.failure_reason,
            "impact": job.impact, "repair_action": job.repair_action,
            "queue_state": str(row["state"]) if row is not None else "not_submitted",
            "queue_wait_reason": str(row["wait_reason"]) if row is not None and row["wait_reason"] else None,
            "events": [{"type": event.event_type, "payload": _public_event_payload(event),
                        "created_at": event.created_at.isoformat()} for event in events]}


def _public_event_payload(event: TaskEvent) -> dict[str, object]:
    allowed = {
        "platform_publish_prepared": {"platform", "account_ref", "task_id", "timeline_version_id",
                                       "title", "description", "topics", "content_digest", "confirmed"},
        "platform_publish_submission_started": {"platform", "account_ref", "timeline_version_id", "content_digest"},
        "platform_publish_succeeded": {"platform", "account_ref", "remote_publish_id", "response_text", "content_digest"},
        "platform_publish_unknown": {"reason", "error_type"},
        "platform_publish_safe_retry": {"reason", "error_type"},
        "platform_publish_queue_failure": {"detail"},
        "platform_publish_recovered": {"submission_started", "automatic_resend"},
    }.get(event.event_type, set())
    return {key: value for key, value in event.payload.items() if key in allowed}
