"""Actual linked-speed application with opaque source facts and synthetic PCM/SRT."""
from dataclasses import replace
from datetime import datetime, timezone
import os
import sqlite3
import subprocess
import sys
from uuid import uuid4
import wave

import pytest

from app.audio_timeline import narration_audio_timeline
from app.creation.contracts import EditCommand, TimelineVersion
from app.creation.editing.linked_timeline_use_case import (
    LinkedTimelineEditUseCase, LinkedEditApplyCommand, LinkedEditPreviewFailed,
)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.creation.transactions import clone_subtitle_track
from app.montage import creation_task_montage_plan_to_payload
from app.production import MaterialAsset, MaterialSegment
from app.creation.plan_contracts import CreationPlan
from test_metadata_edit_use_case import metadata_state, editorial_rows


def speed_state(tmp_path):
    state = metadata_state(tmp_path)
    repo = state.repo
    neighbour_path = repo.video_library_path / 'neighbour.mp4'
    neighbour_path.write_bytes(b'opaque neighbour; only stat identity inspected')
    when = datetime.fromtimestamp(neighbour_path.stat().st_mtime, timezone.utc)
    neighbour = replace(state.asset, id='neighbour', path=str(neighbour_path), file_name=neighbour_path.name,
        size_bytes=neighbour_path.stat().st_size, modified_at=when,
        segments=[MaterialSegment('neighbour-segment', 'neighbour', 0, 4, usage_role='b_roll')])
    repo._save_asset(neighbour)
    neighbour_ref = repo.workspace_media_reference_for_asset('ws', neighbour.id)
    voice_path = repo.video_library_path / 'speech.wav'
    with wave.open(str(voice_path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 64000)
    voice = MaterialAsset('voice-input', 'ws', voice_path.name, str(voice_path), voice_path.stat().st_size,
        datetime.fromtimestamp(voice_path.stat().st_mtime, timezone.utc), media_type='audio', duration_seconds=4,
        owner_user_id=state.member.member_id, visibility='private')
    repo._save_asset(voice)
    voice_ref = repo.workspace_media_reference_for_asset('ws', voice.id)
    with repo._connect() as conn:
        for reference in (neighbour_ref, voice_ref):
            repo._claim_workspace_media_reference(conn, workspace_id='ws', reference=reference)
    first = replace(state.plan.clips[0], source_end_seconds=2, timeline_end_seconds=2)
    second = replace(first, id='neighbour-clip', cue_id='neighbour-cue', asset_id=neighbour.id,
        segment_id='neighbour-segment', source_path=neighbour_ref, timeline_start_seconds=2, timeline_end_seconds=4)
    state.plan.clips = [first, second]
    state.plan.cues = [replace(state.plan.cues[0], duration_seconds=2),
        replace(state.plan.cues[0], id='neighbour-cue', text='后续字幕', duration_seconds=2)]
    state.plan.narration_audio_path = voice_ref
    state.plan.audio_timeline = narration_audio_timeline(voice_ref, 4)
    state.plan.audio_timeline.tracks[0].locked = False
    for clip in state.plan.audio_timeline.tracks[0].clips:
        clip.locked = False
    payload = creation_task_montage_plan_to_payload(state.plan)
    repo.montage_plan_adoption.save_creation_task_montage_plan('ws', state.task.id, state.plan.id, payload)
    state.track = clone_subtitle_track(state.track, timeline_version_id='linked-base', track_id='linked-zh',
        cues=[replace(state.track.cues[0], end_seconds=2), replace(state.track.cues[0], id='neighbour-cue', text='后续字幕', start_seconds=2, end_seconds=4)])
    state.base = TimelineVersion('linked-base', 'ws', state.task.id, state.base.id, 2,
        {'montage': payload, 'subtitle_track_ids': [state.track.id], 'narration_cache_key': state.narration.cache_key}, None)
    repo.timeline_records.create_timeline_version(state.base)
    repo.subtitle_records.create_subtitle_track(state.track)
    state.task = repo.task_records.get_creation_task('ws', state.task.id)
    state.neighbour = neighbour
    state.application = LinkedTimelineEditUseCase(repo, state.case)
    return state


@pytest.fixture
def state(tmp_path):
    return speed_state(tmp_path)


def speed_command(state, rate=2):
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    return EditCommand('ws', task.id, state.identity, task.current_timeline_version_id, 'ChangeLinkedSpeed',
        {'base_version_id': task.current_timeline_version_id, 'clip_id': 'clip', 'playback_rate': rate})


def apply_command(state, proposal, *, requesting=True):
    source = state.repo.edit_records.get_edit_intent('ws', proposal.intent_id).constraints.get('request_source')
    request_id = str(uuid4()) if requesting else None
    base = proposal.operation['payload']['base_version_id']
    fields = {'request_id': request_id, 'expected_base_version_id': base} if requesting else {}
    return LinkedEditApplyCommand('ws', state.task.id, proposal.id, state.identity, state.member.member_id,
        source=source, request_id=request_id, expected_base_version_id=base if requesting else None,
        base_version_submitted=requesting, request_fields=fields)


def test_cold_import_does_not_load_main_or_HTTP():
    command = "import sys; import app.creation.editing.linked_timeline_use_case; assert 'app.main' not in sys.modules; assert 'fastapi' not in sys.modules; assert 'starlette.requests' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', command], env=dict(os.environ), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_public_preview_confirm_replay_recovers_fixed_version_after_lost_projection_and_later_HEAD(state):
    _intent, proposal = state.application.preview(speed_command(state))
    control = apply_command(state, proposal)
    accepted = state.application.confirm(control)
    assert accepted.newly_applied and accepted.record_projection and accepted.outcome.montage.duration_seconds == 3
    original_version = state.repo.timeline_records.get_timeline_version('ws', accepted.outcome.operation.result_version_id)
    state.repo.execution_records.save_edit_request_result(state.task, accepted.event_id, status_code=200, response={'only': 'transport projection'})
    replay = state.application.confirm(control)
    assert not replay.newly_applied and not replay.record_projection
    state.application.apply(speed_command(state))
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id != original_version.id
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (accepted.event_id + '_result',))
    recovered = state.application.confirm(control)
    assert recovered.record_projection and not recovered.newly_applied
    assert recovered.outcome.operation.id == accepted.outcome.operation.id
    assert recovered.outcome.montage.duration_seconds == 3
    saved = state.application.read_result(control)
    assert saved.outcome.operation.result_version_id == original_version.id and saved.outcome.montage.duration_seconds == 3
    assert state.repo.timeline_records.get_timeline_version('ws', original_version.id) == original_version
    assert len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 2
    changed = replace(control, expected_input_digest=saved.input_digest,
        request_fields={**control.request_fields, 'expected_input_digest': saved.input_digest})
    with pytest.raises(EditConflict, match='同一请求|不同|不一致'):
        state.application.confirm(changed)


