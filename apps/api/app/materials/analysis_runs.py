"""Durable records for one material analysis attempt.

The material payload keeps the latest usable result for existing consumers.  This
store keeps the history needed to answer which source revision was analyzed, which
stages ran, and whether a later source change made that result stale.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import sqlite3
from typing import Literal
from uuid import uuid4

from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime


AnalysisRunStatus = Literal["running", "succeeded", "partial", "failed", "expired"]
AnalysisStageStatus = Literal[
    "pending", "running", "succeeded", "blocked", "failed", "skipped"
]


@dataclass
class MaterialAnalysisRun:
    id: str
    workspace_id: str
    asset_id: str
    source_revision: dict[str, str]
    source_digest: str | None = None
    status: AnalysisRunStatus = "running"
    stages: dict[str, dict[str, object]] = field(default_factory=dict)
    model_ids: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, object] = field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)


class MaterialAnalysisRunStore:
    """SQLite owner for analysis-attempt history, scoped by workspace."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def start(
        self,
        workspace_id: str,
        asset_id: str,
        *,
        source_revision: dict[str, str],
        source_digest: str | None = None,
        stages: dict[str, dict[str, object]] | None = None,
        model_ids: dict[str, str] | None = None,
        parameters: dict[str, object] | None = None,
    ) -> MaterialAnalysisRun:
        run = MaterialAnalysisRun(
            id=f"analysis_{uuid4().hex}",
            workspace_id=workspace_id,
            asset_id=asset_id,
            source_revision=dict(source_revision),
            source_digest=source_digest,
            stages=dict(stages or {}),
            model_ids=dict(model_ids or {}),
            parameters=dict(parameters or {}),
        )
        self._write(run)
        return run

    def update_stage(
        self,
        workspace_id: str,
        run_id: str,
        stage: str,
        status: AnalysisStageStatus,
        *,
        message: str = "",
        model_id: str = "",
        completed_count: int = 0,
        total_count: int = 0,
    ) -> MaterialAnalysisRun:
        run = self.get(workspace_id, run_id)
        if run.status != "running":
            raise ValueError("分析运行已结束，不能更新阶段")
        if completed_count < 0 or total_count < 0 or completed_count > total_count:
            raise ValueError("分析阶段覆盖数量无效")
        stage_payload: dict[str, object] = {
            "status": status,
            "message": message,
            "model_id": model_id,
            "completed_count": completed_count,
            "total_count": total_count,
        }
        run.stages[stage] = stage_payload
        if model_id:
            run.model_ids[stage] = model_id
        run.updated_at = utc_now()
        self._write(run)
        return run

    def update_source_digest(
        self, workspace_id: str, run_id: str, source_digest: str
    ) -> MaterialAnalysisRun:
        run = self.get(workspace_id, run_id)
        if run.status != "running":
            raise ValueError("分析运行已结束，不能更新输入摘要")
        if not source_digest.strip():
            raise ValueError("原文件摘要不能为空")
        run.source_digest = source_digest
        run.updated_at = utc_now()
        self._write(run)
        return run

    def finish(
        self,
        workspace_id: str,
        run_id: str,
        *,
        status: Literal["succeeded", "partial", "failed", "expired"],
        error_message: str | None = None,
    ) -> MaterialAnalysisRun:
        run = self.get(workspace_id, run_id)
        if run.status != "running":
            return run
        now = utc_now()
        run.status = status
        run.error_message = error_message
        run.finished_at = now
        run.updated_at = now
        self._write(run)
        return run

    def expire_obsolete(
        self,
        workspace_id: str,
        asset_id: str,
        source_revision: dict[str, str],
    ) -> int:
        """Mark prior runs stale when the same logical asset gets a new file revision."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM material_analysis_runs
                WHERE workspace_id=? AND asset_id=?
                  AND status IN ('running', 'succeeded', 'partial')
                """,
                (workspace_id, asset_id),
            ).fetchall()
            changed = 0
            for row in rows:
                run = self._from_payload(json.loads(str(row["payload"])))
                if run.source_revision == source_revision:
                    continue
                now = utc_now()
                run.status = "expired"
                run.error_message = "原文件修订已变化，本次分析结果已过期。"
                run.finished_at = run.finished_at or now
                run.updated_at = now
                self._write(conn, run)
                changed += 1
        return changed

    def get(self, workspace_id: str, run_id: str) -> MaterialAnalysisRun:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM material_analysis_runs
                WHERE workspace_id=? AND id=?
                """,
                (workspace_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._from_payload(json.loads(str(row["payload"])))

    def list_for_asset(
        self, workspace_id: str, asset_id: str, *, limit: int = 20
    ) -> list[MaterialAnalysisRun]:
        if limit < 1 or limit > 100:
            raise ValueError("分析运行查询数量必须在 1 到 100 之间")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM material_analysis_runs
                WHERE workspace_id=? AND asset_id=?
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (workspace_id, asset_id, limit),
            ).fetchall()
        return [self._from_payload(json.loads(str(row["payload"]))) for row in rows]

    def _write(
        self,
        run_or_connection: MaterialAnalysisRun | sqlite3.Connection,
        run: MaterialAnalysisRun | None = None,
    ) -> None:
        if isinstance(run_or_connection, sqlite3.Connection):
            conn = run_or_connection
            item = run
            if item is None:
                raise ValueError("分析运行不能为空")
            self._write_row(conn, item)
            return
        item = run_or_connection
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._write_row(conn, item)

    @staticmethod
    def _write_row(conn: sqlite3.Connection, run: MaterialAnalysisRun) -> None:
        conn.execute(
            """
            INSERT INTO material_analysis_runs
                (id, workspace_id, asset_id, source_revision, source_digest,
                 status, payload, started_at, finished_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_revision=excluded.source_revision,
                source_digest=excluded.source_digest,
                status=excluded.status,
                payload=excluded.payload,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                updated_at=excluded.updated_at
            """,
            (
                run.id,
                run.workspace_id,
                run.asset_id,
                json.dumps(run.source_revision, ensure_ascii=False, sort_keys=True),
                run.source_digest,
                run.status,
                _json_payload(run),
                run.started_at.isoformat(),
                run.finished_at.isoformat() if run.finished_at else None,
                run.updated_at.isoformat(),
            ),
        )

    @staticmethod
    def _from_payload(data: dict[str, object]) -> MaterialAnalysisRun:
        return MaterialAnalysisRun(
            **_with_datetime(data, "started_at", "finished_at", "updated_at")
        )
