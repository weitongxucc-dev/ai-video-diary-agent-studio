import pytest

from app.conversation_repository import ConversationRepository, ConversationTurnAlreadyRunning


def test_conversation_scope_member_draft_turn_events_and_restart(tmp_path):
    database = tmp_path / "isolated-conversations.sqlite3"
    first = ConversationRepository(database)
    home = first.current_or_create("workspace-one", "member-one", "home", "home")
    assert first.current_or_create("workspace-one", "member-one", "home", "home")["id"] == home["id"]
    assert first.get(home["id"], "workspace-one", "member-two") is None
    assert first.get(home["id"], "workspace-two", "member-one") is None

    first.update(home["id"], "workspace-one", "member-one", draft_text="保留的下一条草稿")
    turn = first.create_turn(home["id"], "请读取当前方案")
    with pytest.raises(ConversationTurnAlreadyRunning):
        first.create_turn(home["id"], "不应覆盖正在运行的轮次")
    first.append_event(turn["id"], {"type": "tool_start", "tool_id": 1, "label": "读取方案"})
    first.append_event(turn["id"], {"type": "text_delta", "delta": "读取完成"})
    first.finish_turn(turn["id"], "completed", "读取完成", {"kind": "plan_preview"})

    restarted = ConversationRepository(database)
    saved = restarted.get(home["id"], "workspace-one", "member-one")
    assert saved is not None
    assert saved["draft_text"] == ""
    assert restarted.list_turns(home["id"])[0]["events"] == [
        {"type": "tool_start", "tool_id": 1, "label": "读取方案"},
        {"type": "text_delta", "delta": "读取完成"},
    ]
    assert restarted.list_turns(home["id"])[0]["status"] == "completed"

    material = restarted.create("workspace-one", "member-one", "material", "asset:visible")
    assert restarted.get(material["id"], "workspace-one", "member-one")["scope_id"] == "asset:visible"


def test_current_selection_survives_old_turn_updates_and_explicit_resume(tmp_path):
    repository = ConversationRepository(tmp_path / "current-selection.sqlite3")
    older = repository.current_or_create("workspace-one", "member-one", "home", "home")
    old_turn = repository.create_turn(older["id"], "旧轮次")
    repository.append_event(old_turn["id"], {"type": "text_delta", "delta": "partial"})

    current = repository.create("workspace-one", "member-one", "home", "home", "新对话")
    repository.update(older["id"], "workspace-one", "member-one", draft_text="迟到的旧草稿")
    assert repository.fail_interrupted_turns() == 1
    repository.append_event(old_turn["id"], {"type": "text_delta", "delta": "不可追加"})
    repository.finish_turn(old_turn["id"], "completed", "被竞态覆盖", {"kind": "unexpected"})

    restarted = ConversationRepository(tmp_path / "current-selection.sqlite3")
    assert restarted.current_or_create("workspace-one", "member-one", "home", "home")["id"] == current["id"]
    old_saved = restarted.get_turn(old_turn["id"])
    assert old_saved is not None
    assert old_saved["status"] == "failed"
    assert old_saved["assistant_text"] == "partial"
    assert old_saved["result"] == {"reason": "api_restarted"}

    assert restarted.select_current(older["id"], "workspace-one", "member-one")["id"] == older["id"]
    assert ConversationRepository(tmp_path / "current-selection.sqlite3").current_or_create(
        "workspace-one", "member-one", "home", "home",
    )["id"] == older["id"]

    assert restarted.select_current(current["id"], "workspace-two", "member-one") is None
    assert restarted.current_or_create("workspace-one", "member-one", "home", "home")["id"] == older["id"]
    assert restarted.current_or_create("workspace-two", "member-one", "home", "home")["id"] != older["id"]


def test_only_completed_plan_turn_can_store_a_server_checked_preview(tmp_path):
    repository = ConversationRepository(tmp_path / "plan-preview.sqlite3")
    conversation = repository.current_or_create("workspace-one", "member-one", "home", "home")
    ordinary = repository.create_turn(conversation["id"], "普通问题")
    repository.finish_turn(ordinary["id"], "completed", "回答", {"kind": "conversation"})
    plan = repository.create_turn(conversation["id"], "创建一个短片方案")
    repository.finish_turn(plan["id"], "completed", "方案已检查", {
        "kind": "plan_preview", "plan_preview": {"duration_target_seconds": 30, "preflight_fingerprint": "a" * 64},
    })

    assert repository.update_completed_plan_preview(ordinary["id"], {"duration_target_seconds": 20}) is False
    assert repository.update_completed_plan_preview(plan["id"], {"duration_target_seconds": 45,
                                                                    "preflight_fingerprint": "b" * 64}) is True
    stored = ConversationRepository(tmp_path / "plan-preview.sqlite3").get_turn(plan["id"])
    assert stored["result"]["plan_preview"] == {"duration_target_seconds": 45,
                                                 "preflight_fingerprint": "b" * 64}


def test_failed_turn_persists_failure_result_without_erasing_partial_reply(tmp_path):
    repository = ConversationRepository(tmp_path / "failed-turn.sqlite3")
    conversation = repository.current_or_create("workspace-one", "member-one", "home", "home")
    turn = repository.create_turn(conversation["id"], "先检查当前条件")
    repository.append_event(turn["id"], {"type": "text_delta", "delta": "检查到连接问题。"})

    failure = {"kind": "failure", "message": "文字模型当前不可用；请先在设置中检查连接。"}
    repository.finish_turn(turn["id"], "failed", result=failure)
    repository.finish_turn(turn["id"], "completed", "不应覆盖失败终态", {"kind": "conversation"})

    saved = ConversationRepository(tmp_path / "failed-turn.sqlite3").get_turn(turn["id"])
    assert saved["status"] == "failed"
    assert saved["assistant_text"] == "检查到连接问题。"
    assert saved["result"] == failure
