"""One explicit, queued storyboard replacement over unchanged narration."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from uuid import uuid4

from app.creation.contracts import EditCommand, EditIntent, EditProposal, JobRun, TaskEvent
from app.creation.transactions import EditChangeSet, clone_cover_asset, clone_subtitle_track, prepare_change_set, commit_direct_change
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload

OPERATION = "regenerate_storyboard"


def speech_windows(plan):
    from app.subtitle_schedule import narration_schedule
    result = {}
    for cue, caption in zip(plan.cues, narration_schedule(plan.cues, plan.duration_seconds)):
        if cue.source_section_id not in result:
            result[cue.source_section_id] = [caption.start_seconds, caption.end_seconds]
        else:
            result[cue.source_section_id][1] = caption.end_seconds
    return result


def move_preserved_voice(previous, candidate):
    from app.audio_timeline import AudioEditRequest, apply_audio_edit
    before, after = speech_windows(previous), speech_windows(candidate)
    if (before.keys() != after.keys() or any(abs((b[1] - b[0]) - (after[key][1] - after[key][0])) > .001
                                           for key, b in before.items())):
        raise ValueError("故事板重算必须保留原正文和真实配音时长")
    if before == after:
        return deepcopy(previous.audio_timeline)
    if previous.audio_timeline is None:
        return candidate.audio_timeline
    graph = previous.audio_timeline.model_copy(deep=True)
    old_voice = [track for track in graph.tracks if track.role == 'narration']
    new_voice = [track for track in candidate.audio_timeline.tracks if track.role == 'narration']
    if len(old_voice) != 1 or len(new_voice) != 1:
        raise ValueError("配音轨不能确定逐段归属，请先核对原音轨；原配音与采用版本未改变")
    old, new = old_voice[0], new_voice[0]
    if old.locked:
        raise ValueError('配音轨已锁定，请先明确解锁再调整语音位置')
    # A single unchanged aggregate can be split using the existing envelope-safe edit.
    if len(old.clips) == 1 and len(new.clips) > 1:
        clip = old.clips[0]
        end = round(sum(right - left for left, right in before.values()) * 1000)
        if (clip.source_ref != previous.narration_audio_path or clip.source_start_ms != 0
                or clip.source_end_ms != end or clip.timeline_start_ms != 0 or clip.playback_rate != 1
                or round(max(value[1] for value in before.values()) * 1000) != end):
            raise ValueError("整条配音有独立裁剪或变速，不能猜测分段；请先核对原音轨")
        at = 0
        for start, stop in list(before.values())[:-1]:
            at += round((stop - start) * 1000)
            tail = next(track for track in graph.tracks if track.role == 'narration').clips[-1]
            graph = apply_audio_edit(graph, AudioEditRequest(action='split', target_id=tail.id,
                values={'at_ms': at}), round(candidate.duration_seconds * 1000))
        old = next(track for track in graph.tracks if track.role == 'narration')
        destinations = [round(value[0] * 1000) for value in after.values()]
    else:
        def source_key(clip):
            return (clip.source_ref, clip.source_start_ms, clip.source_end_ms, clip.playback_rate)
        lookup = {source_key(clip): clip.timeline_start_ms for clip in new.clips}
        if len(lookup) != len(new.clips) or len(old.clips) != len(new.clips) or any(source_key(c) not in lookup for c in old.clips):
            raise ValueError("原配音片段与候选不能唯一关联，请先核对原音轨；不替换原声")
        destinations = [lookup[source_key(clip)] for clip in old.clips]
    for clip, destination in zip(old.clips, destinations):
        if clip.locked and clip.timeline_start_ms != destination:
            raise ValueError("配音片段已锁定，请先明确解锁再调整画面节奏")
        clip.timeline_start_ms = destination
    return graph


def move_preserved_subtitles(previous, candidate, tracks):
    before, after = speech_windows(previous), speech_windows(candidate)
    if before == after:
        return tracks
    by_cue = {cue.id: cue.source_section_id for cue in previous.cues}
    subtitles = {cue.id: cue.source_cue_id for track in tracks for cue in track.cues}
    result = []
    for track in tracks:
        cues = []
        for cue in track.cues:
            if cue.source_cue_id is None:
                cues.append(cue)
                continue
            source, seen = cue.source_cue_id, set()
            while source not in by_cue and source in subtitles:
                if source in seen:
                    raise ValueError("字幕关联形成循环，不能猜测配音时轴")
                seen.add(source)
                source = subtitles[source]
            if source is None:
                cues.append(cue)
                continue
            section = by_cue.get(source)
            if section is None or section not in after:
                raise ValueError("原字幕缺少可确定的段落关联，请先核对字幕，不会静默平移")
            delta = after[section][0] - before[section][0]
            cues.append(replace(cue, start_seconds=round(cue.start_seconds + delta, 3),
                end_seconds=round(cue.end_seconds + delta, 3)))
        result.append(replace(track, cues=cues) if cues != track.cues else track)
    return result


def narration_signature(repository, task) -> str:
    artifact = repository.get_narration_artifact(task.workspace_id, task.narration_cache_key)
    if artifact is None or artifact.task_id != task.id or artifact.script_id != task.confirmed_script_id:
        raise ValueError("故事板必须复用当前文案的真实配音")
    segments = repository.list_narration_segments(task.workspace_id, task.id, task.confirmed_script_id)
    if not segments or any(item.status != "ready" or not item.audio_reference or not item.duration_seconds for item in segments):
        raise ValueError("当前文案缺少可用的真实分段配音")
    files = []
    for reference in dict.fromkeys([repository.narration_artifact_reference(artifact.cache_key),
            *(item.audio_reference for item in segments)]):
        path = repository.resolve_authorized_narration_audio_reference(task.workspace_id, reference)
        info = path.stat()
        if info.st_size <= 0:
            raise ValueError("故事板配音文件为空")
        files.append((reference, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    manifest = [asdict(artifact), [asdict(item) for item in segments], files]
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()


def material_signature(repository, task) -> str:
    assets = {asset.id: asset for asset, _segment in repository.automatic_material_segments(
        task.workspace_id, viewer_user_id=task.owner_user_id)}
    manifest = []
    for asset_id in sorted(assets):
        reference = repository.workspace_media_reference_for_asset(task.workspace_id, asset_id)
        path = repository.resolve_clip_media_reference(task.workspace_id, asset_id, reference)
        info = path.stat()
        manifest.append((asset_id, reference, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return hashlib.sha256(json.dumps(manifest).encode()).hexdigest()


def request_current(repository, task, event) -> bool:
    inputs = event["request_inputs"]
    if (task.current_timeline_version_id != inputs["expected_timeline_version_id"]
            or task.confirmed_script_id != inputs["script_id"]
            or task.narration_cache_key != event["narration_cache_key"]):
        return False
    version = repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
    return (version.sequence == inputs["expected_timeline_sequence"]
            and narration_signature(repository, task) == event["narration_signature"]
            and material_signature(repository, task) == event["material_signature"])


@dataclass(frozen=True)
class PreparedStoryboard:
    command: EditCommand
    intent: EditIntent
    proposal: EditProposal
    change: EditChangeSet


class StoryboardRegeneration:
    def __init__(self, repository, montage_stage=None):
        self.repository, self.montage_stage = repository, montage_stage

    def enqueue(self, task, inputs, actor, *, validate_input, stage_facts):
        if actor.workspace_id != task.workspace_id:
            raise PermissionError("故事板请求不属于当前工作区")
        payload = {"operation": OPERATION, "request_id": inputs["request_id"], "request_inputs": inputs,
            "script_id": inputs["script_id"], "input_digest": inputs["input_digest"], **actor.identity_payload()}
        run = JobRun(id=f"job_{uuid4().hex}", workspace_id=task.workspace_id, task_id=task.id,
            kind="creation_task_execution", idempotency_key=f"storyboard-regeneration:{task.id}:{inputs['request_id']}",
            input_digest=inputs["input_digest"])
        def verify(conn, current):
            member = actor.user_id or actor.member_id
            if current.owner_user_id and current.owner_user_id != member:
                raise PermissionError("当前成员不能重新生成此任务的故事板")
            script, plan = validate_input(current.workspace_id, current, inputs["script_id"], inputs["input_digest"])
            payload.update(plan_id=current.plan_id, narration_cache_key=current.narration_cache_key,
                narration_signature=narration_signature(self.repository, current),
                material_signature=material_signature(self.repository, current),
                stage_key=stage_facts(current, script, plan))
            if not request_current(self.repository, current, payload):
                raise ValueError("时间线、已采用文案或配音已变化，请重新读取当前任务")
        return self.repository.claim_storyboard_regeneration(run, payload, verify_inputs=verify)

    def replay(self, workspace_id, task_id, inputs, actor):
        if actor.workspace_id != workspace_id:
            raise PermissionError("故事板请求不属于当前工作区")
        job = self.repository.storyboard_regeneration_job(workspace_id, task_id, inputs["request_id"])
        if job is None:
            return None
        event = self.repository.get_task_event(workspace_id, f"event_{job.id}_start").payload
        if any(event.get(key) != value for key, value in actor.identity_payload().items()):
            raise PermissionError("故事板原请求属于其他制作身份")
        if event["request_inputs"] != inputs:
            raise ValueError("故事板请求身份已用于不同输入")
        return job

    def read(self, workspace_id, task_id, request_id, actor):
        if actor.workspace_id != workspace_id:
            raise PermissionError("故事板请求不属于当前工作区")
        job, event, result = self.repository.read_storyboard_regeneration_receipt(workspace_id, task_id, request_id)
        if any(event.get(key) != value for key, value in actor.identity_payload().items()):
            raise PermissionError("故事板原请求属于其他制作身份")
        inputs = event["request_inputs"]
        return {"request_id": request_id, "task_id": task_id, "job_id": job.id, "status": job.status,
            "stage": "montage_plan", "script_id": event["script_id"], "narration_cache_key": event["narration_cache_key"],
            "base_timeline_version_id": inputs["expected_timeline_version_id"],
            "base_timeline_sequence": inputs["expected_timeline_sequence"], "result": result,
            "failure_reason": "故事板重算未完成，原稿保留；请读取当前任务条件。" if job.failure_reason else None,
            "result_url": f"/api/creation-tasks/{task_id}/montage/plan/requests/{request_id}"}

    def prepare(self, job, event, context):
        task = self.repository.get_creation_task(job.workspace_id, job.task_id)
        if not request_current(self.repository, task, event):
            raise ValueError("故事板原请求的时间线、文案或配音已变化")
        base = self.repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
        previous = creation_task_montage_plan_from_payload(base.snapshot["montage"])
        candidate = self.montage_stage.build_candidate(job.workspace_id, job.task_id, grant_context=context)
        from app.creation.duration_requirement import DurationRequirement
        DurationRequirement.from_plan(self.repository.get_creation_plan(task.workspace_id, task.plan_id)).require(
            candidate.duration_seconds, 30)
        voice = move_preserved_voice(previous, candidate)
        # The task owns one montage object; immutable TimelineVersions own its revisions.
        candidate.id = previous.id
        for field in ("aspect_ratio", "narration_audio_path", "graphic_overlays", "narration_linkage_created_audio_timeline"):
            setattr(candidate, field, deepcopy(getattr(previous, field)))
        candidate.audio_timeline = voice
        candidate.output_path = candidate.subtitle_path = None
        if len(json.dumps(creation_task_montage_plan_to_payload(candidate)).encode()) > context.max_output_bytes:
            raise ValueError("故事板输出超过阶段预算")
        metadata = self.montage_stage.metadata
        metadata.picture_renderer.validate_material_use(job.workspace_id, candidate)
        version_id = f"timeline_{uuid4().hex}"
        original_tracks = metadata.current_subtitle_tracks(job.workspace_id, task, current_version=base, montage=previous)
        shifted_tracks = move_preserved_subtitles(previous, candidate, original_tracks)
        proposal_id = f"proposal_{uuid4().hex}"
        tracks = []
        for original, shifted in zip(original_tracks, shifted_tracks):
            if shifted.cues != original.cues and shifted.status == 'valid':
                from app.creation.editing.metadata_use_case import execution_subtitle_track
                from app.subtitle_tracks import render_srt
                artifact_ref, transform_id = metadata.publish_subtitle_text(workspace_id=job.workspace_id,
                    task_id=task.id, proposal_id=proposal_id, kind='subtitle_retime',
                    input_refs=[f'timeline:{base.id}', candidate.narration_audio_path],
                    content=render_srt(execution_subtitle_track(shifted)), engine='timeline-clock-v1',
                    parameters={'language': shifted.language, 'operation': 'RegenerateStoryboard'})
                shifted = replace(shifted, artifact_ref=artifact_ref, transform_id=transform_id)
            tracks.append(clone_subtitle_track(shifted, timeline_version_id=version_id))
        cover = metadata.cover_asset_for_version(job.workspace_id, task.id, base)
        cover = clone_cover_asset(cover, timeline_version_id=version_id) if cover is not None else None
        source = {key: event[key] for key in ("member_id", "identity_kind", "device_id", "user_id") if key in event}
        values = {"request_id": event["request_id"], "base_version_id": base.id}
        command = EditCommand(job.workspace_id, job.task_id, source, base.id, "RegenerateStoryboard", values, None)
        intent = EditIntent(f"intent_{uuid4().hex}", job.workspace_id, job.task_id, "重新生成故事板", "montage",
            [f"timeline:{base.id}"], None, "RegenerateStoryboard", values, {"keep_narration": True}, 1, "parsed")
        invalidated = [ref for ref in (previous.output_path, previous.subtitle_path) if ref]
        proposal = EditProposal(proposal_id, job.workspace_id, job.task_id, intent.id,
            "重新生成故事板，保留原配音与其他轨道", [f"timeline:{base.id}"],
            {"operation_type": "RegenerateStoryboard", "payload": values}, invalidated, {}, [])
        change = prepare_change_set(proposal=proposal, base_version=base, operation_type="RegenerateStoryboard",
            operation_payload=values, result_version_id=version_id, montage_plan_id=candidate.id,
            montage_payload=creation_task_montage_plan_to_payload(candidate), subtitle_tracks=tracks, cover_asset=cover,
            absent_cover_value=True)
        if not request_current(self.repository, self.repository.get_creation_task(job.workspace_id, job.task_id), event):
            raise ValueError("故事板生成期间时间线、文案或配音已变化")
        return PreparedStoryboard(command, intent, proposal, change)

    def commit(self, job, event, prepared, conn):
        def verify(command, connection):
            row = connection.execute("SELECT status FROM job_runs WHERE workspace_id=? AND id=?", (job.workspace_id, job.id)).fetchone()
            if row is None or row["status"] != "running":
                raise ValueError("故事板原作业已停止，不能提交新版本")
            task = self.repository._require_creation_task(connection, job.workspace_id, job.task_id)
            if not request_current(self.repository, task, event):
                raise ValueError("故事板生成期间时间线、文案或配音已变化")
            self.montage_stage.metadata.picture_renderer.validate_material_use(job.workspace_id,
                creation_task_montage_plan_from_payload(prepared.change.montage_payload))
        commit_direct_change(self.repository, prepared.command, prepared.intent, prepared.proposal, prepared.change,
            verify_sources=verify, connection=conn)
        result = {"request_id": event["request_id"], "montage_plan_id": prepared.change.montage_plan_id,
            "timeline_version_id": prepared.change.version.id, "timeline_sequence": prepared.change.version.sequence,
            "operation_id": prepared.change.operation.id}
        self.repository.append_task_event(TaskEvent(id=f"event_{job.id}_storyboard_result", workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type="storyboard_regenerated", payload=result), connection=conn)
        return prepared.change.montage_plan_id
