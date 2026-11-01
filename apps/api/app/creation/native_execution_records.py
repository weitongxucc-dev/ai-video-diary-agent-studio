"""Native-operation input, result, usage and release records."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import hashlib
import json
import sqlite3
from uuid import uuid4

from app.creation.contracts import JobRun as _JobRun, TaskEvent as _TaskEvent
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.creation.task_input_reads import require_creation_task


class NativeExecutionRecordStore:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        execution_records: ExecutionRecordStore, job_records: JobRunRecordStore,
        workspace_media_reference_for_asset: Callable[[str, str], str],
    ) -> None:
        self._connect = connection_factory
        self.execution_records = execution_records
        self.job_records = job_records
        self.workspace_media_reference_for_asset = workspace_media_reference_for_asset

    def native_available_inputs(self,workspace_id,task_id,*,offset=0,limit=30):
        query = """WITH candidates AS (
            SELECT a.id,a.logical_ref AS source_ref,a.kind AS source_kind,a.logical_ref AS name
            FROM artifact_records a JOIN job_runs j ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id
            WHERE a.workspace_id=? AND a.task_id=? AND j.status='succeeded'
            UNION ALL
            SELECT a.id,NULL AS source_ref,'material' AS source_kind,json_extract(a.payload,'$.file_name') AS name
            FROM material_assets a JOIN task_authorizations t ON t.workspace_id=a.workspace_id AND t.task_id=?
            WHERE a.workspace_id=? AND a.id IN (SELECT value FROM json_each(t.resource_scope_json,'$.assets'))
        ) """
        args = (workspace_id,task_id,task_id,workspace_id)
        with self._connect() as conn:
            total = conn.execute(query+'SELECT count(*) FROM candidates',args).fetchone()[0]
            rows = conn.execute(query+'SELECT * FROM candidates ORDER BY source_kind,id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        values = [{'id':row['id'],'name':row['name'],'source_kind':row['source_kind'],
            'source_ref':row['source_ref'] or self.workspace_media_reference_for_asset(workspace_id,row['id'])} for row in rows]
        return {'items':values,'offset':offset,'total_count':total,'truncated':offset+len(values)<total}

    def find_task_artifact_reference(self,workspace_id,task_id,reference):
        with self._connect() as conn:
            row = conn.execute("SELECT a.* FROM artifact_records a JOIN job_runs j ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id "
                "WHERE a.workspace_id=? AND a.task_id=? AND a.logical_ref=? AND j.task_id=a.task_id AND j.status='succeeded'",
                (workspace_id,task_id,reference)).fetchone()
            return self.execution_records.artifact_from_row(row) if row else None

    def task_asset_for_path(self,workspace_id,path,allowed_ids):
        with self._connect() as conn:
            row = conn.execute('SELECT id FROM material_assets WHERE workspace_id=? AND file_path=? AND id IN (SELECT value FROM json_each(?))',
                (workspace_id,path,json.dumps(allowed_ids))).fetchone()
            return row['id'] if row else None

    def record_native_input(self,actor,origin,name,entry,*,write_guard):
        key = hashlib.sha256(json.dumps([origin,name,entry],sort_keys=True,default=str).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            row = conn.execute('SELECT id FROM task_events WHERE workspace_id=? AND id=?',(actor.workspace_id,'native_input_'+key)).fetchone()
            if row:
                return
            self.execution_records.append_task_event(_TaskEvent(id='native_input_'+key,workspace_id=actor.workspace_id,task_id=origin['task_id'],
                job_id=None,event_type='native_input_mapped',payload={'origin':origin,'name':name,'entry':entry}),connection=conn)

    def list_native_jobs(self,workspace_id,task_id,*,limit=30,offset=0,turn_id=None,session_id=None,unreleased_only=False,before=None,started_only=False):
        with self._connect() as conn:
            where = "j.workspace_id=? AND j.task_id=? AND j.kind IN ('native_task_command','native_task_file')"
            args = [workspace_id,task_id]
            if started_only:
                where += ' AND j.attempt>0'
            if unreleased_only:
                where += " AND j.kind='native_task_command' AND (j.status IN ('queued','running','interrupted') OR j.attempt>0) "
                where += "AND NOT (j.status='cancelled' AND j.attempt=0) AND NOT EXISTS "
                where += "(SELECT 1 FROM task_events released WHERE released.workspace_id=j.workspace_id AND released.id='event_'||j.id||'_native_released')"
            if turn_id is not None:
                where += " AND json_extract(e.payload_json,'$.origin.assistant_turn_id')=?"
                args.append(turn_id)
            if session_id is not None:
                where += " AND json_extract(e.payload_json,'$.origin.session_id')=?"
                args.append(session_id)
            if before is not None:
                where += ' AND (j.created_at<? OR (j.created_at=? AND j.id<?))'
                args.extend([before[0],before[0],before[1]])
            join = " FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id AND e.id='event_'||j.id||'_start' WHERE "+where
            count = conn.execute('SELECT count(*)'+join,args).fetchone()[0]
            rows = conn.execute('SELECT j.*'+join+' ORDER BY j.created_at DESC,j.id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
            return [self.job_records.job_run_from_row(row) for row in rows],count

    def create_native_operation(self,actor,origin,request_id,operation,arguments,resources,*,write_guard):
        identity = {'operation':operation,'arguments':arguments,'origin':origin,'resources':resources}
        input_digest = hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
        run = _JobRun(id=f'job_{uuid4().hex}',workspace_id=actor.workspace_id,task_id=origin['task_id'],
            kind='native_task_file' if operation=='native_task_file' else 'native_task_command',input_digest=input_digest,
            idempotency_key=f'native:{origin["task_id"]}:{actor.member_id}:{request_id}')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            task = require_creation_task(conn,actor.workspace_id,origin['task_id'])
            if task.owner_user_id!=(actor.user_id or actor.member_id):
                raise PermissionError('通用执行提交时任务归属不一致')
            job = self.job_records.create_job_run(run,connection=conn)
            event = _TaskEvent(id=f'event_{job.id}_start',workspace_id=job.workspace_id,task_id=job.task_id,
                job_id=job.id,event_type='native_operation_submitted',payload={**actor.identity_payload(),
                    'request_id':request_id,**identity},created_at=job.created_at)
            self.execution_records.append_task_event(event,connection=conn)
        return job,event

    def complete_native_operation(self,job,result,*,status,connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            if connection is None:
                conn.execute('BEGIN IMMEDIATE')
            current = self.job_records.get_job_run(job.workspace_id,job.id,connection=conn)
            if current.status=='queued' and status!='cancelled':
                self.job_records.claim_job_run(job.workspace_id,job.id,connection=conn)
            event = _TaskEvent(id=f'event_{job.id}_native_result',workspace_id=job.workspace_id,task_id=job.task_id,
                job_id=job.id,event_type='native_operation_result',payload=result)
            self.execution_records.append_task_event(event,connection=conn)
            self.job_records.finish_job_run(job.workspace_id,job.id,status=status,connection=conn,
                output_refs=[item['logical_ref'] for item in result.get('artifacts',[])],
                failure_reason=result.get('error') or f'native_operation_{status}' if status!='succeeded' else None,
                impact='原任务已采用成果保留' if status!='succeeded' else None,
                repair_action='读取原执行及实际停止/资源释放回执后继续' if status!='succeeded' else None)
            return event.id

    def native_operation_result(self,workspace_id,job_id,*,connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT json_remove(payload_json,'$.work_outputs') FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id,f'event_{job_id}_native_result')).fetchone()
            if row is None:
                return None
            value = json.loads(row[0])
            released = conn.execute('SELECT id FROM task_events WHERE workspace_id=? AND id=?',
                (workspace_id,f'event_{job_id}_native_released')).fetchone()
            return {**value,**({'release_state':'released'} if released else {})}

    def native_output_window(self,workspace_id,job_id,*,offset=0,limit=30):
        with self._connect() as conn:
            row = conn.execute("SELECT json_array_length(payload_json,'$.work_outputs') FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id,f'event_{job_id}_native_result')).fetchone()
            count = row[0] or 0 if row else 0
            values = conn.execute("SELECT output.value FROM task_events e,json_each(e.payload_json,'$.work_outputs') output "
                "WHERE e.workspace_id=? AND e.id=? ORDER BY cast(output.key AS INTEGER) LIMIT ? OFFSET ?",
                (workspace_id,f'event_{job_id}_native_result',limit,offset)).fetchall()
            return {'items':[json.loads(item[0]) for item in values],'total_count':count,'truncated':offset+len(values)<count}

    def native_output_for_path(self,workspace_id,job_id,path):
        with self._connect() as conn:
            row = conn.execute("SELECT output.value FROM task_events e,json_each(e.payload_json,'$.work_outputs') output "
                "WHERE e.workspace_id=? AND e.id=? AND json_extract(output.value,'$.path')=? LIMIT 1",
                (workspace_id,f'event_{job_id}_native_result',path)).fetchone()
            return json.loads(row[0]) if row else None

    def settle_native_execution(self,grant,*,elapsed_seconds,output_bytes,connection):
        job = self.job_records.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_native_usage_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_settled',payload={
                'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,'permit_generation':grant.generation,
                'elapsed_seconds':elapsed_seconds,'output_bytes':output_bytes,
                'output_coverage':'unknown' if output_bytes is None else 'known','usage_source':'local_retained_output_measurement'}),connection=connection)

    def mark_native_released(self,job):
        return self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_native_released',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='native_execution_released',payload={'release_state':'released'}))
