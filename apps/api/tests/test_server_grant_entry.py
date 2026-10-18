"""Fixed child entry checks; these do not launch a packaged API or service."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
import ctypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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


@pytest.mark.parametrize("approval", [b"G", b"X"])
@pytest.mark.parametrize("failure", [None, "set", "read", "not_background"])
def test_background_entry_verifies_after_ack_and_never_execs_on_failure(monkeypatch, approval, failure, capsys):
    server = load_server()
    events = []
    def setpriority(which, who, value):
        assert (which, who, value) == (4, 0, 0x1000) and ctypes.get_errno() == 0
        events.append("set")
        if failure == "set":
            ctypes.set_errno(1)
            return -1
        return 0
    def getpriority(which, who):
        assert (which, who) == (4, 0) and ctypes.get_errno() == 0
        events.append("get")
        if failure == "read":
            ctypes.set_errno(22)
        return 0 if failure == "not_background" else 1
    monkeypatch.setattr(server, "sys", SimpleNamespace(platform="darwin", stderr=sys.stderr))
    monkeypatch.setattr(ctypes, "CDLL", lambda _, use_errno: SimpleNamespace(
        setpriority=Mock(side_effect=setpriority), getpriority=Mock(side_effect=getpriority)))
    monkeypatch.setattr(server.os, "execvp", lambda *args: events.append("exec"))
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, approval)
    finally:
        os.close(write_fd)
    ctypes.set_errno(99)
    if approval != b"G" or failure:
        with pytest.raises(SystemExit) as stopped:
            server._exec_granted_command([str(read_fd), "target"], cpu_mode="darwin_background")
        assert stopped.value.code == (126 if approval != b"G" else 125)
        assert "exec" not in events
        if approval != b"G":
            assert events == [] and "GRANT_CPU_POLICY" not in capsys.readouterr().err
        else:
            assert "state=failed" in capsys.readouterr().err
    else:
        server._exec_granted_command([str(read_fd), "target"], cpu_mode="darwin_background")
        assert events == ["set", "get", "exec"]
        assert "state=verified background=1 errno=0" in capsys.readouterr().err


def test_frozen_background_dispatch_uses_same_fixed_entry(monkeypatch):
    server = load_server()
    calls = []
    monkeypatch.setattr(server, "sys", SimpleNamespace(
        argv=["api-server", "--grant-exec-background", "10", "target"], frozen=True))
    monkeypatch.setattr(server, "_configure_env", lambda: pytest.fail("API environment loaded"))
    monkeypatch.setattr(server, "_exec_granted_command", lambda *args, **kwargs: calls.append((args, kwargs)))
    server.main()
    assert calls == [((["10", "target"],), {"cpu_mode": "darwin_background"})]


@pytest.mark.parametrize("approval", [b"G", b"X"])
@pytest.mark.parametrize("failure", [None, "set", "nice_read", "nice_mismatch", "bg_read", "bg_on"])
def test_nice_entry_requires_exact_nice_and_bg_zero_after_ack(monkeypatch, approval, failure, capsys):
    server = load_server()
    events = []
    def setpriority(which, who, value):
        assert (which, who, value) == (0, 0, 5) and ctypes.get_errno() == 0
        events.append("set")
        if failure == "set":
            ctypes.set_errno(1)
            return -1
        return 0
    def getpriority(which, who):
        assert who == 0 and ctypes.get_errno() == 0
        if which == 0:
            events.append("nice")
            if failure == "nice_read":
                ctypes.set_errno(22)
            return 4 if failure == "nice_mismatch" else 5
        assert which == 4
        events.append("bg")
        if failure == "bg_read":
            ctypes.set_errno(22)
        return 1 if failure == "bg_on" else 0
    monkeypatch.setattr(server, "sys", SimpleNamespace(platform="darwin", stderr=sys.stderr))
    monkeypatch.setattr(ctypes, "CDLL", lambda _, use_errno: SimpleNamespace(
        setpriority=Mock(side_effect=setpriority), getpriority=Mock(side_effect=getpriority)))
    monkeypatch.setattr(server.os, "execvp", lambda *args: events.append("exec"))
    read_fd, write_fd = os.pipe()
    os.write(write_fd, approval)
    os.close(write_fd)
    ctypes.set_errno(99)
    if approval != b"G" or failure:
        with pytest.raises(SystemExit) as stopped:
            server._exec_granted_command([str(read_fd), "target"], cpu_mode="darwin_nice", nice=5)
        assert stopped.value.code == (126 if approval != b"G" else 125)
        assert "exec" not in events
        if approval != b"G":
            assert events == [] and "GRANT_CPU_POLICY" not in capsys.readouterr().err
        else:
            assert "mode=darwin_nice state=failed" in capsys.readouterr().err
    else:
        server._exec_granted_command([str(read_fd), "target"], cpu_mode="darwin_nice", nice=5)
        assert events == ["set", "nice", "bg", "exec"]
        assert "mode=darwin_nice state=verified nice=5 background=0 errno=0" in capsys.readouterr().err


def test_frozen_nice_dispatch_precedes_api_imports_and_preserves_value(monkeypatch):
    server = load_server()
    calls = []
    monkeypatch.setattr(server, "sys", SimpleNamespace(
        argv=["api-server", "--grant-exec-nice", "5", "10", "target"], frozen=True))
    monkeypatch.setattr(server, "_configure_env", lambda: pytest.fail("API environment loaded"))
    monkeypatch.setattr(server, "_exec_granted_command", lambda *args, **kwargs: calls.append((args, kwargs)))
    server.main()
    assert calls == [((["10", "target"],), {"cpu_mode": "darwin_nice", "nice": 5})]


@pytest.mark.parametrize("nice", [None, 0, -1, 21, True, 5., "5"])
def test_nice_invalid_value_never_reaches_native_setter(monkeypatch, nice):
    server = load_server()
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: pytest.fail("Invalid priority reached libc"))
    with pytest.raises(OSError):
        server._set_nice_cpu_policy(nice)
