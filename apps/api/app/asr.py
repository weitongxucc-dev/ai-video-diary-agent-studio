from __future__ import annotations

import base64
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from app.config import Settings


class ASRConfigurationError(RuntimeError):
    pass


class ASRTranscriptionError(RuntimeError):
    pass


class ASRAudioTooLargeError(ASRTranscriptionError):
    pass


class ASRTimeoutError(ASRTranscriptionError):
    pass


@dataclass(frozen=True)
class SubtitleSegment:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class ASRTranscription:
    provider: str
    text: str
    segments: list[SubtitleSegment]


class ASRTranscriber(Protocol):
    @property
    def configured(self) -> bool:
        ...

    def transcribe(self, audio_path: str | Path) -> ASRTranscription:
        ...


class TencentASRApi(Protocol):
    def create_rec_task(self, audio_bytes: bytes) -> int:
        ...

    def describe_task_status(self, task_id: int) -> dict[str, object]:
        ...


class TencentCloudASRTranscriber:
    def __init__(
        self,
        settings: Settings,
        api: TencentASRApi | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._api = api
        self._sleeper = sleeper

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.tencentcloud_secret_id
            and self._settings.tencentcloud_secret_key
        )

    def transcribe(self, audio_path: str | Path) -> ASRTranscription:
        if not self.configured:
            raise ASRConfigurationError("Tencent Cloud ASR is not configured")
        path = Path(audio_path)
        audio_bytes = path.read_bytes()
        if len(audio_bytes) > self._settings.tencentcloud_asr_max_local_audio_bytes:
            raise ASRAudioTooLargeError(
                "local audio is larger than Tencent Cloud direct upload limit"
            )

        api = self._api or TencentCloudASRApiClient(self._settings)
        try:
            task_id = api.create_rec_task(audio_bytes)
        except ASRTranscriptionError:
            raise
        except Exception as exc:
            raise ASRTranscriptionError(str(exc)) from exc
        deadline = time.monotonic() + self._settings.tencentcloud_asr_max_wait_seconds
        last_status: dict[str, object] | None = None
        while time.monotonic() <= deadline:
            try:
                status = api.describe_task_status(task_id)
            except ASRTranscriptionError:
                raise
            except Exception as exc:
                raise ASRTranscriptionError(str(exc)) from exc
            last_status = status
            status_code = int(status.get("Status", -1))
            if status_code == 2:
                return self._transcription_from_status(status)
            if status_code == 3:
                raise ASRTranscriptionError(
                    str(status.get("StatusStr") or "Tencent Cloud ASR task failed")
                )
            self._sleeper(self._settings.tencentcloud_asr_poll_interval_seconds)
        raise ASRTimeoutError(
            str((last_status or {}).get("StatusStr") or "Tencent Cloud ASR timed out")
        )

    def _transcription_from_status(self, status: dict[str, object]) -> ASRTranscription:
        text = str(status.get("Result") or "").strip()
        segments = self._segments_from_detail(status.get("ResultDetail"))
        if not segments and text:
            segments = self._segments_from_result_text(text)
        if not segments and text:
            segments = [SubtitleSegment(0, 3_000, text)]
        return ASRTranscription(
            provider="tencentcloud",
            text=text,
            segments=segments,
        )

    def _segments_from_detail(self, detail: object) -> list[SubtitleSegment]:
        if not isinstance(detail, list):
            return []
        segments: list[SubtitleSegment] = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            text = str(item.get("FinalSentence") or item.get("Sentence") or "").strip()
            if not text:
                continue
            start_ms = int(float(item.get("StartMs") or 0))
            end_ms = int(float(item.get("EndMs") or max(start_ms + 1_800, 3_000)))
            segments.append(SubtitleSegment(start_ms, max(end_ms, start_ms + 500), text))
        return segments

    def _segments_from_result_text(self, result: str) -> list[SubtitleSegment]:
        pattern = re.compile(
            r"\[(?P<start>\d+):(?P<start_seconds>\d+\.\d+),(?P<end>\d+):(?P<end_seconds>\d+\.\d+)\]\s*(?P<text>[^\[]+)"
        )
        segments: list[SubtitleSegment] = []
        for match in pattern.finditer(result):
            start_ms = _timestamp_parts_to_ms(
                int(match.group("start")),
                float(match.group("start_seconds")),
            )
            end_ms = _timestamp_parts_to_ms(
                int(match.group("end")),
                float(match.group("end_seconds")),
            )
            text = match.group("text").strip()
            if text:
                segments.append(SubtitleSegment(start_ms, max(end_ms, start_ms + 500), text))
        return segments


