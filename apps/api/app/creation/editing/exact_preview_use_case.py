"""The finite exact-edit preview application, with explicit source and execution."""
from collections import Counter
from dataclasses import asdict, replace
from uuid import uuid4

from app.audio_timeline import AudioEditRequest, apply_audio_edit
from app.creation.clip_preview import ClipPreviewCommand, ClipPreviewConflict, needs_full_visual_preview
from app.creation.contracts import EditIntent, EditProposal
from app.creation.editing.clips import CLIP_SETTING_OPERATIONS
from app.creation.editing.metadata_use_case import (EditPreviewRejected, edit_preview_failure,
    metadata_edit_command, effective_audio_timeline, logical_montage_output_refs)
from app.creation.editing.subtitle_use_case import subtitle_values
from app.creation.editing.picture import EditConflict
from app.creation.editing.linked_timeline_use_case import LinkedEditInputError, InsertMaterialMissing, InsertSourceRangeError
from app.media.clip_preview import ClipPreviewExecutor
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.models import utc_now
from app.production import TimelineVersionConflict
from app.schemas import CreationTaskExactEditRequest, CreationTaskEditPreviewResponse, EXACT_EDIT_ENVELOPE_FIELDS
from app.task_audio import media_digest
from app.visual_effects import VisualEditRequest
import subprocess


