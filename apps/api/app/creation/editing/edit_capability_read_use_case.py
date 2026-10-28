"""Read-only editing availability; this is not target or execution authorization.

The presenter retains operation field declarations and localized messages.
Readers remain independent, and audio is inspected only for its operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from app.creation.contracts import CreationTask, SubtitleTrack, TimelineVersion
from app.creation.editing.metadata_use_case import effective_audio_timeline
from app.montage import creation_task_montage_plan_from_payload


EditCapabilityReason = Literal[
    "no_storyboard", "no_timeline_version", "no_clip", "no_visual_clip",
    "no_audio_clip", "no_subtitle_tracks",
]


@dataclass(frozen=True)
class EditCapabilityFacts:
    payload: dict[str, object] | None
    clips: list[dict[str, object]]
    tracks: list[SubtitleTrack]
    version: TimelineVersion | None


@dataclass(frozen=True)
class EditCapabilityReadUseCase:
    read_montage: Callable
    read_subtitles: Callable
    read_version: Callable

    def read(self, task: CreationTask) -> EditCapabilityFacts:
        payload = self.read_montage(task.workspace_id, task.id) if task.montage_plan_id else None
        clips = payload.get("clips", []) if payload else []
        tracks = self.read_subtitles(task.workspace_id, task.id, timeline_version_id=task.current_timeline_version_id) if task.current_timeline_version_id else []
        version = self.read_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        return EditCapabilityFacts(payload, clips, tracks, version)

    @staticmethod
    def reason_for(facts: EditCapabilityFacts, operation: str, *, requires_clip: bool) -> EditCapabilityReason | None:
        payload, clips, tracks, version = facts.payload, facts.clips, facts.tracks, facts.version
        reason = None
        if payload is None:
            reason = "no_storyboard"
        elif version is None:
            reason = "no_timeline_version"
        elif requires_clip and not clips:
            reason = "no_clip"
        elif operation == "AdjustVisualEffects" and not clips:
            reason = "no_visual_clip"
        elif operation == "EditAudioTimeline" and not any(track.clips for track in
                effective_audio_timeline(creation_task_montage_plan_from_payload(payload)).tracks):
            reason = "no_audio_clip"
        elif operation in {"UpdateSubtitleCue", "ReplaceSubtitleText", "SplitSubtitleCue", "MergeSubtitleCue", "DeleteSubtitleCue", "ConfigureSubtitleTrack"} and not tracks:
            reason = "no_subtitle_tracks"
        return reason
