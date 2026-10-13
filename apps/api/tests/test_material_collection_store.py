"""Independent real SQLite checks for workspace collection persistence."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from app.materials import collections as collection_storage
from app.materials.collections import MaterialCollectionStore, collection_ids_for_workspace
from app.materials.contracts import MaterialCollection
from app.persistence_json import _json_payload


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "collections.sqlite3"
    state = {"connections": 0, "trace": []}

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        state["connections"] += 1
        number = state["connections"]
        conn.set_trace_callback(lambda sql: state["trace"].append((number, sql)))
        return conn

    # Existing table shape; database setup is owned only by this isolated fixture.
    with connect() as conn:
        conn.execute("""
            CREATE TABLE material_collections (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
    state["connections"] = 0
    state["trace"].clear()
    return connect, state


def _snapshot(connect):
    with connect() as conn:
        return [tuple(row) for row in conn.execute("SELECT * FROM material_collections ORDER BY rowid")]


def test_store_construction_does_not_open_or_initialize_a_database():
    def forbidden():
        raise AssertionError("construction must not request a connection")

    MaterialCollectionStore(forbidden)


def test_store_import_does_not_load_production_or_runtime_adapters(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc
        import sqlite3
        import socket
        import subprocess
        import sys
        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {'app.production', 'app.main', 'app.repository', 'app.model_runtime',
                    'app.asr', 'app.material_speech', 'app.visual_understanding',
                    'app.device_runtime', 'app.device_scheduler'}
                if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                    raise AssertionError('runtime dependency: ' + fullname)
        def forbidden(*args, **kwargs):
            raise AssertionError('import or constructor attempted runtime work')
        sys.meta_path.insert(0, RejectRuntime())
        sqlite3.connect = forbidden
        socket.socket = forbidden
        subprocess.Popen = forbidden
        from app.materials.collections import MaterialCollectionStore
        MaterialCollectionStore(forbidden)
    """)
    script = f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n" + script
    completed = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_save_returns_original_value_and_reopen_preserves_hierarchy_dates_and_order(database, monkeypatch):
    connect, _state = database
    store = MaterialCollectionStore(connect)
    base = datetime(2026, 10, 14, tzinfo=timezone.utc)
    times = iter(base + timedelta(seconds=number) for number in range(1, 5))
    monkeypatch.setattr(collection_storage, "utc_now", lambda: next(times))
    assert store.list_collections("workspace-a") == []
    parent = MaterialCollection("parent", "workspace-a", "办公资料", created_at=base)
    child = MaterialCollection("child", "workspace-a", "日记", "parent", created_at=base)
    assert store.save_collection(parent) is parent
    assert store.save_collection(child) is child
    foreign = MaterialCollection("foreign", "workspace-b", "别人的资料", created_at=base)
    store.save_collection(foreign)
    parent.name = "办公资料更新"
    assert store.save_collection(parent) is parent
    reopened = MaterialCollectionStore(connect)
    assert reopened.list_collections("workspace-a") == [parent, child]
    assert reopened.list_collections("workspace-b") == [foreign]
    assert parent.created_at == base
    assert parent.updated_at == base + timedelta(seconds=4)
    assert child.parent_id == "parent"


def test_parent_read_and_save_share_one_immediate_transaction(database):
    connect, state = database
    store = MaterialCollectionStore(connect)
    store.save_collection(MaterialCollection("parent", "workspace-a", "父级"))
    state["connections"] = 0
    state["trace"].clear()
    child = MaterialCollection("child", "workspace-a", "子级", "parent")
    store.save_collection(child)
    assert state["connections"] == 1
    assert {number for number, _sql in state["trace"]} == {1}
    statements = [" ".join(sql.split()) for _number, sql in state["trace"]]
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[1].startswith("SELECT payload FROM material_collections")
    assert statements[2].startswith("INSERT INTO material_collections")
    assert statements[-1] == "COMMIT"


@pytest.mark.parametrize("parent_id", ["missing", "foreign-parent"])
def test_missing_or_foreign_parent_rejects_without_changing_any_persisted_rows(database, parent_id):
    connect, _state = database
    store = MaterialCollectionStore(connect)
    store.save_collection(MaterialCollection("foreign-parent", "workspace-b", "外国父级"))
    before = _snapshot(connect)
    child = MaterialCollection("child", "workspace-a", "子级", parent_id)
    original_updated_at = child.updated_at
    with pytest.raises(KeyError) as failure:
        store.save_collection(child)
    assert failure.value.args == (parent_id,)
    assert _snapshot(connect) == before
    assert child.updated_at == original_updated_at


def test_sql_write_failure_rolls_back_existing_collection_rows(database):
    connect, state = database
    store = MaterialCollectionStore(connect)
    value = store.save_collection(MaterialCollection("existing", "workspace-a", "原名称"))
    with connect() as conn:
        conn.execute("""
            CREATE TRIGGER reject_collection_update BEFORE UPDATE ON material_collections
            BEGIN SELECT RAISE(ABORT, 'collection-write-rejected'); END
        """)
    before = _snapshot(connect)
    state["trace"].clear()
    value.name = "新名称"
    with pytest.raises(sqlite3.IntegrityError, match="collection-write-rejected"):
        store.save_collection(value)
    assert any(sql == "ROLLBACK" for _number, sql in state["trace"])
    assert _snapshot(connect) == before


def test_serialization_failure_rolls_back_existing_collection_rows(database):
    connect, state = database
    store = MaterialCollectionStore(connect)
    value = store.save_collection(MaterialCollection("existing", "workspace-a", "原名称"))
    before = _snapshot(connect)
    value.name = object()
    state["trace"].clear()
    with pytest.raises(TypeError, match="cannot serialize object"):
        store.save_collection(value)
    assert any(sql == "ROLLBACK" for _number, sql in state["trace"])
    assert _snapshot(connect) == before


def test_parent_full_list_date_decode_failure_is_strict_and_precedes_child_write(database):
    connect, state = database
    store = MaterialCollectionStore(connect)
    store.save_collection(MaterialCollection("parent", "workspace-a", "父级"))
    bad = MaterialCollection("bad-date", "workspace-a", "错误日期")
    bad.created_at = "not-a-date"
    with connect() as conn:
        conn.execute("INSERT INTO material_collections VALUES (?, ?, ?, ?)",
                     (bad.id, bad.workspace_id, _json_payload(bad), bad.updated_at.isoformat()))
    before = _snapshot(connect)
    state["trace"].clear()
    child = MaterialCollection("child", "workspace-a", "子级", "parent")
    original_updated_at = child.updated_at
    with pytest.raises(ValueError, match="Invalid isoformat string"):
        store.save_collection(child)
    assert any(sql == "ROLLBACK" for _number, sql in state["trace"])
    assert not any(sql.lstrip().startswith("INSERT") for _number, sql in state["trace"])
    assert _snapshot(connect) == before
    assert child.updated_at == original_updated_at
    with pytest.raises(ValueError, match="Invalid isoformat string"):
        store.list_collections("workspace-a")


def test_collection_ids_use_caller_transaction_and_see_uncommitted_scoped_rows(database):
    connect, state = database
    store = MaterialCollectionStore(connect)
    store.save_collection(MaterialCollection("existing", "workspace-a", "原集合"))
    store.save_collection(MaterialCollection("foreign", "workspace-b", "别人的集合"))
    before = _snapshot(connect)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        staged = MaterialCollection("uncommitted", "workspace-a", "未提交集合")
        conn.execute("INSERT INTO material_collections VALUES (?, ?, ?, ?)",
                     (staged.id, staged.workspace_id, _json_payload(staged), staged.updated_at.isoformat()))
        connections_before_query = state["connections"]
        state["trace"].clear()
        assert collection_ids_for_workspace(conn, "workspace-a") == {"existing", "uncommitted"}
        assert state["connections"] == connections_before_query
        assert conn.in_transaction
        assert len(state["trace"]) == 1
        assert state["trace"][0][1].startswith("SELECT id FROM material_collections")
        conn.rollback()
    assert _snapshot(connect) == before
