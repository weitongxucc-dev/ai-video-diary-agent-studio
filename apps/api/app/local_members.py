"""Local multi-member roster backed by the shared SQLite database.

Local-first and auth-decoupled by design: members live in the reserved local
workspace and require no login. This is step one of the members/space feature —
a deliberately simple owner/member table plus CRUD and a one-time local join
code. No Supabase, no network.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.database_migrations import LOCAL_MEMBERS_SEED_DDL, LOCAL_MEMBERS_TABLE_DDL


class LocalMembersError(RuntimeError):
    """A member command failed; ``kind`` maps to an HTTP status in main.py."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class LocalMember:
    member_id: str
    name: str
    role: str
    created_at: str


_VALID_ROLES = ("owner", "member")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _open(database_path: str | Path) -> sqlite3.Connection:
    db = Path(database_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Self-provision the members table so a fresh (test) database works without
    # running the non-idempotent full migration chain. Idempotent on an existing
    # database that already applied migration 11.
    conn.executescript(LOCAL_MEMBERS_TABLE_DDL + "\n" + LOCAL_MEMBERS_SEED_DDL)
    return conn


def _validate_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise LocalMembersError("invalid", "角色必须是 owner 或 member")


def _require_remaining_owner(conn: sqlite3.Connection) -> None:
    count = conn.execute(
        "SELECT COUNT(*) FROM local_members WHERE role = 'owner'"
    ).fetchone()[0]
    if count <= 1:
        raise LocalMembersError("conflict", "至少保留一个 owner")


def _member_from_row(row: sqlite3.Row) -> LocalMember:
    return LocalMember(
        member_id=row["member_id"],
        name=row["name"],
        role=row["role"],
        created_at=row["created_at"],
    )


class LocalMemberService:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser()
        # One-time join codes are deliberately in-memory: they are cheap, local,
        # and reset when the process restarts.
        self._invite_codes: set[str] = set()

    def list_members(self) -> list[LocalMember]:
        with _open(self._database_path) as conn:
            rows = conn.execute(
                "SELECT member_id, name, role, created_at FROM local_members "
                "ORDER BY created_at ASC, member_id ASC"
            ).fetchall()
        return [_member_from_row(row) for row in rows]

    def add_member(self, name: str, role: str = "member") -> LocalMember:
        cleaned = name.strip()
        if not cleaned:
            raise LocalMembersError("invalid", "成员名不能为空")
        _validate_role(role)
        member = LocalMember(
            member_id=str(uuid4()),
            name=cleaned,
            role=role,
            created_at=_utc_now_iso(),
        )
        with _open(self._database_path) as conn:
            duplicate = conn.execute(
                "SELECT 1 FROM local_members WHERE name = ?", (cleaned,)
            ).fetchone()
            if duplicate is not None:
                raise LocalMembersError("conflict", "成员名已存在")
            conn.execute(
                "INSERT INTO local_members (member_id, name, role, created_at) "
                "VALUES (?, ?, ?, ?)",
                (member.member_id, member.name, member.role, member.created_at),
            )
        return member

    def remove_member(self, member_id: str) -> None:
        with _open(self._database_path) as conn:
            row = conn.execute(
                "SELECT role FROM local_members WHERE member_id = ?", (member_id,)
            ).fetchone()
            if row is None:
                raise LocalMembersError("not_found", "成员不存在")
            if row["role"] == "owner":
                _require_remaining_owner(conn)
            conn.execute("DELETE FROM local_members WHERE member_id = ?", (member_id,))

    def set_role(self, member_id: str, role: str) -> LocalMember:
        _validate_role(role)
        with _open(self._database_path) as conn:
            row = conn.execute(
                "SELECT role FROM local_members WHERE member_id = ?", (member_id,)
            ).fetchone()
            if row is None:
                raise LocalMembersError("not_found", "成员不存在")
            if row["role"] == "owner" and role == "member":
                _require_remaining_owner(conn)
            conn.execute(
                "UPDATE local_members SET role = ? WHERE member_id = ?",
                (role, member_id),
            )
            updated = conn.execute(
                "SELECT member_id, name, role, created_at FROM local_members "
                "WHERE member_id = ?",
                (member_id,),
            ).fetchone()
        assert updated is not None
        return _member_from_row(updated)

    def create_invite_code(self) -> str:
        code = secrets.token_urlsafe(12)
        self._invite_codes.add(code)
        return code

    def join(self, code: str, name: str) -> LocalMember:
        if code not in self._invite_codes:
            raise LocalMembersError("invalid", "邀请码无效或已使用")
        self._invite_codes.discard(code)
        return self.add_member(name, "member")
