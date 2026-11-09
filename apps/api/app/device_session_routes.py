"""Device-session and queue HTTP; identity, scheduling and job control remain with their original owners."""
from fastapi import APIRouter
from fastapi import HTTPException, Request
from app.member_permits import DeviceHeartbeatRequest

def build_device_session_router(*, workspace_for, require_actor_for, request_actor_for, runtime_for, control_for, repository_for, execution_for, heartbeat_type_for, executor_type_for, quality_kind_for, cover_kind_for, person_kind_for, narration_kind_for, task_media_kind_for) -> APIRouter:
    router = APIRouter()

    def _creation_job_for_request(workspace_id: str, request_id: str):
        return control_for()().job_for_request(workspace_id, request_id)

    @router.post('/api/device/session/heartbeat')
    def report_device_session(payload: DeviceHeartbeatRequest, request: Request) -> dict[str, object]:
        """上报一次真实会话活动。

    身份与工作区都由服务端从本机名册判定，客户端自报的字段里没有这两项；
    成员被移除后，下一次心跳就会因为名册里查不到而失败，旧会话不能续用。
    """
        workspace_id = workspace_for()(request)
        actor = require_actor_for()(request, workspace_id)
        runtime = runtime_for()()
        try:
            runtime.scheduler.heartbeat_session(heartbeat_type_for()(session_id=payload.session_id, workspace_id=workspace_id, playing=payload.playing, recording=payload.recording, editing=payload.editing, interaction=payload.interaction), actor)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {'session_id': payload.session_id, 'workspace_id': workspace_id, 'member_id': actor.member_id}

    @router.get('/api/device/queue')
    def read_device_queue(request: Request) -> dict[str, object]:
        """本机整机队列：自己的执行单元加上匿名的忙闲汇总。"""
        workspace_id = workspace_for()(request)
        actor = require_actor_for()(request, workspace_id)
        runtime = runtime_for()()
        try:
            return runtime.scheduler.list_queue_for(actor)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post('/api/device/queue/{request_id}/cancel')
    def cancel_device_request(request_id: str, request: Request) -> dict[str, object]:
        """释放一条执行单元的整机许可，并把背后的制作作业一并停下。

    权限由调度器按作业归属判定：不是自己的单元就拒绝，取消方向不要求
    成员仍在名册内——撤权必须总是可行。
    """
        workspace_id = workspace_for()(request)
        actor = request_actor_for()(request, workspace_id)
        if actor is None:
            raise HTTPException(status_code=403, detail='缺少可核查的原制作身份，不能取消')
        runtime = runtime_for()()
        try:
            detail = runtime.scheduler.cancel(request_id, actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='该执行单元不存在') from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        quality = runtime.registrations.get(quality_kind_for())
        if quality is not None:
            quality.executor.request_cancel(request_id, actor=actor)
        registration = runtime.registrations.get('creation_job')
        executor = getattr(registration, 'executor', None)
        if isinstance(executor, executor_type_for()):
            job = _creation_job_for_request(workspace_id, request_id)
            if job is not None:
                try:
                    if job.kind == cover_kind_for():
                        if detail['state'] == 'cancelled' and job.status in {'queued', 'interrupted'}:
                            repository_for().job_records.finish_job_run(workspace_id, job.id, status='cancelled', failure_reason='user_cancelled', impact='当前时间线保持不变。', repair_action='查询原封面请求后重新发起。')
                    elif job.kind in {person_kind_for(), narration_kind_for(), task_media_kind_for(), 'creation_task_segment_revision'}:
                        if job.status in {'queued', 'interrupted', 'running'}:
                            repository_for().job_records.finish_job_run(workspace_id, job.id, status='cancelled', failure_reason='user_cancelled', impact='目标片段和当前版本保持不变。', repair_action='需要时重新预览该片段修改。')
                    else:
                        execution_for().cancel(workspace_id, job.task_id, member_id=actor.member_id)
                except PermissionError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            executor.request_cancel(request_id)
        return detail
    return router
