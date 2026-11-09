"""Trusted instruction binding and actual shipped-Node preflight; never call a model."""

from assistant_sdk_fixtures import project_event_fixture, sdk_completed
import json
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.assistant_pi as pi
import app.main as main
from app.config import Settings
from app.creation.contracts import TimelineVersion
from app.schemas import CreationTaskEditPreviewRequest, CreationTaskExactEditRequest, MediaScriptPreviewRequest, EXACT_EDIT_FIELDS, MediaScriptToolSaveRequest, MediaScriptToolUpdateRequest, MediaScriptToolRevokeRequest, MediaScriptToolPreviewRequest
from app.visual_effects import compile_crop_command
from test_assistant_task_context_tools import task_workspace
from test_task_assistant_timeline_snapshot import task_client as task_client
from test_crop_preview_description import legal_crop_state as legal_crop_state, crop_previews as crop_previews
from test_montage_pipeline import person_render_edit_state as person_render_edit_state

ORIGINAL = "把当前选中的第1个镜头改为完整显示。保持镜头顺序、时长、配音和字幕不变，生成修改提案。"


@pytest.fixture(autouse=True)
def isolated_memory_read(monkeypatch):
    # This suite targets the task preflight; actual memory reads and failures
    # are covered by test_assistant_memory_host, not by the external service.
    monkeypatch.setattr(pi, "_read_memory_before_pi", lambda *_args: {
        "revision": 0, "common_expressions": [], "banned_words": [], "style_preferences": {},
    })


def test_original_fit_sentence_preserves_crop_and_checks_selected_index():
    current = dict(fit_mode="fill", crop_x=.4, crop_y=.7, clip_index=1)
    assert compile_crop_command(ORIGINAL, current) == dict(fit_mode="fit", crop_x=.4, crop_y=.7)
    assert compile_crop_command("水平裁剪到40%", {**current, "crop_x": .5}) == {key:value for key,value in current.items() if key != "clip_index"}
    with pytest.raises(ValueError, match="序号.*不一致"):
        compile_crop_command(ORIGINAL.replace("第1", "第2"), current)
    with pytest.raises(ValueError, match="尚未支持"):
        compile_crop_command(ORIGINAL.replace("保持镜头顺序、时长、配音和字幕不变", "删除配音"), current)


@pytest.fixture
def bound_turn(task_client):
    create, production, conversations, _, member_id = task_client
    client, headers, initial = create(0)
    response = client.patch(f"/api/assistant/conversations/{initial['conversation_id']}", headers=headers,
                            json={"draft_text": ORIGINAL, "expected_draft_version": 1})
    assert response.status_code == 200
    context = dict(kind="task", task_id="task", stage="montage", selected_object_refs=["clip:clip-1"], playhead_ms=12,
                   target_snapshot={"timeline_version_id": None, "timeline_sequence": 0})
    turn, created = conversations.create_turn_idempotent(initial["conversation_id"], "qa", member_id, ORIGINAL,
                                                        str(uuid4()), 2, request_context=context)
    assert created
    req = Request({"type":"http", "headers":[(key.lower().encode(), value.encode()) for key,value in headers.items()]})
    task = production.task_records.get_creation_task("qa", "task")
    payload = CreationTaskEditPreviewRequest(assistant_turn_id=turn["id"], raw_command="水平裁剪到0%",
                                            context_stage="montage", selected_object_refs=["clip:clip-1"], playhead_ms=12)
    return req, task, payload, production, conversations


def test_server_uses_persisted_instruction_not_model_rewrite(bound_turn):
    req, task, payload, _, _ = bound_turn
    bound = main._bind_assistant_edit_instruction(req, task, payload)
    assert bound.raw_command == ORIGINAL and payload.raw_command == "水平裁剪到0%"
    direct = payload.model_copy(update={"assistant_turn_id": None})
    assert main._bind_assistant_edit_instruction(req, task, direct) is direct


