"""Current-turn capabilities across one conversation; no service or model calls."""

from assistant_sdk_fixtures import sdk_completed

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app.assistant_pi as pi
from app.config import Settings


SELECTION = {
    "scope": "segment", "asset_id": "asset-current", "segment_id": "segment-current",
    "expected_updated_at": "2026-10-11T01:00:00+00:00",
    "expected_segment_updated_at": "2026-10-11T01:01:00+00:00",
}
TOOL_SELECTION = {
    "scope": "segment", "assetId": "asset-current", "segmentId": "segment-current",
    "expectedUpdatedAt": SELECTION["expected_updated_at"],
    "expectedSegmentUpdatedAt": SELECTION["expected_segment_updated_at"],
}


@pytest.fixture
def captured_runtime(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pi, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr("app.assistant.runtime_context.sdk_module", lambda _: Path("/synthetic/index.js"))
    monkeypatch.setattr(pi, "_pi_executable", lambda: sys.executable)
    monkeypatch.setattr(pi, "_configured_model", lambda _: ("https://synthetic.invalid", "fixture", ""))
    monkeypatch.setattr(pi, "_agent_root", lambda *_: tmp_path / "agent")
    monkeypatch.setattr(pi, "_record_service", lambda *_, **__: None)
    monkeypatch.setattr(pi, "_read_task_before_pi", lambda _extension, env, _cwd: {
        "task_id": env["AI_VIDEO_DIARY_AGENT_TASK_ID"],
        "selected_refs": json.loads(env["AI_VIDEO_DIARY_AGENT_SELECTED_REFS"]),
    })
    monkeypatch.setattr(pi, "_read_memory_before_pi", lambda *_: {
        "revision": 0, "common_expressions": [], "banned_words": [], "style_preferences": {},
    })

    def capture(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return sdk_completed(cmd, 0, stdout="\n".join([
            json.dumps({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "stop",
                "content": [{"type": "text", "text": "已读取本轮对象。"}],
            }}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    monkeypatch.setattr(pi, "_run_pi_streaming", capture)
    return Settings(model_root_path=str(tmp_path)), calls


def envelope(text):
    if text.lstrip().startswith("{"):
        text=json.loads(text)["systemPrompt"]
    return json.loads(text.split("本轮能力与对象（JSON）：\n", 1)[1].splitlines()[0])


def test_first_turn_pdf_uses_owned_draft_and_original_request(captured_runtime, monkeypatch, tmp_path):
    import base64
    import hashlib
    from uuid import uuid4

    from fastapi.testclient import TestClient

    import app.main as main
    from app.conversation_repository import ConversationRepository
    from app.local_members import LocalMemberService
    from app.source_repository import SourceRepository
    from tests.pdf_fixture import _text_pdf

    _, calls = captured_runtime
    database = tmp_path / "assistant.sqlite3"
    repo = ConversationRepository(database)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, token = members.join(members.create_invite_code(), "PDF QA")
    members.set_role(owner.member_id, "owner")
    _, foreign_token = members.join(members.create_invite_code(), "Other QA")
    monkeypatch.setattr(main, "conversation_repo", repo)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "source_repository", SourceRepository(tmp_path / "sources.sqlite3"))
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("X-Test-Workspace", "workspace-qa"))
    client = TestClient(main.app, headers={"X-Local-Member-Token": token})

    conversations = "/api/assistant/conversations"
    scope = {"scope_kind": "home", "scope_id": f"draft:{uuid4()}"}
    draft = client.post(f"{conversations}/current", json=scope).json()
    second = client.post(conversations, json=scope).json()
    path = f"{conversations}/{draft['id']}"
    other = f"{conversations}/{second['id']}"
    assert client.post(f"{path}/resume").status_code == 200
    message = "根据第2页准备型号简介。"
    saved = client.patch(path, json={"draft_text": message, "expected_draft_version": draft["draft_version"]}).json()
    pdf = _text_pdf(["ZX-401 comparison", "ZX-410 isolated proof"])
    attachment_id = hashlib.sha256(pdf).hexdigest()

    def upload(data, version, filename="QA-manual.pdf"):
        return client.post(f"{path}/turn-pdf", json={
            "filename": filename, "content_base64": base64.b64encode(data).decode(),
            "expected_reference_version": version,
        })

    assert upload(b"invalid", 0).status_code == 422
    secret = "synthetic-secret-qa-value"
    refused = upload(_text_pdf([f"token: {secret}"]), 0)
    assert refused.status_code == 422 and secret not in refused.text
    assert not (tmp_path / "originals").exists()
    attached = upload(pdf, 0)
    assert attached.status_code == 200, attached.text
    selection = attached.json()["selected_reference"]
    source, = selection["items"]
    assert source["attachment_id"] == attachment_id and source["page_count"] == 2
    assert upload(pdf, 0).status_code == 409
    assert client.get("/api/source-items").json()["items"] == []
    assert client.get("/api/knowledge-items").json()["items"] == []

    pdf_path = f"{path}/turn-pdf/{attachment_id}"
    assert TestClient(main.app).get(pdf_path).status_code == 403
    assert client.get(pdf_path, headers={"X-Local-Member-Token": foreign_token}).status_code == 404
    assert client.get(pdf_path, headers={"X-Test-Workspace": "workspace-other"}).status_code == 404
    assert client.get(f"{other}/turn-pdf/{attachment_id}").status_code == 404
    forged = client.patch(f"{other}/reference", json={
        "selected_reference": selection, "expected_reference_version": 0,
    })
    assert forged.status_code == 409

    library_source = client.post("/api/source-items/file", json={
        "filename": "Approved-notes.md",
        "content_base64": base64.b64encode(b"Approved library notes.").decode(),
    }).json()
    knowledge = client.post(f"/api/source-items/{library_source['id']}/save-to-knowledge", json={}).json()
    fixed = {"kind": "knowledge", "label": "Approved library notes",
             "knowledge_item_id": knowledge["id"], "version": knowledge["version"], "locator": "第1段"}
    both = client.patch(f"{path}/reference", json={
        "selected_reference": {"kind": "selection", "items": [fixed, source]},
        "expected_reference_version": attached.json()["reference_version"],
    })
    assert both.status_code == 200, both.text
    selection = both.json()["selected_reference"]
    reference_version = both.json()["reference_version"]

    monkeypatch.setattr(main, "conversation_repo", ConversationRepository(database))
    reopened = client.post(f"{conversations}/current", json=scope).json()
    assert reopened["draft_text"] == message and reopened["selected_reference"] == selection
    assert client.get(pdf_path).json()["pages"] == [
        {"locator": "第1页", "text": "ZX-401 comparison"},
        {"locator": "第2页", "text": "ZX-410 isolated proof"},
    ]
    request = {"conversation_id": draft["id"], "message": message,
               "client_request_id": str(uuid4()), "expected_draft_version": saved["draft_version"],
               "expected_reference_version": reference_version}
    original = next((tmp_path / "originals").rglob(attachment_id))
    original.write_bytes(b"!" * len(pdf))
    assert client.post("/api/assistant/plan/turn", json=request).status_code == 409
    assert not calls and client.get(path).json()["draft_text"] == message
    assert client.get(f"{path}/requests/{request['client_request_id']}").status_code == 404
    original.write_bytes(pdf)

    sent = client.post("/api/assistant/plan/turn", json=request)
    assert sent.status_code == 200, sent.text
    turn_id = sent.json()["turn_id"]
    call, = calls
    invocation = json.loads(call["message"])
    assert "ZX-410 isolated proof" in invocation["systemPrompt"] and "第2页" in invocation["systemPrompt"]
    assert "ZX-410 isolated proof" not in invocation["message"]
    assert "turn_pdf" not in call["env"]["AI_VIDEO_DIARY_AGENT_SELECTED_REFERENCES"]
    assert not any("ZX-410 isolated proof" in str(value) for value in call["env"].values())
    receipt = client.get(f"{path}/requests/{request['client_request_id']}").json()
    assert receipt["turn_id"] == turn_id and receipt["request_context"]["selected_reference"] == selection
    assert "ZX-410 isolated proof" not in json.dumps(receipt)
    history = client.get(path).json()
    assert history["draft_text"] == ""
    assert history["selected_reference"] == {"kind": "selection", "items": [fixed]}
    assert history["reference_version"] == reference_version + 1
    assert history["turns"][0]["turn_pdf"] == source
    assert client.get(pdf_path).status_code == 404
    assert client.get(pdf_path, params={"turn_id": turn_id}).json()["pages"][1]["locator"] == "第2页"
    assert client.post("/api/assistant/plan/turn", json=request).json()["turn_id"] == turn_id
    assert len(calls) == 1
    assert upload(pdf, history["reference_version"]).status_code == 409

    next_message = "另写一句开场。"
    next_draft = client.patch(path, json={"draft_text": next_message,
        "expected_draft_version": history["draft_version"]}).json()
    next_turn = client.post("/api/assistant/plan/turn", json={**request, "message": next_message,
        "client_request_id": str(uuid4()), "expected_draft_version": next_draft["draft_version"],
        "expected_reference_version": history["reference_version"]})
    assert next_turn.status_code == 200
    assert "ZX-410 isolated proof" not in json.loads(calls[1]["message"])["systemPrompt"]
    assert client.get(path).json()["turns"][1]["turn_pdf"] is None


def test_one_conversation_refreshes_material_task_capabilities_and_trusted_objects(captured_runtime):
    settings, calls = captured_runtime
    common = {"workspace_id": "workspace-current", "conversation_id": "conversation-current", "caller_user_id": "member-current"}
    pi.run_material_turn(settings, message="读取这条素材", selection=SELECTION, turn_id="turn-material", **common)
    original = "把镜头一换成你选的更好镜头；给对应素材加空镜头标签"
    pi.run_task_turn(settings, task_id="task-current", stage="montage", message=original,
                     selected_refs=["clip:clip-current"], playhead_ms=1500,
                     material_selection=SELECTION, task_version={"timeline_version_id": "version-current", "timeline_sequence": 3},
                     turn_id="turn-task", **common)
    pi.run_material_turn(settings, message="读取素材库", turn_id="turn-material-again", **common)

    sessions = [json.loads(call["message"])["sessionId"] for call in calls]
    assert len(set(sessions)) == 1
    contexts = []
    for call in calls:
        call["cmd"]
        current = envelope(call["message"])
        assert current["task_id"] == (call["env"]["AI_VIDEO_DIARY_AGENT_TASK_ID"] or None)
        assert current["tool_names"] == json.loads(call["message"])["tools"]
        assert "历史真实提案、修改和作业保留其当时的事实" in json.loads(call["message"])["systemPrompt"]
        assert "历史工具清单、失败及助手解释不能定义本轮工具可用性" in json.loads(call["message"])["systemPrompt"]
        contexts.append(current)
    assert contexts[0]["scope_kind"] == "home" and contexts[0]["task_id"] is None
    assert "preview_edit" not in contexts[0]["tool_names"]
    assert "read_material_evidence" not in contexts[0]["tool_names"]
    assert contexts[1]["scope_kind"] == "task" and contexts[1]["task_id"] == "task-current"
    assert "preview_edit" not in contexts[1]["tool_names"]
    assert {"preview_typed_edit", "read_clip_candidates", "read_material_evidence", "read_selected_material",
            "suggest_selected_material_tags", "update_selected_material_tags"} <= set(contexts[1]["tool_names"])
    step = pi._PiStreamProjector().project(json.dumps({
        "type": "tool_execution_start", "toolName": "read_material_evidence", "toolCallId": "call-material-evidence",
    }))
    assert step["label"] == "读取素材时间证据"
    assert "不自行重新生成或用同名图替换" in json.loads(calls[1]["message"])["systemPrompt"]
    prompt = json.loads(calls[1]["message"])["systemPrompt"]
    assert "不对这些对象JSON.parse；CLI stdout才是JSON文本" in prompt
    assert "工具ID可直接只读describe" in prompt
    assert contexts[1]["selected_object_refs"] == ["clip:clip-current"]
    assert contexts[1]["material_selection"] == TOOL_SELECTION
    assert contexts[1]["task_version"]["timeline_sequence"] == 3
    assert "不替代当前任务" in contexts[1]["selection_meaning"]
    assert contexts[2]["material_selection"] is None
    assert "read_selected_material" not in contexts[2]["tool_names"]
    assert "read_clip_candidates" not in contexts[2]["tool_names"]
    assert "read_material_evidence" not in contexts[2]["tool_names"]
    assert json.loads(calls[1]["env"]["AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION"]) == TOOL_SELECTION
    assert calls[1]["env"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == original
    task_input = json.loads(json.loads(calls[1]["message"])["message"].split("本轮输入（JSON）：\n", 1)[1])
    assert task_input["original_user_instruction"] == original


def test_task_without_material_snapshot_keeps_tools_guarded_and_does_not_guess(captured_runtime):
    settings, calls = captured_runtime
    pi.run_task_turn(settings, workspace_id="workspace-current", conversation_id="conversation-current",
                     task_id="task-current", stage="review", message="检查当前任务",
                     selected_refs=[], playhead_ms=None, turn_id="turn-no-selection")
    assert calls[0]["env"]["AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION"] == ""
    context = envelope(calls[0]["message"])
    assert context["material_selection"] is None and context["selected_object_refs"] == []
    assert "read_selected_material" in context["tool_names"]


@pytest.mark.parametrize("stage,expected,shared", [
    ("topic", "generate_creation_task_topics", "preview_script_draft"),
    ("script", "preview_script_draft", "generate_creation_task_topics"),
])
def test_topic_and_script_keep_stage_context_and_shared_capabilities(captured_runtime, stage, expected, shared):
    settings, calls = captured_runtime
    pi.run_task_turn(settings, workspace_id="workspace-current", task_id="task-current",
                     message="检查当前阶段", stage=stage, selected_refs=[], playhead_ms=None, turn_id=f"turn-{stage}")
    context = envelope(calls[0]["message"])
    tools = context["tool_names"]
    assert context["context_stage"] == stage and context["task_id"] == "task-current"
    assert expected in tools and shared in tools
    assert "preview_edit" not in tools
    assert {"read_clip_candidates", "read_material_evidence", "read_task_edit_capabilities",
            "preview_typed_edit", "read_task_object"} <= set(tools)
    prompt = json.loads(calls[0]["message"])["systemPrompt"]
    assert "parameters是API parameters参数对象" in prompt
    assert "inputs[name]按input_refs的name" in prompt and "Image.open(inputs[name])" in prompt
    assert "outputs[name]按requested_outputs的name映射宿主提供的可信输出文件路径字符串" in prompt
    assert "image.save(outputs['name.png'])" in prompt
    assert "不要将outputs[name]替换成bytes/BytesIO，不自行新增路径或输出名称" in prompt
    assert "找不到或同名歧义时说明具体缺口或询问" in json.loads(calls[0]["message"])["systemPrompt"]


@pytest.mark.parametrize("scope,tool_scope", [("asset", "material"), ("segment", "task_edit"), ("home", "home")])
def test_current_sdk_allowlist_has_registered_business_implementations(scope, tool_scope):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to register the shipped TypeScript tools")
    extension = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
    selection = {"scope": "asset", "assetId": "asset-current"} if scope == "asset" else TOOL_SELECTION if scope == "segment" else None
    script = "const {default:register}=await import(process.argv[1]);const names=[];await register({registerTool:tool=>names.push(tool.name)});console.log(JSON.stringify(names));"
    from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS, MediaScriptPreviewRequest, MediaScriptToolSaveRequest, MediaScriptToolUpdateRequest, MediaScriptToolRevokeRequest, MediaScriptToolPreviewRequest
    capability = {"schema": CreationTaskExactEditRequest.model_json_schema(), "supported_operations": list(EXACT_EDIT_FIELDS)}
    capability["direct_apply"]={"endpoint":"/api/creation-tasks/task-current/edits/exact", "operations":[{"operation_type":"UpdateSubtitleCue","parameter_fields":["cue_id","text"]}]}
    from app.assistant.runtime_checks import _pi_executable, sdk_module
    executable = _pi_executable()
    module = sdk_module(executable) if executable is not None else None
    if module is None:
        pytest.skip("Installed Pi is required to declare the codemode tool")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script, extension.as_uri()],
        text=True, capture_output=True, check=True, timeout=10,
        env={**os.environ, "AI_VIDEO_DIARY_AGENT_STAGE": "material" if tool_scope == "material" else "home" if tool_scope == "home" else "montage",
             "AI_VIDEO_DIARY_AGENT_TASK_ID": "" if tool_scope in {"material", "home"} else "task-current",
             "AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION": json.dumps(selection),
             "AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES": json.dumps(capability),
             "AI_VIDEO_DIARY_AGENT_MEDIA_SCRIPT_CAPABILITIES": json.dumps({"schema": MediaScriptPreviewRequest.model_json_schema(), "available": False}),
             "AI_VIDEO_DIARY_AGENT_SCRIPT_TOOL_CAPABILITIES": json.dumps({"schemas": {name:model.model_json_schema() for name,model in (
                 ("save",MediaScriptToolSaveRequest),("update",MediaScriptToolUpdateRequest),("revoke",MediaScriptToolRevokeRequest),("invoke",MediaScriptToolPreviewRequest))}}),
             "AI_VIDEO_DIARY_AGENT_PI_MODULE": str(module)},
    )
    registered = set(json.loads(completed.stdout))
    assert set(pi.ASSISTANT_BUSINESS_TOOLS[tool_scope]) <= registered
    if tool_scope == "home":
        assert "read_voice_profiles" in pi.ASSISTANT_BUSINESS_TOOLS[tool_scope]
        assert pi._PI_TOOL_LABELS["read_voice_profiles"] == "读取声音"
        assert "read_voice_profiles" in pi.PLAN_PROMPT and "selected_voice" in pi.PLAN_PROMPT
        assert "空列表不代表内建配音" in pi.PLAN_PROMPT
    assert "preview_edit" not in registered
    assert all("preview_edit" not in names for names in pi.ASSISTANT_BUSINESS_TOOLS.values())


@pytest.mark.parametrize("name,label", [
    ("read_clip_candidates", "读取替换候选"),
    ("suggest_selected_material_tags", "建议素材标签"),
])
def test_new_tools_have_visible_steps_without_exposing_internal_names(name, label):
    projector = pi._PiStreamProjector()
    assert projector.project(json.dumps({"type": "tool_execution_start", "toolName": name, "toolCallId": "call-current"}))["label"] == label
    end = projector.project(json.dumps({"type": "tool_execution_end", "toolName": name, "toolCallId": "call-current", "result": {}, "isError": False}))
    assert end["ok"] is True
    assert "尚未" in end["summary"] and name not in end["summary"]
