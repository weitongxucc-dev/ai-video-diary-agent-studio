"""Independent import and isolated persistence behavior of the document owner."""

from contextlib import contextmanager
import json
import sqlite3
import subprocess
import sys

from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
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


def test_standalone_owner_roundtrips_its_own_rows_without_cross_domain_ports(tmp_path):
    path = tmp_path / "scripts.sqlite"

    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with connect() as conn:
        conn.execute(
            "CREATE TABLE creation_task_scripts(id TEXT PRIMARY KEY,workspace_id TEXT,task_id TEXT,run_id TEXT,payload TEXT,updated_at TEXT)"
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
        decode_topic=unavailable,
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
