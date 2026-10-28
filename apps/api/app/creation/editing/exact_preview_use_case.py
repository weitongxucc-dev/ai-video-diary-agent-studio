"""The finite exact-edit preview application, with explicit source and execution."""
from collections import Counter
import json
from app.assistant_goal_continuation import GoalResultProjection
from dataclasses import asdict, dataclass, replace
from contextlib import nullcontext
from uuid import uuid4

from app.audio_timeline import AudioEditRequest, apply_audio_edit
from app.creation.clip_preview import ClipPreviewCommand, ClipPreviewConflict, ClipPreviewUnavailable, needs_full_visual_preview
from app.creation.contracts import EditIntent, EditProposal, TaskEvent
from app.creation.editing.audio_edit_use_case import AudioEditRejected
from app.creation.execution_control import ExecutionControlRejected
from app.media.picture_preview import PicturePreviewRejected
from app.creation.editing.clips import CLIP_SETTING_OPERATIONS
from app.creation.editing.metadata_use_case import (EditPreviewRejected, edit_preview_failure,
    metadata_edit_command, effective_audio_timeline, logical_montage_output_refs, MetadataEditReplay)
from app.creation.editing.subtitle_use_case import subtitle_values
from app.creation.editing.picture import EditConflict
from app.creation.editing.linked_timeline_use_case import LinkedEditInputError, InsertMaterialMissing, InsertSourceRangeError
from app.media.clip_preview import ClipPreviewExecutor
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.models import utc_now
from app.production import TimelineVersionConflict
from app.source_policy import knowledge_item_readable as content_readable
from app.schemas import (
    CreationTaskExactEditRequest, CreationTaskEditPreviewResponse,
    CreationTaskObservationRequest, MediaScriptToolPreviewRequest,
    MediaScriptPreviewRequest, CreationTaskEditApplyRequest,
)
from app.task_audio import media_digest
from app.visual_effects import VisualEditRequest
import subprocess


@dataclass(frozen=True)
class ExactEditResult:
    status_code: int
    value: dict
    changed: bool = False


def validate_edit_origin(conversations, source, actor, *, connection=None):
    if source is None:
        return
    if (source.get('workspace_id')!=actor.workspace_id
            or source.get('member_id')!=(actor.user_id or actor.member_id)
            or source.get('actor_identity')!=actor.identity_payload()):
        raise PermissionError('编辑来源与原成员身份不一致')
    if not source.get('assistant_turn_id'):
        if source.get('source_kind')!='interactive' or source.get('input_id') or source.get('input_turn_id'):
            raise PermissionError('交互编辑来源不能缺失或冒用助手输入')
        return
    with (conversations._connect() if connection is None else nullcontext(connection)) as conn:
        row=conn.execute('SELECT status FROM assistant_conversation_turns WHERE id=?',
            (source['assistant_turn_id'],)).fetchone()
        if row is None:
            raise ValueError('原编辑轮次不存在')
        root=conversations._goal_root(conn,source['assistant_turn_id'])
        if root is not None and root['goal']['status'] not in {'running','waiting_result','waiting_adoption','waiting_identity','waiting_unknown'}:
            raise ValueError('原编辑目标已停止')
        if row['status']=='running':
            origin=conversations._task_turn_source(conn,source['assistant_turn_id'],actor.workspace_id,
                actor.user_id or actor.member_id,source['task_id'])
            if root is None:
                root={'id':source['assistant_turn_id'],'conversation_id':origin['conversation_id'],'goal':{}}
        elif row['status'] in {'completed','failed'} and root is not None:
            goal=root['goal']
            if (goal['status'] not in {'running','waiting_result','waiting_adoption','waiting_identity','waiting_unknown'}
                    or (goal['workspace_id'],goal['task_id'],goal['member_id']) !=
                        (actor.workspace_id,source['task_id'],actor.user_id or actor.member_id)
                    or (goal.get('source_input_id'),goal.get('source_input_turn_id')) !=
                        (source.get('input_id'),source.get('input_turn_id'))):
                raise ValueError('原编辑目标已停止或输入已变化')
            origin=conversations.task_result_source(source['assistant_turn_id'],actor.workspace_id,
                actor.user_id or actor.member_id,source['task_id'],input_id=source.get('input_id'),
                input_turn_id=source.get('input_turn_id'))
            if origin['context'].get('source_kind')=='business_event':
                origin['raw_command']=origin['context'].get('input_instruction') or goal['original_instruction']
        else:
            raise ValueError('原编辑轮次已停止且没有继续执行的目标')
        if not conversations._goal_origin_current(conn,root):
            raise ValueError('原编辑目标已被新要求替代')
    if (origin.get('input_id')!=source.get('input_id') or origin.get('input_turn_id')!=source.get('input_turn_id')
            or origin['conversation_id']!=source.get('conversation_id') or origin['raw_command']!=source.get('raw_command')):
        raise ValueError('原编辑输入已变化，不能沿旧来源执行')



