"""Candidate-only use of the granted short clip preview pipeline."""
from contextlib import closing
from dataclasses import asdict

from app.creation.clip_preview import CANDIDATE_CLIP_PREVIEW, CandidateClipPreviewCommand, ClipPreviewConflict
from app.creation_execution import CreationExecutionActor
from app.montage import MontageClip
from app.visual_effects import clip_playback_rate


class TimelineCandidatePreviewUseCase:
    def __init__(self, repository, artifact_store, clip_preview):
        self.repository = repository
        self.artifact_store = artifact_store
        self.clip_preview = clip_preview

    def submit(self, actor, task_id, command, runtime_for):
        with closing(self.repository.connection_factory()) as conn:
            conn.execute("BEGIN")
            self.clip_preview.require_candidate_head(actor, task_id, command, connection=conn)
        runtime = runtime_for()
        media = runtime.registrations["creation_job"].owner.task_media
        job = media.submit(runtime, actor, task_id, CANDIDATE_CLIP_PREVIEW, {
            "candidate": asdict(command),
        }, CANDIDATE_CLIP_PREVIEW)
        return self.read(media, actor, task_id, command.candidate_id, command.version_id, job.id)

    @staticmethod
    def _bound_job(media, actor, task_id, candidate_id, version_id, job_id, *, connection=None):
        job, event = media.read(actor.workspace_id, job_id, connection)
        if (job.task_id != task_id or event.get("operation") != CANDIDATE_CLIP_PREVIEW
                or CreationExecutionActor.from_event(job.workspace_id, event) != actor):
            raise KeyError(job_id)
        target = CandidateClipPreviewCommand(**event["arguments"]["candidate"])
        if (target.candidate_id, target.version_id) != (candidate_id, version_id):
            raise KeyError(job_id)
        verdict = media._read_access(job, event, actor, connection=connection)
        if verdict != "ready":
            raise KeyError(job_id)
        return job, event, target

    def _artifact(self, job, event, target, *, connection=None):
        records = self.repository.execution_records
        result = records.get_task_event(job.workspace_id, f"event_{job.id}_result", connection=connection).payload["result"]
        verified = records.get_task_event(job.workspace_id, f"event_{job.id}_verified", connection=connection).payload["outputs"]
        record = records.get_artifact_record(job.workspace_id, result["candidate_artifact_id"], connection=connection)
        inputs = records.get_task_event(job.workspace_id, f"event_{job.id}_{record.id.removeprefix('artifact_')}_input",
            connection=connection)
        facts = event["input_facts"]
        if (record.producer_job_id != job.id or record.kind != CANDIDATE_CLIP_PREVIEW or record.task_id != job.task_id
                or record.input_refs != [f"timeline:{target.version_id}", facts["clip"]["source_path"]]
                or record.logical_ref not in job.output_refs
                or not any(output["reference"] == record.logical_ref for output in verified)
                or inputs.job_id != job.id or inputs.task_id != job.task_id
                or inputs.event_type != "task_media_input"
                or inputs.payload["parameters"] != {"base_version_id": target.version_id, "side": "after",
                    "clip_id": target.clip_id, "max_seconds": 5, "silent": True,
                    "candidate_id": target.candidate_id, "formal_base_version_id": facts["formal_base_version_id"]}
                or inputs.payload["media_input"] != {"clip": facts["candidates"][0], "source_path": facts["source_path"],
                    "aspect_ratio": facts["aspect_ratio"], "max_seconds": 5}):
            raise ClipPreviewConflict("候选预览产物与原作业或版本不一致")
        publication = self.artifact_store.describe(record.logical_ref)
        if (publication.size_bytes, publication.sha256) != (record.size_bytes, record.sha256):
            raise ClipPreviewConflict("候选预览文件与登记摘要不一致")
        return self.artifact_store.resolve_file(record.logical_ref)

    def _path(self, media, actor, job, target):
        # Re-read the head in the same DB snapshot as the registered result.
        # Do not hold a writer lock while verifying output bytes.
        with closing(self.repository.connection_factory()) as conn:
            conn.execute("BEGIN")
            current, start, _target = self._bound_job(media, actor, job.task_id, target.candidate_id,
                target.version_id, job.id, connection=conn)
            verdict = media.check(current, start, actor, connection=conn)
            if verdict == "unauthorized":
                raise KeyError(job.id)
            if verdict != "ready":
                raise ClipPreviewConflict("候选预览已过期，请读取候选当前版本")
            if current.status != "succeeded":
                raise ClipPreviewConflict("候选短预览尚无可读取画面")
            try:
                return self._artifact(current, start, target, connection=conn)
            except (KeyError, FileNotFoundError) as exc:
                raise ClipPreviewConflict("候选短预览的原产物当前不可读取") from exc

    def read(self, media, actor, task_id, candidate_id, version_id, job_id):
        job, event, target = self._bound_job(media, actor, task_id, candidate_id, version_id, job_id)
        verdict = media.check(job, event, actor)
        if verdict == "unauthorized":
            raise KeyError(job_id)
        status = job.status if verdict == "ready" else "stale"
        url = f"/api/creation-tasks/{task_id}/timeline/candidates/{candidate_id}/versions/{version_id}/previews/{job_id}"
        video_url = None
        failure_reason = job.failure_reason
        if status == "succeeded":
            try:
                self._path(media, actor, job, target)
            except ClipPreviewConflict:
                status = "failed" if media.check(job, event, actor) == "ready" else "stale"
                if status == "failed":
                    failure_reason = "result_invalid"
            else:
                video_url = f"{url}/video"
        waiting = self.repository.job_records.creation_execution_wait_row(
            job.workspace_id, actor.member_id, job.id, job.input_digest) if status in {"queued", "interrupted"} else None
        clip = MontageClip(**event["input_facts"]["candidates"][0])
        duration = event["resources"]["duration_seconds"]
        rate = clip_playback_rate(clip)
        return {"job_id": job.id, "task_id": task_id, "candidate_id": candidate_id, "version_id": version_id,
            "clip_id": clip.id, "status": status, "result_url": url, "video_url": video_url, "silent": True,
            "waiting_reason": waiting["wait_reason"] if waiting else None, "failure_reason": failure_reason,
            "timeline_start_seconds": clip.timeline_start_seconds,
            "timeline_end_seconds": clip.timeline_start_seconds + duration,
            "source_start_seconds": clip.source_start_seconds,
            "source_end_seconds": min(clip.source_start_seconds + rate * 5, clip.source_end_seconds)}

    def video(self, media, actor, task_id, candidate_id, version_id, job_id):
        job, _event, target = self._bound_job(media, actor, task_id, candidate_id, version_id, job_id)
        return self._path(media, actor, job, target)

    def cancel(self, media, actor, task_id, candidate_id, version_id, job_id, runtime_for):
        job, _event, _target = self._bound_job(media, actor, task_id, candidate_id, version_id, job_id)
        if media.cancellation_pending(job):
            runtime = runtime_for()
            media = runtime.registrations["creation_job"].owner.task_media
            job, _event, _target = self._bound_job(media, actor, task_id, candidate_id, version_id, job_id)
            media.cancel(runtime, job, actor)
        return self.read(media, actor, task_id, candidate_id, version_id, job_id)
