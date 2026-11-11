"""Owned conversation management HTTP routes and current read projections.

Conversation/Admission owners retain persistence, CAS and frozen input rules.
The host supplies finite, lazy identity/source readers, not an application bag.
"""
from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from app.assistant.inputs import AssistantInputConflict
from app.conversation_repository import (
    ConversationDraftVersionConflict, ConversationReferenceVersionConflict,
    ConversationTurnRequestConflict,
)
from app.schemas import (
    AssistantConversationCreateRequest,
    AssistantConversationCurrentRequest,
    AssistantConversationDraftRestoreRequest,
    AssistantConversationDraftRestoreResponse,
    AssistantConversationInputRequest,
    AssistantConversationListResponse,
    AssistantConversationReferenceRequest,
    AssistantConversationReferenceResponse,
    AssistantConversationRequestResponse,
    AssistantConversationResponse,
    AssistantConversationUpdateRequest,
    CreationPlanPreviewRequest,
    CreationPlanPreviewResponse,
)


class ConversationHTTPAdapter:
    def __init__(self, *,
                 conversations_for,
                 workspace_for,
                 member_for,
                 validate_scope,
                 owned_conversation,
                 clean_text,
                 validate_reference,
                 recover_request,
                 require_actor,
                 segment_for,
                 restore_for,
                 input_context,
                 preflight,
                 render_plan,
                 ):
        self._conversations_for = conversations_for
        self._workspace_for = workspace_for
        self._member_for = member_for
        self._validate_scope = validate_scope
        self._owned_conversation = owned_conversation
        self._clean_text = clean_text
        self._validate_reference = validate_reference
        self._recover_request = recover_request
        self._require_actor = require_actor
        self._segment_for = segment_for
        self._restore_for = restore_for
        self._input_context = input_context
        self._preflight = preflight
        self._render_plan = render_plan

    def history(self, row: dict, request: Request) -> dict:
        turns = self._conversations_for().list_turns(row['id'])
        for index, turn in enumerate(turns):
            if (turn.get('result') or {}).get('kind') == 'task_turn':
                context = self._conversations_for().consumed_turn_request_context(turn['id'], row['id'], row['workspace_id'], row['member_id'])
                if context.get('kind') == 'task' and context.get('task_id') and (row['scope_kind'] != 'task' or context['task_id'] == row['scope_id']):
                    from app.assistant.inputs import latest_consumed_input
                    from app.assistant.result_projection import refresh_original_receipts
                    consumed = latest_consumed_input(turn['events'])
                    input_id = consumed['input_id'] if consumed else context.get('source_input_id')
                    task_id, ws = (context['task_id'], row['workspace_id'])

                    def read_actor():
                        try:
                            return self._require_actor(request, ws)
                        except HTTPException as exc:
                            raise PermissionError('当前原请求读取身份不可核实') from exc
                    turns[index] = refresh_original_receipts(turn, task_id=task_id, input_id=input_id, read_segment=lambda request_id: self._segment_for().read_request(ws, task_id, request_id, read_actor()), read_restore=lambda request_id: self._restore_for().read_request(ws, task_id, request_id, read_actor()))
            if turn['status'] != 'completed' or (turn.get('result') or {}).get('kind') != 'conversation':
                continue
            saved = self._recover_request(request, turn)
            if saved is not None:
                turn['result'] = {**turn['result'], 'kind': 'creation_request', 'creation_request_id': saved['id']}
        return {**row, 'turns': turns}

    def current(self, request: Request, payload: AssistantConversationCurrentRequest):
        workspace_id = self._workspace_for(request)
        member_id = self._member_for(request, workspace_id)
        self._validate_scope(request, payload.scope_kind, payload.scope_id)
        row = self._conversations_for().current_or_create(workspace_id, member_id, payload.scope_kind, payload.scope_id)
        return AssistantConversationResponse.model_validate(self.history(row, request))

    def create(self, request: Request, payload: AssistantConversationCreateRequest):
        workspace_id = self._workspace_for(request)
        member_id = self._member_for(request, workspace_id)
        if self._clean_text(payload.title, workspace_id) != payload.title:
            raise HTTPException(status_code=422, detail='会话名称不能包含密钥或口令。')
        self._validate_scope(request, payload.scope_kind, payload.scope_id)
        row = self._conversations_for().create(workspace_id, member_id, payload.scope_kind, payload.scope_id, payload.title)
        return AssistantConversationResponse.model_validate(self.history(row, request))

    def list_conversations(self, request: Request, scope_kind: str, scope_id: str):
        workspace_id = self._workspace_for(request)
        member_id = self._member_for(request, workspace_id)
        self._validate_scope(request, scope_kind, scope_id)
        rows = self._conversations_for().list_for_scope(workspace_id, member_id, scope_kind, scope_id)
        if scope_kind == 'material' and scope_id == 'library':
            visible_rows = []
            for row in rows:
                try:
                    self._validate_scope(request, row['scope_kind'], row['scope_id'])
                except HTTPException as exc:
                    if exc.status_code not in {404, 422}:
                        raise
                    continue
                visible_rows.append(row)
            rows = visible_rows
        return AssistantConversationListResponse.model_validate({'items': [self.history(row, request) for row in rows]})

    def get_conversation(self, conversation_id: str, request: Request):
        _workspace_id, _member_id, row = self._owned_conversation(request, conversation_id)
        return AssistantConversationResponse.model_validate(self.history(row, request))

    def get_request(self, conversation_id: str, client_request_id: UUID, request: Request):
        workspace_id, member_id, _row = self._owned_conversation(request, conversation_id)
        receipt = self._conversations_for().get_request_receipt(str(client_request_id), conversation_id, workspace_id, member_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail='当前对话中没有这次请求的保存回执。')
        return AssistantConversationRequestResponse.model_validate(receipt)

    def resume(self, conversation_id: str, request: Request):
        workspace_id, member_id, row = self._owned_conversation(request, conversation_id)
        selected = self._conversations_for().select_current(conversation_id, workspace_id, member_id)
        assert selected is not None
        return AssistantConversationResponse.model_validate(self.history(selected, request))

    def plan_preview(self, conversation_id: str, turn_id: UUID, request: Request, payload: CreationPlanPreviewRequest):
        workspace_id, _member_id, conversation = self._owned_conversation(request, conversation_id)
        turn = self._conversations_for().get_turn(str(turn_id))
        if turn is None or turn['conversation_id'] != conversation_id or turn['status'] != 'completed' or (not isinstance(turn['result'], dict)) or (turn['result'].get('kind') != 'plan_preview'):
            raise HTTPException(status_code=409, detail='所选对话轮次没有可更新的制作方案预览。')
        plan = self._preflight(request, workspace_id, payload)
        response = self._render_plan(plan)
        if not self._conversations_for().update_completed_plan_preview(str(turn_id), response.model_dump(mode='json')):
            raise HTTPException(status_code=409, detail='方案轮次已变化，请重新读取这段对话后再调整。')
        return response

    def reference_response(self, request: Request, row: dict) -> dict:
        reference = row['selected_reference']
        reason = None
        try:
            self._validate_reference(request, reference)
        except HTTPException as exc:
            if exc.status_code not in (404, 409):
                raise
            reason = str(exc.detail)
        return {'selected_reference': reference, 'reference_version': row['reference_version'], 'status': 'unavailable' if reason else 'ready', 'reason': reason}

    def get_reference(self, conversation_id: str, request: Request):
        _workspace_id, _member_id, row = self._owned_conversation(request, conversation_id)
        return self.reference_response(request, row)

    def save_reference(self, conversation_id: str, request: Request, payload: AssistantConversationReferenceRequest):
        workspace_id, member_id, _row = self._owned_conversation(request, conversation_id)
        reference = self._validate_reference(request, payload.selected_reference.model_dump(mode='json', exclude_none=True) if payload.selected_reference else None)
        try:
            saved = self._conversations_for().save_reference(conversation_id, workspace_id, member_id, reference, payload.expected_reference_version)
        except ConversationReferenceVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return self.reference_response(request, saved)

    def update(self, conversation_id: str, request: Request, payload: AssistantConversationUpdateRequest):
        workspace_id, member_id, _row = self._owned_conversation(request, conversation_id)
        if payload.title is not None and self._clean_text(payload.title, workspace_id) != payload.title:
            raise HTTPException(status_code=422, detail='会话名称不能包含密钥或口令。')
        if payload.draft_text is not None and self._clean_text(payload.draft_text, workspace_id) != payload.draft_text:
            raise HTTPException(status_code=422, detail='对话草稿不能包含密钥或口令。')
        if payload.draft_text is not None:
            try:
                updated = self._conversations_for().save_draft(conversation_id, workspace_id, member_id, payload.draft_text, payload.expected_draft_version)
            except ConversationDraftVersionConflict as exc:
                raise HTTPException(status_code=409, detail={'error': 'assistant_draft_conflict', 'message': '草稿已在其他位置更新；当前较新的草稿已保留，请重新读取后继续。', 'current_draft_version': exc.current['draft_version']}) from exc
        else:
            updated = self._conversations_for().get(conversation_id, workspace_id, member_id)
        if updated is not None and payload.title is not None:
            updated = self._conversations_for().update_title(conversation_id, workspace_id, member_id, payload.title)
        assert updated is not None
        return AssistantConversationResponse.model_validate(self.history(updated, request))

    def restore_draft(self, conversation_id: str, request: Request, payload: AssistantConversationDraftRestoreRequest):
        workspace_id, member_id, _row = self._owned_conversation(request, conversation_id)
        if self._clean_text(payload.user_message, workspace_id) != payload.user_message:
            raise HTTPException(status_code=422, detail='未发送草稿不能包含密钥或口令。')
        try:
            conversation, status = self._conversations_for().restore_unpersisted_turn_draft(conversation_id, workspace_id, member_id, str(payload.client_request_id), payload.user_message, payload.expected_draft_version)
        except ConversationDraftVersionConflict as exc:
            raise HTTPException(status_code=409, detail={'error': 'assistant_draft_conflict', 'message': '草稿版本已经变化；较新的草稿已保留，未恢复旧句子。', 'current_draft_version': exc.current['draft_version']}) from exc
        except ConversationTurnRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if conversation is None:
            raise HTTPException(status_code=404, detail='对话不存在或当前成员无权访问。')
        return AssistantConversationDraftRestoreResponse.model_validate({'status': status, 'conversation': self.history(conversation, request)})

    def list_inputs(self, conversation_id: str, request: Request):
        workspace_id, member_id, _row = self._owned_conversation(request, conversation_id)
        return {'items': self._conversations_for().list_inputs(conversation_id, workspace_id, member_id)}

    def enqueue_input(self, conversation_id: str, request: Request, payload: AssistantConversationInputRequest):
        workspace_id, member_id, conversation = self._owned_conversation(request, conversation_id)
        if self._clean_text(payload.message, workspace_id) != payload.message:
            raise HTTPException(status_code=422, detail='请勿在对话中粘贴密钥或口令。')
        context = self._conversations_for().consumed_turn_request_context(str(payload.target_turn_id), conversation_id, workspace_id, member_id)
        if not context:
            raise HTTPException(status_code=404, detail='目标轮次不存在或当前不可访问。')
        previous = next((item for item in self._conversations_for().list_inputs(conversation_id, workspace_id, member_id) if item['input_id'] == str(payload.client_request_id)), None)
        if previous is not None:
            context = previous['request_context']
        else:
            if conversation['reference_version'] != payload.expected_reference_version:
                raise HTTPException(status_code=409, detail='选中引用已变化，请重新读取后发送。')
            reference = self._validate_reference(request, conversation['selected_reference'])
            context = self._input_context(request, context, reference)
            context['expected_reference_version'] = payload.expected_reference_version
        try:
            item, _created = self._conversations_for().enqueue_input(conversation_id, workspace_id, member_id, target_turn_id=str(payload.target_turn_id), client_request_id=str(payload.client_request_id), message=payload.message, mode=payload.mode, expected_draft_version=payload.expected_draft_version, expected_reference_version=payload.expected_reference_version, request_context=context)
        except AssistantInputConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='目标会话或轮次不存在。') from exc
        return {'input': item, 'draft_version': payload.expected_draft_version + 1}

    def withdraw_input(self, conversation_id: str, input_id: str, request: Request):
        workspace_id, member_id, _row = self._owned_conversation(request, conversation_id)
        try:
            return self._conversations_for().transition_input(conversation_id, workspace_id, member_id, input_id, 'withdrawn')
        except AssistantInputConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='追加输入不存在。') from exc

