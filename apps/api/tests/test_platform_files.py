"""Exercise actual OS locks, process exit and publication on each CI host."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.platform_files import durable_replace, lock_file, unlock_file


def test_shared_readers_exclude_writer_and_release_on_close(tmp_path):
    path = tmp_path / "中文许可.lock"
    with path.open("a+b") as first, path.open("a+b") as second:
        lock_file(first.fileno(), shared=True)
        lock_file(second.fileno(), shared=True)
        with path.open("a+b") as writer:
            with pytest.raises(BlockingIOError):
                lock_file(writer.fileno())
        unlock_file(first.fileno())
    with path.open("a+b") as writer:
        lock_file(writer.fileno())
        unlock_file(writer.fileno())


def test_other_process_cannot_take_lock_and_crash_releases_it(tmp_path):
    path = tmp_path / "permit.lock"
    code = """
import os
import sys
from app.platform_files import lock_file
with open(sys.argv[1], 'a+b') as file:
    lock_file(file.fileno())
    print('held', flush=True)
    sys.stdin.read(1)
    os._exit(19)
"""
    process = subprocess.Popen([sys.executable, "-u", "-c", code, str(path)],
                               cwd=Path(__file__).resolve().parents[1],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        assert process.stdout.readline().strip() == "held"
        with path.open("a+b") as contender:
            with pytest.raises(BlockingIOError):
                lock_file(contender.fileno())
        process.stdin.write("x")
        process.stdin.flush()
        assert process.wait(timeout=10) == 19
        with path.open("a+b") as contender:
            lock_file(contender.fileno())
            unlock_file(contender.fileno())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def test_durable_replace_publishes_complete_ledger(tmp_path):
    destination = tmp_path / "许可.state"
    destination.write_bytes(b"old")
    source = tmp_path / "new.tmp"
    with source.open("wb") as output:
        output.write(b'{"state":"held","generation":2}')
        output.flush()
        os.fsync(output.fileno())
    durable_replace(source, destination)
    assert destination.read_bytes() == b'{"state":"held","generation":2}'
    assert not source.exists()


def test_failed_replace_retains_existing_ledger(tmp_path):
    destination = tmp_path / "permit.state"
    destination.write_bytes(b"preserve")
    with pytest.raises(OSError):
        durable_replace(tmp_path / "missing", destination)
    assert destination.read_bytes() == b"preserve"
