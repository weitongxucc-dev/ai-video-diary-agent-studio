"""Standalone profile previews on the application's single persistent DeviceRuntime."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import wave
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.creation_execution import CreationExecutionActor, CreationStageGrantContext
from app.device_runtime import OwnerRegistration
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork
from app.models import utc_now
from app.host_resource_probes import HostResourceRefusal
from app.persistence_json import _json_payload

OWNER = "media_preview"
logger = logging.getLogger(__name__)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class ProfilePreviewOwner:
    def __init__(self, repository, *, voice_service, avatar_service, narration_budget,
                 presenter_budget, verify_identity):
        self.repository = repository
        self.services = {"voice": voice_service, "avatar_lipsync": avatar_service}
        self.narration_budget = narration_budget
        self.presenter_budget = presenter_budget
        self.verify_identity = verify_identity

    def read(self, workspace_id, run_id, conn=None):
        if conn is None:
            with self.repository._connect() as reader:
                return self.read(workspace_id, run_id, reader)
        return self.repository.preview_records.read(workspace_id, run_id, connection=conn)

    def facts(self, run):
        actor = CreationExecutionActor.from_event(run.workspace_id, run.member_identity)
        reader = actor.user_id or actor.member_id
        narration_inputs = self.repository.profile_inputs.resolve_narration_inputs(workspace_id=run.workspace_id,
            episode_id=run.preview_input.get("episode_id", "voice_preview"),
            voice_profile_id=run.voice_profile_id, voice_version=run.voice_version)
        voice = narration_inputs.voice
        source = self.repository.profile_reference_input(voice, viewer_user_id=reader)
        service = self.services[run.preview_type]
        if not service.provider.ready or service.provider.model_id != run.model_id:
            raise ValueError("预览模型不可用或已变化")
        if run.preview_type == "voice":
            budget = self.narration_budget
            if budget is None:
                raise ValueError("独立声音预览同类资源预算尚未标定")
            facts = [asdict(source), asdict(voice), run.preview_input,
                     [entry.task_input() for entry in narration_inputs.pronunciation_entries]]
            resources = {"kind": "creation_narration", "execution_mode": "isolated_qwen3_tts",
                "text_chars": len(run.preview_input["preview_text"]) + len(voice.reference_text), "section_count": 1,
                "reference_bytes": source.size_bytes, "duration_seconds": budget.max_duration_seconds,
                "max_output_bytes": budget.max_output_bytes, "threads": budget.max_threads,
                "timeout_seconds": budget.max_timeout_seconds}
        else:
            budget = self.presenter_budget
            if budget is None:
                raise ValueError("独立人物预览同类资源预算尚未标定")
            avatar_inputs = {key: value for key, value in run.preview_input.items() if key != "retry"}
            resolved = self.repository.profile_inputs.resolve_avatar_lipsync_request(workspace_id=run.workspace_id,
                viewer_user_id=reader, **avatar_inputs)
            person = resolved.person
            visual = self.repository.profile_reference_input(person, viewer_user_id=reader)
            audio = Path(run.preview_input["audio_path"])
            audio.relative_to(service.output_root)
            upstream = self.repository.preview_records.get_media_preview_run(run.workspace_id, "voice", run.preview_input["audio_cache_key"])
            if (upstream is None or upstream.status != "succeeded" or upstream.output_path != str(audio)
                    or upstream.member_identity != run.member_identity
                    or (upstream.voice_profile_id, upstream.voice_version) != (voice.id, voice.version)):
                raise PermissionError("人物预览配音来源或成员/声音版本不匹配")
            with wave.open(str(audio), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
            stat = audio.stat()
            facts = [asdict(source), asdict(visual), asdict(person), asdict(resolved.voice),
                     asdict(resolved.authorization) if resolved.authorization else None, run.preview_input,
                     [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns], asdict(upstream)]
            resources = {"kind": "creation_person_render", "execution_mode": "isolated_avatar_lipsync",
                "section_count": 1, "input_bytes": source.size_bytes + visual.size_bytes + stat.st_size,
                "duration_seconds": duration, "max_output_bytes": budget.max_output_bytes,
                "threads": budget.max_threads, "timeout_seconds": budget.max_timeout_seconds}
        digest = _digest([run.workspace_id, run.member_identity, run.model_id, facts])
        return digest, f"{run.preview_type}:{digest}", resources

    def verify_submission(self, intent, actor):
        if not isinstance(actor, CreationExecutionActor) or intent.owner_type != OWNER or actor.workspace_id != intent.workspace_id:
            raise PermissionError("独立预览身份或工作区不匹配")
        run = self.read(intent.workspace_id, intent.owner_id)
        if (run.status != "queued" or actor != CreationExecutionActor.from_event(run.workspace_id, run.member_identity)
                or self.verify_identity(actor) != "ready"):
            raise PermissionError("独立预览成员许可或排队状态失效")
        digest, unit, resources = self.facts(run)
        if (digest, unit) != (intent.input_digest, intent.unit_key) or digest != run.input_digest:
            raise PermissionError("独立预览输入已变化")
        return VerifiedWork(run.workspace_id, OWNER, run.id, digest, unit, actor.member_id, "normal_make", resources)

    def check_current(self, request, conn):
        if request.owner_type != OWNER or request.source_refs or request.work_class != "normal_make":
            return "unauthorized"
        try:
            run = self.read(request.workspace_id, request.owner_id, conn)
            actor = CreationExecutionActor.from_event(run.workspace_id, run.member_identity)
            if request.member_id != actor.member_id:
                return "unauthorized"
            verdict = self.verify_identity(actor)
            if verdict != "ready":
                return verdict
            if run.status not in {"queued", "running"}:
                return "stale"
            digest, unit, resources = self.facts(run)
            return "ready" if (request.input_digest, request.unit_key, request.resources) == (digest, unit, resources) else "stale"
        except PermissionError:
            return "unauthorized"
        except (ValueError, KeyError, OSError):
            return "dependency"

    def capture_local(self, request, conn):
        try:
            run = self.read(request.workspace_id, request.owner_id, conn)
        except KeyError:
            return lambda current: "stale"
        refs = [("voice", run.voice_profile_id, run.voice_version)]
        if run.preview_type == "avatar_lipsync":
            refs.append(("person", run.preview_input["person_id"], run.preview_input["person_version"]))
        pronunciation_scope = None
        if run.preview_type == "voice":
            try:
                voice = self.repository.get_versioned_voice_profile(run.workspace_id, run.voice_profile_id, run.voice_version)
            except KeyError:
                return lambda current: "stale"
            pronunciation_scope = (voice.person_profile_id, run.preview_input.get("episode_id", "voice_preview"))
        profiles = self.repository.capture_profile_local_check(conn, run.workspace_id, refs,
            pronunciation_scope=pronunciation_scope)
        def read(current):
            own, upstream = self.repository.preview_records.local_revisions(run.workspace_id, run.id,
                upstream_identity=run.preview_input['audio_cache_key'] if run.preview_type == 'avatar_lipsync' else None,
                connection=current)
            authorization = None
            if run.preview_type == "avatar_lipsync":
                authorization = tuple(map(tuple, current.execute(
                    "SELECT version,payload FROM media_authorizations WHERE workspace_id=? AND id=? ORDER BY created_at,version",
                    (run.workspace_id, run.preview_input["authorization_id"]),
                ).fetchall()))
            budget = self.narration_budget if run.preview_type == "voice" else self.presenter_budget
            limits = ((budget.max_duration_seconds, budget.max_output_bytes, budget.max_threads,
                       budget.max_timeout_seconds) if budget else None)
            return (own, upstream, authorization,
                    self.services[run.preview_type].provider.model_id, limits)
        expected = read(conn)
        return lambda current: profiles(current) if read(current) == expected else "stale"

    def check_local(self, request, conn, observed):
        return observed(conn)

    def can_manage(self, request, actor):
        return isinstance(actor, CreationExecutionActor) and (actor.workspace_id, actor.member_id) == (request.workspace_id, request.member_id) and self.verify_identity(actor) == "ready"

    can_view_detail = can_manage

    def can_wake(self, request, actor):
        return False

    def verify_retry(self, request, actor, reason, conn):
        raise PermissionError("通过档案预览入口显式重试，保留旧尝试")


class _PreviewCheckpointRecords:
    """Finite attempt port: only terminal records wait for the scheduler transaction."""
    def __init__(self, records):
        self.records = records

    def get_media_preview_run(self, workspace_id, preview_type, identity):
        return self.records.get_media_preview_run(workspace_id, preview_type, identity)

    def claim_media_preview_run(self, record, *, retry, succeeded_reusable=True):
        return self.records.claim_media_preview_run(record, retry=retry, succeeded_reusable=succeeded_reusable)

    def save_media_preview_run(self, record):
        if record.status in {"queued", "running"}:
            return self.records.save_media_preview_run(record)
        return record


class ProfilePreviewExecutor:
    def __init__(self, owner):
        self.owner = owner
        self.cancellations = {}
        self.lock = threading.Lock()

    def request_cancel(self, request_id):
        with self.lock:
            event = self.cancellations.get(request_id)
        if event is not None:
            event.set()

    def execute(self, grant, scheduler):
        owner = self.owner
        run = owner.read(grant.workspace_id, grant.owner_id)
        _, _, resources = owner.facts(run)
        event = threading.Event()
        with self.lock:
            self.cancellations[grant.request_id] = event
        context = CreationStageGrantContext(scheduler, grant, time.monotonic() + resources["timeout_seconds"],
                                           resources["max_output_bytes"], resources["threads"], event)
        def write(conn, terminal):
            return owner.repository.preview_records.checkpoint(terminal, grant.request_id, connection=conn)

        try:
            context.check()
            if run.status == "running":
                # The scheduler re-granted only after the previous registered group released.
                owner.repository.preview_records.save_media_preview_run(replace(run, status="queued"))
            actor = CreationExecutionActor.from_event(run.workspace_id, run.member_identity)
            result = owner.services[run.preview_type].request(repository=owner.repository,
                preview_records=_PreviewCheckpointRecords(owner.repository.preview_records),
                workspace_id=run.workspace_id, viewer_user_id=actor.user_id or actor.member_id,
                grant_context=context, member_identity=run.member_identity, **run.preview_input)
            context.check()
            if result.id != run.id or result.identity != run.identity:
                raise ValueError("预览输入身份已变化，不能发布到原设备请求")
            if result.status == "succeeded":
                scheduler.complete_unit(grant, checkpoint_database_path=owner.repository.database_path,
                                        checkpoint_write=lambda conn, _: write(conn, result))
            else:
                scheduler.fail_unit(grant, reason="preview_failed", failure_write=lambda conn, _: write(conn, result))
        except HostResourceRefusal as exc:
            logger.warning("Profile preview resource refusal: %s", json.dumps({
                "request_id": grant.request_id, "workspace_id": grant.workspace_id,
                "owner_id": grant.owner_id, "attempt_number": grant.attempt_number,
                "permit_generation": grant.generation, "preview_attempt": run.attempt,
                "decision": exc.decision.diagnostic_payload(),
            }, ensure_ascii=False))
            if not exc.recoverable_pressure:
                terminal = replace(owner.read(run.workspace_id, run.id), status="failed", finished_at=utc_now(),
                    output_path=None, reason=exc.decision.reason or "resource_unavailable",
                    impact="预览资源声明或能力准备不可用，未发布输出。", repair_action=str(exc))
                scheduler.fail_unit(grant, reason="preview_resources_unavailable",
                                    failure_write=lambda conn, _: write(conn, terminal))
            else:
                # defer_unit proves child-group release before returning the permit.
                # The wrapper above has suppressed the service's provisional failed record.
                current = owner.read(run.workspace_id, run.id)
                waiting = replace(current, status="queued", finished_at=None,
                    output_path=None, reason="resource_pressure", impact="预览等待系统压力恢复，保留原请求。",
                    repair_action="系统稳定后自动继续，无需重新生成请求。")
                scheduler.defer_unit(grant, reason="resource_pressure",
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30),
                    owner_database_path=owner.repository.database_path,
                    owner_write=lambda conn, _: write(conn, waiting))
        except RecoveryBlocked:
            raise  # Keep the permit and unfinished record when child release is unknown.
        except Exception as exc:
            from app.granted_subprocess import GrantedCommandCancelled
            terminal = replace(owner.read(run.workspace_id, run.id), status="cancelled" if isinstance(exc, GrantedCommandCancelled) else "failed",
                finished_at=utc_now(), output_path=None, reason=type(exc).__name__, impact="独立预览未完成，参考和输入保留。", repair_action=str(exc))
            if terminal.status == "cancelled":
                scheduler.complete_unit(grant, checkpoint_database_path=owner.repository.database_path,
                                        checkpoint_write=lambda conn, _: write(conn, terminal), cancelled=True)
            else:
                scheduler.fail_unit(grant, reason="preview_failed", failure_write=lambda conn, _: write(conn, terminal))
        finally:
            with self.lock:
                self.cancellations.pop(grant.request_id, None)


def profile_preview_registration(repository, **kwargs):
    owner = ProfilePreviewOwner(repository, **kwargs)
    return OwnerRegistration(owner, ProfilePreviewExecutor(owner))


def submit_profile_preview(runtime, run, actor, preview_input):
    owner = runtime.registrations[OWNER].owner
    repository = owner.repository
    run = replace(run, member_identity=actor.identity_payload(), preview_input=preview_input)
    try:
        digest, unit, _ = owner.facts(run)
        run = repository.preview_records.save_media_preview_run(replace(run, input_digest=digest))
        request = runtime.scheduler.submit(SubmissionIntent(run.workspace_id, OWNER, run.id, digest, unit, SchedulePolicy()), actor)
    except (PermissionError, ValueError, OSError) as exc:
        repository.preview_records.save_media_preview_run(replace(run, status="failed", finished_at=utc_now(),
            reason="device_submission_failed", impact="独立预览未进入设备队列，未调用模型。", repair_action=str(exc)))
        raise
    run = repository.preview_records.bind_preview_device_request(run, request.id)
    runtime.wake()
    return run
