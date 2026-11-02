"""Independent host boundaries: no app.main, database or model account required."""
from __future__ import annotations
from assistant_sdk_fixtures import project_event_fixture, sdk_completed

import ast
import json
import os
import shutil
import subprocess
import sys
import threading
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import psutil

import app.assistant_pi as host
from app.assistant.runtime_context import prepare_runtime
from app.assistant.tool_sources import tool_sources_readable
from app.assistant.turn_control import (
    AssistantProcessReleaseError, TurnControl, _capture_process_group,
)
from app.config import Settings


def test_concrete_modules_do_not_import_assembly_or_routes():
    root = Path(host.__file__).parent / "assistant"
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"app.assistant_pi", "app.main", "app.production"}
            if isinstance(node, ast.Import):
                assert not any(alias.name in {"app.assistant_pi", "app.main", "app.production"} for alias in node.names)
    tree = ast.parse(Path(host.__file__).read_text())
    definitions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert "_PiStreamProjector" not in definitions and "AssistantUnavailable" not in definitions
    assert "_terminate_process_group" not in definitions
    assert "_active_turn_processes" not in Path(host.__file__).read_text()


def test_acknowledged_stop_terminates_real_owned_process_and_releases_marker(tmp_path):
    turn_id = "independent-host-stop"
    entered = threading.Event()
    failures = []
    host.register_turn_control(turn_id)

    def launch():
        try:
            host._run_pi_streaming([sys.executable, "-u", "-c",
                "import json,time; print(json.dumps({'type':'tool_execution_start','toolName':'read_current_task','toolCallId':'probe'}),flush=True); time.sleep(30)"],
                message="synthetic stop", env={"PATH": os.environ["PATH"]}, cwd=tmp_path,
                timeout_seconds=5, on_event=lambda _event: entered.set(), turn_id=turn_id)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=launch)
    worker.start()
    try:
        assert entered.wait(3)
        assert host.request_stop_turn(turn_id)
        worker.join(3)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], host.AssistantStopped)
        assert host._turn_stop_events[turn_id].is_set()
    finally:
        if worker.is_alive():
            host.request_stop_turn(turn_id)
            worker.join(3)
        host.forget_turn_control(turn_id)


def test_stopped_preflight_cannot_launch_second_child(tmp_path, monkeypatch):
    host.register_turn_control("stopped-before-launch")
    assert host.request_stop_turn("stopped-before-launch")
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("stopped turn launched a child"))
    try:
        for _ in range(2):
            with pytest.raises(host.AssistantStopped):
                host._run_pi_streaming(["synthetic"], message="", env={}, cwd=tmp_path,
                    timeout_seconds=1, on_event=lambda _event: None, turn_id="stopped-before-launch")
        assert host._turn_stop_events["stopped-before-launch"].is_set()
    finally:
        host.forget_turn_control("stopped-before-launch")


def test_streaming_projection_preserves_original_nested_receipt_and_final_answer(tmp_path):
    receipt = {"tool_name": "read_task_media_job", "tool_call_id": "source-read", "is_error": False,
        "result": {"kind": "task_media_job", "media_job": {"job_id": "source-job", "status": "succeeded",
            "result_kind": "graphic_source", "result": {"source_ref": "artifact:synthetic", "artifact_id": "synthetic"}}}}
    events = [
        {"type": "tool_execution_end", "toolName": "codemode", "toolCallId": "parent", "isError": False,
            "result": {"details": {"business_receipts": [receipt], "calls": [], "incomplete_calls": False}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "model claims applied"}]}},
        {"type": "agent_settled"},
    ]
    lines = "\n".join(json.dumps(event) for event in events)
    projected = []
    result = host._run_pi_streaming([sys.executable, "-u", "-c", "import sys; print(sys.stdin.read())"],
        message=lines, env={"PATH": os.environ["PATH"]}, cwd=tmp_path, timeout_seconds=3, on_event=projected.append)
    assert result.returncode == 0
    assert json.loads(result.stdout.splitlines()[0])["result"]["details"]["business_receipts"] == [receipt]
    answer, preview = project_event_fixture(result.stdout)
    assert "尚未应用" in answer and "PNG来源已生成" in answer
    assert preview["media_job"]["job_id"] == "source-job"
    assert any(event["type"] == "tool_end" for event in projected)


