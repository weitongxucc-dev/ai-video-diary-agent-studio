"""Creation storyboard planning and its original independent persistence steps."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from app.creation_execution import CreationStageGrantContext, current_presenter_render, iter_presenter_inputs
from app.montage import CreationTaskMontagePlan, MontagePlanner, creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload, select_presenter_sections
from app.materials.contracts import MediaAuthorityError
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask
from app.visual_effects import reusable_color_settings


class MontageStageRejected(Exception):
    def __init__(self, *, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(f'{status_code}: {detail}')


def stage_failure(*, status_code, error, reason, impact, repair_action, retryable):
    return MontageStageRejected(status_code=status_code, detail={
        'error': error, 'reason': reason, 'impact': impact,
        'repair_action': repair_action, 'retryable': retryable,
    })


def voice_timeline(script, audio, windows):
    return voice_timeline_from_audio({section.id: {'id': audio[section.id].id,
        'reference': audio[section.id].audio_reference, 'duration': float(audio[section.id].duration_seconds)}
        for section in script.sections}, windows)


def voice_timeline_from_audio(audio, windows):
    from app.audio_timeline import AudioTimeline, narration_audio_timeline
    first = next(iter(audio.values()))
    voice = narration_audio_timeline(first['reference'], first['duration']).tracks[0]
    voice.clips = []
    for section_id, segment in audio.items():
        clip = narration_audio_timeline(segment['reference'], segment['duration']).tracks[0].clips[0]
        voice.clips.append(clip.model_copy(update={'id': f"audio-narration-{segment['id']}",
            'timeline_start_ms': round(windows[section_id].start_seconds * 1000)}))
    return AudioTimeline(tracks=[voice])


def delivery_windows_from_section_inputs(plan, section_inputs, *, exclude_section=None,
                                        measured_override=None, text_override=None):
    from app.creation.duration_requirement import DurationRequirement, SectionTiming, SectionWindow, measured_section_windows
    requirement = DurationRequirement.from_plan(plan)
    timings, adopted = [], []
    for section, measured, window in section_inputs:
        text = text_override[1] if text_override is not None and section.id == text_override[0] else section.content
        timings.append(SectionTiming.from_text(section.id, text, (measured_override or {}).get(section.id, measured)))
        if window is not None and section.id != exclude_section:
            adopted.append(SectionWindow(**window))
    return requirement, measured_section_windows(requirement, timings, adopted=adopted)


def delivery_windows_for_task(repository, task, script, segments, *, exclude_section=None,
                              measured_override=None, text_override=None):
    # Whole-storyboard consumers already own their full script. They use the
    # same scalar allocator as the single-section snapshot reader.
    plan = repository.plan_records.get_creation_plan(task.workspace_id, task.plan_id)
    measured = {segment.source_section_id: float(segment.duration_seconds)
                for segment in segments if segment.duration_seconds is not None and segment.status == 'ready'}
    def section_inputs():
        for section in script.sections:
            state = repository.stage_segment_state(task.workspace_id, task.id, section.id)
            window = state['montage'].get('delivery_window') if state is not None else None
            yield section, measured.get(section.id, 0), window
    return delivery_windows_from_section_inputs(plan, section_inputs(), exclude_section=exclude_section,
        measured_override=measured_override, text_override=text_override)


def montage_dependency(repository, task, *, connection):
    """Internal preparation proof; never changes a persisted request's recipe."""
    from dataclasses import asdict
    from app.creation.storyboard_regeneration import material_signature
    snapshot = repository.read_creation_execution_snapshot(task.workspace_id, task.id, connection=connection)
    current, header = snapshot.task, snapshot.script_header
    checksum = hashlib.sha256()
    def add(value):
        checksum.update(json.dumps(value, sort_keys=True, default=str).encode())
    add([snapshot.input_digest, current.current_timeline_version_id, current.narration_cache_key])
    narration = repository.get_narration_artifact(task.workspace_id, current.narration_cache_key, connection=connection)
    if narration is None or narration.task_id != task.id or narration.script_id != header.id:
        raise ValueError('当前创作任务的配音产物不可用')
    add(asdict(narration))
    add([asdict(asset) for asset in repository.list_assets(task.workspace_id, connection=connection)])
    count = 0
    for section, audio in repository.iter_creation_narration_sections(current, header, connection=connection):
        if audio is None or audio.status != 'ready' or audio.audio_reference is None or audio.duration_seconds is None:
            raise ValueError('当前创作任务缺少可用的分段配音事实')
        add([asdict(section), asdict(audio), repository.stage_segment_state(
            task.workspace_id, task.id, section.id, connection=connection)])
        path = repository.resolve_authorized_narration_audio_reference(task.workspace_id, audio.audio_reference)
        info = path.stat()
        add([str(path.resolve()), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns])
        count += 1
    if not count or count != header.sections_total_count:
        raise ValueError('原配音片段集合不完整')
    add(material_signature(repository, current, connection=connection))
    return snapshot, checksum.hexdigest()


