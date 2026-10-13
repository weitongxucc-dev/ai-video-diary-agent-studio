"""Pure shot settings and short-preview candidate computation."""
from dataclasses import asdict, dataclass, replace
from copy import deepcopy
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


def apply_clip_settings(plan, operation_type, values, *, source_bounds):
    edited = deepcopy(plan)
    clip = next((item for item in edited.clips if item.id == values['clip_id']), None)
    if clip is None:
        raise KeyError("creation task montage clip not found")
    if plan.status == 'rendering' or (clip.locked and operation_type not in {'SetClipLock', 'ConfirmClip'}):
        raise ClipPreviewConflict("请先解锁镜头，再修改素材、裁剪或位置。")
    start, end, is_speech = source_bounds(clip)
    rate = values['playback_rate'] if operation_type == 'ChangeClipSpeed' else clip_playback_rate(clip)
    if abs(rate-1) > .001 and is_speech:
        raise ClipPreviewConflict("人物口型镜头不能只改变画面速度；请调整配音后重新对口型。")
    source_start = max(start, clip.source_start_seconds)
    source_end = source_start + (clip.timeline_end_seconds-clip.timeline_start_seconds)*rate
    if source_start < start-1e-6 or source_end > end+1e-6:
        raise ValueError("裁剪或变速后的源片段超出当前可用素材区间")
    clip.source_start_seconds = round(source_start, 3)
    clip.source_end_seconds = round(source_end, 3)
    if operation_type == 'ChangeCrop':
        from app.visual_effects import framing_filter
        framing_filter(values['fit_mode'], values['crop_x'], values['crop_y'])
        clip.fit_mode, clip.crop_x, clip.crop_y = values['fit_mode'], values['crop_x'], values['crop_y']
    elif operation_type == 'SetClipLock':
        clip.locked = values['locked']
    elif operation_type == 'ConfirmClip':
        clip.needs_confirmation = False
    elif operation_type != 'ChangeClipSpeed':
        raise ValueError("未知镜头参数命令")
    if [asdict(item) for item in plan.clips] == [asdict(item) for item in edited.clips]:
        raise ClipPreviewConflict("这项设置与当前镜头一致。")
    return edited

