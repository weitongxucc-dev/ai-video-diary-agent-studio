import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from app.assistant.windows_process import WindowsJobProcess
from app.assistant.turn_control import AssistantProcessReleaseError, TurnControl, _capture_process_group, _terminate_process_group
from app.subprocess_environment import subprocess_environment

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Native Windows Job Object lifecycle")


def test_unregistered_process_is_refused_without_signalling_it():
    process = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(20)"])
    try:
        with pytest.raises(AssistantProcessReleaseError, match="Job Object"):
            _capture_process_group(process)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=5)


def test_separate_turn_jobs_do_not_stop_each_other(tmp_path):
    command = [sys._base_executable, "-c", "import time; time.sleep(20)"]
    environment = subprocess_environment({"PATH": os.environ["PATH"]})
    first = WindowsJobProcess(command, env=environment, cwd=tmp_path)
    second = None
    try:
        second = WindowsJobProcess(command, env=environment, cwd=tmp_path)
        first.terminate_group(time.monotonic() + 5)
        assert first.group_released()
        assert second.poll() is None and not second.group_released()
    finally:
        for process in (first, second):
            if process is not None:
                process.terminate_group(time.monotonic() + 5)
                process.close()


def test_failed_job_assignment_never_runs_the_child(monkeypatch, tmp_path):
    import win32job

    marker = tmp_path / "must-not-exist"
    def fail(*_):
        raise OSError("synthetic assignment failure")
    monkeypatch.setattr(win32job, "AssignProcessToJobObject", fail)
    with pytest.raises(OSError, match="assignment failure"):
        WindowsJobProcess([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
                          env=subprocess_environment({"PATH": os.environ["PATH"]}), cwd=tmp_path)
    assert not marker.exists()


@pytest.mark.parametrize("leader_exits_first", [False, True])
def test_acknowledged_stop_releases_descendants_and_preserves_unrelated_process(tmp_path, leader_exits_first):
    gate = tmp_path / "parent-may-exit"
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
        "print(child.pid,flush=True); gate=Path(sys.argv[1]);\n"
        "while not gate.exists(): time.sleep(.01)\n"
    )
    process = WindowsJobProcess([sys._base_executable, "-u", "-c", parent, str(gate)],
                                env=subprocess_environment({"PATH": os.environ["PATH"]}), cwd=tmp_path)
    control = TurnControl(_terminate_process_group)
    event = control.begin("windows-owned")
    control.bind("windows-owned", process, event)
    sentinel = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(20)"])
    try:
        descendant = psutil.Process(int(process.stdout.readline()))
        if leader_exits_first:
            gate.touch()
            assert process.wait(timeout=5) == 0
            assert not process.group_released()
        assert control.request_stop("windows-owned")
        assert process.group_released()
        assert not descendant.is_running()
        assert sentinel.poll() is None
        assert event.is_set()
        control.release("windows-owned", process, keep=False)
    finally:
        process.terminate_group(time.monotonic() + 5)
        process.close()
        sentinel.kill()
        sentinel.wait(timeout=5)


def test_host_crash_closes_job_and_releases_child(tmp_path):
    code = """
import os,sys
from app.assistant.windows_process import WindowsJobProcess
child=WindowsJobProcess([sys._base_executable,'-c','import time; time.sleep(15)'],env=dict(os.environ),cwd=os.getcwd())
print(child.pid,flush=True)
os._exit(23)
"""
    supervisor = subprocess.run([sys.executable, "-u", "-c", code], cwd=Path(__file__).parents[1],
                                capture_output=True, text=True, timeout=10)
    assert supervisor.returncode == 23, supervisor.stderr
    pid = int(supervisor.stdout)
    deadline = time.monotonic() + 5
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not psutil.pid_exists(pid)
