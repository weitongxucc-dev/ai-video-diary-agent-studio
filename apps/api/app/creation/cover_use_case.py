"""Cover input binding, staged-file recovery and one transactional adoption.

The existing creation executor supplies the grant. This module neither schedules
work nor launches a generator; profile admission and the private child entry are
assembled by the application. A published image alone is never an adopted edit.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal
from uuid import uuid4

from app.canvas import canvas_size
from app.cover_generation import CoverGenerationEvidence, CoverSafeArea, _normalized_title
from app.creation.contracts import (
    ArtifactRecord, CoverAsset, DependencyEdge, EditOperation,
    JobRun, TaskEvent, TimelineVersion, TransformRun,
)
from app.creation.transactions import clone_subtitle_track, commit_change_set, prepare_change_set

if TYPE_CHECKING:
    from app.artifact_store import ArtifactStore
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation_execution import CreationExecutionActor, CreationStageGrantContext
    from app.production import ProductionRepository
    from app.host_resource_probes import CoverBudget


COVER_JOB_KIND = "cover_frame_generation"
COVER_OPERATION = "GenerateCoverFromFrame"


def _canonical(value) -> str:
    # Keep the original confirm branch's operation digest, including spacing.
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class CoverCommand:
    workspace_id: str
    task_id: str
    proposal_id: str
    request_id: str | None
    identity_json: str
    operation_json: str
    source_sha256: str
    source_size_bytes: int
    source_identity: tuple[int, ...]

    @classmethod
    def from_payload(cls, value):
        return cls(**{**value, "source_identity": tuple(value["source_identity"])})

    @property
    def operation(self) -> dict:
        return json.loads(self.operation_json)

    @property
    def input_digest(self) -> str:
        return hashlib.sha256(self.operation_json.encode()).hexdigest()

    @property
    def idempotency_key(self) -> str:
        return f"cover:{self.proposal_id}"

    @property
    def unit_key(self) -> str:
        return f"cover:{self.input_digest}"


@dataclass(frozen=True)
class CoverStagedReceipt:
    """The private child publishes this next to its fixed immutable cover file."""

    command: CoverCommand
    job_id: str
    evidence: CoverGenerationEvidence
    generated_at: datetime
    output_identity: tuple[int, ...]

    def payload(self) -> dict:
        return {"schema": "cover_stage_v1", "command": asdict(self.command),
                "job_id": self.job_id, "evidence": self.evidence.as_dict(),
                "generated_at": self.generated_at.isoformat(), "output_identity": list(self.output_identity)}


@dataclass(frozen=True)
class CoverResult:
    operation: EditOperation
    version: TimelineVersion
    job: JobRun | None


@dataclass(frozen=True)
class CoverRecovery:
    state: Literal["new", "pending", "staged", "unknown", "failed", "cancelled", "adopted"]
    job: JobRun | None
    receipt: CoverStagedReceipt | None = None
    result: CoverResult | None = None


class CoverUnavailable(ValueError):
    pass


class CoverUnresolved(RuntimeError):
    def __init__(self, job: JobRun, reason: str):
        self.job = job
        super().__init__(reason)


def file_identity(path: Path) -> tuple[int, ...]:
    """Finite freshness facts; full content hashing belongs to the granted child."""
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


class CoverUseCase:
    def __init__(self, repository: ProductionRepository, artifact_store: ArtifactStore,
                 metadata: MetadataEditUseCase, *,
                 verify_identity: Callable[[CreationExecutionActor], str],
                 output_root: str | Path, budget: CoverBudget | None = None, on_settled=None):
        self.repository = repository
        self.artifact_store = artifact_store
        self.metadata = metadata
        self.verify_identity = verify_identity
        self.task_authorizations = None
        self.output_root = Path(output_root).resolve()
        self.budget = budget
        self.on_settled = on_settled

    def _source_record(self, workspace_id, task_id, logical_ref):
        records = self.repository.list_artifact_records(workspace_id, task_id)
        source = next((item for item in records if item.logical_ref == logical_ref), None)
        if source is None:
            raise ValueError("封面源成片缺少原任务已登记摘要，不能无许可重扫文件")
        return source

    def require_budget(self):
        if self.budget is None:
            raise CoverUnavailable("封面独立资源预算尚未标定，未创建Job或执行生成")
        return self.budget

    def _access(self, actor: CreationExecutionActor, task_id: str):
        task = self.repository.get_creation_task(actor.workspace_id, task_id)
        if self.task_authorizations is not None:
            verdict = self.task_authorizations.verdict(actor, task_id, "cover", check_scope=False)
        else:
            verdict = self.verify_identity(actor)
        if not actor.member_id or verdict != "ready":
            raise PermissionError("原任务封面授权失效或无法复核")
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError("当前成员不能处理此任务的封面")
        return task

    def _proposal(self, workspace_id: str, task_id: str, proposal_id: str):
        proposal = self.repository.get_edit_proposal(workspace_id, proposal_id)
        if proposal.task_id != task_id or proposal.operation.get("operation_type") != COVER_OPERATION:
            raise ValueError("封面提案不属于当前任务或操作")
        payload = proposal.operation.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("封面生成操作无效")
        return proposal, {**payload, "title_position": payload.get("title_position", "bottom")}

    def prepare(self, actor: CreationExecutionActor, task_id: str, proposal_id: str,
                *, request_id: str | None) -> CoverCommand:
        """Seal current inputs without creating a Job or starting any work."""
        task = self._access(actor, task_id)
        proposal, payload = self._proposal(actor.workspace_id, task_id, proposal_id)
        if proposal.status != "proposed":
            raise ValueError("封面提案当前不可确认，请回读原结果")
        if (not isinstance(payload.get("base_version_id"), str)
                or not isinstance(payload.get("source_video_ref"), str)
                or not isinstance(payload.get("title"), str)
                or not isinstance(payload.get("frame_time_seconds"), (int, float))):
            raise ValueError("封面生成操作无效")
        if task.current_timeline_version_id != payload["base_version_id"]:
            raise ValueError("封面提案基础版本已变化")
        base = self.repository.get_timeline_version(actor.workspace_id, payload["base_version_id"])
        if base.task_id != task_id:
            raise ValueError("封面基础版本不属于当前任务")
        source = self._source_record(actor.workspace_id, task_id, payload["source_video_ref"])
        identity = file_identity(self.artifact_store.resolve_file(source.logical_ref))
        if identity[2] != source.size_bytes:
            raise ValueError("封面源文件与原登记大小不一致")
        return CoverCommand(actor.workspace_id, task_id, proposal_id, request_id,
            _canonical(actor.identity_payload()), _canonical(payload), source.sha256, source.size_bytes, identity)

    def _bound(self, command: CoverCommand, actor: CreationExecutionActor, *, current: bool):
        if (actor.workspace_id != command.workspace_id
                or _canonical(actor.identity_payload()) != command.identity_json):
            raise PermissionError("封面原请求身份已变化")
        task = self._access(actor, command.task_id)
        proposal, payload = self._proposal(command.workspace_id, command.task_id, command.proposal_id)
        if _canonical(payload) != command.operation_json:
            raise ValueError("封面原提案输入摘要已变化")
        if current:
            if proposal.status != "proposed" or task.current_timeline_version_id != payload["base_version_id"]:
                raise ValueError("封面原提案或当前版本已变化，不采用旧产物")
            source = self._source_record(command.workspace_id, command.task_id, payload["source_video_ref"])
            if ((source.sha256, source.size_bytes) != (command.source_sha256, command.source_size_bytes)
                    or file_identity(self.artifact_store.resolve_file(source.logical_ref)) != command.source_identity):
                raise ValueError("封面源成片已变化，不采用旧产物")
        return task, proposal

    def find_job(self, command: CoverCommand) -> JobRun | None:
        return self._job(command.workspace_id, command.task_id, command.proposal_id, command.input_digest)

    def _job(self, workspace_id, task_id, proposal_id, input_digest):
        job = self.repository.get_job_run_by_idempotency_key(workspace_id, f"cover:{proposal_id}")
        if job is not None and (job.task_id != task_id or job.kind != COVER_JOB_KIND
                                or job.input_digest != input_digest):
            raise ValueError("原封面幂等键已绑定不同任务或输入")
        return job

    def original_result(self, actor: CreationExecutionActor, task_id: str,
                        proposal_id: str) -> CoverResult | None:
        """Replay adopted facts even after the source/current version has moved."""
        self._access(actor, task_id)
        _proposal, payload = self._proposal(actor.workspace_id, task_id, proposal_id)
        encoded = _canonical(payload)
        job = self._job(actor.workspace_id, task_id, proposal_id, hashlib.sha256(encoded.encode()).hexdigest())
        return self._applied_result(actor.workspace_id, task_id, proposal_id, encoded, job)

    def read_result(self, command: CoverCommand, actor: CreationExecutionActor) -> CoverResult | None:
        self._bound(command, actor, current=False)
        return self._applied_result(command.workspace_id, command.task_id, command.proposal_id,
                                    command.operation_json, self.find_job(command))

    def _applied_result(self, workspace_id, task_id, proposal_id, operation_json, job):
        operations = self.repository.list_edit_operations(workspace_id, task_id)
        operation = next((item for item in operations if item.proposal_id == proposal_id), None)
        if operation is None:
            return None
        if (operation.operation_type != COVER_OPERATION or operation.status != "applied"
                or _canonical(operation.payload) != operation_json):
            raise ValueError("原封面采用记录与请求不一致")
        version = self.repository.get_timeline_version(workspace_id, operation.result_version_id)
        if (version.task_id != task_id or version.created_by_operation_id != operation.id
                or version.parent_version_id != operation.base_version_id):
            raise ValueError("原封面采用记录的版本谱系不一致")
        return CoverResult(operation, version, job)

    @staticmethod
    def output_ref(job: JobRun) -> str:
        return f"artifacts/{job.task_id}/{job.id}/cover.png"

    @staticmethod
    def receipt_ref(job: JobRun) -> str:
        return f"artifacts/{job.task_id}/{job.id}/cover-receipt.json"

    def read_staged(self, command: CoverCommand, job: JobRun, *, max_receipt_bytes: int):
        """Read the same child receipt; never infer success from an image alone."""
        if max_receipt_bytes <= 0:
            raise ValueError("封面回执读取必须使用显式profile输出上限")
        try:
            path = self.artifact_store.resolve_file(self.receipt_ref(job))
        except FileNotFoundError:
            return None
        with path.open("rb") as stream:
            content = stream.read(max_receipt_bytes + 1)
        if len(content) > max_receipt_bytes:
            raise ValueError("封面回执超过原profile输出上限")
        payload = json.loads(content)
        if payload["schema"] != "cover_stage_v1":
            raise ValueError("封面回执格式不受支持")
        receipt = CoverStagedReceipt(CoverCommand.from_payload(payload["command"]), payload["job_id"],
            CoverGenerationEvidence(**payload["evidence"]), datetime.fromisoformat(payload["generated_at"]),
            tuple(payload["output_identity"]))
        if receipt.command != command or receipt.job_id != job.id:
            raise ValueError("封面回执不属于原请求与Job")
        evidence = receipt.evidence
        output_identity = file_identity(self.artifact_store.resolve_file(self.output_ref(job)))
        expected = command.operation
        width, height = canvas_size(expected.get("aspect_ratio", "9:16"))
        if (evidence.input_ref != expected["source_video_ref"]
                or evidence.input_digest != command.source_sha256
                or evidence.input_size_bytes != command.source_size_bytes
                or evidence.output_ref != self.output_ref(job)
                or receipt.output_identity != output_identity
                or evidence.output_size_bytes != output_identity[2]
                or evidence.output_format != "png"
                or (evidence.output_width, evidence.output_height) != (width, height)
                or evidence.engine != "ffmpeg" or evidence.layout_engine != "Pillow"
                or evidence.parameters.get("frame_time_seconds") != float(expected["frame_time_seconds"])
                or evidence.parameters.get("title") != _normalized_title(expected["title"])
                or evidence.parameters.get("title_position") != expected["title_position"]
                or evidence.parameters.get("canvas") != {"width": width, "height": height}
                or evidence.parameters.get("safe_area") != asdict(CoverSafeArea())):
            raise ValueError("封面固定文件或生成证据与原输入不一致")
        return receipt

    def read_recovery(self, command: CoverCommand, actor: CreationExecutionActor,
                      *, max_receipt_bytes: int) -> CoverRecovery:
        result = self.read_result(command, actor)
        if result is not None:
            return CoverRecovery("adopted", result.job, result=result)
        job = self.find_job(command)
        if job is None:
            return CoverRecovery("new", None)
        receipt = self.read_staged(command, job, max_receipt_bytes=max_receipt_bytes)
        if job.status in {"failed", "cancelled"}:
            return CoverRecovery(job.status, job, receipt)
        if receipt is not None:
            return CoverRecovery("staged", job, receipt)
        # Running/interrupted/succeeded without a receipt is unknown, not a
        # licence to claim again. The runtime owns release and retry decisions.
        return CoverRecovery("pending" if job.status == "queued" else "unknown", job)

    def complete(self, command: CoverCommand, actor: CreationExecutionActor,
                 context: CreationStageGrantContext, *, max_receipt_bytes: int) -> CoverResult:
        """Adopt only through the existing scheduler's released-grant transaction."""
        original = self.read_result(command, actor)
        if original is not None:
            return original
        context.check()
        scheduler, grant = context.scheduler, context.grant
        task, proposal = self._bound(command, actor, current=True)
        job = self.find_job(command)
        if job is None or job.status not in {"running", "succeeded"}:
            raise ValueError("原封面Job尚未进入可续交状态")
        if ((grant.workspace_id, grant.owner_type, grant.owner_id, grant.input_digest, grant.unit_key)
                != (command.workspace_id, "creation_job", job.id, command.input_digest, command.unit_key)):
            raise PermissionError("封面采用许可不属于原Job与输入")
        receipt = self.read_staged(command, job, max_receipt_bytes=max_receipt_bytes)
        if receipt is None:
            raise ValueError("原封面缺少可信staged回执，保持未确定，不重生成")
        base = self.repository.get_timeline_version(command.workspace_id, command.operation["base_version_id"])
        version_id = f"timeline_{uuid4().hex}"
        tracks = [clone_subtitle_track(track, timeline_version_id=version_id)
                  for track in self.metadata.current_subtitle_tracks(command.workspace_id, task, current_version=base)]
        evidence = receipt.evidence
        transform_id = f"transform_{job.id}"
        cover = CoverAsset(id=f"cover_{job.id}", workspace_id=command.workspace_id,
            task_id=command.task_id, timeline_version_id=version_id,
            source_video_ref=command.operation["source_video_ref"],
            frame_time_seconds=float(command.operation["frame_time_seconds"]), title=command.operation["title"],
            safe_area={key: float(value) for key, value in asdict(CoverSafeArea()).items()},
            layout={"title_position": command.operation["title_position"], "text_align": "center"},
            generation_method="video_frame", model_or_engine=f"{evidence.engine}+{evidence.layout_engine}",
            artifact_ref=evidence.output_ref, transform_id=transform_id, status="valid",
            created_at=receipt.generated_at, updated_at=receipt.generated_at)
        change = prepare_change_set(proposal=proposal, base_version=base, operation_type=COVER_OPERATION,
            operation_payload=command.operation, result_version_id=version_id,
            montage_plan_id=None, montage_payload=None, subtitle_tracks=tracks, cover_asset=cover)

        def checkpoint(connection, current):
            context.check()
            self._bound(command, actor, current=True)
            # Re-read fixed files inside the checkpoint; late validation/SQL
            # failure rolls back every DB write while leaving the receipt intact.
            checked = self.read_staged(command, job, max_receipt_bytes=max_receipt_bytes)
            if checked != receipt:
                raise ValueError("封面采用前固定回执发生变化")
            context.check()
            self.repository.create_artifact_record(ArtifactRecord(id=f"artifact_{job.id}_cover",
                workspace_id=command.workspace_id, task_id=command.task_id, kind="cover_image",
                logical_ref=evidence.output_ref, sha256=evidence.output_sha256,
                size_bytes=evidence.output_size_bytes, producer_job_id=job.id,
                input_refs=[cover.source_video_ref], created_at=receipt.generated_at), connection=connection)
            self.repository.create_transform_run(TransformRun(id=transform_id, workspace_id=command.workspace_id,
                task_id=command.task_id, job_id=job.id, transform_type="cover_from_frame",
                input_digest=job.input_digest, input_refs=[cover.source_video_ref], output_refs=[evidence.output_ref],
                engine=cover.model_or_engine, parameters=evidence.parameters, status="succeeded",
                started_at=job.started_at or job.created_at, finished_at=receipt.generated_at), connection=connection)
            self.repository.create_dependency_edge(DependencyEdge(id=f"edge_{job.id}_cover",
                workspace_id=command.workspace_id, task_id=command.task_id,
                upstream_ref=cover.source_video_ref, downstream_ref=evidence.output_ref,
                created_by_transform_id=transform_id, created_at=receipt.generated_at), connection=connection)
            self.repository.append_task_event(TaskEvent(id=f"event_{job.id}_cover_adopted",
                workspace_id=command.workspace_id, task_id=command.task_id, job_id=job.id,
                event_type="cover_adopted", payload={"edit_proposal_id": command.proposal_id,
                    "request_id": command.request_id, "receipt_ref": self.receipt_ref(job),
                    "result_version_id": version_id}, created_at=receipt.generated_at), connection=connection)
            commit_change_set(self.repository, change, connection=connection)
            if job.status == "running":
                self.repository.finish_job_run(command.workspace_id, job.id, status="succeeded",
                    output_refs=[evidence.output_ref], connection=connection)
            return f"cover:{job.id}:{version_id}"

        context.check()
        scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                checkpoint_write=checkpoint)
        result = self.read_result(command, actor)
        if result is None:
            raise ValueError("封面采用事务未留下原结果")
        return result


    def resources(self, command: CoverCommand) -> dict:
        budget = self.require_budget()
        if command.source_size_bytes > budget.max_input_bytes:
            raise CoverUnavailable("封面源文件超过独立profile输入上限")
        return {"kind": "cover_generation", "execution_mode": "isolated_cover_python",
                "budget_digest": budget.digest, "input_bytes": command.source_size_bytes,
                "timeout_seconds": budget.max_timeout_seconds, "threads": budget.decoder_threads,
                "max_output_bytes": budget.max_output_bytes, "max_receipt_bytes": budget.max_receipt_bytes,
                "max_stdout_bytes": budget.max_stdout_bytes, "max_stderr_bytes": budget.max_stderr_bytes}

    def _start(self, workspace_id, job_id, connection=None):
        job = self.repository.get_job_run(workspace_id, job_id)
        try:
            event = self.repository.get_task_event(workspace_id, f"event_{job.id}_cover_start", connection=connection)
        except KeyError as exc:
            raise CoverUnresolved(job, "原封面Job缺少封存输入事件，结果保持未确定，不重生成") from exc
        command = CoverCommand.from_payload(event.payload["command"])
        bound = self.find_job(command)
        if bound is None or bound.id != job.id:
            raise ValueError("封面原Job绑定已变化")
        return job, event.payload, command

    def _current(self, command, actor, job, payload):
        self._bound(command, actor, current=True)
        if self.resources(command) != payload["resources"]:
            raise ValueError("封面原独立profile已经变化")
        if job.status not in {"queued", "running", "interrupted", "succeeded"}:
            raise ValueError("封面原Job不可继续")
        deadline = datetime.fromisoformat(payload["deadline_utc"])
        if datetime.now(timezone.utc) >= deadline:
            raise CoverUnresolved(job, "封面原请求等待/执行时限已到，回读原状态，不能重新生成")
        return deadline

    def verify_submission(self, intent, actor):
        from app.device_scheduler import VerifiedWork
        job, payload, command = self._start(intent.workspace_id, intent.owner_id)
        self._current(command, actor, job, payload)
        if ((intent.owner_type, intent.input_digest, intent.unit_key)
                != ("creation_job", command.input_digest, command.unit_key)):
            raise PermissionError("封面执行许可提交与原Job不一致")
        return VerifiedWork(command.workspace_id, "creation_job", job.id, command.input_digest,
                            command.unit_key, actor.member_id, "preview_dependency", payload["resources"])

    def check_current(self, request, connection):
        from app.creation_execution import CreationExecutionActor
        try:
            job, payload, command = self._start(request.workspace_id, request.owner_id, connection)
            actor = CreationExecutionActor.from_event(command.workspace_id, payload)
            self._current(command, actor, job, payload)
            if ((request.owner_type, request.member_id, request.input_digest, request.unit_key,
                 request.work_class, request.resources, request.source_refs)
                    != ("creation_job", actor.member_id, command.input_digest, command.unit_key,
                        "preview_dependency", payload["resources"], ())):
                return "unauthorized"
            try:
                started = self.repository.get_task_event(command.workspace_id, f"event_{job.id}_cover_worker_started", connection=connection)
            except KeyError:
                return "ready" if job.status == "queued" else "unknown"
            if (request.state in {"granted", "yield_requested"} and job.status == "running"
                    and started.payload["request_id"] == request.id
                    and started.payload["generation"] == request.last_grant_generation):
                return "ready"
            receipt = self.read_staged(command, job, max_receipt_bytes=self.require_budget().max_receipt_bytes)
            return "ready" if receipt is not None else "unknown"
        except PermissionError:
            return "unauthorized"
        except (KeyError, ValueError, OSError, CoverUnresolved):
            return "unknown"

    def capture_local(self, request, connection):
        from app.creation_execution import CreationExecutionActor
        try:
            job, payload, command = self._start(request.workspace_id, request.owner_id, connection)
        except (KeyError, ValueError, CoverUnresolved):
            return lambda current: "unknown"
        actor = CreationExecutionActor.from_event(command.workspace_id, payload)
        deadline = datetime.fromisoformat(payload["deadline_utc"])
        def read(current):
            job_row = current.execute(
                "SELECT task_id,kind,input_digest,status FROM job_runs WHERE workspace_id=? AND id=?",
                (command.workspace_id, job.id),
            ).fetchone()
            task = current.execute(
                """SELECT json_extract(payload,'$.current_timeline_version_id'),
                   json_extract(payload,'$.owner_user_id'),json_extract(payload,'$.visibility')
                   FROM creation_tasks WHERE workspace_id=? AND id=?""",
                (command.workspace_id, command.task_id),
            ).fetchone()
            proposal = current.execute(
                "SELECT status,updated_at FROM edit_proposals WHERE workspace_id=? AND id=?",
                (command.workspace_id, command.proposal_id),
            ).fetchone()
            source = current.execute(
                "SELECT id,sha256,size_bytes FROM artifact_records WHERE workspace_id=? AND task_id=? AND logical_ref=?",
                (command.workspace_id, command.task_id, command.operation["source_video_ref"]),
            ).fetchone()
            started = current.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (command.workspace_id, f"event_{job.id}_cover_worker_started"),
            ).fetchone()
            return (tuple(job_row) if job_row else None, tuple(task) if task else None,
                    tuple(proposal) if proposal else None, tuple(source) if source else None,
                    tuple(started) if started else None, self.budget.digest if self.budget else None)
        expected = read(connection)
        if expected[1] is None:
            return lambda current: "stale"
        def check(current):
            if self.task_authorizations is not None:
                verdict = self.task_authorizations.verdict(actor, command.task_id, "cover", connection=current, check_scope=False)
                if verdict != "ready":
                    return verdict
            if datetime.now(timezone.utc) >= deadline:
                return "unknown"
            actual = read(current)
            if actual[1] is None or actual[1][1:] != expected[1][1:]:
                return "unauthorized"
            return "ready" if actual == expected else "stale"
        return check

    def check_local(self, request, connection, observed):
        return observed(connection)

    def submit(self, runtime, actor: CreationExecutionActor, task_id: str, proposal_id: str,
               *, request_id: str | None):
        if self.task_authorizations is not None:
            self.task_authorizations.ensure(self.repository.get_creation_task(actor.workspace_id, task_id), actor)
        from app.device_scheduler import SchedulePolicy, SubmissionIntent
        result = self.original_result(actor, task_id, proposal_id)
        if result is not None:
            return result
        budget = self.require_budget()  # before a new Job or generation
        job = self.repository.get_job_run_by_idempotency_key(actor.workspace_id, f"cover:{proposal_id}")
        if job is None:
            command = self.prepare(actor, task_id, proposal_id, request_id=request_id)
            submitted = datetime.now(timezone.utc)
            payload = {"command": asdict(command), "resources": self.resources(command),
                       "submitted_at_utc": submitted.isoformat(),
                       "deadline_utc": (submitted + timedelta(seconds=budget.max_timeout_seconds)).isoformat(),
                       "edit_proposal_id": proposal_id, **actor.identity_payload()}
            job = self.repository.create_cover_generation_job(JobRun(id=f"job_{uuid4().hex}",
                workspace_id=actor.workspace_id, task_id=task_id, kind=COVER_JOB_KIND,
                idempotency_key=command.idempotency_key, input_digest=command.input_digest), payload,
                expected_version_id=command.operation["base_version_id"])
        job, payload, command = self._start(actor.workspace_id, job.id)
        self._current(command, actor, job, payload)
        # Same persisted request only. submit is idempotent in the existing scheduler.
        runtime.scheduler.submit(SubmissionIntent(command.workspace_id, "creation_job", job.id,
            command.input_digest, command.unit_key, SchedulePolicy()), actor)
        runtime.wake()
        deadline = datetime.fromisoformat(payload["deadline_utc"])
        while True:
            result = self.read_result(command, actor)
            if result is not None:
                return result
            current = self.repository.get_job_run(command.workspace_id, job.id)
            if current.status in {"failed", "cancelled"}:
                raise CoverUnresolved(current, current.failure_reason or current.status)
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                for row in self.repository.list_creation_job_request_states(command.workspace_id, job.id):
                    if row["state"] in {"waiting", "blocked", "granted", "yield_requested"}:
                        detail = runtime.scheduler.cancel(row["id"], actor)
                        runtime.registrations["creation_job"].executor.request_cancel(row["id"])
                        if detail["state"] == "cancelled" and current.status in {"queued", "interrupted"}:
                            self.repository.finish_job_run(command.workspace_id, job.id, status="cancelled",
                                failure_reason="cover_deadline", impact="当前时间线保持不变。",
                                repair_action="回读原封面请求，确认释放后重新发起。")
                raise CoverUnresolved(self.repository.get_job_run(command.workspace_id, job.id),
                    "封面原请求时限已到；未确认释放或采用的结果保持未确定，不重复生成")
            threading.Event().wait(min(budget.runtime_sample_seconds, remaining))

    def execute(self, grant, scheduler, executor):
        from app.creation_execution import CreationExecutionActor, CreationStageGrantContext
        from app.device_scheduler import RecoveryBlocked
        from app.granted_subprocess import GrantedCommandCancelled, run_granted_command
        job, payload, command = self._start(grant.workspace_id, grant.owner_id)
        actor = CreationExecutionActor.from_event(command.workspace_id, payload)
        budget = self.require_budget()
        deadline = self._current(command, actor, job, payload)
        cancellation = threading.Event()
        executor.register_cancellation(grant.request_id, cancellation)
        context = CreationStageGrantContext(scheduler, grant,
            time.monotonic() + (deadline - datetime.now(timezone.utc)).total_seconds(),
            budget.max_output_bytes, budget.decoder_threads, cancellation)
        known_failure = False
        try:
            context.check()
            receipt = self.read_staged(command, job, max_receipt_bytes=budget.max_receipt_bytes)
            if receipt is None:
                try:
                    self.repository.get_task_event(command.workspace_id, f"event_{job.id}_cover_worker_started")
                except KeyError:
                    if job.status != "queued":
                        raise RecoveryBlocked("原封面副作用未知，缺少回执，不重claim或重生成")
                else:
                    raise RecoveryBlocked("原封面worker已启动但无可信回执，不能盲重放")
                job = self.repository.claim_job_run(command.workspace_id, job.id)
                control_ref = f"artifacts/{command.task_id}/{job.id}/cover-request.json"
                control = {"command": asdict(command), "job_id": job.id,
                           "threads": budget.decoder_threads, "max_receipt_bytes": budget.max_receipt_bytes,
                           "max_output_bytes": budget.max_output_bytes}
                encoded = _canonical(control).encode()
                if len(encoded) > budget.max_receipt_bytes:
                    raise ValueError("封面封存输入超过profile控制文件上限")
                self.artifact_store.publish_bytes(control_ref, encoded)
                self.repository.append_task_event(TaskEvent(id=f"event_{job.id}_cover_worker_started",
                    workspace_id=command.workspace_id, task_id=command.task_id, job_id=job.id,
                    event_type="cover_worker_started", payload={"request_id": grant.request_id,
                        "generation": grant.generation, "input_digest": command.input_digest}, created_at=job.created_at))
                entry = [sys.executable]
                if not getattr(sys, "frozen", False):
                    entry.append(str(Path(__file__).resolve().parents[2] / "server.py"))
                env = {**os.environ, "OMP_NUM_THREADS": str(budget.decoder_threads),
                       "OPENBLAS_NUM_THREADS": str(budget.decoder_threads), "MKL_NUM_THREADS": str(budget.decoder_threads)}
                result = run_granted_command(scheduler, grant,
                    [*entry, "--cover-worker", str(self.artifact_store.resolve_file(control_ref)), str(self.output_root)],
                    timeout_seconds=context.deadline_monotonic - time.monotonic(),
                    max_stdout_bytes=budget.max_stdout_bytes, max_stderr_bytes=budget.max_stderr_bytes,
                    cancel_event=cancellation, env=env,
                    output_paths=[self.output_root / f"artifacts/{command.task_id}/{job.id}"],
                    max_output_file_bytes=budget.additional_disk_bytes + budget.max_output_bytes + budget.max_receipt_bytes)
                if result.returncode:
                    known_failure = not (self.output_root / self.output_ref(job)).exists()
                    raise ValueError(result.stderr.decode(errors="replace") or "封面worker执行失败")
            self.complete(command, actor, context, max_receipt_bytes=budget.max_receipt_bytes)
        except GrantedCommandCancelled:
            if executor._pause_for_quit(grant, scheduler):
                return
            def cancelled(connection, current):
                self.repository.finish_job_run(command.workspace_id, job.id, status="cancelled",
                    failure_reason="user_cancelled", impact="当前时间线保持不变。",
                    repair_action="回读原封面状态后重新发起。", connection=connection)
                return f"cover:{job.id}:cancelled"
            scheduler.complete_unit(grant, checkpoint_database_path=self.repository.database_path,
                                    checkpoint_write=cancelled, cancelled=True)
        except RecoveryBlocked:
            raise
        except Exception as exc:
            if executor._pause_for_quit(grant, scheduler):
                return
            if known_failure:
                def failed(connection, current):
                    self.repository.finish_job_run(command.workspace_id, job.id, status="failed",
                        failure_reason=str(exc), impact="当前时间线保持不变。",
                        repair_action="检查原成片、选帧和本机FFmpeg后重新发起。", connection=connection)
                    return f"cover:{job.id}:failed"
                scheduler.fail_unit(grant, reason="cover_generation_failed", failure_write=failed)
            else:
                # Includes late adoption SQL failure. Preserve the concrete
                # cause without turning an unknown side effect into a failure.
                self.repository.append_task_event(TaskEvent(
                    id=f"event_{job.id}_cover_deferred_{grant.generation}",
                    workspace_id=command.workspace_id, task_id=command.task_id, job_id=job.id,
                    event_type="cover_adoption_deferred", payload={"request_id": grant.request_id,
                        "edit_proposal_id": command.proposal_id, "error_type": type(exc).__name__,
                        "reason": str(exc), "receipt_ref": self.receipt_ref(job)}))
                # Defer proves release and retains the original running Job.
                scheduler.defer_unit(grant, reason="transient_failure",
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=budget.runtime_sample_seconds))
        finally:
            with executor._cancellation_lock:
                executor._cancellations.pop(grant.request_id, None)
            if self.on_settled is not None:
                self.on_settled()
