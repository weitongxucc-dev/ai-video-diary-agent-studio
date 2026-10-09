#!/usr/bin/env python3
"""Entry point for the API server packaged with PyInstaller.

Run with::

    ./api-server                 # from dist/
    python server.py             # dev
    uvicorn app.main:app         # dev (raw FastAPI)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _freeze_root() -> Path:
    """Resolve the directory holding the bundled executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent  # repo root


_ENV_FILE_OVERRIDE_VAR = "AI_VIDEO_DIARY_DOTENV"


def _configure_env() -> None:
    """Ensure the repo root is on sys.path and .env is loaded before imports.

    When ``AI_VIDEO_DIARY_DOTENV`` is set (e.g. by the Electron wrapper)
    that path is used instead of the default repo-root ``.env``.
    """
    root = _freeze_root()

    # Make sure apps/api/ is importable
    api_dir = root / "apps" / "api"
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    # Resolve .env path: honour override, fall back to repo root
    override = os.environ.get(_ENV_FILE_OVERRIDE_VAR)
    if override:
        env_path = Path(override)
    else:
        env_path = root / ".env"

    if env_path.exists():
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _exec_granted_command(arguments: list[str]) -> None:
    """Wait for the parent's private registration ACK, then replace this group leader.

    This fixed entry works in the frozen executable without a Python interpreter
    or importing the API's startup/recovery code.
    """
    if len(arguments) < 2:
        raise SystemExit(2)
    try:
        gate = int(arguments[0])
    except ValueError:
        raise SystemExit(2) from None
    if gate < 0:
        raise SystemExit(2)
    try:
        approved = os.read(gate, 1)
    finally:
        os.close(gate)
    if approved != b"G":
        raise SystemExit(126)
    try:
        os.execvp(arguments[1], arguments[1:])
    except OSError as exc:
        print(f"exec failed: {exc}", file=sys.stderr)
        raise SystemExit(127) from exc


def main() -> None:
    if sys.argv[1:2] == ["--grant-exec"]:
        _exec_granted_command(sys.argv[2:])
        return
    if sys.argv[1:]:
        raise SystemExit("api-server accepts only its fixed --grant-exec child entry")
    _configure_env()

    import uvicorn

    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,  # never auto-reload in a frozen build
    )


if __name__ == "__main__":
    main()
