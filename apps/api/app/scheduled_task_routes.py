"""Scheduled-task CRUD HTTP; persistence and validation stay with the service."""
from fastapi import APIRouter, HTTPException, Request
from app.scheduled_tasks import (
    ScheduledTaskCreateRequest, ScheduledTaskEnableRequest, ScheduledTaskResponse,
    ScheduledTasksResponse, ScheduledTaskUpdateRequest,
)


def build_scheduled_task_router(*, service_for, workspace_for) -> APIRouter:
    router = APIRouter()

    @router.get("/api/scheduled-tasks", response_model=ScheduledTasksResponse)
    def list_scheduled_tasks(request: Request) -> ScheduledTasksResponse:
        tasks = service_for().list_tasks(workspace_for(request))
        return ScheduledTasksResponse(tasks=tasks, total_count=len(tasks))


    @router.post(
        "/api/scheduled-tasks",
        response_model=ScheduledTaskResponse,
        status_code=201,
    )
    def create_scheduled_task(request: Request, payload: ScheduledTaskCreateRequest) -> ScheduledTaskResponse:
        try:
            return service_for().create_task(workspace_for(request), payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


    @router.put("/api/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
    def update_scheduled_task(request: Request, task_id: str, payload: ScheduledTaskUpdateRequest) -> ScheduledTaskResponse:
        try:
            return service_for().update_task(workspace_for(request), task_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="定时任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


    @router.patch("/api/scheduled-tasks/{task_id}/enabled", response_model=ScheduledTaskResponse)
    def set_scheduled_task_enabled(request: Request, task_id: str, payload: ScheduledTaskEnableRequest) -> ScheduledTaskResponse:
        try:
            return service_for().set_enabled(workspace_for(request), task_id, payload.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="定时任务不存在") from exc


    @router.delete("/api/scheduled-tasks/{task_id}", status_code=204)
    def delete_scheduled_task(request: Request, task_id: str) -> None:
        try:
            service_for().delete_task(workspace_for(request), task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="定时任务不存在") from exc


    return router
