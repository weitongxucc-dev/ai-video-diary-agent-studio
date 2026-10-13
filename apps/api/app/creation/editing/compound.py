"""One compiled picture/audio/subtitle candidate for a compound edit."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from app.audio_timeline import AudioTimeline
from app.compound_edits import apply_compound_subtitles, compile_compound_edit
from app.creation.contracts import EditCommand, EditIntent, EditProposal, SubtitleTrack
from app.creation.editing.metadata import check_command
from app.creation.editing.sources import EditSourceVerifier
from app.models import utc_now
from app.subtitle_schedule import new_subtitle_gap_intervals
from app.transitions import dissolve_frames
from app.visual_effects import fade_filters

if TYPE_CHECKING:
    from app.montage import CreationTaskMontagePlan
    from app.production import CreationTask


@dataclass(frozen=True)
class CompoundEditCandidate:
    plan: CreationTaskMontagePlan
    subtitle_tracks: list[SubtitleTrack]
    steps: list[dict]
    external_effects: list[str]


def calculate_compound_edit(
    plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack], audio: AudioTimeline,
    *, raw_command: str, selected_refs: list[str], base_version_id: str | None,
    playhead_ms: int | None, included_step_indices: list[int] | None = None,
) -> CompoundEditCandidate | None:
    compiled = compile_compound_edit(raw_command, plan, audio, selected_refs,
        base_version_id, playhead_ms, included_step_indices)
    if compiled is None:
        return None
    if plan.status == "rendering":
        raise ValueError("正在渲染，请完成后重新预览")
    changed, steps = compiled
    dissolve_frames(changed.clips)
    for clip in changed.clips:
        fade_filters(clip)
    captions = apply_compound_subtitles(tracks, steps, plan.duration_seconds)
    if "字幕不变" in raw_command and captions != tracks:
        raise ValueError("字幕不变与字幕修改冲突，请明确要保留的内容")
    effects = ["中文字幕内容已改变，旧英文翻译将失效；需要重新翻译后输出双语字幕。"] if (
        any(track.language == "en-US" and track.source_language == "zh-CN" for track in tracks)
        and not any(track.language == "en-US" and track.source_language == "zh-CN" for track in captions)
    ) else []
    effects.extend(
        f"{'中文' if before.language == 'zh-CN' else '英文'}字幕新增无字幕时段：{start:.2f}–{end:.2f} 秒。"
        for before in tracks for after in captions if before.id == after.id
        for start, end in new_subtitle_gap_intervals(before, after, plan.duration_seconds)
    )
    if any(step["enabled"] and step["operation_type"] == "UpdateSubtitleCue" for step in steps):
        effects.append("字幕文字和显示时间的修改不会重新生成配音。")
    return CompoundEditCandidate(changed, captions, steps, effects)


def prepare_compound_proposal(
    task: CreationTask, before: CreationTaskMontagePlan, command: EditCommand,
    candidate: CompoundEditCandidate,
    *, raw_command: str, context_stage: str, selected_refs: list[str], playhead_ms: int | None,
    selection_command: str | None, verifier: EditSourceVerifier, output_refs: Sequence[str],
) -> tuple[EditCommand, EditIntent, EditProposal]:
    check_command(task, command)
    command = verifier.bind_affected(command, before, candidate.plan)
    refs = list(dict.fromkeys(step["target_ref"] for step in candidate.steps if step["enabled"]))
    now = utc_now()
    intent = EditIntent(
        id=f"intent_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        raw_command=raw_command, context_stage=context_stage, selected_object_refs=refs, playhead_ms=playhead_ms,
        intent_type="compound_edit",
        scope={"target_refs": refs, "context_refs": selected_refs, "selection_command": selection_command},
        constraints={"preserve_video_duration": True, "affected_source_scope": verifier.scope_payload(command),
            **({"request_source": command.source} if command.source is not None else {})},
        confidence=1, status="interpreted", created_at=now,
    )
    proposal = EditProposal(
        id=f"proposal_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        intent_id=intent.id, summary="；".join(step["command"] for step in candidate.steps if step["enabled"]),
        target_refs=refs,
        operation={"operation_type": "EditCompoundTimeline", "payload": {
            "base_version_id": command.base_version_id, "steps": candidate.steps, "selection_enabled": True,
            "clips": [asdict(clip) for clip in candidate.plan.clips],
            "audio_timeline": candidate.plan.audio_timeline.model_dump(),
        }},
        invalidated_refs=list(output_refs), external_effects=candidate.external_effects,
        preview={side: {"ref": f"timeline:{command.base_version_id}", "label": label,
            "detail": "草稿参数已计算；应用时保存新版本，不自动生成媒体。"}
            for side, label in (("before", "当前草稿"), ("after", "修改后草稿"))},
        created_at=now, updated_at=now,
    )
    return command, intent, proposal


def apply_compound_proposal(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack], command: EditCommand,
    *, validate_audio: Callable[[AudioTimeline, float], None],
) -> tuple[CreationTaskMontagePlan, list[SubtitleTrack]]:
    from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload

    check_command(task, command)
    data = creation_task_montage_plan_to_payload(plan)
    data.update(clips=command.values["clips"], audio_timeline=command.values["audio_timeline"])
    changed = creation_task_montage_plan_from_payload(data)
    validate_audio(changed.audio_timeline, changed.duration_seconds)
    captions = apply_compound_subtitles(tracks, command.values["steps"], plan.duration_seconds)
    return changed, captions
