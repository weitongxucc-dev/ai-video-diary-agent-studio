"""Subtitle content/style changes before artifact publication and joint commit."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from app.creation.contracts import EditCommand, EditIntent, EditProposal, SubtitleTrack
from app.creation.editing.metadata import check_command
from app.creation.editing.picture import EditConflict
from app.subtitle_edits import merge_linked_subtitle_tracks, replace_subtitle_text, restructure_subtitle
from app.subtitle_schedule import subtitle_schedule
from app.subtitle_style import validate_style_patch
from app.subtitle_tracks import SubtitleStyle
from app.models import utc_now

if TYPE_CHECKING:
    from app.montage import CreationTaskMontagePlan
    from app.creation.contracts import CreationTask


@dataclass(frozen=True)
class SubtitleContentChange:
    source: SubtitleTrack
    changed_tracks: list[SubtitleTrack]
    retained_tracks: list[SubtitleTrack]
    text_changes: list[dict]


def calculate_subtitle_content(
    tracks: list[SubtitleTrack], operation_type: str, values: dict, duration_seconds: float,
) -> SubtitleContentChange:
    source = next((track for track in tracks if track.id == values["track_id"] and track.status == "valid"), None)
    if source is None:
        raise EditConflict("请选择可用字幕轨")
    if operation_type == "MergeSubtitleCue":
        changed, changes = merge_linked_subtitle_tracks(tracks, source, values["cue_id"])
    elif operation_type == "ReplaceSubtitleText":
        track, changes = replace_subtitle_text(source, values["find_text"], values["replacement_text"])
        changed = [track]
    else:
        track, changes = restructure_subtitle(source, operation_type, values["cue_id"],
            split_offset=values.get("split_offset"), split_time_seconds=values.get("split_time_seconds"))
        changed = [track]
    subtitle_schedule([replace(track, style={**track.style, "enabled": True}) for track in changed], duration_seconds)
    changed_ids = {track.id for track in changed}
    retained = [track for track in tracks if track.id not in changed_ids and not (
        operation_type == "ReplaceSubtitleText" and source.source_language is None
        and track.source_language == source.language
    )]
    return SubtitleContentChange(source, changed, retained, changes)


def apply_subtitle_content_command(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack], command: EditCommand,
) -> tuple[CreationTaskMontagePlan, SubtitleContentChange]:
    check_command(task, command)
    change = calculate_subtitle_content(tracks, command.operation_type, command.values, plan.duration_seconds)
    changed = deepcopy(plan)
    if command.operation_type == "ReplaceSubtitleText" and change.source.source_language is None:
        text_by_id = {cue.id: cue.text for cue in change.changed_tracks[0].cues}
        for cue in changed.cues:
            if cue.id in text_by_id:
                cue.text = text_by_id[cue.id]
    return changed, change


def prepare_subtitle_content_proposal(task, plan, tracks, command, *, output_refs):
    check_command(task, command)
    change = calculate_subtitle_content(tracks, command.operation_type, command.values, plan.duration_seconds)
    source, changed_tracks = change.source, change.changed_tracks
    values = command.values
    replacement = command.operation_type == "ReplaceSubtitleText"
    if replacement:
        raw = f"全文把“{values['find_text']}”替换为“{values['replacement_text']}”"
        count = sum(item["occurrences"] for item in change.text_changes)
        summary = f"替换当前{source.language}字幕轨的 {len(change.text_changes)} 条字幕，共 {count} 处；配音和时间不变。"
        refs = [f"subtitle-track:{source.id}"]
        selected_refs = refs
        intent_type = "subtitle_text_replace"
        constraints = {"preserve_narration": True, "preserve_timing": True}
        preview = {
            "before": {"ref": refs[0], "label": "查找文字", "detail": values["find_text"]},
            "after": {"ref": refs[0], "label": "替换文字", "detail": values["replacement_text"] or "删除命中文字"},
            "text_changes": change.text_changes,
        }
        dependent = [other for other in tracks if source.source_language is None and other.source_language == source.language]
        invalidated = [source.artifact_ref, *(track.artifact_ref for track in dependent)]
        effects = (["当前原文的译文将失效，需重新生成；旧译文可通过撤销恢复。"] if dependent else []) + [
            "按文字精确匹配，不使用正则表达式；不改文案和配音。替换词内部的手动断行会移除。",
        ]
    else:
        raw = {"SplitSubtitleCue": "拆分字幕", "MergeSubtitleCue": "与下一条字幕合并", "DeleteSubtitleCue": "删除字幕"}[command.operation_type]
        bilingual = len(changed_tracks) == 2
        summary = f"{raw}，修改{'中英两轨并保留译文' if bilingual else '当前字幕轨'}；配音和镜头不变。"
        refs = [*[f"subtitle-track:{track.id}" for track in changed_tracks], f"cue:{values['cue_id']}"]
        selected_refs = [f"cue:{values['cue_id']}", *[f"subtitle-track:{track.id}" for track in changed_tracks]]
        intent_type, constraints = "subtitle_structure", {"preserve_narration": True}
        changed_ids = {track.id for track in changed_tracks}
        invalidated = [track.artifact_ref for track in tracks if track.id in changed_ids]
        preview = {
            "before": {"ref": f"subtitle-track:{source.id}", "label": "当前字幕", "detail": f"{len(source.cues)} 条"},
            "after": {"ref": f"subtitle-track:{source.id}", "label": raw, "detail": f"{len(changed_tracks[0].cues)} 条"},
            "text_changes": change.text_changes,
        }
        effects = ["同步合并英文，保留原译文与来源映射，不重新翻译。" if bilingual else "其他语言轨保持不变。", {
            "SplitSubtitleCue": "文字拆分点和显示时间按指定值执行，不代表语音自动对齐；跨拆分点的强调词会移除。",
            "MergeSubtitleCue": "合并后的字幕覆盖两条之间的空白时段。",
            "DeleteSubtitleCue": "删除会留下无字幕时段；删除本轨最后一条后可通过撤销恢复。",
        }[command.operation_type]]
    now = utc_now()
    intent = EditIntent(
        id=f"intent_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        raw_command=raw, context_stage="subtitle_cover", selected_object_refs=selected_refs, playhead_ms=None,
        intent_type=intent_type, scope={"kind": "subtitle_track", "track_id": source.id},
        constraints={**constraints, **({"request_source": command.source} if command.source is not None else {})},
        confidence=1, status="interpreted", created_at=now,
    )
    proposal = EditProposal(
        id=f"proposal_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        intent_id=intent.id, summary=summary, target_refs=refs,
        operation={"operation_type": command.operation_type, "payload": values},
        invalidated_refs=[*output_refs, *(ref for ref in invalidated if ref)],
        preview=preview, external_effects=effects, created_at=now, updated_at=now,
    )
    return change, intent, proposal


@dataclass(frozen=True)
class SubtitleStyleChange:
    original_tracks: list[SubtitleTrack]
    changed_tracks: list[SubtitleTrack]
    patch: dict


def calculate_subtitle_style(
    tracks: list[SubtitleTrack], style: dict, duration_seconds: float,
    *, validate_layout: Callable[[str, dict], None],
) -> SubtitleStyleChange:
    patch = validate_style_patch(style)
    valid = [track for track in tracks if track.status == "valid"]
    if not valid:
        raise EditConflict("当前没有可编辑字幕，请先生成字幕")
    defaults = asdict(SubtitleStyle())
    if all(all(track.style.get(key, defaults[key]) == value for key, value in patch.items()) for track in valid):
        raise EditConflict("字幕样式没有变化")
    changed = [replace(track, style={**track.style, **patch}) for track in valid]
    before = subtitle_schedule([replace(track, style={**track.style, "enabled": True}) for track in valid], duration_seconds)
    after = subtitle_schedule([replace(track, style={**track.style, "enabled": True}) for track in changed], duration_seconds)
    sample = max(before, key=lambda item: len(item.text))
    next(item for item in after if item.start_seconds <= sample.start_seconds < item.end_seconds)
    # The renderer port validates every cue, including currently hidden languages.
    for caption in after:
        validate_layout(caption.text, caption.subtitle_style)
    return SubtitleStyleChange(valid, changed, patch)


def prepare_subtitle_style_proposal(task, command, change, *, output_refs):
    check_command(task, command)
    refs = [f"subtitle-track:{track.id}" for track in change.original_tracks]
    now = utc_now()
    intent = EditIntent(
        id=f"intent_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        raw_command="调整全部字幕样式", context_stage="subtitle_cover", selected_object_refs=refs,
        playhead_ms=None, intent_type="subtitle_style", scope={"kind": "subtitle_tracks"},
        constraints={"preserve_narration": True,
            **({"request_source": command.source} if command.source is not None else {})},
        confidence=1, status="interpreted", created_at=now,
    )
    proposal = EditProposal(
        id=f"proposal_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        intent_id=intent.id, summary="调整全部字幕轨的样式；文字、时间、配音和语言开关保持不变。",
        target_refs=refs, operation={"operation_type": "ConfigureSubtitleTrack", "payload": {
            "base_version_id": command.base_version_id, "style": change.patch}},
        invalidated_refs=list(output_refs),
        preview={side: {"ref": f"timeline:{command.base_version_id}", "label": label,
            "detail": "草稿参数已计算；应用时保存新版本，不自动生成媒体。"}
            for side, label in (("before", "当前草稿"), ("after", "修改后草稿"))},
        external_effects=["中英文共用样式；已关闭的语言仍保持关闭。预览保留当前语言开关。", "SRT仅保存文本与时间，颜色等样式写入视频。"],
        created_at=now, updated_at=now,
    )
    return intent, proposal


def apply_subtitle_style_command(task, tracks, command):
    check_command(task, command)
    patch = validate_style_patch(command.values["style"])
    return [replace(track, style={**track.style, **patch}) if track.status == "valid" else track for track in tracks]
