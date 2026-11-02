from __future__ import annotations

import os
import shutil
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from app.model_install_jobs import ModelInstallError, InstallJobStore, InstallWriter, _utc_now
from app.model_install_execution import ensure_runtime, download_model, download_file, publish_model

from app.model_runtime import (
    MODEL_FILES,
    MODEL_SPECS,
    DownloadFile,
    ModelCatalog,
    ModelSpec,
)

InstallState = Literal["queued", "running", "ready", "failed", "interrupted"]

_SAFETY_FREE_BYTES = 2 * 1024**3
_RUNTIME_RESERVE_BYTES = 8 * 1024**3


class ModelInstaller:
    """Install pinned model artifacts into one explicitly configured root.

    Part files and job state live beside the models, so an interrupted download
    resumes on the same disk. In particular, `/Volumes/<name>` targets are
    rejected when that volume is not mounted; creating the same path on the
    internal disk is never used as a fallback.
    """

    def __init__(
        self,
        catalog: ModelCatalog,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self._on_complete = on_complete
        self._guard = threading.RLock()
        self._active_thread: threading.Thread | None = None
        self._jobs = InstallJobStore(catalog.model_root_path)
        self._writer: InstallWriter | None = None

    def _claim_job(self, spec: ModelSpec) -> dict[str, object]:
        if self._writer is not None:
            raise ValueError("已有模型安装任务在运行，请等待完成后再试。")
        storage = self.storage_preflight(spec)
        writer = self._jobs.claim_writer()
        if writer is None:
            raise ValueError("已有模型安装任务在运行，请等待完成后再试。")
        self._writer = writer
        try:
            job = self._new_job(spec, storage)
            self._write_job(job)
            return job
        except BaseException:
            self._writer = None
            writer.close()
            raise

    def _finish_job(self) -> None:
        with self._guard:
            writer = self._writer
            self._writer = None
            writer.close()

    def _run_owned_job(self, job_id: str, spec: ModelSpec) -> None:
        try:
            self._run_job(job_id, spec)
        finally:
            self._finish_job()

    def start(self, model_id: str, *, license_accepted: bool) -> dict[str, object]:
        spec = self._spec(model_id)
        if not license_accepted:
            raise ValueError(f"安装前必须确认 {spec.license_name} 许可证。")
        with self._guard:
            job = self._claim_job(spec)
            thread = threading.Thread(target=self._run_owned_job, args=(str(job["id"]), spec),
                daemon=True, name=f"model-install-{spec.id}")
            self._active_thread = thread
            try:
                thread.start()
            except RuntimeError:
                try:
                    self._update_job(str(job["id"]), status="failed", phase="failed", message="模型安装线程未能启动。")
                finally:
                    writer = self._writer
                    self._writer = None
                    writer.close()
                raise
        return job

    def run_sync(self, model_id: str, *, license_accepted: bool) -> dict[str, object]:
        """Synchronous entry point used by tests and maintenance commands."""
        spec = self._spec(model_id)
        if not license_accepted:
            raise ValueError(f"安装前必须确认 {spec.license_name} 许可证。")
        with self._guard:
            job = self._claim_job(spec)
            jobs = self._jobs
        self._run_owned_job(str(job["id"]), spec)
        return jobs.get(str(job["id"]))

    def get_job(self, job_id: str) -> dict[str, object]:
        return self._jobs.get(job_id)

    def recover_interrupted_jobs(self) -> list[str]:
        """Explicit startup recovery; a live writer prevents any recovery write."""
        with self._guard:
            if self._writer is not None or not self.jobs_path.is_dir():
                return []
            writer = self._jobs.claim_writer()
            if writer is None:
                return []
            try:
                return self._jobs.recover_interrupted(writer)
            finally:
                writer.close()

    def rebind_catalog(self, catalog: ModelCatalog) -> None:
        """Keep the active install's path snapshot; do not recover on reload."""
        with self._guard:
            self._assert_catalog_rebindable(catalog)
            if self._writer is not None:
                return
            self.catalog = catalog
            self._jobs = InstallJobStore(catalog.model_root_path)

    def _assert_catalog_rebindable(self, catalog: ModelCatalog) -> None:
        if self._writer is not None and _catalog_paths(catalog) != _catalog_paths(self.catalog):
            raise ModelInstallError("已有模型安装任务在运行，当前模型目录和运行环境保持原绑定；完成后再修改。")

    def assert_catalog_rebindable(self, catalog: ModelCatalog) -> None:
        """Read-only prospective-input check; no writer, recovery or file I/O."""
        with self._guard:
            self._assert_catalog_rebindable(catalog)

    @contextmanager
    def guard_catalog_update(self, catalog: ModelCatalog):
        """Hold admission exclusion across the caller's env write and rebind.

        Build the prospective catalog first. Inside this scope persist accepted
        config then call rebind_catalog(catalog); do not defer that binding to a
        background task after the guard has been released.
        """
        with self._guard:
            self._assert_catalog_rebindable(catalog)
            yield

    def storage_preflight(self, spec: ModelSpec) -> dict[str, object]:
        root = self.catalog.model_root_path
        if not root.is_absolute():
            raise ModelInstallError("模型目录必须是绝对路径。")
        external_volume = _external_volume(root)
        if external_volume is not None and not os.path.ismount(external_volume):
            raise ModelInstallError(
                f"移动硬盘未挂载：{external_volume}。已停止安装，不会回退到内置硬盘。"
            )
        existing_parent = _nearest_existing_parent(root)
        usage = shutil.disk_usage(existing_parent)
        staging = self.staging_path / spec.id
        downloaded = sum(
            min((staging / f"{item.path}.part").stat().st_size, item.size)
            for item in MODEL_FILES[spec.id]
            if (staging / f"{item.path}.part").is_file()
        )
        runtime_reserve = (
            0
            if self.catalog.runtime_path(spec)
            and self.catalog.runtime_path(spec).is_file()
            else _RUNTIME_RESERVE_BYTES
        )
        required = max(spec.approximate_download_bytes - downloaded, 0)
        required += runtime_reserve + _SAFETY_FREE_BYTES
        if usage.free < required:
            raise ModelInstallError(
                f"可用空间不足：需要约 {_human_bytes(required)}，"
                f"当前仅 {_human_bytes(usage.free)}。"
            )
        return {
            "model_root_path": str(root),
            "external_volume": str(external_volume) if external_volume else None,
            "free_bytes": usage.free,
            "required_bytes": required,
        }

    @property
    def jobs_path(self) -> Path:
        return self.catalog.model_root_path / ".install-jobs"

    @property
    def staging_path(self) -> Path:
        return self.catalog.model_root_path / ".staging"

    def _run_job(self, job_id: object, spec: ModelSpec) -> None:
        job_id = str(job_id)
        try:
            self._update_job(
                job_id,
                status="running",
                phase="runtime",
                message="正在准备外置模型运行环境…",
            )
            self._ensure_runtime(spec)
            self._update_job(
                job_id,
                phase="download",
                message=f"正在下载 {spec.display_name}…",
            )
            manifest_files = self._download_model(job_id, spec)
            self._publish_model(spec, manifest_files)
            self._update_job(
                job_id,
                status="ready",
                phase="complete",
                bytes_downloaded=spec.approximate_download_bytes,
                progress=1.0,
                message=f"{spec.display_name} 已安装并校验完成。",
            )
            if self._on_complete is not None:
                try:
                    self._on_complete()
                except Exception as exc:  # noqa: BLE001 - callback failures stay visible
                    self._update_job(
                        job_id,
                        message=(
                            f"{spec.display_name} 已安装完成；"
                            f"运行时重载失败，重启应用后生效：{exc}"
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 - job failures stay visible in the UI
            self._update_job(
                job_id,
                status="failed",
                phase="failed",
                message=str(exc),
            )

    def _ensure_runtime(self, spec: ModelSpec) -> None:
        ensure_runtime(self.catalog, spec)

    def _download_model(self, job_id: str, spec: ModelSpec) -> list[dict[str, object]]:
        return download_model(self.staging_path, job_id, spec, self._download_file, self._update_job)

    def _download_file(self, job_id: str, spec: ModelSpec, item: DownloadFile, part_path: Path, completed_bytes: int) -> None:
        download_file(self._update_job, job_id, spec, item, part_path, completed_bytes)

    def _publish_model(self, spec: ModelSpec, manifest_files: list[dict[str, object]]) -> None:
        publish_model(self.catalog, self.staging_path, spec, manifest_files)

    def _new_job(
        self, spec: ModelSpec, storage: dict[str, object]
    ) -> dict[str, object]:
        now = _utc_now()
        return {
            "id": uuid.uuid4().hex,
            "model_id": spec.id,
            "display_name": spec.display_name,
            "status": "queued",
            "phase": "preflight",
            "message": "容量预检通过，等待安装。",
            "bytes_downloaded": 0,
            "bytes_total": spec.approximate_download_bytes,
            "progress": 0.0,
            "license_name": spec.license_name,
            "license_url": spec.license_url,
            "license_accepted_at": now,
            "created_at": now,
            "updated_at": now,
            **storage,
        }

    def _update_job(self, job_id: str, **updates: object) -> dict[str, object]:
        return self._jobs.update(job_id, self._writer, **updates)

    def _write_job(self, job: dict[str, object]) -> None:
        self._jobs.write(job, self._writer)

    @staticmethod
    def _spec(model_id: str) -> ModelSpec:
        for spec in MODEL_SPECS:
            if spec.id == model_id:
                return spec
        raise ValueError(f"不支持的本地模型：{model_id}")


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    if not current.exists():
        raise ModelInstallError(f"找不到可用的存储挂载点：{path}")
    return current


def _external_volume(path: Path) -> Path | None:
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return Path("/Volumes") / parts[2]
    return None


def _human_bytes(value: int) -> str:
    return f"{value / 1024**3:.1f} GiB"




def _catalog_paths(catalog: ModelCatalog):
    return (catalog.model_root_path, catalog.runtime_python_path, catalog.visual_worker_path, catalog.voice_worker_path,
        catalog.understanding_runtime_python_path, catalog.understanding_worker_path)
