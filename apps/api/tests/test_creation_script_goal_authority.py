"""Real request/window/Plan persistence; conversation provenance is a declared host receipt."""
import copy
import hashlib
from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.creation.intent_use_case import CreationIntentUseCase
from app.production import ProductionRepository
from test_creation_requests import OWNER, WORKSPACE, entries
from test_creation_task_script_grounding import (
    FACT, agent_payload, api_state, draft, reviewed, set_agent_reviewer, set_api_provider,
)


@pytest.fixture
def adopted(tmp_path):
    def create(*, kind="goal_delegation", adoption_kind="delegated_system", source_patch=None):
        repository = ProductionRepository(tmp_path / "script-goal.sqlite3", tmp_path / "library")
        body = "这是用户已确认的准确原文。"
        goal = ("请自主制作30秒脱壳机介绍，中英文各一条，不编造型号和性能。"
                if kind == "goal_delegation" else "请按这份定稿制作30秒视频：" + body)
        source = {"kind": "conversation_input", "member_id": OWNER, "request_id": "original-request-key",
            "conversation_id": "home-conversation", "turn_id": "human-input-turn",
            "message_digest": hashlib.sha256(goal.encode()).hexdigest()}
        source.update(source_patch or {})
        count = 2 if kind == "goal_delegation" else 1
        intent = CreationIntentUseCase.receive_intent({"kind": kind, "topic_title": "脱壳机介绍",
            **({"final_script": {"title": "确认稿", "text": body}} if kind == "final_script" else {})},
            source=source, target_count=count, original_message=goal)
        saved = repository.create_creation_request(WORKSPACE, request_key="original-request-key",
            input_fingerprint="human-input-digest", goal=goal, target_count=3 if count == 2 else 1,
            output_relation="variants" if count == 2 else "single",
            requirements={"creation_intent": intent, "language": "zh-CN", "duration_target_seconds": 30},
            material_scope={"mode": "workspace"}, clarification_questions=[], verified_owner_user_id=OWNER)
        proposed = [(replace(plan, goal="Agent自拟旁白：田间收好的玉米棒……；片尾静态署名。",
                            language="zh-CN" if index == 0 else "en-US", aspect_ratio="16:9"), task)
                    for index, (plan, task) in enumerate(entries(count=count))]
        saved = repository.save_creation_request_window(WORKSPACE, saved["id"],
            expected_revision=saved["revision"], input_fingerprint="adopted-window-input", entries=proposed,
            verified_owner_user_id=OWNER)
        window = saved["current_window"]
        confirmed = repository.confirm_creation_request_window(WORKSPACE, saved["id"], window["id"],
            expected_revision=saved["revision"], input_fingerprint=window["input_fingerprint"],
            verified_owner_user_id=OWNER, adoption_kind=adoption_kind)
        pairs = [(repository.get_creation_plan(WORKSPACE, item["plan_id"]),
                  repository.get_creation_task(WORKSPACE, item["task_id"]))
                 for item in confirmed["current_window"]["entries"]]
        return SimpleNamespace(repository=repository, request=confirmed, pairs=pairs, goal=goal, fixed_body=body)
    return create


def test_real_adopted_request_owns_goal_and_keeps_each_bilingual_delivery_unchanged(adopted):
    state = adopted()
    for plan, task in state.pairs:
        before = asdict(plan), asdict(task)
        assert "Agent自拟旁白" in plan.goal and plan.creation_intent["kind"] == "goal_delegation"
        assert CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=OWNER) == state.goal
        assert (asdict(plan), asdict(task)) == before
        assert (plan.language, plan.duration_target_seconds, plan.aspect_ratio, plan.presenter_share_target) == (
            "zh-CN" if task.id == "task-1" else "en-US", 30, "16:9", 0)
    # The same public reads still recover the original authorization after restart.
    restarted = ProductionRepository(state.repository.database_path, state.repository.video_library_path)
    assert CreationIntentUseCase.script_goal(restarted, *state.pairs[0], viewer_user_id=OWNER) == state.goal


@pytest.mark.parametrize("viewer", ["another-current-member", None])
def test_original_owner_cannot_replace_current_viewer_for_private_request(adopted, viewer):
    state = adopted()
    plan, task = state.pairs[0]
    assert plan.owner_user_id == OWNER
    with pytest.raises(KeyError):
        CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=viewer)


@pytest.mark.parametrize("change", ["intent_source", "adoption_source", "source_member", "adoption_member"])
def test_authorization_source_mismatch_does_not_fall_back_to_agent_goal(adopted, change):
    state = adopted()
    plan, task = copy.deepcopy(state.pairs[0])
    if change == "intent_source":
        plan.creation_intent["source"]["turn_id"] = "another-input"
    elif change == "adoption_source":
        plan.adoption["authorization_source"]["turn_id"] = "another-input"
    elif change == "source_member":
        plan.creation_intent["source"]["member_id"] = "another-member"
    else:
        plan.adoption["member_id"] = "another-member"
    with pytest.raises(ValueError, match="授权来源"):
        CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=OWNER)


