"""Concrete metadata commands, durable replay and one montage-version commit."""
from __future__ import annotations

from app.creation.editing.clips import ClipPreviewCommand
from app.schemas import CreationTaskExactEditRequest
from app.schemas import MontageClipCandidateResponse
from app.visual_effects import compile_crop_command
from app.creation.editing.command_compile import edit_material_constraint
from app.production import effective_segment_usage_role
from app.production import material_segment_eligible
import re
from app.creation.editing.clips import reorder_picture

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4
import hashlib
import json
import subprocess
from urllib.parse import quote
from app.creation.editing.clips import CLIP_SETTING_OPERATIONS, ClipPreviewConflict, apply_clip_patch
from app.creation.editing.picture import graphic_overlay_candidate
from app.schemas import EXACT_MONTAGE_REQUIRED_FIELDS, CreationTaskMontageClipUpdateRequest
from app.production import TimelineVersionConflict

from app.creation.contracts import (EditCommand, EditIntent, EditProposal, SubtitleTrack,
    SubtitleCue, JobRun, ArtifactRecord, TransformRun, DependencyEdge, TimelineVersion)
from app.creation.transactions import (clone_subtitle_track, clone_cover_asset,
    prepare_change_set, commit_direct_change, commit_change_set)
from app.creation.editing.metadata import (check_command, prepare_subtitle_display_command,
    prepare_clip_command, prepare_picture_command, prepare_audio_command,
    make_metadata_proposal, metadata_request_identity, PICTURE_METADATA_OPERATIONS, apply_clip_proposal, apply_picture_metadata)
from app.creation.editing.clips import CLIP_METADATA_OPERATIONS
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.creation.editing.affected import affected_source_clip_ids
from app.creation.editing.subtitles import apply_subtitle_cue
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.audio_timeline import narration_audio_timeline
from app.graphic_sources import resolve_graphic_source
from app.graphic_overlays import validate_overlays
from app.transitions import dissolve_frames
from app.visual_effects import fade_filters, clip_playback_rate
from app.linked_timeline import reorder_linked_group
from app.subtitle_edits import normalize_subtitle_text_and_breaks
from app.subtitle_schedule import narration_schedule, retime_subtitle_tracks
from app.subtitle_tracks import (SubtitleTrack as SubtitleExecutionTrack,
    SubtitleStyle as SubtitleExecutionStyle, SubtitleCue as SubtitleExecutionCue, render_srt)
from app.models import utc_now

if TYPE_CHECKING:
    from app.production import CreationTask, NarrationArtifactRecord, NarrationSegment
    from app.creation.contracts import TimelineVersion
    from app.montage import CreationTaskMontagePlan
    from app.audio_timeline import AudioTimeline



class EditPreviewRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code, self.detail = status_code, detail


def edit_preview_failure(*, status_code, error, reason, impact, repair_action, retryable):
    return EditPreviewRejected(status_code=status_code, detail={
        'error': error, 'reason': reason, 'impact': impact,
        'repair_action': repair_action, 'retryable': retryable})


class InvalidMontage(ValueError):
    pass


class SubtitleTargetMissing(KeyError):
    pass


class SubtitleNoChange(EditConflict):
    def __init__(self, has_track):
        self.details = {'status_code': 409, 'error': 'edit_no_change',
            'reason': '字幕文字、时轴和样式没有变化。' if has_track else '新字幕与当前字幕相同。',
            'impact': '没有创建新的时间线版本。',
            'repair_action': '修改文字、断行、强调词或字幕样式后再预览。' if has_track else '输入不同的字幕文字后再试。',
            'retryable': not has_track}
        super().__init__(self.details['reason'])


def normalized_subtitle_text(text, line_breaks):
    try:
        return normalize_subtitle_text_and_breaks(text, line_breaks)
    except ValueError as exc:
        raise InvalidMontage(str(exc)) from exc


class LegacyMontageReference(EditConflict):
    def __init__(self, **details):
        self.details = details
        super().__init__(details["reason"])


@dataclass(frozen=True)
class MetadataEditResult:
    operation: object
    montage: object
    task: object
    narration: object | None


@dataclass(frozen=True)
class MetadataEditReplay:
    recorded_projection: dict | None
    result: MetadataEditResult | None = None


def metadata_edit_command(task, actor_identity, operation_type, values, *, source=None):
    return EditCommand(task.workspace_id, task.id, actor_identity,
        values['base_version_id'], operation_type, values, source)

def default_subtitle_style() -> dict[str, object]:
    return {'name': 'default', 'font_family': 'PingFang SC', 'font_size': 40, 'primary_color': '#FFFFFF', 'emphasis_color': '#FFD54F', 'position': 'bottom', 'bilingual_order': 'source_then_translation'}

def execution_subtitle_track(track: SubtitleTrack) -> SubtitleExecutionTrack:
    return SubtitleExecutionTrack(id=track.id, workspace_id=track.workspace_id, task_id=track.task_id, timeline_version_id=track.timeline_version_id, language=track.language, source_language=track.source_language or track.language, style=SubtitleExecutionStyle(**track.style), cues=tuple((SubtitleExecutionCue(id=cue.id, source_cue_id=cue.source_cue_id, text=cue.text, start_seconds=cue.start_seconds, end_seconds=cue.end_seconds, speaker=cue.speaker, line_breaks=tuple(cue.line_breaks), emphasis=tuple(cue.emphasis)) for cue in track.cues)), generation_method=track.generation_method, status=track.status, model_id=track.model_id, artifact_ref=track.artifact_ref, transform_id=track.transform_id)

def narration_linkage_spans(plan: CreationTaskMontagePlan) -> list[tuple[str, int, int]]:
    """按方案自身的 cue 顺序导出每个来源在整片配音里的源区间。

    与 `_person_source_range` 用的是同一段累加推导（前面各段 `round(duration*1000)`
    依次相加），这里把它抽成可复用函数：镜头换位时靠它拿到"哪句对应哪段源区间"，
    而不是按移动后的时间重叠去猜。
    """
    spans: list[tuple[str, int, int]] = []
    offset = 0
    for cue in plan.cues:
        if not cue.narration_segment_id:
            raise ValueError(f'文案 {cue.id} 没有配音分段来源，无法确定关联声音。')
        span_ms = round(cue.duration_seconds * 1000)
        if span_ms < 10:
            raise ValueError(f'文案 {cue.id} 的配音分段过短，无法整体换位。')
        spans.append((cue.id, offset, span_ms))
        offset += span_ms
    return spans

def effective_audio_timeline(plan: CreationTaskMontagePlan) -> AudioTimeline:
    return plan.audio_timeline or narration_audio_timeline(plan.narration_audio_path or '', plan.duration_seconds)

def refresh_montage_review_state(plan) -> None:
    plan.low_confidence_clip_count = sum((clip.needs_confirmation for clip in plan.clips))
    plan.status = 'ready' if plan.low_confidence_clip_count == 0 else 'waiting_for_user'
    plan.output_path = None
    plan.subtitle_path = None
    plan.message = '镜头审核已完成，可以重新渲染。' if plan.status == 'ready' else f'仍有 {plan.low_confidence_clip_count} 个镜头等待确认。'

def prepare_changed_montage(plan: CreationTaskMontagePlan, *, message: str) -> tuple[CreationTaskMontagePlan, dict[str, object]]:
    refresh_montage_review_state(plan)
    plan.message = message if plan.status == 'ready' else f'{message} 仍有 {plan.low_confidence_clip_count} 个镜头等待确认。'
    return (plan, creation_task_montage_plan_to_payload(plan))

def logical_montage_output_refs(plan: CreationTaskMontagePlan) -> list[str]:
    references = [reference for reference in (plan.output_path, plan.subtitle_path) if reference]
    if any((Path(reference).is_absolute() or not reference.strip() or '..' in Path(reference).parts for reference in references)):
        raise LegacyMontageReference(status_code=409, error='edit_base_not_migrated', reason='当前成片仍使用迁移前的文件引用，不能建立可撤销修改。', impact='当前镜头和成片保持不变。', repair_action='先用当前生产链路重新渲染一次成片，再继续修改。', retryable=True)
    return references


