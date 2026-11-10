"""Structural typed edits reuse the audio transaction, sources and request journal."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.audio_timeline import AudioEditRequest, apply_audio_edit
from app.creation.contracts import EditCommand
from app.creation.editing.audio import apply_audio_proposal
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.montage import creation_task_montage_plan_to_payload
from app.schemas import CreationTaskExactEditRequest
from test_assistant_fine_edit_api import fine_state as fine_state, bound, audio_action  # noqa: F401
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401


def test_structural_actions_save_replay_restore_and_repopulate_empty_tracks(fine_state):
    s = fine_state
    before = s.client.get(s.base_url).json()
    music = s.plan.audio_timeline.tracks[-1].clips[0]
    length = (music.source_end_ms - music.source_start_ms) // 2

    def apply(action, target, values):
        payload = bound(s, **audio_action(action=action, target=target, values=values))
        result = s.client.post(s.base_url + '/edits/exact', json=payload)
        assert result.status_code == 200, result.text
        replay = s.client.post(s.base_url + '/edits/exact', json=payload)
        assert replay.status_code == 200 and replay.json() == result.json()
        actual = s.client.get(s.base_url).json()
        assert actual['montage']['clips'] == before['montage']['clips']
        assert actual['montage']['cues'] == before['montage']['cues']
        return actual['montage']['audio_timeline']['tracks']

    tracks = apply('update', music.id, {'source_end_ms': music.source_start_ms + length,
        'fade_in_ms': 0, 'gain_points': [{'offset_ms': 0, 'gain_db': -12}, {'offset_ms': length, 'gain_db': 0}]})
    tracks = apply('duplicate', music.id, {'timeline_start_ms': length})
    copy = next(c for c in tracks[-1]['clips'] if c['id'] != music.id)
    tracks = apply('split', copy['id'], {'at_ms': length + length // 2})
    tracks = apply('track', 'fine-music', {'locked': True})
    locked = s.client.post(s.base_url + '/edits/exact', json=bound(s,
        **audio_action(action='delete', target=copy['id'], values={})))
    assert locked.status_code >= 400
    tracks = apply('track', 'fine-music', {'locked': False})
    for track in tracks:
        for clip in list(track['clips']):
            apply('delete', clip['id'], {})
    cap = s.client.get(s.base_url + '/assistant/edit-capabilities').json()
    assert next(item for item in cap['operations'] if item['operation_type'] == 'EditAudioTimeline')['available']
    actions = {item['action']: item for item in cap['audio_actions']}
    assert actions['add']['available'] and actions['track']['available']
    assert not actions['split']['available']
    tracks = apply('add', 'fine-music', {'source_ref': music.source_ref, 'name': '再次采用',
        'source_start_ms': music.source_start_ms, 'source_end_ms': music.source_start_ms + length,
        'timeline_start_ms': 0})
    assert len(tracks[-1]['clips']) == 1
    assert s.production.job_records.list_job_runs(s.workspace_id, s.task.id) == s.initial_jobs
    undone = s.client.post(s.base_url + '/timeline/undo', json={'request_id': 'typed-audio-undo'})
    assert undone.status_code == 200, undone.text
    assert not s.client.get(s.base_url).json()['montage']['audio_timeline']['tracks'][-1]['clips']
    redone = s.client.post(s.base_url + '/timeline/redo', json={'request_id': 'typed-audio-redo'})
    assert redone.status_code == 200, redone.text
    assert s.client.get(s.base_url).json()['montage']['audio_timeline']['tracks'] == tracks


@pytest.mark.parametrize('action', ['add', 'split', 'duplicate', 'delete'])
def test_structural_proposal_rechecks_the_original_edit_not_only_media_sources(fine_state, action):
    plan = deepcopy(fine_state.plan)
    clip = plan.audio_timeline.tracks[-1].clips[0]
    half = (clip.source_end_ms - clip.source_start_ms) // 2
    clip.source_end_ms = clip.source_start_ms + half
    clip.fade_in_ms = 0
    values = ({'source_ref': clip.source_ref, 'name': '新增', 'source_start_ms': clip.source_start_ms,
        'source_end_ms': clip.source_end_ms, 'timeline_start_ms': 0} if action == 'add' else
        {'at_ms': half // 2} if action == 'split' else {'timeline_start_ms': half} if action == 'duplicate' else {})
    edit = AudioEditRequest(action=action, target_id='fine-music' if action == 'add' else clip.id, values=values)
    after = apply_audio_edit(plan.audio_timeline, edit, round(plan.duration_seconds * 1000))
    payload = {'expected_audio_timeline': plan.audio_timeline.model_dump(),
        'edit': edit.model_dump(), 'audio_timeline': after.model_dump()}
    assert apply_audio_proposal(plan, payload, validate_sources=lambda *_: None).audio_timeline == after
    payload['audio_timeline']['tracks'][0]['gain_db'] -= 1
    with pytest.raises(EditConflict, match='原编辑命令'):
        apply_audio_proposal(plan, payload, validate_sources=lambda *_: None)


def test_new_audio_source_is_bound_before_and_during_the_commit(fine_state, tmp_path):
    plan = fine_state.plan
    existing = {c.source_ref for t in plan.audio_timeline.tracks for c in t.clips}
    files = {ref: tmp_path / f'source-{i}' for i, ref in enumerate([*existing, 'artifacts/new.wav'])}
    for file in files.values():
        file.write_bytes(b'actual-input')
    verifier = EditSourceVerifier(SimpleNamespace(get_creation_task_montage_plan=lambda *a, **kw:
        creation_task_montage_plan_to_payload(plan)), identity={},
        audio_service=SimpleNamespace(source=lambda ws, task, ref, **kw: files[ref]), audio_actor=None)
    command = EditCommand('ws', 'task', {}, 'base', 'EditAudioTimeline', {'audio_edit': {
        'action': 'add', 'target_id': 'fine-music', 'values': {'source_ref': 'artifacts/new.wav', 'name': 'new',
        'source_start_ms': 0, 'source_end_ms': 100, 'timeline_start_ms': 0}}})
    facts = verifier.freeze(command)
    assert {item['reference'] for item in facts} == existing | {'artifacts/new.wav'}
    command = command.__class__(**{**command.__dict__, 'input_source_facts': facts})
    files['artifacts/new.wav'].write_bytes(b'changed-source-input')
    with pytest.raises(EditConflict, match='源文件'):
        verifier.verify(command, None)


@pytest.mark.parametrize('action,values', [('update', {'source_ref': 'artifacts/new.wav'}),
    ('track', {'gain_points': []}), ('split', {}), ('delete', {'gain_db': -8}),
    ('add', {'source_ref': '/tmp/not-a-source'}), ('replace_narration', {})])
def test_audio_action_schema_preserves_closed_action_and_source_boundaries(action, values):
    with pytest.raises(ValueError):
        CreationTaskExactEditRequest.model_validate(audio_action(action=action, values=values))
