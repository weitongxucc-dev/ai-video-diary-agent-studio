"""Registered output observation on explicit sources, actors and original grants."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time

from app.canvas import canvas_size
from app.creation.contracts import CreationTask, TimelineVersion
from app.creation_execution import CreationExecutionActor
from app.graphic_sources import graphic_source_facts
from app.models import utc_now
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.source_policy import knowledge_item_readable as content_readable
from app.schemas import CreationTaskObservationRequest
from app.task_audio import TASK_MEDIA_KIND
from app.task_observation import observe_output, MAX_SAMPLE_EDGE, MAX_OBSERVATION_BYTES


class OutputObservationRejected(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


@dataclass(frozen=True)
class ObservationSource:
    task: CreationTask
    version: TimelineVersion | None
    payload: CreationTaskObservationRequest
    path: Path
    output: dict
    binding: dict


@dataclass(frozen=True)
class OutputDownload:
    path: Path
    version_id: str | None
    sequence: int | None


class OutputObservationUseCase:
    def __init__(self, repository, artifact_store, audio, temporary_root, ocr_worker_path):
        if audio.repository is not repository:
            raise ValueError('输出观察必须绑定同一仓储负责人')
        self.repository, self.artifact_store, self.audio = repository, artifact_store, audio
        self.temporary_root, self.ocr_worker_path = temporary_root, ocr_worker_path

    def submit(self, runtime, media, task, actor, payload, source, *, receipt_for, write_guard=None):
        """Both transports arrange one original observation through the media owner."""
        previous=self.original_request(task,actor,payload)
        if previous is not None:
            return previous
        try:
            self.version(task,payload)
        except ValueError as exc:
            raise OutputObservationRejected(409,str(exc)) from exc
        event_id=None
        if payload.request_id is not None:
            event_id,fresh=self.repository.claim_edit_request(task,actor.user_id or actor.member_id,
                payload.request_id,'observation',payload.model_dump(mode='json',exclude_unset=True),write_guard=write_guard)
            if not fresh:
                return self.original_request(task,actor,payload)
        values={'_media_kind':'output_observation','_draft':False,'_typed_source':source,
            'payload':payload.model_dump(mode='json',exclude_unset=True)}
        try:
            job=media.submit(runtime,actor,task.id,'output_observation',values,'observation',write_guard=write_guard)
            receipt=receipt_for(media,job,actor,runtime)
            if event_id is not None:
                self.repository.save_edit_request_result(task,event_id,status_code=202,response=receipt)
        except Exception:
            if event_id is not None:
                # A durable claim survives both queue and projection failures.
                return self.original_request(task,actor,payload)
            located=next((event for event in reversed(self.repository.list_task_events(task.workspace_id,task.id))
                if event.event_type=='task_media_submitted' and event.payload['arguments']==values
                and CreationExecutionActor.from_event(task.workspace_id,event.payload)==actor),None)
            if located is not None:
                raise OutputObservationRejected(409,{'error':'observation_result_unknown',
                    'reason':'原观察作业已登记，后续安排或回填未确认；请查询原作业','submission_effect':'unknown',
                    'task_id':task.id,'job_id':located.job_id,'query_tool':'read_task_media_job',
                    'query_url':f'/api/creation-tasks/{task.id}/media-jobs/{located.job_id}'})
            raise
        return {**receipt,'result':None}

    def original_request(self,task,actor,payload):
        return read_original_observation_request(self.repository,task,actor,payload)

    def _task_or_404(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise OutputObservationRejected(status_code=404, detail='creation task not found') from exc

    def version(self, task, payload):
        version = self.repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise ValueError('观察目标时间线已变化，请回读当前版本')
        return version

    def output_path(self, workspace_id: str, task_id: str, *, subtitle: bool) -> Path:
        task = self._task_or_404(workspace_id, task_id)
        try:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
        except KeyError as exc:
            raise OutputObservationRejected(status_code=404, detail='creation task output not found') from exc
        raw_path = plan.subtitle_path if subtitle else plan.output_path
        if plan.status != 'rendered' or not raw_path:
            raise OutputObservationRejected(status_code=404, detail='creation task output not found')
        expected_kind = 'subtitle_track' if subtitle else 'rendered_video'
        registered = next((artifact for artifact in self.repository.list_artifact_records(workspace_id, task.id) if artifact.logical_ref == raw_path and artifact.kind == expected_kind), None)
        if registered is None:
            raise OutputObservationRejected(status_code=409, detail='creation task output is not a registered artifact')
        try:
            path = self.artifact_store.resolve_file(raw_path)
        except (FileNotFoundError, ValueError):
            raise OutputObservationRejected(status_code=404, detail='creation task output not found')
        return path

    def download(self, workspace_id: str, task_id: str, *, expected_version_id: str | None, subtitle: bool) -> OutputDownload:
        task = self._task_or_404(workspace_id, task_id)
        if expected_version_id is not None and expected_version_id != task.current_timeline_version_id:
            raise OutputObservationRejected(409, '作品版本已变化，请刷新当前任务后重新下载。')
        if task.current_timeline_version_id is None:
            return OutputDownload(self.output_path(workspace_id, task_id, subtitle=subtitle), None, None)
        try:
            version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
        except KeyError as exc:
            raise OutputObservationRejected(404, '作品版本不存在。') from exc
        if version.task_id != task_id:
            raise OutputObservationRejected(404, '作品版本不存在。')
        montage = version.snapshot.get('montage')
        ref = montage.get('subtitle_path' if subtitle else 'output_path') if isinstance(montage, dict) else None
        if (not isinstance(ref, str) or not ref or montage.get('status') != 'rendered'
                or ref not in version.snapshot.get('output_refs', [])):
            raise OutputObservationRejected(409, '当前版本尚无可下载的有效产物，请等待该版本制作完成。')
        kind = 'subtitle_track' if subtitle else 'rendered_video'
        registered = next((item for item in self.repository.list_artifact_records(workspace_id, task_id)
            if item.logical_ref == ref and item.kind == kind), None)
        if registered is None:
            raise OutputObservationRejected(409, '当前版本产物尚未登记。')
        try:
            path = self.artifact_store.resolve_file(ref)
        except (FileNotFoundError, ValueError) as exc:
            raise OutputObservationRejected(404, '当前版本产物已不可读取。') from exc
        if path.stat().st_size != registered.size_bytes:
            raise OutputObservationRejected(409, '当前版本产物与登记文件不一致。')
        if self._task_or_404(workspace_id, task_id).current_timeline_version_id != version.id:
            raise OutputObservationRejected(409, '作品版本已变化，请刷新当前任务后重新下载。')
        return OutputDownload(path, version.id, version.sequence)

    def quality_source_metadata(self, workspace_id: str, task_id: str):
        task = self._task_or_404(workspace_id, task_id)
        plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task_id))
        path = self.output_path(workspace_id, task_id, subtitle=False)
        registered = next((record for record in self.repository.list_artifact_records(workspace_id, task_id) if record.logical_ref == plan.output_path and record.kind == 'rendered_video'))
        if path.stat().st_size != registered.size_bytes:
            raise ValueError('成片与登记产物不匹配，请重新生成。')
        return (task, plan, path, registered)

    @staticmethod
    def verify_registered_file(path, record, context):
        from app.quality_worker_runtime import hash_file, QualityWorkerError
        context.check()
        try:
            digest = hash_file(path, check=context.check, max_bytes=record.size_bytes)
        except QualityWorkerError as exc:
            raise ValueError('观察媒体与真实登记摘要不一致') from exc
        if digest != record.sha256 or path.stat().st_size != record.size_bytes:
            raise ValueError('观察媒体与真实登记摘要不一致')

    def published_outputs_current(self, job):
        records = [item for item in self.repository.list_artifact_records(job.workspace_id, job.task_id) if item.producer_job_id == job.id]
        if not records:
            return True
        try:
            verified = self.repository.get_task_event(job.workspace_id, f'event_{job.id}_verified').payload['outputs']
            if not verified or {item['reference'] for item in verified} != {item.logical_ref for item in records}:
                return False
            for item in verified:
                stat = self.artifact_store.resolve_file(item['reference']).stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (item['size_bytes'], item['mtime_ns'], item['ctime_ns']):
                    return False
        except (KeyError, FileNotFoundError, OSError):
            return False
        return True

    def registered_output(self, workspace_id, task_id, target, actor, *, context=None):
        task = self.repository.get_creation_task(workspace_id, task_id)
        if target.kind == 'final':
            _task, plan, path, record = self.quality_source_metadata(workspace_id, task_id)
            binding = {'kind': 'composed_output', 'montage': creation_task_montage_plan_to_payload(plan)}
            width, height = canvas_size(plan.aspect_ratio)
        else:
            job = self.repository.get_job_run(workspace_id, target.job_id)
            if job.task_id != task_id or job.kind != TASK_MEDIA_KIND or job.status != 'succeeded':
                raise ValueError('只能观察本任务真实成功的媒体作业输出')
            start = self.repository.get_task_event(workspace_id, f'event_{job.id}_start').payload
            if CreationExecutionActor.from_event(workspace_id, start) != actor or start['input_facts']['base_version_id'] != task.current_timeline_version_id:
                raise PermissionError('原媒体作业身份或输入版本不再适用当前目标')
            record = self.repository.get_artifact_record(workspace_id, target.artifact_id)
            if record.task_id != task_id or record.producer_job_id != job.id:
                raise ValueError('产物不属于指定任务媒体作业')
            path = self.artifact_store.resolve_file(record.logical_ref)
            if path.suffix == '.png' and record.kind == 'graphic_source':
                graphic = graphic_source_facts(self.repository, self.artifact_store, workspace_id, task_id, record.logical_ref, actor=actor)
                width, height = (graphic['width'], graphic['height'])
                binding = {'kind': 'graphic_source', 'applied': False}
            elif path.suffix == '.mp4':
                self.audio.validate_source_access(workspace_id, task_id, record.logical_ref, actor)
                self.audio.validate_material_use(workspace_id, task_id, record.logical_ref)
                saved = self.repository.get_task_event(workspace_id, f"event_{job.id}_{record.id.removeprefix('artifact_')}_input").payload
                binding = {'kind': 'composed_preview', 'media_input': saved['media_input']}
                if 'clip' in saved['media_input']:
                    from app.canvas import preview_size
                    width, height = preview_size(saved['media_input']['aspect_ratio'])
                else:
                    width, height = canvas_size(saved['media_input']['plan']['aspect_ratio'])
            else:
                raise ValueError('本轮只能观察登记 MP4 预览或 PNG 图片来源')
            if not self.published_outputs_current(job):
                raise ValueError('原媒体输出已丢失或变化，不能观察旧成功状态')
        if context is not None:
            self.verify_registered_file(path, record, context)
        if path.stat().st_size != record.size_bytes:
            raise ValueError('观察媒体与真实登记摘要不一致')
        stat = path.stat()
        output = {'artifact_id': record.id, 'artifact_kind': record.kind, 'sha256': record.sha256, 'registered_width': width, 'registered_height': height, 'size_bytes': record.size_bytes, 'producer_job_id': record.producer_job_id, 'logical_ref': record.logical_ref, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns}
        return (path, output, binding)

    def prepare_source(self, workspace_id, task_id, arguments, actor):
        task = self.repository.get_creation_task(workspace_id, task_id)
        source = arguments.get('_typed_source')
        if source is not None:
            version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
            if (source['workspace_id'], source['task_id'], source['member_id'], source['expected_timeline_version_id'], source['expected_timeline_sequence']) != (workspace_id, task_id, actor.user_id or actor.member_id, task.current_timeline_version_id, version.sequence if version else 0):
                raise ValueError('封存的类型化编辑输入或成员已失效')
        payload = CreationTaskObservationRequest.model_validate(arguments['payload'])
        task = self.repository.get_creation_task(workspace_id, task_id)
        version = self.version(task, payload)
        path, output, binding = self.registered_output(workspace_id, task_id, payload.target, actor)
        return ObservationSource(task, version, payload, path, output, binding)

    @staticmethod
    def require_render_budget(budget):
        if budget is None:
            raise ValueError('实际画面观察的 CPU 执行预算尚未标定')

    def seal_resources(self, source, budget, observation_budget):
        task, version, payload = source.task, source.version, source.payload
        path, output, binding = source.path, source.output, source.binding
        facts = {'base_version_id': task.current_timeline_version_id, 'timeline_sequence': version.sequence if version else 0, 'output': output, 'binding': binding, 'target': payload.target.model_dump(mode='json'), 'sample_times_seconds': payload.sample_times_seconds}
        width, height = (output['registered_width'], output['registered_height'])
        scale = min(1.0, MAX_SAMPLE_EDGE / width, MAX_SAMPLE_EDGE / height)
        sample_width, sample_height = (max(1, round(width * scale)), max(1, round(height * scale)))
        sample_count = 1 if path.suffix == '.png' else len(payload.sample_times_seconds) or 3
        ocr_worker = Path(self.ocr_worker_path).expanduser() if self.ocr_worker_path else None
        if observation_budget is not None and ocr_worker is not None and ocr_worker.is_file():
            worker_digest = hashlib.sha256(ocr_worker.read_bytes()).hexdigest()
            if worker_digest == observation_budget.worker_sha256:
                facts['ocr'] = {'worker_sha256': worker_digest, 'status': 'ready'}
                resources = {'kind': 'task_output_observation', 'execution_mode': 'local_ffmpeg_apple_vision', 'worker_sha256': worker_digest, 'sample_count': sample_count, 'source_pixels': output['registered_width'] * output['registered_height'], 'width_px': sample_width, 'height_px': sample_height, 'input_bytes': path.stat().st_size, 'threads': observation_budget.max_threads, 'timeout_seconds': observation_budget.max_timeout_seconds, 'max_output_bytes': observation_budget.max_output_bytes}
                return (facts, resources)
            facts['ocr'] = {'status': 'unavailable', 'reason': '本地 OCR worker 摘要与已标定执行文件不一致。'}
        else:
            facts['ocr'] = {'status': 'unavailable', 'reason': '本地 OCR 未配置或输出观察执行范围尚未标定。'}
        resources = {'kind': 'creation_render', 'execution_mode': 'cpu_ffmpeg', 'duration_seconds': float(sample_count), 'width_px': sample_width, 'height_px': sample_height, 'input_bytes': path.stat().st_size, 'clip_count': 1, 'overlay_count': 0, 'threads': 1, 'timeout_seconds': budget.max_timeout_seconds, 'max_output_bytes': budget.max_output_bytes}
        if width * height > budget.max_width_px * budget.max_height_px:
            raise ValueError('实际输出源帧像素超过已标定 CPU 范围')
        return (facts, resources)

    def execute(self, job, event, actor, context):
        task_id = job.task_id
        payload = CreationTaskObservationRequest.model_validate(event['arguments']['payload'])
        task = self._task_or_404(job.workspace_id, task_id)
        principal = actor.user_id or actor.member_id
        if not content_readable(user_id=principal, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise OutputObservationRejected(404, 'creation task not found')
        if task.owner_user_id and task.owner_user_id != principal:
            raise OutputObservationRejected(403, '当前成员不能修改此任务')
        try:
            self.version(task, payload)
        except ValueError as exc:
            raise OutputObservationRejected(status_code=409, detail=str(exc)) from exc
        path, output, binding = self.registered_output(task.workspace_id, task_id, payload.target, actor, context=context)
        from app.granted_subprocess import run_granted_command
        def run_command(command, *, output_paths=()):
            context.check()
            result = run_granted_command(context.scheduler, context.grant, command, timeout_seconds=max(0.1, min(30.0, context.deadline_monotonic - time.monotonic())), max_stdout_bytes=MAX_OBSERVATION_BYTES, max_stderr_bytes=32 * 1024, output_paths=output_paths, max_output_file_bytes=context.max_output_bytes, cancel_event=context.cancel_event)
            if result.returncode:
                raise ValueError('本地实际输出采样失败，请检查媒体和工具')
            return result
        measured = observe_output(path, sample_times=payload.sample_times_seconds, temporary_root=self.temporary_root, run_command=run_command, expected_size=(output['registered_width'], output['registered_height']), max_output_bytes=context.max_output_bytes, ocr_worker=Path(self.ocr_worker_path).expanduser() if event['input_facts']['ocr']['status'] == 'ready' else None)
        montage = binding.get('montage') or binding.get('media_input', {}).get('plan')
        single_clip = binding.get('media_input', {}).get('clip')
        for sample in measured['samples']:
            timestamp = sample['timestamp_seconds']
            if montage:
                sample['object_refs'] = [f"clip:{clip['id']}" for clip in montage.get('clips', []) if clip['timeline_start_seconds'] <= timestamp < clip['timeline_end_seconds']]
                sample['object_refs'].extend((f"overlay:{overlay['id']}" for overlay in montage.get('graphic_overlays', []) if overlay['start_seconds'] <= timestamp < overlay['end_seconds']))
            elif single_clip:
                sample['object_refs'] = [f"clip:{single_clip['id']}"]
                sample['timeline_timestamp_seconds'] = single_clip['timeline_start_seconds'] + timestamp
        result = {'observation_id': job.id, 'task_id': task_id, 'timeline_version_id': event['input_facts']['base_version_id'], 'timeline_sequence': event['input_facts']['timeline_sequence'], 'observed_at': utc_now().isoformat(), 'target': payload.target.model_dump(mode='json'), 'ocr_execution': event['input_facts']['ocr'], 'output': {key: value for key, value in output.items() if key not in {'logical_ref', 'mtime_ns', 'ctime_ns'}}, 'output_role': binding['kind'], **measured}
        return result

    def validate_result(self, job, event, result, context):
        context.check()
        if (result.get('timeline_version_id'), result.get('output', {}).get('sha256')) != (event['input_facts']['base_version_id'], event['input_facts']['output']['sha256']):
            raise ValueError('观察结果未绑定原实际输出版本和摘要')
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_OBSERVATION_BYTES:
            raise ValueError('观察回执超过有界大小')
        return []



def read_original_observation_request(repository,task,actor,payload):
    if payload.request_id is None:
        return None
    from app.task_audio import media_digest
    member_id=actor.user_id or actor.member_id
    event_id='event_edit_request_'+media_digest([task.workspace_id,member_id,str(payload.request_id),'observation'])
    try:
        event=repository.get_task_event(task.workspace_id,event_id)
    except KeyError:
        return None
    inputs=payload.model_dump(mode='json',exclude_unset=True)
    digest=media_digest([task.id,inputs])
    if (event.task_id!=task.id or event.payload['member_id']!=member_id or event.payload['input_digest']!=digest):
        raise OutputObservationRejected(409,{'error':'observation_request_conflict','reason':'原观察请求身份已绑定不同任务或输入'})
    result=repository.read_edit_request_result(task,event_id)
    if result is None:
        raise OutputObservationRejected(409,{'error':'observation_result_unknown','reason':'原观察请求结果尚未知，未重复执行；请查询原请求',
            'submission_effect':'unknown','task_id':task.id,'request_id':str(payload.request_id),'input_digest':digest,
            'query_tool':'read_task_observation_request','query_url':f'/api/creation-tasks/{task.id}/assistant/edit-requests/{payload.request_id}?phase=observation'})
    if not 200<=result['status_code']<300:
        response=result['response']
        raise OutputObservationRejected(result['status_code'],response.get('detail',response))
    return result['response']
