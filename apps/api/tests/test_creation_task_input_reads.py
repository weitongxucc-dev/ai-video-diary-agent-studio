"""Complete caller-connection reads with native isolated SQLite and canonical values."""
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
    assert production.CreationTask is contracts.CreationTask
    assert production.NarrationSegment is contracts.NarrationSegment
    assert production.NarrationSegmentStatus is contracts.NarrationSegmentStatus
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
