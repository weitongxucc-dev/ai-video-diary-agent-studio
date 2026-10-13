"""Asset aggregate storage with caller-owned transaction reads and atomic analysis saves."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import json
import sqlite3

from app.materials.contracts import MaterialAsset, MaterialSegment, effective_segment_usage_role
from app.media_observations import (
    MaterialSpeechAnalysis,
    OCR_RETRIEVAL_MIN_CONFIDENCE,
    SegmentUnderstanding,
    SubtitleSegment as SpeechObservation,
)
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime


class MaterialAssetStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

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
        with self._connect() as conn:
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
            parameters.extend([*[f"%{query.strip().lower()}%"] * 7, OCR_RETRIEVAL_MIN_CONFIDENCE])
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

    def save_asset(self, asset: MaterialAsset, *, analysis_only: bool = False) -> MaterialAsset:
        asset.updated_at = utc_now()
        with self._connect() as conn:
            if analysis_only:
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
            **{key: value for key, value in speech.items() if key != "observations"},
            observations=[SpeechObservation(**item) for item in speech.get("observations", [])],
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