@pytest.mark.parametrize("change", [{"context_stage":"review"}, {"selected_object_refs":["clip:clip-2"]}, {"playhead_ms":13}])
def test_server_rejects_model_target_drift(bound_turn, change):
    req, task, payload, _, _ = bound_turn
    with pytest.raises(HTTPException) as caught:
        main._bind_assistant_edit_instruction(req, task, payload.model_copy(update=change))
    assert caught.value.status_code == 409


def test_server_rechecks_running_owner_and_timeline(bound_turn, monkeypatch):
    req, task, payload, production, conversations = bound_turn
    with pytest.raises(HTTPException) as caught:
        main._bind_assistant_edit_instruction(req, replace(task, id="other"), payload)
    assert caught.value.status_code == 409
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "other-workspace")
    with pytest.raises(HTTPException) as caught:
        main._bind_assistant_edit_instruction(req, task, payload)
    assert caught.value.status_code == 404
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "qa")
    production.timeline_records.create_timeline_version(TimelineVersion(id="changed", workspace_id="qa", task_id="task", parent_version_id=None,
                                                     sequence=1, snapshot={}, created_by_operation_id=None))
    task = production.task_records.get_creation_task("qa", "task")
    with pytest.raises(HTTPException, match="版本已变化"):
        main._bind_assistant_edit_instruction(req, task, payload)
    conversations.finish_turn(str(payload.assistant_turn_id), status="failed", result={"reason":"controlled"})
    with pytest.raises(HTTPException, match="当前有效助手轮次"):
        main._bind_assistant_edit_instruction(req, task, payload)


