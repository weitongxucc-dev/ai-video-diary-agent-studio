"""Compound application from qualified opaque clips, registered PCM and real synthetic SRT."""
from dataclasses import replace

import pytest

from app.creation.contracts import TimelineVersion
from app.creation.editing.compound_use_case import CompoundEditUseCase, CompoundPreviewCommand, CompoundSelectionCommand
from app.creation.editing.metadata_use_case import execution_subtitle_track
from app.creation.transactions import clone_subtitle_track
from app.subtitle_tracks import render_srt
from test_linked_speed_application import speed_state

COMPOUND_TEXT = '当前镜头亮一点，同时配音片段1音量降低3分贝，字幕字号改为64'


def compound_state(tmp_path):
    state = speed_state(tmp_path)
    zh = clone_subtitle_track(state.track, timeline_version_id='compound-base', track_id='compound-zh')
    en = replace(zh, id='compound-en', language='en-US', source_language='zh-CN', generation_method='translation',
        style={**zh.style, 'enabled': False}, cues=[
            replace(zh.cues[0], id='en-cue', source_cue_id=zh.cues[0].id, text='Original caption'),
            replace(zh.cues[1], id='en-next', source_cue_id=zh.cues[1].id, text='Next caption')])
    tracks = []
    for track in (zh, en):
        reference, transform = state.case.publish_subtitle_text(workspace_id='ws', task_id=state.task.id,
            proposal_id=f'fixture-{track.id}', kind='fixture_compound_input', input_refs=[],
            content=render_srt(execution_subtitle_track(track)), engine='synthetic-fixture-input/v1',
            parameters={'language': track.language})
        tracks.append(replace(track, artifact_ref=reference, transform_id=transform))
    state.zh, state.en = tracks
    state.base = TimelineVersion('compound-base', 'ws', state.task.id, state.base.id, 3,
        {**state.base.snapshot, 'subtitle_track_ids': [track.id for track in tracks]}, None)
    state.repo.create_timeline_version(state.base)
    for track in tracks:
        state.repo.create_subtitle_track(track)
    state.task = state.repo.task_records.get_creation_task('ws', state.task.id)
    state.application = CompoundEditUseCase(state.repo, state.case)
    return state


def preview_command(state):
    return CompoundPreviewCommand('ws', state.task.id, state.identity, state.base.id, COMPOUND_TEXT,
        'montage', ['clip:clip'], 750)


def test_prepared_selection_recompiles_original_context_and_original_save_base_CAS(tmp_path):
    state = compound_state(tmp_path)
    full = state.application.prepare_preview(preview_command(state))
    assert full.candidate.plan.clips[0].visual_effects['exposure'] == .25
    assert full.candidate.plan.audio_timeline.tracks[0].clips[0].gain_db == -3
    assert all(track.style['font_size'] == 64 for track in full.candidate.subtitle_tracks)
    intent, proposal = state.application.save_preview(full)
    old = state.repo.edit_records.get_edit_proposal('ws', proposal.id)
    partial = state.application.prepare_selection(CompoundSelectionCommand('ws', state.task.id, proposal.id,
        state.identity, included_step_indices=[0, 2]))
    assert [step['index'] for step in partial.candidate.steps] == [0, 1, 2]
    assert [step['enabled'] for step in partial.candidate.steps] == [True, False, True]
    assert partial.candidate.plan.audio_timeline.tracks[0].clips[0].gain_db == 0
    assert partial.intent.raw_command == intent.raw_command and partial.intent.playhead_ms == 750
    assert partial.intent.scope['context_refs'] == ['clip:clip']
    assert state.repo.edit_records.get_edit_proposal('ws', proposal.id) == old
    later_tracks = [clone_subtitle_track(track, timeline_version_id='later-head', track_id='later-' + track.id)
        for track in (state.zh, state.en)]
    state.repo.create_timeline_version(replace(state.base, id='later-head', parent_version_id=state.base.id, sequence=4,
        snapshot={**state.base.snapshot, 'subtitle_track_ids': [track.id for track in later_tracks]}))
    for track in later_tracks:
        state.repo.create_subtitle_track(track)
    with pytest.raises(ValueError, match='版本|变化|version'):
        state.application.save_preview(partial)
    assert not state.repo.edit_records.list_edit_operations('ws', state.task.id)
