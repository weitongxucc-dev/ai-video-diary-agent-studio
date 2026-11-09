"""Task media-script capabilities and preview HTTP; submission stages and execution retain their existing owners."""
from fastapi import APIRouter, HTTPException, Request
from app.creation_execution import CreationExecutionActor
from app.creation import media_script_submission_use_case as script_submission
from app.schemas import MediaScriptPreviewRequest, TaskMediaJobResponse

def media_script_submission_source(payload):
    return script_submission.ScriptSource(
        str(payload.request_id) if payload.request_id is not None else None,
        str(payload.assistant_turn_id) if payload.assistant_turn_id is not None else None,
        payload.expected_timeline_version_id, payload.expected_timeline_sequence,
        [item.model_dump(mode="json") for item in payload.requested_outputs],
        [item.model_dump(mode="json") for item in payload.input_refs])

def media_script_submission_command(task_id, payload, *, tool_id=None):
    return script_submission.ScriptSubmission(task_id,
        str(payload.request_id) if payload.request_id is not None else None,
        payload.model_dump(mode="json", exclude_unset=True),
        media_script_submission_source(payload) if tool_id is None else None, tool_id)


class MediaScriptSubmissionHttpAdapter:
    def __init__(self, *, exact_edit_access, current_workspace_id, require_permit_actor,
                 request_permit_actor, auth_enabled, require_assistant_input_origin,
                 creation_task_or_404, script_configuration, creation_runtime, media_result_receipt,
                 bind_recipe_selection, wake):
        self.exact_edit_access = exact_edit_access
        self.current_workspace_id = current_workspace_id
        self.require_permit_actor = require_permit_actor
        self.request_permit_actor = request_permit_actor
        self.auth_enabled = auth_enabled
        self.require_assistant_input_origin = require_assistant_input_origin
        self.creation_task_or_404 = creation_task_or_404
        self.script_configuration = script_configuration
        self.creation_runtime = creation_runtime
        self.media_result_receipt = media_result_receipt
        self.bind_recipe_selection = bind_recipe_selection
        self.wake = wake

    def run(self, request: Request, steps):
        """Answer only the submission owner's explicit access/configuration/queue stages."""
        try:
            try:
                stage = next(steps)
            except StopIteration as result:
                return result.value
            while True:
                try:
                    if isinstance(stage, script_submission.WriteAccess):
                        answer = self.exact_edit_access(stage.task_id, request, write=True)
                    elif isinstance(stage, script_submission.RequireActor):
                        workspace_id = stage.workspace_id if stage.workspace_id is not None else self.current_workspace_id(request)
                        answer = self.require_permit_actor(request, workspace_id)
                    elif isinstance(stage, script_submission.SourceActor):
                        answer = (self.request_permit_actor(request, stage.workspace_id) if self.auth_enabled()
                            else CreationExecutionActor(stage.workspace_id, stage.member_id))
                    elif isinstance(stage, script_submission.InputOrigin):
                        answer = self.require_assistant_input_origin(request, stage.source)
                    elif isinstance(stage, script_submission.ReadWorkspace):
                        answer = self.current_workspace_id(request)
                    elif isinstance(stage, script_submission.ReadTask):
                        answer = self.creation_task_or_404(stage.workspace_id, stage.task_id)
                    elif isinstance(stage, script_submission.ReadDraft):
                        answer = request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact')
                    elif isinstance(stage, script_submission.ReadConfiguration):
                        answer = self.script_configuration()
                    elif isinstance(stage, script_submission.QueueScript):
                        runtime = self.creation_runtime()
                        media = runtime.registrations['creation_job'].owner.task_media
                        if stage.operation == 'media_script_tool_preview':
                            self.bind_recipe_selection(runtime, stage.actor, stage.task_id, stage.arguments)
                        job = media.submit(runtime, stage.actor, stage.task_id, stage.operation, stage.arguments, 'graphic_source')
                        answer = self.media_result_receipt(media, job, stage.actor, runtime)
                    elif isinstance(stage, script_submission.WakeContinuations):
                        answer = self.wake()
                    else:
                        raise TypeError(f"Unsupported media-script submission stage: {type(stage).__name__}")
                except Exception as exc:
                    # Only formerly HTTP-mapped errors participate in the owner's two recorded-error blocks.
                    if isinstance(exc, HTTPException):
                        failure = script_submission.ScriptSubmissionRejected(exc.status_code, exc.detail)
                        failure.__cause__ = exc.__cause__
                    else:
                        failure = exc
                    try:
                        stage = steps.throw(failure)
                    except StopIteration as result:
                        return result.value
                else:
                    try:
                        stage = steps.send(answer)
                    except StopIteration as result:
                        return result.value
        except script_submission.ScriptSubmissionRejected as exc:
            failure = HTTPException(status_code=exc.status_code, detail=exc.detail)
            if exc.__cause__ is not None:
                raise failure from exc.__cause__
            raise failure from None


def build_task_media_script_router(*, access_for, facts_for, command_for, runner_for, submission_for, json_response_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/assistant/media-script-capabilities')
    def get_media_script_capabilities(task_id: str, request: Request):
        task, member_id = access_for()(task_id, request)
        return facts_for()(task, member_id)

    @router.post('/api/creation-tasks/{task_id}/media-scripts/preview', response_model=TaskMediaJobResponse)
    def preview_media_script(task_id: str, request: Request, payload: MediaScriptPreviewRequest):
        command = command_for()(task_id, payload)
        result = runner_for()(request, submission_for()().submit(command))
        return json_response_for()(status_code=result.status_code, content=result.body)
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
