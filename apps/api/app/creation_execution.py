"""Workspace-bound continuation of an explicitly started CreationTask."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.canvas import canvas_size
from app.device_scheduler import DeviceScheduler, Grant, RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork, WorkRequest
from app.host_resource_probes import HostResourceRefusal, MontageBudget, NarrationBudget, PresenterBudget, admit_resource, sample_host_capacity
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload, select_presenter_sections
from app.avatar_lipsync import _authorization_failure, avatar_lipsync_cache_key
from app.production import CreationPlan, CreationTask, CreationTaskScript, JobRun, TaskEvent
from app.repository import reset_workspace_context, set_workspace_context
from app.task_runtime import JobContext, JobFailure, JobResult, TaskRuntime


KIND = "creation_task_execution"
PERSON_EDIT_KIND = "creation_task_person_edit"
NARRATION_EDIT_KIND = "creation_task_narration_edit"
STAGES = ("narration", "montage_plan", "render")
DEVICE_STAGES = ("narration", "person_render", "montage_plan", "render")
DEVICE_OWNER_TYPE = "creation_job"


@dataclass(frozen=True)
class CreationExecutionActor:
    workspace_id: str
    member_id: str
    device_id: str | None = None
    user_id: str | None = None

    def __post_init__(self) -> None:
        if self.device_id is not None or self.user_id is not None:
            if (not self.device_id or not self.user_id
                    or str(UUID(self.device_id)) != self.device_id
                    or str(UUID(self.user_id)) != self.user_id
                    or str(UUID(self.workspace_id)) != self.workspace_id
                    or self.member_id != f"account:{self.device_id}:{self.user_id}"):
                raise ValueError("账号许可身份元组不完整或不一致")

    @classmethod
    def account(cls, workspace_id: str, device_id: str, user_id: str):
        return cls(workspace_id, f"account:{device_id}:{user_id}", device_id, user_id)

    def identity_payload(self) -> dict[str, str]:
        payload = {"member_id": self.member_id}
        if self.device_id is not None:
            payload.update(identity_kind="account", device_id=self.device_id, user_id=self.user_id)
        return payload

    @classmethod
    def from_event(cls, workspace_id: str, payload: dict):
        member_id = payload.get("member_id")
        if not isinstance(member_id, str) or not member_id:
            raise ValueError("制作作业缺少持久成员身份")
        if payload.get("identity_kind") == "account":
            original = cls.account(workspace_id, payload["device_id"], payload["user_id"])
            if original.member_id != member_id:
                raise ValueError("制作作业账号归属不一致")
            return original
        if (payload.get("identity_kind") is not None or member_id.startswith("account:")
                or payload.get("device_id") is not None or payload.get("user_id") is not None):
            raise ValueError("制作作业身份类型无法复核")
        return cls(workspace_id, member_id)


@dataclass(frozen=True)
class CreationStageGrantContext:
    scheduler: DeviceScheduler
    grant: Grant
    deadline_monotonic: float
    max_output_bytes: int
    threads: int
    cancel_event: threading.Event | None = None

    def check(self) -> None:
        from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise GrantedCommandCancelled("制作阶段已取消")
        if time.monotonic() >= self.deadline_monotonic:
            raise GrantedCommandTimeout("制作阶段总时限已到")
        self.scheduler.assert_grant(self.grant)

    def run(self, command, *, env=None, output_paths=(), output_bytes=None):
        from app.granted_subprocess import run_granted_command
        self.check()
        return run_granted_command(
            self.scheduler, self.grant, command,
            timeout_seconds=self.deadline_monotonic - time.monotonic(),
            max_stdout_bytes=65536, max_stderr_bytes=65536, cancel_event=self.cancel_event,
            env=env, output_paths=output_paths,
            max_output_file_bytes=self.max_output_bytes if output_bytes is None else output_bytes,
        )


@dataclass(frozen=True)
class CpuRenderGrantContext(CreationStageGrantContext):
    pass


@dataclass(frozen=True)
class CpuRenderLimits:
    max_output_bytes: int
    threads: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (type(self.max_output_bytes) is not int or self.max_output_bytes <= 0
                or type(self.threads) is not int or self.threads <= 0
                or type(self.timeout_seconds) not in {int, float}
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("CPU 成片必须显式配置正数输出、线程和总时限")


def presenter_inputs(repository, task, script, plan, models):
    """Re-read current authorized person and narration inputs, also on cache reuse."""
    if not plan.presenter_share_target:
        return []
    if plan.selected_voice is None or plan.lipsync_quality not in models:
        raise ValueError("人物模型或授权人物版本不可用")
    segments = {item.source_section_id: item for item in repository.list_narration_segments(
        task.workspace_id, task.id, script.id)}
    if any(section.id not in segments or segments[section.id].status != "ready"
           or not segments[section.id].duration_seconds for section in script.sections):
        raise ValueError("人物制作缺少当前文案的可用配音分段")
    selected = select_presenter_sections(
        [(section.id, segments[section.id].duration_seconds) for section in script.sections],
        plan.presenter_share_target)
    if plan.selected_person is None:
        raise ValueError("请单独选择人物档案与固定版本后再制作人物段")
    person = repository.get_person_profile(task.workspace_id, plan.selected_person.profile_id,
                                           plan.selected_person.version)
    inputs = []
    for section_id in selected:
        segment = segments[section_id]
        if (segment.voice_profile_id, segment.voice_version) != (plan.selected_voice.profile_id, plan.selected_voice.version):
            raise ValueError("人物配音版本与当前方案不一致")
        audio = repository.resolve_authorized_narration_audio_reference(task.workspace_id, segment.audio_reference)
        request = repository.resolve_avatar_lipsync_request(
            workspace_id=task.workspace_id, person_id=person.id, person_version=person.version,
            voice_profile_id=segment.voice_profile_id, voice_version=segment.voice_version,
            audio_path=str(audio), audio_cache_key=segment.audio_reference, language=segment.language,
            motion="natural", expression="neutral", authorization_id=person.authorization.id if person.authorization else "",
            authorization_version=person.authorization.version if person.authorization else "", model_id=models[plan.lipsync_quality], viewer_user_id=task.owner_user_id)
        if _authorization_failure(request) is not None:
            raise PermissionError("人物或声音授权已失效，不能生成或复用")
        inputs.append((segment, request))
    return inputs


def presenter_input_facts(request):
    """Local mutation facts sealed with the output's existing transform record."""
    facts = []
    for role, value in (("audio", request.audio_path), ("visual", request.visual_path)):
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError("人物输入文件缺失，不能生成或复用")
        stat = path.stat()
        if stat.st_size <= 0:
            raise ValueError("人物输入文件为空，不能生成或复用")
        facts.append({"role": role, "path": str(path), "size_bytes": stat.st_size,
                      "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
                      "inode": stat.st_ino, "device": stat.st_dev})
    return facts


def current_presenter_render(repository, task, segment, request, resolve_output):
    input_facts = presenter_input_facts(request)
    key = avatar_lipsync_cache_key(request, input_facts=input_facts)
    render = next((item for item in repository.list_person_renders(task.workspace_id, task.id)
                   if item.cache_key == key and item.narration_segment_id == segment.id
                   and item.person_profile_id == request.person.id and item.person_version == request.person.version
                   and item.voice_profile_id == segment.voice_profile_id and item.voice_version == segment.voice_version
                   and item.model_id == request.model_id and item.status == "ready"), None)
    if render is None or not render.artifact_ref or not render.alignment_evidence or not render.duration_seconds:
        return None
    if render.duration_seconds + .001 < segment.duration_seconds:
        return None
    transforms = repository.list_transform_runs(task.workspace_id, task.id)
    if not any(item.transform_type == "person_render" and item.status == "succeeded"
               and item.input_digest == key and render.artifact_ref in item.output_refs
               and segment.audio_reference in item.input_refs
               and item.parameters.get("language") == segment.language
               and item.parameters.get("presenter_input_facts") == input_facts for item in transforms):
        return None
    path = resolve_output(render)
    if path is None or not path.is_file() or path.stat().st_size <= 0:
        return None
    return render


def presenter_stage_facts(repository, task, script, plan, budget, models):
    if budget is None:
        raise ValueError("人物制作预算尚未标定，请配置 DEVICE_PRESENTER_* 标定值")
    inputs = presenter_inputs(repository, task, script, plan, models)
    if not inputs:
        raise ValueError("当前方案没有待制作的人物段落")
    files = {fact["path"]: fact for _, item in inputs for fact in presenter_input_facts(item)}
    facts = [files[path] for path in sorted(files)]
    resources = {"kind": "creation_person_render", "execution_mode": "isolated_avatar_lipsync",
                 "section_count": len(inputs), "input_bytes": sum(item["size_bytes"] for item in facts),
                 "duration_seconds": sum(item.duration_seconds for item, _ in inputs),
                 "max_output_bytes": budget.max_output_bytes, "threads": budget.max_threads,
                 "timeout_seconds": budget.max_timeout_seconds}
    manifest = {"inputs": [[asdict(segment), asdict(request)] for segment, request in inputs],
                "files": facts, "resources": resources}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
    return f"person_render:{digest}", resources


def person_edit_digest(operation):
    return hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()


def narration_edit_digest(operation):
    return hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()


