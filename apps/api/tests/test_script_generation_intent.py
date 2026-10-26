"""Real request/turn/Run transactions with isolated content and no model calls."""
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository, ConversationTurnRequestConflict, ConversationTurnAlreadyRunning
from app.creation.script_generation_intent import (
    ScriptGenerationIntent, ScriptGenerationAgentFailed, ScriptGenerationAgentUnknown,
)
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.creation_execution import CreationExecutionActor
from test_creation_task_script_grounding import script_domain_state  # noqa: F401


@pytest.fixture
def generation(script_domain_state):
    state = script_domain_state
    conversations = ConversationRepository(state.repository.database_path)
    case = ScriptGenerationIntent(state.repository, conversations,
        source_inputs=main._source_inputs_use_case().prepare,
        route_snapshot={"execution_location": "cloud", "availability": "unverified"},
        verify_identity=lambda actor: "ready")
    actor = CreationExecutionActor("local", state.owner.member_id)
    return state, conversations, case, actor


def test_user_action_turn_keeps_unsent_draft_and_belongs_to_callers_transaction(tmp_path):
    conversations = ConversationRepository(tmp_path / "isolated.sqlite")
    chat = conversations.current_or_create("ws", "member", "task", "task")
    with conversations._connect() as conn:
        conn.execute("UPDATE assistant_conversations SET draft_text='unsent',draft_version=3 WHERE id=?", (chat["id"],))
    request_id = str(uuid4())
    context = {"kind": "script_generation", "source_kind": "user_action", "task_id": "task",
               "requested_effect": "draft", "generation_request_id": request_id}
    with pytest.raises(RuntimeError, match="business write failed"):
        with conversations._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            turn, created = conversations.create_user_action_turn_idempotent(conn,
                conversation_id=chat["id"], workspace_id="ws", member_id="member", task_id="task",
                client_request_id=request_id, request_context=context)
            assert created and turn["user_message"] == ""
            raise RuntimeError("business write failed")
    assert conversations.get_request_receipt(request_id, chat["id"], "ws", "member") is None
    assert conversations.list_turns(chat["id"]) == []
    with conversations._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        turn, created = conversations.create_user_action_turn_idempotent(conn,
            conversation_id=chat["id"], workspace_id="ws", member_id="member", task_id="task",
            client_request_id=request_id, request_context=context)
        replay, repeated = conversations.create_user_action_turn_idempotent(conn,
            conversation_id=chat["id"], workspace_id="ws", member_id="member", task_id="task",
            client_request_id=request_id, request_context=context)
        assert created and not repeated and replay["id"] == turn["id"]
    current = conversations.get(chat["id"], "ws", "member")
    assert (current["draft_text"], current["draft_version"]) == ("unsent", 3)
    receipt = conversations.get_request_receipt(request_id, chat["id"], "ws", "member")
    assert receipt["user_message"] == "" and receipt["request_context"] == context


def test_generation_request_is_unique_and_failed_owner_write_rolls_back_all_facts(generation, monkeypatch):
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    original = state.repository.append_task_event

    def refuse(event, **kwargs):
        if event.event_type == "script_generation_requested":
            raise RuntimeError("request receipt unavailable")
        return original(event, **kwargs)

    with monkeypatch.context() as failed:
        failed.setattr(state.repository, "append_task_event", refuse)
        with pytest.raises(RuntimeError, match="request receipt unavailable"):
            case.start("local", "task", request_id, actor)
    with state.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM assistant_conversation_turns").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM assistant_conversation_request_receipts").fetchone()[0] == 0
    first, created = case.start("local", "task", request_id, actor)
    replay, repeated = case.start("local", "task", request_id, actor)
    assert created and not repeated and first == replay
    with state.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM assistant_conversation_turns").fetchone()[0] == 1
        guarded = case.read_generation_request(conn, "local", "task", request_id, actor.member_id)
    assert guarded["can_continue"] and guarded["requested_effect"] == "draft"
    assert state.repository.get_creation_task("local", "task").confirmed_script_id is None


