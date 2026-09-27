"""Workspace-bound continuation of an explicitly started CreationTask."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.device_scheduler import DeviceScheduler, SchedulePolicy, SubmissionIntent, VerifiedWork, WorkRequest
from app.production import CreationPlan, CreationTask, CreationTaskScript, JobRun, TaskEvent
from app.repository import reset_workspace_context, set_workspace_context
from app.task_runtime import JobContext, JobFailure, JobResult, TaskRuntime


KIND = "creation_task_execution"
STAGES = ("narration", "montage_plan", "render")
DEVICE_OWNER_TYPE = "creation_job"


@dataclass(frozen=True)
class CreationExecutionActor:
    workspace_id: str
    member_id: str


class CreationJobOwnerVerifier:
    """Validate a persisted JobRun before it can become a device work unit."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def _facts(self, conn: sqlite3.Connection, workspace_id: str, job_id: str):
        row = conn.execute(
            "SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = self.repository._job_run_from_row(row)
        task_row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
            (workspace_id, job.task_id),
        ).fetchone()
        event_row = conn.execute(
            "SELECT payload_json FROM task_events WHERE workspace_id=? AND id=? AND task_id=? AND job_id=?",
            (workspace_id, f"event_{job.id}_start", job.task_id, job.id),
        ).fetchone()
        if task_row is None or event_row is None:
            raise KeyError("创作任务或开始记录不存在")
        task = self.repository._creation_task_from_payload(json.loads(task_row["payload"]))
        event = json.loads(event_row["payload_json"])
        script_row = conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=? AND task_id=?",
            (workspace_id, event.get("script_id"), job.task_id),
        ).fetchone()
        plan_row = conn.execute(
            "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, task.plan_id),
        ).fetchone()
        if script_row is None or plan_row is None:
            raise KeyError("已确认文案或方案不存在")
        script = self.repository._creation_task_script_from_payload(json.loads(script_row["payload"]))
        plan = self.repository._creation_plan_from_payload(json.loads(plan_row["payload"]))
        return job, task, event, script, plan

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork:
        if (not isinstance(actor, CreationExecutionActor) or not actor.member_id
                or actor.workspace_id != intent.workspace_id or intent.owner_type != DEVICE_OWNER_TYPE):
            raise PermissionError("制作执行身份未知或工作区不匹配")
        with self.repository._connect() as conn:
            job, task, event, script, plan = self._facts(conn, intent.workspace_id, intent.owner_id)
            verdict = self._check_facts(job, task, event, script, plan, intent.unit_key, actor.member_id)
        if verdict != "ready":
            raise PermissionError(f"制作单元当前不可提交：{verdict}")
        if intent.input_digest != job.input_digest:
            raise PermissionError("制作输入摘要不匹配")
        return VerifiedWork(
            workspace_id=job.workspace_id, owner_type=DEVICE_OWNER_TYPE,
            owner_id=job.id, input_digest=job.input_digest, unit_key=intent.unit_key,
            member_id=actor.member_id, work_class="normal_make",
            resources={"kind": f"creation_{intent.unit_key}"},
        )

    @staticmethod
    def _check_facts(job, task, event, script, plan, stage: str, member_id: str | None) -> str:
        if (not member_id or event.get("member_id") != member_id
                or job.workspace_id != task.workspace_id or script.workspace_id != job.workspace_id
                or plan.workspace_id != job.workspace_id or script.task_id != job.task_id):
            return "unauthorized"
        if (job.kind != KIND or stage not in STAGES or job.status not in {"queued", "interrupted", "running"}
                or event.get("input_digest") != job.input_digest
                or event.get("script_id") != script.id or event.get("plan_id") != task.plan_id
                or task.confirmed_script_id != script.id or script.selection_status != "confirmed"
                or plan.creation_task_id != task.id or plan.status != "confirmed"
                or plan.selected_voice is None or revision_digest(script, plan) != job.input_digest):
            return "stale"
        return "ready"

    def check_current(self, request: WorkRequest, conn: sqlite3.Connection):
        if (request.owner_type != DEVICE_OWNER_TYPE or request.work_class != "normal_make"
                or request.source_refs or not request.member_id):
            return "unauthorized"
        try:
            job, task, event, script, plan = self._facts(conn, request.workspace_id, request.owner_id)
        except KeyError:
            return "stale"
        if request.input_digest != job.input_digest:
            return "stale"
        verdict = self._check_facts(job, task, event, script, plan, request.unit_key, request.member_id)
        # No creation stage has a grant-bound model/process executor yet.
        # Keep the request visible and blocked until every nested heavy call
        # has a checked execution path; a caller cannot opt into unsafe grants.
        return "dependency" if verdict == "ready" else verdict

    def can_manage(self, request: WorkRequest, actor: object) -> bool:
        return isinstance(actor, CreationExecutionActor) and (actor.workspace_id, actor.member_id) == (request.workspace_id, request.member_id)

    def can_wake(self, request: WorkRequest, actor: object) -> bool:
        return False

    def can_view_detail(self, request: WorkRequest, actor: object) -> bool:
        return self.can_manage(request, actor)

    def verify_retry(self, request: WorkRequest, actor: object, reason: str, conn: sqlite3.Connection) -> str:
        raise PermissionError("请通过已确认任务的继续制作入口重试")