@pytest.fixture
def loopback_tools(tmp_path):
    calls = []
    workspace = {"task": {"id": "task_fixture", "current_timeline_version_id": None, "timeline_sequence": 0,
        "confirmed_script_id": None}, "montage": None, "narration": None, "subtitle_tracks": []}

    class Bridge(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("X-Local-Member-Token")))
            if self.path == "/api/assistant/memory":
                body = {"id": "synthetic-profile", "revision": 1, "common_expressions": [], "banned_words": [], "style_preferences": {"tone": "plain"}}
            elif self.path.endswith("/execution"):
                body = {"task_id": "task_fixture", "job": None}
            elif self.path.endswith("/assistant/edit-capabilities"):
                body = {"task_id": "task_fixture", "timeline_version_id": None, "timeline_sequence": 0,
                    "schema": {"type": "object", "properties": {}}, "supported_operations": []}
            elif self.path.endswith("/assistant/media-script-capabilities"):
                body = {"task_id": "task_fixture", "schema": {"type": "object", "properties": {}}, "available": False}
            elif self.path == "/api/media-script-tools/capabilities":
                body = {"schemas": {action: {"type": "object", "properties": {}} for action in ("save", "update", "revoke", "invoke")},
                    "invocation_phase": "script", "result_kind": "graphic_source"}
            else:
                assert self.path == "/api/creation-tasks/task_fixture"
                body = workspace
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tools = tmp_path / "Resources" / "agent-tools"
    shutil.copytree(Path(__file__).parents[1] / "agent_tools", tools, ignore=shutil.ignore_patterns("*.mjs"))
    shutil.copytree(Path(__file__).parents[1] / "agent_runtime", tools.parent / "agent_runtime")
    assert tool_sources_readable(tools / "diary_tools.ts")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
        "AI_VIDEO_DIARY_AGENT_API_BASE": f"http://127.0.0.1:{server.server_port}",
        "AI_VIDEO_DIARY_AGENT_TASK_ID": "task_fixture", "AI_VIDEO_DIARY_AGENT_STAGE": "montage",
        "AI_VIDEO_DIARY_AGENT_TASK_VERSION": json.dumps({"timeline_version_id": None, "timeline_sequence": 0}),
        "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": "synthetic-member"}
    try:
        yield tools / "diary_tools.ts", env, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_real_host_preflight_imports_packaged_nested_sources_before_model(tmp_path, loopback_tools):
    extension, env, calls = loopback_tools
    facts = host._read_task_before_pi(extension, env, tmp_path)
    memory = host._read_memory_before_pi(extension, env, tmp_path)
    assert facts["task_id"] == "task_fixture"
    assert facts["_edit_capabilities"]["schema"] == {"type": "object", "properties": {}}
    assert memory["style_preferences"] == {"tone": "plain"}
    assert calls == [(path, "synthetic-member") for path in (
        "/api/creation-tasks/task_fixture", "/api/creation-tasks/task_fixture/execution",
        "/api/creation-tasks/task_fixture/assistant/edit-capabilities",
        "/api/creation-tasks/task_fixture/assistant/media-script-capabilities", "/api/media-script-tools/capabilities",
        "/api/assistant/memory")]


