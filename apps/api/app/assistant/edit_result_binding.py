"""Verify and bind an original edit result without submitting or applying work."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import UUID, NAMESPACE_URL, uuid5

from app.creation.contracts import TaskEvent
from app.models import utc_now
from app.schemas import AssistantEditRequestLocator, CreationTaskEditConfirmResponse
from app.task_audio import media_digest

if TYPE_CHECKING:
    from app.conversation_repository import ConversationRepository
    from app.creation_execution import CreationExecutionActor
    from app.production import ProductionRepository


class EditResultRejected(Exception):
    """An unverifiable result, projected by the transport as a receipt rejection."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class EditRequestReference:
    request_id: UUID
    phase: str
    query_url: str


@dataclass(frozen=True)
class PreparedEditResult:
    """One locator's restored source, before any fallback actor is resolved."""

    workspace_id: str
    task_id: str
    member_id: str
    turn_id: str
    locator: AssistantEditRequestLocator
    source: dict | None
    origin: dict | None
    snapshot: dict | None
    locator_created_at: datetime | None


class AssistantEditResultBinding:
    def __init__(self, production: ProductionRepository, conversations: ConversationRepository):
        self.production = production
        self.conversations = conversations

    def completed_background_edit(self, task, actor, turn_id, input_id, events, *, read_source, read_original):
        """Read the committed direct edit, independent of the model's final reply."""
        sources = [event for event in events if event.event_type == 'edit_request_source'
            and event.payload['source'].get('assistant_turn_id') == turn_id
            and event.payload['source'].get('input_id') == input_id]
        for event in reversed(sources):
            claim = next(item for item in events if item.id == event.payload['request_event_id'])
            if claim.payload['phase'] != 'apply':
                continue
            source = read_source(task, event.payload['request_event_id'], actor)
            member = actor.user_id or actor.member_id
            actual = read_original(task, actor, source['request_id'], 'apply')
            if (actual['status'] != 'recorded' or actual['result_status_code'] != 200
                    or 'operation_type' not in (actual['submitted_input'] or {})):
                continue
            details = {'task_id': task.id, 'request_id': source['request_id'], 'phase': 'apply',
                'query_url': f"/api/creation-tasks/{quote(task.id, safe='')}/assistant/edit-requests/{source['request_id']}?phase=apply"}
            reference = self.request_reference(task.id, turn_id, details)
            locator = self.locator(task.id, reference, actual)
            prepared = self.prepare_binding(task.workspace_id, task.id, member, turn_id, locator, details)
            self.require_applied(task.workspace_id, task.id, turn_id, locator)
            return self.record_locator(prepared, actor)
        return None

    @staticmethod
    def request_reference(task_id: str, turn_id: str, details: dict) -> EditRequestReference:
        try:
            request_id = UUID(str(details['request_id']))
        except (KeyError, ValueError) as exc:
            raise EditResultRejected('编辑请求定位缺少有效请求身份') from exc
        phase = details.get('phase', 'preview')
        if phase not in {'preview', 'apply', 'script', 'observation'}:
            raise EditResultRejected('编辑请求定位phase不可核验')
        query_url = f"/api/creation-tasks/{quote(task_id, safe='')}/assistant/edit-requests/{request_id}?phase={phase}"
        if details.get('task_id', task_id) != task_id or details.get('query_url') != query_url:
            raise EditResultRejected('编辑请求定位不属于当前任务')
        return EditRequestReference(request_id, phase, query_url)

    @staticmethod
    def missing_result(task_id: str, turn_id: str, reference: EditRequestReference, details: dict) -> dict:
        """Called only after the canonical reader reported not-found, never 403/409."""
        call_id = details.get('tool_call_id')
        if (details.get('assistant_turn_id') != turn_id or not isinstance(call_id, str)
                or not 1 <= len(call_id) <= 128 or reference.request_id != uuid5(NAMESPACE_URL,
                    json.dumps([task_id, turn_id, call_id], ensure_ascii=False, separators=(',', ':')))):
            raise EditResultRejected('原请求尚无服务端记录，工具定位来源不可核验')
        status = details.get('status')
        status = 'unknown' if status in {'unknown', 'result_unknown'} else status
        if status not in {'unknown', 'rejected', 'not_submitted'}:
            raise EditResultRejected('未提交请求不能声称已有结果')
        return {'status':status, 'input_digest':None, 'result_status_code':None,
                'result':None, 'submitted_input':None}

    @staticmethod
    def locator(task_id: str, reference: EditRequestReference, actual: dict) -> AssistantEditRequestLocator:
        status = ('rejected' if actual['status'] == 'recorded' and actual['result_status_code'] >= 400
                  else actual['status'])
        return AssistantEditRequestLocator(task_id=task_id, request_id=reference.request_id,
            phase=reference.phase, query_url=reference.query_url, status=status,
            input_digest=actual['input_digest'], submitted_input=actual['submitted_input'],
            result_status_code=actual['result_status_code'], result=actual['result'])

    def prepare_binding(
        self, workspace_id: str, task_id: str, member_id: str, turn_id: str,
        locator: AssistantEditRequestLocator, details: dict,
    ) -> PreparedEditResult:
        source = None
        previous = next((event for event in self.production.execution_records.list_task_events(workspace_id, task_id)
            if event.event_type == 'assistant_edit_request_located'
            and event.payload['source']['assistant_turn_id'] == turn_id
            and event.payload['locator']['request_id'] == str(locator.request_id)), None)
        if previous is not None:
            source = previous.payload['source']
        actual_result = locator.result or {}
        if actual_result.get('job_id'):
            start = self.production.execution_records.get_task_event(workspace_id, f"event_{actual_result['job_id']}_start")
            original = start.payload['arguments'].get('_typed_source')
            if original is not None:
                source = original
        elif isinstance(actual_result.get('proposal'), dict):
            proposal = self.production.edit_records.get_edit_proposal(workspace_id, actual_result['proposal']['id'])
            original = self.production.edit_records.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('request_source')
            if original is not None:
                source = original
        if locator.phase == 'apply' and 'operation_type' in (locator.submitted_input or {}):
            recorded_operation = (locator.result or {}).get('operation')
            if locator.result_status_code == 200 and isinstance(recorded_operation, dict):
                operation = self.production.edit_records.get_edit_operation(workspace_id, recorded_operation['id'])
                proposal = self.production.edit_records.get_edit_proposal(workspace_id, operation.proposal_id)
                intent = self.production.edit_records.get_edit_intent(workspace_id, proposal.intent_id)
                original = intent.constraints.get('request_source')
                if (operation.task_id != task_id or not original
                        or (original.get('workspace_id'), original.get('task_id'), original.get('member_id'), original.get('request_id'))
                            != (workspace_id, task_id, member_id, str(locator.request_id))):
                    raise EditResultRejected('原编辑操作与请求来源不一致')
                source = original
        origin, snapshot = None, None
        if source is None:
            try:
                origin = self.conversations.task_result_source(turn_id, workspace_id, member_id, task_id,
                    input_id=details.get('input_id'), input_turn_id=details.get('input_turn_id'))
            except ValueError as exc:
                raise EditResultRejected(str(exc)) from exc
            snapshot = origin['context']['target_snapshot']
        return PreparedEditResult(workspace_id, task_id, member_id, turn_id, locator, source, origin, snapshot,
            previous.created_at if previous is not None else None)

    def record_locator(self, prepared: PreparedEditResult, actor: CreationExecutionActor | None) -> AssistantEditRequestLocator:
        """Persist the original locator before the separate applied-result check."""
        ws, task_id, member_id, turn_id = (prepared.workspace_id, prepared.task_id,
                                         prepared.member_id, prepared.turn_id)
        locator, source = prepared.locator, prepared.source
        if source is None:
            origin = prepared.origin
            snapshot = prepared.snapshot
            source = {'workspace_id':ws, 'task_id':task_id, 'member_id':member_id,
                'conversation_id':origin['conversation_id'], 'assistant_turn_id':turn_id,
                **{key:origin[key] for key in ('input_id','input_turn_id') if key in origin},
                'expected_timeline_version_id':snapshot['timeline_version_id'],
                'expected_timeline_sequence':snapshot['timeline_sequence'],
                'actor_identity':actor.identity_payload() if actor else None}
            if locator.phase == 'apply' and 'operation_type' in (locator.submitted_input or {}):
                submitted = locator.submitted_input
                source.update(assistant_turn_id=submitted.get('assistant_turn_id'),
                    expected_timeline_version_id=submitted.get('expected_timeline_version_id'),
                    expected_timeline_sequence=submitted.get('expected_timeline_sequence'))
        if (source.get('workspace_id'), source.get('task_id'), source.get('member_id'), source.get('assistant_turn_id')) != (
                ws, task_id, member_id, turn_id):
            raise EditResultRejected('原编辑回执来源与当前查询身份不一致')
        self.production.execution_records.append_task_event(TaskEvent(id=f'event_edit_locator_{turn_id}_{locator.request_id}',
            workspace_id=ws, task_id=task_id, job_id=None, event_type='assistant_edit_request_located',
            payload={'source':source, 'locator':locator.model_dump(mode='json'),
                'request_event_id':'event_edit_request_'+media_digest([ws,member_id,str(locator.request_id),locator.phase])},
            created_at=prepared.locator_created_at if prepared.locator_created_at is not None else utc_now()))
        return locator

    def require_applied(self, workspace_id: str, task_id: str, turn_id: str,
                        locator: AssistantEditRequestLocator) -> AssistantEditRequestLocator:
        if locator.phase != 'apply' or locator.status != 'recorded' or locator.result_status_code != 200:
            raise EditResultRejected('直接编辑尚无可核验的完成回执')
        confirmed = CreationTaskEditConfirmResponse.model_validate(locator.result)
        actual = self.production.edit_records.get_edit_operation(workspace_id, confirmed.operation.id)
        submitted = locator.submitted_input or {}
        if (actual.task_id != task_id or actual.status != 'applied' or not actual.result_version_id
                or submitted.get('assistant_turn_id') != turn_id
                or submitted.get('operation_type') != actual.operation_type
                or submitted.get('expected_timeline_version_id') != actual.base_version_id
                or confirmed.operation.result_version_id != actual.result_version_id):
            raise EditResultRejected('直接编辑回执与当前输入及实际操作不一致')
        return locator
