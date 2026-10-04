"""Serial background intake for an explicitly bound local material directory."""

from __future__ import annotations

import logging
import importlib.util
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from app.device_scheduler import (
    DeviceScheduler, Grant, RecoveryBlocked, SchedulePolicy, SubmissionIntent,
    VerifiedWork, WorkRequest,
)
from app.production import (
    MaterialAsset, ProductionRepository, material_probe_unit_key,
)
from app.material_probe import MAX_PROBE_OUTPUT_BYTES, parse_ffprobe_output
from app.material_scene import (
    SCENE_MAX_SAMPLED_FRAMES, SCENE_MAX_WIDTH, SCENE_SAMPLE_FPS,
    SCENE_STDERR_LIMIT_BYTES, SCENE_STDOUT_LIMIT_BYTES, SCENE_TIMEOUT_SECONDS,
    parse_scene_showinfo, scene_command,
)

logger = logging.getLogger(__name__)
PROBE_TIMEOUT_SECONDS = 20.0
PROBE_STDERR_LIMIT_BYTES = 32 * 1024


@dataclass(frozen=True)
class MaterialHashServiceActor:
    """The server's persisted inbox binding, never a request's current login."""

    workspace_id: str
    library_path: str


class MaterialHashOwnerVerifier:
    """Validate material hash, metadata, and scene windows against their source."""

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
        candidate = None
        with self.repository._connect() as conn:
            if intent.unit_key.startswith("hash:v1:"):
                verdict = self.repository.material_hash_verdict(
                    intent.workspace_id, intent.owner_id, intent.input_digest,
                    intent.unit_key, conn,
                )
                row = conn.execute(
                    "SELECT size_bytes FROM material_ingestion_files f "
                    "JOIN material_assets a ON a.workspace_id=f.workspace_id AND a.file_path=f.file_path "
                    "WHERE a.workspace_id=? AND a.id=? AND f.provisional_revision=?",
                    (intent.workspace_id, intent.owner_id, intent.input_digest),
                ).fetchone()
            elif intent.unit_key.startswith(("probe-meta:v1:", "probe-meta:v2:")):
                verdict = self.repository.material_probe_source_verdict(
                    intent.workspace_id, intent.owner_id, intent.input_digest,
                    intent.unit_key, conn,
                )
                row = conn.execute(
                    "SELECT f.size_bytes, f.provisional_revision FROM material_ingestion_files f "
                    "JOIN material_assets a ON a.workspace_id=f.workspace_id AND a.file_path=f.file_path "
                    "WHERE a.workspace_id=? AND a.id=? AND f.attempted_digest=?",
                    (intent.workspace_id, intent.owner_id, intent.input_digest),
                ).fetchone()
                if row is not None and intent.unit_key != self.repository._preferred_material_probe_key(
                    conn, intent.workspace_id, intent.owner_id, intent.input_digest,
                    str(row["provisional_revision"]),
                ):
                    verdict = "stale"
            elif intent.unit_key.startswith("scene-window:v1:"):
                candidate = self.repository.material_scene_candidate(
                    intent.workspace_id, intent.owner_id, intent.input_digest,
                    intent.unit_key, conn,
                )
                verdict = "ready" if candidate is not None else "stale"
                row = None
            else:
                raise PermissionError("素材阶段未注册")
        if verdict != "ready" or (row is None and candidate is None):
            raise PermissionError(f"素材阶段候选不可提交：{verdict}")
        if intent.unit_key.startswith("scene-window:v1:"):
            resources = {
                "kind": "material_scene_window", "size_bytes": candidate.size_bytes,
                "source_codec": candidate.video_codec,
                "source_width_px": candidate.video_width_px,
                "source_height_px": candidate.video_height_px,
                "source_frame_rate_fps": candidate.source_frame_rate_fps,
                "scan_seconds": candidate.window.scan_seconds,
                "output_sample_fps": SCENE_SAMPLE_FPS,
                "output_max_width_px": SCENE_MAX_WIDTH,
                "output_max_frames": SCENE_MAX_SAMPLED_FRAMES,
                "ffmpeg_threads": 1, "timeout_seconds": SCENE_TIMEOUT_SECONDS,
            }
        else:
            resources = {
                "kind": "material_hash" if intent.unit_key.startswith("hash:v1:")
                else "material_probe_meta", "size_bytes": int(row["size_bytes"]),
            }
        return VerifiedWork(
            workspace_id=intent.workspace_id, owner_type="material_asset",
            owner_id=intent.owner_id, input_digest=intent.input_digest,
            unit_key=intent.unit_key, member_id=None, work_class="background",
            resources=resources,
            stage_input_digest=intent.unit_key.rsplit(":", 1)[-1],
        )

    def check_current(self, request: WorkRequest, conn):
        if (request.owner_type != "material_asset" or request.member_id is not None
                or request.work_class != "background" or request.source_refs):
            return "unauthorized"
        if request.unit_key.startswith("hash:v1:"):
            return self.repository.material_hash_verdict(
                request.workspace_id, request.owner_id, request.input_digest,
                request.unit_key, conn,
            )
        if request.unit_key.startswith(("probe-meta:v1:", "probe-meta:v2:")):
            verdict = self.repository.material_probe_source_verdict(
                request.workspace_id, request.owner_id, request.input_digest,
                request.unit_key, conn,
            )
            if verdict == "ready" and (
                shutil.which("ffprobe") is None
                or importlib.util.find_spec("app.granted_subprocess") is None
            ):
                return "dependency"
            if verdict == "ready":
                row = conn.execute(
                    "SELECT f.provisional_revision FROM material_ingestion_files f "
                    "JOIN material_assets a ON a.workspace_id=f.workspace_id "
                    "AND a.file_path=f.file_path WHERE a.workspace_id=? AND a.id=?",
                    (request.workspace_id, request.owner_id),
                ).fetchone()
                if row is None or request.unit_key != self.repository._preferred_material_probe_key(
                    conn, request.workspace_id, request.owner_id, request.input_digest,
                    str(row["provisional_revision"]),
                ):
                    return "stale"
            return verdict
        if request.unit_key.startswith("scene-window:v1:"):
            source_verdict = self.repository.material_probe_source_verdict(
                request.workspace_id, request.owner_id, request.input_digest,
                material_probe_unit_key(request.input_digest), conn,
            )
            if source_verdict != "ready":
                return source_verdict
            candidate = self.repository.material_scene_candidate(
                request.workspace_id, request.owner_id, request.input_digest,
                request.unit_key, conn,
            )
            if candidate is None:
                return "stale"
            if shutil.which("ffmpeg") is None or candidate.source_frame_rate_fps is None:
                return "dependency"
            return "ready"
        return "stale"

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
        if scheduler is not None and Path(repository.database_path).resolve() != scheduler.database_path:
            raise ValueError("素材库与设备协调器必须使用同一业务库")
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

    def confirm_copy_complete(
        self,
        workspace_id: str,
        *,
        relative_path: str,
        expected_revision: dict[str, str],
        confirmation_source: Literal["manual", "app_import"],
    ) -> None:
        """Wake intake only after the repository persists this exact file revision.

        The repository checks the directory binding and current stat revision.
        A manual declaration confirms copy completion, not media validity.
        """
        self.repository.confirm_material_copy_complete(
            workspace_id,
            relative_path=relative_path,
            expected_revision=expected_revision,
            confirmation_source=confirmation_source,
        )
        self.wake()

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
            self.submit_pending_probe_dependencies()
            self.submit_pending_scene_windows()
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

    def submit_pending_probe_dependencies(self) -> list[str]:
        """Replay committed hashes into idempotent, blocked probe requests."""
        if self.execution_mode != "scheduled" or self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        binding = self.repository.get_material_ingestion_binding()
        if binding is None:
            return []
        actor = MaterialHashServiceActor(*binding)
        request_ids: list[str] = []
        for dependency in self.repository.pending_material_probe_dependencies():
            if dependency.workspace_id != binding[0]:
                raise PermissionError("素材哈希检查点归属与绑定不一致")
            unit_key = self.repository.material_probe_key_for_dependency(dependency)
            intent = SubmissionIntent(
                workspace_id=dependency.workspace_id, owner_type="material_asset",
                owner_id=dependency.asset_id, input_digest=dependency.source_sha256,
                unit_key=unit_key,
                policy=SchedulePolicy(),
            )
            legacy_id = (self.repository.legacy_probe_request_for_replacement(dependency)
                         if unit_key.startswith("probe-meta:v1:") else None)
            if legacy_id is not None:
                replace = getattr(self.scheduler, "replace_unexecuted_request", None)
                if replace is None:
                    raise RecoveryBlocked("整机调度器尚无保留旧请求等待年龄的替换接口")
                request = replace(
                    legacy_id, intent, actor, "bounded_ffprobe_metadata_stage",
                )
            else:
                request = self.scheduler.submit(intent, actor)
            request_ids.append(request.id)
        return request_ids

    def submit_pending_scene_windows(self) -> list[str]:
        if self.execution_mode != "scheduled" or self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        binding = self.repository.get_material_ingestion_binding()
        if binding is None:
            return []
        actor = MaterialHashServiceActor(*binding)
        request_ids = []
        for candidate in self.repository.pending_material_scene_windows():
            if candidate.workspace_id != binding[0]:
                raise PermissionError("镜头检查点归属与素材绑定不一致")
            intent = SubmissionIntent(
                workspace_id=candidate.workspace_id, owner_type="material_asset",
                owner_id=candidate.asset_id, input_digest=candidate.source_sha256,
                unit_key=candidate.unit_key, policy=SchedulePolicy(),
            )
            request_ids.append(self.scheduler.submit(intent, actor).id)
        return request_ids

    def run_granted_hash(self, grant: Grant) -> str | None:
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
            return None
        verdict = self.repository.material_hash_verdict_for_grant(grant)
        if verdict != "ready":
            reason = {"dependency": "dependency_unavailable", "stale": "input_stale",
                      "unauthorized": "authorization_lost"}.get(verdict, "transient_failure")
            retry_at = datetime.now(timezone.utc) + timedelta(minutes=1) if reason == "transient_failure" else None
            self.scheduler.defer_unit(grant, reason=reason, retry_not_before_utc=retry_at)
            return None
        self.scheduler.complete_unit(
            grant, checkpoint_database_path=self.repository.database_path,
            checkpoint_write=lambda conn, active: self.repository.commit_material_hash(conn, active, result),
        )
        try:
            self.submit_pending_probe_dependencies()
        except (OSError, ValueError, PermissionError, RecoveryBlocked):
            # The hash checkpoint is already committed; the next scan replays it.
            logger.exception("素材哈希完成，但探测请求提交失败；周期补扫将重试")
        return result.source_sha256

    def run_granted_probe(self, grant: Grant) -> str | None:
        """Run bounded ffprobe under a registered child and commit only metadata."""
        from app.granted_subprocess import (
            GrantedCommandCancelled, GrantedCommandError, GrantedCommandOutputLimit,
            GrantedCommandTimeout, run_granted_command,
        )

        if self.execution_mode != "scheduled" or self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        scheduler = self.scheduler
        try:
            scheduler.assert_grant(grant)
            candidate = self.repository.material_probe_candidate_for_grant(grant)
        except (OSError, ValueError, PermissionError):
            if self._defer_stale_probe(grant):
                return None
            raise
        try:
            ffprobe = shutil.which("ffprobe")
            if ffprobe is None:
                raise FileNotFoundError("ffprobe is unavailable")
            completed = run_granted_command(
                scheduler, grant,
                [ffprobe, "-v", "error", "-show_format", "-show_streams",
                 "-of", "json", "-i", candidate.file_path],
                timeout_seconds=PROBE_TIMEOUT_SECONDS,
                max_stdout_bytes=MAX_PROBE_OUTPUT_BYTES,
                max_stderr_bytes=PROBE_STDERR_LIMIT_BYTES,
                cancel_event=self._stop,
            )
        except RecoveryBlocked:
            raise
        except (GrantedCommandTimeout, GrantedCommandOutputLimit, GrantedCommandCancelled) as error:
            if self._defer_stale_probe(grant):
                return None
            if isinstance(error, GrantedCommandCancelled):
                scheduler.defer_unit(
                    grant, reason="worker_interrupted",
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(minutes=1),
                )
            else:
                reason = "probe_timeout" if isinstance(error, GrantedCommandTimeout) else "probe_output_limit"
                scheduler.fail_unit(
                    grant, reason=reason,
                    failure_write=lambda conn, active: self.repository.fail_material_probe(
                        conn, active, candidate, reason,
                    ),
                )
            return None
        except (OSError, ValueError, PermissionError, GrantedCommandError):
            if self._defer_stale_probe(grant):
                return None
            scheduler.fail_unit(
                grant, reason="probe_failed",
                failure_write=lambda conn, active: self.repository.fail_material_probe(
                    conn, active, candidate, "probe_failed",
                ),
            )
            return None
        if self._defer_stale_probe(grant):
            return None
        if completed.returncode != 0:
            scheduler.fail_unit(
                grant, reason="probe_failed",
                failure_write=lambda conn, active: self.repository.fail_material_probe(
                    conn, active, candidate, "probe_failed",
                ),
            )
            return None
        try:
            facts = parse_ffprobe_output(completed.stdout)
        except ValueError:
            scheduler.fail_unit(
                grant, reason="probe_invalid_media",
                failure_write=lambda conn, active: self.repository.fail_material_probe(
                    conn, active, candidate, "probe_invalid_media",
                ),
            )
            return None
        scheduler.complete_unit(
            grant, checkpoint_database_path=self.repository.database_path,
            checkpoint_write=lambda conn, active: self.repository.commit_material_probe(
                conn, active, candidate, facts,
            ),
        )
        try:
            self.submit_pending_scene_windows()
        except (OSError, ValueError, PermissionError, RecoveryBlocked):
            logger.exception("素材媒体信息已保存；镜头请求将在周期补扫时重试")
        return facts.checkpoint_json()

    def run_granted_scene(self, grant: Grant) -> str | None:
        """Analyze one bounded source-time window under the registered process permit."""
        from app.granted_subprocess import (
            GrantedCommandCancelled, GrantedCommandError, GrantedCommandOutputLimit,
            GrantedCommandResourceLimit, GrantedCommandResourceUnavailable,
            GrantedCommandTimeout, run_granted_command,
        )

        if self.execution_mode != "scheduled" or self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        scheduler = self.scheduler
        try:
            scheduler.assert_grant(grant)
            candidate = self.repository.material_scene_candidate_for_grant(grant)
        except (OSError, ValueError, PermissionError):
            if self._defer_stale_scene(grant):
                return None
            raise
        try:
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg is None:
                raise FileNotFoundError("ffmpeg is unavailable")
            completed = run_granted_command(
                scheduler, grant,
                scene_command(ffmpeg, candidate.file_path,
                              candidate.video_stream_index, candidate.window),
                timeout_seconds=SCENE_TIMEOUT_SECONDS,
                max_stdout_bytes=SCENE_STDOUT_LIMIT_BYTES,
                max_stderr_bytes=SCENE_STDERR_LIMIT_BYTES,
                cancel_event=self._stop,
            )
        except RecoveryBlocked:
            raise
        except GrantedCommandCancelled:
            if self._defer_stale_scene(grant):
                return None
            scheduler.defer_unit(
                grant, reason="worker_interrupted",
                retry_not_before_utc=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            return None
        except GrantedCommandResourceUnavailable:
            if self._defer_stale_scene(grant):
                return None
            scheduler.defer_unit(
                grant, reason="resource_pressure",
                retry_not_before_utc=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            return None
        except GrantedCommandResourceLimit:
            if self._defer_stale_scene(grant):
                return None
            scheduler.fail_unit(
                grant, reason="scene_resource_limit",
                failure_write=lambda conn, active: self.repository.fail_material_scene(
                    conn, active, candidate, "scene_resource_limit",
                ),
            )
            return None
        except (GrantedCommandTimeout, GrantedCommandOutputLimit) as error:
            if self._defer_stale_scene(grant):
                return None
            reason = "scene_timeout" if isinstance(error, GrantedCommandTimeout) else "scene_output_limit"
            scheduler.fail_unit(
                grant, reason=reason,
                failure_write=lambda conn, active: self.repository.fail_material_scene(
                    conn, active, candidate, reason,
                ),
            )
            return None
        except (OSError, ValueError, PermissionError, GrantedCommandError):
            if self._defer_stale_scene(grant):
                return None
            scheduler.fail_unit(
                grant, reason="scene_failed",
                failure_write=lambda conn, active: self.repository.fail_material_scene(
                    conn, active, candidate, "scene_failed",
                ),
            )
            return None
        if self._defer_stale_scene(grant):
            return None
        try:
            if completed.returncode != 0:
                raise ValueError("ffmpeg returned nonzero")
            facts = parse_scene_showinfo(completed.stderr, candidate.window)
        except ValueError:
            scheduler.fail_unit(
                grant, reason="scene_failed",
                failure_write=lambda conn, active: self.repository.fail_material_scene(
                    conn, active, candidate, "scene_failed",
                ),
            )
            return None
        scheduler.complete_unit(
            grant, checkpoint_database_path=self.repository.database_path,
            checkpoint_write=lambda conn, active: self.repository.commit_material_scene(
                conn, active, candidate, facts,
            ),
        )
        try:
            self.submit_pending_scene_windows()
        except (OSError, ValueError, PermissionError, RecoveryBlocked):
            logger.exception("镜头窗口已保存；剩余窗口将在周期补扫时重试")
        return facts.checkpoint_json()

    def _defer_stale_scene(self, grant: Grant) -> bool:
        if self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        verdict = self.repository.material_scene_verdict_for_grant(grant)
        if verdict == "ready" and shutil.which("ffmpeg") is None:
            verdict = "dependency"
        if verdict == "ready":
            return False
        reason = {"dependency": "dependency_unavailable", "stale": "input_stale",
                  "unauthorized": "authorization_lost"}.get(verdict, "transient_failure")
        retry_at = (datetime.now(timezone.utc) + timedelta(minutes=1)
                    if reason == "transient_failure" else None)
        self.scheduler.defer_unit(grant, reason=reason, retry_not_before_utc=retry_at)
        return True

    def _defer_stale_probe(self, grant: Grant) -> bool:
        if self.scheduler is None:
            raise RuntimeError("素材整机调度尚未启用")
        verdict = self.repository.material_probe_verdict_for_grant(grant)
        if verdict == "ready" and shutil.which("ffprobe") is None:
            verdict = "dependency"
        if verdict == "ready":
            return False
        reason = {"dependency": "dependency_unavailable", "stale": "input_stale",
                  "unauthorized": "authorization_lost"}.get(verdict, "transient_failure")
        retry_at = (datetime.now(timezone.utc) + timedelta(minutes=1)
                    if reason == "transient_failure" else None)
        self.scheduler.defer_unit(grant, reason=reason, retry_not_before_utc=retry_at)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("Material ingestion cycle failed")
            self._wake.wait(self.scan_interval_seconds)
            self._wake.clear()


class MaterialHashUnitExecutor:
    """Host runtime adapter for the bounded material intake stages."""

    def __init__(self) -> None:
        self._service: MaterialIngestionService | None = None

    def bind(self, service: MaterialIngestionService) -> None:
        """Bind once after the runtime has built its scheduler, before starting it."""
        if self._service is not None or service.execution_mode != "scheduled" or service.scheduler is None:
            raise ValueError("素材哈希执行器需要一次性绑定已调度的服务")
        self._service = service

    def execute(self, grant: Grant, scheduler: DeviceScheduler) -> None:
        if self._service is None or scheduler is not self._service.scheduler:
            raise PermissionError("素材许可不属于哈希执行器")
        if grant.unit_key.startswith("hash:v1:"):
            self._service.run_granted_hash(grant)
        elif grant.unit_key.startswith(("probe-meta:v1:", "probe-meta:v2:")):
            self._service.run_granted_probe(grant)
        elif grant.unit_key.startswith("scene-window:v1:"):
            self._service.run_granted_scene(grant)
        else:
            raise PermissionError("素材许可阶段没有已注册的执行器")
