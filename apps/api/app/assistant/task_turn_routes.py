"""Task-assistant HTTP/SDK adapters for the complete named turn workflow.

Request, transport credentials, schema rendering and SDK callbacks stay here.
Only explicit owner/access ports are assembled by the application host.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.assistant import turn_admission_use_case as admission
from app.assistant import task_turn_use_case as task
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.creation.native_task_operations import NativeOperationHandler
from app.assistant.input_delivery import AssistantInputDelivery
from app.assistant.result_projection import (
    resolve_quality_continuation, resolve_segment_revision, resolve_timeline_restore,
)
from app.assistant.script_window_receipts import resolve_script_window_request
from app.schemas import (
    AssistantConversationStopRequest, CreationTaskAssistantTurnRequest,
    CreationTaskAssistantTurnResponse, TaskMediaJobResponse,
    CreationTaskScriptResponse, CreationTaskTopicsResponse,
    CreationTaskEditPreviewResponse, AssistantScriptWindowRequestResponse,
)


class TaskTurnHTTPAdapter:
    def __init__(self, *, conversations_for, window_case_for, workspace_for, read_task, read_task_record, read_caller, content_visible, read_member, conversation_owner, member_scope, clean_text, safe_text, validate_reference, profile_snapshot, knowledge_snapshot, access_task, read_subtitles, resolve_scope, read_timeline, timeline_for_task, clip_material, material_snapshot, settings_for, read_goal_plan, goal_shutting_down, runtime_for, local_token, request_actor, require_actor, native_handler, run_task, register_control, forget_control, stop_control, stream_for, applied_edit, storyboard, script_tool, related_media, edit_request, media_binding, completed_media, read_media, read_script, bound_execution, bind_observation, bind_execution, read_proposal, read_intent, render_edit_preview, read_event, list_events, goal_for, bind_goal_scope, wake, read_quality, read_window, read_segment, read_restore, generation_for):
        self._conversations_for = conversations_for
        self._window_case_for = window_case_for
        self._workspace_for = workspace_for
        self._read_task = read_task
        self._read_task_record = read_task_record
        self._read_caller = read_caller
        self._content_visible = content_visible
        self._read_member = read_member
        self._conversation_owner = conversation_owner
        self._member_scope = member_scope
        self._clean_text = clean_text
        self._safe_text = safe_text
        self._validate_reference = validate_reference
        self._profile_snapshot = profile_snapshot
        self._knowledge_snapshot = knowledge_snapshot
        self._access_task = access_task
        self._read_subtitles = read_subtitles
        self._resolve_scope = resolve_scope
        self._read_timeline = read_timeline
        self._timeline_for_task = timeline_for_task
        self._clip_material = clip_material
        self._material_snapshot = material_snapshot
        self._settings_for = settings_for
        self._read_goal_plan = read_goal_plan
        self._goal_shutting_down = goal_shutting_down
        self._runtime_for = runtime_for
        self._local_token = local_token
        self._request_actor = request_actor
        self._require_actor = require_actor
        self._native_handler = native_handler
        self._run_task = run_task
        self._register_control = register_control
        self._forget_control = forget_control
        self._stop_control = stop_control
        self._stream_for = stream_for
        self._applied_edit = applied_edit
        self._storyboard = storyboard
        self._script_tool = script_tool
        self._related_media = related_media
        self._edit_request = edit_request
        self._media_binding = media_binding
        self._completed_media = completed_media
        self._read_media = read_media
        self._read_script = read_script
        self._bound_execution = bound_execution
        self._bind_observation = bind_observation
        self._bind_execution = bind_execution
        self._read_proposal = read_proposal
        self._read_intent = read_intent
        self._render_edit_preview = render_edit_preview
        self._read_event = read_event
        self._list_events = list_events
        self._goal_for = goal_for
        self._bind_goal_scope = bind_goal_scope
        self._wake = wake
        self._read_quality = read_quality
        self._read_window = read_window
        self._read_segment = read_segment
        self._read_restore = read_restore
        self._generation_for = generation_for

    def case(self):
        return task.TaskAssistantTurnUseCase(conversations_for=self._conversations_for,
                                             window_case_for=self._window_case_for, read_event=self._read_event,
                                             read_intent=self._read_intent, list_events=self._list_events)

    def _admission(self):
        return admission.TurnAdmissionUseCase(conversations_for=self._conversations_for)

    @staticmethod
    def command(task_id, payload):
        values = payload.model_dump(mode="python")
        if values["selected_profile_reference"] is not None:
            values["selected_profile_reference"] = task.ProfileReference(**values["selected_profile_reference"])
        if values["selected_knowledge_reference"] is not None:
            values["selected_knowledge_reference"] = task.KnowledgeReference(**values["selected_knowledge_reference"])
        return task.TaskTurnCommand(task_id=task_id, **values)

    def run_goal(self, claim, fresh):
        return self._drive(self.case().run_goal(claim, fresh), None)

    def finish_goal(self, business, claim, reply, preview):
        return self._drive(self.case().finish_goal(business, claim, reply, preview), None)

    def execute(self, task_id, request, payload, on_event=None):
        return self._drive(self.case().turn(self.command(task_id, payload)), request, on_event=on_event)

    def finish(self, task_id, request, payload, turn_id, reply, preview, trusted_stage):
        return self._drive(self.case().finish(task_id, payload, turn_id, reply, preview, trusted_stage), request)

    def begin(self, request, conversation_id, scope_kind, scope_id, message, client_request_id,
              expected_draft_version, request_context, on_event, *, expected_reference_version=None):
        return self._drive(self._admission().begin(conversation_id, scope_kind, scope_id, message,
            client_request_id, expected_draft_version, request_context,
            expected_reference_version=expected_reference_version), request, on_event=on_event)

    def input_context(self, request, context, reference):
        return self._drive(self._admission().freeze_input(context, reference), request)

    def frozen_references(self, request, conversation_id, turn_id):
        workspace_id, member_id = self._member_scope(request)
        context = self._conversations_for().consumed_turn_request_context(
            turn_id, conversation_id, workspace_id, member_id)
        return (context.get("target_snapshot") or {}).get("selected_references", [])

    def input_delivery(self, request, conversation_id, turn_id):
        workspace_id, member_id, _conversation = self._conversation_owner(request, conversation_id)

        def verify(item):
            return self._drive(self._admission().verify_input(item,
                conversation_id=conversation_id, owner=(workspace_id, member_id), turn_id=turn_id), request)

        return AssistantInputDelivery(self._conversations_for(), conversation_id=conversation_id,
            workspace_id=workspace_id, member_id=member_id, turn_id=turn_id, verify=verify)

    def replay(self, turn_id, response_model, *, provider, model, draft_version):
        return self._drive(self._admission().replay(turn_id, provider=provider, model=model,
            draft_version=draft_version), None, response_model=response_model)

    def stop(self, conversation_id: str, request: Request, payload: AssistantConversationStopRequest):
        return self._drive(self._admission().stop(conversation_id, str(payload.turn_id)), request)

    def task_turn(self, task_id: str, request: Request,
                  payload: CreationTaskAssistantTurnRequest) -> CreationTaskAssistantTurnResponse:
        if "text/event-stream" in request.headers.get("accept", "").lower():
            workspace_id = self._workspace_for(request)
            return self._stream_for().response(
                lambda on_event: self.execute(task_id, request, payload, on_event), workspace_id=workspace_id)
        return self.execute(task_id, request, payload, None)

    def _drive(self, operation, request, *, on_event=None, response_model=CreationTaskAssistantTurnResponse):
        try:
            stage = next(operation)
            while True:
                try:
                    result = self._answer(stage, request, on_event, response_model)
                except HTTPException as exc:
                    stage = operation.throw(admission.TurnRejected(exc.status_code, exc.detail))
                except Exception as exc:
                    stage = operation.throw(exc)
                else:
                    stage = operation.send(result)
        except StopIteration as done:
            return done.value
        except admission.TurnRejected as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    def _run(self, stage, request, on_event):
        def record_event(event):
            self.case().record_window_event(event, workspace_id=stage.workspace_id,
                member_id=stage.member_id, task_id=stage.task_id, turn_id=stage.turn_id)
            if on_event is not None:
                on_event(event)

        native_actor = self._request_actor(request, stage.workspace_id)
        native_binding = {'workspace_id':stage.workspace_id, 'member_id':stage.member_id,
            'task_id':stage.task_id, 'conversation_id':stage.conversation_id,
            'turn_id':stage.turn_id, 'input_id':None, 'task_version':stage.task_version}
        native_handler = self._native_handler(native_actor, native_binding) if native_actor is not None else None
        return self._run_task(self._settings_for(stage.workspace_id),
            workspace_id=stage.workspace_id,
            local_member_token=self._local_token(request) or None,
            caller_user_id=stage.caller_user_id if not stage.local_diagnostic else None,
            task_id=stage.task_id, conversation_id=stage.conversation_id,
            message=stage.message, stage=stage.stage, selected_refs=stage.selected_refs,
            playhead_ms=stage.playhead_ms,
            authorization=request.headers.get('authorization'), cookie=request.headers.get('cookie'),
            on_event=record_event, turn_id=stage.turn_id,
            native_handler=native_handler, source_binding=native_binding,
            selected_references=self.frozen_references(request, stage.conversation_id, stage.turn_id),
            input_handler=self.input_delivery(request, stage.conversation_id, stage.turn_id),
            profile_reference=stage.profile_reference, knowledge_reference=stage.knowledge_reference,
            task_version=stage.task_version, material_selection=stage.material_selection)

    def _answer(self, stage, request, on_event, response_model):
        if isinstance(stage, admission.ReadWorkspace):
            return self._workspace_for(request)
        if isinstance(stage, admission.ReadConversationOwner):
            return self._conversation_owner(request, stage.conversation_id)
        if isinstance(stage, admission.CleanText):
            return self._clean_text(stage.value, stage.workspace_id)
        if isinstance(stage, admission.ValidateReference):
            return self._validate_reference(request, stage.reference)
        if isinstance(stage, admission.ProfileSnapshot):
            return self._profile_snapshot(request, stage.kind, stage.profile_id, stage.version)
        if isinstance(stage, admission.KnowledgeSnapshot):
            return self._knowledge_snapshot(request, stage.knowledge_item_id, stage.version, stage.locator)
        if isinstance(stage, admission.AccessTask):
            return (self._access_task(stage.task_id, request, write=True) if stage.write
                    else self._access_task(stage.task_id, request))
        if isinstance(stage, admission.ReadSubtitleTracks):
            return self._read_subtitles(stage.workspace_id, stage.task_id)
        if isinstance(stage, admission.ResolveTurnScope):
            return self._resolve_scope(stage.workspace_id, stage.task, stage.selection)
        if isinstance(stage, admission.ReadTimelineForTask):
            return self._timeline_for_task(stage.workspace_id, stage.task_id, stage.version_id)
        if isinstance(stage, admission.ReadTaskClipMaterial):
            return self._clip_material(request, stage.task, stage.selected_refs)
        if isinstance(stage, admission.ReadMaterialSnapshot):
            return self._material_snapshot(request, stage.selection)
        if isinstance(stage, admission.RegisterControl):
            return self._register_control(stage.turn_id)
        if isinstance(stage, admission.EmitTurnEvent):
            if on_event is not None:
                return on_event(stage.event)
            return None
        if isinstance(stage, admission.RenderReplay):
            return response_model.model_validate(stage.value)
        if isinstance(stage, admission.RenderRunningTurn):
            return response_model(reply=stage.reply, provider=stage.provider, model=stage.model,
                turn_id=stage.turn_id, turn_status='running', draft_version=stage.draft_version)
        if isinstance(stage, admission.RequestGenerationStop):
            return self._generation_for(stage.workspace_id).request_stop(
                stage.workspace_id, stage.task_id, stage.request_id, stage.member_id)
        if isinstance(stage, admission.StopControl):
            return self._stop_control(stage.turn_id)
        if isinstance(stage, admission.InvalidateGoal):
            return self._goal_for().invalidate(stage.turn_id, stage.reason)
        if isinstance(stage, task.ReadTask):
            return self._read_task(stage.workspace_id, stage.task_id)
        if isinstance(stage, task.ReadTaskRecord):
            return self._read_task_record(stage.workspace_id, stage.task_id)
        if isinstance(stage, task.ReadTimeline):
            return self._read_timeline(stage.workspace_id, stage.version_id)
        if isinstance(stage, task.ReadCaller):
            return self._read_caller(request, stage.workspace_id)
        if isinstance(stage, task.TaskVisible):
            return self._content_visible(request, stage.workspace_id, stage.visibility, stage.owner_user_id)
        if isinstance(stage, task.ReadMember):
            return self._read_member(request, stage.workspace_id)
        if isinstance(stage, task.ReadGoalPlan):
            return self._read_goal_plan(stage.workspace_id, stage.task)
        if isinstance(stage, task.GoalShuttingDown):
            return self._goal_shutting_down()
        if isinstance(stage, task.GoalStream):
            return self._stream_for().sanitizer(stage.workspace_id), lambda value: self._safe_text(value, stage.workspace_id)
        if isinstance(stage, task.RunGoalTask):
            goal, business = stage.goal, stage.business
            host = TaskBusinessAdapter(business)
            native = NativeOperationHandler(business.native_case, stage.native_actor, None, self._runtime_for,
                delegate=host.native_call)
            return self._run_task(self._settings_for(goal['workspace_id']), workspace_id=goal['workspace_id'],
                caller_user_id=goal['member_id'], task_id=goal['task_id'], conversation_id=goal['conversation_id'],
                message=stage.message, stage=stage.stage, selected_refs=stage.selected_refs,
                playhead_ms=stage.playhead_ms, on_event=stage.on_event, turn_id=stage.turn_id,
                business_handler=host, native_handler=native, source_binding=stage.binding,
                input_handler=business.input_delivery(stage.binding), profile_reference=stage.facts.get('profile_reference'),
                knowledge_reference=stage.facts.get('knowledge_reference'), task_version=stage.task_version,
                material_selection=None, selected_references=[], continuation_context=stage.continuation_context)
        if isinstance(stage, task.ReadSettings):
            return self._settings_for(stage.workspace_id)
        if isinstance(stage, task.SafeText):
            return self._safe_text(stage.value, stage.workspace_id)
        if isinstance(stage, task.RequireActor):
            return self._require_actor(request, stage.workspace_id)
        if isinstance(stage, task.BeginTurn):
            return self.begin(request, stage.conversation_id, stage.scope_kind, stage.scope_id,
                stage.message, stage.client_request_id, stage.expected_draft_version,
                stage.request_context, on_event, expected_reference_version=stage.expected_reference_version)
        if isinstance(stage, task.ReplayTurn):
            return self.replay(stage.turn_id, CreationTaskAssistantTurnResponse,
                provider=stage.provider, model=stage.model, draft_version=stage.draft_version)
        if isinstance(stage, task.RunTask):
            return self._run(stage, request, on_event)
        if isinstance(stage, task.BuildTaskResponse):
            return CreationTaskAssistantTurnResponse(**stage.fields)
        if isinstance(stage, task.SerializeView):
            return stage.value.model_dump(mode=stage.mode)
        if isinstance(stage, task.ForgetControl):
            return self._forget_control(stage.turn_id)
        if isinstance(stage, task.ReadAppliedEdit):
            return self._applied_edit(stage.task_id, request, stage.turn_id, stage.details)
        if isinstance(stage, task.ResolveQuality):
            return resolve_quality_continuation(stage.preview, task_id=stage.task_id,
                invocation_input_id=stage.invocation_input_id,
                read_original=lambda job_id, failed_request_id: self._read_quality(
                    request, stage.task_id, job_id, failed_request_id))
        if isinstance(stage, task.ResolveWindow):
            return resolve_script_window_request(stage.preview, task_id=stage.task_id,
                read_original=lambda request_id, operation, script_id: self._read_window(
                    request, stage.task_id, request_id, operation, script_id))
        if isinstance(stage, task.ValidateWindow):
            return AssistantScriptWindowRequestResponse.model_validate(stage.value)
        if isinstance(stage, task.ResolveSegmentRevision):
            return resolve_segment_revision(stage.preview, task_id=stage.task_id, input_id=stage.input_id,
                read_original=lambda request_id: self._read_segment(
                    stage.workspace_id, stage.task_id, request_id, stage.actor))
        if isinstance(stage, task.ResolveTimelineRestore):
            return resolve_timeline_restore(stage.preview, task_id=stage.task_id,
                read_original=lambda request_id: self._read_restore(
                    stage.workspace_id, stage.task_id, request_id, stage.actor))
        if isinstance(stage, task.ResolveStoryboard):
            return self._storyboard(stage.task_id, request, stage.turn_id, stage.details)
        if isinstance(stage, task.ResolveScriptTool):
            return self._script_tool(stage.task_id, request, stage.turn_id, stage.details)
        if isinstance(stage, task.RelatedMedia):
            return self._related_media(stage.task_id, request, stage.turn_id, stage.conversation_id, stage.details)
        if isinstance(stage, task.ResolveEditRequest):
            return self._edit_request(stage.task_id, request, stage.turn_id, stage.details)
        if isinstance(stage, task.SelectMediaFinish):
            return self._media_binding().select_finish(stage.turn_id, stage.value)
        if isinstance(stage, task.CompletedMedia):
            return self._completed_media(stage.task, request, stage.turn_id, stage.slot,
                contract=stage.contract, input_refs=stage.input_refs)
        if isinstance(stage, task.ReadMedia):
            return self._read_media(stage.task_id, stage.job_id, request)
        if isinstance(stage, task.ValidateMedia):
            return TaskMediaJobResponse.model_validate(stage.value)
        if isinstance(stage, task.ReadScript):
            return self._read_script(stage.workspace_id, stage.script_id)
        if isinstance(stage, task.ValidateScript):
            return CreationTaskScriptResponse.model_validate(stage.value)
        if isinstance(stage, task.ValidateTopics):
            return CreationTaskTopicsResponse.model_validate(stage.value)
        if isinstance(stage, task.ReadBoundExecution):
            return self._bound_execution(stage.task_id, request, stage.job_id)
        if isinstance(stage, task.BindObservation):
            return self._bind_observation(stage.task_id, request, stage.turn_id, stage.details, stage.result)
        if isinstance(stage, task.BindExecution):
            return self._bind_execution(stage.task_id, request, stage.turn_id, stage.status)
        if isinstance(stage, task.ValidateEditPreview):
            return CreationTaskEditPreviewResponse.model_validate(stage.value)
        if isinstance(stage, task.ReadEditProposal):
            return self._read_proposal(stage.workspace_id, stage.proposal_id)
        if isinstance(stage, task.ReadEditIntent):
            return self._read_intent(stage.workspace_id, stage.intent_id)
        if isinstance(stage, task.RenderEditPreview):
            return self._render_edit_preview(stage.intent, stage.proposal)
        if isinstance(stage, task.RegisterGoal):
            return self._goal_for().register_turn(stage.turn_id)
        if isinstance(stage, task.BindGoalScope):
            return self._bind_goal_scope(request, stage.goal)
        if isinstance(stage, task.WakeContinuations):
            return self._wake()
        raise TypeError(f'Unsupported task-turn checkpoint: {type(stage).__name__}')


def build_task_turn_router(adapter_for):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/assistant/turn', response_model=CreationTaskAssistantTurnResponse)
    def creation_task_assistant_turn(task_id: str, request: Request, payload: CreationTaskAssistantTurnRequest):
        return adapter_for().task_turn(task_id, request, payload)

    @router.post('/api/assistant/conversations/{conversation_id}/stop')
    def stop_assistant_conversation_turn(conversation_id: str, request: Request, payload: AssistantConversationStopRequest):
        return adapter_for().stop(conversation_id, request, payload)

    return router