def submit_creation_stage(execution: "CreationExecution", scheduler: DeviceScheduler,
                          job: JobRun, actor: CreationExecutionActor) -> str | None:
    """Queue exactly the next persisted stage; repeated submissions reuse one request."""
    if job.status not in {"queued", "interrupted"}:
        return None
    task = execution.repository.get_creation_task(job.workspace_id, job.task_id)
    stage = next((item for item in STAGES if not execution._stage_has_matching_output(job, task, item)), None)
    if stage is None:
        return None
    intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id,
                              job.input_digest, stage, SchedulePolicy())
    return scheduler.submit(intent, actor).id


class ExecutionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements_confirmed: bool


def revision_digest(script: CreationTaskScript, plan: CreationPlan) -> str:
    """Bind a start intent to the text and making requirements it approved."""
    value = {
        "script": asdict(script),
        "plan": asdict(plan),
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class CreationExecution:
    def __init__(
        self,
        repository,
        *,
        run_narration: Callable[[str, str], object],
        run_montage_plan: Callable[[str, str], object],
        run_render: Callable[[str, str], object],
        render_is_valid: Callable[[str, str], bool],
    ) -> None:
        self.repository = repository
        self.runtime = TaskRuntime(repository)
        self.actions = {
            "narration": run_narration,
            "montage_plan": run_montage_plan,
            "render": run_render,
        }
        self.render_is_valid = render_is_valid

    def validate_input(
        self, workspace_id: str, task: CreationTask, script_id: str, digest: str
    ) -> tuple[CreationTaskScript, CreationPlan]:
        if task.confirmed_script_id != script_id:
            raise ValueError("请先确认当前文案；方案确认不能代替文案确认")
        script = self.repository.get_creation_task_script(workspace_id, script_id)
        if script.task_id != task.id or script.selection_status != "confirmed":
            raise ValueError("当前文案不是已确认的任务文案")
        plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        if plan.creation_task_id != task.id or plan.status != "confirmed":
            raise ValueError("当前制作要求尚未确认")
        if revision_digest(script, plan) != digest:
            raise ValueError("文案或制作要求已变化，请重新检查并确认")
        return script, plan

    def start(self, workspace_id: str, task_id: str, script_id: str, digest: str,
              *, member_id: str | None = None) -> JobRun:
        task = self.repository.get_creation_task(workspace_id, task_id)
        _, plan = self.validate_input(workspace_id, task, script_id, digest)
        if plan.selected_voice is None:
            raise ValueError("请先为本次制作选择已授权声音")
        key = f"creation-execution:{task_id}:{digest}"
        run = self.repository.create_job_run(
            JobRun(
                id=f"job_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                kind=KIND,
                idempotency_key=key,
                input_digest=digest,
            )
        )
        if not self._has_start_event(workspace_id, run.id):
            self.repository.append_task_event(
                TaskEvent(
                    id=f"event_{run.id}_start",
                    workspace_id=workspace_id,
                    task_id=task_id,
                    job_id=run.id,
                    event_type="creation_execution_started",
                    payload={"script_id": script_id, "plan_id": task.plan_id, "input_digest": digest,
                             **({"member_id": member_id} if member_id is not None else {})},
                    created_at=run.created_at,
                )
            )
        if member_id is not None:
            start_event = self.repository.get_task_event(workspace_id, f"event_{run.id}_start")
            if start_event.payload.get("member_id") != member_id:
                raise PermissionError("已有制作任务的创建者身份与当前成员不同")
        return run

    def _has_start_event(self, workspace_id: str, job_id: str) -> bool:
        try:
            self.repository.get_task_event(workspace_id, f"event_{job_id}_start")
        except KeyError:
            return False
        return True

    def retry(self, workspace_id: str, task_id: str, *, member_id: str | None = None) -> JobRun:
        latest = self.latest(workspace_id, task_id)
        if latest is None:
            raise ValueError("请先明确开始制作")
        event = self.repository.get_task_event(workspace_id, f"event_{latest.id}_start")
        if member_id is not None and event.payload.get("member_id") != member_id:
            raise PermissionError("当前成员不能继续其他成员创建的制作任务")
        if latest.status in {"queued", "running", "succeeded"}:
            return latest
        if latest.status == "interrupted":
            return latest
        script_id = str(event.payload["script_id"])
        task = self.repository.get_creation_task(workspace_id, task_id)
        self.validate_input(workspace_id, task, script_id, latest.input_digest)
        same_input = [
            job for job in self.jobs(workspace_id, task_id)
            if job.input_digest == latest.input_digest
        ]
        retry_number = len(same_input)
        run = self.repository.create_job_run(
            JobRun(
                id=f"job_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                kind=KIND,
                idempotency_key=f"creation-execution:{task_id}:{latest.input_digest}:retry:{retry_number}",
                input_digest=latest.input_digest,
            )
        )
        if not self._has_start_event(workspace_id, run.id):
            self.repository.append_task_event(
                TaskEvent(
                    id=f"event_{run.id}_start", workspace_id=workspace_id,
                    task_id=task_id, job_id=run.id,
                    event_type="creation_execution_resumed",
                    payload=dict(event.payload),
                    created_at=run.created_at,
                )
            )
        if member_id is not None:
            resume_event = self.repository.get_task_event(workspace_id, f"event_{run.id}_start")
            if resume_event.payload.get("member_id") != member_id:
                raise PermissionError("已有继续任务的创建者身份与当前成员不同")
        return run

    def jobs(self, workspace_id: str, task_id: str) -> list[JobRun]:
        return [job for job in self.repository.list_job_runs(workspace_id, task_id) if job.kind == KIND]

    def latest(self, workspace_id: str, task_id: str) -> JobRun | None:
        jobs = self.jobs(workspace_id, task_id)
        return jobs[-1] if jobs else None

    def execute(self, workspace_id: str, job_id: str) -> JobRun:
        job = self.repository.get_job_run(workspace_id, job_id)
        if job.kind != KIND:
            raise ValueError("不是连续制作作业")
        token = set_workspace_context(workspace_id)
        try:
            return self.runtime.resume(workspace_id, job_id, self._work)
        finally:
            reset_workspace_context(token)

    def cancel(self, workspace_id: str, task_id: str, *, member_id: str | None = None) -> JobRun:
        job = self.latest(workspace_id, task_id)
        if job is None:
            raise ValueError("尚未开始制作")
        if member_id is not None:
            event = self.repository.get_task_event(workspace_id, f"event_{job.id}_start")
            if event.payload.get("member_id") != member_id:
                raise PermissionError("当前成员不能停止其他成员创建的制作任务")
        return self.runtime.cancel(
            workspace_id, job.id,
            impact="当前阶段在下一个安全边界停止；此前有效产物保留。",
            repair_action="需要时点击继续制作。",
        )

    def _work(self, context: JobContext) -> JobResult:
        job = self.repository.get_job_run(context.workspace_id, context.job_id)
        event = self.repository.get_task_event(context.workspace_id, f"event_{job.id}_start")
        script_id = str(event.payload["script_id"])
        workspace_id, task_id = context.workspace_id, job.task_id
        for stage in STAGES:
            context.raise_if_cancelled()
            task = self.repository.get_creation_task(workspace_id, task_id)
            try:
                self.validate_input(workspace_id, task, script_id, job.input_digest)
            except (KeyError, ValueError) as exc:
                raise JobFailure(str(exc), "制作输入已变化，未继续执行下游阶段。", "重新确认文案与制作要求后开始。") from exc
            if self._stage_has_matching_output(job, task, stage):
                self._stage_event(context, stage, "reused", output=self._output_identity(workspace_id, task, stage))
            else:
                self._stage_event(context, stage, "running")
                try:
                    self.actions[stage](workspace_id, task_id)
                except Exception as exc:
                    detail = getattr(exc, "detail", None)
                    if isinstance(detail, dict):
                        reason = str(detail.get("reason") or detail.get("error") or exc)
                        impact = str(detail.get("impact") or "当前阶段未完成，上游有效产物保留。")
                        repair = str(detail.get("repair_action") or "修复当前阶段后点击继续制作。")
                    else:
                        reason = str(detail or exc) or type(exc).__name__
                        impact = "当前阶段未完成，上游有效产物保留。"
                        repair = "修复当前阶段后点击继续制作。"
                    self._stage_event(context, stage, "failed", reason=reason)
                    raise JobFailure(reason, impact, repair) from exc
                completed_task = self.repository.get_creation_task(workspace_id, task_id)
                self._stage_event(
                    context, stage, "completed",
                    output=self._output_identity(workspace_id, completed_task, stage),
                )
        self.repository.get_creation_task_montage_plan(workspace_id, task_id)
        if not self.render_is_valid(workspace_id, task_id):
            raise JobFailure("成片产物不可用", "渲染完成状态不能作为可播放成片。", "检查渲染产物后继续制作。")
        self._stage_event(context, "cover", "pending", reason="尚未生成封面，请单独制作或选择封面。")
        return JobResult()

    def _valid_stage(self, workspace_id: str, task: CreationTask, stage: str) -> bool:
        if stage == "narration":
            if not task.narration_cache_key:
                return False
            artifact = self.repository.get_narration_artifact(workspace_id, task.narration_cache_key)
            return (
                artifact is not None and artifact.task_id == task.id
                and artifact.script_id == task.confirmed_script_id
                and Path(artifact.audio_path).is_file()
            )
        if stage == "montage_plan":
            if not task.montage_plan_id:
                return False
            try:
                plan = self.repository.get_creation_task_montage_plan(workspace_id, task.id)
            except KeyError:
                return False
            return plan.get("script_id") == task.confirmed_script_id and plan.get("status") in {"ready", "rendering", "rendered"}
        if not task.montage_plan_id:
            return False
        plan = self.repository.get_creation_task_montage_plan(workspace_id, task.id)
        return plan.get("script_id") == task.confirmed_script_id and self.render_is_valid(workspace_id, task.id)

    def _stage_has_matching_output(self, job: JobRun, task: CreationTask, stage: str) -> bool:
        if not self._valid_stage(job.workspace_id, task, stage):
            return False
        current_output = self._output_identity(job.workspace_id, task, stage)
        if not current_output:
            return False
        matching_ids = {
            prior.id for prior in self.jobs(job.workspace_id, job.task_id)
            if prior.input_digest == job.input_digest
        }
        return any(
            event.job_id in matching_ids
            and event.event_type == "creation_execution_stage"
            and event.payload.get("stage") == stage
            and event.payload.get("status") in {"completed", "reused"}
            and event.payload.get("output") == current_output
            for event in self.repository.list_task_events(job.workspace_id, job.task_id)
        )

    def _output_identity(self, workspace_id: str, task: CreationTask, stage: str) -> str | None:
        if stage == "narration":
            return task.narration_cache_key
        if stage == "montage_plan":
            return task.montage_plan_id
        if not task.current_timeline_version_id:
            return None
        plan = self.repository.get_creation_task_montage_plan(workspace_id, task.id)
        output = plan.get("output_path")
        return f"{task.current_timeline_version_id}:{output}" if isinstance(output, str) else None

    def _stage_event(
        self, context: JobContext, stage: str, status: str,
        *, reason: str | None = None, output: str | None = None,
    ) -> None:
        self.repository.append_task_event(
            TaskEvent(
                id=f"event_{uuid4().hex}", workspace_id=context.workspace_id,
                task_id=self.repository.get_job_run(context.workspace_id, context.job_id).task_id,
                job_id=context.job_id, event_type="creation_execution_stage",
                payload={"stage": stage, "status": status,
                         **({"reason": reason} if reason else {}),
                         **({"output": output} if output else {})},
            )
        )
