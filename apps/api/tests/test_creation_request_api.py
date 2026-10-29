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
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    from app.local_portrait import LocalPortrait
    from app.subtitle_tracks import OpenAICompatibleSubtitleTranslationProvider, UnavailableSubtitleTranslationProvider
    monkeypatch.setattr(LocalPortrait, "process", forbidden)
    monkeypatch.setattr(OpenAICompatibleSubtitleTranslationProvider, "translate", forbidden)
    monkeypatch.setattr(UnavailableSubtitleTranslationProvider, "translate", forbidden)
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
    def preflight(_case, workspace_id, payload, **_dependencies):
        return replace(entries(count=1)[0][0], goal=payload.goal, name=payload.name or payload.goal,
            language=payload.language, aspect_ratio=payload.aspect_ratio,
            presenter_share_target=payload.presenter_share_target, lipsync_quality=payload.lipsync_quality,
            duration_target_seconds=payload.duration_target_seconds, duration_mode=payload.duration_mode,
            duration_range_seconds=payload.duration_range_seconds, preflight_fingerprint=live_fingerprint[0])
    monkeypatch.setattr(main.CreationPlanPreflightUseCase, "prepare", preflight)
    app = FastAPI()
    for path, handler, methods in [
        ("/api/creation-requests", main.create_creation_request, ["POST"]),
        ("/api/assistant/creation-request", main.read_current_input_creation_request, ["GET"]),
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
    assert c.repository.task_records.list_creation_tasks("request-test") == []


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


def test_original_creation_request_can_be_read_by_trusted_input_without_server_id(client_state):
    c = client_state
    message = "制作10000条视频，每条两分钟"
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "原输入")
    chat = c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        message, str(uuid4()), chat["draft_version"])
    lookup = "/api/assistant/creation-request?assistant_turn_id=" + turn["id"]
    assert c.client.get(lookup, headers=c.alice).json() is None
    saved = create(c, assistant_turn_id=turn["id"], request_key=turn["id"])
    assert saved.status_code == 200, saved.text
    recovered = c.client.get(lookup, headers=c.alice)
    assert recovered.status_code == 200 and recovered.json()["id"] == saved.json()["id"]
    assert c.client.get(lookup, headers=c.bob).status_code == 404
    c.conversations.finish_turn(turn["id"], "completed", "原输入已结束")
    assert c.client.get(lookup, headers=c.alice).status_code == 409
    assert c.repository.task_records.list_creation_tasks("request-test") == []
    assert create(c, assistant_turn_id=turn["id"]).status_code == 409


@pytest.mark.parametrize("goal,count,duration", [
    ("做五条视频，每条两分钟", 5, 120), ("做5条，每条120秒", 5, 120),
    ("做一万条视频", 10000, None), ("做成一个2分钟的短视频", None, 120),
])
def test_explicit_user_numbers_are_program_facts(goal, count, duration):
    assert main.request_workflow.explicit_creation_constraints(goal) == (count, duration)


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
    assert rejected.json()["detail"]["error"] == "creation_window_dependencies_changed"
    assert rejected.json()["detail"]["expected_input_fingerprint"] == "a" * 64
    assert rejected.json()["detail"]["current_input_fingerprint"] == "b" * 64
    assert c.client.get(base, headers=c.alice).json()["confirmed_count"] == 0
    assert c.repository.task_records.list_creation_tasks("request-test") == []
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
    assert c.repository.task_records.list_creation_tasks("request-test") == []


