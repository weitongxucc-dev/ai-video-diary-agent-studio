"""Original sealed media Job inputs and submission events in one writer."""
from __future__ import annotations

import hashlib
import json

from app.creation.contracts import JobRun as _JobRun, TaskEvent as _TaskEvent
from app.creation.task_input_reads import require_creation_task


class TaskMediaSubmissionTransactions:
    def __init__(self, *, connection_factory, edit_records, job_records, execution_records, version_conflict):
        self._connect = connection_factory
        self.edit_records = edit_records
        self.job_records = job_records
        self.execution_records = execution_records
        self.version_conflict = version_conflict

    def create_cover_generation_job(self, run: _JobRun, payload: dict, *, expected_version_id: str) -> _JobRun:
        """Save the original cover Job and sealed identity/input in one transaction."""
        command = payload["command"]
        operation = json.loads(command["operation_json"])
        identity = json.loads(command["identity_json"])
        if (run.kind != "cover_frame_generation" or run.idempotency_key != f"cover:{command['proposal_id']}"
                or (run.workspace_id, run.task_id) != (command["workspace_id"], command["task_id"])
                or run.input_digest != hashlib.sha256(command["operation_json"].encode()).hexdigest()
                or operation["base_version_id"] != expected_version_id
                or identity != {key: payload[key] for key in identity}):
            raise ValueError("封面Job与原封存输入或身份不一致")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise self.version_conflict("提交封面前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的封面")
            proposal = self.edit_records.get_edit_proposal(run.workspace_id, command["proposal_id"])
            original = proposal.operation.get("payload", {})
            normalized = {**original, "title_position": original.get("title_position", "bottom")}
            if (proposal.task_id != run.task_id or proposal.status != "proposed"
                    or proposal.operation.get("operation_type") != "GenerateCoverFromFrame"
                    or normalized != operation):
                raise ValueError("封面原提案已经变化")
            job = self.job_records.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{job.id}_cover_start", workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type="cover_submitted", payload=payload,
                created_at=job.created_at), connection=conn)
        return job

    def create_clip_preview_job(self, run: _JobRun, payload: dict, *, expected_version_id: str) -> _JobRun:
        """Compare the caller's original version and save the job/input together."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise self.version_conflict("提交预览前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的媒体处理")
            job = self.job_records.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{job.id}_start", workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type="task_media_submitted", payload=payload,
                created_at=job.created_at), connection=conn)
        return job