def test_runtime_configuration_keeps_session_scope_and_secret_outside_model_file(tmp_path):
    arguments = dict(root=tmp_path, pi_executable="/synthetic/pi", base_url="https://synthetic.invalid/v1",
        model="MiniMax-M3", key="synthetic-private-key", provider="minimax", environment={"PATH": "/synthetic/bin"},
        workspace_id="workspace", session_key="conversation:one", task_id="task_fixture", caller_user_id="member-one",
        stage="montage", selected_refs=["clip:one"], playhead_ms=100, authorization=None, cookie=None, local_member_token="synthetic-member",
        material_selection=None, profile_reference=None, knowledge_reference=None, task_version=None,
        turn_id="synthetic-turn", creation_request=None, message="original instruction", tool_names="read_current_task,codemode")
    runtime, env, tools, session = prepare_runtime(**arguments)
    assert env["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == "original instruction"
    assert env["AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN"] == "synthetic-member"
    assert tools == "read_current_task,codemode" and env["AI_VIDEO_DIARY_AGENT_PI_MODULE"] == ""
    model_file = runtime / "models.json"
    assert model_file.stat().st_mode & 0o777 == 0o600
    assert "synthetic-private-key" not in model_file.read_text()
    assert json.loads(model_file.read_text())["providers"]["ai-video-diary"]["models"][0]["samplingParams"] == {"reasoning_split": True}
    other = prepare_runtime(**{**arguments, "caller_user_id": "member-two"})
    assert other[3] != session and other[0] != runtime


def test_public_turn_binds_real_preflight_before_process_and_preserves_selected_session(tmp_path, monkeypatch, loopback_tools):
    extension, env, calls = loopback_tools
    monkeypatch.setattr("app.assistant.runtime_context.sdk_module", lambda _: Path("/synthetic/index.js"))
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXTENSION", str(extension))
    monkeypatch.setenv("API_PORT", env["AI_VIDEO_DIARY_AGENT_API_BASE"].rsplit(":", 1)[1])
    monkeypatch.setattr(host, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr(host, "_pi_executable", lambda: "/synthetic/pi")
    monkeypatch.setattr(host, "_configured_model", lambda _settings: ("https://synthetic.invalid/v1", "fixture", "synthetic-private-key"))
    monkeypatch.setattr(host, "_agent_root", lambda *_args: tmp_path / "agent")
    observations = []
    monkeypatch.setattr(host, "_record_service", lambda *_args, **kwargs: observations.append(kwargs))
    original_runner = host._run_pi_streaming
    launches = []

    def controlled_runner(command, **kwargs):
        if "--experimental-strip-types" in command:
            return original_runner(command, **kwargs)
        assert len(calls) == 6 and calls[0][0] == "/api/assistant/memory"
        assert kwargs["env"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == "original instruction"
        assert kwargs["env"]["AI_VIDEO_DIARY_AGENT_TASK_READ_READY"] == "1"
        assert json.loads(kwargs["env"]["AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES"])["schema"] == {"type": "object", "properties": {}}
        assert str(extension) == json.loads(kwargs["message"])["extensionPath"]
        assert "synthetic-private-key" not in " ".join(command)
        assert "synthetic-profile" not in kwargs["message"]
        assert "creator_preferences" in kwargs["message"] and "plain" in kwargs["message"]
        launches.append(command)
        return sdk_completed(command, 0, json.dumps({"type": "message_end", "message": {
            "role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "synthetic answer"}]}})
            + "\n" + json.dumps({"type": "agent_settled"}), "")

    monkeypatch.setattr(host, "_run_pi_streaming", controlled_runner)
    events = []
    answer, preview = host.run_task_turn(Settings(), workspace_id="workspace", task_id="task_fixture",
        conversation_id="conversation", message="original instruction", stage="montage", selected_refs=[], playhead_ms=None,
        local_member_token="synthetic-member", caller_user_id="member", on_event=events.append,
        task_version={"timeline_version_id": None, "timeline_sequence": 0})
    assert answer == "synthetic answer" and preview is None
    assert len(launches) == 1
    assert observations == [{"ok": True, "cause": "verified", "workspace_id": "workspace"}]
    assert events[0]["type"] == "tool_start" and events[-1]["ok"] is True


def _live(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="real owned POSIX group boundary")
@pytest.mark.parametrize("leader_exits_first", [False, True])
def test_stop_ack_releases_term_ignoring_descendant_even_after_leader_exit(tmp_path, leader_exits_first):
    gate = tmp_path / "allow-leader-exit"
    child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-u','-c',sys.argv[1]],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True); "
        "assert child.stdout.readline().strip()=='ready'; print(child.pid,flush=True); "
        "gate=Path(sys.argv[2]);\nwhile not gate.exists(): time.sleep(.01)\n"
    )
    process = subprocess.Popen([sys.executable, "-u", "-c", parent_code, child_code, str(gate)],
        start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    control = TurnControl(terminate=host._terminate_process_group)
    event = control.begin("owned-group")
    control.bind("owned-group", process, event)
    child_pid = None
    # A distinct process/session proves that a group stop stays within ownership.
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    try:
        child_pid = int(process.stdout.readline())
        assert os.getpgid(child_pid) == process.pid
        if leader_exits_first:
            gate.touch()
            assert process.wait(timeout=3) == 0
            assert _live(child_pid)
        assert control.request_stop("owned-group") is True
        assert not _live(child_pid), "a parent exit is not a released child group"
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
        assert sentinel.poll() is None
        with pytest.raises(host.AssistantStopped):
            control.begin("owned-group")
    finally:
        # Only exact fixture groups are cleaned; no model or user process.
        if process.poll() is None or (child_pid is not None and _live(child_pid)):
            host._terminate_process_group(process)
        process.wait(timeout=3)
        sentinel.terminate()
        sentinel.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


def test_unknown_or_reused_group_identity_never_signals_or_acknowledges(tmp_path, monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    group = _capture_process_group(process)
    control = TurnControl(terminate=host._terminate_process_group)
    event = control.begin("identity-unknown")
    control.bind("identity-unknown", process, event)
    with monkeypatch.context() as patch:
        patch.setattr(group.probe, "children_released", lambda _children: None)
        patch.setattr(os, "killpg", lambda *_args: pytest.fail("unknown/reused group was signalled"))
        with pytest.raises(AssistantProcessReleaseError, match="身份已变化或释放状态未知"):
            control.request_stop("identity-unknown")
        with pytest.raises(host.AssistantStopped):
            control.begin("identity-unknown")
        assert control.stop_events["identity-unknown"].is_set()
    try:
        assert process.poll() is None
        assert control.request_stop("identity-unknown")
    finally:
        if process.poll() is None:
            host._terminate_process_group(process)
        process.wait(timeout=3)


def test_non_owned_parent_group_is_refused_without_group_signal(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", lambda *_args: pytest.fail("non-owned group was signalled"))
            with pytest.raises(AssistantProcessReleaseError, match="不是本轮独占"):
                host._terminate_process_group(process)
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_released_group_is_never_signalled_again_after_pid_reuse(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    group = _capture_process_group(process)
    host._terminate_process_group(process)
    assert process.poll() is not None and group.released
    # A terminal release proof belongs to the old group. Later PID/PGID reuse
    # cannot reactivate this reference or produce a second signal.
    monkeypatch.setattr(group.probe, "children_released", lambda *_args: pytest.fail("released reference was re-probed"))
    monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("released/reused group was signalled"))
    host._terminate_process_group(process)


def test_stop_waits_for_inflight_launch_registration_and_blocks_later_launches():
    control = TurnControl(terminate=host._terminate_process_group)
    event = control.begin("launch-race")
    launching = threading.Event()
    allow_launch = threading.Event()
    acknowledged = threading.Event()
    processes, failures = [], []

    def create():
        launching.set()
        assert allow_launch.wait(3)
        child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
        processes.append(child)
        return child

    def launch():
        try:
            control.launch("launch-race", event, create)
        except BaseException as error:
            failures.append(error)

    def stop():
        try:
            assert control.request_stop("launch-race")
            acknowledged.set()
        except BaseException as error:
            failures.append(error)

    launcher = threading.Thread(target=launch)
    stopper = threading.Thread(target=stop)
    launcher.start()
    try:
        assert launching.wait(3)
        stopper.start()
        assert not acknowledged.wait(.05), "a stop must not acknowledge before inflight Popen is registered"
        allow_launch.set()
        launcher.join(3)
        stopper.join(3)
        assert not failures and acknowledged.is_set()
        assert processes[0].poll() is not None
        with pytest.raises(host.AssistantStopped):
            control.launch("launch-race", event, lambda: pytest.fail("a second child was launched after stop"))
    finally:
        allow_launch.set()
        launcher.join(3)
        if stopper.ident is not None:
            stopper.join(3)
        for process in processes:
            if process.poll() is None:
                host._terminate_process_group(process)


def test_unsupported_group_platform_refuses_before_popen(tmp_path, monkeypatch):
    import app.assistant.turn_control as control_module
    monkeypatch.setattr(control_module.sys, "platform", "win32")
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("unsupported platform launched a child"))
    with pytest.raises(AssistantProcessReleaseError, match="未启动新的助手子进程"):
        host._run_pi_streaming(["synthetic"], message="", env={}, cwd=tmp_path,
            timeout_seconds=1, on_event=lambda _event: None)


def test_native_release_proof_runs_after_host_worker_drains_and_keeps_unreleased_stop_marker(tmp_path):
    control=TurnControl(terminate=host._terminate_process_group)
    control.begin('native-release')
    active,finish,drained=threading.Event(),threading.Event(),threading.Event()
    native_released={'value':False}
    proofs=[]
    def worker():
        active.set()
        assert finish.wait(1)
        drained.set()
    def verify(deadline):
        assert drained.is_set()
        proofs.append(deadline)
        if not native_released['value']:
            raise AssistantProcessReleaseError('native release remains unknown')
    control.launch_callback('native-release',threading.Thread(target=worker),finish.set,verify_release=verify)
    assert active.wait(1)
    with pytest.raises(AssistantProcessReleaseError,match='native release remains unknown'):
        control.request_stop('native-release')
    assert control.is_stopped('native-release') and 'native-release' in control._callbacks
    native_released['value']=True
    assert control.request_stop('native-release')
    assert len(proofs)==2 and 'native-release' not in control._callbacks
    control.forget('native-release')
    # An admitted job can own no process yet and still be scheduled later.
    # Exercise the real host stop consumer with that original queued receipt.
    class NativeOwner:
        complete = False
        def __call__(self, *args, **kwargs):
            pytest.fail('this stop fixture submits no native operation')
        def cancel(self, **kwargs):
            return {'jobs': [] if self.complete else [{'status':'queued','process_state':'not_started','release_state':'not_acquired'}],
                    'total_count':0 if self.complete else 1,'truncated':False,'cancellation_complete':self.complete}
    owner=NativeOwner()
    entered=threading.Event()
    failures=[]
    turn_id='native-queued-cancellation'
    host.register_turn_control(turn_id)
    def run_host():
        try:
            host._run_pi_streaming([sys.executable,'-u','-c',
                "import json,time; print(json.dumps({'type':'tool_execution_start','toolName':'read_current_task','toolCallId':'probe'}),flush=True); time.sleep(30)"],
                message='',env={'PATH':os.environ['PATH']},cwd=tmp_path,timeout_seconds=5,
                on_event=lambda event:entered.set(),turn_id=turn_id,native_handler=owner,
                source_binding={'turn_id':turn_id,'task_id':'task','conversation_id':'conversation','input_id':'input'})
        except BaseException as error:
            failures.append(error)
    worker=threading.Thread(target=run_host)
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(AssistantProcessReleaseError):
            host.request_stop_turn(turn_id)
        assert turn_id in host._turn_control._callbacks
        owner.complete=True
        assert host.request_stop_turn(turn_id)
        worker.join(3)
        assert not worker.is_alive() and turn_id not in host._turn_control._callbacks
    finally:
        owner.complete=True
        host.request_stop_turn(turn_id)
        worker.join(3)
        host.forget_turn_control(turn_id)
