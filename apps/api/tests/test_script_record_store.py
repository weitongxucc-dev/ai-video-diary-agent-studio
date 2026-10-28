"""Independent import and isolated persistence behavior of the document owner."""

from contextlib import contextmanager
from dataclasses import asdict
import builtins
import json
import sqlite3
import subprocess
import sys
import wave

import pytest

from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection, ScriptGenerationRun
from app.creation.script_records import ScriptRecordStore
from app.creation.script_windows import SCRIPT_SECTION_SCHEMA


def test_script_record_owner_cold_import_does_not_load_application_or_parent_repository():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; import app.creation.script_records; "
            "assert 'app.production' not in sys.modules; assert 'app.main' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_standalone_owner_roundtrips_its_own_rows_without_cross_domain_ports(tmp_path, monkeypatch):
    def forbidden_execution(*args, **kwargs):
        raise AssertionError("record metadata must not run a process, media or runtime")

    monkeypatch.setattr(subprocess, "Popen", forbidden_execution)
    monkeypatch.setattr(wave, "open", forbidden_execution)
    original_import = builtins.__import__

    def metadata_import(name, *args, **kwargs):
        if name.startswith(("app.main", "app.creation_execution", "app.device_",
                            "app.pi_", "app.runtime", "app.providers", "app.granted_subprocess")):
            forbidden_execution()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", metadata_import)
    assert "app.main" not in sys.modules
    assert "app.device_runtime" not in sys.modules
    path = tmp_path / "scripts.sqlite"
    connections = []

    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        connections.append(conn)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with connect() as conn:
        conn.execute(
            "CREATE TABLE creation_task_scripts(id TEXT PRIMARY KEY,workspace_id TEXT,task_id TEXT,run_id TEXT,payload TEXT,updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE script_generation_runs(id TEXT PRIMARY KEY,workspace_id TEXT,task_id TEXT,payload TEXT,started_at TEXT)"
        )
        for statement in SCRIPT_SECTION_SCHEMA:
            conn.execute(statement)

    def unavailable(*args, **kwargs):
        raise AssertionError(
            "own-record persistence must not invoke a Task/Plan/event port"
        )

    store = ScriptRecordStore(
        connect,
        read_task=unavailable,
        require_task=unavailable,
        decode_plan=unavailable,
        read_topic=unavailable,
        transition_task=unavailable,
        create_library_task=unavailable,
        knowledge_inputs_current=unavailable,
        append_event=unavailable,
        get_event=unavailable,
        decode_event=unavailable,
    )
    script = CreationTaskScript(
        id="script",
        workspace_id="workspace",
        task_id="task",
        run_id="run",
        topic_id="topic",
        title="保留原文",
        language="zh",
        sections=[
            CreationTaskScriptSection(
                id="section",
                title="标题",
                content=" 原文\n",
                visual_prompt="画面",
                evidence_reference_ids=[],
            )
        ],
    )
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payload = store._script_payload(conn, script)
        assert "sections" not in json.loads(payload)
        conn.execute(
            "INSERT INTO creation_task_scripts VALUES (?,?,?,?,?,?)",
            (
                script.id,
                script.workspace_id,
                script.task_id,
                script.run_id,
                payload,
                script.updated_at.isoformat(),
            ),
        )
    assert store.get_creation_task_script("workspace", "script") == script
    with connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM creation_task_script_sections"
            ).fetchone()[0]
            == 1
        )

    run = ScriptGenerationRun(
        id="run", workspace_id="workspace", task_id="task", plan_id="plan",
        topic_id="topic", status="running", route_snapshot={},
        prompt_schema_version="v1", input_references=[],
    )
    payload = json.dumps(asdict(run), default=str)
    with connect() as conn:
        conn.execute("INSERT INTO script_generation_runs VALUES (?,?,?,?,?)",
                     (run.id, run.workspace_id, run.task_id, payload, run.started_at.isoformat()))
    assert store.get_script_generation_run("workspace", run.id) == run
    with pytest.raises(KeyError, match="run"):
        store.get_script_generation_run("other-workspace", run.id)

    # A caller's default tuple rows and uncommitted write must remain visible
    # only in that same connection. The owner must neither open nor end it.
    borrowed = sqlite3.connect(path)
    try:
        borrowed.execute("BEGIN IMMEDIATE")
        pending = dict(asdict(run), id="pending")
        borrowed.execute("INSERT INTO script_generation_runs VALUES (?,?,?,?,?)",
                         ("pending", run.workspace_id, run.task_id,
                          json.dumps(pending, default=str), run.started_at.isoformat()))
        count = len(connections)
        assert store.get_script_generation_run("workspace", "pending", connection=borrowed).id == "pending"
        assert borrowed.in_transaction and len(connections) == count
        with pytest.raises(KeyError, match="pending"):
            store.get_script_generation_run("other-workspace", "pending", connection=borrowed)
        assert borrowed.in_transaction and len(connections) == count
        with connect() as outside:
            assert outside.execute("SELECT 1 FROM script_generation_runs WHERE id='pending'").fetchone() is None
        borrowed.rollback()
        with pytest.raises(KeyError, match="pending"):
            store.get_script_generation_run("workspace", "pending", connection=borrowed)
        assert not borrowed.in_transaction
        assert borrowed.execute("SELECT 1").fetchone() == (1,)
    finally:
        borrowed.close()
    assert "app.main" not in sys.modules
    assert "app.device_runtime" not in sys.modules