def narration_edit_stage_facts(repository, task, proposal, plan, budget):
    """Seal one confirmed script segment; admission does not decode or generate."""
    if budget is None:
        raise ValueError("局部配音预算尚未标定，请配置 DEVICE_NARRATION_* 标定值")
    operation = proposal.operation
    payload = operation["payload"]
    if (proposal.workspace_id != task.workspace_id or proposal.task_id != task.id
            or proposal.status != "proposed"
            or operation["operation_type"] != "RegenerateNarrationSegment"
            or task.current_timeline_version_id != payload["base_version_id"]):
        raise ValueError("配音编辑提案或时间线已变化，请重新预览")
    script = repository.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
    segment = repository.get_narration_segment(task.workspace_id, task.id, payload["narration_segment_id"])
    section = next(item for item in script.sections if item.id == segment.source_section_id)
    if (script.selection_status != "confirmed" or segment.script_id != script.id
            or segment.status != "ready" or not segment.duration_seconds
            or segment.duration_seconds <= 0 or segment.text != section.content
            or segment.audio_reference != payload["expected_audio_reference"]
            or not segment.audio_reference or plan.selected_voice is None):
        raise ValueError("配音片段或当前确认文案已变化，请重新预览")
    inputs = repository.resolve_creation_task_narration_inputs(
        workspace_id=task.workspace_id, task_id=task.id,
        voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
    if not plan.selected_voice.version or inputs.voice.version != plan.selected_voice.version:
        raise ValueError("局部配音需要明确的声音固定版本")
    reference = repository.profile_reference_input(inputs.voice, viewer_user_id=task.owner_user_id)
    if segment.audio_reference.startswith("narration://artifact/"):
        artifact = repository.get_narration_artifact(task.workspace_id, segment.audio_reference.removeprefix("narration://artifact/"))
        if artifact is None or artifact.task_id != task.id or artifact.script_id != script.id:
            raise ValueError("原配音产物不属于当前任务及文案版本")
    original = repository.resolve_authorized_narration_audio_reference(task.workspace_id, segment.audio_reference)
    stat = original.stat()
    if not original.is_file() or stat.st_size <= 0:
        raise ValueError("原配音来源缺失，未安排局部重做")
    segments = repository.list_narration_segments(task.workspace_id, task.id, script.id)
    source_audio = []
    segment_artifacts = []
    total_duration = 0.0
    for item in segments:
        if (item.status != "ready" or item.script_id != script.id or not item.duration_seconds
                or item.duration_seconds <= 0 or not item.audio_reference):
            raise ValueError("合并配音存在不可用片段，未安排局部重做")
        record = repository.get_narration_artifact(task.workspace_id,
            item.audio_reference.removeprefix("narration://artifact/"))
        if (not item.audio_reference.startswith("narration://artifact/") or record is None
                or record.task_id != task.id or record.script_id != script.id):
            raise ValueError("合并配音产物不属于当前任务及文案版本")
        segment_artifacts.append(asdict(record))
        path = repository.resolve_authorized_narration_audio_reference(task.workspace_id, item.audio_reference)
        info = path.stat()
        if not path.is_file() or info.st_size <= 0:
            raise ValueError("合并配音来源缺失，未安排局部重做")
        source_audio.append([item.id, str(path.resolve()), info.st_size, info.st_mtime_ns,
                             info.st_ctime_ns, info.st_ino, info.st_dev])
        total_duration += item.duration_seconds
    montage = repository.get_creation_task_montage_plan(task.workspace_id, task.id)
    resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
                 "text_chars": len(section.content) + len(inputs.voice.reference_text),
                 "section_count": 1, "reference_bytes": reference.size_bytes,
                 "duration_seconds": total_duration,
                 "max_output_bytes": budget.max_output_bytes, "threads": budget.max_threads,
                 "timeout_seconds": budget.max_timeout_seconds}
    manifest = {"operation": operation, "script": asdict(script), "segment": asdict(segment),
                "voice": asdict(inputs.voice), "reference_input": asdict(reference),
                "segments": [asdict(item) for item in segments], "source_audio": source_audio,
                "segment_artifacts": segment_artifacts,
                "montage": montage,
                "rules": [asdict(item) for item in inputs.pronunciation_entries],
                "model_id": plan.selected_voice.model_id, "language": segment.language,
                "original_audio": [str(original.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
                "resources": resources}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
    return f"narration_edit:{digest}", resources


def person_edit_stage_facts(repository, task, proposal, plan, budget, models):
    """Seal one selected person segment, never the whole presenter stage."""
    if budget is None:
        raise ValueError("人物制作预算尚未标定，请配置 DEVICE_PRESENTER_* 标定值")
    operation = proposal.operation
    payload = operation["payload"]
    if (proposal.task_id != task.id or proposal.status != "proposed"
            or operation["operation_type"] not in {"ChangePersonSegment", "RegeneratePersonRender"}
            or task.current_timeline_version_id != payload["base_version_id"]):
        raise ValueError("人物编辑提案或时间线已变化，请重新预览")
    montage = creation_task_montage_plan_from_payload(repository.get_creation_task_montage_plan(task.workspace_id, task.id))
    clip = next(item for item in montage.clips if item.id == payload["clip_id"])
    cue = next(item for item in montage.cues if item.id == payload["cue_id"])
    segment = repository.get_narration_segment(task.workspace_id, task.id, payload["narration_segment_id"])
    if (clip.asset_id != payload["expected_asset_id"] or clip.segment_id != payload["expected_segment_id"]
            or clip.person_render_id != payload["expected_person_render_id"]
            or cue.narration_segment_id != segment.id or segment.script_id != task.confirmed_script_id
            or segment.status != "ready" or segment.audio_reference != payload["expected_audio_reference"]
            or payload.get("lipsync_quality", plan.lipsync_quality) != plan.lipsync_quality):
        raise ValueError("人物编辑对象或配音已变化，请重新预览")
    person = repository.get_person_profile(task.workspace_id, payload["person_profile_id"], payload["person_version"])
    audio = repository.resolve_authorized_narration_audio_reference(task.workspace_id, segment.audio_reference)
    request = repository.resolve_avatar_lipsync_request(
        workspace_id=task.workspace_id, person_id=person.id, person_version=person.version,
        voice_profile_id=segment.voice_profile_id, voice_version=segment.voice_version,
        audio_path=str(audio), audio_cache_key=segment.audio_reference, language=segment.language,
        motion=payload["motion"], expression=payload["expression"],
        authorization_id=person.authorization.id if person.authorization else "", authorization_version=person.authorization.version if person.authorization else "",
        model_id=models[plan.lipsync_quality], viewer_user_id=task.owner_user_id)
    if _authorization_failure(request) is not None:
        raise PermissionError("人物或声音授权已失效，不能生成或复用")
    files = presenter_input_facts(request)
    resources = {"kind": "creation_person_render", "execution_mode": "isolated_avatar_lipsync",
                 "section_count": 1, "input_bytes": sum(item["size_bytes"] for item in files),
                 "duration_seconds": segment.duration_seconds,
                 "max_output_bytes": budget.max_output_bytes, "threads": budget.max_threads,
                 "timeout_seconds": budget.max_timeout_seconds}
    manifest = [operation, asdict(segment), asdict(request), files, resources]
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
    return f"person_edit:{digest}", resources


def cpu_render_facts(repository, conn: sqlite3.Connection, task: CreationTask,
                     creation_plan: CreationPlan, limits: CpuRenderLimits,
                     *, request: WorkRequest | None = None, presenter_limits=None,
                     presenter_models=None, resolve_presenter_output=None) -> tuple[str, dict[str, object]]:
    """Derive a bounded pure-material render from current confirmed inputs."""
    if not task.montage_plan_id or not task.narration_cache_key or not task.confirmed_script_id:
        raise ValueError("当前任务缺少已确认故事板或配音")
    row = conn.execute(
        "SELECT payload FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=? AND id=?",
        (task.workspace_id, task.id, task.montage_plan_id),
    ).fetchone()
    if row is None:
        raise ValueError("当前故事板不存在")
    payload = json.loads(row["payload"])
    montage = creation_task_montage_plan_from_payload(payload)
    # Bind the same domain defaults used by the renderer. Saving its running
    # state materializes omitted legacy defaults without changing the edit.
    payload = creation_task_montage_plan_to_payload(montage)
    if (montage.workspace_id != task.workspace_id or montage.task_id != task.id
            or montage.script_id != task.confirmed_script_id
            or montage.status not in {"ready", "rendering", "rendered", "failed"}
            or not montage.clips or any(clip.needs_confirmation for clip in montage.clips)
            or not math.isfinite(montage.duration_seconds) or montage.duration_seconds <= 0):
        raise ValueError("当前故事板尚未确认或与文案不一致")
    presenter = bool(montage.presenter_share_target or any(clip.person_render_id for clip in montage.clips))
    person_inputs = {}
    if presenter:
        if presenter_limits is None or resolve_presenter_output is None:
            raise ValueError("人物组合 CPU 成片专属预算尚未标定")
        limits = presenter_limits
        script = repository.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
        person_inputs = {segment.id: (segment, item) for segment, item in
                         presenter_inputs(repository, task, script, creation_plan, presenter_models or {})}
    if montage.audio_timeline is not None:
        raise ValueError("当前有效音轨组合尚未接入 CPU 成片预算")
    if montage.graphic_overlays:
        raise ValueError("图层组合尚未接入 CPU 成片预算")
    if any(clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure")
           for clip in montage.clips):
        raise ValueError("本机人像效果尚无受许可执行路径")
    narration_reference = repository.narration_artifact_reference(task.narration_cache_key)
    if montage.narration_audio_path != narration_reference:
        raise ValueError("故事板配音与当前任务产物不一致")
    narration = repository.get_narration_artifact(task.workspace_id, task.narration_cache_key)
    if (narration is None or narration.task_id != task.id or narration.script_id != task.confirmed_script_id):
        raise ValueError("当前配音产物不可用")
    if narration.source_kind == "recorded":
        if (presenter or creation_plan.presenter_share_target != 0
                or narration.voice_profile_id is not None or narration.model_id is not None):
            raise ValueError("本人录音只能用于纯素材成片，人物身份仍需已授权的声音版本")
    elif (narration.source_kind != "generated" or creation_plan.selected_voice is None
          or narration.voice_profile_id != creation_plan.selected_voice.profile_id):
        raise ValueError("当前生成配音与已选择的声音版本不一致")
    version = None
    if task.current_timeline_version_id:
        version = repository.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
        if version.task_id != task.id:
            raise ValueError("当前时间线不属于制作任务")
        # Only this request's actual inner publication can advance its own
        # version without changing the inputs bound by the existing Grant.
        if (request is not None and version.created_by_operation_id is None
                and version.snapshot.get("creation_render_request_id") == request.id):
            transform_id = version.snapshot.get("transform_run_id")
            if (not isinstance(transform_id, str) or not transform_id.startswith("transform_job_")
                    or version.id != f"timeline_{transform_id.removeprefix('transform_')}"
                    or "render_input_timeline_version_id" not in version.snapshot
                    or version.parent_version_id != version.snapshot["render_input_timeline_version_id"]):
                raise ValueError("当前成片版本缺少本次许可的原始输入")
            inner = repository.get_job_run(task.workspace_id, transform_id.removeprefix("transform_"))
            transform = repository.get_transform_run(task.workspace_id, transform_id)
            if (inner.task_id != task.id or inner.kind != "creation_task_montage_render"
                    or inner.status != "succeeded" or transform.status != "succeeded"
                    or transform.job_id != inner.id or transform.task_id != task.id
                    or inner.input_digest != version.snapshot.get("render_input_digest")
                    or transform.input_digest != inner.input_digest
                    or inner.output_refs != version.snapshot.get("output_refs")
                    or transform.output_refs != inner.output_refs
                    or payload.get("status") != "rendered"
                    or inner.output_refs != [payload.get("output_path"), payload.get("subtitle_path")]
                    or not all(isinstance(ref, str) and ref for ref in inner.output_refs)):
                raise ValueError("当前成片版本与真实渲染记录不一致")
            version = (repository.get_timeline_version(task.workspace_id, version.parent_version_id)
                       if version.parent_version_id is not None else None)
    if version is not None:
        previous_montage = version.snapshot.get("montage")
        if not isinstance(previous_montage, dict):
            raise ValueError("当前时间线缺少可信故事板快照")
        previous_montage = creation_task_montage_plan_to_payload(
            creation_task_montage_plan_from_payload(previous_montage))
        transient = {"status", "message", "output_path", "subtitle_path", "updated_at"}
        if ({key: item for key, item in previous_montage.items() if key not in transient}
                != {key: item for key, item in payload.items() if key not in transient}):
            raise ValueError("当前时间线与已确认故事板不一致")
    audio = repository.resolve_authorized_narration_audio_reference(task.workspace_id, narration_reference)
    sources = [audio]
    for clip in montage.clips:
        if clip.person_render_id:
            cue = next((item for item in montage.cues if item.id == clip.cue_id), None)
            if cue is None or cue.narration_segment_id not in person_inputs:
                raise ValueError("人物镜头不属于当前配音与露脸选择")
            segment, item = person_inputs[cue.narration_segment_id]
            render = current_presenter_render(repository, task, segment, item, resolve_presenter_output)
            if (render is None or render.id != clip.person_render_id
                    or clip.asset_id != f"person:{render.id}" or clip.source_path != f"person-render://{render.id}"
                    or clip.source_end_seconds > render.duration_seconds + .001):
                raise ValueError("人物镜头版本、授权或输出已失效")
            source = resolve_presenter_output(render)
        else:
            source = repository.resolve_clip_media_reference(task.workspace_id, clip.asset_id, clip.source_path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ValueError("当前镜头源文件不可用")
        sources.append(source)
    source_facts = []
    for path in sources:
        stat = path.stat()
        if stat.st_size <= 0:
            raise ValueError("渲染源文件为空")
        # ctime is a local mutation signal, including same-length writes whose
        # mtime was restored. It is not a content hash or a remote-file proof.
        source_facts.append({"path": str(path), "size": stat.st_size,
                             "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
                             "inode": stat.st_ino,
                             "device": stat.st_dev})
    manifest = {
        "timeline_version_id": version.id if version is not None else None,
        "subtitle_track_ids": version.snapshot.get("subtitle_track_ids") if version is not None else None,
        "subtitle_tracks": [
            {"id": track.id, "language": track.language, "status": track.status,
             "style": track.style, "cues": [asdict(cue) for cue in track.cues]}
            for track in repository.list_subtitle_tracks(
                task.workspace_id, task.id, timeline_version_id=version.id)
        ] if version is not None else [],
        "plan": {key: value for key, value in payload.items()
                 if key not in {"status", "message", "output_path", "subtitle_path", "updated_at"}},
        "sources": source_facts,
    }
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                       default=str).encode("utf-8")).hexdigest()
    # These are the exact fields HostResourceProbe reads back to budget the unit.
    # A set that is not decidable there blocks the render, so the canvas comes
    # from the shared aspect-ratio authority the renderer itself uses, not from
    # the input media, and the byte total is the already-measured source set.
    output_width, output_height = canvas_size(montage.aspect_ratio)
    resources = {
        "kind": "creation_presenter_render" if presenter else "creation_render", "execution_mode": "cpu_ffmpeg",
        "duration_seconds": montage.duration_seconds,
        "timeout_seconds": limits.timeout_seconds,
        "width_px": output_width, "height_px": output_height,
        "input_bytes": sum(int(fact["size"]) for fact in source_facts),
        "clip_count": len(montage.clips),
        "overlay_count": len(montage.graphic_overlays),
        "max_output_bytes": limits.max_output_bytes,
        "threads": limits.threads,
    }
    return f"render:{digest}", resources


def creation_stage_facts(repository, task, script, plan, stage, narration_budget, montage_budget):
    """Derive measured inputs; stage caps never borrow the FFmpeg render budget."""
    if stage == "narration":
        if narration_budget is None:
            raise ValueError("配音预算尚未标定，请配置 DEVICE_NARRATION_* 标定值")
        inputs = repository.resolve_creation_task_narration_inputs(
            workspace_id=task.workspace_id, task_id=task.id,
            voice_profile_id=plan.selected_voice.profile_id, voice_version=plan.selected_voice.version)
        reference_input = repository.profile_reference_input(inputs.voice, viewer_user_id=task.owner_user_id)
        reference = Path(reference_input.source_path)
        stat = reference.stat()
        resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
                     "text_chars": (sum(len(section.content) for section in script.sections)
                                    + len(inputs.voice.reference_text) * len(script.sections)),
                     "section_count": len(script.sections), "reference_bytes": stat.st_size,
                     "duration_seconds": plan.duration_target_seconds,
                     "max_output_bytes": narration_budget.max_output_bytes,
                     "threads": narration_budget.max_threads,
                     "timeout_seconds": narration_budget.max_timeout_seconds}
        manifest = {"reference": [str(reference), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
                    "reference_input": asdict(reference_input),
                    "voice": asdict(inputs.voice), "resources": resources}
    else:
        if montage_budget is None:
            raise ValueError("故事板预算尚未标定，请配置 DEVICE_MONTAGE_* 标定值")
        if not task.narration_cache_key:
            raise ValueError("故事板缺少当前配音")
        narration = repository.get_narration_artifact(task.workspace_id, task.narration_cache_key)
        if narration is None or narration.script_id != script.id:
            raise ValueError("故事板配音版本失效")
        assets = repository.list_assets(task.workspace_id)
        manifest = {"assets": [asdict(asset) for asset in assets],
                    "narration": asdict(narration), "script": asdict(script)}
        encoded = json.dumps(manifest, sort_keys=True, default=str).encode()
        resources = {"kind": "creation_montage_plan", "execution_mode": "metadata_only",
                     "section_count": len(script.sections), "asset_count": len(assets),
                     "input_bytes": len(encoded), "max_output_bytes": montage_budget.max_output_bytes,
                     "timeout_seconds": montage_budget.max_timeout_seconds}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
    return f"{stage}:{digest}", resources


class CreationJobOwnerVerifier:
    """Validate a persisted JobRun before it can become a device work unit."""

    def __init__(self, repository, *, cpu_render_limits: CpuRenderLimits | None = None,
                 verify_identity: Callable[[CreationExecutionActor], str] | None = None,
                 narration_budget: NarrationBudget | None = None,
                 montage_budget: MontageBudget | None = None,
                 presenter_budget: PresenterBudget | None = None,
                 presenter_cpu_render_limits: CpuRenderLimits | None = None,
                 presenter_models: dict | None = None, resolve_presenter_output=None, task_media=None) -> None:
        self.repository = repository
        self.cpu_render_limits = cpu_render_limits
        self.verify_identity = verify_identity
        self.narration_budget = narration_budget
        self.montage_budget = montage_budget
        self.presenter_budget = presenter_budget
        self.presenter_cpu_render_limits = presenter_cpu_render_limits
        self.presenter_models = presenter_models or {}
        self.resolve_presenter_output = resolve_presenter_output
        self.task_media = task_media

    def render_facts(self, conn, task, plan, *, request=None):
        # Person composition never inherits the pure-material caps.
        limits = self.presenter_cpu_render_limits if plan.presenter_share_target else self.cpu_render_limits
        if limits is None:
            raise ValueError("人物组合 CPU 成片专属预算尚未标定" if plan.presenter_share_target else "CPU 成片资源预算尚未配置")
        return cpu_render_facts(self.repository, conn, task, plan, limits, request=request,
            presenter_limits=self.presenter_cpu_render_limits, presenter_models=self.presenter_models,
            resolve_presenter_output=self.resolve_presenter_output)

    def stage_facts(self, task, script, plan, stage):
        if stage == "person_render":
            return presenter_stage_facts(self.repository, task, script, plan, self.presenter_budget, self.presenter_models)
        key, resources = creation_stage_facts(self.repository, task, script, plan, stage,
                                             self.narration_budget, self.montage_budget)
        if stage == "montage_plan" and plan.presenter_share_target:
            inputs = presenter_inputs(self.repository, task, script, plan, self.presenter_models)
            renders = [current_presenter_render(self.repository, task, segment, request,
                        self.resolve_presenter_output) for segment, request in inputs]
            if any(item is None for item in renders):
                raise ValueError("人物段落尚未在专属许可下完成")
            manifest = []
            for item, (_, request) in zip(renders, inputs, strict=True):
                stat = self.resolve_presenter_output(item).stat()
                manifest.append([asdict(item), asdict(request),
                                 [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]])
            key = "montage_plan:" + hashlib.sha256(json.dumps([key, manifest], sort_keys=True, default=str).encode()).hexdigest()
        return key, resources

    def edit_facts(self, job, task, event, plan):
        proposal = self.repository.get_edit_proposal(job.workspace_id, event["edit_proposal_id"])
        return person_edit_stage_facts(self.repository, task, proposal, plan,
                                      self.presenter_budget, self.presenter_models)

    def narration_edit_facts(self, job, task, event, plan):
        proposal = self.repository.get_edit_proposal(job.workspace_id, event["edit_proposal_id"])
        return narration_edit_stage_facts(self.repository, task, proposal, plan, self.narration_budget)

    def person_valid(self, task):
        script = self.repository.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
        plan = self.repository.get_creation_plan(task.workspace_id, task.plan_id)
        inputs = presenter_inputs(self.repository, task, script, plan, self.presenter_models)
        return all(current_presenter_render(self.repository, task, segment, request,
                   self.resolve_presenter_output) is not None for segment, request in inputs)

    def _facts(self, conn: sqlite3.Connection, workspace_id: str, job_id: str):
        row = conn.execute(
            "SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = self.repository._job_run_from_row(row)
        task_row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
            (workspace_id, job.task_id),
        ).fetchone()
        event_row = conn.execute(
            "SELECT payload_json FROM task_events WHERE workspace_id=? AND id=? AND task_id=? AND job_id=?",
            (workspace_id, f"event_{job.id}_start", job.task_id, job.id),
        ).fetchone()
        if task_row is None or event_row is None:
            raise KeyError("创作任务或开始记录不存在")
        task = self.repository._creation_task_from_payload(json.loads(task_row["payload"]))
        event = json.loads(event_row["payload_json"])
        script_row = conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=? AND task_id=?",
            (workspace_id, event.get("script_id"), job.task_id),
        ).fetchone()
        plan_row = conn.execute(
            "SELECT payload, visibility FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, task.plan_id),
        ).fetchone()
        if script_row is None or plan_row is None:
            raise KeyError("已确认文案或方案不存在")
        script = self.repository._creation_task_script_from_payload(json.loads(script_row["payload"]))
        plan_payload = json.loads(plan_row["payload"])
        plan_payload["visibility"] = str(plan_row["visibility"])
        plan = self.repository._creation_plan_from_payload(plan_payload)
        return job, task, event, script, plan

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork:
        if intent.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.verify_submission(intent, actor)
        if (not isinstance(actor, CreationExecutionActor) or not actor.member_id
                or actor.workspace_id != intent.workspace_id or intent.owner_type != DEVICE_OWNER_TYPE):
            raise PermissionError("制作执行身份未知或工作区不匹配")
        with self.repository._connect() as conn:
            job, task, event, script, plan = self._facts(conn, intent.workspace_id, intent.owner_id)
            stage = intent.unit_key.split(":", 1)[0]
            verdict = self._check_facts(job, task, event, script, plan, stage, actor.member_id)
            try:
                original = CreationExecutionActor.from_event(job.workspace_id, event)
            except (ValueError, KeyError, TypeError, AttributeError):
                raise PermissionError("制作作业持久身份无法复核") from None
            if actor != original:
                raise PermissionError("制作作业原身份不匹配")
            if self.verify_identity is not None and self.verify_identity(original) != "ready":
                raise PermissionError("制作许可身份失效或事实源不可用")
            if verdict == "ready" and job.kind == NARRATION_EDIT_KIND:
                key, resources = self.narration_edit_facts(job, task, event, plan)
                if intent.unit_key != key:
                    raise PermissionError("配音编辑输入已变化，请重新安排")
            elif verdict == "ready" and job.kind == PERSON_EDIT_KIND:
                key, resources = self.edit_facts(job, task, event, plan)
                if intent.unit_key != key:
                    raise PermissionError("人物编辑输入已变化，请重新安排")
            elif verdict == "ready" and stage == "render":
                key, resources = self.render_facts(conn, task, plan)
                if intent.unit_key != key:
                    raise PermissionError("故事板或源文件已变化，请重新安排成片")
            elif verdict == "ready" and (
                (stage == "narration" and self.narration_budget is not None)
                or (stage == "montage_plan" and self.montage_budget is not None)
                or stage == "person_render"
            ):
                key, resources = self.stage_facts(task, script, plan, stage)
                if key != intent.unit_key:
                    raise PermissionError("制作阶段输入已变化，请重新安排")
            else:
                resources = {"kind": f"creation_{stage}"}
        if verdict != "ready":
            raise PermissionError(f"制作单元当前不可提交：{verdict}")
        if intent.input_digest != job.input_digest:
            raise PermissionError("制作输入摘要不匹配")
        return VerifiedWork(
            workspace_id=job.workspace_id, owner_type=DEVICE_OWNER_TYPE,
            owner_id=job.id, input_digest=job.input_digest, unit_key=intent.unit_key,
            member_id=actor.member_id, work_class=("preview_dependency" if job.kind == NARRATION_EDIT_KIND else "normal_make"),
            resources=resources,
        )

    def _check_facts(self, job, task, event, script, plan, stage: str, member_id: str | None) -> str:
        if (not member_id or event.get("member_id") != member_id
                or job.workspace_id != task.workspace_id or script.workspace_id != job.workspace_id
                or plan.workspace_id != job.workspace_id or script.task_id != job.task_id):
            return "unauthorized"
        if job.kind in {PERSON_EDIT_KIND, NARRATION_EDIT_KIND}:
            expected_stage = "narration_edit" if job.kind == NARRATION_EDIT_KIND else "person_edit"
            if (stage != expected_stage or job.status not in {"queued", "interrupted", "running"}
                    or event.get("input_digest") != job.input_digest
                    or event.get("script_id") != script.id or event.get("plan_id") != task.plan_id
                    or task.confirmed_script_id != script.id or script.selection_status != "confirmed"
                    or plan.creation_task_id != task.id or plan.status != "confirmed"
                    or event.get("revision_digest") != revision_digest(script, plan)):
                return "stale"
            proposal = self.repository.get_edit_proposal(job.workspace_id, event["edit_proposal_id"])
            if job.kind == NARRATION_EDIT_KIND:
                actor = CreationExecutionActor.from_event(job.workspace_id, event)
                if task.owner_user_id != (actor.user_id or actor.member_id):
                    return "unauthorized"
                return "ready" if narration_edit_digest(proposal.operation) == job.input_digest else "stale"
            return "ready" if person_edit_digest(proposal.operation) == job.input_digest else "stale"
        if (job.kind != KIND or stage not in DEVICE_STAGES or job.status not in {"queued", "interrupted", "running"}
                or event.get("input_digest") != job.input_digest
                or event.get("script_id") != script.id or event.get("plan_id") != task.plan_id
                or task.confirmed_script_id != script.id or script.selection_status != "confirmed"
                or plan.creation_task_id != task.id or plan.status != "confirmed"
                or (plan.selected_voice is None and (
                    plan.presenter_share_target != 0
                    or task.narration_cache_key is None
                    or (recording := self.repository.get_narration_artifact(
                        task.workspace_id, task.narration_cache_key
                    )) is None or recording.source_kind != "recorded"
                ))
                or revision_digest(script, plan) != job.input_digest):
            return "stale"
        return "ready"

    def check_current(self, request: WorkRequest, conn: sqlite3.Connection):
        if request.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.check_current(request, conn)
        if (request.owner_type != DEVICE_OWNER_TYPE
                or request.source_refs or not request.member_id):
            return "unauthorized"
        try:
            job, task, event, script, plan = self._facts(conn, request.workspace_id, request.owner_id)
        except KeyError:
            return "stale"
        expected_class = "preview_dependency" if job.kind == NARRATION_EDIT_KIND else "normal_make"
        if request.work_class != expected_class:
            return "unauthorized"
        if request.input_digest != job.input_digest:
            return "stale"
        stage = request.unit_key.split(":", 1)[0]
        verdict = self._check_facts(job, task, event, script, plan, stage, request.member_id)
        if verdict != "ready":
            return verdict
        try:
            original = CreationExecutionActor.from_event(job.workspace_id, event)
        except (ValueError, KeyError, TypeError, AttributeError):
            return "unauthorized"
        if self.verify_identity is not None:
            identity = self.verify_identity(original)
            if identity != "ready":
                return identity
        try:
            if job.kind == NARRATION_EDIT_KIND:
                key, resources = self.narration_edit_facts(job, task, event, plan)
            elif job.kind == PERSON_EDIT_KIND:
                key, resources = self.edit_facts(job, task, event, plan)
            elif stage == "render":
                key, resources = self.render_facts(conn, task, plan, request=request)
            else:
                key, resources = self.stage_facts(task, script, plan, stage)
        except PermissionError:
            return "unauthorized"
        except (KeyError, StopIteration, OSError, ValueError):
            return "dependency"
        return "ready" if request.unit_key == key and request.resources == resources else "stale"

    def can_manage(self, request: WorkRequest, actor: object) -> bool:
        return isinstance(actor, CreationExecutionActor) and (actor.workspace_id, actor.member_id) == (request.workspace_id, request.member_id)

    def can_wake(self, request: WorkRequest, actor: object) -> bool:
        return False

    def can_view_detail(self, request: WorkRequest, actor: object) -> bool:
        return self.can_manage(request, actor) and (
            self.verify_identity is None or self.verify_identity(actor) == "ready")

    def verify_retry(self, request: WorkRequest, actor: object, reason: str, conn: sqlite3.Connection) -> str:
        if request.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.verify_retry(request, actor, reason, conn)
        raise PermissionError("请通过已确认任务的继续制作入口重试")


