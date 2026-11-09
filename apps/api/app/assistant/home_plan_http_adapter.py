"""Complete workspace planning HTTP host protocol; admission, planning state and persistence retain their original owners."""
from fastapi import Request
from app.schemas import WorkspaceAssistantPlanTurnRequest, WorkspaceAssistantPlanTurnResponse

class HomePlanTurnHTTPAdapter:

    def __init__(self, *, creation_plan_preview_response_type_for, creation_request_response_type_for, http_exception_type_for, material_assistant_result_response_type_for, workspace_assistant_plan_turn_response_type_for, assistant_creation_request_for_turn_for, assistant_knowledge_snapshot_for, assistant_profile_snapshot_for, assistant_stream_safe_text_for, assistant_text_without_credentials_for, content_reader_for, creation_request_or_404_for, creation_request_response_for, home_plan_turn_use_case_for, local_member_token_for, register_adopted_creation_goals_for, task_turn_http_adapter_for, workspace_settings_for_for, current_workspace_id_for, forget_turn_control_for, home_plan_for, run_plan_turn_for):
        """Store lookups only; construction does not read or execute host state."""
        self.creation_plan_preview_response_type_for = creation_plan_preview_response_type_for
        self.creation_request_response_type_for = creation_request_response_type_for
        self.http_exception_type_for = http_exception_type_for
        self.material_assistant_result_response_type_for = material_assistant_result_response_type_for
        self.workspace_assistant_plan_turn_response_type_for = workspace_assistant_plan_turn_response_type_for
        self.assistant_creation_request_for_turn_for = assistant_creation_request_for_turn_for
        self.assistant_knowledge_snapshot_for = assistant_knowledge_snapshot_for
        self.assistant_profile_snapshot_for = assistant_profile_snapshot_for
        self.assistant_stream_safe_text_for = assistant_stream_safe_text_for
        self.assistant_text_without_credentials_for = assistant_text_without_credentials_for
        self.content_reader_for = content_reader_for
        self.creation_request_or_404_for = creation_request_or_404_for
        self.creation_request_response_for = creation_request_response_for
        self.home_plan_turn_use_case_for = home_plan_turn_use_case_for
        self.local_member_token_for = local_member_token_for
        self.register_adopted_creation_goals_for = register_adopted_creation_goals_for
        self.task_turn_http_adapter_for = task_turn_http_adapter_for
        self.workspace_settings_for_for = workspace_settings_for_for
        self.current_workspace_id_for = current_workspace_id_for
        self.forget_turn_control_for = forget_turn_control_for
        self.home_plan_for = home_plan_for
        self.run_plan_turn_for = run_plan_turn_for

    def execute(self, request: Request, payload: WorkspaceAssistantPlanTurnRequest, on_event, *, material_selection: dict | None=None) -> WorkspaceAssistantPlanTurnResponse:
        workspace_id = self.current_workspace_id_for()(request)
        workspace_settings_for_turn = self.workspace_settings_for_for()(workspace_id)
        caller_user_id, local_diagnostic = self.content_reader_for()(request, workspace_id)
        profile_reference = self.assistant_profile_snapshot_for()(request, payload.selected_profile_reference.kind, payload.selected_profile_reference.profile_id, payload.selected_profile_reference.version) if payload.selected_profile_reference is not None else None
        knowledge_reference = self.assistant_knowledge_snapshot_for()(request, payload.selected_knowledge_reference.knowledge_item_id, payload.selected_knowledge_reference.version, payload.selected_knowledge_reference.locator) if payload.selected_knowledge_reference is not None else None
        turn_id, created, turn = self.task_turn_http_adapter_for()().begin(request, str(payload.conversation_id), 'home', 'home', payload.message.strip(), payload.client_request_id, payload.expected_draft_version, {'kind': 'home', 'profile_reference': payload.selected_profile_reference.model_dump() if payload.selected_profile_reference else None, 'knowledge_reference': payload.selected_knowledge_reference.model_dump() if payload.selected_knowledge_reference else None, 'target_snapshot': {'profile_reference': profile_reference, 'knowledge_reference': knowledge_reference, 'material_selection': material_selection}}, on_event, expected_reference_version=payload.expected_reference_version)
        if not created:
            if turn['status'] == 'failed':
                # A committed source task outlives a failed model response.
                # None limits this reconciliation to actual source adoptions.
                self.register_adopted_creation_goals_for()(request, turn_id, None)
            settings_for_replay = self.workspace_settings_for_for()(workspace_id)
            response = self.task_turn_http_adapter_for()().replay(turn_id, self.workspace_assistant_plan_turn_response_type_for(), provider=settings_for_replay.effective_llm_provider, model=settings_for_replay.effective_llm_model, draft_version=payload.expected_draft_version + 1)
            self.register_adopted_creation_goals_for()(request, turn_id, response.creation_request)
            return response
        case = self.home_plan_turn_use_case_for()()
        steps = case.execute(turn_id, payload.message, material_selection=material_selection)
        try:
            stage = next(steps)
            while True:
                try:
                    if isinstance(stage, self.home_plan_for().RunPlanning):
                        answer = self.run_plan_turn_for()(workspace_settings_for_turn, workspace_id=workspace_id, local_member_token=self.local_member_token_for()(request) or None, caller_user_id=caller_user_id if not local_diagnostic else None, conversation_id=str(payload.conversation_id), message=payload.message.strip(), authorization=request.headers.get('authorization'), cookie=request.headers.get('cookie'), on_event=on_event, turn_id=turn_id, selected_references=self.task_turn_http_adapter_for()().frozen_references(request, str(payload.conversation_id), turn_id), input_handler=self.task_turn_http_adapter_for()().input_delivery(request, str(payload.conversation_id), turn_id), profile_reference=profile_reference, knowledge_reference=knowledge_reference, keep_turn_control=True, material_selection=material_selection)
                    elif isinstance(stage, self.home_plan_for().ContinuePlanning):
                        answer = self.run_plan_turn_for()(workspace_settings_for_turn, workspace_id=workspace_id, local_member_token=self.local_member_token_for()(request) or None, caller_user_id=caller_user_id if not local_diagnostic else None, conversation_id=str(payload.conversation_id), message=payload.message.strip(), authorization=request.headers.get('authorization'), cookie=request.headers.get('cookie'), on_event=on_event, turn_id=turn_id, profile_reference=profile_reference, selected_references=self.task_turn_http_adapter_for()().frozen_references(request, str(payload.conversation_id), turn_id), input_handler=self.task_turn_http_adapter_for()().input_delivery(request, str(payload.conversation_id), turn_id), knowledge_reference=knowledge_reference, creation_request=self.creation_request_response_for()(stage.latest_request).model_dump(mode='json'), keep_turn_control=True, material_selection=material_selection)
                    elif isinstance(stage, self.home_plan_for().ReadOwnedRequest):
                        answer = self.creation_request_or_404_for()(request, stage.request_id)
                    elif isinstance(stage, self.home_plan_for().SafeFailureText):
                        answer = self.assistant_stream_safe_text_for()(stage.text, workspace_id)
                    elif isinstance(stage, self.home_plan_for().ForgetTurnControl):
                        answer = self.forget_turn_control_for()(turn_id)
                    elif isinstance(stage, self.home_plan_for().BuildStoppedResponse):
                        response = self.workspace_assistant_plan_turn_response_type_for()(reply=stage.assistant_text, provider=workspace_settings_for_turn.effective_llm_provider, model=workspace_settings_for_turn.effective_llm_model, turn_id=turn_id, turn_status='stopped', draft_version=payload.expected_draft_version + 1, creation_request=self.creation_request_response_type_for().model_validate(stage.saved_goal) if stage.saved_goal else None)
                        answer = response
                    elif isinstance(stage, self.home_plan_for().SerializeStoppedResponse):
                        answer = self.home_plan_for().RenderedResponse(stage.response, stage.response.model_dump(mode='json'))
                    elif isinstance(stage, self.home_plan_for().NormalizeMaterial):
                        material = self.material_assistant_result_response_type_for().model_validate(stage.value)
                        answer = self.home_plan_for().MaterialResult(material.scope, material.asset_ref, material.segment_ref, material)
                    elif isinstance(stage, self.home_plan_for().ReadFrozenSelections):
                        answer = self.task_turn_http_adapter_for()().frozen_references(request, str(payload.conversation_id), turn_id)
                    elif isinstance(stage, self.home_plan_for().RecoverSavedRequest):
                        answer = self.assistant_creation_request_for_turn_for()(request, stage.turn)
                    elif isinstance(stage, self.home_plan_for().BuildCompletedResponse):
                        creation_request = self.creation_request_response_type_for().model_validate(stage.request_result) if stage.request_result is not None else None
                        plan_preview = self.creation_plan_preview_response_type_for().model_validate(stage.preview) if stage.preview is not None and creation_request is None else None
                        self.forget_turn_control_for()(turn_id)
                        safe_reply = self.assistant_text_without_credentials_for()(stage.reply, workspace_id)
                        response = self.workspace_assistant_plan_turn_response_type_for()(reply=safe_reply, provider=workspace_settings_for_turn.effective_llm_provider, model=workspace_settings_for_turn.effective_llm_model, plan_preview=plan_preview, creation_request=creation_request, material_result=stage.material_result.response if stage.material_result is not None else None, turn_id=turn_id, draft_version=payload.expected_draft_version + 1)
                        answer = self.home_plan_for().RenderedResponse(response, response.model_dump(mode='json'))
                    else:
                        raise TypeError(f'Unsupported home planning stage: {type(stage).__name__}')
                except Exception as exc:
                # Return only this checkpoint error to its original business try block.
                    try:
                        stage = steps.throw(exc)
                    except StopIteration as completed:
                        result = completed.value
                        break
                else:
                    try:
                        stage = steps.send(answer)
                    except StopIteration as completed:
                        result = completed.value
                        break
        except self.home_plan_for().HomePlanTurnRejected as exc:
            self.register_adopted_creation_goals_for()(request, turn_id, None)
            failure = self.http_exception_type_for()(status_code=exc.status_code, detail=exc.detail)
            raise failure from exc.__cause__ or exc
        response = result.rendered.response
        if result.completed:
            self.register_adopted_creation_goals_for()(request, turn_id, response.creation_request)
        return response
