"""Complete caller-connection reads with native isolated SQLite and canonical values."""
from app.creation import plan_contracts
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app import production
from app.creation import contracts, task_input_reads


def records():
    created = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    task = contracts.CreationTask(
        id="task", workspace_id="ws", plan_id="plan", name="合成任务", scenario="knowledge_explanation",
        stage="narration", stage_status="failed", status="failed", progress=37,
        recovery_point="narration", selected_topic_id="topic", confirmed_script_id="script",
        stage_reason="narration_generation_failed", stage_impact="合成影响",
        stage_repair_action="压缩文案后重试", stage_retryable=True,
        visibility="shared", owner_user_id="synthetic-member",
        created_at=created, updated_at=created + timedelta(seconds=1),
    )
    segment = contracts.NarrationSegment(
        id="segment", workspace_id="ws", task_id="task", script_id="script",
        source_section_id="section", text="合成旁白", audio_reference="artifacts/synthetic.wav",
        duration_seconds=2.5, sample_rate=16000, model_id="synthetic-model",
        voice_profile_id="voice", voice_version="v1", language="zh-CN",
        pronunciation_version="dictionary-v1", status="ready", source_kind="recorded",
        source_artifact_ref="artifacts/recorded.wav", source_sha256="a" * 64,
        source_script_digest="b" * 64, created_at=created, updated_at=created + timedelta(seconds=2),
    )
    return task, segment


