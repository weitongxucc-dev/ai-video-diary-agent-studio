"""The full-timeline SRT is a bounded original-Job unit, not an encoded picture leaf."""
import hashlib
from pathlib import Path
import time

from app.audio_delivery_policy import SUBTITLE_SIDECAR_UNIT_PREFIX, subtitle_sidecar_resources
from app.creation.contracts import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.creation.picture_leaf_reuse import file_identity
from app.creation.render_generation import settled_render_producer, stored_render_scope
from app.models import utc_now


def completed_sidecar(repo, store, job, manifest, *, connection, completing_in_transaction=False):
    sidecar = manifest['sidecar']
    if sidecar['size_bytes'] == 0:
        return None
    identity = sidecar['id']
    record = repo.execution_records.get_artifact_record(job.workspace_id, f'artifact_subtitles_{identity}', connection=connection)
    transform = repo.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}', connection=connection)
    resources = settled_render_producer(repo, job, transform=transform, scope=stored_render_scope(manifest),
        kind='creation_subtitle_sidecar', unit_key=SUBTITLE_SIDECAR_UNIT_PREFIX+identity,
        measured_suffix='delivery_output', connection=connection, completing_in_transaction=completing_in_transaction)
    path = store.resolve_file(record.logical_ref)
    if ((record.task_id, record.producer_job_id, record.kind) != (job.task_id, job.id, 'subtitle_track')
            or (record.sha256, record.size_bytes) != (sidecar['sha256'], sidecar['size_bytes'])
            or (transform.task_id, transform.job_id, transform.status, transform.transform_type, transform.engine)
                != (job.task_id, job.id, 'succeeded', 'subtitle_render', 'subtitle-sidecar/v2')
            or transform.input_digest != identity or transform.output_refs != [record.logical_ref]
            or transform.input_refs != record.input_refs or record.input_refs != [f"timeline:{manifest['head']}"]
            or transform.parameters['manifest_digest'] != manifest['digest']
            or resources['manifest_digest'] != manifest['digest'] or resources['unit_id'] != identity
            or resources['subtitle_sha256'] != record.sha256 or resources['subtitle_sidecar_bytes'] != record.size_bytes
            or resources['publication_target']['path'] != str(path)
            or file_identity(path) != transform.parameters['file_identity']):
        raise ValueError('整轨字幕缺少本代完整时钟、真实文件或独立成功回执')
    return record


