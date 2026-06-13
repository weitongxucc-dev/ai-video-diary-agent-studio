from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess


DEFAULT_VIDEO_CODEC_ARGS = [
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-crf",
    "23",
    "-c:a",
    "aac",
]


def run_media_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def capture_media_command(
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )


def read_media_duration_seconds(
    input_path: Path,
    *,
    capture: Callable[..., subprocess.CompletedProcess[str]] = capture_media_command,
) -> float:
    result = capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ],
        check=True,
    )
    return float(result.stdout.strip())


def build_cut_video_command(
    source_path: Path,
    output_path: Path,
    keep_ranges: list[tuple[float, float]],
    *,
    speed: float | None = None,
    normalize_audio: bool = False,
) -> list[str]:
    base = ["ffmpeg", "-y", "-i", str(source_path)]
    if not keep_ranges:
        return [
            *base,
            *_optional_simple_filters(speed=speed, normalize_audio=normalize_audio),
            *DEFAULT_VIDEO_CODEC_ARGS,
            str(output_path),
        ]

    filter_complex = build_trim_concat_filter_complex(
        keep_ranges,
        speed=speed,
        normalize_audio=normalize_audio,
    )
    return [
        *base,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        *DEFAULT_VIDEO_CODEC_ARGS,
        str(output_path),
    ]


def build_trim_concat_filter_complex(
    keep_ranges: list[tuple[float, float]],
    *,
    speed: float | None = None,
    normalize_audio: bool = False,
) -> str:
    trim_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, (start, end) in enumerate(keep_ranges):
        trim_parts.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
        )
        trim_parts.append(
            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")

    if speed is None and not normalize_audio:
        output_labels = "[v][a]"
        post_filters = ""
    else:
        speed_value = _speed_value(speed)
        output_labels = "[vcat][acat]"
        audio_filter = f"atempo={speed_value}"
        if normalize_audio:
            audio_filter = f"{audio_filter},loudnorm"
        post_filters = f";[vcat]setpts=PTS/{speed_value}[v];[acat]{audio_filter}[a]"

    return (
        ";".join(trim_parts)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={len(keep_ranges)}:v=1:a=1{output_labels}"
        + post_filters
    )


def build_extract_audio_command(source_path: Path, audio_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(audio_path),
    ]


def _optional_simple_filters(
    *,
    speed: float | None,
    normalize_audio: bool,
) -> list[str]:
    if speed is None and not normalize_audio:
        return []
    speed_value = _speed_value(speed)
    audio_filter = f"atempo={speed_value}"
    if normalize_audio:
        audio_filter = f"{audio_filter},loudnorm"
    return [
        "-filter:v",
        f"setpts=PTS/{speed_value}",
        "-filter:a",
        audio_filter,
    ]


def _speed_value(speed: float | None) -> str:
    return str(speed if speed is not None else 1.0)


def safe_child_file_path(
    root: str | Path,
    file_name: str,
    *,
    allowed_extensions: set[str] | frozenset[str] | None = None,
    must_exist: bool = True,
) -> Path:
    if Path(file_name).name != file_name:
        raise ValueError("invalid video file name")
    if allowed_extensions and Path(file_name).suffix.lower() not in allowed_extensions:
        raise ValueError("unsupported video file")

    resolved_root = Path(root).expanduser().resolve()
    path = (resolved_root / file_name).resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError("video file is outside the video directory")
    if must_exist and (not path.exists() or not path.is_file()):
        raise ValueError("video file not found")
    return path


def safe_file_under_root(
    path_value: str | Path,
    root: str | Path,
    *,
    error_message: str = "file is outside the video output directory",
) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    path = Path(path_value).expanduser().resolve()
    if not path.is_relative_to(resolved_root) or not path.is_file():
        raise ValueError(error_message)
    return path
