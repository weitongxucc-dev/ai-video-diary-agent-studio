"""Automation adapters to existing production facts, confirmations and the sole worker."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import sqlite3
from dataclasses import asdict, replace

from app.automation_service import AutomationBlocked, AutomationService, AutomationTrigger, dump, utc_now
from app.creation_execution import CreationJobOwnerVerifier
from app.platform_publish.queue import PlatformPublishOwnerVerifier
from app.creation.contracts import CreationTask
from app.schemas import CreationPlanPreviewRequest
from fastapi import HTTPException

log = logging.getLogger(__name__)


def automation_job_gate(request, conn):
    row = conn.execute("""SELECT a.paused,r.definition_json,r.member_id FROM automation_runs r
        JOIN automations a ON a.id=r.automation_id JOIN job_runs j ON j.workspace_id=r.workspace_id AND j.task_id=r.task_id
        WHERE j.id=? AND r.workspace_id=?""", (request.owner_id,request.workspace_id)).fetchone()
    if row is None:
        return "ready"
    if request.member_id != row["member_id"]:
        return "unauthorized"
    # An admitted unit may finish under its original still-valid grant; future units wait.
    if request.state in {"granted","yield_requested"}:
        return "ready"
    trigger = AutomationTrigger.model_validate(json.loads(row["definition_json"])["trigger"])
    return "dependency" if row["paused"] or not trigger.allows(utc_now()) else "ready"


class AutomationCreationOwner(CreationJobOwnerVerifier):
    def check_current(self, request, conn):
        verdict = super().check_current(request, conn)
        return automation_job_gate(request,conn) if verdict == "ready" else verdict

    def check_local(self, request, conn, observed):
        verdict = super().check_local(request, conn, observed)
        return automation_job_gate(request, conn) if verdict == "ready" else verdict


class AutomationPlatformPublishOwner(PlatformPublishOwnerVerifier):
    def check_local(self, request, conn, observed):
        verdict = super().check_local(request, conn, observed)
        return automation_job_gate(request, conn) if verdict == "ready" else verdict

    def check_current(self, request, conn):
        verdict = super().check_current(request, conn)
        if verdict != "ready":
            return verdict
        associated = conn.execute("SELECT 1 FROM automation_runs r JOIN job_runs j ON j.workspace_id=r.workspace_id AND j.task_id=r.task_id WHERE j.id=? AND r.workspace_id=?",(request.owner_id,request.workspace_id)).fetchone()
        if associated is not None and self.verify_identity is not None:
            _job,event = self._facts(conn,request.workspace_id,request.owner_id)
            actor = self.actor_from_event(event["identity"])
            identity = self.verify_identity(actor)
            if identity != "ready":
                return identity
        return automation_job_gate(request,conn)


def restrict_automation_registration(registration, *, publishing=False):
    """Reuse original owner configuration and executor; keep one admission path."""
    owner = registration.owner
    configuration = vars(owner).copy()
    repository = configuration.pop("repository")
    owner_class = AutomationPlatformPublishOwner if publishing else AutomationCreationOwner
    return replace(registration,owner=owner_class(repository,**configuration))


class AutomationRuntime:
    def __init__(self, production, sources, *, verify_identity, list_templates,
                 preflight, preview_response, plan_response, execution_status, sync_connector, inspect_topic_route, execution_available):
        self.production, self.sources = production, sources
        self.list_templates = list_templates
        self.preflight, self.preview_response, self.plan_response = preflight, preview_response, plan_response
        self.execution_status, self.sync_connector = execution_status, sync_connector
        self.inspect_topic_route = inspect_topic_route
        self.execution_available = execution_available
        self.service = AutomationService(production.database_path,verify_identity=verify_identity,
            read_inputs=self.read_inputs,project_run=self.project_run,check_capabilities=self.check_capabilities)
        self.stop_event = threading.Event()
        self._paused = threading.Event()
        self._tick_lock = threading.Lock()
        self._wake = threading.Event()
        self.thread = None
        self.driver_error = None

    @staticmethod
    def reader(actor):
        return actor.user_id or actor.member_id

    def check_capabilities(self, actor):
        if self.inspect_topic_route(actor.workspace_id)["availability"] == "unconfigured":
            raise AutomationBlocked("capability_unavailable","当前没有可调用的文字模型连接，本次未生成候选","配置文字模型连接后重试本次运行；仍需逐次确认")

    def options(self, actor):
        self.service.require_actor(actor)
        ws, user = actor.workspace_id, self.reader(actor)
        materials = {asset.id:asset for asset,_ in self.production.automatic_material_segments(ws,viewer_user_id=user,local_diagnostic=False)}
        return {"templates":[{"id":t.id,"version":t.version,"name":t.name} for t in self.list_templates(actor)],
                "connectors":[{"id":c.id,"label":c.platform,"status":c.connection_status} for c in self.sources.list_connectors(ws) if "read" in c.permissions],
                "knowledge":[{"id":k.id,"label":k.title,"version":k.version} for k in self.sources.list_knowledge_items_for_use(ws,purpose="reference",user_id=user) if k.use_state != "not_for_automation"],
                "materials":[{"id":a.id,"label":a.file_name,"version":a.updated_at.isoformat()} for a in materials.values()],
                "driver":{"running":bool(self.thread and self.thread.is_alive()),"error":self.driver_error}}

    def read_inputs(self, actor, definition):
        ws, user = actor.workspace_id, self.reader(actor)
        scope = definition["source_scope"]
        templates = {(t.id,t.version):t for t in self.list_templates(actor)}
        template = templates.get((definition["template_id"],definition["template_version"]))
        if template is None:
            raise AutomationBlocked("template_unavailable","固定模板版本已不可读","选择当前成员有权使用的模板版本")
        connectors = {c.id:c for c in self.sources.list_connectors(ws) if "read" in c.permissions}
        knowledge = {k.id:k for k in self.sources.list_knowledge_items_for_use(ws,purpose="reference",user_id=user) if k.use_state != "not_for_automation"}
        materials = {a.id:a for a,_ in self.production.automatic_material_segments(ws,viewer_user_id=user,local_diagnostic=False)}
        for field, allowed in (("connector_ids",connectors),("knowledge_ids",knowledge),("material_asset_ids",materials)):
            if any(key not in allowed for key in scope[field]):
                raise AutomationBlocked("scope_unavailable","所选来源已不可用、无权限或不允许自动使用","重新核对当前成员的来源范围；不会改用全库")
        items = [item for item in self.sources.list_source_items_for_use(ws,purpose="reference") if item.connector_id in scope["connector_ids"] and item.use_state != "not_for_automation"]
        return {"template":{key:value for key,value in json.loads(dump(asdict(template))).items() if key != "created_at"},
                "knowledge":[{"id":key,"version":knowledge[key].version} for key in sorted(set(scope["knowledge_ids"]))],
                "materials":[{"id":key,"updated_at":materials[key].updated_at.isoformat()} for key in sorted(set(scope["material_asset_ids"]))],
                "sources":[{"id":item.id,"connector_id":item.connector_id,"identity":hashlib.sha256(dump(asdict(item)).encode()).hexdigest()} for item in sorted(items,key=lambda item:item.id)],
                "source_run_ids":[connectors[key].latest_run_id for key in sorted(set(scope["connector_ids"])) if connectors[key].latest_run_id]}

    def require_task_open(self, workspace_id, task_id, actor_for):
        with self.service.connect() as conn:
            row = conn.execute("SELECT id FROM automation_runs WHERE workspace_id=? AND task_id=?",(workspace_id,task_id)).fetchone()
        if row is None:
            return
        actor = actor_for()
        run = self.service.get_run(actor,row["id"])
        try:
            self.service.require_open(run)
        except AutomationBlocked as exc:
            self.service.write_run_state(run["id"],state="blocked",stage=run["stage"],reason_code=exc.code,reason=exc.reason,repair_action=exc.repair,expected_revision=run["revision"])
            raise

    def current_inputs(self, actor, run):
        try:
            self.service.require_actor(actor)
            inputs = self.read_inputs(actor,run["definition"])
            if run["input_snapshot"] != inputs:
                raise AutomationBlocked("inputs_changed","本次固定资料版本或授权已变化，未确认新任务","重试本次运行并重新预检确认最新输入")
            return inputs
        except AutomationBlocked as exc:
            self.service.write_run_state(run["id"],state="blocked",stage="inputs",reason_code=exc.code,reason=exc.reason,repair_action=exc.repair,expected_revision=run["revision"])
            raise

    def sync_sources(self, actor, run_id):
        run = self.service.get_run(actor,run_id)
        self.service.require_open(run)
        if run["task_id"]:
            raise ValueError("本次任务已固定输入，来源同步请用于后续运行")
        self.read_inputs(actor,run["definition"])
        try:
            for connector_id in run["definition"]["source_scope"]["connector_ids"]:
                connector = self.sources.get_connector(actor.workspace_id,connector_id)
                self.sync_connector(actor.workspace_id,connector)
        except HTTPException as exc:
            detail = exc.detail
            reason = detail if isinstance(detail,str) else detail.get("detail", "本次所选来源同步失败")
            repair = "核对原来源配置与真实同步记录后重试" if isinstance(detail,str) else detail.get("repair_action", "核对原来源配置后重试")
            self.service.write_run_state(run_id,state="blocked",stage="inputs",reason_code="source_sync_failed",reason=reason,repair_action=repair,snapshot=self.read_inputs(actor,run["definition"]))
            raise
        self.service.prepare(actor,run)
        return self.service.get_run(actor,run_id)

    def preview(self, request, actor, run_id):
        run = self.service.get_run(actor,run_id)
        self.service.require_open(run)
        self.current_inputs(actor,run)
        plan = self.preflight(request,actor.workspace_id,self.plan_input(run),automation_scope=run["definition"]["source_scope"])
        response = self.preview_response(plan)
        blockers = [b for b in plan.blockers if b.blocks_confirmation]
        if blockers:
            first = blockers[0]
            self.service.write_run_state(run_id,state="blocked",stage="plan",reason_code=first.reason,reason=first.impact,repair_action=first.repair_action)
        return response

    @staticmethod
    def plan_input(run):
        definition = run["definition"]
        return CreationPlanPreviewRequest(goal=definition["goal"],name=definition["name"],template_id=definition["template_id"],template_version=definition["template_version"])

    def confirm(self, request, actor, run_id, fingerprint):
        run = self.service.get_run(actor,run_id)
        # Crash-safe replay reads persisted IDs before checking mutable input facts.
        if run["task_id"]:
            if run["confirmation"].get("plan",{}).get("fingerprint") != fingerprint:
                raise ValueError("本次运行已用不同输入确认，请回读已有任务")
            return self.bound_result(actor,run)
        self.service.require_open(run)
        self.current_inputs(actor,run)
        plan = self.preflight(request,actor.workspace_id,self.plan_input(run),automation_scope=run["definition"]["source_scope"])
        if plan.preflight_fingerprint != fingerprint:
            raise ValueError("预检资料、许可或模型状态已变化，请重新预检")
        now = utc_now()
        plan.id = "plan_" + hashlib.sha256(run_id.encode()).hexdigest()[:24]
        plan.created_at = plan.confirmed_at = now
        task = CreationTask(id="task_"+hashlib.sha256(run_id.encode()).hexdigest()[:24],workspace_id=actor.workspace_id,
            plan_id=plan.id,name=plan.name,scenario=plan.goal,created_at=now,updated_at=now)
        plan,task = self.production.prepare_confirmed_creation_plan(plan,task,verified_owner_user_id=self.reader(actor),local_diagnostic=False)
        self.service.bind_confirmed_plan(self.production,actor,run_id,run,fingerprint,plan,task,now)
        return self.bound_result(actor,self.service.get_run(actor,run_id))

    def bound_result(self, actor, run):
        return self.plan_response(self.production.get_creation_plan(actor.workspace_id,run["plan_id"]),self.production.get_creation_task(actor.workspace_id,run["task_id"]))

    def project_run(self, actor, run):
        projection = self.project_state(actor, run)
        events = self.production.execution_records.list_task_events(actor.workspace_id,run["task_id"])
        confirmation = dict(run["confirmation"])
        task = self.production.get_creation_task(actor.workspace_id,run["task_id"])
        if task.selected_topic_id:
            confirmation["topic"] = {"topic_id":task.selected_topic_id}
        if task.confirmed_script_id:
            confirmation["script"] = {"script_id":task.confirmed_script_id}
        starts = [event for event in events if event.event_type == "creation_execution_started"]
        if starts:
            confirmation["make"] = {"event_id":starts[-1].id,"confirmed_at":starts[-1].created_at.isoformat(),"identity":starts[-1].payload}
        publishes = [event for event in events if event.event_type == "platform_publish_prepared" and event.payload.get("confirmed") is True and event.payload.get("identity",{}).get("member_id") == actor.member_id]
        if publishes:
            confirmation["publish"] = {"event_id":publishes[-1].id,"confirmed_at":publishes[-1].created_at.isoformat(),"job_id":publishes[-1].job_id}
        jobs = self.production.list_job_runs(actor.workspace_id,task.id)
        with self.production._connect() as conn:
            requests = [row[0] for row in conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' AND owner_id IN (SELECT id FROM job_runs WHERE workspace_id=? AND task_id=?)",(actor.workspace_id,actor.workspace_id,task.id))]
        projection.update(confirmation=confirmation,execution={"job_ids":[job.id for job in jobs],"device_request_ids":requests,"output_refs":[ref for job in jobs for ref in job.output_refs]})
        return projection

    def project_state(self, actor, run):
        task = self.production.get_creation_task(actor.workspace_id,run["task_id"])
        if task.owner_user_id != self.reader(actor):
            raise AutomationBlocked("task_permission_lost","本次任务当前归属无法核验","恢复原成员许可后重试")
        status = self.execution_status(actor.workspace_id,task.id)
        job = status["job"]
        publish_jobs = [item for item in self.production.list_job_runs(actor.workspace_id,task.id) if item.kind == "platform_publish"]
        if publish_jobs:
            published = publish_jobs[-1]
            if published.status == "succeeded":
                return dict(state="completed",stage="publish",reason="本次经明确确认的发布作业已成功",job_id=published.id)
            if published.status == "running":
                return dict(state="running",stage="publish",reason="本次经明确确认的发布作业正在执行",job_id=published.id)
            if published.status in {"queued","interrupted"}:
                try:
                    self.service.require_open(run)
                except AutomationBlocked as exc:
                    return dict(state="blocked",stage="publish",job_id=published.id,reason_code=exc.code,reason=exc.reason,repair_action=exc.repair)
                return dict(state="blocked",stage="publish",job_id=published.id,reason_code="device_wait",reason="发布作业已入队，尚未取得原平台与设备许可",repair_action="打开任务核对发布与资源许可")
            if published.status == "failed":
                return dict(state="failed",stage="publish",reason_code="publish_failed",reason=published.failure_reason,repair_action=published.repair_action,job_id=published.id)
        if job:
            if job["status"] == "running":
                return dict(state="running",stage=status["current_stage"] or "make",job_id=job["id"],reason=status["waiting_reason"])
            if job["status"] == "failed":
                return dict(state="failed",stage=status["current_stage"] or "make",job_id=job["id"],reason_code="job_failed",reason=job["failure_reason"],repair_action=job["repair_action"])
            if job["status"] == "cancelled":
                return dict(state="cancelled",stage="make",job_id=job["id"],reason_code="job_cancelled",reason="实际制作作业已取消",repair_action="通过原任务核对恢复动作")
            if status["video_ready"]:
                return dict(state="waiting_review",stage="publish",job_id=job["id"],reason_code="publish_confirmation_required",reason="真实成片已就绪，发布仍需本次明确确认",repair_action="打开创作任务核对成片与发布")
            try:
                self.service.require_open(run)
            except AutomationBlocked as exc:
                return dict(state="blocked",stage=status["current_stage"] or "make",job_id=job["id"],reason_code=exc.code,reason=exc.reason,repair_action=exc.repair)
            return dict(state="blocked",stage=status["current_stage"] or "make",job_id=job["id"],reason_code="device_wait",reason=status["waiting_reason"] or "真实作业已入队，尚未取得下一单元的设备许可",repair_action="打开任务核对资源许可与继续制作")
        stage = "make" if task.confirmed_script_id else "script" if task.selected_topic_id else "topic"
        try:
            self.service.require_open(run)
        except AutomationBlocked as exc:
            return dict(state="blocked",stage=stage,reason_code=exc.code,reason=exc.reason,repair_action=exc.repair)
        if stage in {"topic","script"}:
            try:
                self.check_capabilities(actor)
            except AutomationBlocked as exc:
                return dict(state="blocked",stage=stage,reason_code=exc.code,reason=exc.reason,repair_action=exc.repair)
        elif not self.execution_available():
            return dict(state="blocked",stage="make",reason_code="resource_permission_unavailable",reason="本机资源许可运行时尚未就绪，本次未启动媒体执行",repair_action="恢复主API并完成本机预算标定，再通过原任务确认制作")
        return dict(state="waiting_review",stage=stage,reason_code=stage+"_confirmation_required",reason="本次"+{"make":"制作要求","script":"文案","topic":"选题"}[stage]+"等待明确确认",repair_action="打开创作任务继续")

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.drive,name="automation-due-driver",daemon=True)
        self.thread.start()

    def drive(self):
        while not self.stop_event.is_set():
            try:
                with self._tick_lock:
                    if not self.stop_event.is_set() and not self._paused.is_set():
                        self.service.tick()
                        self.driver_error = None
            except (ValueError,KeyError,OSError,sqlite3.Error) as exc:
                self.driver_error = str(exc)
                log.exception("Automation due scan failed; no execution success is inferred")
            self._wake.wait(5)
            self._wake.clear()

    def pause(self, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("暂停等待时间不能为负数")
        if not self._tick_lock.acquire(timeout=timeout_seconds):
            return False
        try:
            self._paused.set()
        finally:
            self._tick_lock.release()
        return True

    def resume(self) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("自动任务驱动已终止，不能恢复原循环")
        self._paused.clear()
        self._wake.set()

    def request_stop(self):
        self.stop_event.set()
        self._wake.set()

    def stop(self, timeout_seconds=10):
        self.request_stop()
        if self.thread:
            self.thread.join(timeout=max(0, timeout_seconds))
            return not self.thread.is_alive()
        return True
