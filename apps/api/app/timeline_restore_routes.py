"""Timeline restore HTTP; durable restore history and execution remain with the original owner."""
from fastapi import APIRouter, Request
from app.schemas import CreationTaskEditConfirmResponse, CreationTaskTimelineRestoreRequest

def build_timeline_restore_router(*, workspace_for, access_for, restore_for, command_type_for, actor_type_for, unavailable_type_for, legacy_type_for, topic_failure_for, invalid_type_for, http_error_for, blocked_type_for, response_for, route_names):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/timeline/undo', response_model=CreationTaskEditConfirmResponse)
    def undo_creation_task_timeline(task_id: str, request: Request, _payload: CreationTaskTimelineRestoreRequest) -> CreationTaskEditConfirmResponse:
        return _restore_creation_task_timeline(task_id, request, redo=False, control=_payload)

    @router.post('/api/creation-tasks/{task_id}/timeline/redo', response_model=CreationTaskEditConfirmResponse)
    def redo_creation_task_timeline(task_id: str, request: Request, _payload: CreationTaskTimelineRestoreRequest) -> CreationTaskEditConfirmResponse:
        return _restore_creation_task_timeline(task_id, request, redo=True, control=_payload)

    def _restore_creation_task_timeline(task_id: str, request: Request, *, redo: bool, control: CreationTaskTimelineRestoreRequest) -> CreationTaskEditConfirmResponse:
        workspace_id = workspace_for()(request)
        task, member_id = access_for()(task_id, request, write=True)
        source = {'assistant_turn_id': str(control.assistant_turn_id), 'input_id': request.headers.get('x-assistant-input-id')} if control.assistant_turn_id is not None else None
        case = restore_for()()
        try:
            outcome = case.execute(command_type_for()(workspace_id, task, redo), actor=actor_type_for()(workspace_id, member_id), request_id=control.request_id, target_operation_id=control.target_operation_id, expected_timeline_version_id=control.expected_timeline_version_id, expected_timeline_sequence=control.expected_timeline_sequence, source=source)
        except (unavailable_type_for(), legacy_type_for()) as exc:
            raise topic_failure_for()(**exc.details) from exc
        except invalid_type_for() as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError, blocked_type_for()) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
        return response_for()(outcome)

    @router.get('/api/creation-tasks/{task_id}/timeline/restore-requests/{request_id}')
    def get_creation_task_timeline_restore_request(task_id: str, request_id: str, request: Request):
        task, member_id = access_for()(task_id, request)
        try:
            return restore_for()().read_request(task.workspace_id, task_id, request_id, actor_type_for()(task.workspace_id, member_id))
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='原恢复请求不存在，不能据此推定未提交') from exc
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
