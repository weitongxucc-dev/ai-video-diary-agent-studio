"""Review the measured narration clock before adopting a longer delivery."""
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

from app.creation.contracts import TaskEvent
from app.models import utc_now

DELIVERY_FRAME_RATE = 30


def timing_summary(durations, total_count, target_seconds):
    measured = math.fsum(durations)
    required_frames = sum(max(1, math.ceil(seconds * DELIVERY_FRAME_RATE - 1e-9)) for seconds in durations)
    return {
        "completed_sections": len(durations),
        "total_sections": total_count,
        "measured_seconds": measured,
        "target_seconds": target_seconds,
        "minimum_delivery_seconds": required_frames / DELIVERY_FRAME_RATE,
        "exceeds_target": required_frames > round(target_seconds * DELIVERY_FRAME_RATE),
    }


def narration_file_facts(record):
    path = Path(record.audio_path)
    stat = path.stat()
    return [record.cache_key, str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def narration_files_digest(files):
    return hashlib.sha256(json.dumps(files).encode()).hexdigest()


def duration_wait_event_id(job):
    return f"event_{job.id}_narration_duration_{job.input_digest}"


class NarrationDurationReview:
    def __init__(self, repository):
        self.repository = repository

    def _status(self, conn, task, plan, *, files=None):
        durations = []
        if task.confirmed_script_id:
            header, _ = self.repository.script_records.read_execution_script_metadata(
                conn, task.workspace_id, task.confirmed_script_id)
            for section, segment in self.repository.section_inputs.iter_creation_narration_sections(task, header, connection=conn):
                if segment is not None and segment.status == "ready" and segment.audio_reference and segment.text == section.content:
                    durations.append(segment.duration_seconds)
                    if files is not None:
                        key = segment.audio_reference.removeprefix("narration://artifact/")
                        artifact = self.repository.narration_artifacts.get_narration_artifact(task.workspace_id, key, connection=conn)
                        if (artifact is None or artifact.task_id != task.id or artifact.script_id != header.id
                                or artifact.duration_seconds != segment.duration_seconds):
                            raise ValueError("原配音段落或音频产物已经变化。")
                        try:
                            files.append(narration_file_facts(artifact))
                        except OSError as exc:
                            raise ValueError("部分原配音文件已不可读，请核对原作业。") from exc
            return timing_summary(durations, header.sections_total_count, plan.duration_target_seconds)
        return timing_summary(durations, 0, plan.duration_target_seconds)

    def _original(self, conn, task, actor):
        row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND kind='creation_task_execution' "
            "ORDER BY created_at DESC LIMIT 1", (task.workspace_id, task.id)).fetchone()
        if row is None:
            return None
        job = self.repository.job_records.job_run_from_row(row)
        if job.status != "interrupted":
            return None
        try:
            wait = self.repository.execution_records.get_task_event(
                task.workspace_id, duration_wait_event_id(job), connection=conn)
        except KeyError:
            return None
        start = self.repository.creation_execution_jobs.creation_execution_input(task.workspace_id, job.id, connection=conn)
        if any(start.get(key) != actor.identity_payload().get(key) for key in ("member_id", "identity_kind", "device_id", "user_id")):
            return None
        if job.failure_reason != wait.payload["reason"]:
            return None
        return job, wait

    def read(self, workspace_id, task_id, actor):
        with self.repository.execution_inputs.creation_execution_read() as conn:
            task = self.repository.task_records.get_creation_task(workspace_id, task_id, connection=conn)
            plan = self.repository.plan_records.read_creation_plan(conn, workspace_id, task.plan_id)
            summary = self._status(conn, task, plan)
            original = self._original(conn, task, actor)
            return {"task_id": task.id, **summary,
                    "review_id": original[1].id if original and summary == original[1].payload["timing"] else None}

    def adopt(self, workspace_id, task_id, actor, *, request_id, review_id):
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self.repository.task_records.get_creation_task(workspace_id, task_id, connection=conn)
            if task.owner_user_id != (actor.user_id or actor.member_id):
                raise PermissionError("当前成员不能修改此作品的交付时长。")
            event_id = f"event_{task.id}_narration_duration_adopted_{request_id}"
            try:
                previous = self.repository.execution_records.get_task_event(workspace_id, event_id, connection=conn)
            except KeyError:
                previous = None
            if previous is not None:
                if previous.payload["review_id"] != review_id or previous.payload["actor"] != actor.identity_payload():
                    raise ValueError("此请求已绑定另一条时长确认，请查询原回执。")
                return previous.payload["receipt"]
            original = self._original(conn, task, actor)
            if original is None or original[1].id != review_id:
                raise ValueError("当前作品没有可采用的配音时长确认，请刷新配音状态。")
            job, wait = original
            snapshot = self.repository.execution_inputs.read_creation_execution_snapshot(workspace_id, task_id, connection=conn)
            plan = snapshot.plan
            files = []
            timing = self._status(conn, task, plan, files=files)
            if (snapshot.input_digest != job.input_digest or timing != wait.payload["timing"]
                    or narration_files_digest(files) != wait.payload["files_digest"]
                    or not timing["exceeds_target"] or timing["completed_sections"] != timing["total_sections"]
                    or task.stage != "narration" or task.narration_cache_key or task.montage_plan_id or task.current_timeline_version_id):
                raise ValueError("作品方案或配音已变化，请刷新配音状态。")
            usage = self.repository.narration_units.creation_narration_usage(workspace_id, job.id, connection=conn)
            if usage["budget"] is None or not usage["consumption_known"]:
                raise ValueError("原作业配音消耗尚未核实，请查询原配音请求。")
            # Keep the existing JobRun, paid sections and all completed device requests.
            revised = replace(plan, duration_target_seconds=timing["minimum_delivery_seconds"],
                duration_mode="exact", duration_range_seconds=None, updated_at=utc_now())
            self.repository.plan_records.write_plan_payload(conn, workspace_id, revised)
            digest = self.repository.execution_inputs.read_creation_execution_snapshot(
                workspace_id, task_id, connection=conn).input_digest
            self.repository.creation_execution_jobs.rebind_narration_duration(job, digest, event_id, connection=conn)
            receipt = {
                "request_id": request_id, "task_id": task.id, "job_id": job.id, "plan_updated_at": revised.updated_at.isoformat(),
                "duration_target_seconds": revised.duration_target_seconds, "measured_seconds": timing["measured_seconds"],
                "completed_sections": timing["completed_sections"],
            }
            self.repository.execution_records.append_task_event(TaskEvent(event_id, workspace_id, task.id, job.id,
                "creation_narration_duration_adopted", {"review_id": review_id, "actor": actor.identity_payload(),
                    "previous_target_seconds": plan.duration_target_seconds, "receipt": receipt}), connection=conn)
            return receipt
