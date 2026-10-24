from __future__ import annotations

import json
import os
import sys
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Protocol

from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT, ModelCatalog

if TYPE_CHECKING:
    from app.creation_execution import CreationStageGrantContext


@dataclass(frozen=True)
class MediaRuntimeFailure:
    """A user-actionable capability or execution failure at a media boundary."""

    reason: str
    impact: str
    repair_action: str
    retryable: bool


class VoiceCloneError(ValueError):
    def __init__(self, failure: MediaRuntimeFailure | str) -> None:
        self.failure = (
            failure
            if isinstance(failure, MediaRuntimeFailure)
            else MediaRuntimeFailure(
                reason="provider_failed",
                impact="声音生成失败，未产生可用音频。",
                repair_action=str(failure),
                retryable=True,
            )
        )
        super().__init__(self.failure.repair_action)


@dataclass(frozen=True)
class VoiceCloneResult:
    audio_path: str
    duration_seconds: float
    sample_rate: int


class VoiceCloneProvider(Protocol):
    model_id: str
    supported_languages: tuple[str, ...]
    supports_pronunciation_dictionary: bool

    @property
    def ready(self) -> bool: ...

    @property
    def message(self) -> str: ...

    def generate(
        self,
        *,
        text: str,
        reference_audio_path: str | Path,
        reference_text: str,
        language: str,
        pronunciation_dictionary: tuple[dict[str, str], ...],
        output_path: str | Path,
    ) -> VoiceCloneResult: ...


    def generate_granted(
        self,
        *,
        text: str,
        reference_audio_path: str | Path,
        reference_text: str,
        language: str,
        pronunciation_dictionary: tuple[dict[str, str], ...],
        output_path: str | Path,
        grant_context: CreationStageGrantContext,
    ) -> VoiceCloneResult: ...


class UnavailableVoiceCloneProvider:
    model_id = QWEN3_TTS_06B_BASE_8BIT.id
    supported_languages: tuple[str, ...] = ()
    supports_pronunciation_dictionary = False

    def __init__(self, message: str = "本地声音克隆模型尚未安装。") -> None:
        self._message = message

    @property
    def ready(self) -> bool:
        return False

    @property
    def message(self) -> str:
        return self._message

    def generate(
        self,
        *,
        text: str,
        reference_audio_path: str | Path,
        reference_text: str,
        language: str,
        pronunciation_dictionary: tuple[dict[str, str], ...],
        output_path: str | Path,
    ) -> VoiceCloneResult:
        raise VoiceCloneError(
            text_to_speech_preflight(
                self,
                language=language,
                pronunciation_dictionary=pronunciation_dictionary,
            )
            or self.message
        )


    def generate_granted(
        self,
        *,
        text: str,
        reference_audio_path: str | Path,
        reference_text: str,
        language: str,
        pronunciation_dictionary: tuple[dict[str, str], ...],
        output_path: str | Path,
        grant_context: CreationStageGrantContext,
    ) -> VoiceCloneResult:
        if grant_context is None:
            raise PermissionError("声音生成需要本次设备许可")
        grant_context.check()
        return self.generate(
            text=text, reference_audio_path=reference_audio_path,
            reference_text=reference_text, language=language,
            pronunciation_dictionary=pronunciation_dictionary, output_path=output_path,
        )


