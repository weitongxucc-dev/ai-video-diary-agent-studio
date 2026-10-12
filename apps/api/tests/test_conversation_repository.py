import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from app.conversation_repository import (
    ConversationDraftVersionConflict,
    ConversationReferenceVersionConflict,
    ConversationRepository,
    ConversationTurnAlreadyRunning,
    ConversationTurnRequestConflict,
)


def _send(repository: ConversationRepository, conversation: dict, message: str, request_id: str) -> dict:
    owner = (conversation["workspace_id"], conversation["member_id"])
    current = repository.get(conversation["id"], *owner)
    assert current is not None
    saved = repository.save_draft(conversation["id"], *owner, message, current["draft_version"])
    assert saved is not None
    turn, created = repository.create_turn_idempotent(
        conversation["id"], *owner, message, request_id, saved["draft_version"],
    )
    assert created and turn is not None
    return turn


def test_conversation_scope_member_draft_turn_events_and_restart(tmp_path):
    database = tmp_path / "isolated-conversations.sqlite3"
    first = ConversationRepository(database)
    home = first.current_or_create("workspace-one", "member-one", "home", "home")
    assert first.current_or_create("workspace-one", "member-one", "home", "home")["id"] == home["id"]
    assert first.get(home["id"], "workspace-one", "member-two") is None
    assert first.get(home["id"], "workspace-two", "member-one") is None

    drafted = first.save_draft(home["id"], "workspace-one", "member-one", "保留的下一条草稿", 0)
    assert drafted is not None and drafted["draft_version"] == 1
    turn = _send(first, home, "请读取当前方案", "turn-home-1")
    consumed = first.get(home["id"], "workspace-one", "member-one")
    assert consumed is not None and consumed["draft_version"] == 3
    running_draft = first.save_draft(home["id"], "workspace-one", "member-one", "不应覆盖正在运行的轮次", consumed["draft_version"])
    assert running_draft is not None
    with pytest.raises(ConversationTurnAlreadyRunning):
        first.create_turn_idempotent(home["id"], "workspace-one", "member-one", "不应覆盖正在运行的轮次", "turn-home-2", running_draft["draft_version"])
    first.append_event(turn["id"], {"type": "tool_start", "tool_id": 1, "label": "读取方案"})
    first.append_event(turn["id"], {"type": "text_delta", "delta": "读取完成"})
    first.finish_turn(turn["id"], "completed", "读取完成", {"kind": "plan_preview"})

    restarted = ConversationRepository(database)
    saved = restarted.get(home["id"], "workspace-one", "member-one")
    assert saved is not None
    assert saved["draft_text"] == "不应覆盖正在运行的轮次"
    assert saved["draft_version"] == running_draft["draft_version"]
    assert restarted.list_turns(home["id"])[0]["events"] == [
        {"type": "tool_start", "tool_id": 1, "label": "读取方案"},
        {"type": "text_delta", "delta": "读取完成"},
    ]
    assert restarted.list_turns(home["id"])[0]["status"] == "completed"

    material = restarted.create("workspace-one", "member-one", "material", "asset:visible")
    assert restarted.get(material["id"], "workspace-one", "member-one")["scope_id"] == "asset:visible"
    library = restarted.create("workspace-one", "member-one", "material", "library")
    indexed_material = restarted.list_for_scope("workspace-one", "member-one", "material", "library")
    assert {row["id"] for row in indexed_material} == {material["id"], library["id"]}


def test_current_selection_survives_old_turn_updates_and_explicit_resume(tmp_path):
    repository = ConversationRepository(tmp_path / "current-selection.sqlite3")
    older = repository.current_or_create("workspace-one", "member-one", "home", "home")
    old_turn = _send(repository, older, "旧轮次", "old-turn-request")
    repository.append_event(old_turn["id"], {"type": "text_delta", "delta": "partial"})

    current = repository.create("workspace-one", "member-one", "home", "home", "新对话")
    after_turn = repository.get(older["id"], "workspace-one", "member-one")
    assert after_turn is not None
    repository.save_draft(older["id"], "workspace-one", "member-one", "迟到的旧草稿", after_turn["draft_version"])
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
    ordinary = _send(repository, conversation, "普通问题", "ordinary-turn")
    repository.finish_turn(ordinary["id"], "completed", "回答", {"kind": "conversation"})
    plan = _send(repository, conversation, "创建一个短片方案", "plan-turn")
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
    turn = _send(repository, conversation, "先检查当前条件", "failed-turn")
    repository.append_event(turn["id"], {"type": "text_delta", "delta": "检查到连接问题。"})

    failure = {"kind": "failure", "message": "文字模型当前不可用；请先在设置中检查连接。"}
    repository.finish_turn(turn["id"], "failed", result=failure)
    repository.finish_turn(turn["id"], "completed", "不应覆盖失败终态", {"kind": "conversation"})

    saved = ConversationRepository(tmp_path / "failed-turn.sqlite3").get_turn(turn["id"])
    assert saved["status"] == "failed"
    assert saved["assistant_text"] == "检查到连接问题。"
    assert saved["result"] == failure


