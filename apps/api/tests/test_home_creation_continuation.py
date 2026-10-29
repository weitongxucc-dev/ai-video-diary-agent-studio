from app.assistant.task_turn_routes import TaskTurnHTTPAdapter
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import Request

import app.main as main
from app.assistant_goal_continuation import GoalContinuationService
from app.config import Settings
from app.schemas import WorkspaceAssistantPlanTurnRequest
from test_creation_request_api import client_state, create


def adopted(c, *, count=1, kind="goal_delegation", confirm=True, finish=True, workspace_id="request-test"):
    message = "请制作玉米脱粒介绍，文案和剪辑由你完成。"
    if count == 2:
        message = "请制作玉米脱粒介绍，中文和英文各一条，文案和剪辑由你完成。"
    chat = c.conversations.create(workspace_id, c.member.member_id, "home", "home", "目标")
    draft = c.conversations.save_draft(chat["id"], workspace_id, c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], workspace_id, c.member.member_id,
        message, str(uuid4()), draft["draft_version"], request_context={"kind": "home"})
    saved = create(c, goal=message, target_count=count, output_relation="variants" if count == 2 else "single",
        assistant_turn_id=turn["id"], request_key=turn["id"], intent={"kind": kind, "topic_title": "玉米脱粒"}).json()
    base = "/api/creation-requests/" + saved["id"]
    response = c.client.post(base + "/windows", headers=c.alice,
        json={"expected_revision": saved["revision"], "goals": [{"goal": f"玉米脱粒介绍{index}",
            "requirements": {"language": "zh-CN" if index == 0 else "en-US"}} for index in range(count)]})
    assert response.status_code == 200, response.text
    saved = response.json()
    if confirm:
        window = saved["current_window"]
        response = c.client.post(base + "/windows/" + window["id"] + "/confirm", headers=c.alice,
            json={"expected_revision": saved["revision"], "input_fingerprint": window["input_fingerprint"],
                "adoption_kind": "delegated_system", "assistant_turn_id": turn["id"]})
        assert response.status_code == 200, response.text
        saved = response.json()
    if finish:
        c.conversations.finish_turn(turn["id"], "completed", "已保存任务。", result={"kind": "creation_request",
            "creation_request_id": saved["id"]})
    return chat, turn, saved, message


def service(c, calls):
    def run(claim, fresh):
        goal = claim["goal"]
        origin = c.conversations.task_turn_source(claim["turn_id"], "request-test", c.member.member_id, goal["task_id"])
        calls.append((claim, origin))
        with pytest.raises(ValueError):
            c.conversations.task_turn_source(claim["turn_id"], "request-test", c.member.member_id, "another-task")
        c.conversations.finish_turn(claim["turn_id"], "completed", "模型边界测试，不执行真实制作。",
            result={"response": {}})
    return GoalContinuationService(c.conversations, verify_current_goal=lambda goal, event: {
        "status": "ready", "task_version": {"timeline_version_id": None, "timeline_sequence": 0}}, run_goal_turn=run)


def test_adopted_window_enters_existing_task_continuation_once_without_fake_user_messages(client_state):
    c = client_state
    chat, source, saved, message = adopted(c, count=2)
    calls = []
    continuation = service(c, calls)
    roots = continuation.register_creation_request(source["id"], "request-test", c.member.member_id, saved)
    assert len(roots) == 2
    assert continuation.register_creation_request(source["id"], "request-test", c.member.member_id, saved) == roots
    assert continuation.reconcile() == 2
    assert continuation.reconcile() == 0
    expected = {entry["task_id"] for entry in saved["current_window"]["entries"]}
    assert {claim["goal"]["task_id"] for claim, _ in calls} == expected
    for claim, origin in calls:
        assert claim["request_context"]["kind"] == "task" and claim["request_context"]["stage"] == "script"
        assert origin["raw_command"] == message and origin["conversation_id"] == chat["id"]
        assert claim["goal"]["creation_adoption"]["turn_id"] == source["id"]
    with c.conversations._connect() as db:
        rows = db.execute("SELECT user_message FROM assistant_conversation_turns WHERE conversation_id=?", (chat["id"],)).fetchall()
        assert [row[0] for row in rows if row[0]] == [message]


