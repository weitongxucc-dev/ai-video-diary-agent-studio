"""Shared media timing checks for new avatar outputs and cache reuse."""
from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path


class MediaTimingError(ValueError):
    pass


def probe_avatar_media(path: Path, *, expected_duration: float | None = None, engine: str = "ffprobe") -> tuple[float, str]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,sample_rate,channels,duration,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise MediaTimingError(completed.stderr.strip() or "ffprobe failed")
    try:
        payload = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload["streams"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaTimingError("ffprobe returned invalid media metadata") from exc
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if expected_duration is None:
        expected_duration = duration
    if duration <= 0 or video is None or audio is None:
        raise MediaTimingError("rendered output must contain non-empty video and audio streams")
    if (video.get("codec_name") != "h264" or audio.get("codec_name") != "aac"
            or not video.get("width") or not video.get("height")):
        raise MediaTimingError("rendered output requires H.264 video and AAC audio")
    try:
        video_duration = float(video["duration"])
        audio_duration = float(audio["duration"])
        frame_rate = float(Fraction(video["avg_frame_rate"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise MediaTimingError("rendered output is missing valid stream timing") from exc
    if not all(math.isfinite(value) and value > 0 for value in (
        duration, video_duration, audio_duration, frame_rate, expected_duration,
    )):
        raise MediaTimingError("rendered output has invalid stream timing")
    # Container duration can be supplied entirely by the audio even when face
    # detection/inference has dropped video frames. Check each actual stream.
    frame_seconds = 1 / frame_rate
    for name, stream_duration in (("video", video_duration), ("audio", audio_duration)):
        if abs(stream_duration - expected_duration) > frame_seconds + 0.001:
            raise MediaTimingError(
                f"rendered {name} duration {stream_duration:.3f}s does not match "
                f"narration {expected_duration:.3f}s within one output frame; "
                "check face detection and inference output before retrying"
            )
    evidence = json.dumps(
        {
            "engine": engine,
            "duration_seconds": round(duration, 3),
            "video_duration_seconds": video_duration,
            "audio_duration_seconds": audio_duration,
            "expected_duration_seconds": expected_duration,
            "frame_rate": frame_rate,
            "validation_scope": "media_timing_only_not_lip_sync_quality",
            "video_codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "audio_codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return round(duration, 3), evidence


def probe_media_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise MediaTimingError(completed.stderr.strip() or "ffprobe failed")
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise MediaTimingError("ffprobe returned an invalid duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaTimingError("media duration must be finite and positive")
    return duration


