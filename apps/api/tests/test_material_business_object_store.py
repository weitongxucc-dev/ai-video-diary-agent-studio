"""Independent real SQLite checks for business-name association storage."""

from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from app.materials.business_objects import MaterialBusinessObjectStore


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "business-objects.sqlite3"
    state = {"deny_name_reads": False}

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        if state["deny_name_reads"]:
            def authorize(action, table, column, _database, _origin):
                if action == sqlite3.SQLITE_READ and table == "business_objects" and column == "kind":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
        return conn

    # These are the existing three table shapes, created only by the isolated fixture.
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE material_assets (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, file_path TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(workspace_id, file_path)
            );
            CREATE TABLE business_objects (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('product', 'dish', 'person')),
                name TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE material_business_objects (
                workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                business_object_id TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, asset_id, business_object_id)
            );
        """)
        for asset_id, workspace in (("asset-a", "workspace-a"), ("asset-other", "workspace-a"), ("asset-b", "workspace-b")):
            conn.execute(
                "INSERT INTO material_assets VALUES (?, ?, ?, ?, ?)",
                (asset_id, workspace, f"fixture://{asset_id}", "{}", "2026-10-14T00:00:00+00:00"),
            )
    return connect, state


def _snapshot(connect):
    with connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("material_assets", "business_objects", "material_business_objects")
        }


def test_constructing_the_store_does_not_open_or_initialize_a_database():
    def forbidden_connection():
        raise AssertionError("construction must not request a connection")

    MaterialBusinessObjectStore(forbidden_connection)


def test_store_import_does_not_depend_on_application_or_model_runtime():
    script = textwrap.dedent("""
        import importlib.abc
        from pathlib import Path
        import sys

        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {'app.production', 'app.main', 'app.repository', 'app.model_runtime',
                    'app.asr', 'app.material_speech', 'app.visual_understanding',
                    'app.avatar_lipsync', 'app.voice_clone'}
                if fullname in blocked:
                    raise AssertionError(f'Store imported runtime module: {fullname}')

        def reject_side_effects(event, args):
            if event in {'sqlite3.connect', 'socket.connect', 'subprocess.Popen'}:
                raise AssertionError(f'Store import caused runtime effect: {event}')

        sys.meta_path.insert(0, RejectRuntime())
        sys.addaudithook(reject_side_effects)
        Path.rglob = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Vault scan forbidden'))
        from app.materials.business_objects import MaterialBusinessObjectStore
        def forbidden_connection():
            raise AssertionError('construction requested a database')
        MaterialBusinessObjectStore(forbidden_connection)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_names_and_ordered_selection_survive_reopen_and_empty_selection(database):
    connect, _ = database
    store = MaterialBusinessObjectStore(connect)
    first = store.create_business_object("workspace-a", "product", "  产品甲  ")
    second = store.create_business_object("workspace-a", "dish", "菜品乙")
    third = store.create_business_object("workspace-a", "person", "人物丙")
    assert first.name == "产品甲"
    assert store.list_asset_business_objects("workspace-a", "asset-a") == []
    assert store.list_business_objects("workspace-a") == [third, second, first]
    assert store.set_asset_business_objects("workspace-a", "asset-other", [second.id]) == [second]

    selected = store.set_asset_business_objects("workspace-a", "asset-a", [third.id, first.id, third.id, second.id])
    assert selected == [third, first, second]
    reopened = MaterialBusinessObjectStore(connect)
    assert reopened.list_asset_business_objects("workspace-a", "asset-a") == selected
    assert reopened.list_asset_business_objects("workspace-a", "asset-other") == [second]
    assert reopened.set_asset_business_objects("workspace-a", "asset-a", [first.id]) == [first]
    assert reopened.set_asset_business_objects("workspace-a", "asset-a", []) == []
    assert reopened.list_asset_business_objects("workspace-a", "asset-a") == []
    assert reopened.list_business_objects("workspace-a") == [third, second, first]


