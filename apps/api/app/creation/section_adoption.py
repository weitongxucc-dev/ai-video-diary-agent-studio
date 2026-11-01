"""Atomic adoption and restoration of one section's published timeline facts."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime
import json
import sqlite3
from uuid import uuid4

from app.creation.contracts import (
    EditIntent as _EditIntent, EditProposal as _EditProposal, EditOperation as _EditOperation,
    TimelineVersion as _TimelineVersion, TaskEvent as _TaskEvent, NarrationSegment as _NarrationSegment,
)
from app.creation.editing.records import EditRecordStore
from app.creation.execution_records import ExecutionRecordStore
from app.creation.job_records import JobRunRecordStore
from app.creation.lineage_records import LineageRecordStore
from app.creation.script_records import ScriptRecordStore
from app.creation.task_input_reads import require_creation_task
from app.creation.timeline_records import TimelineRecordStore
from app.creation.transactions import EditAdoptionTransaction
from app.models import utc_now
from app.persistence_json import _json_payload


class SectionAdoptionTransaction:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        execution_records: ExecutionRecordStore, lineage_records: LineageRecordStore,
        script_records: ScriptRecordStore, job_records: JobRunRecordStore,
        edit_records: EditRecordStore, edit_adoption: EditAdoptionTransaction,
        timeline_records: TimelineRecordStore,
        stage_section_version: Callable, stage_segment_state: Callable,
        read_creation_section_input: Callable, section_dependency_snapshot: Callable,
        replace_adopted_narration_segment: Callable,
    ) -> None:
        self._connect = connection_factory
        self.execution_records = execution_records
        self.lineage_records = lineage_records
        self.script_records = script_records
        self.job_records = job_records
        self.edit_records = edit_records
        self.edit_adoption = edit_adoption
        self.timeline_records = timeline_records
        self.stage_section_version = stage_section_version
        self.stage_segment_state = stage_segment_state
        self.read_creation_section_input = read_creation_section_input
        self.section_dependency_snapshot = section_dependency_snapshot
        self.replace_adopted_narration_segment = replace_adopted_narration_segment

    def publish_segment_version(self, event, artifacts, transform, edges, *, adopted, expected_version_id,
                                expected_segment_version, segment=None, timeline_change=None, connection):
        task = require_creation_task(connection,event.workspace_id,event.task_id)
        payload = event.payload
        if self.stage_section_version(task.workspace_id,task.id,payload['section_id'],connection=connection) != expected_segment_version:
            raise ValueError('发布前段落或时间线已变化；原采用版本保留')
        for artifact in artifacts:
            self.execution_records.create_artifact_record(artifact,connection=connection)
        self.lineage_records.create_transform_run(transform,connection=connection)
        for edge in edges:
            self.lineage_records.create_dependency_edge(edge,connection=connection)
        self.execution_records.append_task_event(event,connection=connection)
        if adopted:
            row = connection.execute('SELECT snapshot_json,sequence FROM timeline_versions WHERE workspace_id=? AND id=?',
                (task.workspace_id,task.current_timeline_version_id)).fetchone()
            snapshot = json.loads(row['snapshot_json']) if row else {}
            snapshot['stage_segments'] = {**snapshot.get('stage_segments',{}),payload['section_id']:event.id}
            invalidations = dict(snapshot.get('stage_segment_invalidations', {}))
            invalidations.pop(payload['section_id'], None)
            snapshot['stage_segment_invalidations'] = invalidations
            # A local change invalidates the composed output while preserving its immutable ancestor.
            for key in ('rendered_video_ref','rendered_artifact_id','creation_render_request_id','output_refs'):
                snapshot.pop(key,None)
            operation = None
            tracks = []
            if segment is not None:
                header, _ = self.script_records.read_execution_script_metadata(connection, task.workspace_id, payload['script_id'])
                before = self.read_creation_section_input(task, header, payload['section_id'], connection=connection)['audio']
                if before is None:
                    raise ValueError('局部修订缺少原配音分段')
                before_state = self.stage_segment_state(task.workspace_id,task.id,payload['section_id'],connection=connection)
                if before_state is None or task.current_timeline_version_id is None:
                    raise ValueError('局部修订缺少原采用段及历史基础，未覆盖原成果')
                history = {'section_id':payload['section_id'],'before_segment':asdict(before),'after_segment':asdict(segment),
                    'before_publication':before_state['segment_version'],'after_publication':event.id,
                    'request_id':payload['request_id'],'input_id':payload['input_id'],
                    'before_dependencies':self.section_dependency_snapshot(task.workspace_id,task.id,payload['section_id'],connection=connection)}
                history = json.loads(json.dumps(history,default=lambda value:value.isoformat()))
                result_version_id = f'timeline_{uuid4().hex}'
                history['before_timeline_version_id'] = task.current_timeline_version_id
                history['after_timeline_version_id'] = result_version_id
                if timeline_change is None:
                    raise ValueError('局部修改缺少同源时间线联动负责人')
                snapshot,tracks = timeline_change(task,snapshot,payload['section_id'],payload['montage'],segment,
                    result_version_id,self.job_records.get_job_run(task.workspace_id,event.job_id,connection=connection),connection)
                operation_id = f'operation_{uuid4().hex}'
                history['operation_id'] = operation_id
                intent = _EditIntent(id=f'intent_{uuid4().hex}',workspace_id=task.workspace_id,task_id=task.id,
                    raw_command='受约束局部文案修订',context_stage='narration',selected_object_refs=[f'section:{payload["section_id"]}'],
                    playhead_ms=None,intent_type='revise_script_section',scope={'section_id':payload['section_id']},
                    constraints={},confidence=1.0,status='interpreted')
                self.edit_records.create_edit_intent(intent,connection=connection,source=None)
                proposal = _EditProposal(id=f'proposal_{uuid4().hex}',workspace_id=task.workspace_id,task_id=task.id,
                    intent_id=intent.id,summary='更新目标段的正文、配音、字幕及镜头',target_refs=[f'section:{payload["section_id"]}'],
                    operation={'operation_type':'ReviseScriptSection','payload':history},invalidated_refs=[],preview={},external_effects=[])
                self.edit_records.create_edit_proposal(proposal,connection=connection)
                applied_at = utc_now()
                operation = _EditOperation(id=operation_id,workspace_id=task.workspace_id,task_id=task.id,proposal_id=proposal.id,
                    operation_type='ReviseScriptSection',payload=history,base_version_id=task.current_timeline_version_id,
                    result_version_id=result_version_id,status='applied',created_at=applied_at,applied_at=applied_at)
            version = _TimelineVersion(id=f'timeline_{uuid4().hex}',workspace_id=task.workspace_id,task_id=task.id,
                parent_version_id=task.current_timeline_version_id,sequence=row['sequence']+1 if row else 1,
                snapshot=snapshot,created_by_operation_id=None)
            if operation is not None:
                version = replace(version,id=operation.result_version_id,created_by_operation_id=operation.id)
                full = snapshot.get('montage')
                self.edit_adoption.confirm(task.workspace_id,task.id,proposal.id,operation=operation,result_version=version,
                    montage_plan_id=full['id'] if full else None,montage_payload=full,subtitle_tracks=tracks,connection=connection)
            else:
                self.timeline_records.create_timeline_version(version,connection=connection,expected_current_version_id=task.current_timeline_version_id)
            if segment is not None:
                self.replace_adopted_narration_segment(task, segment, connection=connection)
                self._invalidate_section_composition(task.workspace_id,task.id,connection)
                self.execution_records.append_task_event(_TaskEvent(id=f'event_{operation.id}_dependencies',workspace_id=task.workspace_id,
                    task_id=task.id,job_id=event.job_id,event_type='section_edit_dependencies',payload={
                    'operation_id':operation.id,'dependencies':self.section_dependency_snapshot(task.workspace_id,task.id,
                    payload['section_id'],connection=connection)}),connection=connection)
        return event.id

    def _invalidate_section_composition(self,workspace_id,task_id,connection):
        task = require_creation_task(connection,workspace_id,task_id)
        task.narration_cache_key = None
        task.output_ready = False
        task.stage,task.stage_status,task.status = 'narration','not_started','running'
        task.recovery_point = 'narration'
        task.updated_at = utc_now()
        connection.execute('UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?',
            (_json_payload(task),task.updated_at.isoformat(),workspace_id,task_id))
        return task

    def restore_script_section(self,workspace_id,task_id,*,history,expected_dependencies,redo,
                               changed_version_id,restore_version_id,timeline_change,job,control_payload=None,write_guard=None,
                               execution_readiness_write=None):
        """Restore one edit on the latest facts, with one owner and one SQLite commit."""
        side,expected = ('after','before') if redo else ('before','after')
        section_id = history['section_id']
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            task = require_creation_task(conn,workspace_id,task_id)
            state = self.stage_segment_state(workspace_id,task_id,section_id,connection=conn)
            if state is None or state['segment_version'] != history[f'{expected}_publication']:
                raise ValueError('目标段在修改后已变化，不能覆盖新的采用成果')
            actual = self.section_dependency_snapshot(workspace_id,task_id,section_id,connection=conn)
            if json.dumps(actual,sort_keys=True,default=str) != json.dumps(expected_dependencies,sort_keys=True,default=str):
                raise ValueError('目标段的实际音画、字幕或来源依赖已变化，请先核对冲突')
            publication = self.execution_records.get_task_event(workspace_id,history[f'{side}_publication'],connection=conn)
            if publication.task_id != task_id or publication.event_type != 'segment_publication':
                raise ValueError('历史段版本不属于当前任务')
            values = dict(history[f'{side}_segment'])
            for key in ('created_at','updated_at'):
                values[key] = datetime.fromisoformat(values[key])
            segment = _NarrationSegment(**values)
            segment = replace(segment,updated_at=utc_now())
            if publication.payload['narration_reference'] != segment.audio_reference or publication.payload['text'] != segment.text:
                raise ValueError('历史正文、配音与采用段来源不一致')
            current = self.timeline_records.get_timeline_version(workspace_id,task.current_timeline_version_id,connection=conn)
            snapshot = dict(current.snapshot)
            snapshot['stage_segments'] = {**snapshot.get('stage_segments',{}),section_id:publication.id}
            version_id = f'timeline_{uuid4().hex}'
            persisted_job = self.job_records.get_job_run(workspace_id,job.id,connection=conn)
            if persisted_job.task_id!=task_id or persisted_job.kind!='section_timeline_restore' or persisted_job.status!='running':
                raise ValueError('局部恢复作业不属于当前运行请求')
            source_version = self.timeline_records.get_timeline_version(workspace_id,history[f'{expected}_timeline_version_id'],connection=conn)
            desired_version = self.timeline_records.get_timeline_version(workspace_id,history[f'{side}_timeline_version_id'],connection=conn)
            if source_version.task_id!=task_id or desired_version.task_id!=task_id:
                raise ValueError('局部恢复来源版本不属于当前任务')
            invalidations = dict(snapshot.get('stage_segment_invalidations', {}))
            desired_invalidation = desired_version.snapshot.get('stage_segment_invalidations', {}).get(section_id)
            if desired_invalidation is not None and desired_invalidation['publication_id'] == publication.id:
                invalidations[section_id] = desired_invalidation
            else:
                invalidations.pop(section_id, None)
            snapshot['stage_segment_invalidations'] = invalidations
            snapshot,tracks = timeline_change.restore(task,snapshot,section_id,publication.payload['montage'],segment,
                version_id,job,conn,source_version=source_version,desired_version=desired_version)
            action = '重做' if redo else '撤销'
            operation_type = 'RedoTimelineVersion' if redo else 'UndoTimelineVersion'
            intent = self.edit_records.create_edit_intent(_EditIntent(id=f'intent_{uuid4().hex}',workspace_id=workspace_id,task_id=task_id,
                raw_command=f'{action}目标段修改',context_stage='narration',selected_object_refs=[f'section:{section_id}'],
                playhead_ms=None,intent_type='redo_timeline' if redo else 'undo_timeline',scope={'section_id':section_id},
                constraints={},confidence=1.0,status='interpreted'),connection=conn,source=None)
            payload = {'changed_version_id':changed_version_id,'restore_version_id':restore_version_id,
                'base_version_id':current.id,'section_history':history,'section_restore_side':side,**(control_payload or {})}
            proposal = self.edit_records.create_edit_proposal(_EditProposal(id=f'proposal_{uuid4().hex}',workspace_id=workspace_id,
                task_id=task_id,intent_id=intent.id,summary=f'{action}目标段修改，保留其他片段的后续成果',
                target_refs=[f'section:{section_id}'],operation={'operation_type':operation_type,'payload':payload},
                invalidated_refs=[],preview={},external_effects=[]),connection=conn)
            applied_at = utc_now()
            operation = _EditOperation(id=f'operation_{uuid4().hex}',workspace_id=workspace_id,task_id=task_id,
                proposal_id=proposal.id,operation_type=operation_type,payload=payload,base_version_id=current.id,
                result_version_id=version_id,status='applied',created_at=applied_at,applied_at=applied_at)
            version = _TimelineVersion(version_id,workspace_id,task_id,current.id,current.sequence+1,snapshot,operation.id)
            full = snapshot.get('montage')
            self.edit_adoption.confirm(workspace_id,task_id,proposal.id,operation=operation,result_version=version,
                montage_plan_id=full['id'] if full else None,montage_payload=full,subtitle_tracks=tracks,connection=conn)
            if execution_readiness_write is not None:
                readiness=execution_readiness_write(conn)
                self.execution_records.append_task_event(_TaskEvent(f'event_{operation.id}_execution_readiness',workspace_id,task_id,None,
                    'edit_execution_readiness',{'operation_id':operation.id,**readiness}),connection=conn)
            self.replace_adopted_narration_segment(task, segment, connection=conn)
            task = self._invalidate_section_composition(workspace_id,task_id,conn)
            self.execution_records.append_task_event(_TaskEvent(id=f'event_{operation.id}_dependencies',workspace_id=workspace_id,task_id=task_id,
                job_id=job.id,event_type='section_edit_dependencies',payload={'operation_id':operation.id,
                'dependencies':self.section_dependency_snapshot(workspace_id,task_id,section_id,connection=conn)}),connection=conn)
            self.job_records.finish_job_run(workspace_id,job.id,status='succeeded',output_refs=[track.artifact_ref for track in tracks
                if track.artifact_ref and track.transform_id==f'transform_artifact_{version_id}_{track.language}'],connection=conn)
        return operation,full,task
