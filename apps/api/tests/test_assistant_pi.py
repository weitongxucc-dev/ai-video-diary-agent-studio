import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.assistant_pi import AssistantUnavailable, _last_answer_and_preview, run_plan_turn, run_task_turn
from app.config import Settings


def test_pi_events_return_final_answer_and_preview():
    preview = {"proposal": {"id": "proposal-1"}}
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"type": "tool_execution_end", "toolName": "preview_edit", "isError": False,
         "result": {"details": {"kind": "edit_preview", "preview": preview}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "请检查预览后确认。"}]}},
        {"type": "agent_settled"},
    ])
    assert _last_answer_and_preview(output) == ("请检查预览后确认。", preview)


def test_pi_events_reject_incomplete_turn():
    with pytest.raises(AssistantUnavailable, match="完整回复"):
        _last_answer_and_preview('{"type":"agent_settled"}')


def test_pi_events_keep_plan_preview_separate_from_edit_preview():
    output = "\n".join(json.dumps(item) for item in [
        {"type": "tool_execution_end", "toolName": "preview_creation_plan", "isError": False,
         "result": {"details": {"kind": "plan_preview", "preview": {"name": "新任务"}}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "请检查新任务预检。"}]}},
        {"type": "agent_settled"},
    ])
    assert _last_answer_and_preview(output, "plan_preview") == ("请检查新任务预检。", {"name": "新任务"})
    assert _last_answer_and_preview(output, "edit_preview") == ("请检查新任务预检。", None)