def test_claim_and_HTTP200_projection_without_operation_stay_unknown_and_do_not_reexecute(state):
    _intent, proposal = state.application.preview(speed_command(state))
    control = apply_command(state, proposal)
    digest = state.application.input_digest(state.task, proposal, control.source)
    event_id, _ = state.repo.execution_records.claim_edit_request(state.task, control.member_id, control.request_id, 'apply',
        {'proposal_id': proposal.id, 'base_version_id': state.base.id, 'input_digest': digest, 'request': control.request_fields})
    state.repo.execution_records.save_edit_request_result(state.task, event_id, status_code=200, response={'operation': 'not a durable operation'})
    before = editorial_rows(state.repo)
    with pytest.raises(EditConflict, match='尚未确定'):
        state.application.confirm(control)
    assert editorial_rows(state.repo) == before
    assert state.application.read_result(control).outcome is None


@pytest.mark.parametrize('field', ['member', 'workspace', 'task'])
def test_foreign_identity_cannot_read_or_confirm_original_proposal(state, field):
    _intent, proposal = state.application.preview(speed_command(state))
    control = apply_command(state, proposal)
    if field == 'member':
        control = replace(control, actor_identity={'member_id': 'outside', 'local_diagnostic': False}, member_id='outside')
    elif field == 'workspace':
        control = replace(control, workspace_id='outside')
    else:
        control = replace(control, task_id='outside')
    before = editorial_rows(state.repo)
    with pytest.raises((PermissionError, KeyError)):
        state.application.read_result(control)
    with pytest.raises((PermissionError, KeyError)):
        state.application.confirm(control)
    assert editorial_rows(state.repo) == before


