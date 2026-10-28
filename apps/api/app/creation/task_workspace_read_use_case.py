"""Task workspace read orchestration; capabilities are observed, never executed.

The prepared facts stop before DTO validation. Script headers, voices, portrait
and render records remain late reads at their original presentation positions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.creation.contracts import CreationTask, CoverAsset, SubtitleTrack
from app.creation.plan_contracts import CreationPlan, CreationPlanPronunciationReference
from app.materials.contracts import MediaAuthorityError
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_from_payload
from app.creation.projection import TaskReadProjection
from app.creation.topic_contracts import TopicCandidate, TopicEvidenceReference
from app.creation.editing.metadata_use_case import InvalidMontage


class TaskWorkspaceNotFound(Exception):
    """The task's plan is missing or belongs to another task."""


class TaskWorkspaceSubtitleInvalid(Exception):
    """The subtitle owner rejected the current montage projection."""


@dataclass(frozen=True)
class TaskWorkspaceFacts:
    plan: CreationPlan
    selected_topic: TopicCandidate | None
    topic_evidence: list[TopicEvidenceReference]
    artifact: Any
    montage: CreationTaskMontagePlan | None
    task_read: TaskReadProjection
    person_provider: Any
    person_render_available: bool
    subtitle_tracks: list[SubtitleTrack]
    cover_assets: list[CoverAsset]
    translation_provider: Any
    translation_failure: dict[str, object] | None
    cover_frame_available: bool
    image_capability: Any


@dataclass(frozen=True)
class TaskWorkspaceReadUseCase:
    # Separate, finite owner ports. No repository proxy or request is retained.
    read_plan: Callable
    read_topics: Callable
    read_topic_evidence: Callable
    read_narration: Callable
    read_montage: Callable
    read_summary: Callable
    person_provider: Callable
    read_subtitles: Callable
    read_covers: Callable
    translation_provider: Callable
    read_events: Callable
    find_tool: Callable
    image_capability: Callable
    read_script_headers: Callable
    read_voices: Callable
    profile_readable: Callable
    read_person: Callable
    read_voice_reference: Callable
    read_pronunciations: Callable
    portrait_available: Callable
    read_person_renders: Callable

    def read(self, workspace_id: str, task: CreationTask) -> TaskWorkspaceFacts:
        try:
            plan = self.read_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise TaskWorkspaceNotFound("creation task not found") from exc
        if plan.creation_task_id != task.id:
            raise TaskWorkspaceNotFound("creation task not found")
        selected_topic = next(
            (
                candidate
                for candidate in self.read_topics(workspace_id, task.id)
                if candidate.id == task.selected_topic_id
            ),
            None,
        )
        topic_evidence = [
            reference
            for reference in self.read_topic_evidence(workspace_id, task.id)
            if selected_topic is not None and reference.candidate_id == selected_topic.id
        ]
        artifact = (
            self.read_narration(workspace_id, task.narration_cache_key)
            if task.narration_cache_key
            else None
        )
        try:
            montage = creation_task_montage_plan_from_payload(
                self.read_montage(workspace_id, task.id)
            )
        except KeyError:
            montage = None
        task_read = self.read_summary(task)
        current_version = task_read.current_version
        person_provider = self.person_provider(plan.lipsync_quality) if plan.presenter_share_target > 0 else None
        person_render_available = bool(person_provider and person_provider.ready)
        try:
            subtitle_tracks = self.read_subtitles(
                workspace_id, task, current_version=current_version, montage=montage
            )
        except InvalidMontage as exc:
            raise TaskWorkspaceSubtitleInvalid(str(exc)) from exc
        cover_assets = (
            self.read_covers(
                workspace_id,
                task.id,
                timeline_version_id=current_version.id,
            )
            if current_version is not None
            else []
        )
        translation_provider = self.translation_provider()
        last_translation = next((
            event for event in reversed(self.read_events(workspace_id, task.id))
            if event.event_type == "subtitle_translation_returned"
            and translation_provider is not None
            and event.payload.get("model_id") == translation_provider.model_id
            and event.payload.get("provider_name") == translation_provider.provider_name
        ), None)
        translation_failure = (
            last_translation.payload
            if last_translation and last_translation.payload.get("status") != "succeeded"
            else None
        )
        cover_frame_available = bool(
            montage is not None
            and montage.status == "rendered"
            and montage.output_path
            and self.find_tool("ffmpeg")
            and self.find_tool("ffprobe")
        )
        image_capability = self.image_capability()
        return TaskWorkspaceFacts(
            plan=plan,
            selected_topic=selected_topic,
            topic_evidence=topic_evidence,
            artifact=artifact,
            montage=montage,
            task_read=task_read,
            person_provider=person_provider,
            person_render_available=person_render_available,
            subtitle_tracks=subtitle_tracks,
            cover_assets=cover_assets,
            translation_provider=translation_provider,
            translation_failure=translation_failure,
            cover_frame_available=cover_frame_available,
            image_capability=image_capability,
        )

    def _resolve_voice_selection(self, *, workspace_id: str, task: CreationTask, plan: CreationPlan,
                                             voice_profile_id: str, voice_version: str):
        try:
            reference = self.read_voice_reference(workspace_id, voice_profile_id, voice_version,
                viewer_user_id=task.owner_user_id)
            entries = self.read_pronunciations(workspace_id=workspace_id,
                person_profile_id=reference.person_profile_id, episode_id=task.id)
        except (KeyError, ValueError, PermissionError, OSError) as exc:
            raise MediaAuthorityError(reason="voice_reference_unavailable", impact="声音引用未更新，未调用模型。",
                                      repair_action=str(exc)) from exc
        if reference.language not in {{"zh-CN":"zh", "en-US":"en", "zh-CN/en-US":"zh-en"}[plan.language], "zh-en"}:
            raise MediaAuthorityError(reason="voice_language_unavailable", impact="声音语言不匹配。", repair_action="选择匹配方案语言的声音。")
        return reference, [CreationPlanPronunciationReference(id=e.id, version=e.version, scope=e.scope,
            scope_reference=e.scope_reference, language=e.language) for e in entries]

    def voice_options(
        self,
        workspace_id: str,
        task: CreationTask,
        plan: CreationPlan,
    ) -> list[dict[str, object]]:
        options: list[dict[str, object]] = []
        for voice in self.read_voices(workspace_id):
            if not self.profile_readable(voice, task.owner_user_id):
                continue
            try:
                person = self.read_person(
                    workspace_id,
                    voice.person_profile_id,
                    voice.person_version,
                )
            except KeyError:
                person = None
            reason = None
            try:
                self._resolve_voice_selection(
                    workspace_id=workspace_id,
                    task=task,
                    plan=plan,
                    voice_profile_id=voice.id,
                    voice_version=voice.version,
                )
            except MediaAuthorityError as exc:
                reason = exc.reason
            options.append(
                {
                    "profile_id": voice.id,
                    "version": voice.version,
                    "person_display_name": (
                        person.display_name if person is not None else voice.display_name or "未命名声音"
                    ),
                    "language": voice.language,
                    "model_id": voice.model_id,
                    "runnable": reason is None,
                    "runnable_reason": reason,
                }
            )
        return options
