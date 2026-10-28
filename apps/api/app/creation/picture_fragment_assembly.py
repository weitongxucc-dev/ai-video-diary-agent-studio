"""Finite, durable H264 picture windows with the original approved burn-in layers."""
from contextlib import nullcontext
from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import stat

from app.canvas import canvas_size
from app.creation.assembly_manifest import PictureAssemblyWindow, iter_picture_windows
from app.creation.contracts import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.creation.picture_fragment_layers import PictureFrameLayer, picture_fragment_recipe, picture_window_layers
from app.graphic_overlays import validate_overlays
from app.graphic_sources import graphic_source_facts
from app.models import utc_now
from app.montage import creation_task_montage_plan_from_payload, render_granted_picture_fragment
from app.subtitle_schedule import TimedCaption, narration_schedule, subtitle_schedule, schedule_srt
from app.task_audio import media_digest


def _identity(path):
    value = Path(path).lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('画面窗口来源不是普通文件')
    return {'device': value.st_dev, 'inode': value.st_ino, 'size_bytes': value.st_size,
        'mtime_ns': value.st_mtime_ns, 'ctime_ns': value.st_ctime_ns}


class PictureFragmentAssembly:
    def __init__(self, repository, artifact_store, temporary_root, *, metadata, picture_renderer,
                 render_budget, packet_copy_assembly):
        self.repository, self.store = repository, artifact_store
        self.metadata, self.renderer = metadata, picture_renderer
        self.budget, self.packet = render_budget, packet_copy_assembly
        self.root = Path(temporary_root).expanduser().resolve() / 'picture-fragments'

    @staticmethod
    def manifest_id(job):
        return f'event_{job.id}_picture_fragment_manifest'

    def _inputs(self, job, actor, *, connection=None):
        from app.source_policy import knowledge_item_readable as content_readable
        task = self.repository.get_creation_task(job.workspace_id, job.task_id, connection=connection)
        if (actor.workspace_id != job.workspace_id or task.owner_user_id != (actor.user_id or actor.member_id)
                or not content_readable(user_id=actor.user_id or actor.member_id, local_diagnostic=False,
                    visibility=task.visibility, owner_user_id=task.owner_user_id)):
            raise PermissionError('画面窗口不属于原任务成员')
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
            job.workspace_id, job.task_id, connection=connection))
        if any(clip.person_render_id for clip in montage.clips):
            raise ValueError('人物片段尚未接入此硬切窗口生产路径')
        self.renderer.validate_material_use(job.workspace_id, montage)
        files, _graphics = self.renderer.qualified_picture_sources(job.workspace_id, task, montage, [montage], actor)
        tracks = self.metadata.current_subtitle_tracks(job.workspace_id, task, montage=montage)
        current_version = self.repository.get_timeline_version(job.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        explicit_subtitles = current_version is not None and 'subtitle_track_ids' in current_version.snapshot
        tracks = tracks if tracks or explicit_subtitles else None
        captions = (subtitle_schedule(tracks, montage.duration_seconds) if tracks is not None
            else narration_schedule(montage.cues, montage.duration_seconds))
        graphics = [item.model_dump() for item in validate_overlays(montage.graphic_overlays, montage.duration_seconds)]
        image_facts = {}
        for graphic in graphics:
            if graphic['kind'] == 'image':
                original = graphic['source_ref']
                facts = graphic_source_facts(self.repository, self.store, job.workspace_id, job.task_id, original, actor=actor)
                image_facts[original] = facts
                graphic['source_ref'] = facts['path']
        sources = {}
        for clip in montage.clips:
            path = self.repository.resolve_workspace_media_reference(job.workspace_id, clip.source_path, require_registered=True)
            sources[clip.source_path] = {'path': str(path), 'file_identity': _identity(path)}
        sidecar = (subtitle_schedule([replace(track, style={**track.style, 'enabled': True}) for track in tracks],
            montage.duration_seconds) if tracks is not None else captions)
        return task, montage, captions, graphics, sources, image_facts, files, schedule_srt(sidecar)

    def prepare(self, job, *, actor, write_guard, connection=None):
        if self.budget is None:
            raise ValueError('画面窗口缺少本机CPU编码预算')
        task, montage, captions, graphics, sources, images, files, subtitle_text = self._inputs(job, actor, connection=connection)
        width, height = canvas_size(montage.aspect_ratio)
        max_seconds = min(self.budget.max_duration_seconds, self.budget.max_pixel_seconds/(width*height))
        max_frames = int(Fraction(str(max_seconds))*30)
        if max_frames <= 0:
            raise ValueError('当前CPU编码档不能容纳一帧目标画幅')
        units = []
        for window in iter_picture_windows(montage, source_identities=sources, max_frames_per_unit=max_frames):
            layers = picture_window_layers(window, captions=captions, graphic_overlays=graphics)
            recipe = picture_fragment_recipe(window, layers)
            identity = media_digest({'workspace_id': job.workspace_id, 'task_id': job.task_id,
                'job_id': job.id, 'recipe': recipe, 'picture_recipe': self.packet.recipe(job, connection=connection)})
            source = sources[window.clip.source_path]
            graphics_count = sum(layer.kind == 'graphic' for layer in layers)
            caption_count = sum(layer.kind == 'subtitle' for layer in layers)
            # The current filter executor decodes from the original cut origin.
            # Declare the full original input interval, including its prefix.
            decode_seconds = window.clip.source_end_seconds-window.clip.source_start_seconds
            overlay_count = graphics_count + caption_count
            input_bytes = source['file_identity']['size_bytes'] + sum(facts['size_bytes'] for facts in images.values())
            if (decode_seconds > max_seconds or width > self.budget.max_width_px or height > self.budget.max_height_px
                    or input_bytes > self.budget.max_input_bytes or overlay_count > self.budget.max_overlay_count
                    or self.budget.max_clip_count < 1):
                raise ValueError('真实原来源字节、decode prefix、图层或画幅超出当前有限编码单元，未缩报或抬帽')
            unit = {'id': identity, 'artifact_id': f'artifact_picture_fragment_{identity}',
                'window': asdict(window), 'layers': [asdict(layer) for layer in layers], 'recipe': recipe,
                'source': source, 'decode_seconds': decode_seconds, 'input_bytes': input_bytes,
                'overlay_count': overlay_count, 'graphics': graphics_count, 'captions': caption_count}
            units.append(unit)
        if not units:
            raise ValueError('画面窗口没有完整输出单元')
        sidecar_bytes = len(subtitle_text.encode('utf-8'))
        if sidecar_bytes >= self.budget.max_output_bytes:
            raise ValueError('原字幕sidecar单元超过本次总发布分配，不能抬高作品或单元上限')
        last = units[-1]
        last['subtitle_sha256'] = hashlib.sha256(subtitle_text.encode('utf-8')).hexdigest()
        last['subtitle_bytes'] = sidecar_bytes
        last['id'] = media_digest([last['id'], last['subtitle_sha256'], 'subtitle-sidecar/v1'])
        last['artifact_id'] = f'artifact_picture_fragment_{last["id"]}'
        payload = {'version': 'picture-fragment-assembly/v2', 'subtitle_text': subtitle_text, 'job_id': job.id, 'input_digest': job.input_digest,
            'task_id': task.id, 'head': task.current_timeline_version_id, 'subtitles': [asdict(cue) for cue in captions],
            'picture_recipe': self.packet.recipe(job, connection=connection), 'sources': sources, 'images': images,
            'qualified_files': files, 'units': units}
        payload = json.loads(json.dumps(payload, default=str))
        payload['digest'] = media_digest(payload)
        existing = self.manifest(job, actor=actor, connection=connection)
        if existing is not None:
            if payload != existing:
                raise ValueError('原画面窗口manifest已经绑定其他层内容或来源')
            return existing
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            if connection is None:
                conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            from app.creation_execution import CreationExecutionActor
            event = self.repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn)
            if actor != CreationExecutionActor.from_event(job.workspace_id, event.payload):
                raise PermissionError('画面窗口原Job身份不匹配')
            for source in sources.values():
                if _identity(source['path']) != source['file_identity']:
                    raise ValueError('画面来源在封存期间已变化')
            current = self.repository.get_creation_task(job.workspace_id, job.task_id, connection=conn)
            if (current.current_timeline_version_id != payload['head']
                    or self.packet.recipe(job, connection=conn) != payload['picture_recipe']):
                raise ValueError('原任务画面配方在manifest封存期间已变化')
            self.repository.execution_records.append_task_event(TaskEvent(self.manifest_id(job), job.workspace_id, job.task_id,
                job.id, 'picture_fragment_manifest', payload), connection=conn)
        return payload

    def manifest(self, job, *, actor, connection=None):
        try:
            event = self.repository.execution_records.get_task_event(job.workspace_id, self.manifest_id(job), connection=connection)
        except KeyError:
            return None
        payload = event.payload
        task, _montage, captions, _graphics, sources, images, files, subtitle_text = self._inputs(job, actor, connection=connection)
        if (payload['digest'] != media_digest({key: value for key, value in payload.items() if key != 'digest'})
                or payload['job_id'] != job.id or payload['task_id'] != job.task_id or payload['input_digest'] != job.input_digest
                or payload['head'] != task.current_timeline_version_id or payload['sources'] != sources
                or payload['images'] != images or payload['qualified_files'] != json.loads(json.dumps(files))
                or payload['subtitles'] != [asdict(cue) for cue in captions]
                or payload.get('subtitle_text', subtitle_text) != subtitle_text
                or payload['picture_recipe'] != self.packet.recipe(job, connection=connection)):
            raise ValueError('原画面窗口身份、授权来源或实际字幕已变化')
        return payload

    def next_unit(self, job, *, actor, request=None, connection=None):
        manifest = self.manifest(job, actor=actor, connection=connection)
        if manifest is None:
            return None
        for unit in manifest['units']:
            current = request is not None and request.resources.get('picture_fragment_unit') == unit['id'] and request.state in {'granted', 'yield_requested'}
            if not current:
                try:
                    self.packet._parent(job, unit['artifact_id'], manifest['picture_recipe'], connection=connection)
                except KeyError:
                    pass
                else:
                    continue
            window = PictureAssemblyWindow(**unit['window'])
            width, height = canvas_size(window.aspect_ratio)
            resources = {'kind': 'creation_render', 'picture_fragment_unit': unit['id'],
                'picture_recipe_digest': manifest['picture_recipe'], 'picture_fragment_recipe': unit['recipe'],
                'picture_fragment': {'first_frame': window.timeline_start_frame,
                    'packet_count': window.frame_count, 'contract': 'h264-crf20-b0-fmp4/v1'},
                'duration_seconds': unit['decode_seconds'], 'width_px': width, 'height_px': height,
                'input_bytes': unit['input_bytes'], 'clip_count': 1, 'overlay_count': unit['overlay_count'],
                'max_output_bytes': self.budget.max_output_bytes, 'threads': self.budget.max_threads,
                'timeout_seconds': self.budget.max_timeout_seconds,
                'picture_fragment_manifest_digest': manifest['digest'],
                'picture_temporary_limit_bytes': self.budget.additional_disk_bytes,
                'picture_generated_bytes_limit': 2*self.budget.max_output_bytes+self.budget.additional_disk_bytes}
            if 'subtitle_bytes' in unit:
                resources['subtitle_sidecar_bytes'] = unit['subtitle_bytes']
                resources['subtitle_sidecar_sha256'] = unit['subtitle_sha256']
                resources['picture_video_output_bytes'] = self.budget.max_output_bytes - unit['subtitle_bytes']
            return f'render:picture-fragment:v1:{unit["id"]}', resources
        return None

    def _layout(self, job, unit):
        directory = self.root/job.workspace_id/job.task_id/job.id/unit['id']
        logical = f'workspaces/{job.workspace_id}/tasks/{job.task_id}/picture-fragments/{job.id}/{unit["id"]}.mp4'
        return logical, directory, self.store._target(logical).with_suffix('.copying')

    def execute(self, job, *, actor, context, publication_guard):
        request = context.scheduler._get(context.grant.request_id)
        manifest = self.manifest(job, actor=actor)
        if self.next_unit(job, actor=actor, request=request) != (request.unit_key, request.resources):
            raise ValueError('画面窗口实际层配方与原许可不一致')
        unit = next(item for item in manifest['units'] if item['id'] == request.resources['picture_fragment_unit'])
        window = PictureAssemblyWindow(**unit['window'])
        layers = [PictureFrameLayer(item['kind'], TimedCaption(**item['value']) if item['kind'] == 'subtitle' else item['value'],
            item['first_frame'], item['last_frame']) for item in unit['layers']]
        logical, directory, publication_file = self._layout(job, unit)
        if directory.exists() or publication_file.exists():
            raise ValueError('原画面窗口留有执行文件，须按原回执恢复，不能覆盖')
        directory.mkdir(parents=True)
        publication_file.parent.mkdir(parents=True, exist_ok=True)
        output = directory/'picture.mp4'
        started, published, published_subtitles = False, None, None
        subtitle_publication_started = False
        subtitle_logical = logical.removesuffix('.mp4')+'.srt'
        subtitle_temporary = self.store._target(subtitle_logical).with_name(Path(subtitle_logical).name+'.copying')
        try:
            source = Path(unit['source']['path'])
            context.check()
            digest = hashlib.sha256()
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as reader:
                before = _identity(source)
                opened = os.fstat(reader.fileno())
                opened_identity = {'device': opened.st_dev, 'inode': opened.st_ino, 'size_bytes': opened.st_size,
                    'mtime_ns': opened.st_mtime_ns, 'ctime_ns': opened.st_ctime_ns}
                if opened_identity != before:
                    raise ValueError('原画面来源文件描述符与封存stat不一致')
                while True:
                    context.check()
                    chunk = reader.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(reader.fileno())
                if (before != unit['source']['file_identity'] or _identity(source) != before
                        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) !=
                           (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise ValueError('原画面来源在完整SHA期间已变化')
            if manifest['images']:
                montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id))
                self.renderer.graphic_source_paths(job.workspace_id, job.task_id, montage.graphic_overlays, context)
            video_context = replace(context, max_output_bytes=request.resources.get('picture_video_output_bytes', context.max_output_bytes))
            render_granted_picture_fragment(window, source, output, video_context, frame_layers=layers)
            if unit.get('subtitle_bytes', 0) > 0:
                subtitle_path = directory/'subtitles.srt'
                text = manifest['subtitle_text'].encode('utf-8')
                if len(text) != unit['subtitle_bytes'] or hashlib.sha256(text).hexdigest() != unit['subtitle_sha256']:
                    raise ValueError('原字幕sidecar大小或SHA与许可不一致')
                context.check()
                with subtitle_path.open('xb') as subtitle_file:
                    subtitle_file.write(text)
                if output.stat().st_size + len(text) > context.max_output_bytes:
                    raise ValueError('画面与字幕真实总输出超出本单元许可')
                subtitle_publication_started = True
                published_subtitles = self.store.publish_file(subtitle_logical, subtitle_path,
                    temporary_path=subtitle_temporary, check=context.check)
            context.check()
            if _identity(source) != unit['source']['file_identity']:
                raise ValueError('画面来源在编码期间已变化')
            started = True
            published = self.store.publish_file(logical, output, temporary_path=publication_file, check=context.check)
            record = ArtifactRecord(unit['artifact_id'], job.workspace_id, job.task_id, 'picture_fragment', logical,
                published.sha256, published.size_bytes, job.id, [window.clip.source_path, *manifest['images']])
            facts = {**request.resources['picture_fragment'], 'recipe_digest': manifest['picture_recipe'],
                'producer_request_id': context.grant.request_id, 'producer_generation': context.grant.generation,
                'file_identity': _identity(self.store.resolve_file(logical)), 'source_sha256': digest.hexdigest()}
            transform = TransformRun(id=f'transform_{record.id}', workspace_id=job.workspace_id, task_id=job.task_id,
                job_id=job.id, transform_type='picture_fragment', input_digest=unit['id'], input_refs=record.input_refs,
                output_refs=[logical], engine='h264-crf20-b0-fmp4/v1', parameters={'picture_fragment': facts,
                    'picture_fragment_recipe': unit['recipe'], 'manifest_digest': manifest['digest']},
                status='succeeded', started_at=utc_now(), finished_at=utc_now())
            with self.repository._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                publication_guard(conn)
                self.manifest(job, actor=actor, connection=conn)
                self.repository.execution_records.create_artifact_record(record, connection=conn)
                self.repository.lineage_records.create_transform_run(transform, connection=conn)
                if published_subtitles is not None:
                    subtitle_id = f'artifact_subtitles_{unit["id"]}'
                    subtitle_record = ArtifactRecord(subtitle_id, job.workspace_id, job.task_id, 'subtitle_track',
                        subtitle_logical, published_subtitles.sha256, published_subtitles.size_bytes, job.id,
                        [f'montage-plan://{job.task_id}/{self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id, connection=conn)["id"]}'])
                    self.repository.execution_records.create_artifact_record(subtitle_record, connection=conn)
                    self.repository.lineage_records.create_transform_run(TransformRun(id=f'transform_{subtitle_id}',
                        workspace_id=job.workspace_id, task_id=job.task_id, job_id=job.id,
                        transform_type='subtitle_render', input_digest=unit['id'], input_refs=subtitle_record.input_refs,
                        output_refs=[subtitle_logical], engine='subtitle-sidecar/v1', parameters={
                            'manifest_digest': manifest['digest'], 'producer_request_id': context.grant.request_id,
                            'producer_generation': context.grant.generation, 'file_identity': _identity(self.store.resolve_file(subtitle_logical))},
                        status='succeeded', started_at=utc_now(), finished_at=utc_now()), connection=conn)
                    for index, reference in enumerate(subtitle_record.input_refs):
                        self.repository.lineage_records.create_dependency_edge(DependencyEdge(f'edge_{subtitle_id}_{index}',
                            job.workspace_id, job.task_id, reference, subtitle_logical, f'transform_{subtitle_id}'), connection=conn)
                for index, reference in enumerate(record.input_refs):
                    self.repository.lineage_records.create_dependency_edge(DependencyEdge(f'edge_{record.id}_{index}', job.workspace_id,
                        job.task_id, reference, logical, transform.id), connection=conn)
            return record
        finally:
            total, known = 0, (not (started and published is None and not publication_file.exists())
                and not (subtitle_publication_started and published_subtitles is None and not subtitle_temporary.exists()))
            try:
                def unreadable(error):
                    raise error
                for root, directories, names in os.walk(directory, onerror=unreadable):
                    for name in directories:
                        if not stat.S_ISDIR((Path(root)/name).lstat().st_mode):
                            raise ValueError('画面临时文件目录身份不可核实')
                    for name in names:
                        total += _identity(Path(root)/name)['size_bytes']
                if published is not None:
                    total += published.size_bytes
                elif publication_file.exists():
                    total += _identity(publication_file)['size_bytes']
                if published_subtitles is not None:
                    total += published_subtitles.size_bytes
                elif subtitle_temporary.exists():
                    total += _identity(subtitle_temporary)['size_bytes']
            except (OSError, ValueError):
                known = False
            with self.repository._connect() as conn:
                self.repository.execution_records.append_task_event(TaskEvent(self.measured_event_id(context.grant), job.workspace_id,
                    job.task_id, job.id, 'picture_fragment_output_measured', {'request_id': context.grant.request_id,
                        'permit_generation': context.grant.generation, 'consumption_known': known,
                        'output_bytes': total if known else None}), connection=conn)

    @staticmethod
    def measured_event_id(grant):
        return f'event_{grant.request_id}_{grant.generation}_picture_fragment_output'

    def completed_artifact_ids(self, job, *, actor, connection=None):
        manifest = self.manifest(job, actor=actor, connection=connection)
        if manifest is None or self.next_unit(job, actor=actor, connection=connection) is not None:
            return None
        return [unit['artifact_id'] for unit in manifest['units']]


    def subtitle_artifact(self, job, *, actor, connection=None):
        manifest = self.manifest(job, actor=actor, connection=connection)
        if manifest is None or 'subtitle_bytes' not in manifest['units'][-1]:
            raise ValueError('原画面检查点没有真实字幕sidecar，不能冒用文本输出')
        unit = manifest['units'][-1]
        if unit['subtitle_bytes'] == 0:
            return None
        self.packet._parent(job, unit['artifact_id'], manifest['picture_recipe'], connection=connection)
        record = self.repository.execution_records.get_artifact_record(job.workspace_id, f'artifact_subtitles_{unit["id"]}')
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}')
        if (record.producer_job_id != job.id or record.task_id != job.task_id or record.kind != 'subtitle_track'
                or record.sha256 != unit['subtitle_sha256'] or record.size_bytes != unit['subtitle_bytes']
                or transform.input_digest != unit['id'] or transform.status != 'succeeded'
                or transform.job_id != job.id or transform.output_refs != [record.logical_ref]
                or transform.parameters['manifest_digest'] != manifest['digest']
                or _identity(self.store.resolve_file(record.logical_ref)) != transform.parameters['file_identity']):
            raise ValueError('原字幕sidecar缺少成功来源、完整时钟或真实文件检查点')
        return record
