"""Task ownership and artifact publication for local audio operations."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.audio_timeline import AudioTimeline, probe_audio_source
from app.models import utc_now
from app.production import ArtifactRecord, DependencyEdge, JobRun, TransformRun


class TaskAudio:
    def __init__(self, repository, artifact_store, temporary_root: Path):
        self.repository = repository
        self.artifact_store = artifact_store
        self.temporary_root = temporary_root

    def source(self, workspace_id: str, task_id: str, reference: str) -> Path:
        self.repository.get_creation_task(workspace_id, task_id)
        if reference.startswith("narration://artifact/"):
            record = self.repository.get_narration_artifact(workspace_id, reference.removeprefix("narration://artifact/"))
            if record is None or record.task_id != task_id:
                raise ValueError("配音不属于当前任务")
            return self.repository.resolve_authorized_narration_audio_reference(workspace_id, reference)
        if reference.startswith("workspace://"):
            return self.repository.resolve_workspace_media_reference(workspace_id, reference, require_registered=True)
        records = self.repository.list_artifact_records(workspace_id, task_id)
        if not any(record.logical_ref == reference and record.kind in {"audio_source", "audio_preview"} for record in records):
            raise ValueError("音频产物不属于当前任务")
        return self.artifact_store.resolve_file(reference)

    def validate(self, workspace_id: str, task_id: str, timeline: AudioTimeline, duration_seconds: float):
        durations = {}
        for track in timeline.tracks:
            for clip in track.clips:
                if clip.source_ref not in durations:
                    durations[clip.source_ref] = probe_audio_source(self.source(workspace_id, task_id, clip.source_ref))
                if clip.source_end_ms > round(durations[clip.source_ref] * 1000) + 20:
                    raise ValueError("音频裁剪区间超出原文件时长")
                if clip.timeline_start_ms + clip.duration_ms > round(duration_seconds * 1000):
                    raise ValueError("音频片段超出成片范围")

    def publish(self, workspace_id: str, task_id: str, *, kind: str, parameters: dict,
                input_refs: list[str], produce, suffix: str = ".wav", engine: str = "local-audio-v1") -> ArtifactRecord:
        identity = uuid4().hex
        digest = hashlib.sha256(json.dumps({"inputs": input_refs, "parameters": parameters}, sort_keys=True).encode()).hexdigest()
        job = self.repository.create_job_run(JobRun(id=f"job_{identity}", workspace_id=workspace_id, task_id=task_id,
            kind=kind, idempotency_key=f"{kind}:{identity}", input_digest=digest))
        running = self.repository.claim_job_run(workspace_id, job.id)
        try:
            self.temporary_root.mkdir(exist_ok=True)
            with TemporaryDirectory(prefix="task-audio-", dir=self.temporary_root) as directory:
                path = Path(directory) / f"audio{suffix}"
                produce(path)
                probe_audio_source(path)
                published = self.artifact_store.publish_file(f"artifacts/{task_id}/{job.id}/audio{suffix}", path)
            artifact = self.repository.create_artifact_record(ArtifactRecord(id=f"artifact_{identity}",
                workspace_id=workspace_id, task_id=task_id, kind=kind, logical_ref=published.logical_ref,
                sha256=published.sha256, size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=input_refs))
            transform = self.repository.create_transform_run(TransformRun(id=f"transform_{identity}", workspace_id=workspace_id,
                task_id=task_id, job_id=job.id, transform_type=kind, input_digest=digest, input_refs=input_refs,
                output_refs=[artifact.logical_ref], engine=engine, parameters=parameters,
                status="succeeded", started_at=running.started_at, finished_at=utc_now()))
            for index, reference in enumerate(input_refs):
                self.repository.create_dependency_edge(DependencyEdge(id=f"edge_{identity}_{index}", workspace_id=workspace_id,
                    task_id=task_id, upstream_ref=reference, downstream_ref=artifact.logical_ref, created_by_transform_id=transform.id))
            self.repository.finish_job_run(workspace_id, job.id, status="succeeded", output_refs=[artifact.logical_ref])
            return artifact
        except Exception as exc:
            self.repository.finish_job_run(workspace_id, job.id, status="failed", failure_reason=type(exc).__name__,
                impact="未改变当前音轨和时间线。", repair_action="检查音频文件、本地 FFmpeg 与外接盘后重试。")
            raise
