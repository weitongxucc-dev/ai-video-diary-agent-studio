"""Real conversation adoption/claim/input receipts; no model or media execution."""
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.conversation_repository import ConversationRepository
from test_assistant_input_goals import started, consume, result, emit


VERSION = {"timeline_version_id": "v1", "timeline_sequence": 1}


@pytest.fixture
def repo(tmp_path):
    return ConversationRepository(tmp_path / "execution-source.sqlite3")


def human_turn(repo, chat, message, context):
    current = repo.get(chat["id"], "w", "m")
    draft = repo.save_draft(chat["id"], "w", "m", message, current["draft_version"])
    turn, created = repo.create_turn_idempotent(chat["id"], "w", "m", message, str(uuid4()),
        draft["draft_version"], request_context=context)
    assert created
    return turn


def additional_input(repo, chat, turn_id, *, context, status="consumed"):
    message = "按本轮新要求生成，保留字幕修改。"
    current = repo.get(chat["id"], "w", "m")
    draft = repo.save_draft(chat["id"], "w", "m", message, current["draft_version"])
    input_id = str(uuid4())
    repo.enqueue_input(chat["id"], "w", "m", target_turn_id=turn_id, client_request_id=input_id,
        message=message, mode="steer", expected_draft_version=draft["draft_version"], expected_reference_version=0,
        request_context=context)
    if status != "accepted":
        repo.transition_input(chat["id"], "w", "m", input_id, "consumed" if status == "interrupted" else status)
    if status == "interrupted":
        repo.transition_input(chat["id"], "w", "m", input_id, "interrupted")
    return input_id, message


def adopted_goals(repo, *, home_input=False, complete_home_input=True):
    chat = repo.create("w", "m", "home", "home")
    instruction = "请自主制作30秒脱壳机介绍，中英文各一条。"
    human = human_turn(repo, chat, instruction, {"kind": "home"})
    source = {"kind": "conversation_input", "turn_id": human["id"], "conversation_id": chat["id"], "member_id": "m"}
    if home_input:
        input_id, instruction = additional_input(repo, chat, human["id"], context={"kind": "home"})
        source.update(input_id=input_id, input_turn_id=human["id"])
        if complete_home_input:
            repo.transition_input(chat["id"], "w", "m", input_id, "completed")
    source["message_digest"] = hashlib.sha256(instruction.encode()).hexdigest()
    repo.finish_turn(human["id"], "completed", "方案已采用。")
    roots = repo.register_creation_task_goals(human["id"], "w", "m",
        {"request_id": "request", "window_id": "window", "source": source},
        [{"kind": "task", "task_id": task, "stage": "script", "target_snapshot": VERSION} for task in ("cn", "en")])
    assert len(roots) == 2
    return SimpleNamespace(repo=repo, chat=chat, human=human, roots=roots, instruction=instruction, source=source)


def claim_task(state, task):
    candidate = next(item for item in state.repo.goal_event_candidates() if item["root"]["goal"]["task_id"] == task)
    claim = state.repo.claim_goal_event(candidate, VERSION, max_rounds=6)
    assert claim is not None
    return claim


@pytest.mark.parametrize("home_input", [False, True])
def test_both_adopted_tasks_submit_and_resume_under_the_same_actual_human(repo, home_input):
    state = adopted_goals(repo, home_input=home_input)
    for task in ("cn", "en"):
        claim = claim_task(state, task)
        source = repo.task_execution_source(claim["turn_id"], "w", "m", task)
        assert source == repo.task_turn_source(claim["turn_id"], "w", "m", task)
        assert source["context"]["original_turn_id"] in state.roots
        assert source["context"]["original_turn_id"] != state.human["id"]
        assert source["raw_command"] == state.instruction and "input_id" not in source
        execution = {"task_id": task, "job": {"id": f"job-{task}", "status": "queued"}}
        assert repo.record_task_execution(claim["turn_id"], "w", "m", task, execution)
        # Re-registering a business child is not required to replace the adopted root.
        assert repo.get_task_goal(claim["turn_id"])["creation_adoption"]["turn_id"] == state.human["id"]
        repo.finish_turn(claim["turn_id"], "completed", result={"response": {"execution_status": execution}})
        assert repo.settle_goal_round(claim)["status"] == "waiting_result"
        event = {"type": "business_result", "event_id": f"completed-{task}", "phase": "execution",
            "task_id": task, "job_id": f"job-{task}", "status": "succeeded", "base_version_id": "v1", "result_version_id": "v1"}
        assert repo.project_business_result(claim["turn_id"], "w", "m", event, response_patch={}, message="实际结果。")
        resumed = claim_task(state, task)
        assert repo.task_execution_source(resumed["turn_id"], "w", "m", task)["raw_command"] == state.instruction
        repo.finish_turn(resumed["turn_id"], "completed", result={})
        repo.settle_goal_round(resumed)
    assert [turn["user_message"] for turn in repo.list_turns(state.chat["id"]) if turn["user_message"]] == [state.human["user_message"]]


