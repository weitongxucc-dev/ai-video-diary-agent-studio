"""Task media-script capabilities and preview HTTP; submission stages and execution retain their existing owners."""
from fastapi import APIRouter, Request
from app.schemas import MediaScriptPreviewRequest, TaskMediaJobResponse

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