@pytest.mark.parametrize("invalid_selection", ["unknown-name", "foreign-name", "foreign-asset"])
def test_refused_selection_leaves_every_existing_row_unchanged(database, invalid_selection):
    connect, _ = database
    store = MaterialBusinessObjectStore(connect)
    own = store.create_business_object("workspace-a", "product", "本空间")
    other = store.create_business_object("workspace-b", "product", "另一空间")
    store.set_asset_business_objects("workspace-a", "asset-a", [own.id])
    before = _snapshot(connect)
    asset_id = "asset-b" if invalid_selection == "foreign-asset" else "asset-a"
    names = [own.id, other.id] if invalid_selection == "foreign-name" else [own.id, "missing"]

    with pytest.raises(KeyError):
        store.set_asset_business_objects("workspace-a", asset_id, names)

    assert _snapshot(connect) == before
    assert store.list_business_objects("workspace-a") == [own]
    with pytest.raises(KeyError):
        store.list_asset_business_objects("workspace-a", "asset-b")


def test_failed_insert_rolls_back_deletion_and_all_earlier_insertions(database):
    connect, _ = database
    store = MaterialBusinessObjectStore(connect)
    old = store.create_business_object("workspace-a", "product", "旧关联")
    first = store.create_business_object("workspace-a", "product", "新关联甲")
    second = store.create_business_object("workspace-a", "product", "新关联乙")
    store.set_asset_business_objects("workspace-a", "asset-a", [old.id])
    with connect() as conn:
        conn.execute(f"""
            CREATE TRIGGER reject_second_association BEFORE INSERT ON material_business_objects
            WHEN NEW.business_object_id = '{second.id}'
            BEGIN SELECT RAISE(ABORT, 'association write failed'); END
        """)
    before = _snapshot(connect)

    with pytest.raises(sqlite3.IntegrityError, match="association write failed"):
        store.set_asset_business_objects("workspace-a", "asset-a", [first.id, second.id])

    assert _snapshot(connect) == before
    assert MaterialBusinessObjectStore(connect).list_asset_business_objects("workspace-a", "asset-a") == [old]


def test_failed_receipt_read_rolls_back_the_replacement_before_commit(database):
    connect, state = database
    store = MaterialBusinessObjectStore(connect)
    old = store.create_business_object("workspace-a", "product", "旧关联")
    new = store.create_business_object("workspace-a", "product", "新关联")
    store.set_asset_business_objects("workspace-a", "asset-a", [old.id])
    before = _snapshot(connect)
    # id/workspace checks still work; SQLite denies the final name/field read after the writes.
    state["deny_name_reads"] = True
    try:
        with pytest.raises(sqlite3.DatabaseError, match="business_objects.kind"):
            store.set_asset_business_objects("workspace-a", "asset-a", [new.id])
    finally:
        state["deny_name_reads"] = False
    assert _snapshot(connect) == before
    assert MaterialBusinessObjectStore(connect).list_asset_business_objects("workspace-a", "asset-a") == [old]


def test_receipt_date_decode_failure_rolls_back_every_existing_row(database):
    connect, _ = database
    store = MaterialBusinessObjectStore(connect)
    old = store.create_business_object("workspace-a", "product", "旧关联")
    new = store.create_business_object("workspace-a", "product", "新关联")
    store.set_asset_business_objects("workspace-a", "asset-a", [old.id])
    with connect() as conn:
        conn.execute("UPDATE business_objects SET created_at=? WHERE id=?", ("invalid-date", new.id))
    before = _snapshot(connect)

    with pytest.raises(ValueError, match="Invalid isoformat string"):
        store.set_asset_business_objects("workspace-a", "asset-a", [new.id])

    # The bad source row stays unchanged; the failed receipt must roll back only this attempted replacement.
    assert _snapshot(connect) == before
    assert MaterialBusinessObjectStore(connect).list_asset_business_objects("workspace-a", "asset-a") == [old]
