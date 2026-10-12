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


@pytest.fixture
def assistant_state(state, tmp_path, monkeypatch):
    conversations = ConversationRepository(tmp_path / "tool-conversations.sqlite3")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "assistant_continuation_driver", None)
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    monkeypatch.setattr(main.settings, "video_inbox_path", str(state.production.video_library_path), raising=False)
    monkeypatch.setattr(main.settings, "tencentcloud_secret_id", "", raising=False)
    monkeypatch.setattr(main.settings, "tencentcloud_secret_key", "", raising=False)
    chat = conversations.create(state.workspace_id, state.member.member_id, "task", state.task.id)
    raw = "按明确要求保存或读取这张卡片工具"
    chat = conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    state.conversations, state.chat = conversations, chat
    state.turn_payload = {"conversation_id": chat["id"], "client_request_id": str(uuid4()),
        "expected_draft_version": chat["draft_version"], "message": raw, "context_stage": "montage", "selected_object_refs": [],
        "expected_timeline_version_id": state.payload["expected_timeline_version_id"],
        "expected_timeline_sequence": state.payload["expected_timeline_sequence"]}
    def send(receipt):
        monkeypatch.setattr(main, "run_task_turn", lambda _settings, **kwargs: ("模型声称已保存", receipt(kwargs)))
        return state.client.post(state.base_url + "/assistant/turn", json=state.turn_payload)
    state.send = send
    yield state
    main.stop_assistant_continuation_driver()


def original_tool_input(state):
    return {key: value for key, value in state.save_payload.items() if key != "request_id"}


@pytest.mark.parametrize("action", ["save", "query"])
def test_assistant_tool_creation_receipt_reads_original_v1_not_reported_body_or_head(assistant_state, action):
    state = assistant_state
    updated = state.client.patch(f"/api/media-script-tools/{state.tool['tool_id']}", json={
        **original_tool_input(state), "expected_version": 1, "name": "真实v2"})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    response = state.send(lambda _: {"kind": "media_script_tool_result", "action": action,
        "request_id": state.save_payload["request_id"], "query_url": f"/api/media-script-tools/requests/{state.save_payload['request_id']}",
        "tool": {**updated.json(), "name": "模型伪造名称"}})
    assert response.status_code == 200, response.text
    result = response.json()
    receipt = result["script_tool"]
    assert receipt["status"] == "recorded" and receipt["tool_version"] == 1
    assert receipt["tool"]["name"] == state.tool["name"] and "definition" not in receipt["tool"]
    assert result["edit_preview"] is None and result["media_job"] is None
    assert "原v1" in result["reply"] or "实际v1" in result["reply"]
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == state.payload["expected_timeline_version_id"]
    assert len(state.calls) == 1


@pytest.mark.parametrize("action", ["update", "revoke"])
def test_assistant_tool_mutation_receipt_reads_actual_private_version(assistant_state, action):
    state = assistant_state
    submitted = {"expected_version": 1, **(original_tool_input(state) if action == "update" else {})}
    def actual_write(_):
        url = f"/api/media-script-tools/{state.tool['tool_id']}"
        result = (state.client.patch(url, json=submitted) if action == "update"
            else state.client.post(url + "/revoke", json=submitted))
        assert result.status_code == 200
        return {"kind": "media_script_tool_result", "action": action, "tool": result.json(),
            "expected_version": 1, "submitted_input": submitted}
    response = state.send(actual_write)
    assert response.status_code == 200, response.text
    receipt = response.json()["script_tool"]
    assert receipt["status"] == "recorded" and receipt["expected_version"] == 1
    assert receipt["tool_version"] == (2 if action == "update" else 1)
    assert receipt["submitted_input"] == submitted
    assert receipt["tool"]["active"] is (action == "update")
    assert response.json()["edit_preview"] is None and len(state.calls) == 1


