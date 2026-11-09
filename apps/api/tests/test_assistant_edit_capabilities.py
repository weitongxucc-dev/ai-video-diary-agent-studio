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
    assert contexts[1]["scope_kind"] == "task" and contexts[1]["task_id"] == "task-current"
    assert "preview_edit" not in contexts[1]["tool_names"]
    assert {"preview_typed_edit", "read_clip_candidates", "read_selected_material", "suggest_selected_material_tags", "update_selected_material_tags"} <= set(contexts[1]["tool_names"])
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
    assert {"read_clip_candidates", "read_task_edit_capabilities", "preview_typed_edit", "read_task_object"} <= set(tools)
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
    modules = sorted((Path.home() / ".nvm/versions/node").glob("*/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js"))
    if not modules:
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
             "AI_VIDEO_DIARY_AGENT_PI_MODULE": str(modules[-1])},
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
