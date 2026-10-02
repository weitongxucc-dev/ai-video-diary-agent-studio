"""Workspace-scoped scheduled-task model and CRUD (execution wiring deferred).

This is the first slice: persist and manage automation tasks. Nothing here
enables or triggers execution; the startup gate is intentionally left to a
later package. Trigger semantics mirror the existing ``SchedulePolicy`` mode
concepts from ``app.device_scheduler``: ``idle`` (闲时) and ``scheduled``
(到点, via ``not_before_utc``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

TaskType = Literal["material_analysis", "creation_render"]
TriggerMode = Literal["idle", "scheduled"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ScheduledTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    trigger_mode: TriggerMode
    target_ref: str
    not_before_utc: datetime | None = None
    window: dict | None = None


class ScheduledTaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType | None = None
    trigger_mode: TriggerMode | None = None
    target_ref: str | None = None
    not_before_utc: datetime | None = None
    window: dict | None = None


class ScheduledTaskEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ScheduledTaskResponse(BaseModel):
    id: str
    workspace_id: str
    task_type: str
    trigger_mode: str
    target_ref: str
    not_before_utc: str | None
    window: dict | None
    enabled: bool
    created_at: str
    updated_at: str


class ScheduledTasksResponse(BaseModel):
    tasks: list[ScheduledTaskResponse]
    total_count: int


class ScheduledTaskService:
    """CRUD for ``scheduled_tasks``; no execution, no automatic admission."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _validate(
        trigger_mode: str, target_ref: str | None, not_before_utc: datetime | None,
    ) -> None:
        if target_ref is not None and not target_ref.strip():
            raise ValueError("目标对象不能为空")
        # Mirrors SchedulePolicy: 到点模式必须给出最早开始时间。
        if trigger_mode == "scheduled" and not_before_utc is None:
            raise ValueError("到点模式必须给出最早开始时间")

    @staticmethod
    def _to_response(row: sqlite3.Row) -> ScheduledTaskResponse:
        window = json.loads(row["window_json"]) if row["window_json"] else None
        return ScheduledTaskResponse(
            id=row["id"], workspace_id=row["workspace_id"], task_type=row["task_type"],
            trigger_mode=row["trigger_mode"], target_ref=row["target_ref"],
            not_before_utc=row["not_before_utc"], window=window,
            enabled=bool(row["enabled"]), created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_tasks(self, workspace_id: str) -> list[ScheduledTaskResponse]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE workspace_id=? ORDER BY created_at ASC, id ASC",
                (workspace_id,),
            ).fetchall()
        return [self._to_response(row) for row in rows]

    def create_task(self, workspace_id: str, payload: ScheduledTaskCreateRequest) -> ScheduledTaskResponse:
        self._validate(payload.trigger_mode, payload.target_ref, payload.not_before_utc)
        task_id = f"stask_{uuid4().hex}"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO scheduled_tasks
                (id, workspace_id, task_type, trigger_mode, target_ref, not_before_utc,
                 window_json, enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,1,?,?)""",
                (task_id, workspace_id, payload.task_type, payload.trigger_mode,
                 payload.target_ref.strip(), _iso(payload.not_before_utc),
                 json.dumps(payload.window) if payload.window else None, now, now),
            )
            conn.commit()
        return self.get_task(workspace_id, task_id)

    def get_task(self, workspace_id: str, task_id: str) -> ScheduledTaskResponse:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE workspace_id=? AND id=?",
                (workspace_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._to_response(row)

    def update_task(self, workspace_id: str, task_id: str,
                    payload: ScheduledTaskUpdateRequest) -> ScheduledTaskResponse:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE workspace_id=? AND id=?",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task_type = payload.task_type if payload.task_type is not None else row["task_type"]
            trigger_mode = payload.trigger_mode if payload.trigger_mode is not None else row["trigger_mode"]
            target_ref = payload.target_ref if payload.target_ref is not None else row["target_ref"]
            not_before_utc = payload.not_before_utc if payload.not_before_utc is not None else (
                datetime.fromisoformat(row["not_before_utc"]) if row["not_before_utc"] else None
            )
            self._validate(trigger_mode, target_ref, not_before_utc)
            conn.execute(
                """UPDATE scheduled_tasks SET task_type=?, trigger_mode=?, target_ref=?,
                not_before_utc=?, window_json=?, updated_at=? WHERE workspace_id=? AND id=?""",
                (task_type, trigger_mode, target_ref.strip(), _iso(not_before_utc),
                 json.dumps(payload.window) if payload.window is not None else row["window_json"],
                 _utc_now(), workspace_id, task_id),
            )
            conn.commit()
        return self.get_task(workspace_id, task_id)

    def set_enabled(self, workspace_id: str, task_id: str, enabled: bool) -> ScheduledTaskResponse:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM scheduled_tasks WHERE workspace_id=? AND id=?",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=?, updated_at=? WHERE workspace_id=? AND id=?",
                (1 if enabled else 0, _utc_now(), workspace_id, task_id),
            )
            conn.commit()
        return self.get_task(workspace_id, task_id)

    def delete_task(self, workspace_id: str, task_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM scheduled_tasks WHERE workspace_id=? AND id=?",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            conn.execute("DELETE FROM scheduled_tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id))
            conn.commit()
