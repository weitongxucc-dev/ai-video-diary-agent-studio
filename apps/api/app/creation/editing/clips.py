"""Pure shot settings and short-preview candidate computation."""
from dataclasses import asdict, dataclass, replace
from copy import deepcopy
from uuid import uuid4
from app.visual_effects import VisualEffects, with_visual_effects, fade_filters, clip_playback_rate

class ClipPreviewConflict(ValueError):
    """The current version or selected range cannot accept this edit."""


@dataclass(frozen=True)
class ClipPreviewCommand:
    task_id: str
    base_version_id: str
    operation_type: str
    clip_ids: list[str]
    values: dict
    raw_command: str
    source: dict | None = None


def prepare_clip_edit(plan, command):
    """Pure candidate computation; timing and all unselected objects stay intact."""
    selected = [clip for clip in plan.clips if clip.id in command.clip_ids]
    if not selected or len(selected) != len(set(command.clip_ids)):
        raise KeyError("所选镜头不存在")
    if any(clip.locked for clip in selected) or plan.status == "rendering":
        raise ClipPreviewConflict("所选范围包含锁定镜头或任务正在渲染")
    if command.operation_type == "AdjustVisualEffects":
        after = [with_visual_effects(clip, command.values) for clip in selected]
        if all(VisualEffects.model_validate(a.visual_effects).model_dump() == b.visual_effects
               for a, b in zip(selected, after, strict=True)):
            raise ValueError("画面参数没有变化")
    elif command.operation_type == "ChangeCrop":
        if set(command.values) != {"fit_mode", "crop_x", "crop_y"} or len(selected) != 1:
            raise ValueError("exact edit is missing required values or mixes multiple operations")
        from app.visual_effects import framing_filter
        framing_filter(**command.values)
        after = [replace(selected[0], **command.values)]
    elif command.operation_type == "ChangeClipSpeed":
        if set(command.values) != {"playback_rate"} or len(selected) != 1:
            raise ValueError("exact edit is missing required values or mixes multiple operations")
        rate = command.values["playback_rate"]
        if type(rate) not in {int, float} or not .25 <= rate <= 4:
            raise ValueError("画面速度须在0.25至4倍之间")
        clip = selected[0]
        end = round(clip.source_start_seconds + (clip.timeline_end_seconds - clip.timeline_start_seconds) * rate, 3)
        after = [replace(clip, source_end_seconds=end)]
    else:
        raise ValueError("未知短镜头画面修改")
    for clip in after:
        fade_filters(clip)
    if [asdict(item) for item in selected] == [asdict(item) for item in after]:
        raise ClipPreviewConflict("这项设置与当前镜头一致，没有创建新的时间线版本")
    return selected, after


def needs_full_visual_preview(before, after):
    return (before.timeline_end_seconds - before.timeline_start_seconds > 5 and
        bool(before.visual_effects.get("fade_out_seconds") or after.visual_effects.get("fade_out_seconds")))


CLIP_SETTING_OPERATIONS = frozenset({'ChangeCrop', 'ChangeClipSpeed', 'SetClipLock', 'ConfirmClip'})
CLIP_METADATA_OPERATIONS = CLIP_SETTING_OPERATIONS | {'ReplaceClip', 'TrimClip', 'SplitClip', 'ReorderClip'}


def split_clip_picture(plan, clip_id, split_time_seconds):
    edited = deepcopy(plan)
    clip = next((item for item in edited.clips if item.id == clip_id), None)
    if clip is None:
        raise KeyError('creation task montage clip not found')
    if clip.locked:
        raise ClipPreviewConflict('请先解锁镜头，再分割。')
    at = round(split_time_seconds, 3)
    if min(at-clip.timeline_start_seconds, clip.timeline_end_seconds-at) < .04:
        raise ValueError('分割点须在镜头内，两侧至少保留0.04秒。')
    effects = VisualEffects.model_validate(clip.visual_effects)
    source_at = round(clip.source_start_seconds + (at-clip.timeline_start_seconds)*clip_playback_rate(clip), 3)
    second = replace(clip, id=f'clip_{uuid4().hex[:12]}', source_start_seconds=source_at,
        timeline_start_seconds=at, transition_in_seconds=0)
    if effects.fade_in_seconds or effects.fade_out_seconds:
        envelope = clip.fade_envelope_duration_frames
        if envelope is None:
            envelope = round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30)
        second.fade_envelope_duration_frames = clip.fade_envelope_duration_frames = envelope
        second.fade_envelope_offset_frames = clip.fade_envelope_offset_frames + (
            round(at*30)-round(clip.timeline_start_seconds*30))*clip.fade_envelope_rate
    clip.source_end_seconds, clip.timeline_end_seconds = source_at, at
    edited.clips.insert(edited.clips.index(clip)+1, second)
    return edited


