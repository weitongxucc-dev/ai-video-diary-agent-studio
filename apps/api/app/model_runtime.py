from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ModelRuntimeState = Literal[
    "missing_runtime",
    "missing_worker",
    "not_installed",
    "invalid",
    "ready",
]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    capability: str
    display_name: str
    repository: str
    revision: str
    license_name: str
    license_url: str
    approximate_download_bytes: int
    parameter_scale: str
    quantization: str


@dataclass(frozen=True)
class ModelRuntimeStatus:
    model: ModelSpec
    state: ModelRuntimeState
    message: str
    model_path: str
    runtime_python_path: str | None
    worker_path: str | None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["ready"] = self.ready
        return payload


@dataclass(frozen=True)
class ModelReadiness:
    """A single answer to 「本地现在有没有可用的这个模型」that lists every gap.

    ``status()`` is a state machine: it stops at the first missing prerequisite
    so a caller gets one actionable state. That is correct for driving a UI
    button, but it hides the rest of the truth — on a machine with neither the
    runtime nor the weights it reports only the runtime, and the user never
    learns a multi-gigabyte download is also outstanding. Readiness collects all
    unmet prerequisites instead, and derives the single label from the weight
    state because that is the one the user has to act on.
    """

    model: ModelSpec
    available: bool
    label: str
    problems: tuple[str, ...]
    repair_action: str
    message: str


CHINESE_CLIP_VIT_B16 = ModelSpec(
    id="chinese-clip-vit-base-patch16",
    capability="visual_embedding_zh",
    display_name="Chinese-CLIP ViT-B/16",
    repository="OFA-Sys/chinese-clip-vit-base-patch16",
    revision="36e679e65c2a2fead755ae21162091293ad37834",
    license_name="MIT",
    license_url="https://github.com/OFA-Sys/Chinese-CLIP",
    approximate_download_bytes=753_290_873,
    parameter_scale="ViT-B/16 骨干（双塔总参数量未实测，见报告）",
    quantization="上游发布为 fp32 权重，无量化版本",
)

QWEN3_TTS_06B_BASE_8BIT = ModelSpec(
    id="qwen3-tts-12hz-0.6b-base-8bit",
    capability="voice_clone_zh",
    display_name="Qwen3-TTS 0.6B Base 8-bit",
    repository="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
    revision="50f45ef0047cde7e84c2ef04326acb8ada2436a7",
    license_name="Apache-2.0",
    license_url="https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
    approximate_download_bytes=1_991_296_593,
    parameter_scale="0.6B（取自仓库名 Qwen3-TTS-12Hz-0.6B）",
    quantization="8-bit（取自仓库名 8bit）",
)

QWEN3_VL_4B_4BIT = ModelSpec(
    id="Qwen3-VL-4B-Instruct-4bit",
    capability="visual_understanding",
    display_name="Qwen3-VL 4B 4-bit",
    repository="mlx-community/Qwen3-VL-4B-Instruct-4bit",
    revision="2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b",
    license_name="Apache-2.0",
    license_url="https://huggingface.co/mlx-community/Qwen3-VL-4B-Instruct-4bit",
    approximate_download_bytes=3_110_000_000,
    parameter_scale="4B（取自模型标识 Qwen3-VL-4B）",
    quantization="4-bit（取自模型标识 4bit）",
)


MODEL_SPECS = (CHINESE_CLIP_VIT_B16, QWEN3_TTS_06B_BASE_8BIT, QWEN3_VL_4B_4BIT)