def montage_output_digest(payload):
    # Repository save stamps updated_at; every other saved field belongs to the
    # exact prepared output. Neither this proof nor its binding enters a unit key.
    return hashlib.sha256(json.dumps({key: value for key, value in payload.items() if key != 'updated_at'},
        sort_keys=True, default=str).encode()).hexdigest()


def verify_saved_montage_preparation(repository, task, payload, request, *, connection):
    event_id = f"event_montage_prepared_{payload['id']}"
    try:
        event = repository.execution_records.get_task_event(task.workspace_id, event_id, connection=connection)
    except KeyError as exc:
        raise ValueError('原故事板缺少可核验的保存来源，已存候选保留，不能自动补认基础版本') from exc
    binding = event.payload
    if (event.task_id != task.id or event.job_id != request.owner_id
            or task.montage_plan_id != payload['id']
            or binding['request_id'] != request.id or binding['unit_key'] != request.unit_key
            or binding['input_digest'] != request.input_digest
            or binding['output_digest'] != montage_output_digest(payload)
            or binding['dependency'] != montage_dependency(repository, task, connection=connection)[1]):
        raise ValueError('原故事板的保存来源或输出已变化，已存候选保留，不能补认基础版本')


class MontageStageUseCase:
    def __init__(self, repository, metadata, person_stage, montage_planner):
        self.repository = repository
        self.metadata = metadata
        self.person_stage = person_stage
        self.montage_planner = montage_planner

    def generate(self, workspace_id: str, task_id: str,
        *, grant_context: CreationStageGrantContext | None = None) -> CreationTaskMontagePlan:
        task = self.task_or_missing(workspace_id, task_id)
        creation_plan = self.plan_for_task_or_missing(workspace_id, task)
        if grant_context is not None:
            grant_context.check()
        elif creation_plan.presenter_share_target:
            raise MontageStageRejected(status_code=409, detail="人物故事板必须通过当前任务的受许可连续制作入口")
        if task.confirmed_script_id is None or task.narration_cache_key is None:
            raise MontageStageRejected(status_code=409, detail="confirmed script or narration missing")
        try:
            task = self.repository.start_creation_task_montage(workspace_id, task.id)
        except ValueError as exc:
            raise stage_failure(
                status_code=409,
                error="creation_task_montage_conflict",
                reason="当前任务还不能生成故事板。",
                impact="没有读取或改写剪辑计划。",
                repair_action=str(exc),
                retryable=False,
            ) from exc
        try:
            montage, dependency = self._build_candidate(task, creation_plan, grant_context, with_dependency=True)
            if task.montage_plan_id is None:
                montage = self.include_adopted_sections(task,montage)
            def verify_inputs(conn, current):
                if montage_dependency(self.repository, current, connection=conn)[1] != dependency:
                    raise ValueError('故事板准备期间制作输入已变化')
                if grant_context is not None:
                    owner = grant_context.scheduler.owners
                    if hasattr(owner, 'registrations'):
                        owner = owner.registrations['creation_job'].owner
                    request = grant_context.scheduler._get(grant_context.grant.request_id)
                    if owner.check_current(request, conn) != 'ready':
                        raise PermissionError('原故事板许可或当前授权已变化')
            from app.creation.contracts import TaskEvent
            def seal_inputs(conn, current):
                verify_inputs(conn, current)
                grant = grant_context.grant if grant_context is not None else None
                self.repository.execution_records.append_task_event(TaskEvent(
                    f'event_montage_prepared_{montage.id}', workspace_id, task.id,
                    grant.owner_id if grant is not None else None, 'creation_montage_prepared', {
                        'request_id': grant.request_id if grant is not None else None,
                        'unit_key': grant.unit_key if grant is not None else None,
                        'input_digest': grant.input_digest if grant is not None else None,
                        'dependency': dependency,
                        'output_digest': montage_output_digest(creation_task_montage_plan_to_payload(montage)),
                    }), connection=conn)
            task, _ = self.repository.montage_plan_adoption.save_creation_task_montage_plan(
                workspace_id,
                task.id,
                montage.id,
                creation_task_montage_plan_to_payload(montage), verify_inputs=seal_inputs,
            )
            self.metadata.ensure_montage_base(workspace_id, task, montage, verify_inputs=verify_inputs)
        except (KeyError, MediaAuthorityError, ValueError, MontageStageRejected) as exc:
            task = self.repository.fail_creation_task_stage(
                workspace_id,
                task.id,
                stage="montage",
                reason="montage_plan_failed",
                impact="未生成可供审核的故事板和剪辑计划。",
                repair_action=str(exc.detail) if isinstance(exc, MontageStageRejected) else str(exc),
                retryable=True,
            )
            raise stage_failure(
                status_code=409,
                error="montage_plan_failed",
                reason="素材或配音不满足剪辑计划要求。",
                impact=task.stage_impact or "未生成剪辑计划。",
                repair_action=task.stage_repair_action or "审核素材后重试。",
                retryable=True,
            ) from exc
        return montage


    def build_candidate(self, workspace_id: str, task_id: str, *, grant_context: CreationStageGrantContext):
        """Build under the current permit without changing the adopted plan or task."""
        grant_context.check()
        task = self.task_or_missing(workspace_id, task_id)
        creation_plan = self.plan_for_task_or_missing(workspace_id, task)
        if task.confirmed_script_id is None or task.narration_cache_key is None:
            raise ValueError("confirmed script or narration missing")
        montage = self._build_candidate(task, creation_plan, grant_context)
        return montage

    def _build_candidate(self, task, creation_plan, grant_context, *, with_dependency=False):
        workspace_id = task.workspace_id
        planner = self.montage_planner if grant_context is None else MontagePlanner()
        profile = self.repository.get_profile(workspace_id)
        asset_ids = None
        if grant_context is not None:
            owner = grant_context.scheduler.owners
            if hasattr(owner, 'registrations'):
                owner = owner.registrations['creation_job'].owner
            if owner.task_authorizations is not None:
                asset_ids = owner.task_authorizations.material_asset_ids(workspace_id, task.id)
        with self.repository.creation_execution_read() as conn:
            snapshot, dependency = montage_dependency(self.repository, task, connection=conn)
            task, creation_plan, header = snapshot.task, snapshot.plan, snapshot.script_header
            narration = self.repository.get_narration_artifact(workspace_id, task.narration_cache_key, connection=conn)
            audio = {}
            for section, segment in self.repository.iter_creation_narration_sections(task, header, connection=conn):
                audio[section.id] = {'id': segment.id, 'reference': segment.audio_reference,
                    'duration': float(segment.duration_seconds or 0)}
            selected_sections = select_presenter_sections([(key, row['duration']) for key, row in audio.items()],
                creation_plan.presenter_share_target)
            if selected_sections and creation_plan.selected_voice is None:
                raise ValueError('露脸段落缺少当前任务已授权的人物和声音')
            requirement, windows = delivery_windows_from_section_inputs(creation_plan,
                self.repository.iter_creation_section_planning_inputs(task, header, connection=conn))
            prepared = planner.prepare_creation_inputs(
                lambda: (section for section, _ in self.repository.iter_creation_narration_sections(task, header, connection=conn)),
                profile=profile, duration_target_seconds=creation_plan.duration_target_seconds,
                narration_duration_seconds=narration.duration_seconds,
                narration_segment_ids_by_section={key: row['id'] for key, row in audio.items()},
                narration_segment_durations_by_section={key: row['duration'] for key, row in audio.items()})
            assets = list({asset.id: asset for asset, _ in self.repository.automatic_material_segments(workspace_id,
                viewer_user_id=task.owner_user_id, asset_ids=asset_ids, connection=conn)}.values())
            renders = {}
            if selected_sections:
                if grant_context is None:
                    raise MontageStageRejected(status_code=409, detail='人物故事板必须通过当前任务的受许可连续制作入口')
                for segment, request in iter_presenter_inputs(self.repository, task, header, creation_plan,
                        self.person_stage.presenter_model_ids(), connection=conn):
                    render = current_presenter_render(self.repository, task, segment, request,
                        self.metadata.picture_renderer.person_path)
                    if render is None or self.metadata.picture_renderer.person_path(render) is None:
                        raise ValueError('露脸段落的口型视频没有完成，请重试故事板')
                    renders[segment.source_section_id] = render
        # Complete real captions/queries are required by global allocation and
        # the existing semantic batch. No database snapshot outlives preparation.
        montage = planner.build_for_creation_inputs(workspace_id=workspace_id, task_id=task.id,
            script_header=header, prepared=prepared, assets=assets,
            media_reference_for_asset=lambda asset_id: self.repository.workspace_media_reference_for_asset(workspace_id, asset_id),
            narration_audio_path=self.repository.narration_artifact_reference(narration.cache_key),
            person_render_by_section={key: (f'pending:{key}', audio[key]['duration']) for key in selected_sections},
            presenter_share_target=creation_plan.presenter_share_target, delivery_windows=windows)
        montage.duration_requirement = requirement.payload()
        montage.audio_timeline = voice_timeline_from_audio(audio, {window.section_id: window for window in windows})
        montage.narration_linkage_created_audio_timeline = True
        for section_id, render in renders.items():
            grant_context.check()
            for clip in montage.clips:
                cue = next(item for item in montage.cues if item.id == clip.cue_id)
                if cue.source_section_id == section_id:
                    if clip.source_end_seconds > (render.duration_seconds or 0) + 1 / 30:
                        raise ValueError('人物口型输出短于露脸段落，请重试或降低比例')
                    clip.person_render_id = render.id
                    clip.asset_id = f'person:{render.id}'
                    clip.source_path = f'person-render://{render.id}'
                    clip.source_end_seconds = min(clip.source_end_seconds, render.duration_seconds or 0)
        actual = round(100 * sum(clip.timeline_end_seconds - clip.timeline_start_seconds
                                 for clip in montage.clips if clip.person_render_id) / montage.duration_seconds, 1)
        montage.message += f" 露脸目标 {montage.presenter_share_target}%，实际 {actual}%；仅为露脸段落制作口型。"
        if grant_context is not None:
            montage.message += " 本次受许可编排仅使用已授权素材元数据，未运行视觉语义模型。"
        montage.aspect_ratio = creation_plan.aspect_ratio
        for clip in montage.clips:
            clip.fit_mode = creation_plan.template_settings.get("fit_mode", "fill")
            if creation_plan.template_settings.get("color_style") is not None:
                clip.visual_effects.update(reusable_color_settings(creation_plan.template_settings["color_style"]))
        for cue in montage.cues:
            cue.subtitle_style = dict(creation_plan.template_settings.get("subtitle_style", {}))
        if grant_context is not None:
            grant_context.check()
            if len(json.dumps(creation_task_montage_plan_to_payload(montage)).encode()) > grant_context.max_output_bytes:
                raise ValueError("故事板输出超过阶段预算")
        return (montage, dependency) if with_dependency else montage

    def include_adopted_sections(self,task,montage):
        from app.audio_timeline import AudioTimeline,narration_audio_timeline
        with self.repository.creation_execution_read() as conn:
            snapshot = self.repository.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
            task, header = snapshot.task, snapshot.script_header
            audio = {}
            for section, segment in self.repository.iter_creation_narration_sections(task, header, connection=conn):
                if segment is None or segment.status != 'ready':
                    raise ValueError('完整故事板缺少实际段落配音')
                audio[section.id] = {'id': segment.id, 'reference': segment.audio_reference,
                    'duration': float(segment.duration_seconds)}
            requirement, windows = delivery_windows_from_section_inputs(snapshot.plan,
                self.repository.iter_creation_section_planning_inputs(task, header, connection=conn))
            windows = {window.section_id: window for window in windows}
            from fractions import Fraction
            from app.subtitle_schedule import narration_schedule
            global_captions = {cue.id: caption for cue, caption in zip(montage.cues,
                narration_schedule(montage.cues, montage.duration_seconds))}
            cues,clips,cursor = [],[],0.0
            for section_id in audio:
                state = self.repository.stage_segment_state(task.workspace_id, task.id, section_id, connection=conn)
                window = windows[section_id]
                if state is not None:
                    if state['narration_reference'] != audio[section_id]['reference']:
                        raise ValueError('已采用段画面与当前配音不一致，须先恢复该段依赖')
                    local = creation_task_montage_plan_from_payload(state['montage'])
                    section_cues,section_clips,duration = local.cues,local.clips,local.duration_seconds
                    if (abs(duration - window.duration_seconds) > .001
                            or local.delivery_window != {'section_id': section_id,
                                'start_seconds': window.start_seconds, 'end_seconds': window.end_seconds}):
                        raise ValueError(f'已采用段 {section_id} 的画面窗口与目标不一致；请保留原配音并重新确认该段画面。')
                    origin = 0.0
                else:
                    section_cues = [cue for cue in montage.cues if cue.source_section_id==section_id]
                    cue_ids = {cue.id for cue in section_cues}
                    section_clips = [clip for clip in montage.clips if clip.cue_id in cue_ids]
                    origin = min((clip.timeline_start_seconds for clip in section_clips),default=cursor)
                    duration = window.duration_seconds
                if not section_cues or not section_clips:
                    raise ValueError('完整故事板缺少实际段落镜头，未发布半条作品')
                if state is not None:
                    captions = narration_schedule(section_cues, duration)
                    shift = Fraction(round(cursor * 1000), 1000)
                else:
                    captions = [global_captions[cue.id] for cue in section_cues]
                    shift = Fraction(0)
                cues.extend(replace(cue, timeline_start_seconds=float(Fraction(str(caption.start_seconds)) + shift))
                    for cue, caption in zip(section_cues, captions))
                clips.extend(replace(clip,timeline_start_seconds=clip.timeline_start_seconds-origin+cursor,
                    timeline_end_seconds=clip.timeline_end_seconds-origin+cursor) for clip in section_clips)
                cursor = window.end_seconds
            ranges = {}
            for clip in sorted(clips, key=lambda item: (item.asset_id, item.source_start_seconds)):
                if clip.source_start_seconds < ranges.get(clip.asset_id, -1):
                    raise ValueError('已采用段重复消耗同一画面区间；请通过重新生成故事板入口确认完整目标画面，原配音与检查点保留。')
                ranges[clip.asset_id] = clip.source_end_seconds
            timeline = montage.audio_timeline
            created_graph = montage.narration_linkage_created_audio_timeline
            if timeline is None or not any(track.role=='narration' for track in timeline.tracks):
                voice = voice_timeline_from_audio(audio, windows).tracks[0]
                other_tracks = [track.model_copy(deep=True) for track in timeline.tracks] if timeline else []
                timeline,created_graph = AudioTimeline(tracks=[voice,*other_tracks]),True
            return replace(montage,cues=cues,clips=clips,duration_seconds=round(cursor,3),
                duration_requirement=requirement.payload(),
                audio_timeline=timeline,narration_linkage_created_audio_timeline=created_graph,
                low_confidence_clip_count=sum(clip.needs_confirmation for clip in clips),
                status='waiting_for_user' if any(clip.needs_confirmation for clip in clips) else 'ready')

    def task_or_missing(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.task_records.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise MontageStageRejected(status_code=404, detail="creation task not found") from exc


    def plan_for_task_or_missing(
        self,
        workspace_id: str,
        task: CreationTask,
    ) -> CreationPlan:
        try:
            plan = self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise MontageStageRejected(status_code=404, detail="creation task not found") from exc
        if plan.creation_task_id != task.id:
            raise MontageStageRejected(status_code=404, detail="creation task not found")
        return plan