def reorder_picture(plan, new_order):
    current = [clip.id for clip in plan.clips]
    if len(current) != len(new_order) or set(current) != set(new_order):
        raise ClipPreviewConflict('排序目标与当前镜头不一致，请重新预览。')
    if plan.status == 'rendering':
        raise ClipPreviewConflict('rendering montage cannot be edited')
    edited = deepcopy(plan)
    by_id = {clip.id:clip for clip in edited.clips}
    cursor = 0.
    for index, clip_id in enumerate(new_order):
        clip = by_id[clip_id]
        if clip.locked and (current.index(clip_id) != index or abs(round(cursor, 3)-clip.timeline_start_seconds) > .001):
            raise ClipPreviewConflict('本次排序会移动已锁定镜头，请先解锁受影响的镜头。')
        cursor += clip.timeline_end_seconds-clip.timeline_start_seconds
    edited.clips = [by_id[clip_id] for clip_id in new_order]
    cursor = 0.
    for clip in edited.clips:
        duration = clip.timeline_end_seconds-clip.timeline_start_seconds
        clip.timeline_start_seconds, clip.timeline_end_seconds = round(cursor, 3), round(cursor+duration, 3)
        cursor += duration
    return edited


def apply_clip_patch(plan, clip_id, values, *, resolve_source, link_reordered):
    """One shot edit; source authority and optional cross-track linkage are explicit ports."""
    if plan.status == 'rendering':
        raise ClipPreviewConflict('rendering montage cannot be edited')
    if values.get('split_time_seconds') is not None:
        return split_clip_picture(plan, clip_id, values['split_time_seconds']), None, None
    edited = deepcopy(plan)
    clip = next((item for item in edited.clips if item.id == clip_id), None)
    if clip is None:
        raise KeyError('creation task montage clip not found')
    if clip.locked and any(values.get(key) is not None for key in
            ('fit_mode', 'crop_x', 'crop_y', 'asset_id', 'segment_id', 'source_start_seconds', 'position', 'playback_rate')):
        raise ClipPreviewConflict('请先解锁镜头，再修改素材、裁剪或位置。')
    replacing = values.get('asset_id') is not None or values.get('segment_id') is not None
    source = resolve_source(clip, values)
    duration = clip.timeline_end_seconds-clip.timeline_start_seconds
    start = values.get('source_start_seconds')
    if start is None:
        start = source.start_seconds if replacing else max(source.start_seconds, clip.source_start_seconds)
    rate = values.get('playback_rate') if values.get('playback_rate') is not None else clip_playback_rate(clip)
    if abs(rate-1) > .001 and (clip.person_render_id or source.is_speech):
        raise ClipPreviewConflict('人物口型镜头不能只改变画面速度；请调整配音后重新对口型。')
    end = start+duration*rate
    if start < source.start_seconds-1e-6 or end > source.end_seconds+1e-6:
        raise ValueError(f'当前镜头需保留 {duration:.1f} 秒，裁剪起点须在 {source.start_seconds:.1f}–{max(source.start_seconds,source.end_seconds-duration*rate):.1f} 秒之间。')
    if replacing:
        clip.asset_id, clip.segment_id, clip.source_path = source.asset_id, source.segment_id, source.reference
        clip.role, clip.match_reasons, clip.match_score = source.role, ['由用户替换', '当前工作区可用素材'], 1.
        clip.manually_selected, clip.person_render_id = True, None
    clip.source_start_seconds, clip.source_end_seconds = round(start, 3), round(end, 3)
    for key in ('locked', 'fit_mode', 'crop_x', 'crop_y'):
        if values.get(key) is not None:
            setattr(clip, key, values[key])
    if values.get('confirmed') is True or replacing:
        clip.needs_confirmation = False
    tracks = linkage = None
    if values.get('position') is not None:
        index, target = edited.clips.index(clip), min(values['position'], len(edited.clips)-1)
        if index != target:
            order = [item.id for item in edited.clips]
            order.insert(target, order.pop(index))
            edited = reorder_picture(edited, order)
            if clip.person_render_id:
                tracks, linkage = link_reordered(edited)
    return edited, tracks, linkage
