"""Scoped audio adoption undo/redo over real stores, with synthetic audio only."""
from dataclasses import replace
import os
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audio_timeline import AudioEditRequest
from app.conversation_repository import ConversationRepository
from app.creation.editing.picture import EditConflict
from app.creation.editing.timeline_restore_use_case import (
    AudioAdoptionRestoreScope, TimelineRestoreCommand, TimelineRestoreUseCase,
)
from test_audio_edit_application import edit_state


@pytest.fixture
def state(tmp_path, monkeypatch):
    repo, actor, media, runtime, client, base, graph, path, calls = edit_state(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail('adoption history restore cannot decode media or start production')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    conversations = ConversationRepository(repo.database_path)
    return SimpleNamespace(repo=repo, actor=actor, audio=media.audio_edit, path=path,
        conversations=conversations, restore=TimelineRestoreUseCase(repo, media.audio_edit.montage_edits,
            conversations=conversations))


def save_audio(state, action, target, values):
    task = state.repo.task_records.get_creation_task('local', 'task')
    edit = AudioEditRequest(base_version_id=task.current_timeline_version_id,
        action=action, target_id=target, values=values)
    preview = state.audio.preview('local', 'task', edit, state.actor, draft_only=True)
    return state.audio.confirm(task, preview.proposal, state.actor)


def adopt(state):
    reference = state.repo.get_creation_task_montage_plan('local', 'task')['audio_timeline']['tracks'][0]['clips'][0]['source_ref']
    return save_audio(state, 'add', 'audio-music', {'source_ref': reference, 'source_start_ms': 0,
        'source_end_ms': 200, 'timeline_start_ms': 0, 'gain_db': -18, 'name': '钢琴'})


def instruction(state, original, *, redo=False, message=None):
    task = state.repo.task_records.get_creation_task('local', 'task')
    version = state.repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
    message = message or (f'读取真实编辑历史，指定原采用 {original.operation.id}，'
        '使用 undo_last_edit 撤销该钢琴采用（保留后续原声响度归一化和音量修改），'
        '回查资源历史确认钢琴已移除；再使用 redo_last_edit 指定同一原操作恢复钢琴，'
        '不开始制作、不重新导入。')
    chat = state.conversations.create('local', state.actor.member_id, 'task', 'task')
    state.conversations.save_draft(chat['id'], 'local', state.actor.member_id, message, 0)
    turn, _ = state.conversations.create_turn_idempotent(chat['id'], 'local', state.actor.member_id,
        message, str(uuid4()), 1, request_context={'kind': 'task', 'task_id': 'task', 'stage': 'montage',
            'target_snapshot': {'timeline_version_id': version.id, 'timeline_sequence': version.sequence}})
    return TimelineRestoreCommand('local', task, redo), dict(actor=state.actor, request_id=str(uuid4()),
        target_operation_id=original.operation.id, expected_timeline_version_id=version.id,
        expected_timeline_sequence=version.sequence, source={'assistant_turn_id': turn['id'], 'input_id': None})


def graph(state):
    return state.repo.get_creation_task_montage_plan('local', 'task')['audio_timeline']


def test_original_adoption_undo_redo_keeps_later_other_audio_edits_and_original_requests(state, monkeypatch):
    original = adopt(state)
    narration = graph(state)['tracks'][0]['clips'][0]['id']
    save_audio(state, 'update', narration, {'gain_db': -4, 'loudness_target_lufs': -16})
    save_audio(state, 'track', 'audio-music', {'gain_db': -8, 'duck_under_voice': True})
    before = graph(state)
    jobs = state.repo.job_records.list_job_runs('local', 'task')
    source = state.path.read_bytes()
    connections = []
    read_source = state.restore.montage_edits.audio_service.source
    def source_reader(*args, **kwargs):
        connections.append(kwargs.get('connection'))
        return read_source(*args, **kwargs)
    monkeypatch.setattr(state.restore.montage_edits.audio_service, 'source', source_reader)
    command, options = instruction(state, original)
    undone = state.restore.execute(command, **options)
    expected = [dict(track, clips=[] if track['id'] == 'audio-music' else track['clips']) for track in before['tracks']]
    assert graph(state)['tracks'] == expected
    assert undone.operation.payload['target_operation_id'] == original.operation.id
    assert state.restore.execute(command, **options).operation.id == undone.operation.id
    redo_command, redo_options = instruction(state, original, redo=True)
    redone = state.restore.execute(redo_command, **redo_options)
    assert graph(state) == before
    assert redone.operation.payload['target_undo_operation_id'] == undone.operation.id
    assert state.restore.execute(redo_command, **redo_options).operation.id == redone.operation.id
    assert state.repo.job_records.list_job_runs('local', 'task') == jobs
    assert state.path.read_bytes() == source
    assert any(connection is not None for connection in connections)


def test_same_adopted_clip_later_gain_edit_remains_a_conflict_with_original_refusal(state):
    original = adopt(state)
    clip = next(track for track in graph(state)['tracks'] if track['id'] == 'audio-music')['clips'][0]
    save_audio(state, 'update', clip['id'], {'gain_db': -10})
    before = graph(state)
    command, options = instruction(state, original)
    version = command.task.current_timeline_version_id
    with pytest.raises(EditConflict, match='删除目标已变化'):
        state.restore.execute(command, **options)
    assert graph(state) == before
    assert state.repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == version
    receipt = state.restore.read_request('local', 'task', options['request_id'], state.actor)
    assert receipt['status'] == 'refused'
    with pytest.raises(EditConflict):
        state.restore.execute(command, **options)
    assert state.restore.read_request('local', 'task', options['request_id'], state.actor) == receipt


@pytest.mark.parametrize('message', [
    '请撤销音乐，保留原声音量。',
    '请撤销原采用 operation_other，保留音量。',
    '不要撤销原采用 {operation}。',
    '指定原采用 {operation}，不要使用 undo_last_edit 撤销该钢琴采用。',
    '请查询原采用 {operation} 的撤销回执。',
    '指定原采用 {operation}，查询该钢琴的撤销历史。',
    '指定原采用 {operation}，不要撤销该钢琴采用，然后使用 undo_last_edit 撤销该钢琴采用。',
])
def test_audio_restore_requires_explicit_positive_original_operation(state, message):
    original = adopt(state)
    command, options = instruction(state, original, message=message.format(operation=original.operation.id))
    before = graph(state)
    with pytest.raises(EditConflict):
        state.restore.execute(command, **options)
    assert graph(state) == before


@pytest.mark.parametrize('redo', [False, True])
@pytest.mark.parametrize('named_separately', [False, True])
def test_later_retraction_of_named_audio_restore_prevents_any_request_claim(state, redo, named_separately):
    original = adopt(state)
    if redo:
        undo_command, undo_options = instruction(state, original)
        state.restore.execute(undo_command, **undo_options)
    verb = '重做' if redo else '撤销'
    message = (f'指定原操作{original.operation.id}，然后{verb}该操作。' if named_separately
               else f'{verb}原操作{original.operation.id}。') + f'不要{verb}该操作。'
    scope = AudioAdoptionRestoreScope(original.operation.id, 'audio-music', 'original-clip')
    with pytest.raises(EditConflict, match='没有授权'):
        TimelineRestoreUseCase.require_audio_restore_instruction(message, redo=redo, scope=scope)
    command, options = instruction(state, original, redo=redo, message=message)
    with state.repo._connect() as connection:
        before = tuple(connection.iterdump())
    with pytest.raises(EditConflict, match='没有授权'):
        state.restore.execute(command, **options)
    with state.repo._connect() as connection:
        assert tuple(connection.iterdump()) == before


def test_audio_restore_does_not_overwrite_a_new_version_published_after_preparation(state):
    original = adopt(state)
    command, options = instruction(state, original)
    narration = graph(state)['tracks'][0]['clips'][0]['id']
    later = []
    def publish_new_version(*args):
        later.append(save_audio(state, 'update', narration, {'gain_db': -5}))
        return False
    state.restore.qualify_output = publish_new_version
    with pytest.raises(EditConflict):
        state.restore.execute(command, **options)
    assert state.repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == later[0].operation.result_version_id
    assert graph(state)['tracks'][0]['clips'][0]['gain_db'] == -5
    assert next(track for track in graph(state)['tracks'] if track['id'] == 'audio-music')['clips']


def test_audio_restore_keeps_member_input_version_and_source_commit_guards(state):
    original = adopt(state)
    command, options = instruction(state, original)
    before = graph(state)
    with pytest.raises(PermissionError):
        state.restore.execute(command, **{**options, 'actor': replace(state.actor, member_id='other')})
    with pytest.raises(PermissionError):
        state.restore.execute(command, **{**options, 'source': {**options['source'], 'input_id': 'unconsumed'}})
    with pytest.raises(EditConflict):
        state.restore.execute(command, **{**options, 'expected_timeline_sequence': options['expected_timeline_sequence'] + 1})
    def source_changed(*args):
        stat = state.path.stat()
        os.utime(state.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        return False
    state.restore.qualify_output = source_changed
    with pytest.raises(EditConflict, match='来源或许可已变化'):
        state.restore.execute(command, **options)
    assert graph(state) == before
