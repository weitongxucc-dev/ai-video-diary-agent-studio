"""Persistent, member-scoped automation definitions and per-occurrence runs.

The due driver only claims an occurrence and reads current local input facts.
Confirmation and media execution always go through the existing production owners.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.creation_execution import CreationExecutionActor
from app.device_scheduler import Window

CONFIRMATION_POLICY = {name: "required" for name in ("topic", "plan", "script", "make", "publish")}


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda item: item.isoformat())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_instant(local: datetime, zone: ZoneInfo) -> datetime:
    """Reject both missing and ambiguous wall-clock times instead of guessing a fold."""
    candidates = {local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)
                  if local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local}
    if len(candidates) != 1:
        raise ValueError("所选当地时间不存在或有夏令时歧义，请选择明确时间")
    return candidates.pop()


class AutomationTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadence: Literal["once", "daily"]
    timezone: str
    start_local: datetime
    window_start: str | None = None
    window_end: str | None = None

    @model_validator(mode="after")
    def validate_time(self):
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("请选择有效的IANA时区") from exc
        if self.start_local.tzinfo is not None:
            raise ValueError("开始时间应为所选时区的当地时间，不同时提交偏移")
        local_instant(self.start_local, zone)
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("执行窗口必须同时给出开始和结束")
        if self.window_start is not None:
            Window(self.timezone, self.window_start, self.window_end)
        return self

    def first_due(self):
        return local_instant(self.start_local, ZoneInfo(self.timezone))

    def next_due(self, due: datetime, now: datetime):
        if self.cadence == "once":
            return None
        zone = ZoneInfo(self.timezone)
        date = max(due.astimezone(zone).date() + timedelta(days=1), now.astimezone(zone).date())
        candidate = local_instant(datetime.combine(date, self.start_local.time()), zone)
        if candidate <= now:
            candidate = local_instant(datetime.combine(date + timedelta(days=1), self.start_local.time()), zone)
        return candidate

    def allows(self, now: datetime):
        return self.window_start is None or Window(self.timezone, self.window_start, self.window_end).contains(now)


class AutomationSourceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_ids: list[str] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)
    material_asset_ids: list[str] = Field(default_factory=list)


class AutomationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=2000)
    template_id: str = Field(min_length=1)
    template_version: int = Field(ge=1)
    trigger: AutomationTrigger
    source_scope: AutomationSourceScope

    @model_validator(mode="after")
    def not_blank(self):
        self.name = self.name.strip()
        self.goal = self.goal.strip()
        if not self.name or not self.goal:
            raise ValueError("名称和目标不能为空")
        return self


class AutomationBlocked(ValueError):
    def __init__(self, code: str, reason: str, repair: str):
        super().__init__(reason)
        self.code, self.reason, self.repair = code, reason, repair


class AutomationService:
    def __init__(self, database_path: str | Path, *, verify_identity, read_inputs, project_run, check_capabilities=lambda actor: None):
        self.database_path = Path(database_path)
        self.verify_identity = verify_identity
        self.read_inputs = read_inputs
        self.project_run = project_run
        self.check_capabilities = check_capabilities

    def connect(self):
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def definition(row):
        result = dict(row)
        for field in ("actor", "template_snapshot", "trigger", "source_scope"):
            result[field] = json.loads(result.pop(field + "_json"))
        result["paused"] = bool(result["paused"])
        result["confirmation_policy"] = CONFIRMATION_POLICY
        result.pop("actor")  # Trusted identity never needs to be exposed to the client.
        return result

    @staticmethod
    def run(row):
        result = dict(row)
        for field in ("definition", "input_snapshot", "confirmation", "execution"):
            raw = result.pop(field + "_json")
            result[field] = json.loads(raw) if raw else None
        return result

    def require_actor(self, actor):
        verdict = self.verify_identity(actor)
        if verdict != "ready":
            raise AutomationBlocked("identity_" + verdict, "当前成员或工作区许可无法核验", "恢复当前成员许可后重试")

    def list(self, actor):
        self.require_actor(actor)
        with self.connect() as conn:
            return [self.definition(row) for row in conn.execute("SELECT * FROM automations WHERE workspace_id=? AND member_id=? ORDER BY created_at DESC,id", (actor.workspace_id, actor.member_id))]

    def get(self, actor, automation_id):
        self.require_actor(actor)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM automations WHERE workspace_id=? AND member_id=? AND id=?", (actor.workspace_id, actor.member_id, automation_id)).fetchone()
        if row is None:
            raise KeyError(automation_id)
        return self.definition(row)

    def create(self, actor, payload: AutomationCreate, *, now=None):
        self.require_actor(actor)
        inputs = self.read_inputs(actor, payload.model_dump(mode="json"))
        now = now or utc_now()
        identity = actor.identity_payload()
        automation_id = "automation_" + uuid4().hex
        with self.connect() as conn:
            conn.execute("""INSERT INTO automations (id,workspace_id,member_id,actor_json,name,goal,template_id,template_version,template_snapshot_json,trigger_json,source_scope_json,status,next_due_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)""", (automation_id,actor.workspace_id,actor.member_id,dump(identity),payload.name,payload.goal,payload.template_id,payload.template_version,dump(inputs["template"]),dump(payload.trigger.model_dump(mode="json")),dump(payload.source_scope.model_dump()),payload.trigger.first_due().isoformat(),now.isoformat(),now.isoformat()))
        return self.get(actor, automation_id)

    def revise(self, actor, automation_id, payload: AutomationCreate, revision: int, *, now=None):
        self.get(actor, automation_id)
        inputs = self.read_inputs(actor, payload.model_dump(mode="json"))
        now = now or utc_now()
        with self.connect() as conn:
            updated = conn.execute("""UPDATE automations SET name=?,goal=?,template_id=?,template_version=?,template_snapshot_json=?,trigger_json=?,source_scope_json=?,revision=revision+1,next_due_at=?,updated_at=?
                WHERE workspace_id=? AND member_id=? AND id=? AND revision=?""", (payload.name,payload.goal,payload.template_id,payload.template_version,dump(inputs["template"]),dump(payload.trigger.model_dump(mode="json")),dump(payload.source_scope.model_dump()),payload.trigger.first_due().isoformat(),now.isoformat(),actor.workspace_id,actor.member_id,automation_id,revision))
            if updated.rowcount != 1:
                raise ValueError("定义已变化，请重新读取后编辑")
        return self.get(actor, automation_id)

    def pause(self, actor, automation_id, paused: bool, revision: int):
        self.get(actor, automation_id)
        with self.connect() as conn:
            updated = conn.execute("UPDATE automations SET paused=?,status=?,revision=revision+1,updated_at=? WHERE workspace_id=? AND member_id=? AND id=? AND revision=?", (int(paused),"paused" if paused else "active",utc_now().isoformat(),actor.workspace_id,actor.member_id,automation_id,revision))
            if updated.rowcount != 1:
                raise ValueError("定义已变化，请重新读取后操作")
        return self.get(actor, automation_id)

    def runs(self, actor, automation_id):
        self.get(actor, automation_id)
        with self.connect() as conn:
            return [self.run(row) for row in conn.execute("SELECT * FROM automation_runs WHERE workspace_id=? AND member_id=? AND automation_id=? ORDER BY due_at DESC,id", (actor.workspace_id,actor.member_id,automation_id))]

    def get_run(self, actor, run_id):
        self.require_actor(actor)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM automation_runs WHERE workspace_id=? AND member_id=? AND id=?", (actor.workspace_id,actor.member_id,run_id)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self.run(row)

    def bind_confirmed_plan(self, production, actor, run_id, run, fingerprint, plan, task, now):
        """Bind the prepared Plan/Task and this occurrence on one connection."""
        # Preserve this command's original default connection; other service
        # operations retain their existing connect() timeout.
        with sqlite3.connect(self.database_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            bound = conn.execute("SELECT * FROM automation_runs WHERE id=? AND workspace_id=? AND member_id=?",(run_id,actor.workspace_id,actor.member_id)).fetchone()
            if bound is None:
                raise KeyError(run_id)
            bound = self.run(bound)
            if bound["task_id"]:
                if bound["confirmation"].get("plan",{}).get("fingerprint") != fingerprint:
                    raise ValueError("本次运行已用不同输入确认")
            else:
                current = conn.execute("SELECT paused FROM automations WHERE id=?",(run["automation_id"],)).fetchone()
                if current[0] or not AutomationTrigger.model_validate(run["definition"]["trigger"]).allows(utc_now()):
                    raise ValueError("安排已暂停或窗口已关闭，未创建任务")
                self.require_actor(actor)
                production.plan_records.insert_confirmed_creation_plan(conn,plan,task)
                confirmation = {"plan":{"fingerprint":fingerprint,"confirmed_at":now.isoformat(),"member_id":actor.member_id}}
                conn.execute("UPDATE automation_runs SET plan_id=?,task_id=?,confirmation_json=?,state='waiting_review',stage='topic',reason_code='topic_confirmation_required',reason='方案已确认，选题等待逐次确认',repair_action='打开创作任务继续',revision=revision+1,updated_at=? WHERE id=?",(plan.id,task.id,dump(confirmation),now.isoformat(),run_id))

    def write_run_state(self, run_id, *, state, stage, reason_code=None, reason=None, repair_action=None, snapshot=None, job_id=None, confirmation=None, execution=None, expected_revision=None):
        with self.connect() as conn:
            updated = conn.execute("UPDATE automation_runs SET state=?,stage=?,reason_code=?,reason=?,repair_action=?,input_snapshot_json=COALESCE(?,input_snapshot_json),job_id=COALESCE(?,job_id),confirmation_json=COALESCE(?,confirmation_json),execution_json=COALESCE(?,execution_json),revision=revision+1,updated_at=? WHERE id=? AND (? IS NULL OR revision=?)", (state,stage,reason_code,reason,repair_action,dump(snapshot) if snapshot is not None else None,job_id,dump(confirmation) if confirmation is not None else None,dump(execution) if execution is not None else None,utc_now().isoformat(),run_id,expected_revision,expected_revision))
            if not updated.rowcount:
                return
            row = conn.execute("SELECT automation_id FROM automation_runs WHERE id=?", (run_id,)).fetchone()
            conn.execute("UPDATE automations SET status=?,updated_at=? WHERE id=? AND paused=0", ("active" if state in {"completed", "cancelled"} else state,utc_now().isoformat(),row[0]))

    def prepare(self, actor, run):
        inputs = None
        try:
            self.require_actor(actor)
            inputs = self.read_inputs(actor, run["definition"])
            if inputs["template"] != run["definition"]["template_snapshot"]:
                raise AutomationBlocked("template_changed", "固定模板版本内容已变化", "核对模板并修订后续安排；本次不会自动换模板")
            self.check_capabilities(actor)
            self.write_run_state(run["id"],state="waiting_review",stage="plan",reason_code="plan_confirmation_required",reason="本次方案等待确认；选题、文案、制作和发布仍需逐次确认",repair_action="核对本次来源并预检方案",snapshot=inputs,expected_revision=run["revision"])
        except AutomationBlocked as exc:
            self.write_run_state(run["id"],state="blocked",stage="inputs",reason_code=exc.code,reason=exc.reason,repair_action=exc.repair,snapshot=inputs,expected_revision=run["revision"])

    def require_open(self, run, *, now=None):
        with self.connect() as conn:
            paused = conn.execute("SELECT paused FROM automations WHERE id=?", (run["automation_id"],)).fetchone()[0]
        if paused:
            raise AutomationBlocked("automation_paused", "本安排已暂停，未启动下一单元", "重新开启安排后继续本次运行")
        if not AutomationTrigger.model_validate(run["definition"]["trigger"]).allows(now or utc_now()):
            raise AutomationBlocked("outside_window", "当前不在本次执行窗口，未启动下一单元", "在本次窗口内继续")

    def retry(self, actor, run_id):
        run = self.get_run(actor, run_id)
        self.require_open(run)
        if run["task_id"]:
            self.project(actor, run)
        else:
            if not AutomationTrigger.model_validate(run["definition"]["trigger"]).allows(utc_now()):
                raise ValueError("当前不在本次执行窗口，请在窗口内恢复")
            self.prepare(actor, run)
        return self.get_run(actor, run_id)

    def project(self, actor, run):
        projection = self.project_run(actor, run)
        if projection is not None:
            self.write_run_state(run["id"],**projection)

    def tick(self, *, now=None):
        now = now or utc_now()
        with self.connect() as conn:
            due_rows = conn.execute("SELECT * FROM automations WHERE paused=0 AND next_due_at IS NOT NULL AND next_due_at<=? ORDER BY next_due_at,id", (now.isoformat(),)).fetchall()
        for row in due_rows:
            definition = self.definition(row)
            trigger = AutomationTrigger.model_validate(definition["trigger"])
            due = datetime.fromisoformat(row["next_due_at"])
            due_key = "once:" + due.isoformat() if trigger.cadence == "once" else due.astimezone(ZoneInfo(trigger.timezone)).date().isoformat()
            run_id = "automation_run_" + hashlib.sha256(f"{row['id']}:{due_key}".encode()).hexdigest()[:32]
            actor = CreationExecutionActor.from_event(row["workspace_id"],json.loads(row["actor_json"]))
            schedule_error = None
            try:
                next_due = trigger.next_due(due,now)
            except ValueError as exc:
                next_due = None
                schedule_error = str(exc)
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT paused,next_due_at,revision FROM automations WHERE id=?", (row["id"],)).fetchone()
                if current["paused"] or current["next_due_at"] != row["next_due_at"] or current["revision"] != row["revision"]:
                    continue
                inserted = conn.execute("INSERT OR IGNORE INTO automation_runs (id,automation_id,workspace_id,member_id,automation_revision,due_key,due_at,definition_json,state,stage,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'running','inputs',?,?)", (run_id,row["id"],row["workspace_id"],row["member_id"],row["revision"],due_key,due.isoformat(),dump(definition),now.isoformat(),now.isoformat()))
                conn.execute("UPDATE automations SET next_due_at=?,updated_at=? WHERE id=?", (next_due.isoformat() if next_due else None,now.isoformat(),row["id"]))
            if not inserted.rowcount:
                continue
            run = self.get_run_for_driver(run_id)
            if schedule_error:
                self.write_run_state(run_id,state="blocked",stage="trigger",reason_code="schedule_timezone_ambiguity",reason=schedule_error,repair_action="重新选择没有夏令时歧义的每日时间")
            elif not trigger.allows(now):
                self.write_run_state(run_id,state="blocked",stage="inputs",reason_code="outside_window",reason="错过本次允许窗口，未启动下一执行单元",repair_action="等待下一窗口或明确修改安排")
            else:
                self.prepare(actor, run)
        # A crash after the unique claim must recover the same occurrence, never make another run.
        with self.connect() as conn:
            recoveries = conn.execute("SELECT r.*,a.actor_json FROM automation_runs r JOIN automations a ON a.id=r.automation_id WHERE a.paused=0 AND r.task_id IS NULL AND r.state='running' AND r.stage='inputs'").fetchall()
        for row in recoveries:
            run = self.run({key:row[key] for key in row.keys() if key != "actor_json"})
            actor = CreationExecutionActor.from_event(row["workspace_id"],json.loads(row["actor_json"]))
            if AutomationTrigger.model_validate(run["definition"]["trigger"]).allows(now):
                self.prepare(actor, run)
            else:
                self.write_run_state(run["id"],state="blocked",stage="inputs",reason_code="outside_window",reason="恢复时已超出本次窗口，未启动下一单元",repair_action="在窗口内重试本次运行")
        with self.connect() as conn:
            rows = conn.execute("SELECT r.*,a.actor_json FROM automation_runs r JOIN automations a ON a.id=r.automation_id WHERE r.task_id IS NOT NULL AND r.state NOT IN ('completed','cancelled')").fetchall()
        for row in rows:
            actor = CreationExecutionActor.from_event(row["workspace_id"],json.loads(row["actor_json"]))
            try:
                self.require_actor(actor)
                self.project(actor, self.run({key:row[key] for key in row.keys() if key != "actor_json"}))
            except AutomationBlocked as exc:
                self.write_run_state(row["id"],state="blocked",stage="permission",reason_code=exc.code,reason=exc.reason,repair_action=exc.repair)

    def get_run_for_driver(self, run_id):
        with self.connect() as conn:
            return self.run(conn.execute("SELECT * FROM automation_runs WHERE id=?", (run_id,)).fetchone())
