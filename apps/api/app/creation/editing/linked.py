"""Linked shot edits prepare one picture/audio/subtitle change and its proposal."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from app.audio_timeline import AudioTimeline
from app.creation.contracts import EditCommand, EditIntent, EditProposal, SubtitleTrack
from app.creation.editing.metadata import check_command
from app.creation.editing.sources import EditSourceVerifier
from app.linked_timeline import insert_clip, retime_clip, splice_clip
from app.models import utc_now

if TYPE_CHECKING:
    from app.montage import CreationTaskMontagePlan, MontageClip
    from app.creation.contracts import CreationTask


@dataclass(frozen=True)
class PreparedLinkedEdit:
    command: EditCommand
    intent: EditIntent
    proposal: EditProposal
    plan: CreationTaskMontagePlan
    subtitle_tracks: list[SubtitleTrack]


def apply_linked_speed(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack],
    audio: AudioTimeline, command: EditCommand,
    *, validate_audio: Callable[[AudioTimeline, float], None],
) -> tuple[CreationTaskMontagePlan, list[SubtitleTrack]]:
    check_command(task, command)
    changed, captions = retime_clip(
        plan, tracks, audio, command.values["clip_id"], command.values["playback_rate"]
    )
    validate_audio(changed.audio_timeline, changed.duration_seconds)
    return changed, captions


def apply_linked_splice(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack],
    audio: AudioTimeline, command: EditCommand,
) -> tuple[CreationTaskMontagePlan, list[SubtitleTrack]]:
    check_command(task, command)
    action = {"DeleteClip": "delete", "DuplicateClip": "duplicate"}[command.operation_type]
    return splice_clip(plan, tracks, audio, command.values["clip_id"], action)


def apply_linked_insert(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack],
    audio: AudioTimeline, command: EditCommand, inserted_clip: MontageClip,
) -> tuple[CreationTaskMontagePlan, list[SubtitleTrack]]:
    check_command(task, command)
    return insert_clip(plan, tracks, audio, command.values["clip_id"], inserted_clip)


def _prepared_proposal(
    command: EditCommand, before: CreationTaskMontagePlan, after: CreationTaskMontagePlan,
    tracks: list[SubtitleTrack], changed_tracks: list[SubtitleTrack],
    *, verifier: EditSourceVerifier, output_refs: Sequence[str], raw_command: str,
    scope: dict[str, object], playhead_ms: int | None, summary: str,
    payload: dict[str, object], preview: dict[str, object], effects: list[str],
) -> PreparedLinkedEdit:
    # Store exactly the actual moved shots/cuts and the supplied replacement
    # source. The same bound command is rechecked by the joint commit later.
    command = verifier.bind_affected(command, before, after)
    now = utc_now()
    refs = ([f"clip:{command.values['clip_id']}"] if command.values.get('clip_id') else
            [f"clip:{clip_id}" for clip_id in command.affected_clip_ids or ()])
    intent = EditIntent(
        id=f"intent_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        raw_command=raw_command, context_stage="montage", selected_object_refs=refs,
        playhead_ms=playhead_ms, intent_type="linked_timeline_edit", scope=scope,
        constraints={
            "linked_tracks": True,
            "affected_source_scope": verifier.scope_payload(command),
            **({"request_source": command.source} if command.source is not None else {}),
        },
        confidence=1.0, status="interpreted", created_at=now,
    )
    proposal = EditProposal(
        id=f"proposal_{uuid4().hex}", workspace_id=command.workspace_id, task_id=command.task_id,
        intent_id=intent.id, summary=summary, target_refs=refs,
        operation={"operation_type": command.operation_type, "payload": payload},
        invalidated_refs=[*output_refs, *(track.artifact_ref for track in tracks if track.artifact_ref)],
        preview=preview, external_effects=effects, status="proposed", created_at=now, updated_at=now,
    )
    return PreparedLinkedEdit(command, intent, proposal, after, changed_tracks)


def prepare_linked_speed(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack],
    audio: AudioTimeline, command: EditCommand,
    *, verifier: EditSourceVerifier, output_refs: Sequence[str],
    validate_audio: Callable[[AudioTimeline, float], None],
) -> PreparedLinkedEdit:
    changed, captions = apply_linked_speed(
        task, plan, tracks, audio, command, validate_audio=validate_audio
    )
    rate = command.values["playback_rate"]
    preview = {
        side: {
            "ref": f"timeline:{command.base_version_id}", "label": label,
            "detail": f"{candidate.duration_seconds:.3f} 秒；保留原声音来源，明确试听或生成时才按新速度处理。",
        }
        for side, label, candidate in (("before", "当前草稿", plan), ("after", "关联变速草稿", changed))
    }
    return _prepared_proposal(
        command, plan, changed, tracks, captions, verifier=verifier, output_refs=output_refs,
        raw_command=f"关联变速 {rate} 倍", scope={"kind": "clip", "clip_id": command.values["clip_id"]},
        playhead_ms=None,
        summary=f"所选镜头连同声音、字幕按当前速度再变速 {rate} 倍，后续内容同步移动。",
        payload={"base_version_id": command.base_version_id, "clip_id": command.values["clip_id"], "playback_rate": rate},
        preview=preview,
        effects=["草稿保留原声音引用及速度，应用后可撤销；明确试听或生成时才保持音高处理。"],
    )


def prepare_linked_splice(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack],
    audio: AudioTimeline, command: EditCommand,
    *, verifier: EditSourceVerifier, output_refs: Sequence[str],
) -> PreparedLinkedEdit:
    changed, captions = apply_linked_splice(task, plan, tracks, audio, command)
    selected = next(clip for clip in plan.clips if clip.id == command.values["clip_id"])
    label, direction = ("删除", "前移") if command.operation_type == "DeleteClip" else ("复制", "后移")
    duration = selected.timeline_end_seconds - selected.timeline_start_seconds
    summary = f"{label}当前镜头及同时段配音、音乐、音效和字幕，后续内容{direction} {duration:.3f} 秒。"
    return _prepared_proposal(
        command, plan, changed, tracks, captions, verifier=verifier, output_refs=output_refs,
        raw_command=f"{label}当前镜头",
        scope={"kind": "time_range", "start_seconds": selected.timeline_start_seconds, "end_seconds": selected.timeline_end_seconds},
        playhead_ms=round(selected.timeline_start_seconds * 1000), summary=summary,
        payload={"clip_id": selected.id, "base_version_id": command.base_version_id},
        preview={
            "before": {"ref": f"timeline:{command.base_version_id}", "label": "当前时间线", "detail": f"{len(plan.clips)} 个镜头 · {plan.duration_seconds:.3f} 秒"},
            "after": {"ref": f"clip:{selected.id}", "label": "关联编辑后", "detail": f"{len(changed.clips)} 个镜头 · {changed.duration_seconds:.3f} 秒；{summary}"},
        },
        effects=["原始素材和声音文件保留；确认后可撤销，需要重新渲染。"],
    )


def prepare_linked_insert(
    task: CreationTask, plan: CreationTaskMontagePlan, tracks: list[SubtitleTrack],
    audio: AudioTimeline, command: EditCommand, inserted_clip: MontageClip,
    *, verifier: EditSourceVerifier, output_refs: Sequence[str],
) -> PreparedLinkedEdit:
    changed, captions = apply_linked_insert(task, plan, tracks, audio, command, inserted_clip)
    added = next(clip for clip in changed.clips if clip.id == inserted_clip.id)
    length = added.timeline_end_seconds - added.timeline_start_seconds
    summary = f"在当前镜头后插入 {length:.3f} 秒素材，后续镜头、配音、音乐、音效和字幕整体后移 {length:.3f} 秒。"
    return _prepared_proposal(
        command, plan, changed, tracks, captions, verifier=verifier, output_refs=output_refs,
        raw_command="插入镜头",
        scope={"kind": "time_range", "start_seconds": added.timeline_start_seconds, "end_seconds": added.timeline_end_seconds},
        playhead_ms=round(added.timeline_start_seconds * 1000), summary=summary,
        payload={"clip_id": command.values["clip_id"], "asset_id": command.values["asset_id"],
            "segment_id": command.values["segment_id"], "source_start_seconds": command.values.get("source_start_seconds"),
            "base_version_id": command.base_version_id},
        preview={
            "before": {"ref": f"timeline:{command.base_version_id}", "label": "当前时间线", "detail": f"{len(plan.clips)} 个镜头 · {plan.duration_seconds:.3f} 秒"},
            "after": {"ref": f"clip:{added.id}", "label": "关联编辑后", "detail": f"{len(changed.clips)} 个镜头 · {changed.duration_seconds:.3f} 秒；{summary}"},
        },
        effects=["原始素材和声音文件保留；确认后可撤销，需要重新渲染。"],
    )
