"""Storyboard regeneration HTTP; submission, original receipts and execution keep their original owners."""
from uuid import UUID
from fastapi import APIRouter, Request
from app.schemas import StoryboardRegenerationRequest

def build_storyboard_regeneration_router(*, workspace_for, access_for, adoption_for, actor_for, runtime_for, control_for, control_error_for, http_error_for):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/montage/plan', status_code=202)
    def create_creation_task_montage_plan(task_id: str, request: Request, payload: StoryboardRegenerationRequest) -> dict[str, object]:
        workspace_id = workspace_for()(request)
        access_for()(task_id, request, write=True)
        if payload.assistant_turn_id is not None:
            adoption_for()(task_id, request, str(payload.assistant_turn_id), draft_only=True)
        actor = actor_for()(request, workspace_id)
        runtime = runtime_for()()
        try:
            return control_for()().regenerate_storyboard(workspace_id, task_id, payload, actor, runtime)
        except control_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get('/api/creation-tasks/{task_id}/montage/plan/requests/{request_id}')
    def read_storyboard_regeneration(task_id: str, request_id: UUID, request: Request) -> dict[str, object]:
        workspace_id = workspace_for()(request)
        access_for()(task_id, request)
        actor = actor_for()(request, workspace_id)
        try:
            return control_for()().read_storyboard_regeneration(workspace_id, task_id, str(request_id), actor)
        except control_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
    return router
