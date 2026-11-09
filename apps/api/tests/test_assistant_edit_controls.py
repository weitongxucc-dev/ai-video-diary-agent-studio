"""Current-turn history control over real versions, without models or user data."""
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from test_assistant_edit_chain import current, preview, storyboard as storyboard
from test_montage_pipeline import person_render_edit_state  # noqa: F401


@pytest.mark.parametrize('instruction', ['撤销刚才那一步', '说明撤销的效果', '不要撤销刚才那一步',
    '只撤销刚才的调色', '重做这一句口型', '重做刚才撤销那一步'])
def test_original_turn_restore_replays_and_rejects_foreign_input(tmp_path, instruction):
    from app.creation.editing.metadata_use_case import metadata_edit_command
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreCommand, TimelineRestoreUseCase
    from app.creation_execution import CreationExecutionActor
    from test_metadata_edit_use_case import metadata_state

    state = metadata_state(tmp_path)
    edited = state.case.save(state.task, metadata_edit_command(state.task, state.identity, 'SetClipLock',
        {'clip_id': 'clip', 'locked': True, 'base_version_id': state.base.id}))
    task = edited.task
    version = state.repo.timeline_records.get_timeline_version('ws', task.current_timeline_version_id)
    conversations = ConversationRepository(state.repo.database_path)
    member = state.member.member_id
    chat = conversations.create('ws', member, 'home', 'home', 'history')
    conversations.save_draft(chat['id'], 'ws', member, instruction, 0)
    turn, created = conversations.create_turn_idempotent(chat['id'], 'ws', member, instruction,
        str(uuid4()), 1, request_context={'kind': 'task', 'task_id': task.id, 'stage': 'montage',
            'selected_object_refs': [], 'playhead_ms': None,
            'target_snapshot': {'timeline_version_id': version.id, 'timeline_sequence': version.sequence}})
    assert created
    assert 'input_id' not in conversations.task_turn_source(turn['id'], 'ws', member, task.id)
    restore = TimelineRestoreUseCase(state.repo, state.case, conversations=conversations)
    actor = CreationExecutionActor('ws', member)
    command = TimelineRestoreCommand('ws', task, False)
    kwargs = {'actor': actor, 'request_id': 'original-turn-undo',
        'expected_timeline_version_id': version.id, 'expected_timeline_sequence': version.sequence,
        'source': {'assistant_turn_id': turn['id'], 'input_id': None}}
    if instruction != '撤销刚才那一步':
        from app.creation.editing.picture import EditConflict
        with state.repo._connect() as conn:
            before = tuple(conn.iterdump())
        with pytest.raises(EditConflict):
            restore.execute(command, **kwargs)
        with state.repo._connect() as conn:
            assert tuple(conn.iterdump()) == before
        return
    restored = restore.execute(command, **kwargs)
    assert restored.operation.operation_type == 'UndoTimelineVersion'
    assert not restored.montage.clips[0].locked
    head = restored.task.current_timeline_version_id
    replayed = restore.execute(command, **kwargs)
    assert replayed.operation.id == restored.operation.id
    assert replayed.task.current_timeline_version_id == head
    with state.repo._connect() as conn:
        before = conn.iterdump()
        snapshot = tuple(before)
    with pytest.raises(PermissionError, match='原已消费输入'):
        restore.execute(command, **{**kwargs, 'request_id': 'foreign-input',
            'source': {'assistant_turn_id': turn['id'], 'input_id': 'unconsumed-input'}})
    with state.repo._connect() as conn:
        assert tuple(conn.iterdump()) == snapshot


@pytest.mark.parametrize('conflict', [False, 'reused', 'changed', 'not_subtitle'])
def test_subtitle_membership_restore_preserves_other_tracks_and_refuses_later_changes(conflict):
    from dataclasses import replace
    from app.creation.contracts import SubtitleCue, SubtitleTrack
    from app.creation.editing.picture import EditConflict
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase

    original = SubtitleTrack('original-en', 'ws', 'task', 'before', 'en-US', 'zh-CN', {'size': 40},
        [SubtitleCue('en-cue', 'source', 'English', 0, 1)], 'translation', 'model', None, None, 'valid')
    later = replace(original, id='later-zh', language='zh-CN', source_language=None,
        generation_method='timeline', cues=[replace(original.cues[0], id='zh-cue', text='后来字幕')])
    restored = TimelineRestoreUseCase.restore_subtitle_tracks([later], [], [original], allow_membership=True)
    assert restored == [later, original]
    current = [later, replace(original, id='current-en', timeline_version_id='current')]
    if conflict == 'reused':
        expected, desired = [], [original]
    else:
        expected, desired = [original], []
        if conflict == 'changed':
            current[-1] = replace(current[-1], cues=[replace(original.cues[0], text='Later translation')])
    if conflict:
        with pytest.raises(EditConflict):
            TimelineRestoreUseCase.restore_subtitle_tracks(current, expected, desired,
                allow_membership=conflict != 'not_subtitle')
    else:
        assert TimelineRestoreUseCase.restore_subtitle_tracks(current, expected, desired,
            allow_membership=True) == [later]


def control(state, monkeypatch, command):
    conversations = ConversationRepository(state.production.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    chat = conversations.create(state.workspace_id, state.member.member_id, "home", "home", "编辑控制")
    conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, command, 0)
    row, created = conversations.create_turn_idempotent(chat["id"], state.workspace_id, state.member.member_id,
        command, str(uuid4()), 1, request_context={"kind": "task", "task_id": task.id,
            "stage": "montage", "selected_object_refs": [], "playhead_ms": None,
            "target_snapshot": {"timeline_version_id": version.id, "timeline_sequence": version.sequence}})
    assert created
    return {"request_id": "history:" + row["id"], "assistant_turn_id": row["id"], "expected_timeline_version_id": version.id,
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
    restored_head = current(state)["task"]["current_timeline_version_id"]
    assert state.client.post(state.base_url + "/timeline/undo", json=body).status_code == 200
    assert current(state)["task"]["current_timeline_version_id"] == restored_head
    stale = {**body, "request_id": body["request_id"] + ":stale"}
    assert state.client.post(state.base_url + "/timeline/undo", json=stale).status_code == 409
    redone = state.client.post(state.base_url + "/timeline/redo", json=control(state, monkeypatch, "重做刚才撤销那一步"))
    assert redone.status_code == 200, redone.text
    assert [item["id"] for item in current(state)["montage"]["clips"]] == list(reversed(original))


def test_rejected_proposal_never_changes_timeline_and_can_replay(storyboard, monkeypatch):
    state = storyboard
    proposal = preview(state, "1↔2").json()["proposal"]
    before = current(state)["task"]["current_timeline_version_id"]
    body = control(state, monkeypatch, "取消这个修改提案")
    body.pop("request_id")
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
