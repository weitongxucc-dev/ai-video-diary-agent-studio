"""One finite subtitle family; publication and the existing joint save keep their owners."""
from dataclasses import dataclass, replace
from uuid import uuid4

from app.canvas import canvas_size
from app.creation.contracts import EditCommand, EditIntent, EditProposal
from app.creation.editing.metadata import check_command
from app.creation.editing.metadata_use_case import (MetadataEditUseCase, MetadataEditResult,
    SubtitleNoChange, SubtitleTargetMissing, normalized_subtitle_text, execution_subtitle_track,
    logical_montage_output_refs, prepare_changed_montage)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.creation.editing.subtitle_changes import (prepare_subtitle_content_proposal,
    apply_subtitle_content_command, calculate_subtitle_style, prepare_subtitle_style_proposal,
    apply_subtitle_style_command)
from app.creation.editing.subtitles import apply_subtitle_cue
from app.creation.transactions import clone_subtitle_track, clone_cover_asset
from app.compound_edits import synchronize_linked_subtitle_cue
from app.models import utc_now
from app.montage import creation_task_montage_plan_from_payload, _subtitle_font_and_lines
from app.subtitle_schedule import subtitle_schedule, new_subtitle_gap_intervals
from app.subtitle_tracks import render_srt

SUBTITLE_CONTENT_OPERATIONS = frozenset({'ReplaceSubtitleText', 'SplitSubtitleCue', 'MergeSubtitleCue', 'DeleteSubtitleCue'})


def is_subtitle_edit(operation_type, values):
    return (operation_type in SUBTITLE_CONTENT_OPERATIONS
        or (operation_type == 'ConfigureSubtitleTrack' and values.get('style') is not None
            and values.get('subtitle_display') is None and values.get('target_language') is None)
        or (operation_type == 'UpdateSubtitleCue' and isinstance(values.get('track_id'), str)))


@dataclass(frozen=True)
class PreparedSubtitleEdit:
    task: object
    base: object
    montage: object
    intent: object
    proposal: object
    style_change: object | None = None


class SubtitlePreviewFailed(ValueError):
    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


