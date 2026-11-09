"""Persist original-job segment, candidate and intermediate PCM checkpoints."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import json
import sqlite3

from app.creation.contracts import TaskEvent as _TaskEvent
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.creation.task_input_reads import require_creation_task
from app.models import utc_now


class NarrationCheckpointTransactions:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        execution_records: ExecutionRecordStore, job_records: JobRunRecordStore,
        narration_artifact_reference: Callable[[str], str], save_narration_artifact: Callable,
        replace_adopted_narration_segment: Callable,
    ) -> None:
        self._connect = connection_factory
        self.execution_records = execution_records
        self.job_records = job_records
        self.narration_artifact_reference = narration_artifact_reference
        self.save_narration_artifact = save_narration_artifact
        self.replace_adopted_narration_segment = replace_adopted_narration_segment

    def commit_creation_narration_segment(self, artifact, segment, *, connection):
        if (artifact.workspace_id, artifact.task_id, artifact.script_id, artifact.voice_profile_id) != (
                segment.workspace_id, segment.task_id, segment.script_id, segment.voice_profile_id):
            raise ValueError('单段产物与片段谱系不一致')
        if segment.status != 'ready' or segment.audio_reference != self.narration_artifact_reference(artifact.cache_key):
            raise ValueError('单段检查点必须绑定已核实的ready产物')
        self.save_narration_artifact(artifact, connection=connection)
        task = require_creation_task(connection, segment.workspace_id, segment.task_id)
        self.replace_adopted_narration_segment(task, segment, connection=connection)

    def checkpoint_creation_narration_segment(self, grant, artifact, segment, ordinal, *, connection):
        self.commit_creation_narration_segment(artifact, segment, connection=connection)
        now = utc_now()
        changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
            (now.isoformat(), grant.workspace_id, grant.owner_id)).rowcount
        if changed != 1:
            raise PermissionError('原配音作业已停止，不能写入片段成功')
        event_id = f'event_{grant.owner_id}_narration_segment_{grant.generation}'
        self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=grant.workspace_id,
            task_id=segment.task_id, job_id=grant.owner_id, event_type='creation_execution_stage',
            payload={'stage': 'narration', 'status': 'pending', 'request_id': grant.request_id,
                'unit_key': grant.unit_key, 'segment_id': segment.id, 'ordinal': ordinal,
                'phase': 'segment', 'output': segment.audio_reference}, created_at=now), connection=connection)
        return event_id

    def narration_edit_generated(self, workspace_id, job_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_narration_edit_generated' ORDER BY rowid DESC LIMIT 1",(workspace_id,job_id)).fetchone()
            return json.loads(row['payload_json']) if row else None

    def checkpoint_narration_edit_generated(self, grant, artifact, segment, source_key, *, connection):
        self.save_narration_artifact(artifact,connection=connection)
        changed=connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
            (utc_now().isoformat(),grant.workspace_id,grant.owner_id)).rowcount
        if changed != 1:
            raise PermissionError('原局部配音Job已停止，未采用候选')
        event_id=f'event_{grant.owner_id}_narration_edit_generated'
        self.execution_records.append_task_event(_TaskEvent(event_id,grant.workspace_id,segment.task_id,grant.owner_id,
            'creation_narration_edit_generated',{'source_key':source_key,'segment_id':segment.id,
                'cache_key':artifact.cache_key,'request_id':grant.request_id,'permit_generation':grant.generation}),connection=connection)
        return event_id

    def creation_narration_merge_checkpoint(self, workspace_id, job_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_execution_stage' AND json_extract(payload_json,'$.phase')='merge' "
                "AND json_extract(payload_json,'$.status')='pending' ORDER BY rowid DESC LIMIT 1",
                (workspace_id, job_id)).fetchone()
            return json.loads(row['payload_json']) if row is not None else None

    def checkpoint_creation_narration_merge(self, grant, *, source_digest, section_ids,
                                          input_references, pcm_receipt, connection):
        job = self.job_records.get_job_run(grant.workspace_id, grant.owner_id, connection=connection)
        task = require_creation_task(connection, job.workspace_id, job.task_id)
        if (job.kind != 'creation_task_narration_edit' and task.stage != 'narration' or len(section_ids) < 1
                or pcm_receipt['job_id'] != job.id
                or pcm_receipt['unit_id'] != f'{grant.request_id}:{grant.generation}'
                or pcm_receipt['source_digest'] != source_digest or pcm_receipt['finalized']):
            raise ValueError('中间PCM检查点不属于原作业，不能发布为音频产物')
        now = utc_now()
        changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
            (now.isoformat(), grant.workspace_id, grant.owner_id)).rowcount
        if changed != 1:
            raise PermissionError('原配音作业已停止，不能写入合并成功')
        event_id = f'event_{grant.owner_id}_narration_merge_{grant.generation}'
        self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=grant.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type='creation_execution_stage',
            payload={'stage': 'narration', 'status': 'pending', 'phase': 'merge',
                'request_id': grant.request_id, 'unit_key': grant.unit_key, 'source_digest': source_digest,
                'section_ids': section_ids, 'input_references': input_references,
                'pcm': pcm_receipt},
            created_at=now), connection=connection)
        return event_id
