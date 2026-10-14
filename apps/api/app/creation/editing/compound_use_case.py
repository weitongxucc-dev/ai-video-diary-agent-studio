"""Concrete compound preview/selection/application with the existing joint save owner."""
from dataclasses import dataclass, replace
import re
import subprocess

from app.compound_edits import is_indexed_subtitle_command, compile_compound_selection
from app.creation.contracts import EditCommand
from app.creation.editing.compound import calculate_compound_edit, prepare_compound_proposal, apply_compound_proposal
from app.creation.editing.metadata import check_command
from app.creation.editing.metadata_use_case import (MetadataEditUseCase, MetadataEditResult,
    effective_audio_timeline, logical_montage_output_refs, prepare_changed_montage)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.creation_execution import CreationExecutionActor
from app.montage import creation_task_montage_plan_from_payload


@dataclass(frozen=True)
class CompoundPreviewCommand:
    workspace_id: str
    task_id: str
    actor_identity: dict
    base_version_id: str | None
    raw_command: str
    context_stage: str
    selected_object_refs: list[str]
    playhead_ms: int | None = None
    included_step_indices: list[int] | None = None
    selection_command: str | None = None
    source: dict | None = None


@dataclass(frozen=True)
class CompoundSelectionCommand:
    workspace_id: str
    task_id: str
    proposal_id: str
    actor_identity: dict
    included_step_indices: list[int] | None = None
    raw_command: str | None = None
    source: dict | None = None


@dataclass(frozen=True)
class PreparedCompoundEdit:
    task: object
    base: object
    before: object
    original_tracks: list
    candidate: object
    command: EditCommand
    intent: object
    proposal: object


class CompoundProposalMissing(KeyError):
    pass


class CompoundSelectionInputError(ValueError):
    pass


class CompoundAudioSourceChanged(EditConflict):
    pass