def test_canonical_complete_reads_keep_errors_and_caller_uncommitted_transaction(tmp_path):
    assert production._CreationTask is contracts.CreationTask
    assert production._NarrationSegment is contracts.NarrationSegment
    assert production._NarrationSegmentStatus is contracts.NarrationSegmentStatus
    assert production._validate_logical_reference is contracts.validate_logical_reference
    task, segment = records()
    payload = asdict(task)
    for name in ("created_at", "updated_at"):
        payload[name] = payload[name].isoformat()
    assert task_input_reads.creation_task_from_payload(payload) == task
    assert production.ProductionRepository._creation_task_from_payload(payload) == task
    assert task.needs_script_duration_repair and task.can_revise_script
    with pytest.raises(ValueError):
        task_input_reads.creation_task_from_payload({**payload, "updated_at": "bad-date"})

    with closing(sqlite3.connect(tmp_path / "inputs.sqlite3")) as conn, closing(
        sqlite3.connect(tmp_path / "inputs.sqlite3")
    ) as observer:
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE creation_tasks (workspace_id TEXT, id TEXT, payload TEXT);
            CREATE TABLE narration_segments (
                id TEXT, workspace_id TEXT, task_id TEXT, script_id TEXT,
                source_section_id TEXT, text TEXT, audio_reference TEXT,
                duration_seconds REAL, sample_rate INTEGER, model_id TEXT,
                voice_profile_id TEXT, voice_version TEXT, language TEXT,
                pronunciation_version TEXT, status TEXT, source_kind TEXT,
                source_artifact_ref TEXT, source_sha256 TEXT, source_script_digest TEXT,
                created_at TEXT, updated_at TEXT
            );
        """)
        conn.execute("INSERT INTO creation_tasks VALUES (?, ?, ?)", ("ws", task.id, json.dumps(payload)))
        conn.execute("INSERT INTO creation_tasks VALUES (?, ?, ?)", ("ws", "broken", '{"id":"broken"}'))
        nullable = replace(
            segment, id="nullable", audio_reference=None, duration_seconds=None, sample_rate=None,
            model_id=None, voice_profile_id=None, voice_version=None, status="pending",
            source_kind="generated", source_artifact_ref=None, source_sha256=None, source_script_digest=None,
        )
        for item in (segment, nullable):
            values = asdict(item)
            for name in ("created_at", "updated_at"):
                values[name] = values[name].isoformat()
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            conn.execute(f"INSERT INTO narration_segments ({columns}) VALUES ({placeholders})", tuple(values.values()))
        assert conn.in_transaction
        assert observer.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0

        statements = []
        conn.set_trace_callback(statements.append)
        assert task_input_reads.require_creation_task(conn, "ws", task.id) == task
        assert production.ProductionRepository._require_creation_task(conn, "ws", task.id) == task
        for item in (segment, nullable):
            row = conn.execute("SELECT * FROM narration_segments WHERE id = ?", (item.id,)).fetchone()
            decoded = task_input_reads.narration_segment_from_row(row)
            assert decoded == item
            assert isinstance(decoded, contracts.NarrationSegment)
            assert production.ProductionRepository._narration_segment_from_row(row) == item
        with pytest.raises(KeyError) as absent:
            task_input_reads.require_creation_task(conn, "ws", "absent")
        assert absent.value.args == ("absent",)
        with pytest.raises(KeyError):
            production.ProductionRepository._require_creation_task(conn, "other", task.id)
        with pytest.raises(TypeError):
            task_input_reads.require_creation_task(conn, "ws", "broken")
        conn.set_trace_callback(None)
        assert conn.in_transaction
        assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert observer.execute("SELECT COUNT(*) FROM narration_segments").fetchone()[0] == 0
        conn.rollback()
        assert observer.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0
        with pytest.raises(KeyError):
            task_input_reads.require_creation_task(conn, "ws", task.id)

    for reference in ("artifacts/relative.wav", "workspace://approved/source"):
        production._validate_logical_reference(reference, "引用")
    for reference, error in (("  ", "不能为空"), ("/absolute.wav", "不能是绝对路径"), ("file://relative.wav", "不能是绝对路径")):
        with pytest.raises(ValueError, match=error):
            contracts.validate_logical_reference(reference, "引用")
    (tmp_path / "input-read-observations.json").write_text(json.dumps({
        "canonical_aliases_same_objects": True,
        "actual_public_functions": [task_input_reads.require_creation_task.__module__ + "." + task_input_reads.require_creation_task.__name__,
                                    task_input_reads.creation_task_from_payload.__module__ + "." + task_input_reads.creation_task_from_payload.__name__,
                                    task_input_reads.narration_segment_from_row.__module__ + "." + task_input_reads.narration_segment_from_row.__name__],
        "production_static_delegates_called": 3,
        "caller_uncommitted_visible_and_observer_invisible": True,
        "complete_generated_and_recorded_rows": 2,
        "only_select_statements_during_reader_phase": statements,
        "caller_rollback_removed_rows": True,
        "repository_constructor_or_model_media_execution": False,
    }, ensure_ascii=False, indent=2) + "\n")


def test_narration_rows_and_public_reads_keep_scope_selector_and_caller_transaction(tmp_path):
    task, segment = records()
    library = tmp_path / "library"
    library.mkdir()
    repo = production.ProductionRepository(
        tmp_path / "narration-reads.sqlite3", library,
        duration_reader=lambda _: 2.0, scene_reader=lambda *_: [],
    )
    plan = plan_contracts.CreationPlan(
        id=task.plan_id, workspace_id=task.workspace_id, goal="合成读取", name="读取夹具",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=60,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="合成记录",
        model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint="reads-fixture",
    )
    task = repo.plan_confirmation.confirm_creation_plan(plan, task)[1]
    segment = replace(segment, source_kind="generated", source_artifact_ref=None,
                      source_sha256=None, source_script_digest=None)

    def insert(conn, item):
        values = asdict(item)
        for name in ("created_at", "updated_at"):
            values[name] = values[name].isoformat()
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO narration_segments ({columns}) VALUES ({placeholders})", tuple(values.values()))

    with closing(repo._connect()) as conn, closing(repo._connect()) as observer:
        borrowed = replace(segment, id="borrowed", source_section_id="borrowed")
        insert(conn, borrowed)
        assert conn.in_transaction
        statements = []
        conn.set_trace_callback(statements.append)
        raw = task_input_reads.narration_segment_row(conn, "ws", task.id, borrowed.id)
        assert isinstance(raw, sqlite3.Row) and raw["id"] == borrowed.id
        assert [r["id"] for r in task_input_reads.narration_segment_rows(conn, "ws", task.id, "script")] == [borrowed.id]
        assert task_input_reads.narration_segment_row(conn, "other", task.id, borrowed.id) is None
        assert task_input_reads.narration_segment_rows(conn, "ws", "other", "script") == []
        assert observer.execute("SELECT COUNT(*) FROM narration_segments").fetchone()[0] == 0
        with pytest.raises(KeyError) as uncommitted:
            repo.get_narration_segment("ws", task.id, borrowed.id)
        assert uncommitted.value.args == (borrowed.id,)
        assert conn.in_transaction
        conn.set_trace_callback(None)
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        conn.rollback()
        assert task_input_reads.narration_segment_row(conn, "ws", task.id, borrowed.id) is None
        assert observer.execute("SELECT COUNT(*) FROM narration_segments").fetchone()[0] == 0

    first = replace(segment, id="z-first", source_section_id="z-first")
    a = replace(segment, id="a", source_section_id="a", created_at=segment.created_at + timedelta(seconds=1))
    b = replace(a, id="b", source_section_id="b")
    override = replace(segment, id="override", source_section_id="override", script_id="override-script")
    empty = replace(segment, id="empty-script", source_section_id="empty", script_id="")
    with repo._connect() as conn:
        for item in (b, first, a, override, empty):
            insert(conn, item)
    assert repo.get_narration_segment("ws", task.id, first.id) == first
    assert repo.list_narration_segments("ws", task.id) == [first, a, b]
    assert repo.list_narration_segments("ws", task.id, "") == [first, a, b]
    assert repo.list_narration_segments("ws", task.id, "override-script") == [override]
    assert repo.list_narration_segments("ws", task.id, "missing-script") == []
    with pytest.raises(KeyError) as absent:
        repo.get_narration_segment("ws", task.id, "missing-segment")
    assert absent.value.args == ("missing-segment",)
    with pytest.raises(KeyError) as wrong_workspace:
        repo.get_narration_segment("other", task.id, first.id)
    assert wrong_workspace.value.args == (task.id,)
    with pytest.raises(KeyError) as wrong_task:
        repo.list_narration_segments("ws", "missing-task")
    assert wrong_task.value.args == ("missing-task",)

    def confirmed(value):
        values = asdict(replace(task, confirmed_script_id=value))
        for name in ("created_at", "updated_at"):
            values[name] = values[name].isoformat()
        with repo._connect() as conn:
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                         (json.dumps(values), "ws", task.id))

    confirmed(None)
    assert repo.list_narration_segments("ws", task.id) == []
    assert repo.list_narration_segments("ws", task.id, "override-script") == [override]
    confirmed("")
    assert repo.list_narration_segments("ws", task.id) == [empty]
    (tmp_path / "narration-read-observations.json").write_text(json.dumps({
        "actual_raw_readers": [task_input_reads.narration_segment_row.__module__ + ".narration_segment_row",
                               task_input_reads.narration_segment_rows.__module__ + ".narration_segment_rows"],
        "public_get_and_list_exercised": True,
        "normal_repository_schema_and_caller_conn": True,
        "borrowed_uncommitted_visible_observer_invisible_and_caller_rollback": True,
        "public_own_conn_did_not_read_uncommitted_segment": True,
        "only_select_statements_during_raw_reader_phase": statements,
        "canonical_DTO_status": first.status,
        "ordered_current_ids": [first.id, a.id, b.id],
        "confirmed_None_empty_and_confirmed_empty_string_query_distinguished": True,
        "old_test_nodes_executed": 0,
    }, ensure_ascii=False, indent=2) + "\n")
