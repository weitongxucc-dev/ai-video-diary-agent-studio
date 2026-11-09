"""Library migration and bounded SQL reads on isolated databases."""

import json
import sqlite3
import pytest
from app.creation.script_windows import (
    migrate_library_sections,
    library_header,
    read_library_sections,
    ScriptWindowConflict,
)


def legacy(conn, *, invalid=False):
    conn.execute(
        "CREATE TABLE script_library_entries(id TEXT PRIMARY KEY,workspace_id TEXT,source_task_id TEXT,name TEXT,payload TEXT,created_at TEXT)"
    )
    header = {
        "id": "library",
        "workspace_id": "workspace",
        "version": 1,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "sections": [
            {
                "id": "same" if invalid else f"part-{i}",
                "title": "标题",
                "content": f"原文{i}",
                "visual_prompt": "",
                "reference_ids": [],
            }
            for i in range(45)
        ],
    }
    conn.execute(
        "INSERT INTO script_library_entries VALUES (?,?,?,?,?,?)",
        ("library", "workspace", "task", "名字", json.dumps(header), "2026-01-01"),
    )
    conn.commit()
    return header


@pytest.mark.parametrize("invalid", [False, True])
def test_library_body_migration_is_atomic_and_keeps_all_original_text(invalid):
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        original = legacy(conn, invalid=invalid)
        if invalid:
            with pytest.raises(sqlite3.IntegrityError), conn:
                conn.execute("BEGIN IMMEDIATE")
                migrate_library_sections(conn)
            assert (
                json.loads(
                    conn.execute(
                        "SELECT payload FROM script_library_entries"
                    ).fetchone()[0]
                )
                == original
            )
            assert (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='script_library_sections'"
                ).fetchone()
                is None
            )
        else:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                migrate_library_sections(conn)
            body = json.loads(
                conn.execute("SELECT payload FROM script_library_entries").fetchone()[0]
            )
            assert "sections" not in body and body["_sections_storage"] == "rows"
            assert [
                json.loads(r[0])
                for r in conn.execute(
                    "SELECT section_json FROM script_library_sections ORDER BY ordinal"
                )
            ] == original["sections"]
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                migrate_library_sections(conn)
            assert (
                conn.execute("SELECT count(*) FROM script_library_sections").fetchone()[
                    0
                ]
                == 45
            )


def test_library_cursor_pins_version_and_reads_real_indexed_windows():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        legacy(conn)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            migrate_library_sections(conn)
        with conn:
            conn.execute("BEGIN")
            header = library_header(conn, "workspace", "library")
            assert (
                header["sections"] is None
                and not header["sections_loaded"]
                and header["sections_total_count"] == 45
            )
            first = read_library_sections(conn, "workspace", "library")
            assert len(first["items"]) == 20 and first["next_after_ordinal"] == 20
            with pytest.raises(ValueError):
                read_library_sections(conn, "workspace", "library", after_ordinal=20)
            with pytest.raises(ScriptWindowConflict):
                read_library_sections(
                    conn,
                    "workspace",
                    "library",
                    after_ordinal=20,
                    expected_updated_at="2025-01-01T00:00:00+00:00",
                )
            second = read_library_sections(
                conn,
                "workspace",
                "library",
                after_ordinal=20,
                expected_updated_at=first["updated_at"],
            )
            third = read_library_sections(
                conn,
                "workspace",
                "library",
                after_ordinal=40,
                expected_updated_at=first["updated_at"],
            )
            assert (
                len(second["items"]) == 20
                and len(third["items"]) == 5
                and third["next_after_ordinal"] is None
            )
            with pytest.raises(KeyError):
                library_header(conn, "other-workspace", "library")
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT section_json FROM script_library_sections WHERE workspace_id=? AND entry_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
                ("workspace", "library", 20, 20),
            ).fetchall()
            assert any("INDEX" in r["detail"] and "SEARCH" in r["detail"] for r in plan)


def test_old_blob_writer_cannot_reintroduce_a_second_library_body_after_migration():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        original = legacy(conn)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            migrate_library_sections(conn)
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute(
                "UPDATE script_library_entries SET payload=? WHERE id=?",
                (json.dumps(original), "library"),
            )
        assert "sections" not in json.loads(
            conn.execute("SELECT payload FROM script_library_entries").fetchone()[0]
        )
        assert (
            conn.execute("SELECT count(*) FROM script_library_sections").fetchone()[0]
            == 45
        )


def test_header_list_filters_in_sql_and_uses_a_stable_browse_cursor():
    from contextlib import contextmanager
    from app.creation.script_records import ScriptRecordStore

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    legacy(conn)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        migrate_library_sections(conn)
        base = json.loads(
            conn.execute("SELECT payload FROM script_library_entries").fetchone()[0]
        )
        base.update(title="Title", category="demo")
        conn.execute("UPDATE script_library_entries SET payload=?", (json.dumps(base),))
        for i in range(6):
            header = {
                **base,
                "id": f"entry-{i}",
                "title": f"Title {i}",
                "category": "demo" if i % 2 else "other",
            }
            conn.execute(
                "INSERT INTO script_library_entries VALUES (?,?,?,?,?,?)",
                (
                    header["id"],
                    "workspace",
                    "task",
                    f"Name {i}",
                    json.dumps(header),
                    f"2026-02-0{i + 1}",
                ),
            )

    @contextmanager
    def connect():
        with conn:
            yield conn

    def absent(*a, **k):
        raise AssertionError("header query must not hydrate cross-domain records")

    store = ScriptRecordStore(
        connect,
        read_task=absent,
        require_task=absent,
        decode_plan=absent,
        read_topic=absent,
        transition_task=absent,
        create_library_task=absent,
        knowledge_inputs_current=absent,
        append_event=absent,
        get_event=absent,
        decode_event=absent,
    )
    first = store.list_script_library_entry_headers(
        "workspace", query="TITLE", category="demo", limit=2
    )
    assert [x["id"] for x in first["items"]] == ["entry-5", "entry-3"] and first["next"]
    second = store.list_script_library_entry_headers(
        "workspace", query="TITLE", category="demo", limit=2, **first["next"]
    )
    assert [x["id"] for x in second["items"]] == ["entry-1", "library"] and second[
        "next"
    ] is None
    assert all(
        x["sections"] is None and not x["sections_loaded"]
        for x in first["items"] + second["items"]
    )
    assert store.list_script_library_entry_headers("foreign")["items"] == []
    conn.close()
