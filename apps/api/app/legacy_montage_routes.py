"""Read-only historical batches and montage plans; durable readers and shared projections stay host-owned."""
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from app.schemas import ProductionBatchesResponse, ProductionBatchResponse, MontagePlansResponse, MontagePlanResponse, MontageClipCandidateResponse

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
