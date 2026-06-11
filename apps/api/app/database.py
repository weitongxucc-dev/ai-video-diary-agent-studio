from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import sqlite3

DATABASE_SCHEMA_VERSION = 1


def ensure_database_schema_metadata(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_meta (key, value, updated_at)
        VALUES ('schema_version', ?, ?)
        """,
        (str(DATABASE_SCHEMA_VERSION), datetime.utcnow().isoformat()),
    )


def backup_sqlite_database(
    database_path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    timestamp_provider: Callable[[], datetime] = datetime.now,
) -> Path:
    source_path = Path(database_path).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    destination_dir = (
        Path(backup_dir).expanduser()
        if backup_dir
        else source_path.parent / "backups"
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp_provider().strftime("%Y%m%d-%H%M%S")
    suffix = source_path.suffix or ".sqlite3"
    destination_path = destination_dir / f"{source_path.stem}-backup-{timestamp}{suffix}"

    with sqlite3.connect(source_path) as source_conn:
        with sqlite3.connect(destination_path) as destination_conn:
            source_conn.backup(destination_conn)
    return destination_path