@pytest.mark.parametrize("action", ["update", "revoke"])
def test_unknown_tool_write_preserves_input_without_attributing_changed_head(assistant_state, action):
    state = assistant_state
    submitted = {"expected_version": 1, **(original_tool_input(state) if action == "update" else {})}
    changed = state.client.patch(f"/api/media-script-tools/{state.tool['tool_id']}", json={
        **original_tool_input(state), "expected_version": 1, "name": "另一次写入的v2"})
    assert changed.status_code == 200
    response = state.send(lambda _: {"kind": "media_script_tool_status", "action": action,
        "tool_id": state.tool["tool_id"], "expected_version": 1, "status": "unknown", "submitted_input": submitted,
        "query_url": f"/api/media-script-tools/{state.tool['tool_id']}"})
    assert response.status_code == 200, response.text
    receipt = response.json()["script_tool"]
    assert receipt["status"] == "unknown" and receipt["expected_version"] == 1
    assert receipt["submitted_input"] == submitted and receipt["tool_version"] == 2
    assert receipt["tool"]["name"] == "另一次写入的v2" and receipt["tool"]["active"]
    assert "不能将HEAD变化归因于本次请求" in response.json()["reply"]
    assert main._reconcile_assistant_business_results() == 0
    assert state.client.get(f"/api/media-script-tools/{state.tool['tool_id']}").json()["version"] == 2
    saved = state.conversations.get_turn(response.json()["turn_id"])
    assert saved["result"]["response"]["script_tool"] == receipt and len(state.calls) == 1


def unknown_tool_save(state, kwargs, *, status="unknown"):
    call = "lost-tool-save"
    turn_id = kwargs["turn_id"]
    request_id = str(uuid5(NAMESPACE_URL, json.dumps([state.task.id, turn_id, call], separators=(",", ":"))))
    state.pending_request_id = request_id
    return {"kind": "media_script_tool_status", "action": "save", "task_id": state.task.id,
        "assistant_turn_id": turn_id, "tool_call_id": call, "request_id": request_id,
        "query_url": f"/api/media-script-tools/requests/{request_id}", "status": status,
        "submitted_input": original_tool_input(state)}


@pytest.mark.parametrize("change", ["completed", "stopped", "stale", "revoked", "other_member"])
def test_unknown_tool_save_reopens_and_gets_only_original_receipt(assistant_state, monkeypatch, change):
    state = assistant_state
    if change == "stopped":
        turn, created = state.conversations.create_turn_idempotent(state.chat["id"], state.workspace_id, state.member.member_id,
            state.turn_payload["message"], state.turn_payload["client_request_id"], state.chat["draft_version"], request_context={
                "kind": "task", "task_id": state.task.id, "target_snapshot": {
                    "timeline_version_id": state.payload["expected_timeline_version_id"], "timeline_sequence": state.payload["expected_timeline_sequence"]}})
        assert created
        located, message = main._resolve_assistant_script_tool(state.task.id, state.request, turn["id"], unknown_tool_save(state, {"turn_id": turn["id"]}))
        body = {"turn_id": turn["id"], "script_tool": located.model_dump(mode="json"), "edit_preview": None}
        state.conversations.finish_turn(turn["id"], "stopped", message, result={"kind": "task_turn", "response": body})
    else:
        response = state.send(lambda kwargs: unknown_tool_save(state, kwargs))
        assert response.status_code == 200, response.text
        body = response.json()
    original = body["script_tool"]
    assert original["status"] == "unknown" and original["submitted_input"] == original_tool_input(state)
    assert original["tool"] is None and original["request_id"] == state.pending_request_id
    assert main._reconcile_assistant_business_results() == 0
    late = state.client.post(state.base_url + "/media-script-tools", json={
        **original_tool_input(state), "request_id": state.pending_request_id})
    assert late.status_code == 200 and late.json()["version"] == 1
    updated = state.client.patch(f"/api/media-script-tools/{late.json()['tool_id']}", json={
        **original_tool_input(state), "expected_version": 1, "name": "后来更新的v2"})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    turn_id = body["turn_id"]
    if change == "stale":
        version = state.production.get_timeline_version(state.workspace_id, state.payload["expected_timeline_version_id"])
        state.production.create_timeline_version(replace(version, id="tool-later-timeline", parent_version_id=version.id, sequence=version.sequence + 1))
    elif change == "revoked":
        state.members.remove_member(state.member.member_id)
    elif change == "other_member":
        other = state.members.add_member("different chat owner")
        with state.conversations._connect() as conn:
            conn.execute("UPDATE assistant_conversations SET member_id=? WHERE id=?", (other.member_id, state.chat["id"]))
    reopened = ConversationRepository(state.conversations.database_path)
    monkeypatch.setattr(main, "conversation_repo", reopened)
    monkeypatch.setattr(main, "run_task_turn", lambda *_args, **_kwargs: pytest.fail("receipt recovery must never call a model"))
    monkeypatch.setattr(main, "save_media_script_tool", lambda *_args, **_kwargs: pytest.fail("receipt recovery must never POST save"))
    before = reopened.get_turn(turn_id)
    job_count = len(state.production.list_job_runs(state.workspace_id, state.task.id))
    main.start_assistant_continuation_driver()
    main.stop_assistant_continuation_driver()
    after = reopened.get_turn(turn_id)
    assert after["status"] == before["status"] and len(state.calls) == 1
    assert len(state.production.list_job_runs(state.workspace_id, state.task.id)) == job_count
    assert after["result"]["response"]["edit_preview"] is None
    if change in {"revoked", "other_member"}:
        assert after == before
    else:
        event = after["result"]["continuation"]
        assert event["status"] == {"completed": "recorded", "stopped": "stopped", "stale": "stale"}[change]
        assert event["script_tool"]["tool_version"] == 1 and not event["actionable"]
        assert "definition" not in event["script_tool"]["tool"]
        if change == "stopped":
            assert after["assistant_text"] == before["assistant_text"]
            assert after["result"]["response"]["script_tool"]["status"] == "unknown"
        else:
            assert after["result"]["response"]["script_tool"]["tool_version"] == 1
    main.start_assistant_continuation_driver()
    main.stop_assistant_continuation_driver()
    assert reopened.get_turn(turn_id) == after
    assert len(reopened.list_turns(state.chat["id"])) == 1