def test_early_stop_is_durable_replay_does_not_dispatch_and_original_run_is_settled(generation):
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    accepted, created = case.start("local", "task", request_id, actor)
    stopped = case.request_stop("local", "task", request_id, actor.member_id)
    assert stopped["status"] == "stopping" and stopped["turn_status"] == "running"
    assert case.request_stop("local", "task", request_id, actor.member_id) == stopped
    called = []
    with state.repository._connect() as conn:
        with pytest.raises(PermissionError, match="停止"):
            case.read_generation_request(conn, "local", "task", request_id, actor.member_id)
            called.append("provider")
    assert called == []
    result = case.execute("local", "task", request_id, actor.member_id,
        run_agent=lambda record: called.append("provider"), script_case=None)
    assert called == []
    assert result["turn_status"] == "stopped" and result["run_status"] == "failed"
    replay, repeated = case.start("local", "task", request_id, actor)
    assert not repeated and replay["run_id"] == accepted["run_id"] and replay["turn_id"] == accepted["turn_id"]
    assert conversations.get_turn(accepted["turn_id"])["status"] == "stopped"


def test_source_change_or_lost_identity_blocks_write_but_read_needs_no_device_credential(generation):
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    case.start("local", "task", request_id, actor)
    case.verify_identity = lambda actor: "unavailable"
    assert case.read("local", "task", request_id, actor.member_id)["run_status"] == "running"
    with state.repository._connect() as conn:
        with pytest.raises(PermissionError):
            case.read_generation_request(conn, "local", "task", request_id, actor.member_id)
    case.verify_identity = lambda actor: "ready"
    with state.repository._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=json_set(payload,'$.goal','changed goal') WHERE id='plan'")
    with state.repository._connect() as conn:
        with pytest.raises(PermissionError, match="变化"):
            case.read_generation_request(conn, "local", "task", request_id, actor.member_id)
    with pytest.raises(ConversationTurnRequestConflict):
        case.read("local", "task", request_id, "different-member")


def test_ready_result_is_bound_atomically_and_later_edits_do_not_replace_original_version(generation):
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    accepted, _ = case.start("local", "task", request_id, actor)
    script = CreationTaskScript(id="generated-draft", workspace_id="local", task_id="task",
        run_id=accepted["run_id"], topic_id="topic", title="Draft", language="zh-CN",
        sections=[CreationTaskScriptSection(id="section", title="Title", content="本机工作区导览。",
                   visual_prompt="展示入口", evidence_reference_ids=[ref["reference_id"] for ref in state.refs])])
    state.repository.complete_script_generation(accepted["run_id"], script,
        write_guard=lambda conn: case.read_generation_request(conn, "local", "task", request_id, actor.member_id),
        result_write=lambda conn, value: case.bind_generation_result(conn, "local", "task", request_id,
            actor.member_id, run_id=value.run_id, script_id=value.id, draft_ready=True))
    ready = case.public_projection(case.read("local", "task", request_id, actor.member_id))
    assert ready["status"] == "draft_ready" and ready["script_id"] == script.id and not ready["result_stale"]
    assert "actor" not in ready and "frozen_input" not in ready
    assert state.repository.get_creation_task("local", "task").confirmed_script_id is None
    receipt = conversations.get_request_receipt(request_id, accepted["conversation_id"], "local", actor.member_id)
    assert receipt["request_context"]["generation_result"]["updated_at"] == ready["script_updated_at"]
    with state.repository._connect() as conn:
        conn.execute("UPDATE creation_task_scripts SET updated_at='2099-01-01T00:00:00+00:00', "
                     "payload=json_set(payload,'$.updated_at','2099-01-01T00:00:00+00:00') WHERE id=?", (script.id,))
    later = case.public_projection(case.read("local", "task", request_id, actor.member_id))
    assert later["script_updated_at"] == ready["script_updated_at"] and later["result_stale"]


@pytest.mark.parametrize("failure,expected", [(ScriptGenerationAgentFailed("agent_unavailable"), "failed"),
                                               (ScriptGenerationAgentUnknown(), "unknown")])
