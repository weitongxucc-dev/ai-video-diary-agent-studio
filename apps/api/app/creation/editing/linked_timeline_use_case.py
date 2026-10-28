"""One closed linked-timeline family with atomic application and fixed-result recovery."""

from app.creation.contracts import EditIntent
from app.creation.editing.metadata_use_case import EditPreviewRejected
from app.creation.contracts import EditProposal
from app.creation.contracts import TaskEvent
from datetime import UTC
from app.audio_timeline import bounded_audio_context
from datetime import datetime
from app.visual_effects import fade_filters
from app.linked_timeline import remove_time_ranges
from dataclasses import dataclass, field, replace
import subprocess

from app.creation.contracts import EditCommand
from app.creation.editing.linked import (prepare_linked_speed, apply_linked_speed,
    prepare_linked_splice, apply_linked_splice, prepare_linked_insert, apply_linked_insert)
from app.creation.editing.metadata import check_command
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, MetadataEditResult, effective_audio_timeline,
    logical_montage_output_refs, prepare_changed_montage, LegacyMontageReference, InvalidMontage,
)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.montage import creation_task_montage_plan_from_payload, MontageClip
from app.source_policy import knowledge_item_readable as content_readable
from app.production import material_segment_eligible, effective_segment_usage_role
from uuid import uuid4
from app.task_audio import media_digest

LINKED_EDIT_OPERATIONS = frozenset({'ChangeLinkedSpeed', 'DeleteClip', 'DuplicateClip', 'InsertClip'})


@dataclass(frozen=True)
class LinkedEditApplyCommand:
    workspace_id: str
    task_id: str
    proposal_id: str
    actor_identity: dict
    member_id: str | None
    source: dict | None = None
    request_id: str | None = None
    expected_base_version_id: str | None = None
    base_version_submitted: bool = False
    expected_input_digest: str | None = None
    request_fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LinkedEditApplicationResult:
    outcome: MetadataEditResult
    event_id: str | None
    record_projection: bool
    newly_applied: bool


@dataclass(frozen=True)
class LinkedEditSavedResult:
    proposal: object
    input_digest: str
    outcome: MetadataEditResult | None


class LinkedEditPreviewFailed(ValueError):
    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


class LinkedEditInputError(ValueError):
    pass


class InsertMaterialMissing(KeyError):
    pass


class InsertSourceRangeError(ValueError):
    pass


