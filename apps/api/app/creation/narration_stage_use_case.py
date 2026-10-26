from __future__ import annotations
from app.production import MediaAuthorityError
from app.narration import NarrationArtifact, _pronunciation_dictionary, _pronunciation_version
from app.production import NarrationArtifactRecord
from app.production import NarrationSegment
from app.production import CreationTask
from app.montage import CreationTaskMontagePlan
from app.voice_clone import VoiceCloneError
from app.visual_effects import clip_playback_rate
from app.montage import creation_task_montage_plan_from_payload
from app.montage import creation_task_montage_plan_to_payload
from app.audio_timeline import rebind_narration_audio
from app.models import utc_now
from typing import TYPE_CHECKING
from dataclasses import replace
from pathlib import Path
import hashlib
from app.creation.editing.metadata_use_case import refresh_montage_review_state, LegacyMontageReference, InvalidMontage
if TYPE_CHECKING:
    from app.creation_execution import CreationStageGrantContext

class NarrationStageRejected(Exception):

    def __init__(self, *, status_code, detail):
        self.status_code, self.detail = (status_code, detail)
        super().__init__(f'{status_code}: {detail}')

def stage_failure(*, status_code: int, error: str, reason: str, impact: str, repair_action: str, retryable: bool) -> NarrationStageRejected:
    return NarrationStageRejected(status_code=status_code, detail={'error': error, 'reason': reason, 'impact': impact, 'repair_action': repair_action, 'retryable': retryable})

