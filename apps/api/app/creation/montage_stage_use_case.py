"""Creation storyboard planning and its original independent persistence steps."""
from __future__ import annotations

import json
from dataclasses import replace

from app.creation_execution import CreationStageGrantContext, current_presenter_render, presenter_inputs
from app.montage import CreationTaskMontagePlan, MontagePlanner, creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload, select_presenter_sections
from app.production import CreationPlan, CreationTask, MediaAuthorityError
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
            montage = self._build_candidate(task, creation_plan, grant_context)
            if task.montage_plan_id is None:
                montage = self.include_adopted_sections(task,montage)
            task, _ = self.repository.save_creation_task_montage_plan(
                workspace_id,
                task.id,
                montage.id,
                creation_task_montage_plan_to_payload(montage),
            )
            self.metadata.ensure_montage_base(workspace_id, task, montage)
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
        return self.include_adopted_sections(task,montage) if task.montage_plan_id is None else montage

    def _build_candidate(self, task, creation_plan, grant_context):
        workspace_id = task.workspace_id
        script = self.repository.effective_creation_script(workspace_id, task.id)
        narration = self.repository.get_narration_artifact(
            workspace_id, task.narration_cache_key
        )
        if narration is None or narration.task_id != task.id:
            raise ValueError("当前创作任务的配音产物不可用")
        narration_segments = self.repository.list_narration_segments(
            workspace_id, task.id, script.id
        )
        if not narration_segments or any(
            segment.status != "ready"
            or segment.audio_reference is None
            or segment.duration_seconds is None
            for segment in narration_segments
        ):
            raise ValueError("当前创作任务缺少可用的分段配音事实")
        segments_by_section = {segment.source_section_id: segment for segment in narration_segments}
        selected_sections = select_presenter_sections(
            [(section.id, float(segments_by_section[section.id].duration_seconds or 0))
             for section in script.sections],
            creation_plan.presenter_share_target,
        )
        if selected_sections and creation_plan.selected_voice is None:
            raise ValueError("露脸段落缺少当前任务已授权的人物和声音")
        planner = self.montage_planner if grant_context is None else MontagePlanner()
        asset_ids = None
        if grant_context is not None:
            owner = grant_context.scheduler.owners
            if hasattr(owner, "registrations"):
                owner = owner.registrations["creation_job"].owner
            authorizations = owner.task_authorizations
            if authorizations is not None:
                asset_ids = authorizations.material_asset_ids(workspace_id, task.id)
        montage = planner.build_for_creation_task(
            duration_target_seconds=creation_plan.duration_target_seconds,
            workspace_id=workspace_id,
            task_id=task.id,
            script=script,
            profile=self.repository.get_profile(workspace_id),
            assets=list({asset.id: asset for asset, _ in self.repository.automatic_material_segments(workspace_id,
                viewer_user_id=task.owner_user_id, asset_ids=asset_ids)}.values()),
            media_reference_for_asset=lambda asset_id: self.repository.workspace_media_reference_for_asset(
                workspace_id, asset_id
            ),
            narration_audio_path=self.repository.narration_artifact_reference(
                narration.cache_key
            ),
            narration_duration_seconds=narration.duration_seconds,
            narration_segment_ids_by_section={
                segment.source_section_id: segment.id
                for segment in narration_segments
            },
            narration_segment_durations_by_section={
                segment.source_section_id: float(segment.duration_seconds or 0)
                for segment in narration_segments
            },
            person_render_by_section={section_id: (f"pending:{section_id}", float(segments_by_section[section_id].duration_seconds or 0))
                                      for section_id in selected_sections},
            presenter_share_target=creation_plan.presenter_share_target,
        )
        for section_id in selected_sections:
            segment = segments_by_section[section_id]
            selected_voice = creation_plan.selected_voice
            assert selected_voice is not None
            if grant_context is None:
                raise MontageStageRejected(status_code=409, detail="人物故事板必须通过当前任务的受许可连续制作入口")
            inputs = dict((item.id, request) for item, request in presenter_inputs(
                self.repository, task, script, creation_plan, self.person_stage.presenter_model_ids()))
            render = current_presenter_render(self.repository, task, segment, inputs[segment.id], self.metadata.picture_renderer.person_path)
            grant_context.check()
            if render is None or self.metadata.picture_renderer.person_path(render) is None:
                raise ValueError("露脸段落的口型视频没有完成，请重试故事板")
            for clip in montage.clips:
                cue = next(item for item in montage.cues if item.id == clip.cue_id)
                if cue.source_section_id == section_id:
                    if clip.source_end_seconds > (render.duration_seconds or 0) + 1 / 30:
                        raise ValueError("人物口型输出短于露脸段落，请重试或降低比例")
                    clip.person_render_id = render.id
                    clip.asset_id = f"person:{render.id}"
                    clip.source_path = f"person-render://{render.id}"
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
        return montage

    def include_adopted_sections(self,task,montage):
        from app.audio_timeline import AudioTimeline,narration_audio_timeline
        script = self.repository.effective_creation_script(task.workspace_id,task.id)
        audio = {item.source_section_id:item for item in self.repository.list_narration_segments(task.workspace_id,task.id,script.id)}
        cues,clips,cursor = [],[],0.0
        for section in script.sections:
            state = self.repository.stage_segment_state(task.workspace_id,task.id,section.id)
            if state is not None:
                if state['narration_reference'] != audio[section.id].audio_reference:
                    raise ValueError('已采用段画面与当前配音不一致，须先恢复该段依赖')
                local = creation_task_montage_plan_from_payload(state['montage'])
                section_cues,section_clips,duration = local.cues,local.clips,local.duration_seconds
                origin = 0.0
            else:
                section_cues = [cue for cue in montage.cues if cue.source_section_id==section.id]
                cue_ids = {cue.id for cue in section_cues}
                section_clips = [clip for clip in montage.clips if clip.cue_id in cue_ids]
                origin = min((clip.timeline_start_seconds for clip in section_clips),default=cursor)
                duration = float(audio[section.id].duration_seconds)
            if not section_cues or not section_clips:
                raise ValueError('完整故事板缺少实际段落镜头，未发布半条作品')
            cues.extend(section_cues)
            clips.extend(replace(clip,timeline_start_seconds=clip.timeline_start_seconds-origin+cursor,
                timeline_end_seconds=clip.timeline_end_seconds-origin+cursor) for clip in section_clips)
            cursor += duration
        timeline = montage.audio_timeline
        created_graph = montage.narration_linkage_created_audio_timeline
        if timeline is None or not any(track.role=='narration' for track in timeline.tracks):
            voice = narration_audio_timeline(audio[script.sections[0].id].audio_reference,
                float(audio[script.sections[0].id].duration_seconds)).tracks[0]
            voice.clips = []
            at = 0
            for section in script.sections:
                segment = audio[section.id]
                clip = narration_audio_timeline(segment.audio_reference,float(segment.duration_seconds)).tracks[0].clips[0]
                voice.clips.append(clip.model_copy(update={'id':f'audio-narration-{segment.id}','timeline_start_ms':at}))
                at += clip.duration_ms
            other_tracks = [track.model_copy(deep=True) for track in timeline.tracks] if timeline else []
            timeline,created_graph = AudioTimeline(tracks=[voice,*other_tracks]),True
        return replace(montage,cues=cues,clips=clips,duration_seconds=round(cursor,3),
            audio_timeline=timeline,narration_linkage_created_audio_timeline=created_graph,
            low_confidence_clip_count=sum(clip.needs_confirmation for clip in clips),
            status='waiting_for_user' if any(clip.needs_confirmation for clip in clips) else 'ready')

    def task_or_missing(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise MontageStageRejected(status_code=404, detail="creation task not found") from exc


    def plan_for_task_or_missing(
        self,
        workspace_id: str,
        task: CreationTask,
    ) -> CreationPlan:
        try:
            plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise MontageStageRejected(status_code=404, detail="creation task not found") from exc
        if plan.creation_task_id != task.id:
            raise MontageStageRejected(status_code=404, detail="creation task not found")
        return plan