@pytest.mark.parametrize("kind", ["discussion", "goal_delegation"])
def test_discussion_or_unadopted_window_never_schedules_a_task(client_state, kind):
    c = client_state
    _, source, saved, _ = adopted(c, kind=kind, confirm=False)
    calls = []
    continuation = service(c, calls)
    assert continuation.register_creation_request(source["id"], "request-test", c.member.member_id, saved) == []
    assert continuation.reconcile() == 0 and calls == []


@pytest.mark.parametrize("mutation", ["task", "member", "window", "fingerprint"])
def test_only_matching_adoption_can_bind_task_scope(client_state, mutation):
    c = client_state
    _, source, saved, _ = adopted(c)
    bad = copy.deepcopy(saved)
    entry = bad["current_window"]["entries"][0]
    if mutation == "task":
        entry["task_id"] = "model-invented-task"
    else:
        key = {"member": "member_id", "window": "window_id", "fingerprint": "window_input_fingerprint"}[mutation]
        entry["plan"]["adoption"][key] = "another-binding"
    with pytest.raises(ValueError, match="采用回执"):
        service(c, []).register_creation_request(source["id"], "request-test", c.member.member_id, bad)
    assert c.conversations.goal_event_candidates() == []


def test_home_boundary_reads_owner_receipt_instead_of_returned_task_ids(client_state, monkeypatch):
    c = client_state
    _, source, saved, _ = adopted(c)
    wake = Mock()
    monkeypatch.setattr(main, "_wake_assistant_continuations", wake)
    request = Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in c.alice.items()]})
    main._register_adopted_creation_goals(request, source["id"], SimpleNamespace(id=saved["id"], task_id="invented"))
    candidates = c.conversations.goal_event_candidates()
    assert len(candidates) == 1
    assert candidates[0]["root"]["goal"]["task_id"] == saved["current_window"]["entries"][0]["task_id"]
    assert wake.call_count == 1


def test_home_response_and_live_replay_register_the_same_actual_tasks(client_state, monkeypatch):
    c = client_state
    # Bind the isolated workspace to the real local roster and the same DB;
    # retain actual Goal authorization/context persistence instead of bypassing it.
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "request-test")
    monkeypatch.setattr(main, "settings", replace(main.settings, database_path=str(c.repository.database_path)))
    chat, source, saved, message = adopted(c, finish=False)
    created = [True]
    monkeypatch.setattr(TaskTurnHTTPAdapter, "begin", lambda *args, **kwargs: (source["id"], created[0], source))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda workspace: Settings(deepseek_api_key="test-only"))
    monkeypatch.setattr(TaskTurnHTTPAdapter, "frozen_references", lambda *args: [])
    monkeypatch.setattr(TaskTurnHTTPAdapter, "input_delivery", lambda *args: None)
    model_boundary = Mock(return_value=("已采用任务。", {"kind": "creation_request", "request": saved}))
    monkeypatch.setattr(main, "run_plan_turn", model_boundary)
    monkeypatch.setattr(main, "_wake_assistant_continuations", Mock())
    request = Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in c.alice.items()]})
    payload = WorkspaceAssistantPlanTurnRequest(conversation_id=chat["id"], message=message,
        client_request_id=uuid4(), expected_draft_version=1)
    response = main._workspace_assistant_plan_turn(request, payload, None)
    assert response.creation_request.current_window.status == "confirmed"
    candidates = c.conversations.goal_event_candidates()
    assert len(candidates) == 1
    created[0] = False
    replay = main._workspace_assistant_plan_turn(request, payload, None)
    assert replay.creation_request.id == saved["id"] and model_boundary.call_count == 1
    assert [item["root"]["id"] for item in c.conversations.goal_event_candidates()] == [candidates[0]["root"]["id"]]



