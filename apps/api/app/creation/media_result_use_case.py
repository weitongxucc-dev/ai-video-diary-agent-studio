"""Read original task-media results without arranging or executing work."""
import re
from pathlib import Path

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

    def output_path(self, media, task_id, job_id, output_index, actor, scheduler) -> Path:
        """Resolve one registered media output without manufacturing a URL."""
        try:
            job, _event = media.read(actor.workspace_id, job_id)
        except KeyError as exc:
            raise MediaResultRejected(404, '任务媒体作业不存在') from exc
        if job.task_id != task_id:
            raise MediaResultRejected(404, '任务媒体作业不存在')
        receipt = self.receipt(media, job, actor, scheduler)
        if receipt['status'] != 'succeeded' or receipt['result_kind'] != 'media_output':
            raise MediaResultRejected(409, '当前媒体作业尚无可预览的有效产物。')
        result = receipt.get('result') or {}
        inner_job_id = result.get('job_id') or result.get('media_job_id')
        refs = result.get('output_refs')
        version_id = result.get('timeline_version_id')
        if (not isinstance(inner_job_id, str) or not inner_job_id
                or not isinstance(refs, list) or not refs
                or not isinstance(version_id, str) or not version_id):
            raise MediaResultRejected(409, '媒体成功回执缺少已登记的版本或产物引用。')
        if not isinstance(output_index, int) or output_index < 1 or output_index > len(refs):
            raise MediaResultRejected(404, '媒体输出序号不存在。')
        try:
            version = self.repository.timeline_records.get_timeline_version(
                actor.workspace_id, version_id)
        except KeyError as exc:
            raise MediaResultRejected(409, '媒体输出对应的时间线版本尚未登记。') from exc
        media_snapshot = version.snapshot.get('media_execution') if isinstance(version.snapshot, dict) else None
        if (version.task_id != task_id or not isinstance(media_snapshot, dict)
                or media_snapshot.get('job_id') != inner_job_id
                or media_snapshot.get('outputs') != refs):
            raise MediaResultRejected(409, '媒体输出与已登记的时间线版本不一致。')
        logical_ref = refs[output_index - 1]
        records = self.repository.execution_records.list_artifact_records(actor.workspace_id, task_id)
        artifact = next((item for item in records
                         if item.logical_ref == logical_ref
                         and item.producer_job_id == inner_job_id
                         and item.kind == 'media_output'), None)
        if artifact is None:
            raise MediaResultRejected(409, '媒体输出尚未登记为可读取产物。')
        try:
            path = self.outputs.artifact_store.resolve_file(logical_ref)
            if path.stat().st_size != artifact.size_bytes:
                raise MediaResultRejected(409, '媒体输出与登记摘要不一致。')
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise MediaResultRejected(404, '媒体输出文件当前不可读取。') from exc
        return path

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
            result = dict(self.repository.execution_records.get_task_event(
                job.workspace_id, f"event_{job.id}_result").payload["result"])
            if event["result_kind"] == "media_output":
                refs = result.get("output_refs")
                if isinstance(refs, list) and refs:
                    result["output_urls"] = [
                        f"/api/creation-tasks/{job.task_id}/media-jobs/{job.id}/outputs/{index}"
                        for index, _ref in enumerate(refs, start=1)
                    ]
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
