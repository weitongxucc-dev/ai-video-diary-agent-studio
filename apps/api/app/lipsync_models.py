"""Immutable model identities for new choices and historical task replay."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from model_workers import latentsync_16_mlx as mlx

LATENTSYNC_16_MODEL_ID = "latentsync-1.6-512"
LATENTSYNC_15_MODEL_ID = "latentsync-1.5-mlx-fidelity-brow"
MUSETALK_MODEL_ID = "musetalk-v1.5-mps"
LATENTSYNC_16_CODE_REVISION = mlx.UPSTREAM_REVISION
LATENTSYNC_16_WEIGHTS_REVISION = mlx.WEIGHTS_REVISION
LATENTSYNC_16_DOWNLOADS = (
    ("latentsync_unet.pt", 5072222488, "0a478e89eb660f82da4c35dbdde8a5adfb27f99d1b4e50edd03729e1e98316d3"),
    ("whisper/tiny.pt", 75572083, "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9"),
)


def planned_lipsync_model_id(plan, default_model_id: str) -> str:
    if plan.lipsync_model_id:
        return plan.lipsync_model_id
    # Earlier plans sometimes recorded the resolved provider in their preflight
    # snapshot. Ignore the old generic "unconfigured" sentinel.
    for model in plan.model_snapshot:
        if model.get("capability") == "avatar_lipsync":
            model_id = model.get("id")
            if isinstance(model_id, str) and model_id and model_id != "avatar-lipsync-unconfigured":
                return model_id
    if plan.lipsync_quality == "high" and default_model_id == LATENTSYNC_16_MODEL_ID:
        return LATENTSYNC_15_MODEL_ID
    return default_model_id


@dataclass(frozen=True)
class LatentSync16Runtime:
    root: Path
    python: Path
    worker: Path
    backend: str = "cuda"

    @classmethod
    def from_settings(cls, settings):
        backend = "mlx" if sys.platform == "darwin" else "cuda"
        root = Path(settings.latentsync_16_root_path or (
            Path(settings.model_root_path).parent / "model-runtimes" / f"latentsync-1.6-{backend}"
        )).expanduser()
        default_python = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
        return cls(root, Path(settings.latentsync_16_runtime_python_path or root / default_python).expanduser(),
                   Path(settings.latentsync_worker_path).with_name("latentsync_16_worker.py").expanduser(), backend)

    def issue(self) -> str | None:
        if self.backend == "mlx" and (sys.platform != "darwin" or platform.machine() != "arm64"):
            return "LatentSync 1.6 MLX 需要原生 Apple Silicon Python。"
        if not self.python.is_file():
            return f"请配置 LatentSync 1.6 独立的 {self.backend.upper()} Python 环境：{self.python}"
        if not self.worker.is_file():
            return "LatentSync 1.6 工作器缺失，请修复应用安装。"
        if self.backend == "mlx":
            files = (self.python, self.worker, self.worker.with_name("latentsync_16_mlx.py"),
                     self.worker.with_name("latentsync_entry.py"), *mlx.source_files(self.root),
                     *(self.root / item[0] for item in (mlx.UNET_FILE, *mlx.RUNTIME_WEIGHTS)))
            for path in files:
                if not path.is_file() or not path.stat().st_size:
                    return f"LatentSync 1.6 MLX 离线运行文件缺失：{path}"
            return _runtime_issue(self, _file_revision(files))
        upstream = self.root / "upstream"
        required = (
            upstream / "scripts/inference.py", upstream / "configs/unet/stage2_512.yaml",
            self.root / "vae/config.json",
            self.root / "face/buffalo_l/det_10g.onnx", self.root / "face/buffalo_l/2d106det.onnx",
        )
        for path in required:
            if not path.is_file() or not path.stat().st_size:
                return f"LatentSync 1.6 离线运行文件缺失：{path}"
        vae = self.root / "vae"
        vae_weight = next((p for p in (
            vae / "diffusion_pytorch_model.safetensors", vae / "diffusion_pytorch_model.bin"
        ) if p.is_file() and p.stat().st_size), None)
        if vae_weight is None:
            return f"LatentSync 1.6 离线 VAE 权重缺失：{vae}"

        files = (self.python, self.worker, *required, vae_weight)
        # Metadata changes in the actual code consumed by the CUDA worker
        # invalidate readiness, including files added under its import roots.
        sources = tuple(path for name in ("scripts", "configs", "latentsync")
                        for path in sorted((upstream / name).rglob("*"))
                        if path.is_file() and "__pycache__" not in path.parts)
        return _runtime_issue(self, _file_revision((*files, *sources)))


    def backend_identity(self, weights_root: Path) -> dict[str, object]:
        """Bind resource calibration to the installed worker and its effective assets."""
        issue = self.issue()
        if issue:
            raise ValueError(issue)
        artifacts = {}
        for relative, size, expected in LATENTSYNC_16_DOWNLOADS:
            path = weights_root / relative
            artifacts[relative] = _artifact_identity(path, size=size, expected=expected)
        if self.backend == "mlx":
            for relative, size, expected in (mlx.UNET_FILE, *mlx.RUNTIME_WEIGHTS):
                artifacts[relative] = _artifact_identity(self.root / relative, size=size, expected=expected)
            worker_helpers = ("latentsync_16_mlx.py", "latentsync_entry.py", "media_timing.py")
            implementation = {"port_revision": mlx.MLX_REVISION, "source_sha256": mlx.SOURCE_SHA256}
        else:
            vae = next(path for name in ("diffusion_pytorch_model.safetensors", "diffusion_pytorch_model.bin")
                       if (path := self.root / "vae" / name).is_file() and path.stat().st_size)
            for path in (self.root / "vae/config.json", vae, self.root / "face/buffalo_l/det_10g.onnx",
                         self.root / "face/buffalo_l/2d106det.onnx"):
                artifacts[path.relative_to(self.root).as_posix()] = _artifact_identity(path)
            worker_helpers = ("media_timing.py",)
            implementation = {"upstream_path": str((self.root / "upstream").resolve())}
        helper_facts = {name: _artifact_identity(self.worker.with_name(name)) for name in worker_helpers}
        tools = {}
        for name in ("ffmpeg", "ffprobe"):
            executable = shutil.which(name)
            if executable is None:
                raise ValueError(f"LatentSync 1.6 缺少 {name}")
            tools[name] = _artifact_identity(Path(executable))
        return {
            "model_id": LATENTSYNC_16_MODEL_ID, "backend": self.backend,
            "platform": sys.platform, "architecture": platform.machine(),
            "runtime_root": str(self.root.resolve()), "python_path": str(self.python.absolute()),
            "python": _artifact_identity(self.python), "worker": _artifact_identity(self.worker),
            "worker_helpers": helper_facts, "tools": tools,
            "upstream_revision": LATENTSYNC_16_CODE_REVISION, "weights_revision": LATENTSYNC_16_WEIGHTS_REVISION,
            "implementation": implementation, "artifacts": artifacts,
        }


def _artifact_identity(path: Path, *, size: int | None = None, expected: str | None = None) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    if not resolved.is_file() or (size is not None and stat.st_size != size):
        raise ValueError(f"LatentSync 1.6 文件大小不符：{path}")
    facts = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    digest = _artifact_sha256(resolved, facts)
    if expected is not None and digest != expected:
        raise ValueError(f"LatentSync 1.6 文件校验失败：{path}")
    return {"path": str(resolved), "size": stat.st_size, "sha256": digest}


@lru_cache(maxsize=32)
def _artifact_sha256(path: Path, _file_revision) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_revision(files):
    return tuple((str(path), fact.st_size, fact.st_mtime_ns, fact.st_ctime_ns)
                 for path in files for fact in (path.stat(),))


class _RuntimeCheckFailed(RuntimeError):
    pass


def _runtime_issue(runtime: LatentSync16Runtime, revision) -> str | None:
    try:
        _check_runtime(runtime, revision)
    except _RuntimeCheckFailed as exc:
        return str(exc)
    return None


@lru_cache(maxsize=8)
def _check_runtime(runtime: LatentSync16Runtime, revision) -> None:
    # Cache only successful native imports. A transient native failure must not
    # keep the current verified installation unavailable until the API restarts.
    command = [str(runtime.python), "-u", str(runtime.worker), "--check-runtime",
               "--runtime-root", str(runtime.root.resolve()), "--code-revision", LATENTSYNC_16_CODE_REVISION,
               "--backend", runtime.backend]
    env = None
    if runtime.backend == "mlx":
        command = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)", *command]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1",
               "OPENBLAS_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
        env.pop("PYTHONPATH", None)
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _RuntimeCheckFailed(f"LatentSync 1.6 运行环境检查失败：{exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise _RuntimeCheckFailed(detail[-900:] or
                                  f"LatentSync 1.6 {runtime.backend.upper()} 环境检查进程退出：{result.returncode}")
