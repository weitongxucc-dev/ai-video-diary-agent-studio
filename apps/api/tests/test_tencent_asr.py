import subprocess
from pathlib import Path

from app.asr import (
    ASRConfigurationError,
    ASRTranscription,
    ASRAudioTooLargeError,
    ASRTranscriptionError,
    ASRTimeoutError,
    FallbackASRTranscriber,
    LocalWhisperTranscriber,
    SubtitleSegment,
    TencentCloudASRTranscriber,
    asr_transcriber_from_settings,
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


def configured_tencent_transcriber(api, **settings_overrides):
    return TencentCloudASRTranscriber(
        Settings(
            tencentcloud_secret_id="configured",
            tencentcloud_secret_key="configured",
            **settings_overrides,
        ),
        api=api,
        sleeper=lambda _: None,
    )


def safe_local_whisper_paths(tmp_path):
    venv_root = tmp_path / ".venv-whisper"
    python_path = venv_root / "bin" / "python"
    model_path = tmp_path / "small.pt"
    python_path.parent.mkdir(parents=True)
    (venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    python_path.write_text("", encoding="utf-8")
    model_path.write_bytes(b"model")
    return python_path, model_path


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


def test_tencent_asr_requires_configuration(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")

    try:
        TencentCloudASRTranscriber(Settings()).transcribe(audio)
    except ASRConfigurationError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("Tencent ASR should require credentials")


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


def test_tencent_asr_propagates_known_provider_errors(tmp_path):
    class KnownFailingApi:
        def create_rec_task(self, audio_bytes: bytes) -> int:
            raise ASRTranscriptionError("known provider error")

        def describe_task_status(self, task_id: int) -> dict[str, object]:
            raise AssertionError("describe should not be called")

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")

    try:
        configured_tencent_transcriber(KnownFailingApi()).transcribe(audio)
    except ASRTranscriptionError as exc:
        assert "known provider error" in str(exc)
    else:
        raise AssertionError("known provider error should propagate")


def test_tencent_asr_wraps_describe_errors(tmp_path):
    class DescribeFailingApi:
        def create_rec_task(self, audio_bytes: bytes) -> int:
            return 123

        def describe_task_status(self, task_id: int) -> dict[str, object]:
            raise RuntimeError("describe exploded")

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")

    try:
        configured_tencent_transcriber(DescribeFailingApi()).transcribe(audio)
    except ASRTranscriptionError as exc:
        assert "describe exploded" in str(exc)
    else:
        raise AssertionError("describe errors should be wrapped")


def test_tencent_asr_raises_failed_status(tmp_path):
    class FailedStatusApi:
        def create_rec_task(self, audio_bytes: bytes) -> int:
            return 123

        def describe_task_status(self, task_id: int) -> dict[str, object]:
            return {"Status": 3, "StatusStr": "recognition failed"}

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")

    try:
        configured_tencent_transcriber(FailedStatusApi()).transcribe(audio)
    except ASRTranscriptionError as exc:
        assert "recognition failed" in str(exc)
    else:
        raise AssertionError("failed Tencent status should raise")


def test_tencent_asr_times_out_without_polling_when_deadline_has_passed(tmp_path):
    class PendingApi:
        def create_rec_task(self, audio_bytes: bytes) -> int:
            return 123

        def describe_task_status(self, task_id: int) -> dict[str, object]:
            raise AssertionError("deadline should skip polling")

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake-audio")

    try:
        configured_tencent_transcriber(
            PendingApi(),
            tencentcloud_asr_max_wait_seconds=-1,
        ).transcribe(audio)
    except ASRTimeoutError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("expired deadline should time out")


def test_tencent_status_parses_result_text_when_detail_is_missing():
    transcriber = TencentCloudASRTranscriber(Settings())

    transcription = transcriber._transcription_from_status(
        {
            "Result": "[0:00.000,0:01.200] 第一段[0:01.200,0:02.600] 第二段",
            "ResultDetail": None,
        }
    )

    assert transcription.segments == [
        SubtitleSegment(0, 1_200, "第一段"),
        SubtitleSegment(1_200, 2_600, "第二段"),
    ]


def test_tencent_status_falls_back_to_single_plain_text_segment():
    transcriber = TencentCloudASRTranscriber(Settings())

    transcription = transcriber._transcription_from_status(
        {"Result": "没有时间戳的纯文本", "ResultDetail": []}
    )

    assert transcription.segments == [
        SubtitleSegment(0, 3_000, "没有时间戳的纯文本")
    ]


def test_tencent_detail_parser_skips_invalid_and_empty_items():
    transcriber = TencentCloudASRTranscriber(Settings())

    segments = transcriber._segments_from_detail(
        [
            "not-a-dict",
            {"FinalSentence": "   ", "StartMs": 0, "EndMs": 100},
            {"Sentence": "有效句子", "StartMs": 100, "EndMs": 200},
        ]
    )

    assert segments == [SubtitleSegment(100, 600, "有效句子")]


def test_transcription_to_srt_formats_multi_segment_timestamps():
    srt = transcription_to_srt(
        ASRTranscription(
            provider="test",
            text="hello",
            segments=[
                SubtitleSegment(0, 1_234, "第一段"),
                SubtitleSegment(3_661_005, 3_662_006, "第二段"),
            ],
        )
    )

    assert "00:00:00,000 --> 00:00:01,234" in srt
    assert "01:01:01,005 --> 01:01:02,006" in srt


def test_local_whisper_rejects_invalid_srt_timestamp(tmp_path):
    python_path, model_path = safe_local_whisper_paths(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")

    def runner(command):
        output_dir = Path(command[command.index("--output_dir") + 1])
        (output_dir / "audio.srt").write_text(
            "1\nnot-a-timestamp --> 00:00:01,000\n字幕\n",
            encoding="utf-8",
        )

    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        runner=runner,
        allowed_roots=[tmp_path],
    )

    try:
        transcriber.transcribe(audio_path)
    except ASRTranscriptionError as exc:
        assert "invalid SRT timestamp" in str(exc)
    else:
        raise AssertionError("invalid SRT timestamps should be rejected")


def test_local_whisper_transcriber_runs_local_module_and_parses_srt(tmp_path):
    python_path, model_path = safe_local_whisper_paths(tmp_path)
    audio_path = tmp_path / "audio.wav"
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
        allowed_roots=[tmp_path],
    )

    transcription = transcriber.transcribe(audio_path)

    assert calls[0][:3] == [str(python_path), "-m", "whisper"]
    assert transcription.provider == "local_whisper"
    assert transcription.text == "本地字幕"
    assert transcription.segments == [SubtitleSegment(0, 1_200, "本地字幕")]


def test_local_whisper_rejects_missing_audio_file(tmp_path):
    python_path, model_path = safe_local_whisper_paths(tmp_path)
    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        allowed_roots=[tmp_path],
    )

    try:
        transcriber.transcribe(tmp_path / "missing.wav")
    except ASRTranscriptionError as exc:
        assert "audio file not found" in str(exc)
    else:
        raise AssertionError("missing local audio should be rejected")


def test_local_whisper_wraps_runner_failure(tmp_path):
    python_path, model_path = safe_local_whisper_paths(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")

    def runner(command):
        raise subprocess.CalledProcessError(1, command)

    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        runner=runner,
        allowed_roots=[tmp_path],
    )

    try:
        transcriber.transcribe(audio_path)
    except ASRTranscriptionError as exc:
        assert "Local Whisper failed" in str(exc)
    else:
        raise AssertionError("runner failures should be wrapped")


def test_local_whisper_rejects_missing_srt_output(tmp_path):
    python_path, model_path = safe_local_whisper_paths(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")
    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        runner=lambda command: None,
        allowed_roots=[tmp_path],
    )

    try:
        transcriber.transcribe(audio_path)
    except ASRTranscriptionError as exc:
        assert "did not generate SRT" in str(exc)
    else:
        raise AssertionError("missing SRT output should be rejected")


def test_local_whisper_rejects_empty_srt_output(tmp_path):
    python_path, model_path = safe_local_whisper_paths(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")

    def runner(command):
        output_dir = Path(command[command.index("--output_dir") + 1])
        (output_dir / "audio.srt").write_text("", encoding="utf-8")

    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        runner=runner,
        allowed_roots=[tmp_path],
    )

    try:
        transcriber.transcribe(audio_path)
    except ASRTranscriptionError as exc:
        assert "empty subtitles" in str(exc)
    else:
        raise AssertionError("empty SRT output should be rejected")


def test_local_whisper_rejects_python_path_outside_allowed_roots(tmp_path):
    allowed_root = tmp_path / "safe"
    unsafe_venv_root = tmp_path / "unsafe" / ".venv-whisper"
    python_path = unsafe_venv_root / "bin" / "python"
    model_path = allowed_root / "small.pt"
    audio_path = allowed_root / "audio.wav"
    python_path.parent.mkdir(parents=True)
    allowed_root.mkdir()
    (unsafe_venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    python_path.write_text("", encoding="utf-8")
    model_path.write_bytes(b"model")
    audio_path.write_bytes(b"fake-audio")
    calls = []

    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        runner=lambda command: calls.append(command),
        allowed_roots=[allowed_root],
    )

    assert transcriber.configured is False
    try:
        transcriber.transcribe(audio_path)
    except ASRConfigurationError as exc:
        assert "outside allowed directories" in str(exc)
    else:
        raise AssertionError("unsafe local whisper python path should be rejected")
    assert calls == []


def test_local_whisper_rejects_python_without_virtualenv_marker(tmp_path):
    python_path = tmp_path / "bin" / "python"
    model_path = tmp_path / "small.pt"
    audio_path = tmp_path / "audio.wav"
    python_path.parent.mkdir()
    python_path.write_text("", encoding="utf-8")
    model_path.write_bytes(b"model")
    audio_path.write_bytes(b"fake-audio")

    transcriber = LocalWhisperTranscriber(
        Settings(
            local_whisper_python_path=str(python_path),
            local_whisper_model_path=str(model_path),
        ),
        allowed_roots=[tmp_path],
    )

    assert transcriber.configured is False
    try:
        transcriber.transcribe(audio_path)
    except ASRConfigurationError as exc:
        assert "virtual environment" in str(exc)
    else:
        raise AssertionError("local whisper python path should require a venv marker")


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


def test_asr_fallback_configured_reflects_children():
    class UnconfiguredTranscriber:
        configured = False

        def transcribe(self, audio_path):
            raise AssertionError("not used")

    class ConfiguredTranscriber:
        configured = True

        def transcribe(self, audio_path):
            raise AssertionError("not used")

    assert FallbackASRTranscriber([UnconfiguredTranscriber()]).configured is False
    assert FallbackASRTranscriber(
        [UnconfiguredTranscriber(), ConfiguredTranscriber()]
    ).configured is True


def test_asr_transcriber_factory_builds_fallback_chain():
    transcriber = asr_transcriber_from_settings(Settings())

    assert isinstance(transcriber, FallbackASRTranscriber)


def test_asr_fallback_requires_at_least_one_configured_transcriber(tmp_path):
    class UnconfiguredTranscriber:
        configured = False

        def transcribe(self, audio_path):
            raise AssertionError("unconfigured transcriber should be skipped")

    try:
        FallbackASRTranscriber([UnconfiguredTranscriber()]).transcribe(tmp_path / "audio.wav")
    except ASRConfigurationError as exc:
        assert "No ASR transcriber" in str(exc)
    else:
        raise AssertionError("fallback should require a configured transcriber")


def test_asr_fallback_reports_all_configured_failures(tmp_path):
    class FailingTranscriber:
        configured = True

        def transcribe(self, audio_path):
            raise ASRTranscriptionError("first failed")

    class AlsoFailingTranscriber:
        configured = True

        def transcribe(self, audio_path):
            raise ASRConfigurationError("second failed")

    try:
        FallbackASRTranscriber(
            [FailingTranscriber(), AlsoFailingTranscriber()]
        ).transcribe(tmp_path / "audio.wav")
    except ASRTranscriptionError as exc:
        assert "FailingTranscriber: first failed" in str(exc)
        assert "AlsoFailingTranscriber: second failed" in str(exc)
    else:
        raise AssertionError("fallback should aggregate configured failures")
