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

    def __init__(self, repository, *, narration_stage, renderer, artifact_store, output_root, source_repository=None):
        self.repository = repository
        self.narration_stage = narration_stage
        self.renderer = renderer
        self.artifact_store = artifact_store
        self.output_root = Path(output_root)
        self.source_repository = source_repository

    def require_actor(self, workspace_id, task_id, actor, *, connection=None):
        task = self.repository.get_creation_task(workspace_id, task_id, connection=connection)
        if actor.workspace_id != workspace_id or task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError('当前成员不能访问此作品段落')
        return task

    def context(self, workspace_id, task_id, section_id, actor, *, connection=None, facts=None):
        task = self.require_actor(workspace_id, task_id, actor, connection=connection) if facts is None else facts[0]
        script = self.repository.effective_creation_script(workspace_id, task_id, connection=connection) if facts is None else facts[1]
        section = next((item for item in script.sections if item.id == section_id), None)
        if section is None:
            raise ValueError('稳定段落ID不属于当前确认文案')
        ordinal = script.sections.index(section)
        rows = self.repository.list_narration_segments(workspace_id, task_id, script.id, connection=connection) if facts is None else facts[2]
        audio = next((item for item in rows
            if item.source_section_id == section_id), None)
        adopted = self.repository.stage_segment_state(workspace_id, task_id, section_id, connection=connection)
        version = (adopted['segment_version'] if adopted is not None else
            digest_section_version(script.id,section.id,section.content,audio.audio_reference if audio else None))
        text_hash = hashlib.sha256(section.content.encode()).hexdigest()
        dependencies = self.repository.section_dependency_snapshot(workspace_id,task_id,section_id,connection=connection)
        video = None
        if adopted is not None:
            record = self.repository.get_artifact_record(workspace_id, adopted['video_artifact_id'])
            video = {'kind': 'video', 'artifact_ref': record.logical_ref, 'sha256': record.sha256,
                'segment_version': version, 'duration_seconds': adopted['duration_seconds'],
                'url': f'/api/creation-tasks/{task_id}/segments/{section_id}/versions/{version}/video'}
        return {'task_id': task_id, 'section_id': section_id, 'ordinal': ordinal + 1, 'script_id': script.id,
            'base_version_id': task.current_timeline_version_id, 'segment_version': version,
            'title': section.title, 'text': section.content, 'source_text_sha256': text_hash,
            'dependency_digest':digest(dependencies),
            'visual_prompt': section.visual_prompt, 'source_references': section.evidence_reference_ids,
            'source_bindings': self.source_bindings(script, section, limit=24),
            'source_binding_total_count': len(section.evidence_reference_ids),
            'source_bindings_truncated': len(section.evidence_reference_ids) > 24,
            'locked_body_digest': script.locked_body_digest,
            'adjacent_sections': [asdict(item) for item in script.sections[max(0, ordinal-1):ordinal+2] if item.id != section_id],
            'narration_segment_id': audio.id if audio is not None else None,
            'audio': {'kind': 'audio', 'artifact_ref': audio.audio_reference, 'status': audio.status,
                'duration_seconds': audio.duration_seconds} if audio is not None else None,
            'video': video if adopted is not None and adopted['current_validity'] == 'current' else None,
            'historical_video': video,
            'adopted': adopted is not None and adopted['current_validity'] == 'current',
            'adoption': self.adoption(task, adopted, connection=connection)}

    def source_bindings(self, script, section, *, offset=0, limit=24):
        try:
            run = self.repository.get_script_generation_run(script.workspace_id, script.run_id)
        except KeyError:
            run = None
        fixed = ({item['reference_id']: item for item in run.input_references}
            if run is not None and run.task_id == script.task_id else {})
        result = []
        for reference_id in section.evidence_reference_ids[offset:offset+limit]:
            original = fixed.get(reference_id)
            if original is None:
                # Only an evidence object's exact persisted ID is a fallback binding.
                try:
                    evidence = self.repository.get_topic_evidence_reference(script.workspace_id, script.task_id, reference_id)
                except KeyError:
                    evidence = None
                if evidence is not None and evidence.candidate_id == script.topic_id:
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
        task = self.require_actor(workspace_id, task_id, actor)
        script = self.repository.effective_creation_script(workspace_id, task_id)
        section = next((item for item in script.sections if item.id == section_id), None)
        if section is None:
            raise KeyError(section_id)
        items = []
        for binding in self.source_bindings(script, section, offset=offset, limit=limit):
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
            'segment_version': self.repository.stage_section_version(workspace_id, task_id, section_id),
            'items': items, 'offset': offset, 'limit': limit, 'total_count': total, 'truncated': offset+len(items) < total}

    def adoption(self, task, result, *, connection=None):
        current = (self.repository.stage_segment_state(task.workspace_id, task.id, result['section_id'], connection=connection)
            if result is not None else None)
        version = (self.repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id, connection=connection)
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
            'current_timeline_sequence': version.sequence if version else None,
            'current_segment_version': current_version, 'result_segment_version': result_version, 'reason': reason}

    def list_segments(self, workspace_id, task_id, actor, *, offset=0, limit=30):
        task = self.require_actor(workspace_id, task_id, actor)
        script = self.repository.effective_creation_script(workspace_id, task_id)
        rows = self.repository.list_narration_segments(workspace_id, task_id, script.id)
        values = [self.context(workspace_id, task_id, item.id, actor, facts=(task, script, rows))
            for item in script.sections[offset:offset+limit]]
        receipts = self.repository.list_segment_revision_requests(workspace_id, task_id, actor.member_id, limit=limit)
        return {'task_id': task_id, 'total_count': len(script.sections), 'offset': offset,
            'truncated': offset+len(values)<len(script.sections), 'revision_requests': receipts,
            'available_video_count': sum(item['video'] is not None for item in values), 'segments': values}

    def verify_revision(self, workspace_id, task_id, actor, command, *, connection=None):
        current = self.context(workspace_id, task_id, command.section_id, actor, connection=connection)
        if (current['script_id'] != command.script_id or current['segment_version'] != command.expected_segment_version
                or current['source_text_sha256'] != command.source_text_sha256
                or current['dependency_digest'] != command.dependency_digest):
            raise ValueError('原段落、正文或所见版本已变化，请读取当前段落再判断原意')
        if hashlib.sha256(command.replacement_text.encode()).hexdigest() != command.replacement_text_sha256:
            raise ValueError('具体改写与候选正文摘要不一致')
        review = self.repository.get_task_event(workspace_id, command.semantic_review_ref, connection=connection)
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
        job = self.repository.create_segment_revision_job(task, actor, encoded, rules, write_guard=submit_guard)
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

    def section_plan(self, task, section_id, audio, owner, *, connection=None, text=None):
        from app.montage import MontagePlanner, creation_task_montage_plan_to_payload
        script = self.repository.effective_creation_script(task.workspace_id, task.id, connection=connection)
        section = next(item for item in script.sections if item.id == section_id)
        section = replace(section, content=text) if text is not None else section
        allowed = (owner.task_authorizations.material_asset_ids(task.workspace_id, task.id)
            if owner.task_authorizations is not None else None)
        assets = list({asset.id:asset for asset,_ in self.repository.automatic_material_segments(task.workspace_id,
            viewer_user_id=task.owner_user_id,asset_ids=allowed)}.values())
        plan = MontagePlanner().build_for_creation_task(workspace_id=task.workspace_id, task_id=task.id,
            script=replace(script, sections=[section]), profile=self.repository.get_profile(task.workspace_id),
            duration_target_seconds=max(1, math.ceil(audio.duration_seconds)), assets=assets,
            media_reference_for_asset=lambda asset_id: self.repository.workspace_media_reference_for_asset(task.workspace_id, asset_id),
            narration_audio_path=self.repository.narration_artifact_reference(audio.cache_key),
            narration_duration_seconds=audio.duration_seconds,
            narration_segment_ids_by_section={section_id: self.narration_stage.segment_id(task.id, script.id, section_id)},
            narration_segment_durations_by_section={section_id: audio.duration_seconds})
        if any(item.needs_confirmation for item in plan.clips):
            raise ValueError('当前段落的真实画面仍需范围决定，不能发布未确认视频')
        plan.aspect_ratio = self.repository.get_creation_plan(task.workspace_id, task.plan_id).aspect_ratio
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
        return plan, sources, creation_task_montage_plan_to_payload(plan)

    def video_facts(self, job, event, task, section_id, owner, *, connection=None):
        prepared = self.repository.get_task_event(task.workspace_id,
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
        plan, sources, payload = self.section_plan(task, section_id, audio, owner, text=text)
        limits = owner.cpu_render_limits
        if limits is None:
            raise ValueError('有声段落缺少真实CPU渲染资源档')
        width, height = canvas_size(plan.aspect_ratio)
        resources = {'kind': 'creation_render', 'execution_mode': 'cpu_ffmpeg', 'duration_seconds': plan.duration_seconds,
            'width_px': width, 'height_px': height, 'input_bytes': sum(item['size'] for item in sources),
            'clip_count': len(plan.clips), 'overlay_count': len(plan.graphic_overlays),
            'max_output_bytes': limits.max_output_bytes, 'threads': limits.threads, 'timeout_seconds': limits.timeout_seconds}
        version = command['expected_segment_version'] if command else digest_section_version(task.confirmed_script_id,
            section_id,text or next(item.content for item in self.repository.effective_creation_script(task.workspace_id,task.id).sections
                if item.id==section_id),self.repository.narration_artifact_reference(audio.cache_key))
        facts = {'section_id':section_id,'audio_cache_key':audio.cache_key,'montage':payload,'sources':sources,
            'expected_segment_version':version,'resources':resources}
        facts['unit_key'] = f'segment_video:{section_id}:{digest(facts)}'
        return TaskEvent(id=self.video_preparation_id(job,section_id),workspace_id=task.workspace_id,task_id=task.id,
            job_id=job.id,event_type='segment_video_prepared',payload=facts)

    def capture_local(self, request, conn, owner):
        job = self.repository.get_job_run(request.workspace_id,request.owner_id,connection=conn)
        task = self.repository.get_creation_task(request.workspace_id,job.task_id,connection=conn)
        event = self.repository.get_task_event(request.workspace_id,f'event_{job.id}_start',connection=conn).payload
        actor = self.actor_from_event(request.workspace_id,event)
        selected_assets = []
        section_id = event['command']['section_id'] if event.get('command') else None
        if request.unit_key.startswith('segment_video:'):
            section_id = request.unit_key.removeprefix('segment_video:').rpartition(':')[0]
            prepared = self.repository.get_task_event(request.workspace_id,self.video_preparation_id(job,section_id),connection=conn)
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
        plan = self.repository.get_creation_plan(request.workspace_id,task.plan_id)
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
        event = self.repository.get_task_event(intent.workspace_id, f'event_{job.id}_start', connection=connection).payload
        task = self.require_actor(intent.workspace_id, job.task_id, actor, connection=connection)
        if actor != self.actor_from_event(intent.workspace_id, event) or job.status not in {'queued','interrupted','running'}:
            raise PermissionError('段落执行不属于原身份或原作业')
        operation = 'narration_edit' if job.kind == SEGMENT_REVISION_KIND else 'render'
        if owner.authorization_verdict(task, actor, operation, connection=connection,check_scope=connection is None) != 'ready':
            raise PermissionError('段落执行的原任务授权不可用')
        if intent.unit_key.startswith('segment_revision:'):
            from app.schemas import NarrationRevisionRequest
            command = NarrationRevisionRequest.model_validate(event['command'])
            self.verify_revision(task.workspace_id, task.id, actor, command, connection=connection)
            plan = self.repository.get_creation_plan(task.workspace_id, task.plan_id)
            if reference_preparation is None:
                with self.repository._connect() if connection is None else nullcontext(connection) as conn:
                    reference_preparation = self.repository.creation_narration_reference(job.workspace_id, job.id, connection=conn)
                if reference_preparation is None:
                    from app.device_scheduler import RecoveryBlocked
                    raise RecoveryBlocked('original_source_content_proof_missing: 原局部配音缺少来源封存')
            from app.creation.narration_pcm import narration_reference_facts
            inputs, reference, reference_preparation = narration_reference_facts(self.repository, task, plan, reference_preparation)
            budget = owner.narration_budget
            if budget is None or budget.input_scope != 'single_call_experiment_v1':
                raise ValueError('局部改写配音缺少原单次资源档')
            resources = {'kind': 'creation_narration', 'execution_mode': 'isolated_qwen3_tts', 'narration_reference': reference_preparation,
                'input_scope':budget.input_scope,'budget_basis':'controlled_experiment_unverified_legacy_cap',
                'text_chars': len(command.replacement_text)+len(inputs.voice.reference_text), 'section_count': 1,
                'reference_bytes': Path(reference.source_path).stat().st_size, 'duration_seconds': budget.max_duration_seconds,
                'max_output_bytes': budget.max_output_bytes, 'threads': budget.max_threads, 'timeout_seconds': budget.max_timeout_seconds}
            key = self.revision_key(job, reference)
        else:
            section_id = intent.unit_key.removeprefix('segment_video:').rpartition(':')[0]
            key, resources = self.video_facts(job, event, task, section_id, owner, connection=connection)
        if (key != intent.unit_key or intent.input_digest != job.input_digest or intent.owner_type != 'creation_job'):
            raise ValueError('段落执行输入或实际资源版本已变化')
        return VerifiedWork(job.workspace_id, 'creation_job', job.id, job.input_digest, key, actor.member_id,
            'preview_dependency' if job.kind == SEGMENT_REVISION_KIND else 'normal_make', resources)

    def check_current(self, request, connection, owner):
        try:
            event = self.repository.get_task_event(request.workspace_id, f'event_{request.owner_id}_start', connection=connection)
            actor = self.actor_from_event(request.workspace_id, event.payload)
            trusted = self.verify_submission(SubmissionIntent(request.workspace_id, request.owner_type, request.owner_id,
                request.input_digest, request.unit_key, request.policy), actor, owner, connection=connection)
            if request.unit_key.startswith('segment_video:'):
                section_id = request.unit_key.removeprefix('segment_video:').rpartition(':')[0]
                prepared = self.repository.get_task_event(request.workspace_id,self.video_preparation_id(
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

    def next_initial(self, job, owner, *, connection=None):
        task = self.repository.get_creation_task(job.workspace_id, job.task_id, connection=connection)
        script = self.repository.effective_creation_script(job.workspace_id, job.task_id, connection=connection)
        rows = self.repository.list_narration_segments(job.workspace_id, job.task_id, script.id, connection=connection)
        by_section = {item.source_section_id: item for item in rows}
        event = self.repository.get_task_event(job.workspace_id, f'event_{job.id}_start', connection=connection).payload
        for section in script.sections:
            audio = by_section.get(section.id)
            if audio is None or audio.status != 'ready':
                return None
            state = self.repository.stage_segment_state(job.workspace_id, job.task_id, section.id, connection=connection)
            if state is None or state['narration_reference'] != audio.audio_reference:
                try:
                    self.repository.get_task_event(job.workspace_id,self.video_preparation_id(job,section.id),connection=connection)
                except KeyError:
                    if connection is not None and connection.in_transaction:
                        raise ValueError('段落视频输入须先在写事务外准备')
                    record = self.repository.get_narration_artifact(job.workspace_id,audio.audio_reference.removeprefix('narration://artifact/'))
                    prepared = self.prepare_video(job,event,task,section.id,record,owner,text=audio.text)
                    self.repository.save_segment_video_preparation(prepared)
                key, _resources = self.video_facts(job, event, task, section.id, owner, connection=connection)
                return SubmissionIntent(job.workspace_id, 'creation_job', job.id, job.input_digest, key, SchedulePolicy())
        return None

    def record_stopped(self, grant, scheduler, exc):
        from app.device_scheduler import RecoveryBlocked
        reason = str(exc) if isinstance(exc, RecoveryBlocked) else f'段落执行停止：{type(exc).__name__}'
        def stopped(conn, current):
            job = self.repository.get_job_run(current.workspace_id,current.owner_id,connection=conn)
            if job.status == 'running':
                return self.repository.checkpoint_segment_unit(current,status='interrupted',reason=reason,connection=conn)
        scheduler.record_owner_stop(grant,owner_database_path=self.repository.database_path,owner_write=stopped)

    def resume_unit(self,conn,request,actor,owner,*,write_guard=None):
        event = self.repository.get_task_event(request.workspace_id,f'event_{request.owner_id}_start',connection=conn)
        if self.actor_from_event(request.workspace_id,event.payload)!=actor:
            raise PermissionError('不能恢复其他成员的原段落执行')
        if write_guard is not None:
            write_guard(conn)
        self.repository.resume_segment_job(request.workspace_id,request.owner_id,connection=conn)
        return self.verify_submission(SubmissionIntent(request.workspace_id,request.owner_type,request.owner_id,
            request.input_digest,request.unit_key,request.policy),actor,owner,connection=conn)

    def resume_request(self,workspace_id,task_id,request_id,actor,*,runtime,write_guard=None):
        result = self.read_request(workspace_id,task_id,request_id,actor)
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
            event = self.repository.get_task_event(job.workspace_id,f'event_{job.id}_start')
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
        event = self.repository.get_task_event(job.workspace_id,f'event_{job.id}_start').payload
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
                video_preparation = self.prepare_video(job,event,task,command.section_id,audio,owner,text=command.replacement_text)
                def commit_audio(conn,current):
                    context.check()
                    self.verify_revision(job.workspace_id,job.task_id,actor,command,connection=conn)
                    self.repository.save_narration_artifact(audio,connection=conn)
                    self.repository.append_task_event(video_preparation,connection=conn)
                    self.repository.append_task_event(TaskEvent(id=f'event_{job.id}_revision_audio',workspace_id=job.workspace_id,
                        task_id=job.task_id,job_id=job.id,event_type='segment_revision_audio',payload={
                            'audio_cache_key':audio.cache_key,'segment':asdict(segment)}),connection=conn)
                    settle(conn,current)
                    return self.repository.checkpoint_segment_unit(current,status='queued',connection=conn,
                        payload={'audio_cache_key':audio.cache_key})
                measure_retained()
                scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=commit_audio,next_unit_write=next_unit)
                return

            section_id = grant.unit_key.removeprefix('segment_video:').rpartition(':')[0]
            current_context = self.context(job.workspace_id,job.task_id,section_id,actor)
            if command:
                self.verify_revision(job.workspace_id,job.task_id,actor,command)
                prepared = self.repository.get_task_event(job.workspace_id,f'event_{job.id}_revision_audio').payload
                audio = self.repository.get_narration_artifact(job.workspace_id,prepared['audio_cache_key'])
                text = command.replacement_text
            else:
                reference = current_context['audio']['artifact_ref']
                audio = self.repository.get_narration_artifact(job.workspace_id,reference.removeprefix('narration://artifact/'))
                text = current_context['text']
            from app.montage import creation_task_montage_plan_from_payload
            prepared_video = self.repository.get_task_event(job.workspace_id,self.video_preparation_id(job,section_id)).payload
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
                    'narration_reference':input_refs[0],'duration_seconds':audio.duration_seconds,'montage':plan_payload,
                    'request_id':command.request_id if command else grant.request_id,'adopted':adopted,
                    'segment_version':f'event_{job.id}_segment_{digest(grant.unit_key)[:24]}',
                    'input_id':command.input_id if command else None})
            publication.payload['video'] = {'kind':'video','artifact_ref':video_ref,'sha256':published_video.sha256,
                'url':f'/api/creation-tasks/{task.id}/segments/{section_id}/versions/{publication.id}/video',
                'segment_version':publication.id,'duration_seconds':audio.duration_seconds}
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
        event = self.repository.get_task_event(workspace_id,version)
        if event.task_id != task_id or event.event_type != 'segment_publication' or event.payload.get('section_id') != section_id:
            raise PermissionError('视频版本不属于当前段落')
        artifact = self.repository.get_artifact_record(workspace_id,event.payload['video_artifact_id'])
        published = self.artifact_store.describe(artifact.logical_ref)
        if published.sha256 != artifact.sha256 or published.size_bytes != artifact.size_bytes:
            raise ValueError('段落视频完整性核验失败')
        return self.artifact_store.resolve_file(artifact.logical_ref)
