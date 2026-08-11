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


def get_creator_profile(database_path: str | Path) -> CreatorProfile | None:
    """Return the singleton profile or None if not yet created."""
    _ensure_table(database_path)
    db = Path(database_path).expanduser()
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT payload FROM creator_profile ORDER BY updated_at DESC LIMIT 1"
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
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse creator_profile row: %s", exc)
        return None


def upsert_creator_profile(
    profile: CreatorProfile,
    database_path: str | Path,
) -> CreatorProfile:
    """Insert or replace the singleton profile."""
    _ensure_table(database_path)
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
            "INSERT OR REPLACE INTO creator_profile (id, payload, updated_at) VALUES (?, ?, ?)",
            (profile.id, payload, profile.updated_at),
        )
    return profile


def get_or_create_profile(database_path: str | Path) -> CreatorProfile:
    """Return the existing profile or create a default empty one."""
    existing = get_creator_profile(database_path)
    if existing is not None:
        return existing
    profile = CreatorProfile(id=f"cp_{uuid4().hex[:8]}")
    return upsert_creator_profile(profile, database_path)
