"""Task graphic source and visual-preview HTTP; actual media and persistence remain owner-owned."""
from fastapi import APIRouter, Request, UploadFile, File
from app.visual_effects import VisualEditRequest
from app.schemas import CreationTaskEditPreviewResponse

def build_task_visual_router(*, workspace_for, task_for, import_for, records_for, store_for, settings_for, http_error_type_for, access_for, sources_for, resolve_source_for, file_response_for, media_required_type_for, actor_for, runtime_for, plan_from_for, base_for, receipt_for, json_response_for, preview_case_for, needs_full_for, clip_submit_for, edit_response_for, picture_error_type_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/graphics/import')
    def import_creation_task_graphic(task_id: str, request: Request, image: UploadFile=File(...)):
        from PIL import Image
        workspace_id = workspace_for()(request)
        task_for()(workspace_id, task_id)
        try:
            return import_for()(records_for(), store_for(), settings_for().effective_temporary_root_path, workspace_id, task_id, image)
        except (ValueError, OSError, Image.DecompressionBombError) as exc:
            raise http_error_type_for()(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else '图片导入失败，请检查格式、尺寸和外接盘') from exc

    @router.get('/api/creation-tasks/{task_id}/graphics/sources')
    def get_creation_task_graphic_sources(task_id: str, request: Request):
        workspace_id = workspace_for()(request)
        access_for()(task_id, request)
        return sources_for()(records_for(), workspace_id, task_id, store_for())

    @router.get('/api/creation-tasks/{task_id}/graphics/source/{artifact_id}')
    def get_creation_task_graphic_source(task_id: str, artifact_id: str, request: Request):
        workspace_id = workspace_for()(request)
        access_for()(task_id, request)
        try:
            record = records_for().execution_records.get_artifact_record(workspace_id, artifact_id)
            if record.task_id != task_id or record.kind != 'graphic_source':
                raise KeyError(artifact_id)
            path = resolve_source_for()(records_for(), store_for(), workspace_id, task_id, record.logical_ref)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise http_error_type_for()(status_code=404, detail='当前任务图片不存在') from exc
        return file_response_for()(path, media_type='image/png')

    @router.get('/api/creation-tasks/{task_id}/visual/artifacts/{artifact_id}')
    def get_creation_task_visual_preview(task_id: str, artifact_id: str, request: Request):
        workspace_id = workspace_for()(request)
        task_for()(workspace_id, task_id)
        try:
            record = records_for().execution_records.get_artifact_record(workspace_id, artifact_id)
            if record.task_id != task_id or record.kind not in {'color_preview', 'speed_preview', 'canvas_preview', 'subtitle_style_preview', 'transition_preview', 'graphic_preview', 'compound_preview', 'silence_preview'}:
                raise KeyError(artifact_id)
            path = store_for().resolve_file(record.logical_ref)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise http_error_type_for()(status_code=404, detail='当前任务的画面预览不存在') from exc
        return file_response_for()(path, media_type='image/png' if path.suffix == '.png' else 'video/mp4')

    @router.post('/api/creation-tasks/{task_id}/visual/preview', response_model=CreationTaskEditPreviewResponse)
    def preview_creation_task_visual(task_id: str, request: Request, payload: VisualEditRequest):
        try:
            return _preview_visual_request(task_id, request, payload)
        except media_required_type_for() as required:
            media_kind = required.kind
        workspace_id = workspace_for()(request)
        actor = actor_for()(request, workspace_id)
        task = task_for()(workspace_id, task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise http_error_type_for()(status_code=403, detail='当前成员不能安排此任务的媒体处理')
        values = {'_media_kind': media_kind, '_draft': request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'), 'payload': payload.model_dump(mode='json', exclude_unset=True)}
        if values['payload'].get('base_version_id') is None:
            values['payload']['base_version_id'] = task.current_timeline_version_id
        try:
            runtime = runtime_for()()
            media = runtime.registrations['creation_job'].owner.task_media
            if task.montage_plan_id and task.current_timeline_version_id is None:
                media.facts(workspace_id, task_id, 'visual_edit_preview', values, actor)
                if media.verify_identity(actor) != 'ready':
                    raise PermissionError('媒体处理身份失效或无法复核')
                plan = plan_from_for()(records_for().get_creation_task_montage_plan(workspace_id, task_id))
                values['payload']['base_version_id'] = base_for()(workspace_id, task, plan).id
            job = media.submit(runtime, actor, task_id, 'visual_edit_preview', values, 'edit_preview')
            receipt = receipt_for()(media, job, actor, runtime)
            receipt['result'] = None
            return json_response_for()(status_code=202, content=receipt)
        except PermissionError as exc:
            raise http_error_type_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise http_error_type_for()(status_code=422, detail=str(exc)) from exc

    def _preview_visual_request(task_id, request, payload, *, execution=None, source=None):
        draft_only = request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact')
        workspace_id = workspace_for()(request)
        task = task_for()(workspace_id, task_id)
        case = preview_case_for()()
        try:
            prepared = case.prepare(task, payload)
            plan, patch, clips, changed_clips = prepared
            if not needs_full_for()(clips[0], changed_clips[clips[0].id]):
                return clip_submit_for()(task_id, request, base_version_id=payload.base_version_id, operation_type='AdjustVisualEffects', clip_ids=payload.clip_ids, values=patch, raw_command=payload.raw_command)
            return edit_response_for()(*case.preview_full(task, payload, prepared, draft_only=draft_only, source=source, execution=execution))
        except picture_error_type_for() as exc:
            raise http_error_type_for()(status_code=exc.status_code, detail=exc.detail) from exc
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
