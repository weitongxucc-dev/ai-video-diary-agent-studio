from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

DATABASE_SCHEMA_VERSION = 1


class DatabaseMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseMigration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def run_database_migrations(conn: sqlite3.Connection) -> None:
    _ensure_migration_tables(conn)
    current_version = _schema_version(conn)
    if current_version > DATABASE_SCHEMA_VERSION:
        raise DatabaseMigrationError(
            f"database schema version {current_version} is newer than this app supports "
            f"({DATABASE_SCHEMA_VERSION})"
        )

    for migration in DATABASE_MIGRATIONS:
        if migration.version > current_version:
            migration.apply(conn)
            _record_migration(conn, migration)
            current_version = migration.version
        elif not _migration_recorded(conn, migration.version):
            _record_migration(conn, migration)

    _set_schema_version(conn, DATABASE_SCHEMA_VERSION)


def ensure_database_schema_metadata(conn: sqlite3.Connection) -> None:
    run_database_migrations(conn)


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


def _baseline_schema_metadata(conn: sqlite3.Connection) -> None:
    _ensure_migration_tables(conn)


def _ensure_migration_tables(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise DatabaseMigrationError("database schema version is invalid") from exc


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_meta (key, value, updated_at)
        VALUES ('schema_version', ?, ?)
        """,
        (str(version), datetime.utcnow().isoformat()),
    )


def _record_migration(
    conn: sqlite3.Connection,
    migration: DatabaseMigration,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (migration.version, migration.name, datetime.utcnow().isoformat()),
    )


def _migration_recorded(conn: sqlite3.Connection, version: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (version,),
    ).fetchone()
    return row is not None


DATABASE_MIGRATIONS = (
    DatabaseMigration(
        version=1,
        name="baseline_schema_metadata",
        apply=_baseline_schema_metadata,
    ),
)