class ModelCatalog:
    """Read-only inspection for separately installed local model runtimes.

    Installation is deliberately not implicit. A model is considered ready
    only after an installer writes a complete manifest and every declared file
    still exists with the recorded size. Full SHA-256 verification is exposed
    separately because hashing multi-gigabyte weights on every status request
    would make the desktop UI appear frozen.
    """

    def __init__(
        self,
        model_root_path: str | Path,
        runtime_python_path: str | Path | None,
        visual_worker_path: str | Path | None,
        voice_worker_path: str | Path | None = None,
        *,
        understanding_runtime_python_path: str | Path | None = None,
        understanding_worker_path: str | Path | None = None,
    ) -> None:
        self.model_root_path = Path(model_root_path).expanduser()
        self.understanding_runtime_python_path = Path(understanding_runtime_python_path).expanduser() if understanding_runtime_python_path else None
        self.understanding_worker_path = Path(understanding_worker_path).expanduser() if understanding_worker_path else None
        self.runtime_python_path = (
            Path(runtime_python_path).expanduser() if runtime_python_path else None
        )
        self.visual_worker_path = (
            Path(visual_worker_path).expanduser() if visual_worker_path else None
        )
        self.voice_worker_path = (
            Path(voice_worker_path).expanduser() if voice_worker_path else None
        )

    def list_statuses(self) -> list[ModelRuntimeStatus]:
        return [self.status(spec) for spec in MODEL_SPECS]

    def status(self, spec: ModelSpec) -> ModelRuntimeStatus:
        model_path = self.model_path(spec)
        worker_path = self._worker_path(spec)
        runtime_python_path = self.runtime_path(spec)
        common = {
            "model": spec,
            "model_path": str(model_path),
            "runtime_python_path": (
                str(runtime_python_path) if runtime_python_path else None
            ),
            "worker_path": str(worker_path) if worker_path else None,
        }
        if runtime_python_path is None or not runtime_python_path.is_file():
            return ModelRuntimeStatus(
                state="missing_runtime",
                message="本地模型运行环境尚未安装。",
                **common,
            )
        if worker_path is None or not worker_path.is_file():
            return ModelRuntimeStatus(
                state="missing_worker",
                message=f"{spec.display_name} 工作进程缺失，请修复应用安装。",
                **common,
            )
        manifest_path = model_path / "manifest.json"
        if not manifest_path.is_file():
            return ModelRuntimeStatus(
                state="not_installed",
                message=f"{spec.display_name} 尚未下载。",
                **common,
            )
        try:
            manifest = _read_manifest(manifest_path)
            _validate_manifest(spec, model_path, manifest, verify_checksums=False)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return ModelRuntimeStatus(
                state="invalid",
                message=f"模型安装不完整：{exc}",
                **common,
            )
        return ModelRuntimeStatus(
            state="ready",
            message=_ready_message(spec),
            **common,
        )

    def readiness(self, spec: ModelSpec) -> ModelReadiness:
        """Report every unmet prerequisite, not just the first one.

        Both this and ``status()`` answer the same question from the same
        primitives (``runtime_path``, ``_worker_path``, ``model_path`` and the
        manifest validators); they differ only in what they are allowed to hide.
        """
        model_path = self.model_path(spec)
        runtime_python_path = self.runtime_path(spec)
        worker_path = self._worker_path(spec)
        problems: list[str] = []
        actions: list[str] = []
        if runtime_python_path is None or not runtime_python_path.is_file():
            problems.append(f"运行环境缺失：{runtime_python_path or '未配置模型运行环境路径'}")
            actions.append("准备该模型的独立运行环境")
        if worker_path is None or not worker_path.is_file():
            problems.append(f"工作进程缺失：{worker_path or '未配置工作进程路径'}")
            actions.append("修复应用安装以恢复工作进程")
        manifest_path = model_path / "manifest.json"
        weight_state: str | None = None
        if not manifest_path.is_file():
            weight_state = "not_installed"
            problems.append(f"权重未下载：{model_path}")
            actions.append(_download_hint(spec))
        else:
            try:
                _validate_manifest(
                    spec,
                    model_path,
                    _read_manifest(manifest_path),
                    verify_checksums=False,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                weight_state = "invalid"
                problems.append(f"安装不完整：{exc}")
                actions.append("删除该模型目录后重新下载")
        if not problems:
            return ModelReadiness(
                model=spec,
                available=True,
                label="可用",
                problems=(),
                repair_action="无需处理。",
                message=_ready_message(spec),
            )
        label = {
            "not_installed": "未安装",
            "invalid": "安装不完整",
        }.get(weight_state, "运行环境不完整")
        return ModelReadiness(
            model=spec,
            available=False,
            label=label,
            problems=tuple(problems),
            repair_action="；".join(actions) + "。",
            message=f"{spec.display_name} {label}：" + "；".join(problems) + "。",
        )

    def verify(self, spec: ModelSpec) -> ModelRuntimeStatus:
        status = self.status(spec)
        if not status.ready:
            return status
        model_path = self.model_path(spec)
        try:
            manifest = _read_manifest(model_path / "manifest.json")
            _validate_manifest(spec, model_path, manifest, verify_checksums=True)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return ModelRuntimeStatus(
                model=spec,
                state="invalid",
                message=f"模型校验失败：{exc}",
                model_path=str(model_path),
                runtime_python_path=(
                    str(self.runtime_path(spec)) if self.runtime_path(spec) else None
                ),
                worker_path=str(self._worker_path(spec)) if self._worker_path(spec) else None,
            )
        return status

    def model_path(self, spec: ModelSpec) -> Path:
        return self.model_root_path / spec.id

    def runtime_path(self, spec: ModelSpec) -> Path | None:
        return self.understanding_runtime_python_path if spec.capability == "visual_understanding" else self.runtime_python_path

    def _worker_path(self, spec: ModelSpec) -> Path | None:
        if spec.capability == "visual_understanding":
            return self.understanding_worker_path
        if spec.capability == "voice_clone_zh":
            return self.voice_worker_path
        return self.visual_worker_path


def _gib(size: int) -> str:
    return f"{size / 1024 ** 3:.1f} GiB"


def _ready_message(spec: ModelSpec) -> str:
    return (
        f"{spec.display_name} 已就绪：参数规模 {spec.parameter_scale}；"
        f"量化 {spec.quantization}；权重常驻下限约 {_gib(spec.approximate_download_bytes)}"
        "（等于安装包体积；激活值、KV 缓存与图像缓存等运行时额外占用未实测）。"
    )


def _download_hint(spec: ModelSpec) -> str:
    return (
        f"在设置页的模型管理中下载 {spec.display_name}"
        f"（{spec.repository}@{spec.revision[:8]}，约 {_gib(spec.approximate_download_bytes)}，"
        f"许可 {spec.license_name}）"
    )


def _read_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest 格式错误")  # noqa: TRY004 - protocol validation
    return payload


def _validate_manifest(
    spec: ModelSpec,
    model_path: Path,
    manifest: dict[str, object],
    *,
    verify_checksums: bool,
) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest 版本不受支持")
    if manifest.get("model_id") != spec.id:
        raise ValueError("模型标识不匹配")
    if manifest.get("repository") != spec.repository:
        raise ValueError("模型来源不匹配")
    if manifest.get("revision") != spec.revision:
        raise ValueError("模型版本不匹配")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest 未声明模型文件")
    resolved_root = model_path.resolve()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("模型文件记录格式错误")  # noqa: TRY004
        relative_path = item.get("path")
        expected_size = item.get("size")
        expected_sha256 = item.get("sha256")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("模型文件路径缺失")
        file_path = (model_path / relative_path).resolve()
        try:
            file_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("模型文件路径越界") from exc
        if not file_path.is_file():
            raise ValueError(f"缺少模型文件：{relative_path}")
        if not isinstance(expected_size, int) or file_path.stat().st_size != expected_size:
            raise ValueError(f"模型文件大小不匹配：{relative_path}")
        if verify_checksums:
            if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
                raise ValueError(f"模型文件校验值无效：{relative_path}")
            if _sha256(file_path) != expected_sha256.lower():
                raise ValueError(f"模型文件校验失败：{relative_path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
