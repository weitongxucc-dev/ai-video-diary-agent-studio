"""Deterministic continuation with isolated repositories and explicit media substitutes."""
import json
import time
from uuid import NAMESPACE_URL, uuid4, uuid5
from dataclasses import replace
from types import SimpleNamespace

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state, _granted_domain_preview_adapter  # noqa: F401


@pytest.fixture
def state(material_edit_state, tmp_path, monkeypatch, request):
    from assistant_turn_control_fixtures import isolate_assistant_turn_control
    isolate_assistant_turn_control(monkeypatch, request)
    state = material_edit_state
    state.original_jobs = state.production.job_records.list_job_runs(state.workspace_id, state.task.id)
    monkeypatch.setattr(main, "settings", replace(main.settings,
        video_inbox_path=str(state.production.video_library_path),
        tencentcloud_secret_id="", tencentcloud_secret_key=""))
    conversations = ConversationRepository(state.production.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "assistant_continuation_driver", None)
    monkeypatch.setattr(main, "_verify_creation_identity", lambda actor:
        "ready" if actor.member_id == state.member.member_id and main.find_member(state.members, actor.member_id) else "unauthorized")
    conversation = conversations.create(state.workspace_id, state.member.member_id, "task", state.task.id)
    raw = "把当前镜头裁剪到左边，保留旁白"
    conversation = conversations.save_draft(conversation["id"], state.workspace_id, state.member.member_id, raw, conversation["draft_version"])
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    turn, _ = conversations.create_turn_idempotent(conversation["id"], state.workspace_id, state.member.member_id,
        raw, str(uuid4()), conversation["draft_version"], request_context={"kind": "task", "task_id": task.id,
        "target_snapshot": {"timeline_version_id": version.id, "timeline_sequence": version.sequence}})
    state.raw_post = state.client.post
    runtime = _granted_domain_preview_adapter(state, monkeypatch)
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    runtime.task_authorizations = TaskAuthorizationUseCase(state.production,
        lambda identity: main._verify_creation_identity(
            CreationExecutionActor.from_event(identity["workspace_id"], identity)))
    runtime.registrations["creation_job"].owner.task_media.on_settled = main._wake_assistant_continuations
    monkeypatch.setattr(main, "device_runtime", runtime)
    monkeypatch.setattr(main, "run_task_turn", lambda *_args, **_kwargs: pytest.fail("deterministic continuation must not call a model"))
    state.conversations, state.conversation, state.turn, state.runtime, state.version = conversations, conversation, turn, runtime, version
    state.submit = lambda: state.raw_post(state.base_url + "/edits/exact/preview", json={
        "operation_type": "ChangeCrop", "clip_id": state.clip.id, "fit_mode": "fill", "crop_x": 0.2, "crop_y": 0.5,
        "request_id": str(uuid4()), "assistant_turn_id": turn["id"],
        "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence})
    def finish(status="completed", **response):
        conversations.finish_turn(turn["id"], status, "已安排预览", result={"kind": "task_turn", "edit_preview": False,
            "response": {"reply": "已安排预览", "provider": "fixture", "model": "fixture", "turn_id": turn["id"], **response}})
    state.finish = finish
    yield state
    main.stop_assistant_continuation_driver()
    runtime.scheduler.close()


def current(state):
    return state.conversations.get_turn(state.turn["id"])


def await_projection(state, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        turn = current(state)
        if predicate(turn):
            return turn
        time.sleep(0.02)
    pytest.fail("lifecycle driver did not project the persisted result")


@pytest.mark.parametrize("decision", ["adopt", "reject"])
def test_lifecycle_driver_completes_preview_and_projects_user_decision_once(state, decision):
    queued = state.submit()
    assert queued.status_code == 202, queued.text
    state.finish(media_job=queued.json())
    main.start_assistant_continuation_driver()
    assert state.runtime.run_once()
    turn = await_projection(state, lambda item: item["result"].get("edit_preview") is True)
    proposal = turn["result"]["response"]["edit_preview"]["proposal"]
    assert turn["user_message"] == state.turn["user_message"]
    assert len(state.conversations.list_turns(state.conversation["id"])) == 1
    if decision == "adopt":
        result = state.raw_post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": str(uuid4())})
        assert result.status_code == 200, result.text
        outcome = await_projection(state, lambda item: item["result"].get("continuation", {}).get("status") == "confirmed")
        assert outcome["result"]["continuation"]["result_version_id"] == result.json()["operation"]["result_version_id"]
        assert outcome["result"]["continuation"]["waiting_reason"] == "当前草稿已保存；这次修改尚未反映到成片。"
    else:
        # A genuine new user decision goes through the existing trusted
        # control-turn contract; the driver never invents this instruction.
        chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
        chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id,
                                             "取消这个修改提案", chat["draft_version"])
        decision_turn, _ = state.conversations.create_turn_idempotent(chat["id"], state.workspace_id, state.member.member_id,
            "取消这个修改提案", str(uuid4()), chat["draft_version"], request_context={"kind": "task", "task_id": state.task.id,
            "stage": "montage", "selected_object_refs": [], "playhead_ms": None,
            "target_snapshot": {"timeline_version_id": state.version.id, "timeline_sequence": state.version.sequence}})
        result = state.raw_post(state.base_url + f"/edits/{proposal['id']}/reject", json={
            "assistant_turn_id": decision_turn["id"], "expected_timeline_version_id": state.version.id,
            "expected_timeline_sequence": state.version.sequence})
        assert result.status_code == 200, result.text
        outcome = await_projection(state, lambda item: item["result"].get("continuation", {}).get("status") == "rejected")
        assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == state.version.id
    assert outcome["result"]["edit_preview"] is False
    events = outcome["events"]
    assert main._reconcile_assistant_business_results() == 0
    assert current(state)["events"] == events
    assert not any(job.kind == "creation_task_execution" for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id))


def test_startup_recovers_completed_job_without_replaying_it(state):
    queued = state.submit()
    assert queued.status_code == 202
    state.finish()
    assert state.runtime.run_once()
    assert current(state)["result"]["edit_preview"] is False
    count = len(state.preview_inputs)
    main.start_assistant_continuation_driver()
    assert current(state)["result"]["edit_preview"] is True
    main.stop_assistant_continuation_driver()
    before = current(state)
    main.start_assistant_continuation_driver()
    assert current(state)["events"] == before["events"]
    assert len(state.preview_inputs) == count


@pytest.mark.parametrize("change", ["stopped", "stale", "revoked", "other_member"])
def test_stopped_old_or_foreign_target_does_not_resume_an_actionable_turn(state, change):
    queued = state.submit()
    assert queued.status_code == 202
    state.finish("stopped" if change == "stopped" else "completed")
    assert state.runtime.run_once()
    if change == "stale":
        state.production.timeline_records.create_timeline_version(replace(state.version, id="concurrent-version", parent_version_id=state.version.id, sequence=state.version.sequence + 1))
    elif change == "revoked":
        state.members.remove_member(state.member.member_id)
    elif change == "other_member":
        other = state.members.add_member("another conversation owner")
        with state.conversations._connect() as conn:
            conn.execute("UPDATE assistant_conversations SET member_id=? WHERE id=?", (other.member_id, state.conversation["id"]))
    before = current(state)
    main.start_assistant_continuation_driver()
    after = current(state)
    assert not after["result"]["edit_preview"]
    assert after["status"] == before["status"]
    if change in {"revoked", "other_member", "stopped"}:
        assert after["assistant_text"] == before["assistant_text"]
    if change in {"stopped", "stale"}:
        assert after["result"]["continuation"]["status"] == change
    assert state.production.job_records.get_job_run(state.workspace_id, queued.json()["job_id"]).status == "succeeded"


def test_failure_is_projected_without_retry_or_fabricated_proposal(state, monkeypatch):
    queued = state.submit()
    assert queued.status_code == 202
    media = state.runtime.registrations["creation_job"].owner.task_media
    monkeypatch.setattr(media.clip_preview, "execute", lambda *_: (_ for _ in ()).throw(ValueError("fixture failure")))
    state.finish()
    assert state.runtime.run_once()
    main.start_assistant_continuation_driver()
    after = current(state)
    assert after["result"]["continuation"]["status"] == "failed"
    assert not after["result"]["edit_preview"]
    assert after["result"]["response"]["media_job"]["status"] == "failed"
    jobs = state.production.job_records.list_job_runs(state.workspace_id, state.task.id)
    assert [job for job in jobs if job.id != queued.json()["job_id"]] == state.original_jobs
    assert [job.id for job in jobs if job.id not in {original.id for original in state.original_jobs}] == [queued.json()["job_id"]]


def test_locator_unknown_is_bound_then_late_rejection_is_read_without_post(state):
    call_id = "fixture-tool-call"
    request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, state.turn["id"], call_id], separators=(",", ":")))
    details = {"kind": "edit_request_status", "task_id": state.task.id, "request_id": str(request_id),
        "query_url": state.base_url + f"/assistant/edit-requests/{request_id}?phase=preview", "status": "result_unknown",
        "assistant_turn_id": state.turn["id"], "tool_call_id": call_id}
    located = main._resolve_assistant_edit_request(state.task.id, state.request, state.turn["id"], details)
    assert located.status == "unknown" and located.result is None
    state.finish(edit_request=located.model_dump(mode="json"))
    main.start_assistant_continuation_driver()
    assert current(state)["result"]["response"]["edit_request"]["status"] == "unknown"
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    event_id, fresh = main._task_edit_receipt_http_adapter().claim_edit_request(task, state.member.member_id, request_id, "preview", {"fixture": "late rejection"})
    assert fresh
    main._task_edit_receipt_http_adapter().save_edit_request_result(task, event_id, main.JSONResponse(status_code=409, content={"detail": "original turn ended"}))
    after = await_projection(state, lambda item: item["result"]["response"]["edit_request"]["status"] == "rejected")
    assert not after["result"]["edit_preview"]
    assert state.production.job_records.list_job_runs(state.workspace_id, state.task.id) == state.original_jobs
    assert len(state.conversations.list_turns(state.conversation["id"])) == 1


