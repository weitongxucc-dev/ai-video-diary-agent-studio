from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess


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

    path = Path(root).expanduser() / file_name
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
