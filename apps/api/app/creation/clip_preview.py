"""Prepare and seal picture-only edits without executing media or changing drafts."""
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from app.creation.editing.clips import ClipPreviewCommand, ClipPreviewConflict, prepare_clip_edit, needs_full_visual_preview
from app.canvas import preview_size
from app.models import utc_now
from app.montage import creation_task_montage_plan_from_payload
from app.source_policy import knowledge_item_readable as content_readable
from app.production import material_allows_footage, material_segment_eligible, effective_segment_usage_role
from app.creation.contracts import (EditIntent, EditProposal, EditCommand)
from app.visual_effects import VisualEffects, with_visual_effects, fade_filters, clip_playback_rate


class ClipPreviewUnavailable(ValueError):
    """A required measured execution profile is absent."""




class ClipPreviewUseCase:
    def __init__(self, repository, *, resolve_presenter_source, budget):
        self.repository = repository
        self.resolve_presenter_source = resolve_presenter_source
        self.budget = budget

    def prepare(self, actor, command):
        task = self.repository.get_creation_task(actor.workspace_id, command.task_id)
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError("当前成员不能修改此任务")
        if task.current_timeline_version_id != command.base_version_id:
            raise ClipPreviewConflict("时间线已经变化，请刷新后重新预览")
        version = self.repository.get_timeline_version(actor.workspace_id, command.base_version_id)
        if version.task_id != task.id:
            raise ClipPreviewConflict("基础时间线不属于当前任务")
        if command.source is not None and (command.source["workspace_id"], command.source["task_id"],
                command.source["member_id"], command.source["expected_timeline_version_id"],
                command.source["expected_timeline_sequence"]) != (actor.workspace_id, task.id,
                actor.user_id or actor.member_id, version.id, version.sequence):
            raise ClipPreviewConflict("封存的类型化编辑输入或成员已失效")
        if command.source is not None and command.source.get("actor_identity") != actor.identity_payload():
            raise PermissionError("封存的编辑操作者身份已失效")
        plan = creation_task_montage_plan_from_payload(
            self.repository.get_creation_task_montage_plan(actor.workspace_id, task.id))
        before, after = prepare_clip_edit(plan, command)
        for clip, candidate in zip(before, after, strict=True):
            _source, asset = self.authorized_source(actor, task, clip)
            if asset is not None:
                segment = next((item for item in asset.segments if item.id == clip.segment_id), None)
                if command.operation_type == "ChangeClipSpeed":
                    if segment is None or not material_segment_eligible(asset, segment, actor.workspace_id):
                        raise ClipPreviewConflict("素材片段当前不可用，请检查来源、范围和使用限制")
                    if abs(command.values["playback_rate"] - 1) > .001 and effective_segment_usage_role(asset, segment) == "a_roll":
                        raise ClipPreviewConflict("人物口型镜头不能只改变画面速度；请调整配音后重新对口型。")
                    if candidate.source_start_seconds < segment.start_seconds - 1e-6 or candidate.source_end_seconds > segment.end_seconds + 1e-6:
                        raise ValueError("变速后的源片段超出当前可用素材区间")
            if command.operation_type == "ChangeClipSpeed" and clip.person_render_id and abs(command.values["playback_rate"] - 1) > .001:
                raise ClipPreviewConflict("人物口型镜头不能只改变画面速度；请调整配音后重新对口型。")
        return task, version, plan, before, after

    def authorized_source(self, actor, task, clip):
        if clip.person_render_id:
            return self.resolve_presenter_source(task, clip), None
        asset = self.repository.get_asset(actor.workspace_id, clip.asset_id)
        if not material_allows_footage(asset) or not content_readable(user_id=actor.user_id or actor.member_id,
                local_diagnostic=False, visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("当前成员或素材使用状态不允许预览此镜头")
        return self.repository.resolve_clip_media_reference(actor.workspace_id, clip.asset_id, clip.source_path), asset

    def legacy_result_current(self, job, event, actor):
        """Read-only retirement boundary. An old declaration never authorizes new execution."""
        if job.status != "succeeded":
            return False
        task = self.repository.get_creation_task(job.workspace_id, job.task_id)
        facts = event["input_facts"]
        if task.current_timeline_version_id != facts["base_version_id"]:
            return False
        plan = creation_task_montage_plan_from_payload(
            self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id))
        clip = next((item for item in plan.clips if item.id == facts["clip"]["id"]), None)
        if clip is None or asdict(clip) != facts["clip"] or plan.aspect_ratio != facts["aspect_ratio"]:
            return False
        source, _asset = self.authorized_source(actor, task, clip)
        if source is None:
            return False
        stat = Path(source).stat()
        return [clip.source_path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns] == facts["source"]

    def seal(self, actor, command):
        task, version, plan, before, after = self.prepare(actor, command)
        if command.operation_type == "AdjustVisualEffects" and needs_full_visual_preview(before[0], after[0]):
            raise ValueError("结尾淡出需要完整镜头预览，不能封存为开头静音窗口")
        if any(VisualEffects.model_validate(clip.visual_effects).beauty_strength or
               VisualEffects.model_validate(clip.visual_effects).skin_exposure for clip in after):
            raise ValueError("本机人像效果尚无受许可执行路径，请先移除该效果")
        clip = before[0]
        source, _asset = self.authorized_source(actor, task, clip)
        budget = self.budget()
        if source is None:
            raise ValueError("当前镜头媒体不可用")
        if budget is None:
            raise ClipPreviewUnavailable("CPU 短镜头预览预算尚未标定，未接收媒体作业")
        source = Path(source).resolve()
        stat = source.stat()
        width, height = preview_size(plan.aspect_ratio)
        duration = min(5., clip.timeline_end_seconds - clip.timeline_start_seconds)
        facts = {"base_version_id":version.id, "timeline_sequence":version.sequence,
            "clip":asdict(clip), "aspect_ratio":plan.aspect_ratio,
            "source":[clip.source_path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
            "source_path":str(source), "source_inode":stat.st_ino, "source_device":stat.st_dev,
            "selected_clips":[asdict(item) for item in before], "candidates":[asdict(item) for item in after]}
        if command.operation_type == "AdjustVisualEffects":
            facts["visual_preview"] = {"representative_clip_id":clip.id,
                "selected_clip_ids":[item.id for item in before], "start_seconds":clip.timeline_start_seconds,
                "end_seconds":clip.timeline_end_seconds, "scope":"independent_clip", "adjacent_transitions_included":False}
        resources = {"kind":"task_clip_preview", "execution_mode":"cpu_ffmpeg", "duration_seconds":duration,
            "width_px":width, "height_px":height, "input_bytes":stat.st_size, "clip_count":1, "overlay_count":0,
            "max_output_bytes":budget.max_output_bytes, "threads":budget.max_threads, "timeout_seconds":budget.max_timeout_seconds,
            "clip_preview":True, "silent":True, "preview_window_limit_seconds":5., "output_count":2}
        return facts, resources

    def proposal(self, actor, command, *, records=(), job=None):
        task, base, plan, before, after = self.prepare(actor, command)
        refs = [f"clip:{clip.id}" for clip in before]
        source = command.source
        if source is not None and job is not None:
            source = {**source, "media_job_id":job.id, "actor_identity":actor.identity_payload()}
        from copy import deepcopy
        from app.creation.editing.sources import EditSourceVerifier
        candidate = deepcopy(plan)
        changed = {clip.id:clip for clip in after}
        candidate.clips = [changed.get(clip.id, clip) for clip in candidate.clips]
        action = ({'clip_ids':command.clip_ids, 'effects':command.values} if command.operation_type == 'AdjustVisualEffects'
            else {'clip_id':command.clip_ids[0], **command.values})
        metadata = EditCommand(actor.workspace_id, command.task_id,
            {'member_id':actor.user_id or actor.member_id, 'local_diagnostic':False}, command.base_version_id,
            command.operation_type, action, source)
        verifier = EditSourceVerifier(self.repository, identity=metadata.actor_identity)
        metadata = verifier.bind_affected(metadata, plan, candidate)
        source_scope = verifier.scope_payload(metadata)
        now = job.created_at if job is not None else utc_now()
        suffix = job.id if job is not None else uuid4().hex
        intent = EditIntent(id=f"intent_clip_{suffix}", workspace_id=actor.workspace_id, task_id=task.id,
            raw_command=(source.get("raw_command") if source else None) or command.raw_command,
            context_stage="montage", selected_object_refs=refs, playhead_ms=round(before[0].timeline_start_seconds * 1000),
            intent_type="visual_adjustment" if command.operation_type == "AdjustVisualEffects" else "exact_montage_edit",
            scope={"kind":"clips", "clip_ids":command.clip_ids},
            constraints={"preserve_timing":True, "preserve_audio":True, "affected_source_scope":source_scope,
                **({"request_source":source} if source else {})},
            confidence=1, status="interpreted", created_at=now)
        labels = {"AdjustVisualEffects":f"调整 {len(before)} 个镜头的画面效果", "ChangeCrop":"调整画面适配", "ChangeClipSpeed":"改变画面速度，保留成片时长、声音和字幕"}
        if command.operation_type == "AdjustVisualEffects":
            values = {"base_version_id":base.id, "effects":command.values,
                "expected_clips":{clip.id:asdict(clip) for clip in before}}
        else:
            values = {"base_version_id":base.id, "clip_id":before[0].id, "expected_asset_id":before[0].asset_id,
                "expected_segment_id":before[0].segment_id, **command.values}
            if command.operation_type == "ChangeCrop":
                values.update(asset_id=before[0].asset_id, segment_id=before[0].segment_id, source_start_seconds=before[0].source_start_seconds)
        previews = {side:{"ref":f"timeline:{base.id}", "label":label, "detail":"草稿参数已计算；应用时保存新版本，不自动生成媒体。"}
            for side, label in (("before","当前草稿"), ("after","修改后草稿"))}
        for side, record in zip(("before", "after"), records):
            previews[side].update(ref=record.logical_ref, video_url=f"/api/creation-tasks/{task.id}/visual/artifacts/{record.id}",
                detail=f"仅预览代表镜头 {plan.clips.index(before[0])+1} 的开头（最多 5 秒，静音画面，不含字幕和图层）；未覆盖相邻转场。确认将应用到 {len(before)} 个指定镜头，不改变声音和时长。")
        if command.operation_type in {'ChangeCrop', 'ChangeClipSpeed'}:
            from app.creation.editing.metadata import describe_clip_settings
            for side, clip in (('before', before[0]), ('after', after[0])):
                previews[side]['detail'] = describe_clip_settings(clip, plan.clips, command.operation_type)
        invalidated = [ref for ref in (plan.output_path, plan.subtitle_path) if ref]
        if any(Path(ref).is_absolute() or ".." in Path(ref).parts for ref in invalidated):
            raise ClipPreviewConflict("当前成片仍使用迁移前的文件引用，不能建立可撤销修改")
        proposal = EditProposal(id=f"proposal_clip_{suffix}", workspace_id=actor.workspace_id, task_id=task.id,
            intent_id=intent.id, summary=labels[command.operation_type], target_refs=refs,
            operation={"operation_type":command.operation_type, "payload":values}, invalidated_refs=invalidated,
            external_effects=["已在本地生成开头最多5秒的静音画面对比；声音、字幕和任务版本尚未改变。"] if records else [],
            preview=previews, created_at=now, updated_at=now)
        return self.repository.save_clip_preview_proposal(intent, proposal, expected_version_id=base.id)
