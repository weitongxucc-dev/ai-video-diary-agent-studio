"""Independent quality owner and one-grant executor in the existing device runtime."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

from app.creation_execution import CreationExecutionActor
from app.device_runtime import OwnerRegistration
from app.device_scheduler import RecoveryBlocked, SourceRef, VerifiedWork
from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout, run_granted_command
from app.host_resource_probes import HostResourceRefusal
from app.quality_worker_runtime import QualityBudget, QualityWorkerError, quality_phase_request, quality_unit_key
from app.creation.quality_inspection_use_case import QUALITY_KIND


@dataclass(frozen=True)
class QualityGrantContext:
    scheduler: object
    grant: object
    deadline_monotonic: float
    cancel_event: threading.Event

    def check(self):
        if self.cancel_event.is_set():
            raise GrantedCommandCancelled("检查已取消，请重新检查当前成片。")
        if time.monotonic() >= self.deadline_monotonic:
            raise GrantedCommandTimeout("质检单元总时限已到，检查未完成")
        self.scheduler.assert_grant(self.grant)


def run_quality_worker(context, request, temporary_root):
    context.check()
    budget = QualityBudget(**request["budget"])
    temporary_root = Path(temporary_root)
    temporary_root.mkdir(parents=True, exist_ok=True)
    input_path = temporary_root / f"quality_{request['check_id']}_{context.grant.generation}.json"
    encoded = json.dumps({**request, "deadline_monotonic": context.deadline_monotonic}, ensure_ascii=False).encode()
    if len(encoded) > budget.max_report_bytes:
        raise QualityWorkerError("质检封存请求超过本次已标定大小")
    input_path.write_bytes(encoded)
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve().parents[2] / 'server.py'))
    command += ["--quality-worker", str(input_path), str(budget.max_report_bytes)]
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    try:
        result = run_granted_command(context.scheduler, context.grant, command,
            timeout_seconds=context.deadline_monotonic - time.monotonic(),
            max_stdout_bytes=budget.max_report_bytes, max_stderr_bytes=budget.max_stderr_bytes,
            cancel_event=context.cancel_event, env=environment)
    except RecoveryBlocked:
        raise  # Retain the sealed input with an unknown/live group and held permit.
    except Exception:
        input_path.unlink(missing_ok=True)
        raise
    input_path.unlink(missing_ok=True)
    value = json.loads(result.stdout)
    if result.returncode or "error" in value:
        raise QualityWorkerError(value.get("error") or "质检 worker 未正常完成")
    return value


class QualityCheckOwnerVerifier:
    def __init__(self, service):
        self.service = service

    def _verified(self, workspace_id, job_id, *, connection=None):
        job, event, record, request = self.service.read(workspace_id, job_id, connection=connection)
        actor = CreationExecutionActor.from_event(workspace_id, event)
        if job.status not in {"queued", "interrupted", "running"} or record["check_state"] != "checking":
            raise ValueError("该质检已经结束")
        if request["actor"] != actor.identity_payload():
            raise PermissionError("质检原执行身份不匹配")
        if self.service.budget is None or request["budget"] != self.service.budget.__dict__:
            raise ValueError("质检独立预算尚未就绪或已经变化，请重新检查")
        self.service.verify_source(request, actor)
        source = request["source"]
        refs = (SourceRef(workspace_id, "artifact", source["artifact_id"], source["render_ref"]),)
        return job, event, record, quality_phase_request(request, record["evidence"]), actor, refs

    def verify_submission(self, intent, actor):
        job, event, record, request, original, refs = self._verified(intent.workspace_id, intent.owner_id)
        if (intent.owner_type, intent.input_digest, intent.unit_key, actor) != (
                QUALITY_KIND, job.input_digest, quality_unit_key(request), original):
            raise PermissionError("质检请求未绑定原检查和执行身份")
        return VerifiedWork(job.workspace_id, QUALITY_KIND, job.id, job.input_digest,
            intent.unit_key, original.member_id, "normal_make", request["resources"], refs)

    def check_current(self, request, conn):
        try:
            job, event, record, original, actor, refs = self._verified(request.workspace_id, request.owner_id, connection=conn)
            if (request.owner_type, request.input_digest, request.unit_key, request.member_id,
                    request.work_class, request.resources, request.source_refs) != (
                    QUALITY_KIND, job.input_digest, quality_unit_key(original), actor.member_id,
                    "normal_make", original["resources"], refs):
                return "stale"
            return "ready"
        except PermissionError:
            return "unauthorized"
        except (KeyError, OSError, ValueError):
            return "stale"

    def capture_local(self, request, conn):
        try:
            job, event, record, sealed = self.service.read(request.workspace_id, request.owner_id, connection=conn)
        except (KeyError, ValueError):
            return lambda current: "stale"
        source = sealed["source"]
        def read(current):
            job_row = current.execute(
                "SELECT task_id,input_digest,status FROM job_runs WHERE workspace_id=? AND id=?",
                (request.workspace_id, request.owner_id),
            ).fetchone()
            check = current.execute(
                """SELECT updated_at,check_state FROM creation_task_quality_history
                   WHERE workspace_id=? AND task_id=? AND check_id=?""",
                (request.workspace_id, job.task_id, record["check_id"]),
            ).fetchone()
            task = current.execute(
                """SELECT json_extract(payload,'$.owner_user_id'),json_extract(payload,'$.visibility')
                   FROM creation_tasks WHERE workspace_id=? AND id=?""",
                (request.workspace_id, job.task_id),
            ).fetchone()
            artifact = current.execute(
                "SELECT task_id,kind,logical_ref,sha256,size_bytes FROM artifact_records WHERE workspace_id=? AND id=?",
                (request.workspace_id, source["artifact_id"]),
            ).fetchone()
            return (tuple(job_row) if job_row else None, tuple(check) if check else None,
                    tuple(task) if task else None, tuple(artifact) if artifact else None,
                    vars(self.service.budget).copy() if self.service.budget else None)
        expected = read(conn)
        def check(current):
            if self.service.task_authorizations is not None:
                actor = CreationExecutionActor.from_event(request.workspace_id,event)
                verdict = self.service.authorization_verdict(request.workspace_id,job.task_id,actor,connection=current)
                if verdict != "ready":
                    return verdict
            actual = read(current)
            if actual[2] != expected[2]:
                return "unauthorized"
            return "ready" if actual == expected else "stale"
        return check

    def check_local(self, request, conn, observed):
        return observed(conn)

    def can_manage(self, request, actor):
        try:
            _, event, _, _ = self.service.read(request.workspace_id, request.owner_id)
            return request.owner_type == QUALITY_KIND and self.service.same_actor(request.workspace_id, event, actor)
        except (KeyError, ValueError):
            return False

    def can_view_detail(self, request, actor):
        if not self.can_manage(request, actor):
            return False
        try:
            job = self.service.production.get_job_run(request.workspace_id, request.owner_id)
            self.service.require_actor(request.workspace_id, job.task_id, actor)
            return True
        except (KeyError, PermissionError):
            return False

    def can_wake(self, request, actor):
        return self.can_view_detail(request, actor)

    def verify_retry(self, request, actor, reason, conn):
        raise PermissionError("请重新检查当前成片，以创建新的检查编号。")


class QualityCheckUnitExecutor:
    def __init__(self, service, *, worker=run_quality_worker):
        self.service, self.worker = service, worker
        self._cancellations = {}
        self._user_cancelled = set()
        self._lock = threading.Lock()
        self._stopping = False

    def request_cancel(self, request_id, *, actor=None):
        if actor is not None:
            self.service.cancel_queued(request_id, actor)
        with self._lock:
            event = self._cancellations.get(request_id)
            if event is not None:
                self._user_cancelled.add(request_id)
                event.set()

    def request_stop(self):
        with self._lock:
            self._stopping = True
            for event in self._cancellations.values():
                event.set()

    def _pause(self, grant, scheduler, job, check_id, *, reason):
        def save(connection, current):
            row = connection.execute("SELECT state,wait_reason FROM device_work_requests WHERE id=?",
                                     (current.request_id,)).fetchone()
            with self._lock:
                user_cancelled = current.request_id in self._user_cancelled
            if user_cancelled or row is not None and (row["state"] == "cancelled" or row["wait_reason"] == "user_cancelled"):
                raise GrantedCommandCancelled("user_cancelled")
            self.service.pause(connection,job,check_id,reason=reason)
        try:
            scheduler.defer_unit(grant,reason="worker_interrupted",
                retry_not_before_utc=datetime.now(timezone.utc)+timedelta(minutes=1),
                owner_database_path=self.service.production.database_path,owner_write=save)
        except GrantedCommandCancelled:
            return False  # Explicit user cancellation wins; the pause transaction rolled back.
        return True

    def _cancel(self, grant, scheduler, job, check_id, error):
        scheduler.complete_unit(grant,checkpoint_database_path=self.service.production.database_path,
            checkpoint_write=lambda conn,current: self.service.settle(conn,job,check_id,
                status="cancelled",error=error),cancelled=True)

    def execute(self, grant, scheduler):
        if grant.owner_type != QUALITY_KIND:
            raise PermissionError("质检不能借用其他阶段的许可")
        job, event, record, request = self.service.read(grant.workspace_id, grant.owner_id)
        request = quality_phase_request(request, record["evidence"])
        if (grant.input_digest, grant.unit_key) != (job.input_digest, quality_unit_key(request)):
            raise PermissionError("质检许可未绑定原检查输入")
        actor = CreationExecutionActor.from_event(grant.workspace_id, event)
        budget = QualityBudget(**request["budget"])
        with closing(self.service._connect()) as conn:
            permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
        if permit is None or not permit["granted_at_utc"]:
            raise PermissionError("质检缺少当前设备许可")
        elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds())
        cancellation = threading.Event()
        context = QualityGrantContext(scheduler, grant,
            time.monotonic() + budget.max_timeout_seconds - elapsed, cancellation)
        with self._lock:
            self._cancellations[grant.request_id] = cancellation
            if self._stopping:
                cancellation.set()
        with closing(self.service._connect()) as conn:
            row = conn.execute("SELECT state,wait_reason FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
        if row is not None and row["state"] == "yield_requested" and row["wait_reason"] == "user_cancelled":
            cancellation.set()
            with self._lock:
                self._user_cancelled.add(grant.request_id)
        try:
            try:
                context.check()
                if job.status in {"queued", "interrupted"}:
                    job = self.service.production.claim_job_run(job.workspace_id, job.id)
                result = record["evidence"].get("_worker_result")
                if result is None:
                    result = self.worker(context, request, self.service.temporary_root)
                    # A produced, bounded receipt survives Quit/expiry. Adoption
                    # still checks current source and authority below.
                    self.service.validate_result(request, result)
                    self.service.checks.store_worker_result(job.workspace_id, job.task_id, record["check_id"], result)
                self.service.validate_result(request, result)
                self.service.verify_source(request, actor)
                context.check()
            except GrantedCommandCancelled as exc:
                if self._stopping and self._pause(grant,scheduler,job,record["check_id"],reason="application_quit"):
                    return
                self._cancel(grant,scheduler,job,record["check_id"],str(exc))
                return
            except PermissionError:
                reason = "application_quit" if self._stopping else "task_authorization_unavailable"
                if not self._pause(grant,scheduler,job,record["check_id"],reason=reason):
                    self._cancel(grant,scheduler,job,record["check_id"],"user_cancelled")
                return
            except HostResourceRefusal as exc:
                if self._stopping:
                    if not self._pause(grant,scheduler,job,record["check_id"],reason="application_quit"):
                        self._cancel(grant,scheduler,job,record["check_id"],"user_cancelled")
                    return
                if not exc.recoverable_pressure:
                    scheduler.fail_unit(grant, reason="quality_resource_unavailable",
                        failure_write=lambda conn, current: self.service.settle(conn, job, record["check_id"],
                            status="failed", error=str(exc)))
                    return
                scheduler.defer_unit(grant, reason="resource_pressure", retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30))
                self.service.production.finish_job_run(job.workspace_id, job.id, status="interrupted",
                    failure_reason=str(exc), impact="检查等待资源恢复，未产生通过结论。", repair_action="资源恢复后继续原检查。")
                return
            except RecoveryBlocked:
                raise
            except Exception as exc:
                if self._stopping:
                    if not self._pause(grant,scheduler,job,record["check_id"],reason="application_quit"):
                        self._cancel(grant,scheduler,job,record["check_id"],"user_cancelled")
                    return
                scheduler.fail_unit(grant, reason="quality_check_failed",
                    failure_write=lambda conn, current: self.service.settle(conn, job, record["check_id"],
                        status="failed", error=str(exc)))
                return
            # Keep terminal SQL outside the worker-error handler. A late SQL error
            # rolls back the whole checkpoint and preserves its staged worker receipt.
            def checkpoint(connection,current):
                if self._stopping or cancellation.is_set():
                    raise GrantedCommandCancelled("质检已停止，未提交成功")
                return self.service.complete_result(connection,job,record["check_id"],result)
            try:
                scheduler.complete_unit(grant,checkpoint_database_path=self.service.production.database_path,
                    checkpoint_write=checkpoint,
                    next_unit_write=lambda conn, current: self.service.next_unit(conn, job, record["check_id"], actor))
            except GrantedCommandCancelled as exc:
                if not (self._stopping and self._pause(grant,scheduler,job,record["check_id"],reason="application_quit")):
                    self._cancel(grant,scheduler,job,record["check_id"],str(exc))
            except PermissionError:
                reason = "application_quit" if self._stopping else "task_authorization_unavailable"
                if not self._pause(grant,scheduler,job,record["check_id"],reason=reason):
                    self._cancel(grant,scheduler,job,record["check_id"],"user_cancelled")
        finally:
            with self._lock:
                self._cancellations.pop(grant.request_id, None)
                self._user_cancelled.discard(grant.request_id)


def quality_check_registration(service, *, worker=run_quality_worker):
    return OwnerRegistration(QualityCheckOwnerVerifier(service), QualityCheckUnitExecutor(service, worker=worker))
