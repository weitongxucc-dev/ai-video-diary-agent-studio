"""Conversation/SQLite storyboard receipts; synthetic SDK events, no model/media."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5
from dataclasses import replace

import pytest

import app.main as main
from app.assistant.result_projection import project_business_result
from app.conversation_repository import ConversationRepository
from test_storyboard_regeneration import state, inputs, run  # noqa: F401


def completion(details, *, query=False, input_id="current", status="settled", extra=()):
    return {"answer": "模型声称视频已生成", "status": status, "input_id": input_id, "tool_results": [*extra,
        {"type": "tool_execution_end", "toolName": "read_storyboard_regeneration" if query else "regenerate_storyboard",
         "toolCallId": "storyboard-call", "isError": details["kind"] != "storyboard_regeneration_receipt",
         "result": {"details": {**details, "invocation_input_id": input_id}}}]}


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "cancelled", "interrupted"])
def test_projection_uses_storyboard_status_even_after_provider_failure(status):
    message, result = project_business_result(completion(
        {"kind": "storyboard_regeneration_receipt", "receipt": {"status": status}}, status="failed"))
    assert result["kind"] == "storyboard_regeneration_receipt" and result["action"] == "submit"
    assert result["tool_call_id"] == "storyboard-call"
    assert "视频已生成" not in message and "execution_action" not in result
    assert ("已保存" in message) == (status == "succeeded")


@pytest.mark.parametrize("kind", ["storyboard_regeneration_unknown", "storyboard_regeneration_rejected"])
def test_projection_preserves_original_locator_and_discards_other_input_receipts(kind):
    details = {"kind": kind, "request_id": str(uuid4()), "query_url": "original-query",
        "failure": {"reason": "实际失败", "repair_action": "先查询原请求"}}
    old = completion({"kind": "storyboard_regeneration_receipt", "receipt": {"status": "succeeded"}},
        input_id="old")["tool_results"][0]
    _, result = project_business_result(completion(details, extra=[old]))
    assert result["request_id"] == details["request_id"] and result["query_url"] == "original-query"
    assert result["kind"] == kind and result["action"] == "submit" and "receipt" not in result
    current = completion(details)
    current["input_id"] = "new"
    assert project_business_result(current)[1] is None


@pytest.fixture
def chat(state, tmp_path, monkeypatch):
    state.conversations = ConversationRepository(tmp_path / "conversation.sqlite3")
    monkeypatch.setattr(main, "conversation_repo", state.conversations)
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="synthetic", effective_llm_model="synthetic-events", effective_llm_api_key=None))
    monkeypatch.setattr(main, "_goal_service", lambda: SimpleNamespace(register_turn=lambda *_: None))
    monkeypatch.setattr(main, "_wake_assistant_continuations", lambda: None)
    state.chat = state.conversations.create("ws", state.member.member_id, "task", state.task.id)
    return state


def send(state, message="只重新生成故事板，保留配音，不生成视频"):
    current = state.conversations.get(state.chat["id"], "ws", state.member.member_id)
    draft = state.conversations.save_draft(current["id"], "ws", state.member.member_id, message, current["draft_version"])
    task = state.repo.get_creation_task("ws", state.task.id)
    version = state.repo.get_timeline_version("ws", task.current_timeline_version_id)
    payload = {"conversation_id": current["id"], "message": message, "context_stage": "montage",
        "client_request_id": str(uuid4()), "expected_draft_version": draft["draft_version"],
        "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence}
    return state.client.post(state.base_url + "/assistant/turn", json=payload), payload


@pytest.mark.parametrize("stale_field", ["id", "sequence"])
def test_task_send_stale_timeline_returns_machine_code_before_creating_turn(chat, monkeypatch, stale_field):
    state = chat
    current = state.conversations.get(state.chat["id"], "ws", state.member.member_id)
    message = "保留这条尚未发送的修改要求"
    draft = state.conversations.save_draft(current["id"], "ws", state.member.member_id, message, current["draft_version"])
    version = state.repo.get_timeline_version("ws", state.repo.get_creation_task("ws", state.task.id).current_timeline_version_id)
    monkeypatch.setattr(main, "run_task_turn", lambda *_a, **_k: pytest.fail("stale task must not invoke the assistant"))
    payload = {"conversation_id": current["id"], "message": message, "context_stage": "montage",
        "client_request_id": str(uuid4()), "expected_draft_version": draft["draft_version"],
        "expected_timeline_version_id": "old-timeline" if stale_field == "id" else version.id,
        "expected_timeline_sequence": version.sequence - 1 if stale_field == "sequence" else version.sequence}
    response = state.client.post(state.base_url + "/assistant/turn", json=payload)
    assert response.status_code == 409
    assert response.json() == {"detail": {"error": "creation_task_timeline_version_conflict",
        "message": "当前任务版本已变化，请重新查看选中对象后发送。"}}
    assert state.conversations.request_context(payload["client_request_id"], current["id"], "ws", state.member.member_id) is None
    after = state.conversations.get(current["id"], "ws", state.member.member_id)
    assert (after["draft_text"], after["draft_version"]) == (message, draft["draft_version"])
    with state.conversations._connect() as conn:
        assert conn.execute("SELECT count(*) FROM assistant_conversation_turns WHERE conversation_id=?", (current["id"],)).fetchone()[0] == 0


def request_id(task_id, turn_id):
    return str(uuid5(NAMESPACE_URL, json.dumps([task_id, turn_id, "storyboard-call"],
        ensure_ascii=False, separators=(",", ":"))))


def submitted(state, turn_id):
    payload = {**inputs(state), "assistant_turn_id": turn_id, "request_id": request_id(state.task.id, turn_id)}
    response = state.client.post(state.url, json=payload)
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["queued", "succeeded", "lost_after_registration", "forged_status"])
def test_http_turn_rereads_original_request_and_persists_dedicated_receipt(chat, monkeypatch, mode):
    state = chat
    def model(*args, **kwargs):
        receipt = submitted(state, kwargs["turn_id"])
        if mode == "succeeded":
            run(state)
        if mode == "lost_after_registration":
            details = {"kind": "storyboard_regeneration_unknown", "request_id": receipt["request_id"],
                "query_url": receipt["result_url"], "failure": {"reason": "响应丢失", "repair_action": "查询原请求"}}
        else:
            if mode == "forged_status":
                receipt = {**receipt, "status": "succeeded", "result": {"fake": True}}
            details = {"kind": "storyboard_regeneration_receipt", "receipt": receipt}
        return project_business_result(completion(details))
    monkeypatch.setattr(main, "run_task_turn", model)
    response, payload = send(state)
    assert response.status_code == 200, response.text
    value = response.json()
    result = value["storyboard_regeneration"]
    assert result["kind"] == "storyboard_regeneration_receipt" and result["action"] == "submit"
    assert result["receipt"]["status"] == ("succeeded" if mode == "succeeded" else "queued")
    assert value["execution_action"] is value["execution_status"] is value["timeline_action"] is None
    assert "视频已生成" not in value["reply"]
    saved = state.conversations.get_turn(value["turn_id"])["result"]
    assert saved["storyboard_regeneration"] == result == saved["response"]["storyboard_regeneration"]
    replay = state.client.post(state.base_url + "/assistant/turn", json=payload)
    assert replay.status_code == 200 and replay.json()["storyboard_regeneration"] == result
    assert len(state.builds) == (1 if mode == "succeeded" else 0)


@pytest.mark.parametrize("kind", ["storyboard_regeneration_unknown", "storyboard_regeneration_rejected"])
def test_http_unknown_without_server_record_keeps_verified_original_UUID(chat, monkeypatch, kind):
    state = chat
    def model(*args, **kwargs):
        identity = request_id(state.task.id, kwargs["turn_id"])
        return project_business_result(completion({"kind": kind, "request_id": identity,
            "query_url": state.url + "/requests/" + identity,
            "failure": {"reason": "原响应不可得", "repair_action": "先查询原请求，不重发"}}))
    monkeypatch.setattr(main, "run_task_turn", model)
    response, _ = send(state)
    assert response.status_code == 200, response.text
    receipt = response.json()["storyboard_regeneration"]
    assert receipt["kind"] == kind and receipt["action"] == "submit"
    assert state.client.get(receipt["query_url"]).status_code == 404
    assert not state.builds and not state.repo.list_edit_operations("ws", state.task.id)


def test_query_after_undo_returns_original_version_without_adopting_it_again(chat, monkeypatch):
    state = chat
    first = state.client.post(state.url, json=inputs(state)).json()
    run(state)
    original = state.client.get(first["result_url"]).json()
    version = original["result"]
    assert state.client.post(state.base_url + "/timeline/undo", json={
        "expected_timeline_version_id": version["timeline_version_id"],
        "expected_timeline_sequence": version["timeline_sequence"]}).status_code == 200
    before = state.repo.get_creation_task("ws", state.task.id).current_timeline_version_id
    monkeypatch.setattr(main, "run_task_turn", lambda *args, **kwargs: project_business_result(completion(
        {"kind": "storyboard_regeneration_receipt", "receipt": original}, query=True)))
    response, _ = send(state, "查询刚才故事板重算的原结果")
    assert response.status_code == 200, response.text
    value = response.json()["storyboard_regeneration"]
    assert value["action"] == "query" and value["receipt"] == original
    assert state.repo.get_creation_task("ws", state.task.id).current_timeline_version_id == before
    assert len(state.builds) == 1


@pytest.mark.parametrize("fault", ["UUID", "job", "task", "input"])
def test_receipts_cannot_bind_forged_identity_or_previous_input(chat, monkeypatch, fault):
    state = chat
    def model(*args, **kwargs):
        receipt = submitted(state, kwargs["turn_id"])
        details = {"kind": "storyboard_regeneration_receipt", "receipt": deepcopy(receipt)}
        if fault == "UUID":
            details["receipt"]["request_id"] = str(uuid4())
        elif fault == "job":
            details["receipt"]["job_id"] = "foreign-job"
        elif fault == "task":
            details["receipt"]["task_id"] = "foreign-task"
        projected = project_business_result(completion(details))[1]
        if fault == "input":
            projected.update(input_id=str(uuid4()), input_turn_id=kwargs["turn_id"])
        return "模型声称已完成", projected
    monkeypatch.setattr(main, "run_task_turn", model)
    response, _ = send(state)
    assert response.status_code == 502, response.text
    assert not state.builds


def test_original_receipt_cannot_be_attached_after_real_steering_input_is_consumed(chat, monkeypatch):
    state = chat
    def model(*args, **kwargs):
        turn_id = kwargs["turn_id"]
        receipt = submitted(state, turn_id)
        origin = state.conversations.task_turn_source(turn_id, "ws", state.member.member_id, state.task.id)
        current = state.conversations.get(state.chat["id"], "ws", state.member.member_id)
        message = "新要求只查询当前状态"
        draft = state.conversations.save_draft(current["id"], "ws", state.member.member_id, message, current["draft_version"])
        input_id = str(uuid4())
        state.conversations.enqueue_input(current["id"], "ws", state.member.member_id, target_turn_id=turn_id,
            client_request_id=input_id, message=message, mode="steer", expected_draft_version=draft["draft_version"],
            expected_reference_version=current["reference_version"],
            request_context={**origin["context"], "selected_reference": None})
        state.conversations.transition_input(current["id"], "ws", state.member.member_id, input_id, "consumed")
        return project_business_result(completion({"kind": "storyboard_regeneration_receipt", "receipt": receipt}))
    monkeypatch.setattr(main, "run_task_turn", model)
    response, _ = send(state)
    assert response.status_code == 502 and "先前输入" in response.text
    assert not state.builds


def test_reread_keeps_original_permit_actor_check(chat, monkeypatch):
    state = chat
    def model(*args, **kwargs):
        receipt = submitted(state, kwargs["turn_id"])
        state.actor = replace(state.actor, member_id="different-permit-member")
        return project_business_result(completion({"kind": "storyboard_regeneration_receipt", "receipt": receipt}))
    monkeypatch.setattr(main, "run_task_turn", model)
    response, _ = send(state)
    assert response.status_code == 403 and "其他制作身份" in response.text
    assert not state.builds

