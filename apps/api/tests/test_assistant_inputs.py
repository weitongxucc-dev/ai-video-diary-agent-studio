import pytest
from app.conversation_repository import ConversationRepository
from app.assistant.inputs import AssistantInputConflict


def test_input_receipt_consumes_draft_once_keeps_original_target_and_can_be_withdrawn(tmp_path):
    repo = ConversationRepository(tmp_path / "inputs.sqlite")
    chat = repo.create("w", "m", "home", "home")
    repo.save_draft(chat["id"], "w", "m", "第一轮要求", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "w", "m", "第一轮要求", "first", 1,
                                         {"kind": "task", "task_id": "t"})
    repo.record_task_execution(turn["id"],"w","m","t",{"task_id":"t","job":{"id":"existing-job","status":"queued"}})
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    repo.save_draft(chat["id"], "w", "m", "改成第二种字幕", 2)
    values = dict(target_turn_id=turn["id"], client_request_id="second", message="改成第二种字幕",
                  mode="steer", expected_draft_version=3, expected_reference_version=0,
                  request_context={"kind": "task", "task_id": "t", "selected_reference": None})
    receipt, created = repo.enqueue_input(chat["id"], "w", "m", **values)
    assert created and receipt["status"] == "accepted"
    assert repo.get(chat["id"], "w", "m")["draft_version"] == 4
    assert repo.enqueue_input(chat["id"], "w", "m", **values) == (receipt, False)
    with pytest.raises(AssistantInputConflict):
        repo.enqueue_input(chat["id"], "w", "m", **{**values, "message": "不同要求"})
    assert repo.task_turn_source(turn["id"], "w", "m", "t")["raw_command"] == "第一轮要求"
    repo.transition_input(chat["id"], "w", "m", "second", "consumed")
    assert repo.get_task_goal(turn["id"])["status"] == "invalidated"
    assert repo.get_task_goal(turn["id"])["waiting_reason"] == "consumed_input_superseded_business_goal"
    source = repo.task_turn_source(turn["id"], "w", "m", "t")
    assert source["raw_command"] == "改成第二种字幕" and source["input_id"] == "second"
    assert repo.business_turn_context(turn["id"], "w", "m", "t")["context"] == values["request_context"]
    assert repo.consumed_turn_request_context(turn["id"], chat["id"], "w", "m") == values["request_context"]
    with pytest.raises(AssistantInputConflict):
        repo.transition_input(chat["id"], "w", "m", "second", "withdrawn")
    repo.transition_input(chat["id"], "w", "m", "second", "completed")
    assert ConversationRepository(repo.database_path).list_inputs(chat["id"], "w", "m")[0]["status"] == "completed"
    assert repo.list_inputs(chat["id"], "w", "other") == []
    repo.save_draft(chat["id"], "w", "m", "第三种字幕", 4)
    next_context={"kind":"task", "task_id":"t", "selected_reference":None, "selected_object_refs":["cue:third"]}
    repo.enqueue_input(chat["id"], "w", "m", **{**values, "client_request_id":"third", "message":"第三种字幕",
                       "expected_draft_version":5, "request_context":next_context})
    assert repo.consumed_turn_request_context(turn["id"], chat["id"], "w", "m") == values["request_context"]
    repo.transition_input(chat["id"], "w", "m", "third", "consumed")
    assert repo.business_turn_context(turn["id"], "w", "m", "t")["context"] == next_context
    restarted=ConversationRepository(repo.database_path)
    assert restarted.fail_interrupted_turns() == 1
    assert restarted.list_inputs(chat["id"], "w", "m")[-1]["status"] == "interrupted"


def test_withdrawn_input_does_not_change_tool_authorization(tmp_path):
    repo = ConversationRepository(tmp_path / "withdrawn.sqlite")
    chat = repo.create("w", "m", "home", "home")
    repo.save_draft(chat["id"], "w", "m", "第一轮要求", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "w", "m", "第一轮要求", "first", 1, {"kind": "task", "task_id": "t"})
    repo.save_draft(chat["id"], "w", "m", "完成后再改", 2)
    repo.enqueue_input(chat["id"], "w", "m", target_turn_id=turn["id"], client_request_id="later", message="完成后再改",
                       mode="follow_up", expected_draft_version=3, expected_reference_version=0,
                       request_context={"kind": "task", "task_id": "t", "selected_reference": None})
    repo.transition_input(chat["id"], "w", "m", "later", "withdrawn")
    with pytest.raises(AssistantInputConflict):
        repo.transition_input(chat["id"], "w", "m", "later", "consumed")
    assert repo.task_turn_source(turn["id"], "w", "m", "t")["raw_command"] == "第一轮要求"
