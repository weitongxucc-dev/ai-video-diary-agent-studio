from assistant_sdk_fixtures import project_event_fixture, sdk_completed, install_sdk_fixture
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.assistant_pi as pi_module
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.conversation_repository import ConversationRepository
from app.assistant_pi import (
    AssistantStopped,
    AssistantUnavailable,
    _PiStreamProjector,
    _run_pi_streaming,
    _safe_tool_result_summary,
    register_turn_control,
    request_stop_turn,
    run_material_turn,
    run_plan_turn,
    run_task_turn,
)
from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_host_preflight(monkeypatch):
    monkeypatch.setattr("app.assistant.runtime_context.sdk_module", lambda _: Path("/synthetic/index.js"))
    # This file isolates Pi subprocess behavior. The shipped Node and real HTTP
    # preflight are exercised without this seam in test_pi_instruction_closure.
    monkeypatch.setattr(pi_module, "_read_task_before_pi", lambda _extension, env, _cwd: {
        "task_id": env["AI_VIDEO_DIARY_AGENT_TASK_ID"], "selected_refs": [],
    })
    monkeypatch.setattr(pi_module, "_read_memory_before_pi", lambda *_args: {
        "revision": 0, "common_expressions": [], "banned_words": [], "style_preferences": {},
    })


def _install_model_stream(monkeypatch, stream):
    def version(cmd, **_kwargs):
        assert cmd[-1] == "--version"
        return sdk_completed(cmd, 0, stdout=("1.4.0" if Path(cmd[0]).name == "pi" else "v24.16.0"), stderr="")
    monkeypatch.setattr(pi_module.subprocess, "run", version)
    monkeypatch.setattr(pi_module, "_run_pi_streaming", stream)


def _stream_completed(cmd, *, stdout, on_event, stderr=""):
    projector = _PiStreamProjector()
    for line in pi_module._business_event_lines(stdout):
        event = projector.project(line)
        if event:
            on_event(event)
    return sdk_completed(cmd, 0, stdout=stdout, stderr=stderr)


def test_stopping_assistant_turn_terminates_its_controlled_child_group(tmp_path):
    turn_id = "synthetic-turn-stop"
    started = threading.Event()
    failures: list[BaseException] = []
    register_turn_control(turn_id)

    def run_child():
        try:
            _run_pi_streaming(
                [sys.executable, "-u", "-c",
                 "import json,time; print(json.dumps({'type':'tool_execution_start','toolName':'read_current_task','toolCallId':'test'}),flush=True); time.sleep(60)"],
                message="synthetic stop test", env=dict(__import__("os").environ), cwd=tmp_path,
                timeout_seconds=10, on_event=lambda _event: started.set(), turn_id=turn_id,
            )
        except BaseException as cause:
            failures.append(cause)

    worker = threading.Thread(target=run_child)
    worker.start()
    try:
        assert started.wait(timeout=5), "isolated child did not emit its synthetic first event"
        assert request_stop_turn(turn_id) is True
        worker.join(timeout=5)
        assert not worker.is_alive(), "assistant child runner did not release after stop"
        assert len(failures) == 1 and isinstance(failures[0], AssistantStopped)
    finally:
        if worker.is_alive():
            request_stop_turn(turn_id)
            worker.join(timeout=5)


