"""Real SQLite checks for the asset aggregate, independent of production setup."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from app.materials import assets as asset_storage
from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MaterialAsset, MaterialSegment
from app.media_observations import MaterialSpeechAnalysis, SegmentUnderstanding, SubtitleSegment
from app.persistence_json import _json_payload


WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "assets.sqlite3"
    traces = []

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(traces.append)
        return conn

    # Existing migrated table shapes. The store owns no database initialization.
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE material_assets (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, file_path TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL, visibility TEXT NOT NULL,
                UNIQUE(workspace_id, file_path)
            );
            CREATE TABLE material_unmatched_corrections (
                workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                payload TEXT NOT NULL, preserved_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, asset_id, segment_id)
            );
        """)
    traces.clear()
    return connect, traces


def _asset(asset_id="asset", *, workspace="workspace-a", owner="owner-a", visibility="private"):
    return MaterialAsset(
        asset_id, workspace, f"{asset_id}.mp4", f"fixture://{asset_id}", 10, WHEN,
        owner_user_id=owner, visibility=visibility,
        segments=[MaterialSegment(
            f"{asset_id}-segment", asset_id, 0, 2,
            system_tags=["既有标记"], created_at=WHEN, updated_at=WHEN,
        )],
        created_at=WHEN, updated_at=WHEN,
    )


def _insert(conn, asset, *, column_visibility=None, payload=None):
    conn.execute("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", (
        asset.id, asset.workspace_id, asset.path,
        _json_payload(asset) if payload is None else json.dumps(payload, ensure_ascii=False),
        asset.updated_at.isoformat(),
        asset.visibility if column_visibility is None else column_visibility,
    ))


def _snapshot(connect):
    with connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("material_assets", "material_unmatched_corrections")
        }


def test_store_construction_does_not_open_or_initialize_a_database():
    def forbidden():
        raise AssertionError("construction must not request a connection")

    MaterialAssetStore(forbidden)


