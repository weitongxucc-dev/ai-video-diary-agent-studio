"""Concrete metadata commands, durable replay and one montage-version commit."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4
import hashlib
import json

from app.creation.contracts import (EditCommand, EditIntent, EditProposal, SubtitleTrack,
    SubtitleCue, JobRun, ArtifactRecord, TransformRun, DependencyEdge, TimelineVersion)
from app.creation.transactions import (clone_subtitle_track, clone_cover_asset,
    prepare_change_set, commit_direct_change, commit_change_set)
from app.creation.editing.metadata import (check_command, prepare_subtitle_display_command,
    prepare_clip_command, prepare_picture_command, prepare_audio_command,
    make_metadata_proposal, metadata_request_identity, PICTURE_METADATA_OPERATIONS)
from app.creation.editing.clips import CLIP_METADATA_OPERATIONS
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.creation.editing.subtitles import apply_subtitle_cue
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.audio_timeline import narration_audio_timeline
from app.graphic_sources import resolve_graphic_source
from app.graphic_overlays import validate_overlays
from app.transitions import dissolve_frames
from app.visual_effects import fade_filters
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

    def current_narration_snapshot(self, workspace_id: str, task: CreationTask) -> dict[str, object]:
        if not task.confirmed_script_id:
            return {}
        segments = self.repository.list_narration_segments(workspace_id, task.id, task.confirmed_script_id)
        return {'narration_segments': [json.loads(json.dumps(asdict(segment), ensure_ascii=False, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))) for segment in segments], 'narration_cache_key': task.narration_cache_key}

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
