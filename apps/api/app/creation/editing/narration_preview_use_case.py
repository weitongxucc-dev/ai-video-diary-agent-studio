"""Original NarrationPreviewUseCase command preview, separate from compilation and rendering."""
from __future__ import annotations

import re

from app.creation.contracts import EditIntent
from app.creation.contracts import EditProposal
from app.production import MediaAuthorityError
from app.narration import compile_narration_command
from app.montage import creation_task_montage_plan_from_payload
from app.models import utc_now
from uuid import uuid4
from app.creation.editing.metadata_use_case import EditPreviewRejected, edit_preview_failure, metadata_edit_command, effective_audio_timeline, logical_montage_output_refs


def inspect_narration_revision(source_text, replacement_text, protected_rules):
    """Check explicit literal rules only; this result never asserts semantic equivalence."""
    checks = []
    for rule in protected_rules:
        source_present = rule.source_text in source_text
        required_is_bound = rule.required_text in rule.source_text
        if rule.kind in {'exact_text', 'qualified_claim'}:
            required_is_bound = rule.required_text == rule.source_text
            retained = rule.required_text in replacement_text
        else:
            pattern = r'(?<![\d.])' + re.escape(rule.required_text) + r'(?![\d.])'
            required_is_bound = (any(char.isdigit() for char in rule.required_text)
                and re.search(pattern,rule.source_text) is not None)
            retained = re.search(pattern, replacement_text) is not None
        checks.append({'rule_id': rule.id, 'kind': rule.kind, 'source_reference': rule.source_reference,
            'source_bound': source_present and required_is_bound, 'retained': retained,
            'passed': source_present and required_is_bound and retained})
    return {'status': 'passed' if all(item['passed'] for item in checks) else 'rejected',
            'checks': checks, 'semantic_verification': 'requires_pi_review'}


class NarrationPreviewUseCase:
    def __init__(self,repository,metadata):
        self.repository,self.metadata=repository,metadata


    def preview(self, task, payload, selected_ref, command, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        if payload.context_stage not in {'narration', 'montage', 'review'}:
            raise edit_preview_failure(status_code=409, error='edit_scope_unavailable', reason='当前步骤不能修改配音片段。', impact='任务内容没有发生变化。', repair_action='进入配音、视频剪辑或检查成片后选择一段配音。', retryable=True)
        try:
            delivery_tempo = compile_narration_command(command)
        except ValueError as exc:
            raise edit_preview_failure(status_code=409, error='edit_intent_unsupported', reason=str(exc), impact='任务内容没有发生变化。', repair_action='输入“这句重新配音”或“这句读慢一点”。', retryable=True)
        segment_id = selected_ref.removeprefix('narration:')
        try:
            segment = self.repository.get_narration_segment(workspace_id, task.id, segment_id)
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task narration edit target not found') from exc
        prerender = montage.status != 'rendered'
        if prerender:
            try:
                if not task.confirmed_script_id or not task.current_timeline_version_id:
                    raise ValueError('当前任务缺少确认文案或草稿时间线')
                script = self.repository.get_creation_task_script(workspace_id, task.confirmed_script_id)
                current_version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
                snapshot_segments = self.metadata.narration_segments_from_snapshot(current_version.snapshot)
                snapshot_montage = creation_task_montage_plan_from_payload(current_version.snapshot['montage'])
                section = next((item for item in script.sections if item.id == segment.source_section_id))
                if segment.status != 'ready' or not segment.audio_reference or (not segment.audio_reference.startswith('narration://artifact/')):
                    raise ValueError('当前配音片段尚未生成有效音频')
                artifact = self.repository.get_narration_artifact(workspace_id, segment.audio_reference.removeprefix('narration://artifact/'))
                if script.task_id != task.id or script.selection_status != 'confirmed' or segment.script_id != script.id or (segment.text != section.content) or (snapshot_segments is not None and segment not in snapshot_segments) or (not segment.duration_seconds) or (segment.duration_seconds <= 0) or (artifact is None) or (artifact.task_id != task.id) or (artifact.script_id != script.id) or (current_version.task_id != task.id) or (snapshot_montage.task_id != task.id) or (snapshot_montage.script_id != script.id) or (montage.script_id != script.id) or (not any((cue.narration_segment_id == segment.id for cue in snapshot_montage.cues))) or (not any((cue.narration_segment_id == segment.id for cue in montage.cues))):
                    raise ValueError('所选配音与当前确认文案及草稿版本不一致')
                original_audio = self.repository.resolve_authorized_narration_audio_reference(workspace_id, segment.audio_reference)
                if not original_audio.is_file() or original_audio.stat().st_size <= 0:
                    raise ValueError('当前配音产物已不可用')
            except (KeyError, StopIteration, ValueError, MediaAuthorityError, OSError) as exc:
                raise edit_preview_failure(status_code=409, error='narration_version_required', reason=str(exc) or '当前草稿没有所选配音段。', impact='任务内容没有发生变化。', repair_action='先生成当前确认文案的配音并选择当前草稿中的真实配音段，再重新预览。', retryable=True) from exc
        invalidated_refs = [*([segment.audio_reference] if segment.audio_reference else []), *[f'subtitle-cue:{cue.id}' for cue in montage.cues if cue.narration_segment_id == segment.id], *logical_montage_output_refs(montage)]
        base_version = current_version if prerender else self.metadata.ensure_montage_base(workspace_id, task, montage, source=request_source)
        now = utc_now()
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, raw_command=command, context_stage=payload.context_stage, selected_object_refs=[selected_ref], playhead_ms=payload.playhead_ms, intent_type='regenerate_narration_segment', scope={'kind': 'narration_segment', 'segment_id': segment.id}, constraints={'delivery_instruction': command}, confidence=0.98, status='interpreted', created_at=now), source=request_source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, intent_id=intent.id, summary='只重新生成所选配音片段' + (f'，按 {delivery_tempo:g} 倍速处理' if delivery_tempo != 1.0 else '') + '，并同步它对应的字幕时间与镜头范围。', target_refs=[selected_ref], operation={'operation_type': 'RegenerateNarrationSegment', 'payload': {'narration_segment_id': segment.id, 'expected_audio_reference': segment.audio_reference, 'delivery_instruction': command, 'base_version_id': base_version.id}}, invalidated_refs=list(dict.fromkeys(invalidated_refs)), preview={'before': {'ref': selected_ref, 'label': '当前配音片段', 'detail': f'{segment.duration_seconds or 0:.1f} 秒 · {segment.text}', **({'audio_url': f'/api/creation-tasks/{task.id}/narration/segments/{segment.id}/audio'} if prerender else {})}, 'after': {'ref': selected_ref, 'label': '重新生成所选片段', 'detail': f'{command} · 预计约 {segment.duration_seconds / delivery_tempo:.1f} 秒，以生成后实测为准。' if prerender else command}}, external_effects=['确认后会调用一次真实声音模型；不会重做其他配音片段。', *(['当前版本尚未渲染，只比较配音与时间影响，没有视频对比。'] if prerender else [])], created_at=now, updated_at=now))
        return (intent, proposal)
