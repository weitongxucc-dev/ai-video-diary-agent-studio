"""Task budget read and original-job resume HTTP; resource receipts and execution control retain original ownership."""
from fastapi import APIRouter, Request
from app.schemas import TaskBudgetReadResponse

def build_task_budget_router(*, workspace_for, production_for, actor_for, http_error_for, automation_guard_for, control_for, runtime_for, control_error_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/budget', response_model=TaskBudgetReadResponse)
    def creation_task_budget(task_id: str, request: Request):
        from app.creation.task_budget_use_case import TaskBudgetUseCase
        ws = workspace_for()(request)
        try:
            return TaskBudgetUseCase(production_for()).read(ws, task_id, actor_for()(request, ws))
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='任务预算不存在') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.post('/api/creation-tasks/{task_id}/budget/resume')
    def resume_creation_task_budget(task_id: str, request: Request, payload: dict):
        from app.creation.task_budget_use_case import TaskBudgetUseCase
        from app.schemas import TaskBudgetResumeRequest
        ws = workspace_for()(request)
        automation_guard_for()(request, ws, task_id)
        try:
            return TaskBudgetUseCase(production_for()).resume(ws, task_id, actor_for()(request, ws), TaskBudgetResumeRequest.model_validate(payload), execution_control=control_for()(), runtime=runtime_for()())
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, control_error_for()) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
