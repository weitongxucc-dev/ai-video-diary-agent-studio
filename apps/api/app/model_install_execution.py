from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote
from app.model_runtime import (CHINESE_CLIP_VIT_B16, QWEN3_TTS_06B_BASE_8BIT, QWEN3_VL_4B_4BIT,
    MODEL_FILES, DownloadFile, ModelCatalog, ModelSpec)
from app.model_install_jobs import ModelInstallError, _atomic_json

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


def ensure_runtime(catalog: ModelCatalog, spec: ModelSpec) -> None:
    runtime_python = catalog.runtime_path(spec)
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
        cache_root = catalog.model_root_path / ".cache"
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

def download_model(staging_path: Path, job_id: str, spec: ModelSpec, download_file, update_job) -> list[dict[str, object]]:
    staging = staging_path / spec.id
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
            download_file(job_id, spec, item, part_path, completed_bytes)
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
        update_job(
            job_id,
            bytes_downloaded=completed_bytes,
            progress=min(completed_bytes / spec.approximate_download_bytes, 1.0),
            message=f"已完成 {item.path}",
        )
    return manifest_files

def download_file(
    update_job,
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
                    update_job(
                        job_id,
                        bytes_downloaded=downloaded,
                        progress=min(
                            downloaded / spec.approximate_download_bytes, 1.0
                        ),
                        message=f"正在下载 {item.path}…",
                    )
                    last_update = now

def publish_model(catalog: ModelCatalog, staging_path: Path, spec: ModelSpec, manifest_files: list[dict[str, object]]) -> None:
    staging = staging_path / spec.id
    target = catalog.model_path(spec)
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

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
