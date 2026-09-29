from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from app.model_runtime import (
    CHINESE_CLIP_VIT_B16,
    MODEL_FILES,
    MODEL_SPECS,
    QWEN3_TTS_06B_BASE_8BIT,
    QWEN3_VL_4B_4BIT,
    DownloadFile,
    ModelCatalog,
    ModelSpec,
)

InstallState = Literal["queued", "running", "ready", "failed", "interrupted"]

_SAFETY_FREE_BYTES = 2 * 1024**3
_RUNTIME_RESERVE_BYTES = 8 * 1024**3
_CHUNK_BYTES = 4 * 1024**2


MODEL_RUNTIME_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    QWEN3_VL_4B_4BIT.id: ("mlx-vlm==0.7.0",),
    CHINESE_CLIP_VIT_B16.id: (
        "torch==2.13.0",
        "transformers==5.15.1",
        "pillow==12.2.0",
    ),
    QWEN3_TTS_06B_BASE_8BIT.id: ("mlx-audio==0.5.0",),
}


class ModelInstallError(RuntimeError):
    pass


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
        self._guard = threading.Lock()
        self._active_thread: threading.Thread | None = None
        self._recover_interrupted_jobs()

    def start(self, model_id: str, *, license_accepted: bool) -> dict[str, object]:
        spec = self._spec(model_id)
        if not license_accepted:
            raise ValueError(f"安装前必须确认 {spec.license_name} 许可证。")
        with self._guard:
            if self._active_thread is not None and self._active_thread.is_alive():
                raise ValueError("已有模型安装任务在运行，请等待完成后再试。")
            storage = self.storage_preflight(spec)
            job = self._new_job(spec, storage)
            self._write_job(job)
            thread = threading.Thread(
                target=self._run_job,
                args=(job["id"], spec),
                daemon=True,
                name=f"model-install-{spec.id}",
            )
            self._active_thread = thread
            thread.start()
        return job

    def run_sync(self, model_id: str, *, license_accepted: bool) -> dict[str, object]:
        """Synchronous entry point used by tests and maintenance commands."""
        spec = self._spec(model_id)
        if not license_accepted:
            raise ValueError(f"安装前必须确认 {spec.license_name} 许可证。")
        storage = self.storage_preflight(spec)
        job = self._new_job(spec, storage)
        self._write_job(job)
        self._run_job(job["id"], spec)
        return self.get_job(str(job["id"]))

    def get_job(self, job_id: str) -> dict[str, object]:
        safe_id = _safe_job_id(job_id)
        path = self.jobs_path / f"{safe_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"找不到模型安装任务：{safe_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ModelInstallError("模型安装任务记录已损坏。")
        return payload

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
        runtime_python = self.catalog.runtime_path(spec)
        if runtime_python is None:
            raise ModelInstallError("未配置模型运行环境路径。")
        runtime_root = runtime_python.parent.parent
        requirements = set(MODEL_RUNTIME_REQUIREMENTS[spec.id])
        marker_path = runtime_root / "installed-requirements.json"
        installed: set[str] = set()
        if marker_path.is_file():
            try:
                payload = json.loads(marker_path.read_text(encoding="utf-8"))
                installed = set(payload.get("requirements", []))
            except (OSError, ValueError, json.JSONDecodeError):
                installed = set()
        missing = sorted(requirements - installed)
        if runtime_python.is_file() and not missing:
            return

        runtime_root.parent.mkdir(parents=True, exist_ok=True)
        if not runtime_python.is_file():
            base_python = shutil.which("python3")
            if not base_python:
                raise ModelInstallError("未找到 Python 3，无法创建本地模型运行环境。")
            version = subprocess.run(
                [base_python, "-c", "import sys; print(sys.version_info >= (3, 10))"],
                capture_output=True,
                text=True,
                check=False,
            )
            if version.returncode != 0 or version.stdout.strip() != "True":
                raise ModelInstallError("本地模型需要 Python 3.10 或更高版本。")
            subprocess.run([base_python, "-m", "venv", str(runtime_root)], check=True)

        if missing:
            cache_root = self.catalog.model_root_path / ".cache"
            env = os.environ.copy()
            env.update(
                {
                    "PIP_CACHE_DIR": str(cache_root / "pip"),
                    "HF_HOME": str(cache_root / "huggingface"),
                    "TORCH_HOME": str(cache_root / "torch"),
                    "XDG_CACHE_HOME": str(cache_root),
                }
            )
            subprocess.run(
                [str(runtime_python), "-m", "pip", "install", *missing],
                check=True,
                env=env,
            )
            installed.update(missing)
            _atomic_json(
                marker_path,
                {"schema_version": 1, "requirements": sorted(installed)},
            )

    def _download_model(
        self, job_id: str, spec: ModelSpec
    ) -> list[dict[str, object]]:
        staging = self.staging_path / spec.id
        staging.mkdir(parents=True, exist_ok=True)
        files = MODEL_FILES[spec.id]
        completed_bytes = 0
        manifest_files: list[dict[str, object]] = []
        for item in files:
            destination = staging / item.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file() and destination.stat().st_size == item.size:
                completed_bytes += item.size
            else:
                part_path = Path(f"{destination}.part")
                self._download_file(job_id, spec, item, part_path, completed_bytes)
                if part_path.stat().st_size != item.size:
                    raise ModelInstallError(f"下载文件大小不匹配：{item.path}")
                os.replace(part_path, destination)
                completed_bytes += item.size
            manifest_files.append(
                {
                    "path": item.path,
                    "size": item.size,
                    "sha256": _sha256(destination),
                }
            )
            self._update_job(
                job_id,
                bytes_downloaded=completed_bytes,
                progress=min(completed_bytes / spec.approximate_download_bytes, 1.0),
                message=f"已完成 {item.path}",
            )
        return manifest_files

    def _download_file(
        self,
        job_id: str,
        spec: ModelSpec,
        item: DownloadFile,
        part_path: Path,
        completed_bytes: int,
    ) -> None:
        offset = part_path.stat().st_size if part_path.is_file() else 0
        if offset > item.size:
            raise ModelInstallError(f"断点文件大小异常：{item.path}")
        url = (
            f"https://huggingface.co/{spec.repository}/resolve/"
            f"{spec.revision}/{quote(item.path, safe='/')}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "AI-Video-Diary/1.0"})
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.URLError as exc:
            raise ModelInstallError(f"下载失败 {item.path}：{exc.reason}") from exc
        with response:
            append = offset > 0 and getattr(response, "status", 200) == 206
            if offset and not append:
                offset = 0
            mode = "ab" if append else "wb"
            last_update = 0.0
            with part_path.open(mode) as handle:
                while True:
                    chunk = response.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    handle.write(chunk)
                    offset += len(chunk)
                    now = time.monotonic()
                    if now - last_update >= 0.5:
                        downloaded = completed_bytes + offset
                        self._update_job(
                            job_id,
                            bytes_downloaded=downloaded,
                            progress=min(
                                downloaded / spec.approximate_download_bytes, 1.0
                            ),
                            message=f"正在下载 {item.path}…",
                        )
                        last_update = now

    def _publish_model(
        self, spec: ModelSpec, manifest_files: list[dict[str, object]]
    ) -> None:
        staging = self.staging_path / spec.id
        target = self.catalog.model_path(spec)
        if target.exists():
            raise ModelInstallError(
                f"目标模型目录已存在：{target}。为避免覆盖用户数据，已保留断点文件。"
            )
        _atomic_json(
            staging / "manifest.json",
            {
                "schema_version": 1,
                "model_id": spec.id,
                "repository": spec.repository,
                "revision": spec.revision,
                "files": manifest_files,
            },
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)

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
        job = self.get_job(job_id)
        job.update(updates)
        job["updated_at"] = _utc_now()
        self._write_job(job)
        return job

    def _write_job(self, job: dict[str, object]) -> None:
        job_id = _safe_job_id(str(job["id"]))
        _atomic_json(self.jobs_path / f"{job_id}.json", job)

    def _recover_interrupted_jobs(self) -> None:
        if not self.jobs_path.is_dir():
            return
        for path in self.jobs_path.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("status") in {"queued", "running"}:
                    job.update(
                        {
                            "status": "interrupted",
                            "phase": "interrupted",
                            "message": "上次安装被中断；重新安装会从已下载断点继续。",
                            "updated_at": _utc_now(),
                        }
                    )
                    _atomic_json(path, job)
            except (OSError, ValueError, json.JSONDecodeError):
                continue

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


def _safe_job_id(job_id: str) -> str:
    if not job_id or any(char not in "0123456789abcdef" for char in job_id.lower()):
        raise ValueError("模型安装任务 ID 无效。")
    return job_id.lower()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human_bytes(value: int) -> str:
    return f"{value / 1024**3:.1f} GiB"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
