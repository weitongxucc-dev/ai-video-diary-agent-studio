"""One command application: ordered compilation, source binding and explicit media work."""
from __future__ import annotations

from app.audio_timeline import AudioEditRequest
from collections import Counter
from app.creation.editing.picture import EditConflict
from app.production import TimelineVersionConflict
from app.audio_timeline import apply_audio_edit
from app.task_audio import audio_mix_resources
from app.audio_timeline import compile_audio_command
from app.compound_edits import compile_compound_edit
from app.graphic_overlays import compile_overlay_command
from app.subtitle_style import compile_subtitle_style_command
from app.visual_effects import compile_visual_command
from app.montage import creation_task_montage_plan_from_payload
from app.montage import creation_task_montage_plan_to_payload
from app.graphic_sources import graphic_sources
from app.compound_edits import is_indexed_subtitle_command
from app.task_audio import media_digest
import re
import subprocess
from dataclasses import asdict
from app.creation.editing.command_compile import (AudioCommandPreview,CommandParseRejected,indexed_reorder_command,canvas_request,display_request,selected_reference,clip_request)
from app.creation.editing.metadata_use_case import EditPreviewRejected,edit_preview_failure,effective_audio_timeline,metadata_edit_command
from app.creation.editing.compound_use_case import CompoundEditUseCase,CompoundPreviewCommand
from app.creation.clip_preview import ClipPreviewCommand,needs_full_visual_preview
from app.schemas import CreationTaskExactEditRequest,CreationTaskEditPreviewRequest,CreationTaskEditPreviewResponse
from app.visual_effects import VisualEditRequest

