"""Original-Job AAC encoding followed by packet-only audio/video delivery."""
from contextlib import nullcontext
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import wave

from app.audio_delivery_policy import audio_encode_resources, final_mux_resources
from app.creation.contracts import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.models import utc_now
from app.packet_copy_policy import packet_copy_output_allocation
from app.task_audio import media_digest

AUDIO_RECIPE = {'codec': 'aac', 'sample_rate': 48000, 'channels': 2,
    'parameters_digest': media_digest({'encoder': 'ffmpeg:aac', 'bitrate': '192k',
        'rate_control': 'explicit bitrate; remaining encoder defaults bound by backend identity',
        'container': 'mp4', 'editlist': True, 'movie_timescale': 48000}), 'fragmented': False}
MUX_RECIPE = {'video': 'copy', 'audio': 'copy', 'parameters_digest': media_digest({
    'map': ['0:v:0', '1:a:0'], 'container': 'mp4', 'movflags': '+faststart',
    'video_track_timescale': 90000, 'movie_timescale': 48000}),
    'fragmented': False, 'faststart': True}


def _identity(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('音画交付父文件不是普通文件')
    return {'device': info.st_dev, 'inode': info.st_ino, 'size_bytes': info.st_size,
        'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


def _run(context, command, *, output=None):
    from app.granted_subprocess import run_granted_command
    context.check()
    resources = context.scheduler._get(context.grant.request_id).resources
    result = run_granted_command(context.scheduler, context.grant, command,
        timeout_seconds=context.deadline_monotonic-time.monotonic(),
        max_stdout_bytes=resources['max_stdout_bytes'], max_stderr_bytes=resources['max_stderr_bytes'],
        cancel_event=context.cancel_event, output_paths=() if output is None else (output,),
        max_output_file_bytes=context.max_output_bytes if output is not None else None)
    if result.returncode:
        raise ValueError(f'音画交付命令失败：{result.stderr.decode("utf-8", errors="replace")}')
    context.check()
    return result


def _probe_streams(path, context):
    completed = _run(context, ['ffprobe', '-v', 'error', '-count_packets', '-show_entries',
        'stream=index,codec_type,codec_name,sample_rate,channels,time_base,start_pts,duration_ts,nb_read_packets,width,height',
        '-of', 'json', str(path)])
    return json.loads(completed.stdout)['streams']


def _aac_clock(path, context, master_clock):
    streams = _probe_streams(path, context)
    if (len(streams) != 1 or streams[0]['codec_type'] != 'audio' or streams[0]['codec_name'] != 'aac'
            or int(streams[0]['sample_rate']) != master_clock['sample_rate']
            or streams[0]['channels'] != master_clock['channels']):
        raise ValueError('编码音频不是原master采样格式对应的唯一AAC流')
    stream = streams[0]
    tb = Fraction(stream['time_base'])
    if (tb != Fraction(1, master_clock['sample_rate']) or stream['start_pts'] != 0
            or stream['duration_ts'] != master_clock['sample_count']):
        raise ValueError('AAC展示时钟未完整覆盖原master有效样本')
    # Only bounded first/tail packet windows are returned. Unknown codec padding
    # remains unknown; packet count alone never proves the presented sample clock.
    def packets(interval):
        result = _run(context, ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-read_intervals', interval,
            '-show_entries', 'packet=pts,duration:packet_side_data=side_data_type,skip_samples,discard_padding',
            '-of', 'json', str(path)])
        return json.loads(result.stdout)['packets']
    first = packets('%+#1')[0]
    tail_start = max(0, Fraction(master_clock['sample_count'], master_clock['sample_rate']) - Fraction(1, 2))
    tail = packets(f'{float(tail_start):.9f}%')
    if not tail or type(first.get('pts')) is not int or any(type(row.get('duration')) is not int for row in tail):
        raise ValueError('AAC实际包时钟不可核实')
    side = next((item for item in first.get('side_data_list', []) if item.get('side_data_type') == 'Skip Samples'), {})
    last = tail[-1]
    last_end = last['pts'] + last['duration']
    if last_end != master_clock['sample_count']:
        raise ValueError('AAC末包展示终点未覆盖原master')
    return {**{key: master_clock[key] for key in ('sample_count', 'sample_rate', 'channels')},
        'packet_count': int(stream['nb_read_packets']), 'time_base_num': tb.numerator, 'time_base_den': tb.denominator,
        'first_pts': first['pts'], 'last_end_pts': last_end,
        'initial_skip_samples': side.get('skip_samples'),
        'trailing_padding_samples': next((item['discard_padding'] for item in last.get('side_data_list', [])
            if item.get('side_data_type') == 'Skip Samples' and 'discard_padding' in item), None)}


class AudioDeliveryAssembly:
    def __init__(self, repository, artifact_store, temporary_root, *, audio, packet_copy_assembly,
                 audio_budget, mux_budget, reserve_bytes):
        self.repository, self.store, self.audio = repository, artifact_store, audio
        self.picture = packet_copy_assembly
        self.root = Path(temporary_root).expanduser().resolve() / 'audio-delivery'
        self.audio_budget, self.mux_budget, self.reserve_bytes = audio_budget, mux_budget, reserve_bytes
        from app.creation.delivery_adoption_recovery import DeliveryAdoptionRecovery
        self.adoption_recovery = DeliveryAdoptionRecovery(self)

    @staticmethod
    def manifest_id(job):
        return f'event_{job.id}_audio_delivery_manifest'

    def _master(self, job, actor, *, connection=None):
        from app.montage import creation_task_montage_plan_from_payload
        montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
            job.workspace_id, job.task_id, connection=connection))
        return self.audio.prepared_master_facts(job, montage, actor=actor, connection=connection)

    def _picture(self, job, *, connection=None):
        record = self.picture.master(job, connection=connection)
        if record is None:
            raise ValueError('最终音画封装缺少已完成原picture master')
        manifest = self.picture.manifest(job, connection=connection)
        record, path, facts = self.picture._parent(job, record.id, manifest['recipe_digest'], connection=connection)
        if facts['first_frame'] != 0:
            raise ValueError('最终picture master没有覆盖完整原时钟')
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}', connection=connection)
        return {'parent': {'binding': {'artifact_id': record.id, 'sha256': record.sha256, 'size_bytes': record.size_bytes,
            'producer_request_id': facts['producer_request_id'], 'producer_generation': facts['producer_generation'],
            'transform_id': transform.id, 'transform_digest': media_digest(asdict(transform))}, 'clock': {
            'packet_count': facts['packet_count'], 'time_base_num': 1, 'time_base_den': 90000,
            'first_pts': 0, 'last_end_pts': facts['packet_count']*3000}},
            'path': str(path), 'logical_ref': record.logical_ref, 'file_identity': _identity(path)}

    def picture_sample_count(self, job, *, connection=None):
        """Compile the completed picture clock to the delivery's 48 kHz PCM clock."""
        clock = self._picture(job, connection=connection)['parent']['clock']
        samples = Fraction((clock['last_end_pts'] - clock['first_pts']) * clock['time_base_num'],
            clock['time_base_den']) * 48000
        if samples.denominator != 1 or samples <= 0:
            raise ValueError('已完成画面时钟不能完整映射到交付音频采样时钟')
        return samples.numerator

    def prepare(self, job, *, actor, connection=None):
        master, picture = self._master(job, actor, connection=connection), self._picture(job, connection=connection)
        pcm, video = master['parent']['clock'], picture['parent']['clock']
        if Fraction(pcm['sample_count'], pcm['sample_rate']) != Fraction(video['last_end_pts'], video['time_base_den']):
            raise ValueError('音频master与画面master时钟不一致，不能截短或填充后伪装完整交付')
        payload = {'version': 'audio-video-delivery/v1', 'workspace_id': job.workspace_id, 'task_id': job.task_id,
            'job_id': job.id, 'input_digest': job.input_digest, 'picture_recipe': self.picture.recipe(job, connection=connection),
            'master': master, 'picture': picture, 'audio_recipe': AUDIO_RECIPE, 'mux_recipe': MUX_RECIPE}
        payload['digest'] = media_digest(payload)
        existing = self.manifest(job, actor=actor, connection=connection)
        if existing is not None:
            if existing != payload:
                raise ValueError('原音画交付manifest已绑定其他来源、时钟或配方')
            return existing
        self.repository.execution_records.append_task_event(TaskEvent(self.manifest_id(job), job.workspace_id, job.task_id, job.id,
            'audio_delivery_manifest', payload), connection=connection)
        return payload

    def manifest(self, job, *, actor, connection=None):
        try:
            event = self.repository.execution_records.get_task_event(job.workspace_id, self.manifest_id(job), connection=connection)
        except KeyError:
            return None
        payload = event.payload
        if (event.job_id != job.id or event.task_id != job.task_id or payload['digest'] != media_digest({
                key: value for key, value in payload.items() if key != 'digest'})
                or payload['input_digest'] != job.input_digest
                or payload['picture_recipe'] != self.picture.recipe(job, connection=connection)
                or payload['audio_recipe'] != AUDIO_RECIPE or payload['mux_recipe'] != MUX_RECIPE
                or payload['master'] != self._master(job, actor, connection=connection)
                or payload['picture'] != self._picture(job, connection=connection)):
            raise ValueError('原音画交付来源、权限、时钟或配方已变化')
        return payload

    def _unit(self, manifest, phase):
        return media_digest([manifest['digest'], phase])

    def _artifact(self, job, manifest, phase, *, connection=None):
        identity = self._unit(manifest, phase)
        try:
            record = self.repository.execution_records.get_artifact_record(job.workspace_id, f'artifact_{phase}_{identity}', connection=connection)
        except KeyError:
            return None
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}', connection=connection)
        facts = transform.parameters
        if (record.kind != ('rendered_video' if phase == 'final_mux' else phase) or transform.transform_type != phase or transform.engine != 'ffmpeg-audio-delivery/v1'
                or record.producer_job_id != job.id or record.task_id != job.task_id or transform.job_id != job.id
                or transform.task_id != job.task_id or transform.status != 'succeeded'
                or transform.input_digest != identity or transform.output_refs != [record.logical_ref]
                or transform.input_refs != record.input_refs
                or facts['manifest_digest'] != manifest['digest'] or facts['phase'] != phase):
            raise ValueError('音画交付产物没有同原manifest的成功变换')
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            receipt = conn.execute("SELECT r.state,r.resources_json FROM device_work_requests r WHERE r.workspace_id=? "
                "AND r.id=? AND r.owner_id=? AND r.owner_type='creation_job' AND r.input_digest=? "
                "AND r.last_grant_generation=? AND (r.state='done' OR (r.state='granted' AND EXISTS "
                "(SELECT 1 FROM task_events e WHERE e.workspace_id=r.workspace_id AND e.job_id=r.owner_id "
                "AND e.event_type='task_execution_unit_settled' AND json_extract(e.payload_json,'$.usage_id')=r.id||':'||r.last_grant_generation)))",
                (job.workspace_id, facts['producer_request_id'], job.id, job.input_digest, facts['producer_generation'])).fetchone()
        if receipt is None:
            raise ValueError('音画产物原请求与generation完成回执不可核实')
        resources = json.loads(receipt['resources_json'])
        if (resources.get('unit_id') != identity or resources.get('manifest_digest') != manifest['digest']
                or resources.get('kind') != ('creation_audio_encode' if phase == 'audio_encode' else 'creation_final_mux')):
            raise ValueError('音画产物原许可族或manifest与变换不一致')
        path = self.store.resolve_file(record.logical_ref)
        if _identity(path) != facts['file_identity'] or path.stat().st_size != record.size_bytes:
            raise ValueError('音画产物stat已变化')
        return {'parent': {'binding': {'artifact_id': record.id, 'sha256': record.sha256,
            'size_bytes': record.size_bytes, 'producer_request_id': facts['producer_request_id'],
            'producer_generation': facts['producer_generation'], 'transform_id': transform.id,
            'transform_digest': media_digest(asdict(transform))}, 'clock': facts['clock']},
            'path': str(path), 'logical_ref': record.logical_ref, 'file_identity': facts['file_identity']}

    def _layout(self, job, manifest, phase):
        identity = self._unit(manifest, phase)
        directory = self.root / job.workspace_id / job.task_id / job.id / identity
        logical = f'workspaces/{job.workspace_id}/tasks/{job.task_id}/audio-delivery/{job.id}/{phase}-{identity}.mp4'
        return logical, {'manifest': str(directory/'input.json'), 'staging': str(directory/'output.mp4'),
            'published': str(self.store._target(logical).with_suffix('.copying'))}

    def next_unit(self, job, *, actor, request=None, connection=None, prepare=False):
        manifest = self.manifest(job, actor=actor, connection=connection)
        if manifest is None:
            return None
        for phase, budget in (('audio_encode', self.audio_budget), ('final_mux', self.mux_budget)):
            identity = self._unit(manifest, phase)
            current = (request is not None and request.resources.get('unit_id') == identity
                and request.state in {'granted', 'yield_requested'})
            if not current and self._artifact(job, manifest, phase, connection=connection) is not None:
                continue
            if budget is None:
                raise ValueError(f'本机缺少{phase}的有限过程预算')
            parents = [manifest['master']] if phase == 'audio_encode' else [manifest['picture'],
                self._artifact(job, manifest, 'audio_encode', connection=connection)]
            if any(parent is None for parent in parents):
                raise ValueError('音画交付下一单元缺少完成父检查点')
            logical, files = self._layout(job, manifest, phase)
            content = {'manifest_digest': manifest['digest'], 'unit_id': identity, 'phase': phase,
                'parents': parents, 'recipe': manifest['audio_recipe' if phase == 'audio_encode' else 'mux_recipe']}
            manifest_bytes = len(json.dumps(content, sort_keys=True, ensure_ascii=False).encode())
            paths = {key: str(Path(path).parent) for key, path in files.items()}
            event_id = f'event_{job.id}_{phase}_resources_{identity}'
            try:
                sealed = self.repository.execution_records.get_task_event(job.workspace_id, event_id, connection=connection).payload
            except KeyError:
                sealed = None
            if sealed is None and not prepare:
                raise ValueError('音画单元尚未封存实际卷分配')
            published_target = (self.store._target(logical)
                if sealed is None or sealed.get('publication_target') is not None else None)
            if sealed is None:
                limit = packet_copy_output_allocation(write_paths=paths, write_files=files, publication_mode='copy',
                    manifest_bytes=manifest_bytes, reserve_bytes=self.reserve_bytes, published_target=published_target)
                from app.creation.execution_budget_scope import ExecutionBudgetScopes
                scopes = ExecutionBudgetScopes(self.repository)
                with self.repository._connect() if connection is None else nullcontext(connection) as conn:
                    try:
                        binding = scopes.binding(conn, job.workspace_id, job.id)
                    except KeyError:
                        binding = None
                    if binding is not None:
                        authority = scopes.current(conn, job.workspace_id, binding.payload['scope_id']).payload
                        total = authority['limits']['generated_bytes']
                        if total is not None:
                            usage = scopes.usage(conn, job.workspace_id, binding.payload['scope_id'])
                            limit = min(limit, (total-usage['generated_bytes']-usage['reserved_bytes']-manifest_bytes)//2)
            else:
                limit = sealed['max_output_bytes']
            common = dict(budget=budget, manifest_digest=manifest['digest'], unit_id=identity,
                write_paths=paths, write_files=files, manifest_bytes=manifest_bytes,
                output_limit_bytes=limit, publication_mode='copy',
                published_target=published_target)
            resources = (audio_encode_resources(**common, master=parents[0]['parent'], audio_recipe=AUDIO_RECIPE)
                if phase == 'audio_encode' else final_mux_resources(**common, picture=parents[0]['parent'],
                    encoded_audio=parents[1]['parent'], mux_recipe=MUX_RECIPE))
            if sealed is not None and sealed != resources:
                raise ValueError('音画单元原封存声明已变化')
            if sealed is None:
                self.repository.execution_records.append_task_event(TaskEvent(event_id, job.workspace_id, job.task_id, job.id,
                    'audio_delivery_resources', resources), connection=connection)
            return f'render:{phase.replace("_", "-")}:v1:{identity}', resources
        return None

    @staticmethod
    def measured_event_id(grant):
        return f'event_{grant.request_id}_{grant.generation}_delivery_output'

    def execute(self, job, *, actor, context, publication_guard):
        request = context.scheduler._get(context.grant.request_id)
        manifest = self.manifest(job, actor=actor)
        phase = 'audio_encode' if request.resources['kind'] == 'creation_audio_encode' else 'final_mux'
        if self.next_unit(job, actor=actor, request=request) != (request.unit_key, request.resources):
            raise ValueError('音画执行单元与原许可不一致')
        parents = [manifest['master']] if phase == 'audio_encode' else [manifest['picture'], self._artifact(job, manifest, 'audio_encode')]
        logical, files = self._layout(job, manifest, phase)
        for raw in files.values():
            if Path(raw).exists():
                raise ValueError('音画原执行留有文件，须按原回执对账，不能覆盖重做')
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
        manifest_written, publication_started, published = False, False, None
        try:
            # Full-byte verification belongs to the active Grant, never admission.
            for parent in parents:
                context.check()
                path = Path(parent['path'])
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, 'rb') as source:
                    if _identity(path) != parent['file_identity']:
                        raise ValueError('音画父文件在完整SHA前已变化')
                    before = os.fstat(source.fileno())
                    expected = parent['file_identity']
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != tuple(
                            expected[key] for key in ('device', 'inode', 'size_bytes', 'mtime_ns', 'ctime_ns')):
                        raise ValueError('音画父文件描述符与原stat不一致')
                    digest = hashlib.sha256()
                    while True:
                        context.check()
                        data = source.read(65536)
                        if not data:
                            break
                        digest.update(data)
                    after = os.fstat(source.fileno())
                    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    if before_identity != after_identity or _identity(path) != parent['file_identity'] or digest.hexdigest() != parent['parent']['binding']['sha256']:
                        raise ValueError('音画父文件完整SHA或原stat已变化')
            inputs = {'manifest_digest': manifest['digest'], 'unit_id': request.resources['unit_id'], 'phase': phase,
                'parents': parents, 'recipe': manifest['audio_recipe' if phase == 'audio_encode' else 'mux_recipe']}
            encoded = json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()
            if len(encoded) != request.resources['manifest_bytes']:
                raise ValueError('音画输入manifest大小与原许可不一致')
            with Path(files['manifest']).open('xb') as output:
                output.write(encoded)
            manifest_written = True
            output = Path(files['staging'])
            common = ['ffmpeg', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                '-threads', str(context.threads)]
            if phase == 'audio_encode':
                with wave.open(parents[0]['path'], 'rb') as pcm:
                    clock = parents[0]['parent']['clock']
                    if (pcm.getnframes(), pcm.getframerate(), pcm.getnchannels(), pcm.getsampwidth()) != (
                            clock['sample_count'], clock['sample_rate'], clock['channels'], 2):
                        raise ValueError('原master WAV不是封存的完整S16采样时钟')
                command = common + ['-i', parents[0]['path'], '-map', '0:a:0', '-vn', '-c:a', 'aac', '-b:a', '192k',
                    '-ar', '48000', '-ac', '2', '-threads:a', str(context.threads), '-map_metadata', '-1',
                    '-movie_timescale', '48000', '-fs', str(context.max_output_bytes), '-f', 'mp4', str(output)]
                _run(context, command, output=output)
                output_clock = _aac_clock(output, context, parents[0]['parent']['clock'])
            else:
                command = common + ['-i', parents[0]['path'], '-i', parents[1]['path'], '-map', '0:v:0', '-map', '1:a:0',
                    '-map_metadata', '-1', '-c:v', 'copy', '-c:a', 'copy', '-video_track_timescale', '90000',
                    '-movie_timescale', '48000', '-movflags', '+faststart',
                    '-fs', str(context.max_output_bytes), '-f', 'mp4', str(output)]
                _run(context, command, output=output)
                streams = _probe_streams(output, context)
                video = next((item for item in streams if item['codec_type'] == 'video'), None)
                audio = next((item for item in streams if item['codec_type'] == 'audio'), None)
                vclock, aclock = parents[0]['parent']['clock'], parents[1]['parent']['clock']
                if (len(streams) != 2 or video is None or audio is None or video['codec_name'] != 'h264' or audio['codec_name'] != 'aac'
                        or video['time_base'] != '1/90000' or video['start_pts'] != 0
                        or video['duration_ts'] != vclock['last_end_pts'] or int(video['nb_read_packets']) != vclock['packet_count']
                        or audio['time_base'] != f'1/{aclock["sample_rate"]}' or audio['start_pts'] != 0
                        or audio['duration_ts'] != aclock['sample_count'] or int(audio['nb_read_packets']) != aclock['packet_count']):
                    raise ValueError('最终copy没有保留原画面包时钟或AAC有效样本展示时钟')
                output_clock = {'picture': vclock, 'audio': aclock}
            context.check()
            for parent in parents:
                if _identity(parent['path']) != parent['file_identity']:
                    raise ValueError('音画父文件在处理期间已变化，不发布')
            publication_started = True
            published = self.store.publish_file(logical, output, temporary_path=files['published'], check=context.check)
            identity = self._unit(manifest, phase)
            record = ArtifactRecord(f'artifact_{phase}_{identity}', job.workspace_id, job.task_id,
                'rendered_video' if phase == 'final_mux' else phase, logical,
                published.sha256, published.size_bytes, job.id, [parent['logical_ref'] for parent in parents])
            transform = TransformRun(id=f'transform_{record.id}', workspace_id=job.workspace_id, task_id=job.task_id,
                job_id=job.id, transform_type=phase, input_digest=identity, input_refs=record.input_refs,
                output_refs=[logical], engine='ffmpeg-audio-delivery/v1', parameters={
                    'manifest_digest': manifest['digest'], 'phase': phase, 'clock': output_clock,
                    'producer_request_id': context.grant.request_id, 'producer_generation': context.grant.generation,
                    'file_identity': _identity(self.store.resolve_file(logical))},
                status='succeeded', started_at=utc_now(), finished_at=utc_now())
            with self.repository._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                publication_guard(conn)
                self.manifest(job, actor=actor, connection=conn)
                self.repository.execution_records.create_artifact_record(record, connection=conn)
                self.repository.lineage_records.create_transform_run(transform, connection=conn)
                for index, reference in enumerate(record.input_refs):
                    self.repository.lineage_records.create_dependency_edge(DependencyEdge(f'edge_{record.id}_{index}', job.workspace_id,
                        job.task_id, reference, logical, transform.id), connection=conn)
            return record
        finally:
            known = not (publication_started and published is None and not Path(files['published']).exists())
            total = 0
            try:
                for role, raw in files.items():
                    path = self.store.resolve_file(logical) if role == 'published' and published is not None else Path(raw)
                    try:
                        info = _identity(path)
                    except FileNotFoundError:
                        if role == 'manifest' and manifest_written:
                            known = False
                        continue
                    total += info['size_bytes']
            except (OSError, ValueError):
                known = False
            with self.repository._connect() as conn:
                self.repository.execution_records.append_task_event(TaskEvent(self.measured_event_id(context.grant), job.workspace_id,
                    job.task_id, job.id, 'audio_delivery_output_measured', {'request_id': context.grant.request_id,
                        'permit_generation': context.grant.generation, 'consumption_known': known,
                        'output_bytes': total if known else None}), connection=conn)

    def completed(self, job, *, actor, connection=None):
        manifest = self.manifest(job, actor=actor, connection=connection)
        return self._artifact(job, manifest, 'final_mux', connection=connection) if manifest is not None else None


    def adopt(self, connection, job, *, actor, grant, record, picture_fragments):
        from app.creation.final_delivery_adoption import adopt_delivery
        return adopt_delivery(self, connection, job, actor=actor, grant=grant, record=record,
            picture_fragments=picture_fragments)
