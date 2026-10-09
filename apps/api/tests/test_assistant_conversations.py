from fastapi.testclient import TestClient
from types import SimpleNamespace

import app.main as main_module
from app.assistant_pi import AssistantUnavailable
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService


def test_conversation_resume_selects_persistent_current_for_trusted_member(tmp_path, monkeypatch):
    workspace = {"id": "workspace-one"}
    repo = ConversationRepository(tmp_path / "assistant.sqlite3")
    members = LocalMemberService(tmp_path / "members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    other, other_token = members.join(members.create_invite_code(), "成员乙")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace["id"])
    client = TestClient(main_module.app)

    scope = {"scope_kind": "home", "scope_id": "home"}
    assert client.post("/api/assistant/conversations/current", json=scope).status_code == 403
    headers = {"X-Local-Member-Token": token}
    first = client.post("/api/assistant/conversations/current", json=scope, headers=headers)
    assert first.status_code == 200
    old_id = first.json()["id"]
    second = client.post("/api/assistant/conversations", json={**scope, "title": "新对话"}, headers=headers)
    assert second.status_code == 200
    new_id = second.json()["id"]
    client.patch(f"/api/assistant/conversations/{old_id}", json={"draft_text": "迟到的草稿保存"}, headers=headers)
    assert client.post("/api/assistant/conversations/current", json=scope, headers=headers).json()["id"] == new_id

    assert client.post(f"/api/assistant/conversations/{old_id}/resume", headers=headers).json()["id"] == old_id
    repo_after_restart = ConversationRepository(tmp_path / "assistant.sqlite3")
    assert repo_after_restart.current_or_create("workspace-one", members.resolve_member(token).member_id,
                                                "home", "home")["id"] == old_id
    assert client.get(f"/api/assistant/conversations/{old_id}", headers={"X-Local-Member-Token": other_token}).status_code == 404

    workspace["id"] = "workspace-two"
    assert client.get(f"/api/assistant/conversations/{old_id}", headers=headers).status_code == 404


def test_unavailable_assistant_turn_retains_partial_reply_and_persisted_failure(tmp_path, monkeypatch):
    repo = ConversationRepository(tmp_path / "failed-assistant.sqlite3")
    members = LocalMemberService(tmp_path / "failed-members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace-one")
    monkeypatch.setattr(main_module, "_workspace_settings_for", lambda _workspace_id: SimpleNamespace(
        effective_llm_provider="test-provider", effective_llm_model="test-model",
        effective_llm_api_key=None,
    ))
    conversation = repo.current_or_create("workspace-one", members.resolve_member(token).member_id, "home", "home")

    def unavailable(_settings, *, turn_id, **_kwargs):
        repo.append_event(turn_id, {"type": "text_delta", "delta": "已读到部分上下文。"})
        raise AssistantUnavailable("文字模型当前不可用；请检查设置。", cause="runtime_missing")

    monkeypatch.setattr(main_module, "run_plan_turn", unavailable)
    client = TestClient(main_module.app)
    response = client.post("/api/assistant/plan/turn", headers={"X-Local-Member-Token": token}, json={
        "conversation_id": conversation["id"], "message": "先检查当前方案",
    })
    assert response.status_code == 503
    saved = ConversationRepository(tmp_path / "failed-assistant.sqlite3").list_turns(conversation["id"])[0]
    assert saved["status"] == "failed"
    assert saved["assistant_text"] == "已读到部分上下文。"
    assert saved["result"] == {"kind": "failure", "message": "文字模型当前不可用；请检查设置。"}
