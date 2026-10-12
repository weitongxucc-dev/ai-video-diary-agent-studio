"""Shipped Node extension reads the actual member-scoped API before a model call."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import app.assistant_pi as pi
from app.config import Settings
from test_assistant_memory_api import memory_client as memory_client, write


@pytest.fixture
def memory_host(memory_client):
    calls = []
    client = memory_client.client

    class Bridge(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            result = client.get(self.path, headers=dict(self.headers))
            self.send_response(result.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result.content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    executable = pi._pi_executable()
    if executable is None:
        server.shutdown()
        pytest.skip("shipped Node/Pi unavailable")
    env = {**os.environ, "PATH": str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", ""),
           "AI_VIDEO_DIARY_AGENT_API_BASE": f"http://127.0.0.1:{server.server_port}",
           "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": memory_client.alice["X-Local-Member-Token"]}
    try:
        yield env, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shipped_node_reads_saved_preferences_and_forget_removes_them(memory_client, memory_host, tmp_path):
    env, calls = memory_host
    assert write(memory_client, "记住我喜欢短句")[0].status_code == 200
    extension = Path(__file__).parents[1] / "agent_tools/diary_tools.ts"
    assert pi._read_memory_before_pi(extension, env, tmp_path)["style_preferences"] == {"表达风格": "短句"}
    assert write(memory_client, "忘记表达风格", action="forget", value=None)[0].status_code == 200
    assert pi._read_memory_before_pi(extension, env, tmp_path)["style_preferences"] == {}
    assert calls == ["/api/assistant/memory", "/api/assistant/memory"]


def test_invalid_member_blocks_host_preflight_instead_of_using_other_memory(memory_host, tmp_path):
    env, _ = memory_host
    extension = Path(__file__).parents[1] / "agent_tools/diary_tools.ts"
    with pytest.raises(pi.AssistantUnavailable) as failure:
        pi._read_memory_before_pi(extension, {**env, "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": "invalid"}, tmp_path)
    assert failure.value.cause == pi.FAILURE_TOOL_FAILED


def test_each_model_turn_receives_current_memory_as_data_and_keeps_original_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr(pi, "_pi_executable", lambda: "/controlled/pi")
    monkeypatch.setattr(pi, "_configured_model", lambda _settings: ("http://synthetic.invalid", "fixture", ""))
    monkeypatch.setattr(pi, "_agent_root", lambda *_args: tmp_path / "agent")
    monkeypatch.setattr(pi, "_record_service", lambda *_args, **_kwargs: None)
    memory = {"id": "cp_hidden", "revision": 1, "style_preferences": {"表达风格": "短句"}}
    monkeypatch.setattr(pi, "_read_memory_before_pi", lambda *_args: dict(memory))
    sent = []

    def run(_command, *, input, env, **_kwargs):
        sent.append((input, env["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"]))
        result = {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "受控回复"}], "stopReason": "stop"}}
        return __import__("subprocess").CompletedProcess([], 0, json.dumps(result) + "\n" + json.dumps({"type": "agent_settled"}), "")

    monkeypatch.setattr(pi.subprocess, "run", run)
    settings = Settings(model_root_path=str(tmp_path), database_path=str(tmp_path / "db.sqlite"))
    pi.run_plan_turn(settings, workspace_id="isolated", conversation_id="one", caller_user_id="alice", message="这次用长句")
    memory.update(revision=2, style_preferences={})
    pi.run_plan_turn(settings, workspace_id="isolated", conversation_id="two", caller_user_id="alice", message="我保存了哪些偏好")
    assert '"表达风格": "短句"' in sent[0][0] and "这次用长句" in sent[0][0]
    assert '"style_preferences": {}' in sent[1][0] and "短句" not in sent[1][0]
    assert [original for _, original in sent] == ["这次用长句", "我保存了哪些偏好"]
    assert all('"revision"' not in payload and "cp_hidden" not in payload for payload, _ in sent)