@pytest.fixture
def loopback_task(monkeypatch, request):
    from assistant_turn_control_fixtures import isolate_assistant_turn_control
    isolate_assistant_turn_control(monkeypatch, request)
    state = {"status":200, "calls":[], "entered":threading.Event(), "release":threading.Event(), "block":False, "wrong_task":False}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"].append((self.path, self.headers.get("X-Local-Member-Token")))
            if state["block"]:
                state["entered"].set()
                state["release"].wait(timeout=5)
            if self.path == "/api/media-script-tools/capabilities":
                body = {"schemas": {name: model.model_json_schema() for name,model in (
                    ("save",MediaScriptToolSaveRequest),("update",MediaScriptToolUpdateRequest),
                    ("revoke",MediaScriptToolRevokeRequest),("invoke",MediaScriptToolPreviewRequest))},
                    "invocation_phase":"script", "result_kind":"graphic_source"}
            elif self.path.endswith("/assistant/edit-capabilities"):
                body = {"task_id":"task-real", "timeline_version_id": None, "timeline_sequence": 0,
                        "schema":CreationTaskExactEditRequest.model_json_schema(),
                        "supported_operations":list(EXACT_EDIT_FIELDS), "operations":[], "units":{}}
            elif self.path.endswith("/assistant/media-script-capabilities"):
                body = {"task_id":"task-real", "schema":MediaScriptPreviewRequest.model_json_schema(),
                        "available":False, "reason":"controlled missing toolchain", "runtime":None,
                        "limits":None, "purpose":"preview", "input_kinds":["task_graphic_png"], "output_kinds":["graphic_source"]}
            else:
                body = ({"task_id":"task-real", "job":None, "current_stage":None, "waiting_reason":None, "status_updated_at":None}
                        if self.path.endswith("/execution") else task_workspace())
            if state["wrong_task"] and "task" in body:
                body["task"]["id"] = "other-task"
            if state["status"] != 200:
                body = {"detail":"controlled permission revoked"}
            self.send_response(state["status"])
            self.send_header("Content-Type","application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(body).encode())
            except BrokenPipeError:
                pass
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state["port"] = server.server_port
    yield state
    state["release"].set()
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


@pytest.mark.parametrize("read_result", ["ok", "permission_denied", "wrong_task"])
def test_actual_packaged_extension_node_read_is_before_pi_and_fail_closed(tmp_path, monkeypatch, loopback_task, read_result):
    state = loopback_task
    state["status"] = 403 if read_result == "permission_denied" else 200
    state["wrong_task"] = read_result == "wrong_task"
    monkeypatch.setenv("API_PORT", str(state["port"]))
    extension = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXTENSION", str(extension))
    monkeypatch.setenv("AI_VIDEO_DIARY_AGENT_TASK_READ_READY", "1")
    monkeypatch.setattr(pi, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr("app.assistant.runtime_context.sdk_module", lambda _: Path("/synthetic/index.js"))
    monkeypatch.setattr(pi, "_pi_executable", lambda: "/usr/bin/pi")
    monkeypatch.setattr(pi, "_configured_model", lambda _: ("https://controlled.invalid", "fixture", "unused"))
    monkeypatch.setattr(pi, "_agent_root", lambda *_: tmp_path)
    records, launches, events = [], [], []
    monkeypatch.setattr(pi, "_record_service", lambda *_, **kwargs: records.append(kwargs))
    controlled_runner = pi._run_pi_streaming
    def launch(cmd, **kwargs):
        if "--experimental-strip-types" in cmd:
            return controlled_runner(cmd, **kwargs)
        launches.append((cmd,kwargs))
        assert state["calls"] == [(path, "trusted-member") for path in (
            "/api/creation-tasks/task-real", "/api/creation-tasks/task-real/execution",
            "/api/creation-tasks/task-real/assistant/edit-capabilities",
            "/api/creation-tasks/task-real/assistant/media-script-capabilities", "/api/media-script-tools/capabilities")]
        assert kwargs["env"]["AI_VIDEO_DIARY_AGENT_TASK_READ_READY"] == "1"
        assert kwargs["env"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == ORIGINAL
        assert json.loads(kwargs["env"]["AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES"])["schema"] == CreationTaskExactEditRequest.model_json_schema()
        assert "preview_typed_edit" in json.loads(kwargs.get("message", kwargs.get("input")))["tools"]
        assert "preview_edit" not in json.loads(kwargs.get("message", kwargs.get("input")))["tools"]
        model_input = json.loads(json.loads(kwargs["message"])["message"].split("本轮输入（JSON）：\n", 1)[1])
        assert model_input["original_user_instruction"] == ORIGINAL
        assert model_input["task_binding"]["task_id"] == "task-real"
        assert model_input["task_binding"]["selected_refs"] == []
        assert model_input["task_binding"]["timeline_sequence"] == 0
        context = json.loads(kwargs["message"])["taskContext"]
        assert context["plan"]["knowledge_reference_count"] == 1
        assert context["plan"]["source_reference_count"] == 1
        envelope = json.loads(kwargs["message"])
        model_payload = json.dumps({key: envelope[key] for key in ("message", "taskContext", "systemPrompt")}, ensure_ascii=False)
        assert "large-body" not in model_payload and "secret-token" not in model_payload and "/private/" not in model_payload
        return sdk_completed(cmd, 0, stdout=json.dumps({"type":"message_end","message":{"role":"assistant","stopReason":"stop","content":[{"type":"text","text":"已核对当前任务。"}]}})+"\n"+json.dumps({"type":"agent_settled"}), stderr="")
    monkeypatch.setattr(pi, "_run_pi_streaming", launch)
    values = dict(workspace_id="qa", task_id="task-real", message=ORIGINAL, stage="montage", selected_refs=[], playhead_ms=None,
                  authorization=None,cookie=None,local_member_token="trusted-member",caller_user_id="owner",prompt="受控专项",
                  tool_names="read_current_task,preview_typed_edit",preview_kind="edit_preview",task_version={"timeline_version_id":None,"timeline_sequence":0},
                  turn_id=str(uuid4()),on_event=events.append)
    if read_result != "ok":
        with pytest.raises(pi.AssistantUnavailable, match="发送前读取当前任务失败"):
            pi._run_turn(Settings(), **values)
        assert launches == [] and records == []
        assert events[-1]["ok"] is False
    else:
        assert pi._run_turn(Settings(), **values)[0] == "已核对当前任务。"
        assert len(launches) == 1 and events[-1]["ok"] is True
        assert state["calls"] == [(path, "trusted-member") for path in (
            "/api/creation-tasks/task-real", "/api/creation-tasks/task-real/execution",
            "/api/creation-tasks/task-real/assistant/edit-capabilities",
            "/api/creation-tasks/task-real/assistant/media-script-capabilities", "/api/media-script-tools/capabilities")]
    assert events[0] == {"type":"tool_start","tool_id":0,"label":"读取当前任务（发送前）"}


@pytest.mark.parametrize("streaming", [True, False])
def test_task_switch_updates_current_model_input_without_rewriting_bound_instruction_or_session(tmp_path, monkeypatch, bound_turn, streaming):
    _req, _task, payload, _, conversations = bound_turn
    monkeypatch.setattr(pi, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr("app.assistant.runtime_context.sdk_module", lambda _: Path("/synthetic/index.js"))
    monkeypatch.setattr(pi, "_pi_executable", lambda: "/usr/bin/pi")
    monkeypatch.setattr(pi, "_configured_model", lambda _: ("https://controlled.invalid", "fixture", "unused"))
    monkeypatch.setattr(pi, "_agent_root", lambda *_: tmp_path)
    monkeypatch.setattr(pi, "_record_service", lambda *_, **__: None)
    facts_by_task = {
        "task-fa": {"task_id":"task-fa", "current_timeline_version_id":"timeline-fa", "timeline_sequence":5,
                    "selected_refs":["clip:clip-fa"], "selected_stage":"review", "playhead_ms":10},
        "task-45": {"task_id":"task-45", "current_timeline_version_id":"timeline-45", "timeline_sequence":59,
                    "selected_refs":["clip:clip-45"], "selected_stage":"montage", "playhead_ms":12},
    }
    monkeypatch.setattr(pi, "_read_task_before_pi", lambda _extension, env, _cwd: facts_by_task[env["AI_VIDEO_DIARY_AGENT_TASK_ID"]])
    launches = []
    def launch(cmd, **kwargs):
        launches.append((cmd, kwargs))
        return sdk_completed(cmd, 0, stdout=json.dumps({"type":"message_end","message":{"role":"assistant","stopReason":"stop","content":[{"type":"text","text":"受控回复"}]}})+"\n"+json.dumps({"type":"agent_settled"}), stderr="")
    monkeypatch.setattr(pi, "_run_pi_streaming", launch)
    monkeypatch.setattr(pi.subprocess, "run", launch)
    for task_id, facts in facts_by_task.items():
        pi._run_turn(Settings(), workspace_id="qa", task_id=task_id, message=ORIGINAL,
                     stage=facts["selected_stage"], selected_refs=facts["selected_refs"], playhead_ms=facts["playhead_ms"],
                     authorization=None, cookie=None, local_member_token=None, caller_user_id="owner", prompt="受控专项",
                     tool_names="read_current_task,preview_typed_edit", preview_kind="edit_preview", session_key="conversation:shared",
                     turn_id=str(payload.assistant_turn_id) if streaming else None, on_event=(lambda _: None) if streaming else None)
        cmd, kwargs = launches[-1]
        envelope = json.loads(kwargs["message"])
        model_input = envelope["message"]
        current = json.loads(model_input.split("本轮输入（JSON）：\n", 1)[1])
        assert current == {"task_binding": {key: facts[key] for key in (
            "task_id", "current_timeline_version_id", "timeline_sequence", "selected_refs", "selected_stage")},
            "trusted_input_identity": {}, "original_user_instruction": ORIGINAL}
        assert "历史工具结果及失败只属于其当时的任务和版本" in model_input
        assert envelope["taskContext"] == facts
        assert kwargs["env"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == ORIGINAL
        assert kwargs["env"]["AI_VIDEO_DIARY_AGENT_TURN_ID"] == (str(payload.assistant_turn_id) if streaming else "")
        assert "preview_typed_edit" in json.loads(kwargs.get("message", kwargs.get("input")))["tools"]
        assert "preview_edit" not in json.loads(kwargs.get("message", kwargs.get("input")))["tools"]
        assert conversations.get_turn(str(payload.assistant_turn_id))["user_message"] == ORIGINAL
    assert len(launches) == 2
    assert json.loads(launches[0][1].get("message",launches[0][1].get("input")))["sessionId"] == json.loads(launches[1][1].get("message",launches[1][1].get("input")))["sessionId"]


def test_render_configuration_failure_after_read_does_not_claim_unreadable_task():
    output = json.dumps({"type":"tool_execution_end", "toolName":"start_creation_execution", "isError":True,
                         "result":{"content":[{"type":"text","text":"未标定 DEVICE_CPU_RENDER_BYTES_PER_PIXEL"}]}})
    with pytest.raises(pi.AssistantUnavailable) as caught:
        project_event_fixture(output)
    text = str(caught.value)
    assert "渲染尚未完成配置" in text and "当前修改已保留" in text
    assert "拿不到任务数据" not in text and "DEVICE_CPU" not in text and "设置页" not in text


def test_original_fit_sentence_reaches_true_crop_proposal_without_changing_task(legal_crop_state, crop_previews):
    state = legal_crop_state
    main._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    clip = before["montage"]["clips"][0]
    response = state.client.post(base + "/edits/preview", json={"raw_command": ORIGINAL,
        "context_stage":"montage", "selected_object_refs":["clip:" + clip["id"]]})
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    patch = proposal["operation"]["payload"]
    assert proposal["operation"]["operation_type"] == "ChangeCrop" and patch["clip_id"] == clip["id"]
    assert patch["fit_mode"] == "fit" and patch["crop_x"] == clip["crop_x"] and patch["crop_y"] == clip["crop_y"]
    assert state.client.get(base).json()["task"]["current_timeline_version_id"] == before["task"]["current_timeline_version_id"]
    rejected = state.client.post(base + "/edits/preview", json={"raw_command":ORIGINAL.replace("第1","第2"),
        "context_stage":"montage", "selected_object_refs":["clip:" + clip["id"]]})
    assert rejected.status_code == 422 and "序号" in rejected.text


def test_render_configuration_tool_record_keeps_only_actual_diagnostic_names():
    result = {"content":[{"type":"text","text":"未标定 DEVICE_CPU_RENDER_BYTES_PER_PIXEL; /private/video.mp4 secret-token"}]}
    summary = pi._safe_tool_result_summary("start_creation_execution", result, True)
    assert "DEVICE_CPU_RENDER_BYTES_PER_PIXEL" in summary and "未开始制作" in summary
    assert "/private/" not in summary and "secret-token" not in summary


def test_actual_node_preflight_can_be_stopped_before_model_launch(tmp_path, loopback_task):
    state = loopback_task
    state["block"] = True
    turn_id = str(uuid4())
    env = {**os.environ, "AI_VIDEO_DIARY_AGENT_TASK_ID":"task-real", "AI_VIDEO_DIARY_AGENT_STAGE":"montage",
           "AI_VIDEO_DIARY_AGENT_TURN_ID":turn_id, "AI_VIDEO_DIARY_AGENT_API_BASE":f"http://127.0.0.1:{state['port']}",
           "AI_VIDEO_DIARY_AGENT_TASK_READ_READY":"", "AI_VIDEO_DIARY_AGENT_SELECTED_REFS":"[]"}
    failures = []
    pi.register_turn_control(turn_id)
    def read():
        try:
            pi._read_task_before_pi(Path(__file__).parents[1]/"agent_tools"/"diary_tools.ts", env, tmp_path)
        except BaseException as exc:
            failures.append(exc)
    worker = threading.Thread(target=read)
    worker.start()
    try:
        assert state["entered"].wait(timeout=3)
        assert pi.request_stop_turn(turn_id)
        worker.join(timeout=3)
        assert not worker.is_alive() and len(failures) == 1 and isinstance(failures[0], pi.AssistantStopped)
    finally:
        state["release"].set()
        pi.forget_turn_control(turn_id)
        worker.join(timeout=3)


def test_server_rejects_another_real_member(bound_turn):
    _req, task, payload, _, _ = bound_turn
    _member, token = main.local_members_service.join(main.local_members_service.create_invite_code(), "其他成员")
    req = Request({"type":"http", "headers":[(b"x-local-member-token", token.encode())]})
    with pytest.raises(HTTPException) as caught:
        main._bind_assistant_edit_instruction(req, task, payload)
    assert caught.value.status_code == 404
