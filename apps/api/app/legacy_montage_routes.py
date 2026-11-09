"""Read-only historical batches and montage plans; durable readers stay host-owned."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.materials.contracts import MediaAuthorityError
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from app.schemas import MontageClipResponse, NarrationCueResponse, ProductionBatchesResponse, ProductionBatchResponse, MontagePlansResponse, MontagePlanResponse, MontageClipCandidateResponse

if TYPE_CHECKING:
    from app.production import ProductionBatch


class LegacyMontageResponseAdapter:
    def __init__(self, *, repository_for, montage_repository_for, safe_montage_media_path, montage_narration_status):
        self.repository_for = repository_for
        self.montage_repository_for = montage_repository_for
        self.safe_montage_media_path = safe_montage_media_path
        self.montage_narration_status = montage_narration_status

    def montage_clip_response(self, workspace_id: str, clip) -> MontageClipResponse:
        response = MontageClipResponse.model_validate(clip, from_attributes=True)
        try:
            source_reference = self.repository_for().workspace_media_reference_for_asset(
                workspace_id,
                clip.asset_id,
            )
        except (KeyError, MediaAuthorityError):
            source_reference = None
        return response.model_copy(update={"source_path": source_reference})

    def montage_plan_response(self, plan) -> MontagePlanResponse:
        output_path = self.safe_montage_media_path(plan, "output_path")
        subtitle_path = self.safe_montage_media_path(plan, "subtitle_path")
        subtitle_status = (
            "ready"
            if subtitle_path is not None
            else "not_generated"
            if not plan.subtitle_path
            else "unavailable"
        )
        return MontagePlanResponse.model_validate(
            {
                "id": plan.id,
                "workspace_id": plan.workspace_id,
                "batch_id": plan.batch_id,
                "episode_id": plan.episode_id,
                "script_title": plan.script_title,
                "status": plan.status,
                "cues": [
                    NarrationCueResponse.model_validate(cue, from_attributes=True)
                    for cue in plan.cues
                ],
                "clips": [
                    self.montage_clip_response(plan.workspace_id, clip)
                    for clip in plan.clips
                ],
                "duration_seconds": plan.duration_seconds,
                "low_confidence_clip_count": plan.low_confidence_clip_count,
                "aspect_ratio": "9:16",
                "narration_status": self.montage_narration_status(
                    plan.workspace_id, plan.narration_audio_path
                ),
                "subtitle_status": subtitle_status,
                "output_url": (
                    f"/api/production/montage-plans/{plan.id}/output"
                    if output_path is not None
                    else None
                ),
                "subtitle_url": (
                    f"/api/production/montage-plans/{plan.id}/subtitle"
                    if subtitle_path is not None
                    else None
                ),
                "message": plan.message,
                "created_at": plan.created_at,
                "updated_at": plan.updated_at,
            }
        )

    def production_batch_response(self, batch: ProductionBatch) -> ProductionBatchResponse:
        items = []
        for item in batch.items:
            plan = None
            if item.plan_id:
                try:
                    plan = self.montage_repository_for().get(batch.workspace_id, item.plan_id)
                except KeyError:
                    pass
            output_path = self.safe_montage_media_path(plan, "output_path") if plan else None
            subtitle_path = self.safe_montage_media_path(plan, "subtitle_path") if plan else None
            items.append(
                {
                    "episode_id": item.episode_id,
                    "status": item.status,
                    "plan_id": item.plan_id,
                    "aspect_ratio": "9:16",
                    "narration_status": self.montage_narration_status(
                        batch.workspace_id,
                        plan.narration_audio_path if plan else None,
                    ),
                    "subtitle_status": (
                        "ready"
                        if subtitle_path is not None
                        else "not_generated"
                        if plan is None or not plan.subtitle_path
                        else "unavailable"
                    ),
                    "output_url": (
                        f"/api/production/montage-plans/{plan.id}/output"
                        if output_path is not None
                        else None
                    ),
                    "subtitle_url": (
                        f"/api/production/montage-plans/{plan.id}/subtitle"
                        if subtitle_path is not None
                        else None
                    ),
                    "message": item.message,
                    "attempts": item.attempts,
                    "updated_at": item.updated_at,
                }
            )
        return ProductionBatchResponse.model_validate(
            {
                "id": batch.id,
                "workspace_id": batch.workspace_id,
                "name": batch.name,
                "target_count": batch.target_count,
                "status": batch.status,
                "episode_ids": batch.episode_ids,
                "items": items,
                "blocker_codes": batch.blocker_codes,
                "message": batch.message,
                "rendered_count": batch.rendered_count,
                "waiting_review_count": batch.waiting_review_count,
                "failed_count": batch.failed_count,
                "started_at": batch.started_at,
                "finished_at": batch.finished_at,
                "created_at": batch.created_at,
                "updated_at": batch.updated_at,
            }
        )


def build_legacy_montage_router(*, production_for, workspace_for, batches_type_for, batch_response_for, http_error_for, montage_for, plans_type_for, plan_response_for, safe_path_for, file_response_for, candidate_type_for, reader_for, eligible_for, route_names):
    router = APIRouter()

    @router.get('/api/production/batches', response_model=ProductionBatchesResponse)
    def list_production_batches(request: Request) -> ProductionBatchesResponse:
        batches = production_for().list_batches(workspace_for()(request))
        return batches_type_for()(batches=[batch_response_for()(batch) for batch in batches], total_count=len(batches))

    @router.get('/api/production/batches/{batch_id}', response_model=ProductionBatchResponse)
    def get_production_batch(batch_id: str, request: Request) -> ProductionBatchResponse:
        try:
            batch = production_for().get_batch(workspace_for()(request), batch_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='production batch not found') from exc
        return batch_response_for()(batch)

    @router.get('/api/production/batches/{batch_id}/montage-plans', response_model=MontagePlansResponse)
    def list_production_montage_plans(batch_id: str, request: Request) -> MontagePlansResponse:
        workspace_id = workspace_for()(request)
        try:
            production_for().get_batch(workspace_id, batch_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='production batch not found') from exc
        plans = montage_for().list_for_batch(workspace_id, batch_id)
        return plans_type_for()(plans=[plan_response_for()(plan) for plan in plans], total_count=len(plans))

    @router.get('/api/production/montage-plans/{plan_id}', response_model=MontagePlanResponse)
    def get_production_montage_plan(plan_id: str, request: Request) -> MontagePlanResponse:
        try:
            plan = montage_for().get(workspace_for()(request), plan_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='montage plan not found') from exc
        return plan_response_for()(plan)

    def _stream_montage_plan_media(plan_id: str, request: Request, *, field: str, media_type: str) -> FileResponse:
        try:
            plan = montage_for().get(workspace_for()(request), plan_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='montage plan not found') from exc
        path = safe_path_for()(plan, field)
        if path is None:
            raise http_error_for()(status_code=404, detail='montage media not found')
        return file_response_for()(path, media_type=media_type, filename=path.name, content_disposition_type='inline')

    @router.get('/api/production/montage-plans/{plan_id}/output')
    def stream_production_montage_output(plan_id: str, request: Request) -> FileResponse:
        return _stream_montage_plan_media(plan_id, request, field='output_path', media_type='video/mp4')

    @router.get('/api/production/montage-plans/{plan_id}/subtitle')
    def stream_production_montage_subtitle(plan_id: str, request: Request) -> FileResponse:
        return _stream_montage_plan_media(plan_id, request, field='subtitle_path', media_type='application/x-subrip')

    @router.get('/api/production/montage-plans/{plan_id}/clips/{clip_id}/candidates', response_model=list[MontageClipCandidateResponse])
    def list_production_montage_clip_candidates(plan_id: str, clip_id: str, request: Request) -> list[MontageClipCandidateResponse]:
        workspace_id = workspace_for()(request)
        try:
            plan = montage_for().get(workspace_id, plan_id)
            clip = next((item for item in plan.clips if item.id == clip_id))
        except (KeyError, StopIteration) as exc:
            raise http_error_for()(status_code=404, detail='montage clip not found') from exc
        cue = next((item for item in plan.cues if item.id == clip.cue_id), None)
        cue_tokens = set((cue.text if cue else '').lower())
        candidates: list[candidate_type_for()] = []
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        for asset in production_for().list_assets(workspace_id, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic):
            for segment in asset.segments:
                if not eligible_for()(asset, segment, workspace_id):
                    continue
                available = segment.end_seconds - segment.start_seconds
                slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
                if available + 1e-06 < slot_duration:
                    continue
                description = (segment.description or segment.transcript or asset.file_name).strip()
                overlap = len(cue_tokens & set(description.lower())) / max(1, len(cue_tokens))
                score = max(0.0, min(1.0, overlap * 0.7 + min(1.0, available / max(slot_duration, 0.1)) * 0.3))
                candidates.append(candidate_type_for()(asset_id=asset.id, segment_id=segment.id, file_name=asset.file_name, start_seconds=segment.start_seconds, end_seconds=segment.end_seconds, description=description, score=round(score, 3), reasons=['已通过版权和品牌审核', '时长满足当前旁白时间槽']))
        candidates.sort(key=lambda item: (item.asset_id == clip.asset_id and item.segment_id == clip.segment_id, item.score), reverse=True)
        return candidates[:24]
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
