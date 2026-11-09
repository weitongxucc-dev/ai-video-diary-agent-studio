"""Read original task-media results without arranging or executing work."""
import re
from app.creation_execution import CreationExecutionActor
from app.schemas import TaskMediaJobResponse


class MediaResultRejected(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class MediaResultUseCase:
    def __init__(self, repository, outputs):
        self.repository, self.outputs = repository, outputs

    def read(self, media, task_id, job_id, actor, scheduler):
        try:
            job, _event = media.read(actor.workspace_id, job_id)
        except KeyError as exc:
            raise MediaResultRejected(404, "任务媒体作业不存在") from exc
        if job.task_id != task_id:
            raise MediaResultRejected(404, "任务媒体作业不存在")
        return self.receipt(media, job, actor, scheduler)

    def list(self, media, task, actor, scheduler, *, cursor=None, limit=20):
        jobs, next_cursor = media.list(task.workspace_id, task.id, actor, cursor=cursor, limit=limit)
        return {'items': [self.receipt(media, job, actor, scheduler) for job in jobs], 'next_cursor': next_cursor}

    def receipt(self, media, job, actor, scheduler):
        from app.schemas import TaskMediaJobResponse
        current, event = media.read(job.workspace_id, job.id)
        if CreationExecutionActor.from_event(job.workspace_id, event) != actor or media.authorization_verdict(current,event,actor,check_scope=False) != "ready":
            raise MediaResultRejected(404, "任务媒体作业不存在")
        verdict = media.check(current, event, actor)
        status = "stale" if verdict != "ready" and current.status not in {"failed", "cancelled"} else current.status
        result = None
        result_invalid = status == "succeeded" and not self.outputs.published_outputs_current(current)
        if result_invalid:
            status = "failed"
        if status == "succeeded":
            result = self.repository.execution_records.get_task_event(job.workspace_id, f"event_{job.id}_result").payload["result"]
        device_request_id = waiting_reason = None
        device_request, view = scheduler.task_media_result_request(job.workspace_id, job.id, job.input_digest, actor)
        if device_request is not None:
            device_request_id = device_request.id
            waiting_reason = view.get("wait_reason")
        payload = event["arguments"].get("payload", {})
        refs = payload.get("selected_object_refs", [])
        if not refs and payload.get("clip_ids"):
            refs = [f"clip:{item}" for item in payload["clip_ids"]]
        if not refs and payload.get("clip_id"):
            refs = [f"clip:{payload['clip_id']}"]
        if not refs and payload.get("target_id"):
            refs = [f"audio:{payload['target_id']}"]
        if not refs and payload.get("audio_edit"):
            refs = [f"audio:{payload['audio_edit']['target_id']}"]
        if not refs and payload.get("track_id"):
            refs = [f"subtitle-track:{payload['track_id']}"]
        if not refs and event["operation"] == "audio_waveform":
            refs = [f"audio:{event['arguments']['clip_id']}"]
        refs = [item for item in refs if re.fullmatch(r"(?:clip|audio|cue|overlay|subtitle-track):[A-Za-z0-9_-]{1,120}", item)][:8]
        return TaskMediaJobResponse(job_id=job.id, status=status, result_kind=event["result_kind"],
            operation=event["operation"], target_refs=refs, input_digest=job.input_digest, created_at=job.created_at,
            result_url=f"/api/creation-tasks/{job.task_id}/media-jobs/{job.id}", base_version_id=event["input_facts"]["base_version_id"],
            device_request_id=device_request_id, waiting_reason=waiting_reason, result=result,
            resource_wait=media.resource_admission_receipt(current, event, actor, device_request, status),
            failure_reason="result_invalid" if result_invalid else current.failure_reason,
            repair_action="本次媒体文件已丢失或变化；请恢复原文件。系统不会重渲染已提交产物。" if result_invalid else current.repair_action).model_dump(mode="json")


    def terminal_receipt(self, media, data, actor, scheduler, *, task_id, base_version_id):
        """Project the already authorized terminal result; do not reclassify it."""
        job, start = data['job'], data['start']
        device_request, _view = scheduler.task_media_result_request(job.workspace_id, job.id, job.input_digest, actor)
        return TaskMediaJobResponse(job_id=job.id,
            status=data['response_status'], operation=start['operation'], input_digest=job.input_digest, created_at=job.created_at,
            result_kind=start['result_kind'], base_version_id=base_version_id,
            failure_reason=job.failure_reason, result=data['result'],
            resource_wait=media.resource_admission_receipt(job, start, actor, device_request, data['status']),
            result_url=f'/api/creation-tasks/{task_id}/media-jobs/{job.id}').model_dump(mode='json')
