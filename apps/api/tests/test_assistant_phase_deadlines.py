"""Phase clocks and owned-child cancellation; no provider, media or user DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.assistant.process_runner import AssistantPhaseTimeout, _PhaseDeadlines, run_pi_streaming
from app.assistant.projection import _PiStreamProjector, _business_event_lines
from app.assistant.turn_control import TurnControl, _terminate_process_group


def tool(call, end=False):
    return {"type": "tool_execution_end" if end else "tool_execution_start", "toolCallId": call}


def request(call, identity, end=False):
    return {"type": "diary_business_request", "phase": "end" if end else "start",
            "tool_call_id": call, "request_id": identity, "write": True, "timeout_ms": 120000}


def test_two_slow_business_requests_do_not_spend_either_model_call_budget():
    clock = _PhaseDeadlines(180, 0)
    for at, event in [(0, {"type": "turn_start"}),
        (170, {"type": "message_end", "message": {"role": "assistant"}}),
        (171, tool("review")), (172, request("review", "context")),
        (282, request("review", "context", True)), (283, request("review", "post")),
        (393, request("review", "post", True)), (394, tool("review", True)),
        (395, {"type": "turn_end"}), (396, {"type": "turn_start"})]:
        assert clock.expired(at) is None
        clock.observe(event, at)
    assert clock.expired(575) is None
    assert clock.expired(576) == ("model", 180)


@pytest.mark.parametrize("event", [
    {"type": "turn_start"}, {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "仍在输出"}},
    {"type": "heartbeat"}, {"type": "tool_execution_update", "toolCallId": "missing"},
])
def test_text_duplicate_boundary_and_heartbeat_never_extend_one_model_call(event):
    clock = _PhaseDeadlines(180, 0)
    clock.observe({"type": "turn_start"}, 0)
    clock.observe(event, 179)
    assert clock.expired(180) == ("model", 180)


def test_parallel_requests_cannot_pause_another_stuck_tool():
    clock = _PhaseDeadlines(180, 0)
    clock.observe(tool("stuck"), 0)
    clock.observe(tool("busy"), 0)
    clock.observe(request("busy", "first"), 1)
    clock.observe(request("busy", "first", True), 111)
    clock.observe(request("busy", "second"), 112)
    assert clock.expired(179) is None
    assert clock.expired(180) == ("tool", 180)


def test_request_budget_is_120_seconds_and_duplicate_or_late_events_cannot_renew_it():
    clock = _PhaseDeadlines(180, 0)
    clock.observe(tool("work"), 0)
    clock.observe(request("work", "rpc"), 1)
    clock.observe(request("work", "rpc"), 119)
    clock.observe(request("other", "rpc", True), 120)
    assert clock.expired(121) == ("business_request", 120)
    clock.observe(request("work", "rpc", True), 122)
    clock.observe(request("work", "new"), 123)
    assert clock.expired(123) == ("business_request", 120)


def test_completed_request_id_cannot_be_reused_to_pause_tool_time():
    clock = _PhaseDeadlines(180, 0)
    clock.observe(tool("work"), 0)
    clock.observe(request("work", "rpc"), 1)
    clock.observe(request("work", "rpc", True), 111)
    clock.observe(request("work", "rpc"), 200)
    clock.observe(request("work", "rpc", True), 288)
    assert clock.expired(290) == ("tool", 180)


def test_tool_local_execution_budget_is_cumulative_across_distinct_requests():
    clock = _PhaseDeadlines(180, 0)
    clock.observe(tool("work"), 0)
    clock.observe(request("work", "one"), 50)
    clock.observe(request("work", "one", True), 160)
    clock.observe(request("work", "two"), 250)
    clock.observe(request("work", "two", True), 360)
    assert clock.expired(399) is None
    assert clock.expired(400) == ("tool", 180)


def test_tool_end_cannot_hide_an_unfinished_request():
    clock = _PhaseDeadlines(180, 0)
    clock.observe(tool("work"), 0)
    clock.observe(request("work", "rpc"), 1)
    clock.observe(tool("work", True), 2)
    assert clock.expired(121) == ("business_request", 120)


def test_sdk_compaction_boundaries_have_their_own_finite_budget():
    clock = _PhaseDeadlines(180, 0)
    clock.observe({"type": "compaction_start", "reason": "manual"}, 170)
    clock.observe({"type": "compaction_start", "reason": "manual"}, 300)
    assert clock.expired(350) == ("compaction", 180)
    clock = _PhaseDeadlines(180, 0)
    clock.observe({"type": "compaction_start", "reason": "manual"}, 170)
    clock.observe({"type": "compaction_end", "reason": "manual", "result": {}}, 340)
    clock.observe({"type": "turn_start"}, 341)
    assert clock.expired(521) == ("model", 180)


def test_invalid_request_budget_cannot_create_an_unbounded_exemption():
    clock = _PhaseDeadlines(180, 0)
    clock.observe(tool("work"), 0)
    clock.observe({**request("work", "rpc"), "timeout_ms": 120001}, 1)
    assert clock.expired(180) == ("tool", 180)


@pytest.mark.parametrize("cooperative", [True, False])
def test_timeout_collects_cooperative_receipt_then_uses_original_group_release(tmp_path, cooperative):
    events, terminated, inputs = [], [], []
    script = '''import json,sys,time
json.loads(sys.stdin.readline())
print(json.dumps({"type":"tool_execution_start","toolName":"preview_script_draft","toolCallId":"work"}),flush=True)
'''
    if cooperative:
        script += '''assert json.loads(sys.stdin.readline())["type"] == "diary_abort"
print(json.dumps({"type":"diary_input_checkpoint","checkpoint_id":1,"phase":"consume","input_id":"must-not-consume"}),flush=True)
print(json.dumps({"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"late text"}}),flush=True)
print(json.dumps({"type":"tool_execution_start","toolName":"read_current_task","toolCallId":"late-tool"}),flush=True)
print(json.dumps({"type":"tool_execution_end","toolName":"preview_script_draft","toolCallId":"work","isError":True,"result":{"details":{"failure":{"error":"business_connection_lost","reason":"原写入结果未知","submission_effect":"unknown","repair_action":"查询原请求"}}}}),flush=True)
print(json.dumps({"type":"diary_abort_ack"}),flush=True)
'''
    else:
        script += 'time.sleep(30)\n'
    def terminate(process):
        terminated.append(process.pid)
        _terminate_process_group(process)
    started = time.monotonic()
    with pytest.raises(AssistantPhaseTimeout) as caught:
        run_pi_streaming([sys.executable, "-u", "-c", script],
            message=json.dumps({"controlEnabled": True}) + "\n", env={"PATH": os.environ["PATH"]}, cwd=tmp_path,
            timeout_seconds=.1, on_event=events.append, control=TurnControl(terminate), terminate=terminate,
            projector_factory=_PiStreamProjector, business_event_lines=_business_event_lines,
            input_handler=lambda *args: inputs.append(args))
    assert caught.value.phase == "tool"
    assert not inputs
    assert time.monotonic() - started < 4
    if cooperative:
        assert "diary_abort_ack" in caught.value.output
        assert [event["type"] for event in events] == ["tool_start", "tool_end"]
        assert next(event for event in events if event["type"] == "tool_end")["failure"]["submission_effect"] == "unknown"
    else:
        assert terminated


@pytest.mark.parametrize("with_events", [False, True])
@pytest.mark.parametrize("timeout_phase", [None, "model", "business_request", "tool"])
def test_non_streaming_and_streaming_share_the_phase_runner(tmp_path, monkeypatch, with_events, timeout_phase):
    from assistant_sdk_fixtures import install_sdk_fixture
    from app import assistant_pi as host
    from app.config import Settings
    pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(pi))
    monkeypatch.setattr(host, "_read_task_before_pi", lambda *args: {"task_id": "task", "selected_refs": []})
    def version_only(cmd, **kwargs):
        assert cmd[-1] == "--version", "SDK invocation bypassed the phase runner"
        return subprocess.CompletedProcess(cmd, 0, "1.3.0\n" if Path(cmd[0]).name == "pi" else "v24.16.0\n", "")
    monkeypatch.setattr(host.subprocess, "run", version_only)
    calls = []
    def phase_runner(cmd, **kwargs):
        assert kwargs["timeout_seconds"] == 180
        assert json.loads(kwargs["message"])["controlEnabled"] is True
        calls.append(kwargs)
        if timeout_phase:
            raise AssistantPhaseTimeout(cmd, timeout_phase, 180, "", "")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type":"creation_session_result", "protocol":1,
            "owner":"pi-coding-agent-sdk", "status":"settled", "answer":"读取完成。", "tool_results":[]}) + "\n", "")
    monkeypatch.setattr(host, "_run_pi_streaming", phase_runner)
    def run():
        return host.run_task_turn(Settings(model_root_path=str(tmp_path / "models"), llm_api_key="synthetic", llm_api_key_provider="minimax"),
            workspace_id="ws", task_id="task", stage="montage", selected_refs=[], playhead_ms=None,
            message="读取当前任务", on_event=(lambda event: None) if with_events else None)
    if timeout_phase:
        with pytest.raises(host.AssistantUnavailable) as caught:
            run()
        assert caught.value.cause == ("bridge_timeout" if timeout_phase == "model" else host.FAILURE_TOOL_FAILED)
        assert caught.value.service_contacted is False
        if timeout_phase != "model":
            assert "工具结果未知" in str(caught.value)
    else:
        run()
    assert len(calls) == 1