def test_script_turn_uses_task_session_and_only_script_draft_tool(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = tmp_path / "pi"
    fake_pi.touch()
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    commands = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join([
            json.dumps({"type": "tool_execution_end", "toolName": "preview_script_draft",
                        "isError": False, "result": {"details": {"kind": "script_draft",
                        "preview": {"script_id": "draft-one"}}}}),
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                        "stopReason": "stop", "content": [{"type": "text", "text": "请检查文案。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    monkeypatch.setattr("app.assistant_pi.subprocess.run", fake_run)
    for task_id in ("task-one", "task-one", "task-two"):
        reply, preview = run_task_turn(settings, workspace_id="workspace-one", task_id=task_id,
                                       message="把开头改短", stage="script", selected_refs=[], playhead_ms=None)
        assert (reply, preview) == ("请检查文案。", {"script_id": "draft-one"})
    assert commands[0][commands[0].index("--session-id") + 1] == commands[1][commands[1].index("--session-id") + 1]
    assert commands[0][commands[0].index("--session-id") + 1] != commands[2][commands[2].index("--session-id") + 1]
    assert commands[0][commands[0].index("--tools") + 1] == "read_current_task,find_knowledge,preview_script_draft"
    _, edit_preview = run_task_turn(settings, workspace_id="workspace-one", task_id="task-one",
                                    message="检查镜头", stage="montage", selected_refs=[], playhead_ms=None)
    assert edit_preview is None
    assert commands[3][commands[3].index("--session-id") + 1] != commands[0][commands[0].index("--session-id") + 1]
    assert commands[3][commands[3].index("--tools") + 1] == "read_current_task,find_knowledge,find_materials,preview_edit"


def test_pi_turn_uses_isolated_disk_and_keeps_secret_out_of_arguments(monkeypatch, tmp_path):
    model_root = tmp_path / "models"
    model_root.mkdir()
    settings = Settings(model_root_path=str(model_root), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    fake_pi = tmp_path / "pi"
    fake_pi.touch()
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, kwargs=kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                "stopReason": "stop", "content": [{"type": "text", "text": "收到。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    monkeypatch.setattr("app.assistant_pi.subprocess.run", fake_run)
    answer, preview = run_task_turn(settings, workspace_id="workspace-1", task_id="task-1",
                                    message="请说明当前任务", stage="montage",
                                    selected_refs=["clip:clip-1"], playhead_ms=1200)
    assert (answer, preview) == ("收到。", None)
    assert captured["kwargs"]["input"] == "请说明当前任务"
    assert captured["kwargs"]["env"]["PATH"].split(__import__("os").pathsep)[0] == str(fake_pi.parent)
    assert captured["kwargs"]["env"]["AI_VIDEO_DIARY_LLM_KEY"] == "private-test-key"
    assert "private-test-key" not in " ".join(captured["cmd"])
    assert {"--print", "--no-builtin-tools", "--no-context-files", "--no-extensions"} <= set(captured["cmd"])
    runtime_root = Path(captured["kwargs"]["cwd"])
    assert runtime_root.parent.parent == tmp_path / "agent"
    assert "private-test-key" not in (runtime_root / "models.json").read_text()


def test_pi_turn_requires_external_text_model(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = tmp_path / "pi"
    fake_pi.touch()
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_provider="minimax")
    with pytest.raises(AssistantUnavailable, match="API Key"):
        run_task_turn(settings, workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)


def test_assistant_route_scopes_task_before_invoking_pi(monkeypatch):
    seen = []
    def scoped_task(workspace_id, task_id):
        if workspace_id != "workspace-a" or task_id != "task-a":
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="task not found")
        return object()
    monkeypatch.setattr(main_module, "current_workspace_id",
                        lambda request: request.headers.get("x-test-workspace", "workspace-a"))
    monkeypatch.setattr(main_module, "_creation_task_or_404", scoped_task)
    monkeypatch.setattr(main_module, "_assistant_turn_scope",
                        lambda workspace_id, task, payload: (payload.context_stage, payload.selected_object_refs))
    monkeypatch.setattr(main_module, "production_repo", SimpleNamespace(append_task_event=lambda event: event))
    monkeypatch.setattr(main_module, "run_task_turn", lambda settings, **kwargs: (seen.append(kwargs) or ("收到。", None)))
    client = TestClient(main_module.app)
    payload = {"message": "请检查镜头", "context_stage": "montage",
               "selected_object_refs": ["clip:clip-1"], "playhead_ms": 1200}
    denied = client.post("/api/creation-tasks/task-a/assistant/turn", json=payload,
                         headers={"x-test-workspace": "workspace-b"})
    assert denied.status_code == 404
    assert seen == []
    accepted = client.post("/api/creation-tasks/task-a/assistant/turn", json=payload)
    assert accepted.status_code == 200
    assert accepted.json()["reply"] == "收到。"
    assert seen[0]["workspace_id"] == "workspace-a"
    assert seen[0]["selected_refs"] == ["clip:clip-1"]


def test_new_plan_turn_uses_workspace_and_conversation_boundary(monkeypatch):
    seen = []
    monkeypatch.setattr(main_module, "current_workspace_id",
                        lambda request: request.headers.get("x-test-workspace", "workspace-a"))
    monkeypatch.setattr(main_module, "run_plan_turn",
                        lambda settings, **kwargs: (seen.append(kwargs) or ("请说明视频目标。", None)))
    client = TestClient(main_module.app)
    conversation_id = "11111111-1111-4111-8111-111111111111"
    rejected = client.post("/api/assistant/plan/turn", json={"message": "做视频", "conversation_id": "bad"})
    assert rejected.status_code == 422
    assert seen == []
    response = client.post("/api/assistant/plan/turn",
                           json={"message": "做视频", "conversation_id": conversation_id},
                           headers={"x-test-workspace": "workspace-b"})
    assert response.status_code == 200
    assert response.json()["plan_preview"] is None
    assert seen[0]["workspace_id"] == "workspace-b"
    assert seen[0]["conversation_id"] == conversation_id


def test_new_plan_conversations_do_not_share_pi_session(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    fake_pi = tmp_path / "pi"
    fake_pi.touch()
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    settings = Settings(model_root_path=str(tmp_path / "models"), llm_api_key="private-test-key",
                        llm_api_key_provider="minimax")
    sessions = []

    def fake_run(cmd, **_kwargs):
        sessions.append(cmd[cmd.index("--session-id") + 1])
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join([
            json.dumps({"type": "message_end", "message": {"role": "assistant",
                "stopReason": "stop", "content": [{"type": "text", "text": "请补充目标。"}]}}),
            '{"type":"agent_settled"}',
        ]), stderr="")

    monkeypatch.setattr("app.assistant_pi.subprocess.run", fake_run)
    for conversation_id in ("one", "one", "two"):
        run_plan_turn(settings, workspace_id="workspace-a", conversation_id=conversation_id,
                      message="你好", authorization=None, cookie=None)
    assert sessions[0] == sessions[1]
    assert sessions[0] != sessions[2]