def submit_creation_stage(execution: "CreationExecution", scheduler: DeviceScheduler,
                          job: JobRun, actor: CreationExecutionActor) -> str | None:
    """Queue exactly the next persisted stage; repeated submissions reuse one request."""
    if job.status not in {"queued", "interrupted"}:
        return None
    intent = next_creation_intent(execution.repository, scheduler, job)
    return scheduler.submit(intent, actor).id if intent is not None else None


def upstream_stage_valid(repository, task, stage):
    if stage == "narration":
        if not task.narration_cache_key:
            return False
        artifact = repository.get_narration_artifact(task.workspace_id, task.narration_cache_key)
        return (artifact is not None and artifact.task_id == task.id
                and artifact.script_id == task.confirmed_script_id
                and Path(artifact.audio_path).is_file() and Path(artifact.audio_path).stat().st_size > 0)
    if not task.montage_plan_id:
        return False
    try:
        plan = repository.get_creation_task_montage_plan(task.workspace_id, task.id)
    except KeyError:
        return False
    return plan.get("script_id") == task.confirmed_script_id and plan.get("status") in {"ready", "rendering", "rendered", "failed"}


def montage_waiting_for_confirmation(repository, task):
    if not task.montage_plan_id:
        return False
    try:
        plan = repository.get_creation_task_montage_plan(task.workspace_id, task.id)
    except KeyError:
        return False
    return (plan.get("script_id") == task.confirmed_script_id
            and plan.get("status") == "waiting_for_user")


