"""Serial background intake for an explicitly bound local material directory."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.device_scheduler import (
    DeviceScheduler, Grant, RecoveryBlocked, SchedulePolicy, SubmissionIntent,
    VerifiedWork, WorkRequest,
)
from app.production import MaterialAsset, ProductionRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterialHashServiceActor:
    """The server's persisted inbox binding, never a request's current login."""

    workspace_id: str
    library_path: str


class MaterialHashOwnerVerifier:
    """Narrow material owner checks for composition into the device verifier."""

    def __init__(self, repository: ProductionRepository) -> None:
        self.repository = repository

    def verify_submission(self, intent: SubmissionIntent, actor: object) -> VerifiedWork:
        if (not isinstance(actor, MaterialHashServiceActor)
                or intent.owner_type != "material_asset"
                or actor.workspace_id != intent.workspace_id):
            raise PermissionError("素材后台归属未通过验证")
        binding = self.repository.get_material_ingestion_binding()
        if binding != (actor.workspace_id, actor.library_path):
            raise PermissionError("素材后台绑定已失效")
        with self.repository._connect() as conn:
            verdict = self.repository.material_hash_verdict(
                intent.workspace_id, intent.owner_id, intent.input_digest, intent.unit_key, conn
            )
            row = conn.execute(
                "SELECT size_bytes FROM material_ingestion_files f "
                "JOIN material_assets a ON a.workspace_id=f.workspace_id AND a.file_path=f.file_path "
                "WHERE a.workspace_id=? AND a.id=? AND f.provisional_revision=?",
                (intent.workspace_id, intent.owner_id, intent.input_digest),
            ).fetchone()
        if verdict != "ready" or row is None:
            raise PermissionError(f"素材哈希候选不可提交：{verdict}")
        return VerifiedWork(
            workspace_id=intent.workspace_id, owner_type="material_asset",
            owner_id=intent.owner_id, input_digest=intent.input_digest,
            unit_key=intent.unit_key, member_id=None, work_class="background",
            resources={"kind": "material_hash", "size_bytes": int(row["size_bytes"])},
            stage_input_digest=intent.unit_key.rsplit(":", 1)[-1],
        )

    def check_current(self, request: WorkRequest, conn):
        if (request.owner_type != "material_asset" or request.member_id is not None
                or request.work_class != "background" or request.source_refs):
            return "unauthorized"
        return self.repository.material_hash_verdict(
            request.workspace_id, request.owner_id, request.input_digest,
            request.unit_key, conn,
        )

    def can_manage(self, request: WorkRequest, actor: object) -> bool:
        return False

    def can_wake(self, request: WorkRequest, actor: object) -> bool:
        return False

    def verify_session(self, heartbeat: object, actor: object):
        raise PermissionError("素材适配器不验证设备会话")

    def can_view_detail(self, request: WorkRequest, actor: object) -> bool:
        return False

    def can_view_device_summary(self, actor: object) -> bool:
        return False

    def verify_retry(self, request: WorkRequest, actor: object, reason: str, conn):
        raise PermissionError("素材终态重试尚未接入可信授权")


class MaterialIngestionService:
    def __init__(
        self,
        repository: ProductionRepository,
        *,
        scan_interval_seconds: float = 60.0,
        stable_seconds: float = 10.0,
        execution_mode: Literal["legacy", "scheduled"] = "legacy",
        scheduler: DeviceScheduler | None = None,
    ) -> None:
        if scan_interval_seconds <= 0 or stable_seconds < 0:
            raise ValueError("素材扫描和稳定等待时间无效")
        if execution_mode not in {"legacy", "scheduled"}:
            raise ValueError("素材执行模式无效")
        if (execution_mode == "scheduled") != (scheduler is not None):
            raise ValueError("调度素材模式必须显式注入整机协调器")
        self.repository = repository
        self.scan_interval_seconds = scan_interval_seconds
        self.stable_seconds = stable_seconds
        self.execution_mode = execution_mode
        self.scheduler = scheduler
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._scan_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="material-ingestion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def wake(self) -> None:
        """Schedule the next scan after binding or retry without blocking the request."""
        self._wake.set()

    def scan_once(self) -> list[MaterialAsset]:
        """Run one bounded intake cycle; safe to call beside the background thread."""
        if self.execution_mode == "scheduled":
            self.discover_and_submit_hashes()
            return []
        analyzed: list[MaterialAsset] = []
        with self._scan_lock:
            binding = self.repository.get_material_ingestion_binding()
            if binding is None:
                return analyzed
            workspace_id, _ = binding
            for asset_id, digest in self.repository.scan_material_ingestion(stable_seconds=self.stable_seconds):
                if self._stop.is_set():
                    break
                try:
                    asset = self.repository.analyze_asset(workspace_id, asset_id, expected_source_digest=digest)
                except Exception as exc:
                    logger.exception("Material analysis failed for asset %s", asset_id)
                    self.repository.fail_material_ingestion(workspace_id, asset_id, digest, exc)
                    continue
                self.repository.finish_material_ingestion(
                    workspace_id, asset_id, digest, failed=asset.analysis_status == "failed"
                )
                analyzed.append(asset)
        return analyzed

    def discover_and_submit_hashes(self) -> list[str]:
        """Only discover and queue hash units; the host coordinator dispatches grants."""
        if self.execution_mode != "scheduled" or self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        request_ids: list[str] = []
        with self._scan_lock:
            for candidate in self.repository.discover_material_hash_candidates(
                stable_seconds=self.stable_seconds
            ):
                binding = self.repository.get_material_ingestion_binding()
                if binding is None or binding[0] != candidate.workspace_id:
                    raise PermissionError("素材绑定在发现后已变化")
                intent = SubmissionIntent(
                    workspace_id=candidate.workspace_id, owner_type="material_asset",
                    owner_id=candidate.asset_id, input_digest=candidate.provisional_revision,
                    unit_key=candidate.unit_key, policy=SchedulePolicy(),
                )
                actor = MaterialHashServiceActor(*binding)
                request_ids.append(self.scheduler.submit(intent, actor).id)
        return request_ids

    def run_granted_hash(self, grant: Grant) -> str:
        """Execute one hash grant; never choose or issue a host permit here."""
        if self.execution_mode != "scheduled" or self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        try:
            self.scheduler.assert_grant(grant)
            result = self.repository.hash_granted_material(grant, self.scheduler.assert_grant)
        except (OSError, ValueError, PermissionError, RecoveryBlocked) as error:
            verdict = self.repository.material_hash_verdict_for_grant(grant)
            reason = {
                "dependency": "dependency_unavailable",
                "stale": "input_stale",
                "unauthorized": "authorization_lost",
            }.get(verdict, "resource_pressure" if isinstance(error, RecoveryBlocked) else "transient_failure")
            retry_at = (datetime.now(timezone.utc) + timedelta(minutes=1)) if reason in {
                "resource_pressure", "transient_failure",
            } else None
            self.scheduler.defer_unit(grant, reason=reason, retry_not_before_utc=retry_at)
            raise
        verdict = self.repository.material_hash_verdict_for_grant(grant)
        if verdict != "ready":
            reason = {"dependency": "dependency_unavailable", "stale": "input_stale",
                      "unauthorized": "authorization_lost"}.get(verdict, "transient_failure")
            retry_at = datetime.now(timezone.utc) + timedelta(minutes=1) if reason == "transient_failure" else None
            self.scheduler.defer_unit(grant, reason=reason, retry_not_before_utc=retry_at)
            raise ValueError(f"素材哈希输入已失效：{verdict}")
        self.scheduler.complete_unit(
            grant, checkpoint_database_path=self.repository.database_path,
            checkpoint_write=lambda conn, active: self.repository.commit_material_hash(conn, active, result),
        )
        return result.source_sha256

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("Material ingestion cycle failed")
            self._wake.wait(self.scan_interval_seconds)
            self._wake.clear()
