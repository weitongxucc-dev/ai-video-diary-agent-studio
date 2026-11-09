"""Trusted lock-history instructions over isolated SQL, without media or models."""
from dataclasses import asdict, replace
import subprocess
from uuid import uuid4
import wave

import pytest

from app.conversation_repository import ConversationRepository
from app.creation.editing.metadata_use_case import metadata_edit_command
from app.creation.editing.picture import EditConflict
from app.creation.editing.timeline_restore_use_case import TimelineRestoreCommand, TimelineRestoreUseCase
from app.creation.transactions import clone_subtitle_track
from app.creation_execution import CreationExecutionActor
from app.device_scheduler import RecoveryBlocked
from app.montage import creation_task_montage_plan_to_payload
from test_metadata_edit_use_case import editorial_rows, metadata_state


ACTUAL_UNDO = '请撤销刚才这次锁定修改，恢复第1个镜头未锁定的状态，保留原画面和原声，不重新制作，然后结束本轮。'


@pytest.fixture
def state(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('lock restore metadata cannot start processes or decode/generate audio')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    state = metadata_state(tmp_path)
    # Two existing shots make a wrong target distinct from a missing object and
    # let an unrelated later edit prove that this is a local reverse operation.
    clip = state.plan.clips[0]
    state.plan = replace(state.plan, clips=[replace(clip, source_end_seconds=2, timeline_end_seconds=2),
        replace(clip, id='second-clip', source_start_seconds=2, timeline_start_seconds=2)])
    payload = creation_task_montage_plan_to_payload(state.plan)
    parent = state.base
    state.track = clone_subtitle_track(state.track, timeline_version_id='two-shot-base')
    state.base = replace(parent, id='two-shot-base', parent_version_id=parent.id, sequence=parent.sequence + 1,
        snapshot={**parent.snapshot, 'montage': payload, 'subtitle_track_ids': [state.track.id]})
    with state.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        state.repo.montage_plan_adoption.save_creation_task_montage_plan('ws', state.task.id,
            state.plan.id, payload, connection=conn)
        state.repo.timeline_records.create_timeline_version(state.base, connection=conn,
            expected_current_version_id=parent.id)
        state.repo.subtitle_records.create_subtitle_track(state.track, connection=conn)
    state.conversations = ConversationRepository(state.repo.database_path)
    state.restore = TimelineRestoreUseCase(state.repo, state.case, conversations=state.conversations)
    state.actor = CreationExecutionActor('ws', state.member.member_id)
    return state


def save(state, operation, **values):
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    return state.case.save(task, metadata_edit_command(task, state.identity, operation,
        {'base_version_id': task.current_timeline_version_id, **values}))


def instruction(state, message, operation, *, redo=False, selected=()):
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', task.current_timeline_version_id)
    chat = state.conversations.create('ws', state.member.member_id, 'task', task.id)
    state.conversations.save_draft(chat['id'], 'ws', state.member.member_id, message, 0)
    turn, created = state.conversations.create_turn_idempotent(chat['id'], 'ws', state.member.member_id,
        message, str(uuid4()), 1, request_context={'kind': 'task', 'task_id': task.id, 'stage': 'montage',
            'selected_object_refs': list(selected),
            'target_snapshot': {'timeline_version_id': version.id, 'timeline_sequence': version.sequence}})
    assert created
    return TimelineRestoreCommand('ws', task, redo), {
        'actor': state.actor, 'request_id': str(uuid4()), 'target_operation_id': operation.id,
        'expected_timeline_version_id': version.id, 'expected_timeline_sequence': version.sequence,
        'source': {'assistant_turn_id': turn['id'], 'input_id': None}}


def database(state):
    with state.repo._connect() as conn:
        return tuple(conn.iterdump())


def picture_and_sound(plan):
    return {'clips': [{key: value for key, value in asdict(clip).items() if key != 'locked'}
                      for clip in plan.clips],
            'cues': [asdict(cue) for cue in plan.cues], 'audio_timeline': plan.audio_timeline,
            'narration_audio_path': plan.narration_audio_path, 'duration_seconds': plan.duration_seconds}


@pytest.mark.parametrize('original_locked', [True, False])
def test_trusted_lock_undo_redo_keeps_later_crop_original_sources_and_request_identity(state, original_locked):
    if not original_locked:
        save(state, 'SetClipLock', clip_id='clip', locked=True)
    original = save(state, 'SetClipLock', clip_id='clip', locked=original_locked)
    later = save(state, 'ChangeCrop', clip_id='second-clip', fit_mode='fill', crop_x=.2, crop_y=.8)
    before = picture_and_sound(later.montage)
    source_bytes = state.source.read_bytes()
    jobs = state.repo.job_records.list_job_runs('ws', state.task.id)
    message = ACTUAL_UNDO if original_locked else ACTUAL_UNDO.replace('这次锁定', '这次解锁').replace('未锁定', '已锁定')
    command, options = instruction(state, message, original.operation)
    undone = state.restore.execute(command, **options)
    assert undone.montage.clips[0].locked is not original_locked
    assert undone.montage.clips[1].crop_x == .2 and undone.montage.clips[1].crop_y == .8
    assert picture_and_sound(undone.montage) == before
    assert undone.restore_request['target_operation_id'] == original.operation.id
    assert undone.restore_request['status'] == 'applied'
    saved = database(state)
    replay = state.restore.execute(command, **options)
    assert replay.operation.id == undone.operation.id and database(state) == saved
    label, restored = ('锁定', '已锁定') if original_locked else ('解锁', '未锁定')
    command, options = instruction(state,
        f'请重做刚才撤销的{label}修改，恢复第1个镜头{restored}的状态，保留原画面和原声，不重新制作，然后结束本轮。',
        original.operation, redo=True)
    redone = state.restore.execute(command, **options)
    assert redone.montage.clips[0].locked is original_locked
    assert picture_and_sound(redone.montage) == before
    assert redone.restore_request['target_operation_id'] == original.operation.id
    assert state.source.read_bytes() == source_bytes
    assert state.repo.job_records.list_job_runs('ws', state.task.id) == jobs


@pytest.mark.parametrize('message', [
    '不要撤销刚才这次锁定修改。',
    '先讨论撤销刚才这次锁定修改的方案。',
    '说明撤销刚才这次锁定修改的效果。',
    ACTUAL_UNDO + '并把第1个镜头换成新画面。',
    '请撤销刚才这次锁定修改，恢复第2个镜头未锁定的状态。',
    '请撤销刚才这次锁定修改，恢复第1个镜头已锁定的状态。',
    '请撤销刚才这次解锁修改，恢复第1个镜头未锁定的状态。',
    '请重做刚才撤销的锁定修改，恢复第1个镜头已锁定的状态。',
])
def test_unauthorized_lock_restore_wording_does_not_claim_or_change_database(state, message):
    original = save(state, 'SetClipLock', clip_id='clip', locked=True)
    command, options = instruction(state, message, original.operation)
    before = database(state)
    with pytest.raises((EditConflict, ValueError)):
        state.restore.execute(command, **options)
    assert database(state) == before


@pytest.mark.parametrize('target', ['other_clip_lock', 'non_lock_crop'])
def test_lock_instruction_cannot_restore_a_different_object_or_other_edit_kind(state, target):
    save(state, 'SetClipLock', clip_id='clip', locked=True)
    other = (save(state, 'SetClipLock', clip_id='second-clip', locked=True) if target == 'other_clip_lock'
             else save(state, 'ChangeCrop', clip_id='second-clip', fit_mode='fit', crop_x=.2, crop_y=.8))
    command, options = instruction(state, ACTUAL_UNDO, other.operation)
    before = database(state)
    with pytest.raises(EditConflict):
        state.restore.execute(command, **options)
    assert database(state) == before


@pytest.mark.parametrize('selected,accepted', [('clip', True), ('second-clip', False)])
def test_selected_lock_restore_uses_the_trusted_task_input_selection(state, selected, accepted):
    original = save(state, 'SetClipLock', clip_id='clip', locked=True)
    command, options = instruction(state, '请撤销刚才这次锁定修改，恢复选中的镜头未锁定的状态。',
        original.operation, selected=[f'clip:{selected}'])
    before = database(state)
    if accepted:
        assert state.restore.execute(command, **options).montage.clips[0].locked is False
    else:
        with pytest.raises(EditConflict, match='镜头'):
            state.restore.execute(command, **options)
        assert database(state) == before


def test_lock_restore_unknown_request_requires_original_reconciliation_without_another_write(state):
    original = save(state, 'SetClipLock', clip_id='clip', locked=True)
    command, options = instruction(state, ACTUAL_UNDO, original.operation)
    inputs = {'action': 'undo', 'target_operation_id': original.operation.id,
        'expected_timeline_version_id': options['expected_timeline_version_id'],
        'expected_timeline_sequence': options['expected_timeline_sequence'], 'source': options['source'],
        'actor_identity': state.actor.identity_payload()}
    state.repo.restore_requests.claim(command.task, state.member.member_id, options['request_id'], inputs)
    before = database(state)
    with pytest.raises(RecoveryBlocked, match='结果未知'):
        state.restore.execute(command, **options)
    assert database(state) == before


def test_lock_restore_rejects_unconsumed_input_and_rechecks_source_in_claim_writer(state, monkeypatch):
    original = save(state, 'SetClipLock', clip_id='clip', locked=True)
    command, options = instruction(state, ACTUAL_UNDO, original.operation)
    before = database(state)
    with pytest.raises(PermissionError, match='原已消费输入'):
        state.restore.execute(command, **{**options, 'source': {**options['source'], 'input_id': 'unconsumed'}})
    assert database(state) == before
    before_editorial = editorial_rows(state.repo)
    claim = state.repo.restore_requests.claim
    externally_stopped = []
    def stop_before_claim(*args, **kwargs):
        state.conversations.finish_turn(options['source']['assistant_turn_id'], 'stopped')
        externally_stopped.append(database(state))
        return claim(*args, **kwargs)
    monkeypatch.setattr(state.repo.restore_requests, 'claim', stop_before_claim)
    with pytest.raises(ValueError, match='当前有效助手轮次'):
        state.restore.execute(command, **options)
    assert editorial_rows(state.repo) == before_editorial
    assert database(state) == externally_stopped[0]
