"""Existing immutable version payload SQL shared by media profile aggregates."""

from __future__ import annotations

import json
import sqlite3

from app.models import utc_now


def _save_versioned_payload(
    conn: sqlite3.Connection,
    *,
    table: str,
    workspace_id: str,
    record_id: str,
    version: str,
    payload: dict[str, object],
) -> None:
    if not workspace_id.strip() or not record_id.strip() or not version.strip():
        raise ValueError("版本化媒体对象必须包含工作区、ID 和版本")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row = conn.execute(
        f"SELECT payload FROM {table} WHERE workspace_id = ? AND id = ? AND version = ?",
        (workspace_id, record_id, version),
    ).fetchone()
    if row is not None:
        if str(row["payload"]) != encoded:
            raise ValueError("语义变更必须使用新版本")
        return
    conn.execute(
        f"INSERT INTO {table} (workspace_id, id, version, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (workspace_id, record_id, version, encoded, utc_now().isoformat()),
    )


def _list_versioned_payloads(
    conn: sqlite3.Connection,
    *,
    table: str,
    workspace_id: str,
    record_id: str | None = None,
    current_only: bool = False,
) -> list[dict[str, object]]:
    if record_id is None:
        rows = conn.execute(
            f"SELECT id, payload FROM {table} WHERE workspace_id = ? ORDER BY id, created_at DESC, rowid DESC",
            (workspace_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT id, payload FROM {table} WHERE workspace_id = ? AND id = ? ORDER BY created_at DESC, rowid DESC",
            (workspace_id, record_id),
        ).fetchall()
    seen: set[str] = set()
    payloads: list[dict[str, object]] = []
    for row in rows:
        item_id = str(row["id"])
        if current_only and item_id in seen:
            continue
        seen.add(item_id)
        payloads.append(json.loads(str(row["payload"])))
    return payloads


def _versioned_row(
    conn: sqlite3.Connection,
    *,
    table: str,
    workspace_id: str,
    record_id: str,
    version: str | None,
) -> sqlite3.Row:
    if version is None:
        row = conn.execute(
            f"SELECT payload FROM {table} WHERE workspace_id = ? AND id = ? ORDER BY created_at DESC LIMIT 1",
            (workspace_id, record_id),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT payload FROM {table} WHERE workspace_id = ? AND id = ? AND version = ?",
            (workspace_id, record_id, version),
        ).fetchone()
    if row is None:
        raise KeyError(record_id)
    return row
