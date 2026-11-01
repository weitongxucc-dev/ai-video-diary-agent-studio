"""Segment unit usage receipts, checkpoints and original Job recovery."""
from __future__ import annotations

import math

from app.creation.contracts import TaskEvent as _TaskEvent
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.models import utc_now


class SegmentUnitTransactions:
    def __init__(self, *, connection_factory, job_records: JobRunRecordStore,
                 execution_records: ExecutionRecordStore) -> None:
        self._connect = connection_factory
        self.job_records = job_records
        self.execution_records = execution_records

    def start_segment_unit(self, grant):
        from app.device_scheduler import RecoveryBlocked
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            pending = conn.execute("SELECT s.id FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
                "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e "
                "WHERE e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
                "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",
                (grant.workspace_id,grant.owner_id)).fetchone()
            if pending:
                raise RecoveryBlocked('原段落执行的用量未知，保留原请求待核查')
            job = self.job_records.claim_job_run(grant.workspace_id,grant.owner_id,connection=conn)
            event = _TaskEvent(id=f'event_{job.id}_unit_start_{grant.generation}', workspace_id=job.workspace_id,
                task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_started',payload={
                    'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,
                    'permit_generation':grant.generation,'unit_key':grant.unit_key})
            self.execution_records.append_task_event(event,connection=conn)
        return job

    def resume_segment_job(self,workspace_id,job_id,*,connection):
        pending = connection.execute("SELECT 1 FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
            "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e WHERE "
            "e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
            "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",(workspace_id,job_id)).fetchone()
        if pending:
            raise ValueError('原段落用量未核对，不能重启原请求')
        changed = connection.execute("UPDATE job_runs SET status='interrupted',failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=? AND status IN ('failed','cancelled') "
            "AND kind IN ('creation_task_execution','creation_task_segment_revision')",(utc_now().isoformat(),workspace_id,job_id)).rowcount
        if changed!=1:
            raise ValueError('原段落作业不是已释放的可恢复状态')

    def settle_segment_unit(self, grant, *, elapsed_seconds, output_bytes, connection):
        if elapsed_seconds < 0 or output_bytes < 0 or not math.isfinite(elapsed_seconds):
            raise ValueError('段落计量必须来自本次执行的非负实测值')
        job = self.job_records.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        event = _TaskEvent(id=f'event_{job.id}_unit_settle_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_settled',payload={
                'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,
                'permit_generation':grant.generation,'unit_key':grant.unit_key,
                'elapsed_seconds':elapsed_seconds,'output_bytes':output_bytes,'usage_source':'local_execution_measurement'})
        self.execution_records.append_task_event(event,connection=connection)

    def checkpoint_segment_unit(self, grant, *, status, connection, reason=None, payload=None):
        job = self.job_records.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        if status == 'queued':
            changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
                (utc_now().isoformat(),job.workspace_id,job.id)).rowcount
            if changed != 1:
                raise ValueError('原段落作业不可继续提交')
        elif not (status=='cancelled' and job.status=='cancelled'):
            self.job_records.finish_job_run(job.workspace_id,job.id,status=status,connection=connection,
                failure_reason=reason,impact='原已采用有声版本保留' if reason else None,
                repair_action='核查原请求及执行回执后恢复' if reason else None)
        event = _TaskEvent(id=f'event_{job.id}_unit_checkpoint_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='segment_execution_checkpoint',
            payload={'status':status,'reason':reason,'request_id':grant.request_id,'unit_key':grant.unit_key,**(payload or {})})
        self.execution_records.append_task_event(event,connection=connection)
        return event.id

    def unsettled_segment_units(self, workspace_id, job_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
                "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e "
                "WHERE e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
                "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",
                (workspace_id, job_id)).fetchone() is not None
