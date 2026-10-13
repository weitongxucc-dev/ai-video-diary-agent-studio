"""Apply the registered picture/audio metadata commands to a supplied timeline."""
from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4
import hashlib
import json

from app.creation.contracts import EditCommand, EditIntent, EditProposal
from app.creation.editing.picture import (EditConflict, apply_graphic_overlays,
    apply_transition, apply_visual_effects, graphic_overlay_candidate)
from app.creation.editing.audio import apply_audio_proposal
from app.audio_timeline import apply_audio_edit
from app.models import utc_now
from app.creation.editing.clips import ClipPreviewCommand, prepare_clip_edit
from app.creation.editing.clips import apply_clip_settings
from app.creation.editing.subtitles import apply_subtitle_display


PICTURE_METADATA_OPERATIONS = frozenset({'EditGraphicOverlay', 'SetClipTransition', 'AdjustVisualEffects'})


def check_command(task, command: EditCommand):
    if (task.workspace_id, task.id) != (command.workspace_id, command.task_id):
        raise PermissionError("编辑命令不属于当前任务")
    member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
    diagnostic = command.actor_identity.get('local_diagnostic') is True
    if member is None and not diagnostic:
        raise PermissionError("编辑命令缺少可信成员身份")
    if task.owner_user_id and task.owner_user_id != member and not diagnostic:
        raise PermissionError("当前成员不能修改此任务")
    if task.current_timeline_version_id != command.base_version_id:
        raise EditConflict("时间线已经变化，请重新预览")
    if command.source is not None and (
            command.source['workspace_id'], command.source['task_id'], command.source['member_id'],
            command.source['expected_timeline_version_id']) != (command.workspace_id, command.task_id, member, command.base_version_id):
        raise EditConflict("编辑来源与原身份或基础版本不一致")


def apply_picture_metadata(task, plan, command, *, validate_graphic_sources):
    check_command(task, command)
    if plan.status == 'rendering':
        raise EditConflict("正在渲染，请完成后修改")
    if command.operation_type == 'AdjustVisualEffects':
        return apply_visual_effects(plan, command.values)
    if command.operation_type == 'SetClipTransition':
        return apply_transition(plan, command.values)
    if command.operation_type == 'EditGraphicOverlay':
        changed = apply_graphic_overlays(plan, command.values)
        validate_graphic_sources(changed.graphic_overlays)
        return changed
    raise ValueError("未知画面元数据命令")


def apply_audio_metadata(task, plan, command, *, validate_sources):
    check_command(task, command)
    return apply_audio_proposal(plan, command.values, validate_sources=validate_sources)


def prepare_picture_command(task, plan, command, *, validate_graphic_sources):
    """A direct deterministic control forms its domain proposal, without HTTP preview."""
    check_command(task, command)
    if plan.status == 'rendering':
        raise EditConflict("正在渲染，请完成后修改")
    if command.operation_type == 'EditGraphicOverlay':
        values = command.values
        edited = graphic_overlay_candidate(plan, action=values['overlay_action'],
            value=values.get('graphic_overlay'), identity=values.get('overlay_id'))
        validate_graphic_sources(edited.graphic_overlays)
        identity = values['graphic_overlay']['id'] if values['overlay_action'] == 'add' else values['overlay_id']
        refs = [f"overlay:{identity}"]
        summary = {'add':'添加图文叠加层', 'update':'修改图文叠加层', 'delete':'删除图文叠加层'}[values['overlay_action']]
        payload = {'base_version_id':command.base_version_id, 'graphic_overlays':edited.graphic_overlays}
    elif command.operation_type == 'AdjustVisualEffects':
        before, after = prepare_clip_edit(plan, ClipPreviewCommand(command.task_id, command.base_version_id,
            'AdjustVisualEffects', command.values['clip_ids'], command.values['effects'], '调整画面颜色'))
        edited = deepcopy(plan)
        changed = {clip.id:clip for clip in after}
        edited.clips = [changed.get(clip.id, clip) for clip in edited.clips]
        payload = {'base_version_id':command.base_version_id, 'effects':command.values['effects'],
            'expected_clips':{clip.id:asdict(clip) for clip in before}}
        refs = [f"clip:{clip.id}" for clip in before]
        summary = f"调整 {len(before)} 个镜头的画面效果"
    elif command.operation_type == 'SetClipTransition':
        values = command.values
        index = next((i for i, clip in enumerate(plan.clips) if clip.id == values['clip_id']), -1)
        if index <= 0:
            raise ValueError("请选择有前一个镜头的镜头设置转场")
        expected = {clip.id:asdict(clip) for clip in plan.clips[index-1:index+1]}
        payload = {'base_version_id':command.base_version_id, **values, 'expected_clips':expected}
        edited = apply_transition(plan, payload)
        refs = [f"clip:{clip_id}" for clip_id in expected]
        summary = f"镜头 {index} → {index+1}：" + (f"叠化 {values['transition_seconds']:g} 秒" if values['transition_seconds'] else '硬切')
    else:
        raise ValueError("未知直接画面元数据命令")
    return make_metadata_proposal(command, refs, summary, payload), edited