class SubtitleEditUseCase:
    def __init__(self, repository, montage_edits: MetadataEditUseCase):
        if montage_edits.repository is not repository:
            raise ValueError('字幕编辑必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits

    def prepare_preview(self, command: EditCommand) -> PreparedSubtitleEdit:
        if not is_subtitle_edit(command.operation_type, command.values):
            raise ValueError('提案不是纯字幕编辑')
        task = self.repository.get_creation_task(command.workspace_id, command.task_id)
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(command.workspace_id, task.id))
        base = self.montage_edits.ensure_montage_base(command.workspace_id, task, montage)
        task = self.repository.get_creation_task(command.workspace_id, task.id)
        command_base = command.base_version_id if command.base_version_id is not None else base.id
        command = replace(command, base_version_id=command_base, values={**command.values, 'base_version_id': command_base})
        tracks = self.montage_edits.current_subtitle_tracks(command.workspace_id, task, current_version=base, montage=montage)
        style_change = None
        if command.operation_type in SUBTITLE_CONTENT_OPERATIONS:
            change, intent, proposal = prepare_subtitle_content_proposal(task, montage, tracks, command,
                output_refs=logical_montage_output_refs(montage))
            if command.operation_type == 'ReplaceSubtitleText':
                render_srt(execution_subtitle_track(change.changed_tracks[0]))
            else:
                for track in change.changed_tracks:
                    if track.cues:
                        render_srt(execution_subtitle_track(track))
        elif command.operation_type == 'ConfigureSubtitleTrack':
            style_change = calculate_subtitle_style(tracks, command.values['style'], montage.duration_seconds,
                validate_layout=lambda text, style: _subtitle_font_and_lines(text, style, size=canvas_size(montage.aspect_ratio)))
            intent, proposal = prepare_subtitle_style_proposal(task, command, style_change,
                output_refs=logical_montage_output_refs(montage))
        else:
            check_command(task, command)
            intent, proposal = self._prepare_cue(task, montage, base, tracks, command)
        return PreparedSubtitleEdit(task, base, montage, intent, proposal, style_change)

    def save_preview(self, prepared: PreparedSubtitleEdit):
        if prepared.proposal.operation['operation_type'] == 'UpdateSubtitleCue':
            # This original exact-cue inlet registers intent then proposal separately.
            intent = self.repository.create_edit_intent(prepared.intent)
            proposal = self.repository.create_edit_proposal(prepared.proposal)
            return intent, proposal
        return self.repository.save_clip_preview_proposal(prepared.intent, prepared.proposal,
            expected_version_id=prepared.base.id)

    def _binding(self, command, proposal_id):
        task = self.repository.get_creation_task(command.workspace_id, command.task_id)
        check_command(task, EditCommand(command.workspace_id, command.task_id, command.actor_identity,
            task.current_timeline_version_id, 'Subtitle', {}))
        proposal = self.repository.get_edit_proposal(command.workspace_id, proposal_id)
        if proposal.task_id != command.task_id:
            raise KeyError(proposal_id)
        operation, payload = proposal.operation.get('operation_type'), proposal.operation.get('payload')
        if not isinstance(payload, dict) or not is_subtitle_edit(operation, payload):
            raise EditConflict('提案不是纯字幕编辑')
        if operation != command.operation_type or payload != command.values:
            raise EditConflict('原字幕提案输入已变化')
        source = self.repository.get_edit_intent(command.workspace_id, proposal.intent_id).constraints.get('request_source')
        if source != command.source:
            raise EditConflict('原提案来源已变化')
        member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
        if source is not None and source['member_id'] != member:
            raise PermissionError('提案不属于当前请求成员')
        return task, proposal

    def read_result(self, command: EditCommand, proposal_id):
        task, proposal = self._binding(command, proposal_id)
        operations = [item for item in self.repository.list_edit_operations(command.workspace_id, task.id)
            if item.proposal_id == proposal.id]
        if not operations:
            return None
        operation = operations[0]
        version = self.repository.get_timeline_version(command.workspace_id, operation.result_version_id)
        montage = creation_task_montage_plan_from_payload(version.snapshot['montage'])
        return MetadataEditResult(operation, montage, task, None)

    def confirm(self, command: EditCommand, proposal_id):
        task, proposal = self._binding(command, proposal_id)
        if proposal.status != 'proposed':
            raise EditConflict('creation task edit proposal is not confirmable')
        operation_type, operation_payload = proposal.operation['operation_type'], proposal.operation['payload']
        if operation_type == 'UpdateSubtitleCue':
            if any(not isinstance(operation_payload.get(key), str) or not operation_payload.get(key)
                    for key in ('cue_id', 'text', 'expected_text', 'base_version_id')):
                raise EditConflict('invalid subtitle edit operation')
        if task.current_timeline_version_id != operation_payload.get('base_version_id'):
            message = ('字幕版本已变化，请重新预览样式' if operation_type == 'ConfigureSubtitleTrack'
                else 'creation task edit proposal is stale' if operation_type == 'UpdateSubtitleCue'
                else '字幕版本已变化，请重新预览修改')
            raise EditConflict(message)
        if command.source is not None:
            EditSourceVerifier(self.repository, identity=command.actor_identity).verify(command, None)
        check_command(task, command)
        base = self.repository.get_timeline_version(command.workspace_id, task.current_timeline_version_id)
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(command.workspace_id, task.id))
        tracks = self.montage_edits.current_subtitle_tracks(command.workspace_id, task, current_version=base, montage=montage)
        version_id = f'timeline_{uuid4().hex}'
        if operation_type in SUBTITLE_CONTENT_OPERATIONS:
            montage, content = apply_subtitle_content_command(task, montage, tracks, command)
            changed_tracks, changes = content.changed_tracks, content.text_changes
            published_tracks = []
            for changed in changed_tracks:
                original = next(item for item in tracks if item.id == changed.id)
                if changed.cues:
                    subtitle_ref, transform_id = self.montage_edits.publish_subtitle_text(workspace_id=command.workspace_id,
                        task_id=task.id, proposal_id=proposal.id, kind='subtitle_text_edit',
                        input_refs=[original.artifact_ref or f'subtitle-track:{original.id}'],
                        content=render_srt(execution_subtitle_track(changed)), engine='local-subtitle-edit/v1',
                        parameters={'track_id': original.id, 'changes': changes})
                    published_tracks.append(clone_subtitle_track(changed, timeline_version_id=version_id,
                        artifact_ref=subtitle_ref, transform_id=transform_id))
            retained = [clone_subtitle_track(other, timeline_version_id=version_id) for other in content.retained_tracks]
            updated_tracks = published_tracks + retained
            message = '字幕修改已保存，配音未改；请重新渲染。'
        elif operation_type == 'ConfigureSubtitleTrack':
            calculated = apply_subtitle_style_command(task, tracks, command)
            updated_tracks = [clone_subtitle_track(track, timeline_version_id=version_id) for track in calculated]
            message = '字幕样式已保存；重新渲染后写入成片。'
        else:
            montage, updated_tracks = self._apply_cue(command, task, proposal, montage, tracks, version_id)
            message = '字幕已修改；请重新渲染以写入成片。'
        cover = self.montage_edits.cover_asset_for_version(command.workspace_id, task.id, base)
        prepare_changed_montage(montage, message=message)
        return self.montage_edits.persist_subtitle_edit(workspace_id=command.workspace_id, task=task, proposal=proposal,
            base_version=base, result_version_id=version_id, operation_type=operation_type,
            operation_payload=operation_payload, subtitle_tracks=updated_tracks,
            cover_asset=clone_cover_asset(cover, timeline_version_id=version_id) if cover else None, updated_plan=montage)

    def apply(self, command: EditCommand):
        try:
            prepared = self.prepare_preview(command)
            _intent, proposal = self.save_preview(prepared)
        except (PermissionError, EditConflict, SubtitleTargetMissing):
            raise
        except (ValueError, OSError) as exc:
            raise SubtitlePreviewFailed(exc) from exc
        payload = proposal.operation['payload']
        return self.confirm(replace(command, base_version_id=payload['base_version_id'], values=payload), proposal.id)

    def _prepare_cue(self, task, montage, base_version, tracks, command):
        values = command.values
        if not values.get('cue_id'):
            raise ValueError("subtitle edit requires cue_id")
        track = next(
            (
                item
                for item in tracks
                if (values.get('track_id') is None or item.id == values.get('track_id'))
                and any(cue.id == values.get('cue_id') for cue in item.cues)
            ),
            None,
        )
        if track is None:
            raise SubtitleTargetMissing("subtitle track or cue not found")
        cue = next(cue for cue in track.cues if cue.id == values.get('cue_id'))
        if track.language == "en-US" and values.get('style') is not None and values.get('style') != track.style:
            raise ValueError("双语字幕共用样式，请在中文轨调整；英文轨支持文字、断行和强调词。")
        normalized_text, normalized_line_breaks = normalized_subtitle_text(
            values.get('text') if values.get('text') is not None else cue.text,
            values.get('line_breaks') if values.get('line_breaks') is not None else (cue.line_breaks if values.get('text') is None or values.get('text') == cue.text else None),
        )
        start = values.get('start_seconds') if values.get('start_seconds') is not None else cue.start_seconds
        end = values.get('end_seconds') if values.get('end_seconds') is not None else cue.end_seconds
        candidate = replace(cue, text=normalized_text, start_seconds=start, end_seconds=end)
        candidate_track = replace(track, cues=[candidate if item.id == cue.id else item for item in track.cues])
        try:
            subtitle_schedule([replace(track, status="valid", style={**track.style, "enabled": True},
                cues=[candidate if item.id == cue.id else item for item in track.cues])], montage.duration_seconds)
            changed_tracks = synchronize_linked_subtitle_cue(tracks, track, candidate_track, cue.id, montage.duration_seconds,
                                            invalidate_all_english_on_text_change=True)
        except ValueError as exc:
            raise
        changed = (
            start != cue.start_seconds or end != cue.end_seconds
            or
            normalized_text != cue.text
            or normalized_line_breaks != cue.line_breaks
            or values.get('emphasis') is not None
            or values.get('style') is not None
        )
        if not changed:
            raise SubtitleNoChange(True)
        summary = "只修改所选字幕条目；配音、镜头和其他字幕保持不变。"
        target_refs = [f"subtitle-cue:{track.id}:{cue.id}"]
        operation_payload = {
            "track_id": track.id,
            "cue_id": cue.id,
            "text": normalized_text,
            "start_seconds": start,
            "end_seconds": end,
            "line_breaks": normalized_line_breaks,
            "emphasis": values.get('emphasis') if values.get('emphasis') is not None else cue.emphasis,
            "style": values.get('style') if values.get('style') is not None else track.style,
            "expected_text": cue.text,
            "base_version_id": base_version.id,
        }
        preview = {
            "before": {"ref": target_refs[0], "label": "当前字幕", "detail": f"{cue.start_seconds:.2f}–{cue.end_seconds:.2f} 秒 · {cue.text}"},
            "after": {"ref": target_refs[0], "label": "修改后字幕", "detail": f"{start:.2f}–{end:.2f} 秒 · {normalized_text}"},
        }
        effects = ["确认后只重建字幕产物并使当前视频输出失效；不会重新生成配音或镜头。"]
        effects.extend(
            f"{'中文' if before.language == 'zh-CN' else '英文'}字幕新增无字幕时段：{start:.2f}–{end:.2f} 秒。"
            for before in tracks for after in changed_tracks if before.id == after.id
            for start, end in new_subtitle_gap_intervals(before, after, montage.duration_seconds)
        )
        if track.language == "zh-CN" and normalized_text != cue.text and any(item.language == "en-US" for item in tracks):
            effects.append("中文内容已改变，旧英文翻译将移除；需要重新翻译后才能输出双语字幕。")
        now = utc_now()
        intent = EditIntent(id=f'intent_{uuid4().hex}', workspace_id=command.workspace_id, task_id=task.id,
            raw_command=f'精确操作：{summary}', context_stage='review' if task.stage == 'review' else 'montage',
            selected_object_refs=target_refs, playhead_ms=None, intent_type='exact_subtitle_cover_edit',
            scope={'kind': 'UpdateSubtitleCue'}, constraints={'preserve_narration': True, 'preserve_clips': True,
                **({'request_source': command.source} if command.source is not None else {})},
            confidence=1.0, status='interpreted', created_at=now)
        proposal = EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=command.workspace_id, task_id=task.id,
            intent_id=intent.id, summary=summary, target_refs=target_refs,
            operation={'operation_type': 'UpdateSubtitleCue', 'payload': operation_payload},
            invalidated_refs=logical_montage_output_refs(montage), preview=preview, external_effects=effects,
            created_at=now, updated_at=now)
        return intent, proposal

    def _apply_cue(self, command, task, proposal, montage, current_tracks, result_version_id):
        operation_payload = command.values
        track_id = operation_payload['track_id']
        cue_id, text = operation_payload['cue_id'], operation_payload['text']
        track, updated_track, linked_tracks = apply_subtitle_cue(current_tracks, operation_payload, montage.duration_seconds)
        cue = next(item for item in track.cues if item.id == cue_id)
        updated_cues, updated_style = updated_track.cues, updated_track.style
        provisional = clone_subtitle_track(track, timeline_version_id=result_version_id,
            cues=updated_cues, style=dict(updated_style))
        subtitle_ref, transform_id = self.montage_edits.publish_subtitle_text(workspace_id=command.workspace_id,
            task_id=task.id, proposal_id=proposal.id, kind='subtitle_cue_update',
            input_refs=[track.artifact_ref or f'subtitle-track:{track.id}'],
            content=render_srt(execution_subtitle_track(provisional)), engine='subtitle-srt/v1',
            parameters={'track_id': track.id, 'cue_id': cue.id})
        changed_track = clone_subtitle_track(track, timeline_version_id=result_version_id,
            cues=updated_cues, style=dict(updated_style), artifact_ref=subtitle_ref, transform_id=transform_id)
        retained = []
        previous_tracks = {item.id: item for item in current_tracks}
        for item in linked_tracks:
            if item.id == track.id:
                continue
            previous = previous_tracks[item.id]
            if item.cues != previous.cues:
                linked_ref, linked_transform_id = self.montage_edits.publish_subtitle_text(workspace_id=command.workspace_id,
                    task_id=task.id, proposal_id=proposal.id, kind='subtitle_linked_timing_update',
                    input_refs=[previous.artifact_ref or f'subtitle-track:{previous.id}'],
                    content=render_srt(execution_subtitle_track(item)), engine='subtitle-srt/v1',
                    parameters={'track_id': item.id, 'source_track_id': track.id, 'cue_id': cue.id})
                retained.append(clone_subtitle_track(item, timeline_version_id=result_version_id,
                    artifact_ref=linked_ref, transform_id=linked_transform_id))
            else:
                retained.append(clone_subtitle_track(item, timeline_version_id=result_version_id))
        if track.source_language is None:
            montage_cue = next((item for item in montage.cues if item.id == cue.id), None)
            if montage_cue is not None:
                montage_cue.text = str(text)
        return montage, [changed_track, *retained]
