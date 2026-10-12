"""Saved program API over real metadata/Job ownership; explicit script runner substitutes."""
from uuid import uuid4
from uuid import uuid5, NAMESPACE_URL
from types import SimpleNamespace
from dataclasses import replace
import json
import pytest

import app.main as main
from app.creation_execution import CreationExecutionActor
from app.conversation_repository import ConversationRepository
from test_media_script_api import state as script_state, submit  # noqa: F401
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401


@pytest.fixture
def state(script_state, monkeypatch):  # noqa: F811 (existing fixture alias)
    state = script_state
    def actor_for(request, workspace):
        member = state.members.resolve_member(request.headers.get("X-Local-Member-Token"))
        if member is None:
            raise main.HTTPException(status_code=403, detail="member unavailable")
        return CreationExecutionActor(workspace, member.member_id)
    monkeypatch.setattr(main, "_require_permit_actor", actor_for)
    monkeypatch.setattr(main, "_verify_creation_identity", lambda actor:
        "ready" if main.find_member(state.members, actor.member_id) else "unauthorized")
    queued = submit(state)
    assert queued.status_code == 202 and state.runtime.run_once()
    state.producer_job_id = queued.json()["job_id"]
    state.save_payload = {"request_id": str(uuid4()), "producer_job_id": state.producer_job_id,
        "name": "红色卡片", "description": "生成可编辑PNG图形来源", "parameter_schema": {
            "type": "object", "properties": {"color": {"type": "string", "maxLength": 30}},
            "required": ["color"], "additionalProperties": False}}
    saved = state.client.post(state.base_url + "/media-script-tools", json=state.save_payload)
    assert saved.status_code == 200, saved.text
    state.tool = saved.json()
    return state


def invocation(state):
    return {"request_id": str(uuid4()), "tool_version": state.tool["version"],
        "expected_timeline_version_id": state.payload["expected_timeline_version_id"],
        "expected_timeline_sequence": state.payload["expected_timeline_sequence"],
        "parameters": {"color": "blue"}, "input_refs": []}


def test_save_replays_original_v1_after_update_and_rejects_changed_input(state):
    tool_id = state.tool["tool_id"]
    updated = state.client.patch(f"/api/media-script-tools/{tool_id}", json={
        "expected_version": 1, **{key: value for key, value in state.save_payload.items() if key != "request_id"}, "name": "新版卡片"})
    assert updated.status_code == 200 and updated.json()["version"] == 2, updated.text
    replay = state.client.post(state.base_url + "/media-script-tools", json=state.save_payload)
    assert replay.status_code == 200 and replay.json()["version"] == 1, replay.text
    query = state.client.get(f"/api/media-script-tools/requests/{state.save_payload['request_id']}")
    assert query.status_code == 200 and query.json()["version"] == 1
    assert state.client.post(state.base_url + "/media-script-tools", json={**state.save_payload, "name": "different"}).status_code == 409
    assert state.client.patch(f"/api/media-script-tools/{tool_id}", json={
        "expected_version": 1, **{key: value for key, value in state.save_payload.items() if key != "request_id"}}).status_code == 409


def test_discovery_returns_actual_four_schemas_and_private_recipe(state):
    caps = state.client.get("/api/media-script-tools/capabilities")
    assert caps.status_code == 200
    assert set(caps.json()["schemas"]) == {"save", "update", "revoke", "invoke"}
    assert "script_source" not in caps.json()["schemas"]["invoke"]["properties"]
    listed = state.client.get("/api/media-script-tools").json()
    assert not listed["truncated"] and listed["items"][0]["tool_id"] == state.tool["tool_id"]
    assert "definition" not in listed["items"][0]
    concrete = state.client.get(f"/api/media-script-tools/{state.tool['tool_id']}?version=1")
    assert concrete.status_code == 200 and concrete.json()["definition"]["requested_outputs"] == state.payload["requested_outputs"]
    _other, token = state.members.join(state.members.create_invite_code(), "other recipe owner")
    assert state.client.get(f"/api/media-script-tools/{state.tool['tool_id']}", headers={"X-Local-Member-Token": token}).status_code == 404


