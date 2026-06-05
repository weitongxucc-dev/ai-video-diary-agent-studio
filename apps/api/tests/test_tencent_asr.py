from pathlib import Path

from app.asr import (
    ASRTranscription,
    ASRAudioTooLargeError,
    ASRTranscriptionError,
    FallbackASRTranscriber,
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


def test_local_whisper_transcriber_runs_local_module_and_parses_srt(tmp_path):
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
        ),
        runner=runner,
    )

    transcription = transcriber.transcribe(audio_path)

    assert calls[0][:3] == [str(python_path), "-m", "whisper"]
    assert transcription.provider == "local_whisper"
    assert transcription.text == "本地字幕"
    assert transcription.segments == [SubtitleSegment(0, 1_200, "本地字幕")]


def test_asr_fallback_uses_local_whisper_when_primary_provider_fails(tmp_path):
    class FailingTranscriber:
        configured = True

        def transcribe(self, audio_path):
            raise ASRTranscriptionError("cloud quota exhausted")

    class LocalTranscriber:
        configured = True

        def transcribe(self, audio_path):
            return ASRTranscription(
                provider="local_whisper",
                text="本地兜底",
                segments=[SubtitleSegment(0, 900, "本地兜底")],
            )

    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")

    transcription = FallbackASRTranscriber(
        [FailingTranscriber(), LocalTranscriber()]
    ).transcribe(audio_path)

    assert transcription.provider == "local_whisper"
    assert transcription.text == "本地兜底"
