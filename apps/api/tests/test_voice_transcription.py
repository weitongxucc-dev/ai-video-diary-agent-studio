from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.asr import (
    ASRConfigurationError,
    ASRTranscription,
    ASRTranscriptionError,
    SubtitleSegment,
)


client = TestClient(main_module.app)


class FakeVoiceTranscriber:
    configured = True

    def __init__(self) -> None:
        self.audio_path: Path | None = None

    def transcribe(self, audio_path):
        self.audio_path = Path(audio_path)
        assert self.audio_path.suffix == ".webm"
        assert self.audio_path.read_bytes() == b"fake browser audio"
        return ASRTranscription(
            provider="local_whisper",
            text="继续今天的流程",
            segments=[SubtitleSegment(0, 1200, "继续今天的流程")],
        )


def test_voice_transcription_endpoint_accepts_browser_audio_and_cleans_temp_file():
    transcriber = FakeVoiceTranscriber()
    main_module.voice_transcriber = transcriber

    response = client.post(
        "/api/voice/transcribe",
        content=b"fake browser audio",
        headers={"content-type": "audio/webm"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "local_whisper",
        "transcript": "继续今天的流程",
        "segments": [
            {"start_ms": 0, "end_ms": 1200, "text": "继续今天的流程"}
        ],
    }
    assert transcriber.audio_path is not None
    assert not transcriber.audio_path.exists()


def test_voice_transcription_endpoint_rejects_empty_audio():
    response = client.post(
        "/api/voice/transcribe",
        content=b"",
        headers={"content-type": "audio/webm"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "audio body is required"


def test_voice_transcription_endpoint_reports_missing_asr_configuration():
    class MissingTranscriber:
        configured = False

        def transcribe(self, audio_path):
            raise ASRConfigurationError("No ASR transcriber is configured")

    main_module.voice_transcriber = MissingTranscriber()

    response = client.post(
        "/api/voice/transcribe",
        content=b"fake browser audio",
        headers={"content-type": "audio/webm"},
    )

    assert response.status_code == 503
    assert "No ASR transcriber is configured" in response.json()["detail"]


def test_voice_transcription_endpoint_reports_transcription_errors():
    class FailingTranscriber:
        configured = True

        def transcribe(self, audio_path):
            raise ASRTranscriptionError("Local Whisper failed")

    main_module.voice_transcriber = FailingTranscriber()

    response = client.post(
        "/api/voice/transcribe",
        content=b"fake browser audio",
        headers={"content-type": "audio/webm"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Local Whisper failed"
