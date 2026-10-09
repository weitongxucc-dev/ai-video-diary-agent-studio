"""Fixed child entry checks; these do not launch a packaged API or service."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SERVER = Path(__file__).resolve().parents[1] / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("isolated_server_entry", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("approval", [b"G", b"X", b""])
def test_fixed_entry_waits_for_pipe_without_dotenv_or_recovery(tmp_path, approval):
    database = tmp_path / "running-job.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE job_runs (id TEXT, status TEXT)")
        connection.execute("INSERT INTO job_runs VALUES ('unchanged', 'running')")
    before = database.read_bytes()
    dotenv = tmp_path / "must-not-load.env"
    dotenv.write_text("GRANT_TEST_DOTENV_LOADED=yes\n")
    marker = tmp_path / "executed"
    target = (
        "import os,pathlib; "
        "assert 'GRANT_TEST_DOTENV_LOADED' not in os.environ; "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    read_fd, write_fd = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(SERVER), "--grant-exec", str(read_fd),
             sys.executable, "-c", target],
            pass_fds=(read_fd,), start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "AI_VIDEO_DIARY_DOTENV": str(dotenv),
                 "DATABASE_PATH": str(database)},
        )
        os.close(read_fd)
        read_fd = -1
        # No byte is available: the child must remain blocked at the gate.
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        assert not marker.exists()
        if approval:
            os.write(write_fd, approval)
        os.close(write_fd)
        write_fd = -1
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == (0 if approval == b"G" else 126), (stdout, stderr)
        assert marker.exists() is (approval == b"G")
        assert database.read_bytes() == before
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT status FROM job_runs").fetchone() == ("running",)
    finally:
        for descriptor in (read_fd, write_fd):
            if descriptor >= 0:
                os.close(descriptor)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


@pytest.mark.parametrize("arguments", [
    ["--grant-exec"], ["--grant-exec", "invalid", "target"],
    ["--grant-exec", "-1", "target"], ["-c", "raise AssertionError('must not run')"],
])
def test_child_arguments_rejected_before_api_initialization(monkeypatch, arguments):
    server = load_server()
    monkeypatch.setattr(server, "sys", SimpleNamespace(argv=["api-server", *arguments]))
    monkeypatch.setattr(server, "_configure_env", lambda: pytest.fail("API environment loaded"))
    with pytest.raises(SystemExit):
        server.main()


def test_frozen_child_dispatch_precedes_all_api_imports(monkeypatch):
    server = load_server()
    calls = []
    monkeypatch.setattr(server, "sys", SimpleNamespace(
        argv=["api-server", "--grant-exec", "10", "target", "argument"], frozen=True,
    ))
    monkeypatch.setattr(server, "_configure_env", lambda: pytest.fail("API environment loaded"))
    monkeypatch.setattr(server, "_exec_granted_command", calls.append)
    server.main()
    assert calls == [["10", "target", "argument"]]
