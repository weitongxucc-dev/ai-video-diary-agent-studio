"""Member boundary, bounded windows and live preflight revalidation over SQLite."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from test_creation_requests import entries


@pytest.fixture
def client_state(tmp_path, monkeypatch):
    path = tmp_path / "api.sqlite3"
    repo = ProductionRepository(path, tmp_path / "library")
    members = LocalMemberService(path)
    alice, token_a = members.join(members.create_invite_code(), "Alice")
    bob, token_b = members.join(members.create_invite_code(), "Bob")
    conversations = ConversationRepository(path)
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "current_workspace_id", lambda _request: "request-test")
    live_fingerprint = ["a" * 64]
    def preflight(_request, workspace_id, payload):
        return replace(entries(count=1)[0][0], goal=payload.goal, name=payload.name or payload.goal,
            duration_target_seconds=payload.duration_target_seconds, preflight_fingerprint=live_fingerprint[0])
    monkeypatch.setattr(main, "_creation_plan_preflight", preflight)
    app = FastAPI()
    for path, handler, methods in [
        ("/api/creation-requests", main.create_creation_request, ["POST"]),
        ("/api/creation-requests/{request_id}", main.get_creation_request, ["GET"]),
        ("/api/creation-requests/{request_id}", main.revise_creation_request, ["PATCH"]),
        ("/api/creation-requests/{request_id}/windows", main.preview_creation_request_window, ["POST"]),
        ("/api/creation-requests/{request_id}/windows/{window_id}/confirm", main.confirm_creation_request_window, ["POST"]),
        ("/api/creation-requests/{request_id}/windows/{window_id}/replan", main.replan_creation_request_window, ["POST"]),
        ("/api/creation-requests/{request_id}/items", main.list_creation_request_items, ["GET"]),
    ]:
        app.add_api_route(path, handler, methods=methods)
    return SimpleNamespace(client=TestClient(app), alice={"X-Local-Member-Token": token_a},
        bob={"X-Local-Member-Token": token_b}, member=alice, conversations=conversations,
        repository=repo, live_fingerprint=live_fingerprint)


def create(c, **changes):
    body = {"request_key": str(uuid4()), "goal": "制作10000条视频", "target_count": 10000,
        "output_relation": "different_topics", "duration_target_seconds": 120}
    body.update(changes)
    return c.client.post("/api/creation-requests", headers=c.alice, json=body)


def test_large_request_exposes_only_real_bounded_plans_and_tasks(client_state):
    c = client_state
    response = create(c)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["target_count"] == 10000 and saved["confirmed_count"] == 0
    assert "owner_user_id" not in saved and "request_key" not in saved
    base = "/api/creation-requests/" + saved["id"]
    body = {"expected_revision": saved["revision"], "goals": [{"goal": "接口细节"}, {"goal": "实际使用"}]}
    planned = c.client.post(base + "/windows", headers=c.alice, json=body)
    assert planned.status_code == 200, planned.text
    window = planned.json()["current_window"]
    assert len(window["entries"]) == 2 and planned.json()["confirmed_count"] == 0
    assert all(entry["plan"]["target_count"] == 1 for entry in window["entries"])
    assert c.client.get(base + "/items", headers=c.alice).json()["items"][0]["task"] is None
    confirmation = {"expected_revision": planned.json()["revision"], "input_fingerprint": window["input_fingerprint"]}
    confirmed = c.client.post(base + "/windows/" + window["id"] + "/confirm", headers=c.alice, json=confirmation)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_count"] == 2
    replay = c.client.post(base + "/windows/" + window["id"] + "/confirm", headers=c.alice, json=confirmation)
    assert replay.json()["confirmed_count"] == 2
    assert c.client.get(base + "/items?limit=1", headers=c.alice).json()["has_more"]
    assert c.client.get(base, headers=c.bob).status_code == 404
    assert c.client.get(base).status_code == 403


def test_unknown_relationship_and_today_range_stay_waiting_without_tasks(client_state):
    c = client_state
    saved = create(c, goal="把今天发给你的视频做成10000条视频", output_relation=None).json()
    assert saved["status"] == "awaiting_clarification" and len(saved["clarification_questions"]) == 2
    base = "/api/creation-requests/" + saved["id"]
    rejected = c.client.post(base + "/windows", headers=c.alice,
        json={"expected_revision": saved["revision"], "goals": [{"goal": "主题"}]})
    assert rejected.status_code == 409
    revised = c.client.patch(base, headers=c.alice, json={"expected_revision": saved["revision"],
        "output_relation": "series", "material_scope": "workspace_library"})
    assert revised.status_code == 200 and revised.json()["clarification_questions"] == []
    assert c.repository.list_creation_tasks("request-test") == []


def test_model_cannot_change_original_count_or_turn_identity(client_state):
    c = client_state
    message = "请制作10000条视频，每条两分钟"
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "目标")
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message, str(uuid4()), 1)
    rejected = create(c, goal="模型改写的单条目标", target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"])
    assert rejected.status_code == 409
    assert create(c, assistant_turn_id=turn["id"]).status_code == 409
    assert create(c, assistant_turn_id=turn["id"], request_key=turn["id"], duration_target_seconds=60).status_code == 409
    saved = create(c, goal="模型改写", assistant_turn_id=turn["id"], request_key=turn["id"])
    assert saved.status_code == 200 and saved.json()["goal"] == message
    c.conversations.finish_turn(turn["id"], "completed", "已保存")
    assert create(c, assistant_turn_id=turn["id"]).status_code == 409


@pytest.mark.parametrize("goal,count,duration", [
    ("做五条视频，每条两分钟", 5, 120), ("做5条，每条120秒", 5, 120),
    ("做一万条视频", 10000, None), ("做成一个2分钟的短视频", None, 120),
])
def test_explicit_user_numbers_are_program_facts(goal, count, duration):
    assert main._explicit_creation_constraints(goal) == (count, duration)


def test_stale_external_preflight_cannot_partially_confirm_window(client_state):
    c = client_state
    saved = create(c).json()
    base = "/api/creation-requests/" + saved["id"]
    planned = c.client.post(base + "/windows", headers=c.alice, json={"expected_revision": saved["revision"],
        "goals": [{"goal": "主题一"}, {"goal": "主题二"}]}).json()
    c.live_fingerprint[0] = "b" * 64
    window = planned["current_window"]
    rejected = c.client.post(base + "/windows/" + window["id"] + "/confirm", headers=c.alice,
        json={"expected_revision": planned["revision"], "input_fingerprint": window["input_fingerprint"]})
    assert rejected.status_code == 409
    assert c.client.get(base, headers=c.alice).json()["confirmed_count"] == 0
    assert c.repository.list_creation_tasks("request-test") == []
    replacement = c.client.post(base + "/windows/" + window["id"] + "/replan", headers=c.alice,
        json={"expected_revision": planned["revision"]})
    assert replacement.status_code == 200, replacement.text
    fresh = replacement.json()
    assert fresh["current_window"]["id"] != window["id"]
    new_window = fresh["current_window"]
    assert c.client.post(base + "/windows/" + new_window["id"] + "/confirm", headers=c.alice,
        json={"expected_revision": fresh["revision"], "input_fingerprint": new_window["input_fingerprint"]}).status_code == 200
    assert c.client.get(base, headers=c.alice).json()["confirmed_count"] == 2


def test_ninth_window_item_and_duplicate_goal_are_rejected(client_state):
    c = client_state
    saved = create(c).json()
    base = "/api/creation-requests/" + saved["id"] + "/windows"
    for goals in [[{"goal": str(i)} for i in range(9)], [{"goal": "相同"}, {"goal": "相同"}]]:
        assert c.client.post(base, headers=c.alice, json={"expected_revision": saved["revision"], "goals": goals}).status_code == 422
    assert c.repository.list_creation_tasks("request-test") == []


def test_original_turn_scope_and_exact_saved_request_recovery(client_state):
    c = client_state
    message = "使用刚导入的现有素材制作1条视频，不调用声音生成"
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "目标")
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message, str(uuid4()), 1)
    saved = create(c, goal="模型误推今日", target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"], material_scope="today_received").json()
    assert saved["material_scope"] == {"kind": "workspace_library"}
    assert saved["clarification_questions"] == []
    c.conversations.finish_turn(turn["id"], "completed", "已保存", {"kind": "conversation", "creation_request_id": None})
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", main.get_assistant_conversation, methods=["GET"])
    restored = c.client.get("/api/assistant/conversations/"+chat["id"], headers=c.alice)
    assert restored.status_code == 200, restored.text
    assert restored.json()["turns"][0]["result"]["creation_request_id"] == saved["id"]
    assert restored.json()["turns"][0]["result"]["kind"] == "creation_request"
    assert c.conversations.get_turn(turn["id"])["result"]["kind"] == "conversation", "read recovery must not rewrite old evidence"
    assert c.client.get("/api/assistant/conversations/"+chat["id"], headers=c.bob).status_code == 404


def test_request_recovery_does_not_match_a_similar_goal(client_state):
    c = client_state
    message = "同名目标"
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", message)
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message, str(uuid4()), 1)
    create(c, goal=message, target_count=1)
    c.conversations.finish_turn(turn["id"], "completed", "已保存", {"kind": "conversation"})
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", main.get_assistant_conversation, methods=["GET"])
    restored = c.client.get("/api/assistant/conversations/"+chat["id"], headers=c.alice).json()
    assert restored["turns"][0]["result"] == {"kind": "conversation"}


@pytest.mark.parametrize("result_asset,expected_status", [("asset-a", 200), ("other-asset", 502), ("malformed", 502)])
def test_material_tag_receipt_still_requires_exact_bound_object(client_state, monkeypatch, result_asset, expected_status):
    from datetime import datetime, timezone
    c = client_state
    monkeypatch.setattr(main, "get_production_material", lambda *_args, **_kwargs: SimpleNamespace(
        id="asset-a", updated_at=datetime(2026, 10, 16, tzinfo=timezone.utc), segments=[]))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _workspace: SimpleNamespace(
        effective_llm_provider="test", effective_llm_model="test", effective_llm_api_key=None))
    raw = {"scope": "asset", "asset_ref": "asset:"+result_asset, "user_tags": ["配图"], "action": "add", "changed": True}
    if result_asset == "malformed":
        del raw["user_tags"]
    monkeypatch.setattr(main, "run_plan_turn", lambda *_args, **_kwargs: ("已回读", {"kind": "material_result", "material_result": raw}))
    c.client.app.add_api_route("/api/assistant/material/turn", main.material_assistant_turn, methods=["POST"])
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "素材")
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "添加配图标签", 0)
    payload = {"conversation_id": chat["id"], "message": "添加配图标签", "client_request_id": str(uuid4()),
               "expected_draft_version": 1, "selection": {"scope": "asset", "asset_id": "asset-a"}}
    response = c.client.post("/api/assistant/material/turn", headers=c.alice, json=payload)
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        assert response.json()["material_result"]["asset_ref"] == "asset:asset-a"
        assert c.client.post("/api/assistant/material/turn", headers=c.alice, json=payload).json() == response.json()
    else:
        assert c.conversations.list_turns(chat["id"])[0]["status"] == "failed"


def test_interrupted_follow_up_does_not_restore_original_creation_request(client_state):
    c = client_state
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "目标")
    message = "建一条视频"
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message, str(uuid4()), 1)
    create(c, goal=message, target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"])
    c.conversations.append_event(turn["id"], {"type": "assistant_input", "input_id": str(uuid4()), "target_turn_id": turn["id"], "status": "interrupted"})
    c.conversations.finish_turn(turn["id"], "completed", "后续澄清", {"kind": "conversation"})
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", main.get_assistant_conversation, methods=["GET"])
    restored = c.client.get("/api/assistant/conversations/"+chat["id"], headers=c.alice).json()
    assert restored["turns"][0]["result"]["kind"] == "conversation"
