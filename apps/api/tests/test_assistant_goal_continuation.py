"""Real isolated conversation persistence; model and current-principal seams only."""
from uuid import uuid4

import pytest

from app.assistant_goal_continuation import GoalContinuationService
from app.conversation_repository import ConversationRepository


@pytest.fixture
def repo(tmp_path):
    return ConversationRepository(tmp_path / "continuation.sqlite3")


def original(repo, *, message="生成一张标题图片，放进当前镜头，采用后生成成片", register=True):
    chat = repo.create("workspace", "member", "task", "task")
    chat = repo.save_draft(chat["id"], "workspace", "member", message, chat["draft_version"])
    context = {"kind": "task", "task_id": "task", "stage": "montage", "selected_object_refs": ["clip:one"],
               "target_snapshot": {"timeline_version_id": "version:one", "timeline_sequence": 1}}
    request_id = str(uuid4())
    turn, inserted = repo.create_turn_idempotent(chat["id"], "workspace", "member", message,
        request_id, chat["draft_version"], request_context=context)
    assert inserted
    repo.finish_turn(turn["id"], "completed", "图片正在生成。", result={"kind": "task_turn",
        "response": {"media_job": {"job_id": "png:one", "status": "queued"}, "edit_request": None}})
    if register:
        assert repo.register_task_goal(turn["id"])
    return chat, turn, request_id, context


def emit(repo, turn, *, status="succeeded", phase="preview", result_kind="graphic_source", **fields):
    event = {"type": "business_result", "event_id": str(uuid4()), "task_id": "task", "status": status,
             "phase": phase, "result_kind": result_kind, **fields}
    assert repo.project_business_result(turn["id"], "workspace", "member", event,
        response_patch={}, message="实际业务结果已核验。")
    return event


def service(repo, *, response=None, readiness=None, max_rounds=6, action_refs=None):
    calls = []
    def verify(goal, event):
        return readiness or {"status": "ready", "task_version": {"timeline_version_id": "version:one", "timeline_sequence": 1},
                             "facts": {"current": True}, "no_side_effect": True}
    def run(claim, fresh):
        calls.append((claim, fresh))
        source = repo.task_turn_source(claim["turn_id"], "workspace", "member", "task")
        assert source["raw_command"] == claim["goal"]["original_instruction"]
        actual = response if response is not None else {"edit_preview": {"proposal": {"id": "proposal:one", "status": "proposed"}}}
        proofs = [{"kind": kind, "id": identifier} for kind, identifier in (
            ("edit_preview", ((actual.get("edit_preview") or {}).get("proposal") or {}).get("id")),
            ("media_job", (actual.get("media_job") or {}).get("job_id")),
            ("edit_request", (actual.get("edit_request") or {}).get("request_id")),
            ("execution_status", ((actual.get("execution_status") or {}).get("job") or {}).get("id"))) if identifier]
        repo.finish_turn(claim["turn_id"], "completed", "下一步已核验。", result={"kind": "task_turn",
            "response": actual, "goal_business_actions": proofs if action_refs is None else action_refs})
    return GoalContinuationService(repo, verify_current_goal=verify, run_goal_turn=run, max_rounds=max_rounds), calls


def test_real_result_starts_one_empty_human_turn_preserves_instruction_and_new_draft(repo):
    chat, turn, request_id, context = original(repo)
    event = emit(repo, turn)
    chat = repo.get(chat["id"], "workspace", "member")
    newer = repo.save_draft(chat["id"], "workspace", "member", "未发送的新草稿", chat["draft_version"])
    svc, calls = service(repo)
    assert svc.reconcile() == 1
    descendant = repo.list_turns(chat["id"])[1]
    assert descendant["user_message"] == ""
    assert descendant["events"][0] == {"type": "goal_continuation_started", "source_kind": "business_event",
        "original_turn_id": turn["id"], "source_turn_id": turn["id"], "event_id": event["event_id"]}
    assert calls[0][0]["goal"]["original_instruction"] == turn["user_message"]
    assert repo.get(chat["id"], "workspace", "member")["draft_text"] == newer["draft_text"]
    assert repo.get(chat["id"], "workspace", "member")["draft_version"] == newer["draft_version"]
    replay, created = repo.create_turn_idempotent(chat["id"], "workspace", "member", turn["user_message"],
        request_id, 1, request_context=context)
    assert not created and replay["id"] == turn["id"]
    assert svc.reconcile() == 0
    restarted, restart_calls = service(ConversationRepository(repo.database_path))
    assert restarted.reconcile() == 0 and restart_calls == []


