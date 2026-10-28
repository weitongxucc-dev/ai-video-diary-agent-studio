"""Five sealed audio actions using the original source, grant and publication owners."""
from dataclasses import asdict, dataclass
import hashlib
from app.production import NarrationArtifactRecord
from app.creation.contracts import NarrationSegment
from collections import Counter
from pathlib import Path
import subprocess

from app.audio_timeline import AudioListenRequest, probe_audio_source
from app.audio_waveform import AudioWaveformError, audio_waveform
from app.creation.editing.metadata_use_case import effective_audio_timeline
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.task_audio import media_digest, audio_mix_resources

AUDIO_INGRESS_ACTIONS = frozenset({'audio_import','recording_save','audio_waveform','audio_listen','narration_waveform'})

class AudioIngressRejected(ValueError):
    """Original finite route rejection facts, with no HTTP dependency."""
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail

@dataclass(frozen=True)
class StagedAudioInput:
    staged_input: str
    filename: str
    size_bytes: int
    source_sha256: str

class AudioIngressUseCase:
    def __init__(self, repository, task_audio, waveform_cache_root: Path):
        if task_audio.repository is not repository:
            raise ValueError('音频输入必须绑定同一仓储负责人')
        self.repository, self.task_audio = repository, task_audio
        self.waveform_cache_root = waveform_cache_root

    def list_recordings(self, task, *, outputs):
        workspace_id, task_id = task.workspace_id, task.id
        if not task.confirmed_script_id:
            return {"items": []}
        service = self.task_audio
        current_narration_refs = set()
        if task.montage_plan_id:
            plan = creation_task_montage_plan_from_payload(
                self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            if plan.script_id == task.confirmed_script_id:
                current_narration_refs = {clip.source_ref for track in effective_audio_timeline(plan).tracks
                                          if track.role == "narration" for clip in track.clips}
        if task.narration_cache_key:
            current_narration = self.repository.get_narration_artifact(
                workspace_id, task.narration_cache_key,
            )
            if (current_narration is not None and current_narration.task_id == task.id
                    and current_narration.script_id == task.confirmed_script_id
                    and current_narration.source_kind == "recorded"
                    and current_narration.source_artifact_ref):
                current_narration_refs.add(current_narration.source_artifact_ref)
        items = []
        for record in reversed(self.repository.execution_records.list_artifact_records(workspace_id, task_id)):
            if record.kind != "task_recording":
                continue
            try:
                _, duration_ms = service.recording(
                    workspace_id, task_id, record.logical_ref,
                )
                if not outputs.published_outputs_current(self.repository.get_job_run(workspace_id, record.producer_job_id)):
                    continue
            except (KeyError, ValueError, FileNotFoundError):
                continue  # Failed or obsolete uploads are never offered as current narration.
            items.append({"source_ref": record.logical_ref, "artifact_id": record.id,
                          "audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{record.id}",
                          "duration_ms": duration_ms, "created_at": record.created_at.isoformat(),
                          "applied": record.logical_ref in current_narration_refs})
        return {"items": items}


    def adopt_recording(self, task, *, script_id, source_section_id, source_ref, outputs):
        workspace_id = task.workspace_id
        if task.stage != "narration" or task.stage_status not in {"not_started", "failed"}:
            raise AudioIngressRejected(409, "当前任务不能首次采用录音")
        if task.confirmed_script_id != script_id:
            raise AudioIngressRejected(409, "文案版本已变化，请重新选择当前录音")
        try:
            script = self.repository.get_creation_task_script(workspace_id, script_id)
        except KeyError as exc:
            raise AudioIngressRejected(404, "当前文案不存在") from exc
        if script.selection_status != "confirmed" or len(script.sections) != 1:
            raise AudioIngressRejected(409, "当前版本有多个文案段，暂不能确定单段录音的对应关系")
        section = script.sections[0]
        if source_section_id != section.id:
            raise AudioIngressRejected(409, "录音对应的文案段已变化，请重新选择")
        script_digest = self.repository.confirmed_creation_task_script_digest(
            workspace_id, task.id, script.id,
        )
        if script_digest is None:
            raise AudioIngressRejected(409, "当前文案未确认或已经变化，不能采用录音")
        try:
            source, duration_ms = self.task_audio.recording(
                workspace_id, task.id, source_ref,
            )
            if not outputs.published_outputs_current(self.repository.get_job_run(workspace_id, source.producer_job_id)):
                raise ValueError("电脑录音已变化或缺少受许可核验回执，请重新录制")
            audio_path = self.task_audio.source(workspace_id, task.id, source.logical_ref)
            cache_seed = f"recorded:{task.id}:{script.id}:{section.id}:{source.logical_ref}:{source.sha256}"
            cache_key = f"recorded_{hashlib.sha256(cache_seed.encode('utf-8')).hexdigest()[:48]}"
            narration_ref = self.repository.narration_artifact_reference(cache_key)
            narration_id = f"narration_segment_{hashlib.sha256(cache_seed.encode('utf-8')).hexdigest()[:20]}"
            language = {"zh-CN": "zh", "en-US": "en", "zh-CN/en-US": "zh-en"}[script.language]
            record = NarrationArtifactRecord(
                id=cache_key,
                workspace_id=workspace_id,
                task_id=task.id,
                script_id=script.id,
                voice_profile_id=None,
                cache_key=cache_key,
                audio_path=str(audio_path),
                duration_seconds=duration_ms / 1000,
                sample_rate=None,
                model_id=None,
                language=language,
                source_kind="recorded",
                source_artifact_ref=source.logical_ref,
                source_sha256=source.sha256,
                source_script_digest=script_digest,
            )
            segment = NarrationSegment(
                id=narration_id,
                workspace_id=workspace_id,
                task_id=task.id,
                script_id=script.id,
                source_section_id=section.id,
                text=section.content,
                audio_reference=narration_ref,
                duration_seconds=duration_ms / 1000,
                sample_rate=None,
                model_id=None,
                voice_profile_id=None,
                voice_version=None,
                language=language,
                pronunciation_version="",
                status="ready",
                source_kind="recorded",
                source_artifact_ref=source.logical_ref,
                source_sha256=source.sha256,
                source_script_digest=script_digest,
            )
            task, record = self.repository.complete_creation_task_narration(record, segments=[segment])
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise AudioIngressRejected(409, f"当前录音未采用：{exc}") from exc
        return task, record


    def execute(self, job, event, actor, context):
        action = event['operation']
        if action == 'audio_import':
            return self.import_audio(job, event, actor, context)
        if action == 'recording_save':
            return self.save_recording(job, event, actor, context)
        if action == 'audio_waveform':
            return self.audio_waveform(job, event, actor, context)
        if action == 'audio_listen':
            return self.listen(job, event, actor, context)
        if action == 'narration_waveform':
            return self.narration_waveform(job, event, actor, context)
        raise ValueError('不是本音频输入用例的动作')

    def check_public_input(self, workspace_id, task_id, action, arguments, actor):
        task = self.repository.get_creation_task(workspace_id, task_id)
        if action == 'audio_listen':
            if task.current_timeline_version_id != arguments['payload'].get('base_version_id'):
                raise AudioIngressRejected(409, '时间线已经变化，请刷新后试听')
            try:
                plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
                effective_audio_timeline(plan)
            except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
                raise AudioIngressRejected(422, str(exc) if isinstance(exc, ValueError) else '试听生成失败，请检查音频和外接盘') from exc
        elif action == 'audio_waveform':
            try:
                plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
                clip = next(c for t in effective_audio_timeline(plan).tracks for c in t.clips if c.id == arguments['clip_id'])
                self.task_audio.source(workspace_id, task_id, clip.source_ref, actor=actor)
            except (KeyError, StopIteration, ValueError, AudioWaveformError) as exc:
                raise AudioIngressRejected(422, '无法读取所选音频的波形，请检查源文件') from exc
        elif action == 'narration_waveform':
            artifact = self.repository.get_narration_artifact(workspace_id, task.narration_cache_key) if task.narration_cache_key else None
            if artifact is None or artifact.task_id != task.id:
                raise AudioIngressRejected(404, '当前任务还没有可读取的配音。')

    def import_audio(self, job, event, actor, context):
        workspace_id, task_id = (job.workspace_id, job.task_id)
        raw = event['arguments']['audio']
        audio = StagedAudioInput(raw['staged_input'], raw['filename'], raw['size_bytes'], raw['sha256'])
        self.repository.get_creation_task(workspace_id, task_id)
        suffix = Path(audio.filename or '').suffix.lower()
        if suffix not in {'.wav', '.mp3', '.m4a', '.aac', '.flac'}:
            raise AudioIngressRejected(status_code=422, detail='请选择 WAV、MP3、M4A、AAC 或 FLAC 音频')
        try:
            record = self.task_audio.publish(workspace_id, task_id, kind='audio_source', parameters={'name': Path(audio.filename or '音频').name[:120]}, input_refs=[], operation='upload', media_input={'staged_input': audio.staged_input, 'sha256': audio.source_sha256}, suffix=suffix, execution=(context, job, event, actor))
            duration = self.task_audio.probe(self.task_audio.source(workspace_id, task_id, record.logical_ref, actor=actor), context=context)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise AudioIngressRejected(status_code=422, detail='音频导入失败，请检查文件格式、时长及外接盘。') from exc
        return {'source_ref': record.logical_ref, 'name': Path(audio.filename or '音频').name[:120], 'duration_ms': round(duration * 1000)}

    def save_recording(self, job, event, actor, context):
        workspace_id, task_id = (job.workspace_id, job.task_id)
        raw = event['arguments']['audio']
        audio = StagedAudioInput(raw['staged_input'], raw['filename'], raw['size_bytes'], raw['sha256'])
        task = self.repository.get_creation_task(workspace_id, task_id)
        if task.stage not in {'narration', 'montage', 'review'} or not task.confirmed_script_id:
            raise AudioIngressRejected(status_code=409, detail='请先确认当前任务文案，再保存电脑录音')
        script_digest = self.repository.confirmed_creation_task_script_digest(workspace_id, task.id, task.confirmed_script_id)
        if script_digest is None:
            raise AudioIngressRejected(status_code=409, detail='当前文案未确认或已经变化，不能保存为本任务录音')
        suffix = Path(audio.filename or '').suffix.lower()
        if suffix not in {'.webm', '.mp4', '.m4a', '.ogg', '.wav'}:
            raise AudioIngressRejected(status_code=422, detail='当前录音格式不可用，请使用 WebM、MP4、Ogg 或 WAV')
        try:
            service = self.task_audio
            record = service.publish(workspace_id, task_id, kind='task_recording', parameters={'script_id': task.confirmed_script_id, 'script_digest': script_digest, 'montage_plan_id': task.montage_plan_id}, input_refs=[], operation='upload', media_input={'staged_input': audio.staged_input, 'sha256': audio.source_sha256}, suffix=suffix, execution=(context, job, event, actor))
            duration_ms = round(service.probe(service.source(workspace_id, task_id, record.logical_ref, actor=actor), context=context) * 1000)
        except ValueError as exc:
            raise AudioIngressRejected(status_code=422, detail=f'电脑录音未保存：{exc}') from exc
        except (KeyError, OSError, subprocess.SubprocessError) as exc:
            raise AudioIngressRejected(status_code=422, detail='电脑录音未保存，请检查录音格式和本地存储') from exc
        return {'source_ref': record.logical_ref, 'artifact_id': record.id, 'audio_url': f'/api/creation-tasks/{task_id}/audio/artifacts/{record.id}', 'duration_ms': duration_ms, 'applied': False}

    def audio_waveform(self, job, event, actor, context):
        workspace_id, task_id = (job.workspace_id, job.task_id)
        clip_id = event['arguments']['clip_id']
        self.repository.get_creation_task(workspace_id, task_id)
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            clip = next((clip for track in effective_audio_timeline(plan).tracks for clip in track.clips if clip.id == clip_id))
            source = self.task_audio.source(workspace_id, task_id, clip.source_ref, actor=actor)
            return audio_waveform(source, self.waveform_cache_root / workspace_id, context=context)
        except (KeyError, StopIteration, ValueError, AudioWaveformError) as exc:
            raise AudioIngressRejected(status_code=422, detail='无法读取所选音频的波形，请检查源文件') from exc

    def listen(self, job, event, actor, context):
        workspace_id, task_id = (job.workspace_id, job.task_id)
        payload = AudioListenRequest.model_validate(event['arguments']['payload'])
        task = self.repository.get_creation_task(workspace_id, task_id)
        if task.current_timeline_version_id != payload.base_version_id:
            raise AudioIngressRejected(status_code=409, detail='时间线已经变化，请刷新后试听')
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
            graph = effective_audio_timeline(plan)
            service = self.task_audio
            record = service.publish(workspace_id, task_id, kind='audio_preview', parameters={'audio_timeline': graph.model_dump(), 'start_seconds': payload.start_seconds, 'end_seconds': payload.end_seconds, 'solo_track_ids': sorted(payload.solo_track_ids)}, input_refs=list(dict.fromkeys((clip.source_ref for track in graph.tracks for clip in track.clips))), operation='audio_mix', media_input={'audio_timeline': graph.model_dump(), 'duration_seconds': plan.duration_seconds, 'normalize_voice_sources': plan.narration_linkage_created_audio_timeline, 'start_seconds': payload.start_seconds, 'end_seconds': payload.end_seconds, 'solo_track_ids': sorted(payload.solo_track_ids)}, execution=(context, job, event, actor))
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise AudioIngressRejected(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else '试听生成失败，请检查音频和外接盘') from exc
        return {'audio_url': f'/api/creation-tasks/{task_id}/audio/artifacts/{record.id}'}

    def narration_waveform(self, job, event, actor, context):
        workspace_id, task_id = (job.workspace_id, job.task_id)
        task = self.repository.get_creation_task(workspace_id, task_id)
        artifact = self.repository.get_narration_artifact(workspace_id, task.narration_cache_key) if task.narration_cache_key else None
        if artifact is None or artifact.task_id != task.id:
            raise AudioIngressRejected(status_code=404, detail='当前任务还没有可读取的配音。')
        try:
            return audio_waveform(Path(artifact.audio_path), self.waveform_cache_root / workspace_id, context=context)
        except AudioWaveformError as exc:
            raise AudioIngressRejected(status_code=422, detail=str(exc)) from exc

    def seal(self, workspace_id, task_id, operation, arguments, actor, budget):
        task = self.repository.get_creation_task(workspace_id, task_id)
        plan_payload = self.repository.get_creation_task_montage_plan(workspace_id, task_id) if task.montage_plan_id else None
        plan = creation_task_montage_plan_from_payload(plan_payload) if plan_payload else None
        transient = {'status', 'message', 'output_path', 'subtitle_path', 'updated_at'}
        snapshot = {key:value for key,value in creation_task_montage_plan_to_payload(plan).items()
                    if key not in transient} if plan else None
        payload = arguments.get('payload', {})
        if 'base_version_id' in payload and payload['base_version_id'] != task.current_timeline_version_id:
            raise ValueError('时间线已变化，请重新安排当前要求')
        files, audio_inputs, audio_uses, audio_graphs = [], [], {}, []
        def remember(reference, path, metadata=None):
            stat = path.stat()
            fact = [reference, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            if fact not in files:
                files.append(fact)
            if not any(item['reference'] == reference for item in audio_inputs):
                values = metadata if metadata is not None else self.task_audio.source_metadata(workspace_id, task_id, reference)
                audio_inputs.append({'reference':reference, 'path':str(path.resolve()), 'size_bytes':stat.st_size,
                    'uses':audio_uses.get(reference,1), **values})
        if plan is not None and operation not in {'audio_import','recording_save','narration_waveform'}:
            graph = effective_audio_timeline(plan)
            audio_graphs.append(graph)
            solo = set(payload.get('solo_track_ids', []))
            clips = [clip for track in graph.tracks if not track.muted and (not solo or track.id in solo)
                     for clip in track.clips if not clip.muted]
            if operation == 'audio_waveform':
                clips = [clip for track in graph.tracks for clip in track.clips if clip.id == arguments['clip_id']]
                if not clips:
                    raise ValueError('所选音频片段不存在')
            references = {clip.source_ref for clip in clips}
            audio_uses.update(Counter(clip.source_ref for clip in clips))
            for reference in sorted(references):
                remember(reference, self.task_audio.source(workspace_id, task_id, reference, actor=actor))
        if 'audio' in arguments:
            relative = arguments['audio']['staged_input']
            path = self.task_audio.temporary_root / relative
            if path.is_symlink() or not path.resolve().is_relative_to(self.task_audio.temporary_root.resolve()):
                raise ValueError('音频暂存输入越界')
            remember(relative, path, {'duration_seconds':None, 'sample_rate':None, 'channels':None})
        if operation == 'narration_waveform':
            if not task.narration_cache_key:
                raise ValueError('当前任务没有可读取的配音')
            reference = self.repository.narration_artifact_reference(task.narration_cache_key)
            remember(reference, self.task_audio.source(workspace_id, task_id, reference, actor=actor))
        facts = {'base_version_id':task.current_timeline_version_id, 'script_id':task.confirmed_script_id,
            'script_digest':self.repository.confirmed_creation_task_script_digest(workspace_id, task_id, task.confirmed_script_id)
                if task.confirmed_script_id else None,
            'plan_id':task.plan_id, 'plan_digest':media_digest(asdict(self.repository.get_creation_plan(workspace_id,task.plan_id))),
            'montage':snapshot, 'files':files, 'narration_cache_key':task.narration_cache_key}
        if operation == 'audio_import':
            facts = {'base_version_id':None, 'files':files}
        if budget is None:
            raise ValueError('CPU 音频处理预算尚未标定，未启动媒体')
        if not audio_inputs and operation != 'audio_listen':
            raise ValueError('没有固定的音频输入，未启动媒体')
        for source in audio_inputs:
            for field, cap in (('duration_seconds',budget.max_source_duration_seconds),
                               ('sample_rate',budget.max_sample_rate),('channels',budget.max_channels)):
                value = source[field]
                if value is not None and (not isinstance(value,(int,float)) or value <= 0 or value > cap):
                    raise ValueError('已保存的音频源规格超出标定预算')
        known_duration = max((item['duration_seconds'] or 0 for item in audio_inputs),default=0)
        if operation in {'audio_import','recording_save'}:
            output_duration = budget.max_output_duration_seconds
        elif operation in {'audio_waveform','narration_waveform'}:
            if known_duration > budget.max_output_duration_seconds:
                raise ValueError('当前音频超过波形输出时长预算')
            output_duration = budget.max_output_duration_seconds
        else:
            if plan is None:
                raise ValueError('当前任务没有可试听的音轨')
            start, end = payload.get('start_seconds',0), payload.get('end_seconds',plan.duration_seconds)
            if not 0 <= start < end <= plan.duration_seconds:
                raise ValueError('试听范围必须位于时间线内')
            output_duration = end-start
        resources = {'kind':'task_audio','execution_mode':'cpu_ffmpeg','input_policy':'probe_then_bounded_decode',
            'inputs':audio_inputs,'input_count':sum(item['uses'] for item in audio_inputs),
            'input_bytes':sum(item['size_bytes']*item['uses'] for item in audio_inputs),
            'source_duration_limit_seconds':budget.max_source_duration_seconds,'output_duration_limit_seconds':output_duration,
            'source_sample_rate_limit':budget.max_sample_rate,'source_channels_limit':budget.max_channels,
            'output_sample_rate_limit':8000 if operation in {'audio_waveform','narration_waveform'} else
                budget.max_sample_rate if operation in {'audio_import','recording_save'} else 48000,
            'output_channels_limit':budget.max_channels if operation in {'audio_import','recording_save','audio_waveform','narration_waveform'} else 2,
            'max_output_bytes':budget.max_output_bytes,'threads':budget.max_threads,'timeout_seconds':budget.max_timeout_seconds}
        if audio_graphs and operation == 'audio_listen':
            mix = audio_mix_resources(audio_inputs,audio_graphs,output_duration,
                normalize_voice_sources=plan.narration_linkage_created_audio_timeline,budget=budget)
            resources.update({key:mix[key] for key in ('normalize_voice_sources','normalized_source_outputs',
                'mix_output_bytes','temporary_output_bytes','output_duration_limit_seconds')})
        if (resources['input_count'] > budget.max_input_count or resources['input_bytes'] > budget.max_input_bytes
                or output_duration > budget.max_output_duration_seconds or resources['output_sample_rate_limit'] > budget.max_sample_rate):
            raise ValueError('当前音频处理输入或输出超出标定预算')
        return facts, resources

    def validate_result(self, job, event, result, context):
        outputs = []
        for record in self.repository.execution_records.list_artifact_records(job.workspace_id,job.task_id):
            if record.producer_job_id != job.id:
                continue
            context.check()
            publication = self.task_audio.artifact_store.describe(record.logical_ref)
            if (publication.sha256,publication.size_bytes) != (record.sha256,record.size_bytes):
                raise ValueError('已提交预览产物内容发生变化')
            path = self.task_audio.artifact_store.resolve_file(record.logical_ref)
            stat = path.stat()
            outputs.append({'reference':record.logical_ref,'size_bytes':stat.st_size,
                'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns})
            # These actions publish audio_source/task_recording/audio_preview, even in MP4 containers.
            probe_audio_source(path,scheduler=context.scheduler,grant=context.grant,
                cancel_event=context.cancel_event,deadline_monotonic=context.deadline_monotonic,output=True)
        context.check()
        return outputs
