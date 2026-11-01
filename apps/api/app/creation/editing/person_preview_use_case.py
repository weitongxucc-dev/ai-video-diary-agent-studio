"""Original PersonPreviewUseCase command preview, separate from compilation and rendering."""
from __future__ import annotations

from app.creation_execution import presenter_input_facts

from app.creation.contracts import EditIntent
from app.creation.contracts import EditProposal
from app.avatar_lipsync import compile_person_edit_command
from app.montage import creation_task_montage_plan_from_payload
import re
from app.montage import select_presenter_sections
from app.models import utc_now
from uuid import uuid4
from app.creation.editing.metadata_use_case import EditPreviewRejected, edit_preview_failure, metadata_edit_command, effective_audio_timeline, logical_montage_output_refs


class PersonPreviewUseCase:
    def __init__(self,repository,metadata,latentsync_provider,avatar_lipsync_provider):
        self.repository,self.metadata=repository,metadata
        self.latentsync_provider,self.avatar_lipsync_provider=latentsync_provider,avatar_lipsync_provider


    def preview(self, task, payload, selected_ref, command, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        if payload.context_stage not in {'montage', 'review'}:
            raise edit_preview_failure(status_code=409, error='edit_scope_unavailable', reason='当前步骤还不能把人物口型片段写入成片。', impact='任务内容没有发生变化。', repair_action='进入视频剪辑或检查成片后选择一个镜头。', retryable=True)
        clip_id = selected_ref.removeprefix('clip:')
        try:
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
            clip = next((item for item in montage.clips if item.id == clip_id))
            cue = next((item for item in montage.cues if item.id == clip.cue_id))
            segment = self.repository.narration_records.get_narration_segment(workspace_id, task.id, cue.narration_segment_id)
            creation_plan = self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id)
            person_provider = self.provider(creation_plan.lipsync_quality)
            selected_person = creation_plan.selected_person
            if selected_person is None:
                raise ValueError('当前任务没有绑定人物声音版本')
            person = self.repository.get_person_profile(workspace_id, selected_person.profile_id, selected_person.version)
            current_render = self.repository.get_person_render(workspace_id, task.id, clip.person_render_id) if clip.person_render_id else None
            if current_render is not None:
                person = self.repository.get_person_profile(workspace_id, current_render.person_profile_id, current_render.person_version)
        except (KeyError, StopIteration) as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task person edit target not found') from exc
        except ValueError as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        if re.fullmatch('(?:表情|expression).*(?:自然|开心|微笑|natural|happy|smile)', command.casefold()):
            raise edit_preview_failure(status_code=409, error='person_expression_control_unavailable', reason='当前本地人物模型只能沿用原视频中的表情，不能单独改变表情。', impact='人物片段、时间线和成片保持不变。', repair_action='可以输入“这里只重做口型，其他都不要动”。', retryable=False)
        try:
            person_operation = compile_person_edit_command(command)
        except ValueError as exc:
            raise edit_preview_failure(status_code=409, error='edit_intent_unsupported', reason=str(exc), impact='人物片段、时间线和成片保持不变。', repair_action='请明确指定换成人物出镜或重做所选镜头口型。', retryable=True) from exc
        regenerate_requested = person_operation == 'RegeneratePersonRender'
        if regenerate_requested and current_render is None:
            raise edit_preview_failure(status_code=409, error='person_render_required', reason='所选镜头还不是人物口型片段，当前没有可以重做的口型。', impact='当前镜头和成片保持不变。', repair_action='先输入“这一段换成当前人物出镜”。', retryable=True)
        if not person_provider.ready:
            raise edit_preview_failure(status_code=503, error='capability_unavailable', reason=person_provider.message, impact='人物片段、时间线和成片保持不变。', repair_action='配置本地人物口型运行环境后重试。', retryable=True)
        if montage.status != 'rendered':
            raise edit_preview_failure(status_code=409, error='rendered_version_required', reason='当前任务还没有可比较的成片版本。', impact='无法准确展示人物出镜会替换哪一个镜头。', repair_action='先渲染当前成片，再选择镜头提出人物出镜要求。', retryable=True)
        if segment.status != 'ready' or not segment.audio_reference:
            raise edit_preview_failure(status_code=409, error='narration_segment_unavailable', reason='所选镜头没有可用于口型生成的已完成配音。', impact='当前镜头和成片保持不变。', repair_action='先完成对应配音分段，再重新提交人物出镜要求。', retryable=True)
        if segment.language not in person_provider.supported_languages:
            raise edit_preview_failure(status_code=503, error='language_unavailable', reason='当前本地人物模型未声明支持所选配音的语言。', impact='人物片段、时间线和成片保持不变。', repair_action='选择已声明支持该配音语言的本地人物模型。', retryable=False)
        try:
            person_source_start, person_source_end = self.source_range(workspace_id, task, montage, clip, segment)
        except ValueError as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        person_transforms = self.repository.lineage_records.list_transform_runs(workspace_id, task.id)
        existing_render = next((item for item in reversed(self.repository.list_person_renders(workspace_id, task.id)) if item.narration_segment_id == segment.id and item.person_profile_id == person.id and (item.person_version == person.version) and (item.voice_profile_id == segment.voice_profile_id) and (item.voice_version == segment.voice_version) and (item.model_id == person_provider.model_id) and (item.motion == 'natural') and (item.expression == 'neutral') and (item.status == 'ready') and self.render_matches_narration(item, segment, person_transforms) and (self.metadata.picture_renderer.person_path(item) is not None)), None)
        operation_type = 'RegeneratePersonRender' if regenerate_requested else 'ChangePersonSegment'
        intent_type = 'regenerate_person_render' if regenerate_requested else 'change_person_segment'
        base_version = self.metadata.ensure_montage_base(workspace_id, task, montage, source=request_source)
        now = utc_now()
        intent = self.repository.edit_records.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, raw_command=command, context_stage=payload.context_stage, selected_object_refs=[selected_ref], playhead_ms=payload.playhead_ms, intent_type=intent_type, scope={'kind': 'clip', 'clip_id': clip.id, 'narration_segment_id': segment.id}, constraints={'preserve_narration': True, 'preserve_subtitles': True, 'preserve_other_clips': True}, confidence=0.98, status='interpreted', created_at=now), source=request_source)
        proposal = self.repository.edit_records.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, intent_id=intent.id, summary='只重新生成所选人物片段的口型；配音、字幕和其他镜头保持不变。' if regenerate_requested else f'把所选镜头换成 {person.display_name} 的口型片段；配音、字幕和其他镜头保持不变。', target_refs=[selected_ref], operation={'operation_type': operation_type, 'payload': {'clip_id': clip.id, 'cue_id': cue.id, 'narration_segment_id': segment.id, 'expected_audio_reference': segment.audio_reference, 'source_start_seconds': person_source_start, 'source_end_seconds': person_source_end, 'person_profile_id': person.id, 'person_version': person.version, 'expected_asset_id': clip.asset_id, 'expected_segment_id': clip.segment_id, 'expected_person_render_id': clip.person_render_id, 'motion': 'natural', 'expression': 'neutral', 'lipsync_quality': creation_plan.lipsync_quality, 'base_version_id': base_version.id, **({'reuse_person_render_id': existing_render.id} if existing_render is not None and (not regenerate_requested) else {})}}, invalidated_refs=logical_montage_output_refs(montage), preview={'before': {'ref': selected_ref, 'label': '当前镜头', 'detail': f'{clip.timeline_start_seconds:.1f}–{clip.timeline_end_seconds:.1f} 秒'}, 'after': {'ref': f'person:{person.id}:{person.version}', 'label': f'重新生成 {person.display_name} 的口型' if regenerate_requested else f'{person.display_name} 出镜', 'detail': f'使用配音分段第 {person_source_start:.2f}–{person_source_end:.2f} 秒；沿用人物原视频动作与表情，配音和字幕不变。'}}, external_effects=['确认后会生成一个新人物片段；旧片段保留在历史版本中。' if regenerate_requested else '确认后复用已验证的人物口型片段，只使当前成片输出失效。' if existing_render is not None else '确认后调用一次真实人物口型模型；不会重做文案、配音、字幕或其他镜头。'], created_at=now, updated_at=now))
        return (intent, proposal)


    def preview_share(self, task, payload, target, *, identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        base = self.metadata.ensure_montage_base(workspace_id, task, plan, source=request_source)
        selected, actual = self.share_selection(plan, target)
        changed = self.share_changed_clips(plan, selected)
        if not changed and plan.presenter_share_target == target:
            raise EditPreviewRejected(status_code=409, detail='露脸比例与当前版本相同')
        if any((clip.locked for clip in changed)):
            raise EditPreviewRejected(status_code=409, detail='要调整的范围有锁定镜头，请先解锁')
        now = utc_now()
        intent = self.repository.edit_records.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, raw_command=payload.raw_command, context_stage=payload.context_stage, selected_object_refs=[], playhead_ms=None, intent_type='presenter_share', scope={'kind': 'whole_video'}, constraints={'preserve_narration': True}, confidence=1, status='interpreted', created_at=now), source=request_source)
        current = round(100 * sum((clip.timeline_end_seconds - clip.timeline_start_seconds for clip in plan.clips if clip.person_render_id)) / plan.duration_seconds, 1)
        proposal = self.repository.edit_records.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task.id, intent_id=intent.id, summary=f'露脸目标改为 {target}%，按当前段落预计实际 {actual}%；涉及 {len(changed)} 个镜头。', target_refs=[f'clip:{clip.id}' for clip in changed], operation={'operation_type': 'ChangePresenterShare', 'payload': {'target': target, 'base_version_id': base.id}}, invalidated_refs=logical_montage_output_refs(plan), preview={'before': {'ref': f'timeline:{base.id}', 'label': f'露脸 {current}%', 'detail': f'{len(plan.clips)} 个镜头'}, 'after': {'ref': f'target:{target}', 'label': f'目标 {target}% · 预计 {actual}%', 'detail': f'将调整 {len(changed)} 个镜头'}}, external_effects=['只更新露脸目标草稿，不自动运行人物口型；实际镜头不变，可撤销。'], created_at=now, updated_at=now))
        return (intent, proposal)


    def source_range(self, workspace_id, task, montage, clip, segment):
        from app.audio_timeline import narration_source_range
        if clip.locked:
            raise ValueError('所选镜头已锁定，请先解锁。')
        segments = self.repository.narration_records.list_narration_segments(workspace_id, task.id, segment.script_id)
        script = self.repository.script_records.get_creation_task_script(workspace_id, segment.script_id)
        by_section = {item.source_section_id: item for item in segments}
        offset = 0
        for section in script.sections:
            item = by_section.get(section.id)
            if item is None or not item.duration_seconds:
                raise ValueError('配音分段不完整，无法定位人物片段。')
            if item.id == segment.id:
                break
            offset += round(item.duration_seconds * 1000)
        else:
            raise ValueError('所选配音不属于当前文案。')
        refs = {montage.narration_audio_path: offset, segment.audio_reference: 0}
        return narration_source_range(effective_audio_timeline(montage), round(clip.timeline_start_seconds * 1000), round(clip.timeline_end_seconds * 1000), refs, round((segment.duration_seconds or 0) * 1000))


    def share_selection(self, plan, target):
        durations: dict[str, float] = {}
        for cue in plan.cues:
            durations[cue.source_section_id] = durations.get(cue.source_section_id, 0) + cue.duration_seconds
        selected = set(select_presenter_sections(list(durations.items()), target))
        actual = round(100 * sum((clip.timeline_end_seconds - clip.timeline_start_seconds for clip in plan.clips if next((cue for cue in plan.cues if cue.id == clip.cue_id)).source_section_id in selected)) / plan.duration_seconds, 1)
        return (selected, actual)


    def share_changed_clips(self, plan, selected):
        cues = {cue.id: cue for cue in plan.cues}
        new_render_sections = {cues[clip.cue_id].source_section_id for clip in plan.clips if not clip.person_render_id and cues[clip.cue_id].source_section_id in selected}
        return [clip for clip in plan.clips if bool(clip.person_render_id) != (cues[clip.cue_id].source_section_id in selected) or cues[clip.cue_id].source_section_id in new_render_sections]


    def provider(self, quality):
        return self.provider_for_quality(quality,self.latentsync_provider,self.avatar_lipsync_provider)


    def render_matches_narration(self, render: PersonRender, segment: NarrationSegment, transforms: list[TransformRun]) -> bool:
        from app.avatar_lipsync import _authorization_failure
        matching = [item for item in transforms if item.transform_type == 'person_render' and item.status == 'succeeded' and (item.input_digest == render.cache_key) and render.artifact_ref and (render.artifact_ref in item.output_refs) and segment.audio_reference and (segment.audio_reference in item.input_refs) and (item.parameters.get('language') == segment.language) and item.parameters.get('presenter_input_facts')]
        if not matching:
            return False
        try:
            person = self.repository.get_person_profile(render.workspace_id, render.person_profile_id, render.person_version)
            audio = self.repository.narration_artifacts.resolve_authorized_narration_audio_reference(render.workspace_id, segment.audio_reference)
            task = self.repository.task_records.get_creation_task(render.workspace_id, render.task_id)
            request = self.repository.profile_inputs.resolve_avatar_lipsync_request(workspace_id=render.workspace_id, person_id=person.id, person_version=person.version, voice_profile_id=segment.voice_profile_id, voice_version=segment.voice_version, audio_path=str(audio), audio_cache_key=segment.audio_reference, language=segment.language, motion=render.motion, expression=render.expression, authorization_id=person.authorization.id if person.authorization else None, authorization_version=person.authorization.version if person.authorization else None, model_id=render.model_id, viewer_user_id=task.owner_user_id)
            if _authorization_failure(request) is not None:
                return False
            input_facts = presenter_input_facts(request)
        except (KeyError, ValueError, OSError, PermissionError):
            return False
        return any((item.parameters['presenter_input_facts'] == input_facts for item in matching))


    @staticmethod
    def provider_for_quality(quality,latentsync_provider,avatar_lipsync_provider):
        return latentsync_provider if quality == "high" else avatar_lipsync_provider
