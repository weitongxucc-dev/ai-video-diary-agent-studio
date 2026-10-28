"""Bind assistant execution origins and receipts through their existing owners.

TaskEvent is persisted before Conversation, as in the original flow. These are
separate owner transactions; a repeated locator repairs the conversation record
without submitting work or rebinding a newer input.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from app.creation.contracts import TaskEvent
from app.creation.intent_use_case import CreationIntentUseCase

if TYPE_CHECKING:
    from app.assistant_goal_continuation import GoalResultProjection
    from app.conversation_repository import ConversationRepository
    from app.creation.contracts import CreationTask, TimelineVersion
    from app.creation.execution_control import CreationExecutionControl
    from app.creation_execution import CreationExecutionActor
    from app.production import ProductionRepository


class ExecutionBindingRejected(Exception):
    """A semantic rejection; transport adapters choose the HTTP projection."""

    def __init__(self, detail: str, *, kind: Literal[
        'conflict', 'forbidden', 'unavailable', 'invalid_receipt', 'not_found',
    ] = 'conflict'):
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


def execution_locator_id(turn_id: str, input_id: str | None = None) -> str:
    return f"event_assistant_execution_locator_{turn_id}" + (f"_{input_id}" if input_id else "")


@dataclass(frozen=True)
class PreparedExecutionSource:
    """One checked origin, before the transport resolves its permit actor."""

    task: CreationTask
    turn_id: str
    input_id: str | None
    input_digest: str | None
    action: str
    source: dict


class AssistantExecutionBinding:
    def __init__(
        self,
        production: ProductionRepository,
        conversations: ConversationRepository,
        execution_for: Callable[[], CreationExecutionControl],
        projection: GoalResultProjection,
        *,
        verify_identity: Callable[[CreationExecutionActor], str],
        read_edit_receipt: Callable[[CreationTask, str, str, str], dict],
        encode_execution: Callable[[dict], dict],
    ):
        self.production = production
        self.conversations = conversations
        self.execution_for = execution_for
        self.projection = projection
        self.verify_identity = verify_identity
        # Canonical plain receipt: same field allowlist, 64 KiB bound and
        # recorded/unknown shape as the public reader, without an HTTP call.
        self.read_edit_receipt = read_edit_receipt
        # Preserve the existing response's JSON values before Conversation writes.
        # This pure projection accepts data only, never a request or a response.
        self.encode_execution = encode_execution

    def require_actor(self, actor: CreationExecutionActor | None) -> CreationExecutionActor:
        if actor is None:
            raise ExecutionBindingRejected(
                '多成员制作许可尚未接通持续身份核验', kind='unavailable')
        identity = self.verify_identity(actor)
        if identity == 'unknown':
            raise ExecutionBindingRejected(
                '设备成员许可暂不可核验，请恢复连接后重试', kind='unavailable')
        if identity != 'ready':
            raise ExecutionBindingRejected('设备成员或工作区制作许可已失效', kind='forbidden')
        return actor

    @staticmethod
    def require_input_origin(input_id: str | None, source: dict) -> None:
        if source.get('assistant_turn_id') and input_id != source.get('input_id'):
            raise ExecutionBindingRejected('助手输入已变化，旧请求不能归入新要求；请查询原请求回执')

    def read_bound(self, task: CreationTask, actor: CreationExecutionActor | None,
                   job_id: str, *, verify_media: bool) -> dict:
        actor = self.require_actor(actor)
        result = self.execution_for().read_bound(
            task.workspace_id, task.id, job_id, actor, verify_media=verify_media)
        return self.encode_execution(result)

    def edited_execution_version(
        self, task: CreationTask, member_id: str, turn_id: str, origin: dict,
        snapshot: dict, version: TimelineVersion | None, *, input_id: str | None,
    ) -> dict | None:
        """Derive only the same input's committed, immediately succeeding edit."""
        if (version is None or version.parent_version_id != snapshot.get('timeline_version_id')
                or version.sequence != snapshot.get('timeline_sequence', 0) + 1):
            return None
        operations = [item for item in self.production.edit_records.list_edit_operations(task.workspace_id, task.id)
                      if item.status == 'applied' and item.result_version_id == version.id
                      and item.base_version_id == snapshot['timeline_version_id']]
        if len(operations) != 1:
            return None
        operation = operations[0]
        proposal = self.production.edit_records.get_edit_proposal(task.workspace_id, operation.proposal_id)
        source = self.production.edit_records.get_edit_intent(
            task.workspace_id, proposal.intent_id).constraints.get('request_source') or {}
        if any(source.get(key) != value for key, value in {
            'workspace_id': task.workspace_id, 'task_id': task.id, 'member_id': member_id,
            'conversation_id': origin['conversation_id'], 'assistant_turn_id': str(turn_id),
            'input_id': origin.get('input_id'), 'input_turn_id': origin.get('input_turn_id'),
            'expected_timeline_version_id': snapshot['timeline_version_id'],
            'expected_timeline_sequence': snapshot['timeline_sequence'],
        }.items()):
            return None
        request_id = source.get('request_id')
        if not request_id:
            return None
        receipt = self.read_edit_receipt(task, member_id, str(UUID(request_id)), 'apply')
        submitted, result = receipt['submitted_input'], receipt['result']
        if (receipt['status'] != 'recorded' or receipt['result_status_code'] != 200 or not result
                or submitted.get('assistant_turn_id') != str(turn_id)
                or submitted.get('expected_timeline_version_id') != operation.base_version_id
                or submitted.get('expected_timeline_sequence') != snapshot['timeline_sequence']
                or result.get('operation', {}).get('id') != operation.id
                or result['operation'].get('result_version_id') != version.id):
            return None
        self.require_input_origin(input_id, source)
        return {**snapshot, 'timeline_version_id': version.id, 'timeline_sequence': version.sequence}

    def prepare_source(
        self, task: CreationTask, member_id: str, turn_id: str, *,
        input_id: str | None, action: str, input_digest: str | None,
    ) -> PreparedExecutionSource:
        # The transport's no-turn GUI/CLI path bypasses this assistant-only use case.
        try:
            origin = self.conversations.task_execution_source(
                str(turn_id), task.workspace_id, member_id, task.id, input_id=input_id)
        except ValueError as exc:
            raise ExecutionBindingRejected(str(exc)) from exc
        snapshot = origin['context'].get('target_snapshot', {})
        version = (self.production.timeline_records.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
                   if task.current_timeline_version_id else None)
        if not origin['raw_command'].strip():
            raise ExecutionBindingRejected('原助手制作要求的阶段或目标版本已失效')
        if origin['context'].get('stage') in {'topic', 'script'}:
            try:
                plan = self.production.plan_records.get_creation_plan(task.workspace_id, task.plan_id)
            except KeyError as exc:
                raise ExecutionBindingRejected('creation task not found', kind='not_found') from exc
            if plan.creation_task_id != task.id:
                raise ExecutionBindingRejected('creation task not found', kind='not_found')
            script = (self.production.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
                      if task.confirmed_script_id else None)
            if not CreationIntentUseCase.adopted_for_execution(plan, script):
                raise ExecutionBindingRejected('原回合尚无已授权采用的实际文案版本，不能开始制作。')
            try:
                CreationIntentUseCase.reject_discussion_only_input(origin['raw_command'])
            except ValueError as exc:
                raise ExecutionBindingRejected(str(exc)) from exc
        if (snapshot.get('timeline_version_id'), snapshot.get('timeline_sequence')) != (
                task.current_timeline_version_id, version.sequence if version else 0):
            successor = (self.edited_execution_version(
                task, member_id, turn_id, origin, snapshot, version, input_id=input_id)
                if action in {'start', 'resume'} else None)
            if successor is None:
                raise ExecutionBindingRejected('原助手制作要求的目标版本已失效，且没有同一输入的已提交编辑回执')
            snapshot = successor
        goal = self.conversations.get_task_goal(str(turn_id))
        if goal is not None and goal.get('source_input_id') == origin.get('input_id') and (
                goal['status'] in {'invalidated', 'no_progress', 'limit_reached'}
                or self.projection.pending_preview(goal, task.current_timeline_version_id) is not None):
            raise ExecutionBindingRejected('原要求已停止或仍等待真实修改采用，未开始制作')
        source = {'workspace_id': task.workspace_id, 'task_id': task.id, 'member_id': member_id,
            'assistant_turn_id': str(turn_id), 'conversation_id': origin['conversation_id'],
            **{key: origin[key] for key in ('input_id', 'input_turn_id') if key in origin},
            'expected_timeline_version_id': snapshot['timeline_version_id'],
            'expected_timeline_sequence': snapshot['timeline_sequence']}
        return PreparedExecutionSource(task, turn_id, input_id, input_digest, action, source)

    def source(self, prepared: PreparedExecutionSource, actor: CreationExecutionActor | None) -> tuple[dict, str | None]:
        # Only now may the adapter resolve its permit actor (which can itself
        # reject credentials). No request-capturing callback crosses this boundary.
        task, turn_id = prepared.task, prepared.turn_id
        input_id, input_digest = prepared.input_id, prepared.input_digest
        actor = self.require_actor(actor)
        source = {**prepared.source, 'actor_identity': actor.identity_payload(), 'action': prepared.action}
        self.require_input_origin(input_id, source)
        try:
            located = self.production.execution_records.get_task_event(
                task.workspace_id, execution_locator_id(turn_id, source.get('input_id')))
        except KeyError:
            return source, None
        if (located.payload['source'] != source
                or input_digest is not None and located.payload['input_digest'] != input_digest):
            raise ExecutionBindingRejected('本轮已绑定不同的制作请求；请只查询原作业')
        return source, located.payload['job_id']

    @staticmethod
    def candidate_job_id(candidate: dict) -> str:
        if not isinstance(candidate.get('job'), dict) or not isinstance(candidate['job'].get('id'), str):
            raise ExecutionBindingRejected('制作回执缺少真实作业身份', kind='invalid_receipt')
        return candidate['job']['id']

    def record_result(
        self, workspace_id: str, task_id: str, member_id: str,
        turn_id: str, result: dict, *, source: dict | None = None,
    ) -> dict:
        """Persist a result already read through the current task/actor gate."""
        if source is None:
            # A late model completion must retain the Job's original input, even
            # when another input has since been consumed by the conversation.
            previous = next((event for event in self.production.execution_records.list_task_events(workspace_id, task_id)
                if event.event_type == 'assistant_execution_located' and event.job_id == result['job']['id']
                and event.payload['source']['assistant_turn_id'] == turn_id), None)
            if previous is None:
                raise ExecutionBindingRejected('制作结果没有本轮的原始提交绑定，请只查询原作业')
            source = previous.payload['source']
        event_id = execution_locator_id(turn_id, source.get('input_id'))
        try:
            previous = self.production.execution_records.get_task_event(workspace_id, event_id)
        except KeyError:
            self.production.execution_records.append_task_event(TaskEvent(
                id=event_id, workspace_id=workspace_id, task_id=task_id,
                job_id=result['job']['id'], event_type='assistant_execution_located',
                payload={'job_id': result['job']['id'], 'input_digest': result['job']['input_digest'], 'source': source}))
        else:
            if previous.job_id != result['job']['id']:
                raise ExecutionBindingRejected('本轮制作来源已绑定原作业，不能改绑')
        current_task = self.production.task_records.get_creation_task(workspace_id, task_id)
        current = self.projection.execution_source_current(
            current_task, source, result['job']['id'], result['job']['status'])
        self.conversations.record_task_execution(
            turn_id, workspace_id, member_id, task_id, result,
            current_source=current, input_id=source.get('input_id'))
        return result

    def prepare_observation(
        self, workspace_id: str, task_id: str, member_id: str, turn_id: str, details: dict,
    ) -> PreparedExecutionSource | None:
        try:
            origin = self.conversations.task_result_source(
                turn_id, workspace_id, member_id, task_id,
                input_id=details.get('input_id'), input_turn_id=details.get('input_turn_id'))
        except ValueError as exc:
            raise ExecutionBindingRejected(str(exc), kind='invalid_receipt') from exc
        current = self.conversations.task_turn_source(turn_id, workspace_id, member_id, task_id)
        if (current.get('input_id'), current.get('input_turn_id')) != (
                origin.get('input_id'), origin.get('input_turn_id')):
            return None
        task = self.production.task_records.get_creation_task(workspace_id, task_id)
        snapshot = origin['context']['target_snapshot']
        source = {'workspace_id': workspace_id, 'task_id': task_id, 'member_id': member_id,
            'conversation_id': origin['conversation_id'], 'assistant_turn_id': turn_id,
            'input_id': origin['input_id'], 'input_turn_id': origin['input_turn_id'],
            'expected_timeline_version_id': snapshot['timeline_version_id'],
            'expected_timeline_sequence': snapshot['timeline_sequence']}
        return PreparedExecutionSource(task, turn_id, origin['input_id'], None, 'observe', source)

    def observation_source(
        self, prepared: PreparedExecutionSource, actor: CreationExecutionActor | None, result: dict,
    ) -> dict | None:
        """Authorize after the late-input gate; only a current source may bind.

        On None the adapter returns the original result without entering its
        write-access gate or waking anything. Otherwise it performs that existing
        gate, reads the original Job and calls record_result, then wakes after persistence succeeds.
        """
        actor = self.require_actor(actor)
        source = {**prepared.source, 'actor_identity': actor.identity_payload(), 'action': 'observe'}
        if not self.projection.execution_source_current(
                prepared.task, source, result['job']['id'], result['job']['status']):
            return None
        return source
