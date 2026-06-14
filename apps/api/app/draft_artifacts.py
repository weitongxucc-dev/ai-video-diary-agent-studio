from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

from app.asr import (
    ASRConfigurationError,
    ASRTimeoutError,
    ASRTranscriber,
    ASRTranscriptionError,
    transcription_to_srt,
)
from app.cover_artifacts import write_cover as write_cover_artifact
from app.draft_models import DraftAsset
from app.media_commands import build_extract_audio_command
from app.models import Episode, ScriptPackage


def write_script_artifacts(
    package_dir: Path,
    episode: Episode | None,
    video: DraftAsset,
    *,
    asr_transcriber: ASRTranscriber,
    runner: Callable[[list[str]], None],
    video_duration_milliseconds: Callable[[Path], int | None],
) -> dict[str, object]:
    if episode is None:
        return {
            "subtitle_path": None,
            "subtitle_source": "none",
            "cover_path": None,
            "cover_source": "none",
            "asr_status": "not_configured",
        }

    subtitle_path = package_dir / "subtitle.srt"
    cover_path = package_dir / "cover.png"
    script = episode.script
    asr_result = write_asr_subtitle_if_available(
        package_dir,
        Path(video.path),
        subtitle_path,
        asr_transcriber=asr_transcriber,
        runner=runner,
    )
    subtitle_source = "none"
    asr_status = "not_configured"
    asr_error = None
    if asr_result is not None:
        subtitle_source = asr_result["subtitle_source"]
        asr_status = asr_result["asr_status"]
        asr_error = asr_result["asr_error"]
    if subtitle_source == "script_estimate" and script is None:
        subtitle_source = "none"
    if subtitle_source not in {"tencentcloud_asr", "local_whisper"} and script is not None:
        subtitle_source = "script_estimate"
        subtitle_path.write_text(
            script_to_srt(
                script,
                max_duration_ms=video_duration_milliseconds(Path(video.path)),
            ),
            encoding="utf-8",
        )
    write_cover(cover_path, episode)
    artifacts = {
        "subtitle_path": str(subtitle_path) if subtitle_path.exists() else None,
        "subtitle_source": subtitle_source,
        "cover_path": str(cover_path),
        "cover_source": "pixel_template",
        "asr_status": asr_status,
        "audio_path": str(package_dir / "audio_16k.wav")
        if (package_dir / "audio_16k.wav").exists()
        else None,
    }
    if asr_error:
        artifacts["asr_error"] = asr_error
    return artifacts


def write_asr_subtitle_if_available(
    package_dir: Path,
    video_path: Path,
    subtitle_path: Path,
    *,
    asr_transcriber: ASRTranscriber,
    runner: Callable[[list[str]], None],
) -> dict[str, str | None] | None:
    if not asr_transcriber.configured:
        return None
    audio_path = package_dir / "audio_16k.wav"
    extract_audio(video_path, audio_path, runner=runner)
    try:
        transcription = asr_transcriber.transcribe(audio_path)
    except ASRConfigurationError:
        return None
    except ASRTimeoutError as exc:
        return {
            "subtitle_source": "script_estimate",
            "asr_status": "timeout",
            "asr_error": str(exc),
        }
    except ASRTranscriptionError as exc:
        return {
            "subtitle_source": "script_estimate",
            "asr_status": "failed",
            "asr_error": str(exc),
        }
    subtitle_path.write_text(transcription_to_srt(transcription), encoding="utf-8")
    return {
        "subtitle_source": (
            "tencentcloud_asr"
            if transcription.provider == "tencentcloud"
            else transcription.provider
        ),
        "asr_status": "succeeded",
        "asr_error": None,
    }


def extract_audio(
    video_path: Path,
    audio_path: Path,
    *,
    runner: Callable[[list[str]], None],
) -> None:
    runner(build_extract_audio_command(video_path, audio_path))


def script_to_srt(
    script: ScriptPackage,
    max_duration_ms: int | None = None,
) -> str:
    entries: list[tuple[int, int, str]] = []
    cursor_ms = 0
    hook_duration_ms = 4_000
    for chunk in subtitle_chunks(script.hook):
        end_ms = cursor_ms + max(1_800, hook_duration_ms // 2)
        entries.append((cursor_ms, end_ms, chunk))
        cursor_ms = end_ms

    for section in script.sections:
        chunks = subtitle_chunks(section.content)
        section_duration_ms = max(section.estimated_seconds, 8) * 1_000
        chunk_duration_ms = max(1_800, section_duration_ms // max(1, len(chunks)))
        for chunk in chunks:
            end_ms = cursor_ms + chunk_duration_ms
            entries.append((cursor_ms, end_ms, chunk))
            cursor_ms = end_ms

    entries = fit_subtitles_to_duration(entries, max_duration_ms)
    return "\n\n".join(
        "\n".join(
            [
                str(index),
                f"{format_srt_time(start_ms)} --> {format_srt_time(end_ms)}",
                text,
            ]
        )
        for index, (start_ms, end_ms, text) in enumerate(entries, start=1)
    ) + "\n"


def fit_subtitles_to_duration(
    entries: list[tuple[int, int, str]],
    max_duration_ms: int | None,
) -> list[tuple[int, int, str]]:
    if not entries or not max_duration_ms or max_duration_ms <= 0:
        return entries
    total_duration = entries[-1][1]
    if total_duration <= max_duration_ms:
        return entries
    scale = max_duration_ms / total_duration
    fitted: list[tuple[int, int, str]] = []
    for start_ms, end_ms, text in entries:
        fitted_start = int(start_ms * scale)
        fitted_end = min(max_duration_ms, max(fitted_start + 1, int(end_ms * scale)))
        fitted.append((fitted_start, fitted_end, text))
    return fitted


def subtitle_chunks(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    return textwrap.wrap(
        normalized,
        width=22,
        break_long_words=True,
        replace_whitespace=False,
    )


def format_srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def write_cover(cover_path: Path, episode: Episode) -> None:
    write_cover_artifact(cover_path, episode)