class CompoundEditUseCase:
    def __init__(self, repository, montage_edits: MetadataEditUseCase):
        if montage_edits.repository is not repository:
            raise ValueError('复合编辑必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits

    @staticmethod
    def applicable(raw_command):
        if not is_indexed_subtitle_command(raw_command) and not re.search(r'[，,；;]|同时|并且|然后', raw_command):
            return False
        return bool(re.search(r'音乐|配音|原声|音效|字幕|英文放在中文', raw_command))

    def prepare_preview(self, control: CompoundPreviewCommand):
        if not self.applicable(control.raw_command):
            return None
        task = self.repository.get_creation_task(control.workspace_id, control.task_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(control.workspace_id, task.id))
        current = self.repository.get_timeline_version(control.workspace_id, control.base_version_id) if control.base_version_id else None
        tracks = self.montage_edits.current_subtitle_tracks(control.workspace_id, task, current_version=current, montage=plan)
        calculated = calculate_compound_edit(plan, tracks, effective_audio_timeline(plan),
            raw_command=control.raw_command, selected_refs=control.selected_object_refs,
            base_version_id=control.base_version_id, playhead_ms=control.playhead_ms,
            included_step_indices=control.included_step_indices)
        if calculated is None:
            return None
        service = self.montage_edits.audio_service
        service.validate(control.workspace_id, task.id, calculated.plan.audio_timeline, calculated.plan.duration_seconds, draft_only=True)
        base = current if current is not None else self.montage_edits.ensure_montage_base(control.workspace_id, task, plan)
        task = self.repository.get_creation_task(control.workspace_id, task.id)
        command = EditCommand(control.workspace_id, task.id, control.actor_identity, base.id,
            'EditCompoundTimeline', {'base_version_id': base.id}, control.source)
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity,
            audio_service=service, audio_actor=CreationExecutionActor(control.workspace_id, command.actor_identity['member_id']))
        command, intent, proposal = prepare_compound_proposal(task, plan, command, calculated,
            raw_command=control.raw_command, context_stage=control.context_stage,
            selected_refs=control.selected_object_refs, playhead_ms=control.playhead_ms,
            selection_command=control.selection_command, verifier=verifier, output_refs=logical_montage_output_refs(plan))
        return PreparedCompoundEdit(task, base, plan, tracks, calculated, command, intent, proposal)

    def save_preview(self, prepared: PreparedCompoundEdit):
        return self.repository.save_clip_preview_proposal(prepared.intent, prepared.proposal,
            expected_version_id=prepared.base.id)

    def prepare_selection(self, control: CompoundSelectionCommand):
        task = self.repository.get_creation_task(control.workspace_id, control.task_id)
        try:
            proposal = self.repository.get_edit_proposal(control.workspace_id, control.proposal_id)
        except KeyError as exc:
            raise CompoundProposalMissing('修改提案不存在') from exc
        if proposal.task_id != task.id:
            raise CompoundProposalMissing('修改提案不属于当前任务')
        data = proposal.operation.get('payload', {})
        if proposal.status != 'proposed' or proposal.operation.get('operation_type') != 'EditCompoundTimeline' or not data.get('selection_enabled'):
            raise EditConflict('当前提案不能重新选择，请重新预览原始要求')
        if task.current_timeline_version_id != data.get('base_version_id'):
            raise EditConflict('时间线已变化，请重新预览原始要求')
        if (control.included_step_indices is None) == (control.raw_command is None):
            raise CompoundSelectionInputError('请提供保留项或调整要求，不能同时提供两种')
        try:
            included = control.included_step_indices if control.raw_command is None else compile_compound_selection(control.raw_command, data['steps'])
        except ValueError as exc:
            raise CompoundSelectionInputError(str(exc)) from exc
        intent = self.repository.get_edit_intent(control.workspace_id, proposal.intent_id)
        return self.prepare_preview(CompoundPreviewCommand(control.workspace_id, task.id, control.actor_identity,
            data['base_version_id'], intent.raw_command, intent.context_stage, intent.scope['context_refs'],
            intent.playhead_ms, included, control.raw_command, control.source))

    def _binding(self, command: EditCommand, proposal_id):
        task = self.repository.get_creation_task(command.workspace_id, command.task_id)
        check_command(task, EditCommand(command.workspace_id, command.task_id, command.actor_identity,
            task.current_timeline_version_id, 'Compound', {}))
        proposal = self.repository.get_edit_proposal(command.workspace_id, proposal_id)
        if proposal.task_id != command.task_id:
            raise KeyError(proposal_id)
        payload = proposal.operation.get('payload')
        if proposal.operation.get('operation_type') != 'EditCompoundTimeline' or not isinstance(payload, dict):
            raise EditConflict('提案不是复合编辑')
        if command.operation_type != 'EditCompoundTimeline' or command.values != payload:
            raise EditConflict('原复合提案输入已变化')
        intent = self.repository.get_edit_intent(command.workspace_id, proposal.intent_id)
        source = intent.constraints.get('request_source')
        if source != command.source:
            raise EditConflict('原提案来源已变化')
        member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
        if source is not None and source['member_id'] != member:
            raise PermissionError('提案不属于当前请求成员')
        return task, proposal, intent

    def read_result(self, command: EditCommand, proposal_id):
        task, proposal, _intent = self._binding(command, proposal_id)
        operations = [item for item in self.repository.list_edit_operations(command.workspace_id, task.id)
            if item.proposal_id == proposal.id]
        if not operations:
            return None
        operation = operations[0]
        version = self.repository.get_timeline_version(command.workspace_id, operation.result_version_id)
        montage = creation_task_montage_plan_from_payload(version.snapshot['montage'])
        return MetadataEditResult(operation, montage, task, None)

    def confirm(self, command: EditCommand, proposal_id):
        task, proposal, intent = self._binding(command, proposal_id)
        if proposal.status != 'proposed':
            raise EditConflict('creation task edit proposal is not confirmable')
        operation_payload = proposal.operation['payload']
        if task.current_timeline_version_id != operation_payload.get('base_version_id'):
            raise EditConflict('时间线已变化，请重新预览全部修改')
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(command.workspace_id, task.id))
        base = self.repository.get_timeline_version(command.workspace_id, task.current_timeline_version_id)
        tracks = self.montage_edits.current_subtitle_tracks(command.workspace_id, task, current_version=base, montage=montage)
        bound = intent.constraints.get('affected_source_scope')
        if bound is None:
            raise EditConflict('原复合提案未封存实际影响的来源范围，请为当前版本重新预览')
        command = EditSourceVerifier.restore_scope(command, bound)
        service = self.montage_edits.audio_service
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity,
            audio_service=service, audio_actor=CreationExecutionActor(command.workspace_id, command.actor_identity['member_id']))
        try:
            candidate, changed_tracks = apply_compound_proposal(task, montage, tracks, command,
                validate_audio=lambda graph, duration: service.validate(command.workspace_id, task.id, graph, duration, draft_only=True))
        except PermissionError:
            raise
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise CompoundAudioSourceChanged('音频来源已变化，请重新预览') from exc
        prepare_changed_montage(candidate, message='复合修改已应用，可重新渲染；一次撤销恢复全部修改。')
        return self.montage_edits.persist_montage_edit(workspace_id=command.workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=candidate, operation_type='EditCompoundTimeline', operation_payload=operation_payload,
            edited_subtitle_tracks=changed_tracks, proposal_source_command=command, proposal_source_verifier=verifier)
