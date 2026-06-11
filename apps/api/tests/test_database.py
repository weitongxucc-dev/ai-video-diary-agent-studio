from datetime import datetime
import sqlite3

from app.database import DATABASE_SCHEMA_VERSION, backup_sqlite_database
from app.repository import EpisodeRepository


def test_episode_repository_initializes_database_schema_metadata(tmp_path):
    db_path = tmp_path / "agent.sqlite3"

    EpisodeRepository(database_path=db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row == (str(DATABASE_SCHEMA_VERSION),)


def test_backup_sqlite_database_creates_readable_copy(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    repo = EpisodeRepository(database_path=db_path)
    episode = repo.create()

    backup_path = backup_sqlite_database(
        db_path,
        timestamp_provider=lambda: datetime(2026, 6, 10, 12, 30, 15),
    )

    assert backup_path.name == "agent-backup-20260610-123015.sqlite3"
    assert backup_path.parent == tmp_path / "backups"
    assert EpisodeRepository(database_path=backup_path).get(episode.id).id == episode.id
