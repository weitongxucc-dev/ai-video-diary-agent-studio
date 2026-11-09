"""Source editing against real SQL and opaque files, without media decoding."""
from dataclasses import asdict, replace
import json

import pytest

from app.artifact_store import ArtifactStore
from app.creation.editing.metadata_use_case import MetadataEditUseCase, metadata_edit_command
from app.creation.editing.sources import EditSourceVerifier
from app.materials.contracts import MaterialSegment
from app.montage import creation_task_montage_plan_from_payload
from app.task_audio import TaskAudio
from test_source_task_use_case import source_case as source_case


@pytest.fixture
def edit_case(source_case, tmp_path):
    case = source_case
    case.receipt = case.use_case.create(replace(case.command,
        source_start_seconds=1.25, source_end_seconds=7.75), case.actor)
    case.task = case.repo.task_records.get_creation_task('local', case.receipt['task_id'])
    case.identity = {'member_id':case.actor.member_id, 'local_diagnostic':False}
    store = ArtifactStore(tmp_path / 'outputs')
    case.editor = MetadataEditUseCase(case.repo, store, output_root=tmp_path / 'outputs',
        audio_service=TaskAudio(case.repo, store, tmp_path / 'temporary'))
    return case


def montage(case):
    return creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local', case.task.id))


def source_facts(case, *, connection=None):
    return case.repo.clip_sources.facts('local', case.task.id, clip_ids=[clip.id for clip in montage(case).clips],
        replacement=None, user_id=case.actor.member_id, local_diagnostic=False, connection=connection)


def resolve(case, clip=None, values=None):
    return case.repo.clip_sources.resolve('local', case.task.id, clip or montage(case).clips[0], values or {},
        user_id=case.actor.member_id, local_diagnostic=False)


def save(case, kind, clip_id='clip-source', **values):
    task = case.repo.task_records.get_creation_task('local', case.task.id)
    command = metadata_edit_command(task, case.identity, kind,
        {'base_version_id':task.current_timeline_version_id, 'clip_id':clip_id, **values})
    return case.editor.save(task, command)


def mutate_plan(case, change):
    with case.repo._connect() as conn:
        row = conn.execute('SELECT payload FROM creation_plans WHERE id=?', (case.task.plan_id,)).fetchone()
        payload = json.loads(row[0])
        change(payload)
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(payload), case.task.plan_id))


def mutate_montage(case, change):
    payload = case.repo.get_creation_task_montage_plan('local', case.task.id)
    change(payload)
    case.repo.montage_plan_adoption.save_creation_task_montage_plan('local', case.task.id, payload['id'], payload)


def test_selected_raw_source_supports_crop_lock_split_and_slot_preserving_trim(edit_case):
    case = edit_case
    before = asdict(case.repo.get_asset('local', case.asset.id))
    original_bytes = case.path.read_bytes()
    original_audio = montage(case).audio_timeline.model_dump()
    selection = resolve(case)
    assert (selection.asset_id, selection.segment_id, selection.reference) == (case.asset.id, None, case.reference)
    assert (selection.start_seconds, selection.end_seconds, selection.is_speech) == (1.25, 7.75, True)
    crop = save(case, 'ChangeCrop', fit_mode='fill', crop_x=.3, crop_y=.7)
    assert crop.montage.clips[0].crop_x == .3
    assert crop.operation.payload['expected_segment_id'] is None
    locked = save(case, 'SetClipLock', locked=True)
    assert locked.montage.clips[0].locked
    save(case, 'SetClipLock', locked=False)
    split = save(case, 'SplitClip', split_time_seconds=3)
    assert [(c.source_start_seconds, c.source_end_seconds) for c in split.montage.clips] == [(1.25, 4.25), (4.25, 7.75)]
    trimmed = save(case, 'TrimClip', source_start_seconds=2)
    assert [(c.timeline_start_seconds, c.timeline_end_seconds) for c in trimmed.montage.clips] == [(0, 3), (3, 6.5)]
    assert (trimmed.montage.clips[0].source_start_seconds, trimmed.montage.clips[0].source_end_seconds) == (2, 5)
    assert trimmed.montage.audio_timeline.model_dump() == original_audio
    assert trimmed.montage.duration_seconds == 6.5 and trimmed.montage.script_id is None
    assert all(c.segment_id is None and c.cue_id is None for c in trimmed.montage.clips)
    assert len(source_facts(case)) == 1  # Both real split IDs retain the same selected source.
    assert asdict(case.repo.get_asset('local', case.asset.id)) == before
    assert case.path.read_bytes() == original_bytes


def test_trim_cannot_leave_selected_range_or_change_original_audio_speed(edit_case):
    case = edit_case
    before = case.repo.get_creation_task_montage_plan('local', case.task.id)
    with pytest.raises(ValueError, match='裁剪起点'):
        save(case, 'TrimClip', source_start_seconds=0)
    with pytest.raises(ValueError, match='不能只改变画面速度'):
        save(case, 'ChangeClipSpeed', playback_rate=.5)
    assert case.repo.get_creation_task_montage_plan('local', case.task.id) == before
    assert case.repo.edit_records.list_edit_operations('local', case.task.id) == []


@pytest.mark.parametrize('mutation', ['missing_binding', 'script', 'cue', 'wrong_asset', 'reference',
    'range', 'file', 'asset_revision', 'private', 'excluded', 'reference_only', 'unregistered'])
