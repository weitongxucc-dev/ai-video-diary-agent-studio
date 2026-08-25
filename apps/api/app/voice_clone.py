from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Protocol

from app.model_runtime import ModelCatalog, QWEN3_TTS_06B_BASE_8BIT


class VoiceCloneError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceCloneResult:
    audio_path: str
    duration_seconds: float
    sample_rate: int


class VoiceCloneProvider(Protocol):
    model_id: str

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
        output_path: str | Path,
    ) -> VoiceCloneResult: ...


class UnavailableVoiceCloneProvider:
    model_id = QWEN3_TTS_06B_BASE_8BIT.id

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
        output_path: str | Path,
    ) -> VoiceCloneResult:
        raise VoiceCloneError(self.message)


class Qwen3TTSSubprocessProvider:
    model_id = QWEN3_TTS_06B_BASE_8BIT.id

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
        output_path: str | Path,
    ) -> VoiceCloneResult:
        narration = text.strip()
        transcript = reference_text.strip()
        reference = Path(reference_audio_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        if not self.ready:
            raise VoiceCloneError(self.message)
        if not narration:
            raise VoiceCloneError("待配音文案不能为空")
        if not transcript:
            raise VoiceCloneError("声音参考文字不能为空")
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
                self._runner(command)
            except (OSError, subprocess.SubprocessError) as exc:
                raise VoiceCloneError(f"声音克隆工作进程失败：{exc}") from exc
            if not response_path.is_file() or not output.is_file():
                raise VoiceCloneError("声音克隆工作进程没有生成音频")
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8"))
                result = _parse_result(payload, output)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise VoiceCloneError(f"声音克隆结果无效：{exc}") from exc
        return result

    def _run_command(self, command: list[str]) -> None:
        cache_root = self.model_path.parent / ".cache"
        temporary_root = cache_root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.update(
            {
                "HF_HOME": str(cache_root / "huggingface"),
                "XDG_CACHE_HOME": str(cache_root),
                "TMPDIR": str(temporary_root),
            }
        )
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30 * 60,
            env=environment,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
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
    return VoiceCloneResult(
        audio_path=str(expected_output),
        duration_seconds=round(float(duration), 3),
        sample_rate=sample_rate,
    )
