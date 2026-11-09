"""Phase clocks and owned-child cancellation; no provider, media or user DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import shutil

import pytest

from app.assistant.process_runner import AssistantPhaseTimeout, _PhaseDeadlines, run_pi_streaming
from app.assistant.projection import _PiStreamProjector, _business_event_lines
from app.assistant.turn_control import TurnControl, _terminate_process_group
from app.assistant.turn_control import AssistantStopped, AssistantProcessReleaseError


def tool(call, end=False):
    return {"type": "tool_execution_end" if end else "tool_execution_start", "toolCallId": call}


def request(call, identity, end=False):
    return {"type": "diary_business_request", "phase": "end" if end else "start",
            "tool_call_id": call, "request_id": identity, "write": True, "timeout_ms": 120000}


@pytest.mark.parametrize('status',['started','completed','cancelled','failed'])
def test_compaction_projection_keeps_estimates_usage_and_the_actual_end_reason(status):
    event={'type':'compaction_start' if status=='started' else 'compaction_end','reason':'threshold',
        'contextUsage':{'tokens':112607,'contextWindow':1000000}}
    if status=='completed':
        event['result']={'tokensBefore':112607,'estimatedTokensAfter':23000,'summary':'PRIVATE_PROMPT',
            'usage':{'input':61000,'output':2800,'totalTokens':63800,'cost':{'private':'not copied'}}}
    if status=='cancelled':
        event['aborted']=True
    if status=='failed':
        event['errorMessage']='Summarization failed: PRIVATE_USER_PROMPT PRIVATE_PROFILE api_key=private-key https://private.invalid/request'
    value=_PiStreamProjector().project(json.dumps(event))
    assert value['type']=='context_compaction' and value['status']==status and value['trigger']=='threshold'
    if status=='started':
        assert value['sdk_estimate']['tokens_before']==112607 and 'provider_usage' not in value
    if status=='completed':
        assert value['sdk_estimate']['tokens_before']==112607
        assert value['provider_usage']=={'input':61000,'output':2800,'totalTokens':63800}
    if status=='failed':
        assert value['failure_origin']=='sdk' and value['reason_code']=='sdk_error_unknown'
    if status=='cancelled':
        assert value['failure_origin']=='sdk_abort'
    assert not any(secret in json.dumps(value) for secret in ('PRIVATE_PROMPT','PRIVATE_USER_PROMPT','PRIVATE_PROFILE','private-key','private.invalid'))


def test_compaction_deadline_keeps_the_host_cause_and_late_sdk_abort(tmp_path):
    events=[]
    script='''import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({'type':'compaction_start','reason':'threshold','contextUsage':{'tokens':112607,'contextWindow':1000000}}),flush=True)
assert json.loads(sys.stdin.readline())['type']=='diary_abort'
print(json.dumps({'type':'compaction_end','reason':'threshold','aborted':True}),flush=True)
print(json.dumps({'type':'diary_abort_ack'}),flush=True)
'''
    with pytest.raises(AssistantPhaseTimeout) as failure:
        run_pi_streaming([sys.executable,'-u','-c',script],message=json.dumps({'controlEnabled':True})+'\n',
            env={'PATH':os.environ['PATH']},cwd=tmp_path,timeout_seconds=.1,on_event=events.append,
            control=TurnControl(_terminate_process_group),terminate=_terminate_process_group,
            projector_factory=_PiStreamProjector,business_event_lines=_business_event_lines)
    assert failure.value.phase=='compaction'
    assert [(event['status'],event.get('failure_origin')) for event in events]==[
        ('started',None),('cancelled','host_deadline'),('cancelled','sdk_abort')]
    assert all(event['trigger']=='threshold' for event in events)
    assert events[1]['limit_seconds']==.1
    assert all('provider_usage' not in event for event in events)


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


def test_slow_host_business_callback_keeps_output_and_cancel_live(tmp_path):
    control = TurnControl(terminate=_terminate_process_group)
    control.register("host-business-turn")
    started, finished = threading.Event(), threading.Event()
    observed, bindings = [], []
    binding = {"workspace_id": "workspace", "member_id": "member", "task_id": "task",
        "conversation_id": "conversation", "turn_id": "host-business-turn", "goal_revision": "goal-v1",
        "input_id": None, "input_turn_id": None, "task_version": None}

    def callback(operation, arguments, **options):
        assert operation == "start_creation_execution"
        assert arguments["payload"]["request_id"] == "original-request"
        assert options["call_id"] == "transport-call"
        assert options["tool_call_id"] == "work"
        bindings.append(options["source_binding"])
        assert time.monotonic() < options["deadline_monotonic"] <= time.monotonic() + 120
        started.set()
        try:
            assert options["cancel_event"].wait(3), "cancel was blocked behind the business callback"
            return {"ok": False, "failure": {"status_code": 409, "error": "result_unknown", "submission_effect": "unknown"}}
        finally:
            finished.set()

    def on_event(event):
        observed.append(event)
        if event.get("type") == "text_delta":
            assert started.wait(1)
            binding["input_id"] = "later-input"
            control.request_stop("host-business-turn")

    script = '''import json,sys,time
json.loads(sys.stdin.readline())
for event in [
 {"type":"tool_execution_start","toolName":"start_creation_execution","toolCallId":"work"},
 {"type":"diary_business_request","phase":"start","tool_call_id":"work","request_id":"transport-call","write":True,"timeout_ms":120000},
 {"type":"diary_business_call","call_id":"transport-call","tool_call_id":"work","operation":"start_creation_execution","arguments":{"task_id":"task","payload":{"request_id":"original-request"}}},
 {"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"callback remains in flight"}}]:
 print(json.dumps(event),flush=True)
assert json.loads(sys.stdin.readline())["type"]=="diary_abort"
print(json.dumps({"type":"diary_abort_ack"}),flush=True)
'''
    with pytest.raises(AssistantStopped):
        run_pi_streaming([sys.executable, "-u", "-c", script], message='{"controlEnabled":true,"businessEnabled":true}\n',
            env=os.environ.copy(), cwd=tmp_path, timeout_seconds=5, on_event=on_event,
            control=control, terminate=_terminate_process_group, projector_factory=_PiStreamProjector,
            business_event_lines=_business_event_lines, turn_id="host-business-turn",
            business_handler=callback, source_binding=binding)
    assert finished.wait(1)
    assert any(event.get("type") == "text_delta" for event in observed)
    assert bindings[0]["input_id"] is None, "late work must retain its admitted input binding"


def test_background_preflight_uses_host_facts_and_strips_inherited_credentials(tmp_path):
    from app.assistant.runtime_context import prepare_runtime
    from app.assistant.preflight import read_memory_before_pi, read_task_before_pi

    node = shutil.which("node")
    assert node is not None
    runtime, env, _, _ = prepare_runtime(root=tmp_path, pi_executable="/synthetic/pi",
        base_url="https://synthetic.invalid/v1", model="fixture", key="synthetic-model-key", provider="deepseek",
        environment={"PATH": str(Path(node).parent), "AI_VIDEO_DIARY_AGENT_AUTHORIZATION": "inherited-bearer",
            "AI_VIDEO_DIARY_AGENT_COOKIE": "inherited-cookie", "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": "inherited-member",
            "YAOHE_DESKTOP_CONTROL_TOKEN": "inherited-desktop-control"}, workspace_id="workspace",
        session_key="conversation", task_id="task", caller_user_id="member", stage="montage",
        selected_refs=[], playhead_ms=None, authorization="explicit-bearer", cookie="explicit-cookie",
        local_member_token="explicit-member", material_selection=None, profile_reference=None,
        knowledge_reference=None, task_version=None, turn_id=None, creation_request=None,
        message="original instruction", tool_names="read_preference_memory", host_business=True)
    assert not any(key in env for key in ("AI_VIDEO_DIARY_AGENT_AUTHORIZATION", "AI_VIDEO_DIARY_AGENT_COOKIE",
        "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN", "AI_VIDEO_DIARY_AGENT_API_BASE", "YAOHE_DESKTOP_CONTROL_TOKEN"))
    calls = []
    values = {
        "read_creator_memory": {"id": "memory", "revision": 4, "common_expressions": ["a" * 350],
            "banned_words": [], "style_preferences": {"tone": "plain"}},
        "read_task_workspace": {"task": {"id": "task", "stage": "montage", "timeline_sequence": 0}},
        "read_creation_execution": {"task_id": "task", "job": None},
        "read_task_edit_capabilities": {"task_id": "task", "schema": {"type": "object", "properties": {}}, "supported_operations": []},
        "read_media_script_capabilities": {"task_id": "task", "schema": {"type": "object", "properties": {}}, "available": False},
        "read_media_script_tool_capabilities": {"schemas": {name: {"type": "object", "properties": {}}
            for name in ("save", "update", "revoke", "invoke")}, "invocation_phase": "script", "result_kind": "graphic_source"},
    }
    binding = {"workspace_id": "workspace", "member_id": "member", "task_id": "task",
        "conversation_id": "conversation", "turn_id": "turn", "goal_revision": "goal-v1",
        "input_id": None, "input_turn_id": None, "task_version": None}

    def business(operation, arguments, **options):
        calls.append(operation)
        assert arguments == ({} if operation in {"read_creator_memory", "read_media_script_tool_capabilities"} else {"task_id": "task"})
        assert options["source_binding"] == binding
        return {"ok": True, "value": values[operation]}

    def run_process(cmd, **options):
        return run_pi_streaming(cmd, **options, control=TurnControl(terminate=_terminate_process_group),
            terminate=_terminate_process_group, projector_factory=_PiStreamProjector, business_event_lines=_business_event_lines)

    extension = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
    facts = read_memory_before_pi(extension, env, runtime, run_process=run_process,
        business_handler=business, source_binding=binding)
    assert calls == ["read_creator_memory"]
    assert facts["revision"] == 4 and facts["common_expressions"] == ["a" * 300] and facts["truncated"]
    task = read_task_before_pi(extension, env, runtime, run_process=run_process,
        business_handler=business, source_binding=binding)
    assert task["task_id"] == "task" and task["execution"]["job"] is None
    assert task["_edit_capabilities"] == values["read_task_edit_capabilities"]
    assert task["_media_script_capabilities"]["available"] is False


@pytest.mark.parametrize("keep", [False, True])
def test_host_callback_release_is_unconfirmed_until_callback_returns(tmp_path, keep):
    control = TurnControl(terminate=_terminate_process_group)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    errors = []
    binding = {"turn_id": "owned-callback-turn"}

    def callback(_operation, _arguments, **options):
        entered.set()
        try:
            release.wait(3)  # Deliberately ignores cancel to expose the review counterexample.
            return {"ok": True, "value": {}}
        finally:
            returned.set()

    script = '''import sys,json
json.loads(sys.stdin.readline())
print(json.dumps({"type":"tool_execution_start","toolName":"read_current_task","toolCallId":"work"}),flush=True)
print(json.dumps({"type":"diary_business_call","call_id":"transport","tool_call_id":"work","operation":"read_task_workspace","arguments":{"task_id":"task"}}),flush=True)
sys.stdin.readline()
'''

    def run():
        try:
            run_pi_streaming([sys.executable, "-u", "-c", script], message='{"controlEnabled":true}\n',
                env=os.environ.copy(), cwd=tmp_path, timeout_seconds=5, on_event=lambda event: None,
                control=control, terminate=_terminate_process_group, projector_factory=_PiStreamProjector,
                business_event_lines=_business_event_lines, turn_id=binding["turn_id"], keep_turn_control=keep,
                business_handler=callback, source_binding=binding)
        except Exception as error:
            errors.append(error)

    runner = threading.Thread(target=run)
    runner.start()
    try:
        assert entered.wait(2)
        with pytest.raises(AssistantProcessReleaseError):
            control.stop_all(deadline_monotonic=time.monotonic() + .05)
        runner.join(2)
        assert not runner.is_alive()
        assert any(isinstance(error, AssistantProcessReleaseError) for error in errors)
        assert binding["turn_id"] in control.stop_events
        with pytest.raises(AssistantProcessReleaseError):
            control.stop_all(deadline_monotonic=time.monotonic())
    finally:
        release.set()
        assert returned.wait(1)
        runner.join(2)
    assert control.stop_all(deadline_monotonic=time.monotonic() + 1) == [binding["turn_id"]]
    control.forget(binding["turn_id"])
    assert binding["turn_id"] not in control.stop_events


@pytest.mark.parametrize("code,status", [("task_authorization_expired", 409), ("task_authorization_revoked", 403)])
def test_preflight_preserves_safe_authorization_failure(tmp_path, code, status):
    from app.assistant.preflight import read_memory_before_pi
    from app.assistant.failures import AssistantUnavailable

    control = TurnControl(terminate=_terminate_process_group)
    def callback(_operation, _arguments, **options):
        return {"ok": False, "failure": {"status_code": status, "error": code, "reason": "原任务授权不可用。",
            "repair_action": "核对原任务授权后继续。", "submission_effect": "not_submitted", "task_id": "task",
            "request_id": "source-request", "stack": "raw-private-stack"}}
    def run_process(cmd, **options):
        return run_pi_streaming(cmd, **options, control=control, terminate=_terminate_process_group,
            projector_factory=_PiStreamProjector, business_event_lines=_business_event_lines)
    env = {"PATH": os.environ["PATH"], "AI_VIDEO_DIARY_AGENT_TASK_ID": "task"}
    extension = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
    with pytest.raises(AssistantUnavailable) as caught:
        read_memory_before_pi(extension, env, tmp_path, run_process=run_process,
            business_handler=callback, source_binding={"turn_id": "preflight"})
    error = caught.value
    assert error.cause == "tool_failed" and error.service_contacted is False
    assert error.business_failure["error"] == code and error.business_failure["status_code"] == status
    assert error.business_failure["submission_effect"] == "not_submitted"
    assert error.business_failure["task_id"] == "task" and error.business_failure["request_id"] == "source-request"
    assert "raw-private-stack" not in str(error) + json.dumps(error.business_failure)


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
        return subprocess.CompletedProcess(cmd, 0, "1.4.0\n" if Path(cmd[0]).name == "pi" else "v24.16.0\n", "")
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
