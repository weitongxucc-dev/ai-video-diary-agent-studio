"""Finite historical restore using original projection and save owners."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from app.creation.contracts import EditIntent, EditProposal, SubtitleTrack, CoverAsset, TimelineVersion
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, MetadataEditResult, prepare_changed_montage, logical_montage_output_refs,
)
from app.creation.editing.picture import EditConflict
from app.creation.projection import undo_target_versions, redo_target_versions
from app.creation.transactions import clone_subtitle_track, clone_cover_asset
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_from_payload

if TYPE_CHECKING:
    from app.production import CreationTask, NarrationSegment, NarrationArtifactRecord, ProductionRepository


@dataclass(frozen=True)
class TimelineRestoreCommand:
    workspace_id: str
    task: CreationTask
    redo: bool


@dataclass(frozen=True)
class PreparedTimelineRestore:
    command: TimelineRestoreCommand
    changed_version: TimelineVersion
    target_version: TimelineVersion
    current_plan: CreationTaskMontagePlan
    restored_plan: CreationTaskMontagePlan
    restored_segments: list[NarrationSegment] | None
    restored_narration: NarrationArtifactRecord | None
    subtitle_cover_undo: bool
    operation_type: str
    action_label: str
    section_history: dict | None = None
    section_dependencies: dict | None = None


@dataclass(frozen=True)
class TimelineOutputQualification:
    prepared: PreparedTimelineRestore
    output_is_valid: bool


class TimelineRestoreUnavailable(EditConflict):
    def __init__(self, redo: bool):
        action_label = '重做' if redo else '撤销'
        self.details = {'status_code': 409,
            'error': 'timeline_redo_unavailable' if redo else 'timeline_undo_unavailable',
            'reason': f'当前没有可{action_label}的内容修改。', 'impact': '任务和时间线保持不变。',
            'repair_action': '先撤销一次修改，再使用重做。' if redo else '先完成一次镜头、配音或字幕修改，再使用撤销。',
            'retryable': False}
        super().__init__(self.details['reason'])


class TimelineRestoreUseCase:
    def __init__(self, repository: ProductionRepository, montage_edits: MetadataEditUseCase):
        if montage_edits.repository is not repository:
            raise ValueError('历史恢复必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits

    def prepare_restore(self, command: TimelineRestoreCommand) -> PreparedTimelineRestore:
        workspace_id, task = command.workspace_id, command.task
        task_id = task.id
        changed_version, target_version = (redo_target_versions if command.redo else undo_target_versions)(self.repository, workspace_id, task)
        operation_type = "RedoTimelineVersion" if command.redo else "UndoTimelineVersion"
        action_label = "重做" if command.redo else "撤销"
        if changed_version is None or target_version is None:
            raise TimelineRestoreUnavailable(command.redo)
        try:
            changed_operation = self.repository.get_edit_operation(
                workspace_id, changed_version.created_by_operation_id or ""
            )
        except KeyError as exc:
            raise EditConflict("timeline undo operation is unavailable") from exc
        history = changed_operation.payload if changed_operation.operation_type == 'ReviseScriptSection' else changed_operation.payload.get('section_history')
        if isinstance(history,dict) and 'before_publication' in history:
            expected = (history['before_dependencies'] if command.redo else self.repository.get_task_event(
                workspace_id,f'event_{history["operation_id"]}_dependencies').payload['dependencies'])
            # A prior scoped control version records the exact composed dependencies it created.
            cursor = task.current_timeline_version_id
            while cursor:
                recent = self.repository.get_timeline_version(workspace_id,cursor)
                if recent.created_by_operation_id:
                    control = self.repository.get_edit_operation(workspace_id,recent.created_by_operation_id)
                    if (control.payload.get('section_history',{}).get('after_publication') == history['after_publication']
                            and control.payload.get('section_restore_side') == ('before' if command.redo else 'after')):
                        expected = self.repository.get_task_event(workspace_id,f'event_{control.id}_dependencies').payload['dependencies']
                        break
                    if control.operation_type == 'ReviseScriptSection':
                        break
                cursor = recent.parent_version_id
            return PreparedTimelineRestore(command,changed_version,target_version,None,None,None,None,False,
                operation_type,action_label,history,expected)
        self.repository.verify_whole_timeline_restore(workspace_id,task_id,changed_version.id)
        subtitle_cover_undo = command.redo or changed_operation.operation_type in {
            "RedoTimelineVersion",
            "ConfigureSubtitleTrack",
            "GenerateCoverFromFrame",
            "UpdateSubtitleCue",
            "ReplaceSubtitleText",
            "SplitSubtitleCue",
            "MergeSubtitleCue",
            "DeleteSubtitleCue",
            "ChangeCanvas",
            "SetClipTransition",
            "EditGraphicOverlay",
            "EditCompoundTimeline",
            "RemoveSilence",
            "AlignSubtitleTiming",
            "ReorderClip",
            "ReorderTimelineItems",
        }
        montage_snapshot = target_version.snapshot.get("montage")
        if not isinstance(montage_snapshot, dict):
            raise EditConflict("timeline undo snapshot is unavailable")
        try:
            current_plan = creation_task_montage_plan_from_payload(
                self.repository.get_creation_task_montage_plan(workspace_id, task_id)
            )
            restored_plan = creation_task_montage_plan_from_payload(montage_snapshot)
            restored_segments = self.montage_edits.narration_segments_from_snapshot(target_version.snapshot)
            if restored_segments is not None:
                restored_at = utc_now()
                restored_segments = [replace(segment, updated_at=restored_at) for segment in restored_segments]
            restored_narration = None
            restored_cache_key = target_version.snapshot.get("narration_cache_key")
            if isinstance(restored_cache_key, str) and restored_cache_key:
                restored_narration = self.repository.get_narration_artifact(
                    workspace_id, restored_cache_key
                )
                if restored_narration is None:
                    raise ValueError("时间线引用的配音总轨已丢失")
        except (KeyError, ValueError) as exc:
            raise EditConflict("timeline undo snapshot is invalid") from exc

        return PreparedTimelineRestore(command, changed_version, target_version, current_plan, restored_plan,
            restored_segments, restored_narration, subtitle_cover_undo, operation_type, action_label)

    def apply_restore(self, prepared: PreparedTimelineRestore, qualification: TimelineOutputQualification) -> MetadataEditResult:
        if qualification.prepared is not prepared:
            raise EditConflict('历史输出资格不属于本次恢复准备')
        command = prepared.command
        workspace_id, task = command.workspace_id, command.task
        task_id = task.id
        if prepared.section_history is not None:
            from app.creation.section_change_use_case import SectionTimelineChange,SectionPreparationStale
            from app.creation.contracts import JobRun,NarrationSegment
            from datetime import datetime
            import hashlib
            import json
            history = prepared.section_history
            side = 'after' if command.redo else 'before'
            values = dict(history[f'{side}_segment'])
            for key in ('created_at','updated_at'):
                values[key] = datetime.fromisoformat(values[key])
            segment = NarrationSegment(**values)
            desired_version = self.repository.get_timeline_version(workspace_id,history[f'{side}_timeline_version_id'])
            publication = self.repository.get_task_event(workspace_id,history[f'{side}_publication'])
            job = self.repository.create_job_run(JobRun(id=f'job_{uuid4().hex}',workspace_id=workspace_id,task_id=task_id,
                kind='section_timeline_restore',idempotency_key=f'section_restore:{uuid4().hex}',
                input_digest=hashlib.sha256(json.dumps([history,command.redo,prepared.section_dependencies],sort_keys=True).encode()).hexdigest()))
            self.repository.claim_job_run(workspace_id,job.id)
            change = SectionTimelineChange(self.repository,self.montage_edits.artifact_store)
            try:
                for attempt in range(2):
                    change.prepare_current(workspace_id,task_id,history['section_id'],publication.payload['montage'],segment,job,
                        desired_version=desired_version)
                    try:
                        operation,payload,current = self.repository.restore_script_section(workspace_id,task_id,
                            history=history,expected_dependencies=prepared.section_dependencies,redo=command.redo,
                            changed_version_id=prepared.changed_version.id,restore_version_id=prepared.target_version.id,
                            timeline_change=change,job=job)
                        break
                    except SectionPreparationStale:
                        if attempt:
                            raise
            except Exception as exc:
                if self.repository.get_job_run(workspace_id,job.id).status=='running':
                    self.repository.finish_job_run(workspace_id,job.id,status='failed',failure_reason=str(exc),
                        impact='原时间线与已采用成果保留',repair_action='读取当前目标段及原恢复请求后核对冲突')
                raise
            plan = creation_task_montage_plan_from_payload(payload) if payload is not None else None
            return MetadataEditResult(operation,plan,current,None)
        changed_version, target_version = prepared.changed_version, prepared.target_version
        def verify_restore(connection):
            self.repository.verify_whole_timeline_restore(workspace_id,task_id,changed_version.id,connection=connection)
        current_plan, restored_plan = prepared.current_plan, prepared.restored_plan
        restored_segments, restored_narration = prepared.restored_segments, prepared.restored_narration
        subtitle_cover_undo = prepared.subtitle_cover_undo
        operation_type, action_label = prepared.operation_type, prepared.action_label
        restore_output = qualification.output_is_valid
        if not restore_output:
            restored_plan, _ = prepare_changed_montage(
                restored_plan, message="草稿已恢复；原成片依赖或产物已失效，请明确重新生成。",
            )
        current_version = self.repository.get_timeline_version(
            workspace_id, task.current_timeline_version_id or ""
        )
        restored_tracks: list[SubtitleTrack] = []
        restored_cover: CoverAsset | None = None
        result_version_id: str | None = None
        if subtitle_cover_undo:
            result_version_id = f"timeline_{uuid4().hex}"
            target_tracks = self.montage_edits.current_subtitle_tracks(
                workspace_id,
                task,
                current_version=target_version,
                montage=restored_plan,
            )
            restored_tracks = [
                clone_subtitle_track(track, timeline_version_id=result_version_id)
                for track in target_tracks
            ]
            target_cover = self.montage_edits.cover_asset_for_version(workspace_id, task.id, target_version)
            restored_cover = (
                clone_cover_asset(target_cover, timeline_version_id=result_version_id)
                if target_cover is not None
                else None
            )
            current_tracks = self.montage_edits.current_subtitle_tracks(
                workspace_id,
                task,
                current_version=current_version,
                montage=current_plan,
            )
            current_cover = self.montage_edits.cover_asset_for_version(workspace_id, task.id, current_version)
            restored_refs = {
                *logical_montage_output_refs(restored_plan),
                *(track.artifact_ref for track in target_tracks if track.artifact_ref),
                *([target_cover.artifact_ref] if target_cover and target_cover.artifact_ref else []),
            }
            current_refs = [
                *logical_montage_output_refs(current_plan),
                *(track.artifact_ref for track in current_tracks if track.artifact_ref),
                *([current_cover.artifact_ref] if current_cover and current_cover.artifact_ref else []),
            ]
            invalidated_refs = [reference for reference in current_refs if reference not in restored_refs]
        else:
            invalidated_refs = logical_montage_output_refs(current_plan)
            if not restore_output:
                restored_plan, _montage_payload = prepare_changed_montage(
                    restored_plan, message="草稿已恢复，可以重新生成。",
                )
        now = utc_now()
        intent = self.repository.create_edit_intent(
            EditIntent(
                id=f"intent_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                raw_command=f"{action_label}上一次内容修改",
                context_stage="subtitle_cover" if subtitle_cover_undo else "montage",
                selected_object_refs=[],
                playhead_ms=None,
                intent_type="redo_timeline" if command.redo else "undo_timeline",
                scope={"kind": "timeline", "version_id": changed_version.id},
                constraints={},
                confidence=1.0,
                status="interpreted",
                created_at=now,
            )
        )
        proposal = self.repository.create_edit_proposal(
            EditProposal(
                id=f"proposal_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                intent_id=intent.id,
                summary="重新应用刚才撤销的修改。" if command.redo else "恢复到上一次内容修改之前的版本。",
                target_refs=[f"timeline:{changed_version.id}"],
                operation={
                    "operation_type": operation_type,
                    "payload": {
                        "changed_version_id": changed_version.id,
                        "restore_version_id": target_version.id,
                        "base_version_id": task.current_timeline_version_id,
                    },
                },
                invalidated_refs=invalidated_refs,
                preview={
                    "before": {
                        "ref": f"timeline:{task.current_timeline_version_id}",
                        "label": "当前版本",
                        "detail": f"版本 {self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id or '').sequence}",
                    },
                    "after": {
                        "ref": f"timeline:{target_version.id}",
                        "label": "恢复的任务状态",
                        "detail": f"来源版本 {target_version.sequence}",
                    },
                },
                external_effects=[f"{action_label}会创建新的历史版本，不会删除原版本。"],
                created_at=now,
                updated_at=now,
            )
        )
        if subtitle_cover_undo and result_version_id is not None:
            return self.montage_edits.persist_subtitle_edit(
                workspace_id=workspace_id,
                task=task,
                proposal=proposal,
                base_version=current_version,
                result_version_id=result_version_id,
                operation_type=operation_type,
                operation_payload=dict(proposal.operation["payload"]),
                subtitle_tracks=restored_tracks,
                cover_asset=restored_cover,
                updated_plan=restored_plan,
                snapshot_source=target_version.snapshot,
                narration_segments=restored_segments,
                narration_artifact=restored_narration,
                verify_sources=verify_restore,
            )
        return self.montage_edits.persist_montage_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=current_version,
            updated_plan=restored_plan,
            operation_type=operation_type,
            operation_payload=dict(proposal.operation["payload"]),
            narration_segments=restored_segments,
            narration_artifact=restored_narration,
            subtitle_source_version=target_version,
            verify_sources=verify_restore,
        )
