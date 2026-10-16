"""Discovery metadata in the application database; never writes material assets.

The composition/migration owner applies DISCOVERY_SCHEMA. Importing/constructing
this store neither provisions a database nor starts filesystem work.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

DISCOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS material_discovery_sessions (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
    updated_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS discovery_session_owner
    ON material_discovery_sessions(workspace_id, owner_user_id, updated_at);
CREATE TABLE IF NOT EXISTS material_discovery_candidates (
    session_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(session_id, id)
);
CREATE INDEX IF NOT EXISTS discovery_candidate_identity
    ON material_discovery_candidates(id, session_id);
CREATE TABLE IF NOT EXISTS material_discovery_receipts (
    workspace_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, digest TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(workspace_id, owner_user_id, digest)
);
CREATE TABLE IF NOT EXISTS material_discovery_preferences (
    workspace_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, exclusions TEXT NOT NULL,
    PRIMARY KEY(workspace_id, owner_user_id)
);
"""


class DiscoveryStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]):
        self._connect = connection_factory

    def session(self, session_id: str, workspace_id: str, owner_user_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM material_discovery_sessions WHERE id=? AND workspace_id=? AND owner_user_id=?", (session_id, workspace_id, owner_user_id)).fetchone()
            preferences = conn.execute("SELECT exclusions FROM material_discovery_preferences WHERE workspace_id=? AND owner_user_id=?", (workspace_id, owner_user_id)).fetchone()
        if row is None:
            raise KeyError("扫描任务不存在")
        session = json.loads(row[0])
        # New exclusions revoke subsequent reads even in an older open tab or
        # an already selected copy. Historical permissions never widen here.
        if preferences:
            session["exclusions"] = sorted(set(session["exclusions"]) | set(json.loads(preferences[0])))
        return session

    def latest(self, workspace_id: str, owner_user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM material_discovery_sessions WHERE workspace_id=? AND owner_user_id=? ORDER BY updated_at DESC LIMIT 1", (workspace_id, owner_user_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_session(self, session: dict, candidates: list[dict] = ()) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO material_discovery_sessions VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, payload=excluded.payload", (session["id"], session["workspace_id"], session["owner_user_id"], session["updated_at"], json.dumps(session)))
            for candidate in candidates:
                prior = conn.execute(
                    "SELECT c.payload FROM material_discovery_candidates c JOIN material_discovery_sessions s ON s.id=c.session_id "
                    "WHERE c.id=? AND s.workspace_id=? AND s.owner_user_id=? AND json_extract(c.payload, '$.state')='received' LIMIT 1",
                    (candidate["id"], session["workspace_id"], session["owner_user_id"]),
                ).fetchone()
                prior_candidate = json.loads(prior[0]) if prior else {}
                candidate["duplicate_asset_id"] = prior_candidate.get("asset_id")
                if prior_candidate.get("sha256"):
                    candidate["sha256"] = prior_candidate["sha256"]
                conn.execute("INSERT OR IGNORE INTO material_discovery_candidates VALUES (?, ?, ?)", (session["id"], candidate["id"], json.dumps(candidate)))

    def candidates(self, session_id: str, *, offset: int = 0, limit: int = 100) -> tuple[list[dict], int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM material_discovery_candidates WHERE session_id=? ORDER BY id LIMIT ? OFFSET ?", (session_id, limit, offset)).fetchall()
            count = conn.execute("SELECT COUNT(*) FROM material_discovery_candidates WHERE session_id=?", (session_id,)).fetchone()[0]
        return [json.loads(row[0]) for row in rows], count

    def candidate(self, session_id: str, candidate_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM material_discovery_candidates WHERE session_id=? AND id=?", (session_id, candidate_id)).fetchone()
        if row is None:
            raise KeyError("扫描候选不存在")
        return json.loads(row[0])

    def save_candidate(self, session_id: str, candidate: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE material_discovery_candidates SET payload=? WHERE session_id=? AND id=?", (json.dumps(candidate), session_id, candidate["id"]))

    def receipt(self, workspace_id: str, owner_user_id: str, digest: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM material_discovery_receipts WHERE workspace_id=? AND owner_user_id=? AND digest=?", (workspace_id, owner_user_id, digest)).fetchone()
        return json.loads(row[0]) if row else None

    def save_receipt(self, workspace_id: str, owner_user_id: str, digest: str, receipt: dict) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO material_discovery_receipts VALUES (?, ?, ?, ?) ON CONFLICT(workspace_id,owner_user_id,digest) DO UPDATE SET payload=excluded.payload", (workspace_id, owner_user_id, digest, json.dumps(receipt)))

    def exclusions(self, workspace_id: str, owner_user_id: str) -> list[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT exclusions FROM material_discovery_preferences WHERE workspace_id=? AND owner_user_id=?", (workspace_id, owner_user_id)).fetchone()
        return json.loads(row[0]) if row else []

    def save_exclusions(self, workspace_id: str, owner_user_id: str, exclusions: list[str]) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO material_discovery_preferences VALUES (?, ?, ?) ON CONFLICT(workspace_id,owner_user_id) DO UPDATE SET exclusions=excluded.exclusions", (workspace_id, owner_user_id, json.dumps(exclusions)))
