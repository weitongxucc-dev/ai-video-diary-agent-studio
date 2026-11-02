"""HTTP adapters for creation execution; persistence stays with the existing owners."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.creation.contracts import JobRun
from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
from app.creation_execution import ExecutionStartRequest
from app.device_scheduler import RecoveryBlocked
from app.job_progress import summarize_job_progress
from app.schemas import (
    JobProgressResponse,
    SegmentVisualDecisionAdoptRequest,
    TaskJobProgressResponse,
)


def build_creation_execution_router(
    *,
    control_for: Callable[[], CreationExecutionControl],
    workspace_for: Callable[[Request], str],
    readable_task: Callable[[Request, str], object],
    task_for: Callable[[str, str], object],
    request_actor: Callable[[Request, str], object | None],
    require_actor: Callable[[Request, str], object],
    require_task_open: Callable[[Request, str, str], None],
    runtime_for_execution: Callable[[], object],
    current_runtime: Callable[[], object | None],
    list_job_runs: Callable[[str, str], list[JobRun]],
    assistant_source: Callable[
        [str, Request, UUID | None, str, str | None],
        tuple[dict[str, object] | None, dict[str, object] | None],
    ],
    bind_assistant_result: Callable[..., dict[str, object]],
    read_bound_result: Callable[[str, Request, str], dict[str, object]],
) -> APIRouter:
    """Bind finite HTTP ports without owning a runtime or importing the assembly.

    The three assistant ports still belong to the host's existing source/receipt
    orchestration. They are not new business implementations in this adapter.
    Runtime acquisition may start execution; current_runtime only observes it.
    """
    router = APIRouter()

    def execution_status(workspace_id: str, task_id: str, *, actor=None) -> dict[str, object]:
        try:
            return control_for().status(
                workspace_id, task_id, verify_media=current_runtime() is None, actor=actor)
        except ExecutionControlRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/creation-tasks/{task_id}/execution/preview")
    def preview_creation_execution(task_id: str, request: Request) -> dict[str, object]:
        readable_task(request, task_id)
        try:
            return control_for().preview(workspace_for(request), task_id)
        except ExecutionControlRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/api/creation-tasks/{task_id}/execution")
    def get_creation_execution(task_id: str, request: Request, job_id: str | None = None) -> dict[str, object]:
        readable_task(request, task_id)
        if job_id is not None:
            try:
                return read_bound_result(task_id, request, job_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="原制作作业不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return execution_status(workspace_for(request),task_id,
            actor=request_actor(request,workspace_for(request)))

    @router.get('/api/creation-tasks/{task_id}/execution/jobs/{job_id}/sections/{section_id}/visual-decision')
    def get_segment_visual_decision(task_id: str, job_id: str, section_id: str, request: Request,
            offset: int=Query(default=0,ge=0),limit: int=Query(default=8,ge=1,le=8)):
        readable_task(request,task_id)
        ws=workspace_for(request)
        try:
            return control_for().read_visual_decision(ws,task_id,job_id,section_id,
                require_actor(request,ws),offset=offset,limit=limit)
        except PermissionError as exc:
            raise HTTPException(status_code=403,detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404,detail='原画面决定不存在') from exc
        except ValueError as exc:
            raise HTTPException(status_code=409,detail=str(exc)) from exc

    @router.post('/api/creation-tasks/{task_id}/execution/jobs/{job_id}/sections/{section_id}/visual-decision')
    def adopt_segment_visual_decision(task_id: str, job_id: str, section_id: str, request: Request,
            payload: SegmentVisualDecisionAdoptRequest):
        readable_task(request,task_id)
        ws=workspace_for(request)
        require_task_open(request,ws,task_id)
        try:
            return control_for().adopt_visual_decision(ws,task_id,job_id,section_id,
                require_actor(request,ws),payload,runtime=runtime_for_execution())
        except ExecutionControlRejected as exc:
            raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403,detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404,detail='原画面决定不存在') from exc
        except (ValueError,OSError,RecoveryBlocked) as exc:
            raise HTTPException(status_code=409,detail=str(exc)) from exc

    @router.get(
        "/api/creation-tasks/{task_id}/job-runs",
        response_model=TaskJobProgressResponse,
    )
    def get_task_job_progress(task_id: str, request: Request) -> TaskJobProgressResponse:
        """任务作业进度：只读后台已经落库的作业行。

        进度、已耗时、更新时间和失败原因都来自这些记录；重开应用读到的是同一批
        作业，页面关闭不改变它们。等待与处理估时分开，没有可信样本就是未知。
        """
        workspace_id = workspace_for(request)
        readable_task(request, task_id)
        try:
            runs = list_job_runs(workspace_id, task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="creation task not found") from exc
        items, current = summarize_job_progress(runs, now=datetime.now(UTC))
        return TaskJobProgressResponse(
            task_id=task_id,
            current=JobProgressResponse.model_validate(current, from_attributes=True)
            if current
            else None,
            items=[JobProgressResponse.model_validate(item, from_attributes=True) for item in items],
            total_count=len(items),
        )

    @router.post("/api/creation-tasks/{task_id}/execution/start", status_code=202)
    def start_creation_execution(task_id: str, request: Request, payload: ExecutionStartRequest,
                                 assistant_turn_id: UUID | None = None) -> dict[str, object]:
        workspace_id = workspace_for(request)
        require_task_open(request,workspace_id,task_id)
        if not payload.requirements_confirmed:
            raise HTTPException(status_code=409, detail="请先确认实际文案与本次制作要求")
        source, replay = assistant_source(task_id, request, assistant_turn_id, "start", payload.input_digest)
        if replay is not None:
            return replay
        runtime = runtime_for_execution()
        actor = require_actor(request, workspace_id)
        try:
            result = control_for().start(workspace_id, task_id, payload.script_id, payload.input_digest, actor, runtime, verify_media=current_runtime() is None)
        except ExecutionControlRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return bind_assistant_result(task_id, request, str(assistant_turn_id), result, source=source) if source else result

    @router.post("/api/creation-tasks/{task_id}/execution/resume", status_code=202)
    def resume_creation_execution(task_id: str, request: Request, assistant_turn_id: UUID | None = None) -> dict[str, object]:
        workspace_id = workspace_for(request)
        require_task_open(request,workspace_id,task_id)
        task_for(workspace_id, task_id)
        source, replay = assistant_source(task_id, request, assistant_turn_id, "resume", None)
        if replay is not None:
            return replay
        runtime = runtime_for_execution()
        actor = require_actor(request, workspace_id)
        try:
            result = control_for().resume(workspace_id, task_id, actor, runtime, verify_media=current_runtime() is None)
        except ExecutionControlRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return bind_assistant_result(task_id, request, str(assistant_turn_id), result, source=source) if source else result

    @router.post("/api/creation-tasks/{task_id}/execution/cancel")
    def cancel_creation_execution(task_id: str, request: Request) -> dict[str, object]:
        workspace_id = workspace_for(request)
        task_for(workspace_id, task_id)
        # 取消不要求整机运行时在场：没有许可通道时也必须能把已经发起的作业停下来。
        runtime = current_runtime()
        actor = request_actor(request, workspace_id)
        if actor is None:
            raise HTTPException(status_code=403, detail="缺少可核查的原制作身份，不能取消")
        try:
            return control_for().cancel(workspace_id, task_id, actor, runtime, verify_media=current_runtime() is None)
        except ExecutionControlRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router
