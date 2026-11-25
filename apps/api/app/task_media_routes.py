"""Task-media job HTTP adapters; persistence and scheduling remain with their original owners."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.creation.media_result_use_case import MediaResultRejected
from app.creation_execution import CreationExecutionActor
from app.device_resource_status import preparation_error_detail
from app.task_media_submission_use_case import TaskMediaSubmissionRejected


def _public_job(media, task_id, job_id, actor):
    try:
        job, event = media.read_public(actor.workspace_id, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='任务媒体作业不存在') from exc
    if job.task_id != task_id or CreationExecutionActor.from_event(job.workspace_id, event) != actor:
        raise HTTPException(status_code=404, detail='任务媒体作业不存在')
    return job


def build_task_media_router(*, workspace_for, actor_for, task_for, list_for, read_for, read_components_for, runtime_for, repository_for, receipt_for, observations_for, output_for=None, file_response_for=None, submit_for=None, route_names) -> APIRouter:
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/media-jobs')
    def submit_task_media_job(task_id: str, request: Request, payload: dict):
        actor = actor_for()(request, workspace_for()(request))
        if submit_for is None:
            raise HTTPException(status_code=404, detail='媒体提交入口不可用')
        try:
            job, runtime = submit_for()(task_id, actor, payload)
            receipt = receipt_for()(runtime.registrations['creation_job'].owner.task_media, job, actor, runtime)
            return JSONResponse(status_code=202, content=receipt)
        except TaskMediaSubmissionRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except HTTPException as exc:
            detail = preparation_error_detail(exc.detail) if exc.status_code == 503 else None
            if detail is None:
                raise
            raise HTTPException(status_code=503, detail=detail) from exc

    @router.get('/api/creation-tasks/{task_id}/media-jobs')
    def list_task_media_jobs(task_id: str, request: Request, cursor: str | None=None, limit: int=20):
        actor = actor_for()(request, workspace_for()(request))
        task = task_for()(actor.workspace_id, task_id)
        try:
            return list_for()(task, actor, cursor=cursor, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='媒体作业分页位置不存在') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/media-jobs/{job_id}')
    def get_task_media_job(task_id: str, job_id: str, request: Request):
        return read_for()(task_id, job_id, request)

    @router.get('/api/creation-tasks/{task_id}/media-jobs/{job_id}/outputs/{output_index}')
    def get_task_media_output(task_id: str, job_id: str, output_index: int, request: Request):
        if output_for is None or file_response_for is None:
            raise HTTPException(status_code=404, detail='媒体输出读取入口不可用')
        try:
            path = output_for()(task_id, job_id, output_index, request)
        except MediaResultRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return file_response_for()(path, media_type='video/mp4', filename=path.name,
                                   content_disposition_type='inline', headers={'Cache-Control': 'no-store'})

    @router.post('/api/creation-tasks/{task_id}/media-jobs/{job_id}/cancel')
    def cancel_task_media_job(task_id: str, job_id: str, request: Request):
        actor = actor_for()(request, workspace_for()(request))
        observed_media, _requests = read_components_for()()
        observed_job = _public_job(observed_media, task_id, job_id, actor)
        if not observed_media.cancellation_pending(observed_job):
            return read_for()(task_id, job_id, request)
        runtime = runtime_for()()
        media = runtime.registrations['creation_job'].owner.task_media
        job = _public_job(media, task_id, job_id, actor)
        media.cancel(runtime, job, actor)
        return receipt_for()(media, job, actor, runtime)

    @router.post('/api/creation-tasks/{task_id}/media-jobs/{job_id}/retry')
    def retry_task_media_job(task_id: str, job_id: str, request: Request):
        actor = actor_for()(request, workspace_for()(request))
        observed_media, _requests = read_components_for()()
        _public_job(observed_media, task_id, job_id, actor)
        runtime = runtime_for()()
        media = runtime.registrations['creation_job'].owner.task_media
        job = _public_job(media, task_id, job_id, actor)
        reason = 'result_invalid' if job.status == 'succeeded' and (not observations_for()().published_outputs_current(job)) else job.status
        if reason not in {'failed', 'cancelled', 'result_invalid'}:
            raise HTTPException(status_code=409, detail='只有失败或已取消的媒体作业可以显式重试')
        with repository_for()._connect() as conn:
            saved = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_id=? AND owner_type='creation_job'", (job.workspace_id, job.id)).fetchone()
        if saved is None:
            raise HTTPException(status_code=409, detail='媒体作业没有可核验的原设备请求')
        try:
            runtime.scheduler.retry_existing(saved['id'], actor, reason=reason)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        runtime.wake()
        receipt = receipt_for()(media, job, actor, runtime)
        receipt['result'] = None
        return JSONResponse(status_code=202, content=receipt)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
