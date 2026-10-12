"""Current-turn history control over real versions, without models or user data."""
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from test_assistant_edit_chain import storyboard, preview, current  # noqa: F401
from test_montage_pipeline import person_render_edit_state  # noqa: F401


def control(state, monkeypatch, command):
    conversations = ConversationRepository(state.production.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    chat = conversations.create(state.workspace_id, state.member.member_id, "home", "home", "编辑控制")
    conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, command, 0)
    row, created = conversations.create_turn_idempotent(chat["id"], state.workspace_id, state.member.member_id,
        command, str(uuid4()), 1, request_context={"kind": "task", "task_id": task.id,
            "stage": "montage", "selected_object_refs": [], "playhead_ms": None,
            "target_snapshot": {"timeline_version_id": version.id, "timeline_sequence": version.sequence}})
    assert created
    return {"assistant_turn_id": row["id"], "expected_timeline_version_id": version.id,
        "expected_timeline_sequence": version.sequence}


def test_undo_and_redo_use_original_turn_and_actual_versions(storyboard, monkeypatch):
    state = storyboard
    original = [item["id"] for item in current(state)["montage"]["clips"]]
    proposed = preview(state, "1↔3").json()["proposal"]
    assert state.client.post(state.base_url + f"/edits/{proposed['id']}/confirm").status_code == 200
    read = state.client.get(state.base_url + "/edit-controls").json()
    assert read["can_undo"] and len(read["proposals"]) <= 8
    body = control(state, monkeypatch, "撤销刚才那一步")
    undone = state.client.post(state.base_url + "/timeline/undo", json=body)
    assert undone.status_code == 200, undone.text
    assert [item["id"] for item in current(state)["montage"]["clips"]] == original
    assert state.client.post(state.base_url + "/timeline/undo", json=body).status_code == 409
    redone = state.client.post(state.base_url + "/timeline/redo", json=control(state, monkeypatch, "重做刚才撤销那一步"))
    assert redone.status_code == 200, redone.text
    assert [item["id"] for item in current(state)["montage"]["clips"]] == list(reversed(original))


def test_rejected_proposal_never_changes_timeline_and_can_replay(storyboard, monkeypatch):
    state = storyboard
    proposal = preview(state, "1↔2").json()["proposal"]
    before = current(state)["task"]["current_timeline_version_id"]
    body = control(state, monkeypatch, "取消这个修改提案")
    path = state.base_url + f"/edits/{proposal['id']}/reject"
    result = state.client.post(path, json=body)
    assert result.status_code == 200 and result.json()["status"] == "rejected"
    assert state.client.post(path, json=body).status_code == 200
    assert current(state)["task"]["current_timeline_version_id"] == before
    assert state.client.get(state.base_url + f"/edits/{proposal['id']}").status_code == 409
    assert state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm").status_code == 409


@pytest.mark.parametrize("message", ["说明撤销的效果", "不要撤销刚才那一步", "只撤销刚才的调色", "重做这一句口型"])
def test_ambiguous_negative_or_partial_request_cannot_restore_entire_version(storyboard, monkeypatch, message):
    state = storyboard
    assert state.client.post(state.base_url + "/edits/exact", json={"operation_type": "SetClipLock", "clip_id": "clip-3", "locked": True}).status_code == 200
    assert state.client.get(state.base_url + "/edit-controls").json()["can_undo"]
    body = control(state, monkeypatch, message)
    before = current(state)["task"]["current_timeline_version_id"]
    result = state.client.post(state.base_url + "/timeline/undo", json=body)
    assert result.status_code == 409
    assert current(state)["task"]["current_timeline_version_id"] == before


def test_reopen_proposal_checks_current_base_and_permissions(storyboard):
    state = storyboard
    proposal = preview(state, "1↔2").json()["proposal"]
    path = state.base_url + f"/edits/{proposal['id']}"
    assert state.client.get(path).status_code == 200
    state.client.post(state.base_url + "/edits/exact", json={"operation_type": "SetClipLock", "clip_id": "clip-3", "locked": True})
    assert state.client.get(path).status_code == 409