def test_invocation_reuses_common_script_job_and_fresh_request_binding(state):
    payload = invocation(state)
    url = state.base_url + f"/media-script-tools/{state.tool['tool_id']}/preview"
    queued = state.client.post(url, json=payload)
    assert queued.status_code == 202, queued.text
    start = state.production.get_task_event(state.workspace_id, f"event_{queued.json()['job_id']}_start")
    assert start.payload["operation"] == "media_script_tool_preview"
    assert start.payload["arguments"]["payload"] == payload
    assert start.payload["arguments"]["_recipe_ref"]["version"] == 1
    assert "script_source" not in start.payload["arguments"]["payload"]
    assert state.runtime.run_once()
    result = state.client.get(queued.json()["result_url"])
    assert result.json()["status"] == "succeeded", result.text
    assert result.json()["result"]["applied"] is False
    query = state.client.get(state.base_url + f"/assistant/edit-requests/{payload['request_id']}?phase=script")
    assert query.json()["submitted_input"] == {**payload, "tool_id": state.tool["tool_id"]}
    assert state.client.post(url, json=payload).json()["job_id"] == queued.json()["job_id"]
    assert state.client.post(url, json={**payload, "parameters": {"color": "yellow"}}).status_code == 409


def test_revocation_after_submit_prevents_dispatch_and_does_not_invalidate_old_png(state):
    payload = invocation(state)
    url = state.base_url + f"/media-script-tools/{state.tool['tool_id']}/preview"
    queued = state.client.post(url, json=payload)
    assert queued.status_code == 202
    before_calls = len(state.calls)
    revoke = state.client.post(f"/api/media-script-tools/{state.tool['tool_id']}/revoke", json={"expected_version": 1})
    assert revoke.status_code == 200 and revoke.json()["active"] is False
    assert not state.runtime.run_once()
    assert len(state.calls) == before_calls
    assert len(state.client.get(state.base_url + "/graphics/sources").json()) == 1
    assert state.client.post(url, json={**payload, "request_id": str(uuid4())}).status_code in {409, 422}


@pytest.mark.parametrize("change", ["version", "parameters", "input_slots", "host_code", "extra_uuid"])
def test_invocation_and_cas_reject_invalid_contract_fields(state, change):
    payload = invocation(state)
    url = state.base_url + f"/media-script-tools/{state.tool['tool_id']}/preview"
    if change == "version":
        payload.pop("tool_version")
    elif change == "parameters":
        payload["parameters"] = {"color": 42}
    elif change == "input_slots":
        payload["input_refs"] = [{"name": "unexpected.png", "source_ref": "artifacts/foreign.png", "expected_artifact_id": "foreign", "expected_sha256": "b" * 64}]
    elif change == "host_code":
        payload["script_source"] = "print('untrusted override')"
    else:
        response = state.client.post(f"/api/media-script-tools/{state.tool['tool_id']}/revoke", json={"expected_version": 1, "request_id": str(uuid4())})
        assert response.status_code == 422
        return
    response = state.client.post(url, json=payload)
    assert response.status_code in {404, 409, 422}, response.text
    assert len(state.calls) == 1


def test_real_recipe_bridge_keeps_leaf_validated_parameter_defaults(state):
    saved = state.client.post(state.base_url + "/media-script-tools", json={**state.save_payload,
        "request_id": str(uuid4()), "parameter_schema": {"type": "object", "properties": {
            "color": {"type": "string", "maxLength": 30}, "gain": {"type": "number", "minimum": 0, "maximum": 2, "default": 1}},
            "required": ["color"], "additionalProperties": False}})
    assert saved.status_code == 200, saved.text
    actor = CreationExecutionActor(state.workspace_id, state.member.member_id)
    call = main.MediaScriptToolPreviewRequest.model_validate({**invocation(state), "tool_version": saved.json()["version"]})
    expanded, _ref = main._instantiate_script_tool(actor, saved.json()["tool_id"], call)
    assert expanded.parameters == {"color": "blue", "gain": 1}
    queued = state.client.post(state.base_url + f"/media-script-tools/{saved.json()['tool_id']}/preview", json=call.model_dump(mode="json", exclude_unset=True))
    assert queued.status_code == 202 and state.runtime.run_once(), queued.text
    start = state.production.get_task_event(state.workspace_id, f"event_{queued.json()['job_id']}_start")
    # Caller omission remains in the sealed invocation; expansion consistently
    # reuses the same Leaf defaults at admission and dispatch.
    assert start.payload["arguments"]["payload"]["parameters"] == {"color": "blue"}


