"""Legacy task montage HTTP controls; candidates, edit transactions and rendering keep their original owners."""
from fastapi import APIRouter, Request
from app.schemas import MontageClipCandidateResponse, CreationTaskMontagePlanResponse, CreationTaskMontageClipUpdateRequest, CreationTaskSubtitleCueUpdateRequest, CreationTaskMontageRenderRequest

def build_task_montage_control_router(*, workspace_for, task_for, plan_from_for, production_for, http_error_for, candidates_for, reader_for, save_for, edit_request_for, fields_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/montage/clips/{clip_id}/candidates', response_model=list[MontageClipCandidateResponse])
    def get_creation_task_montage_clip_candidates(task_id: str, clip_id: str, request: Request, for_automation: bool=False) -> list[MontageClipCandidateResponse]:
        workspace_id = workspace_for()(request)
        task_for()(workspace_id, task_id)
        try:
            plan = plan_from_for()(production_for().get_creation_task_montage_plan(workspace_id, task_id))
            clip = next((item for item in plan.clips if item.id == clip_id))
        except (KeyError, StopIteration) as exc:
            raise http_error_for()(status_code=404, detail='creation task montage clip not found') from exc
        return candidates_for()(workspace_id, plan, clip, automation_only=for_automation, viewer_user_id=reader_for()(request, workspace_id)[0], local_diagnostic=reader_for()(request, workspace_id)[1])

    @router.patch('/api/creation-tasks/{task_id}/montage/clips/{clip_id}', response_model=CreationTaskMontagePlanResponse)
    def update_creation_task_montage_clip(task_id: str, clip_id: str, request: Request, payload: CreationTaskMontageClipUpdateRequest) -> CreationTaskMontagePlanResponse:
        if any((value is not None for value in (payload.fit_mode, payload.crop_x, payload.crop_y, payload.split_time_seconds, payload.playback_rate))):
            raise http_error_for()(status_code=409, detail='请通过精确编辑预览确认修改。')
        categories = [name for name, active in (('ReplaceClip', payload.asset_id is not None or payload.segment_id is not None), ('TrimClip', payload.source_start_seconds is not None and payload.asset_id is None and (payload.segment_id is None)), ('ReorderClip', payload.position is not None), ('SetClipLock', payload.locked is not None), ('ConfirmClip', payload.confirmed is True)) if active]
        if len(categories) != 1:
            raise http_error_for()(status_code=409, detail='legacy montage PATCH accepts one typed edit at a time')
        result = save_for()(task_id, request, edit_request_for()(operation_type=categories[0], clip_id=clip_id, **{key: value for key, value in payload.model_dump(exclude_unset=True).items() if key in fields_for()[categories[0]]}))
        return result.montage

    @router.patch('/api/creation-tasks/{task_id}/montage/cues/{cue_id}', response_model=CreationTaskMontagePlanResponse)
    def update_creation_task_subtitle_cue(task_id: str, cue_id: str, request: Request, payload: CreationTaskSubtitleCueUpdateRequest) -> CreationTaskMontagePlanResponse:
        result = save_for()(task_id, request, edit_request_for()(operation_type='UpdateSubtitleCue', cue_id=cue_id, text=payload.text))
        return result.montage

    @router.post('/api/creation-tasks/{task_id}/montage/render', response_model=CreationTaskMontagePlanResponse)
    def render_creation_task_montage(task_id: str, request: Request, _payload: CreationTaskMontageRenderRequest) -> CreationTaskMontagePlanResponse:
        raise http_error_for()(status_code=409, detail='请使用开始/继续制作，成片必须进入设备队列')
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
