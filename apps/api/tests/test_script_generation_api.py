"""Real HTTP identity and original request persistence, with no Agent/model run."""
from uuid import uuid4
from dataclasses import replace
from threading import Event
import time
import json

import httpx

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.conversation_repository import ConversationRepository
from app.creation.script_generation_routes import build_script_generation_router
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.creation_execution import CreationExecutionActor
from app.topics import OpenAICompatibleCreationTaskScriptGenerator
from test_creation_task_script_grounding import FACT, _review_tool_response
from test_creation_task_script_grounding import api_state, script_domain_state  # noqa: F401


def test_generation_http_acceptance_query_and_early_stop_keep_one_original_run(api_state, monkeypatch):
    conversations = ConversationRepository(api_state.repository.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    # This representative covers the interactive identity/owner boundary;
    # automation task scheduling retains its own existing tests.
    monkeypatch.setattr(main, "_require_automation_task_open", lambda *args: None)
    dispatched = []
    app = FastAPI()
    app.include_router(build_script_generation_router(
        accept=main._accept_script_generation, read=main._read_script_generation, execute=dispatched.append))
    app.add_api_route("/api/assistant/conversations/{conversation_id}/stop",
                      main.stop_assistant_conversation_turn, methods=["POST"])
    client = TestClient(app, headers=dict(api_state.client.headers))
    chat = conversations.current_or_create("local", api_state.owner.member_id, "task", "task")
    with conversations._connect() as conn:
        conn.execute("UPDATE assistant_conversations SET draft_text=?,draft_version=7 WHERE id=?",
                     ("尚未发送的聊天草稿", chat["id"]))
    request_id = str(uuid4())
    url = "/api/creation-tasks/task/scripts/generate"
    accepted = client.post(url, json={"request_id": request_id})
    assert accepted.status_code == 202, accepted.text
    original = accepted.json()
    assert original["requested_effect"] == "draft" and original["script_id"] is None
    assert original["conversation_id"] == chat["id"]
    assert client.post(url, json={"request_id": request_id}).json() == original
    assert len(dispatched) == 1
    with api_state.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs").fetchone()[0] == 1
    stored_chat = conversations.get(chat["id"], "local", api_state.owner.member_id)
    assert (stored_chat["draft_text"], stored_chat["draft_version"]) == ("尚未发送的聊天草稿", 7)
    assert conversations.get_turn(original["turn_id"])["user_message"] == ""
    query = f"/api/creation-tasks/task/scripts/generation-requests/{request_id}"
    assert TestClient(app).get(query).status_code in {403, 404}
    assert client.get(query.replace(request_id, str(uuid4()))).status_code == 404
    with monkeypatch.context() as unavailable:
        unavailable.setattr(main, "_verify_creation_identity", lambda actor: "unknown")
        assert client.get(query).json() == original
    stop = client.post(f"/api/assistant/conversations/{chat['id']}/stop",
                       json={"turn_id": original["turn_id"]})
    assert stop.status_code == 200, stop.text
    assert stop.json()["status"] == "stopping"
    assert client.get(query).json()["stop_requested"] is True
    intent = main._script_generation_intent("local")
    def forbidden_model(record):
        raise AssertionError("early stopped request must not enter Agent")
    settled = intent.execute(*dispatched[0], run_agent=forbidden_model, script_case=None)
    assert settled["status"] == "stopped" and settled["run_status"] == "failed"
    assert client.get(query).json()["turn_id"] == original["turn_id"]
    assert api_state.repository.get_creation_task("local", "task").confirmed_script_id is None


def test_generation_main_host_uses_request_scope_without_constructing_media_execution(api_state, monkeypatch):
    conversations = ConversationRepository(api_state.repository.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "device_runtime", None)
    monkeypatch.setattr(main, "settings", replace(api_state.settings, database_path=str(api_state.repository.database_path)))
    def forbidden():
        raise AssertionError("draft generation must not require media execution assembly")
    for name in ("_creation_execution_control", "_segment_production_use_case", "_native_task_operations_case",
                 "_timeline_restore_use_case", "_goal_service"):
        monkeypatch.setattr(main, name, forbidden)
    from app.creation.contracts import JobRun, TaskEvent
    from app.creation.execution_control import CreationExecutionControl
    from types import SimpleNamespace
    execution = main._new_creation_execution(api_state.repository)
    monkeypatch.setattr(main, "creation_execution", execution)
    original_job = api_state.repository.create_job_run(JobRun(
        "persisted-execution", "local", "task", "creation_task_execution", "original", "a" * 64))
    api_state.repository.claim_job_run("local", original_job.id)
    original_job = api_state.repository.finish_job_run("local", original_job.id, status="failed",
        failure_reason="原制作失败", impact="尚未完成制作", repair_action="检查原作业后恢复")
    api_state.repository.append_task_event(TaskEvent(
        "original-stage", "local", "task", original_job.id, "creation_execution_stage",
        {"stage": "narration", "status": "failed", "reason": "原制作失败"}))
    case = main._script_generation_intent("local")
    member_id = api_state.owner.member_id
    record, _ = case.start("local", "task", str(uuid4()), CreationExecutionActor("local", member_id))
    business = main._task_business(generation_case=case)
    script = business.script_case(CreationExecutionActor("local", member_id))
    assert script.read_generation_request == case.read_generation_request
    assert script.bind_generation_result == case.bind_generation_result
    assert script.read_generation_result == case.read
    binding = dict(workspace_id="local", task_id="task", member_id=member_id,
                   conversation_id=record["conversation_id"], turn_id=record["turn_id"],
                   intent_kind="script_generation", generation_request_id=record["request_id"])
    host = TaskBusinessAdapter(business)
    options = dict(call_id="call", tool_call_id="tool", source_binding=binding,
                   cancel_event=Event(), deadline_monotonic=time.monotonic() + 10)
    memory = host("read_creator_memory", {}, **options)
    assert memory["ok"] and memory["value"]["revision"] == 0
    execution_facts = host("read_creation_execution", {}, **options)
    assert execution_facts["ok"], execution_facts
    summary = execution_facts["value"]
    assert summary["job"]["id"] == original_job.id and summary["job"]["status"] == "failed"
    assert summary["current_stage"] == "narration"
    assert summary["stages"][0]["reason"] == "原制作失败"
    assert "video_ready" not in summary and "cover_status" not in summary
    # The existing full status still adds media facts to the same persisted summary.
    full = CreationExecutionControl(api_state.repository, execution,
        SimpleNamespace(valid=lambda *args, **kwargs: True), forbidden).status("local", "task", verify_media=False)
    from fastapi.encoders import jsonable_encoder
    assert {key: jsonable_encoder(full[key]) for key in summary} == summary
    assert full["video_ready"] is True and "visual_decisions" in full
    assert len(api_state.repository.list_job_runs("local", "task")) == 1
    rejected = host("confirm_saved_script", {"script_id": "unrelated", "payload": {}}, **options)
    assert not rejected["ok"] and rejected["failure"]["status_code"] == 403
    assert api_state.repository.get_creation_task("local", "task").confirmed_script_id is None


def test_button_application_host_windows_finish_one_run_as_draft(api_state, monkeypatch):
    from app.assistant import script_generation_runner as runner
    conversations = ConversationRepository(api_state.repository.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "device_runtime", None)
    monkeypatch.setattr(main, "settings", replace(api_state.settings, database_path=str(api_state.repository.database_path)))
    monkeypatch.setattr(main, "_require_automation_task_open", lambda *args: None)
    def review(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        return _review_tool_response({"sections": [{"index": part["index"], "supports": [{
            "candidate_quote_id": part["content"][0]["quote_id"],
            "source_quote_id": part["allowed_source_quote_ids"]["evidence_1"][0]}]}
            for part in data["script"]["sections"]]})
    checker = OpenAICompatibleCreationTaskScriptGenerator(
        "https://models.invalid/v1", "same-model", transport=httpx.MockTransport(review))
    monkeypatch.setattr(main, "_creation_task_script_generator_for", lambda settings: checker)
    sdk_calls = []
    def controlled_sdk(settings, **values):
        sdk_calls.append(values["turn_id"])
        host = values["business_handler"]
        options = dict(call_id="call", tool_call_id="tool", source_binding=values["source_binding"],
                       cancel_event=Event(), deadline_monotonic=time.monotonic() + 20)
        def parts(start, end):
            return [dict(id=f"section-{i}", title=f"段{i}", content=FACT, visual_prompt="计划画面",
                         evidence_reference_ids=[api_state.refs[0]["reference_id"]]) for i in range(start, end)]
        first = host("start_script_window", {"payload": {
            "request_id": str(uuid4()), "title": "真实待审稿", "sections": parts(0, 20)}}, **options)
        assert first["ok"], first
        written = first["value"]
        second = host("patch_script_sections", {"script_id": written["script_id"], "payload": {
            "request_id": str(uuid4()), "expected_updated_at": written["updated_at"], "sections": parts(20, 21)}}, **options)
        assert second["ok"], second
        return "model says done", None
    monkeypatch.setattr(runner, "run_task_turn", controlled_sdk)
    app = FastAPI()
    app.include_router(build_script_generation_router(accept=main._accept_script_generation,
        read=main._read_script_generation, execute=main._execute_script_generation))
    client = TestClient(app, headers=dict(api_state.client.headers))
    request_id = str(uuid4())
    accepted = client.post("/api/creation-tasks/task/scripts/generate", json={"request_id": request_id})
    assert accepted.status_code == 202, accepted.text
    result = client.get(f"/api/creation-tasks/task/scripts/generation-requests/{request_id}").json()
    assert result["status"] == "draft_ready", result
    assert result["turn_status"] == "completed" and result["run_id"] == accepted.json()["run_id"]
    assert sdk_calls == [result["turn_id"]]
    with api_state.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM creation_task_script_sections").fetchone()[0] == 21
    header = api_state.repository.script_records.read_creation_task_script_header("local", result["script_id"])
    assert header["draft_complete"] and header["selection_status"] == "draft"
    assert api_state.repository.get_creation_task("local", "task").confirmed_script_id is None
