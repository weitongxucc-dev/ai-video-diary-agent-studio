"""WIL-108: persisted checks of a task's registered current render.

Consumes WIL-119 without changing its findings or unavailable semantics. A check
is bound to both the timeline version and immutable output content; an older
background completion cannot replace a newer request.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.montage import CreationTaskMontagePlan
from app.quality_checks import QualityCheckError, inspect_media_quality, quality_report_evidence


def compute_render_ref(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class QualityCheckRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creation_task_quality_checks (
                    workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    check_id TEXT NOT NULL, timeline_version_id TEXT,
                    source_ref TEXT, render_ref TEXT, check_state TEXT NOT NULL,
                    result_status TEXT, evidence TEXT NOT NULL DEFAULT '{}',
                    error TEXT, checked_at TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, task_id)
                )
            """)
            # The GUI's current row is intentionally replaceable. Agent history
            # needs each check's original version/hash, including late finishes.
            conn.execute("""CREATE TABLE IF NOT EXISTS creation_task_quality_history (
                workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
                check_id TEXT NOT NULL, timeline_version_id TEXT,
                source_ref TEXT, render_ref TEXT, check_state TEXT NOT NULL,
                result_status TEXT, evidence TEXT NOT NULL DEFAULT '{}',
                error TEXT, checked_at TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id, task_id, check_id))""")
            conn.execute("""INSERT OR IGNORE INTO creation_task_quality_history
                SELECT * FROM creation_task_quality_checks""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def begin(self, workspace_id: str, task_id: str, timeline_version_id: str | None,
              source_ref: str | None, render_ref: str | None) -> str:
        check_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO creation_task_quality_checks
                (workspace_id, task_id, check_id, timeline_version_id, source_ref,
                 render_ref, check_state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'checking', ?)
                ON CONFLICT(workspace_id, task_id) DO UPDATE SET
                    check_id=excluded.check_id, timeline_version_id=excluded.timeline_version_id,
                    source_ref=excluded.source_ref, render_ref=excluded.render_ref,
                    check_state='checking', result_status=NULL, evidence='{}',
                    error=NULL, checked_at=NULL, updated_at=excluded.updated_at
            """, (workspace_id, task_id, check_id, timeline_version_id, source_ref, render_ref, now))
            conn.execute("""INSERT INTO creation_task_quality_history
                (workspace_id,task_id,check_id,timeline_version_id,source_ref,render_ref,check_state,updated_at)
                VALUES (?,?,?,?,?,?,'checking',?)""",
                (workspace_id, task_id, check_id, timeline_version_id, source_ref, render_ref, now))
        return check_id

    def finish(self, workspace_id: str, task_id: str, check_id: str, *,
               evidence: dict | None = None, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                UPDATE creation_task_quality_checks SET check_state=?, result_status=?,
                    evidence=?, error=?, checked_at=?, updated_at=?
                WHERE workspace_id=? AND task_id=? AND check_id=?
            """, ("failed" if error is not None else "done",
                  None if error is not None else evidence["status"],
                  json.dumps(evidence or {}, ensure_ascii=False), error, now, now,
                  workspace_id, task_id, check_id))
            conn.execute("""UPDATE creation_task_quality_history SET check_state=?,result_status=?,
                evidence=?,error=?,checked_at=?,updated_at=?
                WHERE workspace_id=? AND task_id=? AND check_id=?""",
                ("failed" if error is not None else "done", None if error is not None else evidence["status"],
                 json.dumps(evidence or {}, ensure_ascii=False), error, now, now, workspace_id, task_id, check_id))

    def get(self, workspace_id: str, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT * FROM creation_task_quality_checks WHERE workspace_id=? AND task_id=?
            """, (workspace_id, task_id)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result["evidence"])
        return result

    def history(self, workspace_id: str, task_id: str, *, limit: int = 5) -> list[dict]:
        if not 1 <= limit <= 20:
            raise ValueError("检查历史最多读取 20 条")
        with self._connect() as conn:
            rows = conn.execute("""SELECT * FROM creation_task_quality_history
                WHERE workspace_id=? AND task_id=? ORDER BY updated_at DESC,check_id DESC LIMIT ?""",
                (workspace_id, task_id, limit)).fetchall()
        return [{**dict(row), "evidence": json.loads(row["evidence"])} for row in rows]

    def recover_interrupted(self) -> None:
        """Called once on service startup, before new inspection requests arrive."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                UPDATE creation_task_quality_checks SET check_state='failed',
                    result_status=NULL, evidence='{}',
                    error='服务中断，检查未完成，请重新检查当前成片。',
                    checked_at=?, updated_at=? WHERE check_state='checking'
            """, (now, now))
            conn.execute("""UPDATE creation_task_quality_history SET check_state='failed',
                result_status=NULL,evidence='{}',error='服务中断，检查未完成，请重新检查当前成片。',
                checked_at=?,updated_at=? WHERE check_state='checking'""", (now, now))


def _inspect_and_finalize(repo: QualityCheckRepository, workspace_id: str, task_id: str,
                          check_id: str, path: Path, render_ref: str) -> None:
    try:
        report = inspect_media_quality(path)
        # Storage is immutable. Reject a deleted/replaced file during detection.
        if compute_render_ref(path) != render_ref:
            raise QualityCheckError("检查期间成片内容已变化，请重新检查当前成片。")
    except (QualityCheckError, OSError, ValueError) as exc:
        repo.finish(workspace_id, task_id, check_id, error=str(exc))
        return
    repo.finish(workspace_id, task_id, check_id, evidence=quality_report_evidence(report))


def start_final_video_quality_check(repo: QualityCheckRepository, workspace_id: str,
                                    task_id: str, timeline_version_id: str | None,
                                    source_ref: str, path: Path, render_ref: str) -> str:
    check_id = repo.begin(workspace_id, task_id, timeline_version_id, source_ref, render_ref)
    threading.Thread(target=_inspect_and_finalize,
                     args=(repo, workspace_id, task_id, check_id, path, render_ref),
                     name=f"quality-{check_id}", daemon=True).start()
    return check_id


def final_quality_check_payload(record: dict | None, plan: CreationTaskMontagePlan | None,
                                *, timeline_version_id: str | None,
                                current_render_ref: str | None) -> dict:
    payload = {
        "check_state": "none", "result_status": None, "stale": False,
        "check_id": None, "timeline_version_id": timeline_version_id,
        "render_ref": None, "checked_at": None, "error": None,
        "findings": [], "unavailable": [], "clipping_summary": None,
        "validation_scope": "deterministic_media_errors_only",
    }
    if record is None:
        return payload
    stale = (record["timeline_version_id"] != timeline_version_id
             or record["source_ref"] != (plan.output_path if plan else None)
             or record["render_ref"] != current_render_ref
             or (record["check_state"] == "done" and current_render_ref is None))
    payload.update({key: record[key] for key in
                    ("check_state", "check_id", "render_ref", "checked_at", "error")})
    payload["stale"] = stale
    if stale:
        payload["error"] = None
    if stale or record["check_state"] != "done":
        return payload
    evidence = record["evidence"]
    payload["result_status"] = record["result_status"]
    payload["findings"] = [
        {**finding, "clip_ids": [clip.id for clip in plan.clips
                                  if finding["start_seconds"] < clip.timeline_end_seconds
                                  and finding["end_seconds"] > clip.timeline_start_seconds]}
        for finding in evidence["findings"]
    ]
    payload["unavailable"] = evidence["unavailable"]
    payload["clipping_summary"] = evidence.get("clipping_summary")
    return payload
