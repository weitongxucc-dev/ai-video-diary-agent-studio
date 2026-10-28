from __future__ import annotations
from app.creation.contracts import ArtifactRecord
from app.avatar_lipsync import AvatarAndLipSyncService
from app.creation_execution import CreationStageGrantContext
from app.creation.contracts import CreationTask
from app.schemas import CreationTaskPersonRenderRequest
from app.creation.contracts import DependencyEdge
from app.task_runtime import JobFailure
from app.task_runtime import JobResult
from app.creation.contracts import JobRun
from app.materials.contracts import MediaAuthorityError
from pathlib import Path
from app.production import PersonRender
from app.creation.contracts import TransformRun
from app.avatar_lipsync import avatar_lipsync_cache_key
from app.montage import creation_task_montage_plan_from_payload
from app.creation_execution import current_presenter_render
import hashlib
from app.creation_execution import presenter_input_facts
from app.creation_execution import iter_presenter_inputs
from app.models import utc_now
from uuid import uuid4
from app.creation.editing.metadata_use_case import prepare_changed_montage, LegacyMontageReference, InvalidMontage

class PersonStageRejected(Exception):

    def __init__(self, *, status_code, detail):
        self.status_code, self.detail = (status_code, detail)
        super().__init__(f'{status_code}: {detail}')

class PersonEditGrantRequired(Exception):
    pass

def stage_failure(*, status_code: int, error: str, reason: str, impact: str, repair_action: str, retryable: bool) -> PersonStageRejected:
    return PersonStageRejected(status_code=status_code, detail={'error': error, 'reason': reason, 'impact': impact, 'repair_action': repair_action, 'retryable': retryable})

def capability_unavailable(capability_id: str, *, message: str, impact: str, repair_action: str) -> PersonStageRejected:
    return PersonStageRejected(status_code=501, detail={'error': 'capability_unavailable', 'capability_id': capability_id, 'detail': message, 'impact': impact, 'repair_action': repair_action, 'retryable': False})

