"""Complete creation input reads using only the caller's SQLite connection."""
from __future__ import annotations
from datetime import datetime
import json
import sqlite3
from app.creation.contracts import CreationTask, NarrationSegment
from app.persistence_json import _with_datetime


def require_creation_task(
    conn: sqlite3.Connection, workspace_id: str, task_id: str
) -> CreationTask:
    row = conn.execute(
        "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
        (workspace_id, task_id),
    ).fetchone()
    if row is None:
        raise KeyError(task_id)
    return creation_task_from_payload(
        json.loads(str(row["payload"]))
    )


def creation_task_from_payload(data: dict[str, object]) -> CreationTask:
    return CreationTask(**_with_datetime(data, "created_at", "updated_at"))


def narration_segment_from_row(row: sqlite3.Row) -> NarrationSegment:
    return NarrationSegment(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        task_id=str(row["task_id"]),
        script_id=str(row["script_id"]),
        source_section_id=str(row["source_section_id"]),
        text=str(row["text"]),
        audio_reference=(
            str(row["audio_reference"])
            if row["audio_reference"] is not None
            else None
        ),
        duration_seconds=(
            float(row["duration_seconds"])
            if row["duration_seconds"] is not None
            else None
        ),
        sample_rate=(
            int(row["sample_rate"]) if row["sample_rate"] is not None else None
        ),
        model_id=str(row["model_id"]) if row["model_id"] is not None else None,
        voice_profile_id=(str(row["voice_profile_id"])
                          if row["voice_profile_id"] is not None else None),
        voice_version=str(row["voice_version"]) if row["voice_version"] is not None else None,
        language=str(row["language"]),
        pronunciation_version=str(row["pronunciation_version"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        source_kind=str(row["source_kind"]),  # type: ignore[arg-type]
        source_artifact_ref=(str(row["source_artifact_ref"])
                             if row["source_artifact_ref"] is not None else None),
        source_sha256=(str(row["source_sha256"])
                       if row["source_sha256"] is not None else None),
        source_script_digest=(str(row["source_script_digest"])
                              if row["source_script_digest"] is not None else None),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def narration_segment_row(
    conn: sqlite3.Connection, workspace_id: str, task_id: str, segment_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
                SELECT * FROM narration_segments
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
        (workspace_id, task_id, segment_id),
    ).fetchone()


def narration_segment_rows(
    conn: sqlite3.Connection, workspace_id: str, task_id: str, script_id: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
                SELECT * FROM narration_segments
                WHERE workspace_id = ? AND task_id = ? AND script_id = ?
                ORDER BY created_at ASC, id ASC
                """,
        (workspace_id, task_id, script_id),
    ).fetchall()