@pytest.mark.parametrize("digest", [None, "digest-of-agent-draft"])
def test_matching_receipts_with_missing_or_wrong_human_body_digest_still_reject(adopted, digest):
    state = adopted(source_patch={"message_digest": digest})
    with pytest.raises(ValueError, match="输入摘要"):
        CreationIntentUseCase.script_goal(state.repository, *state.pairs[0], viewer_user_id=OWNER)


@pytest.mark.parametrize("change", ["task", "workspace", "plan_task", "adopted_plan", "window", "window_digest"])
def test_request_window_and_task_binding_cannot_be_substituted(adopted, change):
    state = adopted()
    plan, task = copy.deepcopy(state.pairs[0])
    if change == "task":
        task = state.pairs[1][1]
    elif change == "workspace":
        task.workspace_id = "another-workspace"
    elif change == "plan_task":
        plan.creation_task_id = "another-task"
    elif change == "adopted_plan":
        plan.adoption["plan_id"] = "another-plan"
    elif change == "window":
        plan.adoption["window_id"] = "unrelated-window"
    else:
        plan.adoption["window_input_fingerprint"] = "unrelated-input"
    with pytest.raises(ValueError, match="关联|窗口"):
        CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=OWNER)


def test_another_real_request_cannot_supply_this_tasks_goal(adopted):
    state = adopted()
    other = state.repository.create_creation_request(WORKSPACE, request_key="other-human-input",
        input_fingerprint="other-input-digest", goal="另一条用户目标", target_count=1, output_relation="single",
        requirements={}, material_scope={"mode": "workspace"}, clarification_questions=[], verified_owner_user_id=OWNER)
    plan, task = copy.deepcopy(state.pairs[0])
    plan.adoption["request_id"] = other["id"]
    with pytest.raises(ValueError, match="授权来源"):
        CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=OWNER)


def test_new_planning_window_does_not_invalidate_an_earlier_adopted_task(adopted, monkeypatch):
    state = adopted()
    saved = state.repository.save_creation_request_window(WORKSPACE, state.request["id"],
        expected_revision=state.request["revision"], input_fingerprint="next-window", entries=entries(start=3, count=1),
        verified_owner_user_id=OWNER)
    assert saved["current_window"]["status"] == "proposed"
    assert saved["current_window"]["id"] != state.pairs[0][0].adoption["window_id"]
    monkeypatch.setattr(state.repository, "list_creation_request_items",
        lambda *_a, **_kw: pytest.fail("文案目标解析不能扫描历史作品"))
    assert CreationIntentUseCase.script_goal(state.repository, *state.pairs[0], viewer_user_id=OWNER) == state.goal
    # Pointing an old task at that unadopted window is not a valid association.
    plan, task = copy.deepcopy(state.pairs[0])
    plan.adoption.update(window_id=saved["current_window"]["id"], window_input_fingerprint="next-window")
    with pytest.raises(ValueError, match="窗口未采用"):
        CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=OWNER)


def test_direct_user_action_uses_its_saved_request_without_inventing_a_conversation_digest(adopted):
    state = adopted(source_patch={"kind": "user_action", "message_digest": None})
    assert CreationIntentUseCase.script_goal(state.repository, *state.pairs[0], viewer_user_id=OWNER) == state.goal


@pytest.mark.parametrize("kind,adoption_kind", [("goal_delegation", "manual"), ("final_script", "delegated_system")])
def test_manual_and_fixed_copy_keep_existing_goal_and_locked_body_rules(adopted, kind, adoption_kind):
    state = adopted(kind=kind, adoption_kind=adoption_kind)
    plan, task = state.pairs[0]
    before = asdict(plan), asdict(task)
    assert CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=None) == plan.goal
    assert (asdict(plan), asdict(task)) == before
    if kind == "final_script":
        script = state.repository.get_creation_task_script(WORKSPACE, task.confirmed_script_id)
        assert script.sections[0].content == state.fixed_body
        assert script.locked_body_digest == hashlib.sha256(state.fixed_body.encode()).hexdigest()
        assert CreationIntentUseCase.adopted_for_execution(plan, script)


def test_partial_request_adoption_cannot_silently_use_agent_draft(adopted):
    state = adopted()
    plan, task = copy.deepcopy(state.pairs[0])
    del plan.adoption["request_id"]
    with pytest.raises(ValueError, match="采用关联"):
        CreationIntentUseCase.script_goal(state.repository, plan, task, viewer_user_id=OWNER)


