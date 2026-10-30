"""Read-only native task operation receipts and artifacts; native operations and qualification remain host-owned."""
from fastapi import APIRouter, Query, Request

def build_native_task_read_router(*, workspace_for, actor_for, http_error_for, case_for, file_response_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/native-operations')
    def creation_task_native_operations(task_id: str, request: Request, offset: int=Query(0, ge=0), limit: int=Query(30, ge=1, le=100)):
        ws = workspace_for()(request)
        actor = actor_for()(request, ws)
        if actor is None:
            raise http_error_for()(status_code=403, detail='缺少原任务成员身份')
        try:
            return case_for()().list_operations(actor, task_id, offset=offset, limit=limit)
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/native-artifacts/{artifact_id}/content')
    def creation_task_native_artifact(task_id: str, artifact_id: str, request: Request):
        ws = workspace_for()(request)
        actor = actor_for()(request, ws)
        if actor is None:
            raise http_error_for()(status_code=403, detail='缺少原任务成员身份')
        try:
            from app.creation.native_task_operations import actual_mime
            path = case_for()().artifact_path(actor, task_id, artifact_id)
            return file_response_for()(path, media_type=actual_mime(path), headers={'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'"})
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, OSError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
