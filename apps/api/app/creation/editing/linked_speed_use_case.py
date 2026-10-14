"""Linked-speed preview, atomic application and recovery from fixed saved facts."""
from dataclasses import dataclass, field, replace
import subprocess

from app.creation.contracts import EditCommand
from app.creation.editing.linked import prepare_linked_speed, apply_linked_speed
from app.creation.editing.metadata import check_command
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, MetadataEditResult, effective_audio_timeline,
    logical_montage_output_refs, prepare_changed_montage,
)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.montage import creation_task_montage_plan_from_payload
from app.task_audio import media_digest


@dataclass(frozen=True)
class LinkedSpeedApplyCommand:
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
class LinkedSpeedApplicationResult:
    outcome: MetadataEditResult
    event_id: str | None
    record_projection: bool
    newly_applied: bool


@dataclass(frozen=True)
class LinkedSpeedSavedResult:
    proposal: object
    input_digest: str
    outcome: MetadataEditResult | None


class LinkedSpeedPreviewFailed(ValueError):
    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


class LinkedSpeedEditUseCase:
    def __init__(self, repository, montage_edits: MetadataEditUseCase):
        if montage_edits.repository is not repository:
            raise ValueError('关联变速必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits

    @staticmethod
    def input_digest(task, proposal, source):
        return media_digest([task.id, proposal.id, proposal.operation, proposal.target_refs,
            source.get('source_facts') if source else None])

    def preview(self, command: EditCommand):
        if (command.operation_type != 'ChangeLinkedSpeed' or not command.values.get('clip_id')
                or command.values.get('playback_rate') is None
                or command.values.keys() - {'base_version_id', 'clip_id', 'playback_rate'}):
            raise ValueError('关联变速需要镜头和倍速，不混入其他操作')
        task = self.repository.get_creation_task(command.workspace_id, command.task_id)
        check_command(task, command)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(command.workspace_id, command.task_id))
        base = self.montage_edits.ensure_montage_base(command.workspace_id, task, plan)
        task = self.repository.get_creation_task(command.workspace_id, command.task_id)
        command = replace(command, base_version_id=base.id, values={**command.values, 'base_version_id': base.id})
        tracks = self.montage_edits.current_subtitle_tracks(command.workspace_id, task, current_version=base, montage=plan)
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
        prepared = prepare_linked_speed(task, plan, tracks, effective_audio_timeline(plan), command,
            verifier=verifier, output_refs=logical_montage_output_refs(plan),
            validate_audio=lambda audio, duration: self.montage_edits.audio_service.validate(
                command.workspace_id, command.task_id, audio, duration, draft_only=True))
        return self.repository.save_clip_preview_proposal(prepared.intent, prepared.proposal, expected_version_id=base.id)

    def _binding(self, command: LinkedSpeedApplyCommand):
        task = self.repository.get_creation_task(command.workspace_id, command.task_id)
        check_command(task, EditCommand(command.workspace_id, command.task_id, command.actor_identity,
            task.current_timeline_version_id, 'ChangeLinkedSpeed', {}))
        proposal = self.repository.get_edit_proposal(command.workspace_id, command.proposal_id)
        if proposal.task_id != command.task_id:
            raise KeyError(command.proposal_id)
        if proposal.operation.get('operation_type') != 'ChangeLinkedSpeed':
            raise EditConflict('提案不是关联变速')
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

    def read_result(self, command: LinkedSpeedApplyCommand) -> LinkedSpeedSavedResult:
        task, proposal, _intent, source = self._binding(command)
        return LinkedSpeedSavedResult(proposal, self.input_digest(task, proposal, source), self._saved_result(task, proposal))

    def confirm(self, control: LinkedSpeedApplyCommand) -> LinkedSpeedApplicationResult:
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
                return LinkedSpeedApplicationResult(saved, event_id, missing_projection, False)
            if not fresh:
                raise EditConflict('原应用请求结果尚未确定，请查询原提案结果，不重复执行')
        if proposal.status != 'proposed':
            raise EditConflict('creation task edit proposal is not confirmable')
        if task.current_timeline_version_id != base_id:
            raise EditConflict('时间线已变化，请重新预览关联变速')
        bound = intent.constraints.get('affected_source_scope')
        if bound is None:
            raise EditConflict('原关联提案未封存实际影响的来源范围，请为当前版本重新预览')
        command = EditSourceVerifier.restore_scope(EditCommand(control.workspace_id, control.task_id,
            control.actor_identity, base_id, 'ChangeLinkedSpeed', payload, source), bound)
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
        verifier.verify(command, None)
        base = self.repository.get_timeline_version(control.workspace_id, base_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(control.workspace_id, control.task_id))
        tracks = self.montage_edits.current_subtitle_tracks(control.workspace_id, task, current_version=base, montage=plan)
        updated, tracks = apply_linked_speed(task, plan, tracks, effective_audio_timeline(plan), command,
            validate_audio=lambda audio, duration: self.montage_edits.audio_service.validate(
                control.workspace_id, control.task_id, audio, duration, draft_only=True))
        prepare_changed_montage(updated, message='关联变速已应用，声音、字幕与后续内容同步；请重新渲染。')
        outcome = self.montage_edits.persist_montage_edit(workspace_id=control.workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=updated, operation_type='ChangeLinkedSpeed', operation_payload=payload,
            edited_subtitle_tracks=tracks, proposal_source_command=command, proposal_source_verifier=verifier)
        return LinkedSpeedApplicationResult(outcome, event_id, event_id is not None, True)

    def apply(self, command: EditCommand) -> MetadataEditResult:
        try:
            _intent, proposal = self.preview(command)
        except (PermissionError, EditConflict):
            raise
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise LinkedSpeedPreviewFailed(exc) from exc
        member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
        return self.confirm(LinkedSpeedApplyCommand(command.workspace_id, command.task_id, proposal.id,
            command.actor_identity, member, source=command.source)).outcome