@pytest.fixture
def consumer_state(api_state, monkeypatch):
    """Keep real HTTP adapters, permissions, sources and request/window storage."""
    import app.main as main
    from app.conversation_repository import ConversationRepository
    from app.production import CreationTask

    def create(*, failure=None):
        state = api_state
        owner = state.owner.member_id if failure != "private_request" else "another-original-owner"
        if failure == "private_request":
            state.sources.set_knowledge_item_visibility("local", "fixture", "shared")
        goal = "请自主制作30秒本机工作区介绍，中文旁白，不编造功能。"
        source = {"kind": "conversation_input", "member_id": owner, "request_id": "http-human-input",
            "turn_id": "http-original-turn", "conversation_id": "http-original-chat",
            "message_digest": hashlib.sha256(goal.encode()).hexdigest() if failure != "source" else "wrong-body"}
        intent = CreationIntentUseCase.receive_intent({"kind": "goal_delegation", "topic_title": "本机工作区导览"},
            source=source, target_count=1, original_message=goal)
        saved = state.repository.create_creation_request("local", request_key="http-human-input",
            input_fingerprint="http-input", goal=goal, target_count=1, output_relation="single",
            requirements={"creation_intent": intent}, material_scope={"mode": "workspace"},
            clarification_questions=[], verified_owner_user_id=owner)
        plan = replace(state.plan, id="derived-plan", creation_task_id=None, goal="Agent自拟完整旁白，不得更改。",
            duration_target_seconds=30)
        task = CreationTask("derived-task", "local", plan.id, plan.name, goal)
        saved = state.repository.save_creation_request_window("local", saved["id"],
            expected_revision=saved["revision"], input_fingerprint="http-window", entries=[(plan, task)],
            verified_owner_user_id=owner)
        state.repository.confirm_creation_request_window("local", saved["id"], saved["current_window"]["id"],
            expected_revision=saved["revision"], input_fingerprint="http-window",
            verified_owner_user_id=owner, adoption_kind="delegated_system")
        if failure == "private_request":
            state.repository.set_creation_task_visibility("local", task.id, "shared")
            state.repository.set_creation_plan_visibility("local", plan.id, "shared")
        task = state.repository.get_creation_task("local", task.id)
        conversations = ConversationRepository(state.repository.database_path)
        monkeypatch.setattr(main, "conversation_repo", conversations)
        instruction = "正文讲得简洁一些，资料不支持的细节可以删掉。"
        chat = conversations.create("local", state.owner.member_id, "task", task.id)
        chat = conversations.save_draft(chat["id"], "local", state.owner.member_id, instruction, 0)
        turn, _ = conversations.create_turn_idempotent(chat["id"], "local", state.owner.member_id, instruction,
            str(uuid4()), chat["draft_version"], request_context={"kind": "task", "task_id": task.id})
        return SimpleNamespace(**{**vars(state), "task": task}, goal=goal, instruction=instruction, turn_id=turn["id"])
    return create


@pytest.mark.parametrize("endpoint", ["assistant/script-draft", "scripts/generate"])
def test_real_main_script_consumers_send_original_user_goal_to_model(consumer_state, monkeypatch, endpoint):
    import json
    state = consumer_state()
    calls, document = [], draft()
    if endpoint == "assistant/script-draft":
        set_agent_reviewer(monkeypatch, reviewed(document), calls)
        payload = {**agent_payload(state, content=FACT), "assistant_turn_id": state.turn_id}
    else:
        set_api_provider(monkeypatch, document, reviewed(document), calls)
        payload = {}
    response = state.client.post(f"/api/creation-tasks/{state.task.id}/{endpoint}", json=payload)
    assert response.status_code == 200, response.text
    bodies = calls if endpoint == "assistant/script-draft" else [body for _, body in calls]
    assert len(bodies) == (1 if endpoint == "assistant/script-draft" else 2)
    for body in bodies:
        model_input = json.loads(body["messages"][1]["content"])
        assert model_input["goal"] == state.goal and "Agent自拟" not in model_input["goal"]
        assert model_input.get("duration_target_seconds", model_input.get("requirements", {}).get("duration_target_seconds")) == 30
    if endpoint == "assistant/script-draft":
        assert json.loads(bodies[0]["messages"][1]["content"])["current_instruction"] == state.instruction


@pytest.mark.parametrize("endpoint", ["assistant/script-draft", "scripts/generate"])
@pytest.mark.parametrize("failure", ["private_request", "source"])
def test_main_rejects_unreadable_or_mismatched_goal_before_model_and_script_run(consumer_state, monkeypatch, endpoint, failure):
    import app.main as main
    state = consumer_state(failure=failure)
    monkeypatch.setattr(main, "_creation_task_script_generator_for",
        lambda *_: pytest.fail("来源拒绝必须发生在模型调用前"))
    payload = {**agent_payload(state), "assistant_turn_id": state.turn_id} if endpoint == "assistant/script-draft" else {}
    response = state.client.post(f"/api/creation-tasks/{state.task.id}/{endpoint}", json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"] == "script_goal_source_changed"
    assert state.repository.list_creation_task_scripts("local", state.task.id) == []
    with state.repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs WHERE task_id=?", (state.task.id,)).fetchone()[0] == 0
