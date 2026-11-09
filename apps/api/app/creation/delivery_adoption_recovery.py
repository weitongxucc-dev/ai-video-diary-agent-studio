"""Continue a known completed final render by verifying bytes and adopting only."""
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace
import hashlib
import json
import os

from app.creation.contracts import TaskEvent
from app.creation_execution import CreationExecutionActor
from app.device_scheduler import RecoveryBlocked, SubmissionIntent
from app.task_audio import media_digest
from app.creation.render_generation import current_render_generation, render_generation_fields, stored_render_scope

PREFIX = 'render:delivery-adopt:v1:'


class DeliveryAdoptionRecovery:
    def __init__(self, delivery):
        self.delivery, self.repository = delivery, delivery.repository

    @staticmethod
    def plan_event_id(job, unit_id):
        return f'event_{job.id}_delivery_adoption_{unit_id}'

    def _authorize(self, job, actor, owner, conn):
        event = self.repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn)
        if actor != CreationExecutionActor.from_event(job.workspace_id, event.payload):
            raise PermissionError('仅原作业成员可接续采用已知成片')
        task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id, connection=conn)
        if task.owner_user_id != (actor.user_id or actor.member_id) or owner.authorization_verdict(task, actor, 'render', connection=conn) != 'ready':
            raise PermissionError('原Job采用权限已失效，保留媒体与原消耗')

    def _settled(self, job, row, conn):
        usage_id = f'{row["id"]}:{row["last_grant_generation"]}'
        receipts = conn.execute("SELECT id,payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
            "AND event_type='task_execution_unit_settled' AND json_extract(payload_json,'$.usage_id')=?",
            (job.workspace_id, job.id, usage_id)).fetchall()
        if len(receipts) != 1:
            raise RecoveryBlocked('原失败单元消耗未知，仅可查询原回执')
        receipt = json.loads(receipts[0]['payload_json'])
        if (receipt.get('output_bytes') is None or receipt.get('elapsed_seconds') is None
                or receipt.get('request_id') != row['id'] or receipt.get('permit_generation') != row['last_grant_generation']):
            raise RecoveryBlocked('原失败单元真实消耗回执不完整，仅可查询')
        return receipts[0]['id'], receipt

    def _known_media(self, job, original, actor, conn):
        from app.creation.audio_delivery_assembly import _identity
        if (original['state'] != 'failed' or original['last_grant_generation'] is None
                or original['owner_type'] != 'creation_job' or original['owner_id'] != job.id
                or original['workspace_id'] != job.workspace_id or original['input_digest'] != job.input_digest):
            raise RecoveryBlocked('仅可接续同原Job、原generation的明确失败封装')
        resources = json.loads(original['resources_json'])
        if resources.get('kind') != 'creation_final_mux':
            raise ValueError('原失败单元不是已知最终封装')
        receipt_id, receipt = self._settled(job, original, conn)
        try:
            measured = self.repository.execution_records.get_task_event(job.workspace_id,
                f'event_{original["id"]}_{original["last_grant_generation"]}_delivery_output', connection=conn)
        except KeyError as exc:
            raise RecoveryBlocked('原最终媒体计量回执缺失，仅可查询，不能采用或重做') from exc
        if (measured.job_id != job.id or measured.payload.get('consumption_known') is not True
                or measured.payload.get('output_bytes') != receipt['output_bytes']):
            raise RecoveryBlocked('原最终媒体计量未知或不匹配，仅可查询')
        manifest = self.delivery.manifest(job, actor=actor, connection=conn)
        if (manifest is None or resources['manifest_digest'] != manifest['digest']
                or stored_render_scope(resources) != stored_render_scope(manifest)):
            raise ValueError('原封装与当前允许采用的manifest不一致')
        unit_id = self.delivery._unit(manifest, 'final_mux')
        if resources['unit_id'] != unit_id or original['unit_key'] != f'render:final-mux:v1:{unit_id}':
            raise ValueError('原封装单元身份不匹配')
        try:
            record = self.repository.execution_records.get_artifact_record(job.workspace_id, f'artifact_final_mux_{unit_id}', connection=conn)
            transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}', connection=conn)
        except KeyError as exc:
            raise RecoveryBlocked('原最终媒体登记未完成，仅可查询原回执，不能采用或重做') from exc
        facts = transform.parameters
        if (record.task_id != job.task_id or record.producer_job_id != job.id or record.kind != 'rendered_video'
                or transform.job_id != job.id or transform.task_id != job.task_id or transform.status != 'succeeded'
                or transform.transform_type != 'final_mux' or transform.input_digest != unit_id
                or transform.output_refs != [record.logical_ref] or transform.input_refs != record.input_refs
                or facts['manifest_digest'] != manifest['digest'] or facts['phase'] != 'final_mux'
                or stored_render_scope(facts) != stored_render_scope(manifest)
                or facts['producer_request_id'] != original['id']
                or facts['producer_generation'] != original['last_grant_generation']):
            raise ValueError('原最终媒体缺少同一许可已成功持久化的变换')
        path = self.delivery.store.resolve_file(record.logical_ref)
        if _identity(path) != facts['file_identity'] or path.stat().st_size != record.size_bytes:
            raise ValueError('原已知最终媒体身份已变化')
        video = {'parent': {'binding': {'artifact_id': record.id, 'sha256': record.sha256, 'size_bytes': record.size_bytes,
            'producer_request_id': original['id'], 'producer_generation': original['last_grant_generation'],
            'transform_id': transform.id, 'transform_digest': media_digest(asdict(transform))}, 'clock': facts['clock']},
            'path': str(path), 'logical_ref': record.logical_ref, 'file_identity': facts['file_identity']}
        return manifest, video, receipt_id

    def _subtitle(self, job, actor, owner, conn):
        from app.creation.audio_delivery_assembly import _identity
        if owner.picture_fragments is None:
            raise ValueError('原Job画面与字幕采用端口尚未装配')
        record = owner.picture_fragments.subtitle_artifact(job, actor=actor, connection=conn)
        if record is None:
            return None
        transform = self.repository.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}', connection=conn)
        request = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? AND id=? AND owner_id=? "
            "AND owner_type='creation_job' AND state='done' AND last_grant_generation=?",
            (job.workspace_id, transform.parameters['producer_request_id'], job.id, transform.parameters['producer_generation'])).fetchone()
        if request is None:
            raise ValueError('原字幕缺少已完成生产回执')
        self._settled(job, request, conn)
        path = self.delivery.store.resolve_file(record.logical_ref)
        return {'artifact_id': record.id, 'sha256': record.sha256, 'size_bytes': record.size_bytes,
            'producer_request_id': request['id'], 'producer_generation': request['last_grant_generation'],
            'transform_id': transform.id, 'transform_digest': media_digest(asdict(transform)),
            'path': str(path), 'logical_ref': record.logical_ref, 'file_identity': _identity(path)}

    def _facts(self, job, actor, owner, original, predecessor, conn, *, sealed=None):
        self._authorize(job, actor, owner, conn)
        manifest, video, origin_receipt = self._known_media(job, original, actor, conn)
        subtitle = self._subtitle(job, actor, owner, conn)
        predecessor_receipt, _ = self._settled(job, predecessor, conn)
        if predecessor['state'] != 'failed':
            raise RecoveryBlocked('原接续前驱不是明确失败状态')
        if predecessor['id'] != original['id']:
            resources = json.loads(predecessor['resources_json'])
            if resources.get('kind') != 'creation_delivery_adoption':
                raise ValueError('原采用接续前驱类型不匹配')
            plan = self.read_plan(job, predecessor['unit_key'].removeprefix(PREFIX), conn)
            if plan['original_request_id'] != original['id']:
                raise ValueError('原采用尝试不属于同一最终媒体来源')
        task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id, connection=conn)
        payload = {'version': 1, **(stored_render_scope(manifest) or {}), 'job_id': job.id, 'input_digest': job.input_digest, 'head': task.current_timeline_version_id,
            'manifest_digest': manifest['digest'], 'original_request_id': original['id'],
            'original_generation': original['last_grant_generation'], 'original_receipt_id': origin_receipt,
            'predecessor': {'request_id': predecessor['id'], 'generation': predecessor['last_grant_generation'],
                'receipt_id': predecessor_receipt, 'resources_digest': media_digest(json.loads(predecessor['resources_json']))},
            'video': video, 'subtitle': subtitle}
        from app.creation.final_delivery_adoption import (compile_delivery_adoption, serialized_adoption_candidate,
            adoption_candidate_bytes)
        current_plan = self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id, connection=conn)
        from app.montage import creation_task_montage_plan_from_payload
        montage = creation_task_montage_plan_from_payload(current_plan)
        tracks = owner.picture_fragments.metadata.current_subtitle_tracks(job.workspace_id, task, montage=montage, connection=conn)
        version = self.repository.timeline_records.get_timeline_version(job.workspace_id, task.current_timeline_version_id, connection=conn) if task.current_timeline_version_id else None
        cover = owner.picture_fragments.metadata.cover_asset_for_version(job.workspace_id, job.task_id, version) if version is not None else None
        basis = json.loads(json.dumps({'montage': current_plan, 'subtitles': [asdict(track) for track in tracks or []],
            'narration': owner.picture_fragments.metadata.current_narration_snapshot(job.workspace_id, task),
            'cover': asdict(cover) if cover is not None else None}, default=str))
        if sealed is None:
            record = self.repository.execution_records.get_artifact_record(job.workspace_id, video['parent']['binding']['artifact_id'], connection=conn)
            candidate = compile_delivery_adoption(self.delivery, conn, job, actor=actor,
                grant=SimpleNamespace(request_id='0'*32, generation=9223372036854775807), record=record,
                picture_fragments=owner.picture_fragments, manifest=manifest, completed=video,
                recovery_plan={**payload, 'unit_id': '0'*64})
            template = serialized_adoption_candidate(candidate)
        else:
            if basis != sealed['projection_basis']:
                raise ValueError('原采用候选的字幕、封面或旁白关系已变化')
            template = sealed['adoption_candidate']
        payload['projection_basis'] = basis
        payload['adoption_candidate'] = template
        payload['metadata_bytes'] = adoption_candidate_bytes(template)
        payload['unit_id'] = media_digest(payload)
        return payload

    def read_plan(self, job, unit_id, conn):
        event = self.repository.execution_records.get_task_event(job.workspace_id, self.plan_event_id(job, unit_id), connection=conn)
        plan = event.payload
        if (event.event_type != 'delivery_adoption_prepared' or event.job_id != job.id or event.task_id != job.task_id
                or plan['unit_id'] != unit_id or plan['job_id'] != job.id or plan['input_digest'] != job.input_digest
                or stored_render_scope(plan) != (render_generation_fields(current_render_generation(self.repository, job, connection=conn)) or None)
                or media_digest({key: value for key, value in plan.items() if key not in {'unit_id', 'new_request_id', 'resources'}}) != unit_id):
            raise ValueError('原仅采用接续manifest身份不一致')
        return plan

    def resources(self, plan):
        from app.audio_delivery_policy import delivery_adoption_resources
        return delivery_adoption_resources(budget=self.delivery.mux_budget, manifest_digest=plan['manifest_digest'],
            unit_id=plan['unit_id'], predecessor=plan['predecessor'], video=plan['video'], subtitle=plan['subtitle'],
            database_path=self.repository.database_path, metadata_bytes=plan['metadata_bytes'], render_scope=stored_render_scope(plan))

    def continue_adoption(self, scheduler, job, actor, *, owner, write_guard=None):
        if job.status == 'succeeded':
            return None
        with self.repository._connect() as conn:
            scope = render_generation_fields(current_render_generation(self.repository, job, connection=conn))
            original = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
                "AND owner_id=? AND input_digest=? AND json_extract(resources_json,'$.kind')='creation_final_mux' "
                "AND json_extract(resources_json,'$.render_generation_id') IS ? "
                "AND json_extract(resources_json,'$.render_input_timeline_version_id') IS ? "
                "ORDER BY created_at DESC,id DESC LIMIT 1", (job.workspace_id, job.id, job.input_digest,
                    scope.get('render_generation_id'), scope.get('render_input_timeline_version_id'))).fetchone()
            if original is None:
                return None
            links = conn.execute("SELECT * FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='delivery_adoption_prepared' ORDER BY created_at DESC,id DESC",
                (job.workspace_id, job.id)).fetchall()
            predecessor = original
            for link in links:
                plan = json.loads(link['payload_json'])
                if plan['original_request_id'] != original['id']:
                    continue
                successor = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (plan['new_request_id'],)).fetchone()
                if successor is None:
                    raise RecoveryBlocked('原采用后继记录缺失，仅可查询')
                if successor['state'] in {'waiting', 'blocked', 'granted', 'yield_requested', 'done'}:
                    self._authorize(job, actor, owner, conn)
                    if write_guard is not None:
                        write_guard(conn)
                    return scheduler._request(successor)
                if successor['state'] != 'failed':
                    raise RecoveryBlocked('原采用后继尚不能明确接续，仅可查询')
                predecessor = successor
                break
            if job.status != 'failed' or original['state'] != 'failed':
                if job.status == 'succeeded':
                    return None
                raise RecoveryBlocked('原最终封装结果未明确失败，仅可查询')
            plan = self._facts(job, actor, owner, original, predecessor, conn)
            resources = self.resources(plan)
            permit = conn.execute('SELECT state,generation FROM device_permit WHERE id=1').fetchone()
            if permit is None or permit['state'] != 'free':
                raise RecoveryBlocked('设备释放未知，仅可查询原最终媒体')
            free_generation = permit['generation']
        prior = scheduler._get(predecessor['id'])
        intent = SubmissionIntent(job.workspace_id, 'creation_job', job.id, job.input_digest,
            PREFIX+plan['unit_id'], prior.policy)
        def write(conn, failed, new_id):
            if write_guard is not None:
                write_guard(conn)
            current_job = self.repository.job_records.get_job_run(job.workspace_id, job.id, connection=conn)
            old = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (original['id'],)).fetchone()
            pred = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (failed.id,)).fetchone()
            current = self._facts(current_job, actor, owner, old, pred, conn, sealed=plan)
            if current != plan or self.resources(current) != resources:
                raise RecoveryBlocked('原采用接续的来源或有限策略已变化')
            self.repository.job_records.reopen_failed_job_run(job.workspace_id, job.id, expected_updated_at=current_job.updated_at,
                expected_attempt=current_job.attempt, connection=conn)
            self.repository.execution_records.append_task_event(TaskEvent(self.plan_event_id(job, plan['unit_id']), job.workspace_id,
                job.task_id, job.id, 'delivery_adoption_prepared', {**plan, 'new_request_id': new_id, 'resources': resources}), connection=conn)
            return owner.verify_submission(intent, actor, connection=conn)
        return scheduler.continue_failed_unit(prior.id, intent, actor, expected_failed_generation=prior.last_grant_generation,
            expected_free_generation=free_generation, owner_database_path=self.repository.database_path, owner_write=write)

    def unit_facts(self, job, actor, owner, *, request=None, connection=None):
        with self.repository._connect() if connection is None else nullcontext(connection) as conn:
            if request is not None and request.unit_key.startswith(PREFIX):
                unit_id = request.unit_key.removeprefix(PREFIX)
            else:
                scope = render_generation_fields(current_render_generation(self.repository, job, connection=conn))
                rows = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type='delivery_adoption_prepared' "
                    "AND json_extract(payload_json,'$.render_generation_id') IS ? "
                    "AND json_extract(payload_json,'$.render_input_timeline_version_id') IS ? "
                    "ORDER BY created_at DESC,id DESC LIMIT 1",
                    (job.workspace_id, job.id, scope.get('render_generation_id'), scope.get('render_input_timeline_version_id'))).fetchone()
                if rows is None:
                    return None
                unit_id = json.loads(rows[0])['unit_id']
            plan = self.read_plan(job, unit_id, conn)
            self._authorize(job, actor, owner, conn)
            original = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (plan['original_request_id'],)).fetchone()
            predecessor = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (plan['predecessor']['request_id'],)).fetchone()
            fresh = self._facts(job, actor, owner, original, predecessor, conn, sealed=plan)
            if {key: value for key, value in plan.items() if key not in {'new_request_id', 'resources'}} != fresh:
                raise ValueError('原采用接续来源、HEAD或权限已变化')
            if self.resources(fresh) != plan['resources']:
                raise ValueError('原采用接续有限策略已变化')
            return PREFIX+unit_id, plan['resources']

    def execute(self, job, *, actor, context, owner):
        from app.creation.audio_delivery_assembly import _identity
        request = context.scheduler._get(context.grant.request_id)
        if self.unit_facts(job, actor, owner, request=request) != (request.unit_key, request.resources):
            raise ValueError('采用执行与原接续许可不一致')
        with self.repository._connect() as conn:
            plan = self.read_plan(job, request.unit_key.removeprefix(PREFIX), conn)
        for facts in [plan['video']] + ([plan['subtitle']] if plan['subtitle'] is not None else []):
            context.check()
            check_current = context.scheduler.capture_running_check(context.grant)
            expected = facts['file_identity']
            binding = facts['parent']['binding'] if 'parent' in facts else facts
            descriptor = os.open(facts['path'], os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as source:
                before = os.fstat(source.fileno())
                fd_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                if fd_identity != tuple(expected[key] for key in ('device', 'inode', 'size_bytes', 'mtime_ns', 'ctime_ns')):
                    raise ValueError('原已知成片文件描述符身份变化')
                digest = hashlib.sha256()
                while True:
                    context.check_execution_limits()
                    check_current()
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(source.fileno())
                if (fd_identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or _identity(facts['path']) != expected or digest.hexdigest() != binding['sha256']):
                    raise ValueError('原已知成片完整SHA变化，不登记')
        context.check()
        return plan

    def adopt(self, conn, job, *, actor, context, plan, picture_fragments):
        from app.creation.final_delivery_adoption import adopt_delivery
        self.unit_facts(job, actor, self._owner(context.scheduler), request=context.scheduler._get(context.grant.request_id), connection=conn)
        record = self.repository.execution_records.get_artifact_record(job.workspace_id, plan['video']['parent']['binding']['artifact_id'])
        return adopt_delivery(self.delivery, conn, job, actor=actor, grant=context.grant, record=record,
            picture_fragments=picture_fragments, recovery_plan=plan)

    @staticmethod
    def _owner(scheduler):
        return scheduler.owners.registrations['creation_job'].owner if hasattr(scheduler.owners, 'registrations') else scheduler.owners