@pytest.fixture
def controlled_pi_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(pi_module, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr(pi_module, "_pi_executable", lambda: sys.executable)
    monkeypatch.setattr(pi_module, "_configured_model", lambda _settings: ("https://synthetic.invalid", "fixture", ""))
    monkeypatch.setattr(pi_module, "_agent_root", lambda _settings, _workspace: tmp_path / "agent")
    records = []
    monkeypatch.setattr(pi_module, "_record_service", lambda *args, **kwargs: records.append(kwargs))
    return Settings(model_root_path=str(tmp_path)), records


def test_acknowledged_stop_preserves_partial_markdown_when_stream_io_raises_oserror(controlled_pi_runtime, monkeypatch, tmp_path):
    settings, records = controlled_pi_runtime
    started, release_fault = threading.Event(), threading.Event()
    turn_id = "controlled-stop-io"
    text = "**部分正文**\n\n已生成的内容保持原样。"
    event = {"type":"message_update", "assistantMessageEvent":{"type":"text_delta", "delta":text}}
    child_code = f"import time; print({json.dumps(event, ensure_ascii=False)!r}, flush=True); time.sleep(60)"
    original_popen = subprocess.Popen
    children, events, failures = [], [], []

    def launch(_cmd, **kwargs):
        child = original_popen([sys.executable, "-u", "-c", child_code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(pi_module.subprocess, "Popen", launch)

    def on_event(projected):
        events.append(projected)
        started.set()
        assert release_fault.wait(timeout=5)
        # Deterministic fault after the real stop was acknowledged; not a claim
        # about which syscall failed in the original packaged run.
        raise OSError("controlled stream I/O failure after stop")

    def run():
        try:
            pi_module.run_plan_turn(settings, workspace_id="controlled", conversation_id="controlled",
                                   message="只验证停止", turn_id=turn_id, on_event=on_event)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert started.wait(timeout=5)
        assert request_stop_turn(turn_id) is True
        assert children[0].poll() is not None
        release_fault.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], AssistantStopped)
        assert isinstance(failures[0].__cause__, OSError)
        assert events == [{"type":"text_delta", "delta":text}]
        assert records == []  # Stopping does not falsely mark the model unavailable.
        assert request_stop_turn(turn_id) is False
    finally:
        release_fault.set()
        if children and children[0].poll() is None:
            pi_module._terminate_process_group(children[0])
        worker.join(timeout=5)
        pi_module.forget_turn_control(turn_id)


def test_real_launch_oserror_without_stop_keeps_runtime_missing_classification(controlled_pi_runtime, monkeypatch):
    settings, records = controlled_pi_runtime
    turn_id = "controlled-launch-error"

    def failed_launch(*_args, **_kwargs):
        raise OSError("controlled executable launch failure")

    monkeypatch.setattr(pi_module.subprocess, "Popen", failed_launch)
    try:
        with pytest.raises(AssistantUnavailable) as caught:
            pi_module.run_plan_turn(settings, workspace_id="controlled", conversation_id="controlled",
                                   message="只验证启动失败", turn_id=turn_id, on_event=lambda _event: None)
        assert caught.value.cause == pi_module.FAILURE_RUNTIME_MISSING
        assert "启动不了本机的对话程序" in str(caught.value)
        assert isinstance(caught.value.__cause__, OSError)
        assert records == []
    finally:
        pi_module.forget_turn_control(turn_id)


def test_requested_execution_tool_uses_fixed_task_routes_and_real_start_facts(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Pi business tools require Node.js")
    extension = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
    script = r"""
import { readFileSync } from 'node:fs';
const input = JSON.parse(readFileSync(0, 'utf8'));
const tools = new Map();
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(init.body) : null});
  const path = new URL(url).pathname;
  const payload = path.endsWith('/execution/preview')
    ? {script_id: 'confirmed-script', input_digest: 'a'.repeat(64), requirements: {can_continue: true}}
    : path.endsWith('/execution')
      ? {task_id:'task-one',job: null, video_ready: false}
      : path.endsWith('/execution/start') || path.endsWith('/execution/resume') || path.endsWith('/execution/cancel')
        ? {job: {status: 'queued'}, video_ready: false}
        : {task: {id:'task-one',name: '当前任务', stage: 'montage'}, montage: {clips: []}};
  return new Response(JSON.stringify(payload), {status: 200});
};
const {default: register} = await import(input.extension);
register({registerTool: tool => tools.set(tool.name, tool)});
let beforeReadError;
beforeReadError = (await tools.get('start_creation_execution').execute('start', {})).details.failure.reason;
await tools.get('read_current_task').execute('read', {});
const result = await tools.get('start_creation_execution').execute('start', {});
let duplicateError;
duplicateError = (await tools.get('cancel_creation_execution').execute('again', {})).details.failure.reason;
const followUpTools = new Map();
register({registerTool: tool => followUpTools.set(tool.name, tool)});
await followUpTools.get('read_current_task').execute('read', {});
const resume = await followUpTools.get('resume_creation_execution').execute('resume', {});
const cancelTools = new Map();
register({registerTool: tool => cancelTools.set(tool.name, tool)});
await cancelTools.get('read_current_task').execute('read', {});
const cancel = await cancelTools.get('cancel_creation_execution').execute('cancel', {});
console.log(JSON.stringify({calls, result, resume, cancel, beforeReadError, duplicateError}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        input=json.dumps({"extension": extension.as_uri()}),
        capture_output=True, text=True, check=True, timeout=15, cwd=tmp_path,
        env={"PATH": str(Path(node).parent), "HOME": str(tmp_path),
             "AI_VIDEO_DIARY_AGENT_API_BASE": "http://127.0.0.1:1",
             "AI_VIDEO_DIARY_AGENT_TASK_ID": "task-one",
             "AI_VIDEO_DIARY_AGENT_TURN_ID": "host-turn",
             "AI_VIDEO_DIARY_AGENT_STAGE": "montage"},
    )
    observed = json.loads(result.stdout)
    assert observed["beforeReadError"] == "请先读取当前创作任务"
    assert "本轮制作动作已经提交" in observed["duplicateError"]
    assert observed["resume"]["details"]["action"] == "resume"
    assert observed["cancel"]["details"]["action"] == "cancel"
    assert [(call["url"].rsplit("/", 1)[-1], call["method"]) for call in observed["calls"]] == [
        ("task-one", "GET"), ("execution", "GET"), ("preview", "GET"), ("execution", "GET"), ("start?assistant_turn_id=host-turn", "POST"),
        ("task-one", "GET"), ("execution", "GET"), ("resume?assistant_turn_id=host-turn", "POST"), ("task-one", "GET"), ("execution", "GET"), ("cancel", "POST"),
    ]
    assert observed["calls"][4]["body"] == {
        "script_id": "confirmed-script", "input_digest": "a" * 64,
        "requirements_confirmed": True,
    }
    assert observed["result"]["details"]["kind"] == "execution_action"
    assert observed["result"]["details"]["status"]["job"]["status"] == "queued"


@pytest.mark.parametrize("operation_type,before,after", [
    ("ChangeCrop", "填满画面 · 水平 60% · 垂直 30%", "填满画面 · 水平 25% · 垂直 30%"),
    ("TrimClip", "源片段 0.0–2.0秒", "源片段 0.25–2.0秒"),
])
def test_pi_events_return_final_answer_and_preview(operation_type, before, after):
    preview = {"proposal": {
        "id": "proposal-1", "summary": "本次实际修改", "target_refs": ["clip:chosen"],
        "operation": {"operation_type": operation_type},
        "preview": {"before": {"detail": before}, "after": {"detail": after}},
        "invalidated_refs": ["artifact:current-output"],
        "external_effects": ["任务版本尚未改变。"],
    }}
    model_text = "确认后才会真正出片。"
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"type": "tool_execution_end", "toolName": "preview_typed_edit", "isError": False,
         "result": {"details": {"kind": "edit_preview", "preview": preview}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": model_text}]}},
        {"type": "agent_settled"},
    ])
    reply, returned_preview = project_event_fixture(output)
    assert returned_preview == preview
    assert f"修改前：{before}" in reply and f"修改后：{after}" in reply
    assert "本次实际修改" in reply
    assert "clip:chosen" not in reply and operation_type not in reply
    assert "clip:chosen" in output and operation_type in output
    assert "任务版本尚未改变。" in reply and "需要重做" in reply
    assert "待确认，尚未应用修改。" in reply
    assert "确认修改不等于完整成片已生成" in reply
    assert ("裁剪窗口位置" in reply) == (operation_type == "ChangeCrop")
    assert model_text not in reply
    assert model_text in output  # Raw model evidence is not rewritten.


def test_pi_events_leave_failed_edit_reply_without_a_proposal():
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"type": "tool_execution_end", "toolName": "preview_typed_edit", "isError": True,
         "result": {"content": [{"type": "text", "text": "没有明确裁剪位置。"}]}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "没有明确裁剪位置，请先说明百分比。"}]}},
        {"type": "agent_settled"},
    ])
    assert project_event_fixture(output) == ("没有明确裁剪位置，请先说明百分比。", None)


def test_material_read_preserves_pi_clarification_and_requires_settled_turn():
    material = {"scope": "asset", "asset_ref": "asset:asset-1", "user_tags": ["旧标签"]}
    pi_answer = "我已读取这条素材的标签。你希望替换成什么，还是只添加一项？"
    events = [
        {"type": "tool_execution_end", "toolName": "read_selected_material", "isError": False,
         "result": {"details": material}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": pi_answer}]}},
        {"type": "agent_settled"},
    ]
    assert project_event_fixture("\n".join(json.dumps(event, ensure_ascii=False) for event in events), "material_result") == (
        pi_answer,
        {"kind": "material_result", "material_result": material},
    )
    with pytest.raises(AssistantUnavailable):
        project_event_fixture("\n".join(json.dumps(event, ensure_ascii=False) for event in events[:-1]), "material_result")


def test_material_saved_update_reply_comes_from_write_readback():
    details = {"scope": "asset", "asset_ref": "asset:asset-1", "user_tags": ["成片素材"],
               "action": "replace", "changed": True, "previous_user_tags": ["旧标签"]}
    output = "\n".join(json.dumps(event, ensure_ascii=False) for event in [
        {"type": "tool_execution_end", "toolName": "read_selected_material", "isError": False,
         "result": {"details": {"scope": "asset", "asset_ref": "asset:asset-1", "user_tags": ["旧标签"]}}},
        {"type": "tool_execution_end", "toolName": "update_selected_material_tags", "isError": False,
         "result": {"details": details}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "我已经把未确认的请求写进去了。"}]}},
        {"type": "agent_settled"},
    ])
    reply, result = project_event_fixture(output, "material_result")
    assert reply == "整条素材的我的标签已保存并回读：成片素材。"
    assert result == {"kind": "material_result", "material_result": details}


def test_material_tool_success_without_exact_object_readback_is_not_claimed():
    output = "\n".join(json.dumps(event, ensure_ascii=False) for event in [
        {"type": "tool_execution_end", "toolName": "update_selected_material_tags", "isError": False,
         "result": {"details": {"scope": "segment", "asset_ref": "asset:asset-1", "user_tags": ["标签"]}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "已经保存。"}]}},
        {"type": "agent_settled"},
    ])
    with pytest.raises(AssistantUnavailable):
        project_event_fixture(output, "material_result")


def test_material_turn_uses_existing_pi_turn_with_selection_scoped_session(monkeypatch, tmp_path):
    seen = {}

    def capture(_settings, **kwargs):
        seen.update(kwargs)
        return "收到。", None

    monkeypatch.setattr("app.assistant_pi._run_turn", capture)
    selection = {"scope": "segment", "asset_id": "asset-1", "segment_id": "segment-2"}
    assert run_material_turn(
        Settings(model_root_path=str(tmp_path), llm_provider="minimax"),
        workspace_id="workspace-1", conversation_id="conversation-1", message="读取这个片段标签",
        selection=selection,
    ) == ("收到。", None)
    assert {"read_material_library_status", "read_selected_material", "update_selected_material_tags", "list_media_script_tools"} <= set(seen["tool_names"].split(","))
    assert "save_media_script_tool" not in seen["tool_names"] and "preview_media_script_tool" not in seen["tool_names"]
    assert seen["material_selection"] == {
        "scope": "segment", "assetId": "asset-1", "segmentId": "segment-2",
    }
    assert seen["stage"] == ""
    assert {"preview_creation_plan", "prepare_creation_window"} <= set(seen["tool_names"].split(","))
    assert seen["session_key"] == "conversation:conversation-1"


def test_pi_events_reject_incomplete_turn():
    with pytest.raises(AssistantUnavailable):
        project_event_fixture('{"type":"agent_settled"}')


def test_pi_stream_projects_only_text_and_safe_business_tool_status():
    projector = _PiStreamProjector()
    assert projector.project(json.dumps({
        "type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "contentIndex": 0, "delta": "我先检查当前任务。",
        },
    })) == {"type": "text_delta", "delta": "我先检查当前任务。"}
    assert projector.project(json.dumps({
        "type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "private thought"},
    })) is None
    started = projector.project(json.dumps({
        "type": "tool_execution_start", "toolCallId": "private-id",
        "toolName": "read_current_task", "args": {"path": "/private/user/key.json"},
    }))
    assert started == {"type": "tool_start", "tool_id": 1, "label": "读取当前任务"}
    ended = projector.project(json.dumps({
        "type": "tool_execution_end", "toolCallId": "private-id",
        "toolName": "read_current_task", "isError": False,
        "result": {"details": {"status": "ready", "path": "/private/user/key.json"}},
    }))
    assert ended == {
        "type": "tool_end", "tool_id": 1, "label": "读取当前任务", "ok": True,
        "summary": "当前任务和所选对象状态已读取。",
    }
    assert "/private/" not in json.dumps([started, ended])
    assert "private-id" not in json.dumps([started, ended])


@pytest.mark.parametrize("name", ["preview_typed_edit", "observe_task_output"])
def test_controlled_parameter_rejection_projects_only_bounded_failure_reason(name):
    reason = "曝光参数超过当前范围，未提交修改。" + "x"*600
    projected = _PiStreamProjector().project(json.dumps({"type":"tool_execution_end", "toolName":name,
        "isError":True, "result":{"details":{"kind":"edit_request_status", "status":"rejected",
            "failure_reason":{"text":reason, "truncated":True}, "raw_payload":{"secret":"private"}},
            "content":[{"type":"text", "text":"not a trusted failure"}]}}))
    assert projected["failure"] == {"reason":reason[:500]}
    assert projected["ok"] is False
    assert "raw_payload" not in json.dumps(projected) and "private" not in json.dumps(projected)


def test_sdk_parameter_validation_projects_concrete_reason_without_received_arguments():
    raw = ('Validation failed for tool "preview_typed_edit":\n'
        '  - audio_edit.values.gain_db: must be number\n'
        '  - audio_edit: must be null\n'
        '  - audio_edit: must match a schema in anyOf\n\nReceived arguments:\n'
        '{"audio_edit":{"values":{"gain_db":"-3"}},"private":"/Volumes/private/key.json","key":"secret"}')
    projected = _PiStreamProjector().project(json.dumps({"type":"tool_execution_end",
        "toolName":"preview_typed_edit", "isError":True,
        "result":{"details":None,"content":[{"type":"text","text":raw}]}}))
    assert projected["failure"] == {"reason":"参数校验未通过，工具未执行。audio_edit.values.gain_db 必须使用数字。",
        "error": "tool_arguments_invalid", "tool_name": "preview_typed_edit", "stage": "parameter_validation",
        "submission_effect": "not_submitted", "repair_action": "按工具参数契约补齐或修正上述字段后再提交；本次工具未执行。"}
    assert projected["ok"] is False
    assert not any(value in json.dumps(projected) for value in ("Received arguments", "must be null", "anyOf", "secret", "/Volumes"))


@pytest.mark.parametrize("tool,prefix", [("preview_typed_edit","ordinary model error"),
    ("preview_typed_edit",'Validation failed for tool "other_tool":\n - gain_db: must be number'),
    ("unknown_tool",'Validation failed for tool "unknown_tool":\n - gain_db: must be number')])
def test_sdk_failure_projection_requires_known_tool_and_matching_validation_prefix(tool, prefix):
    projected = _PiStreamProjector().project(json.dumps({"type":"tool_execution_end", "toolName":tool,
        "isError":True,"result":{"details":None,"content":[{"type":"text","text":prefix}]}}))
    assert projected is None or "failure" not in projected


def test_script_save_summary_does_not_invent_zero_sections_from_identity_only_result():
    result = {"details": {"kind": "script_draft", "preview": {"script_id": "draft-one"}},
              "content": [{"type": "text", "text": '{"title":"真实草稿","section_count":3}'}]}
    summary = _safe_tool_result_summary("preview_script_draft", result, False)
    assert summary == "文案草稿已保存，尚未采用。"
    adopted = {"details": {"kind": "script_draft", "preview": {"script_id": "draft-one", "adopted": True}}}
    assert _safe_tool_result_summary("preview_script_draft", adopted, False) == "实际文案版本已按原委托采用，尚未生成成片。"
    assert "0 段" not in summary
    assert _safe_tool_result_summary("preview_script_draft", result, True).startswith("这一步没有完成")


def test_stream_tool_summary_uses_the_real_material_details_field():
    assert _safe_tool_result_summary("find_materials", {
        "details": {"matches": [{"asset_ref": "asset:one"}, {"asset_ref": "asset:two"}]},
    }, False) == "已读取 2 项素材候选。"


def test_assistant_stream_redacts_a_known_key_split_across_deltas(monkeypatch):
    monkeypatch.setattr(main_module, "_workspace_settings_for",
                        lambda _workspace: SimpleNamespace(effective_llm_api_key="fixture-secret-123"))
    sanitizer = main_module._assistant_stream_transport().sanitizer("local")
    emitted = sanitizer.feed("先说结论：fixture-sec")
    emitted += sanitizer.feed("ret-123 已核实。")
    emitted += sanitizer.feed("", final=True)
    assert "先说结论：" in emitted
    assert "fixture-secret-123" not in emitted
    assert "[已隐藏凭据]" in emitted

    marked = main_module._assistant_stream_transport().sanitizer("local")
    redacted = marked.feed("说明 api_key=fixture-")
    redacted += marked.feed("secret-123 可以读取。")
    redacted += marked.feed("", final=True)
    assert "fixture-secret-123" not in redacted


def test_assistant_stream_response_sends_safe_events_then_authoritative_result(monkeypatch,tmp_path):
    monkeypatch.setattr(main_module, "_workspace_settings_for",
                        lambda _workspace: SimpleNamespace(effective_llm_api_key="fixture-secret-123"))
    repo=ConversationRepository(tmp_path/'stream.sqlite')
    chat=repo.current_or_create('local','member','home','home')
    draft=repo.save_draft(chat['id'],'local','member','原委托',0)
    turn,_=repo.create_turn_idempotent(chat['id'],'local','member','原委托','request',draft['draft_version'])
    monkeypatch.setattr(main_module,'conversation_repo',repo)
    app = FastAPI()

    @app.get("/assistant-stream")
    def stream():
        def execute(publish):
            diagnostic=_PiStreamProjector().project(json.dumps({'type':'compaction_end','reason':'threshold',
                'errorMessage':'PRIVATE_USER_PROMPT PRIVATE_PROFILE fixture-secret-123'}))
            publish({**diagnostic,
                'sdk_estimate':{'tokens_before':112607,'raw_prompt':'PRIVATE_CONTEXT'},
                'reason':'压缩SDK失败 fixture-secret-123','summary':'PRIVATE_CONTEXT','provider_usage':{'input':61000,'cost':99}})
            publish({"type": "text_delta", "delta": "输出 fixture-secret-"})
            publish({"type": "tool_start", "tool_id": 1, "label": "读取当前任务", "args": {"path": "/private/key"}})
            publish({"type": "text_delta", "delta": "123 完成"})
            publish({"type": "tool_end", "tool_id": 1, "label": "读取当前任务", "ok": True,
                     "summary": "当前任务和所选对象状态已读取。", "result": {"token": "fixture-secret-123"}})
            return SimpleNamespace(model_dump=lambda mode: {"reply": "当前制作条件已回读。"})
        return main_module._assistant_stream_transport().response(execute, workspace_id="local",turn_id=turn['id'])

    response = TestClient(app).get("/assistant-stream", headers={"Accept": "text/event-stream"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["turn_started", "text_delta", "tool_start", "text_delta", "tool_end", "turn_status", "result"]
    assert events[0]['turn_id'] == turn['id']
    streamed_text = "".join(event.get("delta", "") for event in events if event["type"] == "text_delta")
    assert "fixture-secret-123" not in streamed_text and "[已隐藏凭据]" in streamed_text
    assert events[2] == {"type": "tool_start", "tool_id": 1, "label": "读取当前任务"}
    assert events[4] == {"type": "tool_end", "tool_id": 1, "label": "读取当前任务", "ok": True,
                         "summary": "当前任务和所选对象状态已读取。"}
    assert events[-1]["result"]["reply"] == "当前制作条件已回读。"
    diagnostic=repo.get_turn(turn['id'])['events'][0]
    assert diagnostic['type']=='context_compaction' and diagnostic['status']=='failed'
    assert diagnostic['sdk_estimate']=={'tokens_before':112607} and diagnostic['provider_usage']=={'input':61000}
    assert diagnostic['reason_code']=='sdk_error_unknown'
    assert all(secret not in json.dumps(repo.get_turn(turn['id'])['events'])
        for secret in ('PRIVATE_CONTEXT','PRIVATE_USER_PROMPT','PRIVATE_PROFILE','fixture-secret-123'))


def test_background_goal_turn_persists_the_same_finite_compaction_diagnostic(monkeypatch,tmp_path):
    repo=ConversationRepository(tmp_path/'background.sqlite')
    chat=repo.current_or_create('local','member','home','home')
    raw='保留原委托，未知动作先查原请求。'
    draft=repo.save_draft(chat['id'],'local','member',raw,0)
    turn,_=repo.create_turn_idempotent(chat['id'],'local','member',raw,'request',draft['draft_version'])
    monkeypatch.setattr(main_module,'conversation_repo',repo)
    monkeypatch.setattr(main_module,'assistant_continuation_driver',None)
    monkeypatch.setattr(main_module,'_workspace_settings_for',lambda _:SimpleNamespace(effective_llm_api_key='fixture-secret-123'))
    task=SimpleNamespace(confirmed_script_id=None,stage='montage')
    monkeypatch.setattr(main_module,'_creation_task_or_404',lambda *_:task)
    monkeypatch.setattr(main_module,'_assistant_turn_scope',lambda *_:('montage',[]))
    business=SimpleNamespace(native_case=None,original=lambda *_:(None,task,None),native_call=None,input_delivery=lambda _:None)
    def run(_settings,**kwargs):
        assert kwargs['message']==raw
        kwargs['on_event']({'type':'context_compaction','status':'cancelled','trigger':'threshold',
            'failure_origin':'host_deadline','limit_seconds':180,'reason':'host截止 fixture-secret-123',
            'prompt':'PRIVATE_CONTEXT','sdk_estimate':{'tokens_before':112607,'raw_prompt':'PRIVATE_CONTEXT'}})
        raise AssistantUnavailable('宿主压缩阶段截止，不证明provider已接收。')
    monkeypatch.setattr(main_module,'run_task_turn',run)
    goal={'task_id':'task','workspace_id':'local','member_id':'member','conversation_id':chat['id'],
        'original_turn_id':turn['id'],'original_instruction':raw,'round_count':1,'target_snapshot':{},
        'unfinished_steps':['read_original'],'handled_event_ids':[]}
    main_module._task_turn_http_adapter().run_goal({'goal':goal,'turn_id':turn['id'],'request_context':{'stage':'montage'},
        'event':{'status':'succeeded'},'phase':'business'}, {'business':business,'facts':{},'task_version':{}})
    saved=repo.get_turn(turn['id'])
    assert saved['status']=='failed' and saved['user_message']==raw
    diagnostic=saved['events'][0]
    assert diagnostic['type']=='context_compaction' and diagnostic['failure_origin']=='host_deadline'
    assert diagnostic['limit_seconds']==180 and diagnostic['sdk_estimate']=={'tokens_before':112607}
    assert 'provider_usage' not in diagnostic
    assert 'PRIVATE_CONTEXT' not in json.dumps(diagnostic) and 'fixture-secret-123' not in json.dumps(diagnostic)


def test_background_goal_setup_failure_forgets_registered_control_without_model(monkeypatch, tmp_path):
    repo = ConversationRepository(tmp_path / 'goal-setup.sqlite')
    chat = repo.current_or_create('local', 'member', 'home', 'home')
    draft = repo.save_draft(chat['id'], 'local', 'member', '原委托', 0)
    turn, _ = repo.create_turn_idempotent(chat['id'], 'local', 'member', '原委托', 'setup-request', draft['draft_version'])
    turn_id = turn['id']
    monkeypatch.setattr(main_module, 'conversation_repo', repo)
    monkeypatch.setattr(main_module, 'assistant_continuation_driver', None)
    monkeypatch.setattr(main_module, '_creation_task_or_404', lambda *_: SimpleNamespace(confirmed_script_id=None, stage='montage'))
    monkeypatch.setattr(main_module, '_assistant_turn_scope', lambda *_: ('montage', []))
    def refuse_stream_setup():
        assert turn_id in pi_module._turn_stop_events
        raise ValueError('injected goal stream setup failure')
    monkeypatch.setattr(main_module, '_assistant_stream_transport', refuse_stream_setup)
    monkeypatch.setattr(main_module, 'run_task_turn', lambda *_args, **_kwargs: pytest.fail('setup failure must not invoke model'))
    claim = {'goal': {'task_id': 'task', 'workspace_id': 'local', 'original_instruction': '原委托'},
        'turn_id': turn_id, 'request_context': {'stage': 'montage'}, 'event': {}}
    assert turn_id not in pi_module._turn_stop_events
    with pytest.raises(ValueError, match='goal stream setup failure'):
        main_module._task_turn_http_adapter().run_goal(claim, {'business': None, 'facts': {}, 'task_version': {}})
    assert turn_id not in pi_module._turn_stop_events
    assert repo.get_turn(turn_id)['user_message'] == '原委托'


def test_assistant_stream_reports_version_rejection_before_turn_started(monkeypatch):
    monkeypatch.setattr(main_module, "_workspace_settings_for",
                        lambda _workspace: SimpleNamespace(effective_llm_api_key="fixture-secret-123"))
    monkeypatch.setattr(main_module.conversation_repo, "finish_turn",
                        lambda *_args, **_kwargs: pytest.fail("no turn was started"))
    app = FastAPI()

    @app.get("/assistant-stream")
    def stream():
        def execute(_publish):
            raise HTTPException(status_code=409, detail="当前任务版本已变化，请重新查看选中对象后发送。")
        return main_module._assistant_stream_transport().response(execute, workspace_id="local")

    response = TestClient(app).get("/assistant-stream", headers={"Accept": "text/event-stream"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events == [{"type": "error", "status": 409,
                       "message": "当前任务版本已变化，请重新查看选中对象后发送。"}]


def test_pi_execution_action_reply_uses_returned_job_state_not_model_claim():
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"type": "tool_execution_end", "toolName": "start_creation_execution", "isError": False,
         "result": {"details": {"kind": "execution_action", "action": "start",
         "status": {"job": {"status": "queued"}, "video_ready": False}}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "成片已经做好了。"}]}},
        {"type": "agent_settled"},
    ])
    assert project_event_fixture(output, "execution_action") == (
        "连续制作已提交并排队，尚未完成；可在任务页查看实际进度。",
        {"kind": "execution_action", "action": "start",
         "status": {"job": {"status": "queued"}, "video_ready": False}},
    )


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("state", ["failed", "queued"])
def test_creation_execution_read_canonical_rejects_model_claim_of_new_submission(nested, state):
    details = {"kind":"execution_result", "status":{"task_id":"task", "job":{"id":"old-job", "status":state},
        "video_ready":False}, "wait":{"queried_only":True}}
    if nested:
        result = {"details":{"business_receipts":[{"tool_name":"read_creation_execution",
            "tool_call_id":"nested-read", "is_error":False, "result":details}], "incomplete_calls":False}}
        name = "codemode"
    else:
        result, name = {"details":details}, "read_creation_execution"
    output = "\n".join(json.dumps(item) for item in [{"type":"tool_execution_end", "toolName":name,
        "isError":False,"result":result}, {"type":"message_end", "message":{"role":"assistant", "stopReason":"stop",
        "content":[{"type":"text", "text":"已按指令提交了恢复，渲染再次失败。"}]}}, {"type":"agent_settled"}])
    answer, preview = project_event_fixture(output)
    assert preview == {"kind":"execution_result", "action":None, "status":details["status"]}
    assert "已查询原制作作业" in answer and "本轮未提交新的开始或恢复请求" in answer
    assert "已按指令提交" not in answer and "已提交并排队" not in answer


@pytest.mark.parametrize("read_id", ["submitted-job", "other-job"])
def test_creation_execution_read_after_submission_preserves_write_action_and_original_job(read_id):
    written = {"task_id":"task", "job":{"id":"submitted-job", "status":"queued"}, "video_ready":False}
    observed = {"task_id":"task", "job":{"id":read_id, "status":"running"}, "video_ready":False}
    output = "\n".join(json.dumps(item) for item in [
        {"type":"tool_execution_end", "toolName":"resume_creation_execution", "isError":False,
            "result":{"details":{"kind":"execution_action", "action":"resume", "status":written}}},
        {"type":"tool_execution_end", "toolName":"codemode", "isError":False,
            "result":{"details":{"incomplete_calls":False, "business_receipts":[{"tool_name":"read_creation_execution",
                "tool_call_id":"nested-read", "is_error":False, "result":{"kind":"execution_result", "status":observed}}]}}},
        {"type":"message_end", "message":{"role":"assistant", "stopReason":"stop", "content":[{"type":"text","text":"仅查询了旧作业。"}]}},
        {"type":"agent_settled"}])
    answer, preview = project_event_fixture(output)
    assert preview == {"kind":"execution_action", "action":"resume", "status":observed if read_id=="submitted-job" else written}
    assert "本轮未提交" not in answer


def test_pi_topic_generation_reply_preserves_real_candidates_and_unconfirmed_state():
    topics = {"task": {"id": "task-one"}, "candidates": [{"id": "topic-one"}],
              "evidence_references": [], "selected_topic_id": None, "latest_run": None,
              "route_preflight": {}, "egress_preflight": {}}
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"type": "tool_execution_end", "toolName": "generate_creation_task_topics", "isError": False,
         "result": {"details": {"kind": "topic_generation", "topics": topics}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "已经替你选好了。"}]}},
        {"type": "agent_settled"},
    ])
    reply, generated = project_event_fixture(output, "topic_generation")
    assert reply == "已为当前任务生成 1 个选题候选；尚未采用。"
    assert generated == {"kind": "topic_generation", "topics": topics}


def test_task_turn_exposes_continuation_tools_and_bound_named_actions(monkeypatch):
    calls = []
    monkeypatch.setattr("app.assistant_pi._run_turn", lambda _settings, **kwargs: calls.append(kwargs) or ("ok", None))
    for stage in ("topic", "script", "narration"):
        run_task_turn(None, workspace_id="local", task_id="task-one", message="用自然语言表达意图",
                      stage=stage, selected_refs=[], playhead_ms=None)
    assert "generate_creation_task_topics" in calls[0]["tool_names"]
    assert "adopt_creation_task_topic" in calls[0]["tool_names"]
    assert "start_creation_execution" in calls[0]["tool_names"]
    assert "start_creation_execution" in calls[1]["tool_names"]
    assert "preview_script_draft" in calls[1]["tool_names"]
    assert all(name in calls[2]["tool_names"] for name in (
        "start_creation_execution", "resume_creation_execution", "cancel_creation_execution",
    ))


def test_business_event_turn_keeps_original_instruction_and_actual_task_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(pi_module, "_run_turn", lambda _settings, **kwargs: calls.append(kwargs) or ("ok", None))
    context = {"original_turn_id": "human:one", "event": {"status": "confirmed"},
               "unfinished_steps": ["complete_original_instruction"]}
    run_task_turn(None, workspace_id="local", task_id="task-one", message="改好后生成成片",
        stage="script", selected_refs=[], playhead_ms=None, continuation_context=context)
    assert calls[0]["message"] == "改好后生成成片"
    assert calls[0]["continuation_context"] == context
    assert calls[0]["prompt"] == pi_module.SYSTEM_PROMPT
    assert {"start_creation_execution", "read_task_observations", "observe_task_output", "read_task_observation_request"} <= set(calls[0]["tool_names"].split(","))


def test_observation_job_is_an_actual_receipt_and_never_a_completed_visual_judgment():
    job = {"job_id": "check:one", "status": "queued", "result_kind": "observation"}
    output = "\n".join(json.dumps(item) for item in [
        {"type": "tool_execution_end", "toolName": "observe_task_output", "isError": False,
         "result": {"details": {"kind": "task_media_job", "media_job": job}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "画面完全正确。"}]}}, {"type": "agent_settled"},
    ])
    answer, preview = project_event_fixture(output, "edit_preview")
    assert preview == {"kind": "media_job", "media_job": job}
    assert "尚未取得成功检查结果" in answer and "画面完全正确" not in answer


def test_pi_events_keep_plan_preview_separate_from_edit_preview():
    output = "\n".join(json.dumps(item) for item in [
        {"type": "tool_execution_end", "toolName": "preview_creation_plan", "isError": False,
         "result": {"details": {"kind": "plan_preview", "preview": {"name": "新任务"}}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "请检查新任务预检。"}]}},
        {"type": "agent_settled"},
    ])
    assert project_event_fixture(output, "plan_preview") == ("请检查新任务预检。", {"name": "新任务"})
    assert project_event_fixture(output, "edit_preview") == ("请检查新任务预检。", None)


def test_script_turn_keeps_generation_gate_with_shared_read_preview_tools(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    commands = []

    def fake_stream(cmd, **kwargs):
        commands.append(json.loads(kwargs["message"]))
        return _stream_completed(cmd, on_event=kwargs["on_event"], stdout="\n".join([
            json.dumps({"type": "tool_execution_end", "toolName": "preview_script_draft",
                        "isError": False, "result": {"details": {"kind": "script_draft",
                        "preview": {"script_id": "draft-one"}}}}),
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                        "stopReason": "stop", "content": [{"type": "text", "text": "请检查文案。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    _install_model_stream(monkeypatch, fake_stream)
    for task_id in ("task-one", "task-one", "task-two"):
        reply, preview = run_task_turn(settings, workspace_id="workspace-one", task_id=task_id,
                                       message="把开头改短", stage="script", selected_refs=[], playhead_ms=None)
        assert (reply, preview) == ("请检查文案。", {"script_id": "draft-one"})
    assert commands[0]["sessionId"] == commands[1]["sessionId"]
    assert commands[0]["sessionId"] != commands[2]["sessionId"]
    tools = commands[0]["tools"]
    assert {"read_current_task", "preview_script_draft", "preview_typed_edit", "read_task_object"} <= set(tools)
    assert "generate_creation_task_topics" not in tools
    _, edit_preview = run_task_turn(settings, workspace_id="workspace-one", task_id="task-one",
                                    message="检查镜头", stage="montage", selected_refs=[], playhead_ms=None)
    assert edit_preview is None
    assert commands[3]["sessionId"] != commands[0]["sessionId"]
    edit_tools = set(commands[3]["tools"])
    assert {"preview_typed_edit", "read_task_edit_capabilities", "read_task_object", "start_creation_execution"} <= edit_tools
    assert "generate_creation_task_topics" not in edit_tools and "preview_script_draft" in edit_tools



def test_turn_passes_verified_member_token_to_the_tool_runtime(monkeypatch, tmp_path):
    """已验证的本地成员令牌必须进到工具运行环境，否则工具回调会变成匿名 local 读取。"""
    (tmp_path / "models").mkdir()
    fake_pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    envs = []

    def fake_stream(cmd, **kwargs):
        envs.append(kwargs["env"])
        return _stream_completed(cmd, on_event=kwargs["on_event"], stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                "stopReason": "stop", "content": [{"type": "text", "text": "好的。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    _install_model_stream(monkeypatch, fake_stream)
    run_task_turn(settings, workspace_id="workspace-one", task_id="task-one", message="检查",
                  stage="montage", selected_refs=[], playhead_ms=None,
                  local_member_token="member-token-one", caller_user_id="user-one")
    run_plan_turn(settings, workspace_id="workspace-one", conversation_id="conv-one", message="你好",
                  local_member_token="member-token-plan", caller_user_id="user-two")
    assert envs[0]["AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN"] == "member-token-one"
    assert envs[1]["AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN"] == "member-token-plan"
    # 没有传身份/令牌时保持旧行为：单机 local 入口不被这次改动挡住。
    run_task_turn(settings, workspace_id="workspace-one", task_id="task-one", message="检查",
                  stage="montage", selected_refs=[], playhead_ms=None)
    assert envs[2]["AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN"] == ""


def test_trusted_callers_do_not_share_the_pi_session(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    sessions = []

    def fake_stream(cmd, **kwargs):
        sessions.append(json.loads(kwargs["message"])["sessionId"])
        return _stream_completed(cmd, on_event=kwargs["on_event"], stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                "stopReason": "stop", "content": [{"type": "text", "text": "好的。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    _install_model_stream(monkeypatch, fake_stream)
    for user_id in ("user-one", "user-one", "user-two"):
        run_plan_turn(settings, workspace_id="workspace-a", conversation_id="conv-one", message="你好",
                      caller_user_id=user_id)
    # 同一可信调用方沿用同一段会话；不同调用方不共用对话历史。
    assert sessions[0] == sessions[1]
    assert sessions[0] != sessions[2]
    # 令牌和身份不进会话键，也不出现在命令行参数里。
    run_plan_turn(settings, workspace_id="workspace-a", conversation_id="conv-one", message="你好",
                  local_member_token="member-token-one", caller_user_id="user-one")
    assert sessions[3] == sessions[0]


def test_pi_turn_uses_isolated_disk_and_keeps_secret_out_of_arguments(monkeypatch, tmp_path):
    model_root = tmp_path / "models"
    model_root.mkdir()
    settings = Settings(model_root_path=str(model_root), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    fake_pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    captured = {}
    schema = {'type':'object','properties':{'operation_type':{'type':'string'}}}
    monkeypatch.setattr(pi_module,'_read_task_before_pi',lambda *_args:{'task_id':'task-1',
        'current_timeline_version_id':'read-version','timeline_sequence':7,'selected_refs':['clip:clip-1'],
        'script_context':{'content':'TASK_FACTS_SENTINEL'},'_edit_capabilities':{'task_id':'task-1',
        'schema':schema,'operations':[{'operation_type':'TrimClip','available':True,'parameter_fields':['large_catalog']} ]}})

    def fake_stream(cmd, **kwargs):
        captured.update(cmd=cmd, kwargs=kwargs)
        return _stream_completed(cmd, on_event=kwargs["on_event"], stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                "stopReason": "stop", "content": [{"type": "text", "text": "收到。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    _install_model_stream(monkeypatch, fake_stream)
    answer, preview = run_task_turn(settings, workspace_id="workspace-1", task_id="task-1",
                                    message="请说明当前任务", stage="montage",
                                    selected_refs=["clip:clip-1"], playhead_ms=1200)
    assert (answer, preview) == ("收到。", None)
    payload = json.loads(captured['kwargs']['message'])
    model_input = json.loads(payload["message"].split("本轮输入（JSON）：\n", 1)[1])
    assert model_input["original_user_instruction"] == "请说明当前任务"
    assert model_input['task_binding']['current_timeline_version_id'] == 'read-version'
    assert model_input['task_binding']['timeline_sequence'] == 7
    assert 'current_task_facts' not in model_input
    assert 'TASK_FACTS_SENTINEL' not in payload['message'] + payload['systemPrompt']
    assert captured['kwargs']['message'].count('TASK_FACTS_SENTINEL') == 1
    assert payload['taskContext']['edit_capabilities']['read_tool'] == 'read_task_edit_capabilities'
    assert 'read_task_edit_capabilities' in payload['tools']
    assert payload['taskContext']['edit_capabilities']['available_operation_count'] == 1
    assert 'operations' not in payload['taskContext']['edit_capabilities']
    assert json.loads(captured['kwargs']['env']['AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES'])['schema'] == schema
    assert payload['modelProvider'] == 'minimax'
    assert captured["kwargs"]["env"]["PATH"].split(__import__("os").pathsep)[0] == str(fake_pi.parent)
    assert captured["kwargs"]["env"]["AI_VIDEO_DIARY_LLM_KEY"] == "private-test-key"
    assert "private-test-key" not in " ".join(captured["cmd"])
    assert len(captured["cmd"]) == 2 and captured["cmd"][1].endswith("agent_runtime/host.mjs")
    assert "--print" not in captured["cmd"]
    runtime_root = Path(captured["kwargs"]["cwd"])
    assert runtime_root.parent.parent == tmp_path / "agent" / "workspaces" / "workspace-1"
    assert "private-test-key" not in (runtime_root / "models.json").read_text()
    definition = json.loads((runtime_root / "models.json").read_text())["providers"]["ai-video-diary"]["models"][0]
    assert definition == {"id": "MiniMax-M3", "samplingParams": {"reasoning_split": True}}


def test_continuation_input_preserves_authorization_without_replaying_its_first_step(controlled_pi_runtime, monkeypatch):
    settings, _ = controlled_pi_runtime
    captured = {}
    def completed(_cmd, **kwargs):
        captured.update(kwargs)
        return _stream_completed(_cmd, on_event=kwargs["on_event"], stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                "content": [{"type": "text", "text": "依据实际完成来源继续。"}]}}), '{"type":"agent_settled"}']), stderr="")
    monkeypatch.setattr(pi_module, "_run_pi_streaming", completed)
    raw = "先创建图片，提交后本轮结束；成功后观察并准备修改，等我采用。"
    context = {"next_checkpoint": "read_completed_source_and_continue", "unfinished_steps": ["read_completed_source_and_continue"],
               "completed_source_slots": [{"name": "title.png", "source_ref": "artifacts/actual/title.png"}]}
    run_task_turn(settings, workspace_id="local", task_id="task", message=raw, stage="montage", selected_refs=[],
        playhead_ms=None, continuation_context=context)
    payload = json.loads(json.loads(captured["message"])["message"].split("本轮输入（JSON）：\n", 1)[1])
    assert payload["original_authorization_context"] == raw and "original_user_instruction" not in payload
    assert payload["trusted_continuation_checkpoint"] == context
    assert captured["env"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == raw


def test_pi_events_keep_thinking_outside_answer_without_rewriting_text():
    text = "说明中保留 <think> 示例，不改写正常正文。"
    output = "\n".join(json.dumps(event) for event in [
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "thinking", "thinking": "离线思考样本"},
                     {"type": "text", "text": text}]}},
        {"type": "agent_settled"},
    ])
    assert project_event_fixture(output) == (text, None)


@pytest.mark.parametrize("provider,model", [
    ("deepseek", "deepseek-chat"), ("custom", "MiniMax-M3"), ("minimax", "custom-model"),
])
def test_pi_reasoning_split_does_not_change_other_models(monkeypatch, tmp_path, provider, model):
    (tmp_path / "models").mkdir()
    monkeypatch.setattr("app.assistant_pi.pi_runtime_checks", lambda: [])
    monkeypatch.setattr("app.assistant_pi._pi_executable", lambda: "/synthetic/pi")
    captured = {}

    def capture_stream(_cmd, **kwargs):
        captured.update(json.loads((Path(kwargs["cwd"]) / "models.json").read_text()))
        raise RuntimeError("offline request boundary")

    monkeypatch.setattr(pi_module, "_run_pi_streaming", capture_stream)
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_provider=provider,
                        llm_api_key="synthetic-key", llm_api_key_provider=provider,
                        llm_base_url="https://example.com/v1", llm_model=model)
    with pytest.raises(RuntimeError, match="offline request boundary"):
        run_plan_turn(settings, workspace_id="offline", conversation_id="offline", message="测试")
    assert captured["providers"]["ai-video-diary"]["models"] == [{"id": model}]


def test_pi_turn_requires_external_text_model(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_provider="minimax")
    with pytest.raises(AssistantUnavailable, match="API Key"):
        run_task_turn(settings, workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)


def test_assistant_route_scopes_task_before_invoking_pi(monkeypatch, tmp_path):
    seen = []
    def scoped_task(workspace_id, task_id):
        if workspace_id != "workspace-a" or task_id != "task-a":
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="task not found")
        # 助手入口会先按可信身份确认这条任务读得到，因此替身也要带上
        # visibility / owner_user_id 这两个真实字段。
        return SimpleNamespace(
            id="task-a", workspace_id=workspace_id, visibility="private",
            owner_user_id="member-caller", stage="script", current_timeline_version_id=None, timeline_sequence=0,
        )
    monkeypatch.setattr(main_module, "current_workspace_id",
                        lambda request: request.headers.get("x-test-workspace", "workspace-a"))
    # 助手只在请求带可信身份时读私有任务：这里用真实成员令牌表明调用方。
    monkeypatch.setattr(
        main_module, "_local_member_token",
        lambda request: request.headers.get("x-test-member", "member-caller"),
    )
    monkeypatch.setattr(
        main_module, "local_members_service",
        SimpleNamespace(resolve_member=lambda token: SimpleNamespace(
            member_id=token, role="member", name="调用方", created_at=None,
        )),
    )
    monkeypatch.setattr(main_module, "_creation_task_or_404", scoped_task)
    repo = ConversationRepository(tmp_path / "assistant-task.sqlite3")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    conversation = repo.current_or_create("workspace-a", "member-caller", "task", "task-a")
    monkeypatch.setattr(main_module, "_assistant_turn_scope",
                        lambda workspace_id, task, payload: (payload.context_stage, payload.selected_object_refs))
    monkeypatch.setattr(main_module, "_assistant_task_clip_material_selection", lambda *_args: None)
    monkeypatch.setattr(main_module, "production_repo", SimpleNamespace(execution_records=SimpleNamespace(append_task_event=lambda event: event), subtitle_records=SimpleNamespace(list_subtitle_tracks=lambda *_: [])))
    monkeypatch.setattr(main_module, "run_task_turn", lambda settings, **kwargs: (seen.append(kwargs) or ("收到。", None)))
    client = TestClient(main_module.app)
    message = "请检查镜头"
    saved = repo.save_draft(conversation["id"], "workspace-a", "member-caller", message, 0)
    payload = {"conversation_id": conversation["id"], "message": message, "context_stage": "montage",
               "client_request_id": str(uuid4()), "expected_draft_version": saved["draft_version"],
               "selected_object_refs": [], "playhead_ms": 1200, "expected_reference_version":0}
    denied = client.post("/api/creation-tasks/task-a/assistant/turn", json=payload,
                         headers={"x-test-workspace": "workspace-b"})
    assert denied.status_code == 404
    assert seen == []
    accepted = client.post("/api/creation-tasks/task-a/assistant/turn", json=payload)
    assert accepted.status_code == 200
    assert accepted.json()["reply"] == "收到。"
    assert seen[0]["workspace_id"] == "workspace-a"
    assert seen[0]["selected_refs"] == []


def test_new_plan_turn_uses_workspace_and_conversation_boundary(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(main_module, "current_workspace_id",
                        lambda request: request.headers.get("x-test-workspace", "workspace-a"))
    monkeypatch.setattr(main_module, "run_plan_turn",
                        lambda settings, **kwargs: (seen.append(kwargs) or ("请说明视频目标。", None)))
    monkeypatch.setattr(main_module, "_local_member_token", lambda _request: "member-caller")
    monkeypatch.setattr(main_module, "local_members_service", SimpleNamespace(
        resolve_member=lambda token: SimpleNamespace(member_id=token, role="member", name="成员甲", created_at=None),
    ))
    repo = ConversationRepository(tmp_path / "assistant-plan.sqlite3")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    conversation = repo.current_or_create("workspace-b", "member-caller", "home", "home")
    client = TestClient(main_module.app)
    rejected = client.post("/api/assistant/plan/turn", json={"message": "做视频", "conversation_id": "bad"})
    assert rejected.status_code == 422
    assert seen == []
    message = "做视频"
    saved = client.patch(f"/api/assistant/conversations/{conversation['id']}",
                         json={"draft_text": message, "expected_draft_version": conversation["draft_version"]},
                         headers={"x-test-workspace": "workspace-b"})
    assert saved.status_code == 200
    request_body = {"message": message, "conversation_id": conversation["id"],
                    "client_request_id": str(uuid4()),
                    "expected_draft_version": saved.json()["draft_version"]}
    response = client.post("/api/assistant/plan/turn", json=request_body,
                           headers={"x-test-workspace": "workspace-b"})
    assert response.status_code == 200
    assert response.json()["plan_preview"] is None
    assert response.json()["draft_version"] == saved.json()["draft_version"] + 1
    assert seen[0]["workspace_id"] == "workspace-b"
    assert seen[0]["conversation_id"] == conversation["id"]
    replay = client.post("/api/assistant/plan/turn", json=request_body,
                         headers={"x-test-workspace": "workspace-b"})
    assert replay.status_code == 200 and replay.json() == response.json()
    assert len(seen) == 1
    reused = client.post("/api/assistant/plan/turn", json={**request_body, "message": "复用身份提交其他内容"},
                         headers={"x-test-workspace": "workspace-b"})
    assert reused.status_code == 409
    assert len(seen) == 1

    streamed_message = "补充新的制作要求"
    latest = repo.get(conversation["id"], "workspace-b", "member-caller")
    streamed_draft = client.patch(f"/api/assistant/conversations/{conversation['id']}",
                                  json={"draft_text": streamed_message,
                                        "expected_draft_version": latest["draft_version"]},
                                  headers={"x-test-workspace": "workspace-b"})
    assert streamed_draft.status_code == 200
    streamed_body = {"message": streamed_message, "conversation_id": conversation["id"],
                     "client_request_id": str(uuid4()),
                     "expected_draft_version": streamed_draft.json()["draft_version"]}
    stream_response = client.post("/api/assistant/plan/turn", json=streamed_body,
                                  headers={"x-test-workspace": "workspace-b", "accept": "text/event-stream"})
    assert stream_response.status_code == 200
    stream_events = [json.loads(line[6:]) for line in stream_response.text.splitlines() if line.startswith("data: ")]
    consumed_version = streamed_draft.json()["draft_version"] + 1
    assert any(event.get("type") == "turn_started" and event.get("draft_version") == consumed_version
               for event in stream_events)
    terminal_result = next(event["result"] for event in stream_events if event.get("type") == "result")
    assert terminal_result["draft_version"] == consumed_version
    assert len(seen) == 2


def test_new_plan_conversations_do_not_share_pi_session(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = install_sdk_fixture(tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    sessions = []

    def fake_stream(cmd, **kwargs):
        sessions.append(json.loads(kwargs["message"])["sessionId"])
        return _stream_completed(cmd, on_event=kwargs["on_event"], stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                "stopReason": "stop", "content": [{"type": "text", "text": "请补充目标。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    _install_model_stream(monkeypatch, fake_stream)
    for conversation_id in ("one", "one", "two"):
        run_plan_turn(settings, workspace_id="workspace-a", conversation_id=conversation_id,
                      message="你好", authorization=None, cookie=None)
    assert sessions[0] == sessions[1]
    assert sessions[0] != sessions[2]


def test_original_input_and_late_receipts_are_separate_creation_results():
    from app.assistant.result_projection import project_business_result
    request = {"id": "request-real", "target_count": 1, "clarification_questions": [], "current_window": {"status": "proposed", "entries": [1]}}
    event = {"type": "tool_execution_end", "toolName": "prepare_creation_window", "isError": False,
             "result": {"details": {"kind": "plan_preview", "invocation_input_id": "original-turn", "preview": {"kind": "creation_request", "request": request}}}}
    completion = {"status": "settled", "answer": "已保存", "input_id": "original-turn", "tool_results": [event]}
    assert project_business_result(completion, "plan_preview")[1]["request"]["id"] == "request-real"
    completion["input_id"] = "follow-up"
    assert project_business_result(completion, "plan_preview")[1] is None


def test_material_reference_keeps_workspace_creation_and_exact_tag_target(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(pi_module, "_run_turn", lambda settings, **kwargs: (seen.update(kwargs) or "收到", None))
    run_plan_turn(Settings(model_root_path=str(tmp_path)), workspace_id="w", conversation_id="c", message="用选中素材建视频",
                  selected_references=[{"kind": "material", "selection": {"scope": "asset", "asset_id": "selected"}}])
    assert seen["material_selection"] == {"scope": "asset", "assetId": "selected"}
    assert {"prepare_creation_window", "preview_creation_plan", "update_selected_material_tags"} <= set(seen["tool_names"].split(","))


@pytest.mark.parametrize('status',['saved','confirmed','unknown','failed','not_submitted','rejected'])
def test_script_window_projection_keeps_original_facts_and_discards_raw_candidate(status):
    request_id=str(uuid4())
    operation='confirm' if status=='confirmed' else 'patch'
    receipt={'task_id':'task','script_id':'script','request_id':request_id,'status':status,
             'updated_at':'2026-10-25T00:00:00+00:00','total_count':45,'affected_section_ids':['section-45'],
             'submitted_input':{'content':'原文秘密'},'raw_provider':'/private/source'}
    diagnostics={'error':'script_fact_unsupported','reason_code':'unsupported_fact','section_index':1,
        'candidate_section_index':1,'candidate_field':'content','candidate_fragment_index':0,
        'candidate_field_digest':'a'*64,'reason':'第1段事实无据'}
    details={'kind':'script_window_request','operation':operation,'task_id':'task','script_id':'script',
             'request_id':request_id,'receipt':receipt,'invocation_input_id':'input','input_id':'input',
             'input_turn_id':'turn','raw_diagnostics':{'source':'/private/file'}}
    if status=='failed':
        receipt['error_detail']=diagnostics
    if status in {'not_submitted','rejected','unknown'}:
        details['failure']={**(diagnostics if status=='rejected' else {}),'submission_effect':status}
    tool='confirm_saved_script' if operation=='confirm' else 'patch_script_sections'
    claim='全部已经导出完毕。'
    output='\n'.join(json.dumps(value,ensure_ascii=False) for value in [
        {'type':'tool_execution_end','toolName':tool,'isError':status not in {'saved','confirmed'},'result':{'details':details}},
        {'type':'message_end','message':{'role':'assistant','stopReason':'stop','content':[{'type':'text','text':claim}]}},
        {'type':'agent_settled'}])
    message,projected=project_event_fixture(output)
    assert message!=claim
    assert projected['request_id']==request_id and projected['status']==status
    assert projected['operation']==operation and projected['task_id']=='task' and projected['script_id']=='script'
    assert projected['input_id']=='input'
    if status in {'saved','confirmed'}:
        assert projected['total_count']==45 and projected['updated_at']==receipt['updated_at']
    if status in {'unknown','failed'}:
        assert projected['submission_effect']=='unknown'
    if status in {'failed','rejected'}:
        assert projected['failure']['candidate_field']=='content'
    public=json.dumps(projected,ensure_ascii=False)
    assert '/private/' not in public and '原文秘密' not in public and 'submitted_input' not in public


def test_script_window_projection_filters_previous_input_and_queries_do_not_invent_success():
    request_id=str(uuid4())
    current={'kind':'script_window_request','operation':'start','task_id':'task','request_id':request_id,
             'invocation_input_id':'new','input_id':'new','failure':{'submission_effect':'unknown','error':'http_404'}}
    old={**current,'invocation_input_id':'old','input_id':'old',
         'receipt':{'task_id':'task','request_id':request_id,'script_id':'script','status':'saved',
                    'updated_at':'2026-10-25T00:00:00+00:00','total_count':20}}
    events=[{'type':'tool_execution_end','toolName':'start_script_window','isError':False,'result':{'details':old}},
            {'type':'tool_execution_end','toolName':'read_script_window_request','isError':True,'result':{'details':current}},
            {'type':'agent_settled'}]
    _,value=project_event_fixture('\n'.join(json.dumps(event) for event in events))
    assert value['status']=='unknown' and value['input_id']=='new'
    assert value['failure']['error']=='http_404' and value['submission_effect']=='unknown'


def test_script_window_resolver_reads_only_original_receipt_and_never_treats_404_as_unsubmitted():
    from app.assistant.script_window_receipts import resolve_script_window_request
    value={'kind':'script_window_request','operation':'confirm','task_id':'task','script_id':'script',
           'request_id':str(uuid4()),'status':'unknown','submission_effect':'unknown'}
    calls=[]
    def missing(request_id,operation,script_id):
        calls.append((request_id,operation,script_id))
        raise KeyError(request_id)
    absent=resolve_script_window_request(value,task_id='task',read_original=missing)
    assert absent['status']=='unknown' and absent['submission_effect']=='unknown'
    receipt={'task_id':'task','script_id':'script','request_id':value['request_id'],'status':'confirmed',
             'updated_at':'2026-10-25T00:00:00+00:00','total_count':45,'submitted_input':{'body':'不持久复制原文'}}
    resolved=resolve_script_window_request(value,task_id='task',read_original=lambda *args:receipt)
    assert resolved['status']=='confirmed' and resolved['total_count']==45
    assert 'submitted_input' not in resolved and 'failure' not in resolved
    broken=resolve_script_window_request({**value,'status':'confirmed','updated_at':receipt['updated_at'],'total_count':45},
        task_id='task',read_original=lambda *args:None)
    assert broken['status']=='unknown' and 'updated_at' not in broken
    with pytest.raises(ValueError):
        resolve_script_window_request(value,task_id='another-task',read_original=missing)
    assert calls==[(value['request_id'],'confirm','script')]


@pytest.mark.parametrize('operation',['patch','confirm'])
def test_real_window_query_producer_projects_script_identity_for_restored_requests(tmp_path,operation):
    node=shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    module=Path(__file__).resolve().parents[1]/'agent_tools/diary/script-tools.ts'
    request_id=str(uuid4())
    code="""
const {registerScriptTools}=await import(process.argv[1]);
const {createToolCallScope}=await import(new URL('./registration.ts',process.argv[1]));
const operation=process.argv[2],request_id=process.argv[3],tools=new Map();
const scope=createToolCallScope(()=>({turnId:'turn',inputId:'input',inputTurnId:'input-turn'}));
registerScriptTools(scope.bind({registerTool:tool=>tools.set(tool.name,tool)}),{taskId:'task',request:async()=>({
 task_id:'task',script_id:'script',request_id,status:operation==='confirm'?'confirmed':'saved',
 updated_at:'2026-10-25T00:00:00+00:00',total_count:45,submitted_input:{content:'不能持久复制'}})});
const result=await tools.get('read_script_window_request').execute('restore',{request_id,operation,script_id:'script',include_input:true});
console.log(JSON.stringify(result));
"""
    completed=subprocess.run([node,'--experimental-strip-types','--input-type=module','-e',code,module.as_uri(),operation,request_id],
        cwd=tmp_path,capture_output=True,text=True,check=True,timeout=10)
    producer=json.loads(completed.stdout)
    assert producer['details']['script_id']=='script'
    event={'type':'tool_execution_end','toolName':'read_script_window_request','isError':producer.get('isError',False),'result':producer}
    _,projected=project_event_fixture(json.dumps(event)+'\n'+json.dumps({'type':'agent_settled'}))
    assert projected['status']==('confirmed' if operation=='confirm' else 'saved')
    assert projected['script_id']=='script' and projected['request_id']==request_id
    assert projected['total_count']==45 and 'submitted_input' not in projected
    from app.assistant.projection import _PiStreamProjector
    streamed=_PiStreamProjector().project(json.dumps(event))
    assert streamed['script_window_request']==projected and streamed['invocation_input_id']=='input'
    assert '不能持久复制' not in json.dumps(streamed,ensure_ascii=False)


def test_stream_window_receipt_is_available_before_agent_settles_and_keeps_initial_none_input():
    from app.assistant.projection import _PiStreamProjector
    request_id=str(uuid4())
    details={'kind':'script_window_request','task_id':'task','request_id':request_id,'operation':'start',
        'input_id':None,'input_turn_id':None,'invocation_input_id':'turn',
        'receipt':{'task_id':'task','request_id':request_id,'script_id':'script','status':'saved',
            'updated_at':'2026-10-25T00:00:00+00:00','submitted_input':{'content':'private candidate'}}}
    event=_PiStreamProjector().project(json.dumps({'type':'tool_execution_end','toolName':'start_script_window',
        'toolCallId':'call','result':{'details':details}}))
    window=event['script_window_request']
    assert window['request_id']==request_id and window['status']=='saved' and window['operation']=='start'
    assert window.get('input_id') is None and window.get('input_turn_id') is None
    assert event['invocation_input_id']=='turn' and 'private candidate' not in json.dumps(event)


@pytest.mark.parametrize('invalid',['other_tool','wrong_operation','foreign_input','missing_source'])
def test_stream_window_locator_rejects_unrelated_tool_and_unbound_invocation(invalid):
    from app.assistant.projection import _PiStreamProjector
    details={'kind':'script_window_request','task_id':'task','request_id':str(uuid4()),'script_id':'script','operation':'patch',
        'input_id':'input','input_turn_id':'turn','invocation_input_id':'input',
        'failure':{'submission_effect':'unknown'}}
    tool='patch_script_sections'
    if invalid=='other_tool':
        tool='read_current_task'
    if invalid=='wrong_operation':
        details['operation']='confirm'
    if invalid=='foreign_input':
        details['invocation_input_id']='old-input'
    if invalid=='missing_source':
        del details['input_id']
    event=_PiStreamProjector().project(json.dumps({'type':'tool_execution_end','toolName':tool,'result':{'details':details}}))
    assert 'script_window_request' not in event and 'invocation_input_id' not in event


def test_thirteen_review_issues_survive_real_stream_and_final_window_failure_projection():
    import hashlib
    issues=[{'error':'script_fact_unsupported','reason_code':'unsupported_fact','section_index':index,
        'candidate_section_index':index,'candidate_field':'content','candidate_fragment_index':0,
        'candidate_field_digest':hashlib.sha256(f'第{index}段待核实事实'.encode()).hexdigest(),
        'review_explanation':f'第{index}段需要证据。 /private/review/source Bearer fixture-private-token',
        'raw_candidate':'不得持久复制的候选正文'} for index in range(1,14)]
    failure={'error':'script_fact_unsupported','reason_code':'unsupported_fact','reason':'13段事实需要核实',
        'submission_effect':'rejected','review_issues':issues}
    details={'kind':'script_window_request','operation':'patch','task_id':'task','script_id':'script',
        'request_id':str(uuid4()),'input_id':'input','input_turn_id':'turn','invocation_input_id':'input','failure':failure}
    event={'type':'tool_execution_end','toolName':'patch_script_sections','toolCallId':'review',
        'isError':True,'result':{'details':details}}
    streamed=_PiStreamProjector().project(json.dumps(event))
    _,final=project_event_fixture(json.dumps(event)+'\n'+json.dumps({'type':'agent_settled'}))
    for public in (streamed['failure'],streamed['script_window_request']['failure'],final['failure']):
        assert len(public['review_issues'])==13
        assert [issue['candidate_section_index'] for issue in public['review_issues']]==list(range(1,14))
        assert [issue['candidate_field_digest'] for issue in public['review_issues']]==[issue['candidate_field_digest'] for issue in issues]
        assert all(set(issue)=={'error','reason_code','section_index','candidate_section_index','candidate_field',
            'candidate_fragment_index','candidate_field_digest','review_explanation'} for issue in public['review_issues'])
    serialized=json.dumps({'stream':streamed,'final':final},ensure_ascii=False)
    assert all(secret not in serialized for secret in ('/private/review/source','fixture-private-token','不得持久复制的候选正文'))
