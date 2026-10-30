"""Creation plan HTTP; preflight, confirmation, reply adoption and visibility retain their original owners."""
from fastapi import APIRouter, Request
from app.schemas import CreationPlanPreviewRequest, CreationPlanPreviewResponse, CreationPlanConfirmRequest, CreationPlanResponse, ContentVisibilityRequest

def build_creation_plan_router(*, preview_response_for, preflight_for, workspace_for, http_error_for, reply_for, reader_for, now_for, uuid_for, task_type_for, production_for, plan_response_for, write_guard_for):
    router = APIRouter()

    @router.post('/api/creation-plans/preview', response_model=CreationPlanPreviewResponse)
    def preview_creation_plan(request: Request, payload: CreationPlanPreviewRequest) -> CreationPlanPreviewResponse:
        return preview_response_for()(preflight_for()(request, workspace_for()(request), payload))

    @router.post('/api/creation-plans/confirm', response_model=CreationPlanResponse, status_code=201)
    def confirm_creation_plan(request: Request, payload: CreationPlanConfirmRequest) -> CreationPlanResponse:
        if payload.target_count != 1:
            raise http_error_for()(status_code=409, detail='多条视频需要分别准备方案，请从新建视频的总目标入口分组创建；没有创建单条任务冒充多条。')
        workspace_id = workspace_for()(request)
        reply_reference = None
        reply_request_id = None
        reply_owner_id = None
        local_diagnostic = False
        if payload.reply_origin is not None:
            origin = payload.reply_origin
            reply_reference, _reply_text, reply_owner_id = reply_for()(request, str(origin.conversation_id), str(origin.turn_id), None)
            request_owner_id, local_diagnostic = reader_for()(request, workspace_id)
            if not request_owner_id or request_owner_id != reply_owner_id:
                raise http_error_for()(status_code=403, detail='对话成员身份已变化，请重新打开当前工作区。')
            reply_request_id = str(origin.request_id)
        plan = preflight_for()(request, workspace_id, payload)
        if payload.preflight_fingerprint != plan.preflight_fingerprint:
            raise http_error_for()(status_code=409, detail={'error': 'creation_plan_preflight_stale', 'impact': '预检期间的资料、授权、素材或模型状态已变化，未创建任务。', 'repair_action': '重新执行内容预检并确认最新方案。', 'retryable': True})
        blocking = next((item for item in plan.blockers if item.blocks_confirmation), None)
        if blocking is not None:
            raise http_error_for()(status_code=409, detail={'error': blocking.reason, 'impact': blocking.impact, 'repair_action': blocking.repair_action, 'retryable': True})
        now = now_for()()
        plan.id = f'plan_{uuid_for()().hex[:12]}'
        plan.created_at = now
        plan.confirmed_at = now
        task = task_type_for()(id=f'task_{uuid_for()().hex[:12]}', workspace_id=workspace_id, plan_id=plan.id, name=plan.name, scenario=plan.goal, created_at=now, updated_at=now)
        try:
            saved_plan, saved_task = production_for().plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=reply_owner_id or reader_for()(request, workspace_id)[0], local_diagnostic=local_diagnostic if reply_reference is not None else reader_for()(request, workspace_id)[1], reply_reference=reply_reference, request_id=reply_request_id)
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail={'error': 'creation_plan_confirmation_rejected', 'impact': '没有创建部分方案或部分创作任务。', 'repair_action': str(exc), 'retryable': True}) from exc
        return plan_response_for()(saved_plan, saved_task)

    @router.put('/api/creation-plans/{plan_id}/visibility', response_model=CreationPlanResponse)
    def set_creation_plan_visibility_route(plan_id: str, request: Request, payload: ContentVisibilityRequest) -> CreationPlanResponse:
        """显式共享或收回一个创作方案；响应回读同一列。"""
        denied = write_guard_for()(request)
        if denied is not None:
            return denied
        workspace_id = workspace_for()(request)
        try:
            production_for().set_creation_plan_visibility(workspace_id, plan_id, payload.visibility)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation plan not found') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        plan = production_for().plan_records.get_creation_plan(workspace_id, plan_id)
        if plan.creation_task_id is None:
            raise http_error_for()(status_code=404, detail='creation plan not found')
        task = production_for().task_records.get_creation_task(workspace_id, plan.creation_task_id)
        return plan_response_for()(plan, task)

    @router.get('/api/creation-plans/{plan_id}', response_model=CreationPlanResponse)
    def get_creation_plan(plan_id: str, request: Request) -> CreationPlanResponse:
        workspace_id = workspace_for()(request)
        try:
            plan = production_for().plan_records.get_creation_plan(workspace_id, plan_id)
            if plan.creation_task_id is None:
                raise KeyError(plan_id)
            task = production_for().task_records.get_creation_task(workspace_id, plan.creation_task_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation plan not found') from exc
        return plan_response_for()(plan, task)
    return router
