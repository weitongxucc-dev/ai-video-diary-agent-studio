"""Conservative, single-host admission for existing production work.

This module is deliberately not wired to routes or media engines yet. Callers
must provide trusted owner, resource, process and activity sources. A grant is
permission for one bounded local unit, never for an entire CreationTask.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo


WorkClass = Literal["preview_dependency", "normal_make", "background"]
PolicyMode = Literal["asap", "idle", "scheduled"]
Verdict = Literal["ready", "dependency", "stale", "unknown"]
RecoveryState = Literal["released", "active", "unknown"]


class RecoveryBlocked(RuntimeError):
    """The host may still be occupied; no further heavy unit is admitted."""


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


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_token: str
    boot_id: str


@dataclass(frozen=True)
class Grant:
    request_id: str
    workspace_id: str
    unit_key: str
    input_digest: str
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

    check_current must recheck input digest, owner access and every referenced
    source's current sharing right. It receives the permit's SQLite connection
    so same-database revocation and checkpoint use one transaction. For an
    external authorization store, unknown or unavailable must return unknown;
    this module cannot promise a cross-database atomic revocation boundary.
    No client-supplied role, class or refs are authoritative; historical work
    with unknown creator is rejected.
    """

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork: ...
    def check_current(self, request: WorkRequest, conn: sqlite3.Connection) -> Verdict: ...
    def can_manage(self, request: WorkRequest, actor: object) -> bool: ...
    def can_wake(self, request: WorkRequest, actor: object) -> bool: ...
    def verify_session(self, heartbeat: SessionHeartbeat, actor: object) -> VerifiedSession: ...
    def can_view_detail(self, request: WorkRequest, actor: object) -> bool: ...
    def can_view_device_summary(self, actor: object) -> bool: ...


class ResourceProbe(Protocol):
    def admit(self, request: WorkRequest) -> bool | None: ...
    def continue_unit(self, grant: Grant) -> bool | None: ...
    def released(self, grant: Grant) -> bool | None: ...


class ProcessProbe(Protocol):
    def current(self) -> ProcessIdentity: ...
    def children_released(self, children: tuple[ProcessIdentity, ...]) -> bool | None: ...
    def recovery_state(self, holder: ProcessIdentity, children: tuple[ProcessIdentity, ...]) -> RecoveryState: ...


CheckpointWrite = Callable[[sqlite3.Connection, Grant], str | None]


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


