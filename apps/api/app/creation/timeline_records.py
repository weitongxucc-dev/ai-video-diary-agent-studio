"""Timeline registration and current-pointer writes share their original connection."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
import sqlite3
from typing import Literal

from app.creation.contracts import TimelineVersion
from app.creation.task_input_reads import require_creation_task
from app.models import utc_now
from app.persistence_json import _json_object, _json_payload


def validate_timeline_version(version: TimelineVersion) -> None:
    if not all(
        value.strip() for value in (version.id, version.workspace_id, version.task_id)
    ):
        raise ValueError("时间线版本缺少 ID、工作区或任务")
    if version.sequence < 1:
        raise ValueError("时间线版本序号必须从 1 开始")
    if version.parent_version_id is not None and not version.parent_version_id.strip():
        raise ValueError("父时间线版本 ID 不能为空")
    if (
        version.created_by_operation_id is not None
        and not version.created_by_operation_id.strip()
    ):
        raise ValueError("创建时间线版本的操作 ID 不能为空")
    _json_object(version.snapshot)


class TimelineRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        decode_snapshot: Callable[[str], dict[str, object]],
    ):
        self._connect = connection_factory
        self._decode_snapshot = decode_snapshot


    def create_timeline_version(
        self, version: TimelineVersion, *, connection: sqlite3.Connection | None = None,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> TimelineVersion:
        validate_timeline_version(version)
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.create_timeline_version(version, connection=conn,
                    expected_current_version_id=expected_current_version_id)
            return self.get_timeline_version(version.workspace_id, version.id)
        conn = connection
        task = require_creation_task(conn, version.workspace_id, version.task_id)
        if (expected_current_version_id is not False
                and task.current_timeline_version_id != expected_current_version_id):
            raise ValueError("当前时间线已变化，不能发布旧版本成片")
        existing_row = conn.execute(
            "SELECT * FROM timeline_versions WHERE id = ?",
            (version.id,),
        ).fetchone()
        if existing_row is None:
            existing_row = conn.execute(
                """
                SELECT * FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND sequence = ?
                """,
                (version.workspace_id, version.task_id, version.sequence),
            ).fetchone()
        if existing_row is not None:
            persisted = self.timeline_version_from_row(existing_row)
            if persisted != version:
                raise ValueError("时间线版本已存在，不能覆盖")
            if task.current_timeline_version_id != version.id:
                task.current_timeline_version_id = version.id
                task.updated_at = utc_now()
                conn.execute(
                    """
                    UPDATE creation_tasks SET payload = ?, updated_at = ?
                    WHERE workspace_id = ? AND id = ?
                    """,
                    (
                        _json_payload(task),
                        task.updated_at.isoformat(),
                        task.workspace_id,
                        task.id,
                    ),
                )
            return persisted
        latest_row = conn.execute(
            """
            SELECT id, sequence FROM timeline_versions
            WHERE workspace_id = ? AND task_id = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (version.workspace_id, version.task_id),
        ).fetchone()
        if latest_row is None:
            if version.parent_version_id is not None or version.sequence != 1:
                raise ValueError("首个时间线版本必须从序号 1 开始且不能有父版本")
        else:
            parent = conn.execute(
                """
                SELECT sequence FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (
                    version.workspace_id,
                    version.task_id,
                    version.parent_version_id,
                ),
            ).fetchone()
            if parent is None:
                raise ValueError("父时间线版本不属于当前工作区与任务")
            if version.sequence != int(latest_row["sequence"]) + 1:
                raise ValueError("时间线版本序号必须连续递增")
        try:
            conn.execute(
                """
                INSERT INTO timeline_versions
                    (id, workspace_id, task_id, parent_version_id, sequence,
                     snapshot_json, created_by_operation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.id,
                    version.workspace_id,
                    version.task_id,
                    version.parent_version_id,
                    version.sequence,
                    _json_object(version.snapshot),
                    version.created_by_operation_id,
                    version.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("时间线版本已存在，不能覆盖") from exc
        task.current_timeline_version_id = version.id
        task.updated_at = utc_now()
        conn.execute(
            """
            UPDATE creation_tasks SET payload = ?, updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (
                _json_payload(task),
                task.updated_at.isoformat(),
                task.workspace_id,
                task.id,
            ),
        )
        return version

    def get_timeline_version(
        self, workspace_id: str, version_id: str, *, connection: sqlite3.Connection | None = None
    ) -> TimelineVersion:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM timeline_versions WHERE workspace_id = ? AND id = ?",
                (workspace_id, version_id),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self.timeline_version_from_row(row)

    def next_timeline_sequence(
        self, workspace_id: str, task_id: str, *, connection: sqlite3.Connection,
    ) -> int:
        require_creation_task(connection, workspace_id, task_id)
        row = connection.execute(
            "SELECT MAX(sequence) FROM timeline_versions WHERE workspace_id = ? AND task_id = ?",
            (workspace_id, task_id),
        ).fetchone()
        return (row[0] or 0) + 1

    def list_timeline_versions(
        self, workspace_id: str, task_id: str
    ) -> list[TimelineVersion]:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? ORDER BY sequence ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self.timeline_version_from_row(row) for row in rows]

    def timeline_contains_version(self, workspace_id, task_id, current_id, ancestor_id):
        with self._connect() as conn:
            return conn.execute("WITH RECURSIVE history(id,parent_version_id) AS ("
                "SELECT id,parent_version_id FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=? UNION "
                "SELECT v.id,v.parent_version_id FROM timeline_versions v JOIN history h ON h.parent_version_id=v.id "
                "WHERE v.workspace_id=? AND v.task_id=?) SELECT 1 FROM history WHERE id=? LIMIT 1",
                (workspace_id, task_id, current_id, workspace_id, task_id, ancestor_id)).fetchone() is not None

    def timeline_version_from_row(self, row: sqlite3.Row) -> TimelineVersion:
        return TimelineVersion(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            parent_version_id=(
                str(row["parent_version_id"])
                if row["parent_version_id"] is not None
                else None
            ),
            sequence=int(row["sequence"]),
            snapshot=self._decode_snapshot(str(row["snapshot_json"])),
            created_by_operation_id=(
                str(row["created_by_operation_id"])
                if row["created_by_operation_id"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
