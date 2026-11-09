from __future__ import annotations

import json
import sqlite3
from typing import Literal

from app.creation.contracts import CreationTask as _CreationTask, TimelineVersion as _TimelineVersion
from app.models import utc_now
from app.persistence_json import _json_payload


class MontagePlanAdoptionTransaction:
    def __init__(self, connection_factory, *, decode_task, read_task, read_montage, require_task, create_version):
        self._connect = connection_factory
        self._decode_task = decode_task
        self._read_task = read_task
        self._read_montage = read_montage
        self._require_task = require_task
        self._create_version = create_version

    def save_creation_task_montage_plan(
        self,
        workspace_id: str,
        task_id: str,
        plan_id: str,
        payload: dict[str, object],
        *, connection: sqlite3.Connection | None = None,
        verify_inputs=None,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> tuple[_CreationTask, dict[str, object]]:
        status = str(payload.get("status", ""))
        if status not in {"waiting_for_user", "ready", "rendering", "rendered", "failed"}:
            raise ValueError("混剪计划状态无效")
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.save_creation_task_montage_plan(workspace_id, task_id, plan_id, payload,
                    connection=conn, expected_current_version_id=expected_current_version_id, verify_inputs=verify_inputs)
            return self._read_task(workspace_id, task_id), self._read_montage(workspace_id, task_id)
        conn = connection
        task_row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        ).fetchone()
        if task_row is None:
            raise KeyError(task_id)
        task = self._decode_task(json.loads(str(task_row["payload"])))
        if verify_inputs is not None:
            verify_inputs(conn, task)
        if (expected_current_version_id is not False
                and task.current_timeline_version_id != expected_current_version_id):
            raise ValueError("当前时间线已变化，不能覆盖新确认的剪辑计划")
        if task.stage not in {"montage", "review"}:
            raise ValueError("当前创作任务不在剪辑或成片检查阶段")
        if str(payload.get("task_id", "")) != task.id:
            raise ValueError("混剪计划与当前创作任务不一致")
        now = utc_now()
        payload = {**payload, "updated_at": now.isoformat()}
        conn.execute(
            """
            INSERT INTO creation_task_montage_plans
                (id, workspace_id, task_id, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, task_id) DO UPDATE SET
                id = excluded.id, payload = excluded.payload, updated_at = excluded.updated_at
            """,
            (plan_id, workspace_id, task_id, json.dumps(payload, ensure_ascii=False), now.isoformat()),
        )
        task.montage_plan_id = plan_id
        task.output_ready = status == "rendered"
        if status != "failed":
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
        if status == "rendering":
            task.stage = "montage"
            task.stage_status = "running"
            task.status = "running"
        elif status == "rendered":
            task.stage = "review"
            task.stage_status = "waiting_for_user"
            task.status = "waiting_for_user"
            task.progress = max(task.progress, 90)
            task.recovery_point = "review"
            task.output_ready = True
        elif status == "failed":
            task.stage = "montage"
            task.stage_status = "failed"
            task.status = "failed"
            task.recovery_point = "montage"
            task.stage_reason = "render_failed"
            task.stage_impact = "未生成可检查的成片。"
            task.stage_repair_action = str(payload.get("message") or "检查 FFmpeg 日志后重试。")
            task.stage_retryable = True
        else:
            task.stage = "montage"
            task.stage_status = "waiting_for_user"
            task.status = "waiting_for_user"
            task.progress = 70 if status == "waiting_for_user" else 75
        task.updated_at = now
        conn.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
            (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
        )
        return task, payload

    def publish_creation_task_render(
        self, workspace_id: str, task_id: str, plan_id: str, payload: dict[str, object],
        version: _TimelineVersion, *,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> None:
        with self._connect() as publish_conn:
            publish_conn.execute("BEGIN IMMEDIATE")
            self.save_creation_task_montage_plan(
                workspace_id, task_id, plan_id, payload, connection=publish_conn,
                expected_current_version_id=expected_current_version_id,
            )
            self._create_version(
                version, connection=publish_conn,
                expected_current_version_id=expected_current_version_id,
            )

    def save_failed_creation_task_render_if_current(
        self, workspace_id: str, task_id: str, plan_id: str, payload: dict[str, object], *,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> None:
        with self._connect() as failure_conn:
            failure_conn.execute("BEGIN IMMEDIATE")
            current_task = self._require_task(failure_conn, workspace_id, task_id)
            # A failed old render must not replace a crop confirmed while it ran.
            if expected_current_version_id is False or current_task.current_timeline_version_id == expected_current_version_id:
                self.save_creation_task_montage_plan(
                    workspace_id, task_id, plan_id, payload, connection=failure_conn,
                )