class LocalWhisperTranscriber:
    def __init__(
        self,
        settings: Settings,
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner or self._run_command

    @property
    def configured(self) -> bool:
        python_path = self._settings.local_whisper_python_path
        model_path = self._settings.local_whisper_model_path
        return bool(
            python_path
            and Path(python_path).expanduser().is_file()
            and model_path
            and Path(model_path).expanduser().is_file()
        )

    def transcribe(self, audio_path: str | Path) -> ASRTranscription:
        if not self.configured:
            raise ASRConfigurationError("Local Whisper is not configured")
        path = Path(audio_path).expanduser()
        if not path.is_file():
            raise ASRTranscriptionError("local audio file not found")
        python_path = str(Path(self._settings.local_whisper_python_path or "").expanduser())
        model_path = str(Path(self._settings.local_whisper_model_path or "").expanduser())
        with TemporaryDirectory(prefix="ai_video_whisper_") as output_dir:
            command = [
                python_path,
                "-m",
                "whisper",
                str(path),
                "--model",
                model_path,
                "--output_dir",
                output_dir,
                "--output_format",
                "srt",
                "--language",
                self._settings.local_whisper_language,
                "--fp16",
                "False",
                "--verbose",
                "False",
            ]
            try:
                self._runner(command)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ASRTranscriptionError(f"Local Whisper failed: {exc}") from exc
            srt_path = Path(output_dir) / f"{path.stem}.srt"
            if not srt_path.is_file():
                raise ASRTranscriptionError("Local Whisper did not generate SRT output")
            segments = _segments_from_srt(srt_path.read_text(encoding="utf-8"))
        if not segments:
            raise ASRTranscriptionError("Local Whisper generated empty subtitles")
        return ASRTranscription(
            provider="local_whisper",
            text="\n".join(segment.text for segment in segments),
            segments=segments,
        )

    def _run_command(self, command: list[str]) -> None:
        subprocess.run(command, check=True)


class FallbackASRTranscriber:
    def __init__(self, transcribers: list[ASRTranscriber]) -> None:
        self._transcribers = transcribers

    @property
    def configured(self) -> bool:
        return any(transcriber.configured for transcriber in self._transcribers)

    def transcribe(self, audio_path: str | Path) -> ASRTranscription:
        errors: list[str] = []
        attempted = False
        for transcriber in self._transcribers:
            if not transcriber.configured:
                continue
            attempted = True
            try:
                return transcriber.transcribe(audio_path)
            except (ASRConfigurationError, ASRTranscriptionError) as exc:
                errors.append(f"{type(transcriber).__name__}: {exc}")
        if not attempted:
            raise ASRConfigurationError("No ASR transcriber is configured")
        raise ASRTranscriptionError("; ".join(errors))


def asr_transcriber_from_settings(settings: Settings) -> ASRTranscriber:
    return FallbackASRTranscriber(
        [
            TencentCloudASRTranscriber(settings),
            LocalWhisperTranscriber(settings),
        ]
    )


class TencentCloudASRApiClient:
    def __init__(self, settings: Settings) -> None:
        try:
            from tencentcloud.asr.v20190614 import asr_client, models
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
        except ImportError as exc:
            raise ASRConfigurationError(
                "tencentcloud-sdk-python is not installed"
            ) from exc
        if not settings.tencentcloud_secret_id or not settings.tencentcloud_secret_key:
            raise ASRConfigurationError("Tencent Cloud ASR is not configured")

        http_profile = HttpProfile()
        http_profile.endpoint = "asr.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        cred = credential.Credential(
            settings.tencentcloud_secret_id,
            settings.tencentcloud_secret_key,
        )
        self._client = asr_client.AsrClient(
            cred,
            settings.tencentcloud_asr_region,
            client_profile,
        )
        self._models = models
        self._settings = settings

    def create_rec_task(self, audio_bytes: bytes) -> int:
        request = self._models.CreateRecTaskRequest()
        request.EngineModelType = self._settings.tencentcloud_asr_engine_model_type
        request.ChannelNum = 1
        request.ResTextFormat = 3
        request.SourceType = 1
        request.Data = base64.b64encode(audio_bytes).decode("ascii")
        request.DataLen = len(audio_bytes)
        request.ConvertNumMode = 1
        request.SentenceMaxLength = 18
        response = self._client.CreateRecTask(request)
        task_id = getattr(getattr(response, "Data", None), "TaskId", None)
        if task_id is None:
            raise ASRTranscriptionError("Tencent Cloud ASR did not return task id")
        return int(task_id)

    def describe_task_status(self, task_id: int) -> dict[str, object]:
        request = self._models.DescribeTaskStatusRequest()
        request.TaskId = int(task_id)
        response = self._client.DescribeTaskStatus(request)
        data = getattr(response, "Data", None)
        if data is None:
            raise ASRTranscriptionError("Tencent Cloud ASR did not return task data")
        return {
            "Status": getattr(data, "Status", None),
            "StatusStr": getattr(data, "StatusStr", None),
            "Result": getattr(data, "Result", None),
            "ResultDetail": [
                _model_object_to_dict(item)
                for item in (getattr(data, "ResultDetail", None) or [])
            ],
        }


