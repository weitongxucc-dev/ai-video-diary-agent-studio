"""Subtitle track records bound to the caller's timeline-version facts."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import math
import sqlite3

from app.creation.contracts import SubtitleTrack, SubtitleCue, validate_logical_reference
from app.creation.task_input_reads import require_creation_task
from app.persistence_json import _json_payload, _with_datetime


def validate_subtitle_track(track: SubtitleTrack) -> None:
    if not all(
        value.strip()
        for value in (
            track.id,
            track.workspace_id,
            track.task_id,
            track.timeline_version_id,
            track.language,
            track.generation_method,
        )
    ):
        raise ValueError("字幕轨缺少任务、时间线、语言或生成方式")
    if track.language not in {"zh-CN", "en-US"}:
        raise ValueError("字幕轨语言无效")
    if track.generation_method not in {"timeline", "translation"}:
        raise ValueError("字幕轨生成方式无效")
    if track.source_language is not None and track.source_language not in {
        "zh-CN",
        "en-US",
    }:
        raise ValueError("字幕轨源语言无效")
    if track.updated_at < track.created_at:
        raise ValueError("字幕轨更新时间不能早于创建时间")
    cue_ids: set[str] = set()
    previous_end = 0.0
    for cue in track.cues:
        if not cue.id.strip() or not cue.text.strip():
            raise ValueError("字幕条目缺少稳定标识或文字")
        if cue.id in cue_ids:
            raise ValueError("字幕轨包含重复条目标识")
        cue_ids.add(cue.id)
        if (
            not math.isfinite(cue.start_seconds)
            or not math.isfinite(cue.end_seconds)
            or cue.start_seconds < 0
            or cue.end_seconds <= cue.start_seconds
            or cue.start_seconds < previous_end - 0.001
        ):
            raise ValueError("字幕条目时间无效或相互重叠")
        if any(index <= 0 or index >= len(cue.text) for index in cue.line_breaks):
            raise ValueError("字幕断行位置超出文字范围")
        previous_end = cue.end_seconds
    if track.status == "valid":
        if not track.cues or not track.artifact_ref or not track.transform_id:
            raise ValueError("valid 字幕轨必须有条目、产物和变换证据")
        validate_logical_reference(track.artifact_ref, "字幕产物引用")
        if track.failure_reason or track.impact or track.repair_action:
            raise ValueError("valid 字幕轨不能包含失败终态")
    elif track.status in {"failed", "unavailable"}:
        if not all(
            (value or "").strip()
            for value in (track.failure_reason, track.impact, track.repair_action)
        ):
            raise ValueError("失败或不可用字幕轨必须记录原因、影响和修复动作")
        if track.artifact_ref is not None or track.transform_id is not None:
            raise ValueError("失败或不可用字幕轨不能登记成功证据")
    elif track.status != "superseded":
        raise ValueError("字幕轨状态无效")


class SubtitleTrackRecordStore:
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


    def create_subtitle_track(
        self, track: SubtitleTrack, *, connection: sqlite3.Connection | None = None,
    ) -> SubtitleTrack:
        validate_subtitle_track(track)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            require_creation_task(conn, track.workspace_id, track.task_id)
            if not self._timeline_belongs(conn, track.workspace_id, track.task_id, track.timeline_version_id):
                raise ValueError("字幕轨必须关联当前任务的时间线版本")
            try:
                conn.execute(
                    """
                    INSERT INTO subtitle_tracks
                        (id, workspace_id, task_id, timeline_version_id, language,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        track.id,
                        track.workspace_id,
                        track.task_id,
                        track.timeline_version_id,
                        track.language,
                        _json_payload(track),
                        track.created_at.isoformat(),
                        track.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("同一时间线版本的字幕轨已存在") from exc
        return self.get_subtitle_track(track.workspace_id, track.task_id, track.id, connection=connection)

    def get_subtitle_track(
        self, workspace_id: str, task_id: str, track_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> SubtitleTrack:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM subtitle_tracks
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, track_id),
            ).fetchone()
        if row is None:
            raise KeyError(track_id)
        return self.subtitle_track_from_row(row)

    def list_subtitle_tracks(
        self,
        workspace_id: str,
        task_id: str,
        *,
        timeline_version_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[SubtitleTrack]:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            require_creation_task(conn, workspace_id, task_id)
            if timeline_version_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM subtitle_tracks
                    WHERE workspace_id = ? AND task_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM subtitle_tracks
                    WHERE workspace_id = ? AND task_id = ? AND timeline_version_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id, timeline_version_id),
                ).fetchall()
        return [self.subtitle_track_from_row(row) for row in rows]

    def subtitle_track_from_row(self, row: sqlite3.Row) -> SubtitleTrack:
        payload = self._decode_payload(str(row["payload_json"]))
        raw_cues = payload.pop("cues", [])
        return SubtitleTrack(
            **_with_datetime(payload, "created_at", "updated_at"),
            cues=[
                SubtitleCue(**cue)
                for cue in raw_cues
                if isinstance(cue, dict)
            ],
        )
