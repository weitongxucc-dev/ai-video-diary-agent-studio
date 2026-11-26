from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from test_creation_request_api import client_state as client_state  # noqa: F401


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


def test_draft_write_identity_belongs_to_the_current_owned_version(client_state, monkeypatch):
    c = client_state
    adapter = main._conversation_http_adapter()
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", adapter.update, methods=["PATCH"])
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", adapter.get_conversation, methods=["GET"])
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home")
    url = f"/api/assistant/conversations/{chat['id']}"
    draft = {"draft_text": "保持原片比例", "expected_draft_version": 0, "draft_write_id": str(uuid4())}
    saved = c.client.patch(url, headers=c.alice, json=draft)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_write_id"] == draft["draft_write_id"]
    restarted = ConversationRepository(c.conversations.database_path)
    monkeypatch.setattr(main, "conversation_repo", restarted)
    assert c.client.get(url, headers=c.alice).json()["draft_write_id"] == draft["draft_write_id"]
    assert c.client.get(url, headers=c.bob).status_code == 404
    assert c.client.patch(url, headers=c.bob, json=draft).status_code == 404
    assert c.client.patch(url, headers=c.alice, json=draft).status_code == 409
    assert c.client.get(url, headers=c.alice).json()["draft_version"] == 1

    legacy = c.client.patch(url, headers=c.alice, json={
        "draft_text": draft["draft_text"], "expected_draft_version": 1,
    }).json()
    assert (legacy["draft_version"], legacy["draft_write_id"]) == (2, None)
    next_write = str(uuid4())
    saved = c.client.patch(url, headers=c.alice, json={
        **draft, "expected_draft_version": 2, "draft_write_id": next_write,
    }).json()
    assert (saved["draft_version"], saved["draft_write_id"]) == (3, next_write)
    restarted.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        draft["draft_text"], str(uuid4()), 3, {"kind": "home"}, expected_reference_version=0)
    consumed = c.client.get(url, headers=c.alice).json()
    assert (consumed["draft_text"], consumed["draft_version"], consumed["draft_write_id"]) == ("", 4, None)


@pytest.mark.parametrize("original_arrives_first", [True, False])
def test_latest_draft_can_follow_only_the_exact_in_flight_write(client_state, original_arrives_first):
    c = client_state
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}",
        main._conversation_http_adapter().update, methods=["PATCH"])
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home")
    url = f"/api/assistant/conversations/{chat['id']}"
    first = {"draft_text": "较早的在途编辑", "expected_draft_version": 0, "draft_write_id": str(uuid4())}
    last = {"draft_text": "关闭前最后一笔", "expected_draft_version": 0, "draft_write_id": str(uuid4()),
        "after_draft_write_id": first["draft_write_id"]}
    if original_arrives_first:
        assert c.client.patch(url, headers=c.alice, json=first).status_code == 200
        unrelated = {**last, "after_draft_write_id": str(uuid4())}
        assert c.client.patch(url, headers=c.alice, json=unrelated).status_code == 409
    assert c.client.patch(url, headers=c.bob, json=last).status_code == 404
    response = c.client.patch(url, headers=c.alice, json=last)
    assert response.status_code == 200, response.text
    version = 2 if original_arrives_first else 1
    assert (response.json()["draft_version"], response.json()["draft_write_id"]) == (version, last["draft_write_id"])
    if not original_arrives_first:
        assert c.client.patch(url, headers=c.alice, json=first).status_code == 409
    c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        last["draft_text"], str(uuid4()), version, {"kind": "home"}, expected_reference_version=0)
    consumed = {**first, "expected_draft_version": version, "after_draft_write_id": last["draft_write_id"]}
    assert c.client.patch(url, headers=c.alice, json=consumed).status_code == 409