def test_fabricated_locator_is_rejected(state):
    request_id = uuid4()
    with pytest.raises(main.HTTPException, match="来源不可核验"):
        main._resolve_assistant_edit_request(state.task.id, state.request, state.turn["id"], {
            "request_id": str(request_id), "query_url": state.base_url + f"/assistant/edit-requests/{request_id}?phase=preview",
            "status": "result_unknown", "assistant_turn_id": state.turn["id"], "tool_call_id": "forged"})


def test_assistant_dispatch_persists_unknown_request_location_without_502(state, monkeypatch):
    state.finish()
    raw = "查看当前裁剪请求的状态"
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    def trusted_tool_receipt(_settings, **kwargs):
        turn_id, call_id = kwargs["turn_id"], "actual-tool-call"
        request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, turn_id, call_id], separators=(",", ":")))
        return "结果未知", {"kind": "edit_request_status", "task_id": state.task.id,
            "request_id": str(request_id), "query_url": state.base_url + f"/assistant/edit-requests/{request_id}?phase=preview",
            "status": "result_unknown", "assistant_turn_id": turn_id, "tool_call_id": call_id}
    monkeypatch.setattr(main, "run_task_turn", trusted_tool_receipt)
    response = state.raw_post(state.base_url + "/assistant/turn", json={
        "conversation_id": chat["id"], "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"],
        "expected_reference_version":chat["reference_version"],
        "message": raw, "context_stage": "montage", "selected_object_refs": [f"clip:{state.clip.id}"],
        "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence})
    assert response.status_code == 200, response.text
    assert response.json()["edit_request"]["status"] == "unknown"
    assert response.json()["edit_preview"] is None and response.json()["media_job"] is None
    saved = state.conversations.get_turn(response.json()["turn_id"])
    assert saved["result"]["response"]["edit_request"] == response.json()["edit_request"]
    assert state.production.job_records.list_job_runs(state.workspace_id, state.task.id) == state.original_jobs


def test_script_view_dispatches_real_typed_proposal_by_receipt_shape(state, monkeypatch):
    # Isolate receipt dispatch from view eligibility; the proposal itself is
    # produced and reread through the real existing task/version API.
    state.finish()
    preview = state.raw_post(state.base_url + "/edits/exact/preview?draft=true", json={
        "operation_type": "ChangeCrop", "clip_id": state.clip.id, "fit_mode": "fill", "crop_x": 0.2, "crop_y": 0.5})
    assert preview.status_code == 200, preview.text
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    raw = "查看这个实际镜头修改提案"
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    monkeypatch.setattr(main, "_assistant_turn_scope", lambda *_: ("script", []))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    monkeypatch.setattr(main, "run_task_turn", lambda *_args, **_kwargs: ("已准备提案", preview.json()))
    response = state.raw_post(state.base_url + "/assistant/turn", json={
        "conversation_id": chat["id"], "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"],
        "message": raw, "context_stage": "script", "selected_object_refs": [],
        "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence})
    assert response.status_code == 200, response.text
    assert response.json()["edit_preview"]["proposal"]["id"] == preview.json()["proposal"]["id"]
    assert response.json()["script_draft"] is None


def test_submitted_input_replays_original_binding_and_filters_the_caller(state):
    payload = {"operation_type": "ChangeCrop", "clip_id": state.clip.id, "fit_mode": "fill", "crop_x": 0.2, "crop_y": 0.5,
        "request_id": str(uuid4()), "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence}
    preview = state.raw_post(state.base_url + "/edits/exact/preview?draft=true", json=payload)
    assert preview.status_code == 200, preview.text
    query = state.base_url + f"/assistant/edit-requests/{payload['request_id']}?phase=preview"
    submitted = state.client.get(query).json()["submitted_input"]
    assert submitted == payload
    assert not {"member_id", "actor_identity", "authorization", "path"} & set(submitted)
    apply = {"request_id": str(uuid4())}
    proposal = preview.json()["proposal"]
    confirmed = state.raw_post(state.base_url + f"/edits/{proposal['id']}/confirm", json=apply)
    assert confirmed.status_code == 200, confirmed.text
    original = state.client.get(state.base_url + f"/assistant/edit-requests/{apply['request_id']}?phase=apply").json()["submitted_input"]
    assert original["proposal_id"] == proposal["id"] and original["request"] == apply
    assert original["base_version_id"] == state.version.id
    assert state.raw_post(state.base_url + "/edits/exact/preview?draft=true", json=submitted).json() == preview.json()
    other, token = state.members.join(state.members.create_invite_code(), "another reader")
    assert other.member_id != state.member.member_id
    assert state.client.get(query, headers={"X-Local-Member-Token": token}).status_code in {403, 404}