class DeviceScheduler:
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
        self._held_fd: int | None = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> datetime:
        return _utc(self.clock())

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
            if request.state in {"done", "cancelled"}:
                raise ValueError("该素材单元已经结束，须核验新的源版本")
            conn.execute("UPDATE device_work_requests SET updated_at=? WHERE id=?", (_stamp(self._now()), request.id))
            conn.commit()
        return self.view_for(request.id, actor)

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
            }
        if self.owners.can_view_device_summary(actor):
            return {"state": request.state, "wait_reason": request.wait_reason,
                    "device_busy": request.state in {"granted", "yield_requested"}}
        raise PermissionError("无权查看设备队列")

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
            previous = conn.execute("SELECT last_interaction_at_utc FROM device_session_activity WHERE session_id=?", (trusted.session_id,)).fetchone()
            last_interaction = now if heartbeat.interaction or previous is None else previous[0]
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
                pending = old_conn.execute(
                    """SELECT 1 FROM device_work_requests
                    WHERE state IN ('waiting', 'blocked', 'granted', 'yield_requested') LIMIT 1"""
                ).fetchone()
                old_permit = old_conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()
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

    def _eligible(self, conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> bool:
        request = self._request(row)
        owner_state = self.owners.check_current(request, conn)
        if owner_state == "stale":
            self._update_wait(conn, row, now, "input_stale")
            return False
        if owner_state != "ready":
            self._update_wait(conn, row, now, "dependency" if owner_state == "dependency" else "owner_unknown")
            return False
        if request.policy.not_before_utc and now < request.policy.not_before_utc:
            self._update_wait(conn, row, now, "scheduled_time")
            return False
        if request.policy.window and not request.policy.window.contains(now):
            self._update_wait(conn, row, now, "outside_window")
            return False
        if request.policy.mode == "idle":
            idle = self._idle(conn, now)
            if idle is not True:
                self._update_wait(conn, row, now, "activity_unknown" if idle is None else "device_active")
                return False
        available = self.resources.admit(request)
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
                rows = conn.execute("SELECT * FROM device_work_requests WHERE state IN ('waiting', 'blocked') ORDER BY first_ready_at_utc, id").fetchall()
                ready_ids = [row["id"] for row in rows if self._eligible(conn, row, now)]
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
                identity = self.processes.current()
                if identity.pid <= 0 or not identity.start_token or not identity.boot_id:
                    raise RecoveryBlocked("当前执行进程身份不可核查")
                token = uuid.uuid4().hex
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                generation = int(data["generation"]) + 1
                grant = Grant(chosen["id"], chosen["workspace_id"], chosen["unit_key"],
                              chosen["input_digest"], generation, token, self.database_path)
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
                    eligible_since_utc=NULL, eligible_wait_seconds=0, updated_at=? WHERE id=?""",
                    (_stamp(now), chosen["id"]),
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
        request = conn.execute("SELECT workspace_id, unit_key, input_digest, state FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
        if (request is None or (request["workspace_id"], request["unit_key"], request["input_digest"])
                != (grant.workspace_id, grant.unit_key, grant.input_digest)
                or request["state"] not in {"granted", "yield_requested"}):
            raise PermissionError("设备许可与执行单元不匹配")
        return data

    def _assert_current(self, conn: sqlite3.Connection, grant: Grant) -> None:
        row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
        if row is None or self.owners.check_current(self._request(row), conn) != "ready":
            raise PermissionError("输入版本或共享授权已失效")

    def assert_grant(self, grant: Grant) -> None:
        """Call immediately before a model/FFmpeg unit; nested calls reuse grant."""
        with self._connect() as conn:
            self._assert_grant(conn, grant)
            self._assert_current(conn, grant)
            if self.resources.continue_unit(grant) is not True:
                raise RecoveryBlocked("运行中资源未知或不足，须在安全边界让出")

    def register_child(self, grant: Grant, child: ProcessIdentity) -> None:
        """A future process wrapper must register and receive ACK before exec."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            data = self._assert_grant(conn, grant)
            self._assert_current(conn, grant)
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

    def complete_unit(
        self,
        grant: Grant,
        *,
        checkpoint_database_path: str | Path,
        checkpoint_write: CheckpointWrite,
        next_intent: SubmissionIntent | None = None,
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
        continuation = self.owners.verify_submission(next_intent, actor) if next_intent else None
        with self._connect() as conn:
            self._assert_grant(conn, grant)
        live_ledger = self._read_ledger(True)
        children = tuple(_identity_from(item) for item in live_ledger.get("children", []))
        if self.processes.children_released(children) is not True:
            raise RecoveryBlocked("已登记子进程仍运行或状态未知")
        if self.resources.released(grant) is not True:
            raise RecoveryBlocked("子进程或常驻资源未确认释放")
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            data = self._assert_grant(conn, grant)
            if not cancelled:
                self._assert_current(conn, grant)
            request_row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            if request_row is None:
                raise KeyError(grant.request_id)
            if next_intent is not None:
                expected = (request_row["workspace_id"], request_row["owner_type"], request_row["owner_id"], request_row["input_digest"])
                actual = (continuation.workspace_id, continuation.owner_type, continuation.owner_id, continuation.input_digest)
                old_refs = tuple(SourceRef(**ref) for ref in json.loads(request_row["source_refs_json"]))
                if (actual != expected or continuation.unit_key != next_intent.unit_key
                        or continuation.member_id != request_row["member_id"]
                        or continuation.source_refs != old_refs):
                    raise PermissionError("下一单元的执行对象或成员归属已变化")
            checkpoint_ref = checkpoint_write(conn, grant)
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
        data["state"] = "free"
        for field in ("holder", "children", "request_id", "token_hash"):
            data.pop(field, None)
        self._write_ledger(data)
        fd, self._held_fd = self._held_fd, None
        self._unlock(fd)
        return checkpoint_ref

    def change_policy(self, request_id: str, policy: SchedulePolicy, actor: object) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            if not self.owners.can_manage(self._request(row), actor):
                raise PermissionError("不能调整其他成员的执行安排")
            if row["state"] not in {"waiting", "blocked"}:
                raise ValueError("当前单元已执行或正在执行")
            conn.execute("UPDATE device_work_requests SET policy=?, not_before_utc=?, window_json=?, updated_at=? WHERE id=?",
                         (policy.mode, _stamp(policy.not_before_utc) if policy.not_before_utc else None,
                          json.dumps(vars(policy.window) if policy.window else None), _stamp(self._now()), request_id))
            conn.commit()
        return self.view_for(request_id, actor)

    def cancel(self, request_id: str, actor: object) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            if not self.owners.can_manage(self._request(row), actor):
                raise PermissionError("不能取消其他成员的执行单元")
            if row["state"] in {"waiting", "blocked"}:
                conn.execute("UPDATE device_work_requests SET state='cancelled', wait_reason='user_cancelled', updated_at=? WHERE id=?", (_stamp(self._now()), request_id))
            elif row["state"] == "granted":
                conn.execute("UPDATE device_work_requests SET state='yield_requested', wait_reason='user_cancelled', updated_at=? WHERE id=?", (_stamp(self._now()), request_id))
            conn.commit()
        return self.view_for(request_id, actor)

    def close(self) -> None:
        """Stop this coordinator; a held ledger remains for recovery audit."""
        if self._held_fd is not None:
            self._unlock(self._held_fd)
            self._held_fd = None
