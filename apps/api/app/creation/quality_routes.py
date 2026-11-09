"""Complete quality inspection and original-failure continuation HTTP adapter."""
from fastapi import APIRouter, HTTPException, Query, Request
from app.schemas import CreationTaskQualityCheckRequest, CreationTaskQualityCheckResponse
from app.device_scheduler import RecoveryBlocked
from app.device_runtime import RuntimeUnavailable
from app.schemas import QualityContinuationRequest, QualityContinuationReceipt, QualityContinuationReadResponse

def build_quality_router(*, workspace_for, access_for, request_actor_for, http_error_for, account_error_for, response_type_for, application_for, reader_for, start_for, require_actor_for, projection_for, read_continuation, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/quality-check', response_model=CreationTaskQualityCheckResponse)
    def get_creation_task_quality_check(task_id: str, request: Request) -> CreationTaskQualityCheckResponse:
        workspace_id = workspace_for()(request)
        access_for()(request, workspace_id, task_id)
        try:
            actor = request_actor_for()(request, workspace_id)
        except (http_error_for(), account_error_for()):
            actor = None
        return response_type_for().model_validate(application_for()().read(workspace_id, task_id, reader_for()(request, workspace_id)[0], actor=actor))

    @router.post('/api/creation-tasks/{task_id}/quality-check', response_model=CreationTaskQualityCheckResponse)
    def check_creation_task_quality(task_id: str, request: Request, _payload: CreationTaskQualityCheckRequest) -> CreationTaskQualityCheckResponse:
        workspace_id = workspace_for()(request)
        access_for()(request, workspace_id, task_id)
        start_for()(workspace_id, task_id, actor=require_actor_for()(request, workspace_id), force=True)
        return projection_for()(workspace_id, task_id)

    def _continue_creation_quality_check(request, task_id, payload):
        workspace_id = workspace_for()(request)
        access_for()(request, workspace_id, task_id)
        actor = require_actor_for()(request, workspace_id)
        return application_for()().continue_failed(workspace_id, task_id, payload.job_id, payload.failed_request_id, actor)

    @router.post('/api/creation-tasks/{task_id}/quality-check/continue', response_model=QualityContinuationReceipt)
    def continue_phase(task_id: str, request: Request, payload: QualityContinuationRequest):
        return _response(lambda: _continue_creation_quality_check(request, task_id, payload))

    @router.get('/api/creation-tasks/{task_id}/quality-check/continuation', response_model=QualityContinuationReadResponse)
    def original_continuation(task_id: str, request: Request, job_id: str=Query(min_length=1), failed_request_id: str=Query(min_length=1)):
        return _response(lambda: read_continuation(request, task_id, job_id, failed_request_id))
    router.routes = [route for route in router.routes if route.name in route_names]
    return router

def _response(action):
    try:
        return action()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, '原质检作业不存在或不属于当前任务') from exc
    except (RecoveryBlocked, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
