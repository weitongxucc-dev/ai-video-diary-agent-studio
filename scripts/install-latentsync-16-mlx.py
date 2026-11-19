#!/usr/bin/env python3
"""Install the verified Apple Silicon LatentSync 1.6 runtime from local inputs."""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "apps/api"))

from app.lipsync_models import LATENTSYNC_16_DOWNLOADS, LatentSync16Runtime, _artifact_identity
from app.model_install_execution import publish_model
from app.model_runtime import LATENTSYNC_16_512, ModelCatalog
from model_workers import latentsync_16_mlx as mlx

OVERLAY = REPOSITORY / "scripts/latentsync-16-mlx"
WORKER = REPOSITORY / "apps/api/model_workers/latentsync_16_worker.py"


def archive(source: Path, revision: str, target: Path, *paths: str) -> None:
    data = subprocess.check_output(["git", "-C", str(source), "archive", revision, *paths])
    with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
        bundle.extractall(target, filter="data")


def copy_asset(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # APFS cloning avoids duplicating multi-gigabyte weight blocks.
    if subprocess.run(["/bin/cp", "-c", str(source), str(target)], capture_output=True).returncode:
        shutil.copy2(source, target)


def install_runtime(options, destination: Path) -> LatentSync16Runtime:
    runtime = LatentSync16Runtime(destination, destination / ".venv/bin/python", WORKER, "mlx")
    if destination.exists():
        issue = runtime.issue()
        if issue:
            raise ValueError(issue)
        mlx.verify_weights(destination, checksums=True)
        return runtime
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".latentsync-16-runtime-", dir=destination.parent) as temporary:
        root = Path(temporary) / "runtime"
        upstream = root / "upstream"
        upstream.mkdir(parents=True)
        archive(options.mlx_source, mlx.MLX_REVISION, root, "LICENSE", "latentsync_mlx")
        archive(options.official_source, mlx.UPSTREAM_REVISION, upstream, "LICENSE",
                "latentsync/whisper", "latentsync/utils/affine_transform.py", "latentsync/utils/image_processor.py",
                "latentsync/utils/util.py", "latentsync/utils/mask.png")
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent)}
        for source, patch in ((root, "port.patch"), (upstream, "upstream.patch")):
            subprocess.run(["git", "-C", str(source), "apply", "--unidiff-zero", str(OVERLAY / patch)],
                           check=True, env=env)
        for path in sorted(OVERLAY.glob("*.py")):
            shutil.copy2(path, root / path.name)
        copy_asset(options.pose_assets / "official_onnx.py", root / "pose/official_onnx.py")
        for source, relative in (
            (options.converted_unet, mlx.UNET_FILE[0]),
            (options.converted_vae, mlx.RUNTIME_WEIGHTS[0][0]),
            (options.pose_assets / "dw-ll_ucoco_384.onnx", mlx.RUNTIME_WEIGHTS[1][0]),
        ):
            copy_asset(source, root / relative)
        for relative, dependency in ((".venv", options.venv), ("dependencies", options.mlx_dependencies),
                                     ("pose/dependencies", options.pose_dependencies)):
            (root / relative).symlink_to(dependency, target_is_directory=True)
        mlx.verify_source(root)
        mlx.verify_weights(root, checksums=True)
        checked = LatentSync16Runtime(root, root / ".venv/bin/python", WORKER, "mlx")
        issue = checked.issue()
        if issue:
            raise ValueError(issue)
        root.rename(destination)
    return runtime



def install_calibration(root: Path, fixtures: Path) -> None:
    destination = root / "calibration"
    if destination.exists():
        for name, expected in mlx.CALIBRATION_FILES.items():
            _artifact_identity(destination / name, expected=expected)
        return
    with TemporaryDirectory(prefix=".latentsync-16-fixtures-", dir=root) as temporary:
        stage = Path(temporary) / "calibration"
        for name, expected in mlx.CALIBRATION_FILES.items():
            copy_asset(fixtures / name, stage / name)
            _artifact_identity(stage / name, expected=expected)
        stage.rename(destination)

def install_official_weights(model_root: Path, assets: Path, runtime: LatentSync16Runtime) -> ModelCatalog:
    catalog = ModelCatalog(model_root, None, None, latentsync_runtime=runtime)
    model = LATENTSYNC_16_512
    model_root.mkdir(parents=True, exist_ok=True)
    if not catalog.model_path(model).exists():
        with TemporaryDirectory(prefix=".latentsync-16-model-", dir=model_root) as temporary:
            stage = Path(temporary)
            manifest = []
            for relative, size, expected in LATENTSYNC_16_DOWNLOADS:
                destination = stage / model.id / relative
                copy_asset(assets / relative, destination)
                checked = _artifact_identity(destination, size=size, expected=expected)
                manifest.append({"path": relative, "size": checked["size"], "sha256": checked["sha256"]})
            publish_model(catalog, stage, model, manifest)
    state = catalog.verify(model)
    if not state.ready:
        raise ValueError(state.message)
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments = {
        "model-root": "曜核 MODEL_ROOT_PATH；官方权重发布在 latentsync-1.6-512 子目录",
        "mlx-source": "sb1992/latentsync-mlx 的本地 Git 仓库，含固定提交",
        "official-source": "bytedance/LatentSync 的本地 Git 仓库，含固定提交",
        "official-weights": "包含官方 latentsync_unet.pt 和 whisper/tiny.pt 的目录",
        "converted-unet": "通过校验的 1.6 FP16 MLX 安全张量",
        "converted-vae": "通过校验的 SD VAE MLX 安全张量",
        "pose-assets": "包含 DWPose ONNX 和归一化适配脚本的目录",
        "venv": "已安装 PyTorch 与预处理依赖的原生 Apple Silicon 虚拟环境目录",
        "mlx-dependencies": "MLX、mlx-metal、Kornia 本地依赖目录",
        "pose-dependencies": "ONNX Runtime 和 loguru 的本地依赖目录",
    }
    for name, description in arguments.items():
        parser.add_argument(f"--{name}", required=True, type=Path, help=description)
    parser.add_argument("--runtime-root", type=Path, help="默认为 MODEL_ROOT_PATH 同级 model-runtimes/latentsync-1.6-mlx")
    parser.add_argument("--calibration-input", type=Path, default=OVERLAY / "calibration",
                        help="校验并安装无个人资料标定素材，默认使用本仓库自带文件")
    options = parser.parse_args()
    for name, value in vars(options).items():
        if value is not None:
            setattr(options, name, value.expanduser().resolve())
    root = options.runtime_root or options.model_root.parent / "model-runtimes/latentsync-1.6-mlx"
    try:
        runtime = install_runtime(options, root)
        catalog = install_official_weights(options.model_root, options.official_weights, runtime)
        install_calibration(runtime.root, options.calibration_input)
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"LatentSync 1.6 MLX 安装失败：{exc}\n")
    print(f"LatentSync 1.6 MLX 安装通过：{runtime.root}  {catalog.model_path(LATENTSYNC_16_512)}")


if __name__ == "__main__":
    main()
