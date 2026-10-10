"""Verified browser-account metadata; never persists platform credentials.

The prior token-bearing tables are intentionally not opened, migrated, or
read by this path. Browser cookies stay in the isolated Chrome profile only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS platform_browser_accounts (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id, platform)
);
"""


@dataclass(frozen=True)
class PlatformAccount:
    workspace_id: str
    user_id: str
    platform: str
    account_ref: str
    profile_key: str
    verified_at: str


def _open(database_path: str | Path) -> sqlite3.Connection:
    db = Path(database_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(TABLE_DDL)
    return conn


def _account_from_row(row: sqlite3.Row) -> PlatformAccount:
    return PlatformAccount(
        workspace_id=str(row["workspace_id"]), user_id=str(row["user_id"]),
        platform=str(row["platform"]), account_ref=str(row["account_ref"]),
        profile_key=str(row["profile_key"]), verified_at=str(row["verified_at"]),
    )
