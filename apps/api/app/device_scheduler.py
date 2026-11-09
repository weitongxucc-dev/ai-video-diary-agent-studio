"""Conservative, single-host admission for existing production work.

This module is deliberately not wired to routes or media engines yet. Callers
must provide trusted owner, resource, process and activity sources. A grant is
permission for one bounded local unit, never for an entire CreationTask.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo


WorkClass = Literal["preview_dependency", "normal_make", "background"]
PolicyMode = Literal["asap", "idle", "scheduled"]
Verdict = Literal["ready", "dependency", "stale", "unauthorized", "unknown"]
LocalCheck = Callable[[sqlite3.Connection], Verdict]
RecoveryState = Literal["released", "active", "unknown"]
RetryReason = Literal["failed", "cancelled", "result_invalid"]
DeferReason = Literal["dependency_unavailable", "input_stale", "authorization_lost", "resource_pressure", "window_closed", "device_active", "worker_interrupted", "transient_failure"]


class RecoveryBlocked(RuntimeError):
    """The host may still be occupied; no further heavy unit is admitted."""


class StaleGrantInput(PermissionError):
    """The owner explicitly rejected current inputs; permit fencing is separate."""


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Window:
    timezone_name: str
    start: str  # HH:MM local wall clock
    end: str

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone_name)
        _clock_time(self.start)
        _clock_time(self.end)
        if self.start == self.end:
            raise ValueError("执行窗口的开始和结束不能相同")

    def contains(self, instant: datetime) -> bool:
        local = instant.astimezone(ZoneInfo(self.timezone_name)).time().replace(tzinfo=None)
        start, end = _clock_time(self.start), _clock_time(self.end)
        return start <= local < end if start < end else local >= start or local < end


@dataclass(frozen=True)
class SchedulePolicy:
    mode: PolicyMode = "asap"
    not_before_utc: datetime | None = None
    window: Window | None = None

    def __post_init__(self) -> None:
        if self.mode == "scheduled" and self.not_before_utc is None:
            raise ValueError("指定时间必须有最早开始时间")
        if self.not_before_utc is not None:
            _utc(self.not_before_utc)


@dataclass(frozen=True)
class SubmissionIntent:
    workspace_id: str
    owner_type: str
    owner_id: str
    input_digest: str  # hash unit may use an explicitly verified provisional revision
    unit_key: str
    policy: SchedulePolicy


@dataclass(frozen=True)
class SourceRef:
    """Canonical referenced object; its owner space need not be the task space."""

    workspace_id: str
    object_type: str
    object_id: str
    version: str


@dataclass(frozen=True)
class VerifiedWork:
    workspace_id: str
    owner_type: str
    owner_id: str
    input_digest: str
    unit_key: str
    member_id: str | None
    work_class: WorkClass
    resources: dict[str, object]
    source_refs: tuple[SourceRef, ...] = ()
    stage_input_digest: str | None = None


@dataclass(frozen=True)
class VerifiedRetry:
    actor_id: str
    owner_write: Callable[[sqlite3.Connection], None] | None = None


@dataclass(frozen=True)
class WorkRequest:
    id: str
    workspace_id: str
    member_id: str | None
    owner_type: str
    owner_id: str
    input_digest: str
    unit_key: str
    work_class: WorkClass
    policy: SchedulePolicy
    first_ready_at_utc: datetime
    eligible_since_utc: datetime | None
    eligible_wait_seconds: float
    state: str
    wait_reason: str | None
    resources: dict[str, object]
    source_refs: tuple[SourceRef, ...]
    attempt_number: int
    retry_not_before_utc: datetime | None
    last_grant_generation: int | None


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_token: str
    boot_id: str


@dataclass(frozen=True)
class Grant:
    request_id: str
    workspace_id: str
    owner_type: str
    owner_id: str
    unit_key: str
    input_digest: str
    attempt_number: int
    generation: int
    token: str
    database_path: Path


@dataclass(frozen=True)
class SessionHeartbeat:
    session_id: str
    workspace_id: str
    playing: bool = False
    recording: bool = False
    editing: bool = False
    interaction: bool = False


@dataclass(frozen=True)
class VerifiedSession:
    session_id: str
    workspace_id: str
    member_id: str


class OwnerVerifier(Protocol):
    """Production adapter derives identity from the saved owner and sharing grants.

    check_current rechecks identity, input digest, material stage input digest,
    owner access and every referenced source's current sharing right outside a
    write transaction. check_local rechecks the local facts on the supplied
    transaction without network calls or waiting resource samples. Its use is
    limited to committing that same observation; execution still checks current.
    verify_retry observes the actor's current right and invalid-result evidence
    outside the write transaction, returning any SQL-only owner update for the
    fenced commit. Local revision checks and owner writes use the permit's
    SQLite connection, so revocation and checkpoint use one transaction. For an
    external authorization store, unknown or unavailable must return unknown;
    this module cannot promise a cross-database atomic revocation boundary.
    No client-supplied role, class or refs are authoritative; historical work
    with unknown creator is rejected.
    """

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork: ...
    def check_current(self, request: WorkRequest, conn: sqlite3.Connection) -> Verdict: ...
    def capture_local(self, request: WorkRequest, conn: sqlite3.Connection) -> LocalCheck: ...
    def check_local(self, request: WorkRequest, conn: sqlite3.Connection, observed: LocalCheck) -> Verdict: ...
    def can_manage(self, request: WorkRequest, actor: object) -> bool: ...
    def can_wake(self, request: WorkRequest, actor: object) -> bool: ...
    def verify_session(self, heartbeat: SessionHeartbeat, actor: object) -> VerifiedSession: ...
    def can_view_detail(self, request: WorkRequest, actor: object) -> bool: ...
    def can_view_device_summary(self, actor: object) -> bool: ...
    def verify_retry(self, request: WorkRequest, actor: object, reason: RetryReason, conn: sqlite3.Connection) -> VerifiedRetry: ...


class ResourceAdmission(Protocol):
    verdict: bool | None
    declared: dict[str, object]
    checks: dict[str, dict[str, object]]
    reason: str | None
    observed_interval_seconds: float | None


ResourceAdmissionWrite = Callable[[sqlite3.Connection, WorkRequest, ResourceAdmission, datetime], Literal['input_rejected'] | None]


class ResourceProbe(Protocol):
    def admit(self, request: WorkRequest) -> ResourceAdmission: ...
    def continue_unit(self, grant: Grant) -> bool | None: ...
    def released(self, grant: Grant) -> bool | None: ...


class ProcessProbe(Protocol):
    def current(self) -> ProcessIdentity: ...
    def children_released(self, children: tuple[ProcessIdentity, ...]) -> bool | None: ...
    def recovery_state(self, holder: ProcessIdentity, children: tuple[ProcessIdentity, ...]) -> RecoveryState: ...


CheckpointWrite = Callable[[sqlite3.Connection, Grant], str | None]
FailureWrite = Callable[[sqlite3.Connection, Grant], None]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("时间必须带时区")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _clock_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("执行窗口须使用 HH:MM") from exc
    if parsed.tzinfo is not None or len(value) != 5:
        raise ValueError("执行窗口须使用 HH:MM")
    return parsed


def _identity_json(identity: ProcessIdentity) -> dict[str, object]:
    return {"pid": identity.pid, "start_token": identity.start_token, "boot_id": identity.boot_id}


def _identity_from(data: dict[str, object]) -> ProcessIdentity:
    return ProcessIdentity(int(data["pid"]), str(data["start_token"]), str(data["boot_id"]))


def material_stage_unit_key(stage: str, stage_input_digest: str) -> str:
    """A material stage's full input digest is part of its idempotency key."""
    if not stage or not re.fullmatch(r"[a-z0-9][a-z0-9:._-]*", stage):
        raise ValueError("素材阶段名称无效")
    if not re.fullmatch(r"[0-9a-f]{64}", stage_input_digest):
        raise ValueError("素材阶段输入摘要必须是完整 SHA256")
    return f"{stage}:{stage_input_digest}"


def _material_key_has_digest(owner_type: str, unit_key: str) -> bool:
    if owner_type != "material_asset":
        return True
    stage, separator, digest = unit_key.rpartition(":")
    return bool(separator and re.fullmatch(r"[a-z0-9][a-z0-9:._-]*", stage)
                and re.fullmatch(r"[0-9a-f]{64}", digest))


