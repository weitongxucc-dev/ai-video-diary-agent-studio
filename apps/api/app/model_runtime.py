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

# The three answers about a model directory, plus one honest refusal. ``unknown``
# is for a directory this project has no expected-file list for: reporting such a
# directory as "complete" would be the very invention this module exists to stop.
WeightsState = Literal["not_found", "unknown", "incomplete", "complete"]


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

    ``severity`` carries that classification as data rather than leaving callers
    to string-match ``label``: the four values are closed, and a caller that
    matched on the Chinese label would silently fall through to its default
    branch the first time a label is reworded.
    """

    model: ModelSpec
    available: bool
    severity: Literal["ready", "missing", "degraded", "invalid"]
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


@dataclass(frozen=True)
class DownloadFile:
    path: str
    size: int


# The upstream file each model is expected to contain. This lives beside the
# specs rather than in the installer because reading a model directory has to be
# possible *without* the installer: the installer is the only component that can
# write an install record, so making inspection depend on it would mean a
# half-installed model can only be diagnosed by the thing meant to fix it.
MODEL_FILES: dict[str, tuple[DownloadFile, ...]] = {
    QWEN3_VL_4B_4BIT.id: (
        DownloadFile("added_tokens.json", 707),
        DownloadFile("chat_template.jinja", 5292),
        DownloadFile("chat_template.json", 5502),
        DownloadFile("config.json", 7137),
        DownloadFile("generation_config.json", 269),
        DownloadFile("merges.txt", 1671853),
        DownloadFile("model.safetensors", 3093767283),
        DownloadFile("model.safetensors.index.json", 64742),
        DownloadFile("preprocessor_config.json", 782),
        DownloadFile("special_tokens_map.json", 613),
        DownloadFile("tokenizer.json", 11422654),
        DownloadFile("tokenizer_config.json", 5445),
        DownloadFile("video_preprocessor_config.json", 817),
        DownloadFile("vocab.json", 2776833),
    ),
    CHINESE_CLIP_VIT_B16.id: (
        DownloadFile("config.json", 3_008),
        DownloadFile("preprocessor_config.json", 342),
        DownloadFile("pytorch_model.bin", 753_177_983),
        DownloadFile("vocab.txt", 109_540),
    ),
    QWEN3_TTS_06B_BASE_8BIT.id: (
        DownloadFile("config.json", 5_522),
        DownloadFile("generation_config.json", 245),
        DownloadFile("merges.txt", 1_671_839),
        DownloadFile("model.safetensors", 1_304_461_214),
        DownloadFile("model.safetensors.index.json", 77_731),
        DownloadFile("preprocessor_config.json", 127),
        DownloadFile("speech_tokenizer/config.json", 2_336),
        DownloadFile("speech_tokenizer/configuration.json", 76),
        DownloadFile("speech_tokenizer/model.safetensors", 682_293_092),
        DownloadFile("speech_tokenizer/preprocessor_config.json", 234),
        DownloadFile("tokenizer_config.json", 7_344),
        DownloadFile("vocab.json", 2_776_833),
    ),
}


@dataclass(frozen=True)
class WeightsInspection:
    """What a model directory actually holds, independent of any install record.

    ``manifest.json`` is this project's own record of an install, not an
    upstream file. Judging a directory by whether that record exists makes a
    folder holding every upstream weight indistinguishable from an empty one,
    and points the user at a multi-gigabyte re-download they do not need. Reading
    the directory itself keeps the three answers apart: nothing there, part of
    it there, all of it there.
    """

    model_path: str
    exists: bool
    expected: tuple[DownloadFile, ...]
    present: tuple[DownloadFile, ...]
    missing: tuple[DownloadFile, ...]
    wrong_size: tuple[DownloadFile, ...]
    unlisted: tuple[str, ...]

    @property
    def state(self) -> WeightsState:
        if not self.exists:
            return "not_found"
        if not self.expected:
            return "unknown"
        if self.missing or self.wrong_size:
            return "incomplete"
        return "complete"

    def describe(self) -> str:
        """One line naming what was found, in words rather than state codes."""
        if self.state == "not_found":
            return f"模型目录不存在：{self.model_path}"
        if self.state == "unknown":
            return f"目录存在但本项目没有它的期望文件清单：{self.model_path}"
        detail = f"已有 {len(self.present)}/{len(self.expected)} 个上游文件"
        if self.missing:
            detail += "，缺少 " + "、".join(item.path for item in self.missing)
        if self.wrong_size:
            detail += "，大小不符 " + "、".join(item.path for item in self.wrong_size)
        return f"{detail}：{self.model_path}"


def inspect_weights_directory(
    model_path: str | Path,
    expected: tuple[DownloadFile, ...] = (),
) -> WeightsInspection:
    """Look at a model directory directly; no manifest is consulted or needed."""
    path = Path(model_path).expanduser()
    if not path.is_dir():
        return WeightsInspection(
            model_path=str(path),
            exists=False,
            expected=expected,
            present=(),
            missing=expected,
            wrong_size=(),
            unlisted=(),
        )
    present: list[DownloadFile] = []
    missing: list[DownloadFile] = []
    wrong_size: list[DownloadFile] = []
    for item in expected:
        candidate = path / item.path
        if not candidate.is_file():
            missing.append(item)
        elif candidate.stat().st_size != item.size:
            wrong_size.append(item)
        else:
            present.append(item)
    listed = {item.path for item in expected}
    # manifest.json is our own record, already reported on its own terms; the
    # rest of the listing is what is genuinely unaccounted for.
    unlisted = tuple(
        sorted(
            entry.name
            for entry in path.iterdir()
            if entry.name not in listed and entry.name != "manifest.json"
        )
    )
    return WeightsInspection(
        model_path=str(path),
        exists=True,
        expected=expected,
        present=tuple(present),
        missing=tuple(missing),
        wrong_size=tuple(wrong_size),
        unlisted=unlisted,
    )


# How each weight state reads to the user, and how it maps onto the four health
# statuses. Kept as one table so the wording and the severity can never drift
# apart, and so ``runtime_health`` reads a field instead of matching a label.
_READINESS_BY_WEIGHT_STATE: dict[str, tuple[str, Literal["ready", "missing", "degraded", "invalid"]]] = {
    "not_found": ("未找到", "missing"),
    "incomplete": ("找到了但不完整", "invalid"),
    "unverifiable": ("缺安装记录，且无法核对完整性", "degraded"),
    "record_missing": ("权重齐全，缺安装记录", "degraded"),
    "record_invalid": ("安装不完整", "invalid"),
    "runtime_missing": ("运行环境不完整", "degraded"),
}


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

    def inspect_weights(self, spec: ModelSpec) -> WeightsInspection:
        """Read this spec's directory, whether or not an install record exists."""
        return inspect_weights_directory(self.model_path(spec), MODEL_FILES.get(spec.id, ()))

    def readiness(self, spec: ModelSpec) -> ModelReadiness:
        """Report every unmet prerequisite, not just the first one.

        Both this and ``status()`` answer the same question from the same
        primitives (``runtime_path``, ``_worker_path``, ``model_path`` and the
        manifest validators); they differ only in what they are allowed to hide.

        The weight branch is where the two differ most. An install record, when
        present, is validated as before and stays authoritative. When it is
        absent the directory itself decides: 找不到 and 找到了但不完整 and 权重齐全
        are three different situations with three different remedies, and none of
        them is 「重新下载」 unless the files really are absent.
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
        weight_state = "ok"
        if manifest_path.is_file():
            try:
                _validate_manifest(
                    spec,
                    model_path,
                    _read_manifest(manifest_path),
                    verify_checksums=False,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                weight_state = "record_invalid"
                problems.append(f"安装不完整：{exc}")
                actions.append("删除该模型目录后重新下载")
        else:
            inspection = self.inspect_weights(spec)
            if inspection.state == "not_found":
                weight_state = "not_found"
                problems.append(f"权重未下载：{inspection.describe()}")
                actions.append(_download_hint(spec))
            elif inspection.state == "incomplete":
                weight_state = "incomplete"
                problems.append(inspection.describe())
                actions.append("补齐缺失文件后重试；确认目录已损坏时删除该模型目录后重新下载")
            elif inspection.state == "unknown":
                # 没有期望文件清单就没有「齐全」这个结论可说。这正是 LatentSync-1.6
                # 那种目录的处境：里面躺着两个 unet，缺 vae 和 pose，谁也看不出来。
                weight_state = "unverifiable"
                problems.append(f"{inspection.describe()}，且缺安装记录 {manifest_path}")
                actions.append("核对该目录是否包含运行所需的全部文件后补写安装记录")
            else:
                # 上游文件齐全，缺的只是本项目自己的安装记录。重下权重解决不了它。
                weight_state = "record_missing"
                problems.append(f"{inspection.describe()}，但缺安装记录 {manifest_path}")
                actions.append("补写安装记录（须核对 revision 与校验值），不必重新下载权重")
        if not problems:
            return ModelReadiness(
                model=spec,
                available=True,
                severity="ready",
                label="可用",
                problems=(),
                repair_action="无需处理。",
                message=_ready_message(spec),
            )
        if weight_state == "ok":
            # 权重没问题，缺口全在运行环境 —— 劝用户重下几 GB 权重是错的。
            weight_state = "runtime_missing"
        label, severity = _READINESS_BY_WEIGHT_STATE[weight_state]
        return ModelReadiness(
            model=spec,
            available=False,
            severity=severity,
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