def test_existing_conversation_and_turn_schema_upgrades_without_losing_saved_text(tmp_path):
    database = tmp_path / "legacy-conversations.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE assistant_conversations (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, member_id TEXT NOT NULL,
                scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, title TEXT NOT NULL,
                draft_text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE assistant_conversation_turns (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                user_message TEXT NOT NULL, assistant_text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                events_json TEXT NOT NULL DEFAULT '[]', result_json TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE(conversation_id, sequence)
            );
            INSERT INTO assistant_conversations VALUES
                ('legacy', 'workspace-one', 'member-one', 'home', 'home', '旧会话', '保留的旧草稿', 'a', 'b');
            INSERT INTO assistant_conversation_turns VALUES
                ('old-turn', 'legacy', 1, '旧请求', '旧回复', 'completed', '[]', NULL, 'a', 'b');
        """)

    repository = ConversationRepository(database)
    saved = repository.get("legacy", "workspace-one", "member-one")
    turn = repository.get_turn("old-turn")
    assert saved is not None
    assert saved["draft_text"] == "保留的旧草稿"
    assert saved["draft_version"] == 0
    assert turn is not None
    assert turn["user_message"] == "旧请求"
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM assistant_conversation_request_receipts").fetchone()[0] == 0


def test_draft_cas_serializes_two_repository_instances(tmp_path):
    database = tmp_path / "draft-cas.sqlite3"
    first = ConversationRepository(database)
    conversation = first.current_or_create("workspace-one", "member-one", "home", "home")
    second = ConversationRepository(database)

    def save(repository: ConversationRepository, text: str):
        try:
            return repository.save_draft(conversation["id"], "workspace-one", "member-one", text, 0)
        except ConversationDraftVersionConflict as conflict:
            return conflict

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda args: save(*args), [(first, "草稿A"), (second, "草稿B")]))

    saved = first.get(conversation["id"], "workspace-one", "member-one")
    assert saved is not None
    assert saved["draft_version"] == 1
    assert saved["draft_text"] in {"草稿A", "草稿B"}
    assert sum(isinstance(result, ConversationDraftVersionConflict) for result in results) == 1
    assert first.save_draft(conversation["id"], "workspace-one", "wrong-member", "越权", 1) is None


@pytest.mark.parametrize("winner_index", [0, 1])
def test_turn_request_is_idempotent_and_rejects_reused_identity_with_other_content_or_scope(tmp_path, winner_index):
    database = tmp_path / "turn-request.sqlite3"
    repository = ConversationRepository(database)
    second_repository = ConversationRepository(database)
    home = repository.current_or_create("workspace-one", "member-one", "home", "home")
    draft = repository.save_draft(home["id"], "workspace-one", "member-one", "读取现在的方案", 0)
    assert draft is not None

    repositories = [repository, second_repository]
    both_started = Barrier(2)
    winner_reading_receipt = Event()
    loser_waiting_to_begin = Event()
    synchronization_errors: list[str] = []

    for index, repo in enumerate(repositories):
        original_connect = repo._connect

        def connect(*, index=index, original_connect=original_connect):
            connection = original_connect()

            def coordinate_lock(statement: str) -> None:
                normalized = statement.lstrip().upper()
                if index == winner_index and normalized.startswith(
                    "SELECT * FROM ASSISTANT_CONVERSATION_REQUEST_RECEIPTS"
                ):
                    winner_reading_receipt.set()
                    if not loser_waiting_to_begin.wait(timeout=10):
                        synchronization_errors.append("loser did not contend for the write lock")
                elif index != winner_index and normalized == "BEGIN IMMEDIATE":
                    if not winner_reading_receipt.wait(timeout=10):
                        synchronization_errors.append("designated winner did not reach the receipt read")
                    loser_waiting_to_begin.set()

            connection.set_trace_callback(coordinate_lock)
            return connection

        repo._connect = connect

    def send(repo: ConversationRepository):
        both_started.wait()
        return repo.create_turn_idempotent(
            home["id"], "workspace-one", "member-one", "读取现在的方案", "request-one", draft["draft_version"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(send, repositories))
    assert synchronization_errors == []
    assert results[winner_index][1] is True
    assert results[1 - winner_index][1] is False
    turn = results[winner_index][0]
    replay = results[1 - winner_index][0]
    assert turn is not None and replay is not None
    assert replay["id"] == turn["id"]
    assert turn["user_message"] == "读取现在的方案"
    assert len(repository.list_turns(home["id"])) == 1
    with repository._connect() as connection:
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM assistant_conversation_request_receipts WHERE client_request_id=?",
            ("request-one",),
        ).fetchone()[0]
    assert receipt_count == 1
    repository.finish_turn(turn["id"], "completed", "动作已完成")
    terminal_replay, terminal_replay_created = repository.create_turn_idempotent(
        home["id"], "workspace-one", "member-one", "读取现在的方案", "request-one", draft["draft_version"],
    )
    assert terminal_replay is not None and terminal_replay_created is False
    assert terminal_replay["id"] == turn["id"]
    assert terminal_replay["status"] == "completed"
    assert len(repository.list_turns(home["id"])) == 1
    consumed = repository.get(home["id"], "workspace-one", "member-one")
    assert consumed is not None
    assert consumed["draft_text"] == ""
    assert consumed["draft_version"] == draft["draft_version"] + 1

    with pytest.raises(ConversationTurnRequestConflict):
        repository.create_turn_idempotent(
            home["id"], "workspace-one", "member-one", "另一条要求", "request-one", draft["draft_version"],
        )
    other_scope = repository.current_or_create("workspace-one", "member-one", "task", "task-one")
    with pytest.raises(ConversationTurnRequestConflict):
        repository.create_turn_idempotent(
            other_scope["id"], "workspace-one", "member-one", "读取现在的方案", "request-one", 0,
        )
    with pytest.raises(ConversationDraftVersionConflict):
        repository.create_turn_idempotent(
            home["id"], "workspace-one", "member-one", "不同请求", "request-two", draft["draft_version"],
        )
    assert len(repository.list_turns(home["id"])) == 1


def test_failure_recovery_restores_only_an_unpersisted_unchanged_draft_version(tmp_path):
    repository = ConversationRepository(tmp_path / "recovery-cas.sqlite3")
    conversation = repository.current_or_create("workspace-one", "member-one", "home", "home")
    prepared = repository.save_draft(conversation["id"], "workspace-one", "member-one", "原始已发送句子", 0)
    assert prepared is not None
    restored, outcome = repository.restore_unpersisted_turn_draft(
        conversation["id"], "workspace-one", "member-one", "request-unpersisted", "原始已发送句子", prepared["draft_version"],
    )
    assert restored is not None and outcome == "restored"
    assert restored["draft_text"] == "原始已发送句子"
    assert restored["draft_version"] == 2
    with pytest.raises(ConversationTurnRequestConflict):
        repository.restore_unpersisted_turn_draft(
            conversation["id"], "workspace-one", "member-one", "request-unpersisted", "被改写的同ID请求", prepared["draft_version"],
        )
    restored, outcome = repository.restore_unpersisted_turn_draft(
        conversation["id"], "workspace-one", "member-one", "request-unpersisted", "原始已发送句子", prepared["draft_version"],
    )
    assert restored is not None and outcome == "already_recovered"
    assert restored["draft_version"] == 2

    newer = repository.save_draft(conversation["id"], "workspace-one", "member-one", "后来编辑的新草稿", 2)
    assert newer is not None and newer["draft_version"] == 3
    with pytest.raises(ConversationDraftVersionConflict):
        repository.restore_unpersisted_turn_draft(
        conversation["id"], "workspace-one", "member-one", "request-stale", "旧失败恢复句", prepared["draft_version"],
        )
    current = repository.get(conversation["id"], "workspace-one", "member-one")
    assert current is not None
    assert current["draft_text"] == "后来编辑的新草稿"
    assert current["draft_version"] == 3

    prepared = repository.save_draft(conversation["id"], "workspace-one", "member-one", "新的真实轮次", 3)
    assert prepared is not None and prepared["draft_version"] == 4
    durable, created = repository.create_turn_idempotent(
        conversation["id"], "workspace-one", "member-one", "新的真实轮次", "request-persisted", 4,
    )
    assert durable is not None and created is True
    current, outcome = repository.restore_unpersisted_turn_draft(
        conversation["id"], "workspace-one", "member-one", "request-persisted", "新的真实轮次", 4,
    )
    assert current is not None and outcome == "already_persisted"
    assert current["draft_text"] == ""


def test_failure_recovery_restores_when_the_checkpoint_never_committed(tmp_path):
    repository = ConversationRepository(tmp_path / "missing-checkpoint.sqlite3")
    conversation = repository.current_or_create("workspace-one", "member-one", "home", "home")

    restored, outcome = repository.restore_unpersisted_turn_draft(
        conversation["id"], "workspace-one", "member-one", "request-without-checkpoint", "提交前等待的原句", 0,
    )

    assert restored is not None and outcome == "restored"
    assert restored["draft_text"] == "提交前等待的原句"
    assert restored["draft_version"] == 1
    duplicate, outcome = repository.restore_unpersisted_turn_draft(
        conversation["id"], "workspace-one", "member-one", "request-without-checkpoint", "提交前等待的原句", 0,
    )
    assert duplicate is not None and outcome == "already_recovered"
    assert duplicate["draft_version"] == 1

    newer = repository.save_draft(conversation["id"], "workspace-one", "member-one", "较新的输入", 1)
    assert newer is not None and newer["draft_version"] == 2
    with pytest.raises(ConversationDraftVersionConflict):
        repository.restore_unpersisted_turn_draft(
            conversation["id"], "workspace-one", "member-one", "stale-uncommitted-request", "旧的等待输入", 0,
        )
    current = repository.get(conversation["id"], "workspace-one", "member-one")
    assert current is not None
    assert current["draft_text"] == "较新的输入"


def test_workspace_pointer_restores_history_without_changing_scope_or_other_members(tmp_path):
    repository = ConversationRepository(tmp_path / "persistent.sqlite3")
    home = repository.current_or_create("workspace", "member", "home", "home")
    history = repository.create("workspace", "member", "task", "task-old", "旧任务会话")
    other = repository.create("workspace", "other", "task", "task-old", "其他成员")
    assert repository.select_current(history["id"], "workspace", "member", workspace_session=True)["scope_kind"] == "task"
    assert repository.current_or_create("workspace", "member", "home", "home")["id"] == history["id"]
    assert {row["id"] for row in repository.list_for_scope("workspace", "member", "home", "home")} == {home["id"], history["id"]}
    assert repository.select_current(other["id"], "workspace", "member", workspace_session=True) is None
    assert repository.get(history["id"], "workspace", "member")["scope_id"] == "task-old"


def test_turn_replay_retains_original_trusted_snapshot_and_checks_client_identity(tmp_path):
    repository = ConversationRepository(tmp_path / "snapshots.sqlite3")
    conversation = repository.current_or_create("workspace", "member", "home", "home")
    saved = repository.save_draft(conversation["id"], "workspace", "member", "读取当前素材", 0)
    context = {"kind":"material", "selection":{"scope":"asset","asset_id":"one"}, "target_snapshot":{"version":"before"}}
    turn, created = repository.create_turn_idempotent(conversation["id"], "workspace", "member", "读取当前素材", "one-request", saved["draft_version"], context)
    assert created
    replay, created = repository.create_turn_idempotent(conversation["id"], "workspace", "member", "读取当前素材", "one-request", saved["draft_version"], {**context,"target_snapshot":{"version":"after"}})
    assert not created and replay["id"] == turn["id"]
    assert repository.request_context("one-request",conversation["id"],"workspace","member")["target_snapshot"] == {"version":"before"}
    with pytest.raises(ConversationTurnRequestConflict):
        repository.create_turn_idempotent(conversation["id"], "workspace", "member", "读取当前素材", "one-request", saved["draft_version"], {**context,"selection":{"scope":"asset","asset_id":"two"}})


def test_reference_metadata_restart_cancel_cas_and_owner_isolation(tmp_path):
    path=tmp_path / "refs.sqlite3"
    repo=ConversationRepository(path)
    a=repo.current_or_create("workspace-a","member-a","home","home")
    reference={"kind":"voice","profile_id":"voice-real","version":"fixed-v1","label":"授权声音"}
    saved=repo.save_reference(a["id"],"workspace-a","member-a",reference,0)
    assert saved["reference_version"]==1 and saved["draft_version"]==0
    restarted=ConversationRepository(path)
    assert restarted.get(a["id"],"workspace-a","member-a")["selected_reference"]==reference
    assert restarted.save_reference(a["id"],"workspace-a","other-member",None,1) is None
    assert restarted.save_reference(a["id"],"other-workspace","member-a",None,1) is None
    cleared=restarted.save_reference(a["id"],"workspace-a","member-a",None,1)
    assert cleared["reference_version"]==2 and cleared["selected_reference"] is None
    with pytest.raises(ConversationReferenceVersionConflict):
        restarted.save_reference(a["id"],"workspace-a","member-a",reference,1)
    assert ConversationRepository(path).get(a["id"],"workspace-a","member-a")["selected_reference"] is None
    b=restarted.create("workspace-a","member-a","home","home")
    assert b["selected_reference"] is None and b["reference_version"]==0
    restarted.save_reference(a["id"],"workspace-a","member-a",reference,2)
    assert restarted.select_current(b["id"],"workspace-a","member-a",workspace_session=True)["selected_reference"] is None
    assert restarted.select_current(a["id"],"workspace-a","member-a",workspace_session=True)["selected_reference"]==reference