def test_replan_applies_new_goals_and_rejects_old_window_and_revision(client_state):
    c = client_state
    saved = create(c, goal="制作中英文各一条", target_count=2, output_relation="variants").json()
    base = "/api/creation-requests/" + saved["id"]
    planned = c.client.post(base + "/windows", headers=c.alice, json={"expected_revision": saved["revision"],
        "goals": [{"goal": "原中文"}, {"goal": "原英文", "requirements": {"language": "en-US"}}]}).json()
    old = planned["current_window"]
    goals = [{"goal": "修订中文", "requirements": {"language": "zh-CN", "duration_target_seconds": 30}},
        {"goal": "修订英文", "requirements": {"language": "en-US", "duration_target_seconds": 30}}]
    replacement = c.client.post(base + "/windows/" + old["id"] + "/replan", headers=c.alice,
        json={"expected_revision": planned["revision"], "goals": goals})
    assert replacement.status_code == 200, replacement.text
    fresh = replacement.json()
    assert fresh["current_window"]["id"] != old["id"]
    assert [(x["plan"]["goal"], x["plan"]["language"], x["plan"]["duration_target_seconds"])
        for x in fresh["current_window"]["entries"]] == [("修订中文", "zh-CN", 30), ("修订英文", "en-US", 30)]
    for invalid_goals in [[], [{"goal": str(i)} for i in range(9)]]:
        invalid = c.client.post(base + "/windows/" + fresh["current_window"]["id"] + "/replan", headers=c.alice,
            json={"expected_revision": fresh["revision"], "goals": invalid_goals})
        assert invalid.status_code == 422
    wrong_input = c.client.post(base + "/windows/" + fresh["current_window"]["id"] + "/confirm", headers=c.alice,
        json={"expected_revision": fresh["revision"], "input_fingerprint": "f" * 64})
    assert wrong_input.status_code == 409
    assert wrong_input.json()["detail"]["error"] == "creation_window_input_conflict"
    assert wrong_input.json()["detail"]["current_input_fingerprint"] == fresh["current_window"]["input_fingerprint"]
    stale = c.client.post(base + "/windows/" + fresh["current_window"]["id"] + "/replan", headers=c.alice,
        json={"expected_revision": planned["revision"], "goals": goals})
    assert stale.status_code == 409
    assert stale.json()["detail"] == {"error": "creation_request_revision_conflict",
        "reason": "制作目标的修订已变化，未应用旧要求。", "repair_action": "先读取当前制作目标，再按实际修订提交新要求。",
        "request_id": saved["id"], "expected_revision": planned["revision"], "current_revision": fresh["revision"]}
    old_confirm = c.client.post(base + "/windows/" + old["id"] + "/confirm", headers=c.alice,
        json={"expected_revision": fresh["revision"], "input_fingerprint": old["input_fingerprint"]})
    assert old_confirm.status_code == 409 and old_confirm.json()["detail"]["error"] == "creation_window_not_current"
    window = fresh["current_window"]
    confirmation = {"expected_revision": fresh["revision"], "input_fingerprint": window["input_fingerprint"]}
    for _ in range(2):
        response = c.client.post(base + "/windows/" + window["id"] + "/confirm", headers=c.alice, json=confirmation)
        assert response.status_code == 200 and response.json()["confirmed_count"] == 2
    assert len(c.repository.task_records.list_creation_tasks("request-test")) == 2


def test_confirm_checks_live_blocking_condition_even_when_digest_is_unchanged(client_state, monkeypatch):
    from app.creation.plan_contracts import CreationPlanBlocker
    c = client_state
    base, data, url, confirmation = prepare_one(c, {"kind": "goal_delegation", "topic_title": "原主题"})
    prepare = main.CreationPlanPreflightUseCase.prepare
    def blocked(*args, **kwargs):
        return replace(prepare(*args, **kwargs), blockers=[CreationPlanBlocker(reason="content_sources_missing",
            impact="当前所选资料已不可使用", repair_action="恢复资料", stage="topic", blocks_confirmation=True)])
    monkeypatch.setattr(main.CreationPlanPreflightUseCase, "prepare", blocked)
    response = c.client.post(url, headers=c.alice, json=confirmation)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "creation_window_blocked"
    assert response.json()["detail"]["reason"] == "content_sources_missing"
    assert c.repository.task_records.list_creation_tasks("request-test") == []
    assert c.client.get(base, headers=c.alice).json()["current_window"]["id"] == data["current_window"]["id"]


