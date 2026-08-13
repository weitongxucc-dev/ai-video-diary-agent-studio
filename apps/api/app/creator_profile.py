"""Creator Profile — learn and persist the creator's expression habits."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

LEGACY_WORKSPACE_ID = "local"


def _resolve_workspace_id(workspace_id: str | None) -> str:
    if workspace_id:
        return workspace_id
    # Avoid a hard import at module load (keeps the module importable in
    # isolation); repository.active_workspace_id resolves the current scope.
    from app.repository import active_workspace_id

    return active_workspace_id()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CreatorProfile:
    id: str
    common_expressions: list[str] = field(default_factory=list)
    banned_words: list[str] = field(default_factory=list)
    style_preferences: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    workspace_id: str = LEGACY_WORKSPACE_ID


def _ensure_table(database_path: str | Path) -> None:
    db = Path(database_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_profile (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _migrate_creator_profile_schema(conn)


def _migrate_creator_profile_schema(conn: sqlite3.Connection) -> None:
    """Add workspace partitioning to the creator_profile table.

    Legacy rows are assigned the reserved 'local' workspace so pre-auth data
    is never visible to an authenticated account.
    """
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(creator_profile)").fetchall()
    }
    if "workspace_id" in columns:
        return
    conn.execute(
        "ALTER TABLE creator_profile "
        "ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'local'"
    )


def get_creator_profile(
    database_path: str | Path,
    workspace_id: str | None = None,
) -> CreatorProfile | None:
    """Return the profile for a workspace or None if not yet created."""
    ws = _resolve_workspace_id(workspace_id)
    _ensure_table(database_path)
    db = Path(database_path).expanduser()
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            """
            SELECT payload FROM creator_profile
            WHERE workspace_id = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (ws,),
        ).fetchall()
    if not rows:
        return None
    try:
        data = json.loads(rows[0][0])
        return CreatorProfile(
            id=data["id"],
            common_expressions=data.get("common_expressions", []),
            banned_words=data.get("banned_words", []),
            style_preferences=data.get("style_preferences", {}),
            created_at=data.get("created_at", _utc_now_iso()),
            updated_at=data.get("updated_at", _utc_now_iso()),
            workspace_id=ws,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse creator_profile row: %s", exc)
        return None


def upsert_creator_profile(
    profile: CreatorProfile,
    database_path: str | Path,
    workspace_id: str | None = None,
) -> CreatorProfile:
    """Insert or replace the profile for a workspace."""
    ws = _resolve_workspace_id(workspace_id)
    _ensure_table(database_path)
    profile.workspace_id = ws
    profile.updated_at = _utc_now_iso()
    payload = json.dumps(
        {
            "id": profile.id,
            "common_expressions": profile.common_expressions,
            "banned_words": profile.banned_words,
            "style_preferences": profile.style_preferences,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        },
        ensure_ascii=False,
    )
    db = Path(database_path).expanduser()
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO creator_profile
                (id, workspace_id, payload, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (profile.id, ws, payload, profile.updated_at),
        )
    return profile


def get_or_create_profile(
    database_path: str | Path,
    workspace_id: str | None = None,
) -> CreatorProfile:
    """Return the existing workspace profile or create a default empty one."""
    ws = _resolve_workspace_id(workspace_id)
    existing = get_creator_profile(database_path, workspace_id=ws)
    if existing is not None:
        return existing
    profile = CreatorProfile(
        id=f"cp_{uuid4().hex[:8]}",
        workspace_id=ws,
    )
    return upsert_creator_profile(
        profile,
        database_path,
        workspace_id=ws,
    )
