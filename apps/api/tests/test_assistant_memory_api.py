"""Member-authorized persistence, provenance and concurrent preference edits."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService


@pytest.fixture
def memory_client(tmp_path, monkeypatch):
    database = tmp_path / "memory.sqlite3"
    members = LocalMemberService(database)
    alice, alice_token = members.join(members.create_invite_code(), "Alice")
    bob, bob_token = members.join(members.create_invite_code(), "Bob")
    conversations = ConversationRepository(database)
    monkeypatch.setattr(main, "settings", SimpleNamespace(database_path=database))
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "current_workspace_id", lambda _request: "memory-workspace")
    app = FastAPI()
    app.add_api_route("/api/assistant/memory", main.read_assistant_memory, methods=["GET"])
    app.add_api_route("/api/assistant/memory", main.update_assistant_memory, methods=["PATCH"])
    app.add_api_route("/api/assistant/memory/user.md", main.export_assistant_memory, methods=["GET"])
    app.add_api_route("/api/creator-profile", main.get_creator_profile_endpoint, methods=["GET"])
    app.add_api_route("/api/creator-profile", main.update_creator_profile_endpoint, methods=["PATCH"])
    app.add_api_route("/api/creator-profile/learn", main.learn_creator_profile_endpoint, methods=["POST"])
    client = TestClient(app)

    def turn(message, member=alice):
        conversation = conversations.create("memory-workspace", member.member_id, "home", "home", "记忆验收")
        conversations.save_draft(conversation["id"], "memory-workspace", member.member_id, message, 0)
        row, created = conversations.create_turn_idempotent(
            conversation["id"], "memory-workspace", member.member_id, message, str(uuid4()), 1,
        )
        assert created
        return row["id"]

    return SimpleNamespace(client=client, alice={"X-Local-Member-Token": alice_token},
                           bob={"X-Local-Member-Token": bob_token}, turn=turn,
                           conversations=conversations, database=database)


def write(memory_client, instruction, *, action="remember", value="短句", quote=None, revision=None, turn_id=None):
    c = memory_client
    current = c.client.get("/api/assistant/memory", headers=c.alice).json()
    payload = {"action": action, "key": "表达风格", "expected_revision": current["revision"] if revision is None else revision,
               "assistant_turn_id": turn_id or c.turn(instruction), "source_quote": quote or instruction}
    if value is not None:
        payload["value"] = value
    return c.client.patch("/api/assistant/memory", headers=c.alice, json=payload), payload


def test_explicit_preference_survives_new_conversation_and_repository_reopen_then_forget(memory_client, monkeypatch):
    c = memory_client
    saved, _ = write(c, "请记住：表达风格使用短句。")
    assert saved.status_code == 200
    assert saved.json()["style_preferences"] == {"表达风格": "短句"}
    c.turn("用我的习惯写一段说明")
    monkeypatch.setattr(main, "conversation_repo", ConversationRepository(c.database))
    assert c.client.get("/api/assistant/memory", headers=c.alice).json()["style_preferences"] == {"表达风格": "短句"}
    assert c.client.get("/api/assistant/memory", headers=c.bob).json()["style_preferences"] == {}
    forgotten, _ = write(c, "忘记表达风格这个偏好", action="forget", value=None)
    assert forgotten.status_code == 200
    assert c.client.get("/api/assistant/memory", headers=c.alice).json()["style_preferences"] == {}


@pytest.mark.parametrize("instruction,quote", [
    ("这条视频用短句", None),
    ("你猜我喜欢什么风格", "请记住短句"),
    ("不要记住，这条视频临时用短句", None),
])
def test_temporary_inferred_or_fabricated_instruction_cannot_write_memory(memory_client, instruction, quote):
    c = memory_client
    response, _ = write(c, instruction, quote=quote)
    assert response.status_code == 409
    assert c.client.get("/api/assistant/memory", headers=c.alice).json()["style_preferences"] == {}


def test_identity_and_running_turn_are_rechecked_and_replay_is_idempotent(memory_client):
    c = memory_client
    response, payload = write(c, "记住我喜欢短句")
    assert response.status_code == 200
    replay = c.client.patch("/api/assistant/memory", headers=c.alice, json=payload)
    assert replay.json() == response.json()
    assert c.client.patch("/api/assistant/memory", headers=c.bob, json=payload).status_code == 404
    c.conversations.finish_turn(payload["assistant_turn_id"], "completed", "已保存")
    assert c.client.patch("/api/assistant/memory", headers=c.alice, json=payload).status_code == 409
    assert c.client.get("/api/assistant/memory").status_code == 403
    assert c.client.get("/api/assistant/memory", headers={"X-Local-Member-Token": "invalid"}).status_code == 401


def test_manual_edit_conflict_does_not_overwrite_current_preferences(memory_client):
    c = memory_client
    saved, _ = write(c, "记住我喜欢短句")
    revision = saved.json()["revision"]
    body = {"expected_revision": revision, "style_preferences": {"表达风格": "自然"}}
    assert c.client.patch("/api/creator-profile", headers=c.alice, json=body).status_code == 200
    assert c.client.patch("/api/creator-profile", headers=c.alice, json={**body, "style_preferences": {}}).status_code == 409
    assert c.client.get("/api/assistant/memory", headers=c.alice).json()["style_preferences"] == {"表达风格": "自然"}
    assert c.client.post("/api/creator-profile/learn", headers=c.alice).status_code == 410


def test_user_md_is_current_member_projection_of_persisted_memory(memory_client):
    c = memory_client
    assert write(c, "记住我喜欢短句")[0].status_code == 200
    export = c.client.get("/api/assistant/memory/user.md", headers=c.alice)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/markdown")
    assert 'filename="user.md"' in export.headers["content-disposition"]
    assert "表达风格" in export.text and "短句" in export.text
    assert "revision" not in export.text and "cp_" not in export.text
    assert "短句" not in c.client.get("/api/assistant/memory/user.md", headers=c.bob).text
    assert c.client.get("/api/assistant/memory/user.md").status_code == 403
    assert write(c, "忘记表达风格", action="forget", value=None)[0].status_code == 200
    assert "短句" not in c.client.get("/api/assistant/memory/user.md", headers=c.alice).text
