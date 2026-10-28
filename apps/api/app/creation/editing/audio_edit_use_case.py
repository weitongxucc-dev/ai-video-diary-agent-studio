"""Finite audio edits on explicit actors, original jobs and the metadata transaction."""
from dataclasses import asdict, dataclass, replace
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from app.audio_timeline import (AudioEditRequest, AudioTimeline, apply_audio_edit, compile_audio_command,
    audio_edit_preview_range)
from app.creation.contracts import EditCommand, EditIntent, EditProposal
from app.creation.editing.metadata_use_case import effective_audio_timeline, logical_montage_output_refs, prepare_changed_montage
from app.creation.editing.metadata import apply_audio_metadata
from app.creation.editing.sources import EditSourceVerifier
from app.creation.editing.picture import EditConflict
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.task_audio import TaskMediaRequired, media_digest, audio_mix_resources

class AudioEditRejected(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail

@dataclass(frozen=True)
class AudioEditPreviewResult:
    intent: EditIntent
    proposal: EditProposal

class AudioEditUseCase:
    def __init__(self, repository, audio, montage_edits, execution_mode):
        if audio.repository is not repository or montage_edits.repository is not repository:
            raise ValueError('音频编辑必须绑定同一仓储负责人')
        self.repository, self.audio, self.montage_edits = repository, audio, montage_edits
        self.execution_mode = execution_mode

    @staticmethod
    def handles(operation, arguments):
        if operation == 'audio_edit_preview':
            return True
        payload = arguments.get('payload', {})
        if operation == 'exact_edit_preview':
            return payload.get('operation_type') == 'EditAudioTimeline'
        if operation == 'command_edit_preview':
            refs = payload.get('selected_object_refs', [])
            return (len(refs) == 1 and refs[0].startswith('audio:') or
                payload.get('context_stage') in {'montage','review'} and re.fullmatch(r'(?:提取|添加|保留)(?:(?:当前|这个|所选)镜头的?)?原声[。！]?', payload.get('raw_command','').strip()) is not None)
        return False

    def edit_from_arguments(self, task, operation, arguments):
        payload = arguments['payload']
        if operation == 'audio_edit_preview':
            return AudioEditRequest.model_validate(payload)
        if operation == 'exact_edit_preview':
            edit = payload['audio_edit']
            return AudioEditRequest(base_version_id=task.current_timeline_version_id,
                action=edit['action'],target_id=edit['target_id'],values=edit['values'])
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id,task.id))
        return compile_audio_command(payload['raw_command'].strip(),payload['selected_object_refs'][0].removeprefix('audio:'),
            effective_audio_timeline(plan),task.current_timeline_version_id,payload.get('playhead_ms'))

    @staticmethod
    def identity(actor):
        return {'member_id':actor.user_id or actor.member_id,'user_id':actor.user_id,'local_diagnostic':False}

    def preview(self, workspace_id, task_id, payload, actor, *, draft_only, context=None, job=None, event=None, source=None):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        if task.current_timeline_version_id != payload.base_version_id:
            raise AudioEditRejected(status_code=409, detail='时间线已经变化，请刷新后重新预览音频修改')
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            before = effective_audio_timeline(plan)
            service = self.audio
            edit = payload
            if payload.action == 'replace_narration':
                if self.execution_mode == 'scheduled':
                    raise AudioEditRejected(status_code=503, detail='电脑录音校验尚未接入整机许可')
                if set(payload.values) != {'source_ref'} or any((clip.person_render_id for clip in plan.clips)):
                    raise ValueError('人物口型或附加参数不能随整条旁白录音替换保留')
                source_ref = payload.values['source_ref']
                if not isinstance(source_ref, str):
                    raise ValueError('请选择当前任务已保存的电脑录音')
                recording, measured_ms = service.recording(workspace_id, task_id, source_ref, verify_digest=True, actor=actor, context=context)
                recording_stat = self.audio.artifact_store.resolve_file(recording.logical_ref).stat()
                recording_file_signature = [recording_stat.st_size, recording_stat.st_mtime_ns, recording_stat.st_ctime_ns]
                duration_ms = round(plan.duration_seconds * 1000)
                if not duration_ms - 20 <= measured_ms <= duration_ms + 250:
                    raise ValueError('录音时长与当前画面不符，请重录到接近整片时长')
                edit = payload.model_copy(update={'values': {'source_ref': source_ref, 'source_end_ms': duration_ms}})
                after = apply_audio_edit(before, edit, duration_ms)
                original_ref = next((clip.source_ref for track in before.tracks if track.role == 'narration' for clip in track.clips))
                service.source(workspace_id, task_id, original_ref, actor=actor)
                if original_ref.startswith('narration://artifact/'):
                    if original_ref != self.repository.narration_artifact_reference(task.narration_cache_key or ''):
                        raise ValueError('当前旁白引用与任务版本不一致，请刷新后重试')
                    before_url = f'/api/creation-tasks/{task_id}/narration/audio'
                else:
                    original = next((record for record in self.repository.execution_records.list_artifact_records(workspace_id, task_id) if record.logical_ref == original_ref))
                    before_url = f'/api/creation-tasks/{task_id}/audio/artifacts/{original.id}'
                preview_media = {'before': {'ref': original_ref, 'label': '当前旁白', 'detail': '当前版本的完整旁白', 'audio_url': before_url}, 'after': {'ref': source_ref, 'label': '本次电脑录音', 'detail': '确认后替换整条旁白；画面时长不变', 'audio_url': f'/api/creation-tasks/{task_id}/audio/artifacts/{recording.id}'}}
                start = 0.0
            else:
                after = apply_audio_edit(before, payload, round(plan.duration_seconds * 1000))
                service.validate(workspace_id, task_id, after, plan.duration_seconds, draft_only=draft_only, actor=actor, context=context, job=job)
                start, end = audio_edit_preview_range(before, after, payload, plan.duration_seconds)
                previews = []
                if not draft_only:
                    for graph in (before, after):
                        record = service.publish(workspace_id, task_id, kind='audio_preview', parameters={'audio_timeline': graph.model_dump(), 'start_seconds': start, 'end_seconds': end}, input_refs=list(dict.fromkeys((clip.source_ref for track in graph.tracks for clip in track.clips))), operation='audio_mix', media_input={'audio_timeline': graph.model_dump(), 'duration_seconds': plan.duration_seconds, 'normalize_voice_sources': plan.narration_linkage_created_audio_timeline, 'start_seconds': start, 'end_seconds': end}, execution=(context, job, event, actor))
                        previews.append(record)
                preview_media = {**{side: {'ref': f'timeline:{payload.base_version_id}', 'label': label, 'detail': '只更新音轨草稿；明确试听或生成时才处理音频。'} for side, label in (('before', '当前音轨'), ('after', '修改后音轨'))}, **{side: {'ref': record.logical_ref, 'label': '修改前' if side == 'before' else '音轨修改后', 'detail': f'试听 {start:.1f}–{end:.1f} 秒；完整音轨在确认后生效', 'audio_url': f'/api/creation-tasks/{task_id}/audio/artifacts/{record.id}'} for side, record in zip(('before', 'after'), previews)}}
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            if isinstance(exc, AudioEditRejected):
                raise
            detail = str(exc) if isinstance(exc, ValueError) else '本地音频预览失败，请检查源文件、FFmpeg 与外接盘'
            raise AudioEditRejected(status_code=422, detail=detail) from exc
        if self.repository.task_records.get_creation_task(workspace_id, task_id).current_timeline_version_id != payload.base_version_id:
            raise AudioEditRejected(status_code=409, detail='试听生成期间时间线发生变化，请重新预览')
        base = self.montage_edits.ensure_montage_base(workspace_id, task, plan, source=source)
        intent = self.repository.edit_records.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, raw_command=payload.raw_command, context_stage='montage', selected_object_refs=[f'audio:{payload.target_id}'], playhead_ms=round(start * 1000), intent_type='edit_audio', scope={'kind': 'audio', 'target_id': payload.target_id}, constraints={'preserve_video': True, 'audio_source_facts': self.source_facts(task, before, after, actor)}, confidence=1, status='interpreted'), source=source)
        labels = {'add': '添加音频片段', 'update': '修改音频片段', 'delete': '删除音频片段（保留时间空隙）', 'split': '分割音频片段', 'duplicate': '复制音频片段', 'track': '修改音轨设置', 'replace_narration': '用本机录音替换整条旁白'}
        summary = labels[payload.action]
        if payload.action == 'add' and any((track.id == payload.target_id and track.role == 'original' for track in before.tracks)):
            summary = '添加原声，与现有音轨混合；画面和配音不变'
        proposal = self.repository.edit_records.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, intent_id=intent.id, summary=summary, target_refs=[f'audio:{payload.target_id}'], operation={'operation_type': 'EditAudioTimeline', 'payload': {'base_version_id': base.id, 'audio_timeline': after.model_dump(), 'edit': edit.model_dump(), 'expected_audio_timeline': before.model_dump(), **({'recording_duration_ms': measured_ms, 'recording_sha256': recording.sha256, 'recording_file_signature': recording_file_signature} if payload.action == 'replace_narration' else {})}}, invalidated_refs=logical_montage_output_refs(plan), preview=preview_media, external_effects=['新录音使用原画面与字幕时轴；确认后旧成片失效，需核对字幕与重新渲染。'] if payload.action == 'replace_narration' else []))
        return AudioEditPreviewResult(intent, proposal)

    def source_facts(self, task, before, after, actor):
        command = EditCommand(task.workspace_id,task.id,self.identity(actor),task.current_timeline_version_id,
            'EditAudioTimeline',{'base_version_id':task.current_timeline_version_id})
        verifier = EditSourceVerifier(self.repository,identity=command.actor_identity,audio_service=self.audio,audio_actor=actor)
        facts = {item['reference']:item for graph in (before,after) for item in verifier.audio_facts(command,graph)}
        return [facts[key] for key in sorted(facts)]

    def confirm(self, task, proposal, actor, source=None):
        payload = proposal.operation['payload']
        if task.current_timeline_version_id != payload.get('base_version_id'):
            raise AudioEditRejected(409,'时间线已经变化，不能确认旧的音频提案')
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id,task.id))
        before = effective_audio_timeline(plan)
        if before.model_dump() != payload.get('expected_audio_timeline'):
            raise AudioEditRejected(409,'音频提案不再适用，请检查源文件并重新预览')
        edit = AudioEditRequest.model_validate(payload['edit'])
        after = AudioTimeline.model_validate(payload['audio_timeline'])
        intent = self.repository.edit_records.get_edit_intent(task.workspace_id,proposal.intent_id)
        expected = intent.constraints.get('audio_source_facts')
        if expected is None:
            raise AudioEditRejected(409,'原音频提案未绑定完整来源，请重新预览')
        command = EditCommand(task.workspace_id,task.id,self.identity(actor),task.current_timeline_version_id,
            'EditAudioTimeline',{'base_version_id':task.current_timeline_version_id,'audio_edit':edit.model_dump(),**payload},source)
        before_refs = {clip.source_ref for track in before.tracks for clip in track.clips}
        command = replace(command,input_source_facts=[fact for fact in expected if fact['reference'] in before_refs],
            affected_clip_ids=(),affected_source_facts=[],affected_clip_facts=[])
        verifier = EditSourceVerifier(self.repository,identity=command.actor_identity,audio_service=self.audio,audio_actor=actor)
        try:
            if edit.action == 'replace_narration':
                self.verify_recording(task,plan,payload,edit,after,actor)
                changed = deepcopy(plan);changed.audio_timeline=after
            else:
                plan.audio_timeline=before
                changed=apply_audio_metadata(task,plan,command,validate_sources=lambda graph,duration:
                    self.audio.validate(task.workspace_id,task.id,graph,duration,draft_only=True,actor=actor))
        except (KeyError,ValueError,OSError,subprocess.SubprocessError) as exc:
            if isinstance(exc,AudioEditRejected):raise
            raise AudioEditRejected(409,'音频提案不再适用，请检查源文件并重新预览') from exc
        prepare_changed_montage(changed,message='音轨已更新，可以试听或重新渲染成片。')
        base=self.repository.timeline_records.get_timeline_version(task.workspace_id,task.current_timeline_version_id)
        guard=AudioEditSourceGuard(self,task,proposal,actor,before,expected,verifier)
        return self.montage_edits.persist_montage_edit(workspace_id=task.workspace_id,task=task,proposal=proposal,
            base_version=base,updated_plan=changed,operation_type='EditAudioTimeline',operation_payload=payload,
            proposal_source_command=command,proposal_source_verifier=guard)

    def verify_recording(self, task, plan, payload, edit, after, actor):
        if self.execution_mode == 'scheduled':
            raise AudioEditRejected(503,'电脑录音校验尚未接入整机许可')
        if any(clip.person_render_id for clip in plan.clips):
            raise ValueError('当前人物口型依赖旧配音，请先移除或重做口型')
        source_ref=edit.values.get('source_ref')
        if not isinstance(source_ref,str):raise ValueError('录音引用无效')
        recording,measured_ms=self.audio.recording(task.workspace_id,task.id,source_ref,actor=actor)
        stat=self.audio.artifact_store.resolve_file(recording.logical_ref).stat()
        duration_ms=round(plan.duration_seconds*1000)
        if (recording.sha256 != payload.get('recording_sha256') or
                [stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns] != payload.get('recording_file_signature') or
                measured_ms != payload.get('recording_duration_ms') or not duration_ms-20 <= measured_ms <= duration_ms+250 or
                apply_audio_edit(effective_audio_timeline(plan),edit,duration_ms).model_dump() != after.model_dump()):
            raise ValueError('录音或旁白替换提案已变化，请重新预览')

    def original_clip(self, workspace_id, task_id, payload, actor, *, draft_only, context=None, job=None, event=None, source=None):
        task=self.repository.task_records.get_creation_task(workspace_id,task_id)
        plan,clip,track,start,end,reference=self.original_inputs(task,payload)
        if context is None:
            raise TaskMediaRequired('audio_stretch')
        source_start,source_end=round(clip.source_start_seconds*1000),round(clip.source_end_seconds*1000)
        rate=(source_end-source_start)/(end-start)
        try:
            record=self.audio.publish(workspace_id,task_id,kind='audio_source',
                parameters={'action':'extract_clip_original','clip_id':clip.id,'source_start_ms':source_start,
                    'source_end_ms':source_end,'rate':rate,'duration_ms':end-start},input_refs=[reference],
                operation='audio_stretch',media_input={'source_start_ms':source_start,'source_end_ms':source_end,
                    'rate':rate,'duration_ms':end-start},execution=(context,job,event,actor))
        except (ValueError,OSError,subprocess.SubprocessError) as exc:
            raise AudioEditRejected(422,str(exc) if isinstance(exc,ValueError) else '无法提取原声，请检查素材是否包含音轨、FFmpeg和外接盘。') from exc
        edit=AudioEditRequest(base_version_id=task.current_timeline_version_id,action='add',target_id=track.id,
            raw_command=payload['raw_command'],values={'source_ref':record.logical_ref,'name':'镜头原声',
                'source_start_ms':0,'source_end_ms':end-start,'timeline_start_ms':start})
        return self.preview(workspace_id,task_id,edit,actor,draft_only=draft_only,context=context,job=job,event=event,source=source)

    def original_inputs(self, task, payload):
        refs=payload['selected_object_refs']
        if len(refs)!=1 or not refs[0].startswith('clip:'):
            raise AudioEditRejected(422,'请先选择一个镜头，再提取原声。')
        plan=creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(task.workspace_id,task.id))
        clip=next((item for item in plan.clips if item.id==refs[0][5:]),None)
        if clip is None:raise AudioEditRejected(404,'所选镜头不存在。')
        if clip.person_render_id:raise AudioEditRejected(409,'生成口型片段使用的是配音；提取真实原声请先选择原视频镜头。')
        tracks=[track for track in effective_audio_timeline(plan).tracks if track.role=='original']
        if len(tracks)!=1:raise AudioEditRejected(409,'需要唯一的原声音轨才能添加镜头原声。')
        track=tracks[0]
        if clip.locked or track.locked:raise AudioEditRejected(409,'镜头或原声音轨已锁定，请先解锁。')
        if track.muted:raise AudioEditRejected(409,'原声音轨已静音，请先取消静音再预览。')
        start,end=round(clip.timeline_start_seconds*1000),round(clip.timeline_end_seconds*1000)
        if any(item.timeline_start_ms<end and item.timeline_start_ms+item.duration_ms>start for item in track.clips):
            raise AudioEditRejected(409,'该范围已有原声，请编辑现有音频片段，避免重复叠加。')
        self.repository.resolve_clip_media_reference(task.workspace_id,clip.asset_id,clip.source_path)
        reference=self.repository.workspace_media_reference_for_asset(task.workspace_id,clip.asset_id)
        return plan,clip,track,start,end,reference

    def execute(self, job, event, actor, context):
        arguments=event['arguments'];operation=event['operation']
        source=arguments.get('_typed_source')
        if source is not None:
            source={**source,'media_job_id':job.id,'actor_identity':actor.identity_payload()}
        draft=arguments.get('_draft',False)
        if operation=='command_edit_preview' and re.fullmatch(r'(?:提取|添加|保留)(?:(?:当前|这个|所选)镜头的?)?原声[。！]?',arguments['payload']['raw_command'].strip()):
            result=self.original_clip(job.workspace_id,job.task_id,arguments['payload'],actor,draft_only=draft,
                context=context,job=job,event=event,source=source)
        else:
            task=self.repository.task_records.get_creation_task(job.workspace_id,job.task_id)
            edit=self.edit_from_arguments(task,operation,arguments)
            result=self.preview(job.workspace_id,job.task_id,edit,actor,draft_only=draft,context=context,job=job,event=event,source=source)
        return {'intent':asdict(result.intent),'proposal':asdict(result.proposal)}

    def validate_result(self, job, event, result, context):
        from app.creation.audio_ingress_use_case import AudioIngressUseCase
        owner=AudioIngressUseCase(self.repository,self.audio,self.audio.temporary_root/'audio-waveforms')
        return owner.validate_result(job,event,result,context)

    def seal(self, workspace_id, task_id, operation, arguments, actor, budget):
        task=self.repository.task_records.get_creation_task(workspace_id,task_id)
        source=arguments.get('_typed_source')
        version=self.repository.timeline_records.get_timeline_version(workspace_id,task.current_timeline_version_id) if task.current_timeline_version_id else None
        if source is not None and (source['workspace_id'],source['task_id'],source['member_id'],source['expected_timeline_version_id'],source['expected_timeline_sequence']) != (
                workspace_id,task_id,actor.user_id or actor.member_id,task.current_timeline_version_id,version.sequence if version else 0):
            raise ValueError('封存的类型化编辑输入或成员已失效')
        plan=creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id,task_id))
        before=effective_audio_timeline(plan);graphs=[before]
        original=(operation=='command_edit_preview' and re.fullmatch(r'(?:提取|添加|保留)(?:(?:当前|这个|所选)镜头的?)?原声[。！]?',arguments['payload']['raw_command'].strip()) is not None)
        if not original:
            edit=self.edit_from_arguments(task,operation,arguments)
            if edit.base_version_id!=task.current_timeline_version_id:raise ValueError('时间线已变化，请重新安排当前要求')
            if edit.action=='replace_narration':edit=edit.model_copy(update={'values':{**edit.values,'source_end_ms':round(plan.duration_seconds*1000)}})
            graphs.append(apply_audio_edit(before,edit,round(plan.duration_seconds*1000)))
        uses=Counter()
        for graph in graphs:
            audible=Counter(clip.source_ref for track in graph.tracks if not track.muted for clip in track.clips if not clip.muted)
            for ref,count in audible.items():uses[ref]=max(uses.get(ref,0),count)
        references=set(uses)
        if original:
            _plan,clip,_track,_start,_end,reference=self.original_inputs(task,arguments['payload'])
            references.add(reference)
        files,inputs=[],[]
        for ref in sorted(references):
            path=self.audio.source(workspace_id,task_id,ref,actor=actor);stat=path.stat()
            files.append([ref,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns])
            inputs.append({'reference':ref,'path':str(path.resolve()),'size_bytes':stat.st_size,'uses':uses.get(ref,1),
                **self.audio.source_metadata(workspace_id,task_id,ref)})
        snapshot={k:v for k,v in creation_task_montage_plan_to_payload(plan).items() if k not in {'status','message','output_path','subtitle_path','updated_at'}}
        facts={'base_version_id':task.current_timeline_version_id,'script_id':task.confirmed_script_id,
            'script_digest':self.repository.confirmed_creation_task_script_digest(workspace_id,task_id,task.confirmed_script_id) if task.confirmed_script_id else None,
            'plan_id':task.plan_id,'plan_digest':media_digest(asdict(self.repository.plan_records.get_creation_plan(workspace_id,task.plan_id))),
            'montage':snapshot,'files':files,'narration_cache_key':task.narration_cache_key}
        if original:
            if budget is None:
                raise ValueError('CPU 音频处理预算尚未标定，未启动媒体')
            if not inputs:
                raise ValueError('没有固定的音频输入，未启动媒体')
            for source in inputs:
                for field, cap in (('duration_seconds', budget.max_source_duration_seconds),
                                   ('sample_rate', budget.max_sample_rate), ('channels', budget.max_channels)):
                    value = source[field]
                    if value is not None and (not isinstance(value, (int, float)) or value <= 0 or value > cap):
                        raise ValueError('已保存的音频源规格超出标定预算')
            start = arguments['payload'].get('start_seconds', 0)
            end = arguments['payload'].get('end_seconds', plan.duration_seconds)
            if not 0 <= start < end <= plan.duration_seconds:
                raise ValueError('试听范围必须位于时间线内')
            output_duration = end - start
            resources = {'kind': 'task_audio', 'execution_mode': 'cpu_ffmpeg',
                'input_policy': 'probe_then_bounded_decode', 'inputs': inputs,
                'input_count': sum(item['uses'] for item in inputs),
                'input_bytes': sum(item['size_bytes'] * item['uses'] for item in inputs),
                'source_duration_limit_seconds': budget.max_source_duration_seconds,
                'output_duration_limit_seconds': output_duration,
                'source_sample_rate_limit': budget.max_sample_rate, 'source_channels_limit': budget.max_channels,
                'output_sample_rate_limit': 48000, 'output_channels_limit': 2,
                'max_output_bytes': budget.max_output_bytes, 'threads': budget.max_threads,
                'timeout_seconds': budget.max_timeout_seconds}
            if (resources['input_count'] > budget.max_input_count or resources['input_bytes'] > budget.max_input_bytes
                    or output_duration > budget.max_output_duration_seconds
                    or resources['output_sample_rate_limit'] > budget.max_sample_rate):
                raise ValueError('当前音频处理输入或输出超出标定预算')
            return facts, resources
        if not inputs:
            raise ValueError('没有固定的音频输入，未启动媒体')
        resources=audio_mix_resources(inputs,graphs,plan.duration_seconds,
            normalize_voice_sources=plan.narration_linkage_created_audio_timeline,budget=budget)
        resources['max_output_bytes']=budget.max_output_bytes
        return facts,resources

class AudioEditSourceGuard:
    """Final caller guard delegates source authority to the existing verifier/audio owner."""
    def __init__(self,case,task,proposal,actor,before,expected,verifier):
        self.case,self.task,self.proposal,self.actor=case,task,proposal,actor
        self.before,self.expected,self.verifier=before,expected,verifier

    def verify_change(self,command,after_payload,connection):
        self.verifier.verify_change(command,after_payload,connection)
        after=creation_task_montage_plan_from_payload(after_payload)
        current=creation_task_montage_plan_from_payload(self.case.repository.get_creation_task_montage_plan(
            command.workspace_id,command.task_id,connection=connection))
        if self.case.source_facts(self.task,effective_audio_timeline(current),after.audio_timeline,self.actor) != self.expected:
            raise EditConflict('预览后的音频来源或许可已变化，请重新预览')
        edit=AudioEditRequest.model_validate(self.proposal.operation['payload']['edit'])
        if edit.action=='replace_narration':
            self.case.verify_recording(self.task,current,self.proposal.operation['payload'],edit,after.audio_timeline,self.actor)
