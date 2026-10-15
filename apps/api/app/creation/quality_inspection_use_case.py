"""One durable quality request/current-history owner; no native work in API calls."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

from app.creation.contracts import JobRun, TaskEvent
from app.creation_execution import CreationExecutionActor
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SourceRef, SubmissionIntent, VerifiedWork
from app.device_runtime import RuntimeUnavailable
from app.montage import creation_task_montage_plan_from_payload
from app.production import content_readable
from app.quality_inspection import final_quality_check_payload
from app.quality_worker_runtime import QualityBudget, budget_identity, file_identity, source_receipt


QUALITY_KIND = "quality_check"


def quality_input_digest(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class QualityInspectionUseCase:
    def __init__(self, production, checks, outputs, *, budget: QualityBudget | None,
                 verify_identity, temporary_root: Path, budget_error: str | None = None):
        self.production, self.checks, self.outputs = production, checks, outputs
        self.budget, self.budget_error = budget, budget_error
        self.verify_identity, self.temporary_root = verify_identity, Path(temporary_root)

    def _connect(self):
        if Path(self.production.database_path).resolve() != Path(self.checks.database_path).resolve():
            raise ValueError("质检、作业和设备许可必须使用同一数据库")
        connection = sqlite3.connect(self.production.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def require_actor(self, workspace_id, task_id, actor):
        if actor.workspace_id != workspace_id or self.verify_identity(actor) != "ready":
            raise PermissionError("质检执行身份失效或尚未就绪")
        task = self.production.get_creation_task(workspace_id, task_id)
        if not content_readable(user_id=actor.user_id or actor.member_id, local_diagnostic=False,
                                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise PermissionError("当前成员无权检查此创作任务")
        return task

    def source_metadata(self, workspace_id, task_id):
        task, plan, path, record = self.outputs.quality_source_metadata(workspace_id, task_id)
        source = {"workspace_id": workspace_id, "task_id": task_id,
            "timeline_version_id": task.current_timeline_version_id, "source_ref": plan.output_path,
            "artifact_id": record.id, "render_ref": record.sha256, "size_bytes": record.size_bytes,
            "file_identity": file_identity(path), "path": str(path)}
        return task, plan, source

    def resources(self, source, budget):
        return {"kind": QUALITY_KIND, "execution_mode": "isolated_quality_python",
            "input_bytes": source["size_bytes"], "size_bytes": source["size_bytes"],
            "timeout_seconds": budget.max_timeout_seconds, "threads": budget.decoder_threads,
            "max_output_bytes": budget.max_report_bytes, "max_pcm_bytes": budget.max_pcm_bytes,
            "max_frame_json_bytes": budget.max_frame_json_bytes, "max_stderr_bytes": budget.max_stderr_bytes,
            "budget_digest": budget_identity(budget)}

    def projection(self, workspace_id, task_id):
        task = self.production.get_creation_task(workspace_id, task_id)
        try:
            plan = creation_task_montage_plan_from_payload(self.production.get_creation_task_montage_plan(workspace_id, task_id))
        except KeyError:
            plan = None
        try:
            _, _, source = self.source_metadata(workspace_id, task_id)
        except (KeyError, OSError, ValueError):
            source = None
        return final_quality_check_payload(self.checks.get(workspace_id, task_id), plan,
            timeline_version_id=task.current_timeline_version_id,
            current_render_ref=source["render_ref"] if source is not None else None, current_source=source)

    def read(self, workspace_id, job_id, *, connection=None):
        job = self.production.get_job_run(workspace_id, job_id)
        event = self.production.get_task_event(workspace_id, f"event_{job_id}_start").payload
        if job.kind != QUALITY_KIND:
            raise ValueError("该作业不属于独立质检")
        record = self.checks.get_check(workspace_id, job.task_id, event["check_id"], connection=connection)
        request = record["evidence"].get("_request")
        if request is None:
            raise ValueError("质检缺少原封存请求")
        if job.input_digest != quality_input_digest(request) or event["input_digest"] != job.input_digest:
            raise ValueError("质检封存输入摘要已变化")
        source = request["source"]
        if (request["check_id"], source["workspace_id"], source["task_id"], source["timeline_version_id"],
                source["source_ref"], source["render_ref"]) != (record["check_id"], workspace_id, job.task_id,
                record["timeline_version_id"], record["source_ref"], record["render_ref"]):
            raise ValueError("质检封存来源与原检查不一致")
        return job, event, record, request

    def start_for_render(self, runtime, workspace_id, task_id, render_job_id):
        try:
            if render_job_id is None:
                raise ValueError("自动质检缺少原制作作业身份，请使用可信成员重新检查。")
            job = self.production.get_job_run(workspace_id, render_job_id)
            if job.task_id != task_id:
                raise ValueError("自动质检与原制作任务不一致")
            event = self.production.get_task_event(workspace_id, f"event_{job.id}_start")
            actor = CreationExecutionActor.from_event(workspace_id, event.payload)
            return self.start(runtime, actor, workspace_id, task_id)
        except (KeyError, PermissionError, ValueError) as exc:
            task = self.production.get_creation_task(workspace_id, task_id)
            try:
                _, _, source = self.source_metadata(workspace_id, task_id)
            except (KeyError, OSError, ValueError):
                source = None
            with closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                check_id = self.checks.begin(workspace_id, task_id, task.current_timeline_version_id,
                    source["source_ref"] if source else None, source["render_ref"] if source else None,
                    source_identity=source, connection=conn)
                self.checks.finish(workspace_id, task_id, check_id,
                    evidence={"_source_input": source_receipt(source, check_id)} if source else {},
                    error=str(exc), connection=conn)
            return check_id

    @staticmethod
    def same_actor(workspace_id, event, actor):
        return CreationExecutionActor.from_event(workspace_id, event) == actor

    def verify_source(self, request, actor):
        source = request["source"]
        self.require_actor(source["workspace_id"], source["task_id"], actor)
        record = self.production.get_artifact_record(source["workspace_id"], source["artifact_id"])
        if (record.task_id, record.kind, record.logical_ref, record.sha256, record.size_bytes) != (
                source["task_id"], "rendered_video", source["source_ref"], source["render_ref"], source["size_bytes"]):
            raise ValueError("质检固定来源产物已变化")
        if source["timeline_version_id"] is not None:
            version = self.production.get_timeline_version(source["workspace_id"], source["timeline_version_id"])
            if version.task_id != source["task_id"] or source["source_ref"] not in version.snapshot.get("output_refs", []):
                raise ValueError("质检固定版本与登记产物不一致")
        path = self.outputs.artifact_store.resolve_file(source["source_ref"])
        if str(path) != source["path"] or file_identity(path) != source["file_identity"]:
            raise ValueError("检查期间成片内容已变化，请重新检查当前成片。")

    def start(self, runtime, actor, workspace_id, task_id, *, force=False):
        check_id, job = self._reserve(actor, workspace_id, task_id, force=force)
        if job is None:
            return check_id
        try:
            if runtime is None:
                raise ValueError("整机质检许可运行时尚未就绪，请恢复后重新检查。")
            runtime.scheduler.submit(SubmissionIntent(workspace_id, QUALITY_KIND, job.id, job.input_digest,
                f"quality:{check_id}", SchedulePolicy()), actor)
            runtime.wake()
        except (ValueError, PermissionError, RuntimeUnavailable) as exc:
            with closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                self.settle(conn, job, check_id, status="cancelled", error=str(exc))
        # An uncertain SQLite submission outcome is deliberately propagated.
        # Repeating the same active request submits its original durable job/key
        # idempotently, covering the reservation-to-queue crash gap.
        return check_id

    def _reserve(self, actor, workspace_id, task_id, *, force):
        task = self.require_actor(workspace_id, task_id, actor)
        try:
            task, plan, source = self.source_metadata(workspace_id, task_id)
        except (KeyError, OSError, ValueError) as exc:
            try:
                ref = self.production.get_creation_task_montage_plan(workspace_id, task_id).get("output_path")
            except KeyError:
                ref = None
            with closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                check_id = self.checks.begin(workspace_id, task_id, task.current_timeline_version_id, ref, None, connection=conn)
                self.checks.finish(workspace_id, task_id, check_id, error=str(exc), connection=conn)
            return check_id, None
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.checks.get(workspace_id, task_id, connection=conn)
            projected = final_quality_check_payload(current, plan, timeline_version_id=task.current_timeline_version_id,
                current_render_ref=source["render_ref"], current_source=source)
            if current is not None and not projected["stale"]:
                active = current["evidence"].get("_request")
                if (current["check_state"] == "checking" and self.budget is not None
                        and active is not None and active["budget"] == asdict(self.budget)):
                    job, event, _, _ = self.read(workspace_id, f"job_quality_{current['check_id']}", connection=conn)
                    return current["check_id"], job if self.same_actor(workspace_id, event, actor) else None
                if not force and current["check_state"] == "done":
                    return current["check_id"], None
            check_id = self.checks.begin(workspace_id, task_id, task.current_timeline_version_id,
                source["source_ref"], source["render_ref"], source_identity=source, connection=conn)
            if self.budget is None or source["size_bytes"] > self.budget.max_input_bytes:
                error = self.budget_error or ("质检独立资源预算尚未标定，请配置后重新检查。" if self.budget is None
                    else "成片超过质检独立输入预算，检查未开始。")
                self.checks.finish(workspace_id, task_id, check_id,
                    evidence={"_source_input": source_receipt(source, check_id)}, error=error, connection=conn)
                return check_id, None
            request = {"source": source, "check_id": check_id, "actor": actor.identity_payload(),
                "budget": asdict(self.budget), "resources": self.resources(source, self.budget), "thresholds": {}}
            digest = quality_input_digest(request)
            job = self.production.create_job_run(JobRun(id=f"job_quality_{check_id}", workspace_id=workspace_id,
                task_id=task_id, kind=QUALITY_KIND, idempotency_key=f"quality:{check_id}", input_digest=digest), connection=conn)
            self.checks.seal_request(workspace_id, task_id, check_id, request, connection=conn)
            self.production.append_task_event(TaskEvent(id=f"event_{job.id}_start", workspace_id=workspace_id,
                task_id=task_id, job_id=job.id, event_type="quality_check_submitted",
                payload={"check_id": check_id, "input_digest": digest, **actor.identity_payload()}), connection=conn)
        return check_id, job

    def validate_result(self, request, result):
        budget = QualityBudget(**request["budget"])
        if (not isinstance(result, dict) or result.get("worker_sha256") != budget.worker_sha256
                or result.get("receipt") != source_receipt(request["source"], request["check_id"])
                or not isinstance(result.get("evidence"), dict)
                or result["evidence"].get("status") not in {"clean", "issues_found", "unavailable"}
                or len(json.dumps(result, ensure_ascii=False).encode()) > budget.max_report_bytes):
            raise ValueError("质检 worker 回执未绑定原请求或超过有限大小")

    def settle(self, connection, job, check_id, *, status, result=None, error=None):
        record = self.checks.get_check(job.workspace_id, job.task_id, check_id, connection=connection)
        private = {key: value for key, value in record["evidence"].items() if key in {"_request", "_source_input"}}
        evidence = ({**private, **result["evidence"], "_source_receipt": result["receipt"]} if result is not None else private)
        self.checks.finish(job.workspace_id, job.task_id, check_id, evidence=evidence, error=error, connection=connection)
        self.production.finish_job_run(job.workspace_id, job.id, status=status, output_refs=[],
            failure_reason=error, impact="检查未完成，原成片保持不变。" if error is not None else None,
            repair_action="恢复质检执行条件后重新检查当前成片。" if error is not None else None, connection=connection)
        event = TaskEvent(id=f"event_{job.id}_quality_terminal", workspace_id=job.workspace_id, task_id=job.task_id,
            job_id=job.id, event_type="quality_check_terminal", payload={"check_id": check_id, "status": status})
        self.production.append_task_event(event, connection=connection)
        return event.id

    def cancel_queued(self, request_id, actor):
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT owner_id,state FROM device_work_requests WHERE id=? AND workspace_id=? AND owner_type=?",
                               (request_id, actor.workspace_id, QUALITY_KIND)).fetchone()
            if row is None:
                return
            job, event, record, _ = self.read(actor.workspace_id, row["owner_id"], connection=conn)
            if not self.same_actor(actor.workspace_id, event, actor):
                raise PermissionError("不能取消其他成员的质检请求")
            if row["state"] == "cancelled" and job.status in {"queued", "interrupted"}:
                self.settle(conn, job, record["check_id"], status="cancelled", error="检查已取消，请重新检查当前成片。")

    def recover(self, scheduler):
        if scheduler is None or Path(scheduler.database_path).resolve() != Path(self.production.database_path).resolve():
            raise RecoveryBlocked("质检缺少同一数据库的设备释放核验，原检查保持未完成。")
        generation = scheduler.reconcile_released()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            permit = conn.execute("SELECT state,generation FROM device_permit WHERE id=1").fetchone()
            if permit is None or (permit["state"], permit["generation"]) != ("free", generation):
                raise RecoveryBlocked("设备许可在恢复前已变化，原检查保持未完成。")
            rows = conn.execute("SELECT workspace_id,id,status FROM job_runs WHERE kind=? AND status IN ('queued','interrupted','running')",
                                (QUALITY_KIND,)).fetchall()
            for row in rows:
                job, original, record, request = self.read(row["workspace_id"], row["id"], connection=conn)
                actor = CreationExecutionActor.from_event(job.workspace_id, original)
                result = record["evidence"].get("_worker_result")
                error = "服务中断，检查未完成，请重新检查当前成片。"
                status = "failed" if row["status"] == "running" else "cancelled"
                terminal_result = None
                if result is not None:
                    try:
                        if request["actor"] != actor.identity_payload():
                            raise PermissionError("质检原执行身份不匹配")
                        self.verify_source(request, actor)
                        self.validate_result(request, result)
                    except (KeyError, PermissionError, OSError, ValueError, TypeError) as exc:
                        error = str(exc)
                    else:
                        if job.status in {"queued", "interrupted"}:
                            job = self.production.claim_job_run(job.workspace_id, job.id, connection=conn)
                        status, terminal_result, error = "succeeded", result, None
                checkpoint = self.settle(conn, job, record["check_id"], status=status, result=terminal_result, error=error)
                source = request["source"]
                expected = VerifiedWork(job.workspace_id, QUALITY_KIND, job.id, job.input_digest,
                    f"quality:{record['check_id']}", actor.member_id, "normal_make", request["resources"],
                    (SourceRef(job.workspace_id, "artifact", source["artifact_id"], source["render_ref"]),))
                scheduler.complete_recovered_quality(conn, expected, generation=generation,
                    checkpoint_ref=checkpoint, status=status)
            self.checks.recover_interrupted(connection=conn)