@pytest.mark.parametrize("status", ["rejected", "not_submitted"])
def test_terminal_tool_save_failure_keeps_original_locator_without_registering(assistant_state, status):
    state = assistant_state
    count = len(state.production.list_job_runs(state.workspace_id, state.task.id))
    response = state.send(lambda kwargs: unknown_tool_save(state, kwargs, status=status))
    assert response.status_code == 200, response.text
    receipt = response.json()["script_tool"]
    assert receipt["status"] == status and receipt["submitted_input"] == original_tool_input(state)
    assert main._reconcile_assistant_business_results() == 0
    assert len(state.production.list_job_runs(state.workspace_id, state.task.id)) == count


@pytest.mark.parametrize("invalid", ["uuid", "turn", "locator"])
def test_unknown_tool_save_refuses_unbound_source(assistant_state, invalid):
    state = assistant_state
    def forged(kwargs):
        value = unknown_tool_save(state, kwargs)
        value[{"uuid": "request_id", "turn": "assistant_turn_id", "locator": "query_url"}[invalid]] = str(uuid4())
        return value
    response = state.send(forged)
    assert response.status_code == 502, response.text
    assert not any(event.event_type == "assistant_script_tool_located"
        for event in state.production.list_task_events(state.workspace_id, state.task.id))


@pytest.mark.parametrize("invalid", ["missing_input", "expected_mismatch"])
def test_unknown_tool_update_requires_original_cas_input(assistant_state, invalid):
    state = assistant_state
    body = {"kind": "media_script_tool_status", "action": "update", "tool_id": state.tool["tool_id"],
        "expected_version": 1, "status": "unknown", "query_url": f"/api/media-script-tools/{state.tool['tool_id']}"}
    if invalid == "expected_mismatch":
        body["submitted_input"] = {**original_tool_input(state), "expected_version": 2}
    response = state.send(lambda _: body)
    assert response.status_code == 502, response.text
    assert state.client.get(f"/api/media-script-tools/{state.tool['tool_id']}").json()["version"] == 1


def test_metadata_receipt_keeps_earlier_real_media_job_from_same_turn(assistant_state):
    state = assistant_state
    def actual_tools(kwargs):
        first = state.client.post(state.base_url + "/media-scripts/preview", json={
            **state.payload, "request_id": str(uuid4()), "assistant_turn_id": kwargs["turn_id"]})
        assert first.status_code == 202, first.text
        return {**unknown_tool_save(state, kwargs), "related_media_job": first.json()}
    response = state.send(actual_tools)
    assert response.status_code == 200, response.text
    assert response.json()["script_tool"]["status"] == "unknown"
    assert response.json()["media_job"]["status"] == "queued"
    assert response.json()["media_job"]["result_kind"] == "graphic_source"
    saved = state.conversations.get_turn(response.json()["turn_id"])
    assert saved["result"]["response"]["media_job"]["job_id"] == response.json()["media_job"]["job_id"]
