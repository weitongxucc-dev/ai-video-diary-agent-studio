"""平台授权账号的持久化（自举 SQLite，仿 local_members 的 _open 模式）。

token 落库但**永不进日志**：本模块不打印任何 token，模型 repr 一律脱敏。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS platform_auth_accounts (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    refresh_expires_at TEXT,
    bound_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revoked_at TEXT,
    PRIMARY KEY (workspace_id, user_id, platform)
);
CREATE TABLE IF NOT EXISTS platform_auth_drafts (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    hashtags TEXT NOT NULL,
    video_ref TEXT NOT NULL,
    mocked INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id, platform, draft_id)
);
"""


def _redact(value: str) -> str:
    if value is None:
        return "None"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


@dataclass(frozen=True)
class PlatformAccount:
    workspace_id: str
    user_id: str
    platform: str
    account_ref: str
    access_token: str
    refresh_token: str
    expires_at: str
    refresh_expires_at: str | None
    bound_at: str
    updated_at: str
    revoked_at: str | None

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self) -> str:  # 脱敏：任何日志/报告路径都拿不到明文 token
        return (
            f"PlatformAccount(workspace_id={self.workspace_id!r}, user_id={self.user_id!r}, "
            f"platform={self.platform!r}, account_ref={self.account_ref!r}, "
            f"access_token={_redact(self.access_token)}, refresh_token={_redact(self.refresh_token)}, "
            f"revoked_at={self.revoked_at!r})"
        )


def _open(database_path: str | Path) -> sqlite3.Connection:
    db = Path(database_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(TABLE_DDL)
    return conn


def _account_from_row(row: sqlite3.Row) -> PlatformAccount:
    return PlatformAccount(
        workspace_id=str(row["workspace_id"]),
        user_id=str(row["user_id"]),
        platform=str(row["platform"]),
        account_ref=str(row["account_ref"]),
        access_token=str(row["access_token"]),
        refresh_token=str(row["refresh_token"]),
        expires_at=str(row["expires_at"]),
        refresh_expires_at=str(row["refresh_expires_at"]) if row["refresh_expires_at"] is not None else None,
        bound_at=str(row["bound_at"]),
        updated_at=str(row["updated_at"]),
        revoked_at=str(row["revoked_at"]) if row["revoked_at"] is not None else None,
    )