def test_script_unknown_receipt_keeps_earlier_job_from_same_real_turn(state, tmp_path, monkeypatch):
    conversations = ConversationRepository(tmp_path / "related-conversations.sqlite3")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    monkeypatch.setattr(main.settings, "tencentcloud_secret_id", "", raising=False)
    monkeypatch.setattr(main.settings, "tencentcloud_secret_key", "", raising=False)
    chat = conversations.create(state.workspace_id, state.member.member_id, "task", state.task.id)
    raw = "先生成卡片，再查看第二个请求"
    chat = conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    def actual_tools(_settings, **kwargs):
        turn_id = kwargs["turn_id"]
        first = state.client.post(state.base_url + "/media-scripts/preview", json={
            **state.payload, "request_id": str(uuid4()), "assistant_turn_id": turn_id})
        assert first.status_code == 202, first.text
        call = "lost-script-call"
        request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, turn_id, call], separators=(",", ":")))
        return "第二个请求结果未知", {"kind": "edit_request_status", "task_id": state.task.id,
            "request_id": str(request_id), "phase": "script",
            "query_url": state.base_url + f"/assistant/edit-requests/{request_id}?phase=script", "status": "result_unknown",
            "assistant_turn_id": turn_id, "tool_call_id": call, "related_media_job": first.json()}
    monkeypatch.setattr(main, "run_task_turn", actual_tools)
    response = state.client.post(state.base_url + "/assistant/turn", json={
        "conversation_id": chat["id"], "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"],
        "message": raw, "context_stage": "montage", "selected_object_refs": [],
        "expected_timeline_version_id": state.payload["expected_timeline_version_id"],
        "expected_timeline_sequence": state.payload["expected_timeline_sequence"]})
    assert response.status_code == 200, response.text
    assert response.json()["edit_request"]["status"] == "unknown"
    assert response.json()["edit_request"]["phase"] == "script"
    assert response.json()["media_job"]["status"] == "queued"
    assert response.json()["media_job"]["result_kind"] == "graphic_source"
    saved = conversations.get_turn(response.json()["turn_id"])
    assert saved["result"]["response"]["media_job"]["job_id"] == response.json()["media_job"]["job_id"]


def test_saved_program_uses_new_task_without_inheriting_old_timeline_or_input_authorization(state):
    plan = state.production.get_creation_plan(state.workspace_id, state.task.plan_id)
    task = replace(state.task, id="new-program-target", plan_id="new-program-plan", montage_plan_id=None,
                   confirmed_script_id=None, current_timeline_version_id=None, narration_cache_key=None, output_ready=False)
    state.production.confirm_creation_plan(replace(plan, id=task.plan_id, creation_task_id=task.id), task,
                                            verified_owner_user_id=state.member.member_id)
    call = {**invocation(state), "expected_timeline_version_id": None, "expected_timeline_sequence": 0}
    queued = state.client.post(f"/api/creation-tasks/{task.id}/media-script-tools/{state.tool['tool_id']}/preview", json=call)
    assert queued.status_code == 202, queued.text
    assert state.runtime.run_once()
    result = state.client.get(queued.json()["result_url"])
    assert result.json()["status"] == "succeeded", result.text
    record = state.production.get_artifact_record(state.workspace_id, result.json()["result"]["artifact_id"])
    assert record.task_id == task.id and record.input_refs == []
    assert state.production.get_creation_task(state.workspace_id, task.id).current_timeline_version_id is None


def test_same_invocation_request_cannot_be_rebound_to_another_tool_path(state):
    second = state.client.post(state.base_url + "/media-script-tools", json={**state.save_payload, "request_id": str(uuid4())})
    assert second.status_code == 200 and second.json()["tool_id"] != state.tool["tool_id"]
    payload = invocation(state)
    first = state.client.post(state.base_url + f"/media-script-tools/{state.tool['tool_id']}/preview", json=payload)
    assert first.status_code == 202, first.text
    rebound = state.client.post(state.base_url + f"/media-script-tools/{second.json()['tool_id']}/preview", json=payload)
    assert rebound.status_code == 409 and "不同编辑输入" in rebound.text
    assert len(state.calls) == 1
