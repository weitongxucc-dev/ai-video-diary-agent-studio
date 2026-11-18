"""Immutable model identities for new choices and historical task replay."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import subprocess
import sys

LATENTSYNC_16_MODEL_ID = "latentsync-1.6-512"
LATENTSYNC_15_MODEL_ID = "latentsync-1.5-mlx-fidelity-brow"
MUSETALK_MODEL_ID = "musetalk-v1.5-mps"
LATENTSYNC_16_CODE_REVISION = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
LATENTSYNC_16_WEIGHTS_REVISION = "c42c7e6c8e9c213626389fa7d9a3c444b8536353"
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

    @classmethod
    def from_settings(cls, settings):
        root = Path(settings.latentsync_16_root_path or (
            Path(settings.model_root_path).parent / "model-runtimes" / "latentsync-1.6-cuda"
        )).expanduser()
        default_python = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
        return cls(root, Path(settings.latentsync_16_runtime_python_path or root / default_python).expanduser(),
                   Path(settings.latentsync_worker_path).with_name("latentsync_16_worker.py").expanduser())

    def issue(self) -> str | None:
        if sys.platform == "darwin":
            return "LatentSync 1.6 官方 512 运行时需要 NVIDIA CUDA 和至少 18 GB 显存；当前 Mac 无 CUDA。"
        if not self.python.is_file():
            return f"请配置 LatentSync 1.6 独立的 CUDA Python 环境：{self.python}"
        if not self.worker.is_file():
            return "LatentSync 1.6 工作器缺失，请修复应用安装。"
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
        revision = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in files)
        return _check_official_runtime(self, revision)


@lru_cache(maxsize=8)
def _check_official_runtime(runtime: LatentSync16Runtime, revision) -> str | None:
    try:
        result = subprocess.run([str(runtime.python), str(runtime.worker), "--check-runtime",
            "--runtime-root", str(runtime.root.resolve()), "--code-revision", LATENTSYNC_16_CODE_REVISION],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"LatentSync 1.6 运行环境检查失败：{exc}"
    if result.returncode:
        return (result.stderr.strip() or result.stdout.strip() or "LatentSync 1.6 CUDA 环境未就绪")[-900:]
    return None