def test_old_completed_history_and_media_events_do_not_infer_a_goal(repo):
    chat, turn, _, _ = original(repo, register=False)
    emit(repo, turn)
    svc, calls = service(ConversationRepository(repo.database_path))
    assert svc.reconcile() == 0 and calls == []
    assert len(repo.list_turns(chat["id"])) == 1


def test_proposal_waits_real_adoption_then_new_version_drives_remaining_goal(repo):
    chat, turn, _, _ = original(repo)
    emit(repo, turn, status="proposed")
    svc, calls = service(repo)
    assert svc.reconcile() == 1 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_adoption"
    event = emit(repo, turn, status="confirmed", phase="apply", result_version_id="version:two")
    svc, calls = service(repo, response={"execution_status": {"job": {"id": "render:one", "status": "queued"}}},
        readiness={"status": "ready", "task_version": {"timeline_version_id": "version:two", "timeline_sequence": 2}})
    assert svc.reconcile() == 1
    assert calls[0][0]["event"] == event
    assert calls[0][0]["request_context"]["target_snapshot"]["timeline_version_id"] == "version:two"
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    assert len(repo.list_turns(chat["id"])) == 2


@pytest.mark.parametrize("status", ["rejected", "cancelled", "stopped", "stale"])
def test_actual_rejection_stop_cancel_or_stale_cannot_run_model(repo, status):
    _, turn, _, _ = original(repo)
    emit(repo, turn, status=status, phase="decision")
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == "invalidated"


def test_new_real_user_instruction_invalidates_prior_goal_and_late_registration(repo):
    chat, turn, _, _ = original(repo)
    emit(repo, turn)
    latest = repo.get(chat["id"], "workspace", "member")
    latest = repo.save_draft(chat["id"], "workspace", "member", "只读取当前结果", latest["draft_version"])
    new, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", latest["draft_text"],
        str(uuid4()), latest["draft_version"], request_context={"kind": "task", "task_id": "task"})
    repo.finish_turn(new["id"], "completed", "已读取。")
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert repo.get_task_goal(turn["id"])["waiting_reason"] == "new_user_instruction"
    assert repo.register_task_goal(turn["id"]) is None


@pytest.mark.parametrize("ready", ["waiting_identity", "invalidated"])
def test_revoked_identity_or_unrelated_version_prevents_continuation(repo, ready):
    _, turn, _, _ = original(repo)
    emit(repo, turn)
    svc, calls = service(repo, readiness={"status": ready, "reason": "membership_revoked_or_version_changed"})
    assert svc.reconcile() == 0 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == ready


def test_unknown_submission_only_waits_and_known_no_effect_can_finitely_correct(repo):
    _, turn, _, _ = original(repo)
    event = emit(repo, turn, status="result_unknown", phase="request")
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert event["event_id"] in repo.get_task_goal(turn["id"])["handled_event_ids"]
    # A later actual query result is a new persisted event, never a replay of
    # the original submission or a new user instruction.
    emit(repo, turn, status="not_submitted", phase="request")
    svc, calls = service(repo, readiness={"status": "ready", "task_version": {}, "no_side_effect": False})
    assert svc.reconcile() == 0 and calls == []
    svc, calls = service(repo)
    assert svc.reconcile() == 1 and len(calls) == 1


