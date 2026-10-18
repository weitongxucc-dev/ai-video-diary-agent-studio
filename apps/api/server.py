#!/usr/bin/env python3
"""Entry point for the API server packaged with PyInstaller.

Run with::

    ./api-server                 # from dist/
    python server.py             # dev
    uvicorn app.main:create_app --factory  # dev (raw FastAPI)
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


def _set_background_cpu_policy() -> None:
    """Set and read back Darwin process background state before any target exec."""
    import ctypes
    import errno

    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "Darwin background CPU scheduling is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.setpriority.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_int]
    libc.setpriority.restype = ctypes.c_int
    libc.getpriority.argtypes = [ctypes.c_int, ctypes.c_uint]
    libc.getpriority.restype = ctypes.c_int
    # macOS SDK sys/resource.h: PRIO_DARWIN_PROCESS=4, PRIO_DARWIN_BG=0x1000.
    ctypes.set_errno(0)
    if libc.setpriority(4, 0, 0x1000) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set Darwin background CPU scheduling")
    ctypes.set_errno(0)
    background = libc.getpriority(4, 0)
    error = ctypes.get_errno()
    if error or background != 1:
        raise OSError(error, "Darwin background CPU scheduling could not be verified")


def _exec_granted_command(arguments: list[str], *, background_cpu: bool = False) -> None:
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
    if background_cpu:
        try:
            _set_background_cpu_policy()
        except OSError as exc:
            print(f"GRANT_CPU_POLICY mode=darwin_background state=failed errno={exc.errno or 0}",
                  file=sys.stderr, flush=True)
            raise SystemExit(125) from exc
        print("GRANT_CPU_POLICY mode=darwin_background state=verified background=1 errno=0",
              file=sys.stderr, flush=True)
    try:
        os.execvp(arguments[1], arguments[1:])
    except OSError as exc:
        print(f"exec failed: {exc}", file=sys.stderr)
        raise SystemExit(127) from exc


def main() -> None:
    if sys.argv[1:2] == ["--grant-exec"]:
        _exec_granted_command(sys.argv[2:])
        return
    if sys.argv[1:2] == ["--grant-exec-background"]:
        _exec_granted_command(sys.argv[2:], background_cpu=True)
        return
    if sys.argv[1:2] == ["--cover-worker"]:
        from app.cover_generation import cover_worker_main
        raise SystemExit(cover_worker_main(sys.argv[2:]))
    if sys.argv[1:2] == ["--quality-worker"]:
        from app.quality_worker import main as quality_worker_main
        raise SystemExit(quality_worker_main(sys.argv[2:]))
    if sys.argv[1:]:
        raise SystemExit("api-server accepts only its fixed --grant-exec child entry")
    _configure_env()

    import uvicorn

    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))

    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
        reload=False,  # never auto-reload in a frozen build
    )


if __name__ == "__main__":
    main()