def next_creation_intent(repository, scheduler: DeviceScheduler, job: JobRun):
    task = repository.get_creation_task(job.workspace_id, job.task_id)
    owner = scheduler.owners
    if hasattr(owner, "registrations"):
        owner = owner.registrations[DEVICE_OWNER_TYPE].owner
    plan = repository.get_creation_plan(job.workspace_id, task.plan_id)
    script = repository.get_creation_task_script(job.workspace_id, task.confirmed_script_id)
    if plan.presenter_share_target and (owner.presenter_budget is None or owner.presenter_cpu_render_limits is None):
        raise ValueError("人物制作及人物组合 CPU 成片专属预算尚未标定")
    if not upstream_stage_valid(repository, task, "narration"):
        stage = "narration"
    elif plan.presenter_share_target and not owner.person_valid(task):
        stage = "person_render"
    elif montage_waiting_for_confirmation(repository, task):
        return None  # User confirmation is a business gate, not another work unit.
    elif not upstream_stage_valid(repository, task, "montage_plan"):
        stage = "montage_plan"
    else:
        stage = "render"
    if stage == "render":
        with repository._connect() as conn:
            stage, _resources = owner.render_facts(conn, task, plan)
    elif stage == "person_render" or getattr(owner, stage.removesuffix("_plan") + "_budget", None) is not None:
        stage, _resources = owner.stage_facts(task, script, plan, stage)
    return SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id,
                            job.input_digest, stage, SchedulePolicy())