def test_stale_assistant_input_cannot_replan_or_revise_request(client_state):
    c = client_state
    base, data, _url, _confirmation = prepare_one(c, {"kind": "goal_delegation", "topic_title": "原主题"})
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "原对话")
    chat = c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "继续制作", 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        "继续制作", str(uuid4()), chat["draft_version"])
    c.conversations.finish_turn(turn["id"], "completed", "输入已结束")
    suffix = "?assistant_turn_id=" + turn["id"]
    response = c.client.post(base + "/windows/" + data["current_window"]["id"] + "/replan" + suffix,
        headers=c.alice, json={"expected_revision": data["revision"], "goals": [{"goal": "不得写入"}]})
    assert response.status_code == 409
    response = c.client.patch(base + suffix, headers=c.alice, json={"expected_revision": data["revision"]})
    assert response.status_code == 409
    assert c.client.get(base, headers=c.alice).json()["revision"] == data["revision"]
    assert c.repository.task_records.list_creation_tasks("request-test") == []


def test_resource_change_at_adoption_is_safe_and_does_not_create_tasks(client_state, monkeypatch):
    c = client_state
    _base, _data, url, confirmation = prepare_one(c, {"kind": "goal_delegation", "topic_title": "原主题"})
    def revoked(*args, **kwargs):
        raise PermissionError("private-source-path-and-credential")
    monkeypatch.setattr(c.repository.creation_request_adoption, "confirm_creation_request_window", revoked)
    response = c.client.post(url, headers=c.alice, json=confirmation)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "creation_window_resource_unavailable"
    assert "private" not in response.text
    assert c.repository.task_records.list_creation_tasks("request-test") == []


def test_language_variants_keep_individual_requirements_through_replan_and_adoption(client_state):
    import json
    c = client_state
    saved = create(c, goal="产品介绍中英文各一条", target_count=2, output_relation="variants",
        language="zh-CN", duration_target_seconds=60, duration_mode="approximate",
        duration_range_seconds=[20, 90]).json()
    base = "/api/creation-requests/" + saved["id"]
    planned = c.client.post(base + "/windows", headers=c.alice, json={"expected_revision": saved["revision"],
        "goals": [{"goal": "同一产品介绍"}, {"goal": "同一产品介绍", "requirements": {
            "language": "en-US", "duration_target_seconds": 30, "aspect_ratio": "16:9",
            "presenter_share_target": 0, "lipsync_quality": "fast"}}]})
    assert planned.status_code == 200, planned.text
    data = planned.json()
    # Declare frozen per-delivery agreements, distinct from the parent's defaults.
    agreements = [("approximate", [50, 70]), ("exact", None)]
    with c.repository._connect() as conn:
        for entry, (mode, bounds) in zip(data["current_window"]["entries"], agreements):
            row = conn.execute("SELECT plan_payload FROM creation_request_items WHERE window_id=? AND plan_id=?",
                (data["current_window"]["id"], entry["plan_id"])).fetchone()
            plan = {**json.loads(row[0]), "duration_mode": mode, "duration_range_seconds": bounds}
            conn.execute("UPDATE creation_request_items SET plan_payload=? WHERE window_id=? AND plan_id=?",
                (json.dumps(plan), data["current_window"]["id"], entry["plan_id"]))
    frozen = c.client.get(base, headers=c.alice).json()["current_window"]["entries"]
    assert [(item["plan"]["duration_mode"], item["plan"]["duration_range_seconds"]) for item in frozen] == agreements
    c.live_fingerprint[0] = "b" * 64
    replanned = c.client.post(base + "/windows/" + data["current_window"]["id"] + "/replan", headers=c.alice,
        json={"expected_revision": data["revision"]})
    assert replanned.status_code == 200, replanned.text
    data = replanned.json()
    window = data["current_window"]
    assert [(item["plan"]["duration_mode"], item["plan"]["duration_range_seconds"]) for item in window["entries"]] == agreements
    confirmation = {"expected_revision": data["revision"], "input_fingerprint": window["input_fingerprint"]}
    for _ in range(2):
        confirmed = c.client.post(base + "/windows/" + window["id"] + "/confirm", headers=c.alice, json=confirmation)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["confirmed_count"] == 2
    plans = [entry["plan"] for entry in c.client.get(base + "/items", headers=c.alice).json()["items"]]
    assert [(p["language"], p["duration_target_seconds"], p["aspect_ratio"]) for p in plans] == [
        ("zh-CN", 60, "9:16"), ("en-US", 30, "16:9")]
    assert [(plan["duration_mode"], plan["duration_range_seconds"]) for plan in plans] == agreements
    assert plans[1]["presenter_share_target"] == 0 and plans[1]["lipsync_quality"] == "fast"
    assert len(c.repository.task_records.list_creation_tasks("request-test")) == 2


