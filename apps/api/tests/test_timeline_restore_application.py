"""Historical restore from real isolated SQLite and synthetic PCM/SRT/PNG inputs."""
from script_seed_fixtures import script_seed_payload
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import pytest

from app.creation.contracts import EditIntent, EditProposal, TimelineVersion
from app.creation.editing.metadata_use_case import MetadataEditUseCase, metadata_edit_command
from app.creation.editing.picture import EditConflict
from app.creation.editing.timeline_restore_use_case import (
    TimelineRestoreCommand, TimelineRestoreUseCase, TimelineOutputQualification,
)
from app.creation.transactions import clone_subtitle_track, clone_cover_asset
from app.models import utc_now
from app.creation.contracts import NarrationSegment
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from test_metadata_edit_use_case import editorial_rows
from test_subtitle_application import subtitle_state


def restore_state(tmp_path, *, empty=False):
    state = subtitle_state(tmp_path)
    # Inputs are synthetic, preexisting editorial facts, not a generation result.
    voice = state.case.output_root / state.narration.audio_path
    state.narration = replace(state.narration, audio_path=str(voice))
    state.repo.narration_artifacts.save_narration_artifact(state.narration)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    segment = NarrationSegment('fixed-segment', 'ws', state.task.id, 'fixed-script', 'section',
        '原配音', 'artifacts/task/fixed-narration.wav', 4, 16000, 'synthetic-fixture-input',
        'fixture-voice', 'fixture-v1', 'zh', 'fixture-pronunciation', 'ready',
        created_at=stamp, updated_at=stamp)
    state.segments = [segment]
    task = replace(state.repo.task_records.get_creation_task('ws', state.task.id), confirmed_script_id='fixed-script')
    script = CreationTaskScript('fixed-script', 'ws', task.id, 'fixture-script-input', 'fixture-topic',
        '固定文案', 'zh-CN', [CreationTaskScriptSection('section', '段落', '原配音', '素材画面', [])],
        selection_status='confirmed', created_at=stamp, updated_at=stamp, confirmed_at=stamp)
    with state.repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
            (json.dumps(asdict(task), default=str), task.id))
        conn.execute('INSERT INTO creation_task_scripts (id, workspace_id, task_id, run_id, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (script.id, 'ws', task.id, script.run_id, script_seed_payload(state.repo, conn, script), stamp.isoformat()))
    state.repo.narration_records.save_narration_segments('ws', task.id, 'fixed-script', state.segments)
    snapshot = {**state.base.snapshot,
        'narration_segments': json.loads(json.dumps([asdict(item) for item in state.segments], default=str))}
    if empty:
        snapshot['subtitle_track_ids'] = []
        snapshot.pop('cover_asset_id', None)
    tracks = []
    if not empty:
        tracks = [clone_subtitle_track(track, timeline_version_id='restore-base') for track in (state.zh, state.en)]
        state.cover = clone_cover_asset(state.cover, timeline_version_id='restore-base')
        snapshot['subtitle_track_ids'] = [track.id for track in tracks]
        snapshot['cover_asset_id'] = state.cover.id
    state.base = TimelineVersion('restore-base', 'ws', task.id, state.base.id, 3, snapshot, None)
    state.repo.timeline_records.create_timeline_version(state.base)
    for track in tracks:
        state.repo.subtitle_records.create_subtitle_track(track)
    if not empty:
        state.repo.cover_records.create_cover_asset(state.cover)
    state.task = state.repo.task_records.get_creation_task('ws', task.id)
    state.application = TimelineRestoreUseCase(state.repo, state.case)
    state.production, state.workspace_id = state.repo, 'ws'
    state.base_url = '/api/creation-tasks/' + state.task.id
    return state


def restore_http_state(tmp_path, monkeypatch, *, empty=False):
    from fastapi.testclient import TestClient
    from app import main
    state = restore_state(tmp_path, empty=empty)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_output_path=str(state.case.output_root), video_inbox_path=str(state.repo.video_library_path)))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    return state


def crop_save(state, x):
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    return state.case.save(task, metadata_edit_command(task, state.identity, 'ChangeCrop',
        {'base_version_id': task.current_timeline_version_id, 'clip_id': 'clip',
         'fit_mode': 'fit', 'crop_x': x, 'crop_y': .7}))


