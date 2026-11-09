"""Task authorization records and fixed goal/source facts."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import sqlite3

from app.creation.contracts import TaskEvent as _TaskEvent
from app.creation.execution_records import ExecutionRecordStore
from app.creation.task_input_reads import require_creation_task


class TaskAuthorizationRecordStore:
    def __init__(self, *, connection_factory: Callable[[], sqlite3.Connection],
                 execution_records: ExecutionRecordStore) -> None:
        self._connect = connection_factory
        self.execution_records = execution_records

    def get_task_authorization(self, workspace_id: str, task_id: str, *, connection=None, metadata_only=False):
        from app.creation.contracts import TaskAuthorization as _TaskAuthorization
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            scope = "'{}' AS resource_scope_json" if metadata_only else "resource_scope_json"
            row = conn.execute("SELECT workspace_id,task_id,id,revision,identity_json,goal_revision,allowed_operations_json,"+scope+
                ",issued_at,verified_at,expires_at,revoked_at,last_checked_at,last_verdict FROM task_authorizations WHERE workspace_id=? AND task_id=?",
                               (workspace_id, task_id)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["identity"] = json.loads(payload.pop("identity_json"))
        payload["resource_scope"] = json.loads(payload.pop("resource_scope_json"))
        payload["allowed_operations"] = tuple(json.loads(payload.pop("allowed_operations_json")))
        for key in ("issued_at", "verified_at", "expires_at", "revoked_at", "last_checked_at"):
            if payload.get(key) is not None:
                payload[key] = datetime.fromisoformat(payload[key])
        return _TaskAuthorization(**payload)

    def save_task_authorization(self, authorization, *, expected_revision: int | None, connection=None):
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.save_task_authorization(authorization, expected_revision=expected_revision, connection=conn)
        require_creation_task(connection, authorization.workspace_id, authorization.task_id)
        current = self.get_task_authorization(authorization.workspace_id, authorization.task_id, connection=connection)
        if (current.revision if current is not None else None) != expected_revision:
            from app.creation.contracts import TaskAuthorizationConflict as _TaskAuthorizationConflict
            raise _TaskAuthorizationConflict("任务授权在核验期间已变化")
        if current is not None and current.revoked_at is not None and authorization.revoked_at is None:
            raise PermissionError("已撤销的任务授权不能由迟到核验恢复")
        payload = asdict(authorization)
        for key in ("identity", "resource_scope", "allowed_operations"):
            payload[key+"_json"] = json.dumps(payload.pop(key), ensure_ascii=False)
        for key in ("issued_at", "verified_at", "expires_at", "revoked_at", "last_checked_at"):
            if payload[key] is not None:
                payload[key] = payload[key].isoformat()
        columns = tuple(payload)
        connection.execute("INSERT INTO task_authorizations("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+") "
            "ON CONFLICT(workspace_id,task_id) DO UPDATE SET "+",".join(key+"=excluded."+key for key in columns if key not in {"workspace_id","task_id"}),
            tuple(payload.values()))
        self.execution_records.append_task_event(_TaskEvent(id=f"event_{authorization.id}_{authorization.revision}",
            workspace_id=authorization.workspace_id, task_id=authorization.task_id, job_id=None,
            event_type="task_authorization", payload={"authorization_id":authorization.id,
                "revision":authorization.revision, "goal_revision":authorization.goal_revision,
                **({"identity":authorization.identity, "allowed_operations":authorization.allowed_operations,
                     "resource_scope":authorization.resource_scope} if current is None or current.issued_at is None else {}),
                "verdict":authorization.last_verdict,
                "expires_at":authorization.expires_at.isoformat() if authorization.expires_at else None,
                "revoked":authorization.revoked_at is not None}), connection=connection)
        return authorization

    def list_task_authorizations(self, *, due_before: datetime, limit: int = 16):
        with self._connect() as conn:
            rows = conn.execute("SELECT a.workspace_id,a.task_id FROM task_authorizations a "
                "WHERE a.revoked_at IS NULL AND (a.last_checked_at IS NULL OR a.last_checked_at<=?) "
                "AND EXISTS(SELECT 1 FROM job_runs j WHERE j.workspace_id=a.workspace_id "
                "AND j.task_id=a.task_id AND j.status IN ('queued','running','interrupted')) "
                "ORDER BY coalesce(a.last_checked_at,'') LIMIT ?",
                (due_before.isoformat(), limit)).fetchall()
            return [self.get_task_authorization(row["workspace_id"], row["task_id"], connection=conn) for row in rows]

    def task_authorization_material_scope(self, workspace_id, task_id):
        with self._connect() as conn:
            row = conn.execute("SELECT r.material_scope_json FROM creation_request_items i "
                "JOIN creation_request_windows w ON w.id=i.window_id JOIN creation_requests r ON r.id=w.request_id "
                "WHERE r.workspace_id=? AND i.task_id=?", (workspace_id,task_id)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def task_source_revisions(self, workspace_id, knowledge_ids, source_ids, *, connection=None):
        """Finite source facts for this goal, re-read on the owner's write connection."""
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            knowledge=[list(row) for row in conn.execute("SELECT id,version,content_hash,use_state,visibility,owner_user_id "
                "FROM knowledge_items WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                (workspace_id,json.dumps(knowledge_ids)))] if knowledge_ids else []
            sources=[list(row) for row in conn.execute("SELECT id,use_state FROM source_items "
                "WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                (workspace_id,json.dumps(source_ids)))] if source_ids else []
        if len(knowledge)!=len(set(knowledge_ids)) or len(sources)!=len(set(source_ids)):
            raise PermissionError("原目标的固定资料已不可用")
        return {"knowledge":knowledge,"sources":sources}

    def task_authorization_goal_revision(self, workspace_id, task_id, *, connection=None):
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute("SELECT json_extract(payload,'$.plan_id') FROM creation_tasks WHERE workspace_id=? AND id=?", (workspace_id,task_id)).fetchone()
            if row is None:
                raise KeyError(task_id)
            keys = ("id","goal","target_count","language","aspect_ratio","duration_target_seconds")
            plan_row = conn.execute("SELECT "+",".join("json_extract(payload,'$."+key+"')" for key in keys)+
                ",json_extract(payload,'$.source_media')"
                " FROM creation_plans WHERE workspace_id=? AND id=?", (workspace_id,row[0])).fetchone()
            # Window creation/confirmation also advances the request's optimistic
            # revision. Bind goal contents so another bounded window cannot revoke
            # this already approved task by merely advancing that cursor.
            origin = conn.execute("SELECT r.id,r.goal,r.target_count,r.output_relation,r.requirements_json,r.material_scope_json FROM creation_request_items i "
                "JOIN creation_request_windows w ON w.id=i.window_id JOIN creation_requests r ON r.id=w.request_id "
                "WHERE r.workspace_id=? AND i.task_id=?", (workspace_id,task_id)).fetchone()
        identity = dict(zip(keys, plan_row[:-1], strict=True))
        if plan_row[-1] is not None:
            identity['source_media'] = json.loads(plan_row[-1])
        identity["request"] = list(origin) if origin is not None else None
        return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