class CreationJobUnitExecutor:
    """Execute one granted creation stage and checkpoint it with the same permit."""

    def __init__(self, repository, *, render_action: Callable[[str, str, CpuRenderGrantContext], object],
                 render_is_valid: Callable[[str, str, CpuRenderGrantContext], bool],
                 stage_actions: dict | None = None, task_media=None) -> None:
        self.repository = repository
        self.render_action = render_action
        self.render_is_valid = render_is_valid
        self.stage_actions = stage_actions or {}
        self.task_media = task_media
        self._cancellations: dict[str, threading.Event] = {}
        self._cancellation_lock = threading.Lock()
        self.last_resource_refusal: HostResourceRefusal | None = None

    def request_cancel(self, request_id: str) -> None:
        with self._cancellation_lock:
            event = self._cancellations.get(request_id)
        if event is not None:
            event.set()

    def _defer_permission_loss(self, grant: Grant, scheduler: DeviceScheduler) -> bool:
        owner = scheduler.owners
        if hasattr(owner, "registrations"):
            owner = owner.registrations[DEVICE_OWNER_TYPE].owner
        if owner.verify_identity is None:
            return False  # Existing diagnostic owners retain their explicit recovery contract.
        with self.repository._connect() as conn:
            conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                         (grant.workspace_id, grant.owner_id))
        # Defer proves group/resource release even when the owner is revoked or
        # temporarily unknown. Eligibility rechecks its precise current verdict.
        scheduler.defer_unit(grant, reason="transient_failure",
                             retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=1))
        return True

    def _checkpoint(self, conn: sqlite3.Connection, grant: Grant, *, status: str,
                    output: str | None = None, reason: str | None = None, stage: str = "render") -> str:
        row = conn.execute("SELECT task_id,status FROM job_runs WHERE workspace_id=? AND id=? AND kind=?",
                           (grant.workspace_id, grant.owner_id, KIND)).fetchone()
        if row is None or row["status"] not in {"running", "cancelled"}:
            raise ValueError("外层制作作业不在可提交状态")
        if row["status"] == "cancelled" and status != "cancelled":
            raise ValueError("已停止的制作作业不能写入成功或失败")
        if status == "succeeded" and stage == "render":
            task_row = conn.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                    (grant.workspace_id, row["task_id"])).fetchone()
            plan_row = conn.execute("SELECT payload FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=?",
                                    (grant.workspace_id, row["task_id"])).fetchone()
            if task_row is None or plan_row is None:
                raise ValueError("成片检查点缺少当前任务或故事板")
            task_payload = json.loads(task_row["payload"])
            plan_payload = json.loads(plan_row["payload"])
            version_id = task_payload.get("current_timeline_version_id")
            ref = plan_payload.get("output_path")
            version_row = conn.execute("SELECT snapshot_json FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=?",
                                       (grant.workspace_id, row["task_id"], version_id)).fetchone()
            if (not isinstance(version_id, str) or not isinstance(ref, str)
                    or plan_payload.get("status") != "rendered" or version_row is None
                    or ref not in json.loads(version_row["snapshot_json"]).get("output_refs", [])
                    or output != f"{version_id}:{ref}"):
                raise ValueError("成片检查点与当前版本产物不一致")
        now = datetime.now(timezone.utc).isoformat()
        if stage in {"narration", "montage_plan"} and status in {"failed", "cancelled"}:
            task_row = conn.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                    (grant.workspace_id, row["task_id"])).fetchone()
            payload = json.loads(task_row["payload"])
            task_stage = "narration" if stage == "narration" else "montage"
            if payload["stage"] == task_stage:
                if status == "failed" and payload["stage_status"] != "failed":
                    self.repository.fail_creation_task_stage(
                        grant.workspace_id, row["task_id"], stage=task_stage,
                        reason=reason, impact="当前制作未完成，上游有效产物保留。",
                        repair_action="修复当前阶段后点击继续制作。", retryable=True, connection=conn,
                    )
                elif status == "cancelled" and payload["stage_status"] == "running":
                    payload.update(stage_status="not_started", status="created",
                                   recovery_point=task_stage, stage_reason="user_cancelled",
                                   stage_impact="当前阶段已停止，上游有效产物保留。",
                                   stage_repair_action="需要时点击继续制作。", stage_retryable=True,
                                   updated_at=now)
                    conn.execute("UPDATE creation_tasks SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
                                 (json.dumps(payload, ensure_ascii=False), now,
                                  grant.workspace_id, row["task_id"]))
        if row["status"] == "running":
            changed = conn.execute(
                "UPDATE job_runs SET status=?, progress=CASE WHEN ?='succeeded' THEN 100 WHEN ?='queued' THEN ? ELSE progress END, "
                "failure_reason=?, impact=?, repair_action=?, finished_at=?, updated_at=? "
                "WHERE workspace_id=? AND id=? AND status='running'",
                (status, status, status, 33 if stage == "narration" else 66, reason,
                 None if status in {"queued", "succeeded"} else "当前制作未完成，上游产物保留。",
                 None if status in {"queued", "succeeded"} else "检查首个失败原因后继续制作。",
                 None if status == "queued" else now, now, grant.workspace_id, grant.owner_id),
            ).rowcount
            if changed != 1:
                raise ValueError("外层制作作业状态已变化")
        event_id = f"event_{grant.owner_id}_{stage}_{status}"
        conn.execute(
            "INSERT OR IGNORE INTO task_events "
            "(id,workspace_id,task_id,job_id,event_type,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, grant.workspace_id, row["task_id"], grant.owner_id,
             "creation_execution_stage",
             json.dumps({"stage": stage, "status": "completed" if status in {"queued", "succeeded"} else status,
                         **({"output": output} if output else {}),
                         **({"reason": reason} if reason else {})}, ensure_ascii=False), now),
        )
        return event_id

    def _execute_upstream(self, grant, scheduler, stage):
        from app.granted_subprocess import GrantedCommandCancelled
        if stage not in self.stage_actions:
            raise PermissionError("该制作阶段没有受许可执行者")
        with self.repository._connect() as conn:
            row = conn.execute("SELECT resources_json FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
        if row is None or permit is None or not permit["granted_at_utc"]:
            raise PermissionError("制作阶段缺少当前设备许可")
        resources = json.loads(row["resources_json"])
        if resources.get("kind") != f"creation_{stage}" or "timeout_seconds" not in resources:
            raise PermissionError("制作阶段资源预算未标定")
        elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds())
        cancellation = threading.Event()
        context = CreationStageGrantContext(scheduler, grant,
            time.monotonic() + resources["timeout_seconds"] - elapsed,
            resources["max_output_bytes"], resources.get("threads", 1), cancellation)
        with self._cancellation_lock:
            self._cancellations[grant.request_id] = cancellation
        job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
        try:
            context.check()
            if job.status == "running":
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                                 (job.workspace_id, job.id))
            job = self.repository.claim_job_run(job.workspace_id, job.id)
            if stage == "person_render":
                self.repository.append_task_event(TaskEvent(
                    id=f"event_{job.id}_person_render_running_{grant.request_id}_{grant.generation}",
                    workspace_id=job.workspace_id, task_id=job.task_id, job_id=job.id,
                    event_type="creation_execution_stage", payload={"stage": stage, "status": "running"},
                    created_at=datetime.now(timezone.utc)))
            current_task = self.repository.get_creation_task(job.workspace_id, job.task_id)
            owner = scheduler.owners
            if hasattr(owner, "registrations"):
                owner = owner.registrations[DEVICE_OWNER_TYPE].owner
            def valid(current):
                return (owner.person_valid(current) if stage == "person_render"
                        else upstream_stage_valid(self.repository, current, stage)
                        or (stage == "montage_plan" and montage_waiting_for_confirmation(self.repository, current)))
            if not valid(current_task):
                self.stage_actions[stage](job.workspace_id, job.task_id, context)
            context.check()
            task = self.repository.get_creation_task(job.workspace_id, job.task_id)
            if not valid(task):
                raise ValueError("制作阶段未产生匹配当前文案的可用结果")
            original = CreationExecutionActor.from_event(job.workspace_id,
                self.repository.get_task_event(job.workspace_id, f"event_{job.id}_start").payload)
            next_intent = next_creation_intent(self.repository, scheduler, job)
            output = (task.narration_cache_key if stage == "narration" else
                      ",".join(item.id for item in self.repository.list_person_renders(task.workspace_id, task.id)
                               if item.status == "ready") if stage == "person_render" else task.montage_plan_id)
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=lambda conn, current: self._checkpoint(conn, current,
                    status="queued", output=output, stage=stage,
                    reason="waiting_for_user" if next_intent is None else None),
                next_intent=next_intent, actor=original)
        except HostResourceRefusal as exc:
            self.last_resource_refusal = exc
            if not exc.recoverable_pressure:
                raise  # Unknown measurements and declaration faults still stop the driver.
            diagnostics = getattr(exc, "command_diagnostics", None)
            natural_exit = diagnostics.get("before_termination_returncode") if diagnostics else None
            if natural_exit is not None and natural_exit != 0:
                # A worker's natural error exit must not be renamed resource waiting.
                reason = diagnostics["stderr"].decode("utf-8", errors="replace")[-4000:]
                scheduler.fail_unit(grant, reason=f"creation_{stage}_failed",
                    failure_write=lambda conn, current: self._checkpoint(conn, current,
                        status="failed", reason=reason or f"worker exit {natural_exit}", stage=stage))
            elif self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled" or cancellation.is_set():
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: self._checkpoint(conn, current,
                        status="cancelled", stage=stage), cancelled=True)
            else:
                # defer_unit itself proves all registered groups and the host lock
                # released; do not mutate the job before that proof succeeds.
                scheduler.defer_unit(grant, reason="resource_pressure",
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30))
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                                 (job.workspace_id, job.id))
                    task_row = conn.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                            (job.workspace_id, job.task_id)).fetchone()
                    payload = json.loads(task_row["payload"])
                    if payload["stage"] == stage and payload["stage_status"] == "running":
                        # The aborted segment has no completed checkpoint. Reuse
                        # the existing start state; sealed upstream stays intact.
                        payload.update(stage_status="not_started", stage_reason="resource_pressure",
                                       recovery_point=stage)
                        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                                     (json.dumps(payload, ensure_ascii=False), job.workspace_id, job.task_id))
                    conn.execute("INSERT INTO task_events (id,workspace_id,task_id,job_id,event_type,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
                        (f"event_{job.id}_pressure_{grant.generation}", job.workspace_id, job.task_id, job.id,
                         "creation_execution_stage", json.dumps({"stage": stage, "status": "interrupted",
                         "reason": "resource_pressure", "decision": asdict(exc.decision)}, ensure_ascii=False),
                         datetime.now(timezone.utc).isoformat()))
        except RecoveryBlocked:
            raise
        except GrantedCommandCancelled:
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=lambda conn, current: self._checkpoint(conn, current,
                    status="cancelled", reason="user_cancelled", stage=stage), cancelled=True)
        except Exception as exc:
            if self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: self._checkpoint(conn, current,
                        status="cancelled", stage=stage), cancelled=True)
            elif isinstance(exc, PermissionError):
                if not self._defer_permission_loss(grant, scheduler):
                    raise
            else:
                reason = str(exc)
                scheduler.fail_unit(grant, reason=f"creation_{stage}_failed",
                    failure_write=lambda conn, current: self._checkpoint(conn, current,
                        status="failed", reason=reason, stage=stage))
        finally:
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)

    def _person_edit_checkpoint(self, conn, grant, status, *, commit=None, reason=None):
        row = conn.execute("SELECT task_id,status FROM job_runs WHERE workspace_id=? AND id=? AND kind=?",
                           (grant.workspace_id, grant.owner_id, PERSON_EDIT_KIND)).fetchone()
        if row is None or row["status"] not in {"running", "cancelled"}:
            raise ValueError("人物编辑作业不在可提交状态")
        if row["status"] == "cancelled" and status != "cancelled":
            raise ValueError("已取消的人物编辑不能提交版本")
        operation = commit(conn) if status == "succeeded" else None
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE job_runs SET status=?,progress=?,failure_reason=?,impact=?,repair_action=?,"
                     "finished_at=?,updated_at=? WHERE workspace_id=? AND id=?",
                     (status, 100 if status == "succeeded" else 0, reason,
                      None if status == "succeeded" else "目标镜头和当前时间线保持不变。",
                      None if status == "succeeded" else "修复原因后重新预览人物修改。",
                      now, now, grant.workspace_id, grant.owner_id))
        event_id = f"event_{grant.owner_id}_person_edit_{status}"
        payload = {"status": status}
        if operation is not None:
            payload.update(operation_id=operation.id, result_version_id=operation.result_version_id)
        if reason:
            payload["reason"] = reason
        conn.execute("INSERT INTO task_events (id,workspace_id,task_id,job_id,event_type,payload_json,created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (event_id, grant.workspace_id, row["task_id"], grant.owner_id,
                     "person_edit_finished", json.dumps(payload, ensure_ascii=False), now))
        return event_id

    def _execute_person_edit(self, grant, scheduler):
        from app.granted_subprocess import GrantedCommandCancelled
        token = set_workspace_context(grant.workspace_id)
        cancellation = threading.Event()
        with self._cancellation_lock:
            self._cancellations[grant.request_id] = cancellation
        try:
            job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
            with self.repository._connect() as conn:
                row = conn.execute("SELECT resources_json FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
                permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
            resources = json.loads(row["resources_json"])
            if resources.get("kind") != "creation_person_render" or not permit["granted_at_utc"]:
                raise PermissionError("人物编辑缺少专属资源许可")
            elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds())
            context = CreationStageGrantContext(scheduler, grant,
                time.monotonic() + resources["timeout_seconds"] - elapsed,
                resources["max_output_bytes"], resources["threads"], cancellation)
            if job.status == "running":
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                                 (job.workspace_id, job.id))
            if job.status != "cancelled":
                job = self.repository.claim_job_run(job.workspace_id, job.id)
            context.check()
            event = self.repository.get_task_event(job.workspace_id, f"event_{job.id}_start")
            commit = self.stage_actions["person_edit"](job.workspace_id, job.task_id, context, event.payload["edit_proposal_id"])
            context.check()
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=lambda conn, current: self._person_edit_checkpoint(conn, current, "succeeded", commit=commit))
        except RecoveryBlocked:
            raise  # Unknown child/permit ownership must retain the original recovery gate.
        except Exception as exc:
            cancelled = (isinstance(exc, GrantedCommandCancelled) or cancellation.is_set()
                         or self.repository.get_job_run(grant.workspace_id, grant.owner_id).status == "cancelled")
            if cancelled:
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: self._person_edit_checkpoint(conn, current, "cancelled", reason="user_cancelled"),
                    cancelled=True)
            else:
                reason = str(exc)
                scheduler.fail_unit(grant, reason="person_edit_failed",
                    failure_write=lambda conn, current: self._person_edit_checkpoint(conn, current, "failed", reason=reason))
        finally:
            reset_workspace_context(token)
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)

    def _narration_edit_checkpoint(self, conn, grant, status, *, commit=None, reason=None):
        row = conn.execute("SELECT task_id,status FROM job_runs WHERE workspace_id=? AND id=? AND kind=?",
                           (grant.workspace_id, grant.owner_id, NARRATION_EDIT_KIND)).fetchone()
        if row is None or row["status"] not in {"running", "cancelled"}:
            raise ValueError("配音编辑作业不在可提交状态")
        if row["status"] == "cancelled" and status != "cancelled":
            raise ValueError("已取消的配音编辑不能提交版本")
        operation = commit(conn) if status == "succeeded" else None
        if operation is not None:
            start = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?",
                                 (grant.workspace_id, grant.owner_id, f"event_{grant.owner_id}_start")).fetchone()
            current = conn.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                   (grant.workspace_id, row["task_id"])).fetchone()
            persisted = conn.execute("SELECT proposal_id,base_version_id,result_version_id,status FROM edit_operations "
                                     "WHERE workspace_id=? AND task_id=? AND id=? AND operation_type='RegenerateNarrationSegment'",
                                     (grant.workspace_id, row["task_id"], operation.id)).fetchone()
            if (operation.workspace_id != grant.workspace_id or operation.task_id != row["task_id"]
                    or operation.operation_type != "RegenerateNarrationSegment" or operation.status != "applied"
                    or not operation.result_version_id
                    or operation.proposal_id != json.loads(start["payload_json"])["edit_proposal_id"]
                    or persisted is None
                    or tuple(persisted) != (operation.proposal_id, operation.base_version_id, operation.result_version_id, "applied")
                    or json.loads(current["payload"])["current_timeline_version_id"] != operation.result_version_id):
                raise ValueError("配音编辑检查点未保存当前精确版本")
        elif status == "succeeded":
            raise ValueError("配音编辑缺少已提交的版本操作")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE job_runs SET status=?,progress=?,failure_reason=?,impact=?,repair_action=?,"
                     "finished_at=?,updated_at=? WHERE workspace_id=? AND id=?",
                     (status, 100 if status == "succeeded" else 0, reason,
                      None if status == "succeeded" else "当前时间线与原配音保持不变。",
                      None if status == "succeeded" else "修复原因后重新预览配音修改。",
                      now, now, grant.workspace_id, grant.owner_id))
        event_id = f"event_{grant.owner_id}_narration_edit_{status}"
        payload = {"status": status, "request_id": grant.request_id,
                   "unit_key": grant.unit_key, "attempt_number": grant.attempt_number}
        if operation is not None:
            payload.update(operation_id=operation.id, result_version_id=operation.result_version_id)
        if reason:
            payload["reason"] = reason
        conn.execute("INSERT INTO task_events (id,workspace_id,task_id,job_id,event_type,payload_json,created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (event_id, grant.workspace_id, row["task_id"], grant.owner_id,
                     "narration_edit_finished", json.dumps(payload, ensure_ascii=False), now))
        return event_id

    def _execute_narration_edit(self, grant, scheduler):
        from app.granted_subprocess import GrantedCommandCancelled
        scheduler.assert_grant(grant)
        job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
        if job.kind != NARRATION_EDIT_KIND:
            raise PermissionError("局部配音许可不属于配音编辑作业")
        token = set_workspace_context(grant.workspace_id)
        cancellation = threading.Event()
        with self._cancellation_lock:
            self._cancellations[grant.request_id] = cancellation
        try:
            with self.repository._connect() as conn:
                row = conn.execute("SELECT resources_json FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
                permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
            resources = json.loads(row["resources_json"])
            if resources.get("kind") != "creation_narration" or not permit["granted_at_utc"]:
                raise PermissionError("配音编辑缺少专属配音资源许可")
            elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds())
            context = CreationStageGrantContext(scheduler, grant,
                time.monotonic() + resources["timeout_seconds"] - elapsed,
                resources["max_output_bytes"], resources["threads"], cancellation)
            if job.status == "running":
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                                 (job.workspace_id, job.id))
            job = self.repository.claim_job_run(job.workspace_id, job.id)
            context.check()
            event = self.repository.get_task_event(job.workspace_id, f"event_{job.id}_start")
            commit = self.stage_actions["narration_edit"](job.workspace_id, job.task_id, context, event.payload["edit_proposal_id"])
            context.check()
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=lambda conn, current: self._narration_edit_checkpoint(conn, current, "succeeded", commit=commit))
        except RecoveryBlocked:
            raise
        except Exception as exc:
            cancelled = (isinstance(exc, GrantedCommandCancelled) or cancellation.is_set()
                         or self.repository.get_job_run(grant.workspace_id, grant.owner_id).status == "cancelled")
            if cancelled:
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=lambda conn, current: self._narration_edit_checkpoint(conn, current, "cancelled", reason="user_cancelled"),
                    cancelled=True)
            else:
                reason = str(exc)
                scheduler.fail_unit(grant, reason="narration_edit_failed",
                    failure_write=lambda conn, current: self._narration_edit_checkpoint(conn, current, "failed", reason=reason))
        finally:
            reset_workspace_context(token)
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)

    def execute(self, grant: Grant, scheduler: DeviceScheduler) -> None:
        if grant.owner_type == DEVICE_OWNER_TYPE and grant.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.execute(grant, scheduler, self)
        from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout

        if grant.owner_type == DEVICE_OWNER_TYPE and grant.unit_key.startswith("narration_edit:"):
            return self._execute_narration_edit(grant, scheduler)
        if grant.owner_type == DEVICE_OWNER_TYPE and grant.unit_key.startswith("person_edit:"):
            return self._execute_person_edit(grant, scheduler)
        if grant.owner_type == DEVICE_OWNER_TYPE and grant.unit_key.split(":", 1)[0] in DEVICE_STAGES[:-1]:
            return self._execute_upstream(grant, scheduler, grant.unit_key.split(":", 1)[0])
        if grant.owner_type != DEVICE_OWNER_TYPE or not grant.unit_key.startswith("render:"):
            raise PermissionError("制作执行器只接受已确认的 CPU 成片单元")
        try:
            scheduler.assert_grant(grant)
        except PermissionError:
            if self.repository.get_job_run(grant.workspace_id, grant.owner_id).status != "cancelled":
                if self._defer_permission_loss(grant, scheduler):
                    return
                raise
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                    checkpoint_write=lambda conn, current: self._checkpoint(
                                        conn, current, status="cancelled", reason="user_cancelled"),
                                    cancelled=True)
            return
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT resources_json FROM device_work_requests WHERE id=? AND workspace_id=? AND owner_id=? "
                "AND input_digest=? AND unit_key=? AND state='granted'",
                (grant.request_id, grant.workspace_id, grant.owner_id, grant.input_digest, grant.unit_key),
            ).fetchone()
            permit = conn.execute("SELECT granted_at_utc FROM device_permit WHERE id=1").fetchone()
        if row is None or permit is None or not permit["granted_at_utc"]:
            raise PermissionError("CPU 成片许可事实不完整")
        resources = json.loads(row["resources_json"])
        if resources.get("kind") not in {"creation_render", "creation_presenter_render"} or resources.get("execution_mode") != "cpu_ffmpeg":
            raise PermissionError("CPU 成片资源事实不匹配")
        elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit["granted_at_utc"])).total_seconds())
        deadline = time.monotonic() + float(resources["timeout_seconds"]) - elapsed
        cancellation = threading.Event()
        with self._cancellation_lock:
            self._cancellations[grant.request_id] = cancellation
        try:
            job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
            if job.status == "running":
                # Host recovery has already proved the prior child tree released.
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? "
                                 "AND kind=? AND status='running'",
                                 (grant.workspace_id, grant.owner_id, KIND))
            try:
                job = self.repository.claim_job_run(grant.workspace_id, grant.owner_id)
            except ValueError:
                if self.repository.get_job_run(grant.workspace_id, grant.owner_id).status != "cancelled":
                    raise
                # No child has been launched by this executor yet. The scheduler
                # still verifies that every previously registered child exited.
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="cancelled", reason="user_cancelled"),
                                        cancelled=True)
                return
            context = CpuRenderGrantContext(
                scheduler, grant, deadline, int(resources["max_output_bytes"]),
                int(resources["threads"]), cancellation,
            )
            try:
                if deadline <= time.monotonic():
                    raise GrantedCommandTimeout("CPU 成片阶段已超出总时限")
                admit_resource("creation_render", {
                    "source_duration_s": float(resources["duration_seconds"]),
                    "input_count": int(resources["clip_count"]),
                    "cpu_threads": int(resources["threads"]),
                    "wall_clock_s": deadline - time.monotonic(),
                }, capacity=sample_host_capacity())
                task = self.repository.get_creation_task(job.workspace_id, job.task_id)
                version = (self.repository.get_timeline_version(job.workspace_id, task.current_timeline_version_id)
                           if task.current_timeline_version_id else None)
                # A prior attempt may have published this request's output before
                # pressure interrupted validation. Revalidate it under the new
                # grant; unrelated/older output never skips the requested render.
                own_output = (version is not None
                              and version.snapshot.get("creation_render_request_id") == grant.request_id)
                if own_output:
                    if not self.render_is_valid(job.workspace_id, job.task_id, context):
                        raise ValueError("本次已提交成片未通过当前许可内校验，未重复渲染")
                else:
                    self.render_action(job.workspace_id, job.task_id, context)
                    if not cancellation.is_set() and self.repository.get_job_run(job.workspace_id, job.id).status != "cancelled":
                        if not self.render_is_valid(job.workspace_id, job.task_id, context):
                            raise ValueError("成片产物未通过当前许可内的完整性与媒体校验")
            except GrantedCommandCancelled:
                cancellation.set()
            except HostResourceRefusal as exc:
                self.last_resource_refusal = exc
                if not exc.recoverable_pressure:
                    raise
                diagnostics = getattr(exc, "command_diagnostics", None)
                natural_exit = diagnostics.get("before_termination_returncode") if diagnostics else None
                if natural_exit is not None and natural_exit != 0:
                    raise  # A worker error is not a recoverable-pressure retry.
                if cancellation.is_set() or self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                    cancellation.set()
                else:
                    # This existing gate proves process/resource release before
                    # freeing the permit. Keep the published version untouched.
                    scheduler.defer_unit(grant, reason="resource_pressure",
                        retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30))
                    with self.repository._connect() as conn:
                        conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'",
                                     (job.workspace_id, job.id))
                        self.repository.append_task_event(TaskEvent(
                            id=f"event_{job.id}_pressure_{grant.generation}", workspace_id=job.workspace_id,
                            task_id=job.task_id, job_id=job.id, event_type="creation_execution_stage",
                            payload={"stage": "render", "status": "interrupted", "reason": "resource_pressure",
                                     "decision": asdict(exc.decision)}), connection=conn)
                    return
            except RecoveryBlocked:
                raise
            except Exception as exc:
                if cancellation.is_set() or self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                    cancellation.set()
                else:
                    if isinstance(exc, PermissionError) and self._defer_permission_loss(grant, scheduler):
                        return
                    reason = str(exc).strip() or type(exc).__name__
                    try:
                        scheduler.fail_unit(grant, reason="creation_render_failed",
                                            failure_write=lambda conn, current: self._checkpoint(
                                                conn, current, status="failed", reason=reason))
                    except ValueError:
                        if self.repository.get_job_run(job.workspace_id, job.id).status != "cancelled":
                            raise
                        scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                                checkpoint_write=lambda conn, current: self._checkpoint(
                                                    conn, current, status="cancelled", reason="user_cancelled"),
                                                cancelled=True)
                    return
            if cancellation.is_set() or self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="cancelled", reason="user_cancelled"),
                                        cancelled=True)
                return
            task = self.repository.get_creation_task(job.workspace_id, job.task_id)
            plan = self.repository.get_creation_task_montage_plan(job.workspace_id, job.task_id)
            if not task.current_timeline_version_id or not plan.get("output_path"):
                raise ValueError("成片未建立可追溯的当前版本")
            output = f"{task.current_timeline_version_id}:{plan['output_path']}"
            try:
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="succeeded", output=output))
            except PermissionError:
                if self.repository.get_job_run(job.workspace_id, job.id).status != "cancelled":
                    if self._defer_permission_loss(grant, scheduler):
                        return
                    raise
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                        checkpoint_write=lambda conn, current: self._checkpoint(
                                            conn, current, status="cancelled", reason="user_cancelled"),
                                        cancelled=True)
        finally:
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)


class ExecutionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements_confirmed: bool


def revision_digest(script: CreationTaskScript, plan: CreationPlan) -> str:
    """Bind a start intent to the text and making requirements it approved."""
    value = {
        "script": asdict(script),
        "plan": asdict(plan),
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class CreationExecution:
    def __init__(
        self,
        repository,
        *,
        run_narration: Callable[[str, str], object],
        run_montage_plan: Callable[[str, str], object],
        run_render: Callable[[str, str], object],
        render_is_valid: Callable[[str, str], bool],
    ) -> None:
        self.repository = repository
        self.runtime = TaskRuntime(repository)
        self.actions = {
            "narration": run_narration,
            "montage_plan": run_montage_plan,
            "render": run_render,
        }
        self.render_is_valid = render_is_valid

    def validate_input(
        self, workspace_id: str, task: CreationTask, script_id: str, digest: str
    ) -> tuple[CreationTaskScript, CreationPlan]:
        if task.confirmed_script_id != script_id:
            raise ValueError("请先确认当前文案；方案确认不能代替文案确认")
        script = self.repository.get_creation_task_script(workspace_id, script_id)
        if script.task_id != task.id or script.selection_status != "confirmed":
            raise ValueError("当前文案不是已确认的任务文案")
        plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        if plan.creation_task_id != task.id or plan.status != "confirmed":
            raise ValueError("当前制作要求尚未确认")
        if revision_digest(script, plan) != digest:
            raise ValueError("文案或制作要求已变化，请重新检查并确认")
        return script, plan

    def start(self, workspace_id: str, task_id: str, script_id: str, digest: str,
              *, member_id: str | None = None, actor: CreationExecutionActor | None = None) -> JobRun:
        if actor is not None:
            if actor.workspace_id != workspace_id or (member_id is not None and actor.member_id != member_id):
                raise PermissionError("制作身份与工作区不匹配")
            member_id = actor.member_id
        task = self.repository.get_creation_task(workspace_id, task_id)
        _, plan = self.validate_input(workspace_id, task, script_id, digest)
        narration = (self.repository.get_narration_artifact(workspace_id, task.narration_cache_key)
                     if task.narration_cache_key else None)
        if (plan.selected_voice is None
                and (plan.presenter_share_target != 0 or narration is None
                     or narration.source_kind != "recorded")):
            raise ValueError("请先为本次制作选择已授权声音")
        key = f"creation-execution:{task_id}:{digest}"
        run = self.repository.create_job_run(
            JobRun(
                id=f"job_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                kind=KIND,
                idempotency_key=key,
                input_digest=digest,
            )
        )
        if not self._has_start_event(workspace_id, run.id):
            self.repository.append_task_event(
                TaskEvent(
                    id=f"event_{run.id}_start",
                    workspace_id=workspace_id,
                    task_id=task_id,
                    job_id=run.id,
                    event_type="creation_execution_started",
                    payload={"script_id": script_id, "plan_id": task.plan_id, "input_digest": digest,
                             **(actor.identity_payload() if actor is not None else
                                {"member_id": member_id} if member_id is not None else {})},
                    created_at=run.created_at,
                )
            )
        if member_id is not None:
            start_event = self.repository.get_task_event(workspace_id, f"event_{run.id}_start")
            if start_event.payload.get("member_id") != member_id:
                raise PermissionError("已有制作任务的创建者身份与当前成员不同")
            if actor is not None and CreationExecutionActor.from_event(workspace_id, start_event.payload) != actor:
                raise PermissionError("已有制作任务的原设备账号身份不同")
        return run

    def _has_start_event(self, workspace_id: str, job_id: str) -> bool:
        try:
            self.repository.get_task_event(workspace_id, f"event_{job_id}_start")
        except KeyError:
            return False
        return True

    def retry(self, workspace_id: str, task_id: str, *, member_id: str | None = None) -> JobRun:
        latest = self.latest(workspace_id, task_id)
        if latest is None:
            raise ValueError("请先明确开始制作")
        event = self.repository.get_task_event(workspace_id, f"event_{latest.id}_start")
        if member_id is not None and event.payload.get("member_id") != member_id:
            raise PermissionError("当前成员不能继续其他成员创建的制作任务")
        if latest.status in {"queued", "running"}:
            return latest
        if latest.status == "succeeded" and self.render_is_valid(workspace_id, task_id):
            return latest
        if latest.status == "interrupted":
            return latest
        script_id = str(event.payload["script_id"])
        task = self.repository.get_creation_task(workspace_id, task_id)
        self.validate_input(workspace_id, task, script_id, latest.input_digest)
        same_input = [
            job for job in self.jobs(workspace_id, task_id)
            if job.input_digest == latest.input_digest
        ]
        retry_number = len(same_input)
        run = self.repository.create_job_run(
            JobRun(
                id=f"job_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                kind=KIND,
                idempotency_key=f"creation-execution:{task_id}:{latest.input_digest}:retry:{retry_number}",
                input_digest=latest.input_digest,
            )
        )
        if not self._has_start_event(workspace_id, run.id):
            self.repository.append_task_event(
                TaskEvent(
                    id=f"event_{run.id}_start", workspace_id=workspace_id,
                    task_id=task_id, job_id=run.id,
                    event_type="creation_execution_resumed",
                    payload=dict(event.payload),
                    created_at=run.created_at,
                )
            )
        if member_id is not None:
            resume_event = self.repository.get_task_event(workspace_id, f"event_{run.id}_start")
            if resume_event.payload.get("member_id") != member_id:
                raise PermissionError("已有继续任务的创建者身份与当前成员不同")
        return run

    def jobs(self, workspace_id: str, task_id: str) -> list[JobRun]:
        return [job for job in self.repository.list_job_runs(workspace_id, task_id) if job.kind == KIND]

    def latest(self, workspace_id: str, task_id: str) -> JobRun | None:
        jobs = self.jobs(workspace_id, task_id)
        return jobs[-1] if jobs else None

    def execute(self, workspace_id: str, job_id: str) -> JobRun:
        job = self.repository.get_job_run(workspace_id, job_id)
        if job.kind != KIND:
            raise ValueError("不是连续制作作业")
        token = set_workspace_context(workspace_id)
        try:
            return self.runtime.resume(workspace_id, job_id, self._work)
        finally:
            reset_workspace_context(token)

    def cancel(self, workspace_id: str, task_id: str, *, member_id: str | None = None) -> JobRun:
        job = self.latest(workspace_id, task_id)
        if job is None:
            raise ValueError("尚未开始制作")
        if member_id is not None:
            event = self.repository.get_task_event(workspace_id, f"event_{job.id}_start")
            if event.payload.get("member_id") != member_id:
                raise PermissionError("当前成员不能停止其他成员创建的制作任务")
        return self.runtime.cancel(
            workspace_id, job.id,
            impact="当前阶段在下一个安全边界停止；此前有效产物保留。",
            repair_action="需要时点击继续制作。",
        )

    def _work(self, context: JobContext) -> JobResult:
        job = self.repository.get_job_run(context.workspace_id, context.job_id)
        event = self.repository.get_task_event(context.workspace_id, f"event_{job.id}_start")
        script_id = str(event.payload["script_id"])
        workspace_id, task_id = context.workspace_id, job.task_id
        for stage in STAGES:
            context.raise_if_cancelled()
            task = self.repository.get_creation_task(workspace_id, task_id)
            try:
                self.validate_input(workspace_id, task, script_id, job.input_digest)
            except (KeyError, ValueError) as exc:
                raise JobFailure(str(exc), "制作输入已变化，未继续执行下游阶段。", "重新确认文案与制作要求后开始。") from exc
            if self._stage_has_matching_output(job, task, stage):
                self._stage_event(context, stage, "reused", output=self._output_identity(workspace_id, task, stage))
            else:
                self._stage_event(context, stage, "running")
                try:
                    self.actions[stage](workspace_id, task_id)
                except Exception as exc:
                    detail = getattr(exc, "detail", None)
                    if isinstance(detail, dict):
                        reason = str(detail.get("reason") or detail.get("error") or exc)
                        impact = str(detail.get("impact") or "当前阶段未完成，上游有效产物保留。")
                        repair = str(detail.get("repair_action") or "修复当前阶段后点击继续制作。")
                    else:
                        reason = str(detail or exc) or type(exc).__name__
                        impact = "当前阶段未完成，上游有效产物保留。"
                        repair = "修复当前阶段后点击继续制作。"
                    self._stage_event(context, stage, "failed", reason=reason)
                    raise JobFailure(reason, impact, repair) from exc
                completed_task = self.repository.get_creation_task(workspace_id, task_id)
                self._stage_event(
                    context, stage, "completed",
                    output=self._output_identity(workspace_id, completed_task, stage),
                )
        self.repository.get_creation_task_montage_plan(workspace_id, task_id)
        if not self.render_is_valid(workspace_id, task_id):
            raise JobFailure("成片产物不可用", "渲染完成状态不能作为可播放成片。", "检查渲染产物后继续制作。")
        self._stage_event(context, "cover", "pending", reason="尚未生成封面，请单独制作或选择封面。")
        return JobResult()

    def _valid_stage(self, workspace_id: str, task: CreationTask, stage: str) -> bool:
        if stage == "narration":
            if not task.narration_cache_key:
                return False
            artifact = self.repository.get_narration_artifact(workspace_id, task.narration_cache_key)
            return (
                artifact is not None and artifact.task_id == task.id
                and artifact.script_id == task.confirmed_script_id
                and Path(artifact.audio_path).is_file()
            )
        if stage == "montage_plan":
            if not task.montage_plan_id:
                return False
            try:
                plan = self.repository.get_creation_task_montage_plan(workspace_id, task.id)
            except KeyError:
                return False
            return plan.get("script_id") == task.confirmed_script_id and plan.get("status") in {"ready", "rendering", "rendered", "failed"}
        if not task.montage_plan_id:
            return False
        plan = self.repository.get_creation_task_montage_plan(workspace_id, task.id)
        return plan.get("script_id") == task.confirmed_script_id and self.render_is_valid(workspace_id, task.id)

    def _stage_has_matching_output(self, job: JobRun, task: CreationTask, stage: str) -> bool:
        if not self._valid_stage(job.workspace_id, task, stage):
            return False
        current_output = self._output_identity(job.workspace_id, task, stage)
        if not current_output:
            return False
        matching_ids = {
            prior.id for prior in self.jobs(job.workspace_id, job.task_id)
            if prior.input_digest == job.input_digest
        }
        return any(
            event.job_id in matching_ids
            and event.event_type == "creation_execution_stage"
            and event.payload.get("stage") == stage
            and event.payload.get("status") in {"completed", "reused"}
            and event.payload.get("output") == current_output
            for event in self.repository.list_task_events(job.workspace_id, job.task_id)
        )

    def _output_identity(self, workspace_id: str, task: CreationTask, stage: str) -> str | None:
        if stage == "narration":
            return task.narration_cache_key
        if stage == "montage_plan":
            return task.montage_plan_id
        if not task.current_timeline_version_id:
            return None
        plan = self.repository.get_creation_task_montage_plan(workspace_id, task.id)
        output = plan.get("output_path")
        return f"{task.current_timeline_version_id}:{output}" if isinstance(output, str) else None

    def _stage_event(
        self, context: JobContext, stage: str, status: str,
        *, reason: str | None = None, output: str | None = None,
    ) -> None:
        self.repository.append_task_event(
            TaskEvent(
                id=f"event_{uuid4().hex}", workspace_id=context.workspace_id,
                task_id=self.repository.get_job_run(context.workspace_id, context.job_id).task_id,
                job_id=context.job_id, event_type="creation_execution_stage",
                payload={"stage": stage, "status": status,
                         **({"reason": reason} if reason else {}),
                         **({"output": output} if output else {})},
            )
        )