class SubtitleDeliverySidecar:
    def __init__(self, repo, store, root, budget):
        self.repo, self.store, self.root, self.budget = repo, store, root, budget

    def _layout(self, job, sidecar):
        identity = sidecar['id']
        logical = f'workspaces/{job.workspace_id}/tasks/{job.task_id}/subtitle-delivery/{job.id}/{identity}.srt'
        directory = self.root / job.workspace_id / job.task_id / job.id / identity
        target = self.store._target(logical)
        return logical, target, {'manifest': str(directory/'timeline.srt'), 'staging': str(directory/'checked.srt'),
            'published': str(target.with_suffix('.copying'))}

    def next_unit(self, job, manifest, *, request, connection, render_output_limit):
        if manifest is None or manifest['version'] != 'picture-fragment-assembly/v3':
            return None
        sidecar = manifest['sidecar']
        if sidecar['size_bytes'] == 0:
            return None
        key = SUBTITLE_SIDECAR_UNIT_PREFIX+sidecar['id']
        if request is None or request.unit_key != key or request.state not in {'granted', 'yield_requested'}:
            original = connection.execute("SELECT state FROM device_work_requests WHERE workspace_id=? "
                "AND owner_type='creation_job' AND owner_id=? AND unit_key=?",
                (job.workspace_id, job.id, key)).fetchone()
            if original is not None and original['state'] in {'done', 'granted'}:
                completed_sidecar(self.repo, self.store, job, manifest, connection=connection,
                    completing_in_transaction=True)
                return None
        _logical, target, files = self._layout(job, sidecar)
        return key, subtitle_sidecar_resources(budget=self.budget, manifest_digest=manifest['digest'], unit_id=sidecar['id'],
            subtitle_sha256=sidecar['sha256'], subtitle_bytes=sidecar['size_bytes'],
            write_paths={key: str(Path(path).parent) for key, path in files.items()}, write_files=files,
            published_target=str(target), render_output_limit=render_output_limit, render_scope=stored_render_scope(manifest))

    def execute(self, job, *, actor, context, publication_guard, picture_fragments):
        from app.granted_subprocess import run_granted_command
        request = context.scheduler._get(context.grant.request_id)
        with self.repo.execution_inputs.creation_execution_read() as conn:
            manifest = picture_fragments.manifest(job, actor=actor, connection=conn)
            if self.next_unit(job, manifest, request=request, connection=conn,
                    render_output_limit=picture_fragments.budget.max_output_bytes) != (request.unit_key, request.resources):
                raise ValueError('整轨字幕真实输入不属于原许可')
        resources = request.resources
        sidecar = manifest['sidecar']
        logical, target, files = self._layout(job, sidecar)
        if target.exists() or any(Path(path).exists() for path in files.values()):
            raise ValueError('整轨字幕原检查点已有文件，不能覆盖，须核对原消耗')
        text = manifest['subtitle_text'].encode('utf-8')
        if len(text) != sidecar['size_bytes'] or hashlib.sha256(text).hexdigest() != sidecar['sha256']:
            raise ValueError('整轨字幕字节或SHA已变化')
        for path in files.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        measured_paths = [Path(files['manifest']), Path(files['staging']), Path(files['published'])]
        publication_attempted = False
        try:
            context.check()
            with measured_paths[0].open('xb') as output:
                output.write(text)
            command = run_granted_command(context.scheduler, context.grant, ['/bin/cp', '-n', files['manifest'], files['staging']],
                timeout_seconds=context.deadline_monotonic-time.monotonic(), max_stdout_bytes=resources['max_stdout_bytes'],
                max_stderr_bytes=resources['max_stderr_bytes'], resource_purpose='subtitle_sidecar',
                cancel_event=context.cancel_event, output_paths=(files['staging'],), max_output_file_bytes=len(text))
            if command.returncode != 0 or file_identity(measured_paths[1])['size_bytes'] != len(text):
                raise ValueError('整轨字幕worker未完成声明的写入')
            context.check()
            publication_attempted = True
            published = self.store.publish_file(logical, files['staging'], temporary_path=files['published'],
                check=context.check, retain_failed_temporary=True)
            if (published.sha256, published.size_bytes) != (sidecar['sha256'], sidecar['size_bytes']):
                raise ValueError('整轨字幕发布内容与原时钟清单不一致')
            measured_paths[-1] = target
            record = ArtifactRecord(f"artifact_subtitles_{sidecar['id']}", job.workspace_id, job.task_id,
                'subtitle_track', logical, published.sha256, published.size_bytes, job.id, [f"timeline:{manifest['head']}"])
            with self.repo._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                publication_guard(conn)
                self.repo.execution_records.create_artifact_record(record, connection=conn)
                transform = TransformRun(id=f'transform_{record.id}', workspace_id=job.workspace_id, task_id=job.task_id,
                    job_id=job.id, transform_type='subtitle_render', input_digest=sidecar['id'], input_refs=record.input_refs,
                    output_refs=[logical], engine='subtitle-sidecar/v2', parameters={**(stored_render_scope(manifest) or {}),
                        'manifest_digest': manifest['digest'], 'producer_request_id': request.id,
                        'producer_generation': context.grant.generation, 'file_identity': file_identity(target)},
                    status='succeeded', started_at=utc_now(), finished_at=utc_now())
                self.repo.lineage_records.create_transform_run(transform, connection=conn)
                self.repo.lineage_records.create_dependency_edge(DependencyEdge(f'edge_{record.id}', job.workspace_id,
                    job.task_id, record.input_refs[0], logical, transform.id), connection=conn)
            return record
        finally:
            try:
                known = not publication_attempted or measured_paths[-1].exists()
                sizes = []
                for path in measured_paths:
                    try:
                        sizes.append(file_identity(path)['size_bytes'])
                    except FileNotFoundError:
                        pass
            except (OSError, ValueError):
                known = False
            with self.repo._connect() as conn:
                self.repo.execution_records.append_task_event(TaskEvent(
                    f'event_{request.id}_{context.grant.generation}_delivery_output', job.workspace_id, job.task_id, job.id,
                    'audio_delivery_output_measured', {'request_id': request.id, 'permit_generation': context.grant.generation,
                        'output_bytes': sum(sizes) if known else None, 'consumption_known': known}), connection=conn)
