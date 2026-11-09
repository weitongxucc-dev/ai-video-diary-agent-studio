"""Complete task editing HTTP: previews, proposal controls, application and original receipts. Durable editing and execution owners remain unchanged."""
from dataclasses import asdict
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from app.creation.editing.metadata_use_case import EditPreviewRejected, LegacyMontageReference
from app.creation.editing.picture import EditConflict
from app.creation.cover_use_case import CoverUnavailable, CoverUnresolved
from app.montage import creation_task_montage_plan_from_payload
from typing import Literal
from uuid import UUID
from app.creation.contracts import CreationTask
from app.schemas import CreationTaskEditPreviewResponse, CreationTaskEditPreviewRequest, CreationTaskTimelineUndoRequest, CreationTaskEditConfirmResponse, CreationTaskEditQueuedResponse, CreationTaskEditJobReplayResponse, CreationTaskEditApplyRequest, CreationTaskExactEditRequest, CompoundEditSelectionRequest

def build_task_edit_router(*, audio_command_preview_for, audio_edit_rejected_for, cover_operation_for, clip_preview_command_for, clip_preview_conflict_for, clip_preview_unavailable_for, command_parse_rejected_for, compound_selection_command_for, creation_execution_actor_for, creation_task_edit_preview_request_for, exact_edit_source_for, edit_preview_rejected_for, goal_projection_rejected_for, http_exception_for, host_budget_uncalibrated_for, json_response_for, linked_edit_operations_for, picture_preview_rejected_for, task_media_required_for, timeline_version_conflict_for, assistant_conversation_for_request_for, assistant_member_id_for, bind_assistant_edit_instruction_for, claim_edit_request_for, command_preview_use_case_for, compound_edit_use_case_for, compound_proposal_command_for, confirm_cover_response_for, confirm_creation_task_edit_for, confirm_linked_timeline_response_for, content_reader_for, content_visible_for, creation_runtime_for, creation_task_or_404_for, current_narration_snapshot_for, edit_preview_response_for, ensure_montage_base_version_for, exact_edit_access_for, exact_preview_use_case_for, linked_timeline_apply_command_for, linked_timeline_use_case_for, logical_montage_output_refs_for, media_result_receipt_for, metadata_edit_command_for, metadata_edit_response_for, original_edit_result_for, read_edit_request_result_for, request_permit_actor_for, require_assistant_input_origin_for, require_permit_actor_for, save_edit_request_result_for, subtitle_edit_use_case_for, subtitle_proposal_command_for, topic_failure_response_for, wake_assistant_continuations_for, auth_context_for, conversation_repo_for, creation_task_montage_plan_from_payload_for, creation_task_montage_plan_to_payload_for, current_workspace_id_for, is_subtitle_edit_for, json_for, media_digest_for, production_repo_for, project_task_summary_for, re_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/assistant/edit-requests/{request_id}')
    def get_exact_edit_request_result(task_id: str, request_id: UUID, request: Request, phase: Literal['preview', 'apply', 'script', 'observation']='preview'):
        return _goal_request_result(task_id, request_id, request, phase)

    @router.post('/api/creation-tasks/{task_id}/timeline/initialize')
    def initialize_creation_task_timeline(task_id: str, request: Request) -> dict[str, str]:
        """Explicitly snapshot a legacy storyboard before the user starts editing it."""
        workspace_id = current_workspace_id_for()(request)
        task = creation_task_or_404_for()(workspace_id, task_id)
        if task.stage not in {'montage', 'review'}:
            raise http_exception_for()(status_code=409, detail='当前任务尚无可编辑的故事板')
        plan = creation_task_montage_plan_from_payload_for()(production_repo_for().get_creation_task_montage_plan(workspace_id, task_id))
        logical_montage_output_refs_for()(plan)
        if task.current_timeline_version_id is None:
            versions = production_repo_for().timeline_records.list_timeline_versions(workspace_id, task_id)
            if versions:
                current_snapshot = {'montage': creation_task_montage_plan_to_payload_for()(plan), **current_narration_snapshot_for()(workspace_id, task)}
                if any((versions[-1].snapshot.get(key) != value for key, value in current_snapshot.items())):
                    raise topic_failure_response_for()(status_code=409, error='timeline_version_conflict', reason='当前故事板与已有时间线版本内容不同，不能把旧版本当作当前版本。', impact='当前故事板和历史版本均已保留，没有写入或替换。', repair_action='请先核对当前故事板与版本历史，再联系支持修复版本关联。', retryable=False)
        version = ensure_montage_base_version_for()(workspace_id, task, plan)
        if task.current_timeline_version_id is None:
            version = production_repo_for().timeline_records.create_timeline_version(version)
        return {'version_id': version.id}

    @router.post('/api/creation-tasks/{task_id}/edits/preview', response_model=CreationTaskEditPreviewResponse)
    def preview_creation_task_edit(task_id: str, request: Request, payload: CreationTaskEditPreviewRequest):
        workspace_id = current_workspace_id_for()(request)
        task = creation_task_or_404_for()(workspace_id, task_id)
        payload = bind_assistant_edit_instruction_for()(request, task, payload)
        base_version_id = task.current_timeline_version_id
        try:
            return _preview_command_edit_request(task_id, request, payload, base_version_id=base_version_id)
        except task_media_required_for() as required:
            media_kind = required.kind
        actor = require_permit_actor_for()(request, workspace_id)
        task = creation_task_or_404_for()(workspace_id, task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise http_exception_for()(status_code=403, detail='当前成员不能安排此任务的媒体处理')
        values = {'_media_kind': media_kind, '_draft': request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'), 'payload': payload.model_dump(mode='json', exclude_unset=True)}
        try:
            runtime = creation_runtime_for()()
            media = runtime.registrations['creation_job'].owner.task_media
            if task.montage_plan_id and task.current_timeline_version_id is None:
                media.facts(workspace_id, task_id, 'command_edit_preview', values, actor)
                if media.verify_identity(actor) != 'ready':
                    raise PermissionError('媒体处理身份失效或无法复核')
                plan = creation_task_montage_plan_from_payload_for()(production_repo_for().get_creation_task_montage_plan(workspace_id, task_id))
                base = ensure_montage_base_version_for()(workspace_id, task, plan)
                if 'base_version_id' in values['payload']:
                    values['payload']['base_version_id'] = base.id
            job = media.submit(runtime, actor, task_id, 'command_edit_preview', values, 'edit_preview')
            receipt = media_result_receipt_for()(media, job, actor, runtime)
            receipt['result'] = None
            return json_response_for()(status_code=202, content=receipt)
        except PermissionError as exc:
            raise http_exception_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise http_exception_for()(status_code=422, detail=str(exc)) from exc

    def _preview_command_edit_request(task_id, request, payload, *, base_version_id=False):
        workspace_id = current_workspace_id_for()(request)
        task = creation_task_or_404_for()(workspace_id, task_id)
        if base_version_id is False:
            base_version_id = task.current_timeline_version_id
        payload = bind_assistant_edit_instruction_for()(request, task, payload)
        reader, diagnostic = content_reader_for()(request, workspace_id)
        identity = {'member_id': reader, 'local_diagnostic': diagnostic}
        request_source = exact_edit_source_for().get()
        if request_source is not None:
            identity = {'member_id': assistant_member_id_for()(request, workspace_id), 'local_diagnostic': False}
        case = command_preview_use_case_for()()
        draft = request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact')
        actor = request_permit_actor_for()(request, workspace_id) if not diagnostic else None
        options = {'identity': identity, 'reader_identity': {'member_id': reader, 'local_diagnostic': diagnostic}, 'request_source': request_source, 'draft_only': draft, 'execution': None, 'actor': actor}
        try:
            outcome = case.preview(task, payload, base_version_id=base_version_id,
                expected_input_id=request.headers.get('X-Assistant-Input-ID'), **options)
            outcome = case.resolve_action(task, outcome, **options)
            if isinstance(outcome, audio_command_preview_for()):
                actor = request_permit_actor_for()(request, workspace_id)
                outcome = case.preview_audio(task, outcome, actor, request_source=request_source, draft_only=draft, execution=None)
            if isinstance(outcome, clip_preview_command_for()):
                actor = require_permit_actor_for()(request, workspace_id)
                command = case.exact.prepare_clip_submission(actor, outcome, original_command=payload)
                if command.source is not None:
                    require_assistant_input_origin_for()(request, command.source)
                runtime = creation_runtime_for()()
                media = runtime.registrations['creation_job'].owner.task_media
                job = case.exact.submit_clip(runtime, media, actor, command, operation='command_edit_preview')
                receipt = media_result_receipt_for()(media, job, actor, runtime)
                receipt['result'] = None
                return json_response_for()(status_code=202, content=receipt)
            return edit_preview_response_for()(*outcome) if outcome is not None else None
        except LegacyMontageReference as exc:
            raise http_exception_for()(status_code=409, detail=exc.details) from exc
        except (edit_preview_rejected_for(), picture_preview_rejected_for(), audio_edit_rejected_for(), command_parse_rejected_for()) as exc:
            raise http_exception_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except (clip_preview_unavailable_for(), host_budget_uncalibrated_for()) as exc:
            raise http_exception_for()(status_code=503, detail=str(exc)) from exc
        except (EditConflict, clip_preview_conflict_for(), timeline_version_conflict_for()) as exc:
            raise http_exception_for()(status_code=409, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/edits/{proposal_id}', response_model=CreationTaskEditPreviewResponse)
    def get_creation_task_edit_preview(task_id: str, proposal_id: str, request: Request):
        workspace_id = current_workspace_id_for()(request)
        task = creation_task_or_404_for()(workspace_id, task_id)
        if not content_visible_for()(request, workspace_id, task.visibility, task.owner_user_id):
            raise http_exception_for()(status_code=404, detail='没有找到当前任务。')
        try:
            proposal = production_repo_for().edit_records.get_edit_proposal(workspace_id, proposal_id)
            intent = production_repo_for().edit_records.get_edit_intent(workspace_id, proposal.intent_id)
        except KeyError as exc:
            raise http_exception_for()(status_code=404, detail='没有找到所选修改方案。') from exc
        if proposal.task_id != task_id or proposal.status != 'proposed':
            raise http_exception_for()(status_code=409, detail='这份修改已处理，请查看当前任务版本。')
        if proposal.operation['payload'].get('base_version_id') != task.current_timeline_version_id:
            raise http_exception_for()(status_code=409, detail='任务版本已变化，旧修改不能直接应用，请重新生成方案。')
        return edit_preview_response_for()(intent, proposal)

    @router.get('/api/creation-tasks/{task_id}/edit-controls')
    def get_creation_task_edit_controls(task_id: str, request: Request):
        workspace_id = current_workspace_id_for()(request)
        task = creation_task_or_404_for()(workspace_id, task_id)
        if not content_visible_for()(request, workspace_id, task.visibility, task.owner_user_id):
            raise http_exception_for()(status_code=404, detail='没有找到当前任务。')
        summary = project_task_summary_for()(production_repo_for(), task).summary
        proposals = production_repo_for().edit_records.list_edit_proposals(workspace_id, task_id, base_version_id=task.current_timeline_version_id, limit=8)
        return {'task_id': task_id, 'timeline_version_id': summary['current_timeline_version_id'], 'timeline_sequence': summary['timeline_sequence'], 'can_undo': summary['can_undo'], 'can_redo': summary['can_redo'], 'proposals': [{'id': item.id, 'summary': item.summary, 'operation_type': item.operation['operation_type'], 'steps': item.operation['payload'].get('steps', [])} for item in proposals]}

    def _assistant_edit_control_instruction(task: CreationTask, request: Request, payload: CreationTaskTimelineUndoRequest) -> str:
        if payload.assistant_turn_id is None:
            raise http_exception_for()(status_code=409, detail='当前修改要求不可核验，未改变任务。')
        turn = conversation_repo_for().get_turn(str(payload.assistant_turn_id))
        if turn is None:
            raise http_exception_for()(status_code=404, detail='没有找到当前对话要求。')
        workspace_id, member_id, _chat = assistant_conversation_for_request_for()(request, turn['conversation_id'])
        try:
            origin = conversation_repo_for().task_turn_source(turn['id'], workspace_id, member_id, task.id)
        except ValueError as exc:
            raise http_exception_for()(status_code=409, detail=str(exc)) from exc
        context = origin['context']
        bound = bind_assistant_edit_instruction_for()(request, task, creation_task_edit_preview_request_for()(assistant_turn_id=payload.assistant_turn_id, raw_command=origin['raw_command'], context_stage=context.get('stage', 'montage'), selected_object_refs=context.get('selected_object_refs', []), playhead_ms=context.get('playhead_ms')))
        if payload.expected_timeline_version_id != task.current_timeline_version_id or payload.expected_timeline_sequence != context.get('target_snapshot', {}).get('timeline_sequence'):
            raise http_exception_for()(status_code=409, detail='修改所依据的任务版本已变化，未改变任务。')
        command = bound.raw_command
        if re_for().search('不要|不用|别|不(?:撤销|重做|取消|放弃)', command):
            raise http_exception_for()(status_code=409, detail='当前要求没有授权这一动作，任务保持不变。')
        prefix = '(?:请|麻烦|现在|先|帮我|请帮我|可以帮我|能不能帮我)?'
        proposal_scope = '(?:这(?:个|份|次)|当前|刚才的)?(?:修改)?(?:提案|方案|修改(?:建议)?)'
        allowed = re_for().fullmatch(prefix + f'(?:取消|放弃|丢弃)(?:一下)?{proposal_scope}(?:一下|吧|吗|好吗)?[。！!？?]?', command.strip())
        if not allowed:
            raise http_exception_for()(status_code=409, detail='需要明确的撤销、重做或取消修改方案要求；未改变任务。')
        return command

    @router.post('/api/creation-tasks/{task_id}/edits/{proposal_id}/reject')
    def reject_creation_task_edit_proposal(task_id: str, proposal_id: str, request: Request, payload: CreationTaskTimelineUndoRequest):
        workspace_id = current_workspace_id_for()(request)
        task = creation_task_or_404_for()(workspace_id, task_id)
        _assistant_edit_control_instruction(task, request, payload)
        try:
            proposal = production_repo_for().edit_records.get_edit_proposal(workspace_id, proposal_id)
            if production_repo_for().edit_records.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('request_source') is not None:
                _edit_proposal_binding(task_id, proposal_id, request)
            if proposal.task_id != task_id or proposal.operation['payload'].get('base_version_id') != task.current_timeline_version_id:
                raise http_exception_for()(status_code=409, detail='这份修改的任务或基础版本已变化。')
            if proposal.status != 'rejected':
                proposal = production_repo_for().edit_records.transition_edit_proposal(workspace_id, task_id, proposal_id, status='rejected')
        except KeyError as exc:
            raise http_exception_for()(status_code=404, detail='没有找到这份修改方案。') from exc
        except ValueError as exc:
            raise http_exception_for()(status_code=409, detail=str(exc)) from exc
        wake_assistant_continuations_for()()
        return {'task_id': task_id, 'proposal_id': proposal.id, 'status': proposal.status, 'timeline_version_id': task.current_timeline_version_id}

    def _edit_proposal_binding(task_id, proposal_id, request):
        task, member_id = exact_edit_access_for()(task_id, request, write=True)
        try:
            proposal = production_repo_for().edit_records.get_edit_proposal(task.workspace_id, proposal_id)
            intent = production_repo_for().edit_records.get_edit_intent(task.workspace_id, proposal.intent_id)
        except KeyError as exc:
            raise http_exception_for()(status_code=404, detail='编辑提案不存在') from exc
        if proposal.task_id != task_id:
            raise http_exception_for()(status_code=404, detail='编辑提案不存在')
        origin = intent.constraints.get('request_source')
        if origin is not None and origin['member_id'] != member_id:
            raise http_exception_for()(status_code=403, detail='提案不属于当前请求成员')
        digest = linked_timeline_use_case_for()().input_digest(task, proposal, origin) if proposal.operation.get('operation_type') in linked_edit_operations_for() else media_digest_for()([task.id, proposal.id, proposal.operation, proposal.target_refs, origin.get('source_facts') if origin else None])
        return (task, member_id, proposal, digest)

    @router.get('/api/creation-tasks/{task_id}/edits/{proposal_id}/result')
    def get_creation_task_edit_result(task_id: str, proposal_id: str, request: Request):
        task, _member_id, proposal, digest = _edit_proposal_binding(task_id, proposal_id, request)
        if proposal.operation.get('operation_type') in linked_edit_operations_for():
            try:
                saved = linked_timeline_use_case_for()().read_result(linked_timeline_apply_command_for()(task, proposal, request))
            except PermissionError as exc:
                raise http_exception_for()(status_code=403, detail=str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise http_exception_for()(status_code=409, detail=str(exc)) from exc
            return {'proposal_id': saved.proposal.id, 'base_version_id': saved.proposal.operation['payload'].get('base_version_id'), 'input_digest': saved.input_digest, 'status': saved.proposal.status, 'result': metadata_edit_response_for()(saved.outcome).model_dump(mode='json') if saved.outcome is not None else None}
        if is_subtitle_edit_for()(proposal.operation.get('operation_type'), proposal.operation.get('payload', {})):
            try:
                outcome = subtitle_edit_use_case_for()().read_result(subtitle_proposal_command_for()(task, proposal, request), proposal.id)
            except PermissionError as exc:
                raise http_exception_for()(status_code=403, detail=str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise http_exception_for()(status_code=409, detail=str(exc)) from exc
            return {'proposal_id': proposal.id, 'base_version_id': proposal.operation['payload'].get('base_version_id'), 'input_digest': digest, 'status': proposal.status, 'result': metadata_edit_response_for()(outcome).model_dump(mode='json') if outcome is not None else None}
        if proposal.operation.get('operation_type') == 'EditCompoundTimeline':
            try:
                outcome = compound_edit_use_case_for()().read_result(compound_proposal_command_for()(task, proposal, request), proposal.id)
            except PermissionError as exc:
                raise http_exception_for()(status_code=403, detail=str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise http_exception_for()(status_code=409, detail=str(exc)) from exc
            return {'proposal_id': proposal.id, 'base_version_id': proposal.operation['payload'].get('base_version_id'), 'input_digest': digest, 'status': proposal.status, 'result': metadata_edit_response_for()(outcome).model_dump(mode='json') if outcome is not None else None}
        result = original_edit_result_for()(task, proposal)
        return {'proposal_id': proposal.id, 'base_version_id': proposal.operation.get('payload', {}).get('base_version_id'), 'input_digest': digest, 'status': proposal.status, 'result': json_for().loads(result.body) if isinstance(result, json_response_for()) else result.model_dump(mode='json') if result else None}

    @router.post('/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm', response_model=CreationTaskEditConfirmResponse | CreationTaskEditQueuedResponse | CreationTaskEditJobReplayResponse)
    def confirm_creation_task_edit(task_id: str, proposal_id: str, request: Request, payload: CreationTaskEditApplyRequest | None=None):
        if payload is None or payload.request_id is None:
            try:
                proposal = production_repo_for().edit_records.get_edit_proposal(current_workspace_id_for()(request), proposal_id)
            except KeyError as exc:
                raise http_exception_for()(status_code=404, detail='creation task edit proposal not found') from exc
            intent = production_repo_for().edit_records.get_edit_intent(proposal.workspace_id, proposal.intent_id)
            if intent.constraints.get('request_source') is not None:
                _edit_proposal_binding(task_id, proposal_id, request)
            if proposal.operation.get('operation_type') in linked_edit_operations_for():
                task = creation_task_or_404_for()(current_workspace_id_for()(request), task_id)
                return confirm_linked_timeline_response_for()(task, proposal, request, payload, wake=True)
            result = confirm_creation_task_edit_for()(task_id, proposal_id, request)
            wake_assistant_continuations_for()()
            return result
        task, member_id, proposal, digest = _edit_proposal_binding(task_id, proposal_id, request)
        if proposal.operation.get('operation_type') in linked_edit_operations_for():
            return confirm_linked_timeline_response_for()(task, proposal, request, payload, wake=True)
        base = proposal.operation.get('payload', {}).get('base_version_id')
        if 'expected_base_version_id' in payload.model_fields_set and payload.expected_base_version_id != base or (payload.expected_input_digest is not None and payload.expected_input_digest != digest):
            raise http_exception_for()(status_code=409, detail='请求与原提案基础版本或输入摘要不一致')
        event_id, fresh = claim_edit_request_for()(task, member_id, payload.request_id, 'apply', {'proposal_id': proposal.id, 'base_version_id': base, 'input_digest': digest, 'request': payload.model_dump(mode='json', exclude_unset=True)})
        previous = read_edit_request_result_for()(task, event_id)
        if proposal.operation.get('operation_type') == cover_operation_for():
            result = confirm_cover_response_for()(task, proposal, request, request_id=payload.request_id)
            save_edit_request_result_for()(task, event_id, result)
            wake_assistant_continuations_for()()
            return result
        if is_subtitle_edit_for()(proposal.operation.get('operation_type'), proposal.operation.get('payload', {})):
            try:
                outcome = subtitle_edit_use_case_for()().read_result(subtitle_proposal_command_for()(task, proposal, request), proposal.id)
            except PermissionError as exc:
                raise http_exception_for()(status_code=403, detail=str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise http_exception_for()(status_code=409, detail=str(exc)) from exc
            previous = (previous if previous is not None else metadata_edit_response_for()(outcome)) if outcome is not None else None
        elif proposal.operation.get('operation_type') == 'EditCompoundTimeline':
            try:
                outcome = compound_edit_use_case_for()().read_result(compound_proposal_command_for()(task, proposal, request), proposal.id)
            except PermissionError as exc:
                raise http_exception_for()(status_code=403, detail=str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise http_exception_for()(status_code=409, detail=str(exc)) from exc
            previous = (previous if previous is not None else metadata_edit_response_for()(outcome)) if outcome is not None else None
        else:
            if previous is not None and 'job_id' not in json_for().loads(previous.body):
                return previous
            previous = original_edit_result_for()(task, proposal)
        if previous is not None:
            if read_edit_request_result_for()(task, event_id) is None:
                save_edit_request_result_for()(task, event_id, previous)
            return previous
        if not fresh:
            raise http_exception_for()(status_code=409, detail='原应用请求结果尚未确定，请查询原提案结果，不重复执行')
        result = confirm_creation_task_edit_for()(task_id, proposal_id, request)
        save_edit_request_result_for()(task, event_id, result)
        wake_assistant_continuations_for()()
        return result

    @router.post('/api/creation-tasks/{task_id}/edits/exact/preview', response_model=CreationTaskEditPreviewResponse)
    def preview_creation_task_exact_edit(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
        task, member_id = exact_edit_access_for()(task_id, request, write=True)
        actor = request_permit_actor_for()(request, task.workspace_id) if auth_context_for().enabled else creation_execution_actor_for()(task.workspace_id, member_id)
        try:
            result = exact_preview_use_case_for()().submit_preview(creation_runtime_for(), actor, task, payload, identity={'member_id': member_id, 'local_diagnostic': False}, project_media=media_result_receipt_for(), expected_input_id=request.headers.get('X-Assistant-Input-ID'), draft_only=request.query_params.get('draft') == 'true')
            return json_response_for()(status_code=result.status_code, content=result.value)
        except (edit_preview_rejected_for(), picture_preview_rejected_for(), audio_edit_rejected_for()) as exc:
            raise http_exception_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except PermissionError as exc:
            raise http_exception_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise http_exception_for()(status_code=409, detail=str(exc)) from exc

    @router.post('/api/creation-tasks/{task_id}/edits/exact', response_model=CreationTaskEditConfirmResponse)
    def apply_creation_task_exact_edit(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
        if payload.assistant_turn_id is not None and payload.request_id is None:
            raise http_exception_for()(status_code=422, detail='助手直接编辑需要原请求身份')
        task, member_id = exact_edit_access_for()(task_id, request, write=True)
        actor = request_permit_actor_for()(request, task.workspace_id) if auth_context_for().enabled else creation_execution_actor_for()(task.workspace_id, member_id)
        try:
            result = exact_preview_use_case_for()().apply(task, actor, payload, identity={'member_id': member_id, 'local_diagnostic': False}, expected_input_id=request.headers.get('X-Assistant-Input-ID'))
            if result.changed:
                wake_assistant_continuations_for()()
            return json_response_for()(status_code=result.status_code, content=result.value)
        except edit_preview_rejected_for() as exc:
            raise http_exception_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise http_exception_for()(status_code=409, detail=str(exc)) from exc

    @router.post('/api/creation-tasks/{task_id}/edits/{proposal_id}/selection/preview')
    def preview_compound_selection(task_id: str, proposal_id: str, request: Request, payload: CompoundEditSelectionRequest):
        task = creation_task_or_404_for()(current_workspace_id_for()(request), task_id)
        actor = metadata_edit_command_for()(task, request, 'EditCompoundTimeline', {'base_version_id': task.current_timeline_version_id}).actor_identity
        control = compound_selection_command_for()(task.workspace_id, task.id, proposal_id, actor, payload.included_step_indices, payload.raw_command)
        draft_only = request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact')
        try:
            outcome = compound_edit_use_case_for()().preview_selection(control, draft_only=draft_only, execution=None)
            return edit_preview_response_for()(*outcome) if outcome is not None else None
        except picture_preview_rejected_for() as exc:
            raise http_exception_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except task_media_required_for() as required:
            media_kind = required.kind
        workspace_id = current_workspace_id_for()(request)
        actor = require_permit_actor_for()(request, workspace_id)
        task = creation_task_or_404_for()(workspace_id, task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise http_exception_for()(status_code=403, detail='当前成员不能安排此任务的媒体处理')
        values = {'_media_kind': media_kind, '_draft': draft_only, 'proposal_id': proposal_id, 'payload': payload.model_dump(mode='json', exclude_unset=True)}
        try:
            runtime = creation_runtime_for()()
            media = runtime.registrations['creation_job'].owner.task_media
            if task.montage_plan_id and task.current_timeline_version_id is None:
                media.facts(workspace_id, task_id, 'compound_selection_preview', values, actor)
                if media.verify_identity(actor) != 'ready':
                    raise PermissionError('媒体处理身份失效或无法复核')
                montage = creation_task_montage_plan_from_payload_for()(production_repo_for().get_creation_task_montage_plan(workspace_id, task_id))
                ensure_montage_base_version_for()(workspace_id, task, montage)
            job = media.submit(runtime, actor, task_id, 'compound_selection_preview', values, 'edit_preview')
            receipt = media_result_receipt_for()(media, job, actor, runtime)
            receipt['result'] = None
            return json_response_for()(status_code=202, content=receipt)
        except PermissionError as exc:
            raise http_exception_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise http_exception_for()(status_code=422, detail=str(exc)) from exc

    def _goal_request_result(task_id, request_id, request, phase):
        task = creation_task_or_404_for()(current_workspace_id_for()(request), task_id)
        member_id = assistant_member_id_for()(request, task.workspace_id)
        try:
            return exact_preview_use_case_for()().read_request_receipt(task, member_id, str(request_id), phase)
        except (goal_projection_rejected_for(), edit_preview_rejected_for()) as exc:
            raise http_exception_for()(status_code=exc.status_code, detail=exc.detail) from exc
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router


class TaskEditReceiptHttpAdapter:
    def __init__(self, *, repository_for, exact_preview_for, montage_response, wake):
        self.repository_for = repository_for
        self.exact_preview_for = exact_preview_for
        self.montage_response = montage_response
        self.wake = wake

    def claim_edit_request(self, task,member_id,request_id,phase,inputs):
        try:
            return self.exact_preview_for().claim_request(task,member_id,request_id,phase,inputs)
        except EditPreviewRejected as exc:
            raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc

    def save_edit_request_result(self, task, event_id, result):
        status = result.status_code if isinstance(result, JSONResponse) else 200
        content = json.loads(result.body) if isinstance(result, JSONResponse) else result.model_dump(mode="json")
        self.exact_preview_for().record_request_result(task, event_id, status_code=status, response=content)
        self.wake()

    def read_edit_request_result(self, task, event_id):
        recorded = self.exact_preview_for().read_request_result(task, event_id)
        return JSONResponse(status_code=recorded["status_code"], content=recorded["response"]) if recorded is not None else None

    def original_edit_result(self, task, proposal):
        operations = [item for item in self.repository_for().edit_records.list_edit_operations(task.workspace_id, task.id) if item.proposal_id == proposal.id]
        if operations:
            operation = operations[0]
            version = self.repository_for().timeline_records.get_timeline_version(task.workspace_id, operation.result_version_id)
            montage = creation_task_montage_plan_from_payload(version.snapshot["montage"])
            return CreationTaskEditConfirmResponse(operation=asdict(operation), montage=self.montage_response(montage), narration=None)
        row = self.repository_for().edit_records.find_proposal_job_result(task.workspace_id, task.id, proposal.id)
        if row is None:
            return None
        result = CreationTaskEditJobReplayResponse(status=row["status"], job_id=row["job_id"],
            request_id=row["request_id"], failure_reason=row["failure_reason"])
        return JSONResponse(status_code=202 if row["status"] in {"queued", "running", "interrupted"} else 200, content=result.model_dump(mode="json"))


class CoverConfirmationHttpAdapter:
    def __init__(self, *, repository_for, require_automation_task_open, require_permit_actor,
                 cover_use_case_for, exact_edit_source_facts, creation_runtime_for,
                 montage_response, topic_failure_response):
        self.repository_for = repository_for
        self.require_automation_task_open = require_automation_task_open
        self.require_permit_actor = require_permit_actor
        self.cover_use_case_for = cover_use_case_for
        self.exact_edit_source_facts = exact_edit_source_facts
        self.creation_runtime_for = creation_runtime_for
        self.montage_response = montage_response
        self.topic_failure_response = topic_failure_response

    def cover_confirm_response(self, result):
        montage = creation_task_montage_plan_from_payload(result.version.snapshot["montage"])
        return CreationTaskEditConfirmResponse(operation=asdict(result.operation),
            montage=self.montage_response(montage), narration=None)

    def confirm_cover_response(self, task, proposal, request, *, request_id=None):
        self.require_automation_task_open(request, task.workspace_id, task.id)
        actor = self.require_permit_actor(request, task.workspace_id)
        try:
            owner = self.cover_use_case_for()
            previous = owner.original_result(actor, task.id, proposal.id)
            if previous is not None:
                return self.cover_confirm_response(previous)
            owner.require_budget()
            source = self.repository_for().edit_records.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get('request_source')
            if source is not None and source['source_facts'] != self.exact_edit_source_facts(task, CreationTaskExactEditRequest.model_validate(source['action']), request):
                raise ValueError('预览后的源文件或片段修订已变化，请重新预览')
            runtime = self.creation_runtime_for()
            registered = runtime.registrations["creation_job"].owner.cover
            return self.cover_confirm_response(registered.submit(runtime, actor, task.id, proposal.id,
                                                             request_id=request_id))
        except CoverUnavailable as exc:
            raise self.topic_failure_response(status_code=503, error='cover_budget_unavailable', reason=str(exc),
                impact='当前时间线和已有成片保持不变。', repair_action='先完成独立封面资源标定。', retryable=False) from exc
        except CoverUnresolved as exc:
            raise HTTPException(status_code=503, detail={'error': 'cover_generation_unknown',
                'reason': str(exc), 'job_id': exc.job.id, 'status': exc.job.status,
                'request_id': request_id, 'impact': '当前时间线保持不变，未伪造采用或释放。',
                'repair_action': '查询原封面请求与设备许可，续交原固定回执，不重复生成。', 'retryable': False}) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
