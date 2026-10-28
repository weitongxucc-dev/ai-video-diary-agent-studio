"""Creation execution control, original queue reconciliation and status projection."""
from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import ExitStack, nullcontext
from datetime import datetime
import logging
import threading
import time
from uuid import uuid4

from app.creation_execution import (CreationExecutionActor, CreationJobUnitExecutor,
    NARRATION_EDIT_KIND, narration_edit_digest, PERSON_EDIT_KIND, person_edit_digest,
    CreationInputRejected, creation_input_rejection, revision_digest, submit_creation_stage, montage_waiting_for_confirmation,
    migrate_creation_narration_request, resume_original_narration)
from app.creation.render_stage_use_case import RenderRejected
from app.creation.contracts import TaskEvent, JobRun
from app.creation.task_authorization import TaskAuthorizationDenied
from app.device_scheduler import SubmissionIntent, SchedulePolicy, RecoveryBlocked
from app.production import CreationPlan
from app.creation.contracts import CreationTask
from app.repository import LEGACY_WORKSPACE_ID
from app.task_audio import TASK_MEDIA_KIND

logger = logging.getLogger("app.main")


class DesktopShutdownUseCase:
    """One save/pause request consumes one common desktop exit budget."""
    def __init__(self, *, runtime, automation, ingestion, assistant, stop_assistant,
                 conversations, close_clients):
        self.runtime, self.automation, self.ingestion = runtime, automation, ingestion
        self.assistant, self.stop_assistant = assistant, stop_assistant
        self.conversations, self.close_clients = conversations, close_clients
        self._lock = threading.Lock()
        self._request_id = None
        self._result = None

    def execute(self, exit_request_id, deadline_monotonic):
        with self._lock:
            if self._request_id is not None:
                if self._request_id != exit_request_id:
                    raise ValueError("本次API已在处理另一个退出请求")
                return self._result
            self._request_id = exit_request_id
            checks = dict.fromkeys(("dispatch_stopped", "executors_notified", "processes_released", "checkpoints_saved"), False)
            if self.runtime is not None:
                self.runtime.begin_shutdown()
            # Notify every driver before waiting on any one executor.
            if self.automation is not None:
                self.automation.request_stop()
            if self.ingestion is not None:
                self.ingestion.request_stop()
            if self.assistant is not None:
                self.assistant.request_stop()
            background_stopped = False
            assistant_released = False
            try:
                turns = self.stop_assistant(deadline_monotonic=deadline_monotonic)
                assistant_released = True
                for turn_id in turns:
                    self.conversations.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})
                automation_stopped = self.automation is None or self.automation.stop(timeout_seconds=max(0, deadline_monotonic-time.monotonic()))
                ingestion_stopped = self.ingestion is None or self.ingestion.stop(timeout_seconds=max(0, deadline_monotonic-time.monotonic()))
                assistant_stopped = self.assistant is None or self.assistant.stop(timeout_seconds=max(0, deadline_monotonic-time.monotonic()), notify=False)
                background_stopped = automation_stopped and ingestion_stopped and assistant_stopped
                if self.runtime is not None:
                    runtime_stopped = self.runtime.stop(timeout_seconds=max(0, deadline_monotonic-time.monotonic()))
                    checks.update(self.runtime.shutdown_checks())
                    checks["checkpoints_saved"] &= runtime_stopped
                else:
                    checks = dict.fromkeys(checks, True)
            except Exception:
                logger.exception("Desktop save/pause remains unconfirmed")
                if self.runtime is not None:
                    checks.update(self.runtime.shutdown_checks())
            checks["dispatch_stopped"] &= background_stopped
            checks["executors_notified"] &= assistant_released
            checks["processes_released"] &= assistant_released and background_stopped
            checks["checkpoints_saved"] &= assistant_released and background_stopped
            if all(checks.values()) and time.monotonic() <= deadline_monotonic:
                self.close_clients()
            elif time.monotonic() > deadline_monotonic:
                checks["checkpoints_saved"] = False
            pending = [name for name, confirmed in checks.items() if not confirmed]
            self._result = {"exit_request_id": exit_request_id,
                "status": "unconfirmed" if pending else "ready_to_exit", "checks": checks, "pending": pending}
            return self._result