class Qwen3TTSSubprocessProvider:
    model_id = QWEN3_TTS_06B_BASE_8BIT.id
    supported_languages = ("zh", "en")
    supports_pronunciation_dictionary = True

    def __init__(
        self,
        *,
        runtime_python_path: str | Path,
        worker_path: str | Path,
        model_path: str | Path,
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.runtime_python_path = Path(runtime_python_path).expanduser()
        self.worker_path = Path(worker_path).expanduser()
        self.model_path = Path(model_path).expanduser()
        self._runner = runner or self._run_command

    @property
    def ready(self) -> bool:
        return (
            self.runtime_python_path.is_file()
            and self.worker_path.is_file()
            and (self.model_path / "manifest.json").is_file()
        )

    @property
    def message(self) -> str:
        return (
            "本地声音克隆模型已就绪。"
            if self.ready
            else "本地声音克隆运行环境不完整。"
        )

    def generate(
        self,
        *,
        text: str,
        reference_audio_path: str | Path,
        reference_text: str,
        language: str,
        pronunciation_dictionary: tuple[dict[str, str], ...],
        output_path: str | Path,
        grant_context=None,
    ) -> VoiceCloneResult:
        narration = text.strip()
        transcript = reference_text.strip()
        reference = Path(reference_audio_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        preflight_failure = text_to_speech_preflight(
            self,
            language=language,
            pronunciation_dictionary=pronunciation_dictionary,
        )
        if preflight_failure is not None:
            raise VoiceCloneError(preflight_failure)
        if not narration:
            raise VoiceCloneError("待配音文案不能为空")
        if not transcript:
            raise VoiceCloneError("声音参考文字不能为空")
        if any(
            not isinstance(entry, dict)
            or any(
                not isinstance(entry.get(field), str) or not entry[field].strip()
                for field in ("id", "version", "term", "language", "target_pronunciation", "scope")
            )
            for entry in pronunciation_dictionary
        ):
            raise VoiceCloneError("发音词典条目无效")
        if not reference.is_file() or reference.suffix.lower() != ".wav":
            raise VoiceCloneError("声音参考文件必须是存在的 WAV 文件")
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="ai_video_voice_") as temporary_dir:
            request_path = Path(temporary_dir) / "request.json"
            response_path = Path(temporary_dir) / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model_id": self.model_id,
                        "model_path": str(self.model_path),
                        "text": narration,
                        "reference_audio_path": str(reference),
                        "reference_text": transcript,
                        "language": language,
                        "pronunciation_dictionary": pronunciation_dictionary,
                        "output_path": str(output),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            command = [
                str(self.runtime_python_path),
                str(self.worker_path),
                "--request",
                str(request_path),
                "--response",
                str(response_path),
            ]
            try:
                if grant_context is None:
                    self._runner(command)
                else:
                    self._run_command(command, grant_context=grant_context,
                        output_paths=(output, output.with_suffix(".tmp.wav"), response_path,
                                      response_path.with_suffix(response_path.suffix + ".tmp")))
            except (OSError, subprocess.SubprocessError) as exc:
                if grant_context is not None and isinstance(exc, PermissionError):
                    raise
                raise VoiceCloneError(f"声音克隆工作进程失败：{exc}") from exc
            if not response_path.is_file() or not output.is_file():
                raise VoiceCloneError("声音克隆工作进程没有生成音频")
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8"))
                result = _parse_result(payload, output)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise VoiceCloneError(f"声音克隆结果无效：{exc}") from exc
        return result

    def generate_granted(
        self,
        *,
        text: str,
        reference_audio_path: str | Path,
        reference_text: str,
        language: str,
        pronunciation_dictionary: tuple[dict[str, str], ...],
        output_path: str | Path,
        grant_context: CreationStageGrantContext,
    ) -> VoiceCloneResult:
        if grant_context is None:
            raise PermissionError("声音生成需要本次设备许可")
        grant_context.check()
        return self.generate(
            text=text, reference_audio_path=reference_audio_path,
            reference_text=reference_text, language=language,
            pronunciation_dictionary=pronunciation_dictionary, output_path=output_path,
            grant_context=grant_context,
        )

    def _run_command(self, command: list[str], *, grant_context=None, output_paths=()) -> None:
        cache_root = self.model_path.parent / ".cache"
        temporary_root = cache_root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        environment = (dict(os.environ) if grant_context is None else {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(cache_root / "home"),
            "XDG_CONFIG_HOME": str(cache_root / "config"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "OMP_NUM_THREADS": str(grant_context.threads),
            "OPENBLAS_NUM_THREADS": str(grant_context.threads),
            "VECLIB_MAXIMUM_THREADS": str(grant_context.threads),
            "RAYON_NUM_THREADS": str(grant_context.threads),
        })
        environment.update(
            {
                "HF_HOME": str(cache_root / "huggingface"),
                "XDG_CACHE_HOME": str(cache_root),
                "TMPDIR": str(temporary_root),
            }
        )
        if grant_context is None:
            completed = subprocess.run(command, check=False, capture_output=True, text=True,
                                       timeout=30 * 60, env=environment)
        else:
            if sys.platform == "darwin":
                # Process-scoped lower I/O priority, not a throughput limit.
                command = ["/usr/sbin/taskpolicy", "-d", "throttle", *command]
            completed = grant_context.run(command, env=environment, output_paths=output_paths)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            if len(detail) > 4000:
                detail = detail[-4000:]
            raise VoiceCloneError(
                f"声音克隆工作进程失败：{detail or f'exit {completed.returncode}'}"
            )


def voice_clone_provider_from_catalog(catalog: ModelCatalog) -> VoiceCloneProvider:
    status = catalog.status(QWEN3_TTS_06B_BASE_8BIT)
    if not status.ready or not status.runtime_python_path or not status.worker_path:
        return UnavailableVoiceCloneProvider(status.message)
    return Qwen3TTSSubprocessProvider(
        runtime_python_path=status.runtime_python_path,
        worker_path=status.worker_path,
        model_path=status.model_path,
    )


def text_to_speech_preflight(
    provider: VoiceCloneProvider,
    *,
    language: str,
    pronunciation_dictionary: tuple[dict[str, str], ...],
) -> MediaRuntimeFailure | None:
    """Reject unsupported TTS inputs before a provider or worker receives them."""
    if not provider.ready:
        return MediaRuntimeFailure(
            reason="capability_unavailable",
            impact="声音生成能力未配置，未生成音频。",
            repair_action=provider.message,
            retryable=True,
        )
    supported_languages = getattr(provider, "supported_languages", ("zh", "en", "zh-en"))
    if language not in supported_languages:
        supported = ", ".join(supported_languages) or "无"
        return MediaRuntimeFailure(
            reason="language_unavailable",
            impact="当前声音运行时不支持请求语言，未生成音频。",
            repair_action=f"选择已声明支持 {language} 的模型；当前模型支持：{supported}。",
            retryable=False,
        )
    if pronunciation_dictionary and not getattr(provider, "supports_pronunciation_dictionary", False):
        return MediaRuntimeFailure(
            reason="pronunciation_dictionary_unavailable",
            impact="当前声音运行时不会应用发音词典，未生成可能被误导的音频。",
            repair_action="选择明确支持发音词典的声音运行时，或移除词典条目后重试。",
            retryable=False,
        )
    return None


def _parse_result(payload: object, expected_output: Path) -> VoiceCloneResult:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("响应协议版本错误")
    if payload.get("model_id") != QWEN3_TTS_06B_BASE_8BIT.id:
        raise ValueError("响应模型不匹配")
    returned_path = payload.get("audio_path")
    duration = payload.get("duration_seconds")
    sample_rate = payload.get("sample_rate")
    if not isinstance(returned_path, str) or Path(returned_path).resolve() != expected_output:
        raise ValueError("响应音频路径不匹配")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError("响应音频时长无效")
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("响应采样率无效")
    _validate_wav_output(expected_output, sample_rate)
    return VoiceCloneResult(
        audio_path=str(expected_output),
        duration_seconds=round(float(duration), 3),
        sample_rate=sample_rate,
    )


def _validate_wav_output(output: Path, expected_sample_rate: int) -> None:
    try:
        with wave.open(str(output), "rb") as audio:
            if audio.getnchannels() < 1 or audio.getframerate() != expected_sample_rate:
                raise ValueError("输出音频格式与响应不匹配")
            if audio.getnframes() <= 0:
                raise ValueError("输出音频为空")
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"输出音频不是有效 WAV：{exc}") from exc
