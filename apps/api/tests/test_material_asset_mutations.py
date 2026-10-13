"""Actual SQLite atomicity for the five existing manual asset commands."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import Barrier

import pytest

from app.materials import assets as asset_storage
from app.materials.assets import MaterialAssetStore, MaterialVersionConflict
from app.materials.contracts import MaterialAsset, MaterialAssetNote, MaterialSegment
from app.media_observations import SegmentUnderstanding
from app.persistence_json import _json_payload


WORKSPACE = "workspace-a"
WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "manual-assets.sqlite3"
    traces = []

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(traces.append)
        return conn

    with connect() as conn:
        conn.executescript("""
            CREATE TABLE material_assets (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, file_path TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL, visibility TEXT NOT NULL,
                UNIQUE(workspace_id, file_path)
            );
            CREATE TABLE material_collections (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        for asset_id, workspace in [("first", WORKSPACE), ("second", WORKSPACE), ("foreign", "workspace-b")]:
            value = MaterialAsset(
                asset_id, workspace, f"{asset_id}.mp4", f"fixture://{asset_id}", 10, WHEN,
                user_tags=["Alpha", "保留"], user_description="原说明", upload_context="原背景",
                owner_user_id="uploader", created_at=WHEN, updated_at=WHEN,
                segments=[MaterialSegment(
                    f"{asset_id}-segment", asset_id, 0, 2,
                    description="原片段说明", transcript="原语音纠正", usage_role="b_roll",
                    user_tags=["原片段标签"], system_tags=["用途:b_roll", "观察:旧画面"],
                    understanding=SegmentUnderstanding("ready", summary="旧画面观察"),
                    created_at=WHEN, updated_at=WHEN,
                )],
            )
            conn.execute("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", (
                value.id, value.workspace_id, value.path, _json_payload(value),
                value.updated_at.isoformat(), value.visibility,
            ))
        for collection_id, workspace in [("own-collection", WORKSPACE), ("foreign-collection", "workspace-b")]:
            conn.execute("INSERT INTO material_collections VALUES (?, ?, ?, ?)",
                         (collection_id, workspace, "{}", WHEN.isoformat()))
    traces.clear()
    return connect, traces


def _snapshot(connect):
    with connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("material_assets", "material_collections")
        }


def _write_batch(store, kind, asset_ids):
    if kind == "tags":
        return store.update_assets_tags_batch(WORKSPACE, asset_ids, add=["新标签"], remove=["Alpha"])
    return store.update_asset_notes(WORKSPACE, [MaterialAssetNote(asset_id, "新说明", "新背景") for asset_id in asset_ids])


@pytest.mark.parametrize("scope", ["asset", "segment"])
def test_same_frozen_revision_allows_only_one_actual_sqlite_writer(database, monkeypatch, scope):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    original = store.get_asset(WORKSPACE, "first")
    barrier = Barrier(2)
    monkeypatch.setattr(asset_storage, "utc_now", lambda: WHEN + timedelta(seconds=1))

    def write(label):
        barrier.wait()
        try:
            if scope == "asset":
                saved = store.update_asset_tags(WORKSPACE, original.id, [label], expected_asset_updated_at=original.updated_at)
            else:
                saved = store.update_segment(
                    WORKSPACE, original.id, original.segments[0].id,
                    description=label, transcript="保留的语音", review_status="raw", user_tags=[label],
                    collection_ids=["own-collection"], expected_asset_updated_at=original.updated_at,
                    expected_segment_updated_at=original.segments[0].updated_at,
                )
            return label, saved
        except MaterialVersionConflict:
            return label, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ["甲", "乙"]))
    winners = [label for label, saved in outcomes if saved is not None]
    assert len(winners) == 1
    reopened = MaterialAssetStore(connect).get_asset(WORKSPACE, original.id)
    if scope == "asset":
        assert reopened.user_tags == winners
        assert reopened.segments == original.segments
    else:
        assert reopened.user_tags == original.user_tags
        assert reopened.segments[0].user_tags == winners
        assert reopened.segments[0].description == winners[0]
        assert reopened.segments[0].collection_ids == ["own-collection"]
        assert reopened.segments[0].understanding == original.segments[0].understanding


@pytest.mark.parametrize("kind", ["tags", "notes"])
def test_second_sql_update_failure_rolls_back_every_row_and_timestamp(database, kind):
    connect, traces = database
    with connect() as conn:
        conn.execute("""
            CREATE TRIGGER reject_second_update BEFORE UPDATE ON material_assets
            WHEN NEW.id = 'second'
            BEGIN SELECT RAISE(ABORT, 'second-update-rejected'); END
        """)
    before = _snapshot(connect)
    traces.clear()
    with pytest.raises(sqlite3.IntegrityError, match="second-update-rejected"):
        _write_batch(MaterialAssetStore(connect), kind, ["first", "second"])
    assert any(sql.startswith("UPDATE material_assets") and "'first'" in sql for sql in traces)
    assert "ROLLBACK" in traces
    assert _snapshot(connect) == before


@pytest.mark.parametrize("kind", ["tags", "notes"])
@pytest.mark.parametrize("rejected_id", ["foreign", "missing"])
def test_foreign_or_missing_second_asset_is_validated_before_any_batch_write(database, kind, rejected_id):
    connect, traces = database
    before = _snapshot(connect)
    traces.clear()
    with pytest.raises(KeyError) as failure:
        _write_batch(MaterialAssetStore(connect), kind, ["first", rejected_id])
    assert failure.value.args == (rejected_id,)
    assert not any(sql.startswith("UPDATE material_assets") for sql in traces)
    assert _snapshot(connect) == before