def test_home_saved_request_planning_keeps_save_only_and_second_unavailable(client_state, monkeypatch):
    """Three bounded metadata scenarios, no SDK/model or production execution."""
    from app.assistant.failures import AssistantUnavailable
    c = client_state
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: Settings(deepseek_api_key="test-only"))
    monkeypatch.setattr(TaskTurnHTTPAdapter, "frozen_references", lambda *_: [])
    monkeypatch.setattr(TaskTurnHTTPAdapter, "input_delivery", lambda *_: None)
    monkeypatch.setattr(main, "_wake_assistant_continuations", Mock())
    request = Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in c.alice.items()]})
    for mode in ("continue", "save_only", "second_unavailable"):
        message = "请制作1条玉米介绍视频，文案和剪辑由你完成。" + ("只先保存，不要规划方案。" if mode == "save_only" else "")
        chat = c.conversations.create("request-test", c.member.member_id, "home", "home", mode)
        c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, message, 0)
        calls, original = [], []

        def model(_settings, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                response = create(c, goal=message, target_count=1, assistant_turn_id=kwargs["turn_id"],
                    request_key=kwargs["turn_id"], intent={"kind": "goal_delegation", "topic_title": "玉米介绍"})
                # client_state registers the handler on its own FastAPI app (HTTP 200).
                # The production route's explicit HTTP 201 declaration is unchanged.
                assert response.status_code == 200, response.text
                original.append(response.json())
                assert original[0]["status"] == "awaiting_plan" and original[0]["clarification_questions"] == []
                return "目标已保存。", {"kind": "creation_request", "request": original[0]}
            assert len(calls) == 2 and kwargs["turn_id"] == calls[0]["turn_id"]
            assert kwargs["creation_request"]["id"] == original[0]["id"]
            if mode == "second_unavailable":
                raise AssistantUnavailable("声明的第二轮不可用，未执行模型")
            response = c.client.post("/api/creation-requests/" + original[0]["id"] + "/windows", headers=c.alice,
                json={"expected_revision": original[0]["revision"], "goals": [{"goal": "玉米介绍"}]})
            assert response.status_code == 200, response.text
            return "已准备原目标的方案。", {"kind": "creation_request", "request": response.json()}

        monkeypatch.setattr(main, "run_plan_turn", model)
        payload = WorkspaceAssistantPlanTurnRequest(conversation_id=chat["id"], message=message,
            client_request_id=uuid4(), expected_draft_version=1)
        response = main._workspace_assistant_plan_turn(request, payload, None)
        assert len(calls) == (1 if mode == "save_only" else 2)
        assert response.creation_request.id == original[0]["id"]
        saved = c.conversations.get_turn(str(response.turn_id))
        assert saved["status"] == "completed"
        assert saved["result"]["response"] == response.model_dump(mode="json")
        assert saved["result"]["creation_request_id"] == original[0]["id"]
        if mode == "continue":
            assert response.creation_request.current_window.status == "proposed"
            assert response.reply == "已准备原目标的方案。"
        else:
            assert response.creation_request.current_window is None
            assert response.creation_request.status == "awaiting_plan"
            assert ("制作目标已保存，当前这组方案尚未完成。" in response.reply) == (mode == "second_unavailable")
        replay = main._workspace_assistant_plan_turn(request, payload, None)
        assert replay.model_dump(mode="json") == response.model_dump(mode="json")
        assert len(calls) == (1 if mode == "save_only" else 2)
    assert c.repository.task_records.list_creation_tasks("request-test") == []

def test_first_task_round_uses_existing_runner_and_trusted_task_source(client_state, monkeypatch):
    c = client_state
    # The real native actor boundary permits local tokens only in the local workspace.
    # Keep the request fixture isolated while binding the same real local member.
    monkeypatch.setattr(main, "current_workspace_id", lambda _request: "local")
    original_preflight = main._creation_plan_preflight
    monkeypatch.setattr(main, "_creation_plan_preflight", lambda *args, **kwargs:
        replace(original_preflight(*args, **kwargs), workspace_id="local"))
    monkeypatch.setattr(main, "settings", replace(main.settings, database_path=str(c.repository.database_path)))
    _, source, saved, message = adopted(c, workspace_id="local")
    model_calls = []
    def model(settings, **kwargs):
        origin = c.conversations.task_turn_source(kwargs["turn_id"], "local", c.member.member_id, kwargs["task_id"])
        assert origin["raw_command"] == message
        assert isinstance(kwargs["business_handler"], main.TaskBusinessAdapter)
        assert kwargs["business_handler"].business is business
        model_calls.append(kwargs)
        return "已读取任务，未执行模型或媒体。", None
    monkeypatch.setattr(main, "run_task_turn", model)
    monkeypatch.setattr(main, "_workspace_settings_for", lambda workspace: Settings(deepseek_api_key="test-only"))
    monkeypatch.setattr(TaskTurnHTTPAdapter, "frozen_references", lambda *args: [])
    monkeypatch.setattr(TaskTurnHTTPAdapter, "input_delivery", lambda *args: None)
    # This metadata-only plan fixture has no imported sources. Content and
    # provider behavior are covered separately; here verify the actual runner.
    monkeypatch.setattr(main.CreationSourceInputs, "prepare", lambda *args, **kwargs: ((), [], {}))
    monkeypatch.setattr(main, "_wake_assistant_continuations", Mock())
    request = Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in c.alice.items()]})
    business = main._task_business()
    continuation = GoalContinuationService(c.conversations, verify_current_goal=lambda goal, event: {
        "status": "ready", "task_version": {"timeline_version_id": None, "timeline_sequence": 0},
        "facts": {}, "request": request, "business": business}, run_goal_turn=main._run_goal_turn)
    roots = continuation.register_creation_request(source["id"], "local", c.member.member_id, saved)
    for root_id in roots:
        main._bind_goal_business_scope(request, c.conversations.get_task_goal(root_id))
    assert continuation.reconcile() == 1
    assert len(model_calls) == 1 and model_calls[0]["stage"] == "script"
    assert model_calls[0]["task_id"] == saved["current_window"]["entries"][0]["task_id"]


