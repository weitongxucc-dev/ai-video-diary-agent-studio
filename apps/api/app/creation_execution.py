"""Workspace-bound continuation of an explicitly started CreationTask."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.canvas import canvas_size
from app.device_scheduler import DeviceScheduler, Grant, RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork, WorkRequest
from app.montage import creation_task_montage_plan_from_payload
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


@dataclass(frozen=True)
class CpuRenderGrantContext:
    scheduler: DeviceScheduler
    grant: Grant
    deadline_monotonic: float
    max_output_bytes: int
    threads: int
    cancel_event: threading.Event | None = None


@dataclass(frozen=True)
class CpuRenderLimits:
    max_output_bytes: int
    threads: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (type(self.max_output_bytes) is not int or self.max_output_bytes <= 0
                or type(self.threads) is not int or self.threads <= 0
                or type(self.timeout_seconds) not in {int, float}
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("CPU 成片必须显式配置正数输出、线程和总时限")


def cpu_render_facts(repository, conn: sqlite3.Connection, task: CreationTask,
                     creation_plan: CreationPlan, limits: CpuRenderLimits) -> tuple[str, dict[str, object]]:
    """Derive a bounded pure-material render from current confirmed inputs."""
    if not task.montage_plan_id or not task.narration_cache_key or not task.confirmed_script_id:
        raise ValueError("当前任务缺少已确认故事板或配音")
    row = conn.execute(
        "SELECT payload FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=? AND id=?",
        (task.workspace_id, task.id, task.montage_plan_id),
    ).fetchone()
    if row is None:
        raise ValueError("当前故事板不存在")
    payload = json.loads(row["payload"])
    montage = creation_task_montage_plan_from_payload(payload)
    if (montage.workspace_id != task.workspace_id or montage.task_id != task.id
            or montage.script_id != task.confirmed_script_id
            or montage.status not in {"ready", "rendering", "rendered", "failed"}
            or not montage.clips or any(clip.needs_confirmation for clip in montage.clips)
            or not math.isfinite(montage.duration_seconds) or montage.duration_seconds <= 0):
        raise ValueError("当前故事板尚未确认或与文案不一致")
    if montage.presenter_share_target or any(clip.person_render_id for clip in montage.clips):
        raise ValueError("人物口型组合尚无 CPU 成片许可预算")
    if montage.audio_timeline is not None:
        raise ValueError("当前有效音轨组合尚未接入 CPU 成片预算")
    if montage.graphic_overlays:
        raise ValueError("图层组合尚未接入 CPU 成片预算")
    if any(clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure")
           for clip in montage.clips):
        raise ValueError("本机人像效果尚无受许可执行路径")
    narration_reference = repository.narration_artifact_reference(task.narration_cache_key)
    if montage.narration_audio_path != narration_reference:
        raise ValueError("故事板配音与当前任务产物不一致")
    narration = repository.get_narration_artifact(task.workspace_id, task.narration_cache_key)
    if (narration is None or narration.task_id != task.id or narration.script_id != task.confirmed_script_id
            or creation_plan.selected_voice is None
            or narration.voice_profile_id != creation_plan.selected_voice.profile_id):
        raise ValueError("当前配音产物不可用")
    if task.current_timeline_version_id:
        version = repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
        transform_id = version.snapshot.get("transform_run_id")
        if (version.task_id != task.id or version.created_by_operation_id is not None
                or not isinstance(transform_id, str) or not transform_id.startswith("transform_job_")):
            raise ValueError("当前时间线已有独立编辑，CPU 成片组合尚未接入该版本")
        previous_montage = version.snapshot.get("montage")
        if not isinstance(previous_montage, dict):
            raise ValueError("当前时间线缺少可信故事板快照")
        transient = {"status", "message", "output_path", "subtitle_path", "updated_at"}
        if ({key: item for key, item in previous_montage.items() if key not in transient}
                != {key: item for key, item in payload.items() if key not in transient}):
            raise ValueError("当前时间线与已确认故事板不一致")
    audio = repository.resolve_authorized_narration_audio_reference(task.workspace_id, narration_reference)
    sources = [audio]
    for clip in montage.clips:
        source = repository.resolve_clip_media_reference(task.workspace_id, clip.asset_id, clip.source_path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ValueError("当前镜头源文件不可用")
        sources.append(source)
    width, height = canvas_size(montage.aspect_ratio)
    source_facts = []
    for path in sources:
        stat = path.stat()
        if stat.st_size <= 0:
            raise ValueError("渲染源文件为空")
        source_facts.append({"path": str(path), "size": stat.st_size,
                             "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino,
                             "device": stat.st_dev})
    manifest = {
        "plan": {key: value for key, value in payload.items()
                 if key not in {"status", "message", "output_path", "subtitle_path", "updated_at"}},
        "sources": source_facts,
    }
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                       default=str).encode("utf-8")).hexdigest()
    resources = {
        "kind": "creation_render", "execution_mode": "cpu_ffmpeg",
        "duration_seconds": montage.duration_seconds, "width_px": width, "height_px": height,
        "input_bytes": sum(item["size"] for item in manifest["sources"]),
        "clip_count": len(montage.clips), "overlay_count": len(montage.cues),
        "max_output_bytes": limits.max_output_bytes, "threads": limits.threads,
        "timeout_seconds": limits.timeout_seconds,
    }
    return f"render:{digest}", resources


class CreationJobOwnerVerifier:
    """Validate a persisted JobRun before it can become a device work unit."""

    def __init__(self, repository, *, cpu_render_limits: CpuRenderLimits | None = None) -> None:
        self.repository = repository
        self.cpu_render_limits = cpu_render_limits

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
            stage = intent.unit_key.split(":", 1)[0]
            verdict = self._check_facts(job, task, event, script, plan, stage, actor.member_id)
            if verdict == "ready" and stage == "render":
                if self.cpu_render_limits is None:
                    raise PermissionError("CPU 成片资源预算尚未配置")
                key, resources = cpu_render_facts(self.repository, conn, task, plan, self.cpu_render_limits)
                if intent.unit_key != key:
                    raise PermissionError("故事板或源文件已变化，请重新安排成片")
            else:
                resources = {"kind": f"creation_{stage}"}
        if verdict != "ready":
            raise PermissionError(f"制作单元当前不可提交：{verdict}")
        if intent.input_digest != job.input_digest:
            raise PermissionError("制作输入摘要不匹配")
        return VerifiedWork(
            workspace_id=job.workspace_id, owner_type=DEVICE_OWNER_TYPE,
            owner_id=job.id, input_digest=job.input_digest, unit_key=intent.unit_key,
            member_id=actor.member_id, work_class="normal_make",
            resources=resources,
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
        stage = request.unit_key.split(":", 1)[0]
        verdict = self._check_facts(job, task, event, script, plan, stage, request.member_id)
        if verdict != "ready":
            return verdict
        if stage != "render" or self.cpu_render_limits is None:
            return "dependency"
        try:
            key, resources = cpu_render_facts(self.repository, conn, task, plan, self.cpu_render_limits)
        except (KeyError, OSError, ValueError):
            return "dependency"
        return "ready" if request.unit_key == key and request.resources == resources else "stale"

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
    if execution._valid_stage(job.workspace_id, task, "narration") and execution._valid_stage(job.workspace_id, task, "montage_plan"):
        owner = scheduler.owners
        if hasattr(owner, "registrations"):
            owner = owner.registrations[DEVICE_OWNER_TYPE].owner
        limits = getattr(owner, "cpu_render_limits", None)
        if limits is None:
            raise ValueError("CPU 成片资源预算尚未配置")
        creation_plan = execution.repository.get_creation_plan(job.workspace_id, task.plan_id)
        with execution.repository._connect() as conn:
            stage, _resources = cpu_render_facts(execution.repository, conn, task, creation_plan, limits)
    else:
        stage = next((item for item in STAGES[:2] if not execution._valid_stage(job.workspace_id, task, item)), None)
        if stage is None:
            return None
    intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id,
                              job.input_digest, stage, SchedulePolicy())
    return scheduler.submit(intent, actor).id


class CreationJobUnitExecutor:
    """Execute one already granted CPU render, then commit the outer JobRun with its permit."""

    def __init__(self, repository, *, render_action: Callable[[str, str, CpuRenderGrantContext], object],
                 render_is_valid: Callable[[str, str, CpuRenderGrantContext], bool]) -> None:
        self.repository = repository
        self.render_action = render_action
        self.render_is_valid = render_is_valid
        self._cancellations: dict[str, threading.Event] = {}
        self._cancellation_lock = threading.Lock()

    def request_cancel(self, request_id: str) -> None:
        with self._cancellation_lock:
            event = self._cancellations.get(request_id)
        if event is not None:
            event.set()

    def _checkpoint(self, conn: sqlite3.Connection, grant: Grant, *, status: str,
                    output: str | None = None, reason: str | None = None) -> str:
        row = conn.execute("SELECT task_id,status FROM job_runs WHERE workspace_id=? AND id=? AND kind=?",
                           (grant.workspace_id, grant.owner_id, KIND)).fetchone()
        if row is None or row["status"] not in {"running", "cancelled"}:
            raise ValueError("外层制作作业不在可提交状态")
        if row["status"] == "cancelled" and status != "cancelled":
            raise ValueError("已停止的制作作业不能写入成功或失败")
        if status == "succeeded":
            task_row = conn.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                    (grant.workspace_id, row["task_id"])).fetchone()
            plan_row = conn.execute("SELECT payload FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=?",
                                    (grant.workspace_id, row["task_id"])).fetchone()
            if task_row is None or plan_row is None:
                raise ValueError("成片检查点缺少当前任务或故事板")
            task_payload = json.loads(task_row["payload"])
            plan_payload = json.loads(plan_row["payload"])
            version_id = task_payload.get("current_timeline_version_id")
            ref = plan_payload.get("output_path")
            version_row = conn.execute("SELECT snapshot_json FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=?",
                                       (grant.workspace_id, row["task_id"], version_id)).fetchone()
            if (not isinstance(version_id, str) or not isinstance(ref, str)
                    or plan_payload.get("status") != "rendered" or version_row is None
                    or ref not in json.loads(version_row["snapshot_json"]).get("output_refs", [])
                    or output != f"{version_id}:{ref}"):
                raise ValueError("成片检查点与当前版本产物不一致")
        now = datetime.now(timezone.utc).isoformat()
        if row["status"] == "running":
            changed = conn.execute(
                "UPDATE job_runs SET status=?, progress=CASE WHEN ?='succeeded' THEN 100 ELSE progress END, "
                "failure_reason=?, impact=?, repair_action=?, finished_at=?, updated_at=? "
                "WHERE workspace_id=? AND id=? AND status='running'",
                (status, status, reason, None if status == "succeeded" else "当前成片未完成，上游产物保留。",
                 None if status == "succeeded" else "检查首个失败原因后继续制作。",
                 now, now, grant.workspace_id, grant.owner_id),
            ).rowcount
            if changed != 1:
                raise ValueError("外层制作作业状态已变化")
        event_id = f"event_{grant.owner_id}_render_{status}"
        conn.execute(
            "INSERT OR IGNORE INTO task_events "
            "(id,workspace_id,task_id,job_id,event_type,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, grant.workspace_id, row["task_id"], grant.owner_id,
             "creation_execution_stage",
             json.dumps({"stage": "render", "status": "completed" if status == "succeeded" else status,
                         **({"output": output} if output else {}),
                         **({"reason": reason} if reason else {})}, ensure_ascii=False), now),
        )
        return event_id

    def execute(self, grant: Grant, scheduler: DeviceScheduler) -> None:
        from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout

        if grant.owner_type != DEVICE_OWNER_TYPE or not grant.unit_key.startswith("render:"):
            raise PermissionError("制作执行器只接受已确认的 CPU 成片单元")
        try:
            scheduler.assert_grant(grant)
        except PermissionError:
            if self.repository.get_job_run(grant.workspace_id, grant.owner_id).status != "cancelled":
                raise
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                    checkpoint_write=lambda conn, current: self._checkpoint(
                                        conn, current, status="cancelled", reason="user_cancelled"),
                                    cancelled=True)
            return
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT resources_json FROM device_work_requests WHERE id=? AND workspace_id=? AND owner_id=? "
                "AND input_digest=? AND unit_key=? AND state='granted'",
                (grant.request_id, grant.workspace_id, grant.owner_id, grant.input_digest, grant.unit_key),
            ).fetchone()
            permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
        if row is None or permit is None or not permit["granted_at_utc"]:
            raise PermissionError("CPU 成片许可事实不完整")
        resources = json.loads(row["resources_json"])
        if resources.get("kind") != "creation_render" or resources.get("execution_mode") != "cpu_ffmpeg":
            raise PermissionError("CPU 成片资源事实不匹配")
        elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds())
        deadline = time.monotonic() + float(resources["timeout_seconds"]) - elapsed
        cancellation = threading.Event()
        with self._cancellation_lock:
            self._cancellations[grant.request_id] = cancellation
        try:
            job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
            if job.status == "running":
                # Host recovery has already proved the prior child tree released.
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? "
                                 "AND kind=? AND status='running'",
                                 (grant.workspace_id, grant.owner_id, KIND))
            try:
                job = self.repository.claim_job_run(grant.workspace_id, grant.owner_id)
            except ValueError:
                if self.repository.get_job_run(grant.workspace_id, grant.owner_id).status != "cancelled":
                    raise
                # No child has been launched by this executor yet. The scheduler
                # still verifies that every previously registered child exited.
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="cancelled", reason="user_cancelled"),
                                        cancelled=True)
                return
            context = CpuRenderGrantContext(
                scheduler, grant, deadline, int(resources["max_output_bytes"]),
                int(resources["threads"]), cancellation,
            )
            try:
                if deadline <= time.monotonic():
                    raise GrantedCommandTimeout("CPU 成片阶段已超出总时限")
                self.render_action(job.workspace_id, job.task_id, context)
                if not cancellation.is_set() and self.repository.get_job_run(job.workspace_id, job.id).status != "cancelled":
                    if not self.render_is_valid(job.workspace_id, job.task_id, context):
                        raise ValueError("成片产物未通过当前许可内的完整性与媒体校验")
            except GrantedCommandCancelled:
                cancellation.set()
            except RecoveryBlocked:
                raise
            except Exception as exc:
                if cancellation.is_set() or self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                    cancellation.set()
                else:
                    reason = str(exc).strip() or type(exc).__name__
                    try:
                        scheduler.fail_unit(grant, reason="creation_render_failed",
                                            failure_write=lambda conn, current: self._checkpoint(
                                                conn, current, status="failed", reason=reason))
                    except ValueError:
                        if self.repository.get_job_run(job.workspace_id, job.id).status != "cancelled":
                            raise
                        scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                                checkpoint_write=lambda conn, current: self._checkpoint(
                                                    conn, current, status="cancelled", reason="user_cancelled"),
                                                cancelled=True)
                    return
            if cancellation.is_set() or self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="cancelled", reason="user_cancelled"),
                                        cancelled=True)
                return
            task = self.repository.get_creation_task(job.workspace_id, job.task_id)
            plan = self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id)
            if not task.current_timeline_version_id or not plan.get("output_path"):
                raise ValueError("成片未建立可追溯的当前版本")
            output = f"{task.current_timeline_version_id}:{plan['output_path']}"
            try:
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="succeeded", output=output))
            except PermissionError:
                if self.repository.get_job_run(job.workspace_id, job.id).status != "cancelled":
                    raise
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="cancelled", reason="user_cancelled"),
                                        cancelled=True)
        finally:
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)


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
            return plan.get("script_id") == task.confirmed_script_id and plan.get("status") in {"ready", "rendering", "rendered", "failed"}
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