@pytest.mark.parametrize("kind", ["tags", "notes"])
def test_empty_batch_keeps_original_error_and_persisted_values(database, kind):
    connect, _traces = database
    before = _snapshot(connect)
    with pytest.raises(ValueError, match="请选择至少一条素材"):
        _write_batch(MaterialAssetStore(connect), kind, [])
    assert _snapshot(connect) == before


def test_tag_batch_retains_add_remove_order_casefold_and_id_deduplication(database):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    result = store.update_assets_tags_batch(
        WORKSPACE, ["first", "first", "second"],
        add=[" Alpha ", " 新标签 ", "新标签"], remove=["alpha"],
    )
    assert [value.id for value in result] == ["first", "second"]
    assert [value.user_tags for value in result] == [["保留", "Alpha", "新标签"]] * 2
    assert [store.get_asset(WORKSPACE, value.id).user_tags for value in result] == [value.user_tags for value in result]
    single = store.update_asset_tags(WORKSPACE, "first", [" 创业 ", "创业", "x" * 81])
    assert single.user_tags == ["创业", "x" * 80]


def test_note_batch_deduplicates_whole_note_and_empty_text_clears_original_values(database):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    first = MaterialAssetNote("first", " 新说明 ", " 新背景 ")
    result = store.update_asset_notes(WORKSPACE, [first, first, MaterialAssetNote("second", "", " ")])
    assert [value.id for value in result] == ["first", "second"]
    assert (result[0].user_description, result[0].upload_context) == ("新说明", "新背景")
    assert (result[1].user_description, result[1].upload_context) == ("", "")
    assert [(store.get_asset(WORKSPACE, value.id).user_description, store.get_asset(WORKSPACE, value.id).upload_context)
            for value in result] == [("新说明", "新背景"), ("", "")]


@pytest.mark.parametrize("collection_id", ["foreign-collection", "missing-collection"])
def test_collection_rejection_rolls_back_all_segment_fields_and_timestamps(database, collection_id):
    connect, traces = database
    before = _snapshot(connect)
    traces.clear()
    with pytest.raises(ValueError, match="标签夹不存在或不属于当前工作区"):
        MaterialAssetStore(connect).update_segment(
            WORKSPACE, "first", "first-segment", description="不能保存", transcript="不能保存",
            review_status="raw", usage_role="exclude", user_tags=["不能保存"], collection_ids=[collection_id],
        )
    assert any(sql.startswith("SELECT id FROM material_collections") for sql in traces)
    assert not any(sql.startswith("UPDATE material_assets") for sql in traces)
    assert _snapshot(connect) == before


def test_segment_usage_change_keeps_observations_and_saves_deduplicated_collections(database):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    saved = store.update_segment(
        WORKSPACE, "first", "first-segment", description=" 新的人工说明 ", transcript=" 新的纠正 ",
        review_status="raw", usage_role="exclude", user_tags=[" 人工标签 ", "人工标签"],
        collection_ids=["own-collection", "own-collection"],
    )
    segment = saved.segments[0]
    assert (segment.description, segment.transcript, segment.review_status) == ("新的人工说明", "新的纠正", "rejected")
    assert segment.user_tags == ["人工标签"]
    assert segment.collection_ids == ["own-collection"]
    assert segment.system_tags == ["观察:旧画面", "用途:exclude"]
    assert segment.understanding.summary == "旧画面观察"
    assert store.get_asset(WORKSPACE, "first").segments == saved.segments


def test_segment_approval_requires_current_asset_approval_without_partial_changes(database):
    connect, _traces = database
    before = _snapshot(connect)
    with pytest.raises(ValueError, match="素材文件尚未进入可用库"):
        MaterialAssetStore(connect).update_segment(
            WORKSPACE, "first", "first-segment", description="不能保存", transcript="不能保存", review_status="approved",
        )
    assert _snapshot(connect) == before


def test_payload_only_tag_mutation_preserves_existing_column_visibility_semantics(database):
    connect, _traces = database
    with connect() as conn:
        conn.execute("UPDATE material_assets SET visibility='shared' WHERE id='first'")
    store = MaterialAssetStore(connect)
    saved = store.update_asset_tags(WORKSPACE, "first", ["人工标签"])
    assert saved.visibility == "private"
    assert store.get_asset(WORKSPACE, "first").visibility == "shared"
    with connect() as conn:
        row = conn.execute("SELECT visibility, payload FROM material_assets WHERE id='first'").fetchone()
    assert row["visibility"] == "shared"
    assert json.loads(row["payload"])["visibility"] == "private"


def test_explicit_visibility_updates_payload_and_authoritative_column_and_reopens(database):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    for visibility in ("shared", "private"):
        saved = store.set_asset_visibility(WORKSPACE, "first", visibility)
        assert saved.visibility == visibility
        with connect() as conn:
            row = conn.execute("SELECT visibility, payload FROM material_assets WHERE id='first'").fetchone()
        assert (row["visibility"], json.loads(row["payload"])["visibility"]) == (visibility, visibility)
        assert MaterialAssetStore(connect).get_asset(WORKSPACE, "first").visibility == visibility


@pytest.mark.parametrize("asset_id,visibility,error", [("first", "unknown", ValueError), ("foreign", "shared", KeyError)])
def test_visibility_rejects_unknown_value_or_foreign_workspace_without_changes(database, asset_id, visibility, error):
    connect, _traces = database
    before = _snapshot(connect)
    with pytest.raises(error):
        MaterialAssetStore(connect).set_asset_visibility(WORKSPACE, asset_id, visibility)
    assert _snapshot(connect) == before