def test_store_import_and_construction_do_not_load_analysis_or_application_runtime(tmp_path):
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
            raise AssertionError('value import or store constructor attempted runtime work')
        sys.meta_path.insert(0, RejectRuntime())
        sqlite3.connect = forbidden
        socket.socket = forbidden
        subprocess.Popen = forbidden
        from app.materials.assets import MaterialAssetStore
        MaterialAssetStore(forbidden)
    """)
    script = f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n" + script
    completed = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_ordinary_save_returns_original_aggregate_and_reopen_preserves_nested_values(database, monkeypatch):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    value = _asset()
    value.segments[0].description = "人工说明"
    value.segments[0].transcript = "人工纠正的语音"
    value.segments[0].collection_ids = ["collection-a"]
    monkeypatch.setattr(asset_storage, "utc_now", lambda: WHEN + timedelta(seconds=1))
    assert store.save_asset(value) is value
    assert value.updated_at == WHEN + timedelta(seconds=1)
    reopened = MaterialAssetStore(connect)
    assert asdict(reopened.get_asset("workspace-a", value.id)) == asdict(value)
    assert reopened.list_assets("workspace-a") == [value]
    assert reopened.list_assets("workspace-b") == []
    with pytest.raises(KeyError) as failure:
        reopened.get_asset("workspace-b", value.id)
    assert failure.value.args == (value.id,)


def test_explicit_connection_reads_uncommitted_asset_without_committing_or_opening_another(database):
    connect, _traces = database

    def forbidden():
        raise AssertionError("supplied connection must be used")

    store = MaterialAssetStore(forbidden)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        value = _asset()
        _insert(conn, value)
        assert asdict(store.get_asset("workspace-a", value.id, connection=conn)) == asdict(value)
        assert conn.in_transaction
        conn.execute("SELECT 1")  # The caller still owns an open connection.
        conn.rollback()
    assert _snapshot(connect) == {"material_assets": [], "material_unmatched_corrections": []}


@pytest.mark.parametrize("viewer,include_private,diagnostic,list_ids,page_ids", [
    (None, True, False, {"own", "other", "unowned", "shared", "revoked"}, {"own", "other", "unowned", "shared", "revoked"}),
    (None, False, False, {"shared"}, set()),
    ("owner-a", True, False, {"own", "shared"}, {"own", "shared"}),
    ("owner-a", False, False, {"own", "shared"}, {"own", "shared"}),
    ("owner-b", True, False, {"other", "shared", "revoked"}, {"other", "shared", "revoked"}),
    ("owner-a", True, True, {"own", "other", "unowned", "shared", "revoked"}, {"own", "other", "unowned", "shared", "revoked"}),
    ("owner-a", False, True, {"shared"}, {"shared"}),
])
def test_column_visibility_owner_scope_and_missing_viewer_modes_precede_paging(
    database, viewer, include_private, diagnostic, list_ids, page_ids,
):
    connect, _traces = database
    with connect() as conn:
        for number, value in enumerate([
            _asset("own"), _asset("other", owner="owner-b"), _asset("unowned", owner=None),
            _asset("shared", owner="owner-b"), _asset("revoked", owner="owner-b", visibility="shared"),
            _asset("foreign", workspace="workspace-b", visibility="shared"),
        ]):
            value.updated_at = WHEN + timedelta(seconds=number)
            column = "shared" if value.id in {"shared", "foreign"} else "private"
            _insert(conn, value, column_visibility=column)
    store = MaterialAssetStore(connect)
    flags = {"viewer_user_id": viewer, "include_private": include_private, "local_diagnostic": diagnostic}
    assert {item.id for item in store.list_assets("workspace-a", **flags)} == list_ids
    found = []
    for offset in range(len(page_ids) + 1):
        page, total = store.list_assets_page("workspace-a", limit=1, offset=offset, **flags)
        assert total == len(page_ids)
        assert len(page) == (1 if offset < len(page_ids) else 0)
        found.extend(page)
    assert {item.id for item in found} == page_ids
    assert [item.updated_at for item in found] == sorted((item.updated_at for item in found), reverse=True)
    assert store.get_asset("workspace-a", "shared").visibility == "shared"
    assert store.get_asset("workspace-a", "revoked").visibility == "private"


def test_query_exact_tag_review_and_legacy_media_type_share_filter_and_count(database):
    connect, _traces = database
    with connect() as conn:
        for asset_id, tag, media in [
            ("legacy-video", "红烧肉", None), ("similar-tag", "红烧肉特写", "video"),
            ("audio", "红烧肉", "audio"),
        ]:
            value = _asset(asset_id)
            value.user_tags = [tag]
            value.review_status = "approved"
            value.segments[0].description = "办公键盘"
            payload = json.loads(_json_payload(value))
            if media is None:
                payload.pop("media_type")
            else:
                payload["media_type"] = media
            _insert(conn, value, payload=payload)
    store = MaterialAssetStore(connect)
    filters = {"query": "办公", "tag": "红烧肉", "review_status": "approved", "media_type": "video"}
    page, total = store.list_assets_page("workspace-a", limit=1, offset=0, **filters)
    assert (total, [item.id for item in page]) == (1, ["legacy-video"])
    assert page[0].media_type == "video"
    assert store.list_assets_page("workspace-a", limit=1, offset=1, **filters) == ([], 1)
    assert store.list_assets_page("workspace-b", limit=1, offset=0, **filters) == ([], 0)


def test_ocr_confidence_filter_keeps_count_and_paged_rows_in_the_same_set(database):
    connect, _traces = database
    store = MaterialAssetStore(connect)
    for asset_id, confidence in [("below", 0.79), ("qualified", 0.8)]:
        value = _asset(asset_id)
        value.segments[0].understanding = SegmentUnderstanding(
            "ready", ocr_status="ready",
            ocr_frames=[{"time": 0.5, "observations": [{"text": "价目", "confidence": confidence}]}],
        )
        store.save_asset(value)
    page, total = store.list_assets_page("workspace-a", limit=1, offset=0, query="价目")
    assert (total, [item.id for item in page]) == (1, ["qualified"])
    assert store.list_assets_page("workspace-a", limit=1, offset=1, query="价目") == ([], 1)


def _analysis_pair(connect):
    current = _asset()
    current.segments.extend([
        MaterialSegment("removed-manual", current.id, 2, 4, description="旧人工纠正", created_at=WHEN, updated_at=WHEN),
        MaterialSegment("removed-raw", current.id, 4, 6, created_at=WHEN, updated_at=WHEN),
    ])
    with connect() as conn:
        _insert(conn, current)
    analyzed = deepcopy(current)
    analyzed.segments = [analyzed.segments[0]]
    analyzed.analysis_status = "ready"
    analyzed.analysis_message = "新的分析结果"
    return current, analyzed


def test_late_analysis_preserves_latest_manual_facts_and_saves_removed_correction_in_same_transaction(database):
    connect, traces = database
    latest, analyzed = _analysis_pair(connect)
    latest.visibility = "shared"
    latest.owner_user_id = "latest-owner"
    latest.use_state = "excluded"
    latest.review_status = "approved"
    latest.user_tags = ["人工资产标签"]
    latest.user_description = "最新资料说明"
    latest.upload_context = "最新批次背景"
    segment = latest.segments[0]
    segment.description = "人工办公说明"
    segment.transcript = "最新人工语音纠正"
    segment.user_tags = ["人工片段标签"]
    segment.collection_ids = ["latest-collection"]
    segment.usage_role = "a_roll"
    segment.review_status = "approved"
    segment.usage_count = 3
    segment.last_used_at = WHEN
    segment.updated_at = WHEN + timedelta(seconds=1)
    with connect() as conn:
        conn.execute("UPDATE material_assets SET payload=?, visibility=? WHERE id=?", (_json_payload(latest), "shared", latest.id))
    analyzed.duration_seconds = 12.0
    analyzed.speech_analysis = MaterialSpeechAnalysis("ready", observations=[SubtitleSegment(0, 1000, "模型语音")])
    analyzed.segments[0].visual_status = "ready"
    analyzed.segments[0].understanding = SegmentUnderstanding("ready", summary="新的画面观察")
    traces.clear()
    store = MaterialAssetStore(connect)
    saved = store.save_asset(analyzed, analysis_only=True)
    for name in ("visibility", "owner_user_id", "use_state", "review_status", "user_tags", "user_description", "upload_context"):
        assert getattr(saved, name) == getattr(latest, name)
    for name in ("description", "transcript", "user_tags", "collection_ids", "usage_role", "review_status", "usage_count", "last_used_at", "updated_at"):
        assert getattr(saved.segments[0], name) == getattr(segment, name)
    assert saved.segments[0].understanding.summary == "新的画面观察"
    assert (saved.duration_seconds, saved.speech_analysis.observations[0].text) == (12.0, "模型语音")
    assert [item.id for item in saved.segments] == [segment.id]
    statements = [" ".join(sql.split()) for sql in traces]
    assert statements[0] == "BEGIN IMMEDIATE"
    correction_index = next(i for i, sql in enumerate(statements) if sql.startswith("INSERT OR IGNORE INTO material_unmatched_corrections"))
    asset_index = next(i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO material_assets"))
    assert correction_index < asset_index < statements.index("COMMIT")
    reopened = MaterialAssetStore(connect)
    assert asdict(reopened.get_asset("workspace-a", latest.id)) == asdict(saved)
    with connect() as conn:
        rows = conn.execute("SELECT segment_id, payload FROM material_unmatched_corrections").fetchall()
    assert [row["segment_id"] for row in rows] == ["removed-manual"]
    assert json.loads(rows[0]["payload"])["description"] == "旧人工纠正"


@pytest.mark.parametrize("failure_kind", ["sql", "serialization"])
def test_failure_after_unmatched_correction_insert_rolls_back_both_tables(database, failure_kind):
    connect, traces = database
    _current, analyzed = _analysis_pair(connect)
    if failure_kind == "sql":
        with connect() as conn:
            conn.execute("""
                CREATE TRIGGER reject_asset_update BEFORE UPDATE ON material_assets
                BEGIN SELECT RAISE(ABORT, 'asset-write-rejected'); END
            """)
        error, message = sqlite3.IntegrityError, "asset-write-rejected"
    else:
        analyzed.speech_analysis = object()
        error, message = TypeError, "cannot serialize object"
    before = _snapshot(connect)
    traces.clear()
    with pytest.raises(error, match=message):
        MaterialAssetStore(connect).save_asset(analyzed, analysis_only=True)
    assert any(sql.lstrip().startswith("INSERT OR IGNORE INTO material_unmatched_corrections") for sql in traces)
    assert "ROLLBACK" in traces
    assert _snapshot(connect) == before


@pytest.mark.parametrize("changed", ["missing", "size", "modified"])
def test_removed_or_changed_source_rejects_analysis_without_persisting_anything(database, changed):
    connect, _traces = database
    _current, analyzed = _analysis_pair(connect)
    if changed == "missing":
        analyzed.id = "missing"
    elif changed == "size":
        analyzed.size_bytes += 1
    else:
        analyzed.modified_at += timedelta(seconds=1)
    before = _snapshot(connect)
    with pytest.raises(ValueError, match="素材已移除|素材文件发生变化"):
        MaterialAssetStore(connect).save_asset(analyzed, analysis_only=True)
    assert _snapshot(connect) == before


@pytest.mark.parametrize("date_field", ["asset", "segment"])
def test_decoder_strict_dates_and_legacy_proof_removal_keep_existing_rules(date_field):
    payload = json.loads(_json_payload(_asset()))
    payload.pop("media_type")
    payload.pop("use_state")
    payload.update(review_status="rejected", rights_confirmed=True, brand_safe=True)
    decoded = MaterialAssetStore._asset_from_payload(payload)
    assert (decoded.media_type, decoded.use_state) == ("video", "excluded")
    assert not hasattr(decoded, "rights_confirmed")
    assert not hasattr(decoded, "brand_safe")
    if date_field == "asset":
        payload["modified_at"] = "not-a-date"
    else:
        payload["segments"][0]["last_used_at"] = "not-a-date"
    with pytest.raises(ValueError, match="Invalid isoformat string"):
        MaterialAssetStore._asset_from_payload(payload)


def test_actual_observations_round_trip_through_sqlite_json_and_response_schema(database):
    from app.schemas import MaterialAssetResponse

    connect, _traces = database
    value = _asset()
    value.speech_analysis = MaterialSpeechAnalysis("ready", observations=[SubtitleSegment(0, 1000, "原始语音")])
    value.segments[0].understanding = SegmentUnderstanding("ready", summary="画面事实", sample_times=[0.5])
    store = MaterialAssetStore(connect)
    store.save_asset(value)
    loaded = store.get_asset("workspace-a", value.id)
    assert type(loaded.speech_analysis) is MaterialSpeechAnalysis
    assert type(loaded.speech_analysis.observations[0]) is SubtitleSegment
    assert type(loaded.segments[0].understanding) is SegmentUnderstanding
    assert asdict(loaded) == asdict(value)
    response = MaterialAssetResponse.model_validate(loaded, from_attributes=True).model_dump(mode="json")
    assert response["speech_analysis"]["observations"] == [{"start_ms": 0, "end_ms": 1000, "text": "原始语音"}]
    assert response["segments"][0]["understanding"]["summary"] == "画面事实"
    assert response["modified_at"] == "2026-10-14T00:00:00Z"


def test_raw_asset_readers_preserve_scoped_projection_payload_visibility_and_missing(database):
    connect, _traces = database

    def forbidden():
        raise AssertionError("raw asset reader must use its supplied connection")

    store = MaterialAssetStore(forbidden)
    value = _asset("local-asset", workspace="local", visibility="private")
    value.path = "/synthetic/raw path.mp4"
    raw = json.dumps(json.loads(_json_payload(value)), ensure_ascii=False, indent=2) + "\n"
    original = (value.id, value.workspace_id, value.path, raw, WHEN.isoformat(), "shared")
    foreign = ("foreign-asset", "foreign", value.path, "foreign undecoded bytes", WHEN.isoformat(), "private")
    with connect() as conn:
        conn.executemany("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", [original, foreign])
    before = _snapshot(connect)
    with connect() as conn:
        row = store._asset_payload_row_by_path(conn, "local", value.path)
        assert isinstance(row, sqlite3.Row)
        assert row.keys() == ["payload", "visibility"]
        assert row["payload"] == raw
        assert row["visibility"] == "shared"
        assert store._asset_from_row(row).visibility == "shared"
        complete = store._asset_row_by_id(conn, "local", value.id)
        assert isinstance(complete, sqlite3.Row)
        assert tuple(complete) == original
        assert complete.keys() == ["id", "workspace_id", "file_path", "payload", "updated_at", "visibility"]
        assert store._asset_row_by_id(conn, "local", "foreign-asset") is None
        assert store._asset_row_by_id(conn, "foreign", value.id) is None
        assert store._asset_payload_row_by_path(conn, "missing-space", value.path) is None
        assert store._asset_payload_row_by_path(conn, "local", "/missing-path") is None
        assert store._asset_row_by_id(conn, "local", "missing-id") is None
        assert store._asset_payload_row_by_path(conn, "foreign", value.path)["payload"] == foreign[3]
    assert _snapshot(connect) == before


def test_raw_asset_global_collisions_include_foreign_private_rows_and_foreign_paths_keep_duplicates(database):
    connect, _traces = database

    def forbidden():
        raise AssertionError("global/foreign reader must use its supplied connection")

    store = MaterialAssetStore(forbidden)
    path = "/synthetic/foreign-collision.wav"
    with connect() as conn:
        conn.executemany("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", [
            ("local-id", "local", "/synthetic/local.wav", "local bytes", WHEN.isoformat(), "private"),
            ("foreign-id", "foreign-a", path, "foreign private bytes", WHEN.isoformat(), "private"),
            ("another-foreign-id", "foreign-b", path, "foreign shared bytes", WHEN.isoformat(), "shared"),
        ])
    before = _snapshot(connect)
    with connect() as conn:
        by_path = store._asset_file_path_existence_row(conn, path)
        by_id = store._asset_id_existence_row(conn, "foreign-id")
        assert isinstance(by_path, sqlite3.Row) and tuple(by_path) == (1,)
        assert isinstance(by_id, sqlite3.Row) and tuple(by_id) == (1,)
        assert store._asset_payload_row_by_path(conn, "local", path) is None
        assert store._asset_row_by_id(conn, "local", "foreign-id") is None
        foreign = store._asset_file_path_rows_outside_workspace(conn, "local")
        assert isinstance(foreign, list)
        assert all(isinstance(row, sqlite3.Row) and row.keys() == ["file_path"] for row in foreign)
        assert [row["file_path"] for row in foreign] == [path, path]
        outside_a = store._asset_file_path_rows_outside_workspace(conn, "foreign-a")
        assert len(outside_a) == 2
        assert {row["file_path"] for row in outside_a} == {"/synthetic/local.wav", path}
        assert store._asset_file_path_existence_row(conn, "/missing-path") is None
        assert store._asset_id_existence_row(conn, "missing-id") is None
    assert _snapshot(connect) == before


def test_five_raw_asset_readers_see_caller_uncommitted_rows_isolate_observer_and_allow_rollback(database):
    connect, _traces = database

    def forbidden():
        raise AssertionError("caller connection must not be replaced by a factory connection")

    store = MaterialAssetStore(forbidden)

    def read_all(conn):
        return [
            store._asset_payload_row_by_path(conn, "local", "/synthetic/local.wav"),
            store._asset_file_path_rows_outside_workspace(conn, "local"),
            store._asset_row_by_id(conn, "local", "local-id"),
            store._asset_file_path_existence_row(conn, "/synthetic/foreign.wav"),
            store._asset_id_existence_row(conn, "foreign-id"),
        ]

    before = _snapshot(connect)
    with connect() as conn:
        conn.execute("BEGIN")
        conn.executemany("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", [
            ("local-id", "local", "/synthetic/local.wav", "uncommitted local raw bytes", WHEN.isoformat(), "private"),
            ("foreign-id", "foreign", "/synthetic/foreign.wav", "uncommitted foreign raw bytes", WHEN.isoformat(), "private"),
        ])
        rows = read_all(conn)
        assert rows[0]["payload"] == "uncommitted local raw bytes"
        assert [row["file_path"] for row in rows[1]] == ["/synthetic/foreign.wav"]
        assert rows[2]["id"] == "local-id"
        assert tuple(rows[3]) == (1,)
        assert tuple(rows[4]) == (1,)
        assert conn.in_transaction
        with connect() as observer:
            assert read_all(observer) == [None, [], None, None, None]
        assert conn.in_transaction
        conn.rollback()
        assert read_all(conn) == [None, [], None, None, None]
    assert _snapshot(connect) == before


def test_probe_metadata_uses_validated_raw_payload_preserves_manual_facts_columns_and_workspace(database, monkeypatch):
    connect, traces = database

    def forbidden():
        raise AssertionError("probe outcome must use its supplied connection")

    store = MaterialAssetStore(forbidden)
    value = _asset()
    value.user_tags = ["人工标签"]
    value.user_description = "人工说明"
    value.segments[0].user_tags = ["人工片段标签"]
    value.use_state = "not_for_automation"
    foreign = _asset("foreign", workspace="workspace-b")
    raw = json.dumps(json.loads(_json_payload(value)), ensure_ascii=False, indent=2) + "\n"
    stamp = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("app.materials.assets.utc_now", lambda: stamp)
    with connect() as conn:
        conn.execute("INSERT INTO material_assets VALUES (?, ?, ?, ?, ?, ?)", (
            value.id, value.workspace_id, value.path, raw, value.updated_at.isoformat(), "shared",
        ))
        _insert(conn, foreign)
    foreign_before = _snapshot(connect)["material_assets"][1]
    with connect() as conn:
        conn.execute("BEGIN")
        source = store._probe_asset_source_row(conn, value.workspace_id, value.id)
        assert source.keys() == ["payload", "file_path"]
        assert source["payload"] == raw and source["file_path"] == value.path
        assert store._probe_asset_source_row(conn, "workspace-b", value.id) is None
        assert store._probe_asset_source_row(conn, value.workspace_id, "missing") is None
        traces.clear()
        updated = store.apply_probe_metadata(conn, value.workspace_id, value.id, source["payload"], 3.25)
        assert len(traces) == 1 and traces[0].startswith("UPDATE material_assets")
        expected = asdict(value)
        expected.update(duration_seconds=3.25, analysis_status="blocked",
                        analysis_message="已读取时长与媒体流；镜头和画面分析尚未完成。", updated_at=stamp)
        assert asdict(updated) == expected
        row = conn.execute("SELECT * FROM material_assets WHERE id=?", (value.id,)).fetchone()
        assert row["visibility"] == "shared"
        assert json.loads(row["payload"])["visibility"] == "private"
        assert row["file_path"] == value.path
        assert row["updated_at"] == stamp.isoformat()
    assert _snapshot(connect)["material_assets"][1] == foreign_before


def test_probe_failure_preserves_original_reason_messages_unknown_keyerror_and_other_rows(database, monkeypatch):
    connect, traces = database

    def forbidden():
        raise AssertionError("failure command must not request another connection")

    store = MaterialAssetStore(forbidden)
    value = _asset()
    value.user_tags = ["人工保留"]
    value.segments[0].description = "人工片段说明"
    foreign = _asset("foreign", workspace="workspace-b")
    stamp = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("app.materials.assets.utc_now", lambda: stamp)
    with connect() as conn:
        _insert(conn, value, column_visibility="shared")
        _insert(conn, foreign)
    foreign_before = _snapshot(connect)["material_assets"][1]
    reasons = {
        "probe_invalid_media": "媒体信息无效；请检查或更换原文件，当前不会自动重试。",
        "probe_timeout": "读取媒体信息超时；请检查磁盘和文件，当前不会自动重试。",
        "probe_output_limit": "媒体信息输出超出上限；请检查文件，当前不会自动重试。",
        "probe_failed": "无法读取媒体信息；请检查磁盘和文件，当前不会自动重试。",
    }
    with connect() as conn:
        conn.execute("BEGIN")
        raw = store._probe_asset_source_row(conn, value.workspace_id, value.id)["payload"]
        for reason, message in reasons.items():
            traces.clear()
            failed = store.mark_probe_failed(conn, value.workspace_id, value.id, raw, reason)
            assert len(traces) == 1 and traces[0].startswith("UPDATE material_assets")
            expected = asdict(value)
            expected.update(analysis_status="failed", analysis_message=message, updated_at=stamp)
            assert asdict(failed) == expected
        before_unknown = tuple(conn.execute("SELECT * FROM material_assets WHERE id=?", (value.id,)).fetchone())
        traces.clear()
        with pytest.raises(KeyError) as error:
            store.mark_probe_failed(conn, value.workspace_id, value.id, raw, "unknown-reason")
        assert error.value.args == ("unknown-reason",)
        assert traces == []
        assert tuple(conn.execute("SELECT * FROM material_assets WHERE id=?", (value.id,)).fetchone()) == before_unknown
        assert conn.execute("SELECT visibility FROM material_assets WHERE id=?", (value.id,)).fetchone()[0] == "shared"
    assert _snapshot(connect)["material_assets"][1] == foreign_before


def test_probe_outcomes_share_caller_transaction_observer_isolation_and_full_rollback(database):
    connect, _traces = database

    def forbidden():
        raise AssertionError("probe methods must not replace the caller connection")

    store = MaterialAssetStore(forbidden)
    value = _asset()
    with connect() as conn:
        _insert(conn, value, column_visibility="shared")
    before = _snapshot(connect)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        original = store._probe_asset_source_row(conn, value.workspace_id, value.id)
        metadata = store.apply_probe_metadata(conn, value.workspace_id, value.id, original["payload"], 3.25)
        changed = store._probe_asset_source_row(conn, value.workspace_id, value.id)
        assert json.loads(changed["payload"])["duration_seconds"] == 3.25
        failed = store.mark_probe_failed(conn, value.workspace_id, value.id, changed["payload"], "probe_failed")
        assert failed.duration_seconds == metadata.duration_seconds == 3.25
        assert failed.analysis_status == "failed"
        assert conn.in_transaction
        with connect() as observer:
            visible = store._probe_asset_source_row(observer, value.workspace_id, value.id)
            assert visible["payload"] == original["payload"]
        assert conn.in_transaction
        conn.rollback()
    assert _snapshot(connect) == before
