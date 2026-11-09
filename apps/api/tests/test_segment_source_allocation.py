"""Metadata-only section planning over authorized, explicitly opaque material."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import TaskEvent, TimelineVersion
from app.creation.duration_requirement import DurationConflict
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.creation.segment_production_use_case import SegmentProductionUseCase
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.production import MaterialAsset, MaterialSegment
from _media_preview_test_fixtures import synthetic_wav_bytes
from test_creation_execution import _narration_units_case


@pytest.fixture
def planning(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('metadata source allocation must not start a media or model process')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    case = _narration_units_case(tmp_path, section_count=2, duration_seconds=30, start_job=False)
    path = tmp_path/'library/opaque.mp4'
    path.write_bytes(b'opaque planning input, never decoded')
    asset = case.repo._save_asset(MaterialAsset(id='material', workspace_id='local', file_name=path.name,
        path=str(path), size_bytes=path.stat().st_size,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), duration_seconds=30,
        usage_role='b_roll', review_status='approved', analysis_status='ready', media_type='video',
        owner_user_id=case.actor.member_id, segments=[MaterialSegment(id='original-segment', asset_id='material',
            start_seconds=0, end_seconds=30, description='现场', review_status='approved')]))
    reference = case.repo.workspace_media_reference_for_asset('local', asset.id)
    with case.repo._connect() as conn:
        case.repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    audio_path = tmp_path/'voice.wav'
    audio_path.write_bytes(synthetic_wav_bytes(1))
    audio = NarrationArtifactRecord(id='voice', workspace_id='local', task_id=case.task_id,
        script_id=case.script_id, voice_profile_id='voice_1', cache_key='a'*64, audio_path=str(audio_path),
        duration_seconds=1, sample_rate=8000, model_id='test', language='zh', pronunciation_version='fixture')
    service = SegmentProductionUseCase(case.repo, narration_stage=case.stage, renderer=None,
        artifact_store=ArtifactStore(tmp_path/'artifacts'), output_root=tmp_path/'temporary')
    case.owner.task_authorizations = TaskAuthorizationUseCase(case.repo,
        lambda identity: 'ready' if identity['member_id'] == case.actor.member_id else 'unauthorized')
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    case.owner.task_authorizations.ensure(task, case.actor)
    outside_path = tmp_path/'library/outside.mp4'
    outside_path.write_bytes(b'available after the original task scope was sealed')
    outside = case.repo._save_asset(replace(asset, id='outside', file_name=outside_path.name,
        path=str(outside_path), size_bytes=outside_path.stat().st_size,
        modified_at=datetime.fromtimestamp(outside_path.stat().st_mtime, timezone.utc),
        segments=[replace(asset.segments[0], id='outside-segment', asset_id='outside')]))
    with case.repo._connect() as conn:
        case.repo._claim_workspace_media_reference(conn, workspace_id='local',
            reference=case.repo.workspace_media_reference_for_asset('local', outside.id))
    state = SimpleNamespace(case=case, service=service, audio=audio, asset=asset, reference=reference)
    yield state
    case.scheduler.close()


def adopt_first(state, start, end):
    case = state.case
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    previous = (case.repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
        if task.current_timeline_version_id else None)
    sequence = previous.sequence+1 if previous is not None else 1
    section = case.repo.script_records.get_creation_task_script('local', case.script_id).sections[0]
    publication = TaskEvent(f'adopted-first-{sequence}', 'local', case.task_id, None, 'segment_publication', {
        'section_id': section.id, 'script_id': case.script_id, 'text': section.content,
        'montage': {'delivery_window': {'section_id': section.id, 'start_seconds': 0, 'end_seconds': 15},
            'clips': [{'asset_id': state.asset.id, 'source_start_seconds': start,
                       'source_end_seconds': end, 'person_render_id': None}]}})
    with case.repo._connect() as conn:
        case.repo.execution_records.append_task_event(publication, connection=conn)
        case.repo.timeline_records.create_timeline_version(TimelineVersion(f'allocated-{sequence}', 'local',
            case.task_id, previous.id if previous else None, sequence,
            {'stage_segments': {section.id: publication.id}}, None), connection=conn,
            expected_current_version_id=task.current_timeline_version_id)


def test_following_section_uses_only_remaining_authorized_source_and_keeps_original_ids(planning):
    state, case = planning, planning.case
    adopt_first(state, 0, 15)
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    before = asdict(case.repo.get_asset('local', state.asset.id))
    plan, _, _, _ = state.service.section_plan(task, 'section_2', state.audio, case.owner)
    assert [(clip.asset_id, clip.segment_id, clip.source_start_seconds, clip.source_end_seconds)
        for clip in plan.clips] == [(state.asset.id, 'original-segment', 15, 30)]
    assert plan.delivery_window == {'section_id': 'section_2', 'start_seconds': 15, 'end_seconds': 30}
    replacement, _, _, _ = state.service.section_plan(task, 'section_1', state.audio, case.owner)
    assert [(clip.source_start_seconds, clip.source_end_seconds) for clip in replacement.clips] == [(0, 15)]
    assert asdict(case.repo.get_asset('local', state.asset.id)) == before


def test_remaining_source_shortage_keeps_adopted_section_and_reports_conflict(planning):
    state, case = planning, planning.case
    # One second cannot fill fifteen seconds within the planner's existing
    # minimum picture speed; it must not reuse the adopted twenty-nine seconds.
    adopt_first(state, 0, 29)
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    adopted = case.repo.section_inputs.stage_segment_state('local', case.task_id, 'section_1')
    with pytest.raises(DurationConflict):
        state.service.section_plan(task, 'section_2', state.audio, case.owner)
    assert case.repo.section_inputs.stage_segment_state('local', case.task_id, 'section_1') == adopted


def test_same_window_with_changed_adopted_source_invalidates_prepared_planning(planning):
    state, case = planning, planning.case
    adopt_first(state, 0, 15)
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    _, _, montage, checksum = state.service.section_plan(task, 'section_2', state.audio, case.owner)
    prepared = {'section_id': 'section_2', 'audio_cache_key': state.audio.cache_key, 'montage': montage,
        'planning_dependency_digest': checksum}
    with case.repo.execution_inputs.creation_execution_read() as conn:
        state.service.validate_planning_dependency(prepared, workspace_id='local', task_id=case.task_id, connection=conn)
    adopt_first(state, 15, 30)
    with case.repo.execution_inputs.creation_execution_read() as conn:
        with pytest.raises(ValueError, match='全片时长规划'):
            state.service.validate_planning_dependency(prepared, workspace_id='local', task_id=case.task_id, connection=conn)
