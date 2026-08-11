"""Voice transcription: accept uploaded audio, transcribe via ASR pipeline."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from app.asr import (
    ASRConfigurationError,
    ASRTranscriber,
    ASRTranscriptionError,
    asr_transcriber_from_settings,
)
from app.config import Settings

logger = logging.getLogger(__name__)


def transcribe_audio_bytes(
    audio_bytes: bytes,
    settings: Settings,
    *,
    transcriber: ASRTranscriber | None = None,
) -> dict:
    """Run ASR on raw audio bytes using the configured transcriber pipeline.

    Uses Tencent Cloud ASR if configured, else falls back to local Whisper.
    Returns dict with keys: text, provider.
    Raises RuntimeError if no transcriber is configured.
    Raises ASRTranscriptionError on transcription failure.
    """
    _transcriber = transcriber or asr_transcriber_from_settings(settings)

    if not _transcriber.configured:
        raise RuntimeError(
            "No ASR transcriber is configured. "
            "Set TENCENTCLOUD_SECRET_ID/TENCENTCLOUD_SECRET_KEY for Tencent Cloud ASR "
            "or LOCAL_WHISPER_PYTHON_PATH/LOCAL_WHISPER_MODEL_PATH for local Whisper."
        )

    suffix = ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    try:
        result = _transcriber.transcribe(tmp_path)
        return {
            "transcript": result.text,
            "provider": result.provider,
            "segments": [
                {"start": seg.start_ms / 1000.0, "end": seg.end_ms / 1000.0, "text": seg.text}
                for seg in result.segments
            ],
        }
    except ASRConfigurationError as exc:
        raise RuntimeError(str(exc)) from exc
    except ASRTranscriptionError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
