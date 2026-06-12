from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


SAFE_MODEL_SUFFIXES = {".pt"}
VENV_BIN_DIR_NAMES = {"bin", "Scripts"}


@dataclass(frozen=True)
class LocalWhisperPathStatus:
    configured: bool
    reason: str
    python_path: Path | None = None
    model_path: Path | None = None


def default_local_whisper_allowed_roots() -> tuple[Path, ...]:
    return (Path.home(), Path(__file__).resolve().parents[3])


def local_whisper_path_status(
    python_path: str | None,
    model_path: str | None,
    allowed_roots: Iterable[str | Path] | None = None,
) -> LocalWhisperPathStatus:
    if not python_path or not model_path:
        return LocalWhisperPathStatus(False, "Local Whisper is not configured")

    expanded_python = Path(python_path).expanduser()
    expanded_model = Path(model_path).expanduser()
    if not expanded_python.is_file() or not expanded_model.is_file():
        return LocalWhisperPathStatus(
            False,
            "Local Whisper Python or model path is missing",
        )

    roots = tuple(
        Path(root).expanduser()
        for root in (allowed_roots or default_local_whisper_allowed_roots())
    )
    if not _is_within_allowed_roots(
        expanded_python,
        roots,
    ) or not _is_within_allowed_roots(expanded_model, roots):
        return LocalWhisperPathStatus(
            False,
            "Local Whisper Python or model path is outside allowed directories",
            expanded_python,
            expanded_model,
        )

    if not _looks_like_python_executable(expanded_python):
        return LocalWhisperPathStatus(
            False,
            "Local Whisper Python path must point to a python executable",
            expanded_python,
            expanded_model,
        )

    if not _has_virtualenv_marker(expanded_python):
        return LocalWhisperPathStatus(
            False,
            "Local Whisper Python path must be inside a virtual environment",
            expanded_python,
            expanded_model,
        )

    if expanded_model.suffix not in SAFE_MODEL_SUFFIXES:
        return LocalWhisperPathStatus(
            False,
            "Local Whisper model path must point to a .pt model file",
            expanded_python,
            expanded_model,
        )

    return LocalWhisperPathStatus(
        True,
        "Local Whisper Python and model paths are valid",
        expanded_python,
        expanded_model,
    )


def _is_within_allowed_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved_path = path.resolve()
    for root in roots:
        try:
            resolved_path.relative_to(root.resolve())
        except ValueError:
            continue
        return True
    return False


def _looks_like_python_executable(path: Path) -> bool:
    return re.fullmatch(r"python(?:3(?:\.\d+)?)?", path.name) is not None


def _has_virtualenv_marker(path: Path) -> bool:
    if path.parent.name not in VENV_BIN_DIR_NAMES:
        return False
    return (path.parent.parent / "pyvenv.cfg").is_file()
