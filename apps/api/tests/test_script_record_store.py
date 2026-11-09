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
            "assert 'app.production' not in sys.modules; assert 'app.main' not in sys.modules; "
            "assert 'app.device_runtime' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("input_form", ["dataclass", "json"])
def test_standalone_owner_roundtrips_its_own_rows_without_cross_domain_ports(tmp_path, monkeypatch, input_form):
    forbidden_prefixes = ("app.main", "app.creation_execution", "app.device_",
                          "app.pi_", "app.runtime", "app.providers", "app.granted_subprocess")
    previously_loaded = {name for name in sys.modules if name.startswith(forbidden_prefixes)}

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("record metadata must not run a process, media or runtime")

    monkeypatch.setattr(subprocess, "Popen", forbidden_execution)
    monkeypatch.setattr(wave, "open", forbidden_execution)
    original_import = builtins.__import__

    def metadata_import(name, *args, **kwargs):
        if name.startswith(forbidden_prefixes):
            forbidden_execution()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", metadata_import)
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
        from types import SimpleNamespace
        from script_seed_fixtures import script_seed_payload
        seed = script if input_form == "dataclass" else json.loads(json.dumps(asdict(script), default=str))
        payload = script_seed_payload(SimpleNamespace(script_records=store), conn, seed)
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

    # The public currentness reader owns one connection and uses the same
    # canonical Task decoder. Its policy port declares facts, not permissions.
    from app.creation.contracts import CreationTask
    from app.creation.task_input_reads import require_creation_task

    task = CreationTask(id="task", workspace_id="workspace", plan_id="plan",
                        name="隔离事实", scenario="knowledge_explanation")
    with connect() as conn:
        conn.execute("CREATE TABLE creation_tasks(workspace_id TEXT,id TEXT,payload TEXT)")
        conn.execute("INSERT INTO creation_tasks VALUES (?,?,?)",
                     (task.workspace_id, task.id, json.dumps(asdict(task), default=str)))

    traces = []
    policy_calls = []
    policy_result = False

    @contextmanager
    def current_connect():
        with connect() as conn:
            trace = []
            traces.append(trace)
            conn.set_trace_callback(trace.append)
            yield conn

    def current_policy(conn, workspace_id, task_id, plan_id, references):
        assert conn is connections[-1] and not conn.in_transaction
        assert (workspace_id, task_id, plan_id, references) == ("workspace", "task", "plan", [])
        policy_calls.append(conn)
        return policy_result

    current_store = ScriptRecordStore(
        current_connect, read_task=unavailable, require_task=require_creation_task,
        decode_plan=unavailable, read_topic=unavailable, transition_task=unavailable,
        create_library_task=unavailable, knowledge_inputs_current=current_policy,
        append_event=unavailable, get_event=unavailable, decode_event=unavailable,
    )
    assert current_store._require_creation_task is require_creation_task

    def closed_current_reader(count):
        assert len(connections) == count + 1
        assert not any(sql.lstrip().upper().startswith("BEGIN") for sql in traces[-1])
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[-1].execute("SELECT 1")

    for policy_result in (False, True):
        count = len(connections)
        assert current_store.script_knowledge_inputs_current("workspace", "task", "script") is policy_result
        assert policy_calls[-1] is connections[-1]
        assert "FROM creation_tasks" in traces[-1][0]
        assert "LEFT JOIN script_generation_runs" in traces[-1][1]
        closed_current_reader(count)
    assert len(policy_calls) == 2

    for task_id, script_id, missing in (("missing-task", "script", "missing-task"),
                                        ("task", "missing-script", "missing-script")):
        count = len(connections)
        with pytest.raises(KeyError) as failed:
            current_store.script_knowledge_inputs_current("workspace", task_id, script_id)
        assert failed.value.args == (missing,) and len(policy_calls) == 2
        assert len(traces[-1]) == (1 if missing == "missing-task" else 2)
        closed_current_reader(count)
    assert {name for name in sys.modules if name.startswith(forbidden_prefixes)} == previously_loaded


def test_positive_seed_caller_owns_header_sections_and_task_transaction(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("seed transaction evidence forbids process, media and runtime")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(wave, "open", forbidden)
    from app.device_runtime import DeviceRuntime
    monkeypatch.setattr(DeviceRuntime, "start", forbidden)
    monkeypatch.setattr(DeviceRuntime, "run_once", forbidden)
    from app.production import ProductionRepository
    from test_production_foundation import _save_creation_task, _seed_confirmed_script
    repository = ProductionRepository(tmp_path / "seed.sqlite", tmp_path / "library",
        duration_reader=forbidden, scene_reader=forbidden)
    before = _save_creation_task(repository, workspace_id="seed-ws", task_id="seed-task")
    with repository._connect() as connection:
        connection.execute("""CREATE TRIGGER reject_seed_task_tail BEFORE UPDATE OF payload ON creation_tasks
            WHEN NEW.id='seed-task' BEGIN SELECT RAISE(ABORT,'seed task tail refused'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="seed task tail refused"):
        _seed_confirmed_script(repository, workspace_id="seed-ws", task_id="seed-task")
    assert repository.task_records.get_creation_task("seed-ws", "seed-task") == before
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM creation_task_scripts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM creation_task_script_sections").fetchone()[0] == 0
        connection.execute("DROP TRIGGER reject_seed_task_tail")
    script_id, sections = _seed_confirmed_script(repository, workspace_id="seed-ws", task_id="seed-task")
    actual = repository.script_records.get_creation_task_script("seed-ws", script_id)
    assert actual.selection_status == "confirmed"
    assert [asdict(section) for section in actual.sections] == sections
    task = repository.task_records.get_creation_task("seed-ws", "seed-task")
    assert task.confirmed_script_id == script_id and task.stage == "narration"
    with repository._connect() as connection:
        payload = json.loads(connection.execute("SELECT payload FROM creation_task_scripts WHERE id=?",
            (script_id,)).fetchone()[0])
        assert payload["_sections_storage"] == "rows" and "sections" not in payload
        assert connection.execute("SELECT COUNT(*) FROM creation_task_script_sections WHERE script_id=?",
            (script_id,)).fetchone()[0] == len(sections)
