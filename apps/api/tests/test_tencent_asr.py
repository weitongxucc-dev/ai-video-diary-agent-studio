from pathlib import Path
from dataclasses import replace
import pytest

from app.asr import (
    ASRAudioTooLargeError,
    ASRTranscription,
    ASRTranscriptionError,
    asr_transcriber_from_settings,
    LocalWhisperTranscriber,
    SubtitleSegment,
    TencentCloudASRTranscriber,
    transcription_to_srt,
)
from app.config import Settings


class FakeTencentASRApi:
    def __init__(self) -> None:
        self.created_audio_bytes = 0
        self.create_payload = {}
        self.describe_calls = 0

    def create_rec_task(self, audio_bytes: bytes) -> int:
        self.created_audio_bytes = len(audio_bytes)
        self.create_payload = {"data_len": len(audio_bytes)}
        return 12345

    def describe_task_status(self, task_id: int) -> dict[str, object]:
        assert task_id == 12345
        self.describe_calls += 1
        if self.describe_calls == 1:
            return {"Status": 1, "StatusStr": "doing", "Result": "", "ResultDetail": []}
        return {
            "Status": 2,
            "StatusStr": "success",
            "Result": "腾讯云语音识别欢迎您。",
            "ResultDetail": [
                {
                    "FinalSentence": "腾讯云语音识别欢迎您。",
                    "StartMs": 20,
                    "EndMs": 2380,
                }
            ],
        }


class FailingTencentASRApi:
    def create_rec_task(self, audio_bytes: bytes) -> int:
        raise RuntimeError("Resource pack exhausted")

    def describe_task_status(self, task_id: int) -> dict[str, object]:
        raise AssertionError("describe should not be called after create failure")


def test_tencent_asr_transcriber_polls_and_returns_timed_segments(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")
    api = FakeTencentASRApi()
    transcriber = TencentCloudASRTranscriber(
        Settings(
            tencentcloud_secret_id="configured",
            tencentcloud_secret_key="configured",
            tencentcloud_asr_max_wait_seconds=5,
        ),
        api=api,
        sleeper=lambda _: None,
    )

    transcription = transcriber.transcribe(audio)

    assert api.created_audio_bytes == len(b"fake-audio")
    assert transcription.provider == "tencentcloud"
    assert transcription.text == "腾讯云语音识别欢迎您。"
    assert transcription.segments[0].start_ms == 20
    assert transcription.segments[0].end_ms == 2380
    assert "腾讯云语音识别欢迎您。" in transcription_to_srt(transcription)


def test_tencent_asr_rejects_local_audio_larger_than_cloud_limit(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x" * 12)
    transcriber = TencentCloudASRTranscriber(
        Settings(
            tencentcloud_secret_id="configured",
            tencentcloud_secret_key="configured",
            tencentcloud_asr_max_local_audio_bytes=10,
        ),
        api=FakeTencentASRApi(),
        sleeper=lambda _: None,
    )

    try:
        transcriber.transcribe(audio)
    except ASRAudioTooLargeError as exc:
        assert "local audio is larger" in str(exc)
    else:
        raise AssertionError("large local audio should be rejected")


def test_tencent_asr_wraps_provider_create_errors(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")
    transcriber = TencentCloudASRTranscriber(
        Settings(
            tencentcloud_secret_id="configured",
            tencentcloud_secret_key="configured",
        ),
        api=FailingTencentASRApi(),
        sleeper=lambda _: None,
    )

    try:
        transcriber.transcribe(audio)
    except ASRTranscriptionError as exc:
        assert "Resource pack exhausted" in str(exc)
    else:
        raise AssertionError("provider errors should be wrapped as ASRTranscriptionError")


@pytest.mark.parametrize("language", ["auto", "", "en", "zh"])
def test_local_whisper_transcriber_runs_local_module_and_parses_srt(tmp_path, language):
    python_path = tmp_path / "python"
    model_path = tmp_path / "small.pt"
    audio_path = tmp_path / "audio.wav"
    python_path.write_text("", encoding="utf-8")
    model_path.write_bytes(b"model")
    audio_path.write_bytes(b"fake-audio")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        output_dir = Path(command[command.index("--output_dir") + 1])
        (output_dir / "audio.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,200\n本地字幕\n",
            encoding="utf-8",
        )

    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
            local_whisper_language=language,
        ),
        runner=runner,
    )

    transcription = transcriber.transcribe(audio_path)

    if language in {"", "auto"}:
        assert "--language" not in calls[0]
    else:
        assert calls[0][calls[0].index("--language") + 1] == language
    assert calls[0][:3] == [str(python_path), "-m", "whisper"]
    assert transcription.provider == "local_whisper"
    assert transcription.text == "本地字幕"
    assert transcription.segments == [SubtitleSegment(0, 1_200, "本地字幕")]


def test_default_asr_does_not_use_saved_cloud_credentials():
    settings = Settings(tencentcloud_secret_id="test-id", tencentcloud_secret_key="test-key")
    assert isinstance(asr_transcriber_from_settings(settings), LocalWhisperTranscriber)
    assert not settings.asr_configured
    explicit = replace(settings, asr_provider="tencentcloud")
    assert isinstance(asr_transcriber_from_settings(explicit), TencentCloudASRTranscriber)
    assert explicit.asr_configured
    with pytest.raises(ValueError, match="ASR_PROVIDER"):
        replace(settings, asr_provider="unknown")


def test_local_whisper_cpp_uses_selected_binary_and_existing_ggml_model(tmp_path):
    binary, model, audio = [tmp_path / name for name in ("whisper-cli", "ggml-small.bin", "voice.wav")]
    for path in (binary, model, audio):
        path.write_bytes(b"fixture")
    calls = []

    def runner(command):
        calls.append(command)
        Path(command[command.index("-of") + 1] + ".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nShow the USB port\n", encoding="utf-8")

    settings = Settings(local_whisper_backend="whisper_cpp", local_whisper_cli_path=str(binary),
                        local_whisper_model_path=str(model))
    transcription = LocalWhisperTranscriber(settings, runner=runner).transcribe(audio)
    assert settings.asr_configured
    assert calls[0][0] == str(binary)
    assert calls[0][calls[0].index("-l") + 1] == "auto"
    assert transcription.text == "Show the USB port"
