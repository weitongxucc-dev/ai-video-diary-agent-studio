"""Creation request HTTP input-stage adapters; workflow, records, adoption and continuation remain original business owners."""
from uuid import UUID
from fastapi import APIRouter, Query, Request
from app.schemas import CreationRequestResponse, CreationRequestCreateRequest, CreationRequestReviseRequest, CreationRequestWindowPreviewRequest, CreationRequestWindowReplanRequest, CreationRequestWindowConfirmRequest, CreationRequestItemsResponse

def build_creation_request_router(*, workflow_module_for, production_for, preflight_type_for, sources_for, catalog_for, lipsync_for, plan_request_type_for, preflight_input_type_for, workspace_for, reader_for, owned_request_for, reply_for, template_for, window_request_type_for, replace_for, http_error_for, conversations_for, conversation_for, goal_for, authorizations_for, safe_text_for, wake_for, response_for, items_type_for, item_for, route_names):
    router = APIRouter()

    def _creation_request_workflow():
        return workflow_module_for().CreationRequestWorkflowUseCase(records=production_for().creation_requests, adoption=production_for().creation_request_adoption, preflight=preflight_type_for()(production_for(), sources_for(), catalog_for(), lipsync_provider_for=lipsync_for()))

    def _creation_window_goals(goals):
        return tuple((workflow_module_for().DeliveryGoal(item.goal, item.name, item.requirements.model_dump(mode='json', exclude_unset=True) if item.requirements else {}, item.model_dump()) for item in goals))

    def _creation_window_input(payload):
        return workflow_module_for().WindowInput(payload.expected_revision, _creation_window_goals(payload.goals))

    def _creation_request_delivery(requirements):
        payload = plan_request_type_for().model_validate(requirements)
        values = preflight_input_type_for()(goal=payload.goal, name=payload.name, target_count=payload.target_count, language=payload.language, aspect_ratio=payload.aspect_ratio, duration_target_seconds=payload.duration_target_seconds, duration_mode=payload.duration_mode, duration_range_seconds=payload.duration_range_seconds, presenter_share_target=payload.presenter_share_target, lipsync_quality=payload.lipsync_quality, template_id=payload.template_id, template_version=payload.template_version, selected_knowledge=(payload.selected_knowledge.knowledge_item_id, payload.selected_knowledge.version) if payload.selected_knowledge is not None else None, selected_voice=(payload.selected_voice.profile_id, payload.selected_voice.version) if payload.selected_voice is not None else None, selected_person=(payload.selected_person.profile_id, payload.selected_person.version) if payload.selected_person is not None else None, provided_fields=frozenset(payload.model_fields_set))
        return workflow_module_for().Delivery(values, payload.model_dump(mode='json'))

    def _run_creation_request_workflow(request: Request, steps):
        """Answer the creation workflow's finite HTTP input stages at their original gates."""
        try:
            try:
                stage = next(steps)
            except StopIteration as result:
                return result.value
            while True:
                try:
                    if isinstance(stage, workflow_module_for().ReadReader):
                        workspace_id = workspace_for()(request)
                        owner, diagnostic = reader_for()(request, workspace_id)
                        answer = workflow_module_for().Reader(workspace_id, owner, diagnostic)
                    elif isinstance(stage, workflow_module_for().ReadWorkspace):
                        answer = workspace_for()(request)
                    elif isinstance(stage, workflow_module_for().ReadOwnedRequest):
                        answer = owned_request_for()(request, stage.request_id)
                    elif isinstance(stage, workflow_module_for().ReadInput):
                        answer = _creation_request_input(request, stage.turn_id)
                    elif isinstance(stage, workflow_module_for().ReadReply):
                        answer = reply_for()(request, stage.conversation_id, stage.turn_id, None)
                    elif isinstance(stage, workflow_module_for().ReadTemplate):
                        answer = template_for()().read_template(request, stage.workspace_id, stage.template_id, stage.version)
                    elif isinstance(stage, workflow_module_for().NormalizeDelivery):
                        answer = _creation_request_delivery(stage.requirements)
                    elif isinstance(stage, workflow_module_for().NormalizeWindow):
                        goals, durations = ([], [])
                        for goal in stage.goals:
                            requirements = dict(goal['requirements'])
                            durations.append({key: requirements.pop(key) for key in ('duration_mode', 'duration_range_seconds') if key in requirements})
                            goals.append({**goal, 'requirements': requirements})
                        window = _creation_window_input(window_request_type_for().model_validate({'expected_revision': stage.expected_revision, 'goals': goals}))
                        answer = replace_for()(window, goals=tuple((replace_for()(goal, overrides={**goal.overrides, **duration}) for goal, duration in zip(window.goals, durations))))
                    else:
                        raise TypeError(f'Unsupported creation-request stage: {type(stage).__name__}')
                except Exception as exc:
                    try:
                        stage = steps.throw(exc)
                    except StopIteration as result:
                        return result.value
                else:
                    try:
                        stage = steps.send(answer)
                    except StopIteration as result:
                        return result.value
        except workflow_module_for().CreationRequestWorkflowRejected as exc:
            failure = http_error_for()(status_code=exc.status_code, detail=exc.detail)
            if exc.__cause__ is not None:
                raise failure from exc.__cause__
            raise failure from None

    def _creation_request_input(request: Request, turn_id: str):
        turn = conversations_for().get_turn(turn_id)
        try:
            workflow_module_for().require_running_turn(turn)
            conversation_for()(request, turn['conversation_id'])
            return workflow_module_for().current_input(turn, request.headers.get('x-assistant-input-id'))
        except workflow_module_for().CreationRequestWorkflowRejected as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post('/api/assistant/creation-request/resume')
    def resume_assistant_creation_request(request: Request, assistant_turn_id: UUID, creation_request_id: str | None=Query(default=None, max_length=128)):
        turn, _message, active = _creation_request_input(request, str(assistant_turn_id))
        workspace_id, member_id, _conversation = conversation_for()(request, turn['conversation_id'])
        try:
            result = goal_for()().resume_creation_request(workspace_id, member_id, turn['id'], active['input_id'] if active else None, creation_request_id, authorizations_for()())
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
        for item in result['tasks']:
            if item.get('reason'):
                item['reason'] = safe_text_for()(item['reason'], workspace_id)
        if result['status'] == 'resumed':
            wake_for()()
        return result

    @router.post('/api/creation-requests', response_model=CreationRequestResponse, status_code=201)
    def create_creation_request(request: Request, payload: CreationRequestCreateRequest):
        command = workflow_module_for().CreateGoal(goal=payload.goal, request_key=str(payload.request_key), target_count=payload.target_count, duration_mode=payload.duration_mode, duration_target_seconds=payload.duration_target_seconds, material_scope=payload.material_scope, output_relation=payload.output_relation, assistant_turn_id=str(payload.assistant_turn_id) if payload.assistant_turn_id is not None else None, requirements=payload.model_dump(mode='json', exclude={'request_key', 'output_relation', 'material_scope', 'assistant_turn_id', 'intent'}), intent=payload.intent.model_dump(mode='json') if payload.intent is not None else None)
        return response_for()(_run_creation_request_workflow(request, _creation_request_workflow().create(command)))

    @router.get('/api/assistant/creation-request', response_model=CreationRequestResponse | None)
    def read_current_input_creation_request(request: Request, assistant_turn_id: UUID):
        turn, _message, source_input = _creation_request_input(request, str(assistant_turn_id))
        workspace_id = workspace_for()(request)
        owner, _diagnostic = reader_for()(request, workspace_id)
        if not owner:
            raise http_error_for()(status_code=403, detail='请先确认当前成员，再读取制作目标。')
        request_key = source_input['input_id'] if source_input else turn['id']
        saved = production_for().creation_requests.find_creation_request_by_key(workspace_id, request_key, viewer_user_id=owner)
        return response_for()(saved) if saved is not None else None

    @router.get('/api/creation-requests/{request_id}', response_model=CreationRequestResponse)
    def get_creation_request(request_id: str, request: Request):
        return response_for()(owned_request_for()(request, request_id))

    @router.patch('/api/creation-requests/{request_id}', response_model=CreationRequestResponse)
    def revise_creation_request(request_id: str, request: Request, payload: CreationRequestReviseRequest):
        command = workflow_module_for().ReviseGoal(payload.expected_revision, payload.output_relation, payload.material_scope, payload.requirements.model_dump(mode='json') if payload.requirements else None, request.query_params.get('assistant_turn_id'))
        return response_for()(_run_creation_request_workflow(request, _creation_request_workflow().revise(request_id, command)))

    @router.post('/api/creation-requests/{request_id}/windows', response_model=CreationRequestResponse)
    def preview_creation_request_window(request_id: str, request: Request, payload: CreationRequestWindowPreviewRequest):
        steps = _creation_request_workflow().prepare_window(request_id, _creation_window_input(payload), tool_turn_id=request.query_params.get('assistant_turn_id'))
        return response_for()(_run_creation_request_workflow(request, steps))

    @router.post('/api/creation-requests/{request_id}/windows/{window_id}/replan', response_model=CreationRequestResponse)
    def replan_creation_request_window(request_id: str, window_id: str, request: Request, payload: CreationRequestWindowReplanRequest):
        goals = _creation_window_goals(payload.goals) if payload.goals is not None else None
        steps = _creation_request_workflow().replan(request_id, window_id, payload.expected_revision, goals, tool_turn_id=request.query_params.get('assistant_turn_id'))
        return response_for()(_run_creation_request_workflow(request, steps))

    @router.post('/api/creation-requests/{request_id}/windows/{window_id}/confirm', response_model=CreationRequestResponse)
    def confirm_creation_request_window(request_id: str, window_id: str, request: Request, payload: CreationRequestWindowConfirmRequest):
        command = workflow_module_for().ConfirmWindow(payload.expected_revision, payload.input_fingerprint, payload.adoption_kind, str(payload.assistant_turn_id) if payload.assistant_turn_id is not None else None)
        steps = _creation_request_workflow().confirm(request_id, window_id, command)
        return response_for()(_run_creation_request_workflow(request, steps))

    @router.get('/api/creation-requests/{request_id}/items', response_model=CreationRequestItemsResponse)
    def list_creation_request_items(request_id: str, request: Request, after_ordinal: int=0, limit: int=20):
        owned_request_for()(request, request_id)
        if after_ordinal < 0 or not 1 <= limit <= 100:
            raise http_error_for()(status_code=422, detail='分页范围无效。')
        owner, diagnostic = reader_for()(request, workspace_for()(request))
        data = production_for().creation_requests.list_creation_request_items(workspace_for()(request), request_id, after_ordinal=after_ordinal, limit=limit, viewer_user_id=owner, local_diagnostic=diagnostic)
        return items_type_for().model_validate({**data, 'items': [item_for()(item) for item in data['items']]})
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