class ExactPreviewUseCase:
    def __init__(self, repository, metadata, subtitles, linked, audio_edit, visual, clip_preview, conversations, settings):
        self.repository, self.metadata, self.subtitles, self.linked = repository, metadata, subtitles, linked
        self.audio_edit, self.visual, self.clip_preview = audio_edit, visual, clip_preview
        self.conversations, self.settings = conversations, settings

    def claim_request(self, task, member_id, request_id, phase, inputs):
        try:
            return self.repository.claim_edit_request(task, member_id, request_id, phase, inputs)
        except ValueError as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc

    def read_request_result(self, task, event_id):
        return self.repository.read_edit_request_result(task, event_id)

    def record_request_result(self, task, event_id, *, status_code, response):
        return self.repository.save_edit_request_result(task, event_id, status_code=status_code, response=response)

    def typed_source(self, task, payload, member_id):
        version = self.repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if not {'expected_timeline_version_id', 'expected_timeline_sequence'} <= payload.model_fields_set:
            raise EditPreviewRejected(status_code=422, detail='类型化请求须绑定期望时间线版本和序号')
        if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise EditPreviewRejected(status_code=409, detail='时间线已变化，请重新读取当前任务')
        source = {'workspace_id': task.workspace_id, 'task_id': task.id, 'member_id': member_id, 'request_id': str(payload.request_id) if payload.request_id else None, 'expected_timeline_version_id': payload.expected_timeline_version_id, 'expected_timeline_sequence': payload.expected_timeline_sequence}
        return source, version
    def complete_typed(self, task, payload, member_id, actor, source, version):
        if actor is not None:
            source['actor_identity'] = actor.identity_payload()
        if payload.assistant_turn_id:
            try:
                origin = self.conversations.task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task.id)
            except ValueError as exc:
                raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
            snapshot = origin['context'].get('target_snapshot', {})
            if (snapshot.get('timeline_version_id'), snapshot.get('timeline_sequence')) != (task.current_timeline_version_id, version.sequence if version else 0):
                raise EditPreviewRejected(status_code=409, detail='助手轮次的目标版本已失效')
            source.update(assistant_turn_id=str(payload.assistant_turn_id), conversation_id=origin['conversation_id'], raw_command=origin['raw_command'],
                          **{key: origin[key] for key in ('input_id', 'input_turn_id') if key in origin})
        clean = CreationTaskExactEditRequest.model_validate(payload.model_dump(mode='json', exclude_unset=True, exclude=EXACT_EDIT_ENVELOPE_FIELDS))
        source['action'] = clean.model_dump(mode='json', exclude_unset=True)
        return (clean, source)

    def source_facts(self, task, payload, identity, actor):
        if payload.operation_type == 'EditAudioTimeline':
            graph = self.audio_source_graph(task, payload)
            return self.audio_source_facts(task, graph, actor)
        ids = payload.clip_ids if payload.operation_type == 'AdjustVisualEffects' else [payload.clip_id] if payload.clip_id else []
        if not ids:
            return []
        replacement = (payload.asset_id, payload.segment_id) if payload.asset_id is not None and payload.segment_id is not None else None
        try:
            return self.repository.clip_edit_source_facts(task.workspace_id, task.id, clip_ids=ids, replacement=replacement, user_id=identity['member_id'], local_diagnostic=identity['local_diagnostic'])
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, OSError) as exc:
            status = 404 if isinstance(exc, KeyError) and exc.args[0] == '当前任务镜头不存在' else 409
            detail = '目标镜头不存在，请重新读取当前任务后选择实际镜头。' if status == 404 else '镜头来源或使用状态已失效'
            raise EditPreviewRejected(status_code=status, detail=detail) from exc
    def audio_source_graph(self, task, payload):
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id, task.id))
        graph = effective_audio_timeline(montage)
        edit = self.audio_request(payload, task.current_timeline_version_id)
        try:
            apply_audio_edit(graph, edit, round(montage.duration_seconds * 1000))
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, OSError) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc
        return graph
    def audio_source_facts(self, task, graph, actor):
        try:
            facts = []
            for reference in sorted({clip.source_ref for track in graph.tracks for clip in track.clips}):
                stat = self.metadata.audio_service.source(task.workspace_id, task.id, reference, actor=actor).stat()
                facts.append({'reference': reference, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns, 'inode': stat.st_ino, 'device': stat.st_dev})
            return facts
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, OSError) as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc

    @staticmethod
    def audio_request(payload, base_version_id):
        return AudioEditRequest(base_version_id=base_version_id, action=payload.audio_edit.action,
            target_id=payload.audio_edit.target_id, values=payload.audio_edit.values.model_dump(mode='json', exclude_unset=True))

    def preview(self, workspace_id, task_id, payload, *, identity, actor, source, draft_only, execution, reader_identity):
        operation = payload.operation_type
        if operation in {'EditAudioTimeline', 'AdjustVisualEffects'}:
            task = self.metadata.task_or_missing(workspace_id, task_id)
            if operation == 'EditAudioTimeline':
                options = {} if execution is None else dict(zip(('context', 'job', 'event'), execution[:3]))
                outcome = self.audio_edit.preview(workspace_id, task_id, self.audio_request(payload, task.current_timeline_version_id), actor, draft_only=draft_only, source=source, **options)
                return (outcome.intent, outcome.proposal)
            visual_payload = VisualEditRequest(base_version_id=source['expected_timeline_version_id'] if source is not None else task.current_timeline_version_id, clip_ids=payload.clip_ids, effects=payload.effects)
            prepared = self.visual.prepare(task, visual_payload)
            plan, patch, clips, changed_clips = prepared
            if not needs_full_visual_preview(clips[0], changed_clips[clips[0].id]):
                return self.clip_command(task, visual_payload.base_version_id, 'AdjustVisualEffects', payload.clip_ids, patch, visual_payload.raw_command, identity=identity, source=source, draft_only=draft_only)
            return self.visual.preview_full(task, visual_payload, prepared, draft_only=draft_only, source=source, execution=execution)
        if operation == 'EditGraphicOverlay':
            return self.metadata.preview_graphic_overlay(workspace_id, task_id, payload, identity=identity, source=source, draft_only=draft_only, execution=execution)
        if operation == 'SetClipTransition':
            return self.metadata.preview_transition(workspace_id, task_id, payload, identity=identity, source=source, draft_only=draft_only, execution=execution)
        if operation == 'ChangeCanvas':
            return self.metadata.preview_canvas(workspace_id, task_id, payload, identity=identity, source=source, draft_only=draft_only, execution=execution)
        if operation == 'ConfigureSubtitleTrack' and payload.style is not None and (payload.target_language is None) or operation in {'ReplaceSubtitleText', 'SplitSubtitleCue', 'MergeSubtitleCue', 'DeleteSubtitleCue'} or (operation == 'UpdateSubtitleCue' and payload.track_id is not None):
            return self.subtitles.preview(workspace_id, task_id, payload, identity=identity, source=source, draft_only=draft_only, execution=execution)
        if operation in {'ChangeLinkedSpeed', 'DeleteClip', 'DuplicateClip', 'InsertClip'}:
            task, values = self.prepare_linked(workspace_id, task_id, payload)
            command = self.linked_command(task, identity, operation, {'base_version_id': task.current_timeline_version_id, **values}, source=source, reader_identity=reader_identity)
            return self.preview_linked(command, payload)
        if operation in CLIP_SETTING_OPERATIONS:
            task = self.metadata.task_or_missing(workspace_id, task_id)
            base_version_id = source['expected_timeline_version_id'] if source is not None else task.current_timeline_version_id
            fields = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if key not in {'operation_type', 'clip_id'} and value is not None}
            if operation in {'SetClipLock', 'ConfirmClip'}:
                return self.metadata.preview_metadata_settings(task, operation, {'clip_id': payload.clip_id, **fields}, base_version_id, identity=identity, source=source)
            return self.clip_command(task, base_version_id, operation, [payload.clip_id], fields, '精确操作：' + operation, identity=identity, source=source, draft_only=draft_only)
        if operation in {'ReplaceClip', 'TrimClip', 'SplitClip', 'ReorderClip'}:
            _task, _base, _plan, intent, proposal = self.metadata.preview_exact_montage(workspace_id, task_id, payload, identity=identity, source=source, draft_only=draft_only, execution=execution)
            return (intent, proposal)
        return self.preview_legacy(workspace_id, task_id, payload, source=source)

    def clip_command(self, task, base_version_id, operation, clip_ids, values, raw_command, *, identity, source, draft_only):
        if draft_only:
            command_values = {'clip_ids': clip_ids, 'effects': values} if operation == 'AdjustVisualEffects' else {'clip_id': clip_ids[0], **values}
            return self.metadata.preview_metadata_settings(task, operation, command_values, base_version_id, identity=identity, source=source)
        return ClipPreviewCommand(task.id, base_version_id, operation, clip_ids, values, raw_command, source)

    def prepare_clip_submission(self, actor, command, *, original_command=None):
        workspace_id, task_id = (actor.workspace_id, command.task_id)
        task = self.metadata.task_or_missing(workspace_id, task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise EditPreviewRejected(status_code=403, detail='当前成员不能修改此任务')
        if command.base_version_id is None:
            raise ClipPreviewConflict('当前故事板没有基础版本，请先保留当前故事板并建立版本')
        if task.current_timeline_version_id != command.base_version_id:
            raise ClipPreviewConflict('时间线已经变化，请刷新后重新预览')
        base = self.repository.get_timeline_version(workspace_id, command.base_version_id)
        source = command.source
        if source is None and original_command is not None:
            action = CreationTaskExactEditRequest(operation_type=command.operation_type, **{'clip_ids': command.clip_ids, 'effects': command.values} if command.operation_type == 'AdjustVisualEffects' else {'clip_id': command.clip_ids[0], **command.values})
            source = {'workspace_id': workspace_id, 'task_id': task_id, 'member_id': actor.user_id or actor.member_id, 'actor_identity': actor.identity_payload(), 'request_id': None, 'raw_command': original_command.raw_command, 'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence, 'action': action.model_dump(mode='json', exclude_unset=True), 'source_facts': self.source_facts(task, action, {'member_id': actor.user_id or actor.member_id, 'local_diagnostic': False}, actor)}
            if original_command.assistant_turn_id is not None:
                origin = self.conversations.task_turn_source(str(original_command.assistant_turn_id), workspace_id, actor.user_id or actor.member_id, task_id)
                source.update(assistant_turn_id=str(original_command.assistant_turn_id), conversation_id=origin['conversation_id'],
                              **{key: origin[key] for key in ('input_id', 'input_turn_id') if key in origin})
            command = replace(command, source=source)
        self.clip_preview.prepare(actor, command)
        self.clip_preview.seal(actor, command)
        return command
    def submit_clip(self, runtime, media, actor, command, *, operation):
        workspace_id, task_id = (actor.workspace_id, command.task_id)
        source = command.source
        media.clip_preview = ClipPreviewExecutor(self.clip_preview, self.metadata.audio_service)
        arguments = {'_media_kind': 'clip_preview', '_draft': False, '_clip_preview': asdict(command)}
        if source is not None:
            arguments['_typed_source'] = source
        arguments['payload'] = {'operation_type': command.operation_type, 'clip_ids': command.clip_ids, 'clip_id': command.clip_ids[0], 'base_version_id': command.base_version_id, **({'effects': command.values} if command.operation_type == 'AdjustVisualEffects' else command.values)}
        return media.submit(runtime, actor, task_id, operation, arguments, 'edit_preview')

    def execute(self, job, event, actor, context):
        payload = CreationTaskExactEditRequest.model_validate(event['arguments']['payload'])
        source = event['arguments'].get('_typed_source')
        if source is not None:
            source = {**source, 'media_job_id': job.id, 'actor_identity': actor.identity_payload()}
        outcome = self.preview(job.workspace_id, job.task_id, payload, identity={'member_id': actor.user_id or actor.member_id, 'local_diagnostic': False}, actor=actor, source=source, draft_only=event['arguments'].get('_draft', False), execution=(context, job, event, actor), reader_identity={'member_id': actor.user_id or actor.member_id, 'local_diagnostic': False})
        intent, proposal = outcome
        return CreationTaskEditPreviewResponse.model_validate({'intent': asdict(intent), 'proposal': asdict(proposal)}).model_dump(mode='json')

    def validate_result(self, job, event, result, context):
        return self.metadata.audio_service.validate_publications(job, event, result, context)


    def linked_values(self, payload):
        if payload.operation_type == 'ChangeLinkedSpeed':
            if not payload.clip_id or payload.playback_rate is None or payload.model_fields_set - {'operation_type', 'clip_id', 'playback_rate'}:
                raise EditPreviewRejected(status_code=422, detail='关联变速需要镜头和倍速，不混入其他操作')
            return {'clip_id': payload.clip_id, 'playback_rate': payload.playback_rate}
        if payload.operation_type in {'DeleteClip', 'DuplicateClip'}:
            if not payload.clip_id or payload.model_fields_set - {'operation_type', 'clip_id'}:
                raise EditPreviewRejected(status_code=422, detail='关联删除或复制只接受一个镜头，不混入其他修改')
            return {'clip_id': payload.clip_id}
        if not payload.clip_id or payload.model_fields_set - {'operation_type', 'clip_id', 'asset_id', 'segment_id', 'source_start_seconds'}:
            raise EditPreviewRejected(status_code=422, detail='插入镜头只接受一个插入位置和一段素材，不混入其他修改')
        return {'clip_id': payload.clip_id, 'asset_id': payload.asset_id, 'segment_id': payload.segment_id, 'source_start_seconds': payload.source_start_seconds}


    def linked_command(self, task, identity, operation_type, values, *, source, reader_identity):
        command = metadata_edit_command(task, identity, operation_type, values, source=source)
        if operation_type == 'InsertClip':
            user_id, diagnostic = (reader_identity['member_id'], reader_identity['local_diagnostic'])
            command = replace(command, actor_identity={**command.actor_identity, 'material_reader_identity': {'member_id': user_id, 'local_diagnostic': diagnostic}})
        return command


    def prepare_linked(self, workspace_id, task_id, payload):
        if payload.operation_type == 'ChangeLinkedSpeed':
            task = self.metadata.task_or_missing(workspace_id, task_id)
            values = self.linked_values(payload)
        else:
            values = self.linked_values(payload)
            task = self.metadata.task_or_missing(workspace_id, task_id)
        return (task, values)

    def preview_linked(self, command, payload):
        try:
            intent, proposal = self.linked.preview(command)
        except LinkedEditInputError as exc:
            raise EditPreviewRejected(status_code=422, detail=str(exc)) from exc
        except InsertMaterialMissing as exc:
            raise EditPreviewRejected(status_code=404, detail=exc.args[0]) from exc
        except InsertSourceRangeError as exc:
            raise EditPreviewRejected(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise EditPreviewRejected(status_code=403, detail=str(exc)) from exc
        except (EditConflict, KeyError, TimelineVersionConflict) as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            if payload.operation_type == 'ChangeLinkedSpeed':
                raise EditPreviewRejected(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else '关联变速预览失败，请检查本地素材和FFmpeg') from exc
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
        return (intent, proposal)


    def preview_legacy(self, workspace_id, task_id, payload, *, source):
        task = self.metadata.task_or_missing(workspace_id, task_id)
        if payload.operation_type not in {'UpdateSubtitleCue', 'ConfigureSubtitleTrack', 'GenerateCoverFromFrame'}:
            raise EditPreviewRejected(status_code=422, detail='该精确操作尚未迁移到预览确认入口')
        try:
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
        except KeyError as exc:
            raise EditPreviewRejected(status_code=404, detail='creation task montage plan not found') from exc
        base_version = self.metadata.ensure_montage_base(workspace_id, task, montage)
        task = self.repository.get_creation_task(workspace_id, task_id)
        tracks = self.metadata.current_subtitle_tracks(workspace_id, task, current_version=base_version, montage=montage)
        now = utc_now()
        if payload.operation_type == 'GenerateCoverFromFrame':
            summary, target_refs, operation_payload, preview, effects = self.metadata.cover_description(montage, base_version, payload)
        else:
            summary, target_refs, operation_payload, preview, effects = self.subtitles.legacy_description(task, montage, base_version, tracks, payload, self.settings)
        intent = self.repository.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=f'精确操作：{summary}', context_stage='review' if task.stage == 'review' else 'montage', selected_object_refs=target_refs, playhead_ms=round((payload.frame_time_seconds or 0) * 1000) if payload.operation_type == 'GenerateCoverFromFrame' else None, intent_type='exact_subtitle_cover_edit', scope={'kind': payload.operation_type}, constraints={'preserve_narration': True, 'preserve_clips': True}, confidence=1.0, status='interpreted', created_at=now), source=source)
        proposal = self.repository.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=summary, target_refs=target_refs, operation={'operation_type': payload.operation_type, 'payload': operation_payload}, invalidated_refs=logical_montage_output_refs(montage) if payload.operation_type != 'GenerateCoverFromFrame' else [], preview=preview, external_effects=effects, created_at=now, updated_at=now))
        return (intent, proposal)

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
        snapshot = {key:value for key,value in creation_task_montage_plan_to_payload(plan).items() if key not in transient} if plan else None
        payload = arguments.get('payload', {})
        picture_candidates = [plan] if plan is not None else []
        if payload.get('operation_type') == 'EditGraphicOverlay':
            overlay_payload = CreationTaskExactEditRequest.model_validate(payload)
            picture_candidates.append(self.metadata.graphic_overlay_candidate(plan, overlay_payload))
        expected = payload.get('base_version_id')
        if 'base_version_id' in payload and expected != task.current_timeline_version_id:
            raise ValueError('时间线已变化，请重新安排当前要求')
        visual_scope = None
        if payload.get('operation_type') == 'AdjustVisualEffects' and plan is not None and arguments.get('_media_kind') == 'picture_preview':
            tracks = self.metadata.current_subtitle_tracks(workspace_id,task,montage=plan)
            representative,_after,projected = self.visual.project(plan,payload['clip_ids'],payload['effects'],tracks)
            visual_scope = {'representative_clip_id':representative.id,'selected_clip_ids':[item.id for item in plan.clips if item.id in payload['clip_ids']], 'start_seconds':representative.timeline_start_seconds,'end_seconds':representative.timeline_end_seconds,'scope':'independent_clip','adjacent_transitions_included':False}
            if projected is None:
                raise ValueError('画面预览范围与封存的媒体用途不一致')
            plan = projected[0][0]
            picture_candidates = [candidate for candidate,_tracks in projected]
            visual_scope['subtitle_tracks'] = [{key:value for key,value in asdict(track).items() if key not in {'created_at','updated_at'}} for track in projected[0][1]]
        files,audio_inputs,graphics = [],[],[]
        service = self.metadata.audio_service
        if plan is not None:
            picture_files,graphics = self.metadata.picture_renderer.qualified_picture_sources(workspace_id,task,plan,picture_candidates,actor)
            files.extend(picture_files)
            graph = effective_audio_timeline(plan)
            solo = set(payload.get('solo_track_ids',[]))
            clips = [clip for track in graph.tracks if not track.muted and (not solo or track.id in solo) for clip in track.clips if not clip.muted]
            uses = Counter(clip.source_ref for clip in clips)
            for reference in sorted({clip.source_ref for clip in clips}):
                path = service.source(workspace_id,task_id,reference,actor=actor)
                stat = path.stat()
                fact = [reference,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]
                if fact not in files:files.append(fact)
                values = service.source_metadata(workspace_id,task_id,reference)
                audio_inputs.append({'reference':reference,'path':str(path.resolve()),'size_bytes':stat.st_size,'uses':uses.get(reference,1),**values})
        facts = {'base_version_id':task.current_timeline_version_id,'script_id':task.confirmed_script_id,'script_digest':self.repository.confirmed_creation_task_script_digest(workspace_id,task_id,task.confirmed_script_id) if task.confirmed_script_id else None,'plan_id':task.plan_id,'plan_digest':media_digest(asdict(self.repository.get_creation_plan(workspace_id,task.plan_id))),'montage':snapshot,'files':files,'narration_cache_key':task.narration_cache_key}
        if visual_scope is not None:facts['visual_preview']=visual_scope
        if graphics:facts['graphic_sources']=graphics
        return task,plan,payload,picture_candidates,facts,audio_inputs

    def picture_resources(self,prepared,budget):
        task,plan,payload,picture_candidates,facts,audio_inputs=prepared
        return self.metadata.picture_renderer.picture_resources(plan,payload,picture_candidates,facts,budget)

    def seal_resources(self,prepared,resources,audio_budget,*,actor):
        task,plan,payload,picture_candidates,facts,audio_inputs=prepared
        facts,resources=self.metadata.picture_renderer.seal_audio_resources(task,picture_candidates,facts,resources,audio_budget,actor=actor)
        if '校准' in payload.get('raw_command',''):
            raise ValueError('本地字幕识别尚无独立标定的模型资源许可，未启动识别')
        return facts,resources

    def submission_arguments(self,workspace_id,task_id,actor,payload,*,media_kind,draft_only,source):
        task=self.metadata.task_or_missing(workspace_id,task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise EditPreviewRejected(status_code=403,detail='当前成员不能安排此任务的媒体处理')
        values={'_media_kind':media_kind,'_draft':draft_only}
        if source is not None:values['_typed_source']=source
        values['payload']=payload.model_dump(mode='json',exclude_unset=True)
        if 'base_version_id' in values['payload'] and values['payload']['base_version_id'] is None:
            values['payload']['base_version_id']=task.current_timeline_version_id
        return task,values

    def submit(self,runtime,media,actor,task,values):
        if task.montage_plan_id and task.current_timeline_version_id is None:
            media.facts(task.workspace_id,task.id,'exact_edit_preview',values,actor)
            if media.verify_identity(actor) != 'ready':
                raise PermissionError('媒体处理身份失效或无法复核')
            montage=creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id,task.id))
            base=self.metadata.ensure_montage_base(task.workspace_id,task,montage)
            if 'base_version_id' in values['payload']:values['payload']['base_version_id']=base.id
        return media.submit(runtime,actor,task.id,'exact_edit_preview',values,'edit_preview')
