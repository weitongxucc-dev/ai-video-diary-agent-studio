"""Validate a proposed audio edit against the current graph and stored sources."""
from copy import deepcopy

from app.audio_timeline import AudioEditRequest, AudioTimeline, apply_audio_edit
from app.creation.editing.picture import EditConflict


def apply_audio_proposal(plan, payload, *, validate_sources):
    current = plan.audio_timeline
    if current is None or current.model_dump() != payload.get('expected_audio_timeline'):
        raise EditConflict("原音轨已变化，请重新预览")
    edit = AudioEditRequest.model_validate(payload['edit'])
    if edit.action == 'replace_narration':
        raise ValueError("整轨录音采用须经过对应录音用例")
    proposed = AudioTimeline.model_validate(payload['audio_timeline'])
    current_ids = {clip.id for track in current.tracks for clip in track.clips}
    added_ids = {clip.id for track in proposed.tracks for clip in track.clips} - current_ids
    if len(added_ids) != (1 if edit.action in {'add', 'split', 'duplicate'} else 0):
        raise EditConflict("音频候选与原编辑命令不一致，请重新预览")
    calculated = apply_audio_edit(current, edit, round(plan.duration_seconds * 1000),
        new_clip_id=next(iter(added_ids), None))
    if calculated.model_dump() != proposed.model_dump():
        raise EditConflict("音频候选与原编辑命令不一致，请重新预览")
    validate_sources(proposed, plan.duration_seconds)
    edited = deepcopy(plan)
    edited.audio_timeline = proposed
    return edited