def restore(state, monkeypatch, *, redo=False):
    from app import main
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    prepared = state.application.prepare_restore(TimelineRestoreCommand('ws', task, redo))
    # This is the original full qualification owner; ready input exits False.
    valid = main._creation_render_is_valid('ws', task.id, verify_media=False,
        timeline_version=prepared.target_version)
    assert valid is False
    return prepared, state.application.apply_restore(prepared, TimelineOutputQualification(prepared, valid))


def test_empty_restore_body_requires_task_write_access(tmp_path, monkeypatch):
    state = restore_http_state(tmp_path, monkeypatch)
    crop_save(state, .3)
    state.repo.task_records.set_creation_task_visibility('ws', state.task.id, 'shared')
    _reader, reader_token = state.members.join(state.members.create_invite_code(), 'reader')
    for action in ('undo', 'redo'):
        url = f'/api/creation-tasks/{state.task.id}/timeline/{action}'
        before = editorial_rows(state.repo)
        for token, status in ((reader_token, 403), ('invalid-token', 401)):
            denied = state.client.post(url, json={}, headers={'X-Local-Member-Token': token})
            assert denied.status_code == status, denied.text
            assert editorial_rows(state.repo) == before
        restored = state.client.post(url, json={})
        assert restored.status_code == 200, restored.text


def test_restore_control_chain_skips_render_only_and_preserves_full_narration(tmp_path, monkeypatch):
    state = restore_http_state(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='同一仓储负责人'):
        TimelineRestoreUseCase(object(), state.case)
    assert MetadataEditUseCase.narration_segments_from_snapshot({}) is None
    assert MetadataEditUseCase.narration_segments_from_snapshot({'narration_segments': []}) == []
    with pytest.raises(ValueError, match='快照无效'):
        MetadataEditUseCase.narration_segments_from_snapshot({'narration_segments': [1]})
    original_versions = {v.id: v for v in state.repo.timeline_records.list_timeline_versions('ws', state.task.id)}
    first = crop_save(state, .3)
    second = crop_save(state, .6)
    second_version = state.repo.timeline_records.get_timeline_version('ws', second.operation.result_version_id)
    render_only = replace(second_version, id='render-only', parent_version_id=second_version.id,
        sequence=second_version.sequence + 1, created_by_operation_id=None)
    state.repo.timeline_records.create_timeline_version(render_only)
    prepared = state.application.prepare_restore(TimelineRestoreCommand('ws',
        state.repo.task_records.get_creation_task('ws', state.task.id), False))
    before = editorial_rows(state.repo)
    with pytest.raises(EditConflict, match='不属于本次恢复准备'):
        state.application.apply_restore(prepared, TimelineOutputQualification(replace(prepared), False))
    assert editorial_rows(state.repo) == before
    branch_calls = []
    for name in ('persist_montage_edit', 'persist_subtitle_edit'):
        original = getattr(state.case, name)
        def observed(*args, _name=name, _save=original, **kwargs):
            branch_calls.append((_name, kwargs))
            return _save(*args, **kwargs)
        monkeypatch.setattr(state.case, name, observed)
    first_prepared, undone = restore(state, monkeypatch)
    assert (first_prepared.changed_version.id, first_prepared.target_version.id) == (
        second_version.id, first.operation.result_version_id)
    assert undone.operation.payload == {'changed_version_id': second_version.id,
        'restore_version_id': first.operation.result_version_id, 'base_version_id': render_only.id}
    assert undone.montage.clips[0].crop_x == .3
    assert undone.montage.message == '草稿已恢复，可以重新生成。'
    _prepared, undone_again = restore(state, monkeypatch)
    assert undone_again.operation.payload['restore_version_id'] == state.base.id
    assert undone_again.montage.clips[0].crop_x == state.plan.clips[0].crop_x
    _prepared, redone = restore(state, monkeypatch, redo=True)
    assert redone.operation.payload['restore_version_id'] == undone.operation.result_version_id
    assert redone.montage.clips[0].crop_x == .3
    _prepared, redone_again = restore(state, monkeypatch, redo=True)
    assert redone_again.operation.payload['restore_version_id'] == render_only.id
    assert redone_again.montage.clips[0].crop_x == .6
    assert [name for name, _ in branch_calls] == ['persist_montage_edit', 'persist_montage_edit',
        'persist_subtitle_edit', 'persist_subtitle_edit']
    assert branch_calls[0][1]['subtitle_source_version'].id == first.operation.result_version_id
    assert branch_calls[-1][1]['snapshot_source'] == render_only.snapshot
    segments = state.repo.narration_records.list_narration_segments('ws', state.task.id, 'fixed-script')
    assert len(segments) == 1
    assert replace(segments[0], updated_at=state.segments[0].updated_at) == state.segments[0]
    assert segments[0].updated_at > state.segments[0].updated_at
    assert state.repo.task_records.get_creation_task('ws', state.task.id).narration_cache_key == state.narration.cache_key
    assert state.narration.audio_path and state.case.output_root.joinpath('artifacts/task/fixed-narration.wav').stat().st_size > 0
    for version_id, version in original_versions.items():
        assert state.repo.timeline_records.get_timeline_version('ws', version_id) == version
    assert state.repo.timeline_records.get_timeline_version('ws', render_only.id) == render_only


