"""Atomic asset review/state commands and persistent usage accounting."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import Barrier, Event, Lock

import pytest

from app.materials import assets as asset_storage
from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MaterialAsset, MaterialAssetNote, MaterialSegment
from app.persistence_json import _json_payload


WORKSPACE = "workspace-a"
WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "review-usage.sqlite3"
    state = {"connections": 0, "traces": []}
    numbering = Lock()

    def connect(on_begin=None):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        with numbering:
            state["connections"] += 1
            number = state["connections"]

        def trace(sql):
            state["traces"].append((number, sql))
            if on_begin is not None and sql == "BEGIN IMMEDIATE":
                on_begin.set()

        conn.set_trace_callback(trace)
        return conn

    with connect() as conn:
        conn.executescript("""
            CREATE TABLE material_assets (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, file_path TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL, visibility TEXT NOT NULL,
                UNIQUE(workspace_id, file_path)
            );
            CREATE TABLE material_segment_usage (
                workspace_id TEXT NOT NULL, plan_id TEXT NOT NULL, clip_id TEXT NOT NULL,
                asset_id TEXT NOT NULL, segment_id TEXT NOT NULL, used_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id, plan_id, clip_id)
            );
        """)
        for asset_id, workspace in [("asset", WORKSPACE), ("foreign", "workspace-b")]:
            value = MaterialAsset(
                asset_id, workspace, f"{asset_id}.mp4", f"fixture://{asset_id}", 10, WHEN,
                analysis_status="ready", duration_seconds=4, owner_user_id="uploader",
                user_tags=["原人工标签"], user_description="原人工说明", created_at=WHEN, updated_at=WHEN,
                segments=[
                    MaterialSegment("segment", asset_id, 0, 2, user_tags=["原片段标签"],
                                    system_tags=["原观察"], created_at=WHEN, updated_at=WHEN),
                    MaterialSegment("assigned", asset_id, 2, 4, usage_role="a_roll", review_status="approved",
                                    system_tags=["原观察"], created_at=WHEN, updated_at=WHEN),
                ],
            )
            conn.execute("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", (
                value.id, value.workspace_id, value.path, _json_payload(value),
                value.updated_at.isoformat(), value.visibility,
            ))
    state["connections"] = 0
    state["traces"].clear()
    return connect, state


def _snapshot(connect):
    with connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("material_assets", "material_segment_usage")
        }


def test_usage_second_write_failure_rolls_back_both_tables_and_same_key_retry_counts_once(database):
    connect, state = database
    store = MaterialAssetStore(connect)
    with connect() as conn:
        conn.execute("""
            CREATE TRIGGER reject_asset_update BEFORE UPDATE ON material_assets
            BEGIN SELECT RAISE(ABORT, 'usage-counter-second-write-rejected'); END
        """)
    before = _snapshot(connect)
    state["traces"].clear()
    with pytest.raises(sqlite3.IntegrityError, match="usage-counter-second-write-rejected"):
        store.record_segment_usage(WORKSPACE, "asset", "segment", plan_id="plan", clip_id="clip")
    assert any("INSERT OR IGNORE INTO material_segment_usage" in sql for _number, sql in state["traces"])
    assert any(sql == "ROLLBACK" for _number, sql in state["traces"])
    assert _snapshot(connect) == before
    with connect() as conn:
        conn.execute("DROP TRIGGER reject_asset_update")
    retried = store.record_segment_usage(WORKSPACE, "asset", "segment", plan_id="plan", clip_id="clip")
    assert retried.segments[0].usage_count == 1
    assert retried.segments[0].last_used_at is not None
    after_success = _snapshot(connect)
    repeated = store.record_segment_usage(WORKSPACE, "asset", "segment", plan_id="plan", clip_id="clip")
    assert repeated.segments[0].usage_count == 1
    assert _snapshot(connect) == after_success
    assert len(after_success["material_segment_usage"]) == 1


def test_usage_counter_failure_after_insert_leaves_no_idempotency_row(database):
    connect, state = database
    with connect() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM material_assets WHERE id='asset'").fetchone()[0])
        payload["segments"][0]["usage_count"] = "invalid-stored-count"
        conn.execute("UPDATE material_assets SET payload=? WHERE id='asset'", (json.dumps(payload),))
    before = _snapshot(connect)
    state["traces"].clear()
    with pytest.raises(TypeError):
        MaterialAssetStore(connect).record_segment_usage(WORKSPACE, "asset", "segment", plan_id="plan", clip_id="clip")
    assert any("INSERT OR IGNORE INTO material_segment_usage" in sql for _number, sql in state["traces"])
    assert _snapshot(connect) == before


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_usage_deliveries_count_current_state_once_per_key(database, same_key):
    connect, _state = database
    store = MaterialAssetStore(connect)
    barrier = Barrier(2)

    def deliver(number):
        barrier.wait()
        key = "same" if same_key else str(number)
        return store.record_segment_usage(WORKSPACE, "asset", "segment", plan_id=f"plan-{key}", clip_id=f"clip-{key}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(deliver, [1, 2]))
    expected = 1 if same_key else 2
    reopened = MaterialAssetStore(connect).get_asset(WORKSPACE, "asset")
    assert reopened.segments[0].usage_count == expected
    assert reopened.segments[1].usage_count == 0
    assert len(_snapshot(connect)["material_segment_usage"]) == expected
    assert sorted(value.segments[0].usage_count for value in receipts) == ([1, 1] if same_key else [1, 2])


def test_manual_usage_fallback_ids_keep_existing_prefixes_and_independent_keys(database):
    connect, _state = database
    store = MaterialAssetStore(connect)
    store.record_segment_usage(WORKSPACE, "asset", "segment")
    store.record_segment_usage(WORKSPACE, "asset", "segment")
    with connect() as conn:
        rows = conn.execute("SELECT plan_id, clip_id FROM material_segment_usage").fetchall()
    assert len({row["plan_id"] for row in rows}) == 2
    assert all(row["plan_id"].startswith("manual_") and row["clip_id"].startswith("clip_") for row in rows)
    assert store.get_asset(WORKSPACE, "asset").segments[0].usage_count == 2


@pytest.mark.parametrize("command", ["state", "review"])
@pytest.mark.parametrize("manual", ["tags", "notes"])
def test_locked_read_serializes_manual_edit_without_losing_either_result(database, command, manual):
    connect, _state = database
    read_finished, release_read, manual_begin = Event(), Event(), Event()

    class PausedReadStore(MaterialAssetStore):
        def get_asset(self, workspace_id, asset_id, *, connection=None):
            assert connection is not None and connection.in_transaction
            actual = super().get_asset(workspace_id, asset_id, connection=connection)
            read_finished.set()
            assert release_read.wait(10)
            return actual

    transactional = PausedReadStore(connect)
    manual_store = MaterialAssetStore(lambda: connect(on_begin=manual_begin))

    def update():
        if command == "state":
            return transactional.update_asset_use_state(WORKSPACE, "asset", "not_for_automation")
        return transactional.update_asset_review(WORKSPACE, "asset", review_status="approved", usage_role="b_roll")

    def edit():
        if manual == "tags":
            return manual_store.update_asset_tags(WORKSPACE, "asset", ["新人工标签"])
        return manual_store.update_asset_notes(WORKSPACE, [MaterialAssetNote("asset", "新人工说明")])

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(update)
        try:
            assert read_finished.wait(10)
            editing = pool.submit(edit)
            assert manual_begin.wait(10)
            assert not editing.done()
        finally:
            release_read.set()
        pending.result(timeout=10)
        editing.result(timeout=10)
    saved = MaterialAssetStore(connect).get_asset(WORKSPACE, "asset")
    assert (saved.user_tags if manual == "tags" else saved.user_description) == (["新人工标签"] if manual == "tags" else "新人工说明")
    if command == "state":
        assert saved.use_state == "not_for_automation"
    else:
        assert saved.review_status == "approved"
        assert saved.segments[0].review_status == "approved"


def test_all_use_states_preserve_manual_and_segment_facts(database):
    connect, _state = database
    store = MaterialAssetStore(connect)
    original = store.get_asset(WORKSPACE, "asset")
    for use_state in ("available", "reference_only", "excluded", "not_for_automation", "not_for_publish"):
        changed = store.update_asset_use_state(WORKSPACE, "asset", use_state)
        assert changed.use_state == use_state
        assert changed.user_tags == original.user_tags
        assert changed.user_description == original.user_description
        assert changed.segments == original.segments
        assert store.get_asset(WORKSPACE, "asset").use_state == use_state
    before = _snapshot(connect)
    with pytest.raises(ValueError, match="素材使用状态无效"):
        store.update_asset_use_state(WORKSPACE, "asset", "unknown")
    assert _snapshot(connect) == before


def test_review_approval_keeps_existing_use_restriction_and_assigned_role(database, monkeypatch):
    connect, _state = database
    store = MaterialAssetStore(connect)
    store.update_asset_use_state(WORKSPACE, "asset", "not_for_automation")
    before = store.get_asset(WORKSPACE, "asset")
    monkeypatch.setattr(asset_storage, "utc_now", lambda: WHEN + timedelta(seconds=1))
    saved = store.update_asset_review(WORKSPACE, "asset", review_status="approved", usage_role="b_roll")
    assert (saved.review_status, saved.usage_role, saved.use_state) == ("approved", "b_roll", "not_for_automation")
    assert (saved.segments[0].usage_role, saved.segments[0].review_status) == ("b_roll", "approved")
    assert saved.segments[0].updated_at == WHEN + timedelta(seconds=1)
    assert saved.segments[1] == before.segments[1]
    assert saved.user_tags == before.user_tags


@pytest.mark.parametrize("usage_role", ["unassigned", "exclude"])
def test_review_rejection_preserves_defined_segment_role_rules(database, usage_role):
    connect, _state = database
    store = MaterialAssetStore(connect)
    original = store.get_asset(WORKSPACE, "asset")
    saved = store.update_asset_review(
        WORKSPACE, "asset", review_status="approved" if usage_role == "exclude" else "rejected", usage_role=usage_role,
    )
    assert saved.use_state == "excluded"
    assert all(segment.review_status == "rejected" for segment in saved.segments)
    assert [segment.usage_role for segment in saved.segments] == (
        ["exclude", "exclude"] if usage_role == "exclude" else [segment.usage_role for segment in original.segments]
    )


@pytest.mark.parametrize("missing", ["role", "analysis", "segments"])
def test_review_approval_prerequisites_reject_without_changes(database, missing):
    connect, _state = database
    if missing != "role":
        with connect() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM material_assets WHERE id='asset'").fetchone()[0])
            if missing == "analysis":
                payload["analysis_status"] = "pending"
            else:
                payload["segments"] = []
            conn.execute("UPDATE material_assets SET payload=? WHERE id='asset'", (json.dumps(payload),))
    before = _snapshot(connect)
    with pytest.raises(ValueError):
        MaterialAssetStore(connect).update_asset_review(
            WORKSPACE, "asset", review_status="approved", usage_role="unassigned" if missing == "role" else "b_roll",
        )
    assert _snapshot(connect) == before


@pytest.mark.parametrize("command", ["state", "review", "usage"])
def test_foreign_workspace_is_rejected_without_any_persistence(database, command):
    connect, _state = database
    store = MaterialAssetStore(connect)
    before = _snapshot(connect)
    with pytest.raises(KeyError):
        if command == "state":
            store.update_asset_use_state(WORKSPACE, "foreign", "available")
        elif command == "review":
            store.update_asset_review(WORKSPACE, "foreign", review_status="approved", usage_role="b_roll")
        else:
            store.record_segment_usage(WORKSPACE, "foreign", "segment", plan_id="plan", clip_id="clip")
    assert _snapshot(connect) == before


def test_missing_segment_does_not_write_usage_or_asset(database):
    connect, _state = database
    before = _snapshot(connect)
    with pytest.raises(KeyError, match="missing-segment"):
        MaterialAssetStore(connect).record_segment_usage(WORKSPACE, "asset", "missing-segment", plan_id="plan", clip_id="clip")
    assert _snapshot(connect) == before


@pytest.mark.parametrize("command", ["state", "review", "usage"])
def test_each_command_reads_and_writes_on_one_immediate_connection(database, command):
    connect, state = database
    store = MaterialAssetStore(connect)
    state["connections"] = 0
    state["traces"].clear()
    if command == "state":
        store.update_asset_use_state(WORKSPACE, "asset", "reference_only")
    elif command == "review":
        store.update_asset_review(WORKSPACE, "asset", review_status="approved", usage_role="b_roll")
    else:
        store.record_segment_usage(WORKSPACE, "asset", "segment", plan_id="plan", clip_id="clip")
    assert state["connections"] == 1
    assert {number for number, _sql in state["traces"]} == {1}
    statements = [" ".join(sql.split()) for _number, sql in state["traces"]]
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[1].startswith("SELECT payload, visibility FROM material_assets")
    assert statements[-1] == "COMMIT"