def transcription_to_srt(transcription: ASRTranscription) -> str:
    return "\n\n".join(
        "\n".join(
            [
                str(index),
                f"{_format_srt_time(segment.start_ms)} --> {_format_srt_time(segment.end_ms)}",
                segment.text,
            ]
        )
        for index, segment in enumerate(transcription.segments, start=1)
    ) + "\n"


def _model_object_to_dict(value: object) -> dict[str, object]:
    if hasattr(value, "_serialize"):
        serialized = value._serialize()  # noqa: SLF001 - Tencent SDK model helper.
        if isinstance(serialized, dict):
            return serialized
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def _timestamp_parts_to_ms(minutes: int, seconds: float) -> int:
    return int((minutes * 60 + seconds) * 1_000)


def _segments_from_srt(srt: str) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    for block in re.split(r"\r?\n\s*\r?\n", srt.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_index = next(
            (index for index, line in enumerate(lines) if " --> " in line),
            None,
        )
        if timestamp_index is None or timestamp_index + 1 >= len(lines):
            continue
        start, end = lines[timestamp_index].split(" --> ", 1)
        text = "\n".join(lines[timestamp_index + 1 :]).strip()
        if text:
            segments.append(
                SubtitleSegment(
                    _srt_timestamp_to_ms(start),
                    _srt_timestamp_to_ms(end),
                    text,
                )
            )
    return segments


def _srt_timestamp_to_ms(timestamp: str) -> int:
    match = re.fullmatch(r"(\d+):(\d+):(\d+),(\d+)", timestamp.strip())
    if match is None:
        raise ASRTranscriptionError(f"invalid SRT timestamp: {timestamp}")
    hours, minutes, seconds, milliseconds = [int(value) for value in match.groups()]
    return ((hours * 60 + minutes) * 60 + seconds) * 1_000 + milliseconds


def _format_srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"