class LinkedTimelineEditUseCase:
    def __init__(self, repository, montage_edits: MetadataEditUseCase):
        if montage_edits.repository is not repository:
            raise ValueError('关联编辑必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits

    @staticmethod
    def input_digest(task, proposal, source):
        return media_digest([task.id, proposal.id, proposal.operation, proposal.target_refs,
            source.get('source_facts') if source else None])

    def preview(self, command: EditCommand):
        operation = command.operation_type
        if operation not in LINKED_EDIT_OPERATIONS:
            raise LinkedEditInputError('提案不是关联时轴编辑')
        if operation == 'ChangeLinkedSpeed':
            if (not command.values.get('clip_id') or command.values.get('playback_rate') is None
                    or command.values.keys() - {'base_version_id', 'clip_id', 'playback_rate'}):
                raise ValueError('关联变速需要镜头和倍速，不混入其他操作')
        elif operation in {'DeleteClip', 'DuplicateClip'}:
            if not command.values.get('clip_id') or command.values.keys() - {'base_version_id', 'clip_id'}:
                raise LinkedEditInputError('关联删除或复制只接受一个镜头，不混入其他修改')
        elif not command.values.get('clip_id') or command.values.keys() - {
                'base_version_id', 'clip_id', 'asset_id', 'segment_id', 'source_start_seconds'}:
            raise LinkedEditInputError('插入镜头只接受一个插入位置和一段素材，不混入其他修改')
        task = self.repository.task_records.get_creation_task(command.workspace_id, command.task_id)
        inserted = self._inserted_clip(command) if operation == 'InsertClip' else None
        if operation == 'ChangeLinkedSpeed':
            check_command(task, command)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(command.workspace_id, command.task_id))
        base = self.montage_edits.ensure_montage_base(command.workspace_id, task, plan, source=command.source)
        task = self.repository.task_records.get_creation_task(command.workspace_id, command.task_id)
        command = replace(command, base_version_id=base.id, values={**command.values, 'base_version_id': base.id})
        tracks = self.montage_edits.current_subtitle_tracks(command.workspace_id, task, current_version=base, montage=plan)
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
        if operation == 'ChangeLinkedSpeed':
            prepared = prepare_linked_speed(task, plan, tracks, effective_audio_timeline(plan), command,
                verifier=verifier, output_refs=logical_montage_output_refs(plan),
                validate_audio=lambda audio, duration: self.montage_edits.audio_service.validate(
                    command.workspace_id, command.task_id, audio, duration, draft_only=True))
        elif operation in {'DeleteClip', 'DuplicateClip'}:
            prepared = prepare_linked_splice(task, plan, tracks, effective_audio_timeline(plan), command,
                verifier=verifier, output_refs=logical_montage_output_refs(plan))
        else:
            prepared = prepare_linked_insert(task, plan, tracks, effective_audio_timeline(plan), command, inserted,
                verifier=verifier, output_refs=logical_montage_output_refs(plan))
        return self.repository.save_clip_preview_proposal(prepared.intent, prepared.proposal, expected_version_id=base.id)

    def _inserted_clip(self, command: EditCommand) -> MontageClip:
        values = command.values
        if not values.get('asset_id') or not values.get('segment_id'):
            raise LinkedEditInputError('插入镜头需要指定素材片段')
        try:
            asset = self.repository.get_asset(command.workspace_id, values['asset_id'])
            segment = next(item for item in asset.segments if item.id == values['segment_id'])
        except (KeyError, StopIteration) as exc:
            raise InsertMaterialMissing('material segment not found') from exc
        reader = command.actor_identity['material_reader_identity']
        if not content_readable(user_id=reader['member_id'], local_diagnostic=reader['local_diagnostic'],
                visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError('当前成员无权使用该素材')
        if not material_segment_eligible(asset, segment, command.workspace_id):
            raise EditConflict('素材片段当前不可用，请检查来源、范围和使用限制')
        source_min, source_max = segment.start_seconds, segment.end_seconds
        source_start = values['source_start_seconds'] if values.get('source_start_seconds') is not None else source_min
        if source_start < source_min - 1e-6 or source_start >= source_max - 1e-6:
            raise InsertSourceRangeError(f'插入起点须在 {source_min:.1f}–{max(source_min, source_max - 0.04):.1f} 秒之间。')
        return MontageClip(id=f'clip_{uuid4().hex[:12]}', cue_id='', asset_id=asset.id, segment_id=segment.id,
            source_path=self.repository.workspace_media_reference_for_asset(command.workspace_id, asset.id),
            source_start_seconds=round(source_start, 3), source_end_seconds=round(source_max, 3),
            timeline_start_seconds=0.0, timeline_end_seconds=0.0, match_score=1.0,
            match_reasons=['由用户插入', '当前工作区可用素材'], needs_confirmation=False,
            role='a_roll' if effective_segment_usage_role(asset, segment) == 'a_roll' else 'b_roll', manually_selected=True)

    def _binding(self, command: LinkedEditApplyCommand):
        task = self.repository.task_records.get_creation_task(command.workspace_id, command.task_id)
        check_command(task, EditCommand(command.workspace_id, command.task_id, command.actor_identity,
            task.current_timeline_version_id, 'LinkedTimeline', {}))
        proposal = self.repository.get_edit_proposal(command.workspace_id, command.proposal_id)
        if proposal.task_id != command.task_id:
            raise KeyError(command.proposal_id)
        if proposal.operation.get('operation_type') not in LINKED_EDIT_OPERATIONS:
            raise EditConflict('提案不是关联时轴编辑')
        intent = self.repository.get_edit_intent(command.workspace_id, proposal.intent_id)
        source = intent.constraints.get('request_source')
        if source != command.source:
            raise EditConflict('原提案来源已变化')
        if source is not None and source['member_id'] != command.member_id:
            raise PermissionError('提案不属于当前请求成员')
        return task, proposal, intent, source

    def _saved_result(self, task, proposal):
        operations = [item for item in self.repository.list_edit_operations(task.workspace_id, task.id)
            if item.proposal_id == proposal.id]
        if not operations:
            return None
        operation = operations[0]
        version = self.repository.get_timeline_version(task.workspace_id, operation.result_version_id)
        montage = creation_task_montage_plan_from_payload(version.snapshot['montage'])
        return MetadataEditResult(operation, montage, task, None)

    def read_result(self, command: LinkedEditApplyCommand) -> LinkedEditSavedResult:
        task, proposal, _intent, source = self._binding(command)
        return LinkedEditSavedResult(proposal, self.input_digest(task, proposal, source), self._saved_result(task, proposal))

    def confirm_remove_silence(self, task, proposal):
        workspace_id, task_id = task.workspace_id, task.id
        operation_type = proposal.operation.get('operation_type')
        operation_payload = proposal.operation.get('payload')
        if task.current_timeline_version_id != operation_payload.get('base_version_id'):
            raise EditPreviewRejected(status_code=409, detail='时间线已变化，请重新预览停顿剪辑。')
        base = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        tracks = self.montage_edits.current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        try:
            updated, tracks = remove_time_ranges(plan, tracks, effective_audio_timeline(plan), operation_payload['removed_ranges_ms'])
            self.montage_edits.audio_service.validate(workspace_id, task.id, updated.audio_timeline, updated.duration_seconds, draft_only=True)
            prepare_changed_montage(updated, message='停顿剪辑已应用；声音、字幕与画面同步缩短，请重新渲染。')
        except (KeyError, TypeError, ValueError) as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        try:
            return self.montage_edits.persist_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base, updated_plan=updated, operation_type=operation_type, operation_payload=operation_payload, edited_subtitle_tracks=tracks)
        except (LegacyMontageReference, InvalidMontage):
            raise
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc

    def confirm(self, control: LinkedEditApplyCommand) -> LinkedEditApplicationResult:
        task, proposal, intent, source = self._binding(control)
        payload = proposal.operation.get('payload')
        if not isinstance(payload, dict):
            raise EditConflict('creation task edit proposal is not confirmable')
        digest = self.input_digest(task, proposal, source)
        base_id = payload.get('base_version_id')
        if ((control.base_version_submitted and control.expected_base_version_id != base_id)
                or (control.expected_input_digest is not None and control.expected_input_digest != digest)):
            raise EditConflict('请求与原提案基础版本或输入摘要不一致')
        event_id = None
        if control.request_id is not None:
            try:
                event_id, fresh = self.repository.claim_edit_request(task, control.member_id, control.request_id, 'apply',
                    {'proposal_id': proposal.id, 'base_version_id': base_id, 'input_digest': digest, 'request': control.request_fields})
            except ValueError as exc:
                raise EditConflict(str(exc)) from exc
            saved = self._saved_result(task, proposal)
            if saved is not None:
                missing_projection = self.repository.read_edit_request_result(task, event_id) is None
                return LinkedEditApplicationResult(saved, event_id, missing_projection, False)
            if not fresh:
                raise EditConflict('原应用请求结果尚未确定，请查询原提案结果，不重复执行')
        if proposal.status != 'proposed':
            raise EditConflict('creation task edit proposal is not confirmable')
        if task.current_timeline_version_id != base_id:
            message = ('时间线已变化，请重新预览关联变速' if proposal.operation['operation_type'] == 'ChangeLinkedSpeed'
                else '时间线已变化，请重新预览插入镜头' if proposal.operation['operation_type'] == 'InsertClip'
                else '时间线已变化，请重新预览关联编辑')
            raise EditConflict(message)
        operation = proposal.operation['operation_type']
        if operation == 'InsertClip':
            required = ('clip_id', 'asset_id', 'segment_id')
            source_start = payload.get('source_start_seconds')
            if (any(not isinstance(payload.get(key), str) or not payload.get(key) for key in required)
                    or (source_start is not None and (isinstance(source_start, bool) or not isinstance(source_start, (int, float))))):
                raise EditConflict('invalid creation task insert operation')
            inserted = self._inserted_clip(EditCommand(control.workspace_id, control.task_id,
                control.actor_identity, base_id, operation, payload, source))
        else:
            inserted = None
        bound = intent.constraints.get('affected_source_scope')
        if bound is None:
            raise EditConflict('原关联提案未封存实际影响的来源范围，请为当前版本重新预览')
        command = EditSourceVerifier.restore_scope(EditCommand(control.workspace_id, control.task_id,
            control.actor_identity, base_id, operation, payload, source), bound)
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
        verifier.verify(command, None)
        base = self.repository.get_timeline_version(control.workspace_id, base_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(control.workspace_id, control.task_id))
        tracks = self.montage_edits.current_subtitle_tracks(control.workspace_id, task, current_version=base, montage=plan)
        if operation == 'ChangeLinkedSpeed':
            updated, tracks = apply_linked_speed(task, plan, tracks, effective_audio_timeline(plan), command,
                validate_audio=lambda audio, duration: self.montage_edits.audio_service.validate(
                    control.workspace_id, control.task_id, audio, duration, draft_only=True))
            message = '关联变速已应用，声音、字幕与后续内容同步；请重新渲染。'
        elif operation in {'DeleteClip', 'DuplicateClip'}:
            updated, tracks = apply_linked_splice(task, plan, tracks, effective_audio_timeline(plan), command)
            message = '镜头及关联音轨、字幕已更新，请重新渲染。'
        else:
            updated, tracks = apply_linked_insert(task, plan, tracks, effective_audio_timeline(plan), command, inserted)
            message = '镜头及关联音轨、字幕已更新，请重新渲染。'
        prepare_changed_montage(updated, message=message)
        outcome = self.montage_edits.persist_montage_edit(workspace_id=control.workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=updated, operation_type=operation, operation_payload=payload,
            edited_subtitle_tracks=tracks, proposal_source_command=command, proposal_source_verifier=verifier)
        return LinkedEditApplicationResult(outcome, event_id, event_id is not None, True)

    def apply(self, command: EditCommand) -> MetadataEditResult:
        try:
            _intent, proposal = self.preview(command)
        except (PermissionError, EditConflict, LinkedEditInputError, InsertMaterialMissing, InsertSourceRangeError):
            raise
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            if command.operation_type == 'ChangeLinkedSpeed':
                raise LinkedEditPreviewFailed(exc) from exc
            raise
        member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
        return self.confirm(LinkedEditApplyCommand(command.workspace_id, command.task_id, proposal.id,
            command.actor_identity, member, source=command.source)).outcome


    def preview_remove_silence(self, task, payload, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        from app.silence_edits import detect_silence_cuts
        if len(payload.selected_object_refs) != 1 or not payload.selected_object_refs[0].startswith('clip:'):
            raise EditPreviewRejected(status_code=422, detail='请先选择要剪掉停顿的镜头。')
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        clip = next((c for c in plan.clips if f'clip:{c.id}' == payload.selected_object_refs[0]), None)
        if clip is None:
            raise EditPreviewRejected(status_code=404, detail='所选镜头不存在。')
        if clip.locked:
            raise EditPreviewRejected(status_code=409, detail='所选镜头已锁定，请先解锁。')
        base = self.montage_edits.ensure_montage_base(workspace_id, task, plan, source=request_source)
        tracks = self.montage_edits.current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        graph = effective_audio_timeline(plan)
        voice_ids = {t.id for t in graph.tracks if t.role in {'narration', 'original'} and (not t.muted) and any((not c.muted for c in t.clips))}
        if not voice_ids:
            raise EditPreviewRejected(status_code=409, detail='所选任务没有可检测的配音或原声，请先加入讲话音轨。')
        service = self.montage_edits.audio_service
        try:
            analysis = service.publish(workspace_id, task.id, kind='audio_preview', parameters={'action': 'silence_analysis', 'clip_id': clip.id}, input_refs=sorted({c.source_ref for t in graph.tracks if t.id in voice_ids for c in t.clips}), operation='audio_mix', media_input={'audio_timeline': graph.model_dump(), 'duration_seconds': plan.duration_seconds, 'normalize_voice_sources': plan.narration_linkage_created_audio_timeline, 'start_seconds': clip.timeline_start_seconds, 'end_seconds': clip.timeline_end_seconds, 'solo_track_ids': sorted(voice_ids)}, execution=execution)
            with bounded_audio_context(execution[0]) as audio_context:
                cuts = detect_silence_cuts(self.montage_edits.artifact_store.resolve_file(analysis.logical_ref), clip.timeline_end_seconds - clip.timeline_start_seconds, clip.timeline_start_seconds, context=audio_context)
            active_context, active_job = execution[:2]
            active_context.check()
            analysis_transform = service._transform_for(workspace_id, task.id, analysis)
            measured_event = TaskEvent(id=f'event_{active_job.id}_{analysis.id}_silence_detected', workspace_id=workspace_id, task_id=task.id, job_id=active_job.id, event_type='silence_analysis_result', payload={'base_version_id': base.id, 'clip_id': clip.id, 'analysis_ref': analysis.logical_ref, 'analysis_sha256': analysis.sha256, 'transform_id': analysis_transform.id, 'removed_ranges_ms': cuts}, created_at=datetime.now(UTC))
            try:
                saved_event = self.repository.execution_records.get_task_event(workspace_id, measured_event.id)
            except KeyError:
                self.repository.execution_records.append_task_event(measured_event)
            else:
                if replace(saved_event, created_at=measured_event.created_at) != measured_event:
                    raise ValueError('同一分析产物的静音结果已变化，未生成新预览')
            edited, changed_tracks = remove_time_ranges(plan, tracks, graph, cuts)
            service.validate(workspace_id, task.id, edited.audio_timeline, edited.duration_seconds, draft_only=True, actor=actor)
            for c in edited.clips:
                fade_filters(c)
            previews = {}
            for side, candidate, captions in (('before', plan, tracks), ('after', edited, changed_tracks)):
                record = self.montage_edits.picture_renderer.publish_picture(workspace_id, task, base, candidate, captions, kind='silence_preview', parameters={'side': side, 'removed_ranges_ms': cuts, 'analysis_ref': analysis.logical_ref}, execution=execution)
                previews[side] = {'ref': record.logical_ref, 'label': '修改前' if side == 'before' else '缩短停顿后', 'detail': f'{candidate.duration_seconds:.3f} 秒', 'video_url': f'/api/creation-tasks/{task.id}/visual/artifacts/{record.id}'}
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else '本地停顿预览失败，请检查音轨、素材和外接盘。') from exc
        if self.repository.task_records.get_creation_task(workspace_id, task.id).current_timeline_version_id != base.id:
            raise EditPreviewRejected(status_code=409, detail='预览期间时间线已变化，请重新预览。')
        removed = sum((b - a for a, b in cuts)) / 1000
        detail = '、'.join((f'{a / 1000:.3f}–{b / 1000:.3f}秒' for a, b in cuts))
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, raw_command=payload.raw_command, context_stage=payload.context_stage, selected_object_refs=payload.selected_object_refs, playhead_ms=payload.playhead_ms, intent_type='remove_silence', scope={'clip_id': clip.id}, constraints={'linked_tracks': True}, confidence=1, status='interpreted'), source=request_source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, intent_id=intent.id, summary=f'缩短 {len(cuts)} 处静音候选，共 {removed:.3f} 秒；画面、声音、字幕与叠加同步前移。', target_refs=payload.selected_object_refs, operation={'operation_type': 'RemoveSilence', 'payload': {'base_version_id': base.id, 'clip_id': clip.id, 'removed_ranges_ms': cuts, 'analysis_ref': analysis.logical_ref}}, invalidated_refs=logical_montage_output_refs(plan), preview=previews, external_effects=[f'删除范围：{detail}', '按低于-40dB且持续0.45秒检测，两端至少保留0.12秒。低声讲话也可能被识别为静音，请试听后确认；不改变保留部分的语速。']))
        return (intent, proposal)
