from uuid import uuid4

import app.main as main
from app.conversation_repository import ConversationRepository
from test_creation_request_api import client_state  # noqa: F401


def request_url(chat, request_id):
    return f"/api/assistant/conversations/{chat['id']}/requests/{request_id}"


def add_locator(c):
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}/requests/{client_request_id}",
        main._conversation_http_adapter().get_request, methods=["GET"])


def test_request_locator_reads_original_identity_and_current_real_turn_status(client_state, monkeypatch):
    c = client_state
    add_locator(c)
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "目标")
    draft = c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "只讨论主题", 0)
    context = {"kind": "home", "selected_reference": None, "expected_reference_version": 0,
        "target_snapshot": {"frozen": "original"}}
    request_id = str(uuid4())
    turn, created = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        "只讨论主题", request_id, draft["draft_version"], context, expected_reference_version=0)
    assert created
    url = request_url(chat, request_id)
    receipt = c.client.get(url, headers=c.alice)
    assert receipt.status_code == 200, receipt.text
    original = receipt.json()
    assert original == {"client_request_id": request_id, "conversation_id": chat["id"],
        "user_message": "只讨论主题", "request_context": context, "expected_draft_version": 1,
        "expected_reference_version": 0, "outcome": "consumed", "turn_id": turn["id"], "turn_status": "running"}
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "下一轮草稿", 2)
    c.conversations.finish_turn(turn["id"], "completed", "已讨论")
    restarted = ConversationRepository(c.conversations.database_path)
    monkeypatch.setattr(main, "conversation_repo", restarted)
    assert c.client.get(url, headers=c.alice).json() == {**original, "turn_status": "completed"}
    assert c.client.get(url, headers=c.bob).status_code == 404
    assert c.client.get(request_url(chat, uuid4()), headers=c.alice).status_code == 404
    other = c.conversations.create("request-test", c.member.member_id, "home", "home", "其他对话")
    assert c.client.get(request_url(other, request_id), headers=c.alice).status_code == 404
    assert restarted.get_request_receipt(request_id, chat["id"], "foreign-workspace", c.member.member_id) is None
    assert c.repository.task_records.list_creation_tasks("request-test") == []


def test_recovered_request_is_reported_without_guessing_a_turn(client_state):
    c = client_state
    add_locator(c)
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "恢复")
    request_id = str(uuid4())
    restored, outcome = c.conversations.restore_unpersisted_turn_draft(chat["id"], "request-test", c.member.member_id,
        request_id, "网络断开前的输入", 0)
    assert restored and outcome == "restored"
    response = c.client.get(request_url(chat, request_id), headers=c.alice)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "recovered"
    assert response.json()["turn_id"] is None and response.json()["turn_status"] is None
