"""Durable packet-copy reduction of the original Job's published picture fragments."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import stat

from app.creation.contracts import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.models import utc_now
from app.packet_copy_policy import packet_copy_output_allocation, packet_copy_resources


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _file_identity(info):
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('画面产物不是普通文件')
    return {'device': info.st_dev, 'inode': info.st_ino, 'size_bytes': info.st_size,
        'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


class PacketCopyAssembly:
    def __init__(self, repository, artifact_store, temporary_root, *, budget, reserve_bytes):
        self.repository, self.store = repository, artifact_store
        self.root = Path(temporary_root).expanduser().resolve() / 'picture-packet-copy'
        self.budget, self.reserve_bytes = budget, reserve_bytes

    def recipe(self, job, *, connection=None):
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id, connection=conn)
            plan = self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id, connection=conn)
            plan = dict(plan)
            for transient in ('status', 'message', 'output_path', 'subtitle_path', 'updated_at'):
                plan.pop(transient, None)
            payload = {'input_digest': job.input_digest, 'head': task.current_timeline_version_id,
                'script_id': task.confirmed_script_id, 'montage': plan}
            return _digest(payload)

    @staticmethod
    def manifest_id(job):
        return f'event_{job.id}_picture_packet_manifest'

    def _parent(self, job, artifact_id, recipe, *, connection=None):
        record = self.repository.execution_records.get_artifact_record(job.workspace_id, artifact_id)
        if (record.task_id != job.task_id or record.producer_job_id != job.id
                or record.kind not in {'picture_fragment', 'picture_packet_copy'}):
            raise ValueError('画面父产物不属于原任务与原producer Job')
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}')
        facts = transform.parameters.get('picture_fragment', {})
        if (transform.task_id != job.task_id or transform.job_id != job.id or transform.status != 'succeeded'
                or transform.output_refs != [record.logical_ref] or transform.input_refs != record.input_refs
                or facts.get('recipe_digest') != recipe
                or facts.get('contract') != 'h264-crf20-b0-fmp4/v1'):
            raise ValueError('画面父产物缺少同一完整时钟与原配方的成功变换')
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            row = conn.execute('SELECT * FROM device_work_requests WHERE workspace_id=? AND id=?',
                (job.workspace_id, facts.get('producer_request_id'))).fetchone()
            settled = conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='task_execution_unit_settled' AND json_extract(payload_json,'$.usage_id')=?",
                (job.workspace_id, job.id, f"{facts.get('producer_request_id')}:{facts.get('producer_generation')}")).fetchone()
        if (row is None or row['owner_type'] != 'creation_job' or row['owner_id'] != job.id
                or row['input_digest'] != job.input_digest
                or not (row['state'] == 'done' or row['state'] == 'granted' and settled is not None)
                or row['last_grant_generation'] != facts.get('producer_generation')):
            raise ValueError('画面父产物没有原请求、原generation的完成回执')
        resources = json.loads(row['resources_json'])
        if record.kind == 'picture_fragment':
            if (resources.get('kind') != 'creation_render' or resources.get('picture_recipe_digest') != recipe
                    or resources.get('picture_fragment') != {key: facts[key] for key in ('first_frame', 'packet_count', 'contract')}):
                raise ValueError('画面父产物与原许可片段时钟不一致')
            if (resources.get('picture_fragment_recipe') is not None
                    and transform.parameters.get('picture_fragment_recipe') != resources['picture_fragment_recipe']):
                raise ValueError('画面父产物未封存原许可的实际烧录图层与合同')
        elif (resources.get('kind') != 'creation_packet_copy'
                or resources.get('unit_id') != transform.parameters.get('packet_copy_unit_id')
                or resources.get('unit_id') != transform.input_digest
                or resources.get('manifest_digest') != transform.parameters.get('packet_copy_manifest_digest')):
            raise ValueError('画面归约产物与原许可unit不一致')
        path = self.store.resolve_file(record.logical_ref)
        identity = _file_identity(path.stat())
        if identity['size_bytes'] != record.size_bytes:
            raise ValueError('画面父产物大小已变化')
        expected_identity = facts.get('file_identity')
        if record.kind == 'picture_fragment':
            try:
                sealed = self.repository.execution_records.get_task_event(job.workspace_id, self.manifest_id(job), connection=connection)
            except KeyError:
                sealed = None
            if sealed is not None:
                expected_identity = sealed.payload['leaf_file_identities'][record.id]
        elif expected_identity is None:
            raise ValueError('画面归约产物缺少原发布stat检查点')
        if expected_identity is not None and identity != expected_identity:
            raise ValueError('画面父产物stat已变化')
        if type(facts.get('first_frame')) is not int or facts['first_frame'] < 0 or type(facts.get('packet_count')) is not int or facts['packet_count'] <= 0:
            raise ValueError('画面父产物帧区间无效')
        return record, path, facts

    def prepare(self, job, artifact_ids, *, actor, write_guard, connection=None):
        recipe = self.recipe(job, connection=connection)
        parents = [self._parent(job, identity, recipe, connection=connection) for identity in artifact_ids]
        expected, level = 0, []
        for record, _path, facts in parents:
            if facts['first_frame'] != expected:
                raise ValueError('画面父产物必须按绝对帧时钟连续、无洞无重叠')
            level.append({'artifact_id': record.id, 'first_frame': expected, 'packet_count': facts['packet_count']})
            expected += facts['packet_count']
        if not level:
            raise ValueError('画面归约缺少原父产物')
        from fractions import Fraction
        plan = self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id, connection=connection)
        if expected != round(Fraction(str(plan['duration_seconds'])) * 30):
            raise ValueError('画面父产物未覆盖完整目标帧时钟')
        instance = {'version': 1, 'workspace_id': job.workspace_id, 'task_id': job.task_id,
            'producer_job_id': job.id, 'input_digest': job.input_digest, 'recipe_digest': recipe,
            'leaf_artifact_ids': list(artifact_ids),
            'leaf_file_identities': {record.id: _file_identity(path.stat()) for record, path, _facts in parents}}
        units = []
        while len(level) > 1:
            following = []
            for offset in range(0, len(level), 2):
                group = level[offset:offset + 2]
                if len(group) == 1:
                    following.extend(group)
                    continue
                unit = {'parents': [item['artifact_id'] for item in group],
                    'first_frame': group[0]['first_frame'], 'packet_count': sum(item['packet_count'] for item in group)}
                identity = _digest({**instance, **unit})
                unit.update(id=identity, artifact_id=f'artifact_picture_packet_{identity}')
                units.append(unit)
                following.append({key: unit[key] for key in ('artifact_id', 'first_frame', 'packet_count')})
            level = following
        payload = {**instance, 'units': units, 'master_artifact_id': level[0]['artifact_id']}
        payload['manifest_digest'] = _digest(payload)
        from app.creation_execution import CreationExecutionActor
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            if connection is None:
                conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            event = self.repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn)
            if CreationExecutionActor.from_event(job.workspace_id, event.payload) != actor or self.recipe(job, connection=conn) != recipe:
                raise PermissionError('原Job身份或画面配方在封存期间已变化')
            for record, path, _facts in parents:
                if _file_identity(path.stat()) != instance['leaf_file_identities'][record.id]:
                    raise ValueError('画面父stat在manifest封存期间已变化')
            self.repository.execution_records.append_task_event(TaskEvent(self.manifest_id(job), job.workspace_id, job.task_id,
                job.id, 'picture_packet_manifest', payload), connection=conn)
        return payload

    def manifest(self, job, *, connection=None):
        try:
            event = self.repository.execution_records.get_task_event(job.workspace_id, self.manifest_id(job), connection=connection)
        except KeyError:
            return None
        payload = event.payload
        identity = {key: value for key, value in payload.items() if key != 'manifest_digest'}
        if (payload.get('manifest_digest') != _digest(identity) or payload.get('producer_job_id') != job.id
                or payload.get('input_digest') != job.input_digest or payload.get('recipe_digest') != self.recipe(job, connection=connection)):
            raise ValueError('原Job画面归约manifest或当前配方已变化')
        return payload

    def _completed(self, job, unit, manifest, *, connection=None):
        try:
            record, _path, _facts = self._parent(job, unit['artifact_id'], manifest['recipe_digest'], connection=connection)
        except KeyError:
            return None
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}')
        if transform.parameters.get('packet_copy_manifest_digest') != manifest['manifest_digest']:
            raise ValueError('画面归约检查点不属于原manifest')
        return record

    def _layout(self, job, unit):
        directory = self.root / job.workspace_id / job.task_id / job.id / unit['id']
        logical = f'workspaces/{job.workspace_id}/tasks/{job.task_id}/picture-packet-copy/{job.id}/{unit["id"]}.mp4'
        target = self.store._target(logical)
        return logical, {'manifest': str(directory / 'output.ffconcat'), 'staging': str(directory / 'output.mp4'),
            'published': str(target.with_suffix('.copying'))}

    def next_unit(self, job, *, request=None, connection=None, prepare=False):
        manifest = self.manifest(job, connection=connection)
        if manifest is None:
            return None
        for unit in manifest['units']:
            current_unit = (request is not None and request.resources.get('unit_id') == unit['id']
                and request.state in {'granted', 'yield_requested'})
            if not current_unit and self._completed(job, unit, manifest, connection=connection) is not None:
                continue
            if self.budget is None:
                raise ValueError('本机尚无匹配的packet-copy过程预算')
            parents = [self._parent(job, identity, manifest['recipe_digest'], connection=connection) for identity in unit['parents']]
            logical, files = self._layout(job, unit)
            lines = ['ffconcat version 1.0']
            for _record, path, _facts in parents:
                if '\n' in str(path) or '\r' in str(path):
                    raise ValueError('画面父产物路径不能含ffconcat行分隔符')
                escaped = str(path).replace("'", "'\\''")
                lines.append(f"file '{escaped}'")
            manifest_bytes = len(('\n'.join(lines) + '\n').encode())
            paths = {role: str(Path(path).parent) for role, path in files.items()}
            resource_id = f'event_{job.id}_picture_packet_resources_{unit["id"]}'
            try:
                sealed = self.repository.execution_records.get_task_event(job.workspace_id, resource_id, connection=connection).payload
            except KeyError:
                sealed = None
            if sealed is not None:
                limit = sealed['max_output_bytes']
            elif not prepare:
                raise ValueError('原Job画面归约尚未封存本单元实际卷分配')
            else:
                limit = packet_copy_output_allocation(write_paths=paths, write_files=files,
                    manifest_bytes=manifest_bytes, publication_mode='copy', reserve_bytes=self.reserve_bytes)
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
                            remaining = max(0, total - usage['generated_bytes'] - usage['reserved_bytes'])
                            limit = min(limit, (remaining - manifest_bytes) // 2)
            facts = packet_copy_resources(budget=self.budget, manifest_digest=manifest['manifest_digest'], unit_id=unit['id'],
                parents=[{'artifact_id': record.id, 'sha256': record.sha256, 'size_bytes': record.size_bytes,
                    'packet_count': info['packet_count'], 'producer_request_id': info['producer_request_id'],
                    'producer_generation': info['producer_generation']} for record, _path, info in parents],
                expected_total_packets=unit['packet_count'], write_paths=paths, write_files=files,
                output_limit_bytes=limit, manifest_bytes=manifest_bytes, publication_mode='copy')
            if sealed is not None and sealed != facts:
                raise ValueError('原归约单元封存预算、父事实或目录已变化，不能重写原许可')
            if sealed is None:
                self.repository.execution_records.append_task_event(TaskEvent(resource_id, job.workspace_id, job.task_id,
                    job.id, 'picture_packet_resources', facts), connection=connection)
            return f'render:packet-copy:v1:{unit["id"]}', facts
        return None

    def master(self, job, *, connection=None):
        manifest = self.manifest(job, connection=connection)
        if manifest is None:
            return None
        if any(self._completed(job, unit, manifest, connection=connection) is None for unit in manifest['units']):
            return None
        return self._parent(job, manifest['master_artifact_id'], manifest['recipe_digest'], connection=connection)[0]

    def measured_bytes(self, resources, *, published_ref=None):
        total = 0
        for role, raw in resources['write_files'].items():
            path = Path(raw)
            if role == 'published' and published_ref is not None:
                path = self.store.resolve_file(published_ref)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('画面归约输出计量遇到非普通文件')
            total += info.st_size
        return total

    def _verify_parent_bytes(self, parents, context):
        verified = {}
        for record, path, _facts in parents:
            context.check()
            before = _file_identity(path.stat())
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as source:
                if _file_identity(os.fstat(source.fileno())) != before:
                    raise ValueError('画面父产物在完整字节核验前已替换')
                digest = hashlib.sha256()
                while True:
                    context.check()
                    part = source.read(65536)
                    if not part:
                        break
                    digest.update(part)
                if (_file_identity(os.fstat(source.fileno())) != before
                        or _file_identity(path.stat()) != before):
                    raise ValueError('画面父产物在完整字节核验期间已变化')
            if digest.hexdigest() != record.sha256:
                raise ValueError('画面父产物完整SHA与原生产回执不一致')
            verified[record.id] = before
        return verified

    def execute(self, job, *, actor, context, publication_guard):
        from app.canvas import canvas_size
        from app.montage import concatenate_granted_picture_fragments
        request = context.scheduler._get(context.grant.request_id)
        manifest = self.manifest(job)
        unit = next(item for item in manifest['units'] if item['id'] == request.resources['unit_id'])
        expected = self.next_unit(job, request=request)
        if expected != (request.unit_key, request.resources):
            raise ValueError('原Job画面归约实际父节点或声明已变化')
        logical, files = self._layout(job, unit)
        for raw in files.values():
            if Path(raw).exists():
                raise ValueError('原归约单元留有文件；不能覆盖，须先核实原消耗与回执')
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
        parents = [self._parent(job, identity, manifest['recipe_digest']) for identity in unit['parents']]
        plan = self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id)
        copy_entered, publication_started, published = False, False, None
        try:
            verified = self._verify_parent_bytes(parents, context)
            with self.repository._connect() as conn:
                self.repository.execution_records.append_task_event(TaskEvent(f'event_{context.grant.request_id}_{context.grant.generation}_picture_parents_verified',
                    job.workspace_id, job.task_id, job.id, 'picture_packet_parents_verified',
                    {'request_id': context.grant.request_id, 'permit_generation': context.grant.generation,
                     'parents': [{'artifact_id': record.id, 'sha256': record.sha256,
                         'file_identity': verified[record.id]} for record, _path, _facts in parents]}), connection=conn)
            copy_entered = True
            concatenate_granted_picture_fragments([(path, info['packet_count']) for _record, path, info in parents],
                Path(files['staging']), context, expected_size=canvas_size(plan['aspect_ratio']))
            context.check()
            for record, path, _facts in parents:
                if _file_identity(path.stat()) != verified[record.id]:
                    raise ValueError('画面父产物在copy期间已变化，未发布归约结果')
            publication_started = True
            published = self.store.publish_file(logical, files['staging'], temporary_path=files['published'], check=context.check)
            fragment = {'recipe_digest': manifest['recipe_digest'], 'contract': 'h264-crf20-b0-fmp4/v1',
                'first_frame': unit['first_frame'], 'packet_count': unit['packet_count'],
                'producer_request_id': context.grant.request_id, 'producer_generation': context.grant.generation,
                'file_identity': _file_identity(self.store.resolve_file(logical).stat())}
            record = ArtifactRecord(unit['artifact_id'], job.workspace_id, job.task_id, 'picture_packet_copy', logical,
                published.sha256, published.size_bytes, job.id, [record.logical_ref for record, _, _ in parents])
            transform = TransformRun(id=f'transform_{record.id}', workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, transform_type='picture_packet_copy', input_digest=unit['id'],
                input_refs=record.input_refs, output_refs=[logical], engine='h264-crf20-b0-fmp4/v1',
                parameters={'picture_fragment': fragment, 'packet_copy_unit_id': unit['id'],
                    'packet_copy_manifest_digest': manifest['manifest_digest']}, status='succeeded',
                started_at=utc_now(), finished_at=utc_now())
            with self.repository._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                publication_guard(conn)
                for identity in unit['parents']:
                    self._parent(job, identity, manifest['recipe_digest'], connection=conn)
                self.repository.execution_records.create_artifact_record(record, connection=conn)
                self.repository.lineage_records.create_transform_run(transform, connection=conn)
                for reference in record.input_refs:
                    self.repository.lineage_records.create_dependency_edge(DependencyEdge(f'edge_{_digest([record.id, reference])}',
                        job.workspace_id, job.task_id, reference, logical, transform.id), connection=conn)
            return record
        finally:
            known = (not copy_entered or (Path(files['manifest']).is_file()
                and not (publication_started and published is None and not Path(files['published']).exists())))
            try:
                generated = self.measured_bytes(request.resources, published_ref=logical if published is not None else None) if known else None
            except (OSError, ValueError):
                generated = None
            with self.repository._connect() as conn:
                self.repository.execution_records.append_task_event(TaskEvent(f'event_{context.grant.request_id}_{context.grant.generation}_picture_output',
                    job.workspace_id, job.task_id, job.id, 'picture_packet_output_measured',
                    {'request_id': context.grant.request_id, 'permit_generation': context.grant.generation,
                     'consumption_known': generated is not None, 'output_bytes': generated}), connection=conn)