def test_restore_explicit_empty_tracks_absent_cover_and_complete_narration(tmp_path, monkeypatch):
    state = restore_http_state(tmp_path, monkeypatch, empty=True)
    now = utc_now()
    intent = state.repo.edit_records.create_edit_intent(EditIntent('fixture-canvas-intent', 'ws', state.task.id,
        '修改画布', 'montage', [], None, 'canvas', {'kind': 'timeline'}, {}, 1, 'interpreted', now))
    proposal = state.repo.edit_records.create_edit_proposal(EditProposal('fixture-canvas-proposal', 'ws', state.task.id,
        intent.id, '修改画布', ['timeline:' + state.base.id],
        {'operation_type': 'ChangeCanvas', 'payload': {'base_version_id': state.base.id}}, [], {}, [],
        created_at=now, updated_at=now))
    result_id = 'canvas-edit'
    tracks = [clone_subtitle_track(state.zh, timeline_version_id=result_id)]
    cover = clone_cover_asset(state.cover, timeline_version_id=result_id)
    plan = replace(state.plan, aspect_ratio='1:1')
    edited = state.case.persist_subtitle_edit(workspace_id='ws', task=state.task, proposal=proposal,
        base_version=state.base, result_version_id=result_id, operation_type='ChangeCanvas',
        operation_payload={'base_version_id': state.base.id}, subtitle_tracks=tracks, cover_asset=cover,
        updated_plan=plan, narration_segments=state.segments, narration_artifact=state.narration)
    edited_version = state.repo.timeline_records.get_timeline_version('ws', edited.operation.result_version_id)
    prepared, undone = restore(state, monkeypatch)
    assert prepared.subtitle_cover_undo
    version = state.repo.timeline_records.get_timeline_version('ws', undone.operation.result_version_id)
    assert version.snapshot['subtitle_track_ids'] == [] and 'cover_asset_id' not in version.snapshot
    assert [row['id'] for row in version.snapshot['narration_segments']] == ['fixed-segment']
    assert state.repo.subtitle_records.list_subtitle_tracks('ws', state.task.id, timeline_version_id=version.id) == []
    assert len(state.repo.narration_records.list_narration_segments('ws', state.task.id, 'fixed-script')) == 1
    assert undone.montage.message == '草稿已恢复；原成片依赖或产物已失效，请明确重新生成。'
    assert set(state.repo.edit_records.get_edit_proposal('ws', undone.operation.proposal_id).invalidated_refs) == {
        tracks[0].artifact_ref, cover.artifact_ref}
    _prepared, redone = restore(state, monkeypatch, redo=True)
    restored = state.repo.timeline_records.get_timeline_version('ws', redone.operation.result_version_id)
    rows = state.repo.subtitle_records.list_subtitle_tracks('ws', state.task.id, timeline_version_id=restored.id)
    assert len(rows) == 1 and rows[0].id != tracks[0].id and rows[0].cues == tracks[0].cues
    restored_cover = state.repo.cover_records.get_cover_asset('ws', state.task.id, restored.snapshot['cover_asset_id'])
    assert restored_cover.id != cover.id and restored_cover.artifact_ref == cover.artifact_ref
    assert [row['id'] for row in restored.snapshot['narration_segments']] == ['fixed-segment']
    assert state.repo.timeline_records.get_timeline_version('ws', state.base.id) == state.base
    assert state.repo.timeline_records.get_timeline_version('ws', edited_version.id) == edited_version