def build_conversation_router(adapter_for):
    router = APIRouter()

    @router.post('/api/assistant/conversations/current', response_model=AssistantConversationResponse)
    def current_assistant_conversation(request: Request, payload: AssistantConversationCurrentRequest):
        return adapter_for().current(request, payload)

    @router.post('/api/assistant/conversations', response_model=AssistantConversationResponse)
    def create_assistant_conversation(request: Request, payload: AssistantConversationCreateRequest):
        return adapter_for().create(request, payload)

    @router.get('/api/assistant/conversations', response_model=AssistantConversationListResponse)
    def list_assistant_conversations(request: Request, scope_kind: str, scope_id: str):
        return adapter_for().list_conversations(request, scope_kind, scope_id)

    @router.get('/api/assistant/conversations/{conversation_id}', response_model=AssistantConversationResponse)
    def get_assistant_conversation(conversation_id: str, request: Request):
        return adapter_for().get_conversation(conversation_id, request)

    @router.get('/api/assistant/conversations/{conversation_id}/requests/{client_request_id}', response_model=AssistantConversationRequestResponse)
    def get_assistant_conversation_request(conversation_id: str, client_request_id: UUID, request: Request):
        return adapter_for().get_request(conversation_id, client_request_id, request)

    @router.post('/api/assistant/conversations/{conversation_id}/resume', response_model=AssistantConversationResponse)
    def resume_assistant_conversation(conversation_id: str, request: Request):
        return adapter_for().resume(conversation_id, request)

    @router.post('/api/assistant/conversations/{conversation_id}/turns/{turn_id}/plan-preview', response_model=CreationPlanPreviewResponse)
    def update_assistant_plan_preview(conversation_id: str, turn_id: UUID, request: Request, payload: CreationPlanPreviewRequest):
        return adapter_for().plan_preview(conversation_id, turn_id, request, payload)

    @router.get('/api/assistant/conversations/{conversation_id}/reference', response_model=AssistantConversationReferenceResponse)
    def get_assistant_conversation_reference(conversation_id: str, request: Request):
        return adapter_for().get_reference(conversation_id, request)

    @router.patch('/api/assistant/conversations/{conversation_id}/reference', response_model=AssistantConversationReferenceResponse)
    def save_assistant_conversation_reference(conversation_id: str, request: Request, payload: AssistantConversationReferenceRequest):
        return adapter_for().save_reference(conversation_id, request, payload)

    @router.patch('/api/assistant/conversations/{conversation_id}', response_model=AssistantConversationResponse)
    def update_assistant_conversation(conversation_id: str, request: Request, payload: AssistantConversationUpdateRequest):
        return adapter_for().update(conversation_id, request, payload)

    @router.post('/api/assistant/conversations/{conversation_id}/restore-unpersisted-turn', response_model=AssistantConversationDraftRestoreResponse)
    def restore_unpersisted_assistant_turn_draft(conversation_id: str, request: Request, payload: AssistantConversationDraftRestoreRequest):
        return adapter_for().restore_draft(conversation_id, request, payload)

    @router.get('/api/assistant/conversations/{conversation_id}/inputs')
    def list_assistant_inputs(conversation_id: str, request: Request):
        return adapter_for().list_inputs(conversation_id, request)

    @router.post('/api/assistant/conversations/{conversation_id}/inputs')
    def enqueue_assistant_input(conversation_id: str, request: Request, payload: AssistantConversationInputRequest):
        return adapter_for().enqueue_input(conversation_id, request, payload)

    @router.post('/api/assistant/conversations/{conversation_id}/inputs/{input_id}/withdraw')
    def withdraw_assistant_input(conversation_id: str, input_id: str, request: Request):
        return adapter_for().withdraw_input(conversation_id, input_id, request)

    return router