def test_real_later_human_invalidates_both_derived_tasks(repo):
    state = adopted_goals(repo)
    stale = repo.goal_event_candidates()
    newer = human_turn(repo, state.chat, "停止旧目标，只讨论新方案。", {"kind": "home"})
    for candidate in stale:
        assert repo.claim_goal_event(candidate, VERSION, max_rounds=6) is None
        assert repo.get_task_goal(candidate["root"]["id"])["status"] == "invalidated"
    repo.finish_turn(newer["id"], "completed")
    with pytest.raises(ValueError):
        repo.task_execution_source(state.roots[0], "w", "m", "cn")


@pytest.mark.parametrize("status", ["accepted", "withdrawn", "consumed", "interrupted"])
def test_derived_child_new_input_requires_actual_consumption_and_matching_request(repo, status):
    state = adopted_goals(repo)
    claim = claim_task(state, "cn")
    original = repo.task_turn_source(claim["turn_id"], "w", "m", "cn")
    input_id, message = additional_input(repo, state.chat, claim["turn_id"], status=status,
        context={"kind": "task", "task_id": "cn", "stage": "narration", "target_snapshot": VERSION})
    if status == "consumed":
        with pytest.raises(ValueError, match="当前已消费输入"):
            repo.task_execution_source(claim["turn_id"], "w", "m", "cn")
        fresh = repo.task_execution_source(claim["turn_id"], "w", "m", "cn", input_id=input_id)
        assert fresh["input_id"] == input_id and fresh["raw_command"] == message
        assert fresh == repo.task_turn_source(claim["turn_id"], "w", "m", "cn")
    elif status == "interrupted":
        for requested in (None, input_id):
            with pytest.raises(ValueError, match="中断"):
                repo.task_execution_source(claim["turn_id"], "w", "m", "cn", input_id=requested)
    else:
        assert repo.task_execution_source(claim["turn_id"], "w", "m", "cn") == original
        with pytest.raises(ValueError, match="当前已消费输入"):
            repo.task_execution_source(claim["turn_id"], "w", "m", "cn", input_id=input_id)


@pytest.mark.parametrize("context", [{"kind": "task", "task_id": "en"}, {"kind": "home"}])
def test_consumed_input_cannot_change_the_childs_bound_task(repo, context):
    state = adopted_goals(repo)
    claim = claim_task(state, "cn")
    input_id, _ = additional_input(repo, state.chat, claim["turn_id"], context=context)
    with pytest.raises(ValueError, match="追加输入不属于当前工具目标"):
        repo.task_execution_source(claim["turn_id"], "w", "m", "cn", input_id=input_id)


@pytest.mark.parametrize("workspace,member,task", [("other", "m", "cn"), ("w", "other", "cn"), ("w", "m", "en")])
def test_derived_execution_does_not_cross_member_workspace_or_task(repo, workspace, member, task):
    state = adopted_goals(repo)
    claim = claim_task(state, "cn")
    with pytest.raises(ValueError):
        repo.task_execution_source(claim["turn_id"], workspace, member, task)


def test_claims_frozen_goal_revision_cannot_authorize_a_later_revision(repo):
    state = adopted_goals(repo)
    claim = claim_task(state, "cn")
    # Isolated persisted-state fault injection: the live root advanced while
    # this claim's immutable request receipt still names revision zero.
    with repo._connect() as conn:
        root = repo._goal_root(conn, claim["turn_id"])
        repo._write_goal(conn, root, {**root["goal"], "revision": 1})
    with pytest.raises(ValueError, match="原创作目标已停止"):
        repo.task_execution_source(claim["turn_id"], "w", "m", "cn")


def test_interrupted_adopted_home_input_cannot_authorize_a_claim(repo):
    state = adopted_goals(repo, home_input=True, complete_home_input=False)
    assert repo.list_inputs(state.chat["id"], "w", "m")[0]["status"] == "interrupted"
    candidate = next(item for item in repo.goal_event_candidates() if item["root"]["goal"]["task_id"] == "cn")
    assert repo.claim_goal_event(candidate, VERSION, max_rounds=6) is None


def test_ordinary_task_and_its_consumed_input_continuation_keep_current_source(repo):
    chat, turn = started(repo)
    assert repo.task_execution_source(turn["id"], "w", "m", "t")["raw_command"] == turn["user_message"]
    input_id = consume(repo, chat, turn, message="字幕改蓝，继续生成。")
    assert repo.task_execution_source(turn["id"], "w", "m", "t", input_id=input_id)["input_id"] == input_id
    with pytest.raises(ValueError):
        repo.task_execution_source(turn["id"], "w", "m", "t")
    result(repo, turn, input_id, "new-job")
    emit(repo, turn, input_id, "new-job-completed")
    claim = repo.claim_goal_event(repo.goal_event_candidates()[0], VERSION, max_rounds=6)
    assert claim is not None
    source = repo.task_execution_source(claim["turn_id"], "w", "m", "t", input_id=input_id)
    assert source["input_turn_id"] == turn["id"] and "字幕改蓝" in source["raw_command"]
    with pytest.raises(ValueError):
        repo.task_execution_source(claim["turn_id"], "w", "m", "t", input_id="stale-input")
