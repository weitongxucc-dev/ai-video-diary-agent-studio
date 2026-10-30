"""Task listing, workspace and visibility HTTP; reads, projections and persistence retain original owners."""
from fastapi import APIRouter, Request
from app.schemas import CreationTasksResponse, CreationTaskWorkspaceResponse, ContentVisibilityRequest

def build_task_workspace_router(*, workspace_for, reader_for, production_for, private_for, list_response_for, summary_for, write_guard_for, http_error_for, workspace_response_for, task_for, readable_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks', response_model=CreationTasksResponse)
    def list_creation_tasks(request: Request) -> CreationTasksResponse:
        workspace_id = workspace_for()(request)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        tasks = production_for().task_records.list_creation_tasks(workspace_id, include_private=private_for()(request), viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return list_response_for()(items=[summary_for()(production_for(), item).summary for item in tasks], total_count=len(tasks))

    @router.put('/api/creation-tasks/{task_id}/visibility', response_model=CreationTaskWorkspaceResponse)
    def set_creation_task_visibility_route(task_id: str, request: Request, payload: ContentVisibilityRequest) -> CreationTaskWorkspaceResponse:
        """显式共享或收回一条创作任务；响应回读同一列。"""
        denied = write_guard_for()(request)
        if denied is not None:
            return denied
        workspace_id = workspace_for()(request)
        try:
            production_for().task_records.set_creation_task_visibility(workspace_id, task_id, payload.visibility)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation task not found') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return workspace_response_for()(workspace_id, task_for()(workspace_id, task_id))

    @router.get('/api/creation-tasks/{task_id}', response_model=CreationTaskWorkspaceResponse)
    def get_creation_task_workspace(task_id: str, request: Request) -> CreationTaskWorkspaceResponse:
        workspace_id = workspace_for()(request)
        return workspace_response_for()(workspace_id, readable_for()(request, task_id))
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
