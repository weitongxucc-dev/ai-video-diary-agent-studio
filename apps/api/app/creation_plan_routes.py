"""Creation plan HTTP; preflight, confirmation, reply adoption and visibility retain their original owners."""
from fastapi import APIRouter, HTTPException, Request
from app.creation.plan_contracts import CreationPlan
from app.creation.plan_preflight_use_case import CreationPlanPreflightInput, CreationPlanPreflightRejected
from app.schemas import CreationPlanPreviewRequest, CreationPlanPreviewResponse, CreationPlanConfirmRequest, CreationPlanResponse, ContentVisibilityRequest

class CreationPlanPreflightHttpAdapter:
    def __init__(self, *, case_for, read_template, content_reader):
        self.case_for = case_for
        self.read_template = read_template
        self.content_reader = content_reader

    def preflight(
        self,
        request: Request,
        workspace_id: str,
        payload: CreationPlanPreviewRequest | CreationPlanConfirmRequest,
        *, automation_scope: dict[str, list[str]] | None = None, frozen_plan: dict | None = None,
    ) -> CreationPlan:
        values = CreationPlanPreflightInput(
            goal=payload.goal, name=payload.name, target_count=payload.target_count,
            language=payload.language, aspect_ratio=payload.aspect_ratio,
            duration_target_seconds=payload.duration_target_seconds,
            duration_mode=payload.duration_mode, duration_range_seconds=payload.duration_range_seconds,
            presenter_share_target=payload.presenter_share_target, lipsync_quality=payload.lipsync_quality,
            template_id=payload.template_id, template_version=payload.template_version,
            selected_knowledge=(payload.selected_knowledge.knowledge_item_id, payload.selected_knowledge.version) if payload.selected_knowledge is not None else None,
            selected_voice=(payload.selected_voice.profile_id, payload.selected_voice.version) if payload.selected_voice is not None else None,
            selected_person=(payload.selected_person.profile_id, payload.selected_person.version) if payload.selected_person is not None else None,
            provided_fields=frozenset(payload.model_fields_set),
        )
        case = self.case_for()
        try:
            case.check_template_selection(values)
            template = (self.read_template(request, workspace_id, values.template_id, values.template_version)
                        if values.template_id is not None else None)
            values = case.inherit_template(values, template)
            user_id, local_diagnostic = self.content_reader(request, workspace_id)
            return case.prepare(workspace_id, values, template=template,
                reader_user_id=user_id, local_diagnostic=local_diagnostic, automation_scope=automation_scope,
                **({"frozen_plan": frozen_plan} if frozen_plan is not None else {}))
        except CreationPlanPreflightRejected as exc:
            status = {"template_version_required": 422, "template_id_required": 422,
                "knowledge_outside_automation_scope": 409, "knowledge_version_unreadable": 404,
                "knowledge_automation_forbidden": 409, "knowledge_reference_forbidden": 409}[exc.code]
            failure = HTTPException(status_code=status, detail=exc.message)
            if exc.__cause__ is not None:
                raise failure from exc.__cause__
            raise failure from None


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
