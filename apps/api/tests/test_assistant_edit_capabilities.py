"""Current-turn capabilities across one conversation; no service or model calls."""

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
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join([
            json.dumps({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "stop",
                "content": [{"type": "text", "text": "已读取本轮对象。"}],
            }}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    monkeypatch.setattr(pi, "_run_pi_streaming", capture)
    return Settings(model_root_path=str(tmp_path)), calls


def envelope(text):
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

    sessions = [call["cmd"][call["cmd"].index("--session-id") + 1] for call in calls]
    assert len(set(sessions)) == 1
    contexts = []
    for call in calls:
        cmd = call["cmd"]
        current = envelope(call["message"])
        assert current == envelope(cmd[cmd.index("--system-prompt") + 1])
        assert current["tool_names"] == cmd[cmd.index("--tools") + 1].split(",")
        assert "历史真实提案、修改和作业保留其当时的事实" in call["message"]
        assert "历史工具清单、失败及助手解释不能定义本轮工具可用性" in call["message"]
        contexts.append(current)
    assert contexts[0]["scope_kind"] == "material" and contexts[0]["task_id"] is None
    assert "preview_edit" not in contexts[0]["tool_names"]
    assert contexts[1]["scope_kind"] == "task" and contexts[1]["task_id"] == "task-current"
    assert {"preview_edit", "read_clip_candidates", "read_selected_material", "suggest_selected_material_tags", "update_selected_material_tags"} <= set(contexts[1]["tool_names"])
    assert contexts[1]["selected_object_refs"] == ["clip:clip-current"]
    assert contexts[1]["material_selection"] == TOOL_SELECTION
    assert contexts[1]["task_version"]["timeline_sequence"] == 3
    assert "不替代当前任务" in contexts[1]["selection_meaning"]
    assert contexts[2]["material_selection"] is None
    assert "read_selected_material" not in contexts[2]["tool_names"]
    assert "read_clip_candidates" not in contexts[2]["tool_names"]
    assert json.loads(calls[1]["env"]["AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION"]) == TOOL_SELECTION
    assert calls[1]["env"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == original
    task_input = json.loads(calls[1]["message"].split("本轮输入（JSON）：\n", 1)[1])
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


@pytest.mark.parametrize("stage,expected,excluded", [
    ("topic", "generate_creation_task_topics", "preview_script_draft"),
    ("script", "preview_script_draft", "generate_creation_task_topics"),
])
def test_topic_and_script_keep_current_stage_capability_gates(captured_runtime, stage, expected, excluded):
    settings, calls = captured_runtime
    pi.run_task_turn(settings, workspace_id="workspace-current", task_id="task-current",
                     message="检查当前阶段", stage=stage, selected_refs=[], playhead_ms=None, turn_id=f"turn-{stage}")
    tools = envelope(calls[0]["message"])["tool_names"]
    assert expected in tools and excluded not in tools
    assert "preview_edit" not in tools
    assert {"read_clip_candidates", "read_task_edit_capabilities", "preview_typed_edit", "read_task_object"} <= set(tools)


@pytest.mark.parametrize("scope,tool_scope", [("asset", "material"), ("segment", "task_edit")])
def test_current_cli_allowlist_has_registered_business_implementations(scope, tool_scope):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to register the shipped TypeScript tools")
    extension = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
    selection = {"scope": "asset", "assetId": "asset-current"} if scope == "asset" else TOOL_SELECTION
    script = "const {default:register}=await import(process.argv[1]);const names=[];await register({registerTool:tool=>names.push(tool.name)});console.log(JSON.stringify(names));"
    from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS
    capability = {"schema": CreationTaskExactEditRequest.model_json_schema(), "supported_operations": list(EXACT_EDIT_FIELDS)}
    modules = sorted((Path.home() / ".nvm/versions/node").glob("*/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js"))
    if not modules:
        pytest.skip("Installed Pi is required to declare the codemode tool")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script, extension.as_uri()],
        text=True, capture_output=True, check=True, timeout=10,
        env={**os.environ, "AI_VIDEO_DIARY_AGENT_STAGE": "material" if tool_scope == "material" else "montage",
             "AI_VIDEO_DIARY_AGENT_TASK_ID": "" if tool_scope == "material" else "task-current",
             "AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION": json.dumps(selection),
             "AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES": json.dumps(capability),
             "AI_VIDEO_DIARY_AGENT_PI_MODULE": str(modules[-1])},
    )
    assert set(pi.ASSISTANT_BUSINESS_TOOLS[tool_scope]) <= set(json.loads(completed.stdout))


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
