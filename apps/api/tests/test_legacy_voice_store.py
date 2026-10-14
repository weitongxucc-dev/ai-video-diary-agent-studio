"""One native SQLite/public wiring difference for mutable historical voice rows."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app import production
from app.profiles import legacy_voice
from app.profiles.contracts import VoiceProfile as VersionedVoiceProfile
from app.persistence_json import _json_payload


WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)


def voice(identity, workspace, *, default=False):
    return legacy_voice.VoiceProfile(
        identity, workspace, identity, "workspace://opaque-reference.wav", "synthetic legacy text", True,
        is_default=default, created_at=WHEN, updated_at=WHEN,
    )


def snapshot(repo):
    with repo._connect() as conn:
        return [tuple(row) for row in conn.execute("SELECT * FROM voice_profiles ORDER BY id")]


def test_legacy_voice_public_store_preserves_defaults_global_id_and_late_failure_rollback(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("store construction must not request a connection")

    legacy_voice.LegacyVoiceProfileStore(forbidden)
    assert production.VoiceProfile is legacy_voice.VoiceProfile
    assert production.VoiceProfile is not VersionedVoiceProfile
    repo = production.ProductionRepository(tmp_path / "legacy.sqlite3", tmp_path / "library")
    store = repo._legacy_voice_profiles
    assert isinstance(store, legacy_voice.LegacyVoiceProfileStore)
    assert store._connect.__self__ is repo
    original_factory = store._connect
    opened, traces = [], []

    def connect():
        conn = original_factory()
        conn.set_trace_callback(traces.append)
        opened.append(conn)
        return conn

    store._connect = connect
    clock = {"now": WHEN}
    monkeypatch.setattr(legacy_voice, "utc_now", lambda: clock["now"])
    first = voice("first", "workspace-a", default=True)
    assert repo.save_voice_profile(first) is first
    clock["now"] = WHEN + timedelta(seconds=1)
    selected = voice("selected", "workspace-a", default=True)
    assert repo.save_voice_profile(selected) is selected
    clock["now"] = WHEN + timedelta(seconds=2)
    foreign = voice("foreign", "workspace-b", default=True)
    repo.save_voice_profile(foreign)
    clock["now"] = WHEN + timedelta(seconds=3)
    newest_nondefault = voice("newest", "workspace-a")
    repo.save_voice_profile(newest_nondefault)
    listed = repo.list_voice_profiles("workspace-a")
    assert [item.id for item in listed] == ["selected", "newest", "first"]
    assert [item.is_default for item in listed] == [True, False, False]
    assert repo.list_voice_profiles("workspace-b")[0].is_default is True
    assert repo.list_voice_profiles("missing-workspace") == []
    restored = repo.get_voice_profile("workspace-a", "selected")
    assert restored.created_at == WHEN and restored.updated_at == WHEN + timedelta(seconds=1)
    with pytest.raises(KeyError) as missing:
        repo.get_voice_profile("workspace-b", "selected")
    assert missing.value.args == ("selected",)
    with repo._connect() as conn:
        old_default = conn.execute("SELECT is_default,payload FROM voice_profiles WHERE id='first'").fetchone()
        assert old_default["is_default"] == 0
        assert json.loads(old_default["payload"])["is_default"] is True
        assert conn.execute("SELECT payload FROM voice_profiles WHERE id='selected'").fetchone()[0] == _json_payload(selected)

    # The original global-id UPSERT replaces fields without replacing the workspace column.
    clock["now"] = WHEN + timedelta(seconds=4)
    collision = voice("foreign", "workspace-a")
    repo.save_voice_profile(collision)
    with repo._connect() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id='foreign'").fetchone()
        assert row["workspace_id"] == "workspace-b" and row["is_default"] == 0
        assert row["payload"] == _json_payload(collision)
        assert row["updated_at"] == clock["now"].isoformat()
    with pytest.raises(KeyError):
        repo.get_voice_profile("workspace-a", "foreign")

    with repo._connect() as conn:
        conn.execute("""CREATE TRIGGER reject_late_legacy_voice
            BEFORE INSERT ON voice_profiles WHEN NEW.id = 'reject-late'
            BEGIN SELECT RAISE(ABORT, 'synthetic late legacy UPSERT refusal'); END""")
    before = snapshot(repo)
    clock["now"] = WHEN + timedelta(seconds=5)
    rejected = voice("reject-late", "workspace-a", default=True)
    traces.clear()
    opened_before = len(opened)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic late legacy UPSERT refusal"):
        repo.save_voice_profile(rejected)
    assert len(opened) == opened_before + 1
    assert "BEGIN IMMEDIATE" not in traces
    assert traces[-1] == "ROLLBACK"
    update = next(index for index, sql in enumerate(traces) if sql.lstrip().startswith("UPDATE voice_profiles SET is_default = 0"))
    insert = next(index for index, sql in enumerate(traces) if sql.lstrip().startswith("INSERT INTO voice_profiles"))
    assert update < insert
    assert snapshot(repo) == before
    assert rejected.updated_at == clock["now"]  # The caller object keeps the original pre-write clock mutation.
    assert repo.get_voice_profile("workspace-a", "selected").is_default is True
