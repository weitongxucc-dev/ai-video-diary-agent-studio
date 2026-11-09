"""Creation-task schema and media-URL response encoding at the presentation boundary."""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from app.creation.editing.metadata_use_case import effective_audio_timeline as metadata_effective_audio_timeline
from app.creation.projection import project_task_summary
from app.schemas import (
    CreationPlanPreviewResponse, CreationPlanResponse, CreationTaskNarrationResponse,
    CreationTaskPersonRenderResponse, CreationTaskMontagePlanResponse,
)

if TYPE_CHECKING:
    from app.audio_timeline import AudioTimeline
    from app.creation.contracts import CreationTask
    from app.creation.narration_artifacts import NarrationArtifactRecord
    from app.creation.person_renders import PersonRender
    from app.creation.plan_contracts import CreationPlan
    from app.montage import CreationTaskMontagePlan


class CreationTaskResponseAdapter:
    def __init__(self, *, repository_for, person_render_media_path):
        self.repository_for = repository_for
        self.person_render_media_path = person_render_media_path

    def creation_plan_preview_response(self, plan: CreationPlan) -> CreationPlanPreviewResponse:
        return CreationPlanPreviewResponse.model_validate(
            {
                "creation_intent": plan.creation_intent,
                "adoption": plan.adoption,
                "updated_at": plan.updated_at,
                "template_id": plan.template_id,
                "template_version": plan.template_version,
                "template_settings": plan.template_settings,
                "goal": plan.goal,
                "name": plan.name,
                "target_count": plan.target_count,
                "language": plan.language,
                "aspect_ratio": plan.aspect_ratio,
                "duration_target_seconds": plan.duration_target_seconds,
                "duration_mode": plan.duration_mode,
                "duration_range_seconds": plan.duration_range_seconds,
                "presenter_share_target": plan.presenter_share_target,
                "lipsync_quality": plan.lipsync_quality,
                "knowledge_references": [item.__dict__ for item in plan.knowledge_references],
                "source_references": [item.__dict__ for item in plan.source_references],
                "selected_person": asdict(plan.selected_person) if plan.selected_person else None,
                "selected_voice": (
                    plan.selected_voice.__dict__ if plan.selected_voice is not None else None
                ),
                "pronunciation_references": [
                    item.__dict__ for item in plan.pronunciation_references
                ],
                "approved_asset_count": plan.approved_asset_count,
                "approved_clip_count": plan.approved_clip_count,
                "material_rule_summary": plan.material_rule_summary,
                "material_matches": [asdict(item) for item in plan.material_matches],
                "model_snapshot": plan.model_snapshot,
                "confirmation_points": plan.confirmation_points,
                "blockers": [item.__dict__ for item in plan.blockers],
                "confirmable": not any(
                    blocker.blocks_confirmation for blocker in plan.blockers
                ),
                "preflight_fingerprint": plan.preflight_fingerprint,
            }
        )

    def creation_plan_response(
        self,
        plan: CreationPlan,
        task: CreationTask,
    ) -> CreationPlanResponse:
        payload = self.creation_plan_preview_response(plan).model_dump()
        payload.update(
            {
                "id": plan.id,
                "workspace_id": plan.workspace_id,
                # 回读列上的当前可见范围：写进去的和读出来的必须是同一个值。
                "visibility": plan.visibility,
                "status": plan.status,
                "creation_task": project_task_summary(self.repository_for(), task).summary,
                "created_at": plan.created_at,
                "confirmed_at": plan.confirmed_at,
                "updated_at": plan.updated_at,
            }
        )
        return CreationPlanResponse.model_validate(payload)

    def creation_task_narration_response(
        self,
        task: CreationTask,
        artifact: NarrationArtifactRecord,
    ) -> CreationTaskNarrationResponse:
        if artifact.task_id != task.id:
            raise ValueError("配音产物与当前创作任务不一致")
        segments = self.repository_for().narration_records.list_narration_segments(
            task.workspace_id, task.id, artifact.script_id
        )
        return CreationTaskNarrationResponse.model_validate(
            {
                "task": project_task_summary(self.repository_for(), task).summary,
                "script_id": artifact.script_id,
                "audio_reference": self.repository_for().narration_artifacts.narration_artifact_reference(artifact.cache_key),
                "duration_seconds": artifact.duration_seconds,
                "sample_rate": artifact.sample_rate,
                "model_id": artifact.model_id,
                "voice_profile_id": artifact.voice_profile_id,
                "source_kind": artifact.source_kind,
                "source_artifact_ref": artifact.source_artifact_ref,
                "source_sha256": artifact.source_sha256,
                "source_script_digest": artifact.source_script_digest,
                "language": artifact.language,
                "pronunciation_version": artifact.pronunciation_version,
                "segments": [
                    {
                        **asdict(segment),
                        "audio_url": (
                            f"/api/creation-tasks/{task.id}/narration/segments/{segment.id}/audio"
                            if segment.status == "ready" and segment.audio_reference
                            else None
                        ),
                    }
                    for segment in segments
                ],
            }
        )

    def effective_audio_timeline(self, plan: CreationTaskMontagePlan) -> AudioTimeline:
        return metadata_effective_audio_timeline(plan)

    def creation_task_montage_response(
        self,
        plan: CreationTaskMontagePlan,
    ) -> CreationTaskMontagePlanResponse:
        return CreationTaskMontagePlanResponse.model_validate(
            {
                "id": plan.id,
                "aspect_ratio": plan.aspect_ratio,
                "presenter_share_target": plan.presenter_share_target,
                "presenter_share_actual": round(
                    100 * sum(clip.timeline_end_seconds - clip.timeline_start_seconds
                              for clip in plan.clips if clip.person_render_id) / plan.duration_seconds, 1
                ) if plan.duration_seconds else 0,
                "task_id": plan.task_id,
                "script_id": plan.script_id,
                "script_title": plan.script_title,
                "status": plan.status,
                "cues": [asdict(cue) for cue in plan.cues],
                "clips": [
                    {
                        **asdict(clip),
                        "source_reference": clip.source_path,
                    }
                    for clip in plan.clips
                ],
                "duration_seconds": plan.duration_seconds,
                "low_confidence_clip_count": plan.low_confidence_clip_count,
                "narration_reference": plan.narration_audio_path,
                "graphic_overlays": plan.graphic_overlays,
                "audio_timeline": self.effective_audio_timeline(plan).model_dump(),
                "output_url": (
                    f"/api/creation-tasks/{plan.task_id}/output"
                    if plan.status == "rendered" and plan.output_path
                    else None
                ),
                "subtitle_url": (
                    f"/api/creation-tasks/{plan.task_id}/output/subtitles"
                    if plan.status == "rendered" and plan.subtitle_path
                    else None
                ),
                "message": plan.message,
                "created_at": plan.created_at,
                "updated_at": plan.updated_at,
            }
        )

    def person_render_response(self, render: PersonRender) -> CreationTaskPersonRenderResponse:
        payload = asdict(render)
        payload.pop("cache_key", None)
        payload.pop("output_path", None)
        payload["media_url"] = (
            f"/api/creation-tasks/{render.task_id}/person-renders/{render.id}/media"
            if self.person_render_media_path(render) is not None
            else None
        )
        return CreationTaskPersonRenderResponse.model_validate(payload)