def test_explicit_default_override_is_duplicate_of_inherited_delivery(client_state):
    c = client_state
    saved = create(c, language="en-US").json()
    response = c.client.post("/api/creation-requests/" + saved["id"] + "/windows", headers=c.alice,
        json={"expected_revision": saved["revision"], "goals": [{"goal": "同主题"},
            {"goal": "同主题", "name": "另一个名字", "requirements": {"language": "en-US"}}]})
    assert response.status_code == 422
    assert c.repository.task_records.list_creation_tasks("request-test") == []


def prepare_one(c, intent):
    saved = create(c, goal="按已给定内容制作1条视频", target_count=1, intent=intent).json()
    base = "/api/creation-requests/" + saved["id"]
    planned = c.client.post(base + "/windows", headers=c.alice,
        json={"expected_revision": saved["revision"], "goals": [{"goal": "同一作品"}]})
    assert planned.status_code == 200, planned.text
    data = planned.json()
    window = data["current_window"]
    return base, data, base + "/windows/" + window["id"] + "/confirm", {
        "expected_revision": data["revision"], "input_fingerprint": window["input_fingerprint"],
        "adoption_kind": "delegated_system"}


def test_discussion_cannot_be_adopted_or_start_a_task(client_state):
    c = client_state
    base, data, url, confirmation = prepare_one(c, {"kind": "discussion", "topic_title": "先讨论这个主题"})
    for kind in ("manual", "delegated_system"):
        rejected = c.client.post(url, headers=c.alice, json={**confirmation, "adoption_kind": kind})
        assert rejected.status_code == 409 and "讨论" in rejected.text
    assert c.repository.task_records.list_creation_tasks("request-test") == []
    assert c.client.get(base, headers=c.alice).json()["intent"]["kind"] == "discussion"


def test_delegated_existing_topic_is_adopted_without_topic_generation(client_state, monkeypatch):
    c = client_state
    _base, _data, url, confirmation = prepare_one(c, {"kind": "goal_delegation", "topic_title": "用户已有主题"})
    adopted = c.client.post(url, headers=c.alice, json=confirmation)
    assert adopted.status_code == 200, adopted.text
    entry = adopted.json()["current_window"]["entries"][0]
    assert entry["plan"]["adoption"]["kind"] == "delegated_system"
    assert entry["plan"]["adoption"]["operator"] == "system"
    task = c.repository.task_records.get_creation_task("request-test", entry["task_id"])
    assert task.stage == "script" and task.selected_topic_id
    topics = c.repository.topic_records.list_topic_candidates("request-test", task.id)
    assert len(topics) == 1 and topics[0].title == "用户已有主题"
    run = c.repository.topic_records.latest_topic_generation_run("request-test", task.id)
    assert run.route_snapshot == {"operation": "provided_topic", "provider": "none"}
    route = {"provider_kind": "openai_compatible", "execution_location": "cloud", "model_id": "test-model",
        "endpoint_identity": "https://models.invalid", "connection_version": "test-connection",
        "availability": "unconfigured", "checked_at": "2026-10-18T00:00:00Z"}
    monkeypatch.setattr(main, "_topic_route_snapshot", lambda workspace: route)
    c.client.app.add_api_route("/api/creation-tasks/{task_id}/topics", main.get_creation_task_topics, methods=["GET"])
    response = c.client.get(f"/api/creation-tasks/{task.id}/topics", headers=c.alice)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["latest_run"]["route_snapshot"] == {"operation": "provided_topic", "provider": "none"}
    assert data["route_preflight"] == route
    assert data["candidates"][0]["selection_status"] == "confirmed"