class NarrationStageUseCase:

    def __init__(self, repository, generator, metadata, narration_budget):
        self.repository = repository
        self.generator = generator
        self.metadata = metadata
        self.narration_budget = narration_budget

    def prepare_segment(self, workspace_id: str, task_id: str, *, script_id: str,
                        section_id: str, grant_context: CreationStageGrantContext) -> tuple[NarrationArtifactRecord, NarrationSegment, int]:
        """Generate one granted section; the owner commits all returned facts."""
        self._check_preparation_grant(grant_context, f'narration:segment:v1:{section_id}:',
            workspace_id, task_id, script_id)
        task, plan, script, inputs = self._preparation_inputs(workspace_id, task_id, script_id)
        section = next((item for item in script.sections if item.id == section_id), None)
        if section is None:
            raise ValueError('单段配音不属于当前确认文案')
        segment_id = self.segment_id(task.id, script.id, section.id)
        try:
            previous = self.repository.get_narration_segment(workspace_id, task.id, segment_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.script_id != script.id or previous.source_section_id != section.id:
                raise ValueError('配音片段身份与当前文案段落不一致')
            if previous.status == 'ready':
                raise ValueError('已完成配音片段须先核对复用，不能重复生成')
        reference_path = self.repository.resolve_workspace_media_reference(
            workspace_id, inputs.voice.reference_audio_path, require_registered=True)
        artifact = self.generator.generate_for_creation_task(
            workspace_id=workspace_id, task_id=task.id, script_id=script.id,
            narration_text=section.content, language={'zh-CN': 'zh', 'en-US': 'en', 'zh-CN/en-US': 'zh-en'}[script.language],
            max_duration_seconds=plan.duration_target_seconds, voice_identity=inputs.voice,
            pronunciation_entries=inputs.pronunciation_entries, resolved_reference_audio_path=reference_path,
            repository=self.repository, viewer_user_id=task.owner_user_id, segment_id=segment_id,
            grant_context=grant_context)
        record, output_bytes = self._prepared_artifact(workspace_id, task, script, artifact, grant_context)
        now = utc_now()
        segment = NarrationSegment(
            id=segment_id, workspace_id=workspace_id, task_id=task.id, script_id=script.id,
            source_section_id=section.id, text=section.content,
            audio_reference=self.repository.narration_artifact_reference(record.cache_key),
            duration_seconds=record.duration_seconds, sample_rate=record.sample_rate,
            model_id=record.model_id, voice_profile_id=inputs.voice.id, voice_version=inputs.voice.version,
            language=artifact.language, pronunciation_version=record.pronunciation_version, status='ready',
            created_at=previous.created_at if previous is not None else now, updated_at=now)
        return record, segment, output_bytes

    def prepare_revision(self, task, command, *, grant_context):
        """Prepare one unadopted rewrite under its own original device grant."""
        grant_context.check()
        grant = grant_context.grant
        job = self.repository.get_job_run(task.workspace_id, grant.owner_id)
        from app.creation.segment_production_use_case import revision_execution_key
        from app.profile_reference_media import ProfileReferenceInput
        with self.repository._connect() as conn:
            saved = self.repository.creation_narration_reference(job.workspace_id, job.id, connection=conn)
        if saved is None:
            from app.device_scheduler import RecoveryBlocked
            raise RecoveryBlocked('original_source_content_proof_missing: 原局部配音缺少来源封存')
        expected_key = revision_execution_key(job.input_digest, ProfileReferenceInput(**saved['reference']))
        if (job.task_id != task.id or job.kind != 'creation_task_segment_revision'
                or grant.unit_key != expected_key):
            raise PermissionError('局部改写配音许可与原作业不一致')
        plan = self.repository.get_creation_plan(task.workspace_id,task.plan_id)
        script = self.repository.effective_creation_script(task.workspace_id,task.id)
        inputs = self.repository.resolve_creation_task_narration_inputs(workspace_id=task.workspace_id,
            task_id=task.id,voice_profile_id=plan.selected_voice.profile_id,voice_version=plan.selected_voice.version)
        previous = self.repository.get_narration_segment(task.workspace_id,task.id,
            self.segment_id(task.id,script.id,command.section_id))
        if previous.status!='ready' or (previous.voice_profile_id,previous.voice_version)!=(inputs.voice.id,inputs.voice.version):
            raise ValueError('原段落配音或声音版本已变化，尚未重生成局部改写')
        artifact = self.generator.generate_for_creation_task(workspace_id=task.workspace_id,task_id=task.id,
            script_id=script.id,narration_text=command.replacement_text,
            max_duration_seconds=plan.duration_target_seconds,voice_identity=inputs.voice,
            pronunciation_entries=inputs.pronunciation_entries,
            resolved_reference_audio_path=self.repository.resolve_workspace_media_reference(
                task.workspace_id,inputs.voice.reference_audio_path,require_registered=True),
            repository=self.repository,viewer_user_id=task.owner_user_id,
            segment_id=self.segment_id(task.id,script.id,command.section_id),generation_identity=job.input_digest,
            language={'zh-CN':'zh','en-US':'en','zh-CN/en-US':'zh-en'}[script.language],grant_context=grant_context)
        record, size = self._prepared_artifact(task.workspace_id,task,script,artifact,grant_context)
        segment = replace(previous,text=command.replacement_text,
            audio_reference=self.repository.narration_artifact_reference(record.cache_key),
            duration_seconds=record.duration_seconds,sample_rate=record.sample_rate,model_id=record.model_id,
            pronunciation_version=record.pronunciation_version,status='ready',updated_at=utc_now())
        return record, segment, size

    def prepare_merge(self, workspace_id: str, task_id: str, *, script_id: str,
                      input_references: list[str],
                      grant_context: CreationStageGrantContext) -> tuple[NarrationArtifactRecord, int]:
        """Combine only the bounded inputs declared by this original Job's permit."""
        self._check_preparation_grant(grant_context, 'narration:merge:v1:', workspace_id, task_id, script_id)
        with self.repository.creation_execution_read() as conn:
            snapshot = self.repository.read_creation_execution_snapshot(workspace_id, task_id, connection=conn)
            task, plan, script = snapshot.task, snapshot.plan, snapshot.script_header
            if task.stage != 'narration' or script.id != script_id or script.selection_status != 'confirmed':
                raise ValueError('配音单元不匹配当前任务阶段或确认文案')
            if plan.selected_voice is None:
                raise MediaAuthorityError(reason='voice_identity_missing', impact='当前创作任务未生成配音，未调用声音模型。',
                    repair_action='重新预检并选择已授权且支持目标语言的声音版本。')
            inputs = self.repository.resolve_creation_task_narration_inputs(workspace_id=workspace_id, task_id=task.id,
                voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
            pronunciation_version = _pronunciation_version(_pronunciation_dictionary(
                entries=inputs.pronunciation_entries, workspace_id=workspace_id,
                person_profile_id=inputs.voice.person_profile_id, task_id=task.id))
            for _ in self._merge_segments(task, script, inputs.voice, pronunciation_version, connection=conn):
                pass
        language = {'zh-CN': 'zh', 'en-US': 'en', 'zh-CN/en-US': 'zh-en'}[script.language]
        if not input_references or len(set(input_references)) != len(input_references):
            raise ValueError('合并单元须声明非空且唯一的实际音频输入')
        artifacts = []
        for reference in input_references:
            if not reference.startswith('narration://artifact/'):
                continue
            audio_path = self.repository.resolve_authorized_narration_audio_reference(workspace_id, reference)
            record = self.repository.get_narration_artifact(
                workspace_id, reference.removeprefix('narration://artifact/'))
            if (record is None or record.task_id != task.id or record.script_id != script.id
                    or record.voice_profile_id != inputs.voice.id
                    or record.language != language or record.pronunciation_version != pronunciation_version):
                raise ValueError('合并片段缺少匹配的配音产物')
            artifacts.append(NarrationArtifact(
                task_id=task.id, script_id=script.id, audio_path=str(audio_path),
                duration_seconds=record.duration_seconds, sample_rate=record.sample_rate,
                model_id=record.model_id, voice_profile_id=record.voice_profile_id,
                cache_key=record.cache_key, language=record.language, pronunciation_version=record.pronunciation_version))
        with self.repository._connect() as conn:
            row = conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?',
                (grant_context.grant.request_id,)).fetchone()
        import json
        resources = json.loads(row['resources_json'])
        if (resources.get('execution_mode') != 'cpu_pcm_wav_assembly'
                or input_references != [item['reference'] for item in resources['inputs']]):
            raise PermissionError('配音组装输入不属于原PCM许可')
        grant_context.check()
        receipt = grant_context.run_pcm_assembly(resources)
        record = None
        if receipt['finalized']:
            with self.repository.creation_execution_read() as conn:
                record = self.pcm_record(task, resources, receipt, self._merge_segments(
                    task, script, inputs.voice, pronunciation_version, connection=conn))
        return record,receipt['generated_bytes'],receipt

    def _merge_segments(self, task, script, voice, pronunciation_version, *, connection):
        if not script.sections_total_count:
            raise ValueError('合并须包含当前确认文案的全部唯一配音片段')
        language = {'zh-CN': 'zh', 'en-US': 'en', 'zh-CN/en-US': 'zh-en'}[script.language]
        for section, segment in self.repository.iter_creation_narration_sections(task, script, connection=connection):
            if (segment is None or segment.status != 'ready' or segment.text != section.content
                    or segment.script_id != script.id or segment.language != language
                    or segment.pronunciation_version != pronunciation_version
                    or (segment.voice_profile_id, segment.voice_version) != (voice.id, voice.version)
                    or not segment.audio_reference):
                raise ValueError('合并片段尚未就绪或不匹配当前文案与声音版本')
            yield segment

    def pcm_record(self, task, resources, receipt, segments):
        if not receipt['finalized'] or receipt['source_digest']!=resources['merge_source_digest']:
            raise ValueError('未封存的PCM工作文件不能成为配音产物')
        import hashlib
        iterator = iter(segments)
        first = next(iterator)
        digest = hashlib.sha256(first.audio_reference.removeprefix('narration://artifact/').encode())
        for segment in iterator:
            digest.update(b'\n')
            digest.update(segment.audio_reference.removeprefix('narration://artifact/').encode())
        cache_key = digest.hexdigest()
        return NarrationArtifactRecord(id=f'narration_{cache_key[:16]}',workspace_id=task.workspace_id,
            task_id=task.id,script_id=resources['script_id'],voice_profile_id=first.voice_profile_id,cache_key=cache_key,
            audio_path=receipt['work_path'],duration_seconds=receipt['pcm_bytes']/48000,
            sample_rate=24000,model_id=first.model_id,language=first.language,
            pronunciation_version=first.pronunciation_version)

    @staticmethod
    def segment_id(task_id: str, script_id: str, section_id: str) -> str:
        return f"narration_segment_{hashlib.sha256(f'{task_id}:{script_id}:{section_id}'.encode('utf-8')).hexdigest()[:20]}"

    def _check_preparation_grant(self, context, prefix, workspace_id, task_id, script_id):
        if context is None or context.grant.owner_type != 'creation_job' or not context.grant.unit_key.startswith(prefix):
            raise PermissionError('配音准备必须使用对应原作业单元的设备许可')
        context.check()
        grant = context.grant
        if grant.workspace_id != workspace_id:
            raise PermissionError('配音准备工作区与原作业许可不一致')
        job = self.repository.get_job_run(workspace_id, grant.owner_id)
        event = self.repository.get_task_event(workspace_id, f'event_{job.id}_start')
        execution_input = self.repository.creation_execution_input(workspace_id, job.id)
        if (job.kind != 'creation_task_execution' or job.workspace_id != workspace_id or job.task_id != task_id
                or job.input_digest != grant.input_digest
                or (event.workspace_id, event.task_id, event.job_id) != (workspace_id, task_id, job.id)
                or execution_input.get('script_id') != script_id or execution_input.get('input_digest') != job.input_digest):
            raise PermissionError('配音准备任务、文案或输入摘要与原作业许可不一致')

    def _preparation_inputs(self, workspace_id, task_id, script_id):
        task = self.task_or_missing(workspace_id, task_id)
        if task.stage != 'narration' or task.confirmed_script_id != script_id:
            raise ValueError('配音单元不匹配当前任务阶段或确认文案')
        plan = self.plan_for_task(workspace_id, task)
        script = self.repository.effective_creation_script(workspace_id, task_id)
        if script.selection_status != 'confirmed':
            raise ValueError('配音单元只能使用已确认文案')
        if plan.selected_voice is None:
            raise MediaAuthorityError(reason='voice_identity_missing', impact='当前创作任务未生成配音，未调用声音模型。',
                repair_action='重新预检并选择已授权且支持目标语言的声音版本。')
        inputs = self.repository.resolve_creation_task_narration_inputs(
            workspace_id=workspace_id, task_id=task.id,
            voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
        return task, plan, script, inputs

    @staticmethod
    def _prepared_artifact(workspace_id, task, script, artifact, context):
        context.check()
        output_bytes = Path(artifact.audio_path).stat().st_size
        if output_bytes <= 0 or output_bytes > context.max_output_bytes:
            raise ValueError('配音单元产物为空或超过剩余字节预算，尚未提交')
        record = NarrationArtifactRecord(
            id=f'narration_{artifact.cache_key[:16]}', workspace_id=workspace_id, task_id=task.id, script_id=script.id,
            voice_profile_id=artifact.voice_profile_id, cache_key=artifact.cache_key, audio_path=artifact.audio_path,
            duration_seconds=artifact.duration_seconds, sample_rate=artifact.sample_rate, model_id=artifact.model_id,
            language=artifact.language, pronunciation_version=artifact.pronunciation_version)
        return record, output_bytes

    def generate_segment_edit(self, *, workspace_id: str, task: CreationTask, segment_id: str, delivery_instruction: str | None, generation_identity: str, context: CreationStageGrantContext) -> tuple[NarrationSegment, NarrationArtifactRecord]:
        context.check()
        budget = self.narration_budget
        if budget is None:
            raise ValueError('局部配音专属预算未标定')
        plan = self.plan_for_task(workspace_id, task)
        current_montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        if current_montage.audio_timeline is not None:
            reference = current_montage.narration_audio_path or ''
            duration_ms = round(current_montage.duration_seconds * 1000)
            rebind_narration_audio(current_montage.audio_timeline, reference, reference, duration_ms, duration_ms)
        if task.confirmed_script_id is None:
            raise ValueError('当前任务没有已确认文案')
        script = self.repository.get_creation_task_script(workspace_id, task.confirmed_script_id)
        target = self.repository.get_narration_segment(workspace_id, task.id, segment_id)
        if target.script_id != script.id:
            raise ValueError('配音片段不属于当前确认文案')
        if plan.selected_voice is None:
            raise ValueError('当前任务没有可执行的声音版本')
        inputs = self.repository.resolve_creation_task_narration_inputs(workspace_id=workspace_id, task_id=task.id, voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
        reference = self.repository.profile_reference_input(inputs.voice, viewer_user_id=task.owner_user_id)
        maximum_duration = min(plan.duration_target_seconds, budget.max_duration_seconds)
        generated = self.generator.generate_for_creation_task(workspace_id=workspace_id, task_id=task.id, script_id=script.id, narration_text=target.text, language=target.language, max_duration_seconds=maximum_duration, voice_identity=inputs.voice, pronunciation_entries=inputs.pronunciation_entries, resolved_reference_audio_path=reference.source_path, repository=self.repository, viewer_user_id=task.owner_user_id, grant_context=context, segment_id=target.id, delivery_instruction=delivery_instruction, generation_identity=generation_identity)
        generated_record = NarrationArtifactRecord(id=f'narration_{generated.cache_key[:16]}', workspace_id=workspace_id, task_id=task.id, script_id=script.id, voice_profile_id=generated.voice_profile_id, cache_key=generated.cache_key, audio_path=generated.audio_path, duration_seconds=generated.duration_seconds, sample_rate=generated.sample_rate, model_id=generated.model_id, language=generated.language, pronunciation_version=generated.pronunciation_version)
        updated_target = replace(target, audio_reference=self.repository.narration_artifact_reference(generated_record.cache_key), duration_seconds=generated_record.duration_seconds, sample_rate=generated_record.sample_rate, model_id=generated_record.model_id, voice_profile_id=inputs.voice.id, voice_version=inputs.voice.version, pronunciation_version=generated_record.pronunciation_version, status='ready', updated_at=utc_now())
        context.check()
        return updated_target,generated_record

    def retime_for_segments(self, workspace_id: str, plan: CreationTaskMontagePlan, segments: list[NarrationSegment], narration_reference: str) -> CreationTaskMontagePlan:
        updated = creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(plan))
        duration_by_segment = {segment.id: float(segment.duration_seconds or 0) for segment in segments if segment.status == 'ready'}
        cues_by_segment: dict[str, list[NarrationCue]] = {}
        for cue in updated.cues:
            cues_by_segment.setdefault(cue.narration_segment_id, []).append(cue)
        segment_clocks = []
        old_voice_end = new_voice_end = 0.0
        for segment_id, cues in cues_by_segment.items():
            segment_duration = duration_by_segment.get(segment_id)
            if segment_duration is None or segment_duration <= 0:
                raise ValueError('时间线引用了不可用的配音片段')
            old_total = sum((cue.duration_seconds for cue in cues))
            segment_clocks.append((old_voice_end, old_voice_end + old_total,
                                   new_voice_end, new_voice_end + segment_duration))
            old_voice_end += old_total
            new_voice_end += segment_duration
            cursor = allocated = 0.0
            for cue in cues:
                cursor += cue.duration_seconds
                end = round(segment_duration * cursor / old_total, 3)
                cue.duration_seconds = round(end - allocated, 3)
                allocated = end
        cue_by_id = {cue.id: cue for cue in updated.cues}

        def retimed_boundary(position):
            for old_start, old_end, new_start, new_end in segment_clocks:
                if old_start - 1e-06 <= position <= old_end + 1e-06:
                    return new_start + (position - old_start) * (new_end - new_start) / (old_end - old_start)
            if position >= old_voice_end:
                # Manual picture tails have no speech coverage, even when they
                # inherit the preceding cue ID. Preserve their elapsed time.
                return new_voice_end + position - old_voice_end
            raise ValueError('镜头边界不在原配音时间轴内，无法可靠调整。')

        for index, clip in enumerate(updated.clips):
            cue = cue_by_id.get(clip.cue_id)
            if cue is None:
                raise ValueError('镜头缺少对应的字幕与配音时间段')
            old_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
            playback_rate = clip_playback_rate(clip)
            start = round(retimed_boundary(clip.timeline_start_seconds), 3)
            end = round(retimed_boundary(clip.timeline_end_seconds), 3)
            duration = round(end - start, 3)
            if duration <= 0:
                raise ValueError('新配音时长不足以保留当前镜头边界。')
            if clip.locked and (abs(start - clip.timeline_start_seconds) > 0.001 or abs(duration - old_duration) > 0.001):
                raise ValueError('配音时长变化会修改已锁定镜头，请先解锁受影响镜头')
            if index:
                previous = plan.clips[index - 1]
                original = plan.clips[index]
                if (previous.asset_id, previous.segment_id, previous.cue_id, previous.person_render_id) == (original.asset_id, original.segment_id, original.cue_id, original.person_render_id) and abs(previous.source_end_seconds - original.source_start_seconds) < 0.001:
                    clip.source_start_seconds = updated.clips[index - 1].source_end_seconds
            if clip.person_render_id:
                render = self.repository.get_person_render(workspace_id, plan.task_id, clip.person_render_id)
                source_end = render.duration_seconds if render.status == 'ready' else None
            else:
                asset = self.repository.get_asset(workspace_id, clip.asset_id)
                source_segment = next((item for item in asset.segments if item.id == clip.segment_id), None)
                source_end = source_segment.end_seconds if source_segment else None
            if source_end is None or clip.source_start_seconds + duration * playback_rate > source_end + 1e-06:
                raise ValueError('调整后的配音时长超过当前镜头可用范围，请先替换或放宽该镜头。')
            clip.source_end_seconds = round(clip.source_start_seconds + duration * playback_rate, 3)
            clip.timeline_start_seconds = start
            clip.timeline_end_seconds = end
        updated.duration_seconds = round(retimed_boundary(plan.duration_seconds), 3)
        if updated.audio_timeline is not None:
            updated.audio_timeline = rebind_narration_audio(updated.audio_timeline, plan.narration_audio_path or '', narration_reference, round(old_voice_end * 1000), round(new_voice_end * 1000))
        updated.narration_audio_path = narration_reference
        updated.output_path = None
        updated.subtitle_path = None
        refresh_montage_review_state(updated)
        updated.message = '所选配音片段已更新，镜头范围已同步；请核对字幕时间及人物口型依赖，再重新渲染。'
        return updated

    def prepare_edit(self, workspace_id, task_id, context, proposal_id, resources):
        candidate = self.repository.narration_edit_generated(workspace_id,context.grant.owner_id)
        receipt = context.run_pcm_assembly(resources)
        return self.prepare_edit_adoption(workspace_id,task_id,proposal_id,candidate,resources,receipt),receipt

    def prepare_edit_adoption(self, workspace_id, task_id, proposal_id, candidate, resources, receipt):
        task=self.repository.get_creation_task(workspace_id,task_id)
        proposal=self.repository.get_edit_proposal(workspace_id,proposal_id)
        payload=proposal.operation['payload']
        if candidate is None or candidate['segment_id']!=payload['narration_segment_id']:
            raise ValueError('局部配音缺少同一原Job的生成候选')
        if not receipt['finalized']:
            return None
        target = self.repository.get_narration_artifact(workspace_id,candidate['cache_key'])
        original = self.repository.get_narration_segment(workspace_id,task.id,candidate['segment_id'])
        updated_target = replace(original,audio_reference=self.repository.narration_artifact_reference(target.cache_key),
            duration_seconds=target.duration_seconds,sample_rate=target.sample_rate,model_id=target.model_id,
            pronunciation_version=target.pronunciation_version,status='ready',updated_at=utc_now())
        by_section = {item.source_section_id:item for item in self.repository.list_narration_segments(
            workspace_id,task.id,task.confirmed_script_id)}
        by_section[updated_target.source_section_id]=updated_target
        script = self.repository.get_creation_task_script(workspace_id,task.confirmed_script_id)
        segments = [by_section[section.id] for section in script.sections]
        combined=self.pcm_record(task,resources,receipt,segments)
        key=combined.cache_key
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id,task.id))
        montage = self.retime_for_segments(workspace_id,montage,segments,self.repository.narration_artifact_reference(key))

        def commit(connection):
            current = self.repository.get_creation_task(workspace_id, task_id)
            if current.current_timeline_version_id != payload['base_version_id']:
                raise ValueError('当前时间线已变化，未提交局部配音')
            base = self.repository.get_timeline_version(workspace_id, payload['base_version_id'])
            self.repository.save_narration_artifact(target, connection=connection)
            try:
                return self.metadata.persist_montage_edit(workspace_id=workspace_id, task=current, proposal=proposal, base_version=base, updated_plan=montage, operation_type='RegenerateNarrationSegment', operation_payload=payload, narration_segments=segments, narration_artifact=combined, connection=connection)
            except LegacyMontageReference as exc:
                raise stage_failure(**exc.details) from exc
            except InvalidMontage as exc:
                raise NarrationStageRejected(status_code=422, detail=str(exc)) from exc
            except PermissionError as exc:
                raise NarrationStageRejected(status_code=403, detail=str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise NarrationStageRejected(status_code=409, detail=str(exc)) from exc
        return commit

    def task_or_missing(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise NarrationStageRejected(status_code=404, detail='creation task not found') from exc

    def plan_for_task(self, workspace_id: str, task: CreationTask) -> CreationPlan:
        try:
            plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise NarrationStageRejected(status_code=404, detail='creation task not found') from exc
        if plan.creation_task_id != task.id:
            raise NarrationStageRejected(status_code=404, detail='creation task not found')
        return plan
