"""Task-media job HTTP adapters; persistence and scheduling remain with their original owners."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from app.creation_execution import CreationExecutionActor

def build_task_media_router(*, workspace_for, actor_for, task_for, list_for, read_for, runtime_for, repository_for, receipt_for, observations_for, route_names) -> APIRouter:
    router = APIRouter()

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

    @router.post('/api/creation-tasks/{task_id}/media-jobs/{job_id}/cancel')
    def cancel_task_media_job(task_id: str, job_id: str, request: Request):
        actor = actor_for()(request, workspace_for()(request))
        runtime = runtime_for()()
        media = runtime.registrations['creation_job'].owner.task_media
        try:
            job, event = media.read(actor.workspace_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='任务媒体作业不存在') from exc
        from app.creation_execution import CreationExecutionActor
        if job.task_id != task_id or CreationExecutionActor.from_event(job.workspace_id, event) != actor:
            raise HTTPException(status_code=404, detail='任务媒体作业不存在')
        if job.status in {'queued', 'running', 'interrupted'}:
            with repository_for()._connect() as conn:
                rows = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_id=? AND owner_type='creation_job' AND state IN ('waiting','blocked','granted','yield_requested')", (job.workspace_id, job.id)).fetchall()
            for row in rows:
                runtime.scheduler.cancel(row['id'], actor)
                runtime.registrations['creation_job'].executor.request_cancel(row['id'])
            repository_for().job_records.finish_job_run(job.workspace_id, job.id, status='cancelled', failure_reason='user_cancelled', impact='当前时间线保持不变。', repair_action='需要时重新安排媒体处理。')
        return receipt_for()(media, job, actor, runtime)

    @router.post('/api/creation-tasks/{task_id}/media-jobs/{job_id}/retry')
    def retry_task_media_job(task_id: str, job_id: str, request: Request):
        actor = actor_for()(request, workspace_for()(request))
        runtime = runtime_for()()
        media = runtime.registrations['creation_job'].owner.task_media
        try:
            job, event = media.read(actor.workspace_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='任务媒体作业不存在') from exc
        if job.task_id != task_id or CreationExecutionActor.from_event(job.workspace_id, event) != actor:
            raise HTTPException(status_code=404, detail='任务媒体作业不存在')
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