def test_source_proof_is_required_in_preview_and_commit_facts(edit_case, mutation):
    case = edit_case
    expected = ValueError
    if mutation == 'missing_binding':
        mutate_plan(case, lambda p: p.update(source_media=None))
    elif mutation == 'script':
        mutate_montage(case, lambda p: p.update(script_id='ordinary-script'))
    elif mutation in {'cue', 'reference', 'range', 'wrong_asset'}:
        key, value = {'cue':('cue_id', 'invented-cue'), 'reference':('source_path', 'workspace://other/source.mp4'),
            'range':('source_start_seconds', 0), 'wrong_asset':('asset_id', 'different-asset')}[mutation]
        mutate_montage(case, lambda p: p['clips'][0].update({key:value}))
    elif mutation == 'file':
        case.path.write_bytes(b'changed declared source bytes')
    elif mutation == 'unregistered':
        with case.repo._connect() as conn:
            conn.execute('DELETE FROM workspace_media_references WHERE reference=?', (case.reference,))
    else:
        asset = case.repo.get_asset('local', case.asset.id)
        if mutation == 'private':
            asset.owner_user_id = case.members.add_member('other owner').member_id
            expected = PermissionError
        elif mutation == 'asset_revision':
            asset.user_description = 'changed registered revision'
        else:
            asset.use_state = mutation
        case.repo._save_asset(asset)
    with pytest.raises(expected):
        resolve(case)
    with pytest.raises(expected):
        source_facts(case)
    assert case.repo.edit_records.list_edit_operations('local', case.task.id) == []


def test_every_split_clip_is_checked_even_when_source_pair_is_shared(edit_case):
    case = edit_case
    save(case, 'SplitClip', split_time_seconds=3)
    mutate_montage(case, lambda p: p['clips'][1].update(source_end_seconds=8))
    assert resolve(case).segment_id is None
    with pytest.raises(ValueError, match='选定区间'):
        source_facts(case)


def test_source_facts_read_uncommitted_plan_on_supplied_connection(edit_case):
    case = edit_case
    original = source_facts(case)
    with case.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT payload FROM creation_plans WHERE id=?', (case.task.plan_id,)).fetchone()
        payload = json.loads(row[0])
        payload['source_media'] = None
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(payload), case.task.plan_id))
        with pytest.raises(ValueError, match='原片来源绑定'):
            source_facts(case, connection=conn)
        conn.rollback()
    assert source_facts(case) == original


def test_direct_commit_rejects_file_changed_after_source_freeze(edit_case, monkeypatch):
    case = edit_case
    original = case.repo.edit_adoption.confirm_direct
    def mutate(command, intent, proposal, change, **kwargs):
        case.path.write_bytes(b'changed between actual source freeze and writer')
        return original(command, intent, proposal, change, **kwargs)
    monkeypatch.setattr(case.repo.edit_adoption, 'confirm_direct', mutate)
    with pytest.raises(ValueError, match='原片文件'):
        save(case, 'ChangeCrop', fit_mode='fill', crop_x=.3, crop_y=.7)
    assert case.repo.edit_records.list_edit_operations('local', case.task.id) == []
    assert case.repo.edit_records.list_edit_proposals('local', case.task.id) == []
    assert case.repo.task_records.get_creation_task('local', case.task.id).current_timeline_version_id == case.receipt['timeline_version_id']


def test_original_source_binding_is_part_of_frozen_facts(edit_case):
    case = edit_case
    command = metadata_edit_command(case.task, case.identity, 'ChangeCrop',
        {'base_version_id':case.task.current_timeline_version_id, 'clip_id':'clip-source', 'fit_mode':'fill'})
    verifier = EditSourceVerifier(case.repo, identity=case.identity)
    command = replace(command, input_source_facts=verifier.freeze(command))
    mutate_plan(case, lambda p: p['source_media'].update(preserve_audio=False))
    with case.repo._connect() as conn, pytest.raises(ValueError, match='预览后的源文件'):
        verifier.verify(command, conn)


def test_ordinary_segment_facts_keep_existing_shape_and_missing_segment_is_rejected(edit_case):
    case = edit_case
    mutate_plan(case, lambda p: p.update(source_media=None))
    with pytest.raises(ValueError, match='原片来源绑定'):
        resolve(case)
    with pytest.raises(ValueError, match='原片来源绑定'):
        source_facts(case)
    asset = case.repo.get_asset('local', case.asset.id)
    asset.segments = [MaterialSegment('actual-segment', asset.id, 0, 9.25, usage_role='b_roll', visual_status='ready')]
    case.repo._save_asset(asset)
    mutate_montage(case, lambda p: p['clips'][0].update(segment_id='actual-segment'))
    assert resolve(case).segment_id == 'actual-segment'
    observed = case.path.stat()
    facts, = source_facts(case)
    assert facts == {'asset_id':asset.id, 'segment_id':'actual-segment', 'reference':case.reference,
        'modified_at':asset.modified_at.isoformat(), 'start_seconds':0, 'end_seconds':9.25,
        'size':observed.st_size, 'mtime_ns':observed.st_mtime_ns, 'ctime_ns':observed.st_ctime_ns,
        'inode':observed.st_ino, 'device':observed.st_dev}