def test_execute_has_one_durable_dispatch_and_never_repeats_an_unknown_write(generation, failure, expected):
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    original, _ = case.start("local", "task", request_id, actor)
    calls = []
    def runner(record):
        calls.append(record["run_id"])
        raise failure
    result = case.execute("local", "task", request_id, actor.member_id, run_agent=runner, script_case=None)
    assert result["status"] == expected
    assert case.execute("local", "task", request_id, actor.member_id, run_agent=runner, script_case=None) == result
    replay, created = case.start("local", "task", request_id, actor)
    assert not created and replay["run_id"] == original["run_id"] and calls == [original["run_id"]]
    if expected == "unknown":
        assert result["run_status"] == result["turn_status"] == "running"
        with state.repository._connect() as conn:
            with pytest.raises(PermissionError, match="未知"):
                case.read_generation_request(conn, "local", "task", request_id, actor.member_id)


def test_execute_finishes_actual_reviewed_window_without_adopting_or_saving_model_text(generation, monkeypatch):
    from app.creation.script_draft_use_case import ScriptDraftUseCase
    from app.schemas import CreationTaskScriptWindowStartRequest

    state, conversations, case, actor = generation
    def unexpected_unknown(*args):
        import sys
        raise AssertionError("successful fixture unexpectedly produced unknown outcome") from sys.exception()
    monkeypatch.setattr(case, "_mark_unknown", unexpected_unknown)
    script_case = ScriptDraftUseCase(state.repository,
        read_inputs=main._assistant_script_inputs,
        validate_inputs=lambda ws, plan, refs: main._validate_script_input_sources(ws, plan, refs, actor.member_id),
        script_goal=lambda plan, task, viewer: plan.goal, topic_origin=main._script_topic_origin,
        check_draft=lambda script_input, candidate: candidate,
        record_service=lambda **facts: None, route_snapshot=main._topic_route_snapshot,
        record_adoption=lambda **facts: pytest.fail("generation must not adopt"))
    script_case.read_generation_request = case.read_generation_request
    script_case.read_generation_result = case.read_generation_result
    script_case.bind_generation_result = case.bind_generation_result
    request_id = str(uuid4())
    accepted, _ = case.start("local", "task", request_id, actor)
    calls = []

    def run_agent(record):
        calls.append(record["turn_id"])
        origin = {**record["source"], "intent_kind": "script_generation", "generation_request_id": request_id,
                  "requested_effect": "draft", "raw_command": ""}
        script_case.start_generation_window("local", "task", CreationTaskScriptWindowStartRequest(
            request_id=uuid4(), title="Draft", sections=[dict(id="first", title="用途", content="本机工作区导览。",
                visual_prompt="展示入口", evidence_reference_ids=[ref["reference_id"] for ref in state.refs])]),
            member_id=actor.member_id, generation_request_id=request_id,
            instruction_origin=origin, reread_origin=lambda draft_only: origin)
        return "pretend already adopted; raw provider text", {"kind": "production_completed"}

    result = case.execute("local", "task", request_id, actor.member_id, run_agent=run_agent, script_case=script_case)
    assert result["status"] == "draft_ready", result
    assert result["run_status"] == "succeeded" and result["turn_status"] == "completed"
    assert state.repository.get_creation_task("local", "task").confirmed_script_id is None
    turn = conversations.get_turn(accepted["turn_id"])
    assert turn["user_message"] == turn["assistant_text"] == ""
    assert turn["result"]["response"]["script_id"] == result["script_id"]
    assert case.execute("local", "task", request_id, actor.member_id, run_agent=run_agent, script_case=script_case) == result
    assert calls == [accepted["turn_id"]]


