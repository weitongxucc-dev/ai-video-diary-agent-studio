"""Finite subtitle application from synthetic SQLite, opaque source facts and PCM/SRT."""
from dataclasses import replace
import hashlib
import wave

from PIL import Image
import pytest

from app.creation.contracts import CoverAsset, EditCommand, SubtitleCue, TimelineVersion, JobRun, ArtifactRecord, TransformRun
from app.creation.editing.metadata_use_case import execution_subtitle_track
from app.creation.editing.subtitle_use_case import SubtitleEditUseCase
from app.creation.transactions import clone_subtitle_track
from app.montage import creation_task_montage_plan_to_payload
from app.models import utc_now
from app.subtitle_tracks import render_srt
from test_metadata_edit_use_case import metadata_state


def subtitle_state(tmp_path, *, bilingual=True, with_cover=True):
    state = metadata_state(tmp_path)
    voice = state.case.output_root / state.narration.audio_path
    voice.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(voice), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 64000)
    state.zh = clone_subtitle_track(state.track, timeline_version_id='subtitle-base', track_id='family-zh',
        cues=[SubtitleCue('cue', None, '原字幕', 0, 2), SubtitleCue('next', None, '后字幕', 2, 4)])
    state.en = replace(state.zh, id='family-en', language='en-US', source_language='zh-CN',
        generation_method='translation', style={**state.zh.style, 'enabled': False},
        cues=[SubtitleCue('en-cue', 'cue', 'Original caption', 0, 2), SubtitleCue('en-next', 'next', 'Next caption', 2, 4)])
    tracks = [state.zh, state.en] if bilingual else [replace(state.zh,
        cues=[SubtitleCue('cue', None, '原字幕', 0, 4)])]
    prepared_tracks = []
    for track in tracks:
        reference, transform = state.case.publish_subtitle_text(workspace_id='ws', task_id=state.task.id,
            proposal_id=f'fixture-{track.id}', kind='fixture_subtitle_input', input_refs=[],
            content=render_srt(execution_subtitle_track(track)), engine='synthetic-fixture-input/v1',
            parameters={'language': track.language})
        prepared_tracks.append(replace(track, artifact_ref=reference, transform_id=transform))
    tracks = prepared_tracks
    state.zh = next(track for track in tracks if track.language == 'zh-CN')
    if bilingual:
        state.en = next(track for track in tracks if track.language == 'en-US')
    snapshot = {**state.base.snapshot, 'montage': creation_task_montage_plan_to_payload(state.plan),
        'subtitle_track_ids': [track.id for track in tracks]}
    state.cover = None
    if with_cover:
        cover_ref = 'artifacts/task/fixture-cover.png'
        cover_file = state.case.output_root / cover_ref
        cover_file.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (8, 8), '#404040').save(cover_file)
        now = utc_now()
        digest = hashlib.sha256(cover_file.read_bytes()).hexdigest()
        state.repo.create_job_run(JobRun('fixture-cover-job', 'ws', state.task.id, 'fixture_input',
            'fixture-cover-input', digest))
        running = state.repo.claim_job_run('ws', 'fixture-cover-job')
        state.repo.create_artifact_record(ArtifactRecord('fixture-cover-artifact', 'ws', state.task.id, 'cover',
            cover_ref, digest, cover_file.stat().st_size, 'fixture-cover-job', []))
        state.repo.lineage_records.create_transform_run(TransformRun('fixture-cover-transform', 'ws', state.task.id,
            'fixture-cover-job', 'fixture_input', digest, [], [cover_ref], 'synthetic-fixture-input/v1',
            {}, 'succeeded', running.started_at, utc_now()))
        state.repo.finish_job_run('ws', 'fixture-cover-job', status='succeeded', output_refs=[cover_ref])
        state.cover = CoverAsset('family-cover', 'ws', state.task.id, 'subtitle-base',
            state.repo.workspace_media_reference_for_asset('ws', state.asset.id),
            0, '原封面', {}, {}, 'video_frame', 'fixture-input', cover_ref, 'fixture-cover-transform', 'valid')
        snapshot['cover_asset_id'] = state.cover.id
    state.base = TimelineVersion('subtitle-base', 'ws', state.task.id, state.base.id, 2, snapshot, None)
    state.repo.create_timeline_version(state.base)
    for track in tracks:
        state.repo.create_subtitle_track(track)
    if state.cover is not None:
        state.repo.create_cover_asset(state.cover)
    state.task = state.repo.get_creation_task('ws', state.task.id)
    state.application = SubtitleEditUseCase(state.repo, state.case)
    state.voice_bytes = voice.read_bytes()
    return state


def subtitle_command(state, operation, **values):
    task = state.repo.get_creation_task('ws', state.task.id)
    return EditCommand('ws', task.id, state.identity, task.current_timeline_version_id, operation,
        {'base_version_id': task.current_timeline_version_id, **values})


def test_style_preparation_keeps_original_ids_and_save_uses_original_base_CAS(tmp_path):
    state = subtitle_state(tmp_path)
    command = subtitle_command(state, 'ConfigureSubtitleTrack', style={'font_size': 64})
    prepared = state.application.prepare_preview(command)
    assert prepared.proposal.operation['payload'] == {'base_version_id': state.base.id, 'style': {'font_size': 64}}
    assert next(track for track in prepared.style_change.changed_tracks if track.language == 'en-US').style['enabled'] is False
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    later_zh = clone_subtitle_track(state.zh, timeline_version_id='later-head', track_id='later-zh')
    later_en = clone_subtitle_track(state.en, timeline_version_id='later-head', track_id='later-en')
    state.repo.create_timeline_version(replace(state.base, id='later-head', parent_version_id=state.base.id, sequence=3,
        snapshot={**state.base.snapshot, 'subtitle_track_ids': [later_zh.id, later_en.id]}))
    state.repo.create_subtitle_track(later_zh)
    state.repo.create_subtitle_track(later_en)
    with pytest.raises(ValueError, match='时间线已经变化'):
        state.application.prepare_preview(command)
    before_ids = prepared.intent.id, prepared.proposal.id
    with pytest.raises(ValueError, match='版本|变化|version'):
        state.application.save_preview(prepared)
    assert (prepared.intent.id, prepared.proposal.id) == before_ids
    assert not state.repo.list_edit_operations('ws', state.task.id)
