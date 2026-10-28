"""Member-owned creation requests and bounded planning-window facts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from app.creation.contracts import CreationTask as _CreationTask
from app.creation.request_contracts import CreationRequestConflict
from app.models import utc_now
from app.persistence_json import _json_default, _json_object, _json_payload
from app.source_policy import content_owner

if TYPE_CHECKING:
    from app.production import CreationPlan


class CreationRequestRecordStore:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    @staticmethod
    def _requirements(output_relation, requirements, material_scope, clarification_questions):
        if output_relation is not None and (not isinstance(output_relation, str) or not output_relation.strip()):
            raise ValueError("创作请求的成片关系需要明确填写")
        if not isinstance(requirements, dict) or not isinstance(material_scope, dict):
            raise ValueError("创作要求和素材范围必须是结构化对象")
        if not isinstance(clarification_questions, list) or any(not isinstance(item, str) or not item.strip() for item in clarification_questions):
            raise ValueError("澄清问题必须包含可读的问题正文")
        return (_json_object(requirements), _json_object(material_scope), json.dumps(clarification_questions, ensure_ascii=False))


    @staticmethod
    def require_request(conn, workspace_id, request_id, user_id, local_diagnostic):
        row = conn.execute("SELECT * FROM creation_requests WHERE workspace_id=? AND id=?", (workspace_id, request_id)).fetchone()
        if row is None or (not local_diagnostic and (not user_id or row["owner_user_id"] != user_id)):
            raise KeyError(request_id)
        return row


    @staticmethod
    def check_revision(row, expected_revision):
        if type(expected_revision) is not int or row["revision"] != expected_revision:
            raise CreationRequestConflict("创作请求已变化，请重新查看当前要求和规划窗口")


    def _window(self, conn, workspace_id, window):
        if window is None:
            return None
        entries = []
        for item in conn.execute(
            """SELECT i.*, p.payload AS actual_plan, t.payload AS actual_task
               FROM creation_request_items i
               LEFT JOIN creation_plans p ON p.id=i.plan_id AND p.workspace_id=?
               LEFT JOIN creation_tasks t ON t.id=i.task_id AND t.workspace_id=?
               WHERE i.window_id=? ORDER BY i.ordinal LIMIT 8""",
            (workspace_id, workspace_id, window["id"]),
        ):
            entries.append({"ordinal": item["ordinal"], "plan_id": item["plan_id"], "task_id": item["task_id"],
                "plan": json.loads(item["actual_plan"] if window["status"] == "confirmed" else item["plan_payload"]),
                "task": json.loads(item["actual_task"] if window["status"] == "confirmed" else item["task_payload"])})
        return {"id": window["id"], "request_id": window["request_id"], "ordinal_start": window["ordinal_start"],
            "status": window["status"], "input_fingerprint": window["input_fingerprint"],
            "created_at": window["created_at"], "confirmed_at": window["confirmed_at"], "entries": entries}


    def header(self, conn, row):
        counts = conn.execute(
            """SELECT COUNT(*) AS expanded_count, COALESCE(SUM(w.status='confirmed'),0) AS confirmed_count
               FROM creation_request_items i JOIN creation_request_windows w ON w.id=i.window_id
               WHERE w.request_id=? AND w.status IN ('proposed','confirmed')""", (row["id"],),
        ).fetchone()
        statuses = conn.execute(
            """SELECT json_extract(t.payload,'$.status') AS status, COUNT(*) AS count
               FROM creation_request_items i JOIN creation_request_windows w ON w.id=i.window_id
               JOIN creation_tasks t ON t.id=i.task_id AND t.workspace_id=?
               WHERE w.request_id=? AND w.status='confirmed' GROUP BY json_extract(t.payload,'$.status')""",
            (row["workspace_id"], row["id"]),
        ).fetchall()
        window = (conn.execute("SELECT * FROM creation_request_windows WHERE request_id=? AND id=?", (row["id"], row["current_window_id"])).fetchone()
                  if row["current_window_id"] else None)
        return {"id": row["id"], "workspace_id": row["workspace_id"], "owner_user_id": row["owner_user_id"] or None,
            "request_key": row["request_key"], "goal": row["goal"], "target_count": row["target_count"],
            "output_relation": row["output_relation"], "requirements": json.loads(row["requirements_json"]),
            "material_scope": json.loads(row["material_scope_json"]), "clarification_questions": json.loads(row["clarification_questions_json"]),
            "revision": row["revision"], "status": row["status"], "expanded_count": counts["expanded_count"],
            "confirmed_count": counts["confirmed_count"], "remaining_count": row["target_count"] - counts["expanded_count"],
            "task_status_counts": {item["status"]: item["count"] for item in statuses},
            "current_window": self._window(conn, row["workspace_id"], window),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


    def create_creation_request(self, workspace_id: str, *, request_key: str, input_fingerprint: str,
                                goal: str, target_count: int, output_relation: str | None,
                                requirements: dict, material_scope: dict, clarification_questions: list[str],
                                verified_owner_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        owner = content_owner(verified_owner_user_id)
        if not owner and not local_diagnostic:
            raise PermissionError("创作请求需要当前可信成员身份")
        if not workspace_id.strip() or not request_key.strip() or not input_fingerprint.strip() or not goal.strip():
            raise ValueError("创作请求缺少工作区、稳定身份、输入摘要或目标")
        if type(target_count) is not int or not 1 <= target_count <= 9007199254740991:
            raise ValueError("总目标数量必须为正安全整数")
        requirements_json, scope_json, questions_json = self._requirements(output_relation, requirements, material_scope, clarification_questions)
        digest = hashlib.sha256(json.dumps({"goal": goal, "target_count": target_count, "output_relation": output_relation,
            "requirements": requirements, "material_scope": material_scope, "clarification_questions": clarification_questions},
            ensure_ascii=False, sort_keys=True, default=_json_default).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM creation_requests WHERE workspace_id=? AND owner_user_id=? AND request_key=?",
                (workspace_id, owner or "", request_key)).fetchone()
            if prior is not None:
                if prior["input_fingerprint"] != input_fingerprint or prior["creation_input_digest"] != digest:
                    raise CreationRequestConflict("同一创作请求身份已绑定不同的目标或要求")
                return self.header(conn, prior)
            request_id = f"request_{uuid4().hex}"
            now = utc_now().isoformat()
            status = "awaiting_clarification" if clarification_questions or (target_count > 1 and output_relation is None) else "awaiting_plan"
            conn.execute("""INSERT INTO creation_requests
                (id,workspace_id,owner_user_id,request_key,input_fingerprint,creation_input_digest,goal,target_count,
                 output_relation,requirements_json,material_scope_json,clarification_questions_json,revision,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                (request_id,workspace_id,owner or "",request_key,input_fingerprint,digest,goal,target_count,output_relation,
                 requirements_json,scope_json,questions_json,status,now,now))
            return self.header(conn, self.require_request(conn,workspace_id,request_id,owner,local_diagnostic))


    def get_creation_request(self, workspace_id: str, request_id: str, *, viewer_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN")
            return self.header(conn, self.require_request(conn, workspace_id, request_id, content_owner(viewer_user_id), local_diagnostic))


    def find_creation_request_by_key(self, workspace_id: str, request_key: str, *, viewer_user_id: str) -> dict | None:
        """Read the original member-owned intake after a lost response; never create."""
        owner = content_owner(viewer_user_id)
        if not owner:
            raise PermissionError("读取原制作请求需要当前成员身份")
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM creation_requests WHERE workspace_id=? AND owner_user_id=? AND request_key=?",
                               (workspace_id, owner, request_key)).fetchone()
            return self.header(conn, row) if row is not None else None


    def revise_creation_request(self, workspace_id: str, request_id: str, *, expected_revision: int,
                                output_relation: str | None, requirements: dict, material_scope: dict, clarification_questions: list[str],
                                verified_owner_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        values = self._requirements(output_relation, requirements, material_scope, clarification_questions)
        owner = content_owner(verified_owner_user_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.require_request(conn, workspace_id, request_id, owner, local_diagnostic)
            self.check_revision(row, expected_revision)
            if conn.execute("SELECT 1 FROM creation_request_windows WHERE request_id=? AND status='confirmed' LIMIT 1", (request_id,)).fetchone():
                raise CreationRequestConflict("已有确认窗口，不能改写其共同要求；请建立新的创作请求")
            conn.execute("UPDATE creation_request_windows SET status='superseded' WHERE request_id=? AND status='proposed'", (request_id,))
            status = "awaiting_clarification" if clarification_questions or (row["target_count"] > 1 and output_relation is None) else "awaiting_plan"
            conn.execute("""UPDATE creation_requests SET output_relation=?,requirements_json=?,material_scope_json=?,clarification_questions_json=?,
                revision=revision+1,status=?,current_window_id=NULL,updated_at=? WHERE id=?""",
                (output_relation,*values,status,utc_now().isoformat(),request_id))
            return self.header(conn, self.require_request(conn,workspace_id,request_id,owner,local_diagnostic))


    def save_creation_request_window(self, workspace_id: str, request_id: str, *, expected_revision: int,
                                    input_fingerprint: str, entries: list[tuple[CreationPlan, _CreationTask]],
                                    verified_owner_user_id: str | None = None, local_diagnostic: bool = False,
                                    replace_window_id: str | None = None) -> dict:
        if not input_fingerprint.strip() or not 1 <= len(entries) <= 8:
            raise ValueError("规划窗口需要输入摘要且只能包含1至8项")
        owner = content_owner(verified_owner_user_id)
        for plan, task in entries:
            if (plan.workspace_id != workspace_id or task.workspace_id != workspace_id or task.plan_id != plan.id
                    or type(plan.target_count) is not int or plan.target_count != 1):
                raise ValueError("每项必须关联当前工作区的独立单片方案和任务")
        if len({plan.id for plan,_ in entries}) != len(entries) or len({task.id for _,task in entries}) != len(entries):
            raise ValueError("规划窗口不能重复使用方案或任务身份")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.require_request(conn,workspace_id,request_id,owner,local_diagnostic)
            current = conn.execute("SELECT * FROM creation_request_windows WHERE id=?", (row["current_window_id"],)).fetchone()
            if replace_window_id is not None:
                self.check_revision(row,expected_revision)
                if current is None or current["id"] != replace_window_id or current["status"] != "proposed":
                    raise CreationRequestConflict("待重新核对的窗口已变化或已确认，请重新查看当前窗口")
                if current["input_fingerprint"] == input_fingerprint:
                    return self.header(conn,row)
            else:
                if current is not None:
                    if current["input_fingerprint"] == input_fingerprint:
                        return self.header(conn,row)
                    if current["status"] == "proposed":
                        raise CreationRequestConflict("当前规划窗口尚未确认，请先审阅或修订要求")
                self.check_revision(row,expected_revision)
            if row["status"] == "awaiting_clarification":
                raise CreationRequestConflict("当前创作请求仍有重要决定待澄清，不能生成生产窗口")
            ordinal_start = conn.execute("""SELECT COALESCE(MAX(i.ordinal),0)+1 FROM creation_request_items i
                JOIN creation_request_windows w ON w.id=i.window_id WHERE w.request_id=? AND w.status='confirmed'""", (request_id,)).fetchone()[0]
            if ordinal_start + len(entries) - 1 > row["target_count"]:
                raise CreationRequestConflict("规划窗口超过该请求的总目标数量")
            for plan,task in entries:
                if (conn.execute("SELECT 1 FROM creation_plans WHERE id=?", (plan.id,)).fetchone()
                        or conn.execute("SELECT 1 FROM creation_tasks WHERE id=?", (task.id,)).fetchone()):
                    raise CreationRequestConflict("窗口方案或任务身份已用于现有对象，不能重新关联")
            window_id = f"window_{uuid4().hex}"
            now = utc_now().isoformat()
            if replace_window_id is not None:
                conn.execute("UPDATE creation_request_windows SET status='superseded' WHERE id=?", (replace_window_id,))
            conn.execute("INSERT INTO creation_request_windows(id,request_id,ordinal_start,input_fingerprint,request_revision,status,created_at) VALUES(?,?,?,?,?,'proposed',?)",
                (window_id,request_id,ordinal_start,input_fingerprint,row["revision"],now))
            for offset,(plan,task) in enumerate(entries):
                frozen_plan = replace(plan,owner_user_id=owner)
                frozen_task = replace(task,owner_user_id=owner)
                conn.execute("INSERT INTO creation_request_items(window_id,ordinal,plan_id,task_id,plan_payload,task_payload) VALUES(?,?,?,?,?,?)",
                    (window_id,ordinal_start+offset,plan.id,task.id,_json_payload(frozen_plan),_json_payload(frozen_task)))
            conn.execute("UPDATE creation_requests SET current_window_id=?,status='awaiting_confirmation',revision=revision+1,updated_at=? WHERE id=?", (window_id,now,request_id))
            return self.header(conn,self.require_request(conn,workspace_id,request_id,owner,local_diagnostic))


    def list_creation_request_items(self, workspace_id: str, request_id: str, *, after_ordinal: int = 0, limit: int = 20,
                                   viewer_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        if type(after_ordinal) is not int or after_ordinal < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("请求条目分页需要非负序号和1至100项页面")
        with self._connect() as conn:
            conn.execute("BEGIN")
            self.require_request(conn,workspace_id,request_id,content_owner(viewer_user_id),local_diagnostic)
            rows = conn.execute("""SELECT i.ordinal,i.window_id,i.plan_id,i.task_id,p.payload AS plan,t.payload AS task
                FROM creation_request_items i JOIN creation_request_windows w ON w.id=i.window_id
                LEFT JOIN creation_plans p ON p.id=i.plan_id AND p.workspace_id=? AND w.status='confirmed'
                LEFT JOIN creation_tasks t ON t.id=i.task_id AND t.workspace_id=? AND w.status='confirmed'
                WHERE w.request_id=? AND w.status IN ('proposed','confirmed') AND i.ordinal>?
                ORDER BY i.ordinal LIMIT ?""",(workspace_id,workspace_id,request_id,after_ordinal,limit+1)).fetchall()
            items = [{"ordinal": item["ordinal"], "window_id": item["window_id"], "plan_id": item["plan_id"], "task_id": item["task_id"],
                "plan": json.loads(item["plan"]) if item["plan"] is not None else None,
                "task": json.loads(item["task"]) if item["task"] is not None else None} for item in rows[:limit]]
            return {"items": items, "has_more": len(rows)>limit, "next_after_ordinal": items[-1]["ordinal"] if len(rows)>limit else None}


    @staticmethod
    def read_window(connection, request_id, window_id):
        return connection.execute("SELECT * FROM creation_request_windows WHERE request_id=? AND id=?", (request_id,window_id)).fetchone()

    @staticmethod
    def read_window_items(connection, window_id):
        return connection.execute("SELECT * FROM creation_request_items WHERE window_id=? ORDER BY ordinal", (window_id,)).fetchall()

    @staticmethod
    def mark_confirmed(connection, request_id, window_id, now):
        connection.execute("UPDATE creation_request_windows SET status='confirmed',confirmed_at=? WHERE id=?", (now,window_id))
        connection.execute("UPDATE creation_requests SET status='active',revision=revision+1,updated_at=? WHERE id=?", (now,request_id))