class PersonStageUseCase:

    def __init__(self, repository, metadata, person_preview, video_job_runner, settings):
        self.repository = repository
        self.metadata = metadata
        self.person_preview = person_preview
        self.video_job_runner = video_job_runner
        self.settings = settings

    def generate_record(self, workspace_id: str, task_id: str, payload: CreationTaskPersonRenderRequest, *, regeneration_token: str | None=None, grant_context: CreationStageGrantContext | None=None) -> PersonRender:
        from app.device_scheduler import RecoveryBlocked
        from app.granted_subprocess import GrantedCommandError
        if grant_context is None:
            raise PersonStageRejected(status_code=409, detail='人物制作必须通过已确认任务的继续制作入口取得专属设备许可')
        grant_context.check()
        task = self.task_or_missing(workspace_id, task_id)
        quality = payload.lipsync_quality or self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id).lipsync_quality
        provider = self.person_preview.provider(quality)
        if not provider.ready:
            raise capability_unavailable('avatar_lipsync', message=provider.message, impact='人物片段未生成，当前任务等待人物制作完成，尚未生成成片。', repair_action='配置本地人物口型运行环境后重试。')
        try:
            segment = self.repository.get_narration_segment(workspace_id, task_id, payload.narration_segment_id)
            if segment.status != 'ready' or not segment.audio_reference:
                raise ValueError('人物片段只能使用已完成的配音分段')
            person = self.repository.get_person_profile(workspace_id, payload.person_profile_id, payload.person_version)
            audio_path = self.repository.resolve_authorized_narration_audio_reference(workspace_id, segment.audio_reference)
            render_request = self.repository.resolve_avatar_lipsync_request(workspace_id=workspace_id, person_id=person.id, person_version=person.version, voice_profile_id=segment.voice_profile_id, voice_version=segment.voice_version, audio_path=str(audio_path), audio_cache_key=segment.audio_reference, language=segment.language, motion=payload.motion, expression=payload.expression, authorization_id=person.authorization.id if person.authorization else None, authorization_version=person.authorization.version if person.authorization else None, viewer_user_id=task.owner_user_id, model_id=provider.model_id)
            input_facts = presenter_input_facts(render_request)
            base_cache_key = avatar_lipsync_cache_key(render_request, input_facts=input_facts)
            cache_key = hashlib.sha256(f'{base_cache_key}\nregenerate:{regeneration_token}'.encode('utf-8')).hexdigest() if regeneration_token else base_cache_key
            render = self.repository.create_person_render(PersonRender(id=f'person_render_{cache_key[:24]}', workspace_id=workspace_id, task_id=task_id, person_profile_id=person.id, person_version=person.version, voice_profile_id=segment.voice_profile_id, voice_version=segment.voice_version, narration_segment_id=segment.id, visual_reference=person.avatar_reference, model_id=provider.model_id, motion=payload.motion, expression=payload.expression, cache_key=cache_key, status='pending'))
            if render.status == 'ready':
                if self.metadata.picture_renderer.person_path(render) is None:
                    raise ValueError('已记录的人物片段输出缺失，请重新生成')
            if render.status == 'running':
                return render
            input_refs = list(dict.fromkeys([f'person-profile://{person.id}/{person.version}', f'voice-profile://{segment.voice_profile_id}/{segment.voice_version}', f'narration-segment://{segment.id}', segment.audio_reference, person.avatar_reference]))
            base_idempotency_key = f'person-render:{task_id}:{cache_key}'
            matching_jobs = [item for item in self.repository.job_records.list_job_runs(workspace_id, task_id) if item.kind == 'person_render' and item.input_digest == cache_key]
            latest_matching_job = matching_jobs[-1] if matching_jobs else None
            idempotency_key = latest_matching_job.idempotency_key if latest_matching_job is not None and latest_matching_job.status in {'interrupted', 'succeeded'} else f'{base_idempotency_key}:retry:{len(matching_jobs)}' if matching_jobs else base_idempotency_key
            job = JobRun(id=f'job_{uuid4().hex}', workspace_id=workspace_id, task_id=task_id, kind='person_render', idempotency_key=idempotency_key, input_digest=cache_key)
            boundary_failure = []

            def person_render_job(context) -> JobResult:
                grant_context.check()
                current = self.repository.get_person_render(context.workspace_id, task_id, render.id)
                source = self.metadata.picture_renderer.person_path(current)
                duration_seconds = current.duration_seconds
                alignment_evidence = current.alignment_evidence
                claimed = current.status != 'ready'
                try:
                    if source is None:
                        self.repository.claim_person_render(context.workspace_id, task_id, current.id)
                        result = AvatarAndLipSyncService(self.settings.video_output_path, provider).render(render_request, output_cache_key=cache_key, grant_context=grant_context, repository=self.repository, viewer_user_id=task.owner_user_id)
                        if result.status != 'succeeded' or not result.artifact_path:
                            failed = self.repository.finish_person_render(context.workspace_id, task_id, current.id, status='failed', failure_reason=result.failure_code or 'person_render_failed', impact=result.failure_impact or '人物片段未生成，当前任务等待人物制作完成，尚未生成成片。', repair_action=result.remediation_action or '检查人物口型运行环境后重试。')
                            raise JobFailure(failed.failure_reason or 'person_render_failed', failed.impact or '人物片段未生成，当前任务等待人物制作完成，尚未生成成片。', failed.repair_action or '检查人物口型运行环境后重试。')
                        source = Path(result.artifact_path)
                        duration_seconds = result.duration_seconds
                        alignment_evidence = result.alignment_evidence
                    grant_context.check()
                    if source.stat().st_size > grant_context.max_output_bytes:
                        raise ValueError('人物输出超过专属预算')
                    logical_ref = f'artifacts/{task_id}/{context.job_id}/person-render.mp4'
                    try:
                        published = self.metadata.artifact_store.describe(logical_ref)
                    except FileNotFoundError:
                        published = self.metadata.artifact_store.publish_file(logical_ref, source)
                    artifact_id = f'artifact_{context.job_id}_person_render'
                    try:
                        self.repository.execution_records.get_artifact_record(context.workspace_id, artifact_id)
                    except KeyError:
                        self.repository.execution_records.create_artifact_record(ArtifactRecord(id=artifact_id, workspace_id=context.workspace_id, task_id=task_id, kind='person_render', logical_ref=logical_ref, sha256=published.sha256, size_bytes=published.size_bytes, producer_job_id=context.job_id, input_refs=input_refs))
                    transform_id = f'transform_{context.job_id}'
                    try:
                        transform = self.repository.lineage_records.get_transform_run(context.workspace_id, transform_id)
                    except KeyError:
                        running_job = self.repository.job_records.get_job_run(context.workspace_id, context.job_id)
                        transform = self.repository.lineage_records.create_transform_run(TransformRun(id=transform_id, workspace_id=context.workspace_id, task_id=task_id, job_id=context.job_id, transform_type='person_render', input_digest=cache_key, input_refs=input_refs, output_refs=[logical_ref], engine=provider.model_id, parameters={'motion': payload.motion, 'expression': payload.expression, 'language': segment.language, 'presenter_input_facts': input_facts}, status='succeeded', started_at=running_job.started_at or utc_now(), finished_at=utc_now()))
                    for upstream_ref in input_refs:
                        edge_digest = hashlib.sha256(f'{upstream_ref}\n{logical_ref}'.encode('utf-8')).hexdigest()[:24]
                        edge_id = f'edge_{context.job_id}_{edge_digest}'
                        try:
                            self.repository.lineage_records.get_dependency_edge(context.workspace_id, edge_id)
                        except KeyError:
                            self.repository.lineage_records.create_dependency_edge(DependencyEdge(id=edge_id, workspace_id=context.workspace_id, task_id=task_id, upstream_ref=upstream_ref, downstream_ref=logical_ref, created_by_transform_id=transform.id))
                    grant_context.check()
                    if claimed:
                        current = self.repository.finish_person_render(context.workspace_id, task_id, current.id, status='ready', output_path=str(self.metadata.artifact_store.resolve_file(logical_ref)), duration_seconds=duration_seconds, alignment_evidence=alignment_evidence)
                    self.repository.bind_person_render_artifact(context.workspace_id, task_id, current.id, logical_ref)
                    return JobResult((logical_ref,))
                except (RecoveryBlocked, PermissionError, GrantedCommandError) as exc:
                    boundary_failure.append(exc)
                    if self.repository.get_person_render(context.workspace_id, task_id, current.id).status == 'running':
                        self.repository.finish_person_render(context.workspace_id, task_id, current.id, status='failed', failure_reason='permit_execution_interrupted', impact='人物片段未完成，当前配音及已完成片段保留。', repair_action='重新核验许可、输入与专属预算后继续制作。')
                    raise
                except JobFailure:
                    raise
                except (FileNotFoundError, MediaAuthorityError, OSError, ValueError) as exc:
                    if claimed:
                        try:
                            self.repository.finish_person_render(context.workspace_id, task_id, current.id, status='failed', failure_reason='person_render_lineage_failed', impact='人物片段未进入可信产物记录，当前时间线保持不变。', repair_action=str(exc))
                        except ValueError:
                            pass
                    raise JobFailure('person_render_lineage_failed', '人物片段未进入可信产物记录，当前时间线保持不变。', str(exc)) from exc
            completed_job = self.video_job_runner.run(job, person_render_job)
            if boundary_failure:
                raise boundary_failure[0]
            grant_context.check()
            completed = self.repository.get_person_render(workspace_id, task_id, render.id)
            if completed_job.status != 'succeeded':
                raise stage_failure(status_code=503, error=completed_job.failure_reason or 'person_render_failed', reason=completed_job.failure_reason or '人物口型片段没有生成可信输出。', impact=completed_job.impact or '当前时间线和成片保持不变。', repair_action=completed_job.repair_action or '检查人物口型运行环境后重试。', retryable=True)
            return completed
        except KeyError as exc:
            raise PersonStageRejected(status_code=404, detail='person render input not found') from exc
        except MediaAuthorityError as exc:
            raise stage_failure(status_code=409, error=exc.reason, reason=exc.reason, impact=exc.impact, repair_action=exc.repair_action, retryable=False) from exc
        except ValueError as exc:
            raise PersonStageRejected(status_code=409, detail=str(exc)) from exc

    def generate_stage(self, workspace_id: str, task_id: str, context: CreationStageGrantContext):
        context.check()
        with self.repository.creation_execution_read() as conn:
            snapshot = self.repository.read_creation_execution_snapshot(workspace_id, task_id, connection=conn)
            selected = [segment.id for segment, _ in iter_presenter_inputs(self.repository, snapshot.task,
                snapshot.script_header, snapshot.plan, self.presenter_model_ids(), connection=conn)]
        total_output_bytes = 0
        for segment_id in selected:
            context.check()
            with self.repository.creation_execution_read() as conn:
                current = self.repository.read_creation_execution_snapshot(workspace_id, task_id, connection=conn)
                target = next(((segment, request) for segment, request in iter_presenter_inputs(self.repository,
                    current.task, current.script_header, current.plan, self.presenter_model_ids(), connection=conn)
                    if segment.id == segment_id), None)
                if target is None:
                    raise ValueError('原人物段落或当前份额已变化，不能继续旧安排')
                segment, request = target
            # Every source reader has exited before cache publication or a model
            # call. The original Grant still owns every generated inner result.
            render = current_presenter_render(self.repository, current.task, segment, request, self.metadata.picture_renderer.person_path)
            if render is None:
                render = self.generate_record(workspace_id, task_id, CreationTaskPersonRenderRequest(narration_segment_id=segment.id,
                    person_profile_id=request.person.id, person_version=request.person.version), grant_context=context)
            total_output_bytes += self.metadata.picture_renderer.person_path(render).stat().st_size
            if total_output_bytes > context.max_output_bytes:
                raise ValueError('人物阶段累计输出超过专属预算')
            context.check()

    def apply_segment_edit(self, workspace_id, task, proposal, *, grant_context=None, generated_render=None, connection=None):
        operation_type = proposal.operation['operation_type']
        operation_payload = proposal.operation['payload']
        planned_quality = self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id).lipsync_quality
        chosen_quality = operation_payload.get('lipsync_quality', planned_quality)
        if chosen_quality != planned_quality:
            raise PersonStageRejected(status_code=409, detail='口型档位与当前任务方案不一致，请重新预览')
        person_provider = self.person_preview.provider(chosen_quality)
        required_strings = {key: operation_payload.get(key) for key in ('clip_id', 'cue_id', 'narration_segment_id', 'person_profile_id', 'person_version', 'expected_asset_id', 'expected_segment_id', 'motion', 'expression', 'base_version_id')}
        if any((not isinstance(value, str) or not value for value in required_strings.values())):
            raise PersonStageRejected(status_code=409, detail='invalid person segment edit operation')
        if task.current_timeline_version_id != required_strings['base_version_id']:
            raise PersonStageRejected(status_code=409, detail='creation task edit proposal is stale')
        try:
            base_version = self.repository.get_timeline_version(workspace_id, required_strings['base_version_id'])
            montage = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, task.id))
            clip = next((item for item in montage.clips if item.id == required_strings['clip_id']))
            cue = next((item for item in montage.cues if item.id == required_strings['cue_id']))
            segment = self.repository.get_narration_segment(workspace_id, task.id, required_strings['narration_segment_id'])
        except (KeyError, StopIteration) as exc:
            raise PersonStageRejected(status_code=409, detail='person segment edit base no longer exists') from exc
        if clip.asset_id != required_strings['expected_asset_id'] or clip.segment_id != required_strings['expected_segment_id'] or clip.person_render_id != operation_payload.get('expected_person_render_id') or (cue.narration_segment_id != required_strings['narration_segment_id']):
            raise PersonStageRejected(status_code=409, detail='person segment edit target has changed')
        if operation_type == 'RegeneratePersonRender' and clip.person_render_id is None:
            raise PersonStageRejected(status_code=409, detail='person render no longer exists')
        if segment.audio_reference != operation_payload.get('expected_audio_reference'):
            raise PersonStageRejected(status_code=409, detail='配音已改变，请重新预览人物口型修改。')
        try:
            source_start, source_end = self.person_preview.source_range(workspace_id, task, montage, clip, segment)
        except ValueError as exc:
            raise PersonStageRejected(status_code=409, detail=str(exc)) from exc
        if (source_start, source_end) != (operation_payload.get('source_start_seconds'), operation_payload.get('source_end_seconds')):
            raise PersonStageRejected(status_code=409, detail='配音时轴已改变，请重新预览人物修改。')
        reuse_person_render_id = operation_payload.get('reuse_person_render_id')
        if generated_render is not None:
            render = generated_render
        elif reuse_person_render_id is not None:
            if operation_type != 'ChangePersonSegment' or not isinstance(reuse_person_render_id, str) or (not reuse_person_render_id):
                raise PersonStageRejected(status_code=409, detail='invalid person render reuse operation')
            try:
                render = self.repository.get_person_render(workspace_id, task.id, reuse_person_render_id)
            except KeyError as exc:
                raise PersonStageRejected(status_code=409, detail='person render reuse target no longer exists') from exc
            if render.status != 'ready' or render.person_profile_id != required_strings['person_profile_id'] or render.person_version != required_strings['person_version'] or (render.narration_segment_id != required_strings['narration_segment_id']) or (render.voice_profile_id != segment.voice_profile_id) or (render.voice_version != segment.voice_version) or (render.model_id != person_provider.model_id) or (not self.person_preview.render_matches_narration(render, segment, self.repository.lineage_records.list_transform_runs(workspace_id, task.id))) or (render.motion != required_strings['motion']) or (render.expression != required_strings['expression']) or (self.metadata.picture_renderer.person_path(render) is None):
                raise PersonStageRejected(status_code=409, detail='person render reuse target has changed')
        else:
            if grant_context is None:
                raise PersonEditGrantRequired()
            render = self.generate_record(workspace_id, task.id, CreationTaskPersonRenderRequest(narration_segment_id=required_strings['narration_segment_id'], person_profile_id=required_strings['person_profile_id'], person_version=required_strings['person_version'], motion=required_strings['motion'], expression=required_strings['expression'], lipsync_quality=chosen_quality), grant_context=grant_context, regeneration_token=proposal.id if operation_type == 'RegeneratePersonRender' else None)
            return render
        if grant_context is not None:
            grant_context.check()
        if render.status != 'ready' or self.metadata.picture_renderer.person_path(render) is None:
            raise stage_failure(status_code=503, error=render.failure_reason or 'person_render_failed', reason=render.failure_reason or '人物口型片段没有生成可信输出。', impact=render.impact or '当前时间线和成片保持不变。', repair_action=render.repair_action or '检查人物口型运行环境后重试。', retryable=True)
        if source_end > (render.duration_seconds or 0) + 1 / 30:
            raise PersonStageRejected(status_code=409, detail='人物口型输出不足以覆盖所选配音范围。')
        clip.source_start_seconds = source_start
        clip.source_end_seconds = min(source_end, render.duration_seconds)
        clip.person_render_id = render.id
        clip.match_reasons = [f'人物 {render.person_profile_id} · {render.person_version}', '口型已按当前配音分段重新生成' if operation_type == 'RegeneratePersonRender' else '口型使用当前配音分段生成']
        clip.role = 'a_roll'
        clip.manually_selected = True
        montage, _ = prepare_changed_montage(montage, message='所选人物片段已重新对口型；配音、字幕和其他镜头保持不变，可以重新渲染。' if operation_type == 'RegeneratePersonRender' else '人物口型片段已写入当前版本；配音、字幕和其他镜头保持不变，可以重新渲染。')
        applied_payload = dict(operation_payload)
        applied_payload['person_render_id'] = render.id
        try:
            return self.metadata.persist_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base_version, updated_plan=montage, operation_type=operation_type, operation_payload=applied_payload, connection=connection)
        except LegacyMontageReference as exc:
            raise stage_failure(**exc.details) from exc
        except InvalidMontage as exc:
            raise PersonStageRejected(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise PersonStageRejected(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise PersonStageRejected(status_code=409, detail=str(exc)) from exc

    def prepare_edit(self, workspace_id, task_id, context, proposal_id):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        proposal = self.repository.get_edit_proposal(workspace_id, proposal_id)
        generated = self.apply_segment_edit(workspace_id, task, proposal, grant_context=context)

        def commit(connection):
            # complete_unit owns grant observation and the final local authority
            # check; its write transaction must not repeat network/resource probes.
            context.check_execution_limits()
            current = self.repository.task_records.get_creation_task(workspace_id, task_id)
            return self.apply_segment_edit(workspace_id, current, proposal, generated_render=generated, connection=connection)
        return commit

    def task_or_missing(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.task_records.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise PersonStageRejected(status_code=404, detail='creation task not found') from exc

    def presenter_model_ids(self):
        return {quality: self.person_preview.provider(quality).model_id for quality in ('fast', 'high')}
