"""Edit intent, proposal, and operation records with caller-owned adoption facts."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
import json
import sqlite3
from typing import Literal

from app.creation.contracts import (
    EditIntent, EditProposal, EditOperation, TimelineVersion, SUPPORTED_EDIT_OPERATION_TYPES,
    validate_logical_reference,
)
from app.creation.task_input_reads import require_creation_task
from app.models import utc_now
from app.persistence_json import _json_object, _json_default


def _json_list_from_text(value: str) -> list[object]:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("持久化 JSON 列表无效")
    return decoded

def validate_edit_intent(intent: EditIntent) -> None:
    if not all(
        value.strip()
        for value in (
            intent.id,
            intent.workspace_id,
            intent.task_id,
            intent.raw_command,
            intent.context_stage,
            intent.intent_type,
            intent.status,
        )
    ):
        raise ValueError("编辑意图缺少必要标识、命令、上下文或状态")
    for reference in intent.selected_object_refs:
        validate_logical_reference(reference, "编辑意图选中对象引用")
    if intent.playhead_ms is not None and intent.playhead_ms < 0:
        raise ValueError("播放位置不能为负数")
    if not 0.0 <= intent.confidence <= 1.0:
        raise ValueError("编辑意图置信度必须在 0 到 1 之间")
    _json_object(intent.scope)
    _json_object(intent.constraints)

def validate_new_edit_proposal(proposal: EditProposal) -> None:
    if not all(
        value.strip()
        for value in (
            proposal.id,
            proposal.workspace_id,
            proposal.task_id,
            proposal.intent_id,
            proposal.summary,
        )
    ):
        raise ValueError("编辑提案缺少必要标识或摘要")
    if proposal.status != "proposed":
        raise ValueError("新编辑提案必须从 proposed 状态开始")
    if not proposal.target_refs:
        raise ValueError("编辑提案必须包含目标引用")
    for reference in (*proposal.target_refs, *proposal.invalidated_refs):
        validate_logical_reference(reference, "编辑提案对象引用")
    if not proposal.operation:
        raise ValueError("编辑提案必须包含类型化操作")
    _json_object(proposal.operation)
    _json_object(proposal.preview)
    json.dumps(proposal.external_effects, ensure_ascii=False, default=_json_default)
    if proposal.updated_at < proposal.created_at:
        raise ValueError("编辑提案更新时间不能早于创建时间")

def validate_edit_operation(operation: EditOperation) -> None:
    if not all(
        value.strip()
        for value in (
            operation.id,
            operation.workspace_id,
            operation.task_id,
            operation.proposal_id,
            operation.operation_type,
            operation.base_version_id,
        )
    ):
        raise ValueError("编辑操作缺少必要标识、提案或基础版本")
    if operation.operation_type not in SUPPORTED_EDIT_OPERATION_TYPES:
        raise ValueError("编辑操作类型不在当前冻结集合中")
    if not operation.payload:
        raise ValueError("编辑操作必须包含类型化 payload")
    _json_object(operation.payload)
    if operation.status == "running":
        if (
            operation.result_version_id is not None
            or operation.failure_reason is not None
            or operation.applied_at is not None
        ):
            raise ValueError("运行中编辑操作不能已有终态字段")
    elif operation.status == "applied":
        if not (operation.result_version_id or "").strip() or operation.applied_at is None:
            raise ValueError("已应用编辑操作必须包含结果版本和应用时间")
        if operation.failure_reason is not None:
            raise ValueError("已应用编辑操作不能包含失败原因")
        if operation.applied_at < operation.created_at:
            raise ValueError("编辑操作应用时间不能早于创建时间")
    elif operation.status == "failed":
        if not (operation.failure_reason or "").strip():
            raise ValueError("失败编辑操作必须包含原因")
        if operation.result_version_id is not None or operation.applied_at is not None:
            raise ValueError("失败编辑操作不能包含成功结果")
    else:
        raise ValueError("编辑操作状态无效")


class EditRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        current_source: Callable[[], dict | None],
        decode_reference_list: Callable[[str], list[str]],
        decode_object: Callable[[str], dict[str, object]],
        decode_timeline_version: Callable[[sqlite3.Row], TimelineVersion],
    ):
        self._connect = connection_factory
        self._current_source = current_source
        self._decode_reference_list = decode_reference_list
        self._decode_object = decode_object
        self._decode_timeline_version = decode_timeline_version


    def create_edit_intent(self, intent: EditIntent, *, connection: sqlite3.Connection | None = None, source: dict | None | Literal[False] = False) -> EditIntent:
        source = self._current_source() if source is False else source
        if source is not None:
            if (source["workspace_id"], source["task_id"]) != (intent.workspace_id, intent.task_id):
                raise ValueError("类型化编辑来源不属于当前任务")
            intent = replace(intent, raw_command=source.get("raw_command") or intent.raw_command,
                             constraints={**intent.constraints, "request_source": source})
        validate_edit_intent(intent)
        if connection is None:
            with self._connect() as conn:
                self.create_edit_intent(intent, connection=conn, source=None)
            return self.get_edit_intent(intent.workspace_id, intent.id)
        conn = connection
        require_creation_task(conn, intent.workspace_id, intent.task_id)
        existing_row = conn.execute(
            "SELECT * FROM edit_intents WHERE id = ?",
            (intent.id,),
        ).fetchone()
        if existing_row is not None:
            persisted = self.edit_intent_from_row(existing_row)
            if persisted != intent:
                raise ValueError("编辑意图 ID 已存在，不能覆盖")
            return persisted
        conn.execute(
            """
            INSERT INTO edit_intents
                (id, workspace_id, task_id, raw_command, context_stage,
                 selected_object_refs_json, playhead_ms, intent_type,
                 scope_json, constraints_json, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.id,
                intent.workspace_id,
                intent.task_id,
                intent.raw_command,
                intent.context_stage,
                json.dumps(intent.selected_object_refs, ensure_ascii=False),
                intent.playhead_ms,
                intent.intent_type,
                _json_object(intent.scope),
                _json_object(intent.constraints),
                intent.confidence,
                intent.status,
                intent.created_at.isoformat(),
            ),
        )
        return intent

    def get_edit_intent(self, workspace_id: str, intent_id: str) -> EditIntent:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM edit_intents WHERE workspace_id = ? AND id = ?",
                (workspace_id, intent_id),
            ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return self.edit_intent_from_row(row)

    def list_edit_intents(
        self, workspace_id: str, task_id: str
    ) -> list[EditIntent]:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM edit_intents
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self.edit_intent_from_row(row) for row in rows]

    def create_edit_proposal(self, proposal: EditProposal, *, connection: sqlite3.Connection | None = None) -> EditProposal:
        validate_new_edit_proposal(proposal)
        if connection is None:
            with self._connect() as conn:
                self.create_edit_proposal(proposal, connection=conn)
            return self.get_edit_proposal(proposal.workspace_id, proposal.id)
        conn = connection
        require_creation_task(conn, proposal.workspace_id, proposal.task_id)
        intent_row = conn.execute(
            """
            SELECT 1 FROM edit_intents
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (proposal.workspace_id, proposal.task_id, proposal.intent_id),
        ).fetchone()
        if intent_row is None:
            raise ValueError("编辑提案关联的意图不属于当前工作区与任务")
        existing_row = conn.execute(
            "SELECT * FROM edit_proposals WHERE id = ?",
            (proposal.id,),
        ).fetchone()
        if existing_row is not None:
            persisted = self.edit_proposal_from_row(existing_row)
            if persisted != proposal:
                raise ValueError("编辑提案 ID 已存在，且领域字段不一致")
            return persisted
        conn.execute(
            """
            INSERT INTO edit_proposals
                (id, workspace_id, task_id, intent_id, summary,
                 target_refs_json, operation_json, invalidated_refs_json,
                 preview_json, external_effects_json, status, created_at,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.id,
                proposal.workspace_id,
                proposal.task_id,
                proposal.intent_id,
                proposal.summary,
                json.dumps(proposal.target_refs, ensure_ascii=False),
                _json_object(proposal.operation),
                json.dumps(proposal.invalidated_refs, ensure_ascii=False),
                _json_object(proposal.preview),
                json.dumps(
                    proposal.external_effects,
                    ensure_ascii=False,
                    default=_json_default,
                ),
                proposal.status,
                proposal.created_at.isoformat(),
                proposal.updated_at.isoformat(),
            ),
        )
        return proposal

    def get_edit_proposal(
        self, workspace_id: str, proposal_id: str, *, connection=None
    ) -> EditProposal:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM edit_proposals WHERE workspace_id = ? AND id = ?",
                (workspace_id, proposal_id),
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return self.edit_proposal_from_row(row)

    def list_edit_proposals(
        self, workspace_id: str, task_id: str, *,
        base_version_id: str | None | Literal[False] = False, limit: int | None = None,
    ) -> list[EditProposal]:
        if limit is not None and not 1 <= limit <= 100:
            raise ValueError("编辑提案读取数量需为1至100")
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            current_clause = " AND status='proposed' AND json_extract(operation_json,'$.payload.base_version_id') IS ?" if base_version_id is not False else ""
            order = "DESC" if limit is not None else "ASC"
            rows = conn.execute(
                f"""
                SELECT * FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ?{current_clause} ORDER BY created_at {order}, id {order}{' LIMIT ?' if limit is not None else ''}
                """,
                (workspace_id, task_id, *([base_version_id] if base_version_id is not False else []), *([limit] if limit is not None else [])),
            ).fetchall()
        return [self.edit_proposal_from_row(row) for row in rows]

    def transition_edit_proposal(
        self,
        workspace_id: str,
        task_id: str,
        proposal_id: str,
        *,
        status: Literal["confirmed", "rejected", "expired"],
    ) -> EditProposal:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, proposal_id),
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            current = self.edit_proposal_from_row(row)
            if current.status != "proposed":
                raise ValueError("只有 proposed 编辑提案可以确认、拒绝或过期")
            now = utc_now()
            changed = conn.execute(
                """
                UPDATE edit_proposals SET status = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'proposed'
                """,
                (
                    status,
                    now.isoformat(),
                    workspace_id,
                    task_id,
                    proposal_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("编辑提案已被其他操作转换")
        return self.get_edit_proposal(workspace_id, proposal_id)

    def create_edit_operation(self, operation: EditOperation) -> EditOperation:
        validate_edit_operation(operation)
        with self._connect() as conn:
            require_creation_task(
                conn, operation.workspace_id, operation.task_id
            )
            proposal_row = conn.execute(
                """
                SELECT status FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (
                    operation.workspace_id,
                    operation.task_id,
                    operation.proposal_id,
                ),
            ).fetchone()
            if proposal_row is None or str(proposal_row["status"]) != "confirmed":
                raise ValueError("编辑操作必须关联同源且已确认的提案")
            self._require_operation_timeline_lineage(conn, operation)
            existing_row = conn.execute(
                "SELECT * FROM edit_operations WHERE id = ?",
                (operation.id,),
            ).fetchone()
            if existing_row is None:
                existing_row = conn.execute(
                    """
                    SELECT * FROM edit_operations
                    WHERE workspace_id = ? AND task_id = ? AND proposal_id = ?
                    """,
                    (
                        operation.workspace_id,
                        operation.task_id,
                        operation.proposal_id,
                    ),
                ).fetchone()
            if existing_row is not None:
                persisted = self.edit_operation_from_row(existing_row)
                if persisted != operation:
                    raise ValueError("编辑操作 ID 或提案已存在，不能覆盖")
                return persisted
            conn.execute(
                """
                INSERT INTO edit_operations
                    (id, workspace_id, task_id, proposal_id, operation_type,
                     payload_json, base_version_id, result_version_id, status,
                     failure_reason, created_at, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.id,
                    operation.workspace_id,
                    operation.task_id,
                    operation.proposal_id,
                    operation.operation_type,
                    _json_object(operation.payload),
                    operation.base_version_id,
                    operation.result_version_id,
                    operation.status,
                    operation.failure_reason,
                    operation.created_at.isoformat(),
                    operation.applied_at.isoformat() if operation.applied_at else None,
                ),
            )
        return self.get_edit_operation(operation.workspace_id, operation.id)

    def finish_edit_operation(
        self,
        workspace_id: str,
        task_id: str,
        operation_id: str,
        *,
        status: Literal["applied", "failed"],
        result_version_id: str | None = None,
        failure_reason: str | None = None,
        applied_at: datetime | None = None,
    ) -> EditOperation:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM edit_operations
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, operation_id),
            ).fetchone()
            if row is None:
                raise KeyError(operation_id)
            current = self.edit_operation_from_row(row)
            if current.status != "running":
                raise ValueError("只有 running 编辑操作可以进入终态")
            terminal = replace(
                current,
                status=status,
                result_version_id=result_version_id,
                failure_reason=failure_reason,
                applied_at=applied_at,
            )
            validate_edit_operation(terminal)
            self._require_operation_timeline_lineage(conn, terminal)
            changed = conn.execute(
                """
                UPDATE edit_operations
                SET result_version_id = ?, status = ?, failure_reason = ?, applied_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'running'
                """,
                (
                    terminal.result_version_id,
                    terminal.status,
                    terminal.failure_reason,
                    terminal.applied_at.isoformat() if terminal.applied_at else None,
                    workspace_id,
                    task_id,
                    operation_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("编辑操作已被其他操作结束")
        return self.get_edit_operation(workspace_id, operation_id)

    def get_edit_operation(
        self, workspace_id: str, operation_id: str
    ) -> EditOperation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM edit_operations WHERE workspace_id = ? AND id = ?",
                (workspace_id, operation_id),
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return self.edit_operation_from_row(row)

    def list_edit_operations(
        self, workspace_id: str, task_id: str
    ) -> list[EditOperation]:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM edit_operations
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self.edit_operation_from_row(row) for row in rows]

    def find_proposal_job_result(self, workspace_id: str, task_id: str, proposal_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT j.* FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id AND e.job_id=j.id "
                "WHERE j.workspace_id=? AND j.task_id=? AND json_extract(e.payload_json,'$.edit_proposal_id')=? "
                "ORDER BY j.created_at LIMIT 1", (workspace_id, task_id, proposal_id),
            ).fetchone()
            if row is None:
                return None
            request_row = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_id=? ORDER BY created_at LIMIT 1",
                                       (workspace_id, row["id"])).fetchone()
        return {"status": row["status"], "job_id": row["id"],
            "request_id": request_row["id"] if request_row else None, "failure_reason": row["failure_reason"]}

    def _require_operation_timeline_lineage(
        self,
        conn: sqlite3.Connection, operation: EditOperation
    ) -> None:
        base_row = conn.execute(
            """
            SELECT * FROM timeline_versions
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (
                operation.workspace_id,
                operation.task_id,
                operation.base_version_id,
            ),
        ).fetchone()
        if base_row is None:
            raise ValueError("编辑操作基础版本不属于当前工作区与任务")
        if operation.status != "applied":
            return
        result_row = conn.execute(
            """
            SELECT * FROM timeline_versions
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (
                operation.workspace_id,
                operation.task_id,
                operation.result_version_id,
            ),
        ).fetchone()
        if result_row is None:
            raise ValueError("已应用编辑操作的结果版本不属于当前任务")
        result = self._decode_timeline_version(result_row)
        if (
            result.parent_version_id != operation.base_version_id
            or result.created_by_operation_id != operation.id
        ):
            raise ValueError("编辑操作与时间线版本谱系不一致")

    def edit_intent_from_row(self, row: sqlite3.Row) -> EditIntent:
        return EditIntent(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            raw_command=str(row["raw_command"]),
            context_stage=str(row["context_stage"]),
            selected_object_refs=self._decode_reference_list(
                str(row["selected_object_refs_json"])
            ),
            playhead_ms=(
                int(row["playhead_ms"])
                if row["playhead_ms"] is not None
                else None
            ),
            intent_type=str(row["intent_type"]),
            scope=self._decode_object(str(row["scope_json"])),
            constraints=self._decode_object(str(row["constraints_json"])),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def edit_proposal_from_row(self, row: sqlite3.Row) -> EditProposal:
        return EditProposal(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            intent_id=str(row["intent_id"]),
            summary=str(row["summary"]),
            target_refs=self._decode_reference_list(str(row["target_refs_json"])),
            operation=self._decode_object(str(row["operation_json"])),
            invalidated_refs=self._decode_reference_list(
                str(row["invalidated_refs_json"])
            ),
            preview=self._decode_object(str(row["preview_json"])),
            external_effects=_json_list_from_text(
                str(row["external_effects_json"])
            ),
            status=str(row["status"]),  # type: ignore[arg-type]
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def edit_operation_from_row(self, row: sqlite3.Row) -> EditOperation:
        return EditOperation(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            proposal_id=str(row["proposal_id"]),
            operation_type=str(row["operation_type"]),
            payload=self._decode_object(str(row["payload_json"])),
            base_version_id=str(row["base_version_id"]),
            result_version_id=(
                str(row["result_version_id"])
                if row["result_version_id"] is not None
                else None
            ),
            status=str(row["status"]),  # type: ignore[arg-type]
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            applied_at=(
                datetime.fromisoformat(str(row["applied_at"]))
                if row["applied_at"] is not None
                else None
            ),
        )