class CommandPreviewUseCase:
    def __init__(self,repository,conversations,metadata,subtitles,linked,compound,exact,audio,visual,narration,person,*,budgets_for):
        self.budgets_for=budgets_for
        self.repository,self.conversations=repository,conversations
        self.metadata,self.subtitles,self.linked,self.compound=metadata,subtitles,linked,compound
        self.exact,self.audio,self.visual,self.narration,self.person=exact,audio,visual,narration,person

    def resolve_action(self,task,action,*,identity,reader_identity,request_source,draft_only,execution,actor):
        if isinstance(action,CreationTaskExactEditRequest):
            return self.exact_action(task,action,identity=identity,reader_identity=reader_identity,request_source=request_source,draft_only=draft_only,execution=execution,actor=actor)
        if isinstance(action,ClipPreviewCommand) and draft_only:
            return self.exact.clip_command(task,action.base_version_id,action.operation_type,action.clip_ids,action.values,action.raw_command,
                identity=identity,source=request_source,draft_only=True)
        return action

    def exact_action(self,task,payload,*,identity,reader_identity,request_source,draft_only,execution,actor):
        return self.exact.preview(task.workspace_id,task.id,payload,identity=identity,reader_identity=reader_identity,
            actor=actor,source=request_source,draft_only=draft_only,execution=execution)

    def preview_visual(self,task,payload,*,identity,reader_identity,request_source,draft_only,execution,actor):
        prepared=self.visual.prepare(task,payload)
        plan,patch,clips,changed=prepared
        if not needs_full_visual_preview(clips[0],changed[clips[0].id]):
            return self.exact.clip_command(task,payload.base_version_id,'AdjustVisualEffects',payload.clip_ids,patch,payload.raw_command,
                identity=identity,source=request_source,draft_only=draft_only)
        return self.visual.preview_full(task,payload,prepared,draft_only=draft_only,source=request_source,execution=execution)

    def preview_compound(self,task,payload,*,identity,request_source,draft_only,execution,actor):
        if not self.compound.applicable(payload.raw_command):return None
        control=CompoundPreviewCommand(task.workspace_id,task.id,identity,task.current_timeline_version_id,payload.raw_command,
            payload.context_stage,payload.selected_object_refs,payload.playhead_ms,source=request_source)
        try:return self.compound.preview(self.compound.prepare_preview(control),draft_only=draft_only,execution=execution)
        except PermissionError as exc:raise EditPreviewRejected(status_code=403,detail=str(exc)) from exc
        except (EditConflict,TimelineVersionConflict) as exc:raise EditPreviewRejected(status_code=409,detail=str(exc)) from exc
        except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
            raise EditPreviewRejected(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else '复合修改预览失败，请检查来源后重试') from exc

    def preview_audio(self,task,action,actor,*,request_source,draft_only,execution):
        options={} if execution is None else dict(zip(('context','job','event'),execution[:3]))
        if action.original_clip:
            result=self.audio.original_clip(task.workspace_id,task.id,action.request.model_dump(mode='json'),actor,draft_only=draft_only,source=request_source,**options)
        else:result=self.audio.preview(task.workspace_id,task.id,action.request,actor,draft_only=draft_only,source=request_source,**options)
        return result.intent,result.proposal

    def execute(self,job,event,actor,context):
        payload=CreationTaskEditPreviewRequest.model_validate(event['arguments']['payload'])
        task=self.metadata.task_or_missing(job.workspace_id,job.task_id)
        if payload.assistant_turn_id is not None:
            payload=CreationTaskEditPreviewRequest.model_validate(payload.model_dump(mode='json',exclude_unset=True,exclude={'assistant_turn_id'}))
        identity={'member_id':actor.user_id or actor.member_id,'local_diagnostic':False}
        source=event['arguments'].get('_typed_source')
        if source is not None:source={**source,'media_job_id':job.id,'actor_identity':actor.identity_payload()}
        outcome=self.preview(task,payload,base_version_id=event['input_facts']['base_version_id'],identity=identity,
            reader_identity={'member_id':actor.user_id or actor.member_id,'local_diagnostic':False},request_source=source,
            draft_only=event['arguments'].get('_draft',False),execution=(context,job,event,actor),actor=actor)
        if isinstance(outcome,AudioCommandPreview):outcome=self.preview_audio(task,outcome,actor,request_source=source,draft_only=event['arguments'].get('_draft',False),execution=(context,job,event,actor))
        if outcome is None:return None
        intent,proposal=outcome
        return CreationTaskEditPreviewResponse.model_validate({'intent':asdict(intent),'proposal':asdict(proposal)}).model_dump(mode='json')

    def validate_result(self,job,event,result,context):
        return self.metadata.audio_service.validate_publications(job,event,result,context)


    def preview(self, task, payload, *, base_version_id, identity, reader_identity, request_source, draft_only, execution, actor):
        workspace_id, task_id = (task.workspace_id, task.id)
        short_preview_base_version = base_version_id
        command = payload.raw_command.strip()
        parsed_overlay = None
        if '/' in command or '\\' in command:
            try:
                overlay_plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
                parsed_overlay = compile_overlay_command(command, overlay_plan.graphic_overlays, (), payload.selected_object_refs)
            except (KeyError, ValueError):
                parsed_overlay = None
            if not (parsed_overlay and isinstance(parsed_overlay[1], dict) and (parsed_overlay[1].get('kind') in {'text', 'arrow'})):
                raise edit_preview_failure(status_code=409, error='edit_path_input_forbidden', reason='修改要求中不能指定本机或网络文件路径。', impact='系统没有读取该路径，任务内容保持不变。', repair_action='先将内容导入当前工作区，再选中具体对象提出修改要求。', retryable=True)
        if payload.context_stage in {'montage', 'review'}:
            reorder = indexed_reorder_command(command)
            if reorder is not None:
                if payload.replacement_asset_id is not None:
                    raise EditPreviewRejected(status_code=422, detail='镜头排序不能混入替换素材。')
                return self.metadata.preview_reorder(task, payload, reorder, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if payload.replacement_asset_id is not None:
            return self.metadata.preview_replacement(task, payload, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if payload.context_stage in {'montage', 'review'} and len(payload.selected_object_refs) == 1 and payload.selected_object_refs[0].startswith('clip:') and re.fullmatch('把当前选中的第[1-9]\\d*个镜头改为完整显示。保持镜头顺序、时长、配音和字幕不变，生成修改提案[。！]?', command):
            return self.metadata.selected_crop_command(workspace_id, task_id, payload, base_version_id=short_preview_base_version, request_source=request_source)
        share_command = re.fullmatch('(?:把)?(?:这条视频的?|整片的?)?(?:露脸|口播出镜)(?:占比|比例)?(?:改成|改为|调到|设为)\\s*(0|20|40|60|80|100)\\s*%[。！]?', command)
        if share_command and payload.context_stage in {'montage', 'review'}:
            return self.person.preview_share(task, payload, int(share_command.group(1)), identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        alignment_command = re.fullmatch('(?:按配音|根据配音)(?:校准|对齐)(中文|英文|双语|中英双语|中英)?字幕[。！]?', command)
        if alignment_command and payload.context_stage in {'montage', 'review', 'subtitle_cover'}:
            return self.subtitles.preview_alignment(task, payload, alignment_command[1])
        if payload.context_stage in {'montage', 'review'} and re.fullmatch('(?:提取|添加|保留)(?:(?:当前|这个|所选)镜头的?)?原声[。！]?', command):
            return AudioCommandPreview(payload, True)
        if payload.context_stage in {'montage', 'review'} and re.fullmatch('(?:剪掉|删掉|删除)(?:当前镜头(?:的)?)?(?:停顿|静音)[。！]?', command):
            return self.linked.preview_remove_silence(task, payload, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if payload.context_stage in {'montage', 'review'} or (payload.context_stage == 'subtitle_cover' and is_indexed_subtitle_command(command)):
            combined = self.preview_compound(task, payload, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
            if combined is not None:
                return combined
        action = canvas_request(payload, command)
        if action is not None:
            return self.exact_action(task, action, identity=identity, reader_identity=reader_identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        action = display_request(payload, command)
        if action is not None:
            return self.exact_action(task, action, identity=identity, reader_identity=reader_identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if payload.context_stage in {'montage', 'review', 'subtitle_cover'}:
            try:
                style_patch = compile_subtitle_style_command(command)
            except ValueError as exc:
                raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc
            if style_patch is not None:
                return self.exact_action(task, CreationTaskExactEditRequest(operation_type='ConfigureSubtitleTrack', style=style_patch), identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor, reader_identity=reader_identity)
        if payload.context_stage in {'montage', 'review'} and any((word in command for word in ('标题', '图片', '标识', '箭头'))):
            try:
                overlay_plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
                edit = parsed_overlay or compile_overlay_command(command, overlay_plan.graphic_overlays, graphic_sources(self.repository, workspace_id, task_id) if any((word in command for word in ('图片', '标识'))) else (), payload.selected_object_refs)
            except (ValueError, KeyError) as exc:
                raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc
            if edit:
                action, value, overlay_identity = edit
                return self.exact_action(task, CreationTaskExactEditRequest(operation_type='EditGraphicOverlay', overlay_action=action, graphic_overlay=value, overlay_id=overlay_identity), identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor, reader_identity=reader_identity)
        selected_ref = selected_reference(payload)
        if selected_ref.startswith('clip:'):
            transition = re.fullmatch('(?:(?:当前|这个)?镜头)?(?:与前一个镜头)?(?:添加|改为|设置)?叠化(?:转场)?\\s*(\\d+(?:\\.\\d+)?)\\s*秒[。！]?', command)
            remove_transition = command.rstrip('。！') in {'取消叠化', '改为硬切'}
            if transition or remove_transition:
                return self.exact_action(task, CreationTaskExactEditRequest(operation_type='SetClipTransition', clip_id=selected_ref.removeprefix('clip:'), transition_seconds=0 if remove_transition else float(transition[1])), identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor, reader_identity=reader_identity)
            crop_preview = self.metadata.selected_crop_command(workspace_id, task_id, payload, base_version_id=short_preview_base_version, request_source=request_source)
            if crop_preview is not None:
                return self.resolve_action(task, crop_preview, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor, reader_identity=reader_identity)
            try:
                visual_plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
                selected_clip = next((clip for clip in visual_plan.clips if clip.id == selected_ref.removeprefix('clip:')))
                effects = compile_visual_command(command, selected_clip.visual_effects)
            except (ValueError, KeyError, StopIteration) as exc:
                raise EditPreviewRejected(status_code=422, detail=str(exc) or '当前镜头不可用') from exc
            if effects is not None:
                return self.preview_visual(task, VisualEditRequest(base_version_id=short_preview_base_version, clip_ids=[selected_clip.id], effects=effects, raw_command=command), identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor, reader_identity=reader_identity)
        if selected_ref.startswith('audio:'):
            try:
                montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
                audio_edit = compile_audio_command(command, selected_ref.removeprefix('audio:'), effective_audio_timeline(montage), task.current_timeline_version_id, payload.playhead_ms)
            except (KeyError, ValueError) as exc:
                raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc
            return AudioCommandPreview(audio_edit)
        if selected_ref.startswith('narration:'):
            return self.narration.preview(task, payload, selected_ref, command, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if selected_ref.startswith('cover:'):
            action = self.metadata.command_cover(task, payload, selected_ref, command, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
            return self.resolve_action(task, action, identity=identity, reader_identity=reader_identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if selected_ref.startswith('clip:') and payload.context_stage in {'montage', 'review', 'subtitle_cover'}:
            action = clip_request(task_id, payload, selected_ref, command, base_version_id, request_source)
            if action is not None:
                return self.resolve_action(task, action, identity=identity, reader_identity=reader_identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        action = self.subtitles.command_batch_or_structure(task, payload, selected_ref, command, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if action is not None:
            return self.resolve_action(task, action, identity=identity, reader_identity=reader_identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if selected_ref.startswith('cue:'):
            action = self.subtitles.command_cue(task, payload, selected_ref, command, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
            return self.resolve_action(task, action, identity=identity, reader_identity=reader_identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        if selected_ref.startswith('clip:') and any((token in command.casefold() for token in ('出镜', '口型', '人物', '表情', 'avatar', 'lip sync', 'lipsync', 'expression'))):
            return self.person.preview(task, payload, selected_ref, command, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)
        return self.metadata.preview_replacement(task, payload, identity=identity, request_source=request_source, draft_only=draft_only, execution=execution, actor=actor)


    def bind_after_access(self, task, payload, turn, workspace_id, member_id):
        context = self.conversations.consumed_turn_request_context(turn['id'], turn['conversation_id'], workspace_id, member_id)
        if turn['status'] != 'running' or context.get('kind') != 'task' or context.get('task_id') != task.id or (context.get('stage') != payload.context_stage) or (context.get('selected_object_refs') != sorted(payload.selected_object_refs)) or (context.get('playhead_ms') != payload.playhead_ms):
            raise EditPreviewRejected(status_code=409, detail='修改目标与本轮原始要求不一致，未生成提案。')
        snapshot = context.get('target_snapshot', {})
        version = self.repository.timeline_records.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if snapshot.get('timeline_version_id') != task.current_timeline_version_id or snapshot.get('timeline_sequence') != (version.sequence if version else 0):
            raise EditPreviewRejected(status_code=409, detail='任务版本已变化，请重新查看目标后发送；未生成提案。')
        try:
            original = self.conversations.task_turn_source(turn['id'], workspace_id, member_id, task.id)['raw_command'].strip()
        except ValueError as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        if not 2 <= len(original) <= 500:
            raise EditPreviewRejected(status_code=422, detail='原始修改要求需为2至500字，请缩短后重新发送；未生成提案。')
        return payload.model_copy(update={'raw_command': original})


    def facts(self, workspace_id, task_id, arguments, actor):
        operation = 'command_edit_preview'
        from app.canvas import canvas_size
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        if arguments.get('_media_kind') == 'clip_preview':
            raise ValueError('历史短预览没有独立的封存执行输入，请查询原记录并为当前版本重新预览')
        source = arguments.get('_typed_source')
        if source is not None:
            version = self.repository.timeline_records.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
            if (source['workspace_id'], source['task_id'], source['member_id'], source['expected_timeline_version_id'], source['expected_timeline_sequence']) != (workspace_id, task_id, actor.user_id or actor.member_id, task.current_timeline_version_id, version.sequence if version else 0):
                raise ValueError('封存的类型化编辑输入或成员已失效')
        plan_payload = self.repository.get_creation_task_montage_plan(workspace_id, task_id) if task.montage_plan_id else None
        plan = creation_task_montage_plan_from_payload(plan_payload) if plan_payload else None
        transient = {'status', 'message', 'output_path', 'subtitle_path', 'updated_at'}
        snapshot = {key: value for key, value in creation_task_montage_plan_to_payload(plan).items() if key not in transient} if plan else None
        payload = arguments.get('payload', {})
        picture_candidates = [plan] if plan is not None else []
        if plan is not None and arguments.get('_media_kind') == 'picture_preview':
            command_payload, included = (payload, None)
            compiled = compile_compound_edit(command_payload['raw_command'], plan, effective_audio_timeline(plan), command_payload.get('selected_object_refs', []), task.current_timeline_version_id, command_payload.get('playhead_ms'), included)
            if compiled is not None:
                picture_candidates.append(compiled[0])
        expected = payload.get('base_version_id')
        if 'base_version_id' in payload and expected != task.current_timeline_version_id:
            raise ValueError('时间线已变化，请重新安排当前要求')
        visual_scope = None
        audio_operations = {'audio_extract', 'audio_edit_preview'}
        if arguments.get('_media_kind') in {'audio_mix', 'audio_probe', 'audio_stretch'} and (not re.fullmatch('(?:剪掉|删掉|删除)(?:当前镜头(?:的)?)?(?:停顿|静音)[。！]?', payload.get('raw_command', '').strip())):
            audio_operations.add('command_edit_preview')
        files, audio_inputs, audio_uses, graphics = ([], [], {}, [])
        audio_graphs = []
        service = self.metadata.audio_service

        def remember(reference, path, *, audio=False, metadata=None):
            stat = path.stat()
            fact = [reference, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            if fact not in files:
                files.append(fact)
            if audio and (not any((item['reference'] == reference for item in audio_inputs))):
                values = metadata if metadata is not None else service.source_metadata(workspace_id, task_id, reference)
                audio_inputs.append({'reference': reference, 'path': str(path.resolve()), 'size_bytes': stat.st_size, 'uses': audio_uses.get(reference, 1), **values})
        if plan is not None:
            if 'command_edit_preview' not in audio_operations:
                picture_files, graphics = self.metadata.picture_renderer.qualified_picture_sources(workspace_id, task, plan, picture_candidates, actor)
                files.extend(picture_files)
            graph = effective_audio_timeline(plan)
            audio_graphs.append(graph)
            solo = set(payload.get('solo_track_ids', []))
            clips = [clip for track in graph.tracks if not track.muted and (not solo or track.id in solo) for clip in track.clips if not clip.muted]
            if arguments.get('_media_kind') == 'audio_stretch':
                selected = set(payload.get('selected_object_refs', []))
                for video_clip in plan.clips:
                    if f'clip:{video_clip.id}' in selected:
                        reference = self.repository.workspace_media_reference_for_asset(workspace_id, video_clip.asset_id)
                        remember(reference, self.repository.resolve_clip_media_reference(workspace_id, video_clip.asset_id, video_clip.source_path), audio=True, metadata=service.source_metadata(workspace_id, task_id, reference))
            references = {clip.source_ref for clip in clips}
            audio_uses.update(Counter((clip.source_ref for clip in clips)))
            for reference in sorted(references):
                remember(reference, service.source(workspace_id, task_id, reference, actor=actor), audio=True)
        facts = {'base_version_id': task.current_timeline_version_id, 'script_id': task.confirmed_script_id, 'script_digest': self.repository.script_records.confirmed_creation_task_script_digest(workspace_id, task_id, task.confirmed_script_id) if task.confirmed_script_id else None, 'plan_id': task.plan_id, 'plan_digest': media_digest(asdict(self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id))), 'montage': snapshot, 'files': files, 'narration_cache_key': task.narration_cache_key}
        if visual_scope is not None:
            facts['visual_preview'] = visual_scope
        if graphics:
            facts['graphic_sources'] = graphics
        budgets = self.budgets_for()
        if 'command_edit_preview' in audio_operations:
            budget = budgets.cpu_audio
            if budget is None:
                raise ValueError('CPU 音频处理预算尚未标定，未启动媒体')
            if not audio_inputs:
                raise ValueError('没有固定的音频输入，未启动媒体')
            for source in audio_inputs:
                for field, cap in (('duration_seconds', budget.max_source_duration_seconds), ('sample_rate', budget.max_sample_rate), ('channels', budget.max_channels)):
                    value = source[field]
                    if value is not None and (not isinstance(value, (int, float)) or value <= 0 or value > cap):
                        raise ValueError('已保存的音频源规格超出标定预算')
            known_duration = max((item['duration_seconds'] or 0 for item in audio_inputs), default=0)
            if plan is None:
                raise ValueError('当前任务没有可试听的音轨')
            start = payload.get('start_seconds', 0)
            end = payload.get('end_seconds', plan.duration_seconds)
            if not 0 <= start < end <= plan.duration_seconds:
                raise ValueError('试听范围必须位于时间线内')
            output_duration = end - start
            resources = {'kind': 'task_audio', 'execution_mode': 'cpu_ffmpeg', 'input_policy': 'probe_then_bounded_decode', 'inputs': audio_inputs, 'input_count': sum((item['uses'] for item in audio_inputs)), 'input_bytes': sum((item['size_bytes'] * item['uses'] for item in audio_inputs)), 'source_duration_limit_seconds': budget.max_source_duration_seconds, 'output_duration_limit_seconds': output_duration, 'source_sample_rate_limit': budget.max_sample_rate, 'source_channels_limit': budget.max_channels, 'output_sample_rate_limit': 48000, 'output_channels_limit': 2, 'max_output_bytes': budget.max_output_bytes, 'threads': budget.max_threads, 'timeout_seconds': budget.max_timeout_seconds}
            if audio_graphs and arguments.get('_media_kind') == 'audio_mix':
                mix = audio_mix_resources(audio_inputs, audio_graphs, output_duration, normalize_voice_sources=plan.narration_linkage_created_audio_timeline, budget=budget)
                resources.update({key: mix[key] for key in ('normalize_voice_sources', 'normalized_source_outputs', 'mix_output_bytes', 'temporary_output_bytes', 'output_duration_limit_seconds')})
            if resources['input_count'] > budget.max_input_count or resources['input_bytes'] > budget.max_input_bytes or output_duration > budget.max_output_duration_seconds or (resources['output_sample_rate_limit'] > budget.max_sample_rate):
                raise ValueError('当前音频处理输入或输出超出标定预算')
        else:
            if plan is None:
                raise ValueError('当前任务没有可预览的故事板')
            budget = budgets.cpu_render
            if budget is None:
                raise ValueError('CPU 画面预览预算尚未标定，未启动媒体')
            if any((clip.person_render_id for clip in plan.clips)):
                person_budget = budgets.presenter_cpu_render
                if person_budget is None:
                    raise ValueError('人物组合 CPU 预览预算尚未标定，未启动媒体')
                budget = person_budget
            renderer = self.metadata.picture_renderer
            resources = renderer.picture_resources(plan, payload, picture_candidates, facts, budget)
            audio_budget = budgets.cpu_audio
            facts, resources = renderer.seal_audio_resources(task, picture_candidates, facts, resources, audio_budget, actor=actor)
            if re.fullmatch('(?:剪掉|删掉|删除)(?:当前镜头(?:的)?)?(?:停顿|静音)[。！]?', payload.get('raw_command', '').strip()):
                selected = payload.get('selected_object_refs', [])
                if len(selected) != 1 or not selected[0].startswith('clip:'):
                    raise ValueError('静音检测必须绑定一个实际镜头')
                clip = next((item for item in plan.clips if f'clip:{item.id}' == selected[0]), None)
                if clip is None or clip.locked:
                    raise ValueError('静音检测镜头不存在或已锁定')
                resources['silence_analysis_scope'] = {'base_version_id': task.current_timeline_version_id, 'clip_id': clip.id}
                resources['dynamic_clip_count_limit'] = budget.max_clip_count
                resources['dynamic_overlay_count_limit'] = budget.max_overlay_count
                resources['audio_mix']['dynamic_input_count_limit'] = audio_budget.max_input_count
                resources['audio_mix']['dynamic_input_bytes_limit'] = audio_budget.max_input_bytes
            if '校准' in payload.get('raw_command', ''):
                raise ValueError('本地字幕识别尚无独立标定的模型资源许可，未启动识别')
        return (facts, resources)