def test_exact_final_copy_is_adopted_atomically_locked_and_replayed(client_state):
    import hashlib
    from dataclasses import replace
    c = client_state
    text = "  第一段保留空格。\n\n第二段：标点、换行和末尾空格都不改。  "
    _base, _data, url, confirmation = prepare_one(c,
        {"kind": "final_script", "final_script": {"title": "准确版本", "text": text}})
    first = c.client.post(url, headers=c.alice, json=confirmation)
    assert first.status_code == 200, first.text
    second = c.client.post(url, headers=c.alice, json=confirmation)
    assert second.status_code == 200
    entry = first.json()["current_window"]["entries"][0]
    assert second.json()["current_window"]["entries"][0]["task_id"] == entry["task_id"]
    task = c.repository.task_records.get_creation_task("request-test", entry["task_id"])
    assert task.stage == "narration" and task.confirmed_script_id
    script = c.repository.script_records.get_creation_task_script("request-test", task.confirmed_script_id)
    assert script.sections[0].content == text
    assert script.locked_body_digest == hashlib.sha256(text.encode()).hexdigest()
    assert script.adoption["kind"] == "delegated_system" and script.adoption["script_id"] == script.id
    with pytest.raises(ValueError, match="定稿锁定"):
        c.repository.script_records.confirm_creation_task_script("request-test", task.id, script.id, title=script.title,
            sections=[replace(script.sections[0], content="模型改写版本")], expected_updated_at=script.updated_at,
            verified_owner_user_id=c.member.member_id, adoption_kind="delegated_system")
    assert c.repository.script_records.get_creation_task_script("request-test", script.id).sections[0].content == text
    assert len(c.repository.script_records.list_creation_task_scripts("request-test", task.id)) == 1


def test_final_copy_failure_rolls_back_entire_window(client_state):
    import sqlite3
    c = client_state
    base, _data, url, confirmation = prepare_one(c,
        {"kind": "final_script", "final_script": {"title": "原稿", "text": "用户准确正文"}})
    with c.repository._connect() as conn:
        conn.execute("CREATE TRIGGER fail_final_copy BEFORE INSERT ON creation_task_scripts "
            "BEGIN SELECT RAISE(ABORT,'copy write failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="copy write failed"):
        c.client.post(url, headers=c.alice, json=confirmation)
    assert c.repository.task_records.list_creation_tasks("request-test") == []
    saved = c.client.get(base, headers=c.alice).json()
    assert saved["confirmed_count"] == 0 and saved["current_window"]["status"] == "proposed"
    with c.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_plans").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM topic_candidates").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_intent_adopted'").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_final_copy")
    # The same original request can now complete its real owner transaction.
    accepted = c.client.post(url, headers=c.alice, json=confirmation)
    assert accepted.status_code == 200, accepted.text
    current = accepted.json()
    assert current["id"] == saved["id"] and current["confirmed_count"] == 1
    assert current["current_window"]["id"] == saved["current_window"]["id"]
    entry = current["current_window"]["entries"][0]
    task = c.repository.task_records.get_creation_task("request-test", entry["task_id"])
    script = c.repository.script_records.get_creation_task_script("request-test", task.confirmed_script_id)
    assert task.stage == "narration" and task.selected_topic_id
    assert script.sections[0].content == "用户准确正文" and script.locked_body_digest
    event = c.repository.execution_records.get_task_event("request-test", "creation_adopted_" + task.id)
    assert event.payload["script_id"] == script.id and event.payload["script_adoption"] == script.adoption


