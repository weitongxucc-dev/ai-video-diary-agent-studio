from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.asr import ASRConfigurationError, ASRTranscriber

MAX_VOICE_AUDIO_BYTES = 25_000_000

CONTENT_TYPE_SUFFIXES = {
    "application/octet-stream": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
}


def transcribe_voice_audio(
    audio_bytes: bytes,
    *,
    content_type: str | None,
    transcriber: ASRTranscriber,
) -> dict[str, Any]:
    if not audio_bytes:
        raise ValueError("audio body is required")
    if len(audio_bytes) > MAX_VOICE_AUDIO_BYTES:
        raise ValueError("audio body is too large")
    if not transcriber.configured:
        raise ASRConfigurationError("No ASR transcriber is configured")

    suffix = _suffix_for_content_type(content_type)
    with TemporaryDirectory(prefix="ai_video_voice_") as temporary_dir:
        audio_path = Path(temporary_dir) / f"voice-command{suffix}"
        audio_path.write_bytes(audio_bytes)
        transcription = transcriber.transcribe(audio_path)
    return {
        "provider": transcription.provider,
        "transcript": transcription.text,
        "segments": [
            {
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.text,
            }
            for segment in transcription.segments
        ],
    }


def _suffix_for_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_SUFFIXES.get(normalized, ".webm")
