"""Metadata-only real SQLite use cases; no HTTP, renderer or model calls."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import TimelineVersion, SubtitleTrack, SubtitleCue
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, metadata_edit_command, default_subtitle_style,
)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.local_members import LocalMemberService
from app.montage import (
    CreationTaskMontagePlan, MontageClip, NarrationCue,
    creation_task_montage_plan_to_payload,
)
from app.production import (
    ProductionRepository, CreationPlan, CreationTask, MaterialAsset, MaterialSegment,
    NarrationArtifactRecord,
)
from app.task_audio import TaskAudio


def metadata_state(tmp_path):
    """Seed fixed editorial facts and opaque source bytes; never decode media."""
    library = tmp_path / 'library'
    library.mkdir()
    repo = ProductionRepository(tmp_path / 'editor.sqlite3', library,
        duration_reader=lambda _: 4, scene_reader=lambda *_: [])
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    owner = member.member_id
    plan = CreationPlan('plan', 'ws', 'metadata', 'metadata', 1, 'zh-CN', '9:16', 4,
        [], [], None, [], 0, 0, 'fixed source', [], [], [], 'fixture')
    task = CreationTask('task', 'ws', plan.id, 'metadata', 'knowledge_explanation',
        stage='montage', owner_user_id=owner, visibility='private', narration_cache_key='fixed-narration')
    repo.confirm_creation_plan(plan, task, verified_owner_user_id=owner)
    # The fixture begins at an already-produced editorial state. This is input
    # seeding, not evidence of a script/model/production workflow completing.
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
            (json.dumps(asdict(task), default=str), task.id))
    source = library / 'source.mp4'
    source.write_bytes(b'opaque metadata source; only stat identity is inspected')
    stat = source.stat()
    asset = MaterialAsset(id='asset', workspace_id='ws', file_name=source.name, path=str(source),
        size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        duration_seconds=4, owner_user_id=owner, visibility='private', analysis_status='ready', usage_role='b_roll',
        segments=[MaterialSegment('segment', 'asset', 0, 4, usage_role='b_roll')])
    repo._save_asset(asset)
    reference = repo.workspace_media_reference_for_asset('ws', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='ws', reference=reference)
    montage = CreationTaskMontagePlan('montage', 'ws', task.id, 'fixed-script', 'fixed', 'ready',
        [NarrationCue('cue', 'section', '原字幕', 4, narration_segment_id='fixed-segment')],
        [MontageClip('clip', 'cue', asset.id, 'segment', reference, 0, 4, 0, 4, 1, [], False)],
        4, 0, narration_audio_path='artifacts/task/fixed-narration.wav')
    payload = creation_task_montage_plan_to_payload(montage)
    repo.save_creation_task_montage_plan('ws', task.id, montage.id, payload)
    narration = NarrationArtifactRecord('narration', 'ws', 'fixed-script', None, 'fixed-narration',
        montage.narration_audio_path, 4, 16000, 'fixture-input', task_id=task.id)
    repo.save_narration_artifact(narration)
    base = TimelineVersion('base', 'ws', task.id, None, 1,
        {'montage': payload, 'subtitle_track_ids': ['base-zh'], 'narration_cache_key': narration.cache_key}, None)
    repo.create_timeline_version(base)
    subtitle_ref = 'artifacts/task/fixed-subtitles.srt'
    subtitle_file = tmp_path / 'outputs' / subtitle_ref
    subtitle_file.parent.mkdir(parents=True)
    subtitle_file.write_text('1\n00:00:00,000 --> 00:00:04,000\n原字幕\n', encoding='utf-8')
    track = SubtitleTrack('base-zh', 'ws', task.id, base.id, 'zh-CN', None, default_subtitle_style(),
        [SubtitleCue('cue', None, '原字幕', 0, 4)], 'timeline', None, subtitle_ref, 'fixed-track-input', 'valid')
    repo.create_subtitle_track(track)
    store = ArtifactStore(tmp_path / 'outputs')
    audio = TaskAudio(repo, store, tmp_path / 'temporary')
    case = MetadataEditUseCase(repo, store, output_root=tmp_path / 'outputs', audio_service=audio)
    return SimpleNamespace(repo=repo, task=repo.get_creation_task('ws', task.id), plan=montage, base=base,
        asset=asset, source=source, case=case, store=store, members=members, member=member, token=token,
        identity={'member_id': owner, 'local_diagnostic': False}, narration=narration, track=track)


@pytest.fixture
def state(tmp_path):
    return metadata_state(tmp_path)


def crop_command(state, **values):
    return metadata_edit_command(state.task, state.identity, 'ChangeCrop',
        {'base_version_id': state.base.id, 'clip_id': 'clip', 'fit_mode': 'fit', 'crop_x': .3, 'crop_y': .7, **values})


def editorial_rows(repo):
    with repo._connect() as conn:
        return {name: tuple(conn.execute(f'SELECT * FROM {name} ORDER BY rowid')) for name in
            ('creation_tasks', 'timeline_versions', 'edit_intents', 'edit_proposals', 'edit_operations',
             'subtitle_tracks', 'task_events', 'creation_task_montage_plans')}


def test_use_case_cold_import_has_no_main_or_http_dependency():
    code = "import sys; import app.creation.editing.metadata_use_case; assert 'app.main' not in sys.modules; assert 'fastapi' not in sys.modules; assert 'starlette.requests' not in sys.modules"
    result = subprocess.run([sys.executable, '-B', '-c', code], env=dict(os.environ),
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_metadata_save_and_staged_request_replay_recover_original_version(state):
    request_id = uuid4()
    inputs = {'operation_type': 'ChangeCrop', 'crop_x': .3}
    event_id, replay = state.case.begin(state.task, state.member.member_id, request_id, inputs)
    assert replay is None
    command = crop_command(state)
    source = {'workspace_id': state.task.workspace_id, 'task_id': state.task.id,
        'member_id': state.member.member_id, 'request_id': str(request_id),
        'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': state.base.sequence,
        'action': {'operation_type': command.operation_type,
            **{key: value for key, value in command.values.items() if key != 'base_version_id'}},
        'source_facts': EditSourceVerifier(state.repo, identity=state.identity).freeze(command)}
    result = state.case.save(state.task, replace(command, source=source))
    projection = json.loads(json.dumps({'operation': asdict(result.operation), 'montage_id': result.montage.id},
        default=lambda value: value.isoformat()))
    state.case.record_result(state.task, event_id, status_code=200, response=projection)
    current = state.repo.get_creation_task('ws', state.task.id)
    _, replay = state.case.begin(current, state.member.member_id, request_id, inputs)
    assert replay.recorded_projection['response'] == projection
    with pytest.raises(ValueError, match='不同编辑输入'):
        state.case.begin(current, state.member.member_id, request_id, {**inputs, 'crop_x': .9})
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (event_id + '_result',))
    _, recovered = state.case.begin(current, state.member.member_id, request_id, inputs)
    assert recovered.recorded_projection is None
    assert recovered.result.operation == result.operation
    assert recovered.result.montage.clips[0].crop_x == .3
    assert state.repo.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert current.narration_cache_key == state.narration.cache_key
    assert result.montage.narration_audio_path == state.plan.narration_audio_path
    assert len(state.repo.list_edit_operations('ws', state.task.id)) == 1


@pytest.mark.parametrize('field', ['member', 'workspace', 'task'])
def test_metadata_command_refuses_foreign_identity_without_any_writes(state, field):
    command = crop_command(state)
    if field == 'member':
        command = replace(command, actor_identity={'member_id': 'other', 'local_diagnostic': False})
    else:
        command = replace(command, **{field + '_id': 'other'})
    before = editorial_rows(state.repo)
    with pytest.raises(PermissionError):
        state.case.save(state.task, command)
    assert editorial_rows(state.repo) == before


@pytest.mark.parametrize('mutation', ['source', 'version'])
def test_metadata_commit_rechecks_real_source_and_version(state, monkeypatch, mutation):
    original = state.repo.confirm_direct_edit
    def mutate(command, intent, proposal, change, **kwargs):
        if kwargs.get('connection') is None:
            if mutation == 'source':
                state.source.write_bytes(b'changed source identity')
            else:
                state.repo.create_timeline_version(replace(state.base, id='concurrent', sequence=2,
                    parent_version_id=state.base.id))
        return original(command, intent, proposal, change, **kwargs)
    monkeypatch.setattr(state.repo, 'confirm_direct_edit', mutate)
    with pytest.raises(ValueError):
        state.case.save(state.task, crop_command(state))
    assert state.repo.list_edit_operations('ws', state.task.id) == []
    assert state.repo.list_edit_proposals('ws', state.task.id) == []
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == (
        'concurrent' if mutation == 'version' else state.base.id)


def test_metadata_supplied_connection_does_not_commit_or_open_a_second_writer(state):
    with state.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        operation = state.case.save(state.task, crop_command(state), connection=conn)
        assert conn.execute('SELECT id FROM edit_operations').fetchone()[0] == operation.id
        assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
        conn.rollback()
    assert state.repo.list_edit_operations('ws', state.task.id) == []
    assert state.repo.list_edit_proposals('ws', state.task.id) == []
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id


def test_metadata_late_write_failure_rolls_back_change_but_keeps_prior_claim(state):
    event_id, _ = state.case.begin(state.task, state.member.member_id, uuid4(), {'operation_type': 'ChangeCrop'})
    with state.repo._connect() as conn:
        conn.execute("CREATE TRIGGER reject_operation BEFORE INSERT ON edit_operations BEGIN SELECT RAISE(ABORT, 'second write'); END")
    before = editorial_rows(state.repo)
    with pytest.raises(sqlite3.IntegrityError, match='second write'):
        state.case.save(state.task, crop_command(state))
    assert editorial_rows(state.repo) == before
    assert state.repo.get_task_event('ws', event_id).event_type == 'edit_request_claimed'
    assert state.repo.read_edit_request_result(state.task, event_id) is None


def test_public_reorder_proposal_confirms_once_through_metadata_owner(tmp_path):
    from app.creation_execution import CreationExecutionActor
    from app.schemas import CreationTaskEditPreviewRequest

    state = metadata_state(tmp_path)
    first = replace(state.plan.clips[0], source_end_seconds=2, timeline_end_seconds=2)
    second = replace(state.plan.clips[0], id='clip-second', source_start_seconds=2,
        timeline_start_seconds=2)
    state.plan.clips = [first, second]
    payload = creation_task_montage_plan_to_payload(state.plan)
    state.repo.save_creation_task_montage_plan('ws', state.task.id, state.plan.id, payload)
    base = TimelineVersion('two-clips-base', 'ws', state.task.id, state.base.id, 2,
        {**state.base.snapshot, 'montage': payload, 'subtitle_track_ids': ['two-clips-zh']}, None)
    state.repo.create_timeline_version(base)
    state.repo.create_subtitle_track(replace(state.track, id='two-clips-zh', timeline_version_id=base.id))
    task = state.repo.get_creation_task('ws', state.task.id)
    preview = CreationTaskEditPreviewRequest(raw_command='交换第1和第2个镜头', context_stage='montage',
        selected_object_refs=['clip:clip', 'clip:clip-second'])
    _, proposal = state.case.preview_reorder(task, preview, ('swap', 0, 1), identity=state.identity,
        request_source=None, draft_only=True, execution=None,
        actor=CreationExecutionActor('ws', state.member.member_id))
    assert proposal.operation['operation_type'] == 'ReorderTimelineItems'
    before = len(state.repo.list_timeline_versions('ws', task.id))
    result = state.case.confirm(task, proposal, identity=state.identity, reader_identity=state.identity, source=None)
    reopened = ProductionRepository(state.repo.database_path, tmp_path / 'library')
    versions = reopened.list_timeline_versions('ws', task.id)
    operations = reopened.list_edit_operations('ws', task.id)
    current = reopened.get_creation_task('ws', task.id)
    assert len(versions) == before + 1 and len(operations) == 1
    assert operations[0].proposal_id == proposal.id and operations[0].result_version_id == current.current_timeline_version_id
    assert reopened.get_edit_proposal('ws', proposal.id).status == 'confirmed'
    assert [clip.id for clip in result.montage.clips] == ['clip-second', 'clip']
    assert result.montage.duration_seconds == 4
    assert result.montage.narration_audio_path == state.plan.narration_audio_path
    version = reopened.get_timeline_version('ws', current.current_timeline_version_id)
    assert version.parent_version_id == base.id and version.sequence == 3
    assert [clip['id'] for clip in version.snapshot['montage']['clips']] == ['clip-second', 'clip']
    assert reopened.get_timeline_version('ws', base.id).snapshot == base.snapshot
    assert reopened.get_creation_task_montage_plan('ws', task.id)['clips'][0]['id'] == 'clip-second'
    assert len(reopened.list_edit_operations('ws', task.id)) == 1
    evidence = os.environ.get('METADATA_CONFIRM_EVIDENCE_DIR')
    if evidence:
        Path(evidence, 'NORMAL_FACTS.json').write_text(json.dumps({'proposal': proposal.id,
            'operation_type': proposal.operation['operation_type'], 'operation': operations[0].id,
            'base_version': base.id, 'result_version': current.current_timeline_version_id,
            'versions_added': 1, 'operations': 1, 'fresh_readback': True, 'native_calls': 0}, indent=2))
