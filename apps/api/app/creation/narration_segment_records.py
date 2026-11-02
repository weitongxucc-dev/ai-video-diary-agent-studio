"""Narration segment facts; complete-set writes stay separate from row hot paths."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
import json
import math
import re
import sqlite3

from app.creation.contracts import NarrationSegment, validate_logical_reference
from app.creation.script_contracts import CreationTaskScript
from app.creation.task_input_reads import (
    require_creation_task, narration_segment_from_row, narration_segment_row, narration_segment_rows,
)


def narration_segment_values(segment: NarrationSegment) -> tuple[object, ...]:
    return (
        segment.id,
        segment.workspace_id,
        segment.task_id,
        segment.script_id,
        segment.source_section_id,
        segment.text,
        segment.audio_reference,
        segment.duration_seconds,
        segment.sample_rate,
        segment.model_id,
        segment.voice_profile_id,
        segment.voice_version,
        segment.language,
        segment.pronunciation_version,
        segment.status,
        segment.created_at.isoformat(),
        segment.updated_at.isoformat(),
        segment.source_kind,
        segment.source_artifact_ref,
        segment.source_sha256,
        segment.source_script_digest,
    )


def validate_narration_segment(segment: NarrationSegment) -> None:
    identity = (segment.id, segment.workspace_id, segment.task_id, segment.script_id,
                segment.source_section_id, segment.text, segment.language)
    if not all(value.strip() for value in identity):
        raise ValueError("配音分段缺少必要谱系或文字")
    if segment.source_kind == "generated":
        if not all(value and value.strip() for value in
                   (segment.model_id, segment.voice_profile_id, segment.voice_version)):
            raise ValueError("生成配音分段必须有关联模型与声音版本")
        if (segment.source_artifact_ref is not None or segment.source_sha256 is not None
                or segment.source_script_digest is not None):
            raise ValueError("生成配音不能携带电脑录音来源")
    elif segment.source_kind == "recorded":
        if (segment.model_id is not None or segment.voice_profile_id is not None
                or segment.voice_version is not None
                or not segment.source_artifact_ref
                or not segment.source_sha256
                or not segment.source_script_digest
                or not re.fullmatch(r"[0-9a-f]{64}", segment.source_sha256)):
            raise ValueError("电脑录音分段必须仅关联真实录音产物与SHA-256")
        validate_logical_reference(segment.source_artifact_ref, "电脑录音来源")
    else:
        raise ValueError("配音来源类型无效")
    if segment.audio_reference is not None:
        validate_logical_reference(segment.audio_reference, "配音分段音频引用")
    if segment.duration_seconds is not None and (
        not math.isfinite(segment.duration_seconds) or segment.duration_seconds < 0
    ):
        raise ValueError("配音分段时长无效")
    if segment.sample_rate is not None and segment.sample_rate <= 0:
        raise ValueError("配音分段采样率无效")
    if segment.status == "ready" and (
        segment.audio_reference is None
        or segment.duration_seconds is None
        or segment.duration_seconds <= 0
        or (segment.sample_rate is None and segment.source_kind != "recorded")
    ):
        raise ValueError("ready 配音分段必须有可定位音频、时长和采样率")
    if segment.status not in {"pending", "generating", "ready", "failed"}:
        raise ValueError("配音分段状态无效")
    if segment.updated_at < segment.created_at:
        raise ValueError("配音分段更新时间不能早于创建时间")


class NarrationSegmentRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        decode_script: Callable[..., CreationTaskScript],
        confirmed_script_digest: Callable[[str, str, str], str | None],
        stage_segment_state: Callable[..., dict | None],
        iter_section_change_segments: Callable,
    ):
        self._connect = connection_factory
        self._decode_script = decode_script
        self._confirmed_script_digest = confirmed_script_digest
        self._stage_segment_state = stage_segment_state
        self._iter_section_change_segments = iter_section_change_segments


    def save_narration_segments(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[NarrationSegment],
        *, connection: sqlite3.Connection | None = None,
    ) -> list[NarrationSegment]:
        """Persist the complete current narration fact set for one confirmed script."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            self.upsert_narration_segments(
                conn,
                workspace_id=workspace_id,
                task_id=task_id,
                script_id=script_id,
                segments=segments,
            )
        return self.list_narration_segments(workspace_id, task_id, script_id, connection=connection)

    def get_narration_segment(
        self, workspace_id: str, task_id: str, segment_id: str, *, connection=None
    ) -> NarrationSegment:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            require_creation_task(conn, workspace_id, task_id)
            row = narration_segment_row(conn, workspace_id, task_id, segment_id)
        if row is None:
            raise KeyError(segment_id)
        return narration_segment_from_row(row)

    def iter_narration_segments(self, workspace_id, task_id, script_id, *, connection):
        """Original manifest order; the caller owns the entire read snapshot."""
        if not connection.in_transaction:
            raise ValueError('配音行读取必须属于原事务')
        task = require_creation_task(connection, workspace_id, task_id)
        if task.confirmed_script_id != script_id:
            raise ValueError('配音行不属于当前确认文案')
        cursor = connection.execute('SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? '
            'AND script_id=? ORDER BY created_at ASC,id ASC', (workspace_id, task_id, script_id))
        try:
            for row in cursor:
                yield narration_segment_from_row(row)
        finally:
            cursor.close()

    def list_narration_segments(
        self, workspace_id: str, task_id: str, script_id: str | None = None,
        *, connection: sqlite3.Connection | None = None,
    ) -> list[NarrationSegment]:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            task = require_creation_task(conn, workspace_id, task_id)
            current_script_id = script_id or task.confirmed_script_id
            if current_script_id is None:
                return []
            rows = narration_segment_rows(conn, workspace_id, task_id, current_script_id)
        return [narration_segment_from_row(row) for row in rows]

    def upsert_narration_segments(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[NarrationSegment],
    ) -> None:
        task = require_creation_task(conn, workspace_id, task_id)
        if task.confirmed_script_id != script_id:
            raise ValueError("配音分段必须关联当前任务已确认的文案")
        script_row = conn.execute(
            """
            SELECT payload FROM creation_task_scripts
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (workspace_id, task_id, script_id),
        ).fetchone()
        if script_row is None:
            raise ValueError("配音分段关联的文案不属于当前工作区与任务")
        script = self._decode_script(
            json.loads(str(script_row["payload"])), connection=conn
        )
        if script.selection_status != "confirmed":
            raise ValueError("配音分段只能关联已确认文案")
        script_digest = self._confirmed_script_digest(
            workspace_id, task_id, script_id,
        )
        sections = {section.id: section for section in script.sections}
        for section_id, section in tuple(sections.items()):
            adopted = self._stage_segment_state(workspace_id, task_id, section_id, connection=conn)
            if adopted is not None and adopted['script_id'] == script_id:
                sections[section_id] = replace(section, content=adopted['text'])
        supplied_section_ids = [segment.source_section_id for segment in segments]
        if (
            not sections
            or len(supplied_section_ids) != len(set(supplied_section_ids))
            or set(supplied_section_ids) != set(sections)
        ):
            raise ValueError("必须为已确认文案的每个段落保存唯一配音分段")

        existing_rows = conn.execute(
            """
            SELECT * FROM narration_segments
            WHERE workspace_id = ? AND task_id = ? AND script_id = ?
            """,
            (workspace_id, task_id, script_id),
        ).fetchall()
        if existing_rows and len(existing_rows) != len(sections):
            raise ValueError("已持久化的配音分段集不完整，不能覆盖")
        existing_by_section = {
            str(row["source_section_id"]): narration_segment_from_row(row)
            for row in existing_rows
        }

        for segment in segments:
            validate_narration_segment(segment)
            if (
                segment.workspace_id != workspace_id
                or segment.task_id != task_id
                or segment.script_id != script_id
            ):
                raise ValueError("配音分段与当前工作区、任务或文案谱系不一致")
            if segment.text != sections[segment.source_section_id].content:
                raise ValueError("配音分段文字必须与已确认文案段落一致")
            if segment.source_kind == "recorded" and segment.source_script_digest != script_digest:
                raise ValueError("电脑录音必须绑定当前确认文案内容")
            identity_row = conn.execute(
                "SELECT * FROM narration_segments WHERE id = ?",
                (segment.id,),
            ).fetchone()
            existing = existing_by_section.get(segment.source_section_id)
            if identity_row is not None and (
                existing is None or str(identity_row["id"]) != existing.id
            ):
                raise ValueError("配音分段 ID 已属于其他段落，不能覆盖")
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO narration_segments
                        (id, workspace_id, task_id, script_id, source_section_id,
                         text, audio_reference, duration_seconds, sample_rate,
                         model_id, voice_profile_id, voice_version, language,
                         pronunciation_version, status, created_at, updated_at,
                         source_kind, source_artifact_ref, source_sha256,
                         source_script_digest)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    narration_segment_values(segment),
                )
                continue
            if existing.id != segment.id or existing.created_at != segment.created_at:
                raise ValueError("配音分段稳定 ID 或创建时间不能覆盖")
            if segment.updated_at < existing.updated_at:
                raise ValueError("不能用过期状态覆盖配音分段")
            if segment == existing:
                continue
            conn.execute(
                """
                UPDATE narration_segments
                SET text = ?, audio_reference = ?, duration_seconds = ?,
                    sample_rate = ?, model_id = ?, voice_profile_id = ?,
                    voice_version = ?, language = ?, pronunciation_version = ?,
                    status = ?, updated_at = ?, source_kind = ?,
                    source_artifact_ref = ?, source_sha256 = ?, source_script_digest = ?
                WHERE workspace_id = ? AND task_id = ? AND script_id = ?
                  AND source_section_id = ? AND id = ?
                """,
                (
                    segment.text,
                    segment.audio_reference,
                    segment.duration_seconds,
                    segment.sample_rate,
                    segment.model_id,
                    segment.voice_profile_id,
                    segment.voice_version,
                    segment.language,
                    segment.pronunciation_version,
                    segment.status,
                    segment.updated_at.isoformat(),
                    segment.source_kind,
                    segment.source_artifact_ref,
                    segment.source_sha256,
                    segment.source_script_digest,
                    workspace_id,
                    task_id,
                    script_id,
                    segment.source_section_id,
                    segment.id,
                ),
            )

    def replace_adopted_narration_segment(self, task, replacement, *, connection):
        for _ in self._iter_section_change_segments(task, replacement, connection=connection, adopted=True):
            pass
        # Validation above preserves the complete-set contract, while this write
        # cannot insert, overwrite another segment, or rewrite unrelated rows.
        connection.execute('UPDATE narration_segments SET text=?,audio_reference=?,duration_seconds=?,sample_rate=?,model_id=?, '
            'voice_profile_id=?,voice_version=?,language=?,pronunciation_version=?,status=?,updated_at=?,source_kind=?, '
            'source_artifact_ref=?,source_sha256=?,source_script_digest=? '
            'WHERE workspace_id=? AND task_id=? AND script_id=? AND source_section_id=? AND id=?',
            (replacement.text,replacement.audio_reference,replacement.duration_seconds,replacement.sample_rate,replacement.model_id,
             replacement.voice_profile_id,replacement.voice_version,replacement.language,replacement.pronunciation_version,
             replacement.status,replacement.updated_at.isoformat(),replacement.source_kind,replacement.source_artifact_ref,
             replacement.source_sha256,replacement.source_script_digest,task.workspace_id,task.id,task.confirmed_script_id,
             replacement.source_section_id,replacement.id))
        return replacement
