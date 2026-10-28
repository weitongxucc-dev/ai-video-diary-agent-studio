"""Concrete compound preview/selection/application with the existing joint save owner."""
from dataclasses import asdict, dataclass, replace
from collections import Counter
import json
from app.persistence_json import _json_object
import re
import subprocess

from app.compound_edits import is_indexed_subtitle_command, compile_compound_selection, compile_compound_edit
from app.creation.contracts import EditCommand
from app.creation.editing.compound import calculate_compound_edit, prepare_compound_proposal, apply_compound_proposal
from app.creation.editing.metadata import check_command
from app.creation.editing.metadata_use_case import (MetadataEditUseCase, MetadataEditResult,
    effective_audio_timeline, logical_montage_output_refs, prepare_changed_montage)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.creation_execution import CreationExecutionActor
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.production import TimelineVersionConflict
from app.media.picture_preview import PicturePreviewRejected
from app.schemas import CompoundEditSelectionRequest, CreationTaskEditPreviewResponse
from app.task_audio import media_digest


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
    def __init__(self, repository, montage_edits: MetadataEditUseCase, *, picture_renderer=None):
        if montage_edits.repository is not repository:
            raise ValueError('复合编辑必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits
        self.picture_renderer = picture_renderer

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


    def preview(self, prepared, *, draft_only, execution):
        if prepared is None:
            return None
        task, base, plan, tracks = prepared.task, prepared.base, prepared.before, prepared.original_tracks
        calculated = prepared.candidate
        workspace_id = task.workspace_id
        proposal = prepared.proposal
        records = []
        if not draft_only:
            records = [self.picture_renderer.publish_picture(workspace_id, task, base, item, captions,
                kind="compound_preview", parameters={"steps": calculated.steps, "side": side}, execution=execution)
                for side, item, captions in (("before", plan, tracks), ("after", calculated.plan, calculated.subtitle_tracks))]
        proposal = replace(proposal, preview={**proposal.preview, **{
            side: {"ref": record.logical_ref, "label": "修改前" if side == "before" else proposal.summary,
                "detail": "整片视听预览；全部修改确认后写入同一个版本，可一次撤销。",
                "video_url": f"/api/creation-tasks/{task.id}/visual/artifacts/{record.id}"}
            for side, record in zip(("before", "after"), records)}})
        intent, proposal = self.save_preview(replace(prepared, proposal=proposal))
        return intent, proposal

    def preview_selection(self, control, *, draft_only, execution):
        try:
            return self.preview(self.prepare_selection(control), draft_only=draft_only, execution=execution)
        except CompoundProposalMissing as exc:
            raise PicturePreviewRejected(status_code=404, detail=exc.args[0]) from exc
        except CompoundSelectionInputError as exc:
            raise PicturePreviewRejected(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise PicturePreviewRejected(status_code=403, detail=str(exc)) from exc
        except (EditConflict, TimelineVersionConflict) as exc:
            raise PicturePreviewRejected(status_code=409, detail=str(exc)) from exc
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            raise PicturePreviewRejected(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "复合修改预览失败，请检查来源后重试") from exc

    def prepare_source(self, workspace_id, task_id, arguments, actor):
        task = self.repository.get_creation_task(workspace_id, task_id)
        if arguments.get('_media_kind') == 'clip_preview':
            raise ValueError('历史短预览没有独立的封存执行输入，请查询原记录并为当前版本重新预览')
        source = arguments.get('_typed_source')
        if source is not None:
            version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
            if (source['workspace_id'], source['task_id'], source['member_id'], source['expected_timeline_version_id'], source['expected_timeline_sequence']) != (workspace_id, task_id, actor.user_id or actor.member_id, task.current_timeline_version_id, version.sequence if version else 0):
                raise ValueError('封存的类型化编辑输入或成员已失效')
        plan_payload = self.repository.get_creation_task_montage_plan(workspace_id, task_id) if task.montage_plan_id else None
        plan = creation_task_montage_plan_from_payload(plan_payload) if plan_payload else None
        transient = {'status', 'message', 'output_path', 'subtitle_path', 'updated_at'}
        snapshot = {key: value for key, value in creation_task_montage_plan_to_payload(plan).items() if key not in transient} if plan else None
        payload = arguments.get('payload', {})
        picture_candidates = [plan] if plan is not None else []
        compound_proposal = None
        if plan is not None and arguments.get('_media_kind') == 'picture_preview':
            proposal = self.repository.get_edit_proposal(workspace_id, arguments['proposal_id'])
            selection = CompoundEditSelectionRequest.model_validate(payload)
            data = proposal.operation.get('payload', {})
            if proposal.task_id != task_id or proposal.status != 'proposed' or proposal.operation.get('operation_type') != 'EditCompoundTimeline' or (not data.get('selection_enabled')) or (data.get('base_version_id') != task.current_timeline_version_id):
                raise ValueError('当前复合修改提案已失效，不能封存预览')
            included = selection.included_step_indices if selection.raw_command is None else compile_compound_selection(selection.raw_command, data['steps'])
            intent = self.repository.get_edit_intent(workspace_id, proposal.intent_id)
            command_payload = {'raw_command': intent.raw_command, 'selected_object_refs': intent.scope['context_refs'], 'playhead_ms': intent.playhead_ms}
            compound_proposal = json.loads(_json_object(asdict(proposal)))
            compiled = compile_compound_edit(command_payload['raw_command'], plan, effective_audio_timeline(plan), command_payload.get('selected_object_refs', []), task.current_timeline_version_id, command_payload.get('playhead_ms'), included)
            if compiled is not None:
                picture_candidates.append(compiled[0])
        expected = payload.get('base_version_id')
        if 'base_version_id' in payload and expected != task.current_timeline_version_id:
            raise ValueError('时间线已变化，请重新安排当前要求')
        files, audio_inputs, graphics = [], [], []
        service = self.montage_edits.audio_service
        if plan is not None:
            files, graphics = self.picture_renderer.qualified_picture_sources(workspace_id, task, plan, picture_candidates, actor)
            graph = effective_audio_timeline(plan)
            solo = set(payload.get('solo_track_ids', []))
            clips = [clip for track in graph.tracks if not track.muted and (not solo or track.id in solo) for clip in track.clips if not clip.muted]
            audio_uses = Counter(clip.source_ref for clip in clips)
            for reference in sorted({clip.source_ref for clip in clips}):
                path = service.source(workspace_id, task_id, reference, actor=actor)
                stat = path.stat()
                fact = [reference, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
                if fact not in files:
                    files.append(fact)
                values = service.source_metadata(workspace_id, task_id, reference)
                audio_inputs.append({'reference': reference, 'path': str(path.resolve()), 'size_bytes': stat.st_size, 'uses': audio_uses.get(reference, 1), **values})
        facts = {'base_version_id': task.current_timeline_version_id, 'script_id': task.confirmed_script_id, 'script_digest': self.repository.confirmed_creation_task_script_digest(workspace_id, task_id, task.confirmed_script_id) if task.confirmed_script_id else None, 'plan_id': task.plan_id, 'plan_digest': media_digest(asdict(self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id))), 'montage': snapshot, 'files': files, 'narration_cache_key': task.narration_cache_key}
        if graphics:
            facts['graphic_sources'] = graphics
        if compound_proposal is not None:
            facts['compound_selection_proposal'] = compound_proposal
        return task, plan, payload, picture_candidates, facts, audio_inputs

    def seal_resources(self, prepared, budget, audio_budget, *, actor):
        task, plan, payload, picture_candidates, facts, audio_inputs = prepared
        resources = self.picture_renderer.picture_resources(plan, payload, picture_candidates, facts, budget)
        facts, resources = self.picture_renderer.seal_audio_resources(task, picture_candidates, facts, resources, audio_budget, actor=actor)
        if '校准' in payload.get('raw_command', ''):
            raise ValueError('本地字幕识别尚无独立标定的模型资源许可，未启动识别')
        return facts, resources

    def execute(self, job, event, actor, context):
        payload = CompoundEditSelectionRequest.model_validate(event['arguments']['payload'])
        # The old worker's principal resolves to this finite metadata identity; selection has no typed source.
        control = CompoundSelectionCommand(job.workspace_id, job.task_id, event['arguments']['proposal_id'],
            {'member_id': actor.user_id or actor.member_id, 'local_diagnostic': False},
            payload.included_step_indices, payload.raw_command)
        outcome = self.preview_selection(control, draft_only=event['arguments'].get('_draft', False),
            execution=(context, job, event, actor))
        if outcome is None:
            return None
        intent, proposal = outcome
        return CreationTaskEditPreviewResponse.model_validate({'intent': asdict(intent), 'proposal': asdict(proposal)}).model_dump(mode='json')

    def validate_result(self, job, event, result, context):
        return self.montage_edits.audio_service.validate_publications(job, event, result, context)
