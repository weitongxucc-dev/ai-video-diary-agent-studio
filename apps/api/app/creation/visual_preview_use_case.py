"""Visual preview preparation, original full-tail comparison and explicit execution."""
from app.creation.plan_contracts import creation_plan_payload
from dataclasses import asdict, replace
from collections import Counter
import subprocess
from uuid import uuid4
from app.creation.contracts import EditIntent, EditProposal
from app.creation.editing.clips import ClipPreviewCommand, ClipPreviewConflict, prepare_clip_edit
from app.creation.editing.metadata_use_case import effective_audio_timeline, logical_montage_output_refs
from app.linked_timeline import remove_time_ranges
from app.media.picture_preview import PicturePreviewRejected
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.schemas import CreationTaskEditPreviewResponse
from app.task_audio import media_digest
from app.visual_effects import VisualEditRequest, with_visual_effects


class VisualPreviewUseCase:
    def __init__(self, repository, store, audio, metadata, renderer):
        self.repository, self.store, self.audio = repository, store, audio
        self.metadata, self.renderer = metadata, renderer

    def project(self, plan, clip_ids, patch, tracks=()):
        """Project one representative shot identically at sealing and execution."""
        from copy import deepcopy
        selected = [clip for clip in plan.clips if clip.id in clip_ids]
        if len(selected) != len(set(clip_ids)) or not selected:
            raise ValueError("所选镜头不存在")
        if any(clip.locked for clip in selected):
            raise ValueError("所选范围包含锁定镜头，请先解锁或缩小范围")
        before = selected[0]
        after = with_visual_effects(before, patch)
        if (before.timeline_end_seconds - before.timeline_start_seconds <= 5 or
                not (before.visual_effects.get("fade_out_seconds") or after.visual_effects.get("fade_out_seconds"))):
            return before, after, None
        start, end = round(before.timeline_start_seconds * 1000), round(before.timeline_end_seconds * 1000)
        duration = round(plan.duration_seconds * 1000)
        removed = [(a, b) for a, b in ((0, start), (end, duration)) if a < b]
        def project(clip):
            readonly = deepcopy(plan)
            # This comparison covers the shot itself, not its neighboring dissolve.
            readonly.clips = [replace(deepcopy(clip), locked=False, transition_in_seconds=0)]
            readonly.status = "ready"
            audio = effective_audio_timeline(plan).model_copy(deep=True)
            for track in audio.tracks:
                track.locked = False
                for item in track.clips:
                    item.locked = False
            if removed:
                # One retained interval keeps all original IDs: no split/copy UUIDs.
                return remove_time_ranges(readonly, tracks, audio, removed)
            readonly.audio_timeline = audio
            return readonly, deepcopy(tracks)
        return before, after, [project(before), project(after)]


    def prepare(self, task, payload):
        workspace_id, task_id = task.workspace_id, task.id
        if task.current_timeline_version_id != payload.base_version_id:
            raise PicturePreviewRejected(status_code=409,detail="时间线已经变化，请刷新后重新预览")
        plan=creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id,task_id))
        patch = payload.effects.model_dump(exclude_unset=True)
        try:
            clips, candidates = prepare_clip_edit(plan, ClipPreviewCommand(task_id, payload.base_version_id,
                "AdjustVisualEffects", payload.clip_ids, patch, payload.raw_command))
        except KeyError as exc:
            raise PicturePreviewRejected(status_code=404, detail=str(exc)) from exc
        except ClipPreviewConflict as exc:
            raise PicturePreviewRejected(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise PicturePreviewRejected(status_code=422, detail=str(exc)) from exc
        changed_clips = {clip.id: clip for clip in candidates}
        return plan, patch, clips, changed_clips

    def preview_full(self, task, payload, prepared, *, draft_only, source, execution):
        workspace_id, task_id = task.workspace_id, task.id
        plan, patch, clips, changed_clips = prepared
        clip = clips[0]
        preview_limit = None
        def resolve_target(target):
            preview_target=replace(target)
            if target.person_render_id:
                from app.creation.person_stage_use_case import adopted_person_render_source
                cue=next((cue for cue in plan.cues if cue.id==target.cue_id),None)
                source=adopted_person_render_source(self.repository,task,target,cue,self.renderer.person_path)
            else:
                source=self.repository.resolve_clip_media_reference(workspace_id,target.asset_id,target.source_path)
            return preview_target,source
        try:
            preview_clip,_source=resolve_target(clip)
            records=[]
            if not draft_only:
                if any(item.visual_effects["beauty_strength"] or item.visual_effects["skin_exposure"] for item in changed_clips.values()):
                    raise ValueError("本机人像效果尚无受许可执行路径，请先移除该效果")
                base = self.metadata.ensure_montage_base(workspace_id, task, plan, source=source)
                tracks = self.renderer.current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
                before, after, projected = self.project(plan, payload.clip_ids, patch, tracks)
                records = [self.publish_picture(workspace_id, task, base, candidate, scoped_tracks,
                    kind="color_preview", parameters={"effects": patch, "side": side,
                        "representative_clip_id": before.id, "scope": "independent_clip"}, execution=execution)
                    for side, (candidate, scoped_tracks) in zip(("before", "after"), projected, strict=True)]
        except PermissionError as exc:
            raise PicturePreviewRejected(status_code=403,detail=str(exc)) from exc
        except (ValueError,OSError,subprocess.SubprocessError,StopIteration) as exc:
            raise PicturePreviewRejected(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else "本地画面预览失败，请检查所选源素材和 FFmpeg") from exc
        if self.repository.task_records.get_creation_task(workspace_id,task_id).current_timeline_version_id != payload.base_version_id:
            raise PicturePreviewRejected(status_code=409,detail="预览期间时间线发生变化，请重新预览")
        base=self.metadata.ensure_montage_base(workspace_id,task,plan,source=source)
        refs=[f"clip:{clip.id}" for clip in clips]
        intent=self.repository.edit_records.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
            raw_command=payload.raw_command,context_stage="montage",selected_object_refs=refs,playhead_ms=round(clip.timeline_start_seconds*1000),intent_type="visual_adjustment",
            scope={"kind":"clips","clip_ids":[clip.id for clip in clips]},constraints={"preserve_timing":True,"preserve_audio":True,"preserve_face_geometry":True},confidence=1,status="interpreted"), source=source)
        labels = {"exposure": "曝光", "contrast": "对比度", "saturation": "饱和度", "temperature": "冷暖",
            "beauty_strength": "磨皮", "skin_exposure": "肤色曝光", "zoom": "缩放", "rotation": "旋转",
            "offset_x": "水平位置", "offset_y": "垂直位置", "fade_in_seconds": "黑场淡入秒", "fade_out_seconds": "黑场淡出秒"}
        detail = "只调整 " + " · ".join(f"{labels[key]} {value:g}" for key, value in patch.items())
        proposal=self.repository.edit_records.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
            intent_id=intent.id,summary=f"调整 {len(clips)} 个镜头的画面效果",target_refs=refs,
            operation={"operation_type":"AdjustVisualEffects","payload":{"base_version_id":base.id,
                "effects":patch,"expected_clips":{clip.id:asdict(clip) for clip in clips}}},
            invalidated_refs=logical_montage_output_refs(plan),external_effects=[],
            preview={**{side: {"ref": f"timeline:{base.id}", "label": label, "detail": "草稿参数已计算；应用时保存新版本，不自动生成媒体。"} for side, label in (("before", "当前草稿"), ("after", "修改后草稿"))}, **{side:{"ref":record.logical_ref,"label":"当前画面" if side=="before" else detail,
                "detail":f"仅预览代表镜头 {plan.clips.index(clip)+1} 的{'完整区间（包含结尾淡出及该区间音轨、字幕和图层）' if preview_limit is None else '开头（最多 5 秒，静音画面，不含字幕和图层）'}；未覆盖相邻转场。确认将应用到 {len(clips)} 个指定镜头，不改变声音和时长。",
                "video_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"}
                for side,record in zip(("before","after"),records)}}))
        return intent, proposal


    def publish_picture(self, workspace_id,task,base,candidate,tracks,*,kind,parameters,execution):
        return self.renderer.publish_picture(workspace_id,task,base,candidate,tracks,
            kind=kind,parameters=parameters,execution=execution)

    def prepare_source(self, workspace_id, task_id, arguments, actor):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        if arguments.get('_media_kind') == 'clip_preview':
            raise ValueError('历史短预览没有独立的封存执行输入，请查询原记录并为当前版本重新预览')
        source = arguments.get('_typed_source')
        if source is not None:
            version = self.repository.timeline_records.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
            if (source['workspace_id'], source['task_id'], source['member_id'], source['expected_timeline_version_id'], source['expected_timeline_sequence']) != (workspace_id, task_id, actor.user_id or actor.member_id, task.current_timeline_version_id, version.sequence if version else 0):
                raise ValueError('封存的类型化编辑输入或成员已失效')
        plan_payload = self.repository.get_creation_task_montage_plan(workspace_id, task_id) if task.montage_plan_id else None
        plan = creation_task_montage_plan_from_payload(plan_payload) if plan_payload else None
        transient = {'status', 'message', 'output_path', 'subtitle_path', 'updated_at'}
        snapshot = {key: value for key, value in creation_task_montage_plan_to_payload(plan).items() if key not in transient} if plan else None
        payload = arguments.get('payload', {})
        expected = payload.get('base_version_id')
        if 'base_version_id' in payload and expected != task.current_timeline_version_id:
            raise ValueError('时间线已变化，请重新安排当前要求')
        picture_candidates = [plan] if plan is not None else []
        visual_scope = None
        if plan is not None and arguments.get('_media_kind') == 'picture_preview':
            tracks = self.renderer.current_subtitle_tracks(workspace_id, task, montage=plan)
            representative, _after, projected = self.project(plan, payload['clip_ids'], payload['effects'], tracks)
            visual_scope = {'representative_clip_id': representative.id, 'selected_clip_ids': [item.id for item in plan.clips if item.id in payload['clip_ids']], 'start_seconds': representative.timeline_start_seconds, 'end_seconds': representative.timeline_end_seconds, 'scope': 'independent_clip', 'adjacent_transitions_included': False}
            if projected is None:
                raise ValueError('画面预览范围与封存的媒体用途不一致')
            plan = projected[0][0]
            picture_candidates = [candidate for candidate, _tracks in projected]
            visual_scope['subtitle_tracks'] = [{key: value for key, value in asdict(track).items() if key not in {'created_at', 'updated_at'}} for track in projected[0][1]]
        files, graphics, audio_inputs = [], [], []
        if plan is not None:
            files, graphics = self.renderer.qualified_picture_sources(workspace_id, task, plan, picture_candidates, actor)
            graph = effective_audio_timeline(plan)
            solo = set(payload.get('solo_track_ids', []))
            clips = [clip for track in graph.tracks if not track.muted and (not solo or track.id in solo) for clip in track.clips if not clip.muted]
            uses = Counter(clip.source_ref for clip in clips)
            for reference in sorted({clip.source_ref for clip in clips}):
                path = self.audio.source(workspace_id, task_id, reference, actor=actor)
                stat = path.stat()
                fact = [reference, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
                if fact not in files:
                    files.append(fact)
                audio_inputs.append({'reference': reference, 'path': str(path.resolve()), 'size_bytes': stat.st_size,
                    'uses': uses.get(reference, 1), **self.audio.source_metadata(workspace_id, task_id, reference)})
        facts = {'base_version_id': task.current_timeline_version_id, 'script_id': task.confirmed_script_id,
            'script_digest': self.repository.script_records.confirmed_creation_task_script_digest(workspace_id, task_id, task.confirmed_script_id) if task.confirmed_script_id else None,
            'plan_id': task.plan_id, 'plan_digest': media_digest(creation_plan_payload(self.repository.plan_records.get_creation_plan(workspace_id, task.plan_id))),
            'montage': snapshot, 'files': files, 'narration_cache_key': task.narration_cache_key}
        if visual_scope is not None:
            facts['visual_preview'] = visual_scope
        if graphics:
            facts['graphic_sources'] = graphics
        return task, plan, payload, picture_candidates, facts, audio_inputs

    @staticmethod
    def require_picture_plan(prepared):
        if prepared[1] is None:
            raise ValueError('当前任务没有可预览的故事板')

    @staticmethod
    def uses_presenter(prepared):
        return any(clip.person_render_id for clip in prepared[1].clips)

    @staticmethod
    def require_render_budget(budget):
        if budget is None:
            raise ValueError('CPU 画面预览预算尚未标定，未启动媒体')
        return budget

    @staticmethod
    def require_presenter_budget(budget):
        if budget is None:
            raise ValueError('人物组合 CPU 预览预算尚未标定，未启动媒体')
        return budget

    def seal_resources(self, prepared, budget, audio_budget, *, actor):
        task, plan, payload, picture_candidates, facts, audio_inputs = prepared
        resources = self.renderer.picture_resources(plan, payload, picture_candidates, facts, budget)
        facts, resources = self.renderer.seal_audio_resources(task, picture_candidates, facts, resources, audio_budget, actor=actor)
        if '校准' in payload.get('raw_command', ''):
            raise ValueError('本地字幕识别尚无独立标定的模型资源许可，未启动识别')
        return facts, resources

    def execute(self, job, event, actor, context):
        try:
            task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id)
        except KeyError as exc:
            raise PicturePreviewRejected(status_code=404, detail='制作任务不存在') from exc
        payload = VisualEditRequest.model_validate(event['arguments']['payload'])
        source = event['arguments'].get('_typed_source')
        if source is not None:
            source = {**source, 'media_job_id': job.id, 'actor_identity': actor.identity_payload()}
        prepared = self.prepare(task, payload)
        intent, proposal = self.preview_full(task, payload, prepared, draft_only=event['arguments'].get('_draft', False),
            source=source, execution=(context, job, event, actor))
        return CreationTaskEditPreviewResponse.model_validate({'intent': asdict(intent), 'proposal': asdict(proposal)}).model_dump(mode='json')

    def validate_result(self, job, event, result, context):
        return self.audio.validate_publications(job, event, result, context)

