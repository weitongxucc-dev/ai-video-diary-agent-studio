"""Picture metadata computations. No repository, renderer or HTTP calls."""
from copy import deepcopy
from dataclasses import asdict

from app.visual_effects import VisualEffects, with_visual_effects
from app.graphic_overlays import validate_overlays
from app.transitions import dissolve_frames


class EditConflict(ValueError):
    """The proposal no longer describes the selected current objects."""


def apply_visual_effects(plan, payload):
    edited = deepcopy(plan)
    expected = payload["expected_clips"]
    selected = [clip for clip in edited.clips if clip.id in expected]
    if len(selected) != len(expected) or any(clip.locked or asdict(clip) != expected[clip.id] for clip in selected):
        raise EditConflict("所选镜头已变化，请重新预览")
    patch = VisualEffects.model_validate(payload["effects"]).model_dump(exclude_unset=True)
    for clip in selected:
        changed = with_visual_effects(clip, patch)
        clip.visual_effects = changed.visual_effects
        clip.fade_envelope_rate = changed.fade_envelope_rate
        clip.fade_envelope_offset_frames = changed.fade_envelope_offset_frames
        clip.fade_envelope_duration_frames = changed.fade_envelope_duration_frames
    return edited


def graphic_overlay_candidate(plan, *, action, value, identity):
    edited = deepcopy(plan)
    if action == 'add' and value is not None and identity is None:
        edited.graphic_overlays.append(value)
    elif action in {'update', 'delete'} and identity:
        index = next((i for i, item in enumerate(edited.graphic_overlays) if item['id'] == identity), -1)
        if index < 0:
            raise KeyError("图文叠加层不存在，请刷新列表")
        if action == 'delete' and value is None:
            edited.graphic_overlays.pop(index)
        elif action == 'update' and value is not None and value['id'] == identity:
            edited.graphic_overlays[index] = value
        else:
            raise ValueError("图文叠加层操作的对象或参数不匹配")
    else:
        raise ValueError("请选择添加、修改或删除图文叠加层，并提供对应参数")
    if edited.graphic_overlays == plan.graphic_overlays:
        raise ValueError("图文叠加层没有变化")
    validate_overlays(edited.graphic_overlays, edited.duration_seconds)
    return edited


def apply_graphic_overlays(plan, payload):
    edited = deepcopy(plan)
    edited.graphic_overlays = deepcopy(payload['graphic_overlays'])
    validate_overlays(edited.graphic_overlays, edited.duration_seconds)
    return edited


def apply_transition(plan, payload):
    edited = deepcopy(plan)
    expected = payload['expected_clips']
    targets = [clip for clip in edited.clips if clip.id in expected]
    if len(targets) != 2 or any(clip.locked or asdict(clip) != expected[clip.id] for clip in targets):
        raise EditConflict("转场相邻镜头已变化，请重新预览")
    next(clip for clip in targets if clip.id == payload['clip_id']).transition_in_seconds = payload['transition_seconds']
    dissolve_frames(edited.clips)
    return edited
