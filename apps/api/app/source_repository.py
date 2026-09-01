from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.sources import KnowledgeItem, SourceConnector, SourceItem, SourceRunEvidence


class SourceRepository:
    """Workspace-partitioned persistence for knowledge and external sources.

    This intentionally uses the application's existing SQLite database path.
    HTTP composition owns the repository instance; this module owns only the
    source/knowledge tables and never falls back across workspace boundaries.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def upsert_connector(self, connector: SourceConnector) -> SourceConnector:
        self._require_workspace(connector.workspace_id)
        platform = connector.platform.strip()
        protocol = connector.protocol.strip()
        account_or_rule = connector.account_or_rule.strip()
        frequency = connector.frequency.strip()
        if not all((platform, protocol, account_or_rule, frequency)):
            raise ValueError(
                "connector requires platform, protocol, account_or_rule, and frequency"
            )
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT workspace_id FROM source_connectors WHERE id = ?",
                (connector.id,),
            ).fetchone()
            if existing is not None and existing["workspace_id"] != connector.workspace_id:
                raise ValueError("connector id already belongs to another workspace")
            conn.execute(
                """
                INSERT INTO source_connectors (
                    id, workspace_id, platform, protocol, account_or_rule,
                    frequency, cursor_value, permissions, connection_status,
                    latest_run_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    platform = excluded.platform,
                    protocol = excluded.protocol,
                    account_or_rule = excluded.account_or_rule,
                    frequency = excluded.frequency,
                    cursor_value = excluded.cursor_value,
                    permissions = excluded.permissions,
                    connection_status = excluded.connection_status,
                    latest_run_id = excluded.latest_run_id,
                    updated_at = excluded.updated_at
                """,
                (
                    connector.id,
                    connector.workspace_id,
                    platform,
                    protocol,
                    account_or_rule,
                    frequency,
                    connector.cursor,
                    json.dumps(connector.permissions, ensure_ascii=False),
                    connector.connection_status,
                    connector.latest_run_id,
                    connector.created_at.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.get_connector(connector.workspace_id, connector.id)

    def get_connector(self, workspace_id: str, connector_id: str) -> SourceConnector:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_connectors WHERE id = ? AND workspace_id = ?",
                (connector_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(connector_id)
        return self._connector_from_row(row)

    def list_connectors(self, workspace_id: str) -> list[SourceConnector]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM source_connectors WHERE workspace_id = ? ORDER BY created_at",
                (workspace_id,),
            ).fetchall()
        return [self._connector_from_row(row) for row in rows]

    def save_source_items(self, workspace_id: str, items: list[SourceItem]) -> list[SourceItem]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            self._save_source_items(conn, workspace_id, items)
        return [self.get_source_item(workspace_id, item.id) for item in items]

    def get_source_item(self, workspace_id: str, item_id: str) -> SourceItem:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._source_item_from_row(row)

    def list_source_items(
        self, workspace_id: str, connector_id: str | None = None
    ) -> list[SourceItem]:
        self._require_workspace(workspace_id)
        query = "SELECT * FROM source_items WHERE workspace_id = ?"
        values: list[str] = [workspace_id]
        if connector_id:
            query += " AND connector_id = ?"
            values.append(connector_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._source_item_from_row(row) for row in rows]

    def create_knowledge_item(
        self,
        item: KnowledgeItem,
    ) -> KnowledgeItem:
        self._require_workspace(item.workspace_id)
        ownership = item.ownership.strip()
        authorization = item.authorization.strip()
        version = item.version.strip()
        if not item.title.strip() or not all((ownership, authorization, version)):
            raise ValueError(
                "knowledge item requires title, ownership, authorization, and version"
            )
        if not item.body and not item.file_reference:
            raise ValueError("knowledge item requires body or file_reference")
        if item.source_item_id:
            self.get_source_item(item.workspace_id, item.source_item_id)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT workspace_id FROM knowledge_items WHERE id = ?", (item.id,)
            ).fetchone()
            if existing is not None:
                raise ValueError("knowledge item id already exists")
            conn.execute(
                """
                INSERT INTO knowledge_items (
                    id, workspace_id, content_type, title, body, file_reference,
                    authorization, ownership, source_item_id, version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.workspace_id,
                    item.content_type,
                    item.title,
                    item.body,
                    item.file_reference,
                    authorization,
                    ownership,
                    item.source_item_id,
                    version,
                    item.created_at.isoformat(),
                ),
            )
        return self.get_knowledge_item(item.workspace_id, item.id)

    def save_source_item_to_knowledge(
        self,
        workspace_id: str,
        source_item_id: str,
        *,
        ownership: str,
        authorization: str,
        version: str = "1",
    ) -> KnowledgeItem:
        source = self.get_source_item(workspace_id, source_item_id)
        return self.create_knowledge_item(
            KnowledgeItem(
                id=f"ki_{uuid4().hex}",
                workspace_id=workspace_id,
                content_type=source.content_type,
                title=source.title,
                body=source.body,
                file_reference=source.file_reference,
                authorization=authorization,
                ownership=ownership,
                source_item_id=source.id,
                version=version,
            )
        )

    def get_knowledge_item(self, workspace_id: str, item_id: str) -> KnowledgeItem:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._knowledge_item_from_row(row)

    def list_knowledge_items(self, workspace_id: str) -> list[KnowledgeItem]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_items WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        return [self._knowledge_item_from_row(row) for row in rows]

    def record_sync_success(
        self,
        workspace_id: str,
        connector_id: str,
        items: list[SourceItem],
        cursor_after: str | None,
    ) -> SourceRunEvidence:
        connector = self.get_connector(workspace_id, connector_id)
        run = SourceRunEvidence(
            id=f"src_run_{uuid4().hex}",
            workspace_id=workspace_id,
            connector_id=connector_id,
            status="succeeded",
            started_at=_utc_now(),
            finished_at=_utc_now(),
            cursor_before=connector.cursor,
            cursor_after=cursor_after,
            item_count=len(items),
        )
        with self._connect() as conn:
            self._save_source_items(conn, workspace_id, items)
            self._insert_run(conn, run)
            conn.execute(
                """
                UPDATE source_connectors
                SET cursor_value = ?, connection_status = 'connected', latest_run_id = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (cursor_after, run.id, run.finished_at.isoformat(), connector_id, workspace_id),
            )
        return run

    def record_sync_failure(
        self,
        workspace_id: str,
        connector_id: str,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        connection_status: str = "failed",
    ) -> SourceRunEvidence:
        connector = self.get_connector(workspace_id, connector_id)
        now = _utc_now()
        run = SourceRunEvidence(
            id=f"src_run_{uuid4().hex}",
            workspace_id=workspace_id,
            connector_id=connector_id,
            status="failed",
            started_at=now,
            finished_at=now,
            cursor_before=connector.cursor,
            cursor_after=connector.cursor,
            item_count=0,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )
        with self._connect() as conn:
            self._insert_run(conn, run)
            conn.execute(
                """
                UPDATE source_connectors
                SET connection_status = ?, latest_run_id = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (connection_status, run.id, now.isoformat(), connector_id, workspace_id),
            )
        return run

    def get_run(self, workspace_id: str, run_id: str) -> SourceRunEvidence:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_from_row(row)

    def list_runs(
        self, workspace_id: str, connector_id: str | None = None
    ) -> list[SourceRunEvidence]:
        self._require_workspace(workspace_id)
        query = "SELECT * FROM source_runs WHERE workspace_id = ?"
        values: list[str] = [workspace_id]
        if connector_id:
            query += " AND connector_id = ?"
            values.append(connector_id)
        query += " ORDER BY finished_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._run_from_row(row) for row in rows]

    def _init_database(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS source_connectors (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    account_or_rule TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    cursor_value TEXT,
                    permissions TEXT NOT NULL,
                    connection_status TEXT NOT NULL,
                    latest_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_connectors_workspace
                    ON source_connectors(workspace_id);
                CREATE TABLE IF NOT EXISTS source_items (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    source_label TEXT NOT NULL DEFAULT '',
                    source_url TEXT,
                    content_type TEXT NOT NULL,
                    author TEXT,
                    published_at TEXT,
                    body TEXT,
                    subtitles TEXT,
                    media_references TEXT NOT NULL,
                    license TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_reference TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(connector_id) REFERENCES source_connectors(id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_items_workspace
                    ON source_items(workspace_id, connector_id, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT,
                    file_reference TEXT,
                    authorization TEXT NOT NULL,
                    ownership TEXT NOT NULL,
                    source_item_id TEXT,
                    version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_item_id) REFERENCES source_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_items_workspace
                    ON knowledge_items(workspace_id, created_at);
                CREATE TABLE IF NOT EXISTS source_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    cursor_before TEXT,
                    cursor_after TEXT,
                    item_count INTEGER NOT NULL,
                    reason TEXT,
                    impact TEXT,
                    repair_action TEXT,
                    retryable INTEGER,
                    FOREIGN KEY(connector_id) REFERENCES source_connectors(id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_runs_workspace
                    ON source_runs(workspace_id, connector_id, finished_at DESC);
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(source_items)")
            }
            if "source_label" not in columns:
                conn.execute(
                    "ALTER TABLE source_items ADD COLUMN source_label TEXT NOT NULL DEFAULT ''"
                )

    def _insert_run(self, conn: sqlite3.Connection, run: SourceRunEvidence) -> None:
        conn.execute(
            """
            INSERT INTO source_runs (
                id, workspace_id, connector_id, status, started_at, finished_at,
                cursor_before, cursor_after, item_count, reason, impact,
                repair_action, retryable
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id, run.workspace_id, run.connector_id, run.status,
                run.started_at.isoformat(), run.finished_at.isoformat(),
                run.cursor_before, run.cursor_after, run.item_count, run.reason,
                run.impact, run.repair_action,
                int(run.retryable) if run.retryable is not None else None,
            ),
        )

    def _save_source_items(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        items: list[SourceItem],
    ) -> None:
        for item in items:
            self._validate_source_item(workspace_id, item)
            existing_item = conn.execute(
                "SELECT workspace_id FROM source_items WHERE id = ?", (item.id,)
            ).fetchone()
            if (
                existing_item is not None
                and existing_item["workspace_id"] != workspace_id
            ):
                raise ValueError("source item id already belongs to another workspace")
            connector = conn.execute(
                "SELECT 1 FROM source_connectors WHERE id = ? AND workspace_id = ?",
                (item.connector_id, workspace_id),
            ).fetchone()
            if connector is None:
                raise KeyError(item.connector_id)
            conn.execute(
                """
                INSERT INTO source_items (
                    id, workspace_id, connector_id, source_label, source_url, content_type,
                    author, published_at, body, subtitles, media_references,
                    license, title, file_reference, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_label = excluded.source_label,
                    source_url = excluded.source_url,
                    content_type = excluded.content_type,
                    author = excluded.author,
                    published_at = excluded.published_at,
                    body = excluded.body,
                    subtitles = excluded.subtitles,
                    media_references = excluded.media_references,
                    license = excluded.license,
                    title = excluded.title,
                    file_reference = excluded.file_reference
                WHERE source_items.workspace_id = excluded.workspace_id
                """,
                (
                    item.id,
                    item.workspace_id,
                    item.connector_id,
                    item.source_label,
                    item.source_url,
                    item.content_type,
                    item.author,
                    item.published_at.isoformat() if item.published_at else None,
                    item.body,
                    item.subtitles,
                    json.dumps(item.media_references, ensure_ascii=False),
                    item.license,
                    item.title,
                    item.file_reference,
                    item.created_at.isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _connector_from_row(self, row: sqlite3.Row) -> SourceConnector:
        return SourceConnector(
            id=row["id"], workspace_id=row["workspace_id"], platform=row["platform"],
            protocol=row["protocol"], account_or_rule=row["account_or_rule"],
            frequency=row["frequency"], cursor=row["cursor_value"],
            permissions=tuple(json.loads(row["permissions"])),
            connection_status=row["connection_status"], latest_run_id=row["latest_run_id"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def _source_item_from_row(self, row: sqlite3.Row) -> SourceItem:
        return SourceItem(
            id=row["id"], workspace_id=row["workspace_id"], connector_id=row["connector_id"],
            source_label=row["source_label"], source_url=row["source_url"],
            content_type=row["content_type"], author=row["author"],
            published_at=_parse_datetime(row["published_at"]) if row["published_at"] else None,
            body=row["body"], subtitles=row["subtitles"],
            media_references=tuple(json.loads(row["media_references"])),
            license=row["license"], title=row["title"], file_reference=row["file_reference"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def _knowledge_item_from_row(self, row: sqlite3.Row) -> KnowledgeItem:
        return KnowledgeItem(
            id=row["id"], workspace_id=row["workspace_id"], content_type=row["content_type"],
            title=row["title"], body=row["body"], file_reference=row["file_reference"],
            authorization=row["authorization"], ownership=row["ownership"],
            source_item_id=row["source_item_id"], version=row["version"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def _run_from_row(self, row: sqlite3.Row) -> SourceRunEvidence:
        return SourceRunEvidence(
            id=row["id"], workspace_id=row["workspace_id"], connector_id=row["connector_id"],
            status=row["status"], started_at=_parse_datetime(row["started_at"]),
            finished_at=_parse_datetime(row["finished_at"]), cursor_before=row["cursor_before"],
            cursor_after=row["cursor_after"], item_count=row["item_count"], reason=row["reason"],
            impact=row["impact"], repair_action=row["repair_action"],
            retryable=bool(row["retryable"]) if row["retryable"] is not None else None,
        )

    def _validate_source_item(self, workspace_id: str, item: SourceItem) -> None:
        if item.workspace_id != workspace_id:
            raise ValueError("source item workspace does not match save workspace")
        if (
            not item.title.strip()
            or not item.source_label.strip()
            or not item.content_type.strip()
            or not item.license.strip()
        ):
            raise ValueError("source item requires source_label, title, content_type, and license")
        if not (
            item.source_url
            or item.file_reference
            or item.body
            or item.subtitles
            or item.media_references
        ):
            raise ValueError("source item requires content, source_url, or file_reference")

    def _require_workspace(self, workspace_id: str) -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