def test_new_user_message_invalidates_adopted_task_continuation(client_state):
    c = client_state
    chat, source, saved, _ = adopted(c)
    calls = []
    continuation = service(c, calls)
    roots = continuation.register_creation_request(source["id"], "request-test", c.member.member_id, saved)
    current = c.conversations.get(chat["id"], "request-test", c.member.member_id)
    draft = c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "先讨论，不要制作", current["draft_version"])
    c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id, draft["draft_text"],
        str(uuid4()), draft["draft_version"], request_context={"kind": "home"})
    assert continuation.reconcile() == 0 and calls == []
    assert c.conversations.get_task_goal(roots[0])["status"] == "invalidated"


@pytest.mark.parametrize('execution_already_submitted', [False, True])
def test_adoption_inside_home_child_continues_once_unless_execution_already_owns_the_wakeup(
    client_state, execution_already_submitted,
):
    c = client_state
    _, source, saved, _ = adopted(c)
    calls = []
    def run(claim, fresh):
        calls.append(claim)
        task_id = claim['goal']['task_id']
        if len(calls) == 1:
            origin = c.conversations.task_turn_source(claim['turn_id'], 'request-test', c.member.member_id, task_id)
            assert c.conversations.record_task_script_adoption(claim['turn_id'], 'request-test', c.member.member_id,
                task_id, script_id='actual-adopted-script', script_version='fixed-version', source=origin)
            if execution_already_submitted:
                c.conversations.record_task_execution(claim['turn_id'], 'request-test', c.member.member_id, task_id,
                    {'task_id': task_id, 'job': {'id': 'actual-execution', 'status': 'queued'}})
            c.conversations.finish_turn(claim['turn_id'], 'completed', '真实采用已记录。',
                result={'response': {'script_draft': {'id': 'actual-adopted-script'}}})
        else:
            c.conversations.finish_turn(claim['turn_id'], 'completed', '后续边界回读完成。', result={})
    continuation = GoalContinuationService(c.conversations, verify_current_goal=lambda *_: {
        'status': 'ready', 'task_version': {'timeline_version_id': None, 'timeline_sequence': 0}}, run_goal_turn=run)
    continuation.register_creation_request(source['id'], 'request-test', c.member.member_id, saved)
    assert continuation.reconcile() == 1
    assert continuation.reconcile() == (0 if execution_already_submitted else 1)
    assert continuation.reconcile() == 0
    assert len(calls) == (1 if execution_already_submitted else 2)
