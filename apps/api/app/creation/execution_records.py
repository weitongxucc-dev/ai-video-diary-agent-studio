"""Execution artifacts and task-event evidence; transaction decisions stay with callers."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
import json
import re
import sqlite3

from app.creation.contracts import (
    ArtifactRecord as _ArtifactRecord,
    CreationTask,
    TaskEvent as _TaskEvent,
    validate_logical_reference as _validate_logical_reference,
)
from app.persistence_json import _json_object


def _validate_artifact_record(artifact: _ArtifactRecord) -> None:
    if not all(
        value.strip()
        for value in (
            artifact.id,
            artifact.workspace_id,
            artifact.task_id,
            artifact.kind,
            artifact.logical_ref,
            artifact.sha256,
            artifact.producer_job_id,
        )
    ):
        raise ValueError("产物记录缺少必要标识、逻辑引用或摘要")
    _validate_logical_reference(artifact.logical_ref, "产物逻辑引用")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256):
        raise ValueError("产物摘要必须是小写 SHA-256")
    if artifact.size_bytes < 0:
        raise ValueError("产物大小不能为负数")
    for reference in artifact.input_refs:
        _validate_logical_reference(reference, "产物输入引用")


def _validate_task_event(event: _TaskEvent) -> None:
    if not all(
        value.strip()
        for value in (
            event.id,
            event.workspace_id,
            event.task_id,
            event.event_type,
        )
    ):
        raise ValueError("任务事件缺少必要标识或类型")
    if event.job_id is not None and not event.job_id.strip():
        raise ValueError("任务事件的运行记录 ID 不能为空")
    _json_object(event.payload)


class ExecutionRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        require_task: Callable[[sqlite3.Connection, str, str], CreationTask],
        job_belongs: Callable[[sqlite3.Connection, str, str, str], bool],
        decode_reference_list: Callable[[str], list[str]],
        decode_payload_object: Callable[[str], dict[str, object]],
    ):
        self._connect = connection_factory
        self._require_creation_task = require_task
        self._job_belongs = job_belongs
        self._decode_reference_list = decode_reference_list
        self._decode_payload_object = decode_payload_object

    def create_artifact_record(self, artifact: _ArtifactRecord, *, connection: sqlite3.Connection | None = None) -> _ArtifactRecord:
        _validate_artifact_record(artifact)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, artifact.workspace_id, artifact.task_id)
            if not self._job_belongs(conn, artifact.workspace_id, artifact.task_id, artifact.producer_job_id):
                raise ValueError('产物生产任务不属于当前工作区与创作任务')
            existing_row = conn.execute(
                "SELECT * FROM artifact_records WHERE id = ?",
                (artifact.id,),
            ).fetchone()
            if existing_row is None:
                existing_row = conn.execute(
                    """
                    SELECT * FROM artifact_records
                    WHERE workspace_id = ? AND logical_ref = ?
                    """,
                    (artifact.workspace_id, artifact.logical_ref),
                ).fetchone()
            if existing_row is not None:
                persisted = self.artifact_from_row(existing_row)
                if persisted != artifact:
                    raise ValueError("产物记录或逻辑引用已存在，且领域字段不一致")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO artifact_records
                        (id, workspace_id, task_id, kind, logical_ref, sha256,
                         size_bytes, producer_job_id, input_refs_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.id,
                        artifact.workspace_id,
                        artifact.task_id,
                        artifact.kind,
                        artifact.logical_ref,
                        artifact.sha256,
                        artifact.size_bytes,
                        artifact.producer_job_id,
                        json.dumps(artifact.input_refs, ensure_ascii=False),
                        artifact.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("产物记录或逻辑引用已存在") from exc
            row = conn.execute("SELECT * FROM artifact_records WHERE workspace_id=? AND id=?", (artifact.workspace_id, artifact.id)).fetchone()
            return self.artifact_from_row(row)

    def get_artifact_record(
        self, workspace_id: str, artifact_id: str, *, connection=None
    ) -> _ArtifactRecord:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_records WHERE workspace_id = ? AND id = ?",
                (workspace_id, artifact_id),
            ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self.artifact_from_row(row)

    def list_artifact_records(
        self, workspace_id: str, task_id: str
    ) -> list[_ArtifactRecord]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM artifact_records
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self.artifact_from_row(row) for row in rows]

    def append_task_event(self, event: _TaskEvent, *, connection: sqlite3.Connection | None = None) -> _TaskEvent:
        _validate_task_event(event)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, event.workspace_id, event.task_id)
            if event.job_id is not None:
                if not self._job_belongs(conn, event.workspace_id, event.task_id, event.job_id):
                    raise ValueError('任务事件关联的运行记录不属于当前任务')
            existing_row = conn.execute(
                "SELECT * FROM task_events WHERE id = ?",
                (event.id,),
            ).fetchone()
            if existing_row is not None:
                persisted = self.task_event_from_row(existing_row)
                if persisted != event:
                    raise ValueError("任务事件 ID 已存在，且领域字段不一致")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO task_events
                        (id, workspace_id, task_id, job_id, event_type,
                         payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.workspace_id,
                        event.task_id,
                        event.job_id,
                        event.event_type,
                        _json_object(event.payload),
                        event.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raced = conn.execute(
                    "SELECT * FROM task_events WHERE id = ?", (event.id,)
                ).fetchone()
                if raced is not None:
                    persisted = self.task_event_from_row(raced)
                    if persisted != event:
                        raise ValueError("任务事件 ID 已存在，且领域字段不一致") from exc
                    return persisted
                raise ValueError("任务事件 ID 已存在") from exc
        return event if connection is not None else self.get_task_event(event.workspace_id, event.id)

    def get_task_event(self, workspace_id: str, event_id: str, *,
                       connection: sqlite3.Connection | None = None) -> _TaskEvent:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM task_events WHERE workspace_id = ? AND id = ?",
                (workspace_id, event_id),
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self.task_event_from_row(row)

    def latest_task_resource_admission(self, workspace_id: str, task_id: str, job_id: str,
                                       request_id: str, *,
                                       connection: sqlite3.Connection | None = None) -> _TaskEvent | None:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT * FROM task_events WHERE workspace_id=? AND task_id=? AND job_id=? "
                "AND event_type='task_media_resource_admission' "
                "AND json_extract(payload_json, '$.device_request_id')=? "
                "ORDER BY json_extract(payload_json, '$.attempt_number') DESC, "
                "COALESCE(json_extract(payload_json, '$.last_grant_generation'), -1) DESC, "
                "created_at DESC, rowid DESC LIMIT 1",
                (workspace_id, task_id, job_id, request_id),
            ).fetchone()
        return self.task_event_from_row(row) if row is not None else None

    def list_task_events(self, workspace_id: str, task_id: str) -> list[_TaskEvent]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM task_events
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self.task_event_from_row(row) for row in rows]

    def artifact_from_row(self, row: sqlite3.Row) -> _ArtifactRecord:
        return _ArtifactRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            logical_ref=str(row["logical_ref"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            producer_job_id=str(row["producer_job_id"]),
            input_refs=self._decode_reference_list(str(row["input_refs_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def task_event_from_row(self, row: sqlite3.Row) -> _TaskEvent:
        return _TaskEvent(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            job_id=str(row["job_id"]) if row["job_id"] is not None else None,
            event_type=str(row["event_type"]),
            payload=self._decode_payload_object(str(row["payload_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def registered_job_output_refs(connection, workspace_id, task_id, job_id):
        return {
            str(artifact_row["logical_ref"])
            for artifact_row in connection.execute(
                """
                SELECT logical_ref FROM artifact_records
                WHERE workspace_id = ? AND task_id = ? AND producer_job_id = ?
                """,
                (workspace_id, task_id, job_id),
            ).fetchall()
        }

    @staticmethod
    def output_producers(connection, workspace_id, task_id, references):
        placeholders = ", ".join("?" for _ in references)
        rows = connection.execute(
            f"SELECT logical_ref, producer_job_id FROM artifact_records WHERE workspace_id = ? AND task_id = ? AND logical_ref IN ({placeholders})",
            (workspace_id, task_id, *references),
        ).fetchall()
        return {str(row["logical_ref"]): str(row["producer_job_id"]) for row in rows}


    @staticmethod
    def knowledge_adoption_request(connection, event_id):
        return connection.execute(
            "SELECT task_id,payload_json FROM task_events WHERE id=?", (event_id,),
        ).fetchone()

    @staticmethod
    def knowledge_adoption_receipt(connection, workspace_id, event_id):
        return connection.execute(
            "SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND id=? AND event_type='task_knowledge_adopted'",
            (workspace_id, event_id),
        ).fetchone()

    @staticmethod
    def task_knowledge_was_adopted(connection, workspace_id, task_id):
        return connection.execute(
            "SELECT 1 FROM task_events WHERE workspace_id=? AND task_id=? AND event_type='task_knowledge_adopted' "
            "AND json_extract(payload_json,'$.receipt.status')='adopted' LIMIT 1",
            (workspace_id, task_id),
        ).fetchone() is not None
