"""Workspace-bound continuation of an explicitly started CreationTask."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.canvas import canvas_size
from app.graphic_overlays import validate_overlays
from app.device_scheduler import DeviceScheduler, Grant, RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork, WorkRequest
from app.host_resource_probes import HostResourceRefusal, MontageBudget, NarrationBudget, PresenterBudget, admit_resource, sample_host_capacity, audio_resource_refusal
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload, select_presenter_sections
from app.avatar_lipsync import _authorization_failure, avatar_lipsync_cache_key
from app.production import (CreationPlan, CreationTask, CreationTaskScript, NarrationSegment)
from app.creation.contracts import (JobRun, TaskEvent)
from app.repository import reset_workspace_context, set_workspace_context
from app.task_runtime import JobContext, JobFailure, JobResult, TaskRuntime


KIND = "creation_task_execution"
PERSON_EDIT_KIND = "creation_task_person_edit"
NARRATION_EDIT_KIND = "creation_task_narration_edit"
STAGES = ("narration", "montage_plan", "render")
DEVICE_STAGES = ("narration", "person_render", "montage_plan", "render")
DEVICE_OWNER_TYPE = "creation_job"
logger = logging.getLogger(__name__)


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
    started_monotonic: float = field(default_factory=time.monotonic)
    resource_purpose: str | None = None
    # Replaced views of this grant share one audio allowance; video wall time is separate.
    _audio_active_seconds: list[float] = field(default_factory=lambda: [0.0], repr=False, compare=False)
    narration_output_usage: list[int | None] | None = field(default=None, repr=False, compare=False)

    def check_execution_limits(self) -> None:
        from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise GrantedCommandCancelled("制作阶段已取消")
        if time.monotonic() >= self.deadline_monotonic:
            raise GrantedCommandTimeout("制作阶段总时限已到")

    def check(self) -> None:
        self.check_execution_limits()
        self.scheduler.assert_grant(self.grant)

    def task_authorization_verdict(self, actor, task_id):
        owner = self.scheduler.owners
        if hasattr(owner, "registrations"):
            owner = owner.registrations[DEVICE_OWNER_TYPE].owner
        authorizations = owner.task_authorizations
        if authorizations is None:
            return None  # An explicitly configured diagnostic owner keeps its live verifier.
        job = owner.repository.get_job_run(self.grant.workspace_id,self.grant.owner_id)
        if job.task_id != task_id or actor.workspace_id != self.grant.workspace_id:
            return "unauthorized"
        operation = ("render" if self.grant.unit_key.startswith("render:") else
                     "narration" if self.grant.unit_key.startswith("narration:") else "task_media")
        return authorizations.verdict(actor,task_id,operation,check_scope=False)

    def run(self, command, *, env=None, output_paths=(), output_bytes=None):
        from app.granted_subprocess import run_granted_command, GrantedCommandOutputLimit
        self.check()
        limit = self.max_output_bytes if output_bytes is None else output_bytes
        if self.narration_output_usage is not None:
            if self.narration_output_usage[0] is None:
                raise RecoveryBlocked('配音临时产物消耗不可读取，不能按零继续')
            limit = min(limit, self.max_output_bytes - self.narration_output_usage[0])
            if limit <= 0:
                raise GrantedCommandOutputLimit('配音父阶段剩余输出额度已耗尽')
        try:
            return run_granted_command(
                self.scheduler, self.grant, command,
                timeout_seconds=self.deadline_monotonic - time.monotonic(),
                max_stdout_bytes=65536, max_stderr_bytes=65536, cancel_event=self.cancel_event,
                env=env, output_paths=output_paths, max_output_file_bytes=limit,
                resource_purpose=self.resource_purpose)
        finally:
            if self.narration_output_usage is not None and self.narration_output_usage[0] is not None:
                for path in output_paths:
                    try:
                        self.narration_output_usage[0] += Path(path).stat().st_size
                    except FileNotFoundError:
                        continue  # The worker's bounded temporary/final rename may remove either path.
                    except OSError:
                        self.narration_output_usage[0] = None
                        break

    def run_pcm_assembly(self, resources):
        """Run one original unit and charge only its verified append receipt."""
        import sys
        from app.creation.narration_pcm import read_receipt
        from app.granted_subprocess import run_granted_command
        baseline = resources['pcm_assembly']
        unit_id = f'{self.grant.request_id}:{self.grant.generation}'
        manifest = {'version': 1, 'operation': 'append', 'unit_id': unit_id,
            'job_id': self.grant.owner_id, 'source_digest': resources['merge_source_digest'],
            **baseline, 'expected_file_identity': baseline['file_identity'],
            'sources': resources['pcm_sources'], 'final': resources['merge_final']}
        manifest.pop('file_identity')
        manifest.pop('header_bytes_allowance')
        work = Path(baseline['work_path'])
        try:
            work.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            receipt_path = Path(baseline['receipt_path'])
            seal_path = Path(str(work)+'.sealed.json')
            original_receipt = receipt_path if receipt_path.exists() else seal_path if seal_path.exists() else None
            if original_receipt is not None:
                receipt = read_receipt(original_receipt,unit_id=unit_id,job_id=self.grant.owner_id,
                    source_digest=resources['merge_source_digest'],baseline=baseline,
                    sources=resources['pcm_sources'],final=resources['merge_final'])
                self.narration_output_usage[0] += receipt['generated_bytes']
                return receipt
            manifest_path = Path(baseline['receipt_path'] + '.manifest.json')
            if manifest_path.exists():
                raise RecoveryBlocked('原PCM单元已有开始文件且没有成功回执，须先对账')
            with manifest_path.open('x') as stream:
                json.dump(manifest, stream, sort_keys=True)
            self.check()
            completed = run_granted_command(self.scheduler,self.grant,
                ([sys.executable] if getattr(sys,'frozen',False) else
                    [sys.executable,str(Path(__file__).resolve().parents[1]/'server.py')]) +
                    ['--pcm-assembly-worker','--manifest',str(manifest_path)],
                timeout_seconds=self.deadline_monotonic-time.monotonic(),
                max_stdout_bytes=16384,max_stderr_bytes=16384,cancel_event=self.cancel_event,
                output_paths=(work,receipt_path,seal_path),
                max_output_file_bytes=baseline['absolute_max_output_bytes'],resource_purpose='task_audio')
            if completed.returncode:
                raise RecoveryBlocked('原PCM追加未成功，须按原单元回执核对实际消耗')
            receipt = read_receipt(receipt_path if receipt_path.exists() else seal_path,
                unit_id=unit_id,job_id=self.grant.owner_id,
                source_digest=resources['merge_source_digest'],baseline=baseline,
                sources=resources['pcm_sources'],final=resources['merge_final'])
            self.narration_output_usage[0] += receipt['generated_bytes']
            return receipt
        except Exception:
            self.narration_output_usage[0] = None
            raise


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


def narration_edit_stage_facts(repository, task, proposal, plan, budget, *, reference_preparation=None, comparison_reference=None):
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
    from app.creation.narration_pcm import narration_reference_facts
    inputs,reference,reference_preparation=narration_reference_facts(repository,task,plan,reference_preparation)
    if not plan.selected_voice.version or inputs.voice.version != plan.selected_voice.version:
        raise ValueError('局部配音需要明确的声音固定版本')
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
    digest_reference = comparison_reference or reference_preparation
    resources = {"kind": "creation_narration", "narration_reference":digest_reference, "execution_mode": "isolated_qwen3_tts",
                 "text_chars": len(section.content) + len(inputs.voice.reference_text),
                 "section_count": 1, "reference_bytes": reference.size_bytes,
                 "duration_seconds": total_duration,
                 "max_output_bytes": budget.max_output_bytes, "threads": budget.max_threads,
                 "timeout_seconds": budget.max_timeout_seconds}
    manifest = {"operation": operation, "script": asdict(script), "segment": asdict(segment),
                "voice": asdict(inputs.voice), "reference_input": digest_reference['reference'],
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
                     presenter_models=None, resolve_presenter_output=None,
                     actor=None, graphic_source_facts=None, audio_mix_facts=None) -> tuple[str, dict[str, object]]:
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
    audio_mix = None
    if montage.audio_timeline is not None:
        if not isinstance(actor, CreationExecutionActor) or audio_mix_facts is None:
            raise PermissionError("音轨成片缺少当前成员或已许可来源解析器")
        audio_mix = audio_mix_facts(task.workspace_id, task.id, montage, actor=actor)
    if any(item.get("kind") not in {"image", "text"} for item in montage.graphic_overlays):
        raise ValueError("CPU 成片当前仅支持文字和已登记PNG图片图层")
    validate_overlays(montage.graphic_overlays, montage.duration_seconds)
    graphics = []
    image_refs = dict.fromkeys(item["source_ref"] for item in montage.graphic_overlays if item.get("kind") == "image")
    if image_refs:
        if not isinstance(actor, CreationExecutionActor) or graphic_source_facts is None:
            raise PermissionError("图片成片缺少当前成员或已登记来源解析器")
        graphics = [graphic_source_facts(task.workspace_id, task.id, ref, actor=actor) for ref in image_refs]
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
    sources = [audio, *(Path(item["path"]) for item in graphics),
               *(Path(item["path"]) for item in (audio_mix["sources"] if audio_mix is not None else []))]
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
    for path in dict.fromkeys(sources):
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
    if graphics:
        manifest["graphic_sources"] = graphics
    if audio_mix is not None:
        manifest["audio_mix"] = audio_mix
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
    if audio_mix is not None:
        resources["audio_mix"] = audio_mix["resources"]
    return f"render:{digest}", resources


def creation_stage_facts(repository, task, script, plan, stage, narration_budget, montage_budget, *, connection=None, reference_preparation=None, comparison_reference=None):
    """Derive measured inputs; stage caps never borrow the FFmpeg render budget."""
    if stage == "narration":
        if narration_budget is None:
            raise ValueError("配音预算尚未标定，请配置 DEVICE_NARRATION_* 标定值")
        from app.creation.narration_pcm import narration_reference_facts
        inputs, reference_input, qualified = narration_reference_facts(repository, task, plan, reference_preparation)
        reference = Path(reference_input.source_path)
        stat = reference.stat()
        saved = (comparison_reference or qualified)['reference']
        resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
                     "text_chars": (sum(len(section.content) for section in script.sections)
                                    + len(inputs.voice.reference_text) * len(script.sections)),
                     "section_count": len(script.sections), "reference_bytes": stat.st_size,
                     "duration_seconds": plan.duration_target_seconds,
                     "max_output_bytes": narration_budget.max_output_bytes,
                     "threads": narration_budget.max_threads,
                     "timeout_seconds": narration_budget.max_timeout_seconds}
        manifest = {"reference": [saved['source_path'], saved['size_bytes'], saved['mtime_ns'], saved['ctime_ns']],
                    "reference_input": saved,
                    "voice": asdict(inputs.voice), "resources": resources}
    else:
        if montage_budget is None:
            raise ValueError("故事板预算尚未标定，请配置 DEVICE_MONTAGE_* 标定值")
        if not task.narration_cache_key:
            raise ValueError("故事板缺少当前配音")
        narration = repository.get_narration_artifact(task.workspace_id, task.narration_cache_key, connection=connection)
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


def creation_input_rejection(events, job_id):
    return next((event.payload for event in reversed(events) if event.job_id==job_id
        and event.event_type=='creation_execution_stage' and event.payload.get('failure_kind')=='input_budget_exceeded'),None)


class CreationInputRejected(ValueError):
    def __init__(self, rejection):
        self.detail={'error':rejection['reason_code'],'reason':rejection['reason'],'stage':'admission',
            'job_id':rejection['job_id'],'request_id':rejection['device_request_id'],
            'submission_effect':'rejected','repair_action':rejection['repair_action'],
            'query_tool':'read_creation_execution','query_url':f"/api/creation-tasks/{rejection['task_id']}/execution"}
        super().__init__(rejection['reason'])


class CreationJobOwnerVerifier:
    """Validate a persisted JobRun before it can become a device work unit."""

    def __init__(self, repository, *, cpu_render_limits: CpuRenderLimits | None = None,
                 verify_identity: Callable[[CreationExecutionActor], str] | None = None,
                 narration_budget: NarrationBudget | None = None,
                 narration_audio_budget=None,
                 montage_budget: MontageBudget | None = None,
                 presenter_budget: PresenterBudget | None = None,
                 presenter_cpu_render_limits: CpuRenderLimits | None = None,
                 presenter_models: dict | None = None, resolve_presenter_output=None, task_media=None,
                 graphic_source_facts=None, audio_mix_facts=None, cover=None, task_authorizations=None, segments=None, native_case=None) -> None:
        self.repository = repository
        self.cpu_render_limits = cpu_render_limits
        self.verify_identity = verify_identity
        self.narration_budget = narration_budget
        self.narration_audio_budget = narration_audio_budget
        self.montage_budget = montage_budget
        self.presenter_budget = presenter_budget
        self.presenter_cpu_render_limits = presenter_cpu_render_limits
        self.presenter_models = presenter_models or {}
        self.resolve_presenter_output = resolve_presenter_output
        self.task_media = task_media
        self.graphic_source_facts = graphic_source_facts
        self.audio_mix_facts = audio_mix_facts
        self.cover = cover
        self.task_authorizations = task_authorizations
        self.segments = segments
        self.native_case = native_case

    def authorization_verdict(self, task, actor, stage, *, connection=None, check_scope=True):
        if self.task_authorizations is None:
            return self.verify_identity(actor) if self.verify_identity is not None else "ready"
        status = self.task_authorizations.status(task.workspace_id, task.id,
            identity=self.task_authorizations.identity(actor), operation=stage, connection=connection, check_scope=check_scope)
        return "ready" if status == "valid" else "unknown" if status in {"expired", "unverified"} else "unauthorized"

    def record_admission(self, conn, request, decision, checked_at):
        if request.owner_type!='creation_job':
            return None
        if request.unit_key.startswith('task_media:') and self.task_media is not None:
            return self.task_media.record_admission(conn,request,decision,checked_at)
        stage=request.unit_key.split(':',1)[0]
        if (stage not in {'narration','person_render','montage_plan'} or decision.verdict is not False
                or decision.reason!='declared_resources_exceed_budget'):
            return None
        job,task,start,script,plan=self._facts(conn,request.workspace_id,request.owner_id)
        if job.kind!=KIND:
            return None
        if decision.declared!=request.resources or request.input_digest!=job.input_digest or start.get('member_id')!=request.member_id:
            raise PermissionError('准入观察不是原作业成员和固定输入的决定')
        binding={'workspace_id':job.workspace_id,'device_request_id':request.id,'job_id':job.id,'task_id':task.id,'input_digest':job.input_digest,
            'unit_key':request.unit_key,'attempt_number':request.attempt_number,'last_grant_generation':request.last_grant_generation}
        event_id='event_creation_input_refused_'+hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
        try:
            existing=self.repository.get_task_event(job.workspace_id,event_id,connection=conn)
        except KeyError:
            existing=None
        if existing is not None:
            if any(existing.payload.get(key)!=value for key,value in binding.items()):
                raise PermissionError('原准入拒绝回执的归属不一致')
            return 'input_rejected'
        if self._check_facts(job,task,start,script,plan,stage,request.member_id)!='ready':
            raise PermissionError('原作业的固定输入已变化，未写入新的准入拒绝')
        fields={'text_chars','section_count','reference_bytes','duration_seconds','max_output_bytes','threads',
            'timeout_seconds','input_bytes','asset_count','clip_count','overlay_count','width_px','height_px',
            'input_count','source_duration_limit_seconds','output_duration_limit_seconds','source_sample_rate_limit',
            'source_channels_limit','output_sample_rate_limit','output_channels_limit'}
        failed=[]
        for name,check in decision.checks.items():
            if name not in fields or check.get('passed') is not False:
                continue
            actual,maximum=check.get('actual'),check.get('maximum')
            if (type(actual) not in {int,float} or type(maximum) not in {int,float}
                    or not math.isfinite(actual) or not math.isfinite(maximum) or actual<=maximum
                    or request.resources.get(name)!=actual):
                raise ValueError('声明超限字段缺少同次观察的实际值或上限')
            failed.append({'field':name,'actual':actual,'maximum':maximum})
        if not failed:
            raise ValueError('声明超限决定没有可核验的超限字段')
        reason='固定输入超出当前执行档：'+'；'.join(f"{item['field']} {item['actual']:g} > {item['maximum']:g}" for item in failed)
        repair='先读取原作业与准入拒绝；仅在输入或适用执行档依据变化后重新核实，不要原样重试或扩大预算。'
        payload={**binding,'observation_id':event_id,'stage':stage,'status':'failed','failure_kind':'input_budget_exceeded',
            'reason_code':decision.reason,'reason':reason,'failed_limits':failed,'retryable':False,'context':'history','admitted':False,
            'checked_at':checked_at.astimezone(timezone.utc).isoformat(),'repair_action':repair}
        event=TaskEvent(event_id,job.workspace_id,task.id,job.id,'creation_execution_stage',payload,created_at=checked_at)
        self.repository.reject_creation_execution_admission(job.workspace_id,job.id,request.id,job.input_digest,
            event=event,reason=reason,impact='本次执行单元未获设备许可；原作业、上游产物和已采用版本保留。',
            repair_action=repair,connection=conn)
        return 'input_rejected'

    def admission_basis_changed(self, rejection):
        """Reopen only when current fixed unit/profile facts resolve the refusal."""
        with self.repository._connect() as conn:
            job,task,start,script,plan=self._facts(conn,rejection['workspace_id'],rejection['job_id'])
        stage=rejection['stage']
        budget={'narration':self.narration_budget,'person_render':self.presenter_budget,
            'montage_plan':self.montage_budget}.get(stage)
        if budget is None:
            return False
        actor=CreationExecutionActor.from_event(job.workspace_id,start)
        if self.authorization_verdict(task,actor,stage,check_scope=False)!='ready':
            return False
        key,resources=self.stage_facts(task,script,plan,stage)
        changed=key!=rejection['unit_key']
        for failed in rejection['failed_limits']:
            field=failed['field'];limit=getattr(budget,field if field.startswith('max_') else 'max_'+field)
            actual=resources[field]
            if actual>limit:
                return False
            changed=changed or actual!=failed['actual'] or limit!=failed['maximum']
        return changed

    def render_facts(self, conn, task, plan, *, request=None, actor=None):
        # Person composition never inherits the pure-material caps.
        limits = self.presenter_cpu_render_limits if plan.presenter_share_target else self.cpu_render_limits
        if limits is None:
            raise ValueError("人物组合 CPU 成片专属预算尚未标定" if plan.presenter_share_target else "CPU 成片资源预算尚未配置")
        return cpu_render_facts(self.repository, conn, task, plan, limits, request=request,
            presenter_limits=self.presenter_cpu_render_limits, presenter_models=self.presenter_models,
            resolve_presenter_output=self.resolve_presenter_output,
            actor=actor, graphic_source_facts=self.graphic_source_facts, audio_mix_facts=self.audio_mix_facts)

    def stage_facts(self, task, script, plan, stage, *, connection=None):
        if stage == "person_render":
            return presenter_stage_facts(self.repository, task, script, plan, self.presenter_budget, self.presenter_models)
        key, resources = creation_stage_facts(self.repository, task, script, plan, stage,
                                             self.narration_budget, self.montage_budget, connection=connection)
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

    def narration_unit_facts(self, conn, job, task, script, plan, *, request=None, original_recovery=False, pcm_preparation=None, comparison_reference=None):
        from app.creation.narration_stage_use_case import NarrationStageUseCase
        from app.narration import _pronunciation_dictionary, _pronunciation_version
        script = self.repository.effective_creation_script(task.workspace_id, task.id, connection=conn)
        if pcm_preparation is None and request is not None and request.resources.get('execution_mode')=='cpu_pcm_wav_assembly':
            pcm_preparation=self.persisted_pcm_sources(request,conn)
        budget = self.narration_budget
        if budget is None or budget.input_scope != 'single_call_experiment_v1':
            raise ValueError('配音单次输入尚无明确执行范围；原总量374不能默认作为生产单段标定')
        if self.narration_audio_budget is None:
            raise ValueError('逐段配音缺少独立CPU音频合并预算，尚未调用声音模型')
        usage = self.repository.creation_narration_usage(job.workspace_id, job.id, connection=conn)
        unresolved = usage['unsettled']
        if unresolved and (request is None or not original_recovery and request.state not in {'granted', 'yield_requested'} or len(unresolved) != 1
                or unresolved[0]['usage_id'] != f'{request.id}:{request.last_grant_generation}'):
            raise RecoveryBlocked('原配音请求的时间或产物消耗未知，须先核对原单元')
        limits = usage['budget'] or {'parent_timeout_seconds': min(300.0, budget.max_timeout_seconds),
            'parent_max_output_bytes': min(3 * 1024 * 1024, budget.max_output_bytes)}
        if (usage['used_seconds'] >= limits['parent_timeout_seconds']
                or usage['used_output_bytes'] >= limits['parent_max_output_bytes']):
            raise ValueError('原配音阶段累计300秒或3MiB额度已耗尽，不能重置')
        from app.creation.narration_pcm import narration_reference_facts
        reference_preparation=(pcm_preparation or {}).get('_narration_reference')
        if reference_preparation is None:
            reference_preparation = (request.resources.get('narration_reference') if request is not None else
                self.repository.creation_narration_reference(job.workspace_id, job.id, connection=conn))
            if reference_preparation is None and job.attempt == 0 and job.started_at is None:
                self.repository.creation_narration_source_binding(job.workspace_id, job.id, connection=conn)
        inputs,reference_input,reference_preparation=narration_reference_facts(self.repository,task,plan,reference_preparation)
        pronunciation = _pronunciation_version(_pronunciation_dictionary(entries=inputs.pronunciation_entries,
            workspace_id=task.workspace_id, person_profile_id=inputs.voice.person_profile_id, task_id=task.id))
        reference = Path(reference_input.source_path)
        stat = reference.stat()
        digest_reference = comparison_reference or reference_preparation
        saved = digest_reference['reference']
        manifest = {'script_id': script.id, 'language': script.language, 'voice': asdict(inputs.voice), 'pronunciation_version': pronunciation,
            'reference_input': saved,
            'reference': [saved['source_path'], saved['size_bytes'], saved['mtime_ns'], saved['ctime_ns']]}
        segments = self.repository.list_narration_segments(task.workspace_id, task.id, script.id, connection=conn)
        by_section = {item.source_section_id: item for item in segments}
        section_ids = {item.id for item in script.sections}
        if len(by_section) != len(segments) or not set(by_section).issubset(section_ids):
            raise ValueError('现有配音片段集不属于当前唯一文案段落')
        ready_inputs = []
        source_files = []
        language = {'zh-CN': 'zh', 'en-US': 'en', 'zh-CN/en-US': 'zh-en'}[script.language]
        missing = None
        for ordinal, section in enumerate(script.sections, 1):
            segment = by_section.get(section.id)
            if segment is None or segment.status != 'ready':
                if missing is None:
                    missing = ordinal, section
                continue
            record = self.repository.get_narration_artifact(task.workspace_id,
                (segment.audio_reference or '').removeprefix('narration://artifact/'), connection=conn)
            if (segment.id != NarrationStageUseCase.segment_id(task.id, script.id, section.id)
                    or segment.text != section.content or segment.language != language
                    or (segment.voice_profile_id, segment.voice_version) != (inputs.voice.id, inputs.voice.version)
                    or segment.pronunciation_version != pronunciation or record is None
                    or (record.task_id, record.script_id, record.voice_profile_id, record.pronunciation_version)
                        != (task.id, script.id, inputs.voice.id, pronunciation)
                    or record.duration_seconds != segment.duration_seconds or record.sample_rate != segment.sample_rate):
                raise ValueError('已保存配音片段的文字、声音或发音版本不匹配，须先核对原产物')
            path = Path(record.audio_path)
            source_stat = path.stat()
            if source_stat.st_size <= 0:
                raise ValueError('已保存配音片段为空')
            ready_inputs.append({'reference': segment.audio_reference, 'size_bytes': source_stat.st_size,
                'duration_seconds': record.duration_seconds, 'sample_rate': record.sample_rate, 'channels': 1, 'uses': 1})
            source_files.append([record.cache_key, str(path.resolve()), source_stat.st_size,
                source_stat.st_mtime_ns, source_stat.st_ctime_ns])
        common = {'input_contract': 'narration_units_v1', 'script_id': script.id,
            'narration_reference':digest_reference,
            'pronunciation_version': pronunciation,
            'voice_profile_id': inputs.voice.id, 'voice_version': inputs.voice.version,
            'model_id': plan.selected_voice.model_id, 'language': language,
            'parent_timeout_seconds': limits['parent_timeout_seconds'],
            'parent_max_output_bytes': limits['parent_max_output_bytes']}
        if missing is not None:
            ordinal, section = missing
            manifest['section'] = asdict(section)
            resources = {**common, 'kind': 'creation_narration', 'execution_mode': 'isolated_qwen3_tts',
                'narration_phase': 'segment', 'section_id': section.id, 'ordinal': ordinal,
                'segment_id': NarrationStageUseCase.segment_id(task.id, script.id, section.id),
                'input_scope': budget.input_scope, 'budget_basis': 'controlled_experiment_unverified_legacy_cap',
                'text_chars': len(section.content) + len(inputs.voice.reference_text), 'section_count': 1,
                'reference_bytes': stat.st_size, 'duration_seconds': plan.duration_target_seconds,
                'max_output_bytes': limits['parent_max_output_bytes'], 'threads': budget.max_threads,
                'timeout_seconds': limits['parent_timeout_seconds']}
            prefix = f'narration:segment:v1:{section.id}:'
        else:
            audio = self.narration_audio_budget
            if audio is None or not ready_inputs:
                raise ValueError('配音合并缺少全部ready片段或独立CPU音频预算')
            manifest['segments'] = [asdict(by_section[section.id]) for section in script.sections]
            manifest['source_files'] = source_files
            source_digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
            ordered_ids = [section.id for section in script.sections]
            resources = self.pcm_resources(conn, job, common, ready_inputs, source_files,
                source_digest, ordered_ids, limits, plan, pcm_preparation=pcm_preparation)
            prefix = 'narration:merge:v1:'
        digest = hashlib.sha256(json.dumps([manifest, resources], sort_keys=True, default=str).encode()).hexdigest()
        return prefix + digest, resources

    def pcm_resources(self, conn, job, common, ready_inputs, source_files, source_digest, ordered_ids, limits, plan, *, pcm_preparation=None):
        from app.creation.narration_pcm import prepare_sources, prepared_source_facts, work_baseline
        audio = self.narration_audio_budget
        checkpoint = self.repository.creation_narration_merge_checkpoint(job.workspace_id, job.id, connection=conn)
        covered = len(checkpoint['section_ids']) if checkpoint else 0
        if checkpoint and (checkpoint['source_digest'] != source_digest
                or checkpoint['section_ids'] != ordered_ids[:covered] or covered >= len(ordered_ids)
                or 'pcm' not in checkpoint):
            raise RecoveryBlocked('原PCM检查点不属于当前完整来源，须查询原请求')
        if audio.max_input_count < 2 and len(ordered_ids) > 1:
            raise ValueError('原CPU音频输入预算不足以追加两个实际输入')
        count = min(2,audio.max_input_count) - int(covered > 0)
        if pcm_preparation is None:
            if conn.in_transaction:
                raise RecoveryBlocked('PCM格式准备必须在原动作的写事务外完成')
            pcm_preparation=prepare_sources(item[1] for item in source_files)
        original_sources=[prepared_source_facts(item[1],pcm_preparation) for item in source_files]
        sources=original_sources[covered:covered+count]
        if not sources:
            raise RecoveryBlocked('原PCM检查点没有可追加的片段')
        work = Path(source_files[0][1]).parent / ('.pcm-job-' + hashlib.sha256(job.id.encode()).hexdigest()[:24]) / 'narration.wav'
        usage = self.repository.creation_narration_usage(job.workspace_id,job.id,connection=conn)
        absolute = min(audio.max_output_bytes, limits['parent_max_output_bytes'])
        baseline = work_baseline(work, checkpoint['pcm'] if checkpoint else None,
            limits['parent_max_output_bytes']-usage['used_output_bytes'],absolute,
            sum(item['pcm_frames']*2 for item in sources))
        covered_after = covered + len(sources)
        receipt_key = hashlib.sha256(json.dumps([source_digest,ordered_ids[:covered_after],baseline],sort_keys=True).encode()).hexdigest()[:24]
        baseline['receipt_path'] = str(work.parent / ('receipt-'+receipt_key+'.json'))
        inputs = ready_inputs[covered:covered_after]
        if covered:
            inputs = [{'reference':str(work),'size_bytes':baseline['committed_pcm_bytes']+44,
                'duration_seconds':baseline['committed_pcm_bytes']/48000,'sample_rate':24000,'channels':1,'uses':1},*inputs]
        resources = {**common,'kind':'task_audio','execution_mode':'cpu_pcm_wav_assembly','narration_phase':'merge',
            'merge_source_digest':source_digest,'merge_section_ids':ordered_ids[:covered_after],
            'merge_final':covered_after==len(ordered_ids),'pcm_assembly':baseline,'pcm_sources':sources,
            'pcm_original_sources':original_sources,
            'pcm_source_identities':[{'path':item['path'],**pcm_preparation[item['path']]['identity']} for item in original_sources],
            'input_policy':'fixed_pcm_header','inputs':inputs,'input_count':len(inputs),
            'input_bytes':sum(item['size_bytes'] for item in inputs),
            'source_duration_limit_seconds':audio.max_source_duration_seconds,
            'output_duration_limit_seconds':plan.duration_target_seconds,
            'source_sample_rate_limit':audio.max_sample_rate,'source_channels_limit':audio.max_channels,
            'output_sample_rate_limit':24000,'output_channels_limit':1,
            'max_output_bytes':absolute,'threads':audio.max_threads,
            'timeout_seconds':min(audio.max_timeout_seconds,limits['parent_timeout_seconds']-usage['used_seconds'])}
        if (sum(item['duration_seconds'] for item in inputs)>plan.duration_target_seconds
                or audio_resource_refusal(resources,audio) is not None):
            raise ValueError('原PCM追加超出已有CPU音频预算')
        return resources

    def prepare_pcm_sources(self, job, *, generated=None, section_id=None):
        from app.creation.narration_pcm import prepare_sources
        task=self.repository.get_creation_task(job.workspace_id,job.task_id)
        if generated is None and job.kind==NARRATION_EDIT_KIND:
            candidate=self.repository.narration_edit_generated(job.workspace_id,job.id)
            if candidate is not None:
                generated=self.repository.get_narration_artifact(job.workspace_id,candidate['cache_key'])
                section_id=self.repository.get_narration_segment(job.workspace_id,job.task_id,candidate['segment_id']).source_section_id
        paths=[]
        for segment in self.repository.list_narration_segments(job.workspace_id,job.task_id,task.confirmed_script_id):
            if segment.status!='ready' or generated is not None and segment.source_section_id==section_id:
                continue
            record=self.repository.get_narration_artifact(job.workspace_id,
                segment.audio_reference.removeprefix('narration://artifact/'))
            if record is None:
                raise RecoveryBlocked('原PCM来源产物已丢失')
            paths.append(record.audio_path)
        if generated is not None:
            paths.append(generated.audio_path)
        plan=self.repository.get_creation_plan(job.workspace_id,task.plan_id)
        from app.creation.narration_pcm import narration_reference_facts
        _,_,reference=narration_reference_facts(self.repository,task,plan)
        return {**prepare_sources(paths),'_narration_reference':reference}

    def persisted_pcm_sources(self,request,conn):
        row=conn.execute('SELECT workspace_id,owner_id,input_digest,unit_key,resources_json FROM device_work_requests WHERE id=?',
            (request.id,)).fetchone()
        if row is None or (row['workspace_id'],row['owner_id'],row['input_digest'],row['unit_key'],json.loads(row['resources_json']))!=(
                request.workspace_id,request.owner_id,request.input_digest,request.unit_key,request.resources):
            raise RecoveryBlocked('PCM格式依据不属于原持久请求')
        resources=request.resources
        if 'pcm_source_identities' not in resources:
            raise RecoveryBlocked('原PCM请求缺少已核验的来源文件身份')
        identities={item['path']:{key:item[key] for key in ('device','inode')} for item in resources['pcm_source_identities']}
        if set(identities)!={item['path'] for item in resources['pcm_original_sources']}:
            raise RecoveryBlocked('原PCM来源身份与格式依据不一致')
        return {**{item['path']:{'facts':dict(item),'identity':identities[item['path']]}
            for item in resources['pcm_original_sources']},'_narration_reference':resources['narration_reference']}

    def pcm_assembly_baseline(self, request, conn, *, original_recovery=False):
        if request.resources.get('execution_mode') != 'cpu_pcm_wav_assembly':
            raise RecoveryBlocked('原请求不是PCM组装许可')
        job,task,event,script,plan = self._facts(conn,request.workspace_id,request.owner_id)
        key,resources = (self.narration_edit_facts(job,task,event,plan,connection=conn,request=request,original_recovery=original_recovery)
            if job.kind == NARRATION_EDIT_KIND else self.narration_unit_facts(conn,job,task,script,plan,request=request,original_recovery=original_recovery))
        if key != request.unit_key or resources != request.resources:
            raise RecoveryBlocked('原PCM请求与Job检查点、累计用量不符')
        return resources['pcm_assembly']

    def migrate_narration_request(self, conn, old, actor, *, source_preparation, write_guard=None):
        from app.creation.narration_pcm import verify_narration_source
        job, task, event, script, plan = self._facts(conn, old.workspace_id, old.owner_id)
        if write_guard is not None:
            write_guard(conn)
        original = CreationExecutionActor.from_event(job.workspace_id, event)
        usage = self.repository.creation_narration_usage(job.workspace_id, job.id, connection=conn)
        binding = self.repository.creation_narration_source_binding(job.workspace_id, job.id, connection=conn)
        stage = 'narration' if job.kind == KIND else 'narration_edit'
        if original != actor or self.authorization_verdict(task, original, stage, connection=conn) != 'ready':
            raise PermissionError('原配音身份或任务授权不可核实')
        if (old.input_digest != job.input_digest or job.attempt != 0 or job.started_at is not None
                or usage['unsettled'] or usage['used_seconds'] != 0 or usage['used_output_bytes'] != 0
                or binding.payload['actor'] != original.identity_payload()
                or conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? AND event_type IN "
                    "('task_execution_unit_started','creation_narration_unit_started','creation_narration_edit_generated') LIMIT 1",
                    (job.workspace_id, job.id)).fetchone()):
            raise RecoveryBlocked('原配音输入、产物或未执行事实不匹配，不能自动转换')
        verify_narration_source(self.repository, task, plan, source_preparation, connection=conn)
        preparation = {'_narration_reference': source_preparation['narration_reference']}
        if job.kind == 'creation_task_segment_revision':
            if job.status != 'queued' or self.segments is None:
                raise RecoveryBlocked('原段配音不在未执行排队边界')
            from app.profile_reference_media import ProfileReferenceInput
            key = self.segments.revision_key(job, ProfileReferenceInput(**source_preparation['narration_reference']['reference']))
            intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id, job.input_digest, key, old.policy)
            trusted = self.segments.verify_submission(intent, actor, self, connection=conn,
                reference_preparation=source_preparation['narration_reference'])
            if (old.unit_key != self.segments.revision_key(job, ProfileReferenceInput(**old.resources['narration_reference']['reference']))
                    or {key:value for key,value in old.resources.items() if key != 'narration_reference'} !=
                    {key:value for key,value in trusted.resources.items() if key != 'narration_reference'}):
                raise RecoveryBlocked('原段配音的其他输入或预算已变化')
            return intent, trusted
        if self._check_facts(replace(job, status='queued'), task, event, script, plan, stage, old.member_id) != 'ready':
            raise RecoveryBlocked('原配音业务依赖已变化')
        if job.kind == KIND:
            if (task.narration_cache_key is not None
                    or self.repository.list_narration_segments(task.workspace_id, task.id, script.id, connection=conn)):
                raise RecoveryBlocked('原制作已经产生配音，不能按未执行恢复')
            if old.unit_key.count(':') == 1:
                original_key, original_resources = creation_stage_facts(self.repository, task, script, plan, 'narration',
                    self.narration_budget, self.montage_budget, reference_preparation=source_preparation['narration_reference'],
                    comparison_reference=binding.payload['narration_reference'])
            else:
                original_key, original_resources = self.narration_unit_facts(conn, job, task, script, plan,
                    pcm_preparation=preparation, comparison_reference=old.resources['narration_reference'])
            if (original_key, original_resources) != (old.unit_key, old.resources):
                raise RecoveryBlocked('原配音其他输入或预算已变化，来源重验证不能改写')
            self.repository.requeue_ungranted_narration_job(job.workspace_id, job.id, old.id, connection=conn)
            job, task, event, script, plan = self._facts(conn, old.workspace_id, old.owner_id)
            key, resources = self.narration_unit_facts(conn, job, task, script, plan, pcm_preparation=preparation)
        else:
            if job.status != 'queued':
                raise RecoveryBlocked('原局部配音不在未执行排队边界')
            original_key, original_resources = self.narration_edit_facts(job, task, event, plan, connection=conn,
                pcm_preparation=preparation, comparison_reference=old.resources['narration_reference'])
            if (original_key, original_resources) != (old.unit_key, old.resources):
                raise RecoveryBlocked('原局部配音的其他产物或依赖已变化')
            key, resources = self.narration_edit_facts(job, task, event, plan, connection=conn, pcm_preparation=preparation)
        if resources['text_chars'] > self.narration_budget.max_text_chars:
            raise ValueError('单次正文与参考仍超出受控实验上限，不能转换后执行')
        intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id, job.input_digest, key, old.policy)
        return intent, self.verify_submission(intent, actor, connection=conn, pcm_preparation=preparation)

    def resume_narration_request(self, conn, request, actor, *, write_guard=None):
        if write_guard is not None:
            write_guard(conn)
        job, task, event, script, plan = self._facts(conn, request.workspace_id, request.owner_id)
        if CreationExecutionActor.from_event(job.workspace_id, event) != actor:
            raise PermissionError('不能继续其他成员的原配音作业')
        self.repository.resume_creation_narration_job(job.workspace_id, job.id, connection=conn)
        intent = SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id, job.input_digest, request.unit_key, request.policy)
        preparation=self.persisted_pcm_sources(request,conn) if request.resources.get('execution_mode')=='cpu_pcm_wav_assembly' else None
        return self.verify_submission(intent, actor, connection=conn,pcm_preparation=preparation)

    def edit_facts(self, job, task, event, plan):
        proposal = self.repository.get_edit_proposal(job.workspace_id, event["edit_proposal_id"])
        return person_edit_stage_facts(self.repository, task, proposal, plan,
                                      self.presenter_budget, self.presenter_models)

    def narration_edit_facts(self, job, task, event, plan, *, connection=None, request=None, original_recovery=False, pcm_preparation=None, comparison_reference=None):
        with nullcontext(connection) if connection is not None else self.repository._connect() as conn:
            if pcm_preparation is None and request is not None and request.resources.get('execution_mode')=='cpu_pcm_wav_assembly':
                pcm_preparation=self.persisted_pcm_sources(request,conn)
            proposal = self.repository.get_edit_proposal(job.workspace_id,event['edit_proposal_id'])
            reference = (pcm_preparation or {}).get('_narration_reference')
            if reference is None:
                if request is not None:
                    reference = request.resources.get('narration_reference')
                elif conn.execute('SELECT 1 FROM task_events WHERE workspace_id=? AND id=?',
                        (job.workspace_id, f'event_{job.id}_start')).fetchone():
                    reference = self.repository.creation_narration_reference(job.workspace_id, job.id, connection=conn)
                    if reference is None and job.attempt == 0 and job.started_at is None:
                        self.repository.creation_narration_source_binding(job.workspace_id, job.id, connection=conn)
            key,resources = narration_edit_stage_facts(self.repository,task,proposal,plan,self.narration_budget,
                reference_preparation=reference, comparison_reference=comparison_reference)
            if self.narration_budget.input_scope != 'single_call_experiment_v1' or self.narration_audio_budget is None:
                raise ValueError('局部配音缺少明确单次范围或独立PCM预算')
            usage = self.repository.creation_narration_usage(job.workspace_id,job.id,connection=conn)
            if usage['unsettled'] and (request is None or len(usage['unsettled']) != 1
                    or usage['unsettled'][0]['usage_id'] != f'{request.id}:{request.last_grant_generation}'
                    or not original_recovery and request.state not in {'granted','yield_requested'}):
                raise RecoveryBlocked('原局部配音用量未知，须先查询同一Job')
            limits = usage['budget'] or {'parent_timeout_seconds':min(300.0,self.narration_budget.max_timeout_seconds),
                'parent_max_output_bytes':min(3*1024*1024,self.narration_budget.max_output_bytes)}
            common = {'input_contract':'narration_units_v1','script_id':task.confirmed_script_id,
                'narration_reference':resources['narration_reference'],
                'parent_timeout_seconds':limits['parent_timeout_seconds'],
                'parent_max_output_bytes':limits['parent_max_output_bytes']}
            candidate = self.repository.narration_edit_generated(job.workspace_id,job.id,connection=conn)
            if candidate is None:
                return key,{**resources,**common,'narration_phase':'segment_edit',
                    'input_scope':self.narration_budget.input_scope,
                    'max_output_bytes':min(resources['max_output_bytes'],limits['parent_max_output_bytes']-usage['used_output_bytes']),
                    'timeout_seconds':min(resources['timeout_seconds'],limits['parent_timeout_seconds']-usage['used_seconds'])}
            if candidate['source_key'] != key:
                raise RecoveryBlocked('局部配音候选的原版本或来源已变化')
            target = self.repository.get_narration_artifact(job.workspace_id,candidate['cache_key'],connection=conn)
            segments = self.repository.list_narration_segments(task.workspace_id,task.id,task.confirmed_script_id,connection=conn)
            script = self.repository.get_creation_task_script(task.workspace_id,task.confirmed_script_id)
            ordered = {item.source_section_id:item for item in segments}
            files,ready = [],[]
            for section in script.sections:
                segment = ordered[section.id]
                record = target if segment.id == candidate['segment_id'] else self.repository.get_narration_artifact(
                    job.workspace_id,segment.audio_reference.removeprefix('narration://artifact/'),connection=conn)
                if record is None or record.task_id != task.id or record.script_id != script.id:
                    raise RecoveryBlocked('局部配音候选或其他原片段已丢失')
                path=Path(record.audio_path);info=path.stat()
                files.append([record.cache_key,str(path.resolve()),info.st_size,info.st_mtime_ns,info.st_ctime_ns])
                ready.append({'reference':self.repository.narration_artifact_reference(record.cache_key),
                    'size_bytes':info.st_size,'duration_seconds':record.duration_seconds,
                    'sample_rate':record.sample_rate,'channels':1,'uses':1})
            source_digest = hashlib.sha256(json.dumps([key,files],sort_keys=True).encode()).hexdigest()
            resources = self.pcm_resources(conn,job,common,ready,files,source_digest,
                [section.id for section in script.sections],limits,plan,pcm_preparation=pcm_preparation)
            digest=hashlib.sha256(json.dumps(resources,sort_keys=True).encode()).hexdigest()
            return 'narration_edit:pcm:'+digest,resources

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
        event = self.repository.creation_execution_input(workspace_id, job.id, connection=conn)
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

    def verify_submission(self, intent: SubmissionIntent, actor: object, *, connection=None, pcm_preparation=None) -> VerifiedWork:
        if intent.unit_key.startswith('native_command:') and self.native_case is not None:
            return self.native_case.verify_submission(intent,actor,self,connection=connection)
        if intent.unit_key.startswith(('segment_video:', 'segment_revision:')) and self.segments is not None:
            return self.segments.verify_submission(intent,actor,self,connection=connection)
        if intent.unit_key.startswith("cover:") and self.cover is not None:
            return self.cover.verify_submission(intent, actor)
        if intent.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.verify_submission(intent, actor)
        if (not isinstance(actor, CreationExecutionActor) or not actor.member_id
                or actor.workspace_id != intent.workspace_id or intent.owner_type != DEVICE_OWNER_TYPE):
            raise PermissionError("制作执行身份未知或工作区不匹配")
        with nullcontext(connection) if connection is not None else self.repository._connect() as conn:
            job, task, event, script, plan = self._facts(conn, intent.workspace_id, intent.owner_id)
            stage = intent.unit_key.split(":", 1)[0]
            verdict = self._check_facts(job, task, event, script, plan, stage, actor.member_id)
            try:
                original = CreationExecutionActor.from_event(job.workspace_id, event)
            except (ValueError, KeyError, TypeError, AttributeError):
                raise PermissionError("制作作业持久身份无法复核") from None
            if actor != original:
                raise PermissionError("制作作业原身份不匹配")
            if verdict == "ready" and self.task_authorizations is not None and connection is None:
                self.task_authorizations.ensure(task, original)
                if stage == "person_edit":
                    proposal = self.repository.get_edit_proposal(job.workspace_id, event["edit_proposal_id"])
                    target = proposal.operation["payload"]
                    authorization = self.repository.get_task_authorization(task.workspace_id,task.id)
                    if f"{target['person_profile_id']}:{target['person_version']}" not in authorization.resource_scope.get("persons", []):
                        raise PermissionError("新人物不在原任务授权范围内，需要实时核实新的资源授权")
            if self.authorization_verdict(task, original, stage, connection=conn) != "ready":
                raise PermissionError("原任务授权未就绪、已到期或不包含本次操作")
            if verdict == "ready" and job.kind == NARRATION_EDIT_KIND:
                key, resources = self.narration_edit_facts(job, task, event, plan, connection=conn, pcm_preparation=pcm_preparation)
                if intent.unit_key != key:
                    raise PermissionError("配音编辑输入已变化，请重新安排")
            elif verdict == "ready" and job.kind == PERSON_EDIT_KIND:
                key, resources = self.edit_facts(job, task, event, plan)
                if intent.unit_key != key:
                    raise PermissionError("人物编辑输入已变化，请重新安排")
            elif verdict == "ready" and stage == "render":
                key, resources = self.render_facts(conn, task, plan, actor=original)
                if intent.unit_key != key:
                    raise PermissionError("故事板或源文件已变化，请重新安排成片")
            elif verdict == 'ready' and stage == 'narration' and intent.unit_key.startswith(('narration:segment:v1:', 'narration:merge:v1:')):
                key, resources = self.narration_unit_facts(conn, job, task, script, plan, pcm_preparation=pcm_preparation)
                if key != intent.unit_key:
                    raise PermissionError('配音下一单元或输入版本已变化')
            elif verdict == "ready" and (
                (stage == "narration" and self.narration_budget is not None)
                or (stage == "montage_plan" and self.montage_budget is not None)
                or stage == "person_render"
            ):
                key, resources = (self.stage_facts(task, script, plan, stage, connection=conn)
                    if connection is not None else self.stage_facts(task, script, plan, stage))
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
        if event.get("operation") == "regenerate_storyboard":
            from app.creation.storyboard_regeneration import request_current
            try:
                if stage != "montage_plan" or not request_current(self.repository, task, event):
                    return "stale"
            except (KeyError, OSError, ValueError):
                return "dependency"
        return "ready"

    def check_current(self, request: WorkRequest, conn: sqlite3.Connection):
        if request.unit_key.startswith('native_command:') and self.native_case is not None:
            return self.native_case.check_current(request,conn,self)
        if request.unit_key.startswith(('segment_video:', 'segment_revision:')) and self.segments is not None:
            return self.segments.check_current(request,conn,self)
        if request.unit_key.startswith("cover:") and self.cover is not None:
            return self.cover.check_current(request, conn)
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
        verdict = self.authorization_verdict(task, original, stage, connection=conn, check_scope=False)
        if verdict != "ready":
            return verdict
        try:
            if job.kind == NARRATION_EDIT_KIND:
                key, resources = self.narration_edit_facts(job, task, event, plan, connection=conn, request=request)
            elif job.kind == PERSON_EDIT_KIND:
                key, resources = self.edit_facts(job, task, event, plan)
            elif stage == "render":
                key, resources = self.render_facts(conn, task, plan, request=request, actor=original)
            elif stage == 'narration' and request.unit_key.startswith(('narration:segment:v1:', 'narration:merge:v1:')):
                key, resources = self.narration_unit_facts(conn, job, task, script, plan, request=request)
            else:
                key, resources = self.stage_facts(task, script, plan, stage)
        except PermissionError:
            return "unauthorized"
        except (KeyError, StopIteration, OSError, ValueError, RecoveryBlocked):
            return "dependency"
        return "ready" if request.unit_key == key and request.resources == resources else "stale"

    def owns_task_stage(self, conn, workspace_id, job_id, stage):
        """Local final ownership check; never observes identity, resources or media."""
        try:
            job, task, event, script, plan = self._facts(conn, workspace_id, job_id)
        except KeyError:
            return False  # Retired inputs cannot own the task's current projection.
        latest = conn.execute(
            "SELECT id FROM job_runs WHERE workspace_id=? AND task_id=? AND kind=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1", (workspace_id, task.id, KIND)).fetchone()
        return (latest is not None and latest["id"] == job.id
                and self._check_facts(job, task, event, script, plan, stage, event.get("member_id")) == "ready")

    def capture_local(self, request, conn):
        if request.unit_key.startswith('native_command:') and self.native_case is not None:
            return self.native_case.capture_local(request,conn,self)
        if request.unit_key.startswith(('segment_video:', 'segment_revision:')) and self.segments is not None:
            return self.segments.capture_local(request,conn,self)
        if request.unit_key.startswith("cover:") and self.cover is not None:
            return self.cover.capture_local(request, conn)
        if request.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.capture_local(request, conn)
        try:
            job, task, event, script, plan = self._facts(conn, request.workspace_id, request.owner_id)
        except KeyError:
            return lambda current: "stale"
        stage = request.unit_key.split(":", 1)[0]
        refs = []
        if plan.selected_voice:
            refs.append(("voice", plan.selected_voice.profile_id, plan.selected_voice.version))
        if plan.selected_person:
            refs.append(("person", plan.selected_person.profile_id, plan.selected_person.version))
        if stage == "person_edit":
            target = self.repository.get_edit_proposal(job.workspace_id,event["edit_proposal_id"]).operation["payload"]
            refs.append(("person", target["person_profile_id"], target["person_version"]))
        pronunciation_scope = None
        if stage == "narration" and plan.selected_voice:
            try:
                voice = self.repository.get_versioned_voice_profile(request.workspace_id,
                    plan.selected_voice.profile_id, plan.selected_voice.version)
            except KeyError:
                return lambda current: "stale"
            pronunciation_scope = (voice.person_profile_id, task.id)
        profiles = self.repository.capture_profile_local_check(conn, request.workspace_id, refs,
            [(item.id, item.version) for item in plan.pronunciation_references],
            pronunciation_scope=pronunciation_scope)
        assets, persons = set(), set()
        if stage == "montage_plan" and self.task_authorizations is not None:
            authorization = self.repository.get_task_authorization(task.workspace_id, task.id, connection=conn)
            assets.update(authorization.resource_scope.get("assets", []) if authorization is not None else [])
        if stage == "render" and task.montage_plan_id:
            try:
                montage = self.repository.get_creation_task_montage_plan(request.workspace_id, task.id)
            except KeyError:
                return lambda current: "stale"
            for clip in montage.get("clips", []):
                if clip.get("asset_id"):
                    assets.add(clip["asset_id"])
                if clip.get("person_render_id"):
                    persons.add(clip["person_render_id"])
            references = set()
            def collect(value):
                if isinstance(value, str):
                    references.add(value)
                elif isinstance(value, dict):
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)
            collect(montage)
            for reference in references:
                if reference.startswith("workspace://"):
                    try:
                        reference = str(self.repository.resolve_workspace_media_reference(
                            request.workspace_id, reference, require_registered=True))
                    except (KeyError, ValueError, OSError):
                        return lambda current: "stale"
                assets.update(row[0] for row in conn.execute(
                    "SELECT id FROM material_assets WHERE workspace_id=? AND file_path=?",
                    (request.workspace_id, reference)))

        asset_ids_json = json.dumps(sorted(assets))
        person_ids_json = json.dumps(sorted(persons))
        def read(current):
            job_row = current.execute("SELECT task_id,kind,input_digest,status FROM job_runs WHERE workspace_id=? AND id=?",
                (request.workspace_id, request.owner_id)).fetchone()
            task_row = current.execute("SELECT plan_id,json_extract(payload,'$.confirmed_script_id'),json_extract(payload,'$.narration_cache_key'),json_extract(payload,'$.montage_plan_id'),json_extract(payload,'$.current_timeline_version_id'),json_extract(payload,'$.owner_user_id'),json_extract(payload,'$.visibility') FROM creation_tasks WHERE workspace_id=? AND id=?",
                (request.workspace_id, task.id)).fetchone()
            plan_row = current.execute("SELECT updated_at,visibility FROM creation_plans WHERE workspace_id=? AND id=?",
                (request.workspace_id, plan.id)).fetchone()
            script_row = current.execute("SELECT updated_at FROM creation_task_scripts WHERE workspace_id=? AND id=?",
                (request.workspace_id, script.id)).fetchone()
            montage_row = current.execute("SELECT id,updated_at FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=?",
                (request.workspace_id, task.id)).fetchone() if stage == "render" else None
            if stage == "montage_plan" and self.task_authorizations is None:
                source_rows = [tuple(row) for row in current.execute("SELECT id,updated_at FROM material_assets WHERE workspace_id=? ORDER BY id", (request.workspace_id,))]
            else:
                source_rows = [tuple(row) for row in current.execute(
                    "SELECT id,updated_at FROM material_assets WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                    (request.workspace_id,asset_ids_json))]
            person_rows = [tuple(row) for row in current.execute(
                "SELECT id,status,output_path,updated_at FROM person_renders WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                (request.workspace_id,person_ids_json))]
            proposal_row = current.execute("SELECT updated_at,status FROM edit_proposals WHERE workspace_id=? AND id=?",
                (request.workspace_id, event["edit_proposal_id"])).fetchone() if event.get("edit_proposal_id") else None
            narration_row = current.execute("SELECT id,created_at FROM narration_artifacts WHERE workspace_id=? AND cache_key=?",
                (request.workspace_id, task.narration_cache_key)).fetchone() if task.narration_cache_key else None
            subtitles = [tuple(row) for row in current.execute("SELECT id,updated_at FROM subtitle_tracks WHERE workspace_id=? AND task_id=? AND timeline_version_id=? ORDER BY id",
                (request.workspace_id, task.id, task.current_timeline_version_id))] if stage == "render" and task.current_timeline_version_id else []
            segments = [tuple(row) for row in current.execute(
                "SELECT id,updated_at FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? ORDER BY id",
                (request.workspace_id, task.id, script.id))] if stage in {"person_render", "person_edit", "narration_edit"} or plan.presenter_share_target else []
            if stage == "montage_plan" and plan.presenter_share_target:
                person_rows.extend(tuple(row) for row in current.execute(
                    """SELECT p.id,p.updated_at FROM person_renders p JOIN narration_segments n
                       ON n.workspace_id=p.workspace_id AND n.id=p.narration_segment_id
                       WHERE p.workspace_id=? AND p.task_id=? AND n.script_id=? ORDER BY p.id""",
                    (request.workspace_id, task.id, script.id)))
            return tuple(tuple(row) if row is not None else None for row in (job_row, task_row, plan_row, script_row, montage_row, proposal_row, narration_row)), source_rows, person_rows, subtitles, segments

        expected = read(conn)
        pcm = request.resources.get('pcm_assembly') if request.resources.get('execution_mode') == 'cpu_pcm_wav_assembly' else None
        observed_pcm_identity = [pcm['file_identity'] if pcm else None]
        if pcm is not None:
            from app.creation.narration_pcm import prefix_identity
            try:
                observed_pcm_identity[0]=prefix_identity(pcm)
            except (OSError,RecoveryBlocked):
                return lambda _:'stale'

        def check(current):
            if read(current) != expected:
                return "stale"
            if pcm is not None:
                from app.creation.narration_pcm import prefix_identity
                try:
                    identity = prefix_identity(pcm,observed_identity=observed_pcm_identity[0],verify_prefix=False)
                    if identity is not None:
                        observed_pcm_identity[0] = identity
                    for item in request.resources['pcm_original_sources']:
                        info=os.stat(item['path'])
                        if (info.st_mtime_ns,info.st_ctime_ns,info.st_size)!=(item['mtime_ns'],item['ctime_ns'],item['size_bytes']):
                            return 'stale'
                except (OSError,RecoveryBlocked):
                    return 'stale'
            if self.task_authorizations is not None:
                actor = CreationExecutionActor.from_event(task.workspace_id, event)
                verdict = self.authorization_verdict(task, actor, stage, connection=current, check_scope=False)
                if verdict != "ready":
                    return verdict
            return profiles(current)

        return check

    def check_local(self, request, conn, observed):
        return observed(conn)

    def can_manage(self, request: WorkRequest, actor: object) -> bool:
        return isinstance(actor, CreationExecutionActor) and (actor.workspace_id, actor.member_id) == (request.workspace_id, request.member_id)

    def can_wake(self, request: WorkRequest, actor: object) -> bool:
        return False

    def can_view_detail(self, request: WorkRequest, actor: object) -> bool:
        return self.can_manage(request, actor) and (
            self.verify_identity is None or self.verify_identity(actor) == "ready")

    def verify_retry(self, request: WorkRequest, actor: object, reason: str, conn: sqlite3.Connection):
        if request.unit_key.startswith("cover:") and self.cover is not None:
            raise PermissionError("封面只能续交原请求的固定回执，不能盲重试生成")
        if request.unit_key.startswith("task_media:") and self.task_media is not None:
            return self.task_media.verify_retry(request, actor, reason, conn)
        raise PermissionError("请通过已确认任务的继续制作入口重试")


def submit_creation_stage(execution: "CreationExecution", scheduler: DeviceScheduler,
                          job: JobRun, actor: CreationExecutionActor) -> str | None:
    """Queue exactly the next persisted stage; repeated submissions reuse one request."""
    if job.status not in {"queued", "interrupted"}:
        return None
    migrated = migrate_creation_narration_request(execution.repository, scheduler, job, actor)
    if migrated is not None:
        return migrated.id
    try:
        intent = next_creation_intent(execution.repository, scheduler, job)
    except (ValueError, PermissionError) as exc:
        # This is before scheduler.submit: the registered job has a known
        # rejection, rather than an unknown submission or a running worker.
        now = datetime.now(timezone.utc).isoformat()
        with execution.repository._connect() as conn:
            event = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (job.workspace_id, f"event_{job.id}_start")).fetchone()
            original = CreationExecutionActor.from_event(job.workspace_id, json.loads(event["payload_json"])) if event else None
            if original != actor:
                raise PermissionError("不能改变其他成员的制作作业") from exc
            changed = conn.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,"
                "finished_at=?,updated_at=? WHERE workspace_id=? AND id=? AND status IN ('queued','interrupted') "
                "AND NOT EXISTS (SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_id=? "
                "AND owner_type='creation_job' AND state IN ('waiting','blocked','granted','yield_requested'))",
                (str(exc), "当前阶段未提交设备队列，上游产物和已采用版本保留。",
                 "修复首个拒绝原因后点击继续制作。", now, now, job.workspace_id, job.id, job.workspace_id, job.id)).rowcount
            if changed:
                conn.execute("INSERT INTO task_events (id,workspace_id,task_id,job_id,event_type,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (f"event_{job.id}_admission_failed", job.workspace_id, job.task_id, job.id,
                    "creation_execution_stage", json.dumps({"stage": "admission", "status": "failed", "reason": str(exc)}, ensure_ascii=False), now))
        raise
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


def migrate_creation_narration_request(repository, scheduler, job, actor, *, write_guard=None):
    owner = scheduler.owners
    if hasattr(owner, 'registrations'):
        owner = owner.registrations[DEVICE_OWNER_TYPE].owner
    if (job.kind not in {KIND, NARRATION_EDIT_KIND, 'creation_task_segment_revision'} or owner.narration_budget is None
            or owner.narration_budget.input_scope != 'single_call_experiment_v1'):
        return None
    with repository._connect() as conn:
        rows = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
            "AND owner_id=? AND input_digest=? AND state!='cancelled' ORDER BY created_at,id",
            (job.workspace_id, job.id, job.input_digest)).fetchall()
        narration = [row for row in rows if row['unit_key'].startswith(('narration:', 'narration_edit:', 'segment_revision:'))]
        if not narration:
            return None
        # Completed/started units continue through their existing recovery owner;
        # this path only replaces the unique first, never-granted source request.
        pending = [row for row in narration if row['state'] != 'done']
        if not pending:
            return None
        old = scheduler._request(pending[-1])
        current, task, event, _script, plan = owner._facts(conn, job.workspace_id, job.id)
        stage = 'narration' if job.kind == KIND else 'narration_edit'
        if (CreationExecutionActor.from_event(job.workspace_id, event) != actor
                or owner.authorization_verdict(task, actor, stage, connection=conn) != 'ready'):
            raise PermissionError('原配音请求的成员或任务授权不匹配')
        legacy = old.unit_key.startswith('narration:') and old.unit_key.count(':') == 1
        if not legacy and owner.check_current(old, conn) == 'ready':
            if old.state in {'waiting', 'blocked'} and conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type='creation_narration_source_revalidated' AND json_extract(payload_json,'$.new_request_id')=?",
                    (job.workspace_id, job.id, old.id)).fetchone():
                return old
            return None
        if (old.last_grant_generation is not None or old.attempt_number != 1
                or current.attempt != 0 or current.started_at is not None):
            raise RecoveryBlocked('原配音已有授予或结果未知，不能重验证未执行来源')
        if len(narration) != 1:
            raise RecoveryBlocked('原作业来源请求不唯一，须先按原请求核对')
        source = repository.creation_narration_source_binding(job.workspace_id, job.id, connection=conn)
    from app.creation.narration_pcm import prepare_narration_source
    with prepare_narration_source(repository, task, plan, actor, binding=source.payload) as prepared:
        def audit(conn, previous, replacement):
            repository.append_task_event(TaskEvent(f'event_{replacement.id}_source_revalidated', job.workspace_id,
                job.task_id, job.id, 'creation_narration_source_revalidated',
                {'source_binding_event_id': source.id, 'old_request_id': previous.id,
                    'new_request_id': replacement.id, 'input_digest': job.input_digest,
                    'sha256': source.payload['sha256'], 'narration_reference': prepared['narration_reference']}),
                connection=conn)
        return scheduler.replace_ungranted_creation_narration(old.id,
            replacement_write=lambda conn, request: owner.migrate_narration_request(conn, request, actor,
                source_preparation=prepared, write_guard=write_guard), replacement_audit=audit)


def resume_original_narration(repository, scheduler, job, actor, *, write_guard=None, executor=None):
    usage = repository.creation_narration_usage(job.workspace_id, job.id)
    if usage['unsettled'] and executor is not None and executor.recover_pcm_unit(job,actor,scheduler,write_guard=write_guard):
        return True
    if usage['budget'] is None or job.status not in {'failed', 'cancelled'}:
        return False
    if usage['unsettled']:
        raise RecoveryBlocked('原配音单元消耗未知，继续前须核对原请求，不能创建新作业重置')
    with repository._connect() as conn:
        rows = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
            "AND owner_id=? AND state IN ('failed','cancelled') AND (unit_key LIKE 'narration:segment:v1:%' "
            "OR unit_key LIKE 'narration:merge:v1:%' OR unit_key LIKE 'segment_video:%') ORDER BY updated_at DESC LIMIT 2", (job.workspace_id, job.id)).fetchall()
    if len(rows) != 1:
        raise RecoveryBlocked('原配音没有唯一可继续单元；保留累计预算，先核对原作业')
    owner = scheduler.owners
    if hasattr(owner, 'registrations'):
        owner = owner.registrations[DEVICE_OWNER_TYPE].owner
    def resume(conn,request):
        if request.unit_key.startswith('segment_video:') and owner.segments is not None:
            return owner.segments.resume_unit(conn,request,actor,owner,write_guard=write_guard)
        return owner.resume_narration_request(conn,request,actor,write_guard=write_guard)
    scheduler.resume_creation_narration(rows[0]['id'],resume_write=resume)
    return True


def next_creation_intent(repository, scheduler: DeviceScheduler, job: JobRun, *, connection=None, pcm_preparation=None):
    owner = scheduler.owners
    if hasattr(owner, "registrations"):
        owner = owner.registrations[DEVICE_OWNER_TYPE].owner
    with nullcontext(connection) if connection is not None else repository._connect() as conn:
        job, task, event, script, plan = owner._facts(conn, job.workspace_id, job.id)
    if owner.segments is not None and job.kind == KIND and not event.get('operation'):
        intent = owner.segments.next_initial(job,owner,connection=connection)
        if intent is not None:
            return intent
    regenerate = event.get("operation") == "regenerate_storyboard"
    if plan.presenter_share_target and (owner.presenter_budget is None or owner.presenter_cpu_render_limits is None):
        raise ValueError("人物制作及人物组合 CPU 成片专属预算尚未标定")
    if regenerate:
        from app.creation.storyboard_regeneration import request_current
        if not request_current(repository, task, event):
            raise ValueError("故事板原请求的时间线、文案或配音已变化")
        stage = "montage_plan"
    elif not (task.narration_cache_key and repository.get_narration_artifact(
            task.workspace_id, task.narration_cache_key, connection=connection)):
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
        event = repository.get_task_event(job.workspace_id, f"event_{job.id}_start")
        actor = CreationExecutionActor.from_event(job.workspace_id, event.payload) if event.payload.get("member_id") else None
        with repository._connect() as conn:
            stage, _resources = owner.render_facts(conn, task, plan, actor=actor)
    elif stage == 'narration':
        with nullcontext(connection) if connection is not None else repository._connect() as conn:
            stage, _resources = owner.narration_unit_facts(conn, job, task, script, plan, pcm_preparation=pcm_preparation)
    elif stage == "person_render" or getattr(owner, stage.removesuffix("_plan") + "_budget", None) is not None:
        stage, _resources = (owner.stage_facts(task, script, plan, stage, connection=connection)
            if connection is not None else owner.stage_facts(task, script, plan, stage))
    if regenerate and stage != event["stage_key"]:
        raise ValueError("故事板原请求的来源已变化")
    return SubmissionIntent(job.workspace_id, DEVICE_OWNER_TYPE, job.id,
                            job.input_digest, stage, SchedulePolicy())


class CreationJobUnitExecutor:
    """Execute one granted creation stage and checkpoint it with the same permit."""

    def __init__(self, repository, *, render_action: Callable[[str, str, CpuRenderGrantContext], object], render_is_valid: Callable[[str, str, CpuRenderGrantContext], bool], montage_stage=None, task_media=None, narration_stage=None, person_stage=None, cover=None, on_render_completed=None, segments=None, native_case=None) -> None:
        self.repository = repository
        self.render_action = render_action
        self.render_is_valid = render_is_valid
        self.on_render_completed = on_render_completed
        self.montage_stage = montage_stage
        self.task_media = task_media
        self.cover = cover
        self.narration_stage = narration_stage
        self.person_stage = person_stage
        self.segments = segments
        self.native_case = native_case
        self._cancellations: dict[str, threading.Event] = {}
        self._cancellation_lock = threading.Lock()
        self._application_quit = threading.Event()
        self.last_resource_refusal: HostResourceRefusal | None = None

    def request_cancel(self, request_id: str) -> None:
        with self._cancellation_lock:
            event = self._cancellations.get(request_id)
        if event is not None:
            event.set()

    def register_cancellation(self, request_id, event):
        with self._cancellation_lock:
            self._cancellations[request_id] = event
            if self._application_quit.is_set():
                event.set()

    def request_stop(self):
        self._application_quit.set()
        with self._cancellation_lock:
            for event in self._cancellations.values():
                event.set()

    def _pause_for_quit(self, grant, scheduler):
        if not self._application_quit.is_set():
            return False
        if self.repository.get_job_run(grant.workspace_id, grant.owner_id).status == "cancelled":
            return False  # A prior explicit user cancellation keeps its terminal meaning.
        scheduler.defer_unit(grant, reason="worker_interrupted",
            retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=1),
            owner_database_path=self.repository.database_path,
            owner_write=lambda conn, current: self._write_interrupted(conn, current,
                scheduler=scheduler, stage=current.unit_key.split(":", 1)[0], reason="application_quit"))
        return True

    def _write_interrupted(self, conn, grant, *, scheduler, stage, reason, decision=None, permit_retained=False):
        job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
        owner = scheduler.owners
        if hasattr(owner, "registrations"):
            owner = owner.registrations[DEVICE_OWNER_TYPE].owner
        receipt = "stopped" if permit_retained else "pressure" if reason == "resource_pressure" else "permission"
        payload = {"stage": stage, "status": "interrupted", "reason": reason,
                   "request_id": grant.request_id, "permit_generation": grant.generation,
                   "permit_retained": permit_retained}
        if decision is not None:
            payload["decision"] = asdict(decision)
        self.repository.interrupt_creation_execution(TaskEvent(
            id=f"event_{job.id}_{receipt}_{grant.generation}", workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type="creation_execution_stage", payload=payload),
            impact="本次执行未完成，上游有效产物和已采用版本保留。",
            repair_action=("当前许可保留待核查；确认旧执行者和子进程已释放后再恢复。" if permit_retained
                           else "等待条件恢复；继续前将重新核验身份、输入和资源。"),
            task_is_current=lambda current: owner.owns_task_stage(current, job.workspace_id, job.id, stage),
            connection=conn)

    def record_stopped(self, grant, scheduler, exc):
        if grant.unit_key.startswith('native_command:') and self.native_case is not None:
            return self.native_case.record_stopped(grant,scheduler,exc)
        if grant.unit_key.startswith(('segment_video:', 'segment_revision:')) and self.segments is not None:
            return self.segments.record_stopped(grant,scheduler,exc)
        reason = str(exc) if isinstance(exc, RecoveryBlocked) else f"制作执行已停止（{type(exc).__name__}），须核查本次许可。"
        scheduler.record_owner_stop(grant, owner_database_path=self.repository.database_path,
            owner_write=lambda conn, current: self._write_interrupted(conn, current,
                scheduler=scheduler, stage=current.unit_key.split(":", 1)[0], reason=reason,
                decision=exc.decision if isinstance(exc, HostResourceRefusal) else None, permit_retained=True))

    def _defer_permission_loss(self, grant: Grant, scheduler: DeviceScheduler) -> bool:
        owner = scheduler.owners
        if hasattr(owner, "registrations"):
            owner = owner.registrations[DEVICE_OWNER_TYPE].owner
        if owner.verify_identity is None and owner.task_authorizations is None:
            return False  # Existing diagnostic owners retain their explicit recovery contract.
        # Defer proves group/resource release even when the owner is revoked or
        # temporarily unknown. Eligibility rechecks its precise current verdict.
        scheduler.defer_unit(grant, reason="worker_interrupted",
            retry_not_before_utc=datetime.now(timezone.utc) + timedelta(minutes=1),
            owner_database_path=self.repository.database_path,
            owner_write=lambda conn, current: self._write_interrupted(conn, current,
                scheduler=scheduler, stage=current.unit_key.split(":", 1)[0], reason="task_authorization_unavailable"))
        return True

    def _checkpoint(self, conn: sqlite3.Connection, grant: Grant, *, status: str,
                    output: str | None = None, reason: str | None = None, stage: str = "render") -> str:
        if status in {"queued", "succeeded"} and self._application_quit.is_set():
            from app.granted_subprocess import GrantedCommandCancelled
            raise GrantedCommandCancelled("应用退出，保存原任务暂停点")
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
        start = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                             (grant.workspace_id, f"event_{grant.owner_id}_start")).fetchone()
        regeneration = start is not None and json.loads(start["payload_json"]).get("operation") == "regenerate_storyboard"
        if not regeneration and stage in {"narration", "montage_plan"} and status in {"failed", "cancelled"}:
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

    def recover_pcm_unit(self, job, actor, scheduler, *, write_guard=None):
        """Adopt a sealed original attempt after proving its process group released."""
        from app.creation.narration_pcm import prepared_receipt
        usage=self.repository.creation_narration_usage(job.workspace_id,job.id)
        if not usage['unsettled']:
            return False
        active=usage['unsettled'][0]
        request=scheduler._get(active['request_id'])
        if request.resources.get('execution_mode')!='cpu_pcm_wav_assembly':
            return False
        if active['usage_id']!=f'{request.id}:{request.last_grant_generation}':
            raise RecoveryBlocked('原PCM开始记录与原许可代次不符')
        free_generation=scheduler.reconcile_released()
        request=scheduler._get(request.id)
        owner=scheduler.owners
        if hasattr(owner,'registrations'):
            owner=owner.registrations[DEVICE_OWNER_TYPE].owner
        with self.repository._connect() as conn:
            original,task,event,script,plan=owner._facts(conn,job.workspace_id,job.id)
            if (CreationExecutionActor.from_event(job.workspace_id,event)!=actor
                    or original.status=='cancelled'
                    or owner.authorization_verdict(task,actor,request.unit_key.split(':',1)[0],connection=conn)!='ready'):
                raise PermissionError('原PCM恢复的身份、授权或任务已变化')
            baseline=owner.pcm_assembly_baseline(request,conn,original_recovery=True)
            started_event=self.repository.get_task_event(job.workspace_id,
                f'event_{job.id}_narration_started_{request.last_grant_generation}',connection=conn)
        resources=request.resources
        receipt_path=Path(baseline['receipt_path'])
        with prepared_receipt(receipt_path if receipt_path.exists() else baseline['work_path']+'.sealed.json',
            unit_id=active['usage_id'],job_id=job.id,source_digest=resources['merge_source_digest'],
            baseline=baseline,sources=resources['pcm_sources'],final=resources['merge_final']) as (receipt,verify_prepared):
            finished=datetime.fromisoformat(receipt['finished_at_utc'])
            began=datetime.fromisoformat(receipt['started_at_utc'])
            elapsed=(finished-started_event.created_at).total_seconds()
            if not 0<=receipt['elapsed_seconds']<=elapsed or began<started_event.created_at or finished<began:
                raise RecoveryBlocked('原PCM回执时钟与原started不一致')
            grant=Grant(request.id,request.workspace_id,request.owner_type,request.owner_id,request.unit_key,
                request.input_digest,request.attempt_number,request.last_grant_generation,'',scheduler.database_path)
            commit=None
            if receipt['finalized'] and job.kind==NARRATION_EDIT_KIND:
                candidate=self.repository.narration_edit_generated(job.workspace_id,job.id)
                commit=self.narration_stage.prepare_edit_adoption(job.workspace_id,job.task_id,event['edit_proposal_id'],
                    candidate,resources,receipt)
            segments=self.repository.list_narration_segments(job.workspace_id,job.task_id,resources['script_id'])
            by_section={item.source_section_id:item for item in segments}
            ordered=[by_section[section.id] for section in script.sections]
            record=self.narration_stage.pcm_record(task,resources,receipt,ordered) if receipt['finalized'] and job.kind==KIND else None
            with self.repository._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if write_guard is not None:
                    write_guard(conn)
                if owner.pcm_assembly_baseline(request,conn,original_recovery=True)!=baseline:
                    raise RecoveryBlocked('原PCM恢复提交前依赖已变化')
                current_job,current_task,current_event,current_script,current_plan=owner._facts(conn,job.workspace_id,job.id)
                if (owner._check_facts(current_job,current_task,current_event,current_script,current_plan,
                        request.unit_key.split(':',1)[0],actor.member_id)!='ready'
                        or owner.authorization_verdict(current_task,actor,request.unit_key.split(':',1)[0],connection=conn)!='ready'):
                    raise PermissionError('原PCM恢复提交前授权或目标已变化')
                verify_prepared()
                self.repository.settle_creation_narration_unit(grant,elapsed_seconds=elapsed,
                    output_bytes=receipt['generated_bytes'],connection=conn)
                conn.execute("UPDATE job_runs SET status='running' WHERE workspace_id=? AND id=? AND status IN ('interrupted','running','queued')",
                    (job.workspace_id,job.id))
                if receipt['finalized']:
                    if job.kind==NARRATION_EDIT_KIND:
                        checkpoint=self._narration_edit_checkpoint(conn,grant,'succeeded',commit=commit)
                    else:
                        current_task.stage_status,current_task.status='running','running'
                        current_task.stage_reason=current_task.stage_impact=current_task.stage_repair_action=current_task.stage_retryable=None
                        conn.execute('UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?',
                            (json.dumps(asdict(current_task),default=str),datetime.now(timezone.utc).isoformat(),job.workspace_id,job.task_id))
                        self.repository.complete_creation_task_narration(record,ordered,connection=conn)
                        checkpoint=self._checkpoint(conn,grant,status='queued',output=record.cache_key,stage='narration')
                else:
                    checkpoint=self.repository.checkpoint_creation_narration_merge(grant,
                        source_digest=resources['merge_source_digest'],section_ids=resources['merge_section_ids'],
                        input_references=[item['reference'] for item in resources['inputs']],pcm_receipt=receipt,connection=conn)
                scheduler.complete_recovered_narration_pcm(conn,request,generation=free_generation,checkpoint_ref=checkpoint)
            return True

    def _release_unknown_pcm(self, grant, scheduler):
        scheduler.defer_unit(grant,reason='worker_interrupted',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=1),
            owner_database_path=self.repository.database_path,
            owner_write=lambda conn,current:self._write_interrupted(conn,current,scheduler=scheduler,
                stage=grant.unit_key.split(':',1)[0],reason='pcm_result_unknown'))

    def _execute_narration_unit(self, grant, scheduler):
        from app.granted_subprocess import GrantedCommandCancelled
        if self.narration_stage is None:
            raise PermissionError('配音缺少单段执行用例')
        with self.repository._connect() as conn:
            row = conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?', (grant.request_id,)).fetchone()
            permit = conn.execute('SELECT granted_at_utc FROM device_permit WHERE id=1 AND request_id=?', (grant.request_id,)).fetchone()
        if row is None or permit is None or not permit['granted_at_utc']:
            raise PermissionError('配音单元缺少原请求设备许可')
        resources = json.loads(row['resources_json'])
        if (resources.get('input_contract') != 'narration_units_v1'
                or resources.get('narration_phase') not in {'segment', 'merge'}):
            raise PermissionError('原整阶段配音契约须先转换为逐段执行')
        scheduler.assert_grant(grant)
        owner = scheduler.owners
        if hasattr(owner, 'registrations'):
            owner = owner.registrations[DEVICE_OWNER_TYPE].owner
        with self.repository._connect() as conn:
            job, task, event, script, plan = owner._facts(conn, grant.workspace_id, grant.owner_id)
        pending = [NarrationSegment(id=self.narration_stage.segment_id(task.id, script.id, section.id),
            workspace_id=task.workspace_id, task_id=task.id, script_id=script.id, source_section_id=section.id,
            text=section.content, audio_reference=None, duration_seconds=None, sample_rate=None,
            model_id=resources['model_id'], voice_profile_id=resources['voice_profile_id'], voice_version=resources['voice_version'],
            language=resources['language'], pronunciation_version=resources['pronunciation_version'], status='pending')
            for section in script.sections]
        job, remaining_seconds, remaining_bytes = self.repository.start_creation_narration_unit(grant, resources, pending)
        already_elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit['granted_at_utc'])).total_seconds())
        cancellation = threading.Event()
        started = time.monotonic() - already_elapsed
        context = CreationStageGrantContext(scheduler, grant,
            started + min(remaining_seconds, resources['timeout_seconds']),
            min(remaining_bytes, resources['max_output_bytes']), resources['threads'], cancellation,
            started_monotonic=started, narration_output_usage=[0])
        self.register_cancellation(grant.request_id, cancellation)
        event = self.repository.get_task_event(job.workspace_id, f'event_{job.id}_start')
        actor = CreationExecutionActor.from_event(job.workspace_id, event.payload)
        result_bytes = 0
        output_file = None
        previous_output = self.repository.previous_creation_narration_output(grant)

        def settle(conn, current):
            measured = context.narration_output_usage[0]
            if measured is None:
                raise RecoveryBlocked('本次配音输出消耗未知，保留原开始记录等待核对')
            return self.repository.settle_creation_narration_unit(current,
                elapsed_seconds=max(0, time.monotonic() - started),
                output_bytes=max(measured, 0 if output_file is not None and output_file == previous_output else result_bytes),
                output_file=output_file, connection=conn)

        pcm_preparation=None
        def continue_unit(conn, current):
            usage = self.repository.creation_narration_usage(job.workspace_id, job.id, connection=conn)
            limits = usage['budget']
            current_task = self.repository.get_creation_task(job.workspace_id, job.task_id, connection=conn)
            if (current_task.stage == 'narration'
                    and (usage['used_seconds'] >= limits['parent_timeout_seconds']
                         or usage['used_output_bytes'] >= limits['parent_max_output_bytes'])):
                self.repository.exhaust_creation_narration_budget(current, job.task_id, connection=conn)
                return None
            intent = next_creation_intent(self.repository, scheduler, job, connection=conn, pcm_preparation=pcm_preparation)
            return (intent, owner.verify_submission(intent, actor, connection=conn, pcm_preparation=pcm_preparation)) if intent is not None else None

        try:
            context.check()
            if resources['narration_phase'] == 'segment':
                artifact, segment, result_bytes = self.narration_stage.prepare_segment(job.workspace_id, job.task_id,
                    script_id=resources['script_id'], section_id=resources['section_id'], grant_context=context)
            else:
                artifact, result_bytes, pcm_receipt = self.narration_stage.prepare_merge(job.workspace_id, job.task_id,
                    script_id=resources['script_id'],
                    input_references=[item['reference'] for item in resources['inputs']], grant_context=context)
                segment = None
            pcm_preparation=owner.prepare_pcm_sources(job,generated=artifact if segment is not None else None,
                section_id=segment.source_section_id if segment is not None else None) if segment is not None or not resources['merge_final'] else None
            video_preparation = (self.segments.prepare_video(job,event.payload,task,segment.source_section_id,
                artifact,owner,text=segment.text) if self.segments is not None and segment is not None else None)
            if artifact is not None:
                try:
                    output_stat = Path(artifact.audio_path).stat()
                except OSError as exc:
                    context.narration_output_usage[0] = None
                    raise RecoveryBlocked('配音最终产物的消耗状态不可读取') from exc
                output_file = {'cache_key': artifact.cache_key, 'size_bytes': output_stat.st_size,
                    'mtime_ns': output_stat.st_mtime_ns, 'ctime_ns': output_stat.st_ctime_ns,
                    'path_identity': hashlib.sha256(str(Path(artifact.audio_path).resolve()).encode()).hexdigest()}
            context.check()

            def commit(conn, current):
                if self._application_quit.is_set():
                    raise GrantedCommandCancelled('应用退出，先结算原配音单元消耗')
                settle(conn, current)
                if segment is None:
                    if not resources['merge_final']:
                        return self.repository.checkpoint_creation_narration_merge(current,
                            source_digest=resources['merge_source_digest'], section_ids=resources['merge_section_ids'],
                            input_references=[item['reference'] for item in resources['inputs']],
                            pcm_receipt=pcm_receipt, connection=conn)
                    segments = self.repository.list_narration_segments(job.workspace_id, job.task_id,
                        resources['script_id'], connection=conn)
                    self.repository.complete_creation_task_narration(artifact, segments, connection=conn)
                    return self._checkpoint(conn, current, status='queued', output=artifact.cache_key, stage='narration')
                if video_preparation is not None:
                    self.repository.append_task_event(video_preparation,connection=conn)
                return self.repository.checkpoint_creation_narration_segment(current, artifact, segment, resources['ordinal'], connection=conn)

            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                checkpoint_write=commit, next_unit_write=continue_unit)
        except Exception as exc:
            if isinstance(exc, HostResourceRefusal):
                self.last_resource_refusal = exc
            if resources.get('execution_mode')=='cpu_pcm_wav_assembly' and (
                    context.narration_output_usage[0] is None or context.narration_output_usage[0]>0
                    or Path(resources['pcm_assembly']['receipt_path']+'.manifest.json').exists()):
                context.narration_output_usage[0]=None
                if self.repository.get_job_run(job.workspace_id,job.id).status=='cancelled':
                    scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                        checkpoint_write=lambda conn,current:self._checkpoint(conn,current,status='cancelled',
                            reason='user_cancelled_result_unknown',stage='narration'),cancelled=True)
                else:
                    self._release_unknown_pcm(grant,scheduler)
                raise RecoveryBlocked('原PCM结果未知，释放进程后查询同一started回执') from exc
            if isinstance(exc, RecoveryBlocked) and not (
                    isinstance(exc, HostResourceRefusal) and exc.recoverable_pressure):
                raise
            cancelled = self.repository.get_job_run(job.workspace_id, job.id).status == 'cancelled'
            quitting = self._application_quit.is_set() and not cancelled
            pressure = isinstance(exc, HostResourceRefusal) and exc.recoverable_pressure
            diagnostic = getattr(exc, 'command_diagnostics', None)
            if pressure and diagnostic and diagnostic.get('before_termination_returncode') not in {None, 0}:
                pressure = False
            if quitting or pressure or isinstance(exc, PermissionError):
                reason = 'application_quit' if quitting else 'resource_pressure' if pressure else 'task_authorization_unavailable'
                def interrupted(conn, current):
                    settle(conn, current)
                    self._write_interrupted(conn, current, scheduler=scheduler, stage='narration', reason=reason,
                        decision=exc.decision if isinstance(exc, HostResourceRefusal) else None)
                scheduler.defer_unit(grant, reason='resource_pressure' if pressure else 'worker_interrupted',
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30 if pressure else 1 if quitting else 60),
                    owner_database_path=self.repository.database_path, owner_write=interrupted)
            elif cancelled or isinstance(exc, GrantedCommandCancelled):
                def stopped(conn, current):
                    settle(conn, current)
                    return self._checkpoint(conn, current, status='cancelled', stage='narration', reason='user_cancelled')
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=stopped, cancelled=True)
            else:
                def failed(conn, current):
                    settle(conn, current)
                    return self._checkpoint(conn, current, status='failed', stage='narration', reason=str(exc))
                scheduler.fail_unit(grant, reason='creation_narration_failed', failure_write=failed)
        finally:
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)

    def _execute_upstream(self, grant, scheduler, stage):
        from app.granted_subprocess import GrantedCommandCancelled
        if stage == 'person_render' and self.person_stage is None or (stage != 'person_render' and (stage == 'narration' and self.narration_stage is None or (stage != 'narration' and (stage != 'montage_plan' or self.montage_stage is None)))):
            raise PermissionError('该制作阶段没有受许可执行者')
        with self.repository._connect() as conn:
            row = conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?', (grant.request_id,)).fetchone()
            permit = conn.execute('SELECT granted_at_utc FROM device_permit WHERE id=1').fetchone()
        if row is None or permit is None or (not permit['granted_at_utc']):
            raise PermissionError('制作阶段缺少当前设备许可')
        resources = json.loads(row['resources_json'])
        if resources.get('kind') != f'creation_{stage}' or 'timeout_seconds' not in resources:
            raise PermissionError('制作阶段资源预算未标定')
        elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit['granted_at_utc'])).total_seconds())
        cancellation = threading.Event()
        context = CreationStageGrantContext(scheduler, grant, time.monotonic() + resources['timeout_seconds'] - elapsed, resources['max_output_bytes'], resources.get('threads', 1), cancellation)
        self.register_cancellation(grant.request_id, cancellation)
        job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
        try:
            context.check()
            if job.status == 'running':
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'", (job.workspace_id, job.id))
            job = self.repository.claim_job_run(job.workspace_id, job.id)
            if stage == 'person_render':
                self.repository.append_task_event(TaskEvent(id=f'event_{job.id}_person_render_running_{grant.request_id}_{grant.generation}', workspace_id=job.workspace_id, task_id=job.task_id, job_id=job.id, event_type='creation_execution_stage', payload={'stage': stage, 'status': 'running'}, created_at=datetime.now(timezone.utc)))
            current_task = self.repository.get_creation_task(job.workspace_id, job.task_id)
            owner = scheduler.owners
            if hasattr(owner, 'registrations'):
                owner = owner.registrations[DEVICE_OWNER_TYPE].owner

            event = self.repository.get_task_event(job.workspace_id, f'event_{job.id}_start').payload
            if event.get('operation') == 'regenerate_storyboard':
                from app.creation.storyboard_regeneration import StoryboardRegeneration
                if stage != 'montage_plan':
                    raise PermissionError('故事板重算不得执行配音或渲染')
                regeneration = StoryboardRegeneration(self.repository, self.montage_stage)
                prepared = regeneration.prepare(job, event, context)
                context.check()
                def commit_storyboard(conn, current):
                    output = regeneration.commit(job, event, prepared, conn)
                    return self._checkpoint(conn, current, status='succeeded', output=output, stage=stage)
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=commit_storyboard)
                return

            def valid(current):
                return owner.person_valid(current) if stage == 'person_render' else upstream_stage_valid(self.repository, current, stage) or (stage == 'montage_plan' and montage_waiting_for_confirmation(self.repository, current))
            if not valid(current_task):
                if stage == 'person_render':
                    self.person_stage.generate_stage(job.workspace_id, job.task_id, context)
                elif stage == 'narration':
                    raise PermissionError('整阶段配音执行已退役，须使用单段或合并许可')
                else:
                    self.montage_stage.generate(job.workspace_id, job.task_id, grant_context=context)
            context.check()
            task = self.repository.get_creation_task(job.workspace_id, job.task_id)
            if not valid(task):
                raise ValueError('制作阶段未产生匹配当前文案的可用结果')
            original = CreationExecutionActor.from_event(job.workspace_id, self.repository.get_task_event(job.workspace_id, f'event_{job.id}_start').payload)
            next_intent = next_creation_intent(self.repository, scheduler, job)
            output = task.narration_cache_key if stage == 'narration' else ','.join((item.id for item in self.repository.list_person_renders(task.workspace_id, task.id) if item.status == 'ready')) if stage == 'person_render' else task.montage_plan_id
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path, checkpoint_write=lambda conn, current: self._checkpoint(conn, current, status='queued', output=output, stage=stage, reason='waiting_for_user' if next_intent is None else None), next_intent=next_intent, actor=original)
        except HostResourceRefusal as exc:
            if self._pause_for_quit(grant, scheduler):
                return
            self.last_resource_refusal = exc
            if not exc.recoverable_pressure:
                raise
            diagnostics = getattr(exc, 'command_diagnostics', None)
            natural_exit = diagnostics.get('before_termination_returncode') if diagnostics else None
            if natural_exit is not None and natural_exit != 0:
                reason = diagnostics['stderr'].decode('utf-8', errors='replace')[-4000:]
                scheduler.fail_unit(grant, reason=f'creation_{stage}_failed', failure_write=lambda conn, current: self._checkpoint(conn, current, status='failed', reason=reason or f'worker exit {natural_exit}', stage=stage))
            elif self.repository.get_job_run(job.workspace_id, job.id).status == 'cancelled' or cancellation.is_set():
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path, checkpoint_write=lambda conn, current: self._checkpoint(conn, current, status='cancelled', stage=stage), cancelled=True)
            else:
                scheduler.defer_unit(grant, reason='resource_pressure',
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30),
                    owner_database_path=self.repository.database_path,
                    owner_write=lambda conn, current: self._write_interrupted(conn, current,
                        scheduler=scheduler, stage=stage, reason="resource_pressure", decision=exc.decision))
        except RecoveryBlocked:
            raise
        except GrantedCommandCancelled:
            if self._pause_for_quit(grant, scheduler):
                return
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path, checkpoint_write=lambda conn, current: self._checkpoint(conn, current, status='cancelled', reason='user_cancelled', stage=stage), cancelled=True)
        except Exception as exc:
            if self._pause_for_quit(grant, scheduler):
                return
            if self.repository.get_job_run(job.workspace_id, job.id).status == 'cancelled':
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path, checkpoint_write=lambda conn, current: self._checkpoint(conn, current, status='cancelled', stage=stage), cancelled=True)
            elif isinstance(exc, PermissionError):
                if not self._defer_permission_loss(grant, scheduler):
                    raise
            else:
                reason = str(exc)
                scheduler.fail_unit(grant, reason=f'creation_{stage}_failed', failure_write=lambda conn, current: self._checkpoint(conn, current, status='failed', reason=reason, stage=stage))
        finally:
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)

    def _person_edit_checkpoint(self, conn, grant, status, *, commit=None, reason=None):
        if status == "succeeded" and self._application_quit.is_set():
            from app.granted_subprocess import GrantedCommandCancelled
            raise GrantedCommandCancelled("应用退出，保存原任务暂停点")
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
        self.register_cancellation(grant.request_id, cancellation)
        try:
            job = self.repository.get_job_run(grant.workspace_id, grant.owner_id)
            with self.repository._connect() as conn:
                row = conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?', (grant.request_id,)).fetchone()
                permit = conn.execute('SELECT granted_at_utc FROM device_permit WHERE id=1').fetchone()
            resources = json.loads(row['resources_json'])
            if resources.get('kind') != 'creation_person_render' or not permit['granted_at_utc']:
                raise PermissionError('人物编辑缺少专属资源许可')
            elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(permit['granted_at_utc'])).total_seconds())
            context = CreationStageGrantContext(scheduler, grant, time.monotonic() + resources['timeout_seconds'] - elapsed, resources['max_output_bytes'], resources['threads'], cancellation)
            if job.status == 'running':
                with self.repository._connect() as conn:
                    conn.execute("UPDATE job_runs SET status='interrupted' WHERE workspace_id=? AND id=? AND status='running'", (job.workspace_id, job.id))
            if job.status != 'cancelled':
                job = self.repository.claim_job_run(job.workspace_id, job.id)
            context.check()
            event = self.repository.get_task_event(job.workspace_id, f'event_{job.id}_start')
            commit = self.person_stage.prepare_edit(job.workspace_id, job.task_id, context, event.payload['edit_proposal_id'])
            context.check()
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path, checkpoint_write=lambda conn, current: self._person_edit_checkpoint(conn, current, 'succeeded', commit=commit))
        except RecoveryBlocked:
            raise
        except Exception as exc:
            if self._pause_for_quit(grant, scheduler):
                return
            if isinstance(exc, PermissionError) and self._defer_permission_loss(grant, scheduler):
                return
            cancelled = isinstance(exc, GrantedCommandCancelled) or cancellation.is_set() or self.repository.get_job_run(grant.workspace_id, grant.owner_id).status == 'cancelled'
            if cancelled:
                scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path, checkpoint_write=lambda conn, current: self._person_edit_checkpoint(conn, current, 'cancelled', reason='user_cancelled'), cancelled=True)
            else:
                reason = str(exc)
                scheduler.fail_unit(grant, reason='person_edit_failed', failure_write=lambda conn, current: self._person_edit_checkpoint(conn, current, 'failed', reason=reason))
        finally:
            reset_workspace_context(token)
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id, None)

    def _narration_edit_checkpoint(self, conn, grant, status, *, commit=None, reason=None):
        if status == "succeeded" and self._application_quit.is_set():
            from app.granted_subprocess import GrantedCommandCancelled
            raise GrantedCommandCancelled("应用退出，保存原任务暂停点")
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
        with self.repository._connect() as conn:
            row=conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?',(grant.request_id,)).fetchone()
            permit=conn.execute('SELECT granted_at_utc FROM device_permit WHERE id=1 AND request_id=?',(grant.request_id,)).fetchone()
        resources=json.loads(row['resources_json'])
        if resources.get('input_contract')!='narration_units_v1' or permit is None or not permit['granted_at_utc']:
            raise PermissionError('旧局部配音请求须先按原Job转换为有界单元，未调用模型')
        owner=scheduler.owners
        if hasattr(owner,'registrations'):
            owner=owner.registrations[DEVICE_OWNER_TYPE].owner
        with self.repository._connect() as conn:
            _job,task,event,_script,plan=owner._facts(conn,grant.workspace_id,grant.owner_id)
        actor=CreationExecutionActor.from_event(grant.workspace_id,event)
        job,remaining_seconds,remaining_bytes=self.repository.start_creation_narration_unit(grant,resources,[])
        started=time.monotonic()-max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(permit['granted_at_utc'])).total_seconds())
        cancellation=threading.Event()
        context=CreationStageGrantContext(scheduler,grant,started+min(remaining_seconds,resources['timeout_seconds']),
            min(remaining_bytes,resources['max_output_bytes']),resources['threads'],cancellation,
            started_monotonic=started,narration_output_usage=[0])
        self.register_cancellation(grant.request_id,cancellation)
        def settle(conn,current):
            measured=context.narration_output_usage[0]
            if measured is None:
                raise RecoveryBlocked('原局部配音的消耗未知，保留原started等待对账')
            return self.repository.settle_creation_narration_unit(current,
                elapsed_seconds=max(0,time.monotonic()-started),output_bytes=measured,connection=conn)
        pcm_preparation=None
        def next_unit(conn,current):
            current_job=self.repository.get_job_run(job.workspace_id,job.id,connection=conn)
            current_task=self.repository.get_creation_task(job.workspace_id,job.task_id,connection=conn)
            key,_=owner.narration_edit_facts(current_job,current_task,event,plan,connection=conn,pcm_preparation=pcm_preparation)
            intent=SubmissionIntent(job.workspace_id,DEVICE_OWNER_TYPE,job.id,job.input_digest,key,SchedulePolicy())
            return intent,owner.verify_submission(intent,actor,connection=conn,pcm_preparation=pcm_preparation)
        try:
            if resources['narration_phase']=='segment_edit':
                proposal=self.repository.get_edit_proposal(job.workspace_id,event['edit_proposal_id'])
                payload=proposal.operation['payload']
                segment,artifact=self.narration_stage.generate_segment_edit(workspace_id=job.workspace_id,task=task,
                    segment_id=payload['narration_segment_id'],delivery_instruction=payload.get('delivery_instruction'),
                    generation_identity=proposal.id,context=context)
                pcm_preparation=owner.prepare_pcm_sources(job,generated=artifact,section_id=segment.source_section_id)
                context.narration_output_usage[0]=max(context.narration_output_usage[0],Path(artifact.audio_path).stat().st_size)
                def checkpoint(conn,current):
                    settle(conn,current)
                    return self.repository.checkpoint_narration_edit_generated(current,artifact,segment,grant.unit_key,connection=conn)
                scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=checkpoint,next_unit_write=next_unit)
            else:
                commit,receipt=self.narration_stage.prepare_edit(job.workspace_id,job.task_id,context,event['edit_proposal_id'],resources)
                pcm_preparation=owner.prepare_pcm_sources(job) if not receipt['finalized'] else None
                def checkpoint(conn,current):
                    settle(conn,current)
                    if receipt['finalized']:
                        return self._narration_edit_checkpoint(conn,current,'succeeded',commit=commit)
                    return self.repository.checkpoint_creation_narration_merge(current,
                        source_digest=resources['merge_source_digest'],section_ids=resources['merge_section_ids'],
                        input_references=[item['reference'] for item in resources['inputs']],pcm_receipt=receipt,connection=conn)
                scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=checkpoint,next_unit_write=None if receipt['finalized'] else next_unit)
        except Exception as exc:
            pcm_started=resources.get('execution_mode')=='cpu_pcm_wav_assembly' and (
                context.narration_output_usage[0] is None or context.narration_output_usage[0]>0
                or Path(resources['pcm_assembly']['receipt_path']+'.manifest.json').exists())
            cancelled=self.repository.get_job_run(job.workspace_id,job.id).status=='cancelled'
            if context.narration_output_usage[0] is None or pcm_started:
                context.narration_output_usage[0]=None
                if cancelled:
                    scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                        checkpoint_write=lambda conn,current:self._narration_edit_checkpoint(conn,current,'cancelled',
                            reason='user_cancelled_result_unknown'),cancelled=True)
                else:
                    self._release_unknown_pcm(grant,scheduler)
                raise RecoveryBlocked('原局部配音结果或消耗未知，须查询原单元') from exc
            if cancelled:
                def cancel(conn,current):
                    settle(conn,current)
                    return self._narration_edit_checkpoint(conn,current,'cancelled',reason='user_cancelled')
                scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,
                    checkpoint_write=cancel,cancelled=True)
                return
            if self._application_quit.is_set():
                def pause(conn,current):
                    settle(conn,current)
                    return self._write_interrupted(conn,current,scheduler=scheduler,stage='narration_edit',reason='application_quit')
                scheduler.defer_unit(grant,reason='worker_interrupted',
                    retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=1),
                    owner_database_path=self.repository.database_path,owner_write=pause)
                return
            def fail(conn,current):
                settle(conn,current)
                return self._narration_edit_checkpoint(conn,current,'failed',reason=str(exc))
            scheduler.fail_unit(grant,reason='narration_edit_failed',failure_write=fail)
        finally:
            with self._cancellation_lock:
                self._cancellations.pop(grant.request_id,None)

    def execute(self, grant: Grant, scheduler: DeviceScheduler) -> None:
        if grant.unit_key.startswith('native_command:') and self.native_case is not None:
            return self.native_case.execute(grant,scheduler,self)
        if grant.unit_key.startswith(('segment_video:', 'segment_revision:')) and self.segments is not None:
            return self.segments.execute(grant,scheduler,self)
        if self._pause_for_quit(grant, scheduler):
            return
        try:
            self._execute(grant, scheduler)
        except RecoveryBlocked:
            raise
        except Exception:
            if not self._pause_for_quit(grant, scheduler):
                raise

    def _execute(self, grant: Grant, scheduler: DeviceScheduler) -> None:
        if grant.owner_type == DEVICE_OWNER_TYPE and grant.unit_key.startswith(('narration:segment:v1:', 'narration:merge:v1:')):
            return self._execute_narration_unit(grant, scheduler)
        if grant.owner_type == DEVICE_OWNER_TYPE and grant.unit_key.startswith("cover:") and self.cover is not None:
            return self.cover.execute(grant, scheduler, self)
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
        self.register_cancellation(grant.request_id, cancellation)
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
                if self._pause_for_quit(grant, scheduler):
                    return
                cancellation.set()
            except HostResourceRefusal as exc:
                if self._pause_for_quit(grant, scheduler):
                    return
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
                        retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30),
                        owner_database_path=self.repository.database_path,
                        owner_write=lambda conn, current: self._write_interrupted(conn, current,
                            scheduler=scheduler, stage="render", reason="resource_pressure", decision=exc.decision))
                    return
            except RecoveryBlocked:
                raise
            except Exception as exc:
                if self._pause_for_quit(grant, scheduler):
                    return
                if cancellation.is_set() or self.repository.get_job_run(job.workspace_id, job.id).status == "cancelled":
                    cancellation.set()
                else:
                    if isinstance(exc, PermissionError) and self._defer_permission_loss(grant, scheduler):
                        return
                    detail = getattr(exc, "detail", None)
                    reason = (str(detail.get("reason") or detail.get("error") or exc)
                              if isinstance(detail, dict)
                              else str(detail or exc).strip() or type(exc).__name__)
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
                return
            if self._application_quit.is_set():
                return
            if self.on_render_completed is not None:
                try:
                    self.on_render_completed(job.workspace_id, job.task_id, job.id)
                except Exception:
                    logger.exception("Rendered output committed; independent quality scheduling remains pending")
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
        run_montage_plan: Callable[[str, str], object],
        run_render: Callable[[str, str], object],
        render_is_valid: Callable[[str, str], bool],
    ) -> None:
        self.repository = repository
        self.runtime = TaskRuntime(repository)
        self.actions = {
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
              *, member_id: str | None = None, actor: CreationExecutionActor | None = None, write_guard=None, execution_source=None) -> JobRun:
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
        from app.creation.narration_pcm import prepare_narration_source
        existing = any(job.idempotency_key == key for job in self.jobs(workspace_id, task_id))
        source_context = (prepare_narration_source(self.repository, task, plan, actor)
            if actor is not None and task.stage == 'narration' and plan.selected_voice is not None and narration is None and not existing
            else nullcontext(None))
        with source_context as source_preparation:
            run = self.repository.create_creation_execution_job(
                JobRun(
                    id=f"job_{uuid4().hex}",
                    workspace_id=workspace_id,
                    task_id=task_id,
                    kind=KIND,
                    idempotency_key=key,
                    input_digest=digest,
                )
                , start_payload={"script_id":script_id,"plan_id":task.plan_id,"input_digest":digest,
                    **(actor.identity_payload() if actor is not None else {"member_id":member_id} if member_id is not None else {})},
                write_guard=write_guard,execution_source=execution_source,source_preparation=source_preparation)
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

    def regenerate_storyboard(self, workspace_id, task_id, inputs, actor, *, stage_facts):
        from app.creation.storyboard_regeneration import StoryboardRegeneration
        task = self.repository.get_creation_task(workspace_id, task_id)
        return StoryboardRegeneration(self.repository).enqueue(task, inputs, actor,
            validate_input=self.validate_input, stage_facts=stage_facts)

    def retry(self, workspace_id: str, task_id: str, *, member_id: str | None = None, write_guard=None, execution_source=None,
              admission_recheck=None) -> JobRun:
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
        if self.repository.creation_narration_usage(workspace_id, latest.id)['budget'] is not None:
            raise ValueError('原配音已有累计消耗，须继续原作业单元，不能创建新作业重置额度')
        script_id = str(event.payload["script_id"])
        task = self.repository.get_creation_task(workspace_id, task_id)
        _, plan = self.validate_input(workspace_id, task, script_id, latest.input_digest)
        if task.stage == 'narration' and task.narration_cache_key is None and plan.selected_voice is not None:
            self.repository.creation_narration_source_binding(workspace_id, latest.id)
        rejection=creation_input_rejection(self.repository.list_task_events(workspace_id,task_id),latest.id)
        if rejection is not None and (admission_recheck is None or not admission_recheck(rejection)):
            raise CreationInputRejected(rejection)
        same_input = [
            job for job in self.jobs(workspace_id, task_id)
            if job.input_digest == latest.input_digest
        ]
        retry_number = len(same_input)
        resumed_payload=dict(self.repository.creation_execution_input(workspace_id, latest.id))
        if resumed_payload.get("operation")=="regenerate_storyboard":
            resumed_payload={key:resumed_payload[key] for key in ("script_id","plan_id","input_digest","member_id","identity_kind","device_id","user_id") if key in resumed_payload}
        run = self.repository.create_creation_execution_job(
            JobRun(
                id=f"job_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                kind=KIND,
                idempotency_key=f"creation-execution:{task_id}:{latest.input_digest}:retry:{retry_number}",
                input_digest=latest.input_digest,
            )
, start_payload=resumed_payload,event_type="creation_execution_resumed",
            write_guard=write_guard,execution_source=execution_source)
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
        event = self.repository.get_task_event(workspace_id, f"event_{job.id}_start")
        if event.payload.get("operation") == "regenerate_storyboard":
            raise PermissionError("故事板重算必须由现有设备队列取得许可后执行")
        token = set_workspace_context(workspace_id)
        try:
            return self.runtime.resume(workspace_id, job_id, self._work)
        finally:
            reset_workspace_context(token)

    def cancel(self, workspace_id: str, task_id: str, *, member_id: str | None = None, write_guard=None) -> JobRun:
        job = self.latest(workspace_id, task_id)
        if job is None:
            raise ValueError("尚未开始制作")
        if member_id is not None:
            event = self.repository.get_task_event(workspace_id, f"event_{job.id}_start")
            if event.payload.get("member_id") != member_id:
                raise PermissionError("当前成员不能停止其他成员创建的制作任务")
        if write_guard is not None:
            if job.status in {"succeeded","failed","cancelled"}:
                return job
            return self.repository.finish_job_run(workspace_id,job.id,status="cancelled",failure_reason="user_cancelled",
                impact="当前阶段在下一个安全边界停止；此前有效产物保留。",repair_action="需要时点击继续制作。",write_guard=write_guard)
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
                if stage == 'narration':
                    raise JobFailure('配音必须通过原作业的逐段设备队列执行',
                        '旧整阶段回调已退役，未调用声音模型。', '使用开始或继续制作入口。')
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
