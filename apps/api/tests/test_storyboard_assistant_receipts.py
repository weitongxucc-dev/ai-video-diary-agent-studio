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
from test_storyboard_regeneration import state as state, inputs, run  # noqa: F401


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


def _metadata_receipt_state(tmp_path, monkeypatch):
    """Declared original-request facts only; no registration, scheduling or media."""
    from dataclasses import asdict
    from fastapi.testclient import TestClient
    from app.creation_execution import CreationExecutionActor
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
    from app.creation.script_windows import write_script_sections
    from app.local_portrait import LocalPortrait
    from app.subtitle_tracks import OpenAICompatibleSubtitleTranslationProvider, UnavailableSubtitleTranslationProvider
    from test_metadata_edit_use_case import metadata_state
    from test_production_foundation import edit_adoption_no_execution

    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(LocalPortrait, "process", forbidden)
    monkeypatch.setattr(OpenAICompatibleSubtitleTranslationProvider, "translate", forbidden)
    monkeypatch.setattr(UnavailableSubtitleTranslationProvider, "translate", forbidden)
    state = metadata_state(tmp_path)
    script = CreationTaskScript("fixed-script", "ws", state.task.id, "declared-run", "declared-topic",
        "声明的已确认文案", "zh-CN", [CreationTaskScriptSection("section", "段落", "原配音", "素材画面", [])],
        selection_status="confirmed")
    state.task = replace(state.task, confirmed_script_id=script.id)
    header = asdict(script)
    sections = header.pop("sections")
    header["_sections_storage"] = "rows"
    with state.repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?",
                     (json.dumps(asdict(state.task), default=str), state.task.id))
        conn.execute("INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
            (script.id, "ws", state.task.id, script.run_id, json.dumps(header, default=str), script.updated_at.isoformat()))
        write_script_sections(conn, "ws", script.id, sections)
    monkeypatch.setattr(main, "production_repo", state.repo)
    monkeypatch.setattr(main, "artifact_store", state.store)
    monkeypatch.setattr(main, "local_members_service", state.members)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "ws"))
    monkeypatch.setattr(main, "settings", replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path / "outputs")))
    # The isolated local workspace uses the real roster/token identity path.
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "ws")
    state.actor = CreationExecutionActor("ws", state.member.member_id)
    state.control = CreationExecutionControl(state.repo, None, None, lambda _: "ready")
    monkeypatch.setattr(main, "_creation_execution_control", lambda: state.control)
    monkeypatch.setattr(main, "_require_permit_actor", lambda *_: state.actor)
    state.client = TestClient(main.app, headers={"X-Local-Member-Token": state.token})
    state.base_url = "/api/creation-tasks/" + state.task.id
    state.url = state.base_url + "/montage/plan"
    state.builds = []
    state.metadata_receipts = True
    return state


@pytest.fixture
def chat(request, tmp_path, monkeypatch):
    from assistant_turn_control_fixtures import isolate_assistant_turn_control
    isolate_assistant_turn_control(monkeypatch, request)
    parameters = getattr(getattr(request.node, "callspec", None), "params", {})
    metadata = (request.node.originalname == "test_http_turn_rereads_original_request_and_persists_dedicated_receipt"
                and parameters.get("mode") == "forged_status") or (
                request.node.originalname == "test_http_unknown_without_server_record_keeps_verified_original_UUID"
                and parameters.get("kind") == "storyboard_regeneration_unknown")
    state = _metadata_receipt_state(tmp_path, monkeypatch) if metadata else request.getfixturevalue("state")
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "ws")
    state.conversations = ConversationRepository(state.repo.database_path)
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
    task = state.repo.task_records.get_creation_task("ws", state.task.id)
    version = state.repo.timeline_records.get_timeline_version("ws", task.current_timeline_version_id)
    payload = {"conversation_id": current["id"], "message": message, "context_stage": "montage",
        "client_request_id": str(uuid4()), "expected_draft_version": draft["draft_version"],
        "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence}
    return state.client.post(state.base_url + "/assistant/turn", json=payload), payload


@pytest.mark.parametrize("stale_field", ["id", "sequence"])
@pytest.mark.parametrize("stream", [False, True])
def test_task_send_stale_timeline_returns_machine_code_before_creating_turn(chat, monkeypatch, stale_field, stream):
    state = chat
    current = state.conversations.get(state.chat["id"], "ws", state.member.member_id)
    message = "保留这条尚未发送的修改要求"
    draft = state.conversations.save_draft(current["id"], "ws", state.member.member_id, message, current["draft_version"])
    version = state.repo.timeline_records.get_timeline_version("ws", state.repo.task_records.get_creation_task("ws", state.task.id).current_timeline_version_id)
    monkeypatch.setattr(main, "run_task_turn", lambda *_a, **_k: pytest.fail("stale task must not invoke the assistant"))
    payload = {"conversation_id": current["id"], "message": message, "context_stage": "montage",
        "client_request_id": str(uuid4()), "expected_draft_version": draft["draft_version"],
        "expected_timeline_version_id": "old-timeline" if stale_field == "id" else version.id,
        "expected_timeline_sequence": version.sequence - 1 if stale_field == "sequence" else version.sequence}
    response = state.client.post(state.base_url + "/assistant/turn", json=payload,
        headers={"Accept": "text/event-stream"} if stream else {})
    if stream:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [event for event in events if event["type"] == "error"] == [{"type": "error", "status": 409,
            "message": "当前任务版本已变化，请重新查看选中对象后发送。",
            "failure": {"code": "creation_task_timeline_version_conflict"}}]
        assert not any(event["type"] in {"turn_started", "result"} for event in events)
    else:
        assert response.status_code == 409
        assert response.json() == {"detail": {"error": "creation_task_timeline_version_conflict",
            "message": "当前任务版本已变化，请重新查看选中对象后发送。"}}
    assert state.conversations.request_context(payload["client_request_id"], current["id"], "ws", state.member.member_id) is None
    after = state.conversations.get(current["id"], "ws", state.member.member_id)
    assert (after["draft_text"], after["draft_version"]) == (message, draft["draft_version"])
    with state.conversations._connect() as conn:
        assert conn.execute("SELECT count(*) FROM assistant_conversation_turns WHERE conversation_id=?", (current["id"],)).fetchone()[0] == 0


def test_stream_other_http_detail_does_not_expose_arbitrary_fields(chat):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    app = FastAPI()
    @app.get("/stream")
    def stream():
        def execute(_publish):
            raise HTTPException(status_code=409, detail={"error": "other_conflict",
                "message": "private detail", "private": "must not escape"})
        return main._assistant_stream_transport().response(execute, workspace_id="ws")
    response = TestClient(app).get("/stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event for event in events if event["type"] == "error"] == [{"type": "error", "status": 409,
        "message": "对话未能完成，请查看当前任务状态。"}]
    assert "private" not in response.text


