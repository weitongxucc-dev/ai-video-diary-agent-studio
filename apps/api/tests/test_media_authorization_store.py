"""Versioned authorization persistence without application or model initialization."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from app.profiles import authorizations, versioned_payloads
from app.profiles.authorizations import MediaAuthorizationStore
from app.profiles.contracts import Authorization


WORKSPACE = "workspace-a"
WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)
TABLES = ("media_authorizations", "person_profiles", "versioned_voice_profiles", "pronunciation_entries")


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "authorizations.sqlite3"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    with connect() as conn:
        for table in TABLES:
            conn.execute(f"""
                CREATE TABLE {table} (
                    workspace_id TEXT NOT NULL, id TEXT NOT NULL, version TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
                )
            """)
    return connect


def authorization(**changes):
    original = Authorization(
        id="grant", version="v1", rights_holder="登记成员", content_types=("voice", "avatar"),
        usage_scopes=("preview", "publish"), effective_from=WHEN - timedelta(days=1),
        expires_at=None, evidence_reference="fixture://proof", status="active", workspace_id=WORKSPACE,
    )
    return replace(original, **changes)


def snapshot(connect):
    with connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in TABLES
        }


def test_import_and_construction_do_not_open_database_or_load_execution(tmp_path):
    source_root = str(Path(__file__).resolve().parents[1])
    script = f"import sys; sys.path.insert(0, {source_root!r})\n" + textwrap.dedent("""
        import importlib.abc
        import socket
        import sqlite3
        import subprocess

        class RejectExecution(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {'app.production', 'app.main', 'app.repository', 'app.asr',
                    'app.avatar_lipsync', 'app.narration', 'app.model_runtime',
                    'app.device_runtime', 'app.device_scheduler'}
                if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                    raise AssertionError('execution dependency: ' + fullname)

        def forbidden(*args, **kwargs):
            raise AssertionError('import or construction attempted runtime work')

        sys.meta_path.insert(0, RejectExecution())
        sqlite3.connect = forbidden
        socket.socket = forbidden
        subprocess.Popen = forbidden
        from app.profiles.authorizations import MediaAuthorizationStore
        from app.profiles import contracts, authorizations
        assert authorizations._as_utc is contracts._as_utc
        MediaAuthorizationStore(forbidden)
    """)
    completed = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_public_roundtrip_immutable_versions_and_workspace_scoped_lists(database, monkeypatch):
    times = iter(WHEN + timedelta(seconds=number) for number in range(5))
    monkeypatch.setattr(versioned_payloads, "utc_now", lambda: next(times))
    store = MediaAuthorizationStore(database)
    first = authorization()
    assert store.save_authorization(first) is first
    before_repeat = snapshot(database)
    store.save_authorization(first)
    assert snapshot(database) == before_repeat
    with pytest.raises(ValueError, match="语义变更必须使用新版本"):
        store.save_authorization(replace(first, rights_holder="其他人"))
    assert snapshot(database) == before_repeat

    second = replace(first, version="v2", status="revoked")
    store.save_authorization(second)
    other = authorization(id="another")
    foreign = authorization(workspace_id="workspace-b")
    store.save_authorization(other)
    store.save_authorization(foreign)
    restarted = MediaAuthorizationStore(database)
    assert restarted.get_authorization(WORKSPACE, "grant", "v1") == first
    assert restarted.get_authorization(WORKSPACE, "grant") == second
    assert restarted.list_authorizations(WORKSPACE) == [other, second]
    assert restarted.list_authorization_versions(WORKSPACE, "grant") == [second, first]
    assert restarted.list_authorizations("workspace-b") == [foreign]


def test_supplied_connection_reads_uncommitted_authorization_and_rolls_back_with_parent(database):
    def forbidden():
        raise AssertionError("caller-supplied connection must not request another connection")

    store = MediaAuthorizationStore(forbidden)
    value = authorization()
    with database() as conn:
        conn.execute("BEGIN")
        store._save_authorization(conn, WORKSPACE, value)
        versioned_payloads._save_versioned_payload(
            conn, table="person_profiles", workspace_id=WORKSPACE, record_id="person", version="v1",
            payload={"authorization_id": value.id, "authorization_version": value.version},
        )
        assert store._get_authorization(conn, WORKSPACE, "grant", "v1") == value
        assert store._current_authorization(conn, WORKSPACE, value) == value
        assert conn.in_transaction
        with pytest.raises(KeyError):
            MediaAuthorizationStore(database).get_authorization(WORKSPACE, "grant", "v1")
        conn.rollback()
    assert all(not rows for rows in snapshot(database).values())


@pytest.mark.parametrize("failure", ["sql", "serialization"])
def test_later_parent_write_failure_rolls_back_authorization_in_same_transaction(database, failure):
    store = MediaAuthorizationStore(database)
    original = authorization()
    store.save_authorization(original)
    if failure == "sql":
        with database() as conn:
            conn.execute("""
                CREATE TRIGGER reject_profile BEFORE INSERT ON person_profiles
                BEGIN SELECT RAISE(ABORT, 'profile-second-write-rejected'); END
            """)
    before = snapshot(database)
    expected_error = sqlite3.IntegrityError if failure == "sql" else TypeError
    with pytest.raises(expected_error):
        with database() as conn:
            store._save_authorization(conn, WORKSPACE, replace(original, version="v2"))
            versioned_payloads._save_versioned_payload(
                conn, table="person_profiles", workspace_id=WORKSPACE, record_id="person", version="v1",
                payload={"authorization_id": "grant"} if failure == "sql" else {"unsupported": object()},
            )
    assert snapshot(database) == before


def test_foreign_workspace_lookup_and_connected_save_reject_without_changes(database):
    store = MediaAuthorizationStore(database)
    store.save_authorization(authorization(workspace_id="workspace-b"))
    before = snapshot(database)
    with pytest.raises(KeyError):
        store.get_authorization(WORKSPACE, "grant", "v1")
    with pytest.raises(ValueError, match="授权不属于当前工作区"):
        with database() as conn:
            store._save_authorization(conn, WORKSPACE, authorization(workspace_id="workspace-b"))
    assert store.list_authorization_versions(WORKSPACE, "grant") == []
    assert snapshot(database) == before


@pytest.mark.parametrize("changes", [
    {"workspace_id": ""}, {"evidence_reference": ""}, {"content_types": ()}, {"status": "unknown"},
])
def test_original_authorization_validation_rejects_before_persistence(database, changes):
    before = snapshot(database)
    with pytest.raises(ValueError):
        MediaAuthorizationStore(database).save_authorization(authorization(**changes))
    assert snapshot(database) == before


@pytest.mark.parametrize("historical", [
    authorization(status="revoked"), authorization(status="expired"),
    authorization(expires_at=WHEN - timedelta(seconds=1)),
])
def test_revoked_or_expired_history_cannot_upgrade_to_later_active_version(database, monkeypatch, historical):
    monkeypatch.setattr(authorizations, "utc_now", lambda: WHEN)
    store = MediaAuthorizationStore(database)
    store.save_authorization(historical)
    newer = authorization(version="v2")
    store.save_authorization(newer)
    before = snapshot(database)
    with database() as conn:
        assert store._current_authorization(conn, WORKSPACE, historical) is historical
        assert store._current_authorization(conn, WORKSPACE, None) is None
    assert snapshot(database) == before


def test_current_authorization_rechecks_latest_but_missing_fixed_version_never_falls_back(database, monkeypatch):
    monkeypatch.setattr(authorizations, "utc_now", lambda: WHEN)
    times = iter([WHEN, WHEN + timedelta(seconds=1)])
    monkeypatch.setattr(versioned_payloads, "utc_now", lambda: next(times))
    store = MediaAuthorizationStore(database)
    first = authorization()
    revoked = authorization(version="v2", status="revoked")
    store.save_authorization(first)
    store.save_authorization(revoked)
    before = snapshot(database)
    with database() as conn:
        assert store._current_authorization(conn, WORKSPACE, first) == revoked
        with pytest.raises(KeyError):
            store._get_authorization(conn, WORKSPACE, "grant", "missing-fixed-version")
        with pytest.raises(KeyError):
            store._current_authorization(conn, WORKSPACE, authorization(id="missing-grant"))
    assert snapshot(database) == before


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("content_types", None), ("effective_from", 12), ("expires_at", "invalid-date"),
])
def test_original_strict_decode_errors_leave_existing_payload_untouched(database, field, value):
    store = MediaAuthorizationStore(database)
    store.save_authorization(authorization())
    with database() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM media_authorizations").fetchone()[0])
        payload[field] = value
        conn.execute("UPDATE media_authorizations SET payload=?", (json.dumps(payload),))
    before = snapshot(database)
    with pytest.raises(ValueError):
        store.get_authorization(WORKSPACE, "grant", "v1")
    assert snapshot(database) == before


@pytest.mark.parametrize("table", TABLES)
def test_shared_version_helpers_preserve_canonical_json_current_lists_and_immutability(database, monkeypatch, table):
    times = iter([WHEN, WHEN + timedelta(seconds=1), WHEN + timedelta(seconds=2)])
    monkeypatch.setattr(versioned_payloads, "utc_now", lambda: next(times))
    first = {"名字": "原值", "sequence": [1, 2]}
    second = {"sequence": [1, 2], "名字": "新值"}
    with database() as conn:
        versioned_payloads._save_versioned_payload(
            conn, table=table, workspace_id=WORKSPACE, record_id="item", version="v1", payload=first,
        )
        versioned_payloads._save_versioned_payload(
            conn, table=table, workspace_id=WORKSPACE, record_id="item", version="v1",
            payload={"sequence": [1, 2], "名字": "原值"},
        )
        versioned_payloads._save_versioned_payload(
            conn, table=table, workspace_id=WORKSPACE, record_id="item", version="v2", payload=second,
        )
        assert versioned_payloads._list_versioned_payloads(
            conn, table=table, workspace_id=WORKSPACE, current_only=True,
        ) == [second]
        assert versioned_payloads._list_versioned_payloads(
            conn, table=table, workspace_id=WORKSPACE, record_id="item",
        ) == [second, first]
        row = versioned_payloads._versioned_row(
            conn, table=table, workspace_id=WORKSPACE, record_id="item", version="v1",
        )
        assert row["payload"] == json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    before = snapshot(database)
    with pytest.raises(ValueError, match="语义变更必须使用新版本"):
        with database() as conn:
            versioned_payloads._save_versioned_payload(
                conn, table=table, workspace_id=WORKSPACE, record_id="item", version="v1", payload=second,
            )
    assert snapshot(database) == before


def test_raw_authorization_history_keeps_full_rows_strings_ascending_time_and_rowid(database):
    def forbidden():
        raise AssertionError("history reader must use its supplied connection")

    store = MediaAuthorizationStore(forbidden)
    early = WHEN.isoformat()
    late = (WHEN + timedelta(seconds=1)).isoformat()
    raw_values = [
        (WORKSPACE, "grant", "later", "raw later JSON is not decoded", late),
        (WORKSPACE, "grant", "z-first-tie", ' { "原始": "first" }\n', early),
        (WORKSPACE, "grant", "a-second-tie", ' { "原始": "second" }\n', early),
        ("workspace-b", "grant", "foreign", "foreign bytes", early),
        (WORKSPACE, "other-grant", "other", "other ID bytes", early),
    ]
    with database() as conn:
        conn.executemany("INSERT INTO media_authorizations VALUES (?, ?, ?, ?, ?)", raw_values)
    before = snapshot(database)
    with database() as conn:
        rows = store._authorization_history_rows(conn, WORKSPACE, "grant")
        assert isinstance(rows, list)
        assert all(isinstance(row, sqlite3.Row) for row in rows)
        assert [tuple(row) for row in rows] == [raw_values[1], raw_values[2], raw_values[0]]
        assert [row["payload"] for row in rows] == [raw_values[1][3], raw_values[2][3], raw_values[0][3]]
        assert rows[0].keys() == ["workspace_id", "id", "version", "payload", "created_at"]
        assert [tuple(row) for row in store._authorization_history_rows(conn, "workspace-b", "grant")] == [raw_values[3]]
        assert [tuple(row) for row in store._authorization_history_rows(conn, WORKSPACE, "other-grant")] == [raw_values[4]]
        assert store._authorization_history_rows(conn, "missing-workspace", "grant") == []
        assert store._authorization_history_rows(conn, WORKSPACE, "missing-grant") == []
    assert snapshot(database) == before


def test_raw_authorization_history_reads_caller_uncommitted_rows_without_commit_or_rollback(database):
    def forbidden():
        raise AssertionError("history reader must not request another connection")

    store = MediaAuthorizationStore(forbidden)
    raw = (WORKSPACE, "grant", "v1", ' { "原始": "未提交" }\n', WHEN.isoformat())
    before = snapshot(database)
    with database() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO media_authorizations VALUES (?, ?, ?, ?, ?)", raw)
        assert [tuple(row) for row in store._authorization_history_rows(conn, WORKSPACE, "grant")] == [raw]
        assert conn.in_transaction
        with database() as observer:
            assert store._authorization_history_rows(observer, WORKSPACE, "grant") == []
        assert conn.in_transaction
        conn.rollback()
        assert store._authorization_history_rows(conn, WORKSPACE, "grant") == []
    assert snapshot(database) == before
