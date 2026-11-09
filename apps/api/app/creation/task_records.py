"""Task record reads, visibility changes, and caller-owned adoption writes."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
import sqlite3
from typing import Literal

from app.creation.contracts import CreationTask
from app.creation.task_input_reads import creation_task_from_payload
from app.materials.contracts import ContentVisibility
from app.models import utc_now
from app.persistence_json import _json_payload
from app.source_policy import knowledge_item_readable as _content_readable


class TaskRecordStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]):
        self._connect = connection_factory

    def get_creation_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        include_private: bool = True,
        connection: sqlite3.Connection | None = None,
    ) -> CreationTask:
        if connection is None:
            with self._connect() as conn:
                return self.get_creation_task(workspace_id, task_id,
                    include_private=include_private, connection=conn)
        visibility_clause = "" if include_private else " AND visibility = 'shared'"
        row = connection.execute(
            f"""
            SELECT payload, visibility FROM creation_tasks
            WHERE workspace_id = ? AND id = ?{visibility_clause}
            """,
            (workspace_id, task_id),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self.task_from_row(row)

    def list_creation_tasks(
        self,
        workspace_id: str,
        *,
        include_private: bool = True,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> list[CreationTask]:
        """列出创作任务。给出可信读取身份时，私有任务只对上传者本人可见。

        `include_private` 是旧的粗粒度开关（本地单机用）；一旦传入可信身份，
        判据就换成与内容域同一套规则：私有按 owner 过滤，不因为「没带令牌」
        就把别人的私有任务也算进来。
        """
        if local_diagnostic or (viewer_user_id is None and include_private):
            return self._list_creation_tasks_by_visibility(workspace_id, include_private)
        return [
            task for task in self._list_creation_tasks_by_visibility(workspace_id, True)
            if _content_readable(
                user_id=viewer_user_id,
                local_diagnostic=False,
                visibility=task.visibility,
                owner_user_id=task.owner_user_id,
            )
        ]

    def _list_creation_tasks_by_visibility(
        self, workspace_id: str, include_private: bool
    ) -> list[CreationTask]:
        with self._connect() as conn:
            visibility_clause = "" if include_private else " AND visibility = 'shared'"
            rows = conn.execute(
                f"""
                SELECT payload, visibility FROM creation_tasks
                WHERE workspace_id = ?{visibility_clause} ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self.task_from_row(row) for row in rows]

    def set_creation_task_visibility(
        self, workspace_id: str, task_id: str, visibility: ContentVisibility
    ) -> CreationTask:
        """明确共享或收回一条创作任务的可见性。"""
        if visibility not in ("private", "shared"):
            raise ValueError("可见性只能是 private 或 shared。")
        task = self.get_creation_task(workspace_id, task_id)
        task = replace(task, visibility=visibility, updated_at=utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE creation_tasks SET payload=?, visibility=?, updated_at=? WHERE workspace_id=? AND id=?",
                (_json_payload(task), visibility, task.updated_at.isoformat(), workspace_id, task_id),
            )
        return task

    def write_adopted_task(self, connection, task):
        if not connection.in_transaction:
            raise ValueError("选题任务状态保存必须属于原事务")
        connection.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
            (_json_payload(task), task.updated_at.isoformat(), task.workspace_id, task.id),
        )

    @staticmethod
    def task_from_row(row: sqlite3.Row) -> CreationTask:
        """任务回读以列为准：存量行的 visibility 只写在列上。"""
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return creation_task_from_payload(data)


    def write_knowledge_adopted_task(self, connection, workspace_id, task):
        connection.execute(
            "UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(task), task.updated_at.isoformat(), workspace_id, task.id),
        )

    def _script_task_transition(self, conn, task, transition, now, *, script_id=None,
                                reason=None, impact=None, repair_action=None, retryable=None):
        if not conn.in_transaction:
            raise ValueError("文案引起的任务状态转换必须属于原事务")
        if transition == "started":
            task.stage = "script"
            task.stage_status = task.status = "running"
            task.stage_reason = task.stage_impact = task.stage_repair_action = task.stage_retryable = None
        elif transition == "saved":
            task.stage_status = task.status = "waiting_for_user"
            task.progress = max(task.progress,35)
        elif transition == "transferred":
            if task.stage_status == "not_started":
                task.stage_status = task.status = "waiting_for_user"
                task.progress = max(task.progress,35)
        elif transition == "failed":
            task.stage_status = task.status = "failed"
            task.recovery_point = "script"
            task.stage_reason,task.stage_impact = reason,impact
            task.stage_repair_action,task.stage_retryable = repair_action,retryable
        elif transition == "adopted":
            task.confirmed_script_id=script_id
            task.stage,task.stage_status,task.status="narration","not_started","running"
            task.progress=max(task.progress,40)
            task.recovery_point="narration"
            task.stage_reason=task.stage_impact=task.stage_repair_action=task.stage_retryable=None
        else:
            raise ValueError("未知文案任务转换")
        task.updated_at=now
        conn.execute("UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(task),now.isoformat(),task.workspace_id,task.id))

    def fail_creation_task_stage(
        self,
        workspace_id: str,
        task_id: str,
        *,
        stage: Literal["narration", "montage"],
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        connection: sqlite3.Connection | None = None,
    ) -> CreationTask:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("阶段失败必须包含原因、影响和修复动作")
        if connection is None:
            with self._connect() as conn:
                self.fail_creation_task_stage(
                    workspace_id, task_id, stage=stage, reason=reason, impact=impact,
                    repair_action=repair_action, retryable=retryable, connection=conn,
                )
            return self.get_creation_task(workspace_id, task_id)
        row = connection.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        task = creation_task_from_payload(json.loads(str(row["payload"])))
        if task.stage != stage:
            raise ValueError("任务当前阶段与失败阶段不一致")
        task.stage_status = "failed"
        task.status = "failed"
        task.recovery_point = stage
        task.stage_reason = reason
        task.stage_impact = impact
        task.stage_repair_action = repair_action
        task.stage_retryable = retryable
        task.updated_at = utc_now()
        connection.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
            (_json_payload(task), task.updated_at.isoformat(), workspace_id, task_id),
        )
        return task

    def _start_creation_task_stage(
        self,
        workspace_id: str,
        task_id: str,
        stage: Literal["narration", "montage"],
    ) -> CreationTask:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = creation_task_from_payload(json.loads(str(row["payload"])))
            if task.stage != stage or task.stage_status not in {"not_started", "failed"}:
                raise ValueError("当前创作任务不能开始该阶段")
            task.stage_status = "running"
            task.status = "running"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = utc_now()
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), task.updated_at.isoformat(), workspace_id, task_id),
            )
        return self.get_creation_task(workspace_id, task_id)