class DeviceRequestReadAccess:
    """Read original request views without creating a queue, lock or driver."""

    def __init__(self, database_path, *, owners):
        self.database_path = Path(database_path).expanduser().resolve()
        self.owners = owners

    def _connect(self):
        conn = sqlite3.connect(self.database_path.as_uri() + '?mode=ro', uri=True,
                               timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _get(self, request_id: str) -> WorkRequest:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._request(row)

    @staticmethod
    def _request(row: sqlite3.Row) -> WorkRequest:
        window_data = json.loads(row["window_json"]) if row["window_json"] else None
        return WorkRequest(
            id=row["id"], workspace_id=row["workspace_id"], member_id=row["member_id"],
            owner_type=row["owner_type"], owner_id=row["owner_id"], input_digest=row["input_digest"],
            unit_key=row["unit_key"], work_class=row["work_class"],
            policy=SchedulePolicy(row["policy"], _parse(row["not_before_utc"]), Window(**window_data) if window_data else None),
            first_ready_at_utc=_parse(row["first_ready_at_utc"]), eligible_since_utc=_parse(row["eligible_since_utc"]),
            eligible_wait_seconds=float(row["eligible_wait_seconds"]), state=row["state"],
            wait_reason=row["wait_reason"], resources=json.loads(row["resources_json"]),
            source_refs=tuple(SourceRef(**ref) for ref in json.loads(row["source_refs_json"])),
            attempt_number=int(row["attempt_number"]), retry_not_before_utc=_parse(row["retry_not_before_utc"]),
            last_grant_generation=row["last_grant_generation"],
        )

    def view_for(self, request_id: str, actor: object) -> dict[str, object]:
        """Never expose the internal WorkRequest through an HTTP response."""
        request = self._get(request_id)
        if self.owners.can_view_detail(request, actor):
            return {
                "id": request.id, "state": request.state, "wait_reason": request.wait_reason,
                "workspace_id": request.workspace_id, "owner_type": request.owner_type,
                "owner_id": request.owner_id, "unit_key": request.unit_key,
                "first_ready_at": _stamp(request.first_ready_at_utc),
                "attempt_number": request.attempt_number,
                "retry_not_before_at": _stamp(request.retry_not_before_utc) if request.retry_not_before_utc else None,
            }
        if self.owners.can_view_device_summary(actor):
            return {"state": request.state, "wait_reason": request.wait_reason,
                    "device_busy": request.state in {"granted", "yield_requested"}}
        raise PermissionError("无权查看设备队列")

    def task_media_result_request(self, workspace_id, job_id, input_digest, actor):
        """Return original request detail only when the owner permits that detail."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? "
                "AND owner_type='creation_job' AND owner_id=? AND input_digest=? AND unit_key=? "
                "ORDER BY created_at DESC LIMIT 1",
                (workspace_id, job_id, input_digest, f"task_media:{input_digest}")).fetchone()
        if row is None:
            return None, {}
        view = self.view_for(row['id'], actor)
        if view.get('id', view.get('request_id')) != row['id']:
            return None, {}
        return self._request(row), view


class DeviceScheduler(DeviceRequestReadAccess):
    """One SQLite queue plus a host-wide, path-stable OS lock and recovery ledger.

    The lock path must be supplied from one machine-wide device configuration,
    independent of workspace and business database. The sidecar contains only
    execution-control identities. There is intentionally no implicit default.
    """

    def __init__(
        self,
        database_path: str | Path,
        device_lock_path: str | Path,
        *,
        owners: OwnerVerifier,
        resources: ResourceProbe,
        processes: ProcessProbe,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        activity_source_reliable: bool = False,
        session_ttl: timedelta = timedelta(seconds=30),
        admission_recorder: ResourceAdmissionWrite | None = None,
        dispatch_owner: tuple[str, str] | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        raw_lock = Path(device_lock_path).expanduser()
        if not raw_lock.is_absolute() or raw_lock.is_symlink():
            raise ValueError("全机锁路径必须是稳定的本机绝对路径且不能是符号链接")
        self.lock_path = raw_lock.resolve()
        self.ledger_path = Path(str(self.lock_path) + ".state")
        self.lock_key = hashlib.sha256(str(self.lock_path).encode()).hexdigest()
        self.owners, self.resources, self.processes = owners, resources, processes
        self.clock, self.activity_source_reliable, self.session_ttl = clock, activity_source_reliable, session_ttl
        self.admission_recorder = admission_recorder
        if dispatch_owner is not None and (len(dispatch_owner) != 2 or
                any(not isinstance(value, str) or not value for value in dispatch_owner)):
            raise ValueError("调度范围须明确执行对象类型和身份")
        self.dispatch_owner = tuple(dispatch_owner) if dispatch_owner is not None else None
        self._held_fd: int | None = None
        self._last_issued_grant: Grant | None = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> datetime:
        return _utc(self.clock())

    @staticmethod
    def _assert_material_stage_key(trusted: VerifiedWork) -> None:
        if trusted.owner_type != "material_asset":
            return
        if (trusted.stage_input_digest is None or
                not _material_key_has_digest(trusted.owner_type, trusted.unit_key) or
                trusted.unit_key != material_stage_unit_key(
                    trusted.unit_key.rsplit(":", 1)[0], trusted.stage_input_digest)):
            raise ValueError("素材阶段单元键必须包含完整阶段输入摘要")

    def submit(self, intent: SubmissionIntent, actor: object) -> WorkRequest:
        trusted = self.owners.verify_submission(intent, actor)
        actual = (trusted.workspace_id, trusted.owner_type, trusted.owner_id, trusted.input_digest, trusted.unit_key)
        supplied = (intent.workspace_id, intent.owner_type, intent.owner_id, intent.input_digest, intent.unit_key)
        if actual != supplied or any(not value for value in actual):
            raise PermissionError("执行对象或输入版本未通过服务端验证")
        if trusted.work_class not in {"preview_dependency", "normal_make", "background"}:
            raise ValueError("工作类别无效")
        if (trusted.work_class == "background") != (trusted.member_id is None):
            raise PermissionError("前台成员或后台归属无效")
        if trusted.owner_type == "material_asset" and (trusted.work_class != "background" or trusted.member_id is not None):
            raise PermissionError("素材自动单元只能由服务端后台身份创建")
        self._assert_material_stage_key(trusted)
        if any(not all(vars(ref).values()) for ref in trusted.source_refs):
            raise ValueError("引用对象身份不完整")
        now = _stamp(self._now())
        request_id = uuid.uuid4().hex
        window = vars(intent.policy.window) if intent.policy.window else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO device_work_requests
                (id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key, source_refs_json,
                 work_class, policy, not_before_utc, window_json, first_ready_at_utc,
                 state, resources_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?)""",
                (request_id, trusted.workspace_id, trusted.member_id, trusted.owner_type,
                 trusted.owner_id, trusted.input_digest, trusted.unit_key,
                 json.dumps([vars(ref) for ref in trusted.source_refs], sort_keys=True), trusted.work_class,
                 intent.policy.mode, _stamp(intent.policy.not_before_utc) if intent.policy.not_before_utc else None,
                 json.dumps(window), now, json.dumps(trusted.resources, sort_keys=True), now, now),
            )
            row = conn.execute(
                """SELECT * FROM device_work_requests WHERE workspace_id=? AND owner_type=?
                AND owner_id=? AND input_digest=? AND unit_key=?""", supplied,
            ).fetchone()
            assert row is not None
            if (row["member_id"] != trusted.member_id or row["work_class"] != trusted.work_class
                    or row["source_refs_json"] != json.dumps([vars(ref) for ref in trusted.source_refs], sort_keys=True)):
                raise PermissionError("既有执行单元的可信归属已变化")
            conn.commit()
        return self._request(row)

    def replace_unexecuted_request(
        self, old_request_id: str, replacement_intent: SubmissionIntent,
        actor: object, reason: str,
    ) -> WorkRequest:
        """Supersede an unexecuted legacy material probe without resetting its wait age.

        Both rows and the transition event remain in the production database.
        This is intentionally limited to the probe-scene:v1 to probe-meta:v1
        contract change; it cannot migrate a grant, attempt, or result.
        """
        if not reason or len(reason) > 128:
            raise ValueError("替换原因须简短且非空")
        trusted = self.owners.verify_submission(replacement_intent, actor)
        actual = (trusted.workspace_id, trusted.owner_type, trusted.owner_id,
                  trusted.input_digest, trusted.unit_key)
        supplied = (replacement_intent.workspace_id, replacement_intent.owner_type,
                    replacement_intent.owner_id, replacement_intent.input_digest,
                    replacement_intent.unit_key)
        if actual != supplied or any(not value for value in actual):
            raise PermissionError("替换单元未通过服务端验证")
        self._assert_material_stage_key(trusted)
        if (trusted.owner_type != "material_asset" or trusted.work_class != "background"
                or trusted.member_id is not None or trusted.resources.get("kind") != "material_probe_meta"
                or type(trusted.resources.get("size_bytes")) is not int
                or trusted.resources["size_bytes"] < 0):
            raise ValueError("仅支持可信的素材元数据探针替换")
        previous = self._get(old_request_id)
        replacement = replace(previous, unit_key=trusted.unit_key, resources=trusted.resources,
                              source_refs=trusted.source_refs)
        with self._connect() as reader:
            local = self.owners.capture_local(replacement, reader)
            observed = self.owners.check_current(replacement, reader)
        now = _stamp(self._now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old_row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (old_request_id,)).fetchone()
            if old_row is None:
                raise KeyError(old_request_id)
            old = self._request(old_row)
            old_prefix, _, old_digest = old.unit_key.rpartition(":")
            new_prefix, _, new_digest = trusted.unit_key.rpartition(":")
            if (old.owner_type != "material_asset" or old_prefix != "probe-scene:v1"
                    or new_prefix != "probe-meta:v1"
                    or not re.fullmatch(r"[0-9a-f]{64}", old_digest)
                    or not re.fullmatch(r"[0-9a-f]{64}", new_digest)
                    or (old.workspace_id, old.owner_id, old.input_digest) !=
                    (trusted.workspace_id, trusted.owner_id, trusted.input_digest)
                    or old.policy != replacement_intent.policy
                    or old.member_id is not None or old.work_class != "background"
                    or old.source_refs != trusted.source_refs):
                raise ValueError("旧请求与替换单元的素材、摘要或执行策略不匹配")
            existing = conn.execute(
                """SELECT * FROM device_work_requests WHERE workspace_id=? AND owner_type=?
                AND owner_id=? AND input_digest=? AND unit_key=?""", supplied,
            ).fetchone()
            if old.state == "cancelled" and old.wait_reason and old.wait_reason.startswith("replaced_by:"):
                if existing is None or old.wait_reason != f"replaced_by:{existing['id']}":
                    raise RecoveryBlocked("已替换请求的审计指针不一致")
                return self._request(existing)
            if old != previous:
                raise PermissionError("观察期间旧请求已经变化")
            if (old.state not in {"waiting", "blocked"} or old.attempt_number != 1
                    or old.last_grant_generation is not None or old_row["checkpoint_ref"] is not None
                    or old_row["last_failure_reason"] is not None):
                raise ValueError("仅可替换尚未执行的阻断请求")
            if existing is not None and (existing["attempt_number"] != 1
                                         or existing["last_grant_generation"] is not None
                                         or existing["state"] not in {"waiting", "blocked"}):
                raise ValueError("替换单元已开始执行，不能迁移等待年龄")
            refs_json = json.dumps([vars(ref) for ref in trusted.source_refs], sort_keys=True)
            if existing is not None and (existing["member_id"] != trusted.member_id
                                         or existing["work_class"] != trusted.work_class
                                         or existing["source_refs_json"] != refs_json
                                         or self._request(existing).policy != replacement_intent.policy
                                         or json.loads(existing["resources_json"]) != trusted.resources):
                raise PermissionError("既有替换单元的可信归属已变化")
            if existing is None:
                new_id = uuid.uuid4().hex
                window = vars(replacement_intent.policy.window) if replacement_intent.policy.window else None
                conn.execute(
                    """INSERT INTO device_work_requests
                    (id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key,
                     source_refs_json, work_class, policy, not_before_utc, window_json,
                     first_ready_at_utc, eligible_since_utc, eligible_wait_seconds,
                     state, resources_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?)""",
                    (new_id, trusted.workspace_id, trusted.member_id, trusted.owner_type,
                     trusted.owner_id, trusted.input_digest, trusted.unit_key, refs_json,
                     trusted.work_class, replacement_intent.policy.mode,
                     _stamp(replacement_intent.policy.not_before_utc) if replacement_intent.policy.not_before_utc else None,
                     json.dumps(window), old_row["first_ready_at_utc"],
                     old_row["eligible_since_utc"], old_row["eligible_wait_seconds"],
                     json.dumps(trusted.resources, sort_keys=True), now, now),
                )
            else:
                new_id = existing["id"]
                earliest_ready = min(old_row["first_ready_at_utc"], existing["first_ready_at_utc"])
                eligible_since = min(filter(None, (old_row["eligible_since_utc"],
                                                   existing["eligible_since_utc"])), default=None)
                conn.execute(
                    """UPDATE device_work_requests SET first_ready_at_utc=?, eligible_since_utc=?,
                    eligible_wait_seconds=?, updated_at=? WHERE id=?""",
                    (earliest_ready, eligible_since,
                     max(old_row["eligible_wait_seconds"], existing["eligible_wait_seconds"]),
                     now, new_id),
                )
            new_row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (new_id,)).fetchone()
            current = self.owners.check_local(self._request(new_row), conn, local)
            if (observed if current == "ready" else current) != "ready":
                raise PermissionError("替换单元的当前素材或授权尚未就绪")
            self._record_event(conn, old_row, "deferred", f"superseded_by:{new_id}:{reason}", None, now)
            conn.execute("UPDATE device_work_requests SET state='cancelled', wait_reason=?, updated_at=? WHERE id=?",
                         (f"replaced_by:{new_id}", now, old.id))
            conn.commit()
        return self._request(new_row)

    def replace_unstarted_narration_voice(self, job, *, connection, replacement_write):
        return self._replace_unstarted_narration(job, connection=connection,
            replacement_write=replacement_write, reason='voice_selected')

    def _replace_unstarted_narration(self, job, *, connection, replacement_write, reason):
        """One finite never-granted boundary for voice adoption/source revalidation."""
        if reason not in {'voice_selected', 'source_revalidated'}:
            raise ValueError('不支持的原配音替换原因')
        if not connection.in_transaction:
            raise ValueError('声音改选必须与原作业和设备请求在同一事务提交')
        rows = connection.execute("SELECT * FROM device_work_requests WHERE workspace_id=? "
            "AND owner_type='creation_job' AND owner_id=?", (job.workspace_id, job.id)).fetchall()
        current = [row for row in rows if row['state'] != 'cancelled']
        if (len(current) != 1 or any(row['last_grant_generation'] is not None or row['attempt_number'] != 1
                or row['checkpoint_ref'] is not None for row in rows)
                or connection.execute("SELECT 1 FROM device_request_events event JOIN device_work_requests request "
                    "ON event.request_id=request.id WHERE request.workspace_id=? AND request.owner_type='creation_job' "
                    "AND request.owner_id=? AND event.permit_generation IS NOT NULL LIMIT 1",
                    (job.workspace_id, job.id)).fetchone()
                or connection.execute("SELECT 1 FROM device_permit permit JOIN device_work_requests request "
                    "ON permit.request_id=request.id WHERE request.workspace_id=? AND request.owner_type='creation_job' "
                    "AND request.owner_id=? AND permit.state!='free' LIMIT 1", (job.workspace_id, job.id)).fetchone()):
            raise RecoveryBlocked('原配音请求已有授予、结果或归属不唯一，不能改选声音')
        old_row = current[0]
        old = self._request(old_row)
        source_edit = reason == 'source_revalidated' and old.unit_key.startswith(('narration_edit:', 'segment_revision:'))
        if (not (old.unit_key.startswith('narration:') or source_edit) or old.input_digest != job.input_digest
                or old.source_refs or old.work_class != ('preview_dependency' if source_edit else 'normal_make')
                or old.state not in {'waiting', 'blocked'} and not (
                    old.state == 'failed' and old_row['last_failure_reason'] == 'declared_resources_exceed_budget')):
            raise RecoveryBlocked('原配音请求不在可安全改选的未执行边界')
        intent, trusted = replacement_write(connection, old)
        actual = (trusted.workspace_id, trusted.owner_type, trusted.owner_id, trusted.input_digest, trusted.unit_key)
        if (actual != (intent.workspace_id, intent.owner_type, intent.owner_id, intent.input_digest, intent.unit_key)
                or actual[:3] != (old.workspace_id, old.owner_type, old.owner_id)
                or not (trusted.unit_key.startswith('narration:segment:v1:') or source_edit
                    and trusted.unit_key.startswith(old.unit_key.split(':', 1)[0]+':'))
                or trusted.member_id != old.member_id or trusted.work_class != old.work_class
                or trusted.source_refs != old.source_refs or intent.policy != old.policy
                or trusted.resources.get('parent_timeout_seconds', trusted.resources['timeout_seconds']) != old.resources.get('parent_timeout_seconds', old.resources['timeout_seconds'])
                or trusted.resources.get('parent_max_output_bytes', trusted.resources['max_output_bytes']) != old.resources.get('parent_max_output_bytes', old.resources['max_output_bytes'])):
            raise PermissionError('声音改选不能改变原作业、成员、文案归属或等待策略')
        new_id = uuid.uuid4().hex
        now = _stamp(self._now())
        connection.execute("INSERT INTO device_work_requests (id,workspace_id,member_id,owner_type,owner_id,input_digest,unit_key,"
            "source_refs_json,work_class,policy,not_before_utc,window_json,first_ready_at_utc,eligible_since_utc,"
            "eligible_wait_seconds,state,resources_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting',?,?,?)",
            (new_id, trusted.workspace_id, trusted.member_id, trusted.owner_type, trusted.owner_id, trusted.input_digest,
             trusted.unit_key, old_row['source_refs_json'], trusted.work_class, old_row['policy'], old_row['not_before_utc'],
             old_row['window_json'], old_row['first_ready_at_utc'], old_row['eligible_since_utc'],
             old_row['eligible_wait_seconds'], json.dumps(trusted.resources, sort_keys=True), now, now))
        self._record_event(connection, old_row, 'deferred', f'superseded_by:{new_id}:{reason}', trusted.member_id, now)
        connection.execute("UPDATE device_work_requests SET state='cancelled',wait_reason=?,updated_at=? WHERE id=?",
            (f'replaced_by:{new_id}', now, old.id))
        row = connection.execute('SELECT * FROM device_work_requests WHERE id=?', (new_id,)).fetchone()
        self._record_event(connection, row, 'deferred', f'replaces:{old.id}:{reason}', trusted.member_id, now)
        return self._request(row)

    def replace_ungranted_creation_narration(self, old_request_id, *, replacement_write, replacement_audit=None):
        """Preserve original Job, allowance and wait age; never cross a grant."""
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old_row = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (old_request_id,)).fetchone()
            if old_row is None:
                raise KeyError(old_request_id)
            old = self._request(old_row)
            if old.owner_type != 'creation_job' or not old.unit_key.startswith(('narration:', 'narration_edit:', 'segment_revision:')):
                raise ValueError('仅支持原配音请求的来源重验证')
            if old.state == 'cancelled' and (old.wait_reason or '').startswith('replaced_by:'):
                row = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (old.wait_reason.split(':', 1)[1],)).fetchone()
                if row is None or (row['workspace_id'], row['owner_id'], row['input_digest']) != (old.workspace_id, old.owner_id, old.input_digest):
                    raise RecoveryBlocked('原配音请求的替代指针不可核实')
                return self._request(row)
            # Only identity fields are consumed by the shared boundary. Job facts
            # and its zero-consumption invariant remain the creation owner's work.
            from types import SimpleNamespace
            job = SimpleNamespace(workspace_id=old.workspace_id, id=old.owner_id, input_digest=old.input_digest)
            new = self._replace_unstarted_narration(job, connection=conn,
                replacement_write=replacement_write, reason='source_revalidated')
            if new.input_digest != old.input_digest:
                raise PermissionError('来源重验证不能更换业务输入')
            if replacement_audit is not None:
                replacement_audit(conn, old, new)
            return new

    def resume_creation_narration(self, request_id, *, resume_write):
        """Explicit continuation keeps the released unit, identity and original allowance."""
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            request = self._request(row)
            if (request.owner_type != 'creation_job' or request.state not in {'failed', 'cancelled'}
                    or not request.unit_key.startswith(('narration:segment:v1:', 'narration:merge:v1:', 'segment_video:', 'segment_revision:'))):
                raise ValueError('只能显式继续原已结束的配音单元')
            permit = conn.execute('SELECT request_id FROM device_permit WHERE id=1').fetchone()
            if permit is None or permit['request_id'] == request_id or conn.execute(
                    "SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' AND owner_id=? "
                    "AND state IN ('waiting','blocked','granted','yield_requested')", (request.workspace_id, request.owner_id)).fetchone():
                raise RecoveryBlocked('原配音仍有许可或活动请求，先核对原执行')
            trusted = resume_write(conn, request)
            if ((trusted.workspace_id, trusted.owner_type, trusted.owner_id, trusted.input_digest, trusted.unit_key,
                 trusted.member_id, trusted.work_class, trusted.source_refs, trusted.resources)
                    != (request.workspace_id, request.owner_type, request.owner_id, request.input_digest, request.unit_key,
                        request.member_id, request.work_class, request.source_refs, request.resources)):
                raise PermissionError('继续配音不能替换原单元输入或身份')
            now = _stamp(self._now())
            self._record_event(conn, row, 'retried', 'explicit_narration_continue', trusted.member_id, now)
            conn.execute("UPDATE device_work_requests SET state='waiting',wait_reason=NULL,last_failure_reason=NULL,"
                "checkpoint_ref=NULL,retry_not_before_utc=NULL,attempt_number=attempt_number+1,updated_at=? WHERE id=?", (now, request_id))
        return self._get(request_id)

    def wake_existing(self, intent: SubmissionIntent, actor: object) -> dict[str, object]:
        """Authorized manual analysis may wake an existing material unit only.

        No member, class, ready age, resource request or policy is changed.
        Discovery/automatic submission creates the unit before this call.
        """
        if intent.owner_type != "material_asset":
            raise ValueError("仅素材单元支持手动唤醒")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM device_work_requests WHERE workspace_id=? AND owner_type=?
                AND owner_id=? AND input_digest=? AND unit_key=?""",
                (intent.workspace_id, intent.owner_type, intent.owner_id, intent.input_digest, intent.unit_key),
            ).fetchone()
            if row is None:
                raise KeyError("素材候选尚未由后台登记")
            request = self._request(row)
            if not self.owners.can_wake(request, actor):
                raise PermissionError("不能唤醒未授权素材")
            if not _material_key_has_digest(request.owner_type, request.unit_key):
                raise ValueError("旧素材请求缺少阶段输入摘要，须由发现循环创建新请求")
            if request.state in {"done", "cancelled"}:
                raise ValueError("该素材单元已经结束，须核验新的源版本")
            conn.execute("UPDATE device_work_requests SET updated_at=? WHERE id=?", (_stamp(self._now()), request.id))
            conn.commit()
        return self.view_for(request.id, actor)





    def list_queue_for(self, actor: object) -> dict[str, object]:
        if not self.owners.can_view_device_summary(actor):
            raise PermissionError("无权查看设备队列")
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM device_work_requests WHERE state IN ('waiting', 'blocked', 'granted', 'yield_requested')").fetchall()
        requests = [self._request(row) for row in rows]
        mine = [self.view_for(item.id, actor) for item in requests if self.owners.can_view_detail(item, actor)]
        return {"mine": mine,
                "device": {"running": sum(item.state in {"granted", "yield_requested"} for item in requests),
                           "waiting": sum(item.state in {"waiting", "blocked"} for item in requests)}}

    def heartbeat_session(self, heartbeat: SessionHeartbeat, actor: object) -> None:
        trusted = self.owners.verify_session(heartbeat, actor)
        if (trusted.session_id, trusted.workspace_id) != (heartbeat.session_id, heartbeat.workspace_id) or not trusted.member_id:
            raise PermissionError("会话归属未通过服务端验证")
        now = _stamp(self._now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT member_id, last_interaction_at_utc FROM device_session_activity WHERE session_id=?", (trusted.session_id,)).fetchone()
            if previous is not None and previous["member_id"] != trusted.member_id:
                # 会话一旦记在某个成员名下就不能改挂到别人身上（auth §9：
                # 外部身份必须绑定成员）。另一个成员重开页面会拿到新的 session_id。
                raise PermissionError("该会话已绑定其他成员，不能重新绑定")
            last_interaction = now if heartbeat.interaction or previous is None else previous["last_interaction_at_utc"]
            conn.execute(
                """INSERT INTO device_session_activity
                (session_id, workspace_id, member_id, last_heartbeat_at_utc, last_interaction_at_utc, playing, recording, editing)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET workspace_id=excluded.workspace_id,
                member_id=excluded.member_id, last_heartbeat_at_utc=excluded.last_heartbeat_at_utc,
                last_interaction_at_utc=excluded.last_interaction_at_utc,
                playing=excluded.playing, recording=excluded.recording, editing=excluded.editing""",
                (trusted.session_id, trusted.workspace_id, trusted.member_id, now, last_interaction,
                 int(heartbeat.playing), int(heartbeat.recording), int(heartbeat.editing)),
            )
            conn.commit()

    def _idle(self, conn: sqlite3.Connection, now: datetime) -> bool | None:
        if not self.activity_source_reliable:
            return None
        for row in conn.execute("SELECT * FROM device_session_activity"):
            if now - _parse(row["last_heartbeat_at_utc"]) > self.session_ttl:
                continue
            if row["playing"] or row["recording"] or row["editing"]:
                return False
            if now - _parse(row["last_interaction_at_utc"]) < timedelta(minutes=5):
                return False
        return True

    def _acquire_file(self) -> tuple[int | None, bool]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.lock_path.exists()
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None, existed
        return fd, existed

    @staticmethod
    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _read_ledger(self, existed: bool) -> dict[str, object]:
        if not self.ledger_path.exists():
            if existed:
                raise RecoveryBlocked("全机许可记录缺失，不能判断旧进程")
            initial = {"state": "free", "generation": 0, "database_path": None}
            self._write_ledger(initial)
            return initial
        try:
            data = json.loads(self.ledger_path.read_text())
            if data["state"] not in {"free", "held", "reconciling"} or not isinstance(data["generation"], int):
                raise ValueError("invalid ledger")
            return data
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RecoveryBlocked("全机许可记录不可核查") from exc

    def _write_ledger(self, data: dict[str, object]) -> None:
        temporary = self.ledger_path.with_name(f"{self.ledger_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x") as output:
                json.dump(data, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.ledger_path)
            directory = os.open(self.ledger_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _recover_ledger(self, data: dict[str, object]) -> dict[str, object]:
        if data["state"] == "free":
            return data
        try:
            holder = _identity_from(data["holder"])
            children = tuple(_identity_from(item) for item in data.get("children", []))
            state = self.processes.recovery_state(holder, children)
        except (KeyError, TypeError, ValueError):
            state = "unknown"
        if state != "released":
            if state == "unknown":
                data["state"] = "reconciling"
                self._write_ledger(data)
            raise RecoveryBlocked("旧执行者或子进程仍占用设备，或归属无法核查")
        data["state"] = "free"
        for field in ("holder", "children", "request_id", "token_hash"):
            data.pop(field, None)
        self._write_ledger(data)
        return data

    def _check_database_switch(self, data: dict[str, object]) -> None:
        old = data.get("database_path")
        if not old or Path(str(old)) == self.database_path:
            return
        old_path = Path(str(old))
        if not old_path.is_file():
            raise RecoveryBlocked("前一业务库不可用，不能确认其待办已处理")
        try:
            with sqlite3.connect(f"file:{quote(str(old_path))}?mode=ro", uri=True) as old_conn:
                old_conn.execute("BEGIN")  # All prior-dataset facts share one read snapshot.
                pending = old_conn.execute(
                    """SELECT 1 FROM device_work_requests
                    WHERE state IN ('waiting', 'blocked', 'granted', 'yield_requested') LIMIT 1"""
                ).fetchone()
                old_permit = old_conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()
                if pending and old_conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_runs'"
                ).fetchone():
                    pending = old_conn.execute(
                        """SELECT 1 FROM device_work_requests AS request
                        WHERE request.state IN ('waiting', 'blocked', 'granted', 'yield_requested')
                        AND NOT (
                            request.state IS 'blocked'
                            AND request.wait_reason IS 'input_stale'
                            AND request.last_grant_generation IS NULL
                            AND request.owner_type IS 'creation_job'
                            AND EXISTS (
                                SELECT 1 FROM job_runs AS job
                                WHERE job.id = request.owner_id
                                  AND job.workspace_id = request.workspace_id
                                  AND job.kind = 'creation_task_execution'
                                  AND job.status = 'succeeded'
                            )
                        ) LIMIT 1"""
                    ).fetchone()
        except sqlite3.Error as exc:
            raise RecoveryBlocked("前一业务库的待办无法核查") from exc
        if pending or old_permit is None or old_permit[0] != "free":
            raise RecoveryBlocked("前一业务库仍有未处理设备请求，不能切换协调者")

    def _reconcile_local(self, conn: sqlite3.Connection, data: dict[str, object], now: str) -> sqlite3.Row:
        permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
        if permit is None:
            raise RecoveryBlocked("设备许可表缺失")
        if permit["lock_key"] not in {None, self.lock_key}:
            raise RecoveryBlocked("业务库关联了另一全机锁路径")
        if permit["state"] != "free":
            try:
                holder = _identity_from(json.loads(permit["holder_json"]))
                children = tuple(_identity_from(item) for item in json.loads(permit["children_json"]))
                state = self.processes.recovery_state(holder, children)
            except (ValueError, TypeError, KeyError):
                state = "unknown"
            if state != "released" or data["state"] != "free":
                raise RecoveryBlocked("业务库原许可尚未确认释放")
            conn.execute("UPDATE device_work_requests SET state='waiting', wait_reason='recovered', updated_at=? WHERE id=? AND state IN ('granted', 'yield_requested')", (now, permit["request_id"]))
            conn.execute("UPDATE device_permit SET state='free', request_id=NULL, token_hash=NULL, holder_json=NULL, children_json='[]', granted_at_utc=NULL, heartbeat_at_utc=NULL WHERE id=1")
            permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
        return permit

    def _update_wait(self, conn: sqlite3.Connection, row: sqlite3.Row, now: datetime, reason: str | None) -> None:
        since = _parse(row["eligible_since_utc"])
        total = float(row["eligible_wait_seconds"])
        if reason is None:
            since = since or now
        else:
            if since is not None:
                total += max(0, (now - since).total_seconds())
            since = None
        conn.execute(
            "UPDATE device_work_requests SET state=?, wait_reason=?, eligible_since_utc=?, eligible_wait_seconds=?, updated_at=? WHERE id=?",
            ("waiting" if reason is None else "blocked", reason, _stamp(since) if since else None, total, _stamp(now), row["id"]),
        )

    def _schedule_wait(self, conn: sqlite3.Connection, request: WorkRequest, now: datetime) -> str | None:
        if not _material_key_has_digest(request.owner_type, request.unit_key):
            return "legacy_stage_digest_missing"
        if request.policy.not_before_utc and now < request.policy.not_before_utc:
            return "scheduled_time"
        if request.retry_not_before_utc and now < request.retry_not_before_utc:
            return "retry_not_before"
        if request.policy.window and not request.policy.window.contains(now):
            return "outside_window"
        if request.policy.mode == "idle":
            idle = self._idle(conn, now)
            if idle is not True:
                return "activity_unknown" if idle is None else "device_active"
        return None

    def _eligible(self, conn: sqlite3.Connection, row: sqlite3.Row, now: datetime,
                  observed_owner: Verdict, decision: ResourceAdmission | None,
                  observed_at: datetime, local_check: LocalCheck) -> bool:
        request = self._request(row)
        wait = self._schedule_wait(conn, request, now)
        if wait is not None:
            self._update_wait(conn, row, now, wait)
            return False
        owner_state = self.owners.check_local(request, conn, local_check)
        if owner_state == "ready":
            owner_state = observed_owner
        if owner_state == "stale":
            self._update_wait(conn, row, now, "input_stale")
            return False
        if owner_state == "unauthorized":
            self._update_wait(conn, row, now, "authorization_lost")
            return False
        if owner_state != "ready":
            self._update_wait(conn, row, now, "dependency" if owner_state == "dependency" else "owner_unknown")
            return False
        assert decision is not None  # A ready observation includes its actual resource decision.
        disposition=(self.admission_recorder(conn, request, decision, observed_at)
            if self.admission_recorder is not None else None)
        if disposition=='input_rejected':
            if decision.verdict is not False or decision.reason!='declared_resources_exceed_budget':
                raise ValueError('固定输入拒绝必须来自本次确定的声明超限')
            self._record_event(conn,row,'failed',decision.reason,None,_stamp(now))
            conn.execute("UPDATE device_work_requests SET state='failed',wait_reason=?,last_failure_reason=?,"
                "eligible_since_utc=NULL,retry_not_before_utc=NULL,updated_at=? WHERE id=?",
                (decision.reason,decision.reason,_stamp(now),request.id))
            return False
        available = decision.verdict
        if available is not True:
            self._update_wait(conn, row, now, "resource_unknown" if available is None else "resource_busy")
            return False
        self._update_wait(conn, row, now, None)
        return True

    @staticmethod
    def _choose(rows: list[sqlite3.Row], permit: sqlite3.Row, now: datetime) -> sqlite3.Row:
        groups = {kind: [r for r in rows if r["work_class"] == kind] for kind in ("preview_dependency", "normal_make", "background")}
        aged = [r for r in groups["background"] if float(r["eligible_wait_seconds"]) + max(0, (now - _parse(r["eligible_since_utc"])).total_seconds()) >= 1800]
        if aged:
            kind = "background"
        elif permit["normal_burst"] >= 3 and groups["background"]:
            kind = "background"
        elif permit["preview_burst"] >= 3 and groups["normal_make"]:
            kind = "normal_make"
        else:
            kind = next(k for k, values in groups.items() if values)
        candidates = aged if aged else groups[kind]
        last_groups = json.loads(permit["last_groups_json"])
        by_group: dict[str, list[sqlite3.Row]] = {}
        for row in candidates:
            key = str(row["member_id"] or f"workspace:{row['workspace_id']}")
            by_group.setdefault(key, []).append(row)
        keys = sorted(by_group)
        last = last_groups.get(kind)
        selected_key = next((key for key in keys if last is None or key > last), keys[0])
        return min(by_group[selected_key], key=lambda r: (r["first_ready_at_utc"], r["id"]))

    def reconcile_released(self) -> int:
        """Prove old groups released without selecting or admitting new work.

        Return the reconciled free permit generation. An owner doing metadata
        recovery must fence that generation under its own write transaction.
        """
        if self._held_fd is not None:
            raise RecoveryBlocked("当前协调者仍持有设备许可，不能提前恢复业务终态")
        fd, existed = self._acquire_file()
        if fd is None:
            raise RecoveryBlocked("设备锁仍被占用，旧执行单元尚不能恢复")
        try:
            data = self._recover_ledger(self._read_ledger(existed))
            self._check_database_switch(data)
            now = self._now()
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                permit = self._reconcile_local(conn, data, _stamp(now))
                conn.commit()
                return int(permit["generation"])
        finally:
            self._unlock(fd)

    def complete_recovered_quality(self, connection: sqlite3.Connection, expected: VerifiedWork, *,
                                   generation: int, checkpoint_ref: str,
                                   status: Literal["succeeded", "failed", "cancelled"]) -> None:
        """Close only an original quality request in its owner's recovery transaction."""
        database = next(row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main")
        if not connection.in_transaction or Path(database).resolve() != self.database_path:
            raise ValueError("质检恢复终态必须使用同一 Production SQLite 写事务")
        if expected.owner_type != "quality_check" or status not in {"succeeded", "failed", "cancelled"} or not checkpoint_ref:
            raise ValueError("恢复终态仅属于封存的质检请求")
        permit = connection.execute("SELECT state,generation FROM device_permit WHERE id=1").fetchone()
        if permit is None or (permit["state"], permit["generation"]) != ("free", generation):
            raise RecoveryBlocked("设备许可在质检恢复提交前已变化")
        rows = connection.execute("SELECT * FROM device_work_requests WHERE workspace_id=? AND owner_type='quality_check' AND owner_id=? AND unit_key=?",
                                  (expected.workspace_id, expected.owner_id, expected.unit_key)).fetchall()
        if not rows:
            if status == "succeeded":
                raise RecoveryBlocked("已完成质检缺少原设备请求，不能伪造检查点")
            return  # A failed pre-submission reservation has no queue fact to close.
        if len(rows) != 1:
            raise RecoveryBlocked("质检恢复存在多个原设备请求")
        row = rows[0]
        request = self._request(row)
        if (request.input_digest, request.unit_key, request.member_id, request.work_class,
                request.resources, request.source_refs) != (expected.input_digest, expected.unit_key,
                expected.member_id, expected.work_class, expected.resources, expected.source_refs):
            raise PermissionError("质检恢复请求未绑定原输入、身份和来源")
        state = "done" if status == "succeeded" else status
        terminal_checkpoint = None if status == "failed" else checkpoint_ref
        if row["state"] in {"done", "failed", "cancelled"} and row["checkpoint_ref"] is not None:
            if (row["state"], row["checkpoint_ref"]) != (state, terminal_checkpoint):
                raise RecoveryBlocked("原质检终态与检查点已存在，不能替换")
            return
        if row["state"] not in {"waiting", "blocked"} and not (row["state"] == state == "cancelled"):
            raise RecoveryBlocked("原质检请求尚未处于可恢复的已释放状态")
        now = _stamp(self._now())
        if status == "failed":
            self._record_event(connection, row, "failed", "quality_recovery_failed", None, now)
        # Original complete/cancel has no success event; failure keeps its failed event.
        # Last SQL write: rejection rolls back the owner's check/job/event as well.
        connection.execute("UPDATE device_work_requests SET state=?,checkpoint_ref=?,wait_reason=?,retry_not_before_utc=NULL,last_failure_reason=CASE WHEN ?='failed' THEN 'quality_recovery_failed' ELSE last_failure_reason END,updated_at=? WHERE id=?",
                           (state, terminal_checkpoint, "quality_recovery_failed" if status == "failed" else None,
                            status, now, request.id))

    def complete_recovered_narration_pcm(self, connection, request, *, generation, checkpoint_ref):
        """Fence metadata recovery of this original released PCM request only."""
        database=next(row[2] for row in connection.execute('PRAGMA database_list') if row[1]=='main')
        if not connection.in_transaction or Path(database).resolve()!=self.database_path:
            raise ValueError('PCM恢复必须使用同一Production写事务')
        if request.owner_type!='creation_job' or request.resources.get('execution_mode')!='cpu_pcm_wav_assembly':
            raise PermissionError('恢复只属于原PCM请求')
        permit=connection.execute('SELECT state,generation FROM device_permit WHERE id=1').fetchone()
        if permit is None or (permit['state'],permit['generation'])!=('free',generation):
            raise RecoveryBlocked('原进程释放证明在PCM恢复提交前已变化')
        row=connection.execute('SELECT * FROM device_work_requests WHERE id=?',(request.id,)).fetchone()
        if row is None or self._request(row)!=request or request.state not in {'waiting','blocked'}:
            raise RecoveryBlocked('原PCM请求在恢复提交前已变化或仍占用设备')
        connection.execute("UPDATE device_work_requests SET state='done',checkpoint_ref=?,wait_reason=NULL,"
            "retry_not_before_utc=NULL,updated_at=? WHERE id=?",(checkpoint_ref,_stamp(self._now()),request.id))

    def pick_and_grant(self) -> Grant | None:
        if self._held_fd is not None:
            return None
        fd, existed = self._acquire_file()
        if fd is None:
            return None
        committed = False
        prepared_token_hash: str | None = None
        try:
            data = self._recover_ledger(self._read_ledger(existed))
            self._check_database_switch(data)
            now = self._now()
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                permit = self._reconcile_local(conn, data, _stamp(now))
                # Preparation shares the machine permit, but must not reclassify
                # unrelated business requests as owner_unknown or admit old work.
                scope_sql = " AND owner_type=? AND owner_id=?" if self.dispatch_owner else ""
                rows = conn.execute("SELECT * FROM device_work_requests WHERE state IN ('waiting', 'blocked')"
                                    + scope_sql + " ORDER BY first_ready_at_utc, id",
                                    self.dispatch_owner or ()).fetchall()
                candidates = []
                for row in rows:
                    request = self._request(row)
                    wait = self._schedule_wait(conn, request, now)
                    if wait is not None:
                        self._update_wait(conn, row, now, wait)
                    else:
                        candidates.append(request)
                conn.commit()
            observations = []
            for request in candidates:
                with self._connect() as conn:
                    local_check = self.owners.capture_local(request, conn)
                    owner_state = self.owners.check_current(request, conn)
                decision = self.resources.admit(request) if owner_state == "ready" else None
                observations.append((request, owner_state, decision, self._now(), local_check))
            now = self._now()
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current_permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
                if current_permit is None or current_permit["state"] != "free" or current_permit["generation"] != permit["generation"] or current_permit["lock_key"] != permit["lock_key"]:
                    raise RecoveryBlocked("设备许可在候选核验后已变化")
                permit = current_permit
                ready_ids = []
                for request, owner_state, decision, observed_at, local_check in observations:
                    row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request.id,)).fetchone()
                    if row is None or self._request(row) != request:
                        continue
                    if self._eligible(conn, row, now, owner_state, decision, observed_at, local_check):
                        ready_ids.append(request.id)
                if not ready_ids:
                    conn.commit()
                    return None
                ready = conn.execute(
                    f"SELECT * FROM device_work_requests WHERE id IN ({','.join('?' for _ in ready_ids)})",
                    ready_ids,
                ).fetchall()
                # Idle work yields whenever a foreground heavy unit is ready.
                if any(r["work_class"] != "background" and r["policy"] != "idle" for r in ready):
                    ready = [r for r in ready if r["policy"] != "idle"]
                if not ready:
                    conn.commit()
                    return None
                chosen = self._choose(ready, permit, now)
                selected = self._request(chosen)
                conn.commit()
            # Earlier candidates may have waited behind slow owner checks. The
            # selected unit gets its own current decision, outside the write lock.
            with self._connect() as conn:
                local_check = self.owners.capture_local(selected, conn)
                owner_state = self.owners.check_current(selected, conn)
            decision = self.resources.admit(selected) if owner_state == "ready" else None
            observed_at = self._now()
            now = self._now()
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current_permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
                if current_permit is None or current_permit["state"] != "free" or current_permit["generation"] != permit["generation"] or current_permit["lock_key"] != permit["lock_key"]:
                    raise RecoveryBlocked("设备许可在最终候选核验后已变化")
                permit = current_permit
                chosen = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (selected.id,)).fetchone()
                if chosen is None or self._request(chosen) != selected:
                    conn.commit()
                    return None
                if not self._eligible(conn, chosen, now, owner_state, decision, observed_at, local_check):
                    conn.commit()
                    return None
                identity = self.processes.current()
                if identity.pid <= 0 or not identity.start_token or not identity.boot_id:
                    raise RecoveryBlocked("当前执行进程身份不可核查")
                token = uuid.uuid4().hex
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                generation = int(data["generation"]) + 1
                grant = Grant(chosen["id"], chosen["workspace_id"], chosen["owner_type"],
                              chosen["owner_id"], chosen["unit_key"], chosen["input_digest"],
                              int(chosen["attempt_number"]), generation, token, self.database_path)
                held_data = {"state": "held", "generation": generation, "database_path": str(self.database_path),
                             "request_id": chosen["id"], "token_hash": token_hash,
                             "holder": _identity_json(identity), "children": []}
                self._write_ledger(held_data)
                prepared_token_hash = token_hash
                last_groups = json.loads(permit["last_groups_json"])
                kind = chosen["work_class"]
                last_groups[kind] = chosen["member_id"] or f"workspace:{chosen['workspace_id']}"
                preview_burst = int(permit["preview_burst"]) + 1 if kind == "preview_dependency" else 0
                normal_burst = int(permit["normal_burst"]) + 1 if kind == "normal_make" else (0 if kind == "background" else int(permit["normal_burst"]))
                conn.execute("""UPDATE device_work_requests SET state='granted', wait_reason=NULL,
                    eligible_since_utc=NULL, eligible_wait_seconds=0, retry_not_before_utc=NULL,
                    last_grant_generation=?, updated_at=? WHERE id=?""",
                    (generation, _stamp(now), chosen["id"]),
                )
                conn.execute(
                    """UPDATE device_permit SET lock_key=?, generation=?, request_id=?, token_hash=?, holder_json=?,
                    children_json='[]', granted_at_utc=?, heartbeat_at_utc=?, state='held',
                    last_groups_json=?, preview_burst=?, normal_burst=? WHERE id=1 AND state='free'""",
                    (self.lock_key, generation, chosen["id"], token_hash, json.dumps(_identity_json(identity)),
                     _stamp(now), _stamp(now), json.dumps(last_groups), preview_burst, normal_burst),
                )
                conn.commit()
                committed = True
            self._held_fd = fd
            self._last_issued_grant = grant
            return grant
        except Exception:
            if not committed:
                # A prepared ledger is conservative on crash. Here no worker was returned.
                try:
                    current = self._read_ledger(True)
                    if prepared_token_hash and current.get("token_hash") == prepared_token_hash:
                        current["state"] = "free"
                        for field in ("holder", "children", "request_id", "token_hash"):
                            current.pop(field, None)
                        self._write_ledger(current)
                except RecoveryBlocked:
                    pass
            raise
        finally:
            if self._held_fd != fd:
                self._unlock(fd)

    def _assert_grant(self, conn: sqlite3.Connection, grant: Grant) -> dict[str, object]:
        if self._held_fd is None or grant.database_path != self.database_path:
            raise PermissionError("执行许可不属于当前业务库或持有者")
        data = self._read_ledger(True)
        expected_hash = hashlib.sha256(grant.token.encode()).hexdigest()
        permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
        if (data.get("state"), data.get("request_id"), data.get("generation"), data.get("token_hash")) != ("held", grant.request_id, grant.generation, expected_hash):
            raise PermissionError("设备许可已失效")
        if permit is None or (permit["state"], permit["request_id"], permit["generation"], permit["token_hash"]) != ("held", grant.request_id, grant.generation, expected_hash):
            raise PermissionError("业务库设备许可已失效")
        request = conn.execute("SELECT workspace_id, owner_type, owner_id, unit_key, input_digest, attempt_number, state FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
        if (request is None or (request["workspace_id"], request["owner_type"], request["owner_id"],
                                request["unit_key"], request["input_digest"])
                != (grant.workspace_id, grant.owner_type, grant.owner_id, grant.unit_key, grant.input_digest)
                or request["attempt_number"] != grant.attempt_number
                or request["state"] not in {"granted", "yield_requested"}):
            raise PermissionError("设备许可与执行单元不匹配")
        return data

    def _assert_current(self, conn: sqlite3.Connection, grant: Grant) -> None:
        row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
        if row is not None and not _material_key_has_digest(row["owner_type"], row["unit_key"]):
            raise PermissionError("旧素材请求缺少阶段输入摘要，不能执行")
        if row is None:
            raise PermissionError("输入版本或共享授权已失效")
        verdict = self.owners.check_current(self._request(row), conn)
        self._require_current(verdict, grant)

    @staticmethod
    def _require_current(verdict: Verdict, grant: Grant) -> None:
        if verdict != "ready":
            correlation = {}
            verification_id = getattr(verdict, "verification_id", None)
            if isinstance(verification_id, str) and re.fullmatch(r"[0-9a-f]{32}", verification_id):
                correlation["verification_id"] = verification_id
            logger.warning("Device owner current check refused: %s", json.dumps({
                "request_id": grant.request_id, "attempt_number": grant.attempt_number,
                "permit_generation": grant.generation,
                "verdict": verdict if verdict in ("stale", "unauthorized", "dependency", "unknown") else "unknown",
                **correlation,
            }))
            if verdict == "stale":
                raise StaleGrantInput("输入版本已过期")
            error = PermissionError("当前成员或设备许可暂不可核实" if verdict == "unknown" else "输入版本或共享授权已失效")
            error.business_failure = {"source": "owner_current", "verdict": verdict, **correlation}
            raise error

    def _observe_grant(self, grant: Grant):
        with self._connect() as conn:
            self._assert_grant(conn, grant)
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            request = self._request(row)
            local = self.owners.capture_local(request, conn)
            verdict = self.owners.check_current(request, conn)
        return request, local, verdict

    def _observed_current(self, conn, observation):
        request, local, verdict = observation
        row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request.id,)).fetchone()
        if row is None or self._request(row) != request:
            raise PermissionError("观察期间执行单元、取消状态或输入已变化")
        if not _material_key_has_digest(request.owner_type, request.unit_key):
            raise PermissionError("旧素材请求缺少阶段输入摘要，不能执行")
        current = self.owners.check_local(request, conn, local)
        return verdict if current == "ready" else current

    def assert_grant(self, grant: Grant) -> None:
        """Call immediately before a model/FFmpeg unit; nested calls reuse grant."""
        with self._connect() as conn:
            self._assert_grant(conn, grant)
            self._assert_current(conn, grant)
            if self.resources.continue_unit(grant) is not True:
                raise RecoveryBlocked("运行中资源未知或不足，须在安全边界让出")

    def capture_running_check(self, grant: Grant):
        """Freeze this command's local dependencies before launch, then read versions."""
        with self._connect() as conn:
            self._assert_grant(conn, grant)
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            request = self._request(row)
            local = self.owners.capture_local(request, conn)
        def check():
            with self._connect() as conn:
                self._assert_grant(conn, grant)
                self._require_current(self.owners.check_local(request, conn, local), grant)
                if self.resources.continue_unit(grant) is not True:
                    raise RecoveryBlocked("运行中资源未知或不足，须在安全边界让出")
        return check

    def shutdown_released(self) -> bool:
        """Read existing release proofs; never reclaim a group or invent a receipt."""
        if self._held_fd is not None:
            return False
        with self._connect() as conn:
            row = conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()
        return row is not None and row[0] == "free" and self._read_ledger(self.lock_path.exists())["state"] == "free"

    def assert_released(self, grant: Grant) -> None:
        """Verify this coordinator's exact grant no longer owns the host or DB."""
        if grant != self._last_issued_grant or grant.database_path != self.database_path:
            raise PermissionError("释放核验的许可不是当前协调者派发的许可")
        if self._held_fd is not None:
            raise RecoveryBlocked("执行单元返回时仍持有全机许可")
        ledger = self._read_ledger(True)
        ledger_generation = int(ledger["generation"])
        if (ledger_generation < grant.generation or
                (ledger_generation == grant.generation and
                 (ledger["state"] != "free" or ledger.get("request_id") is not None
                  or ledger.get("token_hash") is not None))):
            raise RecoveryBlocked("全机许可账本尚未释放")
        with self._connect() as conn:
            permit = conn.execute("SELECT state, generation, request_id, token_hash FROM device_permit WHERE id=1").fetchone()
            request = conn.execute(
                """SELECT workspace_id, owner_type, owner_id, input_digest, unit_key,
                   attempt_number, last_grant_generation, state
                   FROM device_work_requests WHERE id=?""", (grant.request_id,)
            ).fetchone()
        if (permit is None or permit["generation"] < grant.generation or
                (permit["generation"] == grant.generation and
                 (permit["state"] != "free" or permit["request_id"] is not None
                  or permit["token_hash"] is not None))):
            raise RecoveryBlocked("业务库许可尚未释放")
        if (request is None or
                (request["workspace_id"], request["owner_type"], request["owner_id"],
                 request["input_digest"], request["unit_key"], request["attempt_number"],
                 request["last_grant_generation"]) !=
                (grant.workspace_id, grant.owner_type, grant.owner_id, grant.input_digest,
                 grant.unit_key, grant.attempt_number, grant.generation) or
                request["state"] not in {"done", "cancelled", "failed", "blocked"}):
            raise RecoveryBlocked("执行单元尚未提交完成、失败或安全等待状态")

    def register_child(self, grant: Grant, child: ProcessIdentity) -> None:
        """A future process wrapper must register and receive ACK before exec."""
        observation = self._observe_grant(grant)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            data = self._assert_grant(conn, grant)
            self._require_current(self._observed_current(conn, observation), grant)
            if child.pid <= 0 or not child.start_token or not child.boot_id:
                raise ValueError("子进程身份不完整")
            children = data.get("children", [])
            if _identity_json(child) not in children:
                children.append(_identity_json(child))
            data["children"] = children
            self._write_ledger(data)
            conn.execute("UPDATE device_permit SET children_json=?, heartbeat_at_utc=? WHERE id=1", (json.dumps(children), _stamp(self._now())))
            conn.commit()

    def request_yield(self, grant: Grant, reason: Literal["resource_pressure", "recording_active", "window_closed", "user_cancelled"]) -> None:
        if reason not in {"resource_pressure", "recording_active", "window_closed", "user_cancelled"}:
            raise ValueError("让出原因无效")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_grant(conn, grant)
            conn.execute("UPDATE device_work_requests SET state='yield_requested', wait_reason=?, updated_at=? WHERE id=?", (reason, _stamp(self._now()), grant.request_id))
            conn.commit()

    def _released_for_commit(self, conn: sqlite3.Connection, grant: Grant) -> dict[str, object]:
        """Check fencing and all descendants while holding the permit transaction."""
        data = self._assert_grant(conn, grant)
        try:
            children = tuple(_identity_from(item) for item in data.get("children", []))
        except (TypeError, ValueError, KeyError) as exc:
            raise RecoveryBlocked("已登记子进程身份不可核查") from exc
        if self.processes.children_released(children) is not True:
            raise RecoveryBlocked("已登记子进程仍运行或状态未知")
        if self.resources.released(grant) is not True:
            raise RecoveryBlocked("子进程或常驻资源未确认释放")
        return data

    @staticmethod
    def _record_event(conn: sqlite3.Connection, row: sqlite3.Row, event: str, reason: str,
                      actor_id: str | None, now: str) -> None:
        conn.execute(
            """INSERT INTO device_request_events
            (request_id, attempt_number, permit_generation, event_type, reason,
             actor_id, prior_state, prior_checkpoint_ref, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["id"], row["attempt_number"], row["last_grant_generation"], event,
             reason, actor_id, row["state"], row["checkpoint_ref"], now),
        )

    def _finish_host_release(self, data: dict[str, object]) -> None:
        data["state"] = "free"
        for field in ("holder", "children", "request_id", "token_hash"):
            data.pop(field, None)
        self._write_ledger(data)
        fd, self._held_fd = self._held_fd, None
        self._unlock(fd)

    def complete_unit(
        self,
        grant: Grant,
        *,
        checkpoint_database_path: str | Path,
        checkpoint_write: CheckpointWrite,
        next_intent: SubmissionIntent | None = None,
        next_unit_write: Callable[[sqlite3.Connection, Grant], tuple[SubmissionIntent, VerifiedWork] | None] | None = None,
        actor: object | None = None,
        cancelled: bool = False,
    ) -> str | None:
        """Atomically commit owner checkpoint, request result and local permit.

        The callback receives the exact Production SQLite connection used for
        the permit. A different business DB is rejected, not emulated as atomic.
        The host ledger is marked free only after this transaction commits.
        """
        if Path(checkpoint_database_path).expanduser().resolve() != self.database_path:
            raise ValueError("业务检查点与设备许可必须在同一 Production SQLite 库")
        if next_intent is not None and next_unit_write is not None:
            raise ValueError("下一单元只能由一个事务入口提供")
        continuation = self.owners.verify_submission(next_intent, actor) if next_intent else None
        if continuation is not None:
            self._assert_material_stage_key(continuation)
        observation = self._observe_grant(grant) if not cancelled else None
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            data = self._released_for_commit(conn, grant)
            if not cancelled:
                self._require_current(self._observed_current(conn, observation), grant)
            request_row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            if request_row is None:
                raise KeyError(grant.request_id)
            checkpoint_ref = checkpoint_write(conn, grant)
            if next_unit_write is not None and not cancelled:
                prepared_next = next_unit_write(conn, grant)
                if prepared_next is not None:
                    next_intent, continuation = prepared_next
                    self._assert_material_stage_key(continuation)
            if next_intent is not None:
                expected = (request_row["workspace_id"], request_row["owner_type"], request_row["owner_id"], request_row["input_digest"])
                actual = (continuation.workspace_id, continuation.owner_type, continuation.owner_id, continuation.input_digest)
                old_refs = tuple(SourceRef(**ref) for ref in json.loads(request_row["source_refs_json"]))
                if (actual != expected or continuation.unit_key != next_intent.unit_key
                        or continuation.member_id != request_row["member_id"]
                        or continuation.source_refs != old_refs):
                    raise PermissionError("下一单元的执行对象或成员归属已变化")
                if (continuation.unit_key != next_intent.unit_key
                        or continuation.work_class != request_row['work_class']
                        or (next_intent.workspace_id, next_intent.owner_type, next_intent.owner_id, next_intent.input_digest)
                        != actual):
                    raise PermissionError('下一单元的请求与可信契约不一致')
            conn.execute("UPDATE device_work_requests SET state=?, checkpoint_ref=?, wait_reason=NULL, updated_at=? WHERE id=?", ("cancelled" if cancelled else "done", checkpoint_ref, _stamp(now), grant.request_id))
            if continuation is not None and not cancelled:
                conn.execute(
                    """INSERT OR IGNORE INTO device_work_requests
                    (id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key, source_refs_json,
                     work_class, policy, not_before_utc, window_json, first_ready_at_utc,
                     state, resources_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?)""",
                    (uuid.uuid4().hex, continuation.workspace_id, continuation.member_id,
                     continuation.owner_type, continuation.owner_id, continuation.input_digest,
                     continuation.unit_key, json.dumps([vars(ref) for ref in continuation.source_refs], sort_keys=True),
                     continuation.work_class, next_intent.policy.mode,
                     _stamp(next_intent.policy.not_before_utc) if next_intent.policy.not_before_utc else None,
                     json.dumps(vars(next_intent.policy.window) if next_intent.policy.window else None),
                     request_row["first_ready_at_utc"], json.dumps(continuation.resources, sort_keys=True),
                     _stamp(now), _stamp(now)),
                )
            conn.execute("UPDATE device_permit SET state='free', request_id=NULL, token_hash=NULL, holder_json=NULL, children_json='[]', granted_at_utc=NULL, heartbeat_at_utc=NULL WHERE id=1")
            conn.commit()
        self._finish_host_release(data)
        return checkpoint_ref

    def defer_unit(self, grant: Grant, *, reason: DeferReason,
                   retry_not_before_utc: datetime | None = None,
                   owner_write: FailureWrite | None = None,
                   owner_database_path: str | Path | None = None) -> None:
        """Return a released unit to the queue without claiming work completed."""
        if owner_write is not None:
            if owner_database_path is None or Path(owner_database_path).expanduser().resolve() != self.database_path:
                raise ValueError("业务等待回写与设备许可必须在同一 Production SQLite 库")
        elif owner_database_path is not None:
            raise ValueError("业务等待回写缺少事务回调")
        allowed: set[str] = {"dependency_unavailable", "input_stale", "authorization_lost",
                             "resource_pressure", "window_closed", "device_active",
                             "worker_interrupted", "transient_failure"}
        if reason not in allowed:
            raise ValueError("等待原因无效")
        now = self._now()
        retry_at = _utc(retry_not_before_utc) if retry_not_before_utc else None
        if reason in {"resource_pressure", "device_active", "worker_interrupted", "transient_failure"}:
            if retry_at is None or retry_at <= now:
                raise ValueError("该等待原因必须指定未来的最早重试时间")
        observation = self._observe_grant(grant)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            data = self._released_for_commit(conn, grant)
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            request = self._request(row)
            verdict = self._observed_current(conn, observation)
            if ((reason == "authorization_lost" and verdict != "unauthorized")
                    or (reason == "input_stale" and verdict != "stale")
                    or (reason == "dependency_unavailable" and verdict != "dependency")
                    or (reason == "window_closed" and
                        (request.policy.window is None or request.policy.window.contains(now)))):
                raise ValueError("等待原因与当前业务状态不符")
            if owner_write is not None:
                owner_write(conn, grant)
            stamp = _stamp(now)
            self._record_event(conn, row, "deferred", reason, None, stamp)
            conn.execute(
                """UPDATE device_work_requests SET state='blocked', wait_reason=?,
                retry_not_before_utc=?, updated_at=? WHERE id=?""",
                (reason, _stamp(retry_at) if retry_at else None, stamp, grant.request_id),
            )
            conn.execute("UPDATE device_permit SET state='free', request_id=NULL, token_hash=NULL, holder_json=NULL, children_json='[]', granted_at_utc=NULL, heartbeat_at_utc=NULL WHERE id=1")
            conn.commit()
        self._finish_host_release(data)

    def record_owner_stop(self, grant: Grant, *, owner_database_path: str | Path,
                          owner_write: FailureWrite) -> None:
        """Fence a stopped owner's receipt without asserting or changing release."""
        if Path(owner_database_path).expanduser().resolve() != self.database_path:
            raise ValueError("停止回执与设备许可必须在同一 Production SQLite 库")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?",
                               (grant.request_id,)).fetchone()
            if (row is not None and row["last_grant_generation"] == grant.generation
                    and (row["workspace_id"], row["owner_type"], row["owner_id"], row["unit_key"], row["input_digest"], row["attempt_number"])
                    == (grant.workspace_id, grant.owner_type, grant.owner_id, grant.unit_key, grant.input_digest, grant.attempt_number)
                    and row["state"] not in {"granted", "yield_requested"}):
                return  # SQLite already committed an outcome before host-ledger finalization failed.
            self._assert_grant(conn, grant)
            owner_write(conn, grant)
            conn.commit()

    def fail_unit(self, grant: Grant, *, reason: str, failure_write: FailureWrite) -> None:
        """Commit an owner failure and terminal scheduler state in one transaction."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
            raise ValueError("失败原因必须是可公开的短代码")
        now = _stamp(self._now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            data = self._released_for_commit(conn, grant)
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            failure_write(conn, grant)
            self._record_event(conn, row, "failed", reason, None, now)
            conn.execute(
                """UPDATE device_work_requests SET state='failed', wait_reason=?,
                last_failure_reason=?, checkpoint_ref=NULL, retry_not_before_utc=NULL,
                updated_at=? WHERE id=?""",
                (reason, reason, now, grant.request_id),
            )
            conn.execute("UPDATE device_permit SET state='free', request_id=NULL, token_hash=NULL, holder_json=NULL, children_json='[]', granted_at_utc=NULL, heartbeat_at_utc=NULL WHERE id=1")
            conn.commit()
        self._finish_host_release(data)

    def continue_failed_unit(
        self, failed_request_id: str, replacement_intent: SubmissionIntent, actor: object, *,
        expected_failed_generation: int, expected_free_generation: int,
        owner_database_path: str | Path,
        owner_write: Callable[[sqlite3.Connection, WorkRequest, str], VerifiedWork],
        write_guard: Callable[[sqlite3.Connection], None] | None = None,
    ) -> WorkRequest:
        """Create an owner-verified attempt without rewriting its failed predecessor.

        The owner callback owns business/source checks and its recovery facts;
        the scheduler fences the released permit and preserves execution identity.
        Both writes use the same transaction, as with completed-unit continuation.
        """
        if Path(owner_database_path).expanduser().resolve() != self.database_path:
            raise ValueError("失败阶段接续必须使用同一 Production SQLite 库")
        observed = self._get(failed_request_id)
        if not self.owners.can_manage(observed, actor):
            raise PermissionError("不能接续其他成员的失败单元")
        if observed.state != 'failed' or observed.last_grant_generation != expected_failed_generation:
            raise RecoveryBlocked("原失败单元或许可代次已经变化")
        identity = (observed.workspace_id, observed.owner_type, observed.owner_id, observed.input_digest)
        proposed = (replacement_intent.workspace_id, replacement_intent.owner_type,
                    replacement_intent.owner_id, replacement_intent.input_digest)
        if proposed != identity or not replacement_intent.unit_key or replacement_intent.unit_key == observed.unit_key:
            raise PermissionError("接续必须保持原执行对象与输入，并使用新的单元身份")
        # A replay is a read of the previously committed successor, even if that
        # successor has since run. It must not reopen the business owner again.
        with self._connect() as reader:
            existing = reader.execute(
                """SELECT r.* FROM device_work_requests r JOIN device_request_events e
                ON e.request_id=? AND e.event_type='retried' AND e.reason='continued_as:' || r.id
                WHERE r.workspace_id=? AND r.owner_type=? AND r.owner_id=? AND r.input_digest=? AND r.unit_key=?""",
                (failed_request_id, *proposed, replacement_intent.unit_key),
            ).fetchone()
        if existing is not None:
            result = self._request(existing)
            if result.policy != replacement_intent.policy or result.member_id != observed.member_id:
                raise PermissionError("原接续请求的策略或成员不匹配")
            return result
        if self._held_fd is not None:
            raise RecoveryBlocked("当前协调者仍持有设备许可")
        fd, existed = self._acquire_file()
        if fd is None:
            raise RecoveryBlocked("设备仍被占用或释放状态未知")
        try:
            data = self._recover_ledger(self._read_ledger(existed))
            self._check_database_switch(data)
            now = _stamp(self._now())
            new_id = uuid.uuid4().hex
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                permit = self._reconcile_local(conn, data, now)
                row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (failed_request_id,)).fetchone()
                if row is None:
                    raise KeyError(failed_request_id)
                original = self._request(row)
                if (original != observed or original.state != 'failed'
                        or original.last_grant_generation != expected_failed_generation):
                    raise RecoveryBlocked("原失败单元或许可代次已经变化")
                if (permit['state'], permit['generation']) != ('free', expected_free_generation):
                    raise RecoveryBlocked("设备空闲许可代次已经变化")
                active = conn.execute("""SELECT 1 FROM device_work_requests
                    WHERE workspace_id=? AND owner_type=? AND owner_id=?
                    AND state IN ('waiting','blocked','granted','yield_requested') LIMIT 1""", identity[:3]).fetchone()
                if active is not None:
                    raise RecoveryBlocked("原执行对象仍有在途单元")
                if write_guard is not None:
                    write_guard(conn)
                trusted = owner_write(conn, original, new_id)
                if ((trusted.workspace_id, trusted.owner_type, trusted.owner_id, trusted.input_digest) != identity
                        or trusted.unit_key != replacement_intent.unit_key
                        or trusted.member_id != original.member_id or trusted.work_class != original.work_class
                        or trusted.source_refs != original.source_refs):
                    raise PermissionError("接续单元的可信来源、执行对象或成员已变化")
                self._assert_material_stage_key(trusted)
                conn.execute("""INSERT INTO device_work_requests
                    (id,workspace_id,member_id,owner_type,owner_id,input_digest,unit_key,source_refs_json,
                     work_class,policy,not_before_utc,window_json,first_ready_at_utc,state,resources_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting',?,?,?)""",
                    (new_id, *identity[:1], trusted.member_id, *identity[1:], trusted.unit_key,
                     json.dumps([vars(ref) for ref in trusted.source_refs], sort_keys=True), trusted.work_class,
                     replacement_intent.policy.mode,
                     _stamp(replacement_intent.policy.not_before_utc) if replacement_intent.policy.not_before_utc else None,
                     json.dumps(vars(replacement_intent.policy.window) if replacement_intent.policy.window else None),
                     row['first_ready_at_utc'], json.dumps(trusted.resources, sort_keys=True), now, now))
                self._record_event(conn, row, 'retried', 'continued_as:' + new_id, trusted.member_id, now)
                created = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (new_id,)).fetchone()
                conn.commit()
            return self._request(created)
        finally:
            self._unlock(fd)

    def retry_existing(self, request_id: str, actor: object, *, reason: RetryReason, write_guard=None) -> dict[str, object]:
        """Explicitly reopen a terminal request after trusted business validation."""
        if reason not in {"failed", "cancelled", "result_invalid"}:
            raise ValueError("重试原因无效")
        with self._connect() as reader:
            reader.execute("BEGIN")
            row = reader.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            expected_state = "done" if reason == "result_invalid" else reason
            if row["state"] != expected_state:
                raise ValueError("只有匹配的终态请求可以显式重试")
            request = self._request(row)
            local = self.owners.capture_local(request, reader)
        # Identity/source observations can wait. Release the local snapshot so
        # an unrelated edit can commit while the retry is being verified.
        with self._connect() as observer:
            retry = self.owners.verify_retry(request, actor, reason, observer)
            if not retry.actor_id:
                raise PermissionError("重试身份未通过服务端验证")
            observation = request, local, self.owners.check_current(request, observer)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            expected_state = "done" if reason == "result_invalid" else reason
            if row["state"] != expected_state:
                raise ValueError("只有匹配的终态请求可以显式重试")
            if not _material_key_has_digest(request.owner_type, request.unit_key):
                raise ValueError("旧素材请求缺少阶段输入摘要，须由发现循环创建新请求")
            if self._observed_current(conn, observation) != "ready":
                raise PermissionError("输入版本或共享授权未恢复")
            permit = conn.execute("SELECT request_id FROM device_permit WHERE id=1").fetchone()
            if permit is None or permit["request_id"] == request_id:
                raise RecoveryBlocked("请求仍有关联的设备许可")
            if retry.owner_write is not None:
                retry.owner_write(conn)
            now = _stamp(self._now())
            self._record_event(conn, row, "retried", reason, retry.actor_id, now)
            conn.execute(
                """UPDATE device_work_requests SET state='waiting', wait_reason=NULL,
                checkpoint_ref=NULL, last_failure_reason=NULL, retry_not_before_utc=NULL,
                attempt_number=attempt_number+1, updated_at=? WHERE id=?""",
                (now, request_id),
            )
            conn.commit()
        return self.view_for(request_id, actor)

    def change_policy(self, request_id: str, policy: SchedulePolicy, actor: object) -> dict[str, object]:
        observed = self._get(request_id)
        if not self.owners.can_manage(observed, actor):
            raise PermissionError("不能调整其他成员的执行安排")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            if self._request(row) != observed:
                raise PermissionError("观察期间执行安排已变化，请回读原请求")
            if row["state"] not in {"waiting", "blocked"}:
                raise ValueError("当前单元已执行或正在执行")
            conn.execute("UPDATE device_work_requests SET policy=?, not_before_utc=?, window_json=?, updated_at=? WHERE id=?",
                         (policy.mode, _stamp(policy.not_before_utc) if policy.not_before_utc else None,
                          json.dumps(vars(policy.window) if policy.window else None), _stamp(self._now()), request_id))
            conn.commit()
        return self.view_for(request_id, actor)

    def cancel(self, request_id: str, actor: object) -> dict[str, object]:
        observed = self._get(request_id)
        if not self.owners.can_manage(observed, actor):
            raise PermissionError("不能取消其他成员的执行单元")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            current = self._request(row)
            if ((current.workspace_id, current.member_id, current.owner_type, current.owner_id,
                 current.input_digest, current.unit_key, current.attempt_number) !=
                    (observed.workspace_id, observed.member_id, observed.owner_type, observed.owner_id,
                     observed.input_digest, observed.unit_key, observed.attempt_number)):
                raise PermissionError("观察期间执行单元的归属或尝试已变化")
            if row["state"] in {"waiting", "blocked"}:
                conn.execute("UPDATE device_work_requests SET state='cancelled', wait_reason='user_cancelled', updated_at=? WHERE id=?", (_stamp(self._now()), request_id))
            elif row["state"] == "granted":
                conn.execute("UPDATE device_work_requests SET state='yield_requested', wait_reason='user_cancelled', updated_at=? WHERE id=?", (_stamp(self._now()), request_id))
            cancelled = conn.execute("SELECT id,state,wait_reason FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            conn.commit()
        # Cancellation is authorized by exact ownership even after revocation.
        # Return its actual stored result without granting any queue read right.
        return dict(cancelled)

    def close(self) -> None:
        """Stop this coordinator; a held ledger remains for recovery audit."""
        if self._held_fd is not None:
            self._unlock(self._held_fd)
            self._held_fd = None