class MetadataEditUseCase:
    def __init__(self, repository, artifact_store, *, output_root, audio_service):
        self.repository = repository
        self.artifact_store = artifact_store
        self.output_root = output_root
        self.audio_service = audio_service
        self.picture_renderer = None
        self.preview_settings = None

    def confirm(self, task, proposal, *, identity, reader_identity, source):
        workspace_id, task_id = task.workspace_id, task.id
        operation_type = proposal.operation.get('operation_type')
        operation_payload = proposal.operation.get('payload')
        if operation_type == 'ReorderTimelineItems' and isinstance(operation_payload, dict):
            if task.current_timeline_version_id != operation_payload.get('base_version_id'):
                raise EditPreviewRejected(status_code=409, detail='时间线已变化，请重新预览镜头排序。')
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            old_order = [clip.id for clip in plan.clips]
            if old_order != operation_payload.get('current_order'):
                raise EditPreviewRejected(status_code=409, detail='镜头顺序已变化，请重新预览。')
            new_order = operation_payload['new_order']
            target_ids = operation_payload['target_clip_ids']
            if not target_ids or not set(target_ids) <= set(old_order):
                raise EditPreviewRejected(status_code=409, detail='镜头排序目标已变化，请重新预览。')
            for index, clip_id in enumerate(new_order):
                if index >= len(old_order) or old_order[index] != clip_id:
                    plan, _tracks, _linkage = self.apply_clip_update(workspace_id, task_id, plan, clip_id, CreationTaskMontageClipUpdateRequest(), reader_identity=reader_identity)
            link_narration = self.reorder_narration_scope(plan, new_order, target_ids)
            if link_narration != operation_payload['link_narration']:
                raise EditPreviewRejected(status_code=409, detail='人物镜头关联已变化，请重新预览。')
            tracks, linkage = self.reorder_montage_plan(workspace_id, task, plan, new_order, link_narration=link_narration)
            bound = self.repository.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('affected_source_scope')
            if bound is None:
                raise EditPreviewRejected(status_code=409, detail='原排序提案未封存来源范围，请为当前版本重新预览')
            source_command = metadata_edit_command(task, identity, operation_type, operation_payload, source=source)
            source_command = EditSourceVerifier.restore_scope(source_command, bound)
            verifier = EditSourceVerifier(self.repository, identity=source_command.actor_identity)
            prepare_changed_montage(plan, message='整组镜头排序已保存；一次撤销可恢复，重新生成后播放新成片。')
            return self._persist_confirmed_montage(task=task, proposal=proposal, base_version=self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id), updated_plan=plan, operation_type=operation_type, operation_payload=operation_payload, edited_subtitle_tracks=tracks, proposal_source_command=source_command, proposal_source_verifier=verifier)
        if operation_type == 'ChangePresenterShare' and isinstance(operation_payload, dict):
            if task.current_timeline_version_id != operation_payload.get('base_version_id'):
                raise EditPreviewRejected(status_code=409, detail='时间线已变化，请重新预览露脸比例')
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            base = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
            target = operation_payload['target']
            plan.presenter_share_target = target
            prepare_changed_montage(plan, message=f'露脸目标 {target}% 已保存到草稿；实际镜头保持不变，请明确生成后再核对实际占比。')
            return self._persist_confirmed_montage(task=task, proposal=proposal, base_version=base, updated_plan=plan, operation_type=operation_type, operation_payload=operation_payload)
        if operation_type in CLIP_METADATA_OPERATIONS and isinstance(operation_payload, dict):
            command = metadata_edit_command(task, identity, operation_type, operation_payload, source=source)
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            try:
                changed, edited_tracks, _linkage = apply_clip_proposal(task, montage, command, resolve_source=lambda clip, values: self.resolve_edit_clip_source(task, reader_identity, clip, values), link_reordered=lambda candidate: self.link_reordered_edit(task, candidate))
            except PermissionError as exc:
                raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
            except (ValueError, KeyError) as exc:
                raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
            bound = self.repository.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('affected_source_scope')
            if bound is not None:
                command = EditSourceVerifier.restore_scope(command, bound)
                verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
            else:
                selected = set(command.values.get('clip_ids') or ([command.values['clip_id']] if command.values.get('clip_id') else []))
                if not set(affected_source_clip_ids(montage, changed)) <= selected:
                    raise EditPreviewRejected(status_code=409, detail='原提案未封存实际影响的来源范围，请为当前版本重新预览')
                verifier = EditSourceVerifier(self.repository, identity=command.actor_identity) if command.source is not None else None
            prepare_changed_montage(changed, message='镜头设置已更新；文案、配音和其他镜头保持不变，可以重新渲染。')
            return self._persist_confirmed_montage(task=task, proposal=proposal, base_version=self.repository.get_timeline_version(workspace_id, command.base_version_id), updated_plan=changed, operation_type=operation_type, operation_payload=operation_payload, edited_subtitle_tracks=edited_tracks, proposal_source_command=command, proposal_source_verifier=verifier)
        if operation_type in PICTURE_METADATA_OPERATIONS and isinstance(operation_payload, dict):
            command = metadata_edit_command(task, identity, operation_type, operation_payload, source=source)
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            try:
                changed = apply_picture_metadata(task, montage, command, validate_graphic_sources=lambda overlays: self.validate_graphic_sources(workspace_id, task_id, overlays))
            except (EditConflict, ValueError, OSError) as exc:
                raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
            bound = self.repository.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('affected_source_scope')
            if bound is not None:
                command = EditSourceVerifier.restore_scope(command, bound)
                verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
            else:
                selected = set(command.values.get('clip_ids') or ([command.values['clip_id']] if command.values.get('clip_id') else []))
                if not set(affected_source_clip_ids(montage, changed)) <= selected:
                    raise EditPreviewRejected(status_code=409, detail='原提案未封存实际影响的来源范围，请为当前版本重新预览')
                verifier = EditSourceVerifier(self.repository, identity=command.actor_identity) if command.source is not None else None
            messages = {'EditGraphicOverlay': '图文已更新，可以重新渲染。', 'SetClipTransition': '转场已更新，配音与字幕时轴不变。', 'AdjustVisualEffects': '画面效果已更新，可以重新渲染。'}
            prepare_changed_montage(changed, message=messages[operation_type])
            base = self.repository.get_timeline_version(workspace_id, command.base_version_id)
            return self._persist_confirmed_montage(task=task, proposal=proposal, base_version=base, updated_plan=changed, operation_type=operation_type, operation_payload=operation_payload, proposal_source_command=command, proposal_source_verifier=verifier)
        if operation_type == 'ChangeCanvas' and isinstance(operation_payload, dict):
            if task.current_timeline_version_id != operation_payload.get('base_version_id'):
                raise EditPreviewRejected(status_code=409, detail='版本已变化，请重新预览画幅')
            base = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
            montage.aspect_ratio = operation_payload['aspect_ratio']
            if operation_payload.get('fit_mode'):
                for clip in montage.clips:
                    clip.fit_mode = operation_payload['fit_mode']
            version_id = f'timeline_{uuid4().hex}'
            tracks = [clone_subtitle_track(track, timeline_version_id=version_id) for track in self.current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)]
            prepare_changed_montage(montage, message='画幅已修改，请重新渲染；封面需从新成片重新生成。')
            try:
                return self.persist_subtitle_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base, result_version_id=version_id, operation_type=operation_type, operation_payload=operation_payload, subtitle_tracks=tracks, cover_asset=None, updated_plan=montage)
            except ValueError as exc:
                raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        raise EditPreviewRejected(status_code=409, detail='unsupported metadata confirmation operation')

    def _persist_confirmed_montage(self, *, task, proposal, base_version, updated_plan,
            operation_type, operation_payload, edited_subtitle_tracks=None,
            proposal_source_command=None, proposal_source_verifier=None):
        # Preserve the former HTTP facade's finite error boundary around this save.
        try:
            return self.persist_montage_edit(workspace_id=task.workspace_id, task=task, proposal=proposal,
                base_version=base_version, updated_plan=updated_plan, operation_type=operation_type,
                operation_payload=operation_payload, edited_subtitle_tracks=edited_subtitle_tracks,
                proposal_source_command=proposal_source_command, proposal_source_verifier=proposal_source_verifier)
        except (LegacyMontageReference, InvalidMontage):
            raise
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc

    def current_narration_snapshot(self, workspace_id: str, task: CreationTask) -> dict[str, object]:
        if not task.confirmed_script_id:
            return {}
        segments = self.repository.list_narration_segments(workspace_id, task.id, task.confirmed_script_id)
        return {'narration_segments': [json.loads(json.dumps(asdict(segment), ensure_ascii=False, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))) for segment in segments], 'narration_cache_key': task.narration_cache_key}

    @staticmethod
    def narration_segments_from_snapshot(
        snapshot: dict[str, object],
    ) -> list[NarrationSegment] | None:
        from app.production import NarrationSegment

        raw_segments = snapshot.get("narration_segments")
        if not isinstance(raw_segments, list):
            return None
        segments: list[NarrationSegment] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                raise ValueError("时间线中的配音片段快照无效")
            payload = dict(item)
            for key in ("created_at", "updated_at"):
                if isinstance(payload.get(key), str):
                    payload[key] = datetime.fromisoformat(str(payload[key]))
            segments.append(NarrationSegment(**payload))
        return segments


    def ensure_montage_base(self, workspace_id: str, task: CreationTask, plan: CreationTaskMontagePlan) -> TimelineVersion:
        if task.current_timeline_version_id is not None:
            return self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
        versions = self.repository.list_timeline_versions(workspace_id, task.id)
        if versions:
            return versions[-1]
        version = TimelineVersion(id=f'timeline_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, parent_version_id=None, sequence=1, snapshot={'montage': creation_task_montage_plan_to_payload(plan), **self.current_narration_snapshot(workspace_id, task)}, created_by_operation_id=None)
        return self.repository.create_timeline_version(version)

    def begin(self, task, member_id, request_id, inputs):
        if request_id is None:
            return None, None
        event_id, fresh = self.repository.claim_edit_request(task, member_id, request_id, 'preview', inputs)
        if fresh:
            return event_id, None
        recorded = self.repository.read_edit_request_result(task, event_id)
        if recorded is not None:
            return event_id, MetadataEditReplay(recorded)
        identity = metadata_request_identity({'workspace_id':task.workspace_id, 'task_id':task.id,
            'member_id':member_id, 'request_id':str(request_id)})
        try:
            proposal = self.repository.get_edit_proposal(task.workspace_id, 'proposal_metadata_' + identity)
        except KeyError as exc:
            raise EditConflict('原保存结果尚未确定，请查询原请求，不重复执行') from exc
        operations = [item for item in self.repository.list_edit_operations(task.workspace_id, task.id)
            if item.proposal_id == proposal.id]
        if not operations:
            raise EditConflict('原保存结果尚未确定，请查询原请求，不重复执行')
        operation = operations[0]
        version = self.repository.get_timeline_version(task.workspace_id, operation.result_version_id)
        plan = creation_task_montage_plan_from_payload(version.snapshot['montage'])
        return event_id, MetadataEditReplay(None, MetadataEditResult(operation, plan, task, None))

    def record_result(self, task, event_id, *, status_code, response):
        if event_id is not None:
            self.repository.save_edit_request_result(task, event_id, status_code=status_code, response=response)

    def save(self, task, command, *, audio_edit=None, audio_actor=None, connection=None):
        if command.base_version_id is None:
            raise EditConflict('先保留当前故事板并建立可撤销版本')
        check_command(task, command)
        base = self.repository.get_timeline_version(command.workspace_id, command.base_version_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(command.workspace_id, task.id))
        verifier = EditSourceVerifier(self.repository, identity=command.actor_identity,
            audio_service=self.audio_service if command.operation_type == 'EditAudioTimeline' else None,
            audio_actor=audio_actor if command.operation_type == 'EditAudioTimeline' else None)
        if command.source is None:
            command = replace(command, input_source_facts=verifier.freeze(command))
        edited_tracks = None
        result_version_id, narration_segments = None, None
        if command.operation_type == 'UpdateSubtitleCue':
            (intent, proposal), changed, edited_tracks, result_version_id, narration_segments = self.subtitle_text_change(task, plan, base, command)
        elif command.operation_type == 'ConfigureSubtitleTrack':
            tracks = self.current_subtitle_tracks(command.workspace_id, task, current_version=base, montage=plan)
            (intent, proposal), changed, edited_tracks = prepare_subtitle_display_command(task, plan, tracks, command)
        elif command.operation_type in CLIP_METADATA_OPERATIONS:
            (intent, proposal), changed, edited_tracks, _linkage = prepare_clip_command(task, plan, command,
                resolve_source=lambda clip, values: self.repository.resolve_edit_clip_source(task.workspace_id, task.id, clip, values,
                    user_id=command.actor_identity['member_id'], local_diagnostic=command.actor_identity['local_diagnostic']),
                link_reordered=lambda candidate: self.link_reordered_edit(task, candidate))
        elif command.operation_type in PICTURE_METADATA_OPERATIONS:
            (intent, proposal), changed = prepare_picture_command(task, plan, command,
                validate_graphic_sources=lambda overlays: self.validate_graphic_sources(task.workspace_id, task.id, overlays))
        else:
            plan.audio_timeline = effective_audio_timeline(plan)
            (intent, proposal), changed = prepare_audio_command(task, plan, command,
                edit=audio_edit, validate_sources=lambda graph, duration: self.audio_service.validate(task.workspace_id, task.id, graph, duration, draft_only=True))
        proposal = replace(proposal, invalidated_refs=logical_montage_output_refs(plan))
        command = verifier.bind_affected(command, plan, changed)
        message = ('字幕已修改；请重新渲染以写入成片。' if edited_tracks is not None else
            '字幕已修改；配音和镜头保持不变，可以重新渲染。') if command.operation_type == 'UpdateSubtitleCue' else '修改已保存到草稿；明确生成后更新成片。'
        prepare_changed_montage(changed, message=message)
        return self.persist_montage_edit(workspace_id=task.workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=changed, operation_type=command.operation_type,
            operation_payload=proposal.operation['payload'], direct_command=command, direct_intent=intent,
            edited_subtitle_tracks=edited_tracks, direct_source_verifier=verifier,
            result_version_id=result_version_id, narration_segments=narration_segments, connection=connection)

    def subtitle_text_change(self, task, plan, base, command):
        tracks = self.current_subtitle_tracks(task.workspace_id, task, current_version=base, montage=plan)
        cue_id = command.values['cue_id']
        track = next((item for item in tracks if any(cue.id == cue_id for cue in item.cues)), None)
        cue = next((cue for cue in (track.cues if track is not None else plan.cues) if cue.id == cue_id), None)
        if cue is None:
            raise SubtitleTargetMissing('creation task subtitle edit target not found')
        text, line_breaks = normalized_subtitle_text(command.values['text'].strip(), None)
        if text == cue.text:
            raise SubtitleNoChange(track is not None)
        payload = {'base_version_id': base.id, 'cue_id': cue_id, 'text': text, 'expected_text': cue.text}
        changed = deepcopy(plan)
        if track is None:
            next(item for item in changed.cues if item.id == cue_id).text = text
            segments = self.repository.list_narration_segments(task.workspace_id, task.id, task.confirmed_script_id) if task.confirmed_script_id else []
            pair = make_metadata_proposal(command, [f'cue:{cue_id}'], '只修改所选字幕；配音和镜头保持不变。', payload)
            return pair, changed, None, None, segments
        payload.update(track_id=track.id, start_seconds=cue.start_seconds, end_seconds=cue.end_seconds,
            expected_start_seconds=cue.start_seconds, expected_end_seconds=cue.end_seconds,
            line_breaks=line_breaks, emphasis=cue.emphasis, style=dict(track.style))
        source, edited, linked = apply_subtitle_cue(tracks, payload, plan.duration_seconds)
        pair = make_metadata_proposal(command, [f'cue:{cue_id}'], '只修改所选字幕；配音和镜头保持不变。', payload)
        result_version_id = f'timeline_{uuid4().hex}'
        provisional = clone_subtitle_track(edited, timeline_version_id=result_version_id)
        reference, transform = self.publish_subtitle_text(workspace_id=task.workspace_id, task_id=task.id,
            proposal_id=pair[1].id, kind='subtitle_cue_update',
            input_refs=[source.artifact_ref or f'subtitle-track:{source.id}'],
            content=render_srt(execution_subtitle_track(provisional)), engine='subtitle-srt/v1',
            parameters={'track_id': source.id, 'cue_id': cue_id})
        changed_track = clone_subtitle_track(edited, timeline_version_id=result_version_id,
            artifact_ref=reference, transform_id=transform)
        retained = [item for item in linked if item.id != source.id]
        if source.source_language is None:
            montage_cue = next((item for item in changed.cues if item.id == cue_id), None)
            if montage_cue is not None:
                montage_cue.text = text
        return pair, changed, [changed_track, *retained], result_version_id, None

    def validate_graphic_sources(self, workspace_id, task_id, overlays):
        for overlay in overlays:
            if overlay.get('kind') == 'image':
                resolve_graphic_source(self.repository, self.artifact_store, workspace_id, task_id, overlay['source_ref'])

    def link_reordered_edit(self, task, candidate):
        linkage, tracks = self.reorder_narration_linkage(task.workspace_id, task, candidate)
        return tracks, linkage

    def subtitle_track_from_montage(self, workspace_id: str, task: CreationTask, version: TimelineVersion, montage: CreationTaskMontagePlan):
        if montage.status != 'rendered' or not montage.subtitle_path:
            return None
        transform_id = version.snapshot.get('transform_run_id')
        if not isinstance(transform_id, str) or not transform_id:
            transform_id = version.snapshot.get('reused_job_id')
            if isinstance(transform_id, str) and transform_id:
                transform_id = f'transform_{transform_id}'
        if not isinstance(transform_id, str) or not transform_id:
            return None
        schedule = narration_schedule(montage.cues, montage.duration_seconds)
        cues: list[SubtitleCue] = []
        for cue, caption in zip(montage.cues, schedule, strict=True):
            start = round(caption.start_seconds, 3)
            end = round(caption.end_seconds, 3)
            normalized_text, line_breaks = normalized_subtitle_text(cue.text, None)
            cues.append(SubtitleCue(id=cue.id, source_cue_id=None, text=normalized_text, start_seconds=start, end_seconds=end, line_breaks=line_breaks))
        source_language = self.repository.get_creation_plan(workspace_id, task.plan_id).language
        track_language = 'en-US' if source_language == 'en-US' else 'zh-CN'
        return SubtitleTrack(id=f"subtitle_{version.id}_{('en' if track_language == 'en-US' else 'zh')}", workspace_id=workspace_id, task_id=task.id, timeline_version_id=version.id, language=track_language, source_language=None, style=dict(montage.cues[0].subtitle_style) if montage.cues and montage.cues[0].subtitle_style else default_subtitle_style(), cues=cues, generation_method='timeline', model_id=None, artifact_ref=montage.subtitle_path, transform_id=transform_id, status='valid')

    def current_subtitle_tracks(self, workspace_id: str, task: CreationTask, *, current_version: TimelineVersion | None=None, montage: CreationTaskMontagePlan | None=None):
        if current_version is None and task.current_timeline_version_id:
            try:
                current_version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
            except KeyError:
                current_version = None
        if current_version is None:
            return []
        tracks = self.repository.list_subtitle_tracks(workspace_id, task.id, timeline_version_id=current_version.id)
        if tracks:
            return tracks
        if current_version.snapshot.get('subtitle_track_ids') == []:
            return []
        if montage is None:
            try:
                montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
            except KeyError:
                return []
        projected = self.subtitle_track_from_montage(workspace_id, task, current_version, montage)
        return [projected] if projected is not None else []

    def cover_asset_for_version(self, workspace_id: str, task_id: str, version: TimelineVersion):
        cover_id = version.snapshot.get('cover_asset_id')
        if not isinstance(cover_id, str) or not cover_id:
            return None
        try:
            return self.repository.get_cover_asset(workspace_id, task_id, cover_id)
        except KeyError:
            return None

    def publish_subtitle_text(self, *, workspace_id: str, task_id: str, proposal_id: str, kind: str, input_refs: list[str], content: str, engine: str, parameters: dict[str, object]):
        input_digest = hashlib.sha256(json.dumps({'inputs': input_refs, 'content': content, 'parameters': parameters}, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        job = self.repository.create_job_run(JobRun(id=f'job_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, kind=kind, idempotency_key=f'{kind}:{proposal_id}:{input_digest}', input_digest=input_digest))
        running = self.repository.claim_job_run(workspace_id, job.id)
        logical_ref = f'artifacts/{task_id}/{job.id}/subtitles.srt'
        staging_root = Path(self.output_root).expanduser() / '.job-staging'
        staging_root.mkdir(parents=True, exist_ok=True)
        try:
            with TemporaryDirectory(prefix=f'{job.id}-', dir=staging_root) as staging:
                staged = Path(staging) / 'subtitles.srt'
                staged.write_text(content, encoding='utf-8')
                publication = self.artifact_store.publish_file(logical_ref, staged)
            artifact = self.repository.create_artifact_record(ArtifactRecord(id=f'artifact_{job.id}_subtitles', workspace_id=workspace_id, task_id=task_id, kind='subtitle_track', logical_ref=publication.logical_ref, sha256=publication.sha256, size_bytes=publication.size_bytes, producer_job_id=job.id, input_refs=input_refs))
            transform = self.repository.create_transform_run(TransformRun(id=f'transform_{job.id}', workspace_id=workspace_id, task_id=task_id, job_id=job.id, transform_type=kind, input_digest=input_digest, input_refs=input_refs, output_refs=[artifact.logical_ref], engine=engine, parameters=parameters, status='succeeded', started_at=running.started_at or utc_now(), finished_at=utc_now()))
            for upstream in input_refs:
                self.repository.create_dependency_edge(DependencyEdge(id=f'edge_{job.id}_{hashlib.sha256(upstream.encode()).hexdigest()[:20]}', workspace_id=workspace_id, task_id=task_id, upstream_ref=upstream, downstream_ref=artifact.logical_ref, created_by_transform_id=transform.id))
            self.repository.finish_job_run(workspace_id, job.id, status='succeeded', output_refs=[artifact.logical_ref])
            return (artifact.logical_ref, transform.id)
        except Exception as exc:
            try:
                self.repository.finish_job_run(workspace_id, job.id, status='failed', failure_reason=str(exc) or type(exc).__name__, impact='没有保存新的字幕轨，当前版本保持不变。', repair_action='检查字幕内容与本机产物目录后重试。')
            except (KeyError, ValueError):
                pass
            raise

    def reorder_narration_linkage(self, workspace_id: str, task: CreationTask, plan: CreationTaskMontagePlan):
        """已做口型的人物镜头换位后，把配音与字幕按来源身份一并换到新位置。

        来源引用与源区间逐字保留，只有时间线区间移动；音乐/音效/原声轨完全不碰。
        关联缺失或对不上时抛 ValueError，由调用方转 409 并把具体缺口回报给用户。
        """
        try:
            spans = narration_linkage_spans(plan)
            known = {cue_id for cue_id, _o, _d in spans}
            clip_spans = []
            for item in plan.clips:
                if item.cue_id not in known:
                    continue
                clip_spans.append((item.cue_id, round(item.timeline_start_seconds * 1000), round(item.timeline_end_seconds * 1000) - round(item.timeline_start_seconds * 1000)))
            audio, edited_tracks, linkage = reorder_linked_group(effective_audio_timeline(plan), self.current_subtitle_tracks(workspace_id, task, montage=plan), spans, clip_spans, plan.duration_seconds)
        except ValueError as exc:
            raise EditConflict(f'镜头顺序未保存：{exc}') from exc
        created_audio_timeline = plan.narration_linkage_created_audio_timeline or plan.audio_timeline is None
        plan.audio_timeline = audio
        plan.narration_linkage_created_audio_timeline = created_audio_timeline
        rows = [dict(row, narration_segment_id=next((cue.narration_segment_id for cue in plan.cues if cue.id == row['cue_id']))) for row in linkage]
        return (rows, edited_tracks)

    def persist_subtitle_edit(self, *, workspace_id: str, task, proposal, base_version,
            result_version_id: str, operation_type: str, operation_payload: dict, subtitle_tracks,
            cover_asset=None, updated_plan=None, snapshot_source=None, narration_segments=None,
            narration_artifact=None) -> MetadataEditResult:
        montage_payload = creation_task_montage_plan_to_payload(updated_plan) if updated_plan is not None else None
        change = prepare_change_set(proposal=proposal, base_version=base_version,
            operation_type=operation_type, operation_payload=operation_payload, result_version_id=result_version_id,
            montage_plan_id=updated_plan.id if updated_plan is not None else None, montage_payload=montage_payload,
            subtitle_tracks=subtitle_tracks, cover_asset=cover_asset, snapshot_source=snapshot_source,
            narration_segments=narration_segments, narration_artifact=narration_artifact)
        try:
            _proposal, persisted_operation, _version, current_task = commit_change_set(self.repository, change)
        except ValueError as exc:
            raise EditConflict(str(exc)) from exc
        persisted_plan = creation_task_montage_plan_from_payload(
            self.repository.get_creation_task_montage_plan(workspace_id, task.id)
        )
        narration = (
            self.repository.get_narration_artifact(workspace_id, current_task.narration_cache_key)
            if current_task.narration_cache_key
            else None
        )
        return MetadataEditResult(persisted_operation, persisted_plan, current_task, narration)

    def persist_montage_edit(self, *, workspace_id: str, task: CreationTask, proposal: EditProposal, base_version: TimelineVersion, updated_plan: CreationTaskMontagePlan, operation_type: str, operation_payload: dict[str, object], narration_segments: list[NarrationSegment] | None=None, narration_artifact: NarrationArtifactRecord | None=None, subtitle_source_version: TimelineVersion | None=None, edited_subtitle_tracks: list[SubtitleTrack] | None=None, connection=None, direct_command=None, direct_intent=None, direct_source_verifier=None, proposal_source_command=None, proposal_source_verifier=None, result_version_id=None):
        try:
            dissolve_frames(updated_plan.clips)
            validate_overlays(updated_plan.graphic_overlays, updated_plan.duration_seconds)
            for clip in updated_plan.clips:
                fade_filters(clip)
        except ValueError as exc:
            raise InvalidMontage(str(exc)) from exc
        montage_payload = creation_task_montage_plan_to_payload(updated_plan)
        result_version_id = result_version_id or f'timeline_{uuid4().hex}'
        source_version = subtitle_source_version or base_version
        source_plan = creation_task_montage_plan_from_payload(source_version.snapshot['montage'])
        carried_tracks = [clone_subtitle_track(track, timeline_version_id=result_version_id) for track in (edited_subtitle_tracks if edited_subtitle_tracks is not None else self.current_subtitle_tracks(workspace_id, task, current_version=source_version, montage=source_plan))]
        audio_edit = operation_payload.get('edit') if operation_type == 'EditAudioTimeline' else None
        if isinstance(audio_edit, dict) and audio_edit.get('action') == 'replace_narration':
            carried_tracks = [replace(track, status='unavailable', artifact_ref=None, transform_id=None, failure_reason='整条旁白已更换，旧字幕时间尚未复核。', impact='原字幕文字已保留，但旧字幕轨不能作为新旁白的有效字幕输出。', repair_action='逐条检查字幕文字和时间，确认后重新生成字幕与成片。') for track in carried_tracks]
        if operation_type in {'RegenerateNarrationSegment', 'DeleteClip', 'DuplicateClip', 'InsertClip', 'ChangeLinkedSpeed', 'EditCompoundTimeline', 'RemoveSilence', 'AlignSubtitleTiming', 'ReorderClip', 'ReorderTimelineItems'}:
            try:
                if operation_type == 'RegenerateNarrationSegment':
                    carried_tracks = retime_subtitle_tracks(carried_tracks, source_plan.cues, updated_plan.cues)
                    carried_tracks = [replace(track, status='unavailable', artifact_ref=None, transform_id=None, failure_reason='局部配音已更新，字幕时间尚未复核。', impact='原字幕文字已保留，旧字幕产物不能作为新旁白的有效输出。', repair_action='核对字幕文字和时间后重新生成字幕与成片。') if track.status == 'valid' else track for track in carried_tracks]
                for index, track in enumerate(carried_tracks):
                    if operation_type == 'RegenerateNarrationSegment' or track.artifact_ref is not None:
                        continue
                    artifact_ref, transform_id = self.publish_subtitle_text(workspace_id=workspace_id, task_id=task.id, proposal_id=proposal.id, kind='subtitle_compound' if operation_type == 'EditCompoundTimeline' else 'subtitle_retime', input_refs=[f'timeline:{source_version.id}', updated_plan.narration_audio_path, *([operation_payload['source_audio_ref']] if operation_type == 'AlignSubtitleTiming' else [])], content=render_srt(execution_subtitle_track(track)), engine='subtitle-srt/v1' if operation_type in {'EditCompoundTimeline', 'AlignSubtitleTiming'} else 'timeline-clock-v1', parameters={'language': track.language, 'operation': operation_type})
                    carried_tracks[index] = clone_subtitle_track(track, timeline_version_id=result_version_id, artifact_ref=artifact_ref, transform_id=transform_id)
            except (OSError, ValueError) as exc:
                raise EditConflict(f'字幕重定时未保存，当前版本保持不变：{exc}') from exc
        source_cover = self.cover_asset_for_version(workspace_id, task.id, source_version)
        carried_cover = clone_cover_asset(source_cover, timeline_version_id=result_version_id) if source_cover is not None else None
        change = prepare_change_set(proposal=proposal, base_version=base_version, operation_type=operation_type, operation_payload=operation_payload, result_version_id=result_version_id, montage_plan_id=updated_plan.id, montage_payload=montage_payload, subtitle_tracks=carried_tracks, cover_asset=carried_cover, snapshot_source=source_version.snapshot, narration_segments=narration_segments, narration_artifact=narration_artifact, include_track_ids=edited_subtitle_tracks is not None or bool(carried_tracks) or 'subtitle_track_ids' in source_version.snapshot, absent_cover_value=True)
        try:
            _confirmed, persisted_operation, _version, _task = commit_direct_change(self.repository, direct_command, direct_intent, proposal, change, verify_sources=lambda command, conn: direct_source_verifier.verify_change(command, change.montage_payload, conn), connection=connection) if direct_intent is not None else commit_change_set(self.repository, change, connection=connection, verify_sources=(lambda conn: proposal_source_verifier.verify_change(proposal_source_command, change.montage_payload, conn)) if proposal_source_verifier is not None else None)
        except PermissionError as exc:
            raise PermissionError(str(exc)) from exc
        except OSError as exc:
            raise EditConflict('修改来源已失效，请重新预览') from exc
        except ValueError as exc:
            raise EditConflict(str(exc)) from exc
        if connection is not None:
            return persisted_operation
        persisted_plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        current_task = self.repository.get_creation_task(workspace_id, task.id)
        current_narration = self.repository.get_narration_artifact(workspace_id, current_task.narration_cache_key) if current_task.narration_cache_key else None
        return MetadataEditResult(persisted_operation, persisted_plan, current_task, current_narration)


    def preview_graphic_overlay(self, workspace_id, task_id, payload, *, identity, source, draft_only, execution):
        if payload.model_fields_set - {'operation_type', 'overlay_action', 'graphic_overlay', 'overlay_id'}:
            raise EditPreviewRejected(status_code=422, detail='图文叠加层编辑不能混入其他操作')
        task = self.task_or_missing(workspace_id, task_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
        if plan.status == 'rendering':
            raise EditPreviewRejected(status_code=409, detail='正在渲染，请完成后修改图文叠加层')
        try:
            edited = self.graphic_overlay_candidate(plan, payload)
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail=exc.args[0]) from exc
        except ValueError as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc
        action = payload.overlay_action
        identity = payload.graphic_overlay.id if action == 'add' else payload.overlay_id
        base = self.ensure_montage_base(workspace_id, task, plan)
        tracks = self.current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        try:
            for item in edited.graphic_overlays:
                if item.get('kind') == 'image':
                    resolve_graphic_source(self.repository, self.artifact_store, workspace_id, task_id, item['source_ref'])
            records = []
            if not draft_only:
                records = [self.picture_renderer.publish_picture(workspace_id, task, base, candidate, tracks, kind='graphic_preview', parameters={'graphic_overlays': candidate.graphic_overlays}, execution=execution) for candidate in (plan, edited)]
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc) if isinstance(exc, (ValueError, OSError)) else '图文叠加层预览失败，请检查本机渲染器') from exc
        if self.repository.get_creation_task(workspace_id, task_id).current_timeline_version_id != base.id:
            raise EditPreviewRejected(status_code=409, detail='预览期间时间线变化，请重新预览图文叠加层')
        summary = {'add': '添加图文叠加层', 'update': '修改图文叠加层', 'delete': '删除图文叠加层'}[action]
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=summary, context_stage='montage', selected_object_refs=[f'overlay:{identity}'], playhead_ms=None, intent_type='graphic_overlay', scope={'kind': 'overlay', 'id': identity}, constraints={'preserve_narration': True}, confidence=1, status='interpreted'), source=source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=summary, target_refs=[f'overlay:{identity}'], operation={'operation_type': 'EditGraphicOverlay', 'payload': {'base_version_id': base.id, 'graphic_overlays': edited.graphic_overlays}}, invalidated_refs=logical_montage_output_refs(plan), external_effects=[], preview={**{side: {'ref': f'timeline:{base.id}', 'label': label, 'detail': '草稿参数已计算；应用时保存新版本，不自动生成媒体。'} for side, label in (('before', '当前草稿'), ('after', '修改后草稿'))}, **{side: {'ref': record.logical_ref, 'label': '修改前' if side == 'before' else summary, 'detail': '完整成片视听预览；图文叠加层在指定区间显示，字幕、声音和镜头保持不变。', 'video_url': f'/api/creation-tasks/{task_id}/visual/artifacts/{record.id}'} for side, record in zip(('before', 'after'), records)}}))
        return (intent, proposal)


    def preview_transition(self, workspace_id, task_id, payload, *, identity, source, draft_only, execution):
        from copy import deepcopy
        if not payload.clip_id or payload.transition_seconds is None or payload.model_fields_set - {'operation_type', 'clip_id', 'transition_seconds'}:
            raise EditPreviewRejected(status_code=422, detail='转场需要后一个镜头和叠化秒数，不能混入其他操作')
        task = self.task_or_missing(workspace_id, task_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
        index = next((i for i, clip in enumerate(plan.clips) if clip.id == payload.clip_id), -1)
        if index <= 0:
            raise EditPreviewRejected(status_code=422, detail='请选择第二个或之后的镜头，修改它与前一个镜头之间的转场')
        targets = plan.clips[index - 1:index + 1]
        if plan.status == 'rendering' or any((clip.locked for clip in targets)):
            raise EditPreviewRejected(status_code=409, detail='正在渲染或转场镜头已锁定，请先解锁或等待完成')
        if targets[1].transition_in_seconds == payload.transition_seconds:
            raise EditPreviewRejected(status_code=422, detail='转场没有变化')
        base = self.ensure_montage_base(workspace_id, task, plan)
        edited = deepcopy(plan)
        edited.clips[index].transition_in_seconds = payload.transition_seconds
        tracks = self.current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        try:
            dissolve_frames(edited.clips)
            records = []
            if not draft_only:
                records = [self.picture_renderer.publish_picture(workspace_id, task, base, candidate, tracks, kind='transition_preview', parameters={'transition_seconds': candidate.clips[index].transition_in_seconds, 'clip_id': payload.clip_id}, execution=execution) for candidate in (plan, edited)]
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else '本地转场预览失败，请检查源素材和FFmpeg') from exc
        if self.repository.get_creation_task(workspace_id, task_id).current_timeline_version_id != base.id:
            raise EditPreviewRejected(status_code=409, detail='预览期间时间线变化，请重新预览转场')
        refs = [f'clip:{clip.id}' for clip in targets]
        source_command = metadata_edit_command(task, identity, 'SetClipTransition', {'base_version_id': base.id, 'clip_id': payload.clip_id, 'transition_seconds': payload.transition_seconds}, source=source)
        source_verifier = EditSourceVerifier(self.repository, identity=source_command.actor_identity)
        source_command = self.bind_affected_source_command(source_verifier, source_command, plan, edited)
        source_scope = source_verifier.scope_payload(source_command)
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=f'叠化{payload.transition_seconds:g}秒', context_stage='montage', selected_object_refs=refs, playhead_ms=round(targets[1].timeline_start_seconds * 1000), intent_type='transition', scope={'kind': 'clips', 'clip_ids': [clip.id for clip in targets]}, constraints={'preserve_timing': True, 'preserve_audio': True, 'affected_source_scope': source_scope}, confidence=1, status='interpreted'), source=source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=f'镜头 {index} → {index + 1}：' + (f'叠化 {payload.transition_seconds:g} 秒' if payload.transition_seconds else '硬切'), target_refs=refs, operation={'operation_type': 'SetClipTransition', 'payload': {'base_version_id': base.id, 'clip_id': payload.clip_id, 'transition_seconds': payload.transition_seconds, 'expected_clips': {clip.id: asdict(clip) for clip in targets}}}, invalidated_refs=logical_montage_output_refs(plan), external_effects=[], preview={**{side: {'ref': f'timeline:{base.id}', 'label': label, 'detail': '草稿参数已计算；应用时保存新版本，不自动生成媒体。'} for side, label in (('before', '当前草稿'), ('after', '修改后草稿'))}, **{side: {'ref': record.logical_ref, 'label': '当前转场' if side == 'before' else '修改后转场', 'detail': f'完整成片视听预览；转场从 {targets[1].timeline_start_seconds:.2f} 秒开始，读取前镜头结尾的真实素材余量，声音与字幕时轴不变。', 'video_url': f'/api/creation-tasks/{task_id}/visual/artifacts/{record.id}'} for side, record in zip(('before', 'after'), records)}}))
        return (intent, proposal)


    def preview_canvas(self, workspace_id, task_id, payload, *, identity, source, draft_only, execution):
        if payload.aspect_ratio is None or payload.model_fields_set - {'operation_type', 'aspect_ratio', 'fit_mode'}:
            raise EditPreviewRejected(status_code=422, detail='画幅修改需要9:16、16:9或1:1，可同时指定填充或适应')
        task = self.task_or_missing(workspace_id, task_id)
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
        base = self.ensure_montage_base(workspace_id, task, montage)
        if any((clip.locked for clip in montage.clips)):
            raise EditPreviewRejected(status_code=422, detail='改变画幅会影响所有镜头，请先解锁镜头')
        if montage.aspect_ratio == payload.aspect_ratio and (payload.fit_mode is None or all((clip.fit_mode == payload.fit_mode for clip in montage.clips))):
            raise EditPreviewRejected(status_code=409, detail='画幅与适配方式没有变化')
        clip = montage.clips[0]
        try:
            self.repository.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
            records = []
            if not draft_only:
                from copy import deepcopy
                changed = deepcopy(montage)
                changed.aspect_ratio = payload.aspect_ratio
                changed.clips = [replace(item, fit_mode=payload.fit_mode or item.fit_mode) for item in changed.clips]
                tracks = self.current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)
                records = [self.picture_renderer.publish_picture(workspace_id, task, base, candidate, tracks, kind='canvas_preview', parameters={'side': side, 'aspect_ratio': candidate.aspect_ratio}, execution=execution) for side, candidate in (('before', montage), ('after', changed))]
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else '画幅预览失败，请检查素材与本机FFmpeg') from exc
        if self.repository.get_creation_task(workspace_id, task_id).current_timeline_version_id != base.id:
            raise EditPreviewRejected(status_code=409, detail='预览期间版本变化，请重新预览')
        now = utc_now()
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=f'画幅改为{payload.aspect_ratio}', context_stage='montage', selected_object_refs=[], playhead_ms=None, intent_type='canvas', scope={'kind': 'timeline'}, constraints={'preserve_narration': True}, confidence=1, status='interpreted', created_at=now), source=source)
        cover = self.cover_asset_for_version(workspace_id, task_id, base)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=f'整片改为{payload.aspect_ratio}；配音、字幕文字和时轴不变。', target_refs=[f'timeline:{base.id}'], operation={'operation_type': 'ChangeCanvas', 'payload': {'base_version_id': base.id, 'aspect_ratio': payload.aspect_ratio, 'fit_mode': payload.fit_mode}}, invalidated_refs=[*logical_montage_output_refs(montage), *([cover.artifact_ref] if cover and cover.artifact_ref else [])], preview={**{side: {'ref': f'timeline:{base.id}', 'label': label, 'detail': '草稿参数已计算；应用时保存新版本，不自动生成媒体。'} for side, label in (('before', '当前草稿'), ('after', '修改后草稿'))}, **{side: {'ref': record.logical_ref, 'label': ratio, 'detail': '第一镜头最多5秒的静音画面预览；字幕将按新画幅重新排版。', 'video_url': f'/api/creation-tasks/{task_id}/visual/artifacts/{record.id}'} for side, ratio, record in zip(('before', 'after'), (montage.aspect_ratio, payload.aspect_ratio), records)}}, external_effects=['影响所有镜头，封面需重新生成；撤销可恢复。', '适应模式完整保留画面，可能留黑边；填充模式按裁切位置裁掉超出部分。'], status='proposed', created_at=now, updated_at=now))
        return (intent, proposal)


    def preview_metadata_settings(self, task, operation_type, values, base_version_id, *, identity, source):
        if base_version_id is None:
            raise EditPreviewRejected(status_code=409, detail='当前故事板没有基础版本，请先保留当前故事板并建立版本')
        command = metadata_edit_command(task, identity, operation_type, {'base_version_id': base_version_id, **values}, source=source)
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id, task.id))
            if operation_type in CLIP_SETTING_OPERATIONS:
                pair, _changed, _tracks, _linkage = prepare_clip_command(task, plan, command, resolve_source=lambda clip, values: self.resolve_edit_clip_source(task, identity, clip, values), link_reordered=lambda candidate: self.link_reordered_edit(task, candidate))
            else:
                pair, _changed = prepare_picture_command(task, plan, command, validate_graphic_sources=lambda overlays: self.validate_graphic_sources(task.workspace_id, task.id, overlays))
            intent, proposal = pair
            verifier = EditSourceVerifier(self.repository, identity=command.actor_identity)
            command = self.bind_affected_source_command(verifier, command, plan, _changed)
            intent = replace(intent, constraints={**intent.constraints, 'affected_source_scope': verifier.scope_payload(command)})
            proposal = replace(proposal, invalidated_refs=logical_montage_output_refs(plan))
            return (*self.repository.save_clip_preview_proposal(intent, proposal, expected_version_id=base_version_id),)
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail=str(exc)) from exc
        except (ValueError, EditConflict) as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc


    def preview_exact_montage(self, workspace_id, task_id, payload, *, identity, source, draft_only, execution):
        task = self.task_or_missing(workspace_id, task_id)
        if not payload.clip_id:
            raise EditPreviewRejected(status_code=422, detail='visual edit requires clip_id')
        try:
            persisted_plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            before_clip = next((item for item in persisted_plan.clips if item.id == payload.clip_id))
        except (KeyError, StopIteration) as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task montage clip not found') from exc
        if payload.operation_type == 'ReplaceClip' and (payload.asset_id == before_clip.asset_id and payload.segment_id == before_clip.segment_id and (payload.source_start_seconds in {None, before_clip.source_start_seconds})):
            raise edit_preview_failure(status_code=409, error='edit_no_change', reason='这个候选就是当前镜头。', impact='原镜头和成片保持不变。', repair_action='选择另一个片段，或调整裁剪起点。', retryable=True)
        invalidated_refs = logical_montage_output_refs(persisted_plan)
        if payload.operation_type == 'TrimClip' and task.current_timeline_version_id is None:
            raise edit_preview_failure(status_code=409, error='timeline_version_required', reason='当前故事板还没有可撤销的初始版本。', impact='裁剪预览和有效时间线均未改变。', repair_action='点击“保留当前故事板并建立版本”，再预览裁剪。', retryable=True)
        base_version = self.ensure_montage_base(workspace_id, task, persisted_plan)
        task = self.repository.get_creation_task(workspace_id, task_id)
        plan = creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(persisted_plan))
        supplied = {'fit_mode': payload.fit_mode, 'crop_x': payload.crop_x, 'crop_y': payload.crop_y, 'asset_id': payload.asset_id, 'segment_id': payload.segment_id, 'source_start_seconds': payload.source_start_seconds, 'split_time_seconds': payload.split_time_seconds, 'playback_rate': payload.playback_rate, 'position': payload.position, 'locked': payload.locked}
        required = EXACT_MONTAGE_REQUIRED_FIELDS[payload.operation_type]
        if any((supplied[key] is None for key in required)):
            raise EditPreviewRejected(status_code=422, detail='exact edit is missing required values')
        permitted = required | ({'source_start_seconds'} if payload.operation_type == 'ReplaceClip' else set())
        if any((value is not None and key not in permitted for key, value in supplied.items())):
            raise EditPreviewRejected(status_code=422, detail='exact edit mixes multiple operations')
        update = CreationTaskMontageClipUpdateRequest(split_time_seconds=payload.split_time_seconds if payload.operation_type == 'SplitClip' else None, playback_rate=payload.playback_rate if payload.operation_type == 'ChangeClipSpeed' else None, fit_mode=payload.fit_mode if payload.operation_type == 'ChangeCrop' else None, crop_x=payload.crop_x if payload.operation_type == 'ChangeCrop' else None, crop_y=payload.crop_y if payload.operation_type == 'ChangeCrop' else None, asset_id=payload.asset_id if payload.operation_type == 'ReplaceClip' else None, segment_id=payload.segment_id if payload.operation_type == 'ReplaceClip' else None, source_start_seconds=payload.source_start_seconds if payload.operation_type in {'ReplaceClip', 'TrimClip'} else None, position=payload.position if payload.operation_type == 'ReorderClip' else None, locked=payload.locked if payload.operation_type == 'SetClipLock' else None, confirmed=True if payload.operation_type == 'ConfirmClip' else None)
        plan, edited_tracks, linkage = self.apply_clip_update(workspace_id, task_id, plan, payload.clip_id, update, reader_identity=identity)
        after_clip = next((item for item in plan.clips if item.id == payload.clip_id))
        if creation_task_montage_plan_to_payload(plan)['clips'] == creation_task_montage_plan_to_payload(persisted_plan)['clips']:
            raise edit_preview_failure(status_code=409, error='edit_no_change', reason='这项设置与当前镜头一致。', impact='没有创建新的时间线版本。', repair_action='调整镜头、裁剪位置、顺序或锁定状态后再试。', retryable=True)
        labels = {'ChangeCrop': '调整画面适配', 'ReplaceClip': '替换镜头', 'TrimClip': '调整镜头起点', 'SplitClip': '分割镜头，保留配音与字幕时轴', 'ChangeClipSpeed': '改变画面速度，保留成片时长、声音和字幕', 'ReorderClip': '调整镜头顺序', 'SetClipLock': '更改镜头锁定状态', 'ConfirmClip': '确认当前镜头'}
        operation_payload: dict[str, object] = {'clip_id': payload.clip_id, 'base_version_id': base_version.id, 'expected_asset_id': before_clip.asset_id, 'expected_segment_id': before_clip.segment_id}
        for key, value in supplied.items():
            if value is not None:
                operation_payload[key] = value
        if payload.operation_type == 'ReplaceClip':
            operation_payload['source_start_seconds'] = after_clip.source_start_seconds
        if payload.operation_type == 'ChangeCrop':
            operation_payload.update(asset_id=before_clip.asset_id, segment_id=before_clip.segment_id, source_start_seconds=before_clip.source_start_seconds)

        def describe_clip(clip, clips):
            if payload.operation_type == 'ChangeClipSpeed':
                return f'{clip_playback_rate(clip):.2f} 倍速 · 源素材 {clip.source_start_seconds:.3f}–{clip.source_end_seconds:.3f} 秒'
            if payload.operation_type == 'SplitClip':
                count = len(clips) - len(persisted_plan.clips) + 1
                return f'{count} 个片段 · 成片 {before_clip.timeline_start_seconds:.3f}–{before_clip.timeline_end_seconds:.3f} 秒' + (f' · 分割点 {payload.split_time_seconds:.3f} 秒' if count == 2 else '')
            if payload.operation_type == 'ReorderClip':
                return f'第 {clips.index(clip) + 1} 个镜头'
            if payload.operation_type == 'SetClipLock':
                return '已锁定' if clip.locked else '未锁定'
            if payload.operation_type == 'ConfirmClip':
                return '待确认' if clip.needs_confirmation else '已确认'
            if payload.operation_type == 'ChangeCrop':
                if clip.fit_mode == 'fit':
                    return '完整显示 · 裁剪位置已保留，在此模式下不生效'
                return f'填满画面 · 水平 {clip.crop_x:.0%} · 垂直 {clip.crop_y:.0%}'
            return f'{clip.source_start_seconds:.1f}–{clip.source_end_seconds:.1f} 秒'
        media_previews = {}
        if payload.operation_type == 'TrimClip':
            try:
                for side, target in (('before', before_clip), ('after', after_clip)):
                    cue = next((cue for cue in plan.cues if cue.id == target.cue_id))
                    media_previews[side] = {'video_url': self.trim_clip_source_url(workspace_id, task_id, target, cue)}
            except (KeyError, StopIteration, ValueError, OSError) as exc:
                raise EditPreviewRejected(status_code=422, detail='裁剪源片段不可播放，请检查素材或人物口型产物') from exc
            if self.repository.get_creation_task(workspace_id, task_id).current_timeline_version_id != base_version.id:
                raise EditPreviewRejected(status_code=409, detail='预览期间时间线已变化，请重新预览')
        before_label = '人物口播' if before_clip.person_render_id else self.repository.get_asset(workspace_id, before_clip.asset_id).file_name
        after_label = '人物口播' if after_clip.person_render_id else self.repository.get_asset(workspace_id, after_clip.asset_id).file_name
        after_detail = describe_clip(after_clip, plan.clips)
        if linkage:
            after_detail = '\n'.join([after_detail, *self.linkage_lines(linkage)])
        now = utc_now()
        source_command = metadata_edit_command(task, identity, payload.operation_type, {'base_version_id': base_version.id, 'clip_id': payload.clip_id, **{key: value for key, value in supplied.items() if value is not None}}, source=source)
        source_verifier = EditSourceVerifier(self.repository, identity=source_command.actor_identity)
        source_command = self.bind_affected_source_command(source_verifier, source_command, persisted_plan, plan)
        source_scope = source_verifier.scope_payload(source_command)
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=f'精确操作：{labels[payload.operation_type]}', context_stage='montage', selected_object_refs=[f'clip:{payload.clip_id}'], playhead_ms=round(after_clip.timeline_start_seconds * 1000), intent_type='exact_montage_edit', scope={'kind': 'clip', 'clip_id': payload.clip_id}, constraints={'preserve_narration': True, 'affected_source_scope': source_scope}, confidence=1.0, status='interpreted', created_at=now), source=source)
        if linkage:
            proposal_summary = '调整镜头顺序；该人物镜头的配音与字幕已按来源一并移动，文案和其他镜头不变。'
        elif payload.operation_type == 'ReorderClip' and after_clip.person_render_id is None and any((item.person_render_id for item in plan.clips)):
            proposal_summary = '调整镜头顺序；本次移动的是纯素材镜头，按已冻结的纯素材默认只换画面，同片人物口型镜头的配音与字幕保持不变。'
        else:
            proposal_summary = f'{labels[payload.operation_type]}；文案、配音和其他镜头保持不变。'
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=proposal_summary, target_refs=[f'clip:{payload.clip_id}'], operation={'operation_type': payload.operation_type, 'payload': operation_payload}, invalidated_refs=invalidated_refs, preview={'before': {'ref': f'clip:{before_clip.id}', 'label': before_label, 'detail': describe_clip(before_clip, persisted_plan.clips), **media_previews.get('before', {})}, 'after': {'ref': f'clip:{after_clip.id}', 'label': after_label, 'detail': after_detail, **media_previews.get('after', {})}}, external_effects=['这里播放的是原视频画面和原声；配音、字幕和画面效果以最终成片为准。当前任务版本未改变。'] if payload.operation_type == 'TrimClip' else ['已在本地生成开头最多5秒的静音画面对比；声音、字幕和任务版本尚未改变。'] if media_previews else ['只更新任务版本；重新渲染前不会执行媒体进程。'], created_at=now, updated_at=now))
        return (task, base_version, plan, intent, proposal)


    def apply_clip_update(self, workspace_id, task_id, plan, clip_id, payload, *, reader_identity):
        identity, diagnostic = (reader_identity['member_id'], reader_identity['local_diagnostic'])
        task = self.repository.get_creation_task(workspace_id, task_id)
        try:
            edited, tracks, linkage = apply_clip_patch(plan, clip_id, payload.model_dump(exclude_unset=True), resolve_source=lambda clip, values: self.repository.resolve_edit_clip_source(workspace_id, task_id, clip, values, user_id=identity, local_diagnostic=diagnostic), link_reordered=lambda candidate: self.link_reordered_edit(task, candidate))
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail=str(exc)) from exc
        except TimelineVersionConflict as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        except ClipPreviewConflict as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            status = 422 if payload.split_time_seconds is not None else 400
            raise EditPreviewRejected(status_code=status, detail=str(exc)) from exc
        refresh_montage_review_state(edited)
        return (edited, tracks, linkage)


    def trim_clip_source_url(self, workspace_id, task_id, clip, cue):
        """Return an authorized source interval; browser playback does no media work."""
        if clip.person_render_id:
            render = self.repository.get_person_render(workspace_id, task_id, clip.person_render_id)
            if render.narration_segment_id != cue.narration_segment_id:
                raise ValueError('人物口型镜头与当前配音段落不匹配')
            synthetic_source = clip.asset_id == f'person:{render.id}' and clip.source_path == f'person-render://{render.id}'
            legacy_source = clip.source_path == render.visual_reference and (not clip.asset_id.startswith('person:'))
            if legacy_source:
                self.repository.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
            if not (synthetic_source or legacy_source):
                raise ValueError('人物口型镜头来源不匹配')
            path = self.picture_renderer.person_path(render)
            if path is None or clip.source_end_seconds > (render.duration_seconds or 0) + 0.001:
                raise ValueError('人物口型源片段不可用')
            route = f"/api/creation-tasks/{quote(task_id, safe='')}/person-renders/{quote(render.id, safe='')}/media"
        else:
            path = self.repository.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
            path.relative_to(Path(self.preview_settings.video_inbox_path).expanduser().resolve())
            route = f"/api/production/materials/{quote(clip.asset_id, safe='')}/media"
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('镜头源片段不可用')
        return f'{route}#t={clip.source_start_seconds:.3f},{clip.source_end_seconds:.3f}'


    def linkage_lines(self, linkage):
        """把关联对象与旧→新位置写成预览正文，不新增无人消费的字段。"""
        lines = ['该人物镜头的配音与字幕按来源一并移动：']
        for row in linkage:
            old_ms = int(row['old_timeline_start_ms'])
            new_ms = int(row['new_timeline_start_ms'])
            source_ms = int(row['source_end_ms']) - int(row['source_start_ms'])
            subtitles = '、'.join((str(item) for item in row['subtitle_cue_ids'])) or '无字幕'
            lines.append(f"- 文案 {row['cue_id']}（配音分段 {row['narration_segment_id']}，音频 {row['audio_clip_id']}，音频来源 {row['source_ref']}）：源区间 {int(row['source_start_ms'])}–{int(row['source_end_ms'])} 毫秒保持不变，时间线 {round(old_ms / 1000, 3)} 秒 → {round(new_ms / 1000, 3)} 秒（时长 {round(source_ms / 1000, 3)} 秒）；字幕 {subtitles}。")
        return lines


    def bind_affected_source_command(self, verifier, command, before, after):
        try:
            return verifier.bind_affected(command, before, after)
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc


    def resolve_edit_clip_source(self, task, reader_identity, clip, values):
        identity, diagnostic = (reader_identity['member_id'], reader_identity['local_diagnostic'])
        return self.repository.resolve_edit_clip_source(task.workspace_id, task.id, clip, values, user_id=identity, local_diagnostic=diagnostic)


    def graphic_overlay_candidate(self, plan, payload):
        return graphic_overlay_candidate(plan, action=payload.overlay_action, value=payload.graphic_overlay.model_dump() if payload.graphic_overlay is not None else None, identity=payload.overlay_id)


    def task_or_missing(self, workspace_id, task_id):
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task not found') from exc


    def cover_description(self, montage, base_version, payload):
        if payload.title is None or payload.frame_time_seconds is None:
            raise EditPreviewRejected(status_code=422, detail='cover edit requires title and frame_time_seconds')
        if montage.status != 'rendered' or not montage.output_path:
            raise edit_preview_failure(status_code=409, error='rendered_version_required', reason='当前任务没有可选帧的已渲染成片。', impact='没有生成封面，当前任务保持不变。', repair_action='先渲染当前成片，再选择画面和标题生成封面。', retryable=True)
        position_label = '画面中部' if payload.title_position == 'center' else '画面底部'
        summary = f'从成片 {payload.frame_time_seconds:.1f} 秒选择真实画面，并在{position_label}叠加标题生成封面。'
        target_refs = [montage.output_path]
        operation_payload = {'source_video_ref': montage.output_path, 'aspect_ratio': montage.aspect_ratio, 'frame_time_seconds': payload.frame_time_seconds, 'title': payload.title, 'title_position': payload.title_position, 'base_version_id': base_version.id}
        preview = {'before': {'ref': montage.output_path, 'label': '当前成片画面', 'detail': f'{payload.frame_time_seconds:.1f} 秒'}, 'after': {'ref': 'cover:preview', 'label': '封面标题', 'detail': f'{position_label} · {payload.title}'}}
        effects = ['确认后只运行一次本机 FFmpeg 与图片排版；不会调用 AI 生图模型。']
        return (summary, target_refs, operation_payload, preview, effects)


    def preview_replacement(self, task, payload, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        task_id = task.id
        command = payload.raw_command.strip()
        if payload.context_stage not in {'montage', 'review'}:
            raise edit_preview_failure(status_code=409, error='edit_scope_unavailable', reason='当前步骤不支持这项镜头修改。', impact='任务内容没有发生变化。', repair_action='进入视频剪辑或检查成片，选中一个镜头后再输入替换要求。', retryable=True)
        if not re.search('替换|改用|replace|换(?:成|为|一下|一段|一条|一个|个|镜头|素材|画面)', command.casefold()):
            raise edit_preview_failure(status_code=409, error='edit_intent_unsupported', reason='这条镜头指令还无法完整执行。', impact='任务内容没有发生变化。', repair_action='可指定替换镜头、曝光、冷暖或美颜；其他操作暂时使用右侧已有控件。', retryable=True)
        if len(payload.selected_object_refs) != 1 or not payload.selected_object_refs[0].startswith('clip:'):
            raise edit_preview_failure(status_code=409, error='edit_scope_required', reason='还没有确定要替换哪个镜头。', impact='系统不会猜测修改范围。', repair_action='先在故事板中选中一个镜头，再提交修改要求。', retryable=True)
        clip_id = payload.selected_object_refs[0].removeprefix('clip:')
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            clip = next((item for item in plan.clips if item.id == clip_id))
            current_asset = None if clip.person_render_id else self.repository.get_asset(workspace_id, clip.asset_id)
        except (KeyError, StopIteration) as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task edit target not found') from exc
        if plan.status == 'rendering':
            raise EditPreviewRejected(status_code=409, detail='rendering montage cannot be edited')
        if clip.locked:
            raise EditPreviewRejected(status_code=409, detail='请先解锁镜头，再替换素材。')
        invalidated_refs = logical_montage_output_refs(plan)
        viewer_user_id, local_diagnostic = (identity['member_id'], identity['local_diagnostic'])
        candidates = [candidate for candidate in self.montage_candidates(workspace_id, plan, clip, automation_only=True, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic) if (candidate.asset_id, candidate.segment_id) != (clip.asset_id, clip.segment_id) and candidate.end_seconds - candidate.start_seconds + 1e-06 >= (clip.timeline_end_seconds - clip.timeline_start_seconds) * clip_playback_rate(clip)]
        if payload.replacement_asset_id is not None:
            candidates = [item for item in candidates if (item.asset_id, item.segment_id) == (payload.replacement_asset_id, payload.replacement_segment_id)]
        material_constraint = '' if payload.replacement_asset_id is not None else edit_material_constraint(command)
        if material_constraint:
            slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
            matched_refs = self.repository.search_material_segments(workspace_id, query=material_constraint, usage_roles=['a_roll', 'b_roll'], min_duration_seconds=slot_duration, limit=24, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
            candidate_by_ref = {(f'asset:{candidate.asset_id}', f'segment:{candidate.segment_id}'): candidate for candidate in candidates}
            candidates = [candidate_by_ref[match.asset_ref, match.segment_ref] for match in matched_refs if (match.asset_ref, match.segment_ref) in candidate_by_ref]
        if not candidates:
            raise edit_preview_failure(status_code=409, error='replacement_clip_unavailable', reason=f'当前工作区没有符合“{material_constraint}”且满足时间槽的其他镜头。' if material_constraint else '当前工作区没有满足这个时间槽的其他可用镜头。', impact='原镜头和当前成片保持不变。', repair_action='导入或审核更多素材后重新提出替换要求。', retryable=True)
        base_version = self.ensure_montage_base(workspace_id, task, plan)
        replacement = candidates[0]
        now = utc_now()
        source_command = metadata_edit_command(task, identity, 'ReplaceClip', {'base_version_id': base_version.id, 'clip_id': clip.id, 'asset_id': replacement.asset_id, 'segment_id': replacement.segment_id, 'source_start_seconds': replacement.start_seconds}, source=request_source)
        changed, _tracks, _linkage = apply_clip_patch(plan, clip.id, source_command.values, resolve_source=lambda target, values: self.resolve_edit_clip_source(task, identity, target, values), link_reordered=lambda target: self.link_reordered_edit(task, target))
        verifier = EditSourceVerifier(self.repository, identity=source_command.actor_identity)
        source_command = self.bind_affected_source_command(verifier, source_command, plan, changed)
        source_scope = verifier.scope_payload(source_command)
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=command, context_stage=payload.context_stage, selected_object_refs=payload.selected_object_refs, playhead_ms=payload.playhead_ms, intent_type='replace_visual', scope={'kind': 'clip', 'clip_id': clip.id}, constraints={**{'preserve_narration': True, 'preserve_timing': True, 'material_query': material_constraint or None}, 'affected_source_scope': source_scope}, confidence=0.95, status='interpreted', created_at=now), source=request_source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=f'把选中的镜头替换为 {replacement.file_name}，保留当前配音和时间位置。', target_refs=[f'clip:{clip.id}'], operation={'operation_type': 'ReplaceClip', 'payload': {'clip_id': clip.id, 'asset_id': replacement.asset_id, 'segment_id': replacement.segment_id, 'source_start_seconds': replacement.start_seconds, 'expected_asset_id': clip.asset_id, 'expected_segment_id': clip.segment_id, 'base_version_id': base_version.id, 'automation_only': True}}, invalidated_refs=invalidated_refs, preview={'before': {'ref': f'clip:{clip.id}', 'label': current_asset.file_name if current_asset else '人物口播', 'detail': f'{clip.source_start_seconds:.1f}–{clip.source_end_seconds:.1f} 秒'}, 'after': {'ref': f'segment:{replacement.segment_id}', 'label': replacement.file_name, 'detail': f'{replacement.start_seconds:.1f}–{replacement.start_seconds + clip.timeline_end_seconds - clip.timeline_start_seconds:.1f} 秒 · {replacement.description}'}}, external_effects=['确认后只更新任务版本，不会立即调用模型或渲染。'], created_at=now, updated_at=now))
        return (intent, proposal)


    def montage_candidates(self, workspace_id, plan, clip, *, automation_only=False, viewer_user_id=None, local_diagnostic=False):
        cue = next((item for item in plan.cues if item.id == clip.cue_id), None)
        cue_tokens = set((cue.text if cue else '').lower())
        slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
        rate = clip_playback_rate(clip)
        source_need = slot_duration * rate
        candidates: list[MontageClipCandidateResponse] = []
        for asset in self.repository.list_assets(workspace_id, include_private=viewer_user_id is not None or local_diagnostic, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic):
            for segment in asset.segments:
                if not material_segment_eligible(asset, segment, workspace_id, automation_only=automation_only):
                    continue
                if abs(rate - 1) > 0.001 and (clip.person_render_id or effective_segment_usage_role(asset, segment) == 'a_roll'):
                    continue
                available = segment.end_seconds - segment.start_seconds
                if available + 1e-06 < source_need:
                    continue
                description = (segment.description or segment.transcript or asset.file_name).strip()
                overlap = len(cue_tokens & set(description.lower())) / max(1, len(cue_tokens))
                score = max(0.0, min(1.0, overlap * 0.7 + min(1.0, available / max(slot_duration, 0.1)) * 0.3))
                candidates.append(MontageClipCandidateResponse(asset_id=asset.id, segment_id=segment.id, file_name=asset.file_name, start_seconds=segment.start_seconds, end_seconds=segment.end_seconds, description=description, score=round(score, 3), reasons=['当前工作区可用素材', '时长满足当前旁白时间槽']))
        candidates.sort(key=lambda item: (item.asset_id == clip.asset_id and item.segment_id == clip.segment_id, item.score), reverse=True)
        return candidates[:24]


    def preview_reorder(self, task, payload, change, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        except KeyError as exc:
            raise EditPreviewRejected(status_code=409, detail='当前任务没有可排序的故事板。') from exc
        kind, source, destination = change
        if min(source, destination) < 0 or max(source, destination) >= len(plan.clips):
            raise EditPreviewRejected(status_code=422, detail='镜头序号超出当前故事板，请重新查看镜头。')
        if source == destination:
            raise EditPreviewRejected(status_code=409, detail='两个镜头位置相同，没有需要应用的修改。')
        old_order = [clip.id for clip in plan.clips]
        new_order = list(old_order)
        target_ids = [old_order[source]]
        if kind == 'swap':
            target_ids.append(old_order[destination])
            new_order[source], new_order[destination] = (new_order[destination], new_order[source])
        else:
            new_order.insert(destination, new_order.pop(source))
        link_narration = self.reorder_narration_scope(plan, new_order, target_ids)
        base = self.ensure_montage_base(workspace_id, task, plan)
        candidate = creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(plan))
        for index, clip_id in enumerate(new_order):
            if old_order[index] != clip_id:
                candidate, _tracks, _linkage = self.apply_clip_update(workspace_id, task.id, candidate, clip_id, CreationTaskMontageClipUpdateRequest(), reader_identity=identity)
        _tracks, linkage = self.reorder_montage_plan(workspace_id, task, candidate, new_order, link_narration=link_narration)
        if linkage and ('配音不变' in payload.raw_command or '配音和字幕不变' in payload.raw_command):
            raise EditPreviewRejected(status_code=409, detail='人物镜头换位须联动对应配音和字幕，与保持配音时轴不变的要求冲突。')
        summary = f'交换第{source + 1}和第{destination + 1}个镜头' if kind == 'swap' else f'将第{source + 1}个镜头移动到第{destination + 1}个位置'
        summary += '；总时长保持不变；' + ('人物镜头的配音和字幕按来源一并移动。' if linkage else '配音和字幕时轴保持不变。')
        source_command = metadata_edit_command(task, identity, 'ReorderTimelineItems', {'base_version_id': base.id, 'target_clip_ids': target_ids, 'new_order': new_order}, source=request_source)
        verifier = EditSourceVerifier(self.repository, identity=source_command.actor_identity)
        source_command = self.bind_affected_source_command(verifier, source_command, plan, candidate)
        source_scope = verifier.scope_payload(source_command)
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, raw_command=payload.raw_command, context_stage=payload.context_stage, selected_object_refs=payload.selected_object_refs, playhead_ms=payload.playhead_ms, intent_type='trim_or_reorder', scope={'target_refs': [f'clip:{item}' for item in target_ids]}, constraints={**{'preserve_duration': True, 'linked_narration': link_narration}, 'affected_source_scope': source_scope}, confidence=1, status='interpreted'), source=request_source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, intent_id=intent.id, summary=summary, target_refs=[f'clip:{item}' for item in target_ids], operation={'operation_type': 'ReorderTimelineItems', 'payload': {'base_version_id': base.id, 'current_order': old_order, 'new_order': new_order, 'target_clip_ids': target_ids, 'link_narration': link_narration}}, invalidated_refs=logical_montage_output_refs(plan), preview={'before': {'ref': f'timeline:{base.id}', 'label': '当前镜头顺序', 'detail': ' → '.join((str(old_order.index(item) + 1) for item in old_order))}, 'after': {'ref': f'timeline:{base.id}', 'label': '修改后镜头顺序', 'detail': summary + ('\n' + '\n'.join(self.linkage_lines(linkage)) if linkage else '')}}, external_effects=['应用时只保存一个新版本；不自动渲染，可一次撤销整组排序。'], status='proposed'))
        return (intent, proposal)


    def reorder_narration_scope(self, plan, new_order, target_ids):
        if any((clip.person_render_id for clip in plan.clips if clip.id in target_ids)):
            return True
        by_id = {clip.id: clip for clip in plan.clips}
        cursor = 0.0
        for clip_id in new_order:
            clip = by_id[clip_id]
            duration = clip.timeline_end_seconds - clip.timeline_start_seconds
            if clip.person_render_id and (abs(round(cursor, 3) - clip.timeline_start_seconds) > 0.001 or abs(round(cursor + duration, 3) - clip.timeline_end_seconds) > 0.001):
                raise EditPreviewRejected(status_code=409, detail='这次换位会移动人物口型镜头；需连同其配音字幕一起调整，请明确联动范围。')
            cursor += duration
        return False


    def reorder_montage_plan(self, workspace_id, task, plan, new_order, *, link_narration):
        try:
            changed = reorder_picture(plan, new_order)
        except ValueError as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        plan.clips = changed.clips
        if link_narration:
            linkage, tracks = self.reorder_narration_linkage(workspace_id, task, plan)
            return (tracks, linkage)
        return (None, None)


    def selected_crop_command(self, workspace_id, task_id, payload, *, base_version_id, request_source):
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            clip = next((item for item in plan.clips if item.id == payload.selected_object_refs[0].removeprefix('clip:')))
            crop = compile_crop_command(payload.raw_command.strip(), {'fit_mode': clip.fit_mode, 'crop_x': clip.crop_x, 'crop_y': clip.crop_y, 'clip_index': plan.clips.index(clip) + 1})
            if crop is not None:
                return ClipPreviewCommand(task_id, base_version_id, 'ChangeCrop', [clip.id], crop, payload.raw_command, request_source)
        except (ValueError, KeyError, StopIteration) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc) or '当前镜头不可用') from exc
        return None


    def command_cover(self, task, payload, selected_ref, command, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        if payload.context_stage != 'subtitle_cover':
            raise edit_preview_failure(status_code=409, error='edit_scope_unavailable', reason='当前步骤不能修改封面。', impact='任务内容没有发生变化。', repair_action='进入字幕与封面步骤后选择封面。', retryable=True)
        try:
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task montage plan not found') from exc
        title_match = re.search('标题(?:写|改成|改为|为)?[：:]?\\s*[“\\"\']?(.+?)[”\\"\']?(?:[，,。]|$)', command)
        title = title_match.group(1).strip(' “”。"\'') if title_match else None
        if not title:
            raise edit_preview_failure(status_code=409, error='cover_title_missing', reason='没有识别出要显示的封面标题。', impact='没有生成封面，当前任务保持不变。', repair_action='输入“标题写……”并保留完整标题。', retryable=True)
        seconds_match = re.search('(\\d+(?:\\.\\d+)?)\\s*秒', command)
        frame_time = float(seconds_match.group(1)) if seconds_match else None
        ordinal_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        ordinal_match = re.search('第([一二三四五六七八九十]|\\d+)个?镜头', command)
        if frame_time is None and ordinal_match:
            raw_index = ordinal_match.group(1)
            index = int(raw_index) if raw_index.isdigit() else ordinal_map.get(raw_index, 0)
            if 1 <= index <= len(montage.clips):
                clip = montage.clips[index - 1]
                frame_time = min(clip.timeline_end_seconds - 0.05, clip.timeline_start_seconds + 0.2)
        if frame_time is None:
            frame_time = payload.playhead_ms / 1000 if payload.playhead_ms is not None else 0.0
        return CreationTaskExactEditRequest(operation_type='GenerateCoverFromFrame', frame_time_seconds=frame_time, title=title)