def prepare_audio_command(task, plan, command, *, edit, validate_sources):
    check_command(task, command)
    before = plan.audio_timeline
    after = apply_audio_edit(before, edit, round(plan.duration_seconds*1000))
    if before.model_dump() == after.model_dump():
        raise ValueError("音轨参数没有变化")
    validate_sources(after, plan.duration_seconds)
    payload = {'base_version_id':command.base_version_id, 'expected_audio_timeline':before.model_dump(),
        'audio_timeline':after.model_dump(), 'edit':edit.model_dump(mode='json')}
    pair = make_metadata_proposal(command, [f"audio:{edit.target_id}"],
        '修改音轨设置' if edit.action == 'track' else '修改音频片段', payload)
    edited = deepcopy(plan)
    edited.audio_timeline = after
    return pair, edited


def make_metadata_proposal(command, refs, summary, payload):
    now = utc_now()
    source = command.source
    suffix = metadata_request_identity(source) if source and source.get('request_id') else uuid4().hex
    intent = EditIntent(id=f"intent_metadata_{suffix}", workspace_id=command.workspace_id, task_id=command.task_id,
        raw_command=source.get('raw_command') if source and source.get('raw_command') else summary,
        context_stage='montage', selected_object_refs=refs, playhead_ms=None, intent_type='exact_montage_edit',
        scope={'target_refs':refs}, constraints={'preserve_timing':True, **({'request_source':source} if source else {})},
        confidence=1, status='interpreted', created_at=now)
    proposal = EditProposal(id=f"proposal_metadata_{suffix}", workspace_id=command.workspace_id, task_id=command.task_id,
        intent_id=intent.id, summary=summary, target_refs=refs,
        operation={'operation_type':command.operation_type, 'payload':payload}, invalidated_refs=[], external_effects=[],
        preview={side:{'ref':f"timeline:{command.base_version_id}", 'label':label,
            'detail':'草稿参数已计算；应用时保存新版本，不自动生成媒体。'} for side,label in (('before','当前草稿'),('after','修改后草稿'))},
        created_at=now, updated_at=now)
    return intent, proposal


def metadata_request_identity(source):
    return hashlib.sha256(json.dumps([source['workspace_id'], source['task_id'], source['member_id'],
        source['request_id']], ensure_ascii=False).encode()).hexdigest()


def prepare_clip_command(task, plan, command, *, source_bounds):
    check_command(task, command)
    clip = next((item for item in plan.clips if item.id == command.values['clip_id']), None)
    if clip is None:
        raise KeyError("creation task montage clip not found")
    changed = apply_clip_settings(plan, command.operation_type, command.values, source_bounds=source_bounds)
    values = {key:value for key,value in command.values.items() if key != 'base_version_id'}
    payload = {'base_version_id':command.base_version_id, 'expected_asset_id':clip.asset_id,
        'expected_segment_id':clip.segment_id, **values}
    if command.operation_type == 'ChangeCrop':
        payload.update(asset_id=clip.asset_id, segment_id=clip.segment_id, source_start_seconds=clip.source_start_seconds)
    labels = {'ChangeCrop':'调整画面适配', 'ChangeClipSpeed':'改变画面速度，保留成片时长、声音和字幕',
        'SetClipLock':'更改镜头锁定状态', 'ConfirmClip':'确认当前镜头'}
    return make_metadata_proposal(command, [f"clip:{clip.id}"], labels[command.operation_type], payload), changed


def apply_clip_proposal(task, plan, command, *, source_bounds):
    check_command(task, command)
    values = command.values
    clip = next((item for item in plan.clips if item.id == values['clip_id']), None)
    if clip is None or (clip.asset_id, clip.segment_id) != (values['expected_asset_id'], values['expected_segment_id']):
        raise EditConflict("creation task edit target has changed")
    return apply_clip_settings(plan, command.operation_type, values, source_bounds=source_bounds)


def prepare_subtitle_display_command(task, plan, tracks, command):
    check_command(task, command)
    mode = command.values['subtitle_display']
    changed = apply_subtitle_display(tracks, mode)
    payload = {'base_version_id':command.base_version_id, 'subtitle_display':mode}
    summary = {'none':'关闭字幕', 'zh':'只显示中文字幕', 'en':'只显示英文字幕', 'zh-en':'显示中英双语字幕'}[mode]
    refs = [f"subtitle-track:{track.id}" for track in tracks]
    return make_metadata_proposal(command, refs, summary, payload), deepcopy(plan), changed
