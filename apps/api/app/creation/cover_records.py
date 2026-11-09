"""Cover asset records bound to the caller's timeline-version facts."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import math
import json
import sqlite3

from app.creation.contracts import CoverAsset, validate_logical_reference
from app.creation.task_input_reads import require_creation_task
from app.persistence_json import _json_payload, _with_datetime


def validate_cover_asset(cover: CoverAsset) -> None:
    if not all(
        value.strip()
        for value in (
            cover.id,
            cover.workspace_id,
            cover.task_id,
            cover.timeline_version_id,
            cover.source_video_ref,
            cover.title,
            cover.generation_method,
            cover.model_or_engine,
        )
    ):
        raise ValueError("封面缺少任务、时间线、源画面、标题或执行器")
    if not math.isfinite(cover.frame_time_seconds) or cover.frame_time_seconds < 0:
        raise ValueError("封面选帧时间无效")
    validate_logical_reference(cover.source_video_ref, "封面源视频引用")
    if cover.generation_method not in {"video_frame", "image_model"}:
        raise ValueError("封面生成方式无效")
    if cover.updated_at < cover.created_at:
        raise ValueError("封面更新时间不能早于创建时间")
    if cover.status == "valid":
        if not cover.artifact_ref or not cover.transform_id:
            raise ValueError("valid 封面必须有产物和变换证据")
        validate_logical_reference(cover.artifact_ref, "封面产物引用")
        if cover.failure_reason or cover.impact or cover.repair_action:
            raise ValueError("valid 封面不能包含失败终态")
    elif cover.status in {"failed", "unavailable"}:
        if not all(
            (value or "").strip()
            for value in (cover.failure_reason, cover.impact, cover.repair_action)
        ):
            raise ValueError("失败或不可用封面必须记录原因、影响和修复动作")
        if cover.artifact_ref is not None or cover.transform_id is not None:
            raise ValueError("失败或不可用封面不能登记成功证据")
    elif cover.status != "superseded":
        raise ValueError("封面状态无效")


class CoverAssetRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        decode_payload: Callable[[str], dict[str, object]],
        timeline_belongs: Callable[[sqlite3.Connection, str, str, str], bool],
    ):
        self._connect = connection_factory
        self._decode_payload = decode_payload
        self._timeline_belongs = timeline_belongs


    def create_cover_asset(
        self, cover: CoverAsset, *, connection: sqlite3.Connection | None = None,
    ) -> CoverAsset:
        validate_cover_asset(cover)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            require_creation_task(conn, cover.workspace_id, cover.task_id)
            if not self._timeline_belongs(conn, cover.workspace_id, cover.task_id, cover.timeline_version_id):
                raise ValueError("封面必须关联当前任务的时间线版本")
            try:
                conn.execute(
                    """
                    INSERT INTO cover_assets
                        (id, workspace_id, task_id, timeline_version_id,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cover.id,
                        cover.workspace_id,
                        cover.task_id,
                        cover.timeline_version_id,
                        _json_payload(cover),
                        cover.created_at.isoformat(),
                        cover.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("同一时间线版本的封面已存在") from exc
        return self.get_cover_asset(cover.workspace_id, cover.task_id, cover.id, connection=connection)

    def get_cover_asset(
        self, workspace_id: str, task_id: str, cover_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> CoverAsset:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM cover_assets
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, cover_id),
            ).fetchone()
        if row is None:
            raise KeyError(cover_id)
        return self.cover_asset_from_row(row)

    def list_cover_assets(
        self,
        workspace_id: str,
        task_id: str,
        *,
        timeline_version_id: str | None = None,
    ) -> list[CoverAsset]:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            if timeline_version_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM cover_assets
                    WHERE workspace_id = ? AND task_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM cover_assets
                    WHERE workspace_id = ? AND task_id = ? AND timeline_version_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id, timeline_version_id),
                ).fetchall()
        return [self.cover_asset_from_row(row) for row in rows]

    def cover_asset_from_row(self, row: sqlite3.Row) -> CoverAsset:
        payload = self._decode_payload(str(row["payload_json"]))
        return CoverAsset(**_with_datetime(payload, "created_at", "updated_at"))

    def read_restore_facts(self, workspace_id, task_id, cover_id, *, connection):
        record_fields = {'id','workspace_id','task_id','timeline_version_id','created_at','updated_at'}
        row = connection.execute('SELECT payload_json FROM cover_assets WHERE workspace_id=? AND task_id=? AND id=?',
            (workspace_id,task_id,cover_id)).fetchone()
        if row is None:
            raise ValueError('历史封面记录缺失，不能证明整片恢复安全')
        return {key:value for key,value in json.loads(row[0]).items() if key not in record_fields}
