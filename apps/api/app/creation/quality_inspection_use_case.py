"""One durable quality request/current-history owner; no native work in API calls."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import logging
import re
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.creation.contracts import JobRun, TaskEvent
from app.creation.task_authorization import TaskAuthorizationDenied
from app.creation_execution import CreationExecutionActor
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SourceRef, SubmissionIntent, VerifiedWork, Window
from app.device_runtime import RuntimeUnavailable
from app.montage import creation_task_montage_plan_from_payload
from app.source_policy import knowledge_item_readable as content_readable
from app.quality_inspection import final_quality_check_payload
from app.quality_checks import fit_quality_result
from app.quality_worker_runtime import (QualityBudget, file_identity, source_receipt,
    QUALITY_PHASE_PROTOCOLS, QUALITY_STREAM_PROTOCOL, quality_phase_request, quality_unit_key, quality_stream_resources, quality_request_byte_limit,
    validate_phase_measurement, quality_attempt_digest, QUALITY_PHASES)


QUALITY_KIND = "quality_check"
logger = logging.getLogger(__name__)


def quality_input_digest(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class QualityInspectionUseCase:
    def __init__(self, production, checks, outputs, *, budget: QualityBudget | None,
                 verify_identity, temporary_root: Path, budget_error: str | None = None, task_authorizations=None,
                 current_policy=None):
        self.production, self.checks, self.outputs = production, checks, outputs
        self.budget, self.budget_error = budget, budget_error
        self.verify_identity, self.temporary_root = verify_identity, Path(temporary_root)
        self.task_authorizations = task_authorizations
        self.current_policy = current_policy

    def _current_preparation_policy(self):
        selected = self.current_policy() if self.current_policy is not None else None
        if not isinstance(selected, dict) or not isinstance(selected.get("budget"), QualityBudget):
            raise ValueError("质检缺少当前设备策略来源，不能修订原失败阶段")
        budget, profile = selected["budget"], selected.get("profile")
        if (budget != self.budget or not isinstance(profile, dict)
                or set(profile) != {"profile_id", "revision", "digest", "source_kind"}
                or profile["source_kind"] != "device_profile"
                or not isinstance(profile["profile_id"], str) or not profile["profile_id"]
                or type(profile["revision"]) is not int or profile["revision"] <= 0
                or not isinstance(profile["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", profile["digest"])):
            raise ValueError("质检策略修订尚未绑定当前运行设备档")
        return {"budget": asdict(budget), "profile": dict(profile), "mode": "bounded_preparation"}

    def _read_failed_phase_link(self, workspace_id, job_id, failed_request_id, actor):
        job, original, record, sealed = self.read(workspace_id, job_id)
        if not self.same_actor(workspace_id, original, actor):
            raise PermissionError("不能接续其他成员的质检")
        self.require_actor(workspace_id, job.task_id, actor)
        with closing(self._connect()) as reader:
            failed = reader.execute("SELECT * FROM device_work_requests WHERE id=? AND workspace_id=? "
                "AND owner_type=? AND owner_id=? AND input_digest=?",
                (failed_request_id, workspace_id, QUALITY_KIND, job_id, job.input_digest)).fetchone()
            if failed is None or failed["state"] != "failed" or failed["last_grant_generation"] is None:
                raise RecoveryBlocked("原阶段没有明确失败且已授予的执行回执")
            source = sealed["source"]
            refs = [vars(SourceRef(workspace_id, "artifact", source["artifact_id"], source["render_ref"]))]
            if failed["member_id"] != actor.member_id or json.loads(failed["source_refs_json"]) != refs:
                raise PermissionError("原失败请求的成员或固定来源不匹配")
            successor = reader.execute("SELECT r.* FROM device_work_requests r JOIN device_request_events e "
                "ON e.request_id=? AND e.event_type='retried' AND e.reason='continued_as:' || r.id "
                "WHERE r.workspace_id=? AND r.owner_type=? AND r.owner_id=? AND r.input_digest=?",
                (failed_request_id, workspace_id, QUALITY_KIND, job_id, job.input_digest)).fetchone()
        receipt = None
        if successor is not None:
            if (successor["member_id"] != failed["member_id"]
                    or successor["work_class"] != failed["work_class"]
                    or json.loads(successor["source_refs_json"]) != refs):
                raise PermissionError("质检后继请求的成员或固定来源不匹配")
            digest = successor["unit_key"].rsplit(":", 1)[-1]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("质检后继缺少封存尝试身份")
            event = self.production.execution_records.get_task_event(workspace_id, f"event_{job_id}_quality_attempt_{digest}")
            payload = event.payload
            attempt = payload.get("attempt", {})
            if (event.task_id != job.task_id or event.job_id != job_id
                    or event.event_type != "quality_check_phase_attempt_created"
                    or payload.get("check_id") != record["check_id"] or payload.get("new_request_id") != successor["id"]
                    or attempt.get("previous_request_id") != failed_request_id
                    or attempt.get("previous_generation") != failed["last_grant_generation"]
                    or attempt.get("digest") != digest):
                raise ValueError("质检后继未绑定原失败尝试回执")
            phase = attempt.get("starting_phase")
            if phase not in QUALITY_PHASES:
                raise ValueError("质检后继阶段无效")
            prefix = record["evidence"].get("_phases", {})
            request = quality_phase_request(sealed, {"_phase_attempt": attempt,
                "_phases": {key: prefix[key] for key in QUALITY_PHASES[:QUALITY_PHASES.index(phase)] if key in prefix}})
            base = f"quality:{record['check_id']}:{phase}"
            if (failed["unit_key"] != base and not failed["unit_key"].startswith(base + ":attempt:")
                    or quality_unit_key(request) != successor["unit_key"]
                    or request["resources"] != json.loads(successor["resources_json"])):
                raise ValueError("质检后继的阶段或资源封存与原回执不一致")
            receipt = {"check_id": record["check_id"], "job_id": job_id, "request_id": successor["id"]}
        return job, original, record, sealed, failed, receipt

    def continued_phase_receipt(self, workspace_id, job_id, failed_request_id, actor):
        """Read an already committed successor without runtime/profile/media IO.

        Authority and immutable request/source lineage still apply. The caller
        decides whether a terminal Job permits its no-runtime replay shortcut.
        """
        return self._read_failed_phase_link(workspace_id, job_id, failed_request_id, actor)[-1]

    def continue_failed_phase(self, scheduler, workspace_id, job_id, failed_request_id, actor, *, write_guard=None):
        """Explicit finite preparation under the original check/Job and prefix.

        Policy is read from the trusted bound runtime, never from tool/HTTP caps.
        This does not learn a speed, change the original request, or retry unknown
        work. The scheduler owns released-failure fencing and the shared commit.
        """
        job, original, record, sealed, failed, receipt = self._read_failed_phase_link(
            workspace_id, job_id, failed_request_id, actor)
        if receipt is not None:
            return receipt["request_id"]
        with closing(self._connect()) as reader:
            task_access = reader.execute("SELECT json_extract(payload,'$.owner_user_id'),"
                "json_extract(payload,'$.visibility') FROM creation_tasks WHERE workspace_id=? AND id=?",
                (workspace_id, job.task_id)).fetchone()
        if job.status != "failed" or record["check_state"] != "failed" or sealed.get("protocol") != QUALITY_STREAM_PROTOCOL:
            raise ValueError("仅可接续原流式检查的明确失败阶段")
        request = quality_phase_request(sealed, record["evidence"])
        if (failed["unit_key"] != quality_unit_key(request)
                or json.loads(failed["resources_json"]) != request["resources"]):
            raise ValueError("原失败请求不属于当前未完成阶段")
        self.verify_source(request, actor)
        selected = self._current_preparation_policy()
        for key in ("worker_sha256", "ffmpeg_sha256", "ffprobe_sha256"):
            if selected["budget"][key] != request["budget"][key]:
                raise ValueError("质检算法或工具已变化，不能继承旧阶段结论")
        attempt = {"version": 1, "previous_request_id": failed_request_id,
            "previous_generation": failed["last_grant_generation"], "starting_phase": request["phase"],
            "checkpoint_digest": request["checkpoint_digest"], "budget": selected["budget"],
            "policy": {"mode": selected["mode"], "profile": selected["profile"]}}
        attempt["digest"] = quality_attempt_digest(attempt)
        candidate = quality_phase_request(sealed, {**record["evidence"], "_phase_attempt": attempt})
        if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode()) > quality_request_byte_limit(candidate):
            raise ValueError("质检接续封存包超过本次有限输入容量")
        window = json.loads(failed["window_json"])
        policy = SchedulePolicy(mode=failed["policy"],
            not_before_utc=datetime.fromisoformat(failed["not_before_utc"]) if failed["not_before_utc"] else None,
            window=Window(**window) if window else None)
        intent = SubmissionIntent(workspace_id, QUALITY_KIND, job_id, job.input_digest, quality_unit_key(candidate), policy)
        source = sealed["source"]
        generation = scheduler.reconcile_released()
        def write(conn, old_request, new_request_id):
            if write_guard is not None:
                write_guard(conn)
            current_job, _, current_record, current_sealed = self.read(workspace_id, job_id, connection=conn)
            current_access = conn.execute("SELECT json_extract(payload,'$.owner_user_id'),"
                "json_extract(payload,'$.visibility') FROM creation_tasks WHERE workspace_id=? AND id=?",
                (workspace_id, job.task_id)).fetchone()
            if (current_job.updated_at != job.updated_at or current_job.status != "failed"
                    or current_record["updated_at"] != record["updated_at"] or current_sealed != sealed
                    or current_access is None or task_access is None or tuple(current_access) != tuple(task_access)
                    or self.authorization_verdict(workspace_id, job.task_id, actor, connection=conn) != "ready"
                    or self._current_preparation_policy() != selected):
                raise ValueError("质检来源、授权或策略在接续观察后发生变化")
            artifact = conn.execute("SELECT task_id,kind,logical_ref,sha256,size_bytes FROM artifact_records "
                "WHERE workspace_id=? AND id=?", (workspace_id, source["artifact_id"])).fetchone()
            if artifact is None or tuple(artifact) != (job.task_id, "rendered_video", source["source_ref"],
                    source["render_ref"], source["size_bytes"]):
                raise ValueError("质检固定产物在接续观察后发生变化")
            self.production.reopen_failed_job_run(workspace_id, job_id, expected_updated_at=job.updated_at,
                expected_attempt=job.attempt, connection=conn)
            self.checks.reopen_phase(workspace_id, job.task_id, record["check_id"],
                expected_updated_at=record["updated_at"], attempt=attempt, connection=conn)
            self.production.execution_records.append_task_event(TaskEvent(id=f"event_{job_id}_quality_attempt_{attempt['digest']}",
                workspace_id=workspace_id, task_id=job.task_id, job_id=job_id,
                event_type="quality_check_phase_attempt_created", payload={"check_id": record["check_id"],
                    "attempt": attempt, "new_request_id": new_request_id, "previous_failure": record["error"]}), connection=conn)
            return VerifiedWork(workspace_id, QUALITY_KIND, job_id, job.input_digest, intent.unit_key,
                actor.member_id, old_request.work_class, candidate["resources"],
                (SourceRef(workspace_id, "artifact", source["artifact_id"], source["render_ref"]),))
        return scheduler.continue_failed_unit(failed_request_id, intent, actor,
            expected_failed_generation=failed["last_grant_generation"], expected_free_generation=generation,
            owner_database_path=self.production.database_path, owner_write=write).id

    def authorization_verdict(self, workspace_id, task_id, actor, *, connection=None):
        if actor.workspace_id != workspace_id:
            return "unauthorized"
        if self.task_authorizations is not None:
            return self.task_authorizations.verdict(actor, task_id, QUALITY_KIND, connection=connection, check_scope=False)
        return self.verify_identity(actor)

    def _connect(self):
        if Path(self.production.database_path).resolve() != Path(self.checks.database_path).resolve():
            raise ValueError("质检、作业和设备许可必须使用同一数据库")
        connection = sqlite3.connect(self.production.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def require_actor(self, workspace_id, task_id, actor):
        if self.authorization_verdict(workspace_id, task_id, actor) != "ready":
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
        from app.creation.duration_requirement import DurationRequirement
        source['duration_requirement'] = DurationRequirement.from_plan(
            self.production.get_creation_plan(workspace_id, task.plan_id)).payload()
        return task, plan, source

    def resources(self, source, budget):
        return quality_stream_resources({"source": source, "budget": asdict(budget)}, "metadata", {})

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

    def continuation_projection(self, workspace_id, task_id, actor, *, runtime_available=False):
        """Declared eligibility only; never reconcile, admit, or hash media here.

        The HTTP caller must first enforce task visibility. It sets runtime_available
        only when using the actual runtime's registered quality service. Submission
        revalidates authority/source/policy and proves process/permit release.
        """
        record = self.checks.get(workspace_id, task_id)
        if record is None or record["check_state"] != "failed" or "_request" not in record["evidence"]:
            return None
        job_id = f"job_quality_{record['check_id']}"
        try:
            job, original, current, sealed = self.read(workspace_id, job_id)
            request = quality_phase_request(sealed, current["evidence"])
        except (KeyError, TypeError, ValueError):
            return None  # No trustworthy executable identity to offer.
        if job.task_id != task_id or "phase" not in request:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT id,state,last_grant_generation,resources_json FROM device_work_requests "
                "WHERE workspace_id=? AND owner_type=? AND owner_id=? AND input_digest=? AND unit_key=?",
                (workspace_id, QUALITY_KIND, job_id, job.input_digest, quality_unit_key(request))).fetchone()
            active = conn.execute("SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_type=? "
                "AND owner_id=? AND state IN ('waiting','blocked','granted','yield_requested') LIMIT 1",
                (workspace_id, QUALITY_KIND, job_id)).fetchone()
        result = {"job_id": job_id, "failed_request_id": row["id"] if row is not None else None,
            "phase": request["phase"], "completed_phases": list(QUALITY_PHASES[:len(request["checkpoints"])]),
            "can_request": False, "reason_code": None, "release_check": "on_submit"}
        reason = None
        if actor is None:
            reason = "actor_unavailable"
        elif not self.same_actor(workspace_id, original, actor):
            reason = "original_actor_required"
        elif not runtime_available:
            reason = "runtime_unavailable"
        elif active is not None:
            reason = "request_in_flight"
        elif (job.status != "failed" or row is None or row["state"] != "failed"
                or row["last_grant_generation"] is None
                or sealed.get("protocol") != QUALITY_STREAM_PROTOCOL
                or json.loads(row["resources_json"]) != request["resources"]):
            reason = "failed_request_unavailable"
        else:
            try:
                # Existing verification reads registered facts and file identity
                # (stat); full SHA belongs to actual metadata/finalize workers.
                self.verify_source(request, actor)
            except PermissionError:
                reason = "original_actor_required"
            except (KeyError, OSError, ValueError):
                reason = "source_changed"
            if reason is None:
                try:
                    selected = self._current_preparation_policy()
                    if any(selected["budget"][key] != request["budget"][key]
                           for key in ("worker_sha256", "ffmpeg_sha256", "ffprobe_sha256")):
                        reason = "algorithm_changed"
                except (KeyError, OSError, TypeError, ValueError, RuntimeError):
                    reason = "policy_unavailable"
        result.update(can_request=reason is None, reason_code=reason)
        return result

    def read(self, workspace_id, job_id, *, connection=None):
        job = self.production.get_job_run(workspace_id, job_id)
        event = self.production.execution_records.get_task_event(workspace_id, f"event_{job_id}_start").payload
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
            event = self.production.execution_records.get_task_event(workspace_id, f"event_{job.id}_start")
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

    def schedule_rendered_tasks(self, runtime):
        """Recover the render-commit/schedule gap from existing durable outcomes."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT j.workspace_id,j.task_id,j.id FROM job_runs j "
                "JOIN creation_tasks t ON t.workspace_id=j.workspace_id AND t.id=j.task_id "
                "JOIN creation_task_montage_plans m ON m.workspace_id=t.workspace_id AND m.task_id=t.id "
                "WHERE j.kind='creation_task_execution' AND j.status='succeeded' "
                "AND json_extract(t.payload,'$.output_ready')=1 "
                "AND NOT EXISTS(SELECT 1 FROM creation_task_quality_checks q WHERE q.workspace_id=t.workspace_id "
                "AND q.task_id=t.id AND q.timeline_version_id=json_extract(t.payload,'$.current_timeline_version_id') "
                "AND q.source_ref=json_extract(m.payload,'$.output_path')) "
                "AND NOT EXISTS(SELECT 1 FROM job_runs newer WHERE newer.workspace_id=j.workspace_id "
                "AND newer.task_id=j.task_id AND newer.kind=j.kind AND newer.created_at>j.created_at) "
                "ORDER BY j.updated_at LIMIT 8").fetchall()
        for row in rows:
            try:
                self.start_for_render(runtime,row["workspace_id"],row["task_id"],row["id"])
            except Exception:
                logger.exception("Rendered output remains saved; independent quality scheduling is pending")

    @staticmethod
    def same_actor(workspace_id, event, actor):
        return CreationExecutionActor.from_event(workspace_id, event) == actor

    def verify_source(self, request, actor):
        source = request["source"]
        self.require_actor(source["workspace_id"], source["task_id"], actor)
        record = self.production.execution_records.get_artifact_record(source["workspace_id"], source["artifact_id"])
        if (record.task_id, record.kind, record.logical_ref, record.sha256, record.size_bytes) != (
                source["task_id"], "rendered_video", source["source_ref"], source["render_ref"], source["size_bytes"]):
            raise ValueError("质检固定来源产物已变化")
        if source["timeline_version_id"] is not None:
            version = self.production.get_timeline_version(source["workspace_id"], source["timeline_version_id"])
            if version.task_id != source["task_id"] or source["source_ref"] not in version.snapshot.get("output_refs", []):
                raise ValueError("质检固定版本与登记产物不一致")
        from app.creation.duration_requirement import DurationRequirement
        task = self.production.get_creation_task(source['workspace_id'], source['task_id'])
        current_requirement = DurationRequirement.from_plan(
            self.production.get_creation_plan(task.workspace_id, task.plan_id))
        if ('duration_requirement' not in source
                or DurationRequirement(**source['duration_requirement']) != current_requirement):
            raise ValueError("质检固定的交付时长约定已变化，请重新检查当前成片")
        path = self.outputs.artifact_store.resolve_file(source["source_ref"])
        if str(path) != source["path"] or file_identity(path) != source["file_identity"]:
            raise ValueError("检查期间成片内容已变化，请重新检查当前成片。")

    def start(self, runtime, actor, workspace_id, task_id, *, force=False):
        if self.task_authorizations is not None:
            self.task_authorizations.ensure(self.production.get_creation_task(workspace_id, task_id), actor)
        check_id, job = self._reserve(actor, workspace_id, task_id, force=force)
        if job is None:
            return check_id
        try:
            if runtime is None:
                raise ValueError("整机质检许可运行时尚未就绪，请恢复后重新检查。")
            _, _, record, sealed = self.read(workspace_id, job.id)
            runtime.scheduler.submit(SubmissionIntent(workspace_id, QUALITY_KIND, job.id, job.input_digest,
                quality_unit_key(quality_phase_request(sealed, record["evidence"])), SchedulePolicy()), actor)
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
            if self.budget is None:
                error = self.budget_error or "质检独立资源预算尚未标定，请配置后重新检查。"
                self.checks.finish(workspace_id, task_id, check_id,
                    evidence={"_source_input": source_receipt(source, check_id)}, error=error, connection=conn)
                return check_id, None
            request = {"source": source, "check_id": check_id, "actor": actor.identity_payload(),
                "budget": asdict(self.budget), "resources": self.resources(source, self.budget), "thresholds": {},
                "protocol": QUALITY_STREAM_PROTOCOL}
            digest = quality_input_digest(request)
            job = self.production.create_job_run(JobRun(id=f"job_quality_{check_id}", workspace_id=workspace_id,
                task_id=task_id, kind=QUALITY_KIND, idempotency_key=f"quality:{check_id}", input_digest=digest), connection=conn)
            self.checks.seal_request(workspace_id, task_id, check_id, request, connection=conn)
            self.production.execution_records.append_task_event(TaskEvent(id=f"event_{job.id}_start", workspace_id=workspace_id,
                task_id=task_id, job_id=job.id, event_type="quality_check_submitted",
                payload={"check_id": check_id, "input_digest": digest, **actor.identity_payload()}), connection=conn)
        return check_id, job

    def validate_result(self, request, result):
        budget = QualityBudget(**request["budget"])
        if (not isinstance(result, dict) or result.get("worker_sha256") != budget.worker_sha256
                or result.get("receipt") != source_receipt(request["source"], request["check_id"])
                or len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > budget.max_report_bytes):
            raise ValueError("质检 worker 回执未绑定原请求或超过有限大小")
        if request.get("protocol") in QUALITY_PHASE_PROTOCOLS:
            if (result.get("phase"), result.get("checkpoint_digest")) != (request.get("phase"), request.get("checkpoint_digest")):
                raise ValueError("质检 worker 回执未绑定当前阶段和前序检查点")
            if request["phase"] in {"metadata", "finalize"} and result.get("file_sha256") != request["source"]["render_ref"]:
                raise ValueError("质检阶段缺少完整文件 SHA 回执")
            validate_phase_measurement(request, result)
            if request["phase"] != "finalize":
                if not isinstance(result.get("data"), dict):
                    raise ValueError("质检阶段缺少有限检查结果")
                # A fixed finite set of phase summaries fits the sealed protocol
                # envelope. No PCM, frame list, or exported hash state is stored.
                candidate = {**request["checkpoints"], request["phase"]: result["data"]}
                prefix_limit = quality_request_byte_limit(request)
                if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode()) > prefix_limit:
                    raise ValueError("质检阶段累计摘要超过有限协议包容量")
                return
        if (not isinstance(result.get("evidence"), dict)
                or result["evidence"].get("status") not in {"clean", "issues_found", "unavailable"}):
            raise ValueError("质检 worker 缺少完整报告")

        from app.creation.duration_requirement import DurationRequirement
        evidence = result['evidence']
        requirement = DurationRequirement(**request['source']['duration_requirement'])
        assessment = requirement.assess(evidence.get('video_duration_seconds'), evidence.get('video_frame_rate'))
        evidence['duration_assessment'] = {**requirement.payload(), 'status': assessment}
        new_check = 'delivery_duration' not in evidence.get('kinds_checked', [])
        if new_check:
            evidence.setdefault('kinds_checked', []).append('delivery_duration')
        if assessment != 'met' and new_check:
            evidence['is_clean'] = False
            if assessment == 'mismatch':
                evidence['status'] = 'issues_found'
                evidence.setdefault('findings', []).append({'kind': 'delivery_duration', 'start_seconds': 0,
                    'end_seconds': evidence.get('video_duration_seconds') or 0,
                    'duration_seconds': evidence.get('video_duration_seconds'),
                    'detail': f'成片技术有效不代表时长达标：目标 {requirement.target_seconds:g} 秒，'
                              f'实际视频 {evidence.get("video_duration_seconds")} 秒。'})
            else:
                if evidence['status'] == 'clean': evidence['status'] = 'unavailable'
                evidence.setdefault('unavailable', []).append({'kind': 'delivery_duration',
                    'reason': '缺少实际视频时基或帧率，不能证明交付时长', 'status': 'unavailable'})

        if request.get("protocol") == QUALITY_STREAM_PROTOCOL:
            bounded = fit_quality_result(result, budget.max_report_bytes)
            if bounded is not result:
                result.clear()
                result.update(bounded)
        elif len(json.dumps(result, ensure_ascii=False).encode()) > budget.max_report_bytes:
            raise ValueError("包含交付时长判断的报告超过本次许可上限")

    def complete_result(self, connection, job, check_id, result):
        record = self.checks.get_check(job.workspace_id, job.task_id, check_id, connection=connection)
        request = quality_phase_request(record["evidence"]["_request"], record["evidence"])
        self.validate_result(request, result)
        measurement = result.get("measurement")
        if measurement is not None:
            execution = measurement.get("execution")
            if execution is None:
                raise ValueError("质检测量缺少真实许可执行绑定")
            row = connection.execute("SELECT workspace_id,owner_type,owner_id,input_digest,unit_key,"
                "attempt_number,last_grant_generation FROM device_work_requests WHERE id=?",
                (execution["request_id"],)).fetchone()
            if row is None or tuple(row) != (job.workspace_id, QUALITY_KIND, job.id, job.input_digest,
                    quality_unit_key(request), execution["attempt_number"], execution["generation"]):
                raise ValueError("质检测量未绑定原许可尝试")
            # This write shares the phase/checkpoint and released-permit commit.
            # A staged worker response alone is never exposed as usable evidence.
        if measurement is not None or "_phase_attempt" in request:
            result = {**result}
            result.pop("measurement", None)
            field = "evidence" if request["phase"] == "finalize" else "data"
            result[field] = {**result[field],
                **({"_measurement": measurement} if measurement is not None else {}),
                **({"_phase_attempt": request["_phase_attempt"]} if "_phase_attempt" in request else {})}
            result = fit_quality_result(result, request["budget"]["max_report_bytes"])
        if "phase" not in request or request["phase"] == "finalize":
            return self.settle(connection, job, check_id, status="succeeded", result=result)
        self.checks.save_phase(job.workspace_id, job.task_id, check_id, request["phase"], result["data"], connection=connection)
        event = TaskEvent(id=f"event_{job.id}_quality_{request['phase']}", workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type="quality_check_phase_completed",
            payload={"check_id": check_id, "phase": request["phase"], "checkpoint_digest": request["checkpoint_digest"],
                "worker_sha256": result["worker_sha256"],
                **({"file_sha256": result["file_sha256"]} if "file_sha256" in result else {})})
        self.production.execution_records.append_task_event(event, connection=connection)
        return event.id

    def read_measurements(self, workspace_id, job_id, actor):
        """At most the protocol's committed phases, never staged/failed work.

        These are eligible learning inputs, not a calibrated capacity claim.
        Device/shape applicability and policy remain the device profile owner's
        responsibility; no deadline is inferred by this read port.
        """
        job, original, record, sealed = self.read(workspace_id, job_id)
        if not self.same_actor(workspace_id, original, actor):
            raise PermissionError("质检测量不属于当前执行身份")
        self.require_actor(workspace_id, job.task_id, actor)
        return self.checks.read_measurements(workspace_id, job.task_id, record["check_id"],
            job_id=job_id, input_digest=job.input_digest)

    def next_unit(self, connection, job, check_id, actor):
        """Trusted continuation from the prefix just saved by complete_result.

        complete_unit fences the original owner/source/authority observation and
        enforces the same job, actor and source references on this continuation.
        """
        record = self.checks.get_check(job.workspace_id, job.task_id,
            check_id, connection=connection)
        if record["check_state"] != "checking":
            return None
        request = quality_phase_request(record["evidence"]["_request"], record["evidence"])
        source = request["source"]
        unit = quality_unit_key(request)
        intent = SubmissionIntent(job.workspace_id, QUALITY_KIND, job.id, job.input_digest, unit, SchedulePolicy())
        verified = VerifiedWork(job.workspace_id, QUALITY_KIND, job.id, job.input_digest, unit, actor.member_id,
            "normal_make", request["resources"],
            (SourceRef(job.workspace_id, "artifact", source["artifact_id"], source["render_ref"]),))
        return intent, verified

    def submit_next(self, scheduler, workspace_id, job_id, actor):
        job, _, record, sealed = self.read(workspace_id, job_id)
        if record["check_state"] != "checking":
            return
        request = quality_phase_request(sealed, record["evidence"])
        scheduler.submit(SubmissionIntent(workspace_id, QUALITY_KIND, job.id, job.input_digest,
            quality_unit_key(request), SchedulePolicy()), actor)

    def settle(self, connection, job, check_id, *, status, result=None, error=None):
        record = self.checks.get_check(job.workspace_id, job.task_id, check_id, connection=connection)
        private = {key: value for key, value in record["evidence"].items() if key in {"_request", "_source_input", "_worker_result", "_phases", "_phase_attempt"}}
        evidence = ({**private, **result["evidence"], "_source_receipt": result["receipt"]} if result is not None else private)
        self.checks.finish(job.workspace_id, job.task_id, check_id, evidence=evidence, error=error, connection=connection)
        self.production.finish_job_run(job.workspace_id, job.id, status=status, output_refs=[],
            failure_reason=error, impact="检查未完成，原成片保持不变。" if error is not None else None,
            repair_action="恢复质检执行条件后重新检查当前成片。" if error is not None else None, connection=connection)
        suffix = "_" + private["_phase_attempt"]["digest"] if "_phase_attempt" in private else ""
        event = TaskEvent(id=f"event_{job.id}_quality_terminal{suffix}", workspace_id=job.workspace_id, task_id=job.task_id,
            job_id=job.id, event_type="quality_check_terminal", payload={"check_id": check_id, "status": status})
        self.production.execution_records.append_task_event(event, connection=connection)
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

    def pause(self, connection, job, check_id, *, reason):
        # The sealed request and staged worker result stay in their original row.
        if job.status == "interrupted" and job.failure_reason == reason:
            return
        if job.status in {"queued", "interrupted"}:
            job = self.production.claim_job_run(job.workspace_id, job.id, connection=connection)
        self.production.finish_job_run(job.workspace_id, job.id, status="interrupted",
            failure_reason=reason, impact="检查暂停，原成片和已有检查回执保留。",
            repair_action="原任务授权及执行条件恢复后继续同一次检查。", connection=connection)
        event = TaskEvent(id=f"event_{job.id}_quality_pause_{uuid4().hex}",
            workspace_id=job.workspace_id, task_id=job.task_id, job_id=job.id,
            event_type="quality_check_paused", payload={"check_id": check_id, "reason": reason})
        self.production.execution_records.append_task_event(event, connection=connection)

    def recover(self, scheduler):
        if scheduler is None or Path(scheduler.database_path).resolve() != Path(self.production.database_path).resolve():
            raise RecoveryBlocked("质检缺少同一数据库的设备释放核验，原检查保持未完成。")
        generation = scheduler.reconcile_released()
        with closing(self._connect()) as reader:
            rows = reader.execute("SELECT workspace_id,id FROM job_runs WHERE kind=? "
                "AND status IN ('queued','interrupted','running') ORDER BY updated_at LIMIT 16", (QUALITY_KIND,)).fetchall()
        for row in rows:
            job, original, record, request = self.read(row["workspace_id"], row["id"])
            request = quality_phase_request(request, record["evidence"])
            actor = CreationExecutionActor.from_event(job.workspace_id, original)
            authorization_denied = False
            if self.task_authorizations is not None:
                try:
                    self.task_authorizations.ensure(self.production.get_creation_task(job.workspace_id, job.task_id), actor)
                except TaskAuthorizationDenied:
                    authorization_denied = True
            result = record["evidence"].get("_worker_result")
            # Cloud/identity and source/file/result observations precede the writer.
            ready = not authorization_denied and self.authorization_verdict(job.workspace_id, job.task_id, actor) == "ready"
            error = None
            if ready:
                try:
                    if request["actor"] != actor.identity_payload():
                        raise PermissionError("质检原执行身份不匹配")
                    self.verify_source(request, actor)
                    if result is not None:
                        self.validate_result(request, result)
                except (KeyError, PermissionError, OSError, ValueError, TypeError) as exc:
                    error = str(exc)
            with closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                permit = conn.execute("SELECT state,generation FROM device_permit WHERE id=1").fetchone()
                if permit is None or (permit["state"], permit["generation"]) != ("free", generation):
                    raise RecoveryBlocked("设备许可在恢复前已变化，原检查保持未完成。")
                current = conn.execute("SELECT status,updated_at FROM job_runs WHERE workspace_id=? AND id=?",
                                       (job.workspace_id,job.id)).fetchone()
                current_record = conn.execute("SELECT updated_at,check_state FROM creation_task_quality_history "
                    "WHERE workspace_id=? AND task_id=? AND check_id=?", (job.workspace_id,job.task_id,record["check_id"])).fetchone()
                if (current is None or current["updated_at"] != job.updated_at.isoformat() or current_record is None
                        or tuple(current_record) != (record["updated_at"], record["check_state"])):
                    continue  # A newer executor/check owns its own receipt.
                if self.task_authorizations is not None:
                    ready = ready and self.authorization_verdict(job.workspace_id,job.task_id,actor,connection=conn) == "ready"
                source = request["source"]
                current_source = conn.execute("SELECT task_id,kind,logical_ref,sha256,size_bytes FROM artifact_records "
                    "WHERE workspace_id=? AND id=?", (job.workspace_id,source["artifact_id"])).fetchone()
                if ready and (current_source is None or tuple(current_source) != (job.task_id,"rendered_video",
                        source["source_ref"],source["render_ref"],source["size_bytes"])):
                    error = "质检固定来源在恢复观察后已变化"
                if not ready or result is None and error is None:
                    self.pause(conn, job, record["check_id"], reason="task_authorization_unavailable" if not ready else "process_interrupted")
                    terminal = False
                else:
                    status = "failed" if error is not None else "succeeded"
                    if job.status in {"queued", "interrupted"}:
                        job = self.production.claim_job_run(job.workspace_id, job.id, connection=conn)
                    checkpoint = (self.complete_result(conn, job, record["check_id"], result) if status == "succeeded"
                        else self.settle(conn, job, record["check_id"], status=status, error=error))
                    source = request["source"]
                    expected = VerifiedWork(job.workspace_id, QUALITY_KIND, job.id, job.input_digest,
                        quality_unit_key(request), actor.member_id, "normal_make", request["resources"],
                        (SourceRef(job.workspace_id, "artifact", source["artifact_id"], source["render_ref"]),))
                    scheduler.complete_recovered_quality(conn, expected, generation=generation,
                        checkpoint_ref=checkpoint, status=status)
                    terminal = status != "succeeded" or "phase" not in request or request["phase"] == "finalize"
            if not terminal and ready:
                try:
                    self.submit_next(scheduler, job.workspace_id, job.id, actor)
                except (PermissionError, ValueError):
                    logger.info("Original quality request remains paused; its current authority/input is not ready")