class ExactPreviewUseCase:
    def __init__(self, repository, metadata, subtitles, linked, audio_edit, visual, clip_preview, conversations, settings, *, edit_result=None, preview_result=None, person_preview=None, narration_preview=None, person_stage=None, execution_control=None, runtime_for=None):
        self.repository, self.metadata, self.subtitles, self.linked = repository, metadata, subtitles, linked
        self.audio_edit, self.visual, self.clip_preview = audio_edit, visual, clip_preview
        self.conversations, self.settings = conversations, settings
        self.edit_result, self.preview_result = edit_result, preview_result
        self.person_preview,self.narration_preview,self.person_stage=person_preview,narration_preview,person_stage
        self.execution_control,self.runtime_for=execution_control,runtime_for

    def claim_request(self, task, member_id, request_id, phase, inputs):
        try:
            return self.repository.claim_edit_request(task, member_id, request_id, phase, inputs, write_guard=self.metadata.write_guard)
        except ValueError as exc:
            raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc

    def read_request_result(self, task, event_id):
        return self.repository.read_edit_request_result(task, event_id)

    def read_request_receipt(self, task, member_id, request_id, phase):
        """The bounded canonical receipt, shared by HTTP and execution provenance."""
        # The former self-HTTP read performed a fresh write-access check here,
        # after successor lineage checks. Preserve it for every plain reader too.
        try:
            task = self.repository.task_records.get_creation_task(task.workspace_id, task.id)
        except KeyError as exc:
            raise EditPreviewRejected(404, "creation task not found") from exc
        if not content_readable(user_id=member_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise EditPreviewRejected(404, "creation task not found")
        if task.owner_user_id and task.owner_user_id != member_id:
            raise EditPreviewRejected(403, "当前成员不能修改此任务")
        event_id, event = GoalResultProjection(self.repository, self.conversations).request_claim(
            task, member_id, str(request_id), phase)
        inputs = event.payload["inputs"]
        if phase in {"preview", "script", "observation"} or (phase == "apply" and "operation_type" in inputs):
            fields = (CreationTaskExactEditRequest.model_fields if phase in {"preview", "apply"} else
                      CreationTaskObservationRequest.model_fields if phase == "observation" else
                      MediaScriptToolPreviewRequest.model_fields if "tool_id" in inputs else MediaScriptPreviewRequest.model_fields)
            submitted_input = {key: value for key, value in inputs.items() if key in fields}
            if phase == "script" and "tool_id" in inputs:
                submitted_input["tool_id"] = inputs["tool_id"]
        else:
            submitted_input = {key: inputs[key] for key in ("proposal_id", "base_version_id", "input_digest")}
            submitted_input["request"] = {key: value for key, value in inputs["request"].items() if key in CreationTaskEditApplyRequest.model_fields}
        if len(json.dumps(submitted_input, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise EditPreviewRejected(409, "原请求字段超过有界查询范围，不能省略字段或换绑新版重放")
        result = self.read_request_result(task, event_id)
        return {"request_id": str(request_id), "phase": phase, "input_digest": event.payload["input_digest"],
                "task_id": task.id, "status": "recorded" if result is not None else "unknown",
                "submitted_input": submitted_input,
                "result_status_code": result["status_code"] if result is not None else None,
                "result": result["response"] if result is not None else None}

    def record_request_result(self, task, event_id, *, status_code, response):
        if event_id is not None:
            return self.repository.save_edit_request_result(task, event_id, status_code=status_code, response=response)

    def _request_origin(self, task, payload, identity, actor, origin, expected_input_id):
        source={'workspace_id':task.workspace_id,'task_id':task.id,'member_id':identity['member_id'],
            'request_id':str(payload.request_id),'actor_identity':actor.identity_payload()}
        if payload.assistant_turn_id is None:
            return {**source,'source_kind':'interactive'}
        if origin is None:
            origin=self.conversations.task_turn_source(str(payload.assistant_turn_id),task.workspace_id,
                identity['member_id'],task.id)
            if expected_input_id != origin.get('input_id'):
                raise EditPreviewRejected(409,'助手输入已变化，不能封存旧请求来源')
        elif origin['assistant_turn_id'] != str(payload.assistant_turn_id):
            raise EditPreviewRejected(409,'原请求不能切换助手轮次')
        return {**source,'assistant_turn_id':str(payload.assistant_turn_id),
            **{key:origin[key] for key in ('conversation_id','raw_command','input_id','input_turn_id') if key in origin}}

    def _seal_request_origin(self, task, event_id, source, actor):
        if event_id is None:
            return
        with self.repository._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if self.metadata.write_guard is not None:
                self.metadata.write_guard(conn)
            claim=self.repository.execution_records.get_task_event(task.workspace_id,event_id,connection=conn)
            self.validate_origin(source,actor,connection=conn)
            if source.get('assistant_turn_id'):
                self.conversations._task_turn_source(conn,source['assistant_turn_id'],task.workspace_id,
                    source['member_id'],task.id)
            value={'request_event_id':event_id,'input_digest':claim.payload['input_digest'],'source':source}
            self.repository.execution_records.append_task_event(TaskEvent(event_id+'_source',task.workspace_id,task.id,None,
                'edit_request_source',value),connection=conn)

    def read_request_source(self, task, event_id, actor):
        claim=self.repository.execution_records.get_task_event(task.workspace_id,event_id)
        if claim.task_id != task.id or claim.payload.get('phase') not in {'apply','preview'}:
            raise EditPreviewRejected(409,'该来源读回只适用于原 exact apply/preview 请求')
        try:
            anchor=self.repository.execution_records.get_task_event(task.workspace_id,event_id+'_source')
        except KeyError as exc:
            raise EditPreviewRejected(409,{'error':'edit_request_source_unavailable',
                'reason':'原请求已存在，但缺少可信来源锚点，不能推定未提交或自动重试',
                'submission_effect':'unknown','repair_action':'保留原请求，核对既有结果与任务状态。'}) from exc
        source=anchor.payload['source']
        if (anchor.event_type!='edit_request_source' or anchor.task_id!=task.id
                or anchor.payload['request_event_id']!=event_id
                or anchor.payload['input_digest']!=claim.payload['input_digest']
                or source['workspace_id']!=task.workspace_id or source['task_id']!=task.id
                or source['request_id']!=claim.payload['request_id']
                or source['member_id']!=claim.payload['member_id']
                or source['actor_identity']!=actor.identity_payload()
                or source['member_id']!=(actor.user_id or actor.member_id)):
            raise PermissionError('原请求来源与任务或完整成员身份不一致')
        return source

    def _verify_request_replay(self, task, event_id, actor, payload, origin, expected_input_id):
        try:
            source=self.read_request_source(task,event_id,actor)
        except PermissionError as exc:
            raise EditPreviewRejected(403,str(exc)) from exc
        expected_turn=str(payload.assistant_turn_id) if payload.assistant_turn_id is not None else None
        expected_input=origin.get('input_id') if origin is not None else expected_input_id
        if (source.get('assistant_turn_id')!=expected_turn or source.get('input_id')!=expected_input
                or origin is not None and source.get('input_turn_id')!=origin.get('input_turn_id')):
            raise EditPreviewRejected(403,'原请求不能归入另一轮次或输入')

    def typed_source(self, task, payload, member_id):
        version = self.repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if not {'expected_timeline_version_id', 'expected_timeline_sequence'} <= payload.model_fields_set:
            raise EditPreviewRejected(status_code=422, detail='类型化请求须绑定期望时间线版本和序号')
        if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise EditPreviewRejected(status_code=409, detail='时间线已变化，请重新读取当前任务')
        source = {'workspace_id': task.workspace_id, 'task_id': task.id, 'member_id': member_id, 'request_id': str(payload.request_id) if payload.request_id else None, 'expected_timeline_version_id': payload.expected_timeline_version_id, 'expected_timeline_sequence': payload.expected_timeline_sequence}
        return source, version
    def complete_typed(self, task, payload, member_id, actor, source, version, *, origin=None):
        if actor is not None:
            source['actor_identity'] = actor.identity_payload()
        if origin is not None:
            if str(payload.assistant_turn_id) != origin['assistant_turn_id']:
                raise EditPreviewRejected(409, '类型化请求不能切换原回合')
            source.update({key: origin[key] for key in ('assistant_turn_id', 'conversation_id', 'raw_command', 'input_id', 'input_turn_id') if key in origin})
        elif payload.assistant_turn_id:
            try:
                origin = self.conversations.task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task.id)
            except ValueError as exc:
                raise EditPreviewRejected(status_code=409, detail=str(exc)) from exc
            snapshot = origin['context'].get('target_snapshot', {})
            if (snapshot.get('timeline_version_id'), snapshot.get('timeline_sequence')) != (task.current_timeline_version_id, version.sequence if version else 0):
                raise EditPreviewRejected(status_code=409, detail='助手轮次的目标版本已失效')
            source.update(assistant_turn_id=str(payload.assistant_turn_id), conversation_id=origin['conversation_id'], raw_command=origin['raw_command'],
                          **{key: origin[key] for key in ('input_id', 'input_turn_id') if key in origin})
        if not payload.assistant_turn_id:
            source['source_kind']='interactive'
        clean = CreationTaskExactEditRequest.model_validate(payload.action_values())
        source['action'] = clean.action_values()
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
        if operation in {'ChangePersonSegment','RegeneratePersonRender','RegenerateNarrationSegment'}:
            from app.schemas import CreationTaskEditPreviewRequest
            task=self.metadata.task_or_missing(workspace_id,task_id)
            if operation=='RegenerateNarrationSegment':
                selected=f'narration:{payload.narration_segment_id}'
                command=payload.delivery_instruction or '这句重新配音'
                preview_payload=CreationTaskEditPreviewRequest(raw_command=command,context_stage='narration',selected_object_refs=[selected])
                return self.narration_preview.preview(task,preview_payload,selected,command,identity=identity,
                    request_source=source,draft_only=draft_only,execution=execution,actor=actor)
            selected=f'clip:{payload.clip_id}'
            command='这里只重做口型，其他都不要动' if operation=='RegeneratePersonRender' else '这一段换成当前人物出镜'
            preview_payload=CreationTaskEditPreviewRequest(raw_command=command,context_stage='review' if task.stage=='review' else 'montage',selected_object_refs=[selected])
            return self.person_preview.preview(task,preview_payload,selected,command,identity=identity,
                request_source=source,draft_only=draft_only,execution=execution,actor=actor)
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
        return media.submit(runtime, actor, task_id, operation, arguments, 'edit_preview', write_guard=self.metadata.write_guard)

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
        base_version = self.metadata.ensure_montage_base(workspace_id, task, montage, source=source)
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        tracks = self.metadata.current_subtitle_tracks(workspace_id, task, current_version=base_version, montage=montage)
        now = utc_now()
        if payload.operation_type == 'GenerateCoverFromFrame':
            summary, target_refs, operation_payload, preview, effects = self.metadata.cover_description(montage, base_version, payload)
        else:
            summary, target_refs, operation_payload, preview, effects = self.subtitles.legacy_description(task, montage, base_version, tracks, payload, self.settings)
        intent = self.repository.edit_records.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=f'精确操作：{summary}', context_stage='review' if task.stage == 'review' else 'montage', selected_object_refs=target_refs, playhead_ms=round((payload.frame_time_seconds or 0) * 1000) if payload.operation_type == 'GenerateCoverFromFrame' else None, intent_type='exact_subtitle_cover_edit', scope={'kind': payload.operation_type}, constraints={'preserve_narration': True, 'preserve_clips': True}, confidence=1.0, status='interpreted', created_at=now), source=source)
        proposal = self.repository.edit_records.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=summary, target_refs=target_refs, operation={'operation_type': payload.operation_type, 'payload': operation_payload}, invalidated_refs=logical_montage_output_refs(montage) if payload.operation_type != 'GenerateCoverFromFrame' else [], preview=preview, external_effects=effects, created_at=now, updated_at=now))
        return (intent, proposal)

    def prepare_source(self, workspace_id, task_id, arguments, actor):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
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
        facts = {'base_version_id':task.current_timeline_version_id,'script_id':task.confirmed_script_id,'script_digest':self.repository.confirmed_creation_task_script_digest(workspace_id,task_id,task.confirmed_script_id) if task.confirmed_script_id else None,'plan_id':task.plan_id,'plan_digest':media_digest(asdict(self.repository.plan_records.get_creation_plan(workspace_id,task.plan_id))),'montage':snapshot,'files':files,'narration_cache_key':task.narration_cache_key}
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

    def submit(self,runtime,media,actor,task,values,*,write_guard=None):
        if task.montage_plan_id and task.current_timeline_version_id is None:
            media.facts(task.workspace_id,task.id,'exact_edit_preview',values,actor)
            if media.verify_identity(actor) != 'ready':
                raise PermissionError('媒体处理身份失效或无法复核')
            montage=creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id,task.id))
            base=self.metadata.ensure_montage_base(task.workspace_id,task,montage,source=values.get('_typed_source'))
            if 'base_version_id' in values['payload']:values['payload']['base_version_id']=base.id
        return media.submit(runtime,actor,task.id,'exact_edit_preview',values,'edit_preview',write_guard=write_guard)

    def validate_origin(self, source, actor, *, connection=None):
        return validate_edit_origin(self.conversations,source,actor,connection=connection)

    def _source(self, task, payload, identity, actor, origin, expected_input_id=None):
        from app.schemas import EXACT_EDIT_ENVELOPE_FIELDS
        if not payload.model_fields_set & EXACT_EDIT_ENVELOPE_FIELDS:
            return payload, None
        source, version = self.typed_source(task, payload, identity['member_id'])
        source['source_facts'] = self.source_facts(task, payload, identity, actor)
        clean, source = self.complete_typed(task, payload, identity['member_id'], actor, source, version, origin=origin)
        if origin is None and source.get('assistant_turn_id') and expected_input_id != source.get('input_id'):
            raise EditPreviewRejected(409, '助手输入已变化，旧请求不能归入新要求；请查询原请求回执')
        return clean, source

    def _record(self, task, event_id, result):
        self.record_request_result(task, event_id, status_code=result.status_code, response=result.value)
        return result

    def _failure(self, task, event_id, status, detail):
        self.record_request_result(task, event_id, status_code=status, response={'detail': detail})
        return EditPreviewRejected(status, detail)

    def confirm(self, task, proposal, *, identity, reader_identity, actor, source):
        from app.creation.editing.metadata_use_case import PICTURE_METADATA_OPERATIONS, CLIP_METADATA_OPERATIONS
        operation = proposal.operation['operation_type']
        if operation in {'ChangePersonSegment','RegeneratePersonRender','RegenerateNarrationSegment'}:
            from app.creation.person_stage_use_case import PersonEditGrantRequired,PersonStageRejected
            if operation!='RegenerateNarrationSegment':
                try:
                    return self.person_stage.apply_segment_edit(task.workspace_id,task,proposal)
                except PersonEditGrantRequired:
                    pass
                except PersonStageRejected as exc:
                    raise EditPreviewRejected(exc.status_code,exc.detail) from exc
            if actor is None:
                raise EditPreviewRejected(403,'提交人物或配音编辑需要原成员身份')
            control=self.execution_control()
            submit=control.submit_narration_edit if operation=='RegenerateNarrationSegment' else control.submit_person_edit
            def submit_guard(conn):
                if self.metadata.write_guard is not None:
                    self.metadata.write_guard(conn)
                self.validate_origin(source,actor,connection=conn)
                if source is not None and source.get('assistant_turn_id'):
                    self.conversations._task_turn_source(conn,source['assistant_turn_id'],actor.workspace_id,
                        actor.user_id or actor.member_id,task.id)
            job,queued=submit(task.workspace_id,task,proposal,actor,self.runtime_for(),source=source,write_guard=submit_guard)
            return ExactEditResult(202,{'status':'queued','job_id':job.id,'request_id':queued.id},True)
        if operation in {'ChangeCanvas','ChangePresenterShare','ReorderTimelineItems'} | PICTURE_METADATA_OPERATIONS | CLIP_METADATA_OPERATIONS:
            return self.metadata.confirm(task,proposal,identity=identity,reader_identity=reader_identity,source=source)
        if operation in {'AlignSubtitleTiming','ConfigureSubtitleTrack','UpdateSubtitleCue'}:
            return self.subtitles.confirm_legacy(task,proposal,settings=self.settings)
        if operation == 'EditAudioTimeline':
            return self.audio_edit.confirm(task,proposal,actor,source=source)
        if operation == 'RemoveSilence':
            return self.linked.confirm_remove_silence(task,proposal)
        raise EditPreviewRejected(409, '此操作不能直接保存，请使用对应预览与采用入口')

    def apply(self, task, actor, payload, *, identity, origin=None, expected_input_id=None):
        from app.creation.editing.metadata_use_case import PICTURE_METADATA_OPERATIONS, CLIP_METADATA_OPERATIONS, MetadataEditReplay, metadata_edit_command
        from app.schemas import EXACT_EDIT_FIELDS, EXACT_MONTAGE_REQUIRED_FIELDS
        from app.creation.editing.subtitle_use_case import is_subtitle_edit, SubtitleNoChange, SubtitleTargetMissing, SubtitlePreviewFailed
        inputs = payload.model_dump(mode='json', exclude_unset=True)
        event_id, replay = self.metadata.begin(task, identity['member_id'], payload.request_id,
            inputs, phase='apply')
        if replay is not None:
            self._verify_request_replay(task,event_id,actor,payload,origin,expected_input_id)
            status, value = self.edit_result(replay)
            result = ExactEditResult(status, value, replay.recorded_projection is None)
            return self._record(task,event_id,result) if replay.recorded_projection is None else result
        try:
            if event_id is not None:
                provenance=self._request_origin(task,payload,identity,actor,origin,expected_input_id)
                self._seal_request_origin(task,event_id,provenance,actor)
            payload, source = self._source(task, payload, identity, actor, origin, expected_input_id)
            if (payload.operation_type in PICTURE_METADATA_OPERATIONS | CLIP_METADATA_OPERATIONS
                    or payload.operation_type == 'EditAudioTimeline' and payload.audio_edit.action in {'update', 'track'}
                    or payload.operation_type == 'ConfigureSubtitleTrack' and payload.subtitle_display is not None
                    or payload.operation_type == 'UpdateSubtitleCue' and payload.track_id is None):
                values = payload.action_values()
                values.pop('operation_type')
                required = ({'cue_id', 'text'} if payload.operation_type == 'UpdateSubtitleCue' else
                    EXACT_MONTAGE_REQUIRED_FIELDS.get(payload.operation_type, set()) | ({'clip_id'} if payload.operation_type in CLIP_METADATA_OPERATIONS else set()))
                if set(values) - EXACT_EDIT_FIELDS[payload.operation_type] or any(values.get(key) is None for key in required):
                    raise EditPreviewRejected(422, '精确编辑包含其他操作字段或缺少必要字段')
                values['base_version_id'] = source['expected_timeline_version_id'] if source else task.current_timeline_version_id
                command = metadata_edit_command(task, identity, payload.operation_type, values, source=source)
                audio_edit = AudioEditRequest(base_version_id=command.base_version_id, **payload.audio_edit.model_dump()) if payload.operation_type == 'EditAudioTimeline' else None
                outcome = self.metadata.save(task, command, audio_edit=audio_edit, audio_actor=actor)
            elif payload.operation_type in {'ChangeLinkedSpeed', 'DeleteClip', 'DuplicateClip', 'InsertClip'}:
                task, values = self.prepare_linked(task.workspace_id, task.id, payload)
                command = self.linked_command(task, identity, payload.operation_type,
                    {'base_version_id': task.current_timeline_version_id, **values}, source=source, reader_identity=identity)
                outcome = self.linked.apply(command)
            elif is_subtitle_edit(payload.operation_type, payload.model_dump(exclude_unset=True)):
                command = metadata_edit_command(task, identity, payload.operation_type,
                    {'base_version_id': task.current_timeline_version_id, **subtitle_values(payload)}, source=source)
                outcome = self.subtitles.apply(command)
            else:
                _intent, proposal = self.preview(task.workspace_id,task.id,payload,identity=identity,
                    actor=actor,source=source,draft_only=True,execution=None,reader_identity=identity)
                outcome = self.confirm(task,proposal,identity=identity,reader_identity=identity,actor=actor,source=source)
            if isinstance(outcome,ExactEditResult):
                result=outcome
            else:
                status, value = self.edit_result(outcome)
                result = ExactEditResult(status, value, not isinstance(outcome, MetadataEditReplay))
        except (EditPreviewRejected,AudioEditRejected,PicturePreviewRejected,ExecutionControlRejected) as exc:
            self.record_request_result(task, event_id, status_code=exc.status_code, response={'detail': exc.detail})
            raise
        except SubtitleNoChange as exc:
            raise self._failure(task, event_id, exc.details['status_code'], {k:v for k,v in exc.details.items() if k != 'status_code'}) from exc
        except (SubtitleTargetMissing, InsertMaterialMissing) as exc:
            raise self._failure(task, event_id, 404, exc.args[0]) from exc
        except LinkedEditInputError as exc:
            raise self._failure(task, event_id, 422, str(exc)) from exc
        except InsertSourceRangeError as exc:
            raise self._failure(task, event_id, 400, str(exc)) from exc
        except SubtitlePreviewFailed as exc:
            raise self._failure(task, event_id, 422, str(exc.original)) from exc
        except PermissionError as exc:
            raise self._failure(task, event_id, 403, str(exc)) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise self._failure(task, event_id, 409, str(exc)) from exc
        return self._record(task, event_id, result)

    def submit_preview(self, runtime_for, actor, task, payload, *, identity, project_media, origin=None, expected_input_id=None, draft_only=False):
        from app.task_audio import TaskMediaRequired
        event_id = None
        inputs = {**payload.model_dump(mode='json', exclude_unset=True), '_draft':draft_only}
        if payload.request_id is not None:
            event_id, fresh = self.claim_request(task, identity['member_id'], payload.request_id, 'preview', inputs)
            recorded = self.read_request_result(task, event_id)
            if recorded is not None:
                self._verify_request_replay(task,event_id,actor,payload,origin,expected_input_id)
                return ExactEditResult(recorded['status_code'], recorded['response'])
            if not fresh:
                raise EditPreviewRejected(409,{'error':'edit_request_result_unknown',
                    'reason':'原预览请求结果尚未确定，请查询原请求，不重复执行', 'submission_effect':'unknown'})
        try:
            if event_id is not None:
                provenance=self._request_origin(task,payload,identity,actor,origin,expected_input_id)
                self._seal_request_origin(task,event_id,provenance,actor)
            clean,source=self._source(task,payload,identity,actor,origin,expected_input_id)
            result=self._preview_or_submit(runtime_for,actor,task,clean,identity=identity,
                source=source,project_media=project_media,draft_only=draft_only)
        except (EditPreviewRejected,AudioEditRejected,PicturePreviewRejected) as exc:
            raise self._failure(task,event_id,exc.status_code,exc.detail) from exc
        except ClipPreviewUnavailable as exc:
            raise self._failure(task,event_id,503,str(exc)) from exc
        except PermissionError as exc:
            raise self._failure(task,event_id,403,str(exc)) from exc
        except (ValueError,KeyError,OSError) as exc:
            raise self._failure(task,event_id,409,str(exc)) from exc
        return self._record(task,event_id,result)

    def _preview_or_submit(self, runtime_for, actor, task, clean, *, identity, source, project_media, draft_only):
        from app.task_audio import TaskMediaRequired
        try:
            outcome = self.preview(task.workspace_id, task.id, clean, identity=identity, actor=actor,
                source=source, execution=None, reader_identity=identity, draft_only=draft_only)
            if isinstance(outcome,ClipPreviewCommand):
                runtime=runtime_for()
                media=runtime.registrations['creation_job'].owner.task_media
                command=self.prepare_clip_submission(actor,outcome)
                job=self.submit_clip(runtime,media,actor,command,operation='exact_edit_preview')
                receipt=project_media(media,job,actor,runtime)
                receipt['result']=None
                result=ExactEditResult(202,receipt)
            else:
                result = ExactEditResult(200, self.preview_result(*outcome))
        except TaskMediaRequired as required:
            runtime = runtime_for()
            media = runtime.registrations['creation_job'].owner.task_media
            task, values = self.submission_arguments(task.workspace_id, task.id, actor, clean,
                media_kind=required.kind, draft_only=draft_only, source=source)
            job = self.submit(runtime, media, actor, task, values, write_guard=self.metadata.write_guard)
            receipt = project_media(media, job, actor, runtime)
            receipt['result'] = None
            result = ExactEditResult(202, receipt)
        return result
