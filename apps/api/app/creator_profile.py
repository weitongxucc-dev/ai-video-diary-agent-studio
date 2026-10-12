"""Persist explicit creator preferences in their workspace and member scope."""

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


class CreatorProfileConflict(ValueError):
    """The caller's profile revision or ownership no longer matches storage."""


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
    owner_user_id: str | None = None
    revision: int = 0
    last_preference_change: dict[str, object] = field(default_factory=dict)
    preference_sources: dict[str, dict[str, str]] = field(default_factory=dict)


def _ensure_table(database_path: str | Path) -> None:
    db = Path(database_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("BEGIN IMMEDIATE")
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
    """Preserve historical rows without assigning them to a new member."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(creator_profile)").fetchall()
    }
    if "workspace_id" not in columns:
        conn.execute(
            "ALTER TABLE creator_profile "
            "ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'local'"
        )
    if "owner_user_id" not in columns:
        conn.execute("ALTER TABLE creator_profile ADD COLUMN owner_user_id TEXT")
    if "revision" not in columns:
        conn.execute("ALTER TABLE creator_profile ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    # Historical workspaces can have several unowned rows. Only new member
    # scopes are unique; migration must not delete or claim historical data.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS creator_profile_member_scope "
        "ON creator_profile(workspace_id, owner_user_id) WHERE owner_user_id IS NOT NULL"
    )


def _check_owner(owner_user_id: str | None) -> None:
    if owner_user_id is not None and not owner_user_id.strip():
        raise ValueError("创作者档案成员身份不能为空")


def _scope_row(conn: sqlite3.Connection, workspace_id: str, owner_user_id: str | None):
    return conn.execute(
        "SELECT * FROM creator_profile WHERE workspace_id=? AND owner_user_id IS ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (workspace_id, owner_user_id),
    ).fetchone()


def _profile_from_row(row: sqlite3.Row) -> CreatorProfile:
    data = json.loads(row["payload"])
    return CreatorProfile(
        id=row["id"],
        common_expressions=data.get("common_expressions", []),
        banned_words=data.get("banned_words", []),
        style_preferences=data.get("style_preferences", {}),
        created_at=data.get("created_at", _utc_now_iso()),
        updated_at=row["updated_at"],
        workspace_id=row["workspace_id"],
        owner_user_id=row["owner_user_id"],
        revision=row["revision"],
        last_preference_change=data.get("last_preference_change", {}),
        preference_sources=data.get("preference_sources", {}),
    )


def _profile_payload(profile: CreatorProfile) -> str:
    return json.dumps(
        {
            "id": profile.id,
            "common_expressions": profile.common_expressions,
            "banned_words": profile.banned_words,
            "style_preferences": profile.style_preferences,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            "last_preference_change": profile.last_preference_change,
            "preference_sources": profile.preference_sources,
        },
        ensure_ascii=False,
    )


def _insert_profile(conn: sqlite3.Connection, profile: CreatorProfile) -> None:
    conn.execute(
        "INSERT INTO creator_profile (id,workspace_id,owner_user_id,revision,payload,updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (profile.id, profile.workspace_id, profile.owner_user_id, profile.revision,
         _profile_payload(profile), profile.updated_at),
    )


def _check_revision(actual: int, expected: int | None) -> None:
    if expected is not None and actual != expected:
        raise CreatorProfileConflict("创作者档案已变化，请重新读取后保存")


def get_creator_profile(
    database_path: str | Path,
    workspace_id: str | None = None,
    *,
    owner_user_id: str | None = None,
) -> CreatorProfile | None:
    """Read the member's profile; None reads only historical unowned rows."""
    _check_owner(owner_user_id)
    ws = _resolve_workspace_id(workspace_id)
    _ensure_table(database_path)
    db = Path(database_path).expanduser()
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = _scope_row(conn, ws, owner_user_id)
    if row is None:
        return None
    try:
        return _profile_from_row(row)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse creator_profile row: %s", exc)
        return None


def upsert_creator_profile(
    profile: CreatorProfile,
    database_path: str | Path,
    workspace_id: str | None = None,
    *,
    owner_user_id: str | None = None,
    expected_revision: int | None = None,
) -> CreatorProfile:
    """Save one profile atomically without replacing another member's row."""
    _check_owner(owner_user_id)
    ws = _resolve_workspace_id(workspace_id)
    _ensure_table(database_path)
    db = Path(database_path).expanduser()
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM creator_profile WHERE id=?", (profile.id,)).fetchone()
        if row is not None and (row["workspace_id"] != ws or row["owner_user_id"] != owner_user_id):
            raise CreatorProfileConflict("创作者档案不属于当前成员和工作区")
        if owner_user_id is not None:
            current = _scope_row(conn, ws, owner_user_id)
            if current is not None and current["id"] != profile.id:
                raise CreatorProfileConflict("当前成员已有创作者档案，请重新读取后保存")
        actual_revision = row["revision"] if row is not None else 0
        _check_revision(actual_revision, expected_revision)
        previous = _profile_from_row(row) if row is not None else None
        # Manual edits retain assistant provenance only for unchanged values;
        # provenance supplied by the editing client is not an authority.
        profile.preference_sources = {
            key: source for key, source in previous.preference_sources.items()
            if key in previous.style_preferences and key in profile.style_preferences
            and previous.style_preferences[key] == profile.style_preferences[key]
        } if previous is not None else {}
        profile.workspace_id = ws
        profile.owner_user_id = owner_user_id
        profile.revision = actual_revision + 1
        profile.updated_at = _utc_now_iso()
        # A manual profile save is a different action. Its new revision must
        # not replay an earlier assistant write using that write's marker.
        profile.last_preference_change = {}
        if row is None:
            _insert_profile(conn, profile)
        else:
            conn.execute(
                "UPDATE creator_profile SET payload=?,updated_at=?,revision=? WHERE id=?",
                (_profile_payload(profile), profile.updated_at, profile.revision, profile.id),
            )
    return profile


def get_or_create_profile(
    database_path: str | Path,
    workspace_id: str | None = None,
    *,
    owner_user_id: str | None = None,
) -> CreatorProfile:
    """Return or create one empty profile under an atomic scope lock."""
    _check_owner(owner_user_id)
    ws = _resolve_workspace_id(workspace_id)
    _ensure_table(database_path)
    with sqlite3.connect(str(Path(database_path).expanduser())) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = _scope_row(conn, ws, owner_user_id)
        if row is not None:
            return _profile_from_row(row)
        profile = CreatorProfile(id=f"cp_{uuid4().hex[:8]}", workspace_id=ws, owner_user_id=owner_user_id)
        _insert_profile(conn, profile)
        return profile


def change_creator_preference(
    database_path: str | Path,
    *,
    workspace_id: str,
    owner_user_id: str,
    key: str,
    value: str | None,
    expected_revision: int,
    source_turn_id: str,
    source_quote: str,
) -> CreatorProfile:
    """Remember or forget one explicit preference, replaying only its last action."""
    _check_owner(owner_user_id)
    if owner_user_id is None or not key.strip() or not source_turn_id.strip() or not source_quote.strip():
        raise ValueError("偏好变更缺少成员、偏好名称或用户指令来源")
    if value is not None and not value.strip():
        raise ValueError("要记住的偏好值不能为空")
    ws = _resolve_workspace_id(workspace_id)
    _ensure_table(database_path)
    change = {"turn_id": source_turn_id, "key": key, "value": value, "quote": source_quote}
    with sqlite3.connect(str(Path(database_path).expanduser())) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = _scope_row(conn, ws, owner_user_id)
        profile = (_profile_from_row(row) if row is not None else
                   CreatorProfile(id=f"cp_{uuid4().hex[:8]}", workspace_id=ws, owner_user_id=owner_user_id))
        if profile.last_preference_change == change and expected_revision in {profile.revision - 1, profile.revision}:
            return profile
        _check_revision(profile.revision, expected_revision)
        if value is None:
            profile.style_preferences.pop(key, None)
            profile.preference_sources.pop(key, None)
        else:
            profile.style_preferences[key] = value
            profile.preference_sources[key] = {
                "source_turn_id": source_turn_id, "source_quote": source_quote,
            }
        profile.revision += 1
        profile.updated_at = _utc_now_iso()
        profile.last_preference_change = change
        if row is None:
            _insert_profile(conn, profile)
        else:
            conn.execute(
                "UPDATE creator_profile SET payload=?,updated_at=?,revision=? WHERE id=?",
                (_profile_payload(profile), profile.updated_at, profile.revision, profile.id),
            )
        return profile
