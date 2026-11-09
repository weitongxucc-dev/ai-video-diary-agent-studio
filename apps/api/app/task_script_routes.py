"""Task script context, drafts, reply transfer and adoption HTTP; transactional owners and model validation remain unchanged."""
from fastapi import APIRouter, Request
from app.schemas import AssistantReplyScriptTransferRequest, CreationTaskScriptsResponse, CreationTaskScriptDraftRequest, CreationTaskScriptResponse, CreationTaskScriptConfirmRequest

def build_task_script_router(*, workspace_for, reader_for, context_for, draft_error_for, http_error_for, reply_for, current_inputs_for, production_for, uuid_for, section_type_for, scripts_response_for, adoption_for, draft_for, script_response_for, asdict_for, readable_for, task_for, plan_for, library_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/assistant/script-context')
    def get_assistant_script_context(task_id: str, request: Request) -> dict[str, object]:
        workspace_id = workspace_for()(request)
        reader_id, local_diagnostic = reader_for()(request, workspace_id)
        try:
            return context_for()().read_context(workspace_id, task_id, reader_id, local_diagnostic=local_diagnostic)
        except draft_error_for() as exc:
            raise http_error_for()(exc.status_code, exc.detail) from exc

    @router.post('/api/creation-tasks/{task_id}/assistant/reply-transfer', response_model=CreationTaskScriptsResponse)
    def transfer_assistant_reply_to_script(task_id: str, request: Request, payload: AssistantReplyScriptTransferRequest) -> CreationTaskScriptsResponse:
        workspace_id = workspace_for()(request)
        reference, reply_text, member_id = reply_for()(request, str(payload.conversation_id), str(payload.turn_id), task_id)
        try:
            task, plan, topic, _sources, input_references = context_for()().read_inputs(workspace_id, task_id)
        except draft_error_for() as exc:
            raise http_error_for()(exc.status_code, exc.detail) from exc
        reader_id, _local_diagnostic = current_inputs_for()(request, workspace_id, plan, input_references)
        if reader_id != member_id:
            raise http_error_for()(status_code=403, detail='对话成员身份已变化，请重新打开当前工作区。')
        scripts = production_for().script_records.list_creation_task_scripts(workspace_id, task_id)
        latest = scripts[0] if scripts else None
        script_id = latest.id if latest and payload.replace_existing_script_id == latest.id else f'script_{uuid_for()().hex[:12]}'
        run_id = f'script_run_{uuid_for()().hex[:12]}'
        title = latest.title if latest and script_id == latest.id else f'对话回复草稿 · {topic.title[:80]}'
        section = section_type_for()(id=f'reply_section_{payload.request_id.hex}', title=topic.title[:160] or '对话回复', content=reply_text, visual_prompt='', evidence_reference_ids=[])
        try:
            task, _run, _script = production_for().script_records.transfer_completed_assistant_reply_to_script(workspace_id, task_id, request_id=str(payload.request_id), reply_reference=reference, reply_text=reply_text, plan_conversion_request_id=str(payload.plan_conversion_request_id) if payload.plan_conversion_request_id else None, expected_plan_id=plan.id, expected_plan_updated_at=payload.expected_plan_updated_at, expected_topic_id=topic.id, expected_task_updated_at=payload.expected_task_updated_at, input_references=input_references, script_id=script_id, run_id=run_id, title=title, language=plan.language, sections=[section], replace_existing_script_id=payload.replace_existing_script_id, expected_script_updated_at=payload.expected_script_updated_at, verified_owner_user_id=reader_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='当前任务或方案不存在') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail={'error': 'assistant_reply_transfer_conflict', 'message': '当前任务或草稿已变化，回复没有写入文案。', 'repair_action': str(exc)}) from exc
        return scripts_response_for()(workspace_id, task)

    @router.post('/api/creation-tasks/{task_id}/assistant/script-draft', response_model=CreationTaskScriptResponse)
    def save_assistant_script_draft(task_id: str, request: Request, payload: CreationTaskScriptDraftRequest) -> CreationTaskScriptResponse:
        workspace_id = workspace_for()(request)
        origin = adoption_for()(task_id, request, str(payload.assistant_turn_id), draft_only=True) if payload.assistant_turn_id is not None else None
        case = draft_for()(workspace_id, validate_inputs=lambda ws, plan, refs: current_inputs_for()(request, ws, plan, refs))
        try:
            draft = case.save(workspace_id, task_id, payload, instruction_origin=origin, reread_origin=lambda draft_only: adoption_for()(task_id, request, str(payload.assistant_turn_id), draft_only=draft_only))
        except draft_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
        return script_response_for().model_validate(asdict_for()(draft))

    @router.get('/api/creation-tasks/{task_id}/scripts', response_model=CreationTaskScriptsResponse)
    def get_creation_task_scripts(task_id: str, request: Request) -> CreationTaskScriptsResponse:
        workspace_id = workspace_for()(request)
        return scripts_response_for()(workspace_id, readable_for()(request, task_id), include_body=False)

    @router.post('/api/creation-tasks/{task_id}/scripts/{script_id}/confirm', response_model=CreationTaskScriptsResponse)
    def confirm_creation_task_script(task_id: str, script_id: str, request: Request, payload: CreationTaskScriptConfirmRequest) -> CreationTaskScriptsResponse:
        workspace_id = workspace_for()(request)
        instruction_origin = None
        if payload.adoption_kind == 'delegated_system' and payload.assistant_turn_id is not None:
            instruction_origin = adoption_for()(task_id, request, str(payload.assistant_turn_id))
        task = task_for()(workspace_id, task_id)
        plan = plan_for()(workspace_id, task)
        try:
            current_script = production_for().script_records.get_creation_task_script(workspace_id, script_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation task script not found') from exc
        if current_script.task_id != task.id:
            raise http_error_for()(status_code=404, detail='creation task script not found')
        if current_script.reused_from_entry_id:
            try:
                library_for()(request).read_reusable_entry(workspace_id, current_script.reused_from_entry_id)
            except draft_error_for() as exc:
                raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
            except KeyError as exc:
                raise http_error_for()(status_code=404, detail='文案库条目不存在') from exc
        elif current_script.run_id:
            try:
                generation_run = production_for().script_records.get_script_generation_run(workspace_id, current_script.run_id)
            except KeyError as exc:
                raise http_error_for()(status_code=409, detail='文案来源记录已不可用，请重新读取任务。') from exc
            current_inputs_for()(request, workspace_id, plan, generation_run.input_references)
        try:
            task, adopted_script = draft_for()(workspace_id, validate_inputs=lambda ws, plan, refs: current_inputs_for()(request, ws, plan, refs), content_reader=lambda ws: reader_for()(request, ws)).confirm(workspace_id, task_id, script_id, payload, member_id=reader_for()(request, workspace_id)[0], origin=instruction_origin, reread_origin=lambda draft_only: adoption_for()(task_id, request, str(payload.assistant_turn_id), draft_only=False))
        except draft_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except (KeyError, ValueError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
        return scripts_response_for()(workspace_id, task)
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
