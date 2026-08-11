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


def main() -> None:
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
