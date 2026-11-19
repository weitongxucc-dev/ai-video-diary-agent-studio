"""Task person render HTTP; generation, projections and authorized media retain their original owners."""
from datetime import datetime

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse
from app.schemas import CreationTaskPersonRenderRequest, CreationTaskPersonRenderResponse, CreationTaskPersonRendersResponse

class PresenterSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_id: str = Field(min_length=1)
    expected_script_updated_at: datetime
    expected_plan_updated_at: datetime
    section_id: str | None = Field(min_length=1)


def build_task_person_render_router(*, workspace_for, task_for, production_for, access_for, require_task_open, list_response_for, render_response_for, generate_for, readable_for, http_error_for, media_path_for, file_response_for, route_names):
    router = APIRouter()

    @router.put('/api/creation-tasks/{task_id}/presenter-selection', status_code=204)
    def save_creation_task_presenter_selection(task_id: str, payload: PresenterSelectionRequest, request: Request) -> Response:
        task, member_id = access_for()(task_id, request, write=True)
        require_task_open()(request, task.workspace_id, task_id)
        try:
            production_for().presenter_selection.save(task.workspace_id, task.id,
                member_id=member_id, **payload.model_dump())
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail="当前任务或确认文案不存在。") from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @router.get('/api/creation-tasks/{task_id}/person-renders', response_model=CreationTaskPersonRendersResponse)
    def list_creation_task_person_renders(task_id: str, request: Request) -> CreationTaskPersonRendersResponse:
        workspace_id = workspace_for()(request)
        task_for()(workspace_id, task_id)
        items = production_for().person_renders.list_person_renders(workspace_id, task_id)
        return list_response_for()(items=[render_response_for()(item) for item in items], total_count=len(items))

    @router.post('/api/creation-tasks/{task_id}/person-renders/generate', response_model=CreationTaskPersonRenderResponse)
    def generate_creation_task_person_render(task_id: str, payload: CreationTaskPersonRenderRequest, request: Request) -> CreationTaskPersonRenderResponse:
        render = generate_for()(workspace_for()(request), task_id, payload)
        return render_response_for()(render)

    @router.get('/api/creation-tasks/{task_id}/person-renders/{render_id}/media')
    def get_creation_task_person_render_media(task_id: str, render_id: str, request: Request) -> FileResponse:
        workspace_id = workspace_for()(request)
        readable_for()(request, task_id)
        try:
            render = production_for().person_renders.get_person_render(workspace_id, task_id, render_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='person render not found') from exc
        output = media_path_for()(render)
        if output is None:
            raise http_error_for()(status_code=409, detail='person render is not ready')
        return file_response_for()(output, media_type='video/mp4', filename=f'{render.id}.mp4')
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
