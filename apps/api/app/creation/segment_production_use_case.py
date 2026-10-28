"""Section-scoped audiovisual production and constrained revisions in the existing task."""
from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path

from app.creation.contracts import JobRun
from app.creation.editing.narration_preview_use_case import inspect_narration_revision
from app.device_scheduler import SchedulePolicy, SubmissionIntent, VerifiedWork, RecoveryBlocked
from app.models import utc_now

SEGMENT_REVISION_KIND = 'creation_task_segment_revision'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def digest_section_version(script_id,section_id,text,audio_reference):
    return digest([script_id,section_id,text,audio_reference])


def revision_execution_key(input_digest, reference):
    return f'segment_revision:{input_digest}:{reference.identity}'


class SegmentProductionUseCase:
    def section_timeline_change(self):
        from app.creation.section_change_use_case import SectionTimelineChange
        return SectionTimelineChange(self.repository,self.artifact_store)

    def __init__(self, repository, *, narration_stage, renderer, artifact_store, output_root, source_repository=None, budget_policy_for=None, narration_runtime_facts=None):
        self.repository = repository
        self.narration_stage = narration_stage
        self.renderer = renderer
        self.artifact_store = artifact_store
        self.output_root = Path(output_root)
        self.source_repository = source_repository
        self.budget_policy_for = budget_policy_for
        self.narration_runtime_facts = narration_runtime_facts

    def require_actor(self, workspace_id, task_id, actor, *, connection=None):
        task = self.repository.get_creation_task(workspace_id, task_id, connection=connection)
        if actor.workspace_id != workspace_id or task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError('当前成员不能访问此作品段落')
        return task

    def context(self, workspace_id, task_id, section_id, actor, *, connection=None):
        if connection is None:
            with self.repository.creation_execution_read() as conn:
                return self.context(workspace_id,task_id,section_id,actor,connection=conn)
        if not connection.in_transaction:
            connection.execute('BEGIN')
        task = self.require_actor(workspace_id, task_id, actor, connection=connection)
        script, metadata = self.repository.script_records.read_execution_script_metadata(
            connection,workspace_id,task.confirmed_script_id)
        current = self.repository.read_creation_section_input(task,script,section_id,connection=connection)
        section, audio, adopted = current['section'], current['audio'], current['adopted']
        version = (adopted['segment_version'] if adopted is not None else
            digest_section_version(script.id,section.id,section.content,audio.audio_reference if audio else None))
        text_hash = hashlib.sha256(section.content.encode()).hexdigest()
        dependencies = self.repository.section_dependency_snapshot(workspace_id,task_id,section_id,connection=connection)
        video = None
        if adopted is not None:
            record = self.repository.execution_records.get_artifact_record(workspace_id, adopted['video_artifact_id'], connection=connection)
            video = {'kind': 'video', 'artifact_ref': record.logical_ref, 'sha256': record.sha256,
                'segment_version': version, 'duration_seconds': adopted['duration_seconds'],
                'url': f'/api/creation-tasks/{task_id}/segments/{section_id}/versions/{version}/video'}
        return {'task_id': task_id, 'section_id': section_id, 'ordinal': current['ordinal'], 'script_id': script.id,
            'base_version_id': task.current_timeline_version_id, 'segment_version': version,
            'title': section.title, 'text': section.content, 'source_text_sha256': text_hash,
            'dependency_digest':digest(dependencies),
            'visual_prompt': section.visual_prompt, 'source_references': section.evidence_reference_ids,
            'source_bindings': self.source_bindings(metadata, section, limit=24, connection=connection),
            'source_binding_total_count': len(section.evidence_reference_ids),
            'source_bindings_truncated': len(section.evidence_reference_ids) > 24,
            'locked_body_digest': metadata['locked_body_digest'],
            'adjacent_sections': [asdict(item) for item in self.repository.creation_section_neighbors(
                task,script,section_id,connection=connection)],
            'narration_segment_id': audio.id if audio is not None else None,
            'audio': {'kind': 'audio', 'artifact_ref': audio.audio_reference, 'status': audio.status,
                'duration_seconds': audio.duration_seconds} if audio is not None else None,
            'video': video if adopted is not None and adopted['current_validity'] == 'current' else None,
            'historical_video': video,
            'adopted': adopted is not None and adopted['current_validity'] == 'current',
            'adoption': self.adoption(task, adopted, connection=connection)}

    def source_bindings(self, metadata, section, *, offset=0, limit=24, connection=None):
        try:
            run = self.repository.get_script_generation_run(metadata['workspace_id'], metadata['run_id'], connection=connection)
        except KeyError:
            run = None
        fixed = ({item['reference_id']: item for item in run.input_references}
            if run is not None and run.task_id == metadata['task_id'] else {})
        result = []
        for reference_id in section.evidence_reference_ids[offset:offset+limit]:
            original = fixed.get(reference_id)
            if original is None:
                # Only an evidence object's exact persisted ID is a fallback binding.
                try:
                    evidence = self.repository.topic_records.get_topic_evidence_reference(metadata['workspace_id'], metadata['task_id'], reference_id, connection=connection)
                except KeyError:
                    evidence = None
                if evidence is not None and evidence.candidate_id == metadata['topic_id']:
                    original = {'kind': evidence.kind, 'object_id': evidence.reference_id,
                        'version_or_identity': evidence.version_or_identity}
            known = (original is not None and original.get('kind') in {'knowledge', 'source'}
                and bool(original.get('object_id')) and bool(original.get('version_or_identity')))
            result.append({'reference_id': reference_id, 'kind': original['kind'] if known else None,
                'object_id': original['object_id'] if known else None,
                'version_or_identity': original['version_or_identity'] if known else None,
                'content_digest': original.get('content_hash') if known else None,
                'binding_status': 'fixed' if known else 'unknown'})
        return result

    def sources(self, workspace_id, task_id, section_id, actor, *, offset=0, limit=8, text_offset=0, max_chars=3000):
        if offset < 0 or not 1 <= limit <= 24 or text_offset < 0 or not 1 <= max_chars <= 8000:
            raise ValueError('段落证据读取范围无效')
        with self.repository.creation_execution_read() as conn:
            task = self.require_actor(workspace_id, task_id, actor, connection=conn)
            script, metadata = self.repository.script_records.read_execution_script_metadata(conn,workspace_id,task.confirmed_script_id)
            section = self.repository.read_creation_section_input(task,script,section_id,connection=conn)['section']
            bindings = self.source_bindings(metadata, section, offset=offset, limit=limit, connection=conn)
            version = self.repository.stage_section_version(workspace_id,task_id,section_id,connection=conn)
        items = []
        for binding in bindings:
            if binding['binding_status'] == 'unknown':
                value = {'status': 'unknown', 'reason_code': 'original_source_binding_unavailable',
                    'text_offset': text_offset, 'truncated': False}
            elif self.source_repository is None:
                value = {'status': 'unavailable', 'reason_code': 'fixed_source_reader_unavailable',
                    'text_offset': text_offset, 'truncated': False}
            else:
                value = self.source_repository.read_fixed_reference_window(workspace_id, kind=binding['kind'],
                    object_id=binding['object_id'], version_or_identity=binding['version_or_identity'],
                    user_id=actor.user_id or actor.member_id, offset=text_offset, limit=max_chars)
                if (value['status'] == 'matched' and binding['content_digest'] is not None
                        and binding['content_digest'] != value.get('content_digest')):
                    value = {'status': 'changed', 'reason_code': 'original_content_digest_mismatch',
                        'text_offset': text_offset, 'truncated': False}
            items.append({'binding': binding, **value})
        total = len(section.evidence_reference_ids)
        return {'task_id': task_id, 'section_id': section_id, 'script_id': script.id,
            'segment_version': version,
            'items': items, 'offset': offset, 'limit': limit, 'total_count': total, 'truncated': offset+len(items) < total}

    def adoption(self, task, result, *, connection=None):
        current = (self.repository.stage_segment_state(task.workspace_id, task.id, result['section_id'], connection=connection)
            if result is not None else None)
        sequence = (self.repository.timeline_sequence(task.workspace_id, task.current_timeline_version_id, connection=connection)
            if task.current_timeline_version_id else None)
        result_version = result['segment_version'] if result is not None else None
        current_version = current['segment_version'] if current is not None else None
        if result is None:
            status, reason = 'not_produced', 'original_request_has_no_publication'
        elif not result['adopted']:
            status, reason = 'candidate', 'candidate_not_adopted'
        elif result_version != current_version:
            status, reason = 'superseded', 'current_section_adopts_other_publication'
        elif current['current_validity'] == 'invalidated':
            status, reason = 'invalidated', current['invalidation_reason']
        else:
            status, reason = 'current', None
        return {'status': status, 'current_timeline_version_id': task.current_timeline_version_id,
            'current_timeline_sequence': sequence,
            'current_segment_version': current_version, 'result_segment_version': result_version, 'reason': reason}

    def list_segments(self, workspace_id, task_id, actor, *, offset=0, limit=30):
        with self.repository.creation_execution_read() as conn:
            task = self.require_actor(workspace_id, task_id, actor, connection=conn)
            script, _ = self.repository.script_records.read_execution_script_metadata(conn,workspace_id,task.confirmed_script_id)
            ids = self.repository.creation_section_ids(task,script,offset=offset,limit=limit,connection=conn)
            values = [self.context(workspace_id,task_id,item,actor,connection=conn) for item in ids]
            receipts = self.repository.list_segment_revision_requests(workspace_id, task_id, actor.member_id, limit=limit, connection=conn)
            return {'task_id': task_id, 'total_count': script.sections_total_count, 'offset': offset,
                'truncated': offset+len(values)<script.sections_total_count, 'revision_requests': receipts,
                'available_video_count': sum(item['video'] is not None for item in values), 'segments': values}

    def verify_revision(self, workspace_id, task_id, actor, command, *, connection=None):
        current = self.context(workspace_id, task_id, command.section_id, actor, connection=connection)
        if (current['script_id'] != command.script_id or current['segment_version'] != command.expected_segment_version
                or current['source_text_sha256'] != command.source_text_sha256
                or current['dependency_digest'] != command.dependency_digest):
            raise ValueError('原段落、正文或所见版本已变化，请读取当前段落再判断原意')
        if hashlib.sha256(command.replacement_text.encode()).hexdigest() != command.replacement_text_sha256:
            raise ValueError('具体改写与候选正文摘要不一致')
        review = self.repository.execution_records.get_task_event(workspace_id, command.semantic_review_ref, connection=connection)
        payload = review.payload
        expected = {name: getattr(command, name) for name in ('section_id','script_id','expected_segment_version',
            'base_version_id','dependency_digest','source_text_sha256','replacement_text_sha256')}
        if (review.task_id != task_id or review.event_type != 'narration_semantic_review'
                or payload.get('evidence_kind') != 'pi_semantic_comparison'
                or any(payload.get(name) != value for name, value in expected.items())
                or (payload.get('source', {}).get('input_id') or payload.get('source', {}).get('assistant_turn_id')) != command.input_id
                or payload.get('input_id') != command.input_id
                or not payload.get('source', {}).get('assistant_turn_id')
                or payload.get('protected_rules') != [item.model_dump(mode='json') for item in command.protected_rules]):
            raise ValueError('语义回执没有绑定同一原输入、稳定段落及改写前后版本')
        rules = inspect_narration_revision(current['text'], command.replacement_text, command.protected_rules)
        if rules['status'] != 'passed':
            raise ValueError('明确保护规则未通过；当前采用版本保持不变')
        if payload.get('conclusion') != 'preserved' or not payload.get('findings') or any(
                item.get('assessment') != 'preserved' for item in payload['findings']):
            raise ValueError('Pi语义复核存在存疑或冲突，须先补充核查或取得必要决定')
        if ({rule.id for rule in command.protected_rules} - {item['rule_id'] for item in payload['findings']}
                or any(item['before'] not in current['text'] or item['after'] not in command.replacement_text
                    for item in payload['findings'])):
            raise ValueError('Pi复核未覆盖明确规则，或对照原句没有绑定实际正文')
        if current['locked_body_digest'] and command.section_id not in payload.get('authorized_locked_section_ids', []):
            raise PermissionError('原正文已锁定，本次回执没有明确的局部解锁授权')
        return current, rules

    def submit_revision(self, workspace_id, task_id, actor, command, *, runtime, write_guard=None):
        self.require_actor(workspace_id, task_id, actor)
        original = self.repository.find_segment_revision_request(workspace_id, task_id, actor.member_id, command.request_id)
        encoded = command.model_dump(mode='json')
        if original is not None:
            if original.payload['command'] != encoded:
                raise ValueError('同一修改请求身份已绑定其他内容')
            return self.read_request(workspace_id, task_id, command.request_id, actor)
        _current, rules = self.verify_revision(workspace_id, task_id, actor, command)
        task = self.repository.get_creation_task(workspace_id, task_id)
        def submit_guard(conn):
            if write_guard is not None:
                write_guard(conn)
            self.verify_revision(workspace_id, task_id, actor, command, connection=conn)
        revision_digest = hashlib.sha256(json.dumps(encoded, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        policy = (self.budget_policy_for(workspace_id, task_id, actor, revision_digest, operation='segment_revision')
            if self.budget_policy_for is not None else None)
        job = self.repository.create_segment_revision_job(task, actor, encoded, rules, write_guard=submit_guard, budget_policy=policy)
        runtime.scheduler.submit(SubmissionIntent(workspace_id, 'creation_job', job.id, job.input_digest,
            self.revision_key(job), SchedulePolicy()), actor)
        runtime.wake()
        return self.read_request(workspace_id, task_id, command.request_id, actor)

    def read_request(self, workspace_id, task_id, request_id, actor):
        self.require_actor(workspace_id, task_id, actor)
        event = self.repository.find_segment_revision_request(workspace_id, task_id, actor.member_id, request_id)
        if event is None:
            raise KeyError(request_id)
        job = self.repository.get_job_run(workspace_id, event.job_id)
        requests = self.repository.list_creation_job_request_states(workspace_id, job.id)
        result = self.repository.segment_revision_result(workspace_id, job.id)
        task = self.repository.get_creation_task(workspace_id, task_id)
        if self.repository.unsettled_segment_units(workspace_id, job.id):
            action, reason = 'query_original', 'original_unit_usage_unknown'
        elif job.status == 'queued' and not requests or (job.status in {'failed', 'cancelled'}
                and len([item for item in requests if item['state'] in {'failed', 'cancelled'}]) == 1):
            action, reason = 'resume_original', 'original_released_request'
        elif job.status in {'queued', 'running', 'interrupted'}:
            action, reason = 'wait', 'original_request_pending'
        else:
            action, reason = 'none', None
        return {'task_id': task_id, 'request_id': request_id, 'input_id': event.payload['command']['input_id'],
            'section_id': event.payload['command']['section_id'], 'job_id': job.id, 'status': job.status,
            'device_requests': [dict(item) for item in requests], 'result': result,
            'rules_check': event.payload['rules_check'], 'semantic_review_ref': event.payload['command']['semantic_review_ref'],
            'queue_state': requests[-1]['state'] if requests else 'submission_pending',
            'waiting_reason': job.failure_reason or (requests[-1]['wait_reason'] or requests[-1]['last_failure_reason']
                if requests else 'queue_submission_pending'),
            'usage_source': 'original_execution_receipts', 'adoption': self.adoption(task, result),
            'recovery': {'action': action, 'reason_code': reason, 'requires_revalidation': action == 'resume_original'}}

    def planning_inputs(self, snapshot, section_id, *, connection, checksum):
        checksum.update(snapshot.input_digest.encode())
        for section, measured, window in self.repository.iter_creation_section_planning_inputs(
                snapshot.task, snapshot.script_header, connection=connection):
            # The target uses a possibly not-yet-persisted audio/text override.
            # Its original version has a separate CAS; all other inputs belong
            # to the global clock observed in this same snapshot.
            value = ([section.id] if section.id == section_id else
                [section.id, hashlib.sha256(section.content.encode()).hexdigest(), measured, window])
            checksum.update(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b'\n')
            yield section, measured, window

    def section_plan(self, task, section_id, audio, owner, *, connection=None, text=None):
        from app.montage import MontagePlanner, creation_task_montage_plan_to_payload
        from app.creation.duration_requirement import SectionWindow
        from app.creation.montage_stage_use_case import delivery_windows_from_section_inputs
        with self.repository.creation_execution_read(connection) as conn:
            snapshot = self.repository.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
            if snapshot.task.confirmed_script_id != task.confirmed_script_id:
                raise ValueError('当前确认文案在规划前变化')
            task, script, creation_plan = snapshot.task, snapshot.script_header, snapshot.plan
            current = self.repository.read_creation_section_input(task, script, section_id, connection=conn)
            section = current['section']
            section = replace(section, content=text) if text is not None else section
            checksum = hashlib.sha256()
            requirement, windows = delivery_windows_from_section_inputs(creation_plan,
                self.planning_inputs(snapshot, section_id, connection=conn, checksum=checksum),
                exclude_section=section_id, measured_override={section_id: audio.duration_seconds},
                text_override=(section_id, section.content))
        window = next(item for item in windows if item.section_id == section_id)
        allowed = (owner.task_authorizations.material_asset_ids(task.workspace_id, task.id)
            if owner.task_authorizations is not None else None)
        assets = list({asset.id:asset for asset,_ in self.repository.automatic_material_segments(task.workspace_id,
            viewer_user_id=task.owner_user_id,asset_ids=allowed)}.values())
        plan = MontagePlanner().build_for_creation_section(workspace_id=task.workspace_id, task_id=task.id,
            delivery_window=SectionWindow(section_id, 0, window.duration_seconds),
            script_header=script, section=section, profile=self.repository.get_profile(task.workspace_id),
            duration_target_seconds=max(1, math.ceil(window.duration_seconds)), assets=assets,
            media_reference_for_asset=lambda asset_id: self.repository.workspace_media_reference_for_asset(task.workspace_id, asset_id),
            narration_audio_path=self.repository.narration_artifact_reference(audio.cache_key),
            narration_duration_seconds=audio.duration_seconds,
            narration_segment_id=self.narration_stage.segment_id(task.id, script.id, section_id))
        from app.audio_timeline import narration_audio_timeline
        plan.audio_timeline = narration_audio_timeline(plan.narration_audio_path, audio.duration_seconds)
        plan.delivery_window = asdict(window)
        plan.duration_requirement = requirement.payload()
        plan.aspect_ratio = creation_plan.aspect_ratio
        # Planner IDs/timestamps are presentation metadata, not fresh inputs on each eligibility check.
        plan.id = 'section_montage_' + digest([task.id, section_id, audio.cache_key])[:24]
        plan.created_at = plan.updated_at = task.created_at
        cue_ids = {cue.id:f'{plan.id}_cue_{index}' for index,cue in enumerate(plan.cues)}
        plan.cues = [replace(cue,id=cue_ids[cue.id]) for cue in plan.cues]
        plan.clips = [replace(clip, id=f'{plan.id}_clip_{index}',cue_id=cue_ids[clip.cue_id]) for index, clip in enumerate(plan.clips)]
        paths = [Path(audio.audio_path)]
        for clip in plan.clips:
            paths.append(self.repository.resolve_clip_media_reference(task.workspace_id, clip.asset_id, clip.source_path))
        sources = []
        for path in dict.fromkeys(paths):
            stat = path.stat()
            if stat.st_size <= 0:
                raise ValueError('段落生产来源为空')
            sources.append({'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns})
        return plan, sources, creation_task_montage_plan_to_payload(plan), checksum.hexdigest()

    def validate_planning_dependency(self, payload, *, workspace_id, task_id, connection, target_text=None):
        snapshot = self.repository.read_creation_execution_snapshot(workspace_id, task_id, connection=connection)
        checksum = hashlib.sha256()
        inputs = self.planning_inputs(snapshot, payload['section_id'], connection=connection, checksum=checksum)
        if 'planning_dependency_digest' in payload:
            for _ in inputs:
                pass
            if checksum.hexdigest() != payload['planning_dependency_digest']:
                raise ValueError('全片时长规划的正文、实测时长或采用窗口已变化')
            return
        # Existing candidates retain their keys. Their saved global window and
        # duration agreement are the available clock proof; never rebuild them.
        from app.creation.montage_stage_use_case import delivery_windows_from_section_inputs
        current = self.repository.read_creation_section_input(snapshot.task, snapshot.script_header,
            payload['section_id'], connection=connection)
        audio = self.repository.get_narration_artifact(workspace_id, payload['audio_cache_key'], connection=connection)
        if audio is None:
            raise ValueError('原画面决定的音频不可读取，不能复核原规划')
        requirement, windows = delivery_windows_from_section_inputs(snapshot.plan, inputs,
            exclude_section=payload['section_id'], measured_override={payload['section_id']: audio.duration_seconds},
            text_override=(payload['section_id'], target_text) if target_text is not None else None)
        window = next(item for item in windows if item.section_id == current['section'].id)
        if (asdict(window) != payload['montage'].get('delivery_window')
                or json.loads(json.dumps(requirement.payload())) != payload['montage'].get('duration_requirement')):
            raise ValueError('原画面决定的全片时长窗口已变化')

    def video_facts(self, job, event, task, section_id, owner, *, connection=None):
        prepared = self.repository.execution_records.get_task_event(task.workspace_id,
            self.video_preparation_id(job,section_id),connection=connection).payload
        if prepared['expected_segment_version'] != self.repository.stage_section_version(
                task.workspace_id,task.id,section_id,connection=connection):
            raise ValueError('已准备的段落输入版本已变化')
        if event.get('command'):
            from app.schemas import NarrationRevisionRequest
            self.verify_revision(task.workspace_id,task.id,self.actor_from_event(task.workspace_id,event),
                NarrationRevisionRequest.model_validate(event['command']),connection=connection)
        return prepared['unit_key'],prepared['resources']

    @staticmethod
    def video_preparation_id(job,section_id):
        return f'event_{job.id}_video_prepared_{section_id}'

    def prepare_video(self, job, event, task, section_id, audio, owner, *, text=None):
        from app.canvas import canvas_size
        from app.creation.contracts import TaskEvent
        command = event.get('command')
        if audio is None:
            raise ValueError('段落配音产物不可读取')
        if text is None:
            with self.repository.creation_execution_read() as conn:
                snapshot = self.repository.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
                current = self.repository.read_creation_section_input(snapshot.task, snapshot.script_header,
                    section_id, connection=conn)
                text = current['section'].content
        plan, sources, payload, planning_dependency = self.section_plan(task, section_id, audio, owner, text=text)
        limits = owner.cpu_render_limits
        if limits is None:
            raise ValueError('有声段落缺少真实CPU渲染资源档')
        width, height = canvas_size(plan.aspect_ratio)
        resources = {'kind': 'creation_render', 'execution_mode': 'cpu_ffmpeg', 'duration_seconds': plan.duration_seconds,
            'width_px': width, 'height_px': height, 'input_bytes': sum(item['size'] for item in sources),
            'clip_count': len(plan.clips), 'overlay_count': len(plan.graphic_overlays),
            'max_output_bytes': limits.max_output_bytes, 'threads': limits.threads, 'timeout_seconds': limits.timeout_seconds}
        version = command['expected_segment_version'] if command else digest_section_version(task.confirmed_script_id,
            section_id,text,self.repository.narration_artifact_reference(audio.cache_key))
        facts = {'section_id':section_id,'audio_cache_key':audio.cache_key,'montage':payload,'sources':sources,
            'expected_segment_version':version,'resources':resources}
        facts['unit_key'] = f'segment_video:{section_id}:{digest(facts)}'
        facts.update(input_digest=job.input_digest,script_id=task.confirmed_script_id,
            audio_reference=self.repository.narration_artifact_reference(audio.cache_key))
        facts['decision_digest'] = digest(facts)
        facts['planning_dependency_digest'] = planning_dependency
        waiting = any(clip.needs_confirmation for clip in plan.clips)
        return TaskEvent(id=self.visual_decision_id(job,section_id) if waiting else self.video_preparation_id(job,section_id),
            workspace_id=task.workspace_id,task_id=task.id,job_id=job.id,
            event_type='segment_visual_decision_required' if waiting else 'segment_video_prepared',payload=facts)

    def planning_conflict_event(self, job, task, section_id, audio, error):
        from app.creation.contracts import TaskEvent
        return TaskEvent(id=f'event_{job.id}_visual_planning_{section_id}', workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type='segment_visual_planning_conflict', payload={
                'section_id': section_id, 'script_id': task.confirmed_script_id, 'input_digest': job.input_digest,
                'audio_cache_key': audio.cache_key, 'reason': str(error)})

    def planning_conflict(self, job, section_id):
        try:
            return self.repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_visual_planning_{section_id}')
        except KeyError:
            return None

    @staticmethod
    def visual_decision_id(job, section_id):
        return f'event_{job.id}_visual_decision_{section_id}'

    def visual_decision(self, job, section_id, *, connection=None):
        try:
            return self.repository.execution_records.get_task_event(job.workspace_id,self.visual_decision_id(job,section_id),connection=connection)
        except KeyError:
            return None

    def waiting_for_visual_decision(self, job, *, connection=None):
        with nullcontext(connection) if connection is not None else self.repository._connect() as conn:
            return conn.execute("SELECT 1 FROM task_events e WHERE e.workspace_id=? AND e.job_id=? "
                "AND e.event_type IN ('segment_visual_decision_required','segment_visual_planning_conflict') "
                "AND NOT EXISTS (SELECT 1 FROM task_events p WHERE p.workspace_id=e.workspace_id "
                "AND p.id='event_' || e.job_id || '_video_prepared_' || json_extract(e.payload_json,'$.section_id')) LIMIT 1",
                (job.workspace_id, job.id)).fetchone() is not None

    def persist_video_preparation(self, prepared, grant, *, connection):
        return self.persist_video_preparation_for_request(prepared, grant.request_id, connection=connection)

    def persist_video_preparation_for_request(self, prepared, request_id, *, connection):
        if prepared.event_type in {'segment_visual_decision_required', 'segment_video_prepared'}:
            self.validate_planning_dependency(prepared.payload, workspace_id=prepared.workspace_id,
                task_id=prepared.task_id, connection=connection)
        if prepared.event_type in {'segment_visual_decision_required', 'segment_visual_planning_conflict'}:
            prepared = replace(prepared,payload={**prepared.payload,'request_id':request_id})
        self.repository.execution_records.append_task_event(prepared,connection=connection)
        if prepared.event_type in {'segment_visual_decision_required', 'segment_visual_planning_conflict'}:
            connection.execute("UPDATE job_runs SET failure_reason='waiting_for_user' WHERE workspace_id=? AND id=?",
                (prepared.workspace_id,prepared.job_id))

    def _validate_visual_decision(self, job, task, actor, owner, payload, *, connection=None, check_scope=None):
        if check_scope is None:
            check_scope = connection is None
        if connection is None or not connection.in_transaction:
            with self.repository.creation_execution_read() as reader:
                return self._validate_visual_decision(job, task, actor, owner, payload, connection=reader, check_scope=check_scope)
        event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start',connection=connection).payload
        if (job.task_id != task.id or actor != self.actor_from_event(job.workspace_id,event)
                or job.status not in {'queued','running','interrupted'} or payload['input_digest'] != job.input_digest
                or payload['script_id'] != task.confirmed_script_id
                or payload['expected_segment_version'] != self.repository.stage_section_version(
                    job.workspace_id,task.id,payload['section_id'],connection=connection)):
            raise ValueError('原画面决定的作业或段落版本已变化')
        operation = 'narration_edit' if job.kind == SEGMENT_REVISION_KIND else 'render'
        if owner is not None and owner.authorization_verdict(task,actor,operation,connection=connection,
                check_scope=check_scope) != 'ready':
            raise PermissionError('原画面决定授权不可用')
        if event.get('command'):
            from app.schemas import NarrationRevisionRequest
            command = NarrationRevisionRequest.model_validate(event['command'])
            self.verify_revision(job.workspace_id,task.id,actor,command,connection=connection)
            if command.section_id != payload['section_id']:
                raise ValueError('画面决定不属于原修改段落')
        else:
            if owner is not None:
                self.require_settled_narration(job, connection=connection)
            snapshot = self.repository.read_creation_execution_snapshot(job.workspace_id, task.id, connection=connection)
            if snapshot.input_digest != job.input_digest:
                raise ValueError('原画面决定的确认文案或方案版本已变化')
            current = self.repository.read_creation_section_input(snapshot.task, snapshot.script_header,
                payload['section_id'], connection=connection)
            audio = current['audio']
            if audio is None or audio.status != 'ready' or audio.audio_reference != payload['audio_reference']:
                raise ValueError('原段落音频检查点已变化')
        artifact = self.repository.get_narration_artifact(job.workspace_id,payload['audio_reference'].removeprefix('narration://artifact/'),connection=connection)
        if artifact is None or not Path(artifact.audio_path).is_file() or Path(artifact.audio_path).stat().st_size <= 0:
            raise ValueError('原段落音频产物不可用')
        for source in payload['sources']:
            stat = Path(source['path']).stat()
            if (stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns) != (source['size'],source['mtime_ns'],source['ctime_ns']):
                raise ValueError('原画面决定的媒体来源已变化')
        self.validate_planning_dependency(payload, workspace_id=job.workspace_id, task_id=task.id, connection=connection,
            target_text=event.get('command', {}).get('replacement_text'))

    def visual_decision_locators(self, workspace_id, task_id, actor, *, job_id=None, limit=24):
        from urllib.parse import quote
        self.require_actor(workspace_id,task_id,actor)
        parameters=[workspace_id,task_id,actor.member_id]
        where=''
        if job_id is not None:
            where=' AND j.id=?'
            parameters.append(job_id)
        sql=(" FROM task_events e JOIN job_runs j ON j.workspace_id=e.workspace_id AND j.id=e.job_id "
            "JOIN task_events start ON start.workspace_id=j.workspace_id AND start.id='event_' || j.id || '_start' "
            "WHERE e.workspace_id=? AND e.task_id=? AND e.event_type IN ('segment_visual_decision_required','segment_visual_planning_conflict') "
            "AND j.status IN ('queued','running','interrupted') AND json_extract(start.payload_json,'$.member_id')=? "
            "AND (e.event_type='segment_visual_decision_required' OR NOT EXISTS (SELECT 1 FROM task_events d "
            "WHERE d.workspace_id=e.workspace_id AND d.id='event_' || j.id || '_visual_decision_' || "
            "json_extract(e.payload_json,'$.section_id'))) "
            "AND (NOT EXISTS (SELECT 1 FROM task_events p WHERE p.workspace_id=e.workspace_id "
            "AND p.id='event_' || j.id || '_video_prepared_' || json_extract(e.payload_json,'$.section_id')) "
            "OR (e.event_type='segment_visual_decision_required' AND NOT EXISTS (SELECT 1 FROM device_work_requests w WHERE w.workspace_id=e.workspace_id "
            "AND w.owner_type='creation_job' AND w.owner_id=j.id AND w.unit_key=json_extract(e.payload_json,'$.unit_key'))))"+where)
        with self.repository._connect() as conn:
            rows=conn.execute("SELECT json_extract(e.payload_json,'$.section_id') AS section_id,"
                "json_extract(e.payload_json,'$.decision_digest') AS decision_digest,e.job_id,start.payload_json AS start_json,EXISTS(SELECT 1 FROM task_events p "
                "WHERE p.workspace_id=e.workspace_id AND p.id='event_' || j.id || '_video_prepared_' || json_extract(e.payload_json,'$.section_id')) AS adopted"+sql+
                ' ORDER BY e.created_at DESC,e.id DESC LIMIT ?',[*parameters,limit]).fetchall()
            total=conn.execute('SELECT COUNT(*)'+sql,parameters).fetchone()[0]
        if not rows:
            return {'items':[],'total_count':total,'truncated':False}
        titles=self.repository.creation_section_titles(workspace_id,task_id,[row['section_id'] for row in rows])
        items=[]
        for row in rows:
            if self.actor_from_event(workspace_id,json.loads(row['start_json']))!=actor:
                continue
            section=row['section_id']
            items.append({'job_id':row['job_id'],'section_id':section,'phase':'segment_video',
                'section_title':titles.get(section,'待确认段落'),'adopted':bool(row['adopted']),'decision_digest':row['decision_digest'],
                'detail_url':f'/api/creation-tasks/{quote(task_id,safe="")}/execution/jobs/{quote(row["job_id"],safe="")}/sections/{quote(section,safe="")}/visual-decision'})
        return {'items':items,'total_count':total,'truncated':total>len(items)}

    def visual_usage(self, job):
        workspace_id, job_id = job.workspace_id, job.id
        if job.kind == SEGMENT_REVISION_KIND:
            with self.repository._connect() as conn:
                rows = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type='task_execution_unit_settled'",(workspace_id,job_id)).fetchall()
            usage = {'settled':not self.repository.unsettled_segment_units(workspace_id,job_id),
                'used_seconds':sum(json.loads(row['payload_json'])['elapsed_seconds'] for row in rows),
                'used_output_bytes':sum(json.loads(row['payload_json'])['output_bytes'] for row in rows),
                'remaining_budget':None}
        else:
            measured = self.repository.creation_narration_usage(workspace_id,job_id)
            budget = measured['budget']
            usage = {'settled':measured['consumption_known'],'used_seconds':measured['used_seconds'],
                'used_output_bytes':measured['used_output_bytes'],'remaining_budget':None if budget is None else {
                    'seconds':None if budget['parent_timeout_seconds'] is None else budget['parent_timeout_seconds']-measured['used_seconds'],
                    'output_bytes':None if budget['parent_max_output_bytes'] is None else budget['parent_max_output_bytes']-measured['used_output_bytes']}}
        return usage

    def read_visual_decision(self, workspace_id, task_id, job_id, section_id, actor, *, offset=0, limit=8):
        if offset<0 or not 1<=limit<=8:
            raise ValueError('画面候选分页范围无效')
        task = self.require_actor(workspace_id,task_id,actor)
        job = self.repository.get_job_run(workspace_id,job_id)
        if job.task_id != task_id:
            raise PermissionError('画面决定不属于当前任务')
        start = self.repository.execution_records.get_task_event(workspace_id,f'event_{job_id}_start').payload
        if actor != self.actor_from_event(workspace_id,start):
            raise PermissionError('画面决定不属于原身份')
        waiting = self.visual_decision(job,section_id)
        if waiting is None:
            conflict = self.planning_conflict(job, section_id)
            if conflict is not None:
                try:
                    self.repository.execution_records.get_task_event(workspace_id, self.video_preparation_id(job, section_id))
                    resolved = True
                except KeyError:
                    resolved = False
                return {'workspace_id': workspace_id, 'task_id': task_id, 'job_id': job_id,
                    'section_id': section_id, 'script_id': task.confirmed_script_id,
                    'input_digest': job.input_digest, 'status': 'adopted' if resolved else 'planning_conflict', 'can_adopt': False,
                    'adopted': resolved, 'request_id': conflict.payload.get('request_id'),
                    'revision_request_id': start.get('command', {}).get('request_id'),
                    'expected_segment_version': start.get('command', {}).get('expected_segment_version'),
                    'decision_digest': None, 'candidate_clips': [], 'candidate_total_count': 0,
                    'candidate_offset': offset, 'next_offset': None, 'confirmed_clip_ids': [],
                    'device_request_id': None, 'required_confirmation_ids': [],
                    'reason': '合法画面已准备，原请求继续执行' if resolved else conflict.payload['reason'],
                    'kept_audio_reference': self.repository.narration_artifact_reference(conflict.payload['audio_cache_key']),
                    'audio': {'artifact_reference': self.repository.narration_artifact_reference(conflict.payload['audio_cache_key']),
                        'persisted': self.repository.get_narration_artifact(workspace_id, conflict.payload['audio_cache_key']) is not None},
                    'usage': self.visual_usage(job),
                    'recovery': {'action': 'resume_original', 'requires_revalidation': True,
                        'detail': '原配音和消耗已保存；处理时长或合法素材冲突后恢复原请求，不会重新生成已就绪配音。'}}
            raise KeyError(section_id)
        payload = waiting.payload
        try:
            prepared = self.repository.execution_records.get_task_event(workspace_id,self.video_preparation_id(job,section_id))
        except KeyError:
            prepared = None
        status = 'adopted' if prepared is not None else 'waiting_for_user'
        try:
            self._validate_visual_decision(job,task,actor,None,payload)
        except (ValueError,KeyError,OSError):
            status = 'stale'
        requests = self.repository.list_creation_job_request_states(workspace_id,job_id)
        video_requests = [row for row in requests if row['unit_key'] == payload['unit_key']]
        usage = self.visual_usage(job)
        clips=payload['montage']['clips']
        cues={item['id']:item['text'] for item in payload['montage']['cues']}
        from urllib.parse import quote
        candidates=[{**clip,'label':cues.get(clip['cue_id'],'候选画面'),
            'preview_url':(f'/api/creation-tasks/{quote(task_id,safe="")}/person-renders/{quote(clip["person_render_id"],safe="")}/media' if clip.get('person_render_id')
                else f'/api/production/materials/{quote(clip["asset_id"],safe="")}/media')+f'#t={clip["source_start_seconds"]},{clip["source_end_seconds"]}'}
            for clip in clips[offset:offset+limit]]
        return {'task_id':task_id,'status':status,'adopted':prepared is not None,'decision_digest':payload['decision_digest'],'job_id':job_id,
            'request_id':payload.get('request_id'),'revision_request_id':start.get('command',{}).get('request_id'),
            'section_id':section_id,'expected_segment_version':payload['expected_segment_version'],
            'audio':{'artifact_reference':payload['audio_reference'],'persisted':True},'usage':usage,
            'candidate_clips':candidates,'candidate_total_count':len(clips),'candidate_offset':offset,
            'next_offset':offset+limit if offset+limit<len(clips) else None,
            'confirmed_clip_ids':prepared.payload['confirmed_clip_ids'] if prepared is not None else [],
            'required_confirmation_ids':[clip['id'] for clip in payload['montage']['clips'] if clip['needs_confirmation']],
            'device_request_id':video_requests[0]['id'] if video_requests else None,
            'recovery':{'action':'revalidate' if status == 'stale' else 'await_confirmation' if status == 'waiting_for_user'
                else 'query_original' if video_requests else 'submit_video'}}

    def adopt_visual_decision(self, workspace_id, task_id, job_id, section_id, actor, *,
            decision_digest, confirmed_clip_ids, runtime, write_guard=None):
        from app.creation.contracts import TaskEvent
        from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
        from app.creation.editing.clips import apply_clip_patch
        owner = runtime.registrations['creation_job'].owner
        with self.repository._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            task = self.require_actor(workspace_id,task_id,actor,connection=conn)
            job = self.repository.get_job_run(workspace_id,job_id,connection=conn)
            waiting = self.visual_decision(job,section_id,connection=conn)
            if waiting is None or job.task_id != task_id:
                raise ValueError('原段落画面决定不存在')
            payload = waiting.payload
            if decision_digest != payload['decision_digest']:
                raise ValueError('画面决定摘要已变化')
            required = [clip['id'] for clip in payload['montage']['clips'] if clip['needs_confirmation']]
            if len(confirmed_clip_ids) != len(set(confirmed_clip_ids)) or set(confirmed_clip_ids) != set(required):
                raise ValueError('必须明确确认原候选全部待决定画面，不能自动采用或替换')
            self._validate_visual_decision(job,task,actor,owner,payload,connection=conn)
            try:
                prepared = self.repository.execution_records.get_task_event(workspace_id,self.video_preparation_id(job,section_id),connection=conn)
            except KeyError:
                prepared = None
            replayed = prepared is not None
            if prepared is None:
                plan = creation_task_montage_plan_from_payload(payload['montage'])
                for clip_id in required:
                    plan,_,_ = apply_clip_patch(plan,clip_id,{'confirmed':True},
                        resolve_source=lambda clip, values:self.repository.resolve_edit_clip_source(
                            workspace_id,task_id,clip,{**values,'automation_only':True},
                            user_id=actor.user_id,local_diagnostic=actor.device_id is None),link_reordered=None)
                from app.creation.editing.metadata_use_case import refresh_montage_review_state
                refresh_montage_review_state(plan)
                encoded = creation_task_montage_plan_to_payload(plan)
                prepared = TaskEvent(id=self.video_preparation_id(job,section_id),workspace_id=workspace_id,
                    task_id=task_id,job_id=job_id,event_type='segment_video_prepared',payload={**payload,
                        'montage':encoded,'confirmed_clip_ids':required})
                self.repository.execution_records.append_task_event(prepared,connection=conn)
            elif prepared.payload.get('decision_digest') != decision_digest:
                raise ValueError('原视频准备已绑定其他决定')
            conn.execute("UPDATE job_runs SET failure_reason=NULL WHERE workspace_id=? AND id=? AND status IN ('queued','interrupted')",
                (workspace_id,job_id))
        request = runtime.scheduler.submit(SubmissionIntent(workspace_id,'creation_job',job.id,job.input_digest,
            prepared.payload['unit_key'],SchedulePolicy()),actor)
        runtime.wake()
        return {**self.read_visual_decision(workspace_id,task_id,job_id,section_id,actor),
            'device_request_id':request.id,'replayed':replayed}

    def capture_local(self, request, conn, owner):
        job = self.repository.get_job_run(request.workspace_id,request.owner_id,connection=conn)
        task = self.repository.get_creation_task(request.workspace_id,job.task_id,connection=conn)
        event = self.repository.execution_records.get_task_event(request.workspace_id,f'event_{job.id}_start',connection=conn).payload
        actor = self.actor_from_event(request.workspace_id,event)
        selected_assets = []
        section_id = event['command']['section_id'] if event.get('command') else None
        if request.unit_key.startswith('segment_video:'):
            section_id = request.unit_key.removeprefix('segment_video:').rpartition(':')[0]
            prepared = self.repository.execution_records.get_task_event(request.workspace_id,self.video_preparation_id(job,section_id),connection=conn)
            selected_assets = sorted({clip['asset_id'] for clip in prepared.payload['montage']['clips']})
        def read(current):
            task_row = current.execute("SELECT plan_id,json_extract(payload,'$.confirmed_script_id'),"
                "json_extract(payload,'$.owner_user_id') "
                "FROM creation_tasks WHERE workspace_id=? AND id=?",(request.workspace_id,task.id)).fetchone()
            plan_row = current.execute('SELECT visibility FROM creation_plans WHERE workspace_id=? AND id=?',
                (request.workspace_id,task.plan_id)).fetchone()
            assets = [tuple(row) for row in current.execute('SELECT id,updated_at FROM material_assets WHERE workspace_id=? '
                'AND id IN (SELECT value FROM json_each(?)) ORDER BY id',(request.workspace_id,json.dumps(selected_assets)))]
            dependencies = self.repository.section_dependency_snapshot(request.workspace_id,task.id,section_id,connection=current)
            return (tuple(task_row) if task_row else None,tuple(plan_row) if plan_row else None,
                digest(dependencies),tuple(assets))
        before = read(conn)
        plan = self.repository.plan_records.read_creation_plan(conn,request.workspace_id,task.plan_id)
        profiles = self.repository.capture_profile_local_check(conn,request.workspace_id,
            [('voice',plan.selected_voice.profile_id,plan.selected_voice.version)],
            [(item.id,item.version) for item in plan.pronunciation_references])
        def check(current):
            if read(current) != before:
                return 'stale'
            current_job = self.repository.get_job_run(request.workspace_id,request.owner_id,connection=current)
            if current_job.status not in {'queued','interrupted','running'}:
                return 'stale'
            verdict = owner.authorization_verdict(task,actor,'narration_edit' if event.get('command') else 'render',
                connection=current,check_scope=False)
            return profiles(current) if verdict=='ready' else verdict
        return check

    @staticmethod
    def actor_from_event(workspace_id, event):
        from app.creation_execution import CreationExecutionActor
        return CreationExecutionActor.from_event(workspace_id, event)

    def revision_key(self, job, reference=None):
        if reference is None:
            from app.profile_reference_media import ProfileReferenceInput
            with self.repository._connect() as conn:
                saved = self.repository.creation_narration_reference(job.workspace_id, job.id, connection=conn)
            if saved is None:
                from app.device_scheduler import RecoveryBlocked
                raise RecoveryBlocked('original_source_content_proof_missing: 原局部配音缺少来源封存')
            reference = ProfileReferenceInput(**saved['reference'])
        return revision_execution_key(job.input_digest, reference)

    def verify_submission(self, intent, actor, owner, *, connection=None, reference_preparation=None):
        job = self.repository.get_job_run(intent.workspace_id, intent.owner_id, connection=connection)
        event = self.repository.execution_records.get_task_event(intent.workspace_id, f'event_{job.id}_start', connection=connection).payload
        task = self.require_actor(intent.workspace_id, job.task_id, actor, connection=connection)
        if actor != self.actor_from_event(intent.workspace_id, event) or job.status not in {'queued','interrupted','running'}:
            raise PermissionError('段落执行不属于原身份或原作业')
        operation = 'narration_edit' if job.kind == SEGMENT_REVISION_KIND else 'render'
        if owner.authorization_verdict(task, actor, operation, connection=connection,check_scope=connection is None) != 'ready':
            raise PermissionError('段落执行的原任务授权不可用')
        if intent.unit_key.startswith('segment_revision:'):
            if self.visual_decision(job,event['command']['section_id'],connection=connection) is not None:
                raise ValueError('原段落已有音频检查点，不能重新配音')
            from app.schemas import NarrationRevisionRequest
            command = NarrationRevisionRequest.model_validate(event['command'])
            self.verify_revision(task.workspace_id, task.id, actor, command, connection=connection)
            plan = self.repository.plan_records.get_creation_plan(task.workspace_id, task.plan_id)
            if reference_preparation is None:
                with self.repository._connect() if connection is None else nullcontext(connection) as conn:
                    reference_preparation = self.repository.creation_narration_reference(job.workspace_id, job.id, connection=conn)
                if reference_preparation is None:
                    from app.device_scheduler import RecoveryBlocked
                    raise RecoveryBlocked('original_source_content_proof_missing: 原局部配音缺少来源封存')
            from app.creation.narration_pcm import narration_reference_facts
            inputs, reference, reference_preparation = narration_reference_facts(self.repository, task, plan, reference_preparation)
            budget = owner.narration_budget
            if budget is None or budget.input_scope not in {'single_call_experiment_v1', 'single_call_prepared_v1'}:
                raise ValueError('局部改写配音缺少原单次资源档')
            resources = {'kind': 'creation_narration', 'execution_mode': 'isolated_qwen3_tts', 'narration_reference': reference_preparation,
                'text_chars': len(command.replacement_text)+len(inputs.voice.reference_text), 'section_count': 1,
                'reference_bytes': Path(reference.source_path).stat().st_size, 'duration_seconds': budget.max_duration_seconds,
                'max_output_bytes': budget.max_output_bytes, 'threads': budget.max_threads, 'timeout_seconds': budget.max_timeout_seconds}
            from app.creation_execution import narration_scope_fields
            script = self.repository.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
            language = {'zh-CN': 'zh', 'en-US': 'en', 'zh-CN/en-US': 'zh-en'}[script.language]
            resources.update(narration_scope_fields(budget, task=task, inputs=inputs, reference=reference,
                language=language, max_output_bytes=resources['max_output_bytes'],
                runtime_facts=self.narration_runtime_facts,
                source_binding=(self.repository.creation_narration_source_binding(
                    job.workspace_id, job.id, connection=connection).payload
                    if budget.input_scope == 'single_call_prepared_v1' else None)))
            key = self.revision_key(job, reference)
        else:
            section_id = intent.unit_key.removeprefix('segment_video:').rpartition(':')[0]
            prepared = self.repository.execution_records.get_task_event(job.workspace_id,self.video_preparation_id(job,section_id),connection=connection)
            self._validate_visual_decision(job,task,actor,owner,prepared.payload,connection=connection)
            key, resources = self.video_facts(job, event, task, section_id, owner, connection=connection)
        if (key != intent.unit_key or intent.input_digest != job.input_digest or intent.owner_type != 'creation_job'):
            raise ValueError('段落执行输入或实际资源版本已变化')
        return VerifiedWork(job.workspace_id, 'creation_job', job.id, job.input_digest, key, actor.member_id,
            'preview_dependency' if job.kind == SEGMENT_REVISION_KIND else 'normal_make', resources)

    def check_current(self, request, connection, owner):
        try:
            event = self.repository.execution_records.get_task_event(request.workspace_id, f'event_{request.owner_id}_start', connection=connection)
            actor = self.actor_from_event(request.workspace_id, event.payload)
            trusted = self.verify_submission(SubmissionIntent(request.workspace_id, request.owner_type, request.owner_id,
                request.input_digest, request.unit_key, request.policy), actor, owner, connection=connection)
            if request.unit_key.startswith('segment_video:'):
                section_id = request.unit_key.removeprefix('segment_video:').rpartition(':')[0]
                prepared = self.repository.execution_records.get_task_event(request.workspace_id,self.video_preparation_id(
                    self.repository.get_job_run(request.workspace_id,request.owner_id,connection=connection),section_id),connection=connection)
                for source in prepared.payload['sources']:
                    stat = Path(source['path']).stat()
                    if (stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns) != (source['size'],source['mtime_ns'],source['ctime_ns']):
                        return 'stale'
            return 'ready' if (trusted.resources == request.resources and trusted.member_id == request.member_id
                and trusted.work_class==request.work_class and trusted.source_refs==request.source_refs) else 'stale'
        except PermissionError:
            return 'unauthorized'
        except (KeyError, StopIteration, ValueError, OSError, RecoveryBlocked):
            return 'dependency'

    def require_settled_narration(self, job, *, connection):
        if self.repository.creation_narration_usage(job.workspace_id, job.id, connection=connection)['unsettled']:
            raise RecoveryBlocked('原配音请求消耗未知，须先核对原单元，不能排入段落视频')

    def next_initial(self, job, owner, *, connection=None):
        candidate = None
        decided = False
        with self.repository.creation_execution_read(connection) as conn:
            self.require_settled_narration(job, connection=conn)
            snapshot = self.repository.read_creation_execution_snapshot(job.workspace_id, job.task_id, connection=conn)
            task, script = snapshot.task, snapshot.script_header
            event = self.repository.execution_records.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=conn).payload
            for section, audio in self.repository.iter_creation_narration_sections(task, script, connection=conn):
                # Finish the scan even after selecting a unit: a bad tail or
                # orphan audio must not be hidden by an unfinished first section.
                if audio is not None and audio.status == 'ready' and audio.id != self.narration_stage.segment_id(
                        task.id, script.id, section.id):
                    raise ValueError('现有配音片段不属于当前唯一文案段落')
                state = self.repository.stage_segment_state(task.workspace_id, task.id, section.id, connection=conn)
                if decided:
                    continue
                if audio is None or audio.status != 'ready':
                    decided = True
                elif state is None or state['narration_reference'] != audio.audio_reference:
                    candidate = section, audio
                    decided = True
        if candidate is None:
            return None
        section, audio = candidate
        try:
            self.repository.execution_records.get_task_event(job.workspace_id,self.video_preparation_id(job,section.id),connection=connection)
        except KeyError:
            if self.visual_decision(job,section.id,connection=connection) is not None:
                return None
            if connection is not None and connection.in_transaction:
                raise ValueError('段落视频输入须先在写事务外准备')
            record = self.repository.get_narration_artifact(job.workspace_id,audio.audio_reference.removeprefix('narration://artifact/'))
            prepared = self.prepare_video(job,event,task,section.id,record,owner,text=audio.text)
            conflict = self.planning_conflict(job, section.id)
            if conflict is not None:
                prepared = replace(prepared, payload={**prepared.payload, 'request_id': conflict.payload['request_id']})
            def verify_preparation(conn):
                current_job = self.repository.get_job_run(job.workspace_id, job.id, connection=conn)
                current_task = self.repository.get_creation_task(job.workspace_id, task.id, connection=conn)
                self._validate_visual_decision(current_job, current_task,
                    self.actor_from_event(job.workspace_id, event), owner, prepared.payload, connection=conn)
            self.repository.save_segment_video_preparation(prepared, write_guard=verify_preparation)
            if prepared.event_type == 'segment_visual_decision_required':
                return None
        key, _resources = self.video_facts(job, event, task, section.id, owner, connection=connection)
        return SubmissionIntent(job.workspace_id, 'creation_job', job.id, job.input_digest, key, SchedulePolicy())

    def record_stopped(self, grant, scheduler, exc):
        from app.device_scheduler import RecoveryBlocked
        reason = str(exc) if isinstance(exc, RecoveryBlocked) else f'段落执行停止：{type(exc).__name__}'
        def stopped(conn, current):
            job = self.repository.get_job_run(current.workspace_id,current.owner_id,connection=conn)
            if job.status == 'running':
                return self.repository.checkpoint_segment_unit(current,status='interrupted',reason=reason,connection=conn)
        scheduler.record_owner_stop(grant,owner_database_path=self.repository.database_path,owner_write=stopped)

    def resume_unit(self,conn,request,actor,owner,*,write_guard=None):
        event = self.repository.execution_records.get_task_event(request.workspace_id,f'event_{request.owner_id}_start',connection=conn)
        if self.actor_from_event(request.workspace_id,event.payload)!=actor:
            raise PermissionError('不能恢复其他成员的原段落执行')
        if write_guard is not None:
            write_guard(conn)
        self.repository.resume_segment_job(request.workspace_id,request.owner_id,connection=conn)
        return self.verify_submission(SubmissionIntent(request.workspace_id,request.owner_type,request.owner_id,
            request.input_digest,request.unit_key,request.policy),actor,owner,connection=conn)

    def resume_request(self,workspace_id,task_id,request_id,actor,*,runtime,write_guard=None):
        result = self.read_request(workspace_id,task_id,request_id,actor)
        job = self.repository.get_job_run(workspace_id,result['job_id'])
        start = self.repository.execution_records.get_task_event(workspace_id,f'event_{job.id}_start').payload
        section_id = start['command']['section_id']
        decision = self.visual_decision(job,section_id)
        if decision is None and job.status == 'queued' and self.planning_conflict(job, section_id) is not None:
            from app.schemas import NarrationRevisionRequest
            owner = runtime.registrations['creation_job'].owner
            command = NarrationRevisionRequest.model_validate(start['command'])
            task = self.require_actor(workspace_id, task_id, actor)
            self.verify_revision(workspace_id, task_id, actor, command)
            try:
                prepared = self.repository.execution_records.get_task_event(workspace_id, self.video_preparation_id(job, section_id))
            except KeyError:
                paid = self.repository.execution_records.get_task_event(workspace_id, f'event_{job.id}_revision_audio').payload
                audio = self.repository.get_narration_artifact(workspace_id, paid['audio_cache_key'])
                prepared = self.prepare_video(job, start, task, section_id, audio, owner, text=command.replacement_text)
                with self.repository._connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if write_guard is not None: write_guard(conn)
                    current = self.repository.get_job_run(workspace_id, job.id, connection=conn)
                    if current.status != 'queued':
                        raise ValueError('原段落作业已停止，不能保存恢复画面；原音频与取消事实保留')
                    self.verify_revision(workspace_id, task_id, actor, command, connection=conn)
                    self.persist_video_preparation_for_request(prepared, result['device_requests'][-1]['id'], connection=conn)
            if prepared.event_type == 'segment_video_prepared':
                runtime.scheduler.submit(SubmissionIntent(workspace_id, 'creation_job', job.id, job.input_digest,
                    prepared.payload['unit_key'], SchedulePolicy()), actor)
                runtime.wake()
            return self.read_request(workspace_id, task_id, request_id, actor)
        if decision is not None and job.status == 'queued':
            view = self.read_visual_decision(workspace_id,task_id,job.id,section_id,actor)
            if view['status'] == 'adopted':
                self.adopt_visual_decision(workspace_id,task_id,job.id,section_id,actor,
                    decision_digest=view['decision_digest'],confirmed_clip_ids=view['required_confirmation_ids'],
                    runtime=runtime,write_guard=write_guard)
            elif view['status'] == 'stale':
                raise ValueError('原画面决定输入已变化，不能恢复')
            return self.read_request(workspace_id,task_id,request_id,actor)
        if not result['device_requests'] and result['status']=='queued':
            job = self.repository.get_job_run(workspace_id,result['job_id'])
            runtime.scheduler.submit(SubmissionIntent(workspace_id,'creation_job',job.id,job.input_digest,
                self.revision_key(job),SchedulePolicy()),actor)
            runtime.wake()
            return self.read_request(workspace_id,task_id,request_id,actor)
        if result['status'] == 'queued':
            from app.creation_execution import migrate_creation_narration_request
            job = self.repository.get_job_run(workspace_id, result['job_id'])
            migrate_creation_narration_request(self.repository, runtime.scheduler, job, actor, write_guard=write_guard)
            runtime.wake()
            return self.read_request(workspace_id, task_id, request_id, actor)
        if result['status'] in {'interrupted','running','succeeded'}:
            runtime.wake()
            return result
        rows = [item for item in result['device_requests'] if item['state'] in {'failed','cancelled'}]
        if len(rows)!=1:
            raise ValueError('原修改没有唯一已释放的执行单元，须先核对原请求')
        owner = runtime.registrations['creation_job'].owner
        runtime.scheduler.resume_creation_narration(rows[0]['id'],resume_write=lambda conn,request:
            self.resume_unit(conn,request,actor,owner,write_guard=write_guard))
        runtime.wake()
        return self.read_request(workspace_id,task_id,request_id,actor)

    def recover(self,runtime):
        for job in self.repository.list_pending_task_media_jobs(SEGMENT_REVISION_KIND):
            event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start')
            actor = self.actor_from_event(job.workspace_id,event.payload)
            owner = runtime.registrations['creation_job'].owner
            if owner.authorization_verdict(self.require_actor(job.workspace_id,job.task_id,actor),actor,'narration_edit')!='ready':
                continue
            self.resume_request(job.workspace_id,job.task_id,event.payload['command']['request_id'],actor,runtime=runtime)

    def execute(self, grant, scheduler, executor):
        """Each model call and audiovisual render uses a separate original grant."""
        import threading
        import time
        from datetime import datetime, timezone, timedelta
        from app.creation_execution import CreationStageGrantContext, next_creation_intent
        from app.creation.contracts import TaskEvent, ArtifactRecord, TransformRun, DependencyEdge
        from app.device_scheduler import RecoveryBlocked
        from app.host_resource_probes import HostResourceRefusal
        from app.granted_subprocess import GrantedCommandCancelled
        from app.schemas import NarrationRevisionRequest

        owner = scheduler.owners
        if hasattr(owner,'registrations'):
            owner = owner.registrations['creation_job'].owner
        if self.repository.get_job_run(grant.workspace_id,grant.owner_id).status=='cancelled':
            scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,cancelled=True,
                checkpoint_write=lambda conn,current:self.repository.checkpoint_segment_unit(current,
                    status='cancelled',reason='user_cancelled',connection=conn))
            return
        scheduler.assert_grant(grant)
        with self.repository._connect() as conn:
            request = conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?',(grant.request_id,)).fetchone()
            permit = conn.execute('SELECT granted_at_utc FROM device_permit WHERE id=1 AND request_id=?',(grant.request_id,)).fetchone()
        if request is None or permit is None:
            raise PermissionError('分段执行缺少当前真实许可')
        resources = json.loads(request[0])
        elapsed_before = max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(permit[0])).total_seconds())
        started = time.monotonic()-elapsed_before
        cancellation = threading.Event()
        executor.register_cancellation(grant.request_id,cancellation)
        context = CreationStageGrantContext(scheduler,grant,started+resources['timeout_seconds'],
            resources['max_output_bytes'],resources['threads'],cancellation,started_monotonic=started,narration_output_usage=[0])
        job = self.repository.start_segment_unit(grant)
        event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start').payload
        actor = self.actor_from_event(job.workspace_id,event)
        task = self.require_actor(job.workspace_id,job.task_id,actor)
        command = NarrationRevisionRequest.model_validate(event['command']) if event.get('command') else None
        stage_dir = self.output_root/'segment-staging'/job.id/digest(grant.unit_key)[:24]/str(grant.generation)
        measured_bytes = 0
        retained_bytes = 0
        pcm_preparation=None

        def measure_retained():
            nonlocal retained_bytes
            try:
                retained_bytes = sum(path.stat().st_size for path in stage_dir.rglob('*') if path.is_file()) if stage_dir.exists() else 0
            except OSError as exc:
                raise RecoveryBlocked('分段产物消耗尚未核实，保留原开始回执') from exc

        def settle(conn,current):
            if context.narration_output_usage[0] is None:
                raise RecoveryBlocked('局部配音输出消耗未知，保留原开始回执')
            self.repository.settle_segment_unit(current,elapsed_seconds=max(0,time.monotonic()-started),
                output_bytes=max(measured_bytes,retained_bytes,context.narration_output_usage[0]),connection=conn)

        def next_unit(conn,current):
            updated = self.repository.get_job_run(job.workspace_id,job.id,connection=conn)
            if command is not None:
                if self.waiting_for_visual_decision(updated,connection=conn):
                    return None
                key,_ = self.video_facts(updated,event,task,command.section_id,owner,connection=conn)
                intent = SubmissionIntent(job.workspace_id,'creation_job',job.id,job.input_digest,key,SchedulePolicy())
            else:
                intent = next_creation_intent(self.repository,scheduler,updated,connection=conn,pcm_preparation=pcm_preparation)
            return (intent,owner.verify_submission(intent,actor,connection=conn,pcm_preparation=pcm_preparation)) if intent else None

        try:
            context.check()
            if grant.unit_key.startswith('segment_revision:'):
                self.verify_revision(job.workspace_id,job.task_id,actor,command)
                audio, segment, measured_bytes = self.narration_stage.prepare_revision(task,command,grant_context=context)
                from app.creation.duration_requirement import DurationConflict
                try:
                    video_preparation = self.prepare_video(job,event,task,command.section_id,audio,owner,text=command.replacement_text)
                except DurationConflict as conflict:
                    video_preparation = self.planning_conflict_event(job,task,command.section_id,audio,conflict)
                def commit_audio(conn,current):
                    context.check()
                    self.verify_revision(job.workspace_id,job.task_id,actor,command,connection=conn)
                    self.repository.save_narration_artifact(audio,connection=conn)
                    self.persist_video_preparation(video_preparation,current,connection=conn)
                    self.repository.execution_records.append_task_event(TaskEvent(id=f'event_{job.id}_revision_audio',workspace_id=job.workspace_id,
                        task_id=job.task_id,job_id=job.id,event_type='segment_revision_audio',payload={
                            'audio_cache_key':audio.cache_key,'segment':asdict(segment)}),connection=conn)
                    settle(conn,current)
                    return self.repository.checkpoint_segment_unit(current,status='queued',connection=conn,
                        reason='waiting_for_user' if video_preparation.event_type in {'segment_visual_decision_required', 'segment_visual_planning_conflict'} else None,
                        payload={'audio_cache_key':audio.cache_key})
                measure_retained()
                scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=commit_audio,next_unit_write=next_unit)
                return

            section_id = grant.unit_key.removeprefix('segment_video:').rpartition(':')[0]
            current_context = self.context(job.workspace_id,job.task_id,section_id,actor)
            if command:
                self.verify_revision(job.workspace_id,job.task_id,actor,command)
                prepared = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_revision_audio').payload
                audio = self.repository.get_narration_artifact(job.workspace_id,prepared['audio_cache_key'])
                text = command.replacement_text
            else:
                reference = current_context['audio']['artifact_ref']
                audio = self.repository.get_narration_artifact(job.workspace_id,reference.removeprefix('narration://artifact/'))
                text = current_context['text']
            from app.montage import creation_task_montage_plan_from_payload
            prepared_video = self.repository.execution_records.get_task_event(job.workspace_id,self.video_preparation_id(job,section_id)).payload
            plan_payload,sources = prepared_video['montage'],prepared_video['sources']
            plan = creation_task_montage_plan_from_payload(plan_payload)
            key,_ = self.video_facts(job,event,task,section_id,owner)
            if key != grant.unit_key:
                raise ValueError('渲染前段落来源已变化')
            prefix = f'creation-tasks/{job.workspace_id}/{job.task_id}/segments/{job.id}/{digest(grant.unit_key)}'
            video_ref, subtitle_ref = prefix+'/video.mp4',prefix+'/subtitles.srt'
            try:
                published_video = self.artifact_store.describe(video_ref)
                published_subtitles = self.artifact_store.describe(subtitle_ref)
            except FileNotFoundError:
                published_video = published_subtitles = None
            if published_video is None:
                executable = replace(plan,clips=[replace(clip,source_path=str(self.repository.resolve_clip_media_reference(
                    job.workspace_id,clip.asset_id,clip.source_path))) for clip in plan.clips])
                self.renderer.render(executable,audio.audio_path,job_staging_path=stage_dir,
                    scheduler=scheduler,grant=grant,cancel_event=cancellation,max_output_bytes=context.max_output_bytes,
                    threads=context.threads,deadline_monotonic=context.deadline_monotonic)
                context.check()
                published_video = self.artifact_store.publish_file(video_ref,stage_dir/'preview.mp4')
                published_subtitles = self.artifact_store.publish_file(subtitle_ref,stage_dir/'subtitles.srt')
                measured_bytes = published_video.size_bytes+published_subtitles.size_bytes
            else:
                from app.montage import _validate_rendered_media
                from app.canvas import canvas_size
                _validate_rendered_media(self.artifact_store.resolve_file(video_ref),expected_size=canvas_size(plan.aspect_ratio),
                    expected_duration_seconds=plan.duration_seconds,scheduler=scheduler,grant=grant,
                    cancel_event=cancellation,deadline_monotonic=context.deadline_monotonic)
            now = utc_now()
            input_refs = [self.repository.narration_artifact_reference(audio.cache_key),*[clip.source_path for clip in plan.clips]]
            artifacts = [ArtifactRecord(id=f'artifact_{job.id}_{digest(grant.unit_key)[:20]}_{kind}',workspace_id=job.workspace_id,
                task_id=job.task_id,kind=kind,logical_ref=value.logical_ref,sha256=value.sha256,size_bytes=value.size_bytes,
                producer_job_id=job.id,input_refs=input_refs) for kind,value in [('rendered_video',published_video),('subtitle_track',published_subtitles)]]
            adopted = command is None or command.adoption_mode=='apply'
            publication = TaskEvent(id=f'event_{job.id}_segment_{digest(grant.unit_key)[:24]}',workspace_id=job.workspace_id,
                task_id=job.task_id,job_id=job.id,event_type='segment_publication',payload={
                    'section_id':section_id,'script_id':task.confirmed_script_id,'text':text,
                    'video_artifact_id':artifacts[0].id,'subtitle_artifact_id':artifacts[1].id,
                    'narration_reference':input_refs[0],'duration_seconds':plan.duration_seconds,
                    'narration_duration_seconds':audio.duration_seconds,'montage':plan_payload,
                    'request_id':command.request_id if command else grant.request_id,'adopted':adopted,
                    'segment_version':f'event_{job.id}_segment_{digest(grant.unit_key)[:24]}',
                    'input_id':command.input_id if command else None})
            publication.payload['video'] = {'kind':'video','artifact_ref':video_ref,'sha256':published_video.sha256,
                'url':f'/api/creation-tasks/{task.id}/segments/{section_id}/versions/{publication.id}/video',
                'segment_version':publication.id,'duration_seconds':plan.duration_seconds,
                'narration_duration_seconds':audio.duration_seconds}
            transform = TransformRun(id=f'transform_{publication.id}',workspace_id=job.workspace_id,task_id=job.task_id,
                job_id=job.id,transform_type='section_audiovisual_render',input_digest=digest([plan_payload,sources]),
                input_refs=input_refs,output_refs=[video_ref,subtitle_ref],engine='ffmpeg',parameters={'montage':plan_payload},
                status='succeeded',started_at=now,finished_at=now)
            edges = [DependencyEdge(id=f'{transform.id}_{index}',workspace_id=job.workspace_id,task_id=job.task_id,
                upstream_ref=ref,downstream_ref=output,created_by_transform_id=transform.id)
                for index,(ref,output) in enumerate((ref,output) for ref in input_refs for output in [video_ref,subtitle_ref])]
            from app.creation.section_change_use_case import SectionTimelineChange,SectionPreparationStale
            timeline_change = SectionTimelineChange(self.repository,self.artifact_store,
                max_output_bytes=max(0,context.max_output_bytes-measured_bytes))
            segment = None
            if command and adopted:
                previous = self.repository.get_narration_segment(job.workspace_id,job.task_id,prepared['segment']['id'])
                segment = replace(previous,**{key:prepared['segment'][key] for key in (
                    'text','audio_reference','duration_seconds','sample_rate','model_id','pronunciation_version','status')},
                    updated_at=utc_now())
            def commit_video(conn,current):
                if command:
                    self.verify_revision(job.workspace_id,job.task_id,actor,command,connection=conn)
                self.repository.publish_segment_version(publication,artifacts,transform,edges,adopted=adopted,
                    expected_version_id=current_context['base_version_id'],expected_segment_version=current_context['segment_version'],
                    segment=segment,timeline_change=timeline_change,connection=conn)
                settle(conn,current)
                return self.repository.checkpoint_segment_unit(current,status='succeeded' if command else 'queued',
                    connection=conn,payload={'publication_ref':publication.id})
            if command is None:
                pcm_preparation=owner.prepare_pcm_sources(job)
            for preparation in range(2):
                if segment is not None:
                    context.check()
                    before_bytes = timeline_change.published_bytes
                    try:
                        timeline_change.prepare_current(job.workspace_id,job.task_id,section_id,plan_payload,segment,job)
                    finally:
                        measured_bytes += timeline_change.published_bytes-before_bytes
                    context.check()
                try:
                    measure_retained()
                    scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,checkpoint_write=commit_video,
                        next_unit_write=None if command else next_unit)
                    break
                except SectionPreparationStale:
                    if preparation:
                        raise
        except Exception as exc:
            if isinstance(exc,RecoveryBlocked) and not (isinstance(exc,HostResourceRefusal) and exc.recoverable_pressure):
                raise
            pressure = isinstance(exc,HostResourceRefusal) and exc.recoverable_pressure
            quitting = executor._application_quit.is_set()
            if isinstance(exc,HostResourceRefusal):
                executor.last_resource_refusal = exc
            reason = 'application_quit' if quitting else 'resource_pressure' if pressure else str(exc)
            def stop(conn,current):
                settle(conn,current)
                return self.repository.checkpoint_segment_unit(current,status='interrupted' if quitting or pressure else 'failed',
                    reason=reason or type(exc).__name__,connection=conn)
            cancelled = self.repository.get_job_run(job.workspace_id,job.id).status=='cancelled'
            measure_retained()
            if cancelled:
                def cancel_write(conn,current):
                    settle(conn,current)
                    return self.repository.checkpoint_segment_unit(current,status='cancelled',reason='user_cancelled',connection=conn)
                scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=cancel_write,cancelled=True)
            elif quitting or pressure:
                scheduler.defer_unit(grant,reason='resource_pressure' if pressure else 'worker_interrupted',
                    retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=15),
                    owner_database_path=self.repository.database_path,owner_write=stop)
            else:
                scheduler.fail_unit(grant,reason='segment_production_failed',failure_write=stop)
        finally:
            with executor._cancellation_lock:
                executor._cancellations.pop(grant.request_id,None)

    def video_path(self, workspace_id, task_id, section_id, version, actor):
        self.require_actor(workspace_id,task_id,actor)
        event = self.repository.execution_records.get_task_event(workspace_id,version)
        if event.task_id != task_id or event.event_type != 'segment_publication' or event.payload.get('section_id') != section_id:
            raise PermissionError('视频版本不属于当前段落')
        artifact = self.repository.execution_records.get_artifact_record(workspace_id,event.payload['video_artifact_id'])
        published = self.artifact_store.describe(artifact.logical_ref)
        if published.sha256 != artifact.sha256 or published.size_bytes != artifact.size_bytes:
            raise ValueError('段落视频完整性核验失败')
        return self.artifact_store.resolve_file(artifact.logical_ref)