@pytest.mark.parametrize('mutation', ['neighbour_permission', 'source_bytes', 'base'])
def test_apply_rechecks_original_sources_moved_neighbour_and_base(state, mutation):
    _intent, proposal = state.application.preview(speed_command(state))
    control = apply_command(state, proposal, requesting=False)
    if mutation == 'neighbour_permission':
        state.repo._save_asset(replace(state.neighbour, owner_user_id='outside', visibility='private'))
    elif mutation == 'source_bytes':
        state.source.write_bytes(b'changed actual source identity')
    else:
        state.repo.timeline_records.create_timeline_version(replace(state.base, id='later-head', parent_version_id=state.base.id, sequence=3))
    before = editorial_rows(state.repo)
    with pytest.raises((PermissionError, EditConflict, OSError)):
        state.application.confirm(control)
    assert editorial_rows(state.repo) == before


def test_late_editorial_write_failure_rolls_back_on_actual_verifier_connection(state, monkeypatch):
    _intent, proposal = state.application.preview(speed_command(state))
    before = editorial_rows(state.repo)
    connections = []
    original = EditSourceVerifier.verify_change
    def observe(self, command, payload, connection):
        assert connection.in_transaction
        connections.append(connection)
        return original(self, command, payload, connection)
    monkeypatch.setattr(EditSourceVerifier, 'verify_change', observe)
    with state.repo._connect() as conn:
        conn.execute("CREATE TRIGGER reject_speed_operation BEFORE INSERT ON edit_operations BEGIN SELECT RAISE(ABORT,'synthetic late speed failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic late speed failure'):
        state.application.confirm(apply_command(state, proposal, requesting=False))
    assert len(connections) == 1
    assert editorial_rows(state.repo) == before
    assert state.repo.edit_records.get_edit_proposal('ws', proposal.id).status == 'proposed'


def test_shared_base_three_branches_and_narration_snapshot_keep_original_rules(state):
    assert state.case.ensure_montage_base('ws', state.task, state.plan).id == state.base.id
    without_head = replace(state.task, current_timeline_version_id=None)
    assert state.case.ensure_montage_base('ws', without_head, state.plan).id == state.base.id
    plan = CreationPlan('legacy-plan', 'ws', 'legacy', 'legacy', 1, 'zh-CN', '9:16', 4,
        [], [], None, [], 0, 0, 'fixed source', [], [], [], 'fixture')
    legacy = replace(state.task, id='legacy-task', plan_id=plan.id, current_timeline_version_id=None,
        confirmed_script_id='legacy-script')
    state.repo.plan_confirmation.confirm_creation_plan(plan, legacy, verified_owner_user_id=state.member.member_id)
    snapshot = state.case.current_narration_snapshot('ws', legacy)
    assert snapshot == {'narration_segments': [], 'narration_cache_key': state.narration.cache_key}
    legacy_montage = replace(state.plan, task_id=legacy.id)
    created = state.case.ensure_montage_base('ws', legacy, legacy_montage)
    assert created.sequence == 1 and created.parent_version_id is None
    assert created.snapshot['narration_cache_key'] == state.narration.cache_key
    assert state.case.current_narration_snapshot('ws', replace(legacy, confirmed_script_id=None)) == {}


def test_constructor_rejects_different_repository_owner(state):
    with pytest.raises(ValueError, match='同一仓储'):
        LinkedTimelineEditUseCase(object(), state.case)


def test_direct_apply_keeps_preview_failure_as_explicit_phase(state):
    command = speed_command(state)
    with pytest.raises(LinkedEditPreviewFailed) as caught:
        state.application.apply(replace(command, values={**command.values, 'playback_rate': None}))
    assert isinstance(caught.value.original, ValueError)
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == []
