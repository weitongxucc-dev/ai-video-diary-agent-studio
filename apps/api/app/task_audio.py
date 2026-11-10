"""Task ownership and artifact publication for local audio operations."""
import hashlib
import json
import math
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from app.audio_timeline import AUDIO_MIX_CONTRACT_VERSION, AudioTimeline, probe_audio_source, probe_audio_metadata, audio_mix_output_bounds
from app.models import utc_now
from app.creation.contracts import (ArtifactRecord, DependencyEdge, JobRun, TransformRun, TaskEvent)
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import threading
import time
from collections import Counter
from app.creation.render_generation import current_render_generation, render_namespace, render_generation_fields, stored_render_scope
from app.creation.audio_source_limits import validate_audio_source_specs


def audio_mix_resources(inputs, timelines, duration_seconds, *, normalize_voice_sources, budget, apply_master=True,
                        output_sample_count=None):
    if budget is None:
        raise ValueError("CPU 音频处理预算尚未标定，未启动媒体")
    bounds = [audio_mix_output_bounds(graph, inputs, duration_seconds, normalize_voice_sources,
        apply_master=apply_master, output_sample_count=output_sample_count) for graph in timelines]
    normalized = {item["reference"]: item for bound in bounds for item in bound["normalized_source_outputs"]}
    mix_bytes = bounds[0]["mix_output_bytes"]
    output_duration = max(bound["output_duration_limit_seconds"] for bound in bounds)
    temporary_bytes = mix_bytes + sum(item["max_output_bytes"] for item in normalized.values())
    resources = {"kind": "task_audio", "execution_mode": "cpu_ffmpeg",
        "input_policy": "probe_then_bounded_decode", "inputs": inputs,
        "input_count": sum(item["uses"] for item in inputs),
        "input_bytes": sum(item["size_bytes"] * item["uses"] for item in inputs),
        "source_duration_limit_seconds": budget.max_source_duration_seconds,
        "output_duration_limit_seconds": output_duration,
        "source_sample_rate_limit": budget.max_sample_rate, "source_channels_limit": budget.max_channels,
        "output_sample_rate_limit": 48000, "output_channels_limit": 2,
        "max_output_bytes": temporary_bytes, "threads": budget.max_threads,
        "timeout_seconds": budget.max_timeout_seconds,
        "normalize_voice_sources": bool(normalize_voice_sources),
        "normalized_source_outputs": list(normalized.values()),
        "mix_output_bytes": mix_bytes, "temporary_output_bytes": temporary_bytes}
    if output_sample_count is not None:
        resources['output_sample_count'] = output_sample_count
    exceeded = [f'{label}需要 {actual}，已准备 {cap}' for label, actual, cap in (
        ('输入路数', resources['input_count'], budget.max_input_count),
        ('输入累计字节', resources['input_bytes'], budget.max_input_bytes),
        ('输出时长（秒）', output_duration, budget.max_output_duration_seconds),
        ('临时输出字节', temporary_bytes, budget.max_output_bytes),
        ('输出采样率', 48000, budget.max_sample_rate), ('输出声道', 2, budget.max_channels)) if actual > cap]
    if exceeded:
        raise ValueError("当前音轨组合输入或临时输出超出已标定 CPU 音频预算：" + '；'.join(exceeded))
    validate_audio_source_specs(inputs, budget)
    return resources


class TaskMediaRequired(RuntimeError):
    """A public operation reached its media boundary before device admission."""
    def __init__(self, kind):
        super().__init__("任务媒体必须先提交设备许可")
        self.kind = kind


class TaskAudio:
    def __init__(self, repository, artifact_store, temporary_root: Path, *, picture_renderer=None):
        self.repository = repository
        self.artifact_store = artifact_store
        self.temporary_root = temporary_root
        self.picture_renderer = picture_renderer

    def mix_facts(self, workspace_id, task_id, timeline, duration_seconds, *, actor,
                  normalize_voice_sources, budget, apply_master=True, assembly_job_id=None, connection=None,
                  output_sample_count=None):
        sources = self._mix_sources(workspace_id, task_id, timeline, actor=actor, budget=budget,
            assembly_job_id=assembly_job_id, connection=connection)
        resources = audio_mix_resources(sources['inputs'], [timeline], duration_seconds,
            normalize_voice_sources=normalize_voice_sources, budget=budget, apply_master=apply_master,
            output_sample_count=output_sample_count)
        return {'sources': sources['sources'], 'resources': resources}

    def _mix_sources(self, workspace_id, task_id, timeline, *, actor, budget, assembly_job_id=None, connection=None):
        """Freeze authorized current source content without declaring a whole-work decode."""
        if budget is None:
            raise ValueError("CPU 音频处理预算尚未标定，未启动媒体")
        if actor is None:
            raise PermissionError("音轨组合缺少当前成员许可身份")
        audible = [clip for track in timeline.tracks if not track.muted for clip in track.clips if not clip.muted]
        uses = Counter(clip.source_ref for clip in audible)
        inputs, files = [], []
        records = {record.logical_ref: record for record in self.repository.execution_records.list_artifact_records(workspace_id, task_id, connection=connection)}
        for reference in dict.fromkeys(clip.source_ref for track in timeline.tracks for clip in track.clips):
            path = self.source(workspace_id, task_id, reference, actor=actor, connection=connection).resolve()
            stat = path.stat()
            if stat.st_size <= 0 or stat.st_size > budget.max_input_bytes:
                raise ValueError("音轨源文件为空或超出已标定输入字节上限")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            after = path.stat()
            if any(getattr(after, field) != getattr(stat, field) for field in
                   ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino", "st_dev")):
                raise ValueError("音轨来源在读取摘要期间发生变化")
            record = records.get(reference)
            if record is not None:
                producer = self.repository.job_records.get_job_run(workspace_id, record.producer_job_id, connection=connection)
                transform = self._transform_for(workspace_id, task_id, record, connection=connection)
                own_fragment = (producer.id == assembly_job_id and record.kind == 'audio_assembly_fragment'
                    and transform.transform_type == 'audio_assembly')
                if (producer.task_id != task_id or producer.status != "succeeded" and not own_fragment
                        or transform.status != "succeeded" or transform.task_id != task_id
                        or transform.job_id != producer.id
                        or (record.sha256, record.size_bytes) != (digest.hexdigest(), stat.st_size)):
                    raise ValueError("音轨产物与实际已完成来源回执不一致")
            fact = {"reference": reference, "path": str(path), "size_bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
                    "inode": stat.st_ino, "device": stat.st_dev, "sha256": digest.hexdigest(),
                    "producer_job_id": record.producer_job_id if record is not None else None,
                    **self.source_metadata(workspace_id, task_id, reference, connection=connection)}
            files.append(fact)
            if reference in uses:
                inputs.append({**fact, "uses": uses[reference]})
        return {"sources": files, "inputs": inputs}

    def _legacy_assembly_plan_id(self, job, montage):
        from app.creation.assembly_manifest import AUDIO_ASSEMBLY_VERSION
        from app.creation.editing.metadata_use_case import effective_audio_timeline
        graph = montage.audio_timeline or (effective_audio_timeline(montage) if montage.narration_audio_path else None)
        # Keep the original locator; the manifest payload versions its output clock.
        digest = media_digest([AUDIO_MIX_CONTRACT_VERSION, AUDIO_ASSEMBLY_VERSION, job.id, graph.model_dump() if graph else None,
            montage.duration_seconds, montage.narration_linkage_created_audio_timeline])
        return f'event_{job.id}_audio_assembly_{digest}'

    def _assembly_plan_id(self, job, montage, *, connection=None):
        from app.creation.editing.metadata_use_case import effective_audio_timeline
        generation = current_render_generation(self.repository, job, connection=connection)
        if generation is None:
            return self._legacy_assembly_plan_id(job, montage)
        binding = self.repository.execution_records.get_task_event(job.workspace_id,
            render_namespace(f'event_{job.id}_render_audio_binding', generation), connection=connection)
        if (binding.task_id != job.task_id or binding.job_id != job.id
                or stored_render_scope(binding.payload) != render_generation_fields(generation)):
            raise PermissionError('本次重新输出的音频清单绑定不属于原范围')
        graph_digest = media_digest([(montage.audio_timeline or effective_audio_timeline(montage)).model_dump(), round(montage.duration_seconds*1000),
            montage.narration_linkage_created_audio_timeline])
        if binding.payload['graph_digest'] != graph_digest:
            raise ValueError('本次重新输出的音轨或逻辑时钟已变化')
        for source in binding.payload['sources']:
            observed = Path(source['path']).stat()
            if any(source[field] != getattr(observed, attr) for field, attr in (
                    ('size_bytes','st_size'),('mtime_ns','st_mtime_ns'),('ctime_ns','st_ctime_ns'),
                    ('inode','st_ino'),('device','st_dev'))):
                raise ValueError('本次重新输出的原音频文件已变化')
        selected = self.repository.execution_records.get_task_event(job.workspace_id, binding.payload['manifest_event_id'], connection=connection)
        if ((selected.task_id, selected.job_id) != (job.task_id, job.id)
                or selected.payload['input_digest'] != job.input_digest
                or selected.payload['manifest']['digest'] != binding.payload['manifest_digest']):
            raise PermissionError('本次音频绑定与原清单摘要不一致')
        return selected.id

    def _prepare_generation_audio(self, job, montage, generation, *, actor, budget, output_sample_count, connection):
        from app.creation.assembly_manifest import AudioAssemblyManifest, plan_audio_assembly
        from app.device_scheduler import RecoveryBlocked
        binding_id = render_namespace(f'event_{job.id}_render_audio_binding', generation)
        try:
            self.repository.execution_records.get_task_event(job.workspace_id, binding_id, connection=connection)
        except KeyError:
            pass
        else:
            return self._assembly_plan_id(job, montage, connection=connection)
        if type(output_sample_count) is not int or output_sample_count <= 0:
            raise ValueError('本次重新输出尚未封存实际画面采样时钟')
        sources = self._mix_sources(job.workspace_id, job.task_id, montage.audio_timeline,
            actor=actor, budget=budget, connection=connection)
        refs = {clip.source_ref for track in montage.audio_timeline.tracks if not track.muted
            for clip in track.clips if not clip.muted}
        hashes = {item['reference']:item['sha256'] for item in sources['sources'] if item['reference'] in refs}
        graph_digest = media_digest([montage.audio_timeline.model_dump(), round(montage.duration_seconds*1000),
            montage.narration_linkage_created_audio_timeline])
        basis = media_digest([graph_digest, hashes, output_sample_count])
        candidates = [self._legacy_assembly_plan_id(job, montage)]
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            rows = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_render_audio_bound' ORDER BY rowid DESC", (job.workspace_id,job.id)).fetchall()
        candidates.extend(json.loads(row['payload_json'])['manifest_event_id'] for row in rows
            if json.loads(row['payload_json'])['audio_input_digest'] == basis)
        selected = None
        for event_id in dict.fromkeys(candidates):
            try:
                previous = self.repository.execution_records.get_task_event(job.workspace_id, event_id, connection=connection)
            except KeyError:
                continue
            manifest = AudioAssemblyManifest.from_payload(previous.payload['manifest'])
            manifest.validate_scope(job.workspace_id, job.task_id, job.id)
            expected = plan_audio_assembly(montage.audio_timeline, round(montage.duration_seconds*1000),
                workspace_id=job.workspace_id, task_id=job.task_id, producer_job_id=job.id,
                source_hashes=hashes, max_input_count=budget.max_input_count,
                normalize_voice_sources=montage.narration_linkage_created_audio_timeline,
                output_sample_count=manifest.output_sample_count)
            if manifest.sample_count != output_sample_count or expected.recipe_digest != manifest.recipe_digest:
                continue
            try:
                self.prepared_master_facts(job, montage, actor=actor, connection=connection, manifest_event_id=event_id)
            except (KeyError, ValueError) as exc:
                raise RecoveryBlocked('原相同音频master尚无完整成功回执，仅可回读原请求') from exc
            selected = previous
            break
        if selected is None:
            manifest = plan_audio_assembly(montage.audio_timeline, round(montage.duration_seconds*1000),
                workspace_id=job.workspace_id, task_id=job.task_id, producer_job_id=job.id,
                source_hashes=hashes, max_input_count=budget.max_input_count,
                normalize_voice_sources=montage.narration_linkage_created_audio_timeline, output_sample_count=output_sample_count)
            selected = self.repository.execution_records.append_task_event(TaskEvent(
                render_namespace(self._legacy_assembly_plan_id(job, montage), generation), job.workspace_id,job.task_id,job.id,
                'creation_audio_assembly_plan', {'input_digest':job.input_digest,'manifest':manifest.payload(),
                    **render_generation_fields(generation)}), connection=connection)
        self.repository.execution_records.append_task_event(TaskEvent(binding_id,job.workspace_id,job.task_id,job.id,
            'creation_render_audio_bound', {**render_generation_fields(generation), 'graph_digest':graph_digest,
                'audio_input_digest':basis,'sources':sources['sources'], 'manifest_event_id':selected.id,
                'manifest_digest':selected.payload['manifest']['digest']}), connection=connection)
        return selected.id

    def render_assembly(self, job, montage, *, actor, budget, prepare=False, connection=None, force_master=False,
                        output_sample_count=None):
        from app.creation.assembly_manifest import AudioAssemblyManifest, plan_audio_assembly
        if montage.audio_timeline is None:
            if not force_master and output_sample_count is None:
                try:
                    self.repository.execution_records.get_task_event(job.workspace_id, self._assembly_plan_id(job, montage, connection=connection), connection=connection)
                except KeyError:
                    return None
            from dataclasses import replace
            from app.creation.editing.metadata_use_case import effective_audio_timeline
            montage = replace(montage, audio_timeline=effective_audio_timeline(montage))
        if budget is None:
            raise ValueError('音频组装缺少已标定CPU音频预算')
        audible = [(track, clip) for track in montage.audio_timeline.tracks if not track.muted
            for clip in track.clips if not clip.muted]
        generation = current_render_generation(self.repository, job, connection=connection)
        event_id = (self._prepare_generation_audio(job, montage, generation, actor=actor, budget=budget,
            output_sample_count=output_sample_count, connection=connection) if generation is not None and prepare
            else self._assembly_plan_id(job, montage, connection=connection))
        try:
            event = self.repository.execution_records.get_task_event(job.workspace_id, event_id, connection=connection)
        except KeyError:
            if len(audible) <= budget.max_input_count and not force_master and output_sample_count is None:
                return None
            if not prepare:
                raise ValueError('原音频组装清单尚未准备，未启动成片')
            sources = self._mix_sources(job.workspace_id, job.task_id, montage.audio_timeline,
                actor=actor, budget=budget, connection=connection)
            refs = {clip.source_ref for _, clip in audible}
            manifest = plan_audio_assembly(montage.audio_timeline, round(montage.duration_seconds * 1000),
                workspace_id=job.workspace_id, task_id=job.task_id, producer_job_id=job.id,
                source_hashes={item['reference']: item['sha256']
                    for item in sources['sources'] if item['reference'] in refs},
                max_input_count=budget.max_input_count,
                normalize_voice_sources=montage.narration_linkage_created_audio_timeline,
                output_sample_count=output_sample_count)
            event = self.repository.execution_records.append_task_event(TaskEvent(id=event_id, workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type='creation_audio_assembly_plan',
                payload={'input_digest': job.input_digest, 'manifest': manifest.payload()}), connection=connection)
        if (event.job_id != job.id or event.task_id != job.task_id or event.payload['input_digest'] != job.input_digest):
            raise PermissionError('音频组装清单不属于原执行输入')
        manifest = AudioAssemblyManifest.from_payload(event.payload['manifest'])
        manifest.validate_scope(job.workspace_id, job.task_id, job.id)
        if output_sample_count is not None and manifest.sample_count != output_sample_count:
            raise ValueError('原音频组装清单采样时钟与原画面master不一致；原清单和产物保持不变')
        return manifest

    def next_assembly_unit(self, job, montage, *, actor, budget, connection=None, request=None):
        manifest = self.render_assembly(job, montage, actor=actor, budget=budget, connection=connection)
        if manifest is None:
            return None
        if request is not None and request.unit_key.startswith('render:audio-assembly:'):
            unit_id = request.resources['assembly_unit_id']
        else:
            unit_id = None
            for unit in manifest.units:
                try:
                    self.repository.execution_records.get_artifact_record(job.workspace_id, f'artifact_audio_assembly_{unit.id}', connection=connection)
                except KeyError:
                    unit_id = unit.id
                    break
            if unit_id is None:
                return None
        unit, facts = self.assembly_unit_facts(job.workspace_id, job.task_id, manifest, unit_id,
            actor=actor, budget=budget, job_id=job.id, connection=connection)
        key = f'render:audio-assembly:{unit.id}:{media_digest(facts)}'
        return key, facts['resources']

    def prepared_master(self, job, montage, *, actor, budget, connection=None):
        manifest = self.render_assembly(job, montage, actor=actor, budget=budget, connection=connection)
        if manifest is None:
            return None
        unit = manifest.final
        record = self.repository.execution_records.get_artifact_record(job.workspace_id, f'artifact_audio_assembly_{unit.id}', connection=connection)
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_audio_assembly_{unit.id}', connection=connection)
        if (record.task_id != job.task_id or record.producer_job_id != job.id or transform.job_id != job.id
                or transform.task_id != job.task_id or record.logical_ref != unit.output_ref(job.task_id)
                or transform.status != 'succeeded' or transform.input_digest != unit.id
                or transform.parameters['manifest_digest'] != manifest.digest
                or transform.parameters.get('output_sample_count') != manifest.output_sample_count):
            raise ValueError('统一master未完整完成，不能作为成片输入')
        sources = self._mix_sources(job.workspace_id, job.task_id, montage.audio_timeline,
            actor=actor, budget=budget, connection=connection)
        expected = dict(manifest.source_hashes)
        if any(item['sha256'] != expected[item['reference']] for item in sources['sources'] if item['reference'] in expected):
            raise ValueError('原音轨输入已变化，不能重用旧master')
        return manifest, record

    def prepared_master_facts(self, job, montage, *, actor, connection=None, manifest_event_id=None):
        """Read the sealed master and its original producer receipt, without mixing or media probes."""
        from dataclasses import asdict
        from app.creation.assembly_manifest import AudioAssemblyManifest
        import stat
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            event = self.repository.execution_records.get_task_event(job.workspace_id,
                manifest_event_id or self._assembly_plan_id(job, montage, connection=conn), connection=conn)
            if (event.job_id != job.id or event.task_id != job.task_id
                    or event.payload['input_digest'] != job.input_digest):
                raise PermissionError('唯一音频master不属于原Job输入')
            manifest = AudioAssemblyManifest.from_payload(event.payload['manifest'])
            manifest.validate_scope(job.workspace_id, job.task_id, job.id)
            unit = manifest.final
            record = self.repository.execution_records.get_artifact_record(job.workspace_id, f'artifact_audio_assembly_{unit.id}', connection=conn)
            transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_audio_assembly_{unit.id}', connection=conn)
            if (not unit.master or record.task_id != job.task_id or record.producer_job_id != job.id
                    or record.logical_ref != unit.output_ref(job.task_id) or transform.job_id != job.id
                    or transform.task_id != job.task_id or transform.status != 'succeeded'
                    or transform.input_digest != unit.id or transform.output_refs != [record.logical_ref]
                    or transform.parameters['manifest_digest'] != manifest.digest
                    or not transform.parameters['master'] or transform.parameters['sample_rate'] != 48000
                    or transform.parameters['channels'] != 2
                    or transform.parameters['duration_ms'] != manifest.duration_ms
                    or transform.parameters.get('output_sample_count') != manifest.output_sample_count):
                raise ValueError('唯一音频master缺少原成功变换与完整采样时钟')
            producer = conn.execute("SELECT r.id,r.last_grant_generation FROM device_work_requests r "
                "JOIN task_events e ON e.workspace_id=r.workspace_id AND e.job_id=r.owner_id "
                "AND e.event_type='task_execution_unit_settled' "
                "AND json_extract(e.payload_json,'$.usage_id')=r.id||':'||r.last_grant_generation "
                "WHERE r.workspace_id=? AND r.owner_type='creation_job' AND r.owner_id=? AND r.input_digest=? "
                "AND (r.state='done' OR (r.state='granted' AND ?)) AND json_extract(r.resources_json,'$.assembly_unit_id')=? "
                "AND json_extract(r.resources_json,'$.assembly_manifest_digest')=? "
                "ORDER BY r.updated_at DESC,r.id DESC LIMIT 1",
                (job.workspace_id, job.id, job.input_digest, int(connection is not None and conn.in_transaction), unit.id, manifest.digest)).fetchone()
            if producer is None:
                raise ValueError('唯一音频master没有原请求与原generation的完成消耗回执')
            self.validate_source_access(job.workspace_id, job.task_id, record.logical_ref, actor, connection=conn)
            self.validate_material_use(job.workspace_id, job.task_id, record.logical_ref, connection=conn)
        path = self.artifact_store.resolve_file(record.logical_ref)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('唯一音频master不是普通文件')
        identity = {'device': info.st_dev, 'inode': info.st_ino, 'size_bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}
        if identity['size_bytes'] != record.size_bytes:
            raise ValueError('唯一音频master大小已变化')
        return {'parent': {'binding': {'artifact_id': record.id, 'sha256': record.sha256,
            'size_bytes': record.size_bytes, 'producer_request_id': producer['id'],
            'producer_generation': producer['last_grant_generation'], 'transform_id': transform.id,
            'transform_digest': media_digest(asdict(transform))}, 'clock': {
                'sample_count': manifest.sample_count, 'sample_rate': 48000,
                'channels': 2, 'sample_format': 's16le'}}, 'path': str(path),
            'logical_ref': record.logical_ref, 'file_identity': identity}

    def master_mix_facts(self, job, montage, *, actor, budget, connection=None):
        from app.audio_timeline import TimelineAudioClip, TimelineAudioTrack
        prepared = self.prepared_master(job, montage, actor=actor, budget=budget, connection=connection)
        if prepared is None:
            return self.mix_facts(job.workspace_id, job.task_id, montage.audio_timeline,
                montage.duration_seconds, actor=actor, budget=budget,
                normalize_voice_sources=montage.narration_linkage_created_audio_timeline, connection=connection)
        manifest, record = prepared
        graph = AudioTimeline(tracks=[TimelineAudioTrack(id='master', name='已完成统一master', role='original',
            clips=[TimelineAudioClip(id='master-clip', name='统一master', source_ref=record.logical_ref,
                source_start_ms=0, source_end_ms=manifest.duration_ms, timeline_start_ms=0)])])
        facts = self.mix_facts(job.workspace_id, job.task_id, graph, montage.duration_seconds,
            actor=actor, budget=budget, normalize_voice_sources=False, assembly_job_id=job.id, connection=connection,
            output_sample_count=manifest.output_sample_count)
        facts['resources']['prepared_master'] = {'manifest_digest': manifest.digest,
            'artifact_id': record.id, 'logical_ref': record.logical_ref, 'sha256': record.sha256}
        return facts

    def assembly_unit_facts(self, workspace_id, task_id, manifest, unit_id, *, actor, budget, job_id, connection=None):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id, connection=connection)
        job = self.repository.job_records.get_job_run(workspace_id, job_id, connection=connection)
        manifest.validate_scope(workspace_id, task_id, job_id)
        if (actor.workspace_id != workspace_id or task.owner_user_id != (actor.user_id or actor.member_id)
                or job.task_id != task_id):
            raise PermissionError('音频组装单元不属于当前任务与成员')
        unit = next((item for item in manifest.units if item.id == unit_id), None)
        if unit is None:
            raise ValueError('组装单元不属于原不可变清单')
        manifest.validate_unit(unit)
        for dependency in unit.dependencies:
            record = self.repository.execution_records.get_artifact_record(workspace_id, f'artifact_audio_assembly_{dependency}', connection=connection)
            transform = self.repository.lineage_records.get_transform_run(workspace_id, f'transform_audio_assembly_{dependency}', connection=connection)
            if (record.task_id != task_id or record.producer_job_id != job_id or transform.task_id != task_id
                    or transform.parameters.get('manifest_digest') != manifest.digest or transform.status != 'succeeded'
                    or transform.input_digest != dependency or record.logical_ref not in transform.output_refs
                    or transform.job_id != record.producer_job_id
                    or transform.parameters.get('output_sample_count') != manifest.output_sample_count):
                raise ValueError('浮点上游缺少同任务完整变换回执')
        facts = self.mix_facts(workspace_id, task_id, unit.graph, manifest.duration_ms / 1000,
            actor=actor, normalize_voice_sources=unit.normalize_voice_sources, budget=budget,
            apply_master=unit.master, assembly_job_id=job_id, connection=connection,
            output_sample_count=manifest.output_sample_count)
        original_hashes = dict(manifest.source_hashes)
        if any(source['sha256'] != original_hashes[source['reference']] for source in facts['sources']
                if source['reference'] in original_hashes):
            raise ValueError('原音频来源内容已变化，不能继续旧组装清单')
        resources = {**facts['resources'], 'assembly_manifest_digest': manifest.digest,
            **render_generation_fields(current_render_generation(self.repository, job, connection=connection)),
            'assembly_unit_id': unit.id, 'apply_master': unit.master,
            'input_timeline_version_id': task.current_timeline_version_id}
        return unit, {**facts, 'resources': resources}

    def execute_assembly_unit(self, workspace_id, task_id, manifest, unit_id, *, actor, budget, context, on_render_started=None):
        from app.audio_timeline import _audio_grant_resources, render_audio_mix
        context.check()
        if context.scheduler.database_path != Path(self.repository.database_path).resolve():
            raise PermissionError('音频组装许可与原任务数据库不一致')
        job = self.repository.job_records.get_job_run(workspace_id, context.grant.owner_id)
        def measured_output(size, *, reused_ref=None):
            self.repository.execution_records.append_task_event(TaskEvent(id=f'event_{job.id}_audio_assembly_measured_{context.grant.generation}',
                workspace_id=workspace_id, task_id=task_id, job_id=job.id, event_type='audio_assembly_output_measured',
                payload={'usage_id': f'{context.grant.request_id}:{context.grant.generation}',
                    'unit_id': unit_id, 'generated_bytes': size, 'reused_artifact_ref': reused_ref}))
        unit, facts = self.assembly_unit_facts(workspace_id, task_id, manifest, unit_id,
            actor=actor, budget=budget, job_id=job.id)
        declared = _audio_grant_resources(context.scheduler, context.grant)
        if declared != facts['resources']:
            raise PermissionError('音频组装单元与本次原许可不一致')
        artifact_id = f'artifact_audio_assembly_{unit.id}'
        try:
            existing = self.repository.execution_records.get_artifact_record(workspace_id, artifact_id)
        except KeyError:
            existing = None
        if existing is not None:
            published = self.artifact_store.describe(existing.logical_ref)
            if (existing.task_id != task_id or existing.producer_job_id != job.id or existing.logical_ref != unit.output_ref(task_id)
                    or (published.sha256, published.size_bytes) != (existing.sha256, existing.size_bytes)):
                raise ValueError('已完成音频组装内容发生变化，不能静默重用')
            transform = self.repository.lineage_records.get_transform_run(workspace_id, f'transform_audio_assembly_{unit.id}')
            if (transform.status != 'succeeded' or transform.input_digest != unit.id or transform.job_id != job.id
                    or transform.parameters.get('manifest_digest') != manifest.digest or existing.logical_ref not in transform.output_refs
                    or transform.parameters.get('output_sample_count') != manifest.output_sample_count):
                raise ValueError('音频组装成果缺少完整变换回执')
            context.check()
            measured_output(0, reused_ref=existing.logical_ref)
            return existing
        started = utc_now()
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='audio-assembly-', dir=self.temporary_root) as directory:
            output = Path(directory) / 'mix.wav'
            if on_render_started is not None:
                on_render_started()
            render_audio_mix(unit.graph, manifest.duration_ms / 1000,
                lambda ref: self.source(workspace_id, task_id, ref, actor=actor), output,
                scheduler=context.scheduler, grant=context.grant, cancel_event=context.cancel_event,
                deadline_monotonic=context.deadline_monotonic, max_output_bytes=context.max_output_bytes,
                threads=context.threads, normalize_voice_sources=unit.normalize_voice_sources,
                apply_master=unit.master, output_measured=measured_output,
                output_sample_count=manifest.output_sample_count)
            measured = probe_audio_metadata(output, scheduler=context.scheduler, grant=context.grant,
                cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic, output=True,
                exact_output_clock=manifest.output_sample_count is not None)
            if (measured['sample_rate'] != 48000 or measured['channels'] != 2
                    or (measured['sample_count'] if manifest.output_sample_count is not None
                        else round(measured['duration_seconds'] * 48000)) != manifest.sample_count):
                raise ValueError('组装音频未完整覆盖原采样时钟，不发布截断成果')
            context.check()
            published = self.artifact_store.publish_file(unit.output_ref(task_id), output)
        record = ArtifactRecord(id=artifact_id, workspace_id=workspace_id, task_id=task_id,
            kind='audio_assembly_fragment', logical_ref=published.logical_ref, sha256=published.sha256,
            size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=list(unit.input_refs))
        transform = TransformRun(id=f'transform_audio_assembly_{unit.id}', workspace_id=workspace_id,
            task_id=task_id, job_id=job.id, transform_type='audio_assembly', input_digest=unit.id,
            input_refs=list(unit.input_refs), output_refs=[record.logical_ref], engine=AUDIO_MIX_CONTRACT_VERSION,
            parameters={'manifest_digest': manifest.digest, 'master': unit.master,
                **(stored_render_scope(declared) or {}),
                'duration_ms': manifest.duration_ms, 'sample_rate': 48000, 'channels': 2,
                **({'output_sample_count':manifest.output_sample_count} if manifest.output_sample_count is not None else {})},
            status='succeeded', started_at=started, finished_at=utc_now())
        edges = [DependencyEdge(id=f'edge_audio_assembly_{unit.id}_{index}', workspace_id=workspace_id,
            task_id=task_id, upstream_ref=reference, downstream_ref=record.logical_ref,
            created_by_transform_id=transform.id) for index, reference in enumerate(unit.input_refs)]
        context.check()
        return self.repository.task_media_publication.record_task_media_publication(record, transform, edges)

    def source(self, workspace_id: str, task_id: str, reference: str, *, actor=None, connection=None) -> Path:
        self.repository.task_records.get_creation_task(workspace_id, task_id, connection=connection)
        active = TASK_MEDIA_CONTEXT.get()
        actor = actor or (active[3] if active is not None else None)
        if actor is not None:
            self.validate_source_access(workspace_id, task_id, reference, actor, connection=connection)
        self.validate_material_use(workspace_id, task_id, reference, connection=connection)
        if reference.startswith("narration://artifact/"):
            record = self.repository.narration_artifacts.get_narration_artifact(workspace_id, reference.removeprefix("narration://artifact/"), connection=connection)
            if record is None or record.task_id != task_id:
                raise ValueError("配音不属于当前任务")
            return self.repository.narration_artifacts.resolve_authorized_narration_audio_reference(workspace_id, reference, connection=connection)
        if reference.startswith("workspace://"):
            return self.repository.resolve_workspace_media_reference(workspace_id, reference, require_registered=True, connection=connection)
        records = self.repository.execution_records.list_artifact_records(workspace_id, task_id, connection=connection)
        if not any(record.logical_ref == reference and record.kind in {"audio_source", "audio_preview", "task_recording", "audio_assembly_fragment"} for record in records):
            raise ValueError("音频产物不属于当前任务")
        return self.artifact_store.resolve_file(reference)

    def validate_source_access(self, workspace_id, task_id, reference, actor, *, connection=None):
        from app.creation_execution import CreationExecutionActor
        from app.source_policy import knowledge_item_readable as content_readable
        records = {item.logical_ref: item for item in self.repository.execution_records.list_artifact_records(workspace_id, task_id, connection=connection)}
        pending, visited = [reference], set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current.startswith("workspace://"):
                path = self.repository.resolve_workspace_media_reference(workspace_id, current, require_registered=True, connection=connection)
                with self.repository._connect() if connection is None else nullcontext(connection) as conn:
                    row = conn.execute("SELECT payload,visibility FROM material_assets WHERE workspace_id=? AND file_path=?", (workspace_id, str(path))).fetchone()
                payload = json.loads(row["payload"]) if row is not None else {}
                if row is None or not content_readable(user_id=actor.user_id or actor.member_id, local_diagnostic=False,
                        visibility=row["visibility"], owner_user_id=payload.get("owner_user_id")):
                    raise PermissionError("当前成员不能读取音频来源素材")
            elif current in records:
                record = records[current]
                job = self.repository.job_records.get_job_run(workspace_id, record.producer_job_id, connection=connection)
                if job.kind == TASK_MEDIA_KIND:
                    event = self.repository.execution_records.get_task_event(workspace_id, f"event_{job.id}_start", connection=connection)
                    if CreationExecutionActor.from_event(workspace_id, event.payload) != actor:
                        raise PermissionError("当前成员不能读取此音频产物")
                pending.extend(record.input_refs)

    def recording(self, workspace_id: str, task_id: str, reference: str,
                  *, verify_digest: bool = False, actor=None, context=None) -> tuple[ArtifactRecord, int]:
        """Return a persisted recording and its server-measured duration."""
        record = next((item for item in self.repository.execution_records.list_artifact_records(workspace_id, task_id)
                       if item.logical_ref == reference and item.kind == "task_recording"), None)
        if record is None:
            raise ValueError("当前任务没有这条电脑录音")
        job = self.repository.job_records.get_job_run(workspace_id, record.producer_job_id)
        transform = self._transform_for(workspace_id, task_id, record)
        duration_ms = transform.parameters.get("duration_ms")
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        saved_script_id = transform.parameters.get("script_id")
        saved_script_digest = transform.parameters.get("script_digest")
        current_script_digest = self.repository.script_records.confirmed_creation_task_script_digest(
            workspace_id, task_id, task.confirmed_script_id,
        )
        saved_montage_id = transform.parameters.get("montage_plan_id")
        if (job.task_id != task_id or job.kind not in {"task_recording", TASK_MEDIA_KIND} or job.status != "succeeded"
                or transform.transform_type != "task_recording" or transform.task_id != task_id
                or transform.job_id != job.id or transform.status != "succeeded"
                or transform.output_refs != [reference]
                or (saved_montage_id is not None
                    and saved_montage_id != task.montage_plan_id)
                or type(duration_ms) is not int or duration_ms <= 0):
            raise ValueError("电脑录音尚未完整保存")
        if (not task.confirmed_script_id or saved_script_id != task.confirmed_script_id
                or not current_script_digest or saved_script_digest != current_script_digest):
            raise ValueError("当前确认文案已变化，不能采用旧录音；请为当前文案重新录制")
        path = self.source(workspace_id, task_id, reference, actor=actor)
        if path.stat().st_size != record.size_bytes:
            raise ValueError("电脑录音文件已变化")
        if verify_digest:
            if context is None:
                active = TASK_MEDIA_CONTEXT.get()
                if active is None:
                    raise TaskMediaRequired("audio_probe")
                context = active[0]
            context.check()
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != record.sha256:
                raise ValueError("电脑录音文件已变化")
        return record, duration_ms

    def validate_material_use(self, workspace_id: str, task_id: str, reference: str, *, connection=None) -> None:
        records = {item.logical_ref: item for item in self.repository.execution_records.list_artifact_records(workspace_id, task_id, connection=connection)}
        pending, visited = [reference], set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current.startswith("workspace://"):
                self.repository.validate_material_reference_use(workspace_id, current, connection=connection)
            elif current in records:
                pending.extend(records[current].input_refs)

    def validate(self, workspace_id: str, task_id: str, timeline: AudioTimeline, duration_seconds: float,
                 *, draft_only: bool = False, actor=None, context=None, job=None):
        durations = {}
        for track in timeline.tracks:
            for clip in track.clips:
                if clip.source_ref not in durations:
                    path = self.source(workspace_id, task_id, clip.source_ref, actor=actor)
                    if not draft_only:
                        active = TASK_MEDIA_CONTEXT.get() if context is None else None
                        producing_job = job if context is not None else active[1] if active is not None else None
                        produced_here = producing_job is not None and any(
                            record.logical_ref == clip.source_ref and record.producer_job_id == producing_job.id
                            for record in self.repository.execution_records.list_artifact_records(workspace_id, task_id))
                        measured = self._probe_metadata(path, output=produced_here, context=context)
                        durations[clip.source_ref] = measured.get('source_duration_seconds', measured['duration_seconds'])
                    elif clip.source_ref.startswith("narration://artifact/"):
                        artifact = self.repository.narration_artifacts.get_narration_artifact(
                            workspace_id, clip.source_ref.removeprefix("narration://artifact/"))
                        durations[clip.source_ref] = artifact.duration_seconds
                    elif clip.source_ref.startswith("workspace://"):
                        with self.repository._connect() as conn:
                            row = conn.execute(
                                "SELECT payload FROM material_assets WHERE workspace_id=? AND file_path=?",
                                (workspace_id, str(path))).fetchone()
                        durations[clip.source_ref] = json.loads(row["payload"]).get("duration_seconds") if row else None
                    else:
                        record = next(item for item in self.repository.execution_records.list_artifact_records(workspace_id, task_id)
                                      if item.logical_ref == clip.source_ref)
                        transform = self._transform_for(workspace_id, task_id, record)
                        measured_ms = transform.parameters.get("duration_ms")
                        durations[clip.source_ref] = measured_ms / 1000 if type(measured_ms) is int and measured_ms > 0 else None
                    if durations[clip.source_ref] is None:
                        raise ValueError("音频缺少已保存的时长，请重新导入后再编辑草稿")
                if clip.source_end_ms > round(durations[clip.source_ref] * 1000) + 20:
                    raise ValueError("音频裁剪区间超出原文件时长")
                if clip.timeline_start_ms + clip.duration_ms > round(duration_seconds * 1000):
                    raise ValueError("音频片段超出成片范围")

    def _transform_for(self, workspace_id, task_id, record, *, connection=None):
        transform = next((item for item in self.repository.lineage_records.list_transform_runs(workspace_id, task_id, connection=connection)
                          if item.job_id == record.producer_job_id and record.logical_ref in item.output_refs), None)
        if transform is None:
            raise ValueError("媒体产物尚无完整变换回执")
        return transform

    def probe(self, path, *, output=True, context=None):
        return self._probe_metadata(path, output=output, context=context)['duration_seconds']

    def _probe_metadata(self, path, *, output=True, context=None):
        if context is None:
            active = TASK_MEDIA_CONTEXT.get()
            if active is None:
                raise TaskMediaRequired("audio_probe")
            context = active[0]
        context.check()
        return probe_audio_metadata(path, scheduler=context.scheduler, grant=context.grant,
            cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic, output=output)

    def source_metadata(self, workspace_id, task_id, reference, *, connection=None):
        data = {"duration_seconds": None, "sample_rate": None, "channels": None}
        if reference.startswith("narration://artifact/"):
            item = self.repository.narration_artifacts.get_narration_artifact(workspace_id, reference.removeprefix("narration://artifact/"), connection=connection)
            if item is not None:
                data.update(duration_seconds=item.duration_seconds, sample_rate=item.sample_rate)
                if item.source_artifact_ref:
                    nested = self.source_metadata(workspace_id, task_id, item.source_artifact_ref, connection=connection)
                    # A narration artifact stays audio even if its provenance is a video.
                    data.update({key: value for key, value in nested.items() if key in data and value is not None})
        elif reference.startswith("workspace://"):
            path = self.repository.resolve_workspace_media_reference(workspace_id, reference, require_registered=True, connection=connection)
            with self.repository._connect() if connection is None else nullcontext(connection) as conn:
                row = conn.execute("SELECT payload FROM material_assets WHERE workspace_id=? AND file_path=?", (workspace_id, str(path))).fetchone()
            if row is not None:
                asset = json.loads(row["payload"])
                data["duration_seconds"] = asset.get("duration_seconds")
                if asset.get("media_type") == "video":
                    data["media_type"] = "video"
        else:
            record = next((item for item in self.repository.execution_records.list_artifact_records(workspace_id, task_id, connection=connection)
                           if item.logical_ref == reference), None)
            if record is not None:
                parameters = self._transform_for(workspace_id, task_id, record, connection=connection).parameters
                data = {"duration_seconds": (parameters['output_sample_count'] / 48000
                            if 'output_sample_count' in parameters else parameters["duration_ms"] / 1000),
                        "sample_rate": parameters.get("sample_rate"), "channels": parameters.get("channels")}
        return data

    def _measure_publication(self,path,operation,inputs,context):
        if operation in {"picture_preview","clip_preview"}:
            from app.montage import probe_preview_video
            from app.canvas import canvas_size,preview_size
            if operation == "clip_preview":
                clip = inputs["clip"]
                size = preview_size(inputs["aspect_ratio"])
                duration = min(inputs["max_seconds"],clip["timeline_end_seconds"]-clip["timeline_start_seconds"])
            else:
                size = canvas_size(inputs["plan"]["aspect_ratio"])
                duration = inputs["plan"]["duration_seconds"]
            return probe_preview_video(path,context,expected_size=size,expected_duration_seconds=duration,silent=operation=="clip_preview")
        return probe_audio_metadata(path,scheduler=context.scheduler,grant=context.grant,
            cancel_event=context.cancel_event,deadline_monotonic=context.deadline_monotonic,output=True)

    def validate_publications(self, job, event, result, context):
        from app.canvas import canvas_size
        outputs = []
        for record in self.repository.execution_records.list_artifact_records(job.workspace_id, job.task_id):
            if record.producer_job_id != job.id:
                continue
            context.check()
            publication = self.artifact_store.describe(record.logical_ref)
            if (publication.sha256, publication.size_bytes) != (record.sha256, record.size_bytes):
                raise ValueError('已提交预览产物内容发生变化')
            path = self.artifact_store.resolve_file(record.logical_ref)
            stat = path.stat()
            outputs.append({'reference': record.logical_ref, 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns})
            if path.suffix == '.mp4':
                from app.montage import probe_preview_video
                with self.repository._connect() as conn:
                    row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND event_type='task_media_input' AND id=?", (job.workspace_id, job.id, f"event_{job.id}_{record.id.removeprefix('artifact_')}_input")).fetchone()
                inputs = json.loads(row['payload_json'])['media_input']
                if 'clip' in inputs:
                    from app.canvas import preview_size
                    clip = inputs['clip']
                    size = preview_size(inputs['aspect_ratio'])
                    duration = min(5, clip['timeline_end_seconds'] - clip['timeline_start_seconds'])
                    silent = True
                else:
                    size = canvas_size(inputs['plan']['aspect_ratio'])
                    duration = inputs['plan']['duration_seconds']
                    silent = False
                probe_preview_video(path, context, expected_size=size, expected_duration_seconds=duration, silent=silent)
            else:
                probe_audio_source(path, scheduler=context.scheduler, grant=context.grant, cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic, output=True)
        context.check()
        return outputs


    def publish(self, workspace_id: str, task_id: str, *, kind: str, parameters: dict,
                input_refs: list[str], operation: str, media_input: dict,
                suffix: str = ".wav", engine: str = "local-audio-v1", execution=None) -> ArtifactRecord:
        from app.audio_timeline import bounded_audio_context, render_audio_mix, render_stretched_audio
        import shutil
        if operation == "clip_preview" and execution is None:
            raise ValueError("镜头预览必须显式传入原作业、操作者及许可")
        active = execution if execution is not None else TASK_MEDIA_CONTEXT.get()
        if active is None:
            raise TaskMediaRequired(operation)
        context, job, event, actor = active
        if (workspace_id, task_id) != (job.workspace_id, job.task_id):
            raise PermissionError("媒体许可不属于当前任务")
        context.check()
        consumed = sum(record.size_bytes for record in self.repository.execution_records.list_artifact_records(workspace_id, task_id)
                       if record.producer_job_id == job.id)
        if operation not in {"audio_mix", "audio_stretch", "picture_preview", "clip_preview", "upload"}:
            raise ValueError("未知任务媒体操作")
        canonical_input = media_input
        if operation == "picture_preview":
            transient = {"status", "message", "output_path", "subtitle_path", "created_at", "updated_at"}
            canonical_input = {"plan": {key: value for key, value in media_input["plan"].items() if key not in transient},
                "subtitle_tracks": [{key: value for key, value in item.items() if key not in {"created_at", "updated_at"}}
                                    for item in media_input["subtitle_tracks"]]}
        identity_parameters = {"parameters": parameters, "media_input": canonical_input}
        if operation == 'audio_mix':
            identity_parameters['audio_mix_contract'] = AUDIO_MIX_CONTRACT_VERSION
        identity = media_digest([job.id, kind, input_refs, operation, media_render_canonical(identity_parameters)])
        input_event_id = f"event_{job.id}_{identity}_input"
        try:
            input_event = self.repository.execution_records.get_task_event(workspace_id, input_event_id)
        except KeyError:
            # Persist the exact compiled candidate before starting any media.
            snapshot = json.loads(json.dumps({"operation": operation, "parameters": parameters,
                "input_refs": input_refs, "media_input": media_input, "suffix": suffix, "engine": engine}, default=str))
            input_event = self.repository.execution_records.append_task_event(TaskEvent(id=input_event_id, workspace_id=workspace_id,
                task_id=task_id, job_id=job.id, event_type="task_media_input", payload=snapshot, created_at=job.created_at))
        parameters, media_input = input_event.payload["parameters"], input_event.payload["media_input"]
        logical_ref = f"artifacts/{task_id}/{job.id}/{identity}{suffix}"
        previous = next((record for record in self.repository.execution_records.list_artifact_records(workspace_id, task_id)
                         if record.logical_ref == logical_ref), None)
        if previous is not None:
            publication = self.artifact_store.describe(logical_ref)
            if (publication.sha256, publication.size_bytes) != (previous.sha256, previous.size_bytes):
                raise ValueError("本次已提交媒体内容发生变化，不能重用或重渲染")
            with bounded_audio_context(context) if operation == "audio_mix" else nullcontext(context) as probe_context:
                self._measure_publication(self.artifact_store.resolve_file(logical_ref),operation,media_input,probe_context)
            context.check()
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="task-media-", dir=self.temporary_root) as directory:
            path = Path(directory) / f"media{suffix}"
            try:
                published_path = self.artifact_store.resolve_file(logical_ref)
            except FileNotFoundError:
                options = {"scheduler": context.scheduler, "grant": context.grant,
                    "cancel_event": context.cancel_event, "deadline_monotonic": context.deadline_monotonic,
                    "max_output_bytes": context.max_output_bytes - consumed, "threads": context.threads}
                if operation == "audio_mix":
                    graph = AudioTimeline.model_validate(media_input["audio_timeline"])
                    from app.audio_timeline import _audio_grant_resources
                    declared = _audio_grant_resources(context.scheduler, context.grant)
                    if declared.get("kind") in {"creation_render", "creation_presenter_render"}:
                        declared = declared["audio_mix"]
                    if bool(media_input.get("normalize_voice_sources", False)) != bool(declared.get("normalize_voice_sources", False)):
                        raise ValueError("音轨响度处理与本次许可事实不一致")
                    with bounded_audio_context(context) as audio_context:
                        options.update(max_output_bytes=min(options["max_output_bytes"], audio_context.max_output_bytes),
                            threads=audio_context.threads, deadline_monotonic=audio_context.deadline_monotonic)
                        produced = {record.logical_ref for record in self.repository.execution_records.list_artifact_records(workspace_id, task_id)
                                    if record.producer_job_id == job.id}
                        render_audio_mix(graph, media_input["duration_seconds"],
                            lambda ref: self.source(workspace_id, task_id, ref, actor=actor), path,
                            produced_source_paths={self.source(workspace_id, task_id, reference, actor=actor) for reference in input_refs if reference in produced},
                            start_seconds=media_input.get("start_seconds", 0),
                            end_seconds=media_input.get("end_seconds"),
                            normalize_voice_sources=media_input.get("normalize_voice_sources", False),
                            solo_track_ids=set(media_input.get("solo_track_ids", [])) or None, **options)
                        measured = self._measure_publication(path,operation,media_input,audio_context)
                elif operation == "audio_stretch":
                    source = self.repository.resolve_workspace_media_reference(workspace_id, input_refs[0], require_registered=True)
                    render_stretched_audio(source, media_input["source_start_ms"], media_input["source_end_ms"],
                        media_input["rate"], media_input["duration_ms"], path, **options)
                elif operation == "clip_preview":
                    from dataclasses import replace
                    clip_context = replace(context, max_output_bytes=context.max_output_bytes-consumed)
                    from app.media.clip_preview import render_clip_input
                    render_clip_input(media_input, path, clip_context)
                elif operation == "picture_preview":
                    if self.picture_renderer is None:
                        raise ValueError("受许可画面预览执行器不可用")
                    self.picture_renderer(workspace_id, task_id, media_input, path, context)
                else:
                    staged = self.temporary_root / media_input["staged_input"]
                    if staged.is_symlink() or not staged.resolve().is_relative_to(self.temporary_root.resolve()):
                        raise ValueError("上传暂存引用越界")
                    expected_sha = media_input.get("sha256")
                    if expected_sha is not None:
                        digest = hashlib.sha256()
                        with staged.open("rb") as source:
                            while chunk := source.read(1024 * 1024):
                                context.check()
                                context.scheduler.resources.continue_unit(context.grant)
                                digest.update(chunk)
                        if digest.hexdigest() != expected_sha:
                            raise ValueError("已接收的固定音频输入内容发生变化，未处理媒体")
                    probe_audio_metadata(staged, scheduler=context.scheduler, grant=context.grant,
                        cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic)
                    shutil.copyfile(staged, path)
                context.check()
                if path.stat().st_size > context.max_output_bytes:
                    raise ValueError("媒体输出超过本次许可上限")
                if operation != "audio_mix":
                    measured = self._measure_publication(path,operation,media_input,context)
                duration = measured["duration_seconds"]
                if kind == "task_recording":
                    if duration > 600:
                        raise ValueError("电脑录音最长为十分钟")
                    with path.open("rb") as source:
                        header = source.read(16)
                    matches = {".webm": header.startswith(b"\x1a\x45\xdf\xa3"), ".ogg": header.startswith(b"OggS"),
                        ".mp4": header[4:8] == b"ftyp", ".m4a": header[4:8] == b"ftyp",
                        ".wav": header.startswith(b"RIFF") and header[8:12] == b"WAVE"}
                    if not matches.get(suffix, False):
                        raise ValueError("录音容器与文件格式不一致")
                if path.stat().st_size + consumed > context.max_output_bytes:
                    raise ValueError("本次媒体产物总量超过许可上限")
                expected = (media_input.get("end_seconds", media_input.get("duration_seconds", 0)) - media_input.get("start_seconds", 0)
                            if operation == "audio_mix" else media_input.get("duration_ms", 0) / 1000
                            if operation == "audio_stretch" else media_input["plan"]["duration_seconds"]
                            if operation == "picture_preview" else None)
                if expected is not None and abs(duration - expected) > .1:
                    raise ValueError("媒体输出未覆盖请求时长，未发布截断产物")
                published = self.artifact_store.publish_file(logical_ref, path)
            else:
                # Publication survived a crash before its database receipt.
                with bounded_audio_context(context) if operation == "audio_mix" else nullcontext(context) as probe_context:
                    measured = self._measure_publication(published_path,operation,media_input,probe_context)
                duration = measured["duration_seconds"]
                published = self.artifact_store.describe(logical_ref)
        context.check()
        record = ArtifactRecord(id=f"artifact_{identity}",
            workspace_id=workspace_id, task_id=task_id, kind=kind, logical_ref=published.logical_ref,
            sha256=published.sha256, size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=input_refs,
            created_at=job.created_at)
        transform = TransformRun(id=f"transform_{identity}",
            workspace_id=workspace_id, task_id=task_id, job_id=job.id, transform_type=kind,
            input_digest=identity, input_refs=input_refs, output_refs=[record.logical_ref], engine=engine,
            parameters={**parameters, "duration_ms": round(duration * 1000), "sample_rate": measured["sample_rate"], "channels": measured["channels"]}, status="succeeded",
            started_at=job.created_at, finished_at=job.created_at)
        edges = [DependencyEdge(id=f"edge_{identity}_{index}",
                workspace_id=workspace_id, task_id=task_id, upstream_ref=reference,
                downstream_ref=record.logical_ref, created_by_transform_id=transform.id, created_at=job.created_at)
            for index, reference in enumerate(input_refs)]
        return self.repository.task_media_publication.record_task_media_publication(record, transform, edges)


# The context exists only while the sole device driver executes a persisted job.
# It is never supplied by HTTP headers or a model tool.

TASK_MEDIA_KIND = "creation_task_media"
TASK_MEDIA_CONTEXT = ContextVar("task_media_grant", default=None)


def media_render_canonical(value):
    """Normalize generated object identities, preserving their internal links.

    The parent job already binds the real task/version/source identities. Render
    commands use positions and data; fresh compiler UUIDs must not rerender a
    published result after a crash between artifact and result receipts.
    """
    identifiers = {}
    def collect(item):
        if isinstance(item, dict):
            if isinstance(item.get("id"), str) and item["id"] not in identifiers:
                identifiers[item["id"]] = f"object:{len(identifiers)}"
            for key in sorted(item):
                if key not in {"created_at", "updated_at"}:
                    collect(item[key])
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(value)
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items() if key not in {"created_at", "updated_at"}}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, str):
            if item in identifiers:
                return identifiers[item]
            for prefix in ("clip:", "audio:", "cue:", "overlay:", "subtitle-track:"):
                if item.startswith(prefix) and item[len(prefix):] in identifiers:
                    return prefix + identifiers[item[len(prefix):]]
        return item
    return normalize(value)


def media_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


class TaskMediaJobs:
    """Persist typed task-media inputs, dispatched by the existing creation owner."""

    def __init__(self, repository, *, facts, verify_identity, on_settled=None, clip_preview=None, audio_ingress=None, audio_edit=None, output_observation=None, media_script=None, media_script_for_task=None, visual_preview=None, compound_selection=None, exact_preview=None, command_preview=None, known_failure_secrets_for=None):
        self.repository = repository
        self.facts = facts
        self.clip_preview = clip_preview
        self.audio_ingress = audio_ingress
        self.audio_edit = audio_edit
        self.output_observation = output_observation
        self.media_script = media_script
        self.media_script_for_task = media_script_for_task
        self.visual_preview = visual_preview
        self.compound_selection = compound_selection
        self.exact_preview = exact_preview
        self.command_preview = command_preview
        self.on_settled = on_settled
        self.verify_identity = verify_identity
        self.task_authorizations = None
        self.known_failure_secrets_for = known_failure_secrets_for

    def list(self, workspace_id, task_id, actor, *, cursor=None, limit=20):
        from app.creation_execution import CreationExecutionActor
        if not 1 <= limit <= 40:
            raise ValueError('媒体作业分页大小须为 1–40')
        self.repository.task_records.get_creation_task(workspace_id, task_id)
        parameters = [workspace_id, task_id, TASK_MEDIA_KIND, actor.member_id]
        before = ''
        if cursor is not None:
            saved, event = self.read(workspace_id, cursor)
            if saved.task_id != task_id or CreationExecutionActor.from_event(workspace_id, event) != actor:
                raise KeyError('媒体作业分页位置不存在')
            before = ' AND (j.created_at < ? OR (j.created_at = ? AND j.id < ?))'
            parameters.extend([saved.created_at.isoformat(), saved.created_at.isoformat(), saved.id])
        parameters.append(limit + 1)
        with self.repository._connect() as conn:
            rows = conn.execute("SELECT j.* FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id "
                "AND e.job_id=j.id AND e.id='event_' || j.id || '_start' "
                "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND json_extract(e.payload_json,'$.member_id')=?" + before +
                ' ORDER BY j.created_at DESC,j.id DESC LIMIT ?', parameters).fetchall()
        jobs = []
        for row in rows[:limit]:
            job, event = self.read(workspace_id, row['id'])
            if CreationExecutionActor.from_event(workspace_id, event) != actor:
                continue
            jobs.append(job)
        return jobs, rows[limit-1]['id'] if len(rows) > limit else None

    def authorization_verdict(self, job, event, actor, *, connection=None, check_scope=True):
        if self.task_authorizations is None:
            return self.verify_identity(actor)
        references = set()
        def collect(value):
            if isinstance(value, dict):
                for key in ("asset_id", "asset_ref"):
                    asset = value.get(key)
                    if isinstance(asset, str):
                        references.add(asset if asset.startswith("asset:") else "asset:"+asset)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
            elif isinstance(value, str) and value.startswith(("workspace://", "artifact://", "narration://", "person-render://", "artifacts/", "asset:")):
                references.add(value)
        if check_scope:
            collect(event.get("arguments", {}))
            collect(event.get("input_facts", {}))
        return self.task_authorizations.verdict(actor, job.task_id, "task_media", references=references, connection=connection, check_scope=check_scope)

    def read(self, workspace_id, job_id, conn=None):
        if conn is None:
            with self.repository._connect() as connection:
                return self.read(workspace_id, job_id, connection)
        row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=? AND kind=?",
                           (workspace_id, job_id, TASK_MEDIA_KIND)).fetchone()
        event = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?",
                             (workspace_id, job_id, f"event_{job_id}_start")).fetchone()
        if row is None or event is None:
            raise KeyError(job_id)
        return self.repository.job_records.job_run_from_row(row), json.loads(event["payload_json"])

    def _read_access(self, job, event, actor, *, check_scope=True):
        from app.creation_execution import CreationExecutionActor
        if media_digest({key: value for key, value in event.items() if key != "input_digest"}) != job.input_digest:
            return "stale"
        if actor != CreationExecutionActor.from_event(job.workspace_id, event):
            return "unauthorized"
        identity = self.authorization_verdict(job, event, actor, check_scope=check_scope)
        if identity != "ready":
            return identity
        try:
            task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id)
        except KeyError:
            return "stale"
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            return "unauthorized"
        return "ready"

    @staticmethod
    def _admission_binding(job, start, request):
        from app.creation_execution import CreationExecutionActor
        original = CreationExecutionActor.from_event(job.workspace_id, start)
        if ((request.workspace_id, request.owner_type, request.owner_id, request.member_id) !=
                (job.workspace_id, "creation_job", job.id, original.member_id)
                or request.input_digest != job.input_digest or start["input_digest"] != job.input_digest
                or request.unit_key != f"task_media:{job.input_digest}"
                or request.resources != start["resources"]
                or media_digest({key: value for key, value in start.items() if key != "input_digest"}) != job.input_digest):
            raise PermissionError("资源观察不属于原任务媒体请求和身份")
        return {"device_request_id": request.id, "workspace_id": job.workspace_id,
            "member_id": original.member_id, "actor_scope_digest": media_digest(original.identity_payload()),
            "task_id": job.task_id, "job_id": job.id, "input_digest": job.input_digest,
            "unit_key": request.unit_key, "base_version_id": start["input_facts"]["base_version_id"],
            "attempt_number": request.attempt_number, "last_grant_generation": request.last_grant_generation,
            "phase": "admission"}

    @staticmethod
    def _admission_event_id(binding, kind):
        return "event_resource_admission_" + media_digest({**binding, "observation_kind": kind})

    @staticmethod
    def _admission_checks(checks):
        def number(value):
            return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None
        result = []
        for metric, unit in (("cpu_percent", "percent"), ("available_memory_bytes", "bytes"),
                             ("swap_used_bytes", "bytes"), ("disk_io_bytes_per_second", "bytes_per_second")):
            if metric not in checks:
                continue
            row = checks[metric]
            values = {key: number(row.get(key)) for key in ("actual", "minimum", "maximum")}
            passed = row.get("passed") if values["actual"] is not None and (values["minimum"] is not None or values["maximum"] is not None) else None
            result.append({"metric": metric, "unit": unit, **values,
                "passed": passed if type(passed) is bool else None})
        disks = [row for key, row in checks.items() if key.startswith("disk_free_bytes:")]
        if disks:
            actuals = [number(row.get("actual")) for row in disks]
            minima = [number(row.get("minimum")) for row in disks]
            actual = min(actuals) if all(value is not None for value in actuals) else None
            minimum = minima[0] if all(value is not None and value == minima[0] for value in minima) else None
            passed = (all(row["passed"] for row in disks) if actual is not None and minimum is not None
                and all(type(row.get("passed")) is bool for row in disks) else None)
            result.append({"metric": "disk_free_bytes", "unit": "bytes", "actual": actual,
                           "minimum": minimum, "maximum": None, "passed": passed})
        return result

    def record_admission(self, conn, request, decision, checked_at):
        if request.owner_type != "creation_job" or not request.unit_key.startswith("task_media:"):
            return
        if decision.verdict is True:
            return
        if decision.verdict is not False and decision.verdict is not None:
            raise ValueError("资源准入决定必须是拒绝或未知")
        job, start = self.read(request.workspace_id, request.owner_id, conn)
        binding = self._admission_binding(job, start, request)
        if decision.declared != request.resources:
            raise PermissionError("资源观察不是该请求的封存处理范围")
        kind = "first_unknown" if decision.verdict is None else "first_refused"
        event_id = self._admission_event_id(binding, kind)
        try:
            existing = self.repository.execution_records.get_task_event(job.workspace_id, event_id, connection=conn)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.job_id != job.id or any(existing.payload.get(key) != value for key, value in binding.items()):
                raise ValueError("原资源检查记录的请求归属不一致")
            return
        from app.schemas import ResourceAdmissionReceipt
        timestamp = checked_at.astimezone(timezone.utc)
        checks = self._admission_checks(decision.checks)
        labels = {"cpu_percent": "CPU占用", "available_memory_bytes": "可用内存",
            "swap_used_bytes": "交换空间占用", "disk_free_bytes": "磁盘余量",
            "disk_io_bytes_per_second": "磁盘读写"}
        refused = "、".join(labels[item["metric"]] for item in checks if item["passed"] is False)
        detail = "部分资源状态暂不能核实" if kind == "first_unknown" else f"{refused}未满足准入条件" if refused else "处理范围或资源条件未获准"
        interval = decision.observed_interval_seconds
        receipt = ResourceAdmissionReceipt(observation_id=event_id,
            **{key: binding[key] for key in ("device_request_id", "task_id", "job_id", "input_digest",
                "base_version_id", "attempt_number", "last_grant_generation", "phase")},
            observation_kind=kind, checked_at=timestamp,
            observed_interval_seconds=interval if type(interval) in {int, float} and math.isfinite(interval) and interval > 0 else None,
            context="history", admitted=decision.verdict, checks=checks,
            summary=f"该请求首次检查（{timestamp.isoformat()}）记录：{detail}。这是当时的准入观察，不是实时资源或执行许可。",
            repair_action="请结合当前作业状态查询原请求；检查记录不会重新提交或启动媒体。")
        self.repository.execution_records.append_task_event(TaskEvent(id=event_id, workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type="task_media_resource_admission",
            payload={**binding, **receipt.model_dump(mode="json")}, created_at=timestamp), connection=conn)

    def resource_admission_receipt(self, job, start, actor, request, projected_status):
        if request is None or self._read_access(job, start, actor) != "ready":
            return None
        binding = self._admission_binding(job, start, request)
        pending = (projected_status in {"queued", "interrupted"} and request.state == "blocked"
                   and request.wait_reason in {"resource_busy", "resource_unknown"})
        if pending:
            kind = "first_refused" if request.wait_reason == "resource_busy" else "first_unknown"
            try:
                saved = self.repository.execution_records.get_task_event(job.workspace_id, self._admission_event_id(binding, kind))
            except KeyError:
                return None
        else:
            saved = self.repository.execution_records.latest_task_resource_admission(job.workspace_id, job.task_id, job.id, request.id)
        if saved is None:
            return None
        if (saved.task_id != job.task_id or saved.job_id != job.id
                or any(saved.payload.get(key) != value for key, value in binding.items()
                       if key not in {"attempt_number", "last_grant_generation"})
                or pending and any(saved.payload.get(key) != binding[key] for key in ("attempt_number", "last_grant_generation"))):
            raise ValueError("资源检查记录未绑定原请求、身份和输入版本")
        from app.schemas import ResourceAdmissionReceipt
        values = {key: value for key, value in saved.payload.items() if key in ResourceAdmissionReceipt.model_fields}
        values["context"] = "pending_request" if pending else "history"
        return ResourceAdmissionReceipt.model_validate(values).model_dump(mode="json")

    def check(self, job, event, actor, *, check_scope=True):
        access = self._read_access(job, event, actor, check_scope=check_scope)
        if access != "ready":
            return access
        try:
            if event["arguments"].get("_media_kind") == "clip_preview" and "_clip_preview" not in event["arguments"]:
                if self.clip_preview is None:
                    return "stale"
                return "ready" if self.clip_preview.use_case.legacy_result_current(job, event, actor) else "stale"
            current, resources = self.facts(job.workspace_id, job.task_id, event["operation"], event["arguments"], actor)
        except PermissionError:
            return "unauthorized"
        except (KeyError, FileNotFoundError, ValueError):
            return "stale"
        if current != event["input_facts"] or resources != event["resources"]:
            return "stale"
        return "ready"

    def prepare_submission(self, actor, task_id, operation, arguments, result_kind):
        """Resolve identity, source facts and resources before a durable request claim."""
        task = self.repository.task_records.get_creation_task(actor.workspace_id, task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError("当前成员不能安排此任务的媒体处理")
        if self.task_authorizations is not None:
            self.task_authorizations.ensure(task, actor)
            identity = self.task_authorizations.verdict(actor, task_id, "task_media")
        else:
            identity = self.verify_identity(actor)
        if identity != "ready":
            raise PermissionError("原任务媒体处理授权失效或无法复核")
        facts, resources = self.facts(actor.workspace_id, task_id, operation, arguments, actor)
        payload = {"operation": operation, "arguments": arguments, "result_kind": result_kind,
                   "input_facts": facts, "resources": resources, **actor.identity_payload()}
        digest = media_digest(payload)
        job_id = "job_media_" + digest
        run = JobRun(id=job_id, workspace_id=actor.workspace_id, task_id=task_id, kind=TASK_MEDIA_KIND,
            idempotency_key=f"task-media:{task_id}:{digest}", input_digest=digest)
        payload["input_digest"] = digest
        return run, payload

    def submit(self, runtime, actor, task_id, operation, arguments, result_kind, *, write_guard=None):
        run, payload = self.prepare_submission(actor, task_id, operation, arguments, result_kind)
        return self.submit_prepared(runtime, actor, run, payload, write_guard=write_guard)

    def submit_prepared(self, runtime, actor, run, payload, *, write_guard=None):
        from app.device_scheduler import SchedulePolicy, SubmissionIntent
        task_id, arguments = run.task_id, payload['arguments']
        if "_clip_preview" in arguments:
            job = self.repository.task_media_submissions.create_clip_preview_job(run, payload,
                expected_version_id=arguments["_clip_preview"]["base_version_id"])
        else:
            with self.repository._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if write_guard is not None:
                    write_guard(conn)
                job = self.repository.job_records.create_job_run(run, connection=conn)
                self.repository.execution_records.append_task_event(TaskEvent(
                    id=f"event_{job.id}_start", workspace_id=job.workspace_id, task_id=task_id, job_id=job.id,
                    event_type="task_media_submitted", payload=payload, created_at=job.created_at), connection=conn)
        if job.status in {"queued", "interrupted"}:
            runtime.scheduler.submit(SubmissionIntent(job.workspace_id, "creation_job", job.id,
                job.input_digest, f"task_media:{job.input_digest}", SchedulePolicy()), actor)
            runtime.wake()
        return job

    def verify_submission(self, intent, actor):
        from app.device_scheduler import VerifiedWork
        job, event = self.read(intent.workspace_id, intent.owner_id)
        if self.task_authorizations is not None:
            self.task_authorizations.ensure(self.repository.task_records.get_creation_task(job.workspace_id, job.task_id), actor)
        if (intent.owner_type != "creation_job" or intent.input_digest != job.input_digest
                or intent.unit_key != f"task_media:{job.input_digest}"
                or job.status not in {"queued", "interrupted", "running"}
                or self.check(job, event, actor) != "ready"):
            raise PermissionError("任务媒体输入或身份已失效")
        return VerifiedWork(job.workspace_id, "creation_job", job.id, job.input_digest,
                            intent.unit_key, actor.member_id, "preview_dependency", event["resources"])

    def check_current(self, request, conn):
        from app.creation_execution import CreationExecutionActor
        try:
            job, event = self.read(request.workspace_id, request.owner_id, conn)
            actor = CreationExecutionActor.from_event(job.workspace_id, event)
        except (KeyError, ValueError):
            return "stale"
        if (request.owner_type != "creation_job" or request.member_id != actor.member_id
                or request.work_class != "preview_dependency" or request.source_refs):
            return "unauthorized"
        terminal = {"done": "succeeded", "failed": "failed", "cancelled": "cancelled"}.get(request.state)
        if (request.input_digest != job.input_digest or request.unit_key != f"task_media:{job.input_digest}"
                or request.resources != event["resources"]
                or job.status not in {"queued", "interrupted", "running", terminal}):
            return "stale"
        if media_digest({key: value for key, value in event.items() if key != "input_digest"}) != job.input_digest:
            return "stale"
        facts = event["input_facts"]
        if event["operation"] == "audio_waveform" and isinstance(facts, dict):
            version = facts.get("base_version_id")
            if isinstance(version, str) and version.strip():
                try:
                    task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id, connection=conn)
                except KeyError:
                    return self.check(job, event, actor, check_scope=False)
                if version != task.current_timeline_version_id:
                    return "stale"
        return self.check(job, event, actor, check_scope=False)

    def capture_local(self, request, conn):
        try:
            job, event = self.read(request.workspace_id, request.owner_id, conn)
        except (KeyError, ValueError):
            return lambda current: "stale"
        # Resolve the sealed source graph once, before any write lock. The
        # commit check reads only revisions of those concrete dependencies.
        references = set()
        def collect(value):
            if isinstance(value, str):
                references.add(value)
            elif isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(event["input_facts"])
        collect(event["arguments"])
        artifacts, assets, narrations = set(), set(), set()
        pending = list(references)
        visited = set()
        while pending:
            reference = pending.pop()
            if reference in visited:
                continue
            visited.add(reference)
            record = conn.execute(
                "SELECT id,input_refs_json FROM artifact_records WHERE workspace_id=? AND task_id=? AND logical_ref=?",
                (job.workspace_id, job.task_id, reference),
            ).fetchone()
            if record is not None:
                artifacts.add(record["id"])
                pending.extend(json.loads(record["input_refs_json"]))
            if reference.startswith("narration://artifact/"):
                narrations.add(reference.removeprefix("narration://artifact/"))
            if reference.startswith("workspace://"):
                try:
                    reference = str(self.repository.resolve_workspace_media_reference(
                        job.workspace_id, reference, require_registered=True, connection=conn))
                except (KeyError, ValueError, OSError):
                    return lambda current: "stale"
            rows = conn.execute(
                "SELECT id FROM material_assets WHERE workspace_id=? AND (id=? OR file_path=?)",
                (job.workspace_id, reference, reference),
            ).fetchall()
            assets.update(row["id"] for row in rows)
        def read(current):
            job_row = current.execute(
                "SELECT task_id,input_digest,status FROM job_runs WHERE workspace_id=? AND id=?",
                (job.workspace_id, job.id),
            ).fetchone()
            task = current.execute(
                """SELECT plan_id,json_extract(payload,'$.confirmed_script_id'),
                   json_extract(payload,'$.narration_cache_key'),json_extract(payload,'$.montage_plan_id'),
                   json_extract(payload,'$.current_timeline_version_id'),json_extract(payload,'$.owner_user_id'),
                   json_extract(payload,'$.visibility') FROM creation_tasks WHERE workspace_id=? AND id=?""",
                (job.workspace_id, job.task_id),
            ).fetchone()
            plan = current.execute(
                "SELECT updated_at FROM creation_plans WHERE workspace_id=? AND id=?",
                (job.workspace_id, task[0] if task else None),
            ).fetchone()
            script = current.execute(
                "SELECT updated_at FROM creation_task_scripts WHERE workspace_id=? AND id=?",
                (job.workspace_id, task[1] if task else None),
            ).fetchone()
            montage = current.execute(
                "SELECT id,updated_at FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=?",
                (job.workspace_id, job.task_id),
            ).fetchone()
            source_rows = []
            for asset_id in sorted(assets):
                row = current.execute("SELECT updated_at,visibility FROM material_assets WHERE workspace_id=? AND id=?",
                                      (job.workspace_id, asset_id)).fetchone()
                source_rows.append(tuple(row) if row else None)
            for artifact_id in sorted(artifacts):
                row = current.execute(
                    """SELECT a.sha256,a.size_bytes,j.status FROM artifact_records a
                       LEFT JOIN job_runs j ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id
                       WHERE a.workspace_id=? AND a.id=?""", (job.workspace_id, artifact_id),
                ).fetchone()
                source_rows.append(tuple(row) if row else None)
            for narration_id in sorted(narrations):
                row = current.execute("SELECT id,created_at FROM narration_artifacts WHERE workspace_id=? AND id=?",
                                      (job.workspace_id, narration_id)).fetchone()
                source_rows.append(tuple(row) if row else None)
            return (tuple(job_row) if job_row else None, tuple(task) if task else None,
                    tuple(plan) if plan else None, tuple(script) if script else None,
                    tuple(montage) if montage else None, tuple(source_rows))
        expected = read(conn)
        if expected[1] is None:
            return lambda current: "stale"
        def check(current):
            from app.creation_execution import CreationExecutionActor
            actor = CreationExecutionActor.from_event(job.workspace_id, event)
            verdict = self.authorization_verdict(job, event, actor, connection=current, check_scope=False)
            if verdict != "ready":
                return verdict
            actual = read(current)
            if actual[1] is None or actual[1][-2:] != expected[1][-2:]:
                return "unauthorized"
            return "ready" if actual == expected else "stale"
        return check

    def check_local(self, request, conn, observed):
        return observed(conn)

    def verify_retry(self, request, actor, reason, conn):
        from app.device_scheduler import VerifiedRetry
        job, event = self.read(request.workspace_id, request.owner_id, conn)
        if event["arguments"].get("_media_kind") == "clip_preview" and "_clip_preview" not in event["arguments"]:
            raise PermissionError("历史短预览仅供回读，请为当前版本提交新的独立预览请求")
        expected = "succeeded" if reason == "result_invalid" else reason
        if job.status != expected or self.check(job, event, actor) != "ready":
            raise PermissionError("只能显式重试当前成员、当前固定输入的对应终态媒体作业")
        def reopen(current):
            changed = current.execute("UPDATE job_runs SET status='interrupted',failure_reason=NULL,impact=NULL,repair_action=NULL,finished_at=NULL,updated_at=? "
                "WHERE workspace_id=? AND id=? AND status=?",
                (utc_now().isoformat(), job.workspace_id, job.id, expected))
            if changed.rowcount != 1:
                raise PermissionError("重试期间原媒体作业终态已经变化")
        return VerifiedRetry(actor.member_id, reopen)

    def execute(self, grant, scheduler, executor):
        from app.creation_execution import CreationExecutionActor, CpuRenderGrantContext
        from app.granted_subprocess import GrantedCommandCancelled
        from app.host_resource_probes import HostResourceRefusal
        from app.device_scheduler import RecoveryBlocked
        from app.repository import set_workspace_context, reset_workspace_context
        from app.creation.audio_ingress_use_case import AUDIO_INGRESS_ACTIONS, AudioIngressRejected
        from app.creation.editing.audio_edit_use_case import AudioEditUseCase, AudioEditRejected
        from app.creation.output_observation_use_case import OutputObservationRejected
        from app.media_script_jobs import MEDIA_SCRIPT_ACTIONS, MediaScriptPreviewRejected
        from app.media.picture_preview import PicturePreviewRejected
        from app.creation.editing.metadata_use_case import EditPreviewRejected
        from app.creation.editing.command_compile import CommandParseRejected
        from app.public_failure import safe_rejection
        job, event = self.read(grant.workspace_id, grant.owner_id)
        direct_compound = event["operation"] == "compound_selection_preview"
        direct_audio = event["operation"] in AUDIO_INGRESS_ACTIONS
        direct_observation = event["operation"] == "output_observation"
        direct_script = event["operation"] in MEDIA_SCRIPT_ACTIONS
        direct_visual = event["operation"] == "visual_edit_preview" and "_clip_preview" not in event["arguments"]
        direct_edit = AudioEditUseCase.handles(event["operation"],event["arguments"])
        direct_exact = event["operation"] == "exact_edit_preview" and not direct_edit and "_clip_preview" not in event["arguments"]
        direct_command = event["operation"] == "command_edit_preview" and not direct_edit and "_clip_preview" not in event["arguments"]
        actor = CreationExecutionActor.from_event(job.workspace_id, event)
        media_script = self.media_script_for_task(job.task_id) if direct_script and self.media_script_for_task is not None else self.media_script
        cancellation = threading.Event()
        executor.register_cancellation(grant.request_id, cancellation)
        token = set_workspace_context(job.workspace_id)
        verification_facts = None
        def checkpoint(conn, current, status, reason=None, failure=None):
            if status == "succeeded":
                context.check_execution_limits()
            if status == "succeeded":
                self.repository.execution_records.append_task_event(TaskEvent(id=f"event_{job.id}_verified", workspace_id=job.workspace_id,
                    task_id=job.task_id,job_id=job.id,event_type="task_media_verified",payload={"outputs":verification_facts},created_at=job.created_at),connection=conn)
            records = conn.execute("SELECT logical_ref FROM artifact_records WHERE workspace_id=? AND producer_job_id=?",
                                   (job.workspace_id, job.id)).fetchall()
            conn.execute("UPDATE job_runs SET status=?,progress=?,output_refs_json=?,failure_reason=?,repair_action=?,finished_at=?,updated_at=? "
                         "WHERE workspace_id=? AND id=? AND status IN ('queued','running','interrupted','cancelled')",
                         (status, 100 if status == "succeeded" else 0,
                          json.dumps([row["logical_ref"] for row in records] if status == "succeeded" else []),
                          reason, "请检查输入、许可与本机媒体工具后重新安排。" if reason else None,
                          utc_now().isoformat(), utc_now().isoformat(), job.workspace_id, job.id))
            if failure is not None:
                detail = failure['detail']
                fields = detail if isinstance(detail, dict) else {}
                repair = fields.get('repair_action') or (
                    '请查询原请求核对结果，不重复执行或更换请求。'
                    if fields.get('submission_effect') == 'unknown'
                    else '请按失败原因核对输入、许可与本机媒体工具；需要重试时显式重试原作业。')
                conn.execute("UPDATE job_runs SET impact=?,repair_action=? WHERE workspace_id=? AND id=? AND status='failed'",
                             (fields.get('impact'), repair, job.workspace_id, job.id))
                self.repository.execution_records.append_task_event(TaskEvent(
                    id=f'event_{job.id}_failed_{grant.generation}', workspace_id=job.workspace_id,
                    task_id=job.task_id, job_id=job.id, event_type='task_media_failed',
                    payload={'device_request_id': grant.request_id, 'grant_generation': grant.generation, **failure},
                    created_at=utc_now()), connection=conn)
            return f"task-media:{job.id}:{status}"
        try:
            if job.status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: checkpoint(conn, current, "cancelled"), cancelled=True)
                return
            scheduler.assert_grant(grant)
            with self.repository._connect() as conn:
                permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds()
            resources = event["resources"]
            context = CpuRenderGrantContext(scheduler, grant,
                time.monotonic() + resources["timeout_seconds"] - max(0, elapsed),
                resources["max_output_bytes"], resources["threads"], cancellation)
            if job.status in {"queued", "interrupted"}:
                job = self.repository.job_records.claim_job_run(job.workspace_id, job.id)
            direct_clip = "_clip_preview" in event["arguments"]
            context.check()
            try:
                result_event = self.repository.execution_records.get_task_event(job.workspace_id, f"event_{job.id}_result")
            except KeyError:
                if direct_command:
                    if self.command_preview is None:
                        raise ValueError('指令编辑执行负责人未组装')
                    result = self.command_preview.execute(job,event,actor,context)
                elif direct_exact:
                    if self.exact_preview is None:
                        raise ValueError('精确编辑执行负责人未组装')
                    result = self.exact_preview.execute(job,event,actor,context)
                elif direct_compound:
                    if self.compound_selection is None:
                        raise ValueError("复合选择执行负责人未组装")
                    result = self.compound_selection.execute(job, event, actor, context)
                elif direct_visual:
                    if self.visual_preview is None:
                        raise ValueError("画面编辑执行负责人未组装")
                    result = self.visual_preview.execute(job, event, actor, context)
                elif direct_script:
                    if media_script is None:
                        raise ValueError("媒体脚本执行负责人未组装")
                    result = media_script.execute(job, event, actor, context)
                elif direct_observation:
                    if self.output_observation is None:
                        raise ValueError("输出观察执行负责人未组装")
                    result = self.output_observation.execute(job, event, actor, context)
                elif direct_edit:
                    if self.audio_edit is None:
                        raise ValueError("音频编辑执行负责人未组装")
                    result = self.audio_edit.execute(job,event,actor,context)
                elif direct_audio:
                    if self.audio_ingress is None:
                        raise ValueError("音频输入执行负责人未组装")
                    result = self.audio_ingress.execute(job, event, actor, context)
                elif direct_clip:
                    result = self.clip_preview.execute(job, event, actor, context)
                else:
                    raise ValueError('未知任务媒体处理类型')
                # Persist the receipt before validation; retry validates this same output.
                self.repository.execution_records.append_task_event(TaskEvent(
                    id=f"event_{job.id}_result", workspace_id=job.workspace_id, task_id=job.task_id,
                    job_id=job.id, event_type="task_media_result", payload={"result": result}, created_at=job.created_at))
            else:
                result = result_event.payload["result"]
            context.check()
            if direct_clip:
                verification_facts=self.clip_preview.validate_result(job,event,result,context)
            elif direct_command:
                verification_facts=self.command_preview.validate_result(job,event,result,context)
            elif direct_exact:
                verification_facts=self.exact_preview.validate_result(job,event,result,context)
            elif direct_compound:
                verification_facts=self.compound_selection.validate_result(job,event,result,context)
            elif direct_visual:
                verification_facts=self.visual_preview.validate_result(job,event,result,context)
            elif direct_script:
                verification_facts=media_script.validate_result(job,event,result,context)
            elif direct_observation:
                verification_facts=self.output_observation.validate_result(job,event,result,context)
            elif direct_edit:
                verification_facts=self.audio_edit.validate_result(job,event,result,context)
            elif direct_audio:
                verification_facts=self.audio_ingress.validate_result(job,event,result,context)
            else:
                raise ValueError('未知任务媒体处理类型')
            if self.check(job, event, actor) != "ready":
                raise ValueError("媒体处理期间输入版本或身份发生变化")
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=lambda conn, current: checkpoint(conn, current, "succeeded"))
        except GrantedCommandCancelled:
            if executor._pause_for_quit(grant, scheduler):
                return
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=lambda conn, current: checkpoint(conn, current, "cancelled", "user_cancelled"), cancelled=True)
        except HostResourceRefusal as exc:
            if executor._pause_for_quit(grant, scheduler):
                return
            diagnostics = getattr(exc, "command_diagnostics", None)
            if not exc.recoverable_pressure or (diagnostics and diagnostics.get("before_termination_returncode") not in {None, 0}):
                raise
            scheduler.defer_unit(grant, reason="resource_pressure",
                                 retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30))
            with self.repository._connect() as conn:
                conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                             (job.workspace_id, job.id))
        except RecoveryBlocked:
            raise
        except PermissionError:
            if executor._pause_for_quit(grant, scheduler):
                return
            if self.repository.job_records.get_job_run(job.workspace_id, job.id).status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: checkpoint(conn, current, "cancelled", "user_cancelled"), cancelled=True)
            elif not executor._defer_permission_loss(grant, scheduler):
                raise
        except Exception as exc:
            if executor._pause_for_quit(grant, scheduler):
                return
            if self.repository.job_records.get_job_run(job.workspace_id, job.id).status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: checkpoint(conn, current, "cancelled", "user_cancelled"), cancelled=True)
            else:
                known_rejection = (direct_audio and isinstance(exc,AudioIngressRejected) or direct_edit and isinstance(exc,AudioEditRejected) or direct_observation and isinstance(exc,OutputObservationRejected) or direct_script and isinstance(exc,MediaScriptPreviewRejected) or direct_visual and isinstance(exc,PicturePreviewRejected) or direct_compound and isinstance(exc,PicturePreviewRejected) or direct_exact and isinstance(exc,(EditPreviewRejected,PicturePreviewRejected)) or direct_command and isinstance(exc,(EditPreviewRejected,PicturePreviewRejected,CommandParseRejected)))
                failure = None
                failure_reason = type(exc).__name__
                if known_rejection:
                    # Resolve known credentials before the scheduler opens its writer.
                    secrets = self.known_failure_secrets_for(job.workspace_id) if self.known_failure_secrets_for else ()
                    failure = safe_rejection(exc.status_code, exc.detail, secrets=secrets)
                    if failure is not None:
                        detail = failure['detail']
                        failure_reason = (detail.get('reason') if isinstance(detail, dict) else detail) or failure_reason
                scheduler.fail_unit(grant, reason="task_media_failed",
                    failure_write=lambda conn, current: checkpoint(conn, current, "failed", failure_reason, failure))
        finally:
            reset_workspace_context(token)
            with executor._cancellation_lock:
                executor._cancellations.pop(grant.request_id, None)
            if self.on_settled is not None:
                self.on_settled()