def test_delegated_script_adoption_records_real_version_without_fake_human_action(client_state):
    from app.creation.script_contracts import (
        CreationTaskScript,
        CreationTaskScriptSection,
        ScriptGenerationRun,
    )
    c = client_state
    _base, _data, url, confirmation = prepare_one(c, {"kind": "goal_delegation", "topic_title": "明确主题"})
    adopted = c.client.post(url, headers=c.alice, json=confirmation).json()
    task = c.repository.task_records.get_creation_task("request-test", adopted["current_window"]["entries"][0]["task_id"])
    run = ScriptGenerationRun("declared-fixture-run", "request-test", task.id, task.plan_id, task.selected_topic_id,
        "running", {"provider": "declared_fixture"}, "fixture/v1", [{"reference_id": "declared-fixture-source"}])
    c.repository.script_records.start_script_generation(run)
    draft = CreationTaskScript("declared-fixture-script", "request-test", task.id, run.id, task.selected_topic_id,
        "有来源的文案", "zh-CN", [CreationTaskScriptSection("section", "段落", "已核对的正文", "明确画面", ["declared-fixture-source"])])
    _task, _run, draft = c.repository.script_records.complete_script_generation(run.id, draft)
    task, script = c.repository.script_records.confirm_creation_task_script("request-test", task.id, draft.id, title=draft.title,
        sections=draft.sections, expected_updated_at=draft.updated_at, adoption_kind="delegated_system",
        verified_owner_user_id=c.member.member_id)
    assert task.confirmed_script_id == script.id and task.stage == "narration"
    assert script.adoption["operator"] == "system" and script.adoption["kind"] == "delegated_system"
    assert script.adoption["version"] == script.updated_at.isoformat()
    assert script.adoption["authorization_source"]["member_id"] == c.member.member_id


def test_fixed_copy_duration_conflict_keeps_text_and_creates_no_task(client_state):
    c = client_state
    text = "这是不可自动删改的定稿。" * 120
    saved = create(c, goal="按定稿制作1条视频", target_count=1, duration_target_seconds=15,
        intent={"kind": "final_script", "final_script": {"title": "长稿", "text": text}}).json()
    base = "/api/creation-requests/" + saved["id"]
    response = c.client.post(base + "/windows", headers=c.alice,
        json={"expected_revision": saved["revision"], "goals": [{"goal": "原稿制作"}]})
    assert response.status_code == 409 and "不会自动删词" in response.text
    assert c.repository.task_records.list_creation_tasks("request-test") == []
    assert c.client.get(base, headers=c.alice).json()["intent"]["final_script"]["text"] == text


