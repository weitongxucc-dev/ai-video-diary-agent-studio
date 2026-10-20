"""Canonical picture source qualification and the original montage renderer."""
from __future__ import annotations
from dataclasses import asdict, replace
import json
from pathlib import Path
import re
from app.audio_timeline import bounded_audio_context, render_audio_mix
from app.creation_execution import CreationExecutionActor, CpuRenderGrantContext, presenter_inputs, current_presenter_render
from app.creation.editing.metadata_use_case import effective_audio_timeline, InvalidMontage
from app.graphic_sources import graphic_source_facts
from app.linked_timeline import remove_time_ranges
from app.local_portrait import LocalPortrait
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.production import PersonRender, MediaAuthorityError, SubtitleTrack, SubtitleCue, content_readable, material_allows_footage
from app.task_audio import TASK_MEDIA_KIND, TaskMediaRequired, media_digest, audio_mix_resources
from app.canvas import canvas_size
from app.transitions import extend_dissolve_sources
from app.visual_effects import VisualEffects


class PicturePreviewRejected(Exception):
    """The original HTTP rejection, represented without importing transport."""
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class PicturePreviewRenderer:
    def __init__(self, repository, store, audio, metadata, montage_renderer, *, verify_identity,
                 presenter_model_ids, output_root, portrait_worker_path, temporary_root):
        self.repository, self.store, self.audio, self.metadata = repository, store, audio, metadata
        self.montage_renderer, self.verify_identity = montage_renderer, verify_identity
        self.presenter_model_ids = presenter_model_ids
        self.output_root = output_root
        self.portrait_worker_path, self.temporary_root = portrait_worker_path, temporary_root

    def portrait_processor(self, workspace_id):
        return LocalPortrait(self.portrait_worker_path, self.temporary_root / "portrait-cache" / workspace_id)

    def current_subtitle_tracks(self, workspace_id, task, *, current_version=None, montage=None):
        try:
            return self.metadata.current_subtitle_tracks(workspace_id, task, current_version=current_version, montage=montage)
        except InvalidMontage as exc:
            raise PicturePreviewRejected(status_code=422, detail=str(exc)) from exc

    def person_path(self, render: PersonRender) -> Path | None:
        if render.status != "ready":
            return None
        if render.artifact_ref:
            try:
                return self.store.resolve_file(render.artifact_ref)
            except (FileNotFoundError, ValueError):
                return None
        if not render.output_path:
            return None
        root = Path(self.output_root).expanduser().resolve()
        output = Path(render.output_path).expanduser().resolve()
        try:
            output.relative_to(root)
        except ValueError:
            return None
        if not output.is_file() or output.stat().st_size == 0:
            return None
        return output


    def validate_material_use(self, workspace_id, plan):
        # Validate before cache reuse as well as inside the actual renderer.
        for clip in plan.clips:
            if not clip.person_render_id:
                try:
                    asset = self.repository.get_asset(workspace_id, clip.asset_id)
                except KeyError as exc:
                    raise ValueError("镜头素材已不存在，请替换后重试") from exc
                if not material_allows_footage(asset):
                    raise ValueError("素材使用状态不允许用于画面，请替换镜头或在素材详情修改使用状态")
        audio_service = self.audio
        for track in effective_audio_timeline(plan).tracks:
            if not track.muted:
                for clip in track.clips:
                    if not clip.muted:
                        audio_service.validate_material_use(workspace_id, plan.task_id, clip.source_ref)


    def graphic_source_paths(self, workspace_id, task_id, overlays, context):
        references = dict.fromkeys(item["source_ref"] for item in overlays if item.get("kind") == "image")
        if not references:
            return {}
        if context is None:
            raise ValueError("图片渲染必须持有当前设备许可")
        context.check()
        actor = CreationExecutionActor.from_event(workspace_id,
            self.repository.get_task_event(workspace_id, f"event_{context.grant.owner_id}_start").payload)
        verdict = context.task_authorization_verdict(actor, task_id)
        if (self.verify_identity(actor) if verdict is None else verdict) != "ready":
            raise PermissionError("当前图片渲染成员许可不可核验")
        from PIL import Image
        paths = {}
        for reference in references:
            facts = graphic_source_facts(self.repository, self.store, workspace_id, task_id, reference, actor=actor)
            context.check()
            publication = self.store.describe(reference)
            if (publication.sha256, publication.size_bytes) != (facts["sha256"], facts["size_bytes"]):
                raise ValueError("登记PNG内容摘要已变化")
            path = Path(facts["path"])
            with Image.open(path) as image:
                if (image.format != "PNG" or getattr(image, "n_frames", 1) != 1
                        or image.size != (facts["width"], facts["height"])):
                    raise ValueError("实际PNG格式或尺寸与登记事实不一致")
                image.load()
            context.check()
            if graphic_source_facts(self.repository, self.store, workspace_id, task_id, reference, actor=actor) != facts:
                raise ValueError("PNG核验期间来源发生变化")
            paths[reference] = path
        return paths


    def verify_silence_audio_candidate(self, workspace_id, plan, context, resources, facts, actor):
        """Accept only the original silence request's actually measured derived candidate."""
        from app.task_audio import media_render_canonical
        scope = resources.get("silence_analysis_scope")
        if not isinstance(scope, dict):
            raise ValueError("画面预览音轨与本次封存候选输入不一致")
        task = self.repository.get_creation_task(workspace_id, plan.task_id)
        job = self.repository.get_job_run(workspace_id, context.grant.owner_id)
        start = self.repository.get_task_event(workspace_id, f"event_{job.id}_start")
        arguments = start.payload.get("arguments", {}).get("payload", {})
        if (job.kind != TASK_MEDIA_KIND or job.task_id != plan.task_id
                or CreationExecutionActor.from_event(workspace_id, start.payload) != actor
                or start.payload.get("operation") != "command_edit_preview"
                or arguments.get("selected_object_refs") != [f"clip:{scope['clip_id']}"]
                or not re.fullmatch(r"(?:剪掉|删掉|删除)(?:当前镜头(?:的)?)?(?:停顿|静音)[。！]?", arguments.get("raw_command", "").strip())
                or task.current_timeline_version_id != scope["base_version_id"]):
            raise PermissionError("动态静音候选没有本次原始指令或当前版本许可")
        inputs = [event for event in self.repository.list_task_events(workspace_id, plan.task_id)
            if event.job_id == job.id and event.event_type == "task_media_input"
            and event.payload.get("operation") == "picture_preview"
            and event.payload.get("parameters", {}).get("side") == "after"
            and event.payload.get("media_input", {}).get("plan", {}).get("audio_timeline") == plan.audio_timeline.model_dump()
            and event.payload["media_input"]["plan"].get("duration_seconds") == plan.duration_seconds]
        if len(inputs) != 1:
            raise ValueError("动态静音候选缺少唯一的本次实际画面输入")
        actual = inputs[0].payload
        analysis_ref = actual["parameters"].get("analysis_ref")
        record = next((item for item in self.repository.list_artifact_records(workspace_id, plan.task_id)
            if item.logical_ref == analysis_ref and item.kind == "audio_preview" and item.producer_job_id == job.id), None)
        if record is None:
            raise ValueError("静音候选缺少本次真实分析产物")
        service = self.audio
        transform = service._transform_for(workspace_id, plan.task_id, record)
        result = self.repository.get_task_event(workspace_id, f"event_{job.id}_{record.id}_silence_detected")
        cuts = actual["parameters"]["removed_ranges_ms"]
        if (transform.status != "succeeded" or transform.job_id != job.id
                or transform.parameters.get("action") != "silence_analysis"
                or transform.parameters.get("clip_id") != scope["clip_id"]
                or result.job_id != job.id or result.event_type != "silence_analysis_result"
                or result.payload != {"base_version_id":scope["base_version_id"], "clip_id":scope["clip_id"],
                    "analysis_ref":record.logical_ref, "analysis_sha256":record.sha256,
                    "transform_id":transform.id, "removed_ranges_ms":cuts}):
            raise ValueError("动态静音候选与已完成的实际分析记录不一致")
        publication = self.store.describe(record.logical_ref)
        if (publication.sha256, publication.size_bytes) != (record.sha256, record.size_bytes):
            raise ValueError("静音分析产物内容已变化")
        service.source(workspace_id, plan.task_id, record.logical_ref, actor=actor)
        before = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(workspace_id, plan.task_id))
        clip = next((item for item in before.clips if item.id == scope["clip_id"]), None)
        voice_ids = sorted(track.id for track in effective_audio_timeline(before).tracks
            if track.role in {"narration", "original"} and not track.muted and any(not item.muted for item in track.clips))
        voice_refs = sorted({item.source_ref for track in effective_audio_timeline(before).tracks
            if track.id in voice_ids for item in track.clips})
        analysis_input = self.repository.get_task_event(workspace_id, f"event_{job.id}_{transform.input_digest}_input")
        if (clip is None or transform.input_refs != voice_refs or transform.output_refs != [record.logical_ref]
                or analysis_input.job_id != job.id or analysis_input.event_type != "task_media_input"
                or analysis_input.payload.get("operation") != "audio_mix"
                or analysis_input.payload.get("parameters") != {"action":"silence_analysis", "clip_id":clip.id}
                or analysis_input.payload.get("media_input") != {"audio_timeline":effective_audio_timeline(before).model_dump(),
                    "duration_seconds":before.duration_seconds, "normalize_voice_sources":before.narration_linkage_created_audio_timeline,
                    "start_seconds":clip.timeline_start_seconds, "end_seconds":clip.timeline_end_seconds,
                    "solo_track_ids":voice_ids}):
            raise ValueError("静音分析不是本次实际镜头的固定原始音轨与区间")
        base = self.repository.get_timeline_version(workspace_id, scope["base_version_id"])
        tracks = self.current_subtitle_tracks(workspace_id, task, current_version=base, montage=before)
        expected, expected_tracks = remove_time_ranges(before, tracks, effective_audio_timeline(before), cuts)
        transient = {"status", "message", "output_path", "subtitle_path", "created_at", "updated_at"}
        expected_input = {"plan": {key: value for key, value in creation_task_montage_plan_to_payload(expected).items() if key not in transient},
            "subtitle_tracks": [{key: value for key, value in asdict(track).items() if key not in {"created_at", "updated_at"}} for track in expected_tracks]}
        actual_input = {"plan": {key: value for key, value in actual["media_input"]["plan"].items() if key not in transient},
            "subtitle_tracks": [{key: value for key, value in track.items() if key not in {"created_at", "updated_at"}} for track in actual["media_input"]["subtitle_tracks"]]}
        if media_render_canonical(expected_input) != media_render_canonical(actual_input):
            raise ValueError("静音画面输入不是原范围与本次实际切点的精确派生")
        if (len(expected.clips) > resources["dynamic_clip_count_limit"]
                or len(expected.graphic_overlays) > resources["dynamic_overlay_count_limit"]):
            raise ValueError("实际静音切分画面超出本次已标定成片上界")
        captured = {item["reference"]: item for item in resources["audio_mix_candidates"][0]["sources"]}
        if any(captured.get(item["reference"]) != item for item in facts["sources"]):
            raise ValueError("静音候选扩大或改变了本次固定音频来源")
        declared, derived = resources["audio_mix"], facts["resources"]
        if (derived["input_count"] > declared["dynamic_input_count_limit"]
                or derived["input_bytes"] > declared["dynamic_input_bytes_limit"]
                or derived["max_output_bytes"] > declared["max_output_bytes"]
                or derived["output_duration_limit_seconds"] > declared["output_duration_limit_seconds"]
                or derived["normalize_voice_sources"] != declared["normalize_voice_sources"]):
            raise ValueError("实际静音切分音轨超出本次已标定音频上界")
        context.check()


    def audio_mix_sources(self, workspace_id, plan, context):
        context.check()
        actor = CreationExecutionActor.from_event(workspace_id,
            self.repository.get_task_event(workspace_id, f"event_{context.grant.owner_id}_start").payload)
        verdict = context.task_authorization_verdict(actor, plan.task_id)
        if (self.verify_identity(actor) if verdict is None else verdict) != "ready":
            raise PermissionError("当前音轨渲染成员许可不可核验")
        with context.scheduler._connect() as conn:
            row = conn.execute("SELECT resources_json FROM device_work_requests WHERE id=?",
                (context.grant.request_id,)).fetchone()
        if row is None:
            raise PermissionError("当前成片音轨许可不可读取")
        resources = json.loads(row["resources_json"])
        declared = resources.get("audio_mix")
        if not isinstance(declared, dict):
            raise PermissionError("当前成片未封存音轨混音用途")
        facts = self.audio.mix_facts(workspace_id, plan.task_id, plan.audio_timeline, plan.duration_seconds,
            actor=actor, normalize_voice_sources=plan.narration_linkage_created_audio_timeline,
            budget=context.scheduler.resources.budget.cpu_audio)
        if "audio_mix_candidates" in resources:
            key = media_digest([plan.audio_timeline.model_dump(), plan.duration_seconds,
                plan.narration_linkage_created_audio_timeline])
            candidate = next((item for item in resources["audio_mix_candidates"] if item["key"] == key), None)
            if candidate is None:
                self.verify_silence_audio_candidate(workspace_id, plan, context, resources, facts, actor)
            elif {name: value for name, value in candidate.items() if name != "key"} != facts:
                raise ValueError("画面预览音轨与本次封存候选输入不一致")
        elif facts["resources"] != declared:
            raise ValueError("音轨输入、摘要或资源与本次成片许可不一致")
        context.check()
        return facts, {item["reference"]: Path(item["path"]) for item in facts["sources"]}


    def render_montage(self, 
        workspace_id: str,
        plan,
        narration_audio_path: Path,
        *,
        job_staging_path: Path | None = None,
        subtitle_tracks: list[SubtitleTrack] | None = None,
        render_context: CpuRenderGrantContext | None = None,
    ):
        if render_context is not None and job_staging_path is None:
            raise ValueError("受许可成片必须写入任务暂存目录")
        original_references = [clip.source_path for clip in plan.clips]
        original_ranges = [
            (clip.source_start_seconds, clip.source_end_seconds) for clip in plan.clips
        ]
        original_narration_reference = plan.narration_audio_path
        original_graphics = getattr(plan,'graphic_overlays',None)
        cues_by_id = {cue.id: cue for cue in plan.cues}
        try:
            if original_graphics is not None:
                graphic_paths = self.graphic_source_paths(workspace_id, plan.task_id, original_graphics, render_context)
                plan.graphic_overlays=[{**item,'source_ref':str(graphic_paths[item['source_ref']])}
                    if item.get('kind')=='image' else item for item in original_graphics]
            for clip in plan.clips:
                if clip.person_render_id:
                    render = self.repository.get_person_render(
                        workspace_id, plan.task_id, clip.person_render_id
                    )
                    output = self.person_path(render)
                    cue = cues_by_id.get(clip.cue_id)
                    if (
                        output is None
                        or cue is None
                        or render.narration_segment_id != cue.narration_segment_id
                    ):
                        raise MediaAuthorityError(
                            reason="person_render_unavailable",
                            impact="人物口型片段未被传给渲染器，当前成片没有改变。",
                            repair_action="重新生成所选人物口型片段后再渲染。",
                        )
                    clip.source_path = str(output)
                    if clip.source_end_seconds > (render.duration_seconds or 0.0) + .001:
                        raise ValueError("人物口型源片段不足以覆盖当前镜头")
                    continue
                resolved_source_path = self.repository.resolve_clip_media_reference(
                    workspace_id,
                    clip.asset_id,
                    clip.source_path,
                )
                if not resolved_source_path.is_file() or resolved_source_path.stat().st_size == 0:
                    raise MediaAuthorityError(
                        reason="clip_media_unavailable",
                        impact="缺失或为空的素材未被传给渲染器。",
                        repair_action="重新扫描当前工作区素材并更新剪辑计划。",
                    )
                clip.source_path = str(resolved_source_path)
            extended,_transitions=extend_dissolve_sources(plan.clips)
            for clip,handle_clip in zip(plan.clips,extended,strict=True):
                effects=VisualEffects.model_validate(clip.visual_effects)
                if effects.beauty_strength or effects.skin_exposure:
                    if render_context is not None:
                        raise ValueError("本机人像效果尚无受许可执行路径，请先移除该效果")
                    processed,metadata=self.portrait_processor(workspace_id).process(Path(clip.source_path),clip.source_start_seconds,
                        handle_clip.source_end_seconds,effects.beauty_strength,effects.skin_exposure)
                    clip.source_path=str(processed)
                    original_duration=clip.source_end_seconds-clip.source_start_seconds
                    clip.source_start_seconds=0
                    clip.source_end_seconds=original_duration if handle_clip is not clip else metadata["duration_seconds"]
            if job_staging_path is None:
                return self.montage_renderer.render(plan, narration_audio_path)
            if isinstance(plan, CreationTaskMontagePlan) and plan.audio_timeline is not None:
                mixed_audio = Path(job_staging_path) / "timeline-mix.wav"
                if render_context is None:
                    raise PermissionError("成片音轨处理必须持有当前设备许可")
                with bounded_audio_context(render_context) as audio_context:
                    audio_facts, source_paths = self.audio_mix_sources(workspace_id, plan, audio_context)
                    audio_resources = audio_facts["resources"]
                    audio_options = {"scheduler": audio_context.scheduler, "grant": audio_context.grant,
                        "cancel_event": audio_context.cancel_event,
                        "deadline_monotonic": audio_context.deadline_monotonic,
                        "max_output_bytes": audio_resources["max_output_bytes"],
                        "threads": audio_resources["threads"]}
                    render_audio_mix(plan.audio_timeline, plan.duration_seconds,
                        lambda reference: source_paths[reference], mixed_audio,
                        normalize_voice_sources=audio_resources["normalize_voice_sources"], **audio_options)
                    audio_context.check()
                    if self.audio_mix_sources(workspace_id, plan, audio_context)[0] != audio_facts:
                        raise ValueError("混音期间音频来源或许可发生变化")
                narration_audio_path = mixed_audio
            render_options = (
                {"scheduler": render_context.scheduler, "grant": render_context.grant,
                 "cancel_event": render_context.cancel_event,
                 "deadline_monotonic": render_context.deadline_monotonic,
                 "max_output_bytes": render_context.max_output_bytes,
                 "threads": render_context.threads}
                if render_context is not None else {}
            )
            return self.montage_renderer.render(
                plan, narration_audio_path, job_staging_path=job_staging_path,
                subtitle_tracks=subtitle_tracks, **render_options,
            )
        finally:
            for clip, reference, source_range in zip(
                plan.clips, original_references, original_ranges, strict=True
            ):
                clip.source_path = reference
                clip.source_start_seconds, clip.source_end_seconds = source_range
            plan.narration_audio_path = original_narration_reference
            if original_graphics is not None:
                plan.graphic_overlays=original_graphics


    def render_picture_input(self, workspace_id, task_id, media_input, output, context):
        plan = creation_task_montage_plan_from_payload(media_input["plan"])
        if plan.workspace_id != workspace_id or plan.task_id != task_id:
            raise PermissionError("画面预览输入不属于当前许可任务")
        tracks = [SubtitleTrack(**{**item, "cues": [SubtitleCue(**cue) for cue in item["cues"]]})
                  for item in media_input["subtitle_tracks"]]
        self.render_montage(workspace_id, plan, output.parent / "timeline-mix.wav",
            job_staging_path=output.parent, subtitle_tracks=tracks, render_context=context)
        (output.parent / "preview.mp4").replace(output)


    def qualified_picture_sources(self, workspace_id, task, plan, picture_candidates, actor):
        task_id = task.id
        files, graphics = [], []
        def remember(reference, path):
            stat = path.stat()
            fact = [reference, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            if fact not in files:
                files.append(fact)
        self.validate_material_use(workspace_id, plan)
        person_ids = set()
        if any((clip.person_render_id for clip in plan.clips)):
            from app.creation_execution import presenter_inputs, current_presenter_render
            creation_plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
            script = self.repository.get_creation_task_script(workspace_id, task.confirmed_script_id)
            for segment, person_request in presenter_inputs(self.repository, task, script, creation_plan, self.presenter_model_ids()):
                current = current_presenter_render(self.repository, task, segment, person_request, self.person_path)
                if current is not None:
                    person_ids.add(current.id)
        for clip in plan.clips:
            if clip.person_render_id:
                if clip.person_render_id not in person_ids:
                    raise PermissionError('当前人物/声音授权或片段版本已失效')
            else:
                asset = self.repository.get_asset(workspace_id, clip.asset_id)
                if not content_readable(user_id=actor.user_id or actor.member_id, local_diagnostic=False, visibility=asset.visibility, owner_user_id=asset.owner_user_id):
                    raise PermissionError('当前成员不能读取镜头素材')
            path = self.person_path(self.repository.get_person_render(workspace_id, task_id, clip.person_render_id)) if clip.person_render_id else self.repository.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
            if path is None:
                raise ValueError('当前人物片段没有有效媒体')
            remember(clip.source_path, path)
        for reference in dict.fromkeys((item['source_ref'] for candidate in picture_candidates for item in candidate.graphic_overlays if item.get('kind') == 'image')):
            item = graphic_source_facts(self.repository, self.store, workspace_id, task_id, reference, actor=actor)
            graphics.append(item)
            remember(reference, Path(item['path']))
        return files, graphics



    def publish_picture(self, workspace_id,task,base,candidate,tracks,*,kind,parameters,execution):
        from copy import deepcopy
        self.validate_material_use(workspace_id,candidate)
        preview_plan=deepcopy(candidate)
        preview_plan.status="ready"
        preview_plan.audio_timeline=effective_audio_timeline(candidate)
        input_refs = list(dict.fromkeys([f"timeline:{base.id}", *[clip.source_path for clip in candidate.clips],
            *sorted({item.source_ref for track in preview_plan.audio_timeline.tracks for item in track.clips}),
            *[item['source_ref'] for item in candidate.graphic_overlays if item.get('kind')=='image'],
            *[track.artifact_ref for track in tracks if track.artifact_ref]]))
        media_input = {"plan": creation_task_montage_plan_to_payload(preview_plan), "subtitle_tracks": [asdict(track) for track in tracks]}
        if execution is None:
            raise TaskMediaRequired("picture_preview")
        return self.audio.publish(workspace_id,task.id,kind=kind,suffix=".mp4",engine="local-montage-v20",
            parameters={"base_version_id":base.id,**parameters}, input_refs=input_refs,
            operation="picture_preview", media_input=media_input, execution=execution)

    def picture_resources(self, plan, payload, picture_candidates, facts, budget):
        width, height = canvas_size(plan.aspect_ratio)
        changed_width, changed_height = canvas_size(payload.get('aspect_ratio') or plan.aspect_ratio)
        width, height = max(width, changed_width), max(height, changed_height)
        resources = {'kind': 'creation_presenter_render' if any(clip.person_render_id for clip in plan.clips) else 'creation_render',
            'execution_mode': 'cpu_ffmpeg', 'duration_seconds': plan.duration_seconds, 'width_px': width, 'height_px': height,
            'input_bytes': sum(item[1] for item in facts['files']), 'clip_count': len(plan.clips),
            'overlay_count': max(len(candidate.graphic_overlays) for candidate in picture_candidates),
            'threads': budget.max_threads, 'timeout_seconds': budget.max_timeout_seconds, 'max_output_bytes': budget.max_output_bytes}
        return resources

    def seal_audio_resources(self, task, picture_candidates, facts, resources, audio_budget, *, actor):
        candidates = []
        for candidate in picture_candidates:
            graph = effective_audio_timeline(candidate)
            mix_facts = self.audio.mix_facts(task.workspace_id, task.id, graph, candidate.duration_seconds, actor=actor,
                normalize_voice_sources=candidate.narration_linkage_created_audio_timeline, budget=audio_budget)
            candidates.append({'key': media_digest([graph.model_dump(), candidate.duration_seconds, candidate.narration_linkage_created_audio_timeline]), **mix_facts})
        mix_inputs = {}
        for candidate in candidates:
            for source in candidate['resources']['inputs']:
                previous = mix_inputs.get(source['reference'])
                if previous is None or previous['uses'] < source['uses']:
                    mix_inputs[source['reference']] = source
        resources['audio_mix'] = audio_mix_resources(list(mix_inputs.values()), [effective_audio_timeline(candidate) for candidate in picture_candidates],
            max(candidate.duration_seconds for candidate in picture_candidates),
            normalize_voice_sources=any(candidate.narration_linkage_created_audio_timeline for candidate in picture_candidates), budget=audio_budget)
        resources['audio_mix_candidates'] = candidates
        facts['audio_mix_candidates'] = candidates
        return facts, resources