@pytest.mark.parametrize("checkpoint", ["accepted", "partial", "ready", "pending_claim", "unknown", "legacy_failed"])
def test_restart_reconciles_original_run_and_preserves_saved_or_unknown_writes(generation, monkeypatch, checkpoint):
    from app.creation.contracts import TaskEvent
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    accepted, _ = case.start("local", "task", request_id, actor)
    if checkpoint in {"partial", "ready"}:
        script = CreationTaskScript(id="restart-draft", workspace_id="local", task_id="task",
            run_id=accepted["run_id"], topic_id="topic", title="Saved", language="zh-CN",
            sections=[CreationTaskScriptSection(id="saved", title="用途", content="原已保存文字",
                visual_prompt="入口", evidence_reference_ids=[])])
        write = (state.repository.complete_script_generation if checkpoint == "ready"
                 else state.repository.script_records.attach_generation_window)
        write(accepted["run_id"], script,
            write_guard=lambda conn: case.read_generation_request(conn, "local", "task", request_id, actor.member_id),
            result_write=lambda conn, value: case.bind_generation_result(conn, "local", "task", request_id,
                actor.member_id, run_id=value.run_id, script_id=value.id, draft_ready=checkpoint == "ready"))
    elif checkpoint == "pending_claim":
        state.repository.append_task_event(TaskEvent("unfinished-window", "local", "task", None,
            "script_window_request_claimed", {"generation_request_id": request_id, "member_id": actor.member_id,
                "operation": "start", "request_id": str(uuid4())}))
    elif checkpoint == "unknown":
        case._mark_unknown("local", "task", request_id, actor.member_id)
    elif checkpoint == "legacy_failed":
        conversations.finish_turn(accepted["turn_id"], "failed", result={"reason": "api_restarted"})

    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "production_repo", state.repository)
    # Recovery only reads durable facts; changed provider/identity availability is irrelevant.
    def forbidden(*args, **kwargs):
        pytest.fail("restart must not call a model, capture sources, or require current device identity")
    monkeypatch.setattr(main, "_topic_route_snapshot", forbidden)
    monkeypatch.setattr(main, "_verify_creation_identity", forbidden)
    main.recover_interrupted_assistant_turns()
    result = case.read("local", "task", request_id, actor.member_id)
    assert result["run_id"] == accepted["run_id"] and result["turn_id"] == accepted["turn_id"]
    with state.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs").fetchone()[0] == 1
        with pytest.raises(PermissionError):
            case.read_generation_request(conn, "local", "task", request_id, actor.member_id)
    assert state.repository.get_creation_task("local", "task").confirmed_script_id is None
    if checkpoint in {"pending_claim", "unknown"}:
        assert result["status"] == "unknown" and result["run_status"] == "running"
        with pytest.raises(ConversationTurnAlreadyRunning):
            case.start("local", "task", str(uuid4()), actor)
    elif checkpoint == "ready":
        assert result["status"] == "draft_ready" and result["run_status"] == "succeeded"
        assert result["turn_status"] == "completed"
    else:
        assert result["status"] == result["run_status"] == result["turn_status"] == "failed"
    if checkpoint in {"partial", "ready"}:
        preserved = state.repository.get_creation_task_script("local", "restart-draft")
        assert preserved.sections[0].content == "原已保存文字" and preserved.selection_status == "draft"
        assert result["saved_result"]["script_id"] == preserved.id
    main.recover_interrupted_assistant_turns()
    assert case.read("local", "task", request_id, actor.member_id) == result
    # A fresh UUID is allowed only after a known failed original, never on its replay.
    if checkpoint in {"accepted", "partial", "legacy_failed"}:
        replay, created = case.start("local", "task", request_id, actor)
        assert not created and replay["run_id"] == accepted["run_id"]
        fresh, created = case.start("local", "task", str(uuid4()), actor)
        assert created and fresh["run_id"] != accepted["run_id"]


def test_restart_owner_failure_rolls_back_turn_run_and_task_together(generation, monkeypatch):
    state, conversations, case, actor = generation
    request_id = str(uuid4())
    accepted, _ = case.start("local", "task", request_id, actor)
    def fail_turn(*args, **kwargs):
        raise RuntimeError("isolated recovery persistence failure")
    monkeypatch.setattr(conversations, "finish_turn", fail_turn)
    with pytest.raises(RuntimeError, match="isolated recovery"):
        conversations.fail_interrupted_turns(recover_user_action=case.recover_interrupted)
    result = case.read("local", "task", request_id, actor.member_id)
    assert result["run_status"] == result["turn_status"] == "running"
    assert state.repository.get_creation_task("local", "task").stage_status == "running"
    assert result["run_id"] == accepted["run_id"]
