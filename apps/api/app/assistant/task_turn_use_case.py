"""Complete task-assistant turn orchestration over named host checkpoints.

Business decisions own the full input-to-result chain. HTTP, SDK invocation and
DTO rendering answer finite checkpoints; durable owners keep their transactions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import UUID
from pydantic import ValidationError
from app.assistant.failures import AssistantUnavailable
from app.assistant.turn_control import AssistantStopped
from app.assistant.task_turn_scope_use_case import TaskTurnSelection
from app.assistant.projection import _safe_business_failure, _safe_compaction_event
from app.creation.media_result_use_case import MediaResultRejected
from app.assistant.turn_admission_use_case import (
    TurnRejected, ReadWorkspace, CleanText, ResolveTurnScope, ReadTaskClipMaterial,
    ProfileSnapshot, KnowledgeSnapshot, AccessTask, RegisterControl,
)


@dataclass(frozen=True)
class ProfileReference:
    kind: str
    profile_id: str
    version: str


@dataclass(frozen=True)
class KnowledgeReference:
    knowledge_item_id: str
    version: str
    locator: str | None


@dataclass(frozen=True)
class TaskTurnCommand:
    task_id: str
    conversation_id: UUID
    message: str
    client_request_id: UUID
    expected_draft_version: int
    expected_reference_version: int | None
    expected_timeline_version_id: str | None
    expected_timeline_sequence: int | None
    selected_profile_reference: ProfileReference | None
    selected_knowledge_reference: KnowledgeReference | None
    context_stage: str
    selected_object_refs: list[str]
    playhead_ms: int | None


@dataclass(frozen=True)
class ReadTask:
    workspace_id: object
    task_id: object

@dataclass(frozen=True)
class ReadTaskRecord:
    workspace_id: object
    task_id: object

@dataclass(frozen=True)
class ReadTimeline:
    workspace_id: object
    version_id: object

@dataclass(frozen=True)
class ReadCaller:
    workspace_id: object

@dataclass(frozen=True)
class TaskVisible:
    workspace_id: object
    visibility: object
    owner_user_id: object

@dataclass(frozen=True)
class ReadMember:
    workspace_id: object

@dataclass(frozen=True)
class ReadSettings:
    workspace_id: object

@dataclass(frozen=True)
class SafeText:
    value: object
    workspace_id: object

@dataclass(frozen=True)
class RequireActor:
    workspace_id: object

@dataclass(frozen=True)
class BeginTurn:
    conversation_id: object
    scope_kind: object
    scope_id: object
    message: object
    client_request_id: object
    expected_draft_version: object
    request_context: object
    expected_reference_version: object

@dataclass(frozen=True)
class ReplayTurn:
    turn_id: object
    provider: object
    model: object
    draft_version: object

@dataclass(frozen=True)
class RunTask:
    workspace_id: object
    member_id: object
    task_id: object
    conversation_id: object
    turn_id: object
    message: object
    stage: object
    selected_refs: object
    playhead_ms: object
    caller_user_id: object
    local_diagnostic: object
    profile_reference: object
    knowledge_reference: object
    task_version: object
    material_selection: object

@dataclass(frozen=True)
class BuildTaskResponse:
    fields: object

@dataclass(frozen=True)
class SerializeView:
    value: object
    mode: object

@dataclass(frozen=True)
class ForgetControl:
    turn_id: object

@dataclass(frozen=True)
class ReadAppliedEdit:
    task_id: object
    turn_id: object
    details: object

@dataclass(frozen=True)
class ResolveQuality:
    preview: object
    task_id: object
    invocation_input_id: object

@dataclass(frozen=True)
class ResolveWindow:
    preview: object
    task_id: object

@dataclass(frozen=True)
class ValidateWindow:
    value: object

@dataclass(frozen=True)
class ResolveSegmentRevision:
    preview: object
    task_id: object
    input_id: object
    actor: object
    workspace_id: object

@dataclass(frozen=True)
class ResolveTimelineRestore:
    preview: object
    task_id: object
    actor: object
    workspace_id: object

@dataclass(frozen=True)
class ResolveStoryboard:
    task_id: object
    turn_id: object
    details: object

@dataclass(frozen=True)
class ResolveScriptTool:
    task_id: object
    turn_id: object
    details: object

@dataclass(frozen=True)
class RelatedMedia:
    task_id: object
    turn_id: object
    conversation_id: object
    details: object

@dataclass(frozen=True)
class ResolveEditRequest:
    task_id: object
    turn_id: object
    details: object

@dataclass(frozen=True)
class SelectMediaFinish:
    turn_id: object
    value: object

@dataclass(frozen=True)
class CompletedMedia:
    task: object
    turn_id: object
    slot: object
    contract: object
    input_refs: object

@dataclass(frozen=True)
class ReadMedia:
    task_id: object
    job_id: object

@dataclass(frozen=True)
class ValidateMedia:
    value: object

@dataclass(frozen=True)
class ReadScript:
    workspace_id: object
    script_id: object

@dataclass(frozen=True)
class ValidateScript:
    value: object

@dataclass(frozen=True)
class ValidateTopics:
    value: object

@dataclass(frozen=True)
class ReadBoundExecution:
    task_id: object
    job_id: object

@dataclass(frozen=True)
class BindObservation:
    task_id: object
    turn_id: object
    details: object
    result: object

@dataclass(frozen=True)
class BindExecution:
    task_id: object
    turn_id: object
    status: object

@dataclass(frozen=True)
class ValidateEditPreview:
    value: object

@dataclass(frozen=True)
class ReadEditProposal:
    workspace_id: object
    proposal_id: object

@dataclass(frozen=True)
class ReadEditIntent:
    workspace_id: object
    intent_id: object

@dataclass(frozen=True)
class RenderEditPreview:
    intent: object
    proposal: object

@dataclass(frozen=True)
class RegisterGoal:
    turn_id: object

@dataclass(frozen=True)
class BindGoalScope:
    goal: object

@dataclass(frozen=True)
class WakeContinuations:
    pass


@dataclass(frozen=True)
class ReadGoalPlan:
    workspace_id: str
    task: object

@dataclass(frozen=True)
class GoalShuttingDown:
    pass

@dataclass(frozen=True)
class GoalStream:
    workspace_id: str

@dataclass(frozen=True)
class RunGoalTask:
    goal: dict
    turn_id: str
    message: str
    stage: str
    selected_refs: list[str]
    playhead_ms: object
    business: object
    native_actor: object
    binding: dict
    task_version: dict
    facts: dict
    continuation_context: dict
    on_event: object


class TaskAssistantTurnUseCase:
    def __init__(self, *, conversations_for, window_case_for, read_event, read_intent, list_events):
        self._conversations_for = conversations_for
        self._window_case_for = window_case_for
        self._read_event, self._read_intent, self._list_events = read_event, read_intent, list_events

    def finish_goal(self, business, claim, reply, preview):
        message = (yield CleanText(reply, claim['goal']['workspace_id']))
        return business.finish_goal(claim, message, preview)

    def run_goal(self, claim, fresh):
        goal, context, business = claim['goal'], claim['request_context'], fresh['business']
        task_id, workspace_id, turn_id = goal['task_id'], goal['workspace_id'], claim['turn_id']
        task = (yield ReadTask(workspace_id, task_id))
        stage = context['stage']
        if stage in {'topic', 'script'} and task.confirmed_script_id:
            from app.creation.intent_use_case import CreationIntentUseCase
            adopted_script = (yield ReadScript(workspace_id, task.confirmed_script_id))
            adopted_plan = (yield ReadGoalPlan(workspace_id, task))
            if CreationIntentUseCase.adopted_for_execution(adopted_plan, adopted_script):
                stage = task.stage
        if (claim['event'].get('status') == 'confirmed' and 'adopted_operation' in fresh['facts']
                or stage == 'review' and task.stage == 'montage'):
            stage = task.stage
        message = claim['event']['message'] if claim['event'].get('phase') == 'input' else goal['original_instruction']
        selection = TaskTurnSelection(stage, [ref for ref in context.get('selected_object_refs', [])
            if ref not in fresh['task_version'].get('removed_selected_refs', [])], context.get('playhead_ms'))
        stage, refs = (yield ResolveTurnScope(workspace_id, task, selection))
        yield RegisterControl(turn_id)
        try:
            if (yield GoalShuttingDown()):
                self._conversations_for().finish_turn(turn_id, 'failed', result={'reason': 'api_shutdown'})
                return
            sanitizer, safe_text = (yield GoalStream(workspace_id))

            def on_event(event):
                self.record_window_event(event, workspace_id=workspace_id, member_id=goal['member_id'],
                    task_id=task_id, turn_id=turn_id)
                if event.get('type') == 'context_compaction':
                    diagnostic = _safe_compaction_event(event)
                    if diagnostic is not None:
                        self._conversations_for().append_event(turn_id, diagnostic)
                    return
                if event.get('type') == 'text_delta':
                    delta = sanitizer.feed(str(event.get('delta', '')))
                    if delta:
                        self._conversations_for().append_event(turn_id, {'type': 'text_delta', 'delta': delta})
                elif event.get('type') in {'tool_start', 'tool_end'}:
                    safe = {key: value for key, value in event.items() if key in {'type', 'tool_id', 'label', 'ok', 'summary'}}
                    for key in ('label', 'summary'):
                        if key in safe:
                            safe[key] = safe_text(str(safe[key]))
                    if isinstance(event.get('failure'), dict):
                        safe['failure'] = {key: safe_text(value) if isinstance(value, str) else value
                            for key, value in _safe_business_failure(event['failure']).items()}
                    self._conversations_for().append_event(turn_id, safe)

            try:
                binding = {'workspace_id': workspace_id, 'member_id': goal['member_id'], 'task_id': task_id,
                    'conversation_id': goal['conversation_id'], 'turn_id': turn_id, 'goal_revision': str(goal.get('revision', 0)),
                    'input_id': goal.get('source_input_id'), 'input_turn_id': goal.get('source_input_turn_id'),
                    'task_version': fresh['task_version']}
                native_actor, _native_task, _native_scope = business.original(goal)
                reply, preview = (yield RunGoalTask(goal, turn_id, message, stage, refs, context.get('playhead_ms'),
                    business, native_actor, binding, fresh['task_version'], fresh['facts'],
                    {'original_turn_id': goal['original_turn_id'], 'business_event': claim['event'],
                     'source_input_id': goal.get('source_input_id'), 'source_input_turn_id': goal.get('source_input_turn_id'),
                     'current_facts': fresh['facts'], 'round_count': goal['round_count'],
                     'goal_checkpoint': {key: goal[key] for key in ('target_snapshot', 'unfinished_steps', 'handled_event_ids')},
                     'completed_source_slots': goal.get('completed_source_slots', []),
                     'completed_output_checkpoint': goal.get('completed_output_checkpoint'),
                     'unfinished_steps': goal['unfinished_steps'], 'next_checkpoint': goal.get('next_checkpoint')}, on_event))
                tail = sanitizer.feed('', final=True)
                if tail:
                    self._conversations_for().append_event(turn_id, {'type': 'text_delta', 'delta': tail})
                yield from self.finish_goal(business, claim, reply, preview)
            except AssistantUnavailable as exc:
                failure_message = safe_text(str(exc))
                self._conversations_for().finish_turn(turn_id, 'failed', failure_message, result={
                    'kind': 'failure', 'message': failure_message,
                    'business_failure': _safe_business_failure(getattr(exc, 'business_failure', None)),
                })
                return
            except AssistantStopped:
                if (yield GoalShuttingDown()):
                    self._conversations_for().finish_turn(turn_id, 'failed', result={'reason': 'api_shutdown'})
                else:
                    self._conversations_for().finish_turn(turn_id, 'stopped',
                        result={'kind': 'task_turn', 'response': {'turn_status': 'stopped'}})
        finally:
            yield ForgetControl(turn_id)

    def turn(self, payload):
        task_id = payload.task_id
        workspace_id = (yield ReadWorkspace())
        task = (yield ReadTask(workspace_id, task_id))
        caller_user_id, local_diagnostic = (yield ReadCaller(workspace_id))
        if not (yield TaskVisible(workspace_id, task.visibility, task.owner_user_id)):
            raise TurnRejected(status_code=404, detail='creation task not found')
        member_id = (yield ReadMember(workspace_id))
        previous_context = self._conversations_for().request_context(str(payload.client_request_id), str(payload.conversation_id), workspace_id, member_id)
        current_version = None
        if task.current_timeline_version_id is not None:
            try:
                current_version = (yield ReadTimeline(workspace_id, task.current_timeline_version_id))
            except KeyError as exc:
                raise TurnRejected(status_code=409, detail='当前任务的时间线版本不可用') from exc
            if current_version.task_id != task.id:
                raise TurnRejected(status_code=409, detail='当前时间线不属于此任务')
        task_version = {'timeline_version_id': task.current_timeline_version_id, 'timeline_sequence': current_version.sequence if current_version is not None else 0}
        if previous_context is None and payload.expected_timeline_sequence is not None and (payload.expected_timeline_sequence != task_version['timeline_sequence'] or payload.expected_timeline_version_id != task.current_timeline_version_id):
            raise TurnRejected(status_code=409, detail={'error': 'creation_task_timeline_version_conflict', 'message': '当前任务版本已变化，请重新查看选中对象后发送。'})
        trusted_stage, trusted_refs = (yield ResolveTurnScope(workspace_id, task, payload))
        material_selection = previous_context.get('target_snapshot', {}).get('material_selection') if previous_context is not None else (yield ReadTaskClipMaterial(task, trusted_refs))
        profile_reference = (yield ProfileSnapshot(payload.selected_profile_reference.kind, payload.selected_profile_reference.profile_id, payload.selected_profile_reference.version)) if payload.selected_profile_reference is not None else None
        message = payload.message.strip()
        if (yield CleanText(message, workspace_id)) != message:
            raise TurnRejected(status_code=422, detail='请勿在创作要求中粘贴密钥或口令。')
        knowledge_reference = (yield KnowledgeSnapshot(payload.selected_knowledge_reference.knowledge_item_id, payload.selected_knowledge_reference.version, payload.selected_knowledge_reference.locator)) if payload.selected_knowledge_reference is not None else None
        turn_id, created, _turn = (yield BeginTurn(str(payload.conversation_id), 'task', task_id, message, payload.client_request_id, payload.expected_draft_version, {'kind': 'task', 'task_id': task_id, 'stage': trusted_stage, 'selected_object_refs': sorted(trusted_refs), 'playhead_ms': payload.playhead_ms, 'expected_timeline_version_id': payload.expected_timeline_version_id, 'expected_timeline_sequence': payload.expected_timeline_sequence, 'profile_reference': asdict(payload.selected_profile_reference) if payload.selected_profile_reference else None, 'knowledge_reference': asdict(payload.selected_knowledge_reference) if payload.selected_knowledge_reference else None, 'target_snapshot': {**task_version, 'profile_reference': profile_reference, 'knowledge_reference': knowledge_reference, **({'material_selection': material_selection} if len(trusted_refs) == 1 and trusted_refs[0].startswith('clip:') else {})}}, expected_reference_version=payload.expected_reference_version))
        if not created:
            replay_settings = (yield ReadSettings(workspace_id))
            return (yield ReplayTurn(turn_id, provider=replay_settings.effective_llm_provider, model=replay_settings.effective_llm_model, draft_version=payload.expected_draft_version + 1))
        frozen_context = self._conversations_for().consumed_turn_request_context(turn_id, str(payload.conversation_id), workspace_id, member_id)
        trusted_refs = frozen_context.get('selected_object_refs', trusted_refs)
        material_selection = (frozen_context.get('target_snapshot') or {}).get('material_selection')
        try:
            reply, preview = (yield RunTask(workspace_id, member_id, task_id, str(payload.conversation_id), turn_id, message, trusted_stage, trusted_refs, payload.playhead_ms, caller_user_id, local_diagnostic, profile_reference, knowledge_reference, task_version, material_selection))
        except AssistantStopped:
            saved = self._conversations_for().get_turn(turn_id)
            response = (yield BuildTaskResponse({'reply': saved['assistant_text'] if saved else '', 'provider': (yield ReadSettings(workspace_id)).effective_llm_provider, 'model': (yield ReadSettings(workspace_id)).effective_llm_model, 'turn_id': turn_id, 'turn_status': 'stopped', 'draft_version': payload.expected_draft_version + 1, 'script_window_request': ((saved or {}).get('result') or {}).get('response', {}).get('script_window_request'), 'quality_continuation': ((saved or {}).get('result') or {}).get('response', {}).get('quality_continuation')}))
            self._conversations_for().finish_turn(turn_id, 'stopped', result={'kind': 'task_turn', 'response': (yield SerializeView(response, 'json'))})
            return response
        except AssistantUnavailable as exc:
            yield ForgetControl(turn_id)
            self._conversations_for().finish_turn(turn_id, 'failed', result={'kind': 'failure', 'message': (yield SafeText(str(exc), workspace_id))})
            raise TurnRejected(status_code=503, detail=str(exc)) from exc
        return (yield from self.finish(task_id, payload, turn_id, reply, preview, trusted_stage))

    def finish(self, task_id, payload, turn_id, reply, preview, trusted_stage):
        workspace_id = (yield ReadWorkspace())
        reply = (yield CleanText(reply, workspace_id))
        script_draft = None
        execution_action = None
        execution_status = None
        topic_candidates = None
        storyboard_regeneration = None
        script_window_request = None
        quality_continuation = None
        timeline_action = None
        media_job, edit_request, script_tool, segment_revision, timeline_restore = (None, None, None, None, None)
        if isinstance(preview, dict) and preview.get('kind') in {'execution_action', 'execution_result'} and (preview.get('applied_edit') is not None):
            edit_request = (yield ReadAppliedEdit(task_id, turn_id, preview['applied_edit']))
            timeline_action = 'apply'
        if isinstance(preview, dict) and preview.get('kind') == 'quality_continuation':
            from app.assistant.result_projection import quality_continuation_message
            origin = self._conversations_for().task_turn_source(str(turn_id), workspace_id, (yield ReadMember(workspace_id)), task_id)
            try:
                quality_continuation = (yield ResolveQuality(preview, task_id=task_id, invocation_input_id=origin.get('input_id') or str(turn_id)))
            except ValueError as exc:
                raise TurnRejected(status_code=502, detail=str(exc)) from exc
            reply = quality_continuation_message(quality_continuation)
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'script_window_request':
            from app.assistant.script_window_receipts import script_window_message
            origin = self._conversations_for().task_turn_source(str(turn_id), workspace_id, (yield ReadMember(workspace_id)), task_id)
            if preview.get('input_id') != origin.get('input_id'):
                raise TurnRejected(status_code=502, detail='文案原请求不属于当前输入')
            resolved = (yield ResolveWindow(preview, task_id=task_id))
            script_window_request = (yield ValidateWindow(resolved))
            reply = script_window_message(resolved)
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'segment_revision':
            from app.assistant.result_projection import segment_revision_message
            actor = (yield RequireActor(workspace_id))
            origin = self._conversations_for().task_turn_source(str(turn_id), workspace_id, (yield ReadMember(workspace_id)), task_id)
            segment_revision = (yield ResolveSegmentRevision(preview, task_id=task_id, input_id=origin['input_id'], actor=actor, workspace_id=workspace_id))
            reply = segment_revision_message(segment_revision)
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'timeline_restore_request':
            from app.assistant.result_projection import timeline_restore_message
            actor = (yield RequireActor(workspace_id))
            timeline_restore = (yield ResolveTimelineRestore(preview, task_id=task_id, actor=actor, workspace_id=workspace_id))
            reply = timeline_restore_message(timeline_restore)
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') in {'storyboard_regeneration_receipt', 'storyboard_regeneration_unknown', 'storyboard_regeneration_rejected'}:
            from app.assistant.result_projection import storyboard_regeneration_message
            storyboard_regeneration = (yield ResolveStoryboard(task_id, turn_id, preview))
            reply = storyboard_regeneration_message((yield SerializeView(storyboard_regeneration, 'json')))
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') in {'media_script_tool_result', 'media_script_tool_status'}:
            script_tool, reply = (yield ResolveScriptTool(task_id, turn_id, preview))
            if preview.get('related_media_job') is not None:
                media_job = (yield RelatedMedia(task_id, turn_id, str(payload.conversation_id), preview['related_media_job']))
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'edit_applied':
            edit_request = (yield ReadAppliedEdit(task_id, turn_id, preview))
            timeline_action = 'apply'
            edit_preview = None
            reply = '修改已保存，可撤销；重新生成后可播放更新的成片。'
        elif isinstance(preview, dict) and preview.get('kind') == 'edit_request_status':
            edit_request = (yield ResolveEditRequest(task_id, turn_id, preview))
            if preview.get('related_media_job') is not None:
                media_job = (yield RelatedMedia(task_id, turn_id, str(payload.conversation_id), preview['related_media_job']))
            edit_preview = None
            reply = '原编辑请求定位已保存；尚未确认修改完成。' if edit_request.status == 'unknown' else '原编辑请求已回读，请查看实际状态；没有重复提交。'
        elif isinstance(preview, dict) and preview.get('kind') == 'media_job':
            try:
                selected = (yield SelectMediaFinish(turn_id, preview.get('media_job')))
            except MediaResultRejected as exc:
                raise TurnRejected(exc.status_code, exc.detail) from exc
            if selected.completed_slot is not None:
                slot = selected.completed_slot
                task, _member_id = (yield AccessTask(task_id, write=True))
                media_job = (yield ValidateMedia((yield CompletedMedia(task, turn_id, slot, contract=slot['requested_output'], input_refs=slot['input_refs']))))
            else:
                media_job = (yield ValidateMedia((yield ReadMedia(task_id, selected.job_id))))
            edit_preview = None
        elif isinstance(preview, dict) and 'script_id' in preview and ('proposal' not in preview):
            script_id = preview.get('script_id') if isinstance(preview, dict) else None
            if not isinstance(script_id, str):
                raise TurnRejected(status_code=502, detail='对话没有返回可核实的文案草稿。')
            try:
                candidate = (yield ReadScript(workspace_id, script_id))
            except KeyError as exc:
                raise TurnRejected(status_code=502, detail='对话返回的文案草稿不存在。') from exc
            adopted = candidate.selection_status == 'confirmed' and candidate.adoption and ((yield ReadTaskRecord(workspace_id, task_id)).confirmed_script_id == candidate.id)
            if candidate.task_id != task_id or (candidate.selection_status != 'draft' and (not adopted)):
                raise TurnRejected(status_code=502, detail='对话返回的文案草稿不属于当前任务。')
            script_draft = (yield ValidateScript(asdict(candidate)))
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'topic_generation':
            try:
                topics = (yield ValidateTopics(preview.get('topics')))
            except ValidationError as exc:
                raise TurnRejected(status_code=502, detail='对话没有返回可核实的选题候选。') from exc
            if topics.task.id != task_id:
                raise TurnRejected(status_code=502, detail='对话生成的选题不属于当前任务。')
            topic_candidates = topics
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'execution_result':
            status = preview.get('status')
            if not isinstance(status, dict) or status.get('task_id') != task_id or (not isinstance(status.get('job'), dict)):
                raise TurnRejected(status_code=502, detail='没有返回可核验的原制作作业')
            try:
                execution_status = (yield ReadBoundExecution(task_id, status['job']['id']))
                if preview.get('input_id') and execution_status['job']['status'] in {'queued', 'running'}:
                    execution_status = (yield BindObservation(task_id, turn_id, preview, execution_status))
            except TurnRejected as exc:
                if edit_request is None:
                    raise
                execution_status = None
                reply = '修改已保存，可撤销；原制作作业回读未通过核验。' + (yield SafeText(str(exc.detail), workspace_id))
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'execution_action':
            action = preview.get('action')
            status = preview.get('status')
            if action not in {'start', 'resume', 'cancel'} or not isinstance(status, dict) or status.get('task_id') != task_id:
                raise TurnRejected(status_code=502, detail='对话没有返回可核实的制作作业状态。')
            execution_action = action
            try:
                execution_status = (yield ReadBoundExecution(task_id, status['job']['id'])) if action == 'cancel' else (yield BindExecution(task_id, turn_id, status))
            except TurnRejected as exc:
                if edit_request is None:
                    raise
                execution_action, execution_status = (None, None)
                reply = '修改已保存，可撤销；后续制作回执未通过核验。' + (yield SafeText(str(exc.detail), workspace_id))
            edit_preview = None
        elif isinstance(preview, dict) and preview.get('kind') == 'edit_control':
            action = preview.get('action')
            if action not in {'undo', 'redo', 'reject'} or preview.get('task_id') != task_id:
                raise TurnRejected(status_code=502, detail='没有返回可核验的当前编辑控制结果。')
            timeline_action = action
            edit_preview = None
        elif preview is not None:
            edit_preview = (yield ValidateEditPreview(preview))
            if edit_preview.proposal.task_id != task_id or edit_preview.proposal.workspace_id != workspace_id:
                raise TurnRejected(status_code=502, detail='对话工具返回了不属于当前任务的编辑提案。')
            try:
                actual_proposal = (yield ReadEditProposal(workspace_id, edit_preview.proposal.id))
                actual_intent = (yield ReadEditIntent(workspace_id, actual_proposal.intent_id))
            except KeyError as exc:
                raise TurnRejected(status_code=502, detail='工具提案没有真实持久结果') from exc
            if actual_proposal.task_id != task_id:
                raise TurnRejected(status_code=502, detail='工具提案不属于当前任务')
            edit_preview = (yield RenderEditPreview(actual_intent, actual_proposal))
        else:
            edit_preview = None
        if edit_request is not None and isinstance(edit_request.result, dict):
            from app.assistant.result_projection import execution_readiness_message
            readiness_message = execution_readiness_message(edit_request.result.get('execution_readiness'))
            if readiness_message:
                reply += ' ' + readiness_message
        response = (yield BuildTaskResponse({'quality_continuation': quality_continuation, 'script_window_request': script_window_request, 'storyboard_regeneration': storyboard_regeneration, 'segment_revision': segment_revision, 'timeline_restore': timeline_restore, 'script_tool': script_tool, 'edit_request': edit_request, 'reply': reply, 'provider': (yield ReadSettings(workspace_id)).effective_llm_provider, 'model': (yield ReadSettings(workspace_id)).effective_llm_model, 'edit_preview': edit_preview, 'script_draft': script_draft, 'execution_action': execution_action, 'execution_status': execution_status, 'topic_candidates': topic_candidates, 'timeline_action': timeline_action, 'media_job': media_job, 'turn_id': turn_id, 'draft_version': payload.expected_draft_version + 1}))
        yield ForgetControl(turn_id)
        self._conversations_for().finish_turn(turn_id, 'completed', reply, {'kind': 'task_turn', 'goal_business_actions': self.action_proofs(workspace_id, task_id, turn_id, media_job=media_job, edit_preview=edit_preview, edit_request=edit_request, execution_status=execution_status), 'edit_preview': bool(edit_preview), 'script_draft': script_draft.id if script_draft else None, 'execution_action': execution_action, 'execution_status': execution_status.get('job', {}).get('status') if isinstance(execution_status, dict) and isinstance(execution_status.get('job'), dict) else None, 'topic_candidates': bool(topic_candidates), 'timeline_action': timeline_action, 'storyboard_regeneration': (yield SerializeView(storyboard_regeneration, 'json')) if storyboard_regeneration else None, 'response': (yield SerializeView(response, 'json'))})
        registered_goal = (yield RegisterGoal(turn_id))
        if registered_goal is not None:
            yield BindGoalScope(registered_goal)
        yield WakeContinuations()
        return response

    def action_proofs(self, workspace_id, task_id, turn_id, *, media_job, edit_preview, edit_request, execution_status):
        proofs = []

        def append(kind, identity, source):
            if source.get('assistant_turn_id') == turn_id:
                proofs.append({'kind': kind, 'id': identity, **({'input_id': source['input_id']} if source.get('input_id') else {})})
        if media_job:
            start = self._read_event(workspace_id, f'event_{media_job.job_id}_start').payload
            append('media_job', media_job.job_id, start['arguments'].get('_typed_source', {}))
        if edit_preview:
            intent = self._read_intent(workspace_id, edit_preview.proposal.intent_id)
            append('edit_preview', edit_preview.proposal.id, intent.constraints.get('request_source', {}))
        if edit_request or execution_status:
            for event in self._list_events(workspace_id, task_id):
                if edit_request and event.event_type == 'assistant_edit_request_located' and (event.payload['locator']['request_id'] == str(edit_request.request_id)):
                    append('edit_request', str(edit_request.request_id), event.payload['source'])
                if execution_status and event.event_type == 'assistant_execution_located' and (event.job_id == execution_status['job']['id']):
                    append('execution_status', event.job_id, event.payload['source'])
        return proofs

    def record_window_event(self, event, *, workspace_id, member_id, task_id, turn_id):
        if event.get('type') != 'tool_end' or not isinstance(event.get('script_window_request'), dict):
            return False
        window = event['script_window_request']
        if event.get('invocation_input_id') != (window.get('input_id') or turn_id):
            return False
        from app.assistant.script_window_persistence import record_script_window_receipt
        case = self._window_case_for(workspace_id, member_id)
        return record_script_window_receipt(conversations=self._conversations_for(), turn_id=turn_id, workspace_id=workspace_id, member_id=member_id, task_id=task_id, value=window, read_original=lambda request_id, operation, script_id: case.window_request_result(workspace_id, task_id, request_id, member_id=member_id, operation=operation, script_id=script_id))