def test_no_progress_and_round_cap_end_automatic_model_calls(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn)
    svc, calls = service(repo, response={})
    assert svc.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == "no_progress"
    assert svc.reconcile() == 0 and len(calls) == 1
    _, bounded, _, _ = original(repo)
    emit(repo, bounded)
    svc, calls = service(repo, response={"media_job": {"job_id": "observation:one", "status": "queued"}}, max_rounds=1)
    assert svc.reconcile() == 1
    child = {"id": calls[0][0]["turn_id"]}
    emit(repo, child, result_kind="output_observation")
    assert svc.reconcile() == 0 and len(calls) == 1
    assert repo.get_task_goal(bounded["id"])["status"] == "limit_reached"


def test_interrupted_claim_is_not_replayed_but_new_actual_result_can_continue(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn)
    candidate = repo.goal_event_candidates()[0]
    claimed = repo.claim_goal_event(candidate, {}, max_rounds=6)
    assert repo.fail_interrupted_turns() == 1
    svc, calls = service(ConversationRepository(repo.database_path))
    assert svc.reconcile() == 0 and calls == []
    emit(repo, {"id": claimed["turn_id"]}, result_kind="output_observation")
    assert svc.reconcile() == 1 and len(calls) == 1


def test_cannot_claim_an_event_from_another_goal(repo):
    chat, turn, _, _ = original(repo)
    event = emit(repo, turn)
    other_chat, other, _, _ = original(repo)
    emit(repo, other)
    candidate = repo.goal_event_candidates()[0]
    candidate["source_turn_id"] = other["id"]
    candidate["event"] = event
    with pytest.raises(ValueError, match="持久化的业务事件"):
        repo.claim_goal_event(candidate, {}, max_rounds=6)
    assert len(repo.list_turns(chat["id"])) == len(repo.list_turns(other_chat["id"])) == 1


@pytest.mark.parametrize("kind,receipt,event_fields", [
    ("media_job", {"job_id": "png:one", "status": "succeeded"}, {"job_id": "png:one"}),
    ("media_job", {"job_id": "unrelated:old", "status": "succeeded"}, {"job_id": "png:one"}),
    ("edit_preview", {"proposal": {"id": "proposal:old", "status": "confirmed"}}, {"proposal_id": "proposal:old"}),
    ("edit_request", {"request_id": "request:old", "status": "recorded"}, {"request_id": "request:old"}),
    ("execution_status", {"job": {"id": "render:old", "status": "succeeded"}}, {"job_id": "render:old"}),
])
def test_reading_settled_old_receipts_does_not_wait_for_an_event_that_will_never_arrive(repo, kind, receipt, event_fields):
    _, turn, _, _ = original(repo)
    event = emit(repo, turn, **event_fields)
    before = list(repo.get_turn(turn["id"])["events"])
    svc, calls = service(repo, response={kind: receipt}, action_refs=[])
    assert svc.reconcile() == 1 and len(calls) == 1
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == "no_progress" and goal["unfinished_steps"] == []
    assert event in repo.get_turn(turn["id"])["events"] == before
    assert svc.reconcile() == 0


def test_new_child_action_already_finished_still_waits_for_its_own_actual_event(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    svc, calls = service(repo, response={"media_job": {"job_id": "observation:new", "status": "succeeded"}})
    assert svc.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    child = {"id": calls[0][0]["turn_id"]}
    emit(repo, child, job_id="observation:new", result_kind="observation")
    readonly, reads = service(repo, response={"media_job": {"job_id": "observation:new", "status": "succeeded"}}, action_refs=[])
    assert readonly.reconcile() == 1 and len(reads) == 1
    assert repo.get_task_goal(turn["id"])["status"] == "no_progress"


@pytest.mark.parametrize("job_id,expected", [("png:one", "waiting_result"), ("unrelated:pending", "no_progress")])
def test_pending_work_must_belong_to_the_original_goal_or_a_new_child_action(repo, job_id, expected):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    svc, _ = service(repo, response={"media_job": {"job_id": job_id, "status": "running"}}, action_refs=[])
    assert svc.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == expected