class ExecutionControlRejected(Exception):
    def __init__(self, *, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(f'{status_code}: {detail}')


_EXECUTION_WAITING_TEXT = {
    "dependency": "等待前一步或受许可执行能力就绪。",
    "owner_unknown": "当前成员或任务归属暂不能核实。",
    "authorization_lost": "当前任务授权已变化，请核对权限。",
    "input_stale": "文案或制作要求已变化，请重新确认。",
    "scheduled_time": "等待指定开始时间。",
    "outside_window": "当前不在选择的执行时段内。",
    "activity_unknown": "暂不能确认设备是否空闲。",
    "device_active": "设备有人正在播放、录音或编辑。",
    "resource_unknown": "暂不能确认设备资源是否足够。",
    "resource_busy": "本机资源检查尚未通过，正在等待满足制作条件。",
    "retry_not_before": "等待下次可重试时间。",
    "recovered": "设备恢复后等待重新核验。",
}


class CreationExecutionControl:
    def __init__(self, repository, execution, render_stage, verify_identity, *, segments=None):
        self.repository = repository
        self.execution = execution
        self.render_stage = render_stage
        self.verify_identity = verify_identity
        self.segments = segments

    def preview(self, workspace_id: str, task_id: str) -> dict[str, object]:
        from app.creation.execution_input import validate_execution_relationships
        try:
            snapshot = self.repository.read_creation_execution_snapshot(workspace_id, task_id)
            task, plan, script = snapshot.task, snapshot.plan, snapshot.script_header
            validate_execution_relationships(task, script, plan)
        except KeyError as exc:
            raise ExecutionControlRejected(status_code=404, detail="创作任务或确认对象不存在") from exc
        except ValueError as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc
        narration_artifact = (
            self.repository.get_narration_artifact(workspace_id, task.narration_cache_key)
            if task.narration_cache_key
            else None
        )
        if (narration_artifact is not None
                and (narration_artifact.task_id != task.id or narration_artifact.script_id != script.id)):
            narration_artifact = None
        recorded_narration_ready = (
            narration_artifact is not None and narration_artifact.source_kind == "recorded"
        )
        can_continue = (
            plan.presenter_share_target == 0
            if recorded_narration_ready
            else bool(plan.selected_voice)
        )
        if can_continue:
            continuation_blocker = None
        elif recorded_narration_ready:
            continuation_blocker = "当前配音是本人录音，不能满足露脸目标与口型身份；请将露脸目标设为0，或改用与授权声音匹配的生成配音。"
        elif narration_artifact is not None:
            continuation_blocker = "当前已有配音产物，但制作要求未选定授权声音版本；请选择授权声音后重新确认。"
        elif task.narration_cache_key:
            continuation_blocker = "当前配音产物不可用或不再对应这版文案；请重新采用录音或选择已授权声音。"
        else:
            continuation_blocker = "请先选择已授权声音，或保存并明确采用本人录音；本人录音仅支持纯素材成片。"
        return {
            "task_id": task.id,
            "script_id": script.id,
            "script_title": script.title,
            "script_updated_at": script.updated_at.isoformat(),
            "script_sections_total_count": script.sections_total_count,
            "script_sections_loaded": False,
            "plan_id": plan.id,
            "narration_source": ({
                "source_kind": narration_artifact.source_kind,
                "source_artifact_ref": narration_artifact.source_artifact_ref,
                "duration_seconds": narration_artifact.duration_seconds,
            } if narration_artifact is not None else None),
            "requirements": {
                "aspect_ratio": plan.aspect_ratio,
                "duration_target_seconds": plan.duration_target_seconds,
                "presenter_share_target": plan.presenter_share_target,
                "lipsync_quality": plan.lipsync_quality,
                "selected_voice": plan.selected_voice.profile_id if plan.selected_voice else None,
                "can_continue": can_continue,
                "continuation_blocker": continuation_blocker,
                "template_settings": plan.template_settings,
            },
            "input_digest": snapshot.input_digest,
        }

    def status_summary(self, workspace_id: str, task_id: str, *, actor=None, job_id=None) -> dict[str, object]:
        """Read persisted execution facts without probing or assembling media."""
        task = self.task_or_missing(workspace_id, task_id)
        return self._status_summary(task, actor=actor, job_id=job_id)

    def _status_summary(self, task, *, actor, job_id):
        workspace_id, task_id = task.workspace_id, task.id
        job = self.execution.latest(workspace_id, task_id) if job_id is None else self.repository.get_job_run(workspace_id,job_id)
        if actor is not None and job_id is None and task.owner_user_id==(actor.user_id or actor.member_id):
            with self.repository._connect() as conn:
                row=conn.execute("SELECT j.* FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id "
                    "AND e.id='event_' || j.id || '_start' WHERE j.workspace_id=? AND j.task_id=? "
                    "AND j.kind='creation_task_segment_revision' AND json_extract(e.payload_json,'$.member_id')=? "
                    "ORDER BY j.created_at DESC,j.id DESC LIMIT 1",(workspace_id,task_id,actor.member_id)).fetchone()
            if row is not None:
                revision=self.repository._job_run_from_row(row)
                start=self.repository.execution_records.get_task_event(workspace_id,f'event_{revision.id}_start').payload
                if CreationExecutionActor.from_event(workspace_id,start)==actor and (job is None or revision.created_at>job.created_at):
                    job=revision

        task_events=self.repository.execution_records.list_task_events(workspace_id,task_id) if job else []
        events = (
            [
                {"stage": event.payload.get("stage"), "status": event.payload.get("status"),
                 "reason": event.payload.get("reason"), "at": event.created_at.isoformat()}
                for event in task_events
                if job is not None and event.job_id == job.id
                and event.event_type == "creation_execution_stage"
            ]
            if job else []
        )
        current_stage = None
        if job and events:
            latest_stage_event = events[-1]
            if (latest_stage_event["stage"] in {"narration", "person_render", "montage_plan", "render", "cover"}
                    and latest_stage_event["status"] in {"running", "failed", "pending"}):
                current_stage = latest_stage_event["stage"]
        waiting_reason = None
        status_updated_at = job.updated_at.isoformat() if job else None
        if job and job.status in {"queued", "interrupted"}:
            start_event = self.repository.execution_records.get_task_event(workspace_id, f"event_{job.id}_start")
            queued_member_id = start_event.payload.get("member_id")
            if queued_member_id:
                row = self.repository.creation_execution_wait_row(workspace_id, queued_member_id, job.id, job.input_digest)
                if row is not None:
                    queued_stage = row["unit_key"].split(":", 1)[0]
                    if queued_stage in {"narration", "person_render", "montage_plan", "render"}:
                        current_stage = queued_stage
                    waiting_reason = _EXECUTION_WAITING_TEXT.get(row["wait_reason"])
                    if datetime.fromisoformat(row["updated_at"]) > job.updated_at:
                        status_updated_at = row["updated_at"]
        if job and job.status == "queued" and montage_waiting_for_confirmation(self.repository, task):
            current_stage = "montage_plan"
            waiting_reason = "故事板仍有镜头等待确认；确认后点击继续制作。"
        return {
            "task_id": task_id,
            "job": asdict(job) if job else None,
            "stages": events,
            "current_stage": current_stage,
            "waiting_reason": waiting_reason,
            "status_updated_at": status_updated_at,
            "input_rejection":creation_input_rejection(task_events,job.id) if job else None,
            "narration_usage": self.repository.creation_narration_usage(workspace_id, job.id) if job else None,
        }

    def status(self, workspace_id: str, task_id: str, *, verify_media: bool, actor=None, job_id=None) -> dict[str, object]:
        task = self.task_or_missing(workspace_id, task_id)
        result = self._status_summary(task, actor=actor, job_id=job_id)
        job = result["job"]
        decisions=self.segments.visual_decision_locators(workspace_id,task_id,actor,job_id=job["id"] if job else None) if (
            self.segments is not None and actor is not None and task.owner_user_id==(actor.user_id or actor.member_id)) else {'items':[],'total_count':0,'truncated':False}
        if decisions['items']:
            result['waiting_reason']=('画面采用已保存，原作业排队状态待回查。' if all(item['adopted'] for item in decisions['items'])
                else '段落配音已保存，画面候选需要用户明确确认。')
            result['current_stage']='segment_video'
        covers = (
            self.repository.list_cover_assets(workspace_id, task_id, timeline_version_id=task.current_timeline_version_id)
            if task.current_timeline_version_id else []
        )
        result.update({
            "visual_decisions": decisions,
            "video_ready": self.render_valid(workspace_id, task_id, verify_media=verify_media),
            "cover_status": "ready" if any(cover.status == "valid" for cover in covers) else "pending",
            "cover_message": None if any(cover.status == "valid" for cover in covers) else "尚未生成封面，请单独制作或选择封面。",
        })
        return result

    def start(self, workspace_id, task_id, script_id, digest, actor, runtime, *, verify_media: bool, write_guard=None, execution_source=None):
        try:
            owner = runtime.registrations["creation_job"].owner
            if owner.task_authorizations is not None:
                task = self.execution.validate_input_snapshot(workspace_id, task_id, script_id, digest).task
                owner.task_authorizations.ensure(task, actor, interactive=write_guard is None)
            job = self.execution.start(workspace_id, task_id, script_id, digest,
                                           actor=actor,write_guard=write_guard,execution_source=execution_source)
            submit_creation_stage(self.execution, runtime.scheduler, job, actor)
        except KeyError as exc:
            raise ExecutionControlRejected(status_code=404, detail="创作任务或确认对象不存在") from exc
        except PermissionError as exc:
            raise ExecutionControlRejected(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc
        runtime.wake()
        result = self.status(workspace_id, task_id, verify_media=verify_media)
        return result

    def resume(self, workspace_id, task_id, actor, runtime, *, verify_media: bool, write_guard=None, execution_source=None):
        try:
            latest = self.execution.latest(workspace_id, task_id)
            owner = runtime.registrations['creation_job'].owner
            delivery = owner.audio_delivery
            recovered = (delivery.adoption_recovery.continue_adoption(runtime.scheduler, latest, actor,
                owner=owner, write_guard=write_guard) if latest is not None and delivery is not None else None)
            if recovered is not None:
                if execution_source is not None:
                    self.repository.bind_creation_execution_source(
                        self.repository.get_job_run(workspace_id, latest.id), execution_source, write_guard=write_guard)
                runtime.wake()
                return self.status(workspace_id, task_id, verify_media=verify_media)
            if latest is not None and self.execution.rerender_current(latest,actor,runtime.scheduler,write_guard=write_guard):
                job = self.repository.get_job_run(workspace_id,latest.id)
            else:
                migrated = (migrate_creation_narration_request(self.repository, runtime.scheduler, latest, actor, write_guard=write_guard)
                    if latest is not None else None)
                resumed = (resume_original_narration(self.repository, runtime.scheduler, latest, actor, write_guard=write_guard,
                    executor=runtime.registrations['creation_job'].executor)
                    if latest is not None and migrated is None else False)
                job = (self.repository.get_job_run(workspace_id, latest.id) if migrated is not None or resumed else
                    self.execution.retry(workspace_id, task_id, member_id=actor.member_id,write_guard=write_guard,execution_source=execution_source,
                        admission_recheck=lambda rejection:runtime.registrations['creation_job'].owner.admission_basis_changed(rejection)))
            if execution_source is not None:
                self.repository.bind_creation_execution_source(job,execution_source,write_guard=write_guard)
            submit_creation_stage(self.execution, runtime.scheduler, job, actor)
        except CreationInputRejected as exc:
            raise ExecutionControlRejected(status_code=409,detail=exc.detail) from exc
        except RecoveryBlocked as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise ExecutionControlRejected(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc
        runtime.wake()
        result = self.status(workspace_id, task_id, verify_media=verify_media)
        return result

    def cancel(self, workspace_id, task_id, actor, runtime, *, verify_media: bool, write_guard=None):
        try:
            job = self.execution.cancel(workspace_id, task_id, member_id=actor.member_id,write_guard=write_guard)
        except PermissionError as exc:
            raise ExecutionControlRejected(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc
        if runtime is not None:
            rows = self.repository.active_creation_request_ids(workspace_id, job.id)
            for row in rows:
                runtime.scheduler.cancel(row["id"], actor)
                registration = runtime.registrations.get("creation_job")
                executor = getattr(registration, "executor", None)
                if isinstance(executor, CreationJobUnitExecutor):
                    executor.request_cancel(row["id"])
        return self.status(workspace_id, task_id, verify_media=verify_media)

    def regenerate_storyboard(self, workspace_id, task_id, payload, actor, runtime):
        from app.creation.storyboard_regeneration import StoryboardRegeneration
        case = StoryboardRegeneration(self.repository)
        inputs = payload.model_dump(mode="json", exclude_unset=True)
        try:
            replay = case.replay(workspace_id, task_id, inputs, actor)
            if replay is not None:
                return case.read(workspace_id, task_id, str(payload.request_id), actor)
            owner = runtime.scheduler.owners
            if hasattr(owner, "registrations"):
                owner = owner.registrations["creation_job"].owner
            job, fresh = self.execution.regenerate_storyboard(workspace_id, task_id, inputs, actor,
                stage_facts=lambda current, script, plan, *, connection=None: owner.stage_facts(
                    current, script, plan, "montage_plan", connection=connection)[0])
            if fresh:
                submit_creation_stage(self.execution, runtime.scheduler, job, actor)
                runtime.wake()
            return case.read(workspace_id, task_id, str(payload.request_id), actor)
        except PermissionError as exc:
            raise ExecutionControlRejected(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise ExecutionControlRejected(status_code=404, detail="故事板任务或输入不存在") from exc
        except OSError as exc:
            raise ExecutionControlRejected(status_code=409, detail="当前配音或素材文件不可用，请读取当前任务后处理") from exc
        except ValueError as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc

    def read_storyboard_regeneration(self, workspace_id, task_id, request_id, actor):
        from app.creation.storyboard_regeneration import StoryboardRegeneration
        try:
            return StoryboardRegeneration(self.repository).read(workspace_id, task_id, request_id, actor)
        except PermissionError as exc:
            raise ExecutionControlRejected(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise ExecutionControlRejected(status_code=404, detail="故事板原请求不存在") from exc
        except ValueError as exc:
            raise ExecutionControlRejected(status_code=409, detail=str(exc)) from exc

    def recover(self, runtime) -> None:
        """开机恢复：身份只取持久行，并且每次都重新对本机名册核一遍。

        被移除的成员留下的作业不会被自动重新申请整机许可——它停在待处理状态等人
        明确处理，而不是靠一个旧的字符串身份继续拿许可。
        """
        # The repository owns the cross-workspace pending-job query. A workspace is
        # taken only from each persisted JobRun, never from the current login.
        if runtime is None or "creation_job" not in runtime.registrations:
            # 没有整机许可通道就不派发。退回旧的本机线程等于绕开身份和主机门禁，
            # 那正是这张许可要挡住的事；作业停在待处理状态，等许可就绪或人明确处理。
            pending = self.repository.list_pending_creation_executions()
            if pending:
                logger.warning(
                    "整机许可通道尚未就绪，%s 件制作作业留在待处理状态等待人工处理",
                    len(pending),
                )
            return
        generation = runtime.scheduler.reconcile_released()
        registration=runtime.registrations['creation_job']
        owner,executor=registration.owner,registration.executor
        for job in self.repository.list_pending_task_media_jobs(NARRATION_EDIT_KIND):
            if job.status=='cancelled':
                continue
            try:
                start=self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start').payload
                original=CreationExecutionActor.from_event(job.workspace_id,start)
                task=self.repository.get_creation_task(job.workspace_id,job.task_id)
                if owner.task_authorizations is not None:
                    owner.task_authorizations.ensure(task,original)
                elif self.verify_identity(original)!='ready':
                    continue
                executor.recover_pcm_unit(job,original,runtime.scheduler)
                job=self.repository.get_job_run(job.workspace_id,job.id)
                if job.status=='succeeded':
                    continue
                plan=self.repository.get_creation_plan(job.workspace_id,task.plan_id)
                key,_=owner.narration_edit_facts(job,task,start,plan)
                runtime.scheduler.submit(SubmissionIntent(job.workspace_id,'creation_job',job.id,
                    job.input_digest,key,SchedulePolicy()),original)
            except (PermissionError,ValueError,KeyError,RecoveryBlocked,OSError) as exc:
                logger.warning('局部配音原作业 %s 尚不能恢复，保留原请求：%s',job.id,exc)
        media = runtime.registrations["creation_job"].owner.task_media
        if media is not None:
            pending_media = self.repository.list_pending_task_media_jobs(TASK_MEDIA_KIND)
            for job in pending_media:
                request_rows = self.repository.list_creation_job_request_states(job.workspace_id, job.id)
                _, event = media.read(job.workspace_id, job.id)
                original = CreationExecutionActor.from_event(job.workspace_id, event)
                if job.status == "cancelled":
                    for saved in request_rows:
                        if saved["state"] in {"waiting", "blocked", "granted", "yield_requested"}:
                            runtime.scheduler.cancel(saved["id"], original)
                    continue
                if media.task_authorizations is not None:
                    try:
                        media.task_authorizations.ensure(self.repository.get_creation_task(job.workspace_id, job.task_id), original)
                    except TaskAuthorizationDenied as exc:
                        logger.warning("任务媒体作业 %s 的原任务授权不可用，保留待处理：%s",job.id,exc)
                        continue
                if media.check(job, event, original) != "ready":
                    continue
                if not request_rows:
                    runtime.scheduler.submit(SubmissionIntent(job.workspace_id, "creation_job", job.id,
                        job.input_digest, f"task_media:{job.input_digest}", SchedulePolicy()), original)
                # Existing grants are reconciled by DeviceScheduler before any dispatch.
        # A crash may land after the JobRun cancellation commits but before
        # its device request is cancelled. Reconcile from both durable rows.
        cancelled_requests = self.repository.list_cancelled_creation_execution_requests()
        for row in cancelled_requests:
            event = self.repository.execution_records.get_task_event(row["workspace_id"], f"event_{row['owner_id']}_start")
            # 撤销方向不需要重新核成员：即使成员已被移除，撤销也必须能落下去。
            try:
                original = CreationExecutionActor.from_event(row["workspace_id"], event.payload)
            except (ValueError, KeyError, TypeError, AttributeError):
                logger.warning("已停止制作请求 %s 的持久身份无法复核", row["id"])
                continue
            if (not row["member_id"] or original.member_id != row["member_id"]
                    or (original.device_id is None and row["workspace_id"] != LEGACY_WORKSPACE_ID)):
                raise RuntimeError("已停止制作请求的持久身份无法复核，不能自动取消设备许可")
            runtime.scheduler.cancel(row["id"], original)
        for job in self.repository.list_pending_creation_executions():
            event = self.repository.execution_records.get_task_event(job.workspace_id, f"event_{job.id}_start")
            try:
                original = CreationExecutionActor.from_event(job.workspace_id, event.payload)
            except (ValueError, KeyError, TypeError, AttributeError):
                logger.warning("制作作业 %s 缺少可复核的持久身份，保留待处理", job.id)
                continue
            if self.repository.creation_narration_usage(job.workspace_id,job.id)['unsettled']:
                try:
                    if not executor.recover_pcm_unit(job,original,runtime.scheduler):
                        continue
                    job=self.repository.get_job_run(job.workspace_id,job.id)
                except (PermissionError,ValueError,KeyError,RecoveryBlocked,OSError) as exc:
                    logger.warning('原PCM作业 %s 结果尚不可核实，保留原请求：%s',job.id,exc)
                    continue
            if job.status == "interrupted":
                task = self.repository.get_creation_task(job.workspace_id, job.task_id)
                request = self.repository.creation_execution_wait_row(
                    job.workspace_id, original.member_id, job.id, job.input_digest)
                stage = request["unit_key"].split(":", 1)[0] if request is not None else task.stage
                self.repository.interrupt_creation_execution(TaskEvent(
                    id=f"event_{job.id}_recovery_{job.attempt}", workspace_id=job.workspace_id,
                    task_id=job.task_id, job_id=job.id, event_type="creation_execution_stage",
                    payload={"stage": stage, "status": "interrupted",
                             "reason": job.failure_reason or "process_interrupted", "permit_retained": False},
                    created_at=job.updated_at),
                    impact=job.impact or "本次执行中断，上游有效产物保留。",
                    repair_action="旧执行者已确认释放；继续前将重新核验身份、输入和资源。",
                    task_is_current=lambda conn: runtime.registrations["creation_job"].owner.owns_task_stage(
                        conn, job.workspace_id, job.id, stage),
                    expected_free_permit_generation=generation)
            owner = runtime.registrations["creation_job"].owner
            if owner.task_authorizations is not None:
                task = self.repository.get_creation_task(job.workspace_id, job.task_id)
                try:
                    owner.task_authorizations.ensure(task, original)
                except TaskAuthorizationDenied as exc:
                    logger.warning("制作作业 %s 的原任务授权不可用，保留待处理：%s",job.id,exc)
                    continue
                verdict = owner.authorization_verdict(task, original, "render")
            else:
                verdict = self.verify_identity(original)
            if verdict != "ready":
                logger.warning("制作作业 %s 的成员许可失效或未知，保留待处理", job.id)
                continue
            try:
                submit_creation_stage(self.execution, runtime.scheduler, job, original)
            except (PermissionError, ValueError, RecoveryBlocked) as exc:
                logger.warning("制作作业 %s 的当前许可或输入未就绪，保留待处理：%s", job.id, exc)
        runtime.wake()

    def _edit_source(self, task, proposal, actor, source):
        if source is not None:
            return source
        base_id=proposal.operation['payload']['base_version_id']
        base=self.repository.get_timeline_version(task.workspace_id,base_id)
        return {'source_kind':'interactive','workspace_id':task.workspace_id,'task_id':task.id,
            'member_id':actor.user_id or actor.member_id,'actor_identity':actor.identity_payload(),
            'proposal_id':proposal.id,'expected_timeline_version_id':base.id,'expected_timeline_sequence':base.sequence}

    def submit_person_edit(self, workspace_id, task, proposal, actor, runtime, *, source=None, write_guard=None):
        operation=proposal.operation
        source=self._edit_source(task,proposal,actor,source)
        script=self.repository.get_creation_task_script(workspace_id,task.confirmed_script_id)
        plan=self.repository.get_creation_plan(workspace_id,task.plan_id)
        digest=person_edit_digest(operation)
        payload={**actor.identity_payload(),'script_id':script.id,'plan_id':plan.id,
            'revision_digest':revision_digest(script,plan),'input_digest':digest,'edit_proposal_id':proposal.id,'source':source}
        candidate=JobRun(f'person_edit_{uuid4().hex}',workspace_id,task.id,PERSON_EDIT_KIND,
            f'person-edit:{workspace_id}:{task.id}:{actor.member_id}:{proposal.id}',digest)
        owner=runtime.registrations['creation_job'].owner
        job=None
        created=False
        try:
            key,_=owner.edit_facts(candidate,task,payload,plan)
            with self.repository._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if write_guard is not None:
                    write_guard(conn)
                prior=conn.execute('SELECT * FROM job_runs WHERE workspace_id=? AND idempotency_key=?',
                    (workspace_id,candidate.idempotency_key)).fetchone()
                if prior is not None and prior['status'] not in {'queued','running','interrupted'}:
                    raise ValueError('这次人物编辑已结束，请查看作业结果后重新预览')
                active=conn.execute("SELECT j.* FROM job_runs j JOIN task_events e ON e.job_id=j.id AND e.workspace_id=j.workspace_id "
                    "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND j.input_digest=? "
                    "AND j.status IN ('queued','running','interrupted') AND e.event_type='person_edit_requested' "
                    "AND json_extract(e.payload_json,'$.member_id')=? ORDER BY j.created_at DESC LIMIT 1",
                    (workspace_id,task.id,PERSON_EDIT_KIND,digest,actor.member_id)).fetchone()
                if active is not None:
                    job=self.repository._job_run_from_row(active)
                    saved=self.repository.execution_records.get_task_event(workspace_id,f'event_{job.id}_start',connection=conn).payload
                    if saved.get('source')!=source:
                        raise ValueError('原人物编辑属于另一输入，请查询原请求')
                else:
                    job=self.repository.create_job_run(candidate,connection=conn)
                    created=True
                    self.repository.execution_records.append_task_event(TaskEvent(f'event_{job.id}_start',workspace_id,task.id,job.id,
                        'person_edit_requested',payload,created_at=job.created_at),connection=conn)
                owner.verify_submission(SubmissionIntent(workspace_id,'creation_job',job.id,digest,key,SchedulePolicy()),
                    actor,connection=conn)
            queued=runtime.scheduler.submit(SubmissionIntent(workspace_id,'creation_job',job.id,digest,key,SchedulePolicy()),actor)
        except (PermissionError,KeyError,StopIteration,ValueError):
            if created:
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='failed',failure_reason='person_edit_not_submitted',"
                        "impact='人物编辑未安排成功，当前版本保持不变。',repair_action='修复原因后重新预览人物修改。',"
                        "finished_at=?,updated_at=? WHERE workspace_id=? AND id=? AND status='queued'",
                        (datetime.now().astimezone().isoformat(),datetime.now().astimezone().isoformat(),workspace_id,job.id))
            raise
        runtime.wake()
        return job,queued

    def submit_narration_edit(self, workspace_id, task, proposal, actor, runtime, *, source=None, write_guard=None):
        """One original edit Job, source adoption, then its existing bounded queue."""
        owner = runtime.registrations['creation_job'].owner
        with self.repository.creation_execution_read() as reader:
            snapshot = self.repository.read_creation_execution_snapshot(workspace_id, task.id, connection=reader)
        task, script, plan = snapshot.task, snapshot.script_header, snapshot.plan
        digest = narration_edit_digest(proposal.operation)
        candidate = JobRun(id=f'job_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id,
            kind=NARRATION_EDIT_KIND, input_digest=digest,
            idempotency_key=f'narration-edit:{task.id}:{actor.member_id}:{digest}:{proposal.id}')
        source=self._edit_source(task,proposal,actor,source)
        payload = {**actor.identity_payload(), 'script_id': script.id, 'plan_id': plan.id, 'source':source,
            'revision_digest': snapshot.input_digest, 'input_digest': digest, 'edit_proposal_id': proposal.id}
        def existing(conn):
            prior = conn.execute('SELECT * FROM job_runs WHERE workspace_id=? AND idempotency_key=?',
                (workspace_id, candidate.idempotency_key)).fetchone()
            if prior is not None and prior['status'] not in {'queued', 'running', 'interrupted'}:
                raise ValueError('这次配音编辑已结束，请查看作业结果后重新预览')
            return conn.execute("SELECT j.* FROM job_runs j JOIN task_events e ON e.job_id=j.id AND e.workspace_id=j.workspace_id "
                "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND j.input_digest=? "
                "AND j.status IN ('queued','running','interrupted') AND e.event_type='narration_edit_requested' "
                "AND json_extract(e.payload_json,'$.member_id')=? ORDER BY j.created_at DESC LIMIT 1",
                (workspace_id, task.id, NARRATION_EDIT_KIND, digest, actor.member_id)).fetchone()
        with self.repository._connect() as conn:
            active = existing(conn)
        from app.creation.narration_pcm import prepare_narration_source
        with (prepare_narration_source(self.repository, task, plan, actor) if active is None else nullcontext(None)) as prepared:
            preparation = {'_narration_reference': prepared['narration_reference']} if prepared else None
            owner.narration_edit_facts(candidate, task, payload, plan, pcm_preparation=preparation)
            with self.repository._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if write_guard is not None:
                    write_guard(conn)
                active = existing(conn)
                if active is not None:
                    job = self.repository._job_run_from_row(active)
                    saved=self.repository.execution_records.get_task_event(workspace_id,f'event_{job.id}_start',connection=conn).payload
                    if saved.get('source')!=source:
                        raise ValueError('原配音编辑属于另一输入，请查询原请求')
                else:
                    if prepared is None:
                        raise ValueError('原配音编辑状态已变化，请查询原请求')
                    job = self.repository.create_job_run(candidate, connection=conn)
                    source_id = self.repository.seal_creation_narration_source(job, prepared,
                        input_digest=digest, origin_event_id=f'event_{job.id}_start', connection=conn)
                    self.repository.execution_records.append_task_event(TaskEvent(f'event_{job.id}_start', workspace_id, task.id, job.id,
                        'narration_edit_requested', {**payload, 'source_binding_event_id': source_id}), connection=conn)
                    owner.verify_submission(SubmissionIntent(workspace_id, 'creation_job', job.id, digest,
                        owner.narration_edit_facts(job, task, payload, plan, connection=conn, pcm_preparation=preparation)[0],
                        SchedulePolicy()), actor, connection=conn, pcm_preparation=preparation)
            usage = self.repository.creation_narration_usage(workspace_id, job.id)
            if usage['unsettled']:
                queued = runtime.scheduler._get(usage['unsettled'][0]['request_id'])
                if (queued.workspace_id, queued.owner_id, queued.member_id) != (workspace_id, job.id, actor.member_id):
                    raise PermissionError('局部配音原请求与当前成员不一致')
            else:
                queued = migrate_creation_narration_request(self.repository, runtime.scheduler, job, actor)
                if queued is None:
                    original = self.repository.creation_execution_input(workspace_id, job.id)
                    key, _ = owner.narration_edit_facts(job, task, original, plan)
                    queued = runtime.scheduler.submit(SubmissionIntent(workspace_id, 'creation_job', job.id,
                        digest, key, SchedulePolicy()), actor)
        runtime.wake()
        return job, queued

    def select_narration_voice(self, *, runtime, actor=None, write_guard=None, **inputs):
        with ExitStack() as stack:
            return self._select_narration_voice(runtime=runtime, stack=stack, actor=actor, write_guard=write_guard, **inputs)

    def _select_narration_voice(self, *, runtime, stack, actor, write_guard=None, **inputs):
        try:
            self.repository.get_creation_task_narration_voice_selection(inputs['workspace_id'], inputs['task_id'],
                inputs['request_id'], member_id=inputs['member_id'])
        except KeyError:
            pass
        else:
            # The repository verifies the original input fingerprint before
            # returning its receipt; replay never prepares/rebinds execution.
            return self.repository.select_creation_task_narration_voice(**inputs)

        def require_same_actor(original):
            if (not isinstance(actor, CreationExecutionActor) or original != actor
                    or inputs['member_id'] != (actor.user_id or actor.member_id)):
                raise TaskAuthorizationDenied('声音选择不属于原作业成员')

        voice_authorization = None
        source_preparation = None
        job = self.execution.latest(inputs['workspace_id'], inputs['task_id'])
        if job is not None and job.status == 'queued':
            owner = runtime.registrations['creation_job'].owner if runtime is not None else None
            if owner is None:
                raise ValueError('原排队配音需要原设备调度器')
            with self.repository._connect() as conn:
                _job, task, start, _script, plan = owner._facts(conn, job.workspace_id, job.id)
            original = CreationExecutionActor.from_event(job.workspace_id, start)
            require_same_actor(original)
            if plan.selected_voice is not None and (plan.selected_voice.profile_id, plan.selected_voice.version) == (
                    inputs['voice_profile_id'], inputs['voice_version']):
                raise ValueError('原作业相同声音不能通过重新选择补建来源证据')
            selected_voice = self.repository.creation_plan_voice_reference(job.workspace_id,
                inputs['voice_profile_id'], inputs['voice_version'], viewer_user_id=inputs['member_id'])
            from app.creation.narration_pcm import prepare_narration_source
            source_preparation = stack.enter_context(prepare_narration_source(self.repository, task,
                plan, actor, selected_voice=selected_voice))
            source_preparation['selected_voice'] = selected_voice
            if owner.task_authorizations is not None:
                voice_authorization = owner.task_authorizations.prepare_voice_selection(task, actor,
                    inputs['voice_profile_id'], inputs['voice_version'])

        def rebind(conn, original_job, previous_plan, selection_event_id):
            if runtime is None or runtime.scheduler.database_path != self.repository.database_path.resolve():
                raise ValueError('原排队配音需要同一工作区的设备调度器才能安全改选声音')
            if runtime.last_error is not None:
                raise ValueError('设备许可状态需要核对，原排队声音选择未提交')
            owner = runtime.registrations['creation_job'].owner
            job, task, start, script, plan = owner._facts(conn, original_job.workspace_id, original_job.id)
            original = CreationExecutionActor.from_event(job.workspace_id, start)
            require_same_actor(original)
            if (job != original_job
                    or owner._check_facts(job, task, start, script, previous_plan, 'narration', actor.member_id) != 'ready'):
                raise ValueError('原排队作业身份、确认文案或方案版本已变化，不能改选声音')

            def replace_input(connection, old):
                # The selection owner just committed the checked plan mutation in
                # this transaction; seal its resulting business revision.
                source_preparation['adoption_revision_digest'] = revision_digest(script, plan)
                source_id = self.repository.seal_creation_narration_source(job, source_preparation,
                    input_digest=revision_digest(script, plan), origin_event_id=f'{selection_event_id}_execution',
                    connection=connection)
                if owner.task_authorizations is not None:
                    if voice_authorization is None:
                        raise TaskAuthorizationDenied('原作业缺少本次固定声音的实时授权核验')
                    voice_authorization(connection, plan.selected_voice)
                rebound = self.repository.rebind_unstarted_creation_voice(job, revision_digest(script, plan),
                    selection_event_id, connection=connection, source_binding_event_id=source_id)
                preparation = {'_narration_reference': source_preparation['narration_reference']}
                key, _resources = owner.narration_unit_facts(connection, rebound, task, script, plan, pcm_preparation=preparation)
                intent = SubmissionIntent(job.workspace_id, 'creation_job', job.id, rebound.input_digest, key, old.policy)
                return intent, owner.verify_submission(intent, actor, connection=connection, pcm_preparation=preparation)

            runtime.scheduler.replace_unstarted_narration_voice(job, connection=conn, replacement_write=replace_input)

        receipt = self.repository.select_creation_task_narration_voice(**inputs, queued_job_rebind=rebind,
            voice_source_preparation=source_preparation, write_guard=write_guard)
        if runtime is not None:
            runtime.wake()
        return receipt

    def read_visual_decision(self, workspace_id, task_id, job_id, section_id, actor, *, offset=0, limit=8):
        return self.segments.read_visual_decision(workspace_id,task_id,job_id,section_id,actor,offset=offset,limit=limit)

    def adopt_visual_decision(self, workspace_id, task_id, job_id, section_id, actor, payload, *, runtime):
        try:
            receipt=self.segments.adopt_visual_decision(workspace_id,task_id,job_id,section_id,actor,
                decision_digest=payload.decision_digest,confirmed_clip_ids=payload.confirmed_clip_ids,runtime=runtime)
        except PermissionError:
            raise
        except Exception as exc:
            # Adoption commits before queue submission; classify only a matching durable receipt.
            view=self.read_visual_decision(workspace_id,task_id,job_id,section_id,actor)
            if (view['status']=='adopted' and view['adopted'] and view['decision_digest']==payload.decision_digest
                    and len(payload.confirmed_clip_ids)==len(set(payload.confirmed_clip_ids))
                    and set(view['confirmed_clip_ids'])==set(payload.confirmed_clip_ids)):
                raise ExecutionControlRejected(status_code=409,detail={'error':'visual_decision_queue_unknown',
                    'reason':'画面采用已保存，原作业排队状态需回查；不要重新生成配音或更换作业。',
                    'submission_effect':'unknown','job_id':job_id}) from exc
            raise
        view=self.read_visual_decision(workspace_id,task_id,job_id,section_id,actor)
        return {'decision':view,'queue':{'request_id':receipt['device_request_id'],'replayed':receipt['replayed']},
            'execution':self.read_bound(workspace_id,task_id,job_id,actor,verify_media=False)}

    def read_bound(self, workspace_id, task_id, job_id, actor, *, verify_media: bool):
        job=self.repository.get_job_run(workspace_id,job_id)
        if job.kind=='creation_task_segment_revision':
            start=self.repository.execution_records.get_task_event(workspace_id,f'event_{job.id}_start').payload
        else:
            start=self.repository.creation_execution_input(workspace_id,job.id)
            latest=self.execution.latest(workspace_id,task_id)
            if latest is None or latest.id!=job.id:
                raise ValueError('原制作作业已被新的制作请求替代')
        if (job.kind not in {'creation_task_execution','creation_task_segment_revision'} or job.task_id!=task_id
                or CreationExecutionActor.from_event(workspace_id,start)!=actor or start['input_digest']!=job.input_digest):
            raise ValueError('原制作作业身份或任务不可核验')
        return self.status(workspace_id,task_id,verify_media=verify_media,actor=actor,job_id=job.id)

    def job_for_request(self, workspace_id: str, request_id: str):
        """这条设备许可背后是不是一个制作作业；不是就返回 ``None``。"""
        row = self.repository.creation_job_for_device_request_row(workspace_id, request_id)
        if row is None:
            return None
        return self.repository.get_job_run(workspace_id, row["owner_id"])

    def task_or_missing(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise ExecutionControlRejected(status_code=404, detail="creation task not found") from exc

    def plan_for_task_or_missing(
        self,
        workspace_id: str,
        task: CreationTask,
    ) -> CreationPlan:
        try:
            plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise ExecutionControlRejected(status_code=404, detail="creation task not found") from exc
        if plan.creation_task_id != task.id:
            raise ExecutionControlRejected(status_code=404, detail="creation task not found")
        return plan

    def render_valid(self, workspace_id, task_id, *, verify_media):
        try:
            return self.render_stage.valid(workspace_id, task_id, verify_media=verify_media)
        except RenderRejected as exc:
            raise ExecutionControlRejected(status_code=exc.status_code, detail=exc.detail) from exc
