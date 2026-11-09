from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING
import hashlib
import json
import sqlite3

from app.creation.contracts import CreationTask as _CreationTask, NarrationSegment as _NarrationSegment
from app.models import utc_now
from app.persistence_json import _json_payload

if TYPE_CHECKING:
    from app.creation.narration_artifacts import NarrationArtifactRecord


class NarrationCompletionTransaction:
    def __init__(self, connection_factory, *, decode_task, upsert_segments, artifact_from_row,
                 read_job, list_transforms, confirmed_script_digest):
        self._connect = connection_factory
        self._decode_task = decode_task
        self._upsert_segments = upsert_segments
        self._artifact_from_row = artifact_from_row
        self._read_job = read_job
        self._list_transforms = list_transforms
        self._confirmed_script_digest = confirmed_script_digest

    def complete_creation_task_narration(
        self,
        artifact: NarrationArtifactRecord,
        segments: list[_NarrationSegment] | None = None,
        *, connection: sqlite3.Connection | None = None,
    ) -> tuple[_CreationTask, NarrationArtifactRecord]:
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.complete_creation_task_narration(artifact, segments, connection=conn)
        if artifact.task_id is None:
            raise ValueError("创作任务配音必须关联 task_id")
        if not Path(artifact.audio_path).is_file() or Path(artifact.audio_path).stat().st_size == 0:
            raise ValueError("声音克隆没有生成有效 WAV")
        with nullcontext(connection) as conn:
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (artifact.workspace_id, artifact.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(artifact.task_id)
            task = self._decode_task(json.loads(str(task_row["payload"])))
            expected_statuses = {"not_started", "failed"} if artifact.source_kind == "recorded" else {"running"}
            if (
                task.stage != "narration"
                or task.stage_status not in expected_statuses
                or task.confirmed_script_id != artifact.script_id
            ):
                raise ValueError("配音产物与当前创作任务不一致")
            if artifact.source_kind == "recorded" and not self.recorded_source_is_current(artifact, connection=conn):
                raise ValueError("采用的电脑录音不再是当前任务的有效来源")
            if artifact.source_kind == "recorded" and (
                segments is None or len(segments) != 1
                or segments[0].source_artifact_ref != artifact.source_artifact_ref
                or segments[0].source_sha256 != artifact.source_sha256
                or segments[0].source_script_digest != artifact.source_script_digest
            ):
                raise ValueError("录音产物与唯一采用的文案分段来源不一致")
            if segments is not None:
                self._upsert_segments(
                    conn,
                    workspace_id=artifact.workspace_id,
                    task_id=artifact.task_id,
                    script_id=artifact.script_id,
                    segments=segments,
                )
            now = utc_now()
            conn.execute(
                """
                INSERT INTO narration_artifacts (id, workspace_id, cache_key, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                    id = excluded.id, payload = excluded.payload, created_at = excluded.created_at
                """,
                (
                    artifact.id,
                    artifact.workspace_id,
                    artifact.cache_key,
                    _json_payload(artifact),
                    artifact.created_at.isoformat(),
                ),
            )
            task.narration_cache_key = artifact.cache_key
            task.stage = "montage"
            task.stage_status = "not_started"
            task.status = "running"
            task.progress = max(task.progress, 60)
            task.recovery_point = "montage"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        return task, artifact

    def recorded_source_is_current(self, artifact: NarrationArtifactRecord, *, connection=None) -> bool:
        if (artifact.source_kind != "recorded" or not artifact.task_id
                or not artifact.source_artifact_ref or not artifact.source_sha256
                or not artifact.source_script_digest
                or artifact.voice_profile_id is not None or artifact.model_id is not None):
            return False
        with self._connect() if connection is None else nullcontext(connection) as conn:
            if connection is None:
                conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM artifact_records WHERE workspace_id=? AND task_id=? AND logical_ref=?",
                (artifact.workspace_id, artifact.task_id, artifact.source_artifact_ref),
            ).fetchone()
            if row is None:
                return False
            source = self._artifact_from_row(row)
            if source.kind != "task_recording" or source.sha256 != artifact.source_sha256:
                return False
            try:
                job = self._read_job(artifact.workspace_id, source.producer_job_id, connection=conn)
                transform = next((item for item in self._list_transforms(artifact.workspace_id, artifact.task_id, connection=conn)
                    if item.job_id == source.producer_job_id and item.output_refs == [source.logical_ref]), None)
            except KeyError:
                return False
            if transform is None:
                return False
            if (job.task_id != artifact.task_id or job.status != "succeeded"
                    or transform.task_id != artifact.task_id or transform.status != "succeeded"
                    or transform.job_id != job.id or transform.output_refs != [source.logical_ref]
                    or transform.parameters.get("script_id") != artifact.script_id
                    or transform.parameters.get("script_digest") != artifact.source_script_digest
                    or self._confirmed_script_digest(
                        artifact.workspace_id, artifact.task_id, artifact.script_id, connection=conn
                    ) != artifact.source_script_digest):
                return False
        path = Path(artifact.audio_path)
        if not path.is_file() or path.stat().st_size != source.size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as audio:
            for chunk in iter(lambda: audio.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == source.sha256
