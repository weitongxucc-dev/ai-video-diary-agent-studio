"""Asset aggregate storage with caller-owned transaction reads and atomic analysis saves."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import math
from itertools import pairwise
from os import stat_result
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.materials.collections import collection_ids_for_workspace
from app.materials.contracts import (
    AssetReviewStatus,
    AssetUsageRole,
    ContentVisibility,
    MaterialAsset,
    MaterialAssetNote,
    MaterialMediaType,
    MaterialSegment,
    MaterialVisualFeature,
    effective_segment_usage_role,
    material_media_type_for,
)
from app.media_observations import (
    MaterialSpeechAnalysis,
    OCR_RETRIEVAL_MIN_CONFIDENCE,
    SegmentUnderstanding,
    SubtitleSegment as SpeechObservation,
)
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime
from app.source_types import ContentUseState


class MaterialVersionConflict(ValueError):
    """A frozen material revision changed before the atomic mutation."""


class MaterialAssetStore:
    def read_evidence(self, workspace_id, asset_id, **window):
        from app.materials.evidence import MaterialEvidenceReader
        return MaterialEvidenceReader(self._connect).read(workspace_id, asset_id, **window)

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def initialize_semantic_queries(self) -> None:
        """Derived private query cache; the device queue alone owns execution state."""
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS material_semantic_queries (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, member_id TEXT NOT NULL,
                identity_json TEXT NOT NULL, query_text TEXT NOT NULL,
                model_id TEXT NOT NULL, index_version TEXT NOT NULL,
                vector_json TEXT)""")

    def save_semantic_query(self, query_id, workspace_id, identity, query, model_id, index_version):
        with self._connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO material_semantic_queries
                (id,workspace_id,member_id,identity_json,query_text,model_id,index_version)
                VALUES (?,?,?,?,?,?,?)""", (query_id, workspace_id, identity["member_id"],
                    json.dumps(identity, sort_keys=True), query, model_id, index_version))

    def read_semantic_query(self, workspace_id, query_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT * FROM material_semantic_queries WHERE workspace_id=? AND id=?",
                               (workspace_id, query_id)).fetchone()
        if row is None:
            raise KeyError(query_id)
        return dict(row)

    def commit_semantic_query(self, conn, workspace_id, query_id, vector):
        conn.execute("UPDATE material_semantic_queries SET vector_json=? WHERE workspace_id=? AND id=?",
                     (json.dumps(vector), workspace_id, query_id))
        return query_id

    def read_visual_segment_feature(self, conn, workspace_id, asset_id, segment_id, model_id, fingerprint):
        row = conn.execute("""SELECT vector_json,sample_count FROM material_segment_features
            WHERE workspace_id=? AND asset_id=? AND segment_id=? AND model_id=? AND fingerprint=?""",
            (workspace_id, asset_id, segment_id, model_id, fingerprint)).fetchone()
        if row is None:
            return None
        vector = json.loads(row["vector_json"])
        if (not isinstance(vector, list) or not vector
                or any(type(value) not in {float, int} or not math.isfinite(value) for value in vector)
                or sum(value * value for value in vector) <= 1e-12
                or not 1 <= row["sample_count"] <= 3):
            raise ValueError("已保存的视觉特征格式无效")
        return MaterialVisualFeature(vector, row["sample_count"], fingerprint)

    def _disk_missing_asset_rows(
        self, conn: sqlite3.Connection, workspace_id: str,
    ) -> list[sqlite3.Row]:
        return conn.execute("SELECT payload FROM material_assets WHERE workspace_id=?", (workspace_id,)).fetchall()

    def mark_disk_missing_assets(self, rows: list[sqlite3.Row]) -> None:
        for row in rows:
            asset = self._asset_from_payload(json.loads(str(row["payload"])))
            if asset.analysis_status != "failed":
                asset.analysis_status = "blocked"
                asset.analysis_message = "外接盘或素材目录不可用；连接原盘后会继续补扫。"
                self.save_asset(asset)

    def get_asset_by_path(self, workspace_id: str, file_path: str) -> MaterialAsset:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM material_assets WHERE workspace_id=? AND file_path=?", (workspace_id, file_path)).fetchone()
        if row is None:
            raise KeyError(file_path)
        return self._asset_from_payload(json.loads(str(row["payload"])))

    def find_asset_by_source_url(self, workspace_id: str, source_url: str) -> MaterialAsset | None:
        """Find the material already registered for one original direct link."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload, visibility FROM material_assets
                WHERE workspace_id = ? AND json_extract(payload, '$.source_url') = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (workspace_id, source_url),
            ).fetchone()
        return (
            self._asset_from_row(row)
            if row is not None
            else None
        )

    def save_discovered_asset(
        self, existing: MaterialAsset | None, workspace_id: str, resolved: Path, stat: stat_result,
        *, media_type: MaterialMediaType | None, source_url: str,
        owner_user_id: str | None, copy_uploader_user_id: str | None,
        connection: sqlite3.Connection | None = None,
    ) -> MaterialAsset:
        if existing is None:
            digest = hashlib.sha256(f"{workspace_id}\0{resolved}".encode("utf-8")).hexdigest()[:16]
            existing = MaterialAsset(
                id=f"asset_{digest}",
                workspace_id=workspace_id,
                owner_user_id=(
                    str(owner_user_id or "").strip()
                    or copy_uploader_user_id
                ) or None,
                file_name=resolved.name,
                path=str(resolved),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo),
                media_type=media_type or material_media_type_for(resolved) or "video",
                source_url=source_url,
            )
        else:
            # An application import may retain the original display filename
            # while its immutable stored path uses a deduplicated identity.
            if not resolved.name.startswith("discovery-"):
                existing.file_name = resolved.name
            existing.size_bytes = stat.st_size
            existing.modified_at = datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo)
            # A later scan must not erase a recorded media type or provenance.
            if media_type is not None:
                existing.media_type = media_type
            if source_url and not existing.source_url:
                existing.source_url = source_url
        return self.save_asset(existing, connection=connection)

    def _asset_payload_row_by_id(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT payload FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()

    def _asset_payload_visibility_row_by_id(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT payload,visibility FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()

    def _visual_feature_asset_payload_row(
        self, conn: sqlite3.Connection, workspace_id: str, segment_id: str, model_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
                """
                SELECT a.payload FROM material_assets a
                JOIN material_segment_features f
                    ON a.workspace_id = f.workspace_id AND a.id = f.asset_id
                WHERE f.workspace_id = ? AND f.segment_id = ? AND f.model_id = ?
                """,
                (workspace_id, segment_id, model_id),
        ).fetchone()

    def _visual_feature_vector_row(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
        segment_id: str, model_id: str, fingerprint: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
                """
                SELECT vector_json FROM material_segment_features
                WHERE workspace_id = ? AND asset_id = ? AND segment_id = ?
                    AND model_id = ? AND fingerprint = ?
                """,
            (workspace_id, asset_id, segment_id, model_id, fingerprint),
        ).fetchone()

    def _visual_feature_cache_rows(
        self, conn: sqlite3.Connection, workspace_id: str, model_id: str,
    ) -> list[sqlite3.Row]:
        return conn.execute(
                """
                SELECT segment_id, fingerprint FROM material_segment_features
                WHERE workspace_id = ? AND model_id = ?
                """,
            (workspace_id, model_id),
        ).fetchall()

    def write_visual_segment_feature(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
        segment_id: str, model_id: str, fingerprint: str, vector_json: str,
        sample_count: int, updated_at: str,
    ) -> None:
        conn.execute(
                        """
                        INSERT INTO material_segment_features
                            (workspace_id, asset_id, segment_id, model_id, fingerprint,
                             vector_json, sample_count, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(workspace_id, segment_id, model_id) DO UPDATE SET
                            asset_id = excluded.asset_id,
                            fingerprint = excluded.fingerprint,
                            vector_json = excluded.vector_json,
                            sample_count = excluded.sample_count,
                            updated_at = excluded.updated_at
                        """,
            (workspace_id, asset_id, segment_id, model_id, fingerprint, vector_json, sample_count, updated_at),
        )

    def assemble_scene_segments(
        self, conn: sqlite3.Connection, source_payload: str, boundaries: list[float], duration_seconds: float,
    ) -> MaterialAsset:
        asset = self._asset_from_payload(json.loads(str(source_payload)))
        old_segments = {segment.id: segment for segment in asset.segments}
        segments = []
        for start, end in _scene_ranges(boundaries, duration_seconds):
            segment_id = _segment_id(asset.id, start, end)
            segments.append(old_segments.pop(segment_id, None) or MaterialSegment(
                id=segment_id, asset_id=asset.id, start_seconds=start, end_seconds=end,
            ))
        for segment in self._corrected_material_segments(list(old_segments.values())):
            conn.execute(
                """INSERT OR IGNORE INTO material_unmatched_corrections
                   (workspace_id, asset_id, segment_id, payload, preserved_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (asset.workspace_id, asset.id, segment.id, _json_payload(segment),
                 utc_now().isoformat()),
            )
        asset.segments = segments
        asset.analysis_status = "blocked"
        asset.analysis_message = (
            f"已定位 {len(segments)} 个镜头候选；原片声音和画面理解尚未完成。"
        )
        asset.updated_at = utc_now()
        conn.execute(
            "UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(asset), asset.updated_at.isoformat(), asset.workspace_id, asset.id),
        )
        return asset

    def mark_scene_failed(
        self, conn: sqlite3.Connection, source_payload: str, reason: str,
    ) -> MaterialAsset:
        asset = self._asset_from_payload(json.loads(str(source_payload)))
        asset.analysis_status = "failed"
        asset.analysis_message = f"镜头窗口分析失败（{reason}）；检查原片后可重试。"
        asset.updated_at = utc_now()
        conn.execute(
            "UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(asset), asset.updated_at.isoformat(), asset.workspace_id, asset.id),
        )
        return asset

    def mark_hash_asset_blocked(self, workspace_id: str, asset_id: str, message: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM material_assets WHERE workspace_id=? AND id=?",
                (workspace_id, asset_id),
            ).fetchone()
            if row is None:
                return
            asset = self._asset_from_payload(json.loads(str(row["payload"])))
            asset.analysis_status = "blocked"
            asset.analysis_message = message
            asset.updated_at = utc_now()
            conn.execute(
                "UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
                (_json_payload(asset), asset.updated_at.isoformat(), workspace_id, asset_id),
            )

    def privatize_local_adoption_asset(
        self, conn: sqlite3.Connection, asset_id: str, serialized_payload: str,
    ) -> None:
        conn.execute("UPDATE material_assets SET payload=?, visibility='private' WHERE id=? AND workspace_id='local'",
                     (serialized_payload, asset_id))

    def insert_local_voice_source_asset(
        self, conn: sqlite3.Connection, source_asset: MaterialAsset, source_payload: str, now: str,
    ) -> None:
        conn.execute("INSERT INTO material_assets(id,workspace_id,file_path,payload,visibility,updated_at) VALUES (?,'local',?,?,'private',?)",
                     (source_asset.id, source_asset.path, source_payload, now))

    def repoint_asset(self, asset: MaterialAsset) -> None:
        """Move an existing material record onto a restored file, keeping its id.

        `_save_asset` upserts on (workspace_id, file_path), so a record whose file
        changed path cannot go through it without colliding on its own id.
        """
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE material_assets
                SET file_path = ?, payload = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    asset.path,
                    _json_payload(asset),
                    asset.updated_at.isoformat(),
                    asset.id,
                    asset.workspace_id,
                ),
            )

    def _probe_asset_source_row(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT payload, file_path FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()

    def apply_probe_metadata(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        asset_id: str,
        source_payload: str,
        duration_seconds: float,
    ) -> MaterialAsset:
        asset = self._asset_from_payload(json.loads(str(source_payload)))
        asset.duration_seconds = duration_seconds
        asset.analysis_status = "blocked"
        asset.analysis_message = "已读取时长与媒体流；镜头和画面分析尚未完成。"
        asset.updated_at = utc_now()
        conn.execute(
            "UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(asset), asset.updated_at.isoformat(), workspace_id, asset_id),
        )
        return asset

    def mark_probe_failed(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        asset_id: str,
        source_payload: str,
        reason: str,
    ) -> MaterialAsset:
        asset = self._asset_from_payload(json.loads(str(source_payload)))
        asset.analysis_status = "failed"
        asset.analysis_message = {
            "probe_invalid_media": "媒体信息无效；请检查或更换原文件，当前不会自动重试。",
            "probe_timeout": "读取媒体信息超时；请检查磁盘和文件，当前不会自动重试。",
            "probe_output_limit": "媒体信息输出超出上限；请检查文件，当前不会自动重试。",
            "probe_failed": "无法读取媒体信息；请检查磁盘和文件，当前不会自动重试。",
        }[reason]
        asset.updated_at = utc_now()
        conn.execute(
            "UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(asset), asset.updated_at.isoformat(), workspace_id, asset_id),
        )
        return asset

    def _asset_id_row_by_path(
        self, conn: sqlite3.Connection, workspace_id: str, file_path: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT id FROM material_assets WHERE workspace_id=? AND file_path=?",
            (workspace_id, file_path),
        ).fetchone()

    def _asset_payload_row_by_path(
        self, conn: sqlite3.Connection, workspace_id: str, file_path: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT payload, visibility FROM material_assets WHERE workspace_id=? AND file_path=?",
            (workspace_id, file_path),
        ).fetchone()

    def _asset_file_path_rows_outside_workspace(
        self, conn: sqlite3.Connection, excluded_workspace_id: str,
    ) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT file_path FROM material_assets WHERE workspace_id!=?",
            (excluded_workspace_id,),
        ).fetchall()

    def _asset_row_by_id(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM material_assets WHERE id=? AND workspace_id=?",
            (asset_id, workspace_id),
        ).fetchone()

    def _asset_file_path_existence_row(
        self, conn: sqlite3.Connection, file_path: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT 1 FROM material_assets WHERE file_path=?", (file_path,),
        ).fetchone()

    def _asset_id_existence_row(
        self, conn: sqlite3.Connection, asset_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT 1 FROM material_assets WHERE id=?", (asset_id,),
        ).fetchone()

    def get_asset(self, workspace_id: str, asset_id: str, *, connection: sqlite3.Connection | None = None) -> MaterialAsset:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                """
                SELECT payload, visibility FROM material_assets
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, asset_id),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return self._asset_from_row(row)

    def list_assets(
        self,
        workspace_id: str,
        *,
        include_private: bool = True,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[MaterialAsset]:
        """列出素材。与分页同一判据：可信身份下私有素材只对上传者本人可见。"""
        clauses = ["workspace_id = ?"]
        parameters: list[object] = [workspace_id]
        if not local_diagnostic and not (viewer_user_id is None and include_private):
            clauses.append(
                "(visibility = 'shared' OR (visibility = 'private' "
                "AND json_extract(payload, '$.owner_user_id') IS NOT NULL "
                "AND json_extract(payload, '$.owner_user_id') = ?))"
            )
            parameters.append(viewer_user_id)
        elif not include_private:
            clauses.append("visibility = 'shared'")
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            rows = conn.execute(
                f"""
                SELECT payload, visibility FROM material_assets
                WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC
                """,
                parameters,
            ).fetchall()
        return [self._asset_from_row(row) for row in rows]

    def list_assets_page(
        self,
        workspace_id: str,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
        tag: str | None = None,
        review_status: str | None = None,
        media_type: str | None = None,
        include_private: bool = True,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> tuple[list[MaterialAsset], int]:
        """列出素材页。

        给出可信读取身份时按同一判据在 SQL 里过滤（保证分页总数与条目一致）：
        私有素材只对上传者本人可见，shared 对成员可见，可见范围未知的按不可见处理。
        """
        if not local_diagnostic and viewer_user_id is None and not include_private:
            return [], 0
        clauses = ["workspace_id = ?"]
        parameters: list[object] = [workspace_id]
        owner_scoped = not local_diagnostic and not (viewer_user_id is None and include_private)
        if owner_scoped:
            # 私有只有 owner 是本人时才可见；其余（含没有 owner 的私有行）不返回。
            clauses.append(
                "(visibility = 'shared' OR (visibility = 'private' "
                "AND json_extract(payload, '$.owner_user_id') IS NOT NULL "
                "AND json_extract(payload, '$.owner_user_id') = ?))"
            )
            parameters.append(viewer_user_id)
        elif not include_private:
            clauses.append("visibility = 'shared'")
        if query:
            clauses.append("""(
                LOWER(json_extract(payload, '$.file_name')) LIKE ?
                OR LOWER(json_extract(payload, '$.user_description')) LIKE ?
                OR LOWER(json_extract(payload, '$.upload_context')) LIKE ?
                OR EXISTS (SELECT 1 FROM json_each(material_assets.payload, '$.user_tags') AS tag WHERE LOWER(tag.value) LIKE ?)
                OR EXISTS (
                    SELECT 1 FROM json_each(material_assets.payload, '$.segments') AS segment
                    WHERE LOWER(CASE WHEN trim(json_extract(segment.value, '$.description')) != ''
                        THEN json_extract(segment.value, '$.description')
                        WHEN json_extract(segment.value, '$.understanding.status') = 'ready'
                        THEN json_extract(segment.value, '$.understanding.summary') ELSE '' END) LIKE ?
                    OR LOWER(CASE WHEN trim(json_extract(segment.value, '$.transcript')) != ''
                        THEN json_extract(segment.value, '$.transcript')
                        ELSE (SELECT group_concat(json_extract(speech.value, '$.text'), ' ')
                            FROM json_each(material_assets.payload, '$.speech_analysis.observations') AS speech
                            WHERE json_extract(material_assets.payload, '$.speech_analysis.status') = 'ready'
                            AND json_extract(speech.value, '$.end_ms') > json_extract(segment.value, '$.start_seconds') * 1000
                            AND json_extract(speech.value, '$.start_ms') < json_extract(segment.value, '$.end_seconds') * 1000)
                        END) LIKE ?
                    OR EXISTS (SELECT 1 FROM json_each(segment.value, '$.user_tags') AS tag WHERE LOWER(tag.value) LIKE ?)
                    OR EXISTS (SELECT 1 FROM json_each(segment.value, '$.system_tags') AS tag WHERE LOWER(tag.value) LIKE ?)
                    OR (json_extract(segment.value, '$.understanding.ocr_status') = 'ready' AND EXISTS (
                        SELECT 1 FROM json_each(segment.value, '$.understanding.ocr_frames') AS frame,
                            json_each(frame.value, '$.observations') AS observation
                        WHERE LOWER(json_extract(observation.value, '$.text')) LIKE ?
                        AND json_extract(observation.value, '$.confidence') >= ?))
                )
            )""")
            parameters.extend([*[f"%{query.strip().lower()}%"] * 9, OCR_RETRIEVAL_MIN_CONFIDENCE])
        if tag:
            # Clicking a tag is an exact choice, not a search: "红烧肉" must not
            # drag in "红烧肉特写", and the tag's own spelling is the whole test —
            # no case folding and no trimming, or the number shown beside the
            # word cloud would not be the number of rows this returns. It joins
            # the same clause as every other filter, so the counted total and the
            # paged rows are one set.
            clauses.append("""(
                EXISTS (SELECT 1 FROM json_each(material_assets.payload, '$.user_tags') AS tag WHERE tag.value = ?)
                OR EXISTS (
                    SELECT 1 FROM json_each(material_assets.payload, '$.segments') AS segment
                    WHERE EXISTS (SELECT 1 FROM json_each(segment.value, '$.user_tags') AS tag WHERE tag.value = ?)
                        OR EXISTS (SELECT 1 FROM json_each(segment.value, '$.system_tags') AS tag WHERE tag.value = ?)
                )
            )""")
            parameters.extend([tag] * 3)
        if review_status:
            clauses.append("json_extract(payload, '$.review_status') = ?")
            parameters.append(review_status)
        if media_type:
            # 和审核状态、标签、关键词一样是同一批行上的一条独立条件，所以计数
            # 和分页自动共用它；省略这个参数就是全部。
            # 缺 media_type 的旧记录在 MaterialAsset 里默认是 video（见字段定义），
            # 筛选必须跟加载后的类型一致，否则这些视频会从「视频」里消失。
            clauses.append("COALESCE(json_extract(payload, '$.media_type'), 'video') = ?")
            parameters.append(media_type)
        where_clause = " AND ".join(clauses)
        with self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM material_assets WHERE {where_clause}",
                    parameters,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT payload, visibility FROM material_assets
                WHERE {where_clause} ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
        assets = [self._asset_from_row(row) for row in rows]
        return assets, total

    def save_asset(self, asset: MaterialAsset, *, analysis_only: bool = False,
                   connection: sqlite3.Connection | None = None) -> MaterialAsset:
        if connection is not None and not connection.in_transaction:
            raise ValueError("素材保存必须在调用方事务中执行")
        asset.updated_at = utc_now()
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            if analysis_only:
                if connection is None:
                    conn.execute("BEGIN IMMEDIATE")
                # 连 visibility 一起读：分析写回时用库里那一列的当前值，
                # 不拿分析结果里的旧值覆盖用户明确设过的共享或收回。
                row = conn.execute(
                    "SELECT payload, visibility FROM material_assets WHERE id = ? AND workspace_id = ?",
                    (asset.id, asset.workspace_id),
                ).fetchone()
                if row is None:
                    raise ValueError("素材已移除，未保存过期的分析结果。")
                current = self._asset_from_row(row)
                if current.size_bytes != asset.size_bytes or current.modified_at != asset.modified_at:
                    raise ValueError("分析期间素材文件发生变化，请重新分析。")
                current_segments = {item.id: item for item in current.segments}
                merged_segments = []
                for analyzed in asset.segments:
                    latest = current_segments.get(analyzed.id)
                    if latest is not None:
                        for name in ("visual_status", "visual_model_id", "visual_sample_count", "understanding",
                                     "suggested_usage_role", "usage_role_confidence", "usage_role_reason"):
                            setattr(latest, name, getattr(analyzed, name))
                        latest.system_tags = _infer_system_tags(current, latest)
                        merged_segments.append(latest)
                    else:
                        merged_segments.append(analyzed)
                merged_ids = {segment.id for segment in merged_segments}
                corrected = self._corrected_material_segments(
                    [segment for segment in current.segments if segment.id not in merged_ids]
                )
                for segment in corrected:
                    conn.execute(
                        "INSERT OR IGNORE INTO material_unmatched_corrections (workspace_id, asset_id, segment_id, payload, preserved_at) VALUES (?, ?, ?, ?, ?)",
                        (current.workspace_id, current.id, segment.id, _json_payload(segment), utc_now().isoformat()),
                    )
                for name in ("duration_seconds", "analysis_status", "analysis_message", "speech_analysis",
                             "suggested_usage_role", "usage_role_confidence", "usage_role_reason"):
                    setattr(current, name, getattr(asset, name))
                current.segments = merged_segments
                if corrected:
                    current.analysis_message += f" {len(corrected)} 条旧片段的人工纠正因边界变化待核对。"
                current.updated_at = asset.updated_at
                asset = current
            self._write_asset(conn, asset)
        return asset

    def save_received_asset(
        self, conn: sqlite3.Connection, asset: MaterialAsset, *,
        workspace_id: str, owner_user_id: str,
    ) -> MaterialAsset:
        """Register a complete received file inside the caller's transaction.

        The caller verifies membership and file publication. Replays preserve
        existing analysis/corrections rather than upserting an empty aggregate.
        """
        if not conn.in_transaction:
            raise ValueError("接收素材必须在调用方事务中保存")
        if not workspace_id or not owner_user_id or (asset.workspace_id, asset.owner_user_id) != (workspace_id, owner_user_id):
            raise ValueError("接收素材的工作区或成员归属不匹配")
        rows = conn.execute(
            "SELECT payload, visibility FROM material_assets WHERE id=? OR (workspace_id=? AND file_path=?)",
            (asset.id, workspace_id, asset.path),
        ).fetchall()
        if rows:
            current = self._asset_from_row(rows[0])
            if len(rows) != 1 or (current.id, current.workspace_id, current.owner_user_id, current.path, current.size_bytes, current.modified_at, current.media_type) != (asset.id, workspace_id, owner_user_id, asset.path, asset.size_bytes, asset.modified_at, asset.media_type):
                raise ValueError("接收素材身份或文件修订发生冲突")
            return current
        asset.updated_at = utc_now()
        self._write_asset(conn, asset)
        return asset

    @staticmethod
    def _write_asset(conn: sqlite3.Connection, asset: MaterialAsset) -> None:
        conn.execute(
            """
                INSERT INTO material_assets
                    (id, workspace_id, file_path, payload, visibility, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
            (
                asset.id,
                asset.workspace_id,
                asset.path,
                _json_payload(asset),
                asset.visibility,
                asset.updated_at.isoformat(),
            ),
        )

    def update_asset_use_state(self, workspace_id: str, asset_id: str, use_state: ContentUseState) -> MaterialAsset:
        if use_state not in {"available", "reference_only", "excluded", "not_for_automation", "not_for_publish"}:
            raise ValueError("素材使用状态无效")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self.get_asset(workspace_id, asset_id, connection=conn)
            asset.use_state = use_state
            asset.updated_at = utc_now()
            self._write_asset(conn, asset)
        return asset

    def update_asset_review(
        self,
        workspace_id: str,
        asset_id: str,
        *,
        review_status: AssetReviewStatus,
        usage_role: AssetUsageRole = "unassigned",
    ) -> MaterialAsset:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self.get_asset(workspace_id, asset_id, connection=conn)
            if usage_role == "exclude":
                review_status = "rejected"
            if review_status == "approved":
                if usage_role not in {"a_roll", "b_roll"}:
                    raise ValueError("先选择人物主镜头或 B-roll，再加入可用库")
                if asset.analysis_status != "ready" or not asset.segments:
                    raise ValueError("请先完成镜头分析再审核素材")
            asset.review_status = review_status
            asset.usage_role = usage_role
            if review_status == "rejected":
                asset.use_state = "excluded"
            if review_status == "approved":
                for segment in asset.segments:
                    if segment.usage_role == "unassigned":
                        segment.usage_role = usage_role
                    if segment.review_status == "raw":
                        segment.review_status = "approved"
                        segment.updated_at = utc_now()
            elif review_status == "rejected":
                for segment in asset.segments:
                    if usage_role == "exclude":
                        segment.usage_role = "exclude"
                    segment.review_status = "rejected"
                    segment.updated_at = utc_now()
            asset.updated_at = utc_now()
            self._write_asset(conn, asset)
        return asset

    def record_segment_usage(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        plan_id: str | None = None,
        clip_id: str | None = None,
        task_id: str | None = None,
        timeline_version_id: str | None = None,
    ) -> MaterialAsset:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self.get_asset(workspace_id, asset_id, connection=conn)
            segment = next((item for item in asset.segments if item.id == segment_id), None)
            if segment is None:
                raise KeyError(segment_id)
            usage_plan_id = plan_id or f"manual_{uuid4().hex}"
            usage_clip_id = clip_id or f"clip_{uuid4().hex}"
            used_at = utc_now()
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO material_segment_usage
                        (workspace_id, plan_id, clip_id, asset_id, segment_id, used_at,
                         task_id, timeline_version_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (workspace_id, usage_plan_id, usage_clip_id, asset_id, segment_id,
                     used_at.isoformat(), task_id, timeline_version_id),
                )
            except sqlite3.OperationalError as exc:
                if "no column named" not in str(exc):
                    raise
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO material_segment_usage
                        (workspace_id, plan_id, clip_id, asset_id, segment_id, used_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workspace_id, usage_plan_id, usage_clip_id, asset_id, segment_id,
                     used_at.isoformat()),
                )
            if cursor.rowcount == 0:
                return asset
            segment.usage_count += 1
            segment.last_used_at = used_at
            segment.updated_at = used_at
            asset.updated_at = utc_now()
            self._write_asset(conn, asset)
        return asset

    def update_asset_tags(self, workspace_id: str, asset_id: str, user_tags: list[str], *, expected_asset_updated_at: datetime | None = None) -> MaterialAsset:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM material_assets WHERE workspace_id=? AND id=?", (workspace_id, asset_id)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            asset = self._asset_from_payload(json.loads(str(row["payload"])))
            if expected_asset_updated_at is not None and asset.updated_at != expected_asset_updated_at:
                raise MaterialVersionConflict("素材版本已变化，未提交标签修改；请重新查看后发送。")
            asset.user_tags = _normalized_labels(user_tags)
            asset.updated_at = utc_now()
            conn.execute("UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?", (_json_payload(asset), asset.updated_at.isoformat(), workspace_id, asset_id))
        return asset

    def update_assets_tags_batch(
        self, workspace_id: str, asset_ids: list[str], *, add: list[str], remove: list[str]
    ) -> list[MaterialAsset]:
        if not asset_ids:
            raise ValueError("请选择至少一条素材")
        normalized_add = _normalized_labels(add)
        normalized_remove = {value.casefold() for value in _normalized_labels(remove)}
        updated: list[MaterialAsset] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for asset_id in dict.fromkeys(asset_ids):
                row = conn.execute("SELECT payload FROM material_assets WHERE workspace_id=? AND id=?", (workspace_id, asset_id)).fetchone()
                if row is None:
                    raise KeyError(asset_id)
                asset = self._asset_from_payload(json.loads(str(row["payload"])))
                kept = [tag for tag in asset.user_tags if tag.casefold() not in normalized_remove]
                asset.user_tags = _normalized_labels([*kept, *normalized_add])
                asset.updated_at = utc_now()
                updated.append(asset)
            for asset in updated:
                conn.execute("UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?", (_json_payload(asset), asset.updated_at.isoformat(), workspace_id, asset.id))
        return updated

    def update_asset_notes(
        self, workspace_id: str, notes: list[MaterialAssetNote]
    ) -> list[MaterialAsset]:
        """Save the user's own text for exactly the materials named in `notes`.

        Each note carries that material's whole text, so this replaces rather
        than merges, and an empty string clears an earlier note instead of being
        rejected. Nothing is written until every named material is confirmed to
        exist in this workspace: a batch naming one missing or foreign asset is
        refused whole, because a half-saved batch would leave the user believing
        a note is stored that is not.
        """
        if not notes:
            raise ValueError("请选择至少一条素材")
        updated: list[MaterialAsset] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for note in dict.fromkeys(notes):
                row = conn.execute(
                    "SELECT payload FROM material_assets WHERE workspace_id=? AND id=?",
                    (workspace_id, note.asset_id),
                ).fetchone()
                if row is None:
                    raise KeyError(note.asset_id)
                asset = self._asset_from_payload(json.loads(str(row["payload"])))
                asset.user_description = note.user_description.strip()
                asset.upload_context = note.upload_context.strip()
                asset.updated_at = utc_now()
                updated.append(asset)
            for asset in updated:
                conn.execute(
                    "UPDATE material_assets SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
                    (_json_payload(asset), asset.updated_at.isoformat(), workspace_id, asset.id),
                )
        return updated

    def update_segment(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        description: str,
        transcript: str,
        review_status: AssetReviewStatus,
        usage_role: AssetUsageRole | None = None,
        user_tags: list[str] | None = None,
        collection_ids: list[str] | None = None,
        expected_asset_updated_at: datetime | None = None,
        expected_segment_updated_at: datetime | None = None,
    ) -> MaterialAsset:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM material_assets WHERE workspace_id=? AND id=?", (workspace_id, asset_id)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            asset = self._asset_from_payload(json.loads(str(row["payload"])))
            if expected_asset_updated_at is not None and asset.updated_at != expected_asset_updated_at:
                raise MaterialVersionConflict("素材版本已变化，未提交片段修改；请重新查看后发送。")
            segment = next((item for item in asset.segments if item.id == segment_id), None)
            if segment is None:
                raise KeyError(segment_id)
            if expected_segment_updated_at is not None and segment.updated_at != expected_segment_updated_at:
                raise MaterialVersionConflict("片段版本已变化，未提交修改；请重新查看后发送。")
            if review_status == "approved" and asset.review_status != "approved":
                raise ValueError("素材文件尚未进入可用库")
            segment.description = description.strip()
            segment.transcript = transcript.strip()
            if usage_role is not None:
                previous_usage_role = segment.usage_role
                segment.usage_role = usage_role
                if usage_role == "exclude":
                    review_status = "rejected"
                if usage_role != previous_usage_role:
                    segment.system_tags = [
                        tag for tag in segment.system_tags if not tag.startswith("用途:")
                    ]
                    if usage_role != "unassigned":
                        segment.system_tags.append(f"用途:{usage_role}")
            if user_tags is not None:
                segment.user_tags = _normalized_labels(user_tags)
            if collection_ids is not None:
                known_collections = collection_ids_for_workspace(conn, workspace_id)
                if not set(collection_ids) <= known_collections:
                    raise ValueError("标签夹不存在或不属于当前工作区")
                segment.collection_ids = list(dict.fromkeys(collection_ids))
            segment.review_status = review_status
            # Editing member text does not replace recognition facts. Analysis owns
            # system_tags and still refreshes them through its existing save path.
            segment.updated_at = utc_now()
            asset.updated_at = utc_now()
            conn.execute("UPDATE material_assets SET payload=?,updated_at=? WHERE workspace_id=? AND id=?", (_json_payload(asset),asset.updated_at.isoformat(),workspace_id,asset_id))

        return asset

    def set_asset_visibility(
        self, workspace_id: str, asset_id: str, visibility: ContentVisibility
    ) -> MaterialAsset:
        """明确共享或收回一条素材的可见性，列与载荷一起改，回读一致。"""
        if visibility not in ("private", "shared"):
            raise ValueError("可见性只能是 private 或 shared。")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, visibility FROM material_assets WHERE workspace_id=? AND id=?",
                (workspace_id, asset_id),
            ).fetchone()
            if row is None:
                raise KeyError(asset_id)
            asset = self._asset_from_row(row)
            asset = replace(asset, visibility=visibility, updated_at=utc_now())
            conn.execute(
                "UPDATE material_assets SET payload=?, visibility=?, updated_at=? WHERE workspace_id=? AND id=?",
                (_json_payload(asset), visibility, asset.updated_at.isoformat(), workspace_id, asset_id),
            )
        return asset

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> MaterialAsset:
        """读素材时，可见性以列为准。

        存量行的 payload 里没有 visibility 字段，值只写在列上（迁移回填的
        shared）；新行的两者一致。以列为准，读到的值和列表过滤永远同一个来源。
        """
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return MaterialAssetStore._asset_from_payload(data)

    @staticmethod
    def _asset_from_payload(data: dict[str, object]) -> MaterialAsset:
        # Read-only legacy boundary: old proof flags never regain mutation authority.
        data = dict(data)
        data.pop("rights_confirmed", None)
        data.pop("brand_safe", None)
        if "use_state" not in data:
            data["use_state"] = "excluded" if data.get("review_status") == "rejected" or data.get("usage_role") == "exclude" else "available"
        segment_payloads = data.pop("segments", [])
        data = _with_datetime(data, "modified_at", "created_at", "updated_at")
        segments = [
            MaterialSegment(
                **_with_datetime(segment, "last_used_at", "created_at", "updated_at")
            )
            for segment in segment_payloads
            if isinstance(segment, dict)
        ]
        speech = data.pop("speech_analysis", None)
        speech_analysis = MaterialSpeechAnalysis(
            **{key: value for key, value in speech.items() if key not in {"observations", "word_observations"}},
            observations=[SpeechObservation(**item) for item in speech.get("observations", [])],
            word_observations=[SpeechObservation(**item) for item in speech.get("word_observations", [])],
        ) if isinstance(speech, dict) else None
        asset = MaterialAsset(**data, segments=segments, speech_analysis=speech_analysis)
        for segment in asset.segments:
            if isinstance(segment.understanding, dict):
                segment.understanding = SegmentUnderstanding(**segment.understanding)
            if not segment.system_tags:
                segment.system_tags = _infer_system_tags(asset, segment)
        return asset

    @staticmethod
    def _corrected_material_segments(segments: list[MaterialSegment]) -> list[MaterialSegment]:
        return [segment for segment in segments if segment.description or segment.transcript or segment.user_tags or segment.collection_ids or segment.usage_role != "unassigned" or segment.review_status != "raw"]

    def reset_changed_material_asset(self, asset: MaterialAsset) -> None:
        self.preserve_unmatched_corrections(asset, asset.segments)
        asset.segments = []
        asset.speech_analysis = None
        asset.duration_seconds = None
        asset.analysis_status = "pending"
        asset.analysis_message = "源文件已变化，正在重新分析；原有人工纠正已保留待核对。"
        self.save_asset(asset)

    def preserve_unmatched_corrections(self, asset: MaterialAsset, segments: list[MaterialSegment]) -> int:
        corrected = self._corrected_material_segments(segments)
        if not corrected:
            return 0
        with self._connect() as conn:
            for segment in corrected:
                conn.execute(
                    "INSERT OR IGNORE INTO material_unmatched_corrections (workspace_id, asset_id, segment_id, payload, preserved_at) VALUES (?, ?, ?, ?, ?)",
                    (asset.workspace_id, asset.id, segment.id, _json_payload(segment), utc_now().isoformat()),
                )
        return len(corrected)

    def list_unmatched_material_corrections(self, workspace_id: str, asset_id: str) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM material_unmatched_corrections WHERE workspace_id=? AND asset_id=? ORDER BY preserved_at", (workspace_id, asset_id)).fetchall()
        return [json.loads(str(row["payload"])) for row in rows]


def _normalized_labels(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip()[:80]
            for value in values
            if value.strip()
        )
    )


def _infer_system_tags(
    asset: MaterialAsset,
    segment: MaterialSegment,
) -> list[str]:
    # File names and spoken claims are search hints, not visible evidence.
    text = segment.description.lower()
    role = effective_segment_usage_role(asset, segment)
    if role == "unassigned":
        role = segment.suggested_usage_role
    tags = [f"用途:{role}"] if role != "unassigned" else []
    rules = {
        "场景:办公": ("办公", "office", "workspace", "desk"),
        "物体:电脑": ("电脑", "笔记本", "laptop", "computer", "keyboard"),
        "动作:打字": ("打字", "typing"),
        "内容:软件界面": ("软件", "界面", "screen", "app", "website"),
        "场景:户外": ("户外", "街道", "风景", "outdoor", "street", "nature"),
        "主体:人物": ("人物", "人脸", "口播", "person", "face", "talking"),
        "镜头:特写": ("特写", "close-up", "closeup"),
    }
    for tag, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    if not segment.description and segment.understanding and segment.understanding.status == "ready":
        for fact in segment.understanding.facts:
            prefix = {"object": "主体", "scene": "场景", "action": "动作"}[fact["kind"]]
            tags.append(f"{prefix}:{fact['text']}")
    return _normalized_labels(tags)


def _scene_ranges(boundaries: list[float], duration: float) -> list[tuple[float, float]]:
    clean = sorted({round(value, 3) for value in boundaries if 0 < value < duration})
    points = [0.0, *clean, round(duration, 3)]
    ranges: list[tuple[float, float]] = []
    for start, end in pairwise(points):
        cursor = start
        while end - cursor > 12:
            ranges.append((round(cursor, 3), round(cursor + 12, 3)))
            cursor += 12
        if end - cursor >= 0.75:
            ranges.append((round(cursor, 3), round(end, 3)))
        elif ranges:
            previous_start, _ = ranges[-1]
            ranges[-1] = (previous_start, round(end, 3))
    return ranges or [(0.0, round(duration, 3))]


def _segment_id(asset_id: str, start: float, end: float) -> str:
    raw = f"{asset_id}:{start:.3f}:{end:.3f}"
    return f"seg_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
