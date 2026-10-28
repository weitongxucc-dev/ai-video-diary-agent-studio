"""PublishJob adapter over the existing DeviceRuntime + JobRun facts."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime

from app.device_scheduler import Grant, RetryReason, SchedulePolicy, SubmissionIntent, VerifiedWork, WorkRequest
from app.device_runtime import OwnerRegistration
from app.platform_browser import (
    PUBLISH_OPERATION_TIMEOUT_SECONDS, DouyinAdapter,
    PublishConfirm, PublishOutcomeUnknown,
)
from app.platform_browser.playwright_driver import PlaywrightSessionManager
from app.creation.contracts import TaskEvent

logger = logging.getLogger(__name__)


class PlatformPublishOwnerVerifier:
    def __init__(self, repository, auth_service, *, publishable_output_for,
                 verify_identity=None, actor_from_event=None) -> None:
        self.repository = repository
        self.auth_service = auth_service
        self.publishable_output_for = publishable_output_for
        self.verify_identity = verify_identity
        self.actor_from_event = actor_from_event

    def _facts(self, conn: sqlite3.Connection, workspace_id: str, job_id: str):
        row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = self.repository._job_run_from_row(row)
        event = conn.execute(
            "SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND event_type='platform_publish_prepared'",
            (workspace_id, job_id),
        ).fetchone()
        if event is None:
            raise KeyError("发布确认事件不存在")
        return job, json.loads(event["payload_json"])

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork:
        if intent.owner_type != "platform_publish" or getattr(actor, "workspace_id", None) != intent.workspace_id:
            raise PermissionError("发布执行身份或工作区不匹配")
        with self.repository._connect() as conn:
            job, event = self._facts(conn, intent.workspace_id, intent.owner_id)
        member_id = getattr(actor, "member_id", None)
        if (job.kind != "platform_publish" or job.status not in {"queued", "interrupted"}
                or job.input_digest != intent.input_digest or event.get("confirmed") is not True
                or event.get("identity", {}).get("member_id") != member_id
                or event.get("platform") != "douyin"):
            raise PermissionError("持久发布确认、成员或输入版本已变化")
        if self.verify_identity is not None and self.verify_identity(actor) != "ready":
            raise PermissionError("发布成员身份已失效")
        key = f"publish:{event['timeline_version_id']}:{job.input_digest}"
        if intent.unit_key != key:
            raise PermissionError("发布版本或单元摘要不匹配")
        size_bytes = event.get("content_size_bytes")
        if type(size_bytes) is not int or size_bytes <= 0:
            raise PermissionError("已确认发布内容缺少真实成片大小")
        return VerifiedWork(
            workspace_id=job.workspace_id, owner_type="platform_publish", owner_id=job.id,
            input_digest=job.input_digest, unit_key=key, member_id=member_id,
            work_class="normal_make", resources={
                "kind": "platform_publish", "size_bytes": size_bytes,
                "timeout_seconds": PUBLISH_OPERATION_TIMEOUT_SECONDS,
                "browser_channel": "chrome",
            },
        )

    def check_current(self, request: WorkRequest, conn: sqlite3.Connection):
        try:
            job, event = self._facts(conn, request.workspace_id, request.owner_id)
        except (KeyError, ValueError, TypeError):
            return "stale"
        if (job.status not in {"queued", "running", "interrupted"}
                or job.input_digest != request.input_digest
                or event.get("identity", {}).get("member_id") != request.member_id):
            return "stale"
        if job.status in {"queued", "interrupted"} and conn.execute(
            "SELECT 1 FROM task_events WHERE job_id=? AND event_type='platform_publish_submission_started'",
            (job.id,),
        ).fetchone():
            return "stale"  # A possibly-sent request is never eligible for automatic retry.
        task_row = conn.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                (job.workspace_id, job.task_id)).fetchone()
        if task_row is None:
            return "stale"
        task = self.repository._creation_task_from_payload(json.loads(task_row["payload"]))
        if task.current_timeline_version_id != event.get("timeline_version_id") or not task.output_ready:
            return "stale"
        try:
            output = self.publishable_output_for(
                job.workspace_id, job.task_id,
                event["identity"].get("user_id") or request.member_id,
            )
        except (PermissionError, KeyError):
            return "unauthorized"
        except (OSError, ValueError):
            return "dependency"
        if (output.get("timeline_version_id") != event.get("timeline_version_id")
                or output.get("sha256") != event.get("content_sha256")
                or output.get("logical_ref") != event.get("logical_ref")
                or output.get("size_bytes") != event.get("content_size_bytes")):
            return "stale"
        account = self.auth_service.get_account(job.workspace_id,
                                               event["identity"].get("user_id") or request.member_id,
                                               event["platform"])
        if account is None or account.account_ref != event.get("account_ref"):
            return "unauthorized"
        return "ready"

    def capture_local(self, request, conn):
        try:
            job, event = self._facts(conn, request.workspace_id, request.owner_id)
        except (KeyError, ValueError, TypeError):
            return lambda current: "stale"
        def read(current):
            job_row = current.execute(
                "SELECT task_id,input_digest,status FROM job_runs WHERE workspace_id=? AND id=?",
                (job.workspace_id, job.id),
            ).fetchone()
            task = current.execute(
                """SELECT json_extract(payload,'$.current_timeline_version_id'),
                   json_extract(payload,'$.output_ready'),json_extract(payload,'$.owner_user_id'),
                   json_extract(payload,'$.visibility') FROM creation_tasks WHERE workspace_id=? AND id=?""",
                (job.workspace_id, job.task_id),
            ).fetchone()
            sent = current.execute(
                "SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? AND event_type='platform_publish_submission_started'",
                (job.workspace_id, job.id),
            ).fetchone()
            artifact = current.execute(
                "SELECT id,sha256,size_bytes FROM artifact_records WHERE workspace_id=? AND task_id=? AND logical_ref=?",
                (job.workspace_id, job.task_id, event.get("logical_ref")),
            ).fetchone()
            account = current.execute(
                "SELECT account_ref FROM platform_browser_accounts WHERE workspace_id=? AND user_id=? AND platform=?",
                (job.workspace_id, event["identity"].get("user_id") or request.member_id, event["platform"]),
            ).fetchone()
            return (tuple(job_row) if job_row else None, tuple(task) if task else None,
                    bool(sent), tuple(artifact) if artifact else None,
                    account["account_ref"] if account else None)
        expected = read(conn)
        def check(current):
            actual = read(current)
            if actual[-1] is None or actual[-1] != expected[-1]:
                return "unauthorized"
            return "ready" if actual == expected else "stale"
        return check

    def check_local(self, request, conn, observed):
        return observed(conn)

    def can_manage(self, request: WorkRequest, actor: object) -> bool:
        try:
            with self.repository._connect() as conn:
                job, event = self._facts(conn, request.workspace_id, request.owner_id)
            return (getattr(actor, "workspace_id", None) == job.workspace_id
                    and getattr(actor, "member_id", None) == event["identity"].get("member_id"))
        except (KeyError, ValueError, TypeError):
            return False

    def can_wake(self, request: WorkRequest, actor: object) -> bool:
        return False

    def can_view_detail(self, request: WorkRequest, actor: object) -> bool:
        return self.can_manage(request, actor)

    def verify_retry(self, request: WorkRequest, actor: object, reason: RetryReason,
                     conn: sqlite3.Connection):
        from app.device_scheduler import VerifiedRetry
        if reason != "failed" or not self.can_manage(request, actor):
            raise PermissionError("发布只能在已有安全重试证据下由原成员重试")
        safe = conn.execute(
            "SELECT 1 FROM task_events WHERE job_id=? AND event_type='platform_publish_safe_retry'",
            (request.owner_id,),
        ).fetchone()
        if safe is None:
            raise PermissionError("未知或可能已提交的平台动作不得重试")
        def recheck(current):
            if current.execute("SELECT 1 FROM task_events WHERE job_id=? AND event_type='platform_publish_safe_retry'",
                               (request.owner_id,)).fetchone() is None:
                raise PermissionError("发布安全重试证据已经失效")
        return VerifiedRetry(request.input_digest, recheck)


class PlatformPublishExecutor:
    def __init__(self, repository, sessions: PlaywrightSessionManager,
                 *, publishable_output_for, actor_from_event=None) -> None:
        self.repository = repository
        self.sessions = sessions
        self.publishable_output_for = publishable_output_for
        self.actor_from_event = actor_from_event

    def _load(self, grant: Grant):
        with self.repository._connect() as conn:
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?",
                                (grant.workspace_id, grant.owner_id)).fetchone()
            event = conn.execute(
                "SELECT * FROM task_events WHERE workspace_id=? AND job_id=? AND event_type='platform_publish_prepared'",
                (grant.workspace_id, grant.owner_id),
            ).fetchone()
        if row is None or event is None:
            raise ValueError("持久发布作业或用户确认已丢失")
        return self.repository._job_run_from_row(row), self.repository.execution_records.task_event_from_row(event)

    def _event(self, conn, *, job, event_type: str, payload: dict[str, object]):
        event_id = f"event_{job.id}_{event_type.removeprefix('platform_publish_')}"
        self.repository.execution_records.append_task_event(TaskEvent(
            id=event_id, workspace_id=job.workspace_id, task_id=job.task_id,
            job_id=job.id, event_type=event_type, payload=payload, created_at=datetime.now(UTC),
        ), connection=conn)

    def execute(self, grant: Grant, scheduler) -> None:
        job, prepared = self._load(grant)
        self.repository.claim_job_run(job.workspace_id, job.id)
        p = prepared.payload
        actor = self.actor_from_event(p["identity"]) if self.actor_from_event else None
        account_id = p["identity"].get("user_id") or p["identity"]["member_id"]
        confirm = PublishConfirm(
            account_ref=str(p["account_ref"]), title=str(p["title"]),
            description=str(p["description"]), topics=tuple(p["topics"]),
            content_digest=str(p["content_digest"]), confirmed=p.get("confirmed") is True,
        )
        started = False

        def before_submit():
            nonlocal started
            scheduler.assert_grant(grant)
            with self.repository._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._event(conn, job=job, event_type="platform_publish_submission_started",
                            payload={"platform": "douyin", "account_ref": confirm.account_ref,
                                     "timeline_version_id": p["timeline_version_id"],
                                     "content_digest": confirm.content_digest})
            started = True

        try:
            scheduler.assert_grant(grant)
            output = self.publishable_output_for(job.workspace_id, job.task_id, account_id)
            if (output.get("timeline_version_id") != p["timeline_version_id"]
                    or output.get("sha256") != p["content_sha256"]
                    or output.get("logical_ref") != p["logical_ref"]
                    or output.get("size_bytes") != p["content_size_bytes"]):
                raise ValueError("确认后的有效成片版本或摘要已变化，发布未提交")
            adapter = DouyinAdapter()
            result = self.sessions.invoke(
                job.workspace_id, account_id, "douyin",
                lambda driver: attach_and_publish(adapter, driver, confirm,
                                                  str(output["path"]), before_submit),
                timeout=PUBLISH_OPERATION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            unknown = started or isinstance(exc, PublishOutcomeUnknown)
            status = "interrupted" if unknown else "failed"
            reason = "publish_outcome_unknown" if unknown else "publish_not_submitted"
            error_type = type(exc).__name__
            now = datetime.now(UTC).isoformat()

            def failure_write(conn, _grant):
                conn.execute(
                    "UPDATE job_runs SET status=?, failure_reason=?, impact=?, repair_action=?, finished_at=?, updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
                    (status, reason,
                     "平台动作可能已经提交；系统不会自动重发。" if unknown else "平台动作尚未开始提交。",
                     "先在平台账号后台核对结果；只有确认未提交后才能再次操作。" if unknown else "修复账号、成片或浏览器问题，再由用户重新确认。",
                     now, now, job.workspace_id, job.id),
                )
                self._event(conn, job=job,
                            event_type="platform_publish_unknown" if unknown else "platform_publish_safe_retry",
                            payload={"reason": reason, "error_type": error_type})

            scheduler.fail_unit(grant, reason=reason.replace("-", "_"), failure_write=failure_write)
            return

        now = datetime.now(UTC).isoformat()

        def checkpoint(conn, _grant):
            conn.execute(
                "UPDATE job_runs SET status='succeeded', progress=100, failure_reason=NULL, impact=NULL, repair_action=NULL, finished_at=?, updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
                (now, now, job.workspace_id, job.id),
            )
            self._event(conn, job=job, event_type="platform_publish_succeeded",
                        payload={"platform": result.platform, "account_ref": result.account_ref,
                                 "remote_publish_id": result.publish_id,
                                 "response_text": result.response_text,
                                 "content_digest": confirm.content_digest})
            return result.publish_id

        scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                checkpoint_write=checkpoint, actor=actor)


def attach_and_publish(adapter, driver, confirm, video_path, before_submit):
    adapter._driver = driver
    return adapter.publish(confirm, video_path, before_submit)


def platform_publish_registration(repository, auth_service, *, publishable_output_for,
                                  verify_identity=None, actor_from_event=None) -> OwnerRegistration:
    return OwnerRegistration(
        PlatformPublishOwnerVerifier(repository, auth_service,
                                     publishable_output_for=publishable_output_for,
                                     verify_identity=verify_identity,
                                     actor_from_event=actor_from_event),
        PlatformPublishExecutor(repository, auth_service.sessions,
                                publishable_output_for=publishable_output_for,
                                actor_from_event=actor_from_event),
    )


def recover_platform_publish_jobs(repository, runtime, *, actor_from_event,
                                 verify_identity) -> None:
    """Recover durable jobs without replaying any submission with uncertain outcome."""
    with repository._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM job_runs WHERE kind='platform_publish' AND status IN ('queued','running') ORDER BY created_at,id"
        ).fetchall()
    for row in rows:
        job = repository._job_run_from_row(row)
        events = repository.execution_records.list_task_events(job.workspace_id, job.task_id)
        prepared = next((event for event in events
                         if event.job_id == job.id and event.event_type == "platform_publish_prepared"), None)
        if prepared is None:
            logger.warning("PublishJob %s 缺少持久确认，保留人工检查", job.id)
            continue
        started = any(event.job_id == job.id and
                      event.event_type == "platform_publish_submission_started" for event in events)
        if job.status == "running" or (job.status == "queued" and started):
            now = datetime.now(UTC).isoformat()
            reason = "publish_outcome_unknown" if started else "publish_interrupted_before_submit"
            impact = "平台动作可能已经提交；系统不会自动重发。" if started else "浏览器动作未到平台提交边界，作业已中断。"
            repair = "先在平台账号后台核对结果；确认未提交后再由用户明确重试。" if started else "检查本机 Chrome 与队列后重新核对当前版本。"
            with repository._connect() as conn:
                conn.execute(
                    "UPDATE job_runs SET status='interrupted',failure_reason=?,impact=?,repair_action=?,finished_at=?,updated_at=? WHERE workspace_id=? AND id=? AND status=?",
                    (reason, impact, repair, now, now, job.workspace_id, job.id, job.status),
                )
                repository.execution_records.append_task_event(TaskEvent(
                    id=f"event_{job.id}_restart_recovered", workspace_id=job.workspace_id,
                    task_id=job.task_id, job_id=job.id,
                    event_type="platform_publish_recovered",
                    payload={"submission_started": started, "automatic_resend": False},
                    created_at=datetime.now(UTC),
                ), connection=conn)
        if started:
            # Even a stale/waiting device request remains ineligible via check_current.
            continue
        identity = prepared.payload.get("identity")
        if not isinstance(identity, dict):
            logger.warning("PublishJob %s 的持久成员身份不可复核", job.id)
            continue
        try:
            actor = actor_from_event(identity)
        except (KeyError, TypeError, ValueError):
            logger.warning("PublishJob %s 的持久成员身份不可复核", job.id)
            continue
        if verify_identity(actor) != "ready":
            logger.warning("PublishJob %s 的成员许可失效或未知，保留待处理", job.id)
            continue
        intent = SubmissionIntent(
            workspace_id=job.workspace_id, owner_type="platform_publish", owner_id=job.id,
            input_digest=job.input_digest,
            unit_key=f"publish:{prepared.payload['timeline_version_id']}:{job.input_digest}",
            policy=SchedulePolicy(mode="asap"),
        )
        try:
            runtime.scheduler.submit(intent, actor)
        except (PermissionError, ValueError) as exc:
            logger.warning("PublishJob %s 当前成员或版本不符，保留待处理：%s", job.id, type(exc).__name__)
    runtime.wake()