def test_actual_chinese_english_each_one_input_is_two_variants_not_one_bilingual_video(client_state):
    c = client_state
    message = "制作产品介绍，中文和英文各一条"
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "双版本")
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message, str(uuid4()), 1)
    wrong = create(c, target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"])
    assert wrong.status_code == 409
    saved = create(c, target_count=2, output_relation=None, assistant_turn_id=turn["id"], request_key=turn["id"],
        intent={"kind": "goal_delegation"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["target_count"] == 2 and saved.json()["output_relation"] == "variants"
    assert saved.json()["clarification_questions"] == []


def test_consumed_input_gets_its_own_request_and_cannot_reuse_original_authority(client_state):
    from starlette.requests import Request
    c = client_state
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "输入切换")
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "制作1条视频", 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        "制作1条视频", str(uuid4()), 1)
    initial = create(c, target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"], intent={"kind": "goal_delegation"})
    assert initial.status_code == 200
    original_base = '/api/creation-requests/' + initial.json()['id']
    original_window = c.client.post(original_base + '/windows', headers=c.alice,
        json={'expected_revision': initial.json()['revision'], 'goals': [{'goal': '原输入的独立视频'}]}).json()
    message, input_id = "先讨论新的产品介绍，不要制作", str(uuid4())
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 2)
    c.conversations.enqueue_input(chat["id"], "request-test", c.member.member_id, target_turn_id=turn["id"],
        client_request_id=input_id, message=message, mode="steer", expected_draft_version=3,
        expected_reference_version=0, request_context={"kind": "home"})
    c.conversations.transition_input(chat["id"], "request-test", c.member.member_id, input_id, "consumed")
    assert create(c, target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"], intent={"kind": "goal_delegation"}).status_code == 409
    headers = {**c.alice, "X-Assistant-Input-ID": input_id}
    window = original_window['current_window']
    rejected = c.client.post(original_base + '/windows/' + window['id'] + '/confirm', headers=headers,
        json={'expected_revision': original_window['revision'], 'input_fingerprint': window['input_fingerprint'],
            'adoption_kind': 'delegated_system', 'assistant_turn_id': turn['id']})
    assert rejected.status_code == 409 and '讨论' in rejected.text
    body = {"request_key": input_id, "assistant_turn_id": turn["id"], "goal": "模型旧目标", "target_count": 1,
        "intent": {"kind": "discussion"}}
    current = c.client.post('/api/creation-requests', headers=headers, json=body)
    assert current.status_code == 200, current.text
    assert current.json()["goal"] == message and current.json()["id"] != initial.json()["id"]
    assert current.json()["intent"]["source"]["input_id"] == input_id
    request = Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in c.alice.items()]})
    recovered = main._assistant_creation_request_for_turn(request, c.conversations.get_turn(turn["id"]))
    assert recovered["id"] == current.json()["id"]


def test_discussion_task_input_can_save_draft_but_cannot_adopt_production(client_state):
    from starlette.requests import Request
    c = client_state
    chat = c.conversations.create("request-test", c.member.member_id, "task", "bound-task", "只讨论")
    message = "先讨论文案，不要制作"
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message,
        str(uuid4()), 1, {"kind": "task", "task_id": "bound-task", "stage": "script"})
    request = Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in c.alice.items()]})
    main._assistant_creation_adoption_scope("bound-task", request, turn["id"], draft_only=True)
    with pytest.raises(main.HTTPException) as failure:
        main._assistant_creation_adoption_scope("bound-task", request, turn["id"])
    assert failure.value.status_code == 409 and "讨论" in str(failure.value.detail)


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
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", main._conversation_http_adapter().get_conversation, methods=["GET"])
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
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", main._conversation_http_adapter().get_conversation, methods=["GET"])
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
        saved = c.conversations.get_turn(response.json()["turn_id"])
        assert saved["result"]["kind"] == "material_result"
        assert saved["result"]["response"]["material_result"] == response.json()["material_result"]
    else:
        assert c.conversations.list_turns(chat["id"])[0]["status"] == "failed"
        assert c.conversations.list_turns(chat["id"])[0]["result"]["kind"] == "failure"


def test_interrupted_follow_up_does_not_restore_original_creation_request(client_state):
    c = client_state
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "目标")
    message = "建一条视频"
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, message, str(uuid4()), 1)
    create(c, goal=message, target_count=1, assistant_turn_id=turn["id"], request_key=turn["id"])
    c.conversations.append_event(turn["id"], {"type": "assistant_input", "input_id": str(uuid4()), "target_turn_id": turn["id"], "status": "interrupted"})
    c.conversations.finish_turn(turn["id"], "completed", "后续澄清", {"kind": "conversation"})
    c.client.app.add_api_route("/api/assistant/conversations/{conversation_id}", main._conversation_http_adapter().get_conversation, methods=["GET"])
    restored = c.client.get("/api/assistant/conversations/"+chat["id"], headers=c.alice).json()
    assert restored["turns"][0]["result"]["kind"] == "conversation"