def request_id(task_id, turn_id):
    return str(uuid5(NAMESPACE_URL, json.dumps([task_id, turn_id, "storyboard-call"],
        ensure_ascii=False, separators=(",", ":"))))


def submitted(state, turn_id):
    payload = {**inputs(state), "assistant_turn_id": turn_id, "request_id": request_id(state.task.id, turn_id)}
    if getattr(state, "metadata_receipts", False):
        from app.creation.contracts import JobRun, TaskEvent
        # Seed an already-registered original request. This is not submission/execution evidence.
        job = JobRun("job_" + uuid4().hex, "ws", state.task.id, "creation_task_execution",
                     f"storyboard-regeneration:{state.task.id}:{payload['request_id']}", payload["input_digest"])
        event = TaskEvent(f"event_{job.id}_start", "ws", state.task.id, job.id,
            "storyboard_regeneration_requested", {"operation": "regenerate_storyboard", "request_inputs": payload,
            "script_id": payload["script_id"], "narration_cache_key": state.task.narration_cache_key,
            **state.actor.identity_payload()})
        with state.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state.repo.job_records.create_job_run(job, connection=conn)
            state.repo.execution_records.append_task_event(event, connection=conn)
        response = state.client.get(state.url + "/requests/" + payload["request_id"])
        assert response.status_code == 200, response.text
        return response.json()
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
    if getattr(state, "metadata_receipts", False):
        with state.repo._connect() as conn:
            jobs = conn.execute("SELECT id FROM job_runs WHERE workspace_id=? AND task_id=?", ("ws", state.task.id)).fetchall()
        assert [row["id"] for row in jobs] == [result["receipt"]["job_id"]]


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
    assert not state.builds and not state.repo.edit_records.list_edit_operations("ws", state.task.id)
    if getattr(state, "metadata_receipts", False):
        with state.repo._connect() as conn:
            assert conn.execute("SELECT count(*) FROM job_runs WHERE workspace_id=? AND task_id=?", ("ws", state.task.id)).fetchone()[0] == 0
        saved = state.conversations.get_turn(response.json()["turn_id"])["result"]
        assert saved["storyboard_regeneration"] == receipt == saved["response"]["storyboard_regeneration"]


def test_query_after_undo_returns_original_version_without_adopting_it_again(chat, monkeypatch):
    state = chat
    first = state.client.post(state.url, json=inputs(state)).json()
    run(state)
    original = state.client.get(first["result_url"]).json()
    version = original["result"]
    assert state.client.post(state.base_url + "/timeline/undo", json={
        "expected_timeline_version_id": version["timeline_version_id"],
        "expected_timeline_sequence": version["timeline_sequence"]}).status_code == 200
    before = state.repo.task_records.get_creation_task("ws", state.task.id).current_timeline_version_id
    monkeypatch.setattr(main, "run_task_turn", lambda *args, **kwargs: project_business_result(completion(
        {"kind": "storyboard_regeneration_receipt", "receipt": original}, query=True)))
    response, _ = send(state, "查询刚才故事板重算的原结果")
    assert response.status_code == 200, response.text
    value = response.json()["storyboard_regeneration"]
    assert value["action"] == "query" and value["receipt"] == original
    assert state.repo.task_records.get_creation_task("ws", state.task.id).current_timeline_version_id == before
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

