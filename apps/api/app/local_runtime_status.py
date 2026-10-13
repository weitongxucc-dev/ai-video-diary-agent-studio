from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def ffmpeg_available() -> bool:
    """The command exists on PATH; this does not verify a render."""
    return shutil.which("ffmpeg") is not None


def _pending_video_count(path: str | None) -> int:
    if not path:
        return 0
    inbox = Path(path).expanduser()
    if not inbox.exists() or not inbox.is_dir():
        return 0
    supported_extensions = {".mp4", ".mov", ".m4v"}
    return len(
        [
            item
            for item in inbox.iterdir()
            if item.is_file() and item.suffix.lower() in supported_extensions
        ]
    )


def _hermes_radar_status(path: str | None) -> dict[str, Any]:
    from app.hermes_radar import HermesRadarSourceClient

    status = HermesRadarSourceClient(path).status()
    return {
        "configured": status.configured,
        "exists": status.exists,
        "path": status.path,
        "latest_report": status.latest_report,
        "total_count": status.total_count,
        "product_hunt_count": status.product_hunt_count,
        "watcha_count": status.watcha_count,
    }


def observe_local_runtime_status(
    *,
    obsidian_vault_path: str | None,
    hermes_radar_path: str | None,
    video_inbox_path: str,
    video_output_path: str,
    database_path: str,
) -> dict[str, Any]:
    obsidian_exists = bool(
        obsidian_vault_path
        and Path(obsidian_vault_path).expanduser().exists()
        and Path(obsidian_vault_path).expanduser().is_dir()
    )
    video_inbox = Path(video_inbox_path).expanduser()
    video_output = Path(video_output_path).expanduser()
    database = Path(database_path).expanduser()
    return {
        "obsidian": {
            "configured": bool(obsidian_vault_path),
            "exists": obsidian_exists,
            "path": obsidian_vault_path,
            "markdown_count": None,
        },
        "hermes_radar": _hermes_radar_status(hermes_radar_path),
        "video": {
            "inbox_path": str(video_inbox),
            "output_path": str(video_output),
            "inbox_exists": video_inbox.exists() and video_inbox.is_dir(),
            "output_exists": video_output.exists() and video_output.is_dir(),
            "pending_count": _pending_video_count(str(video_inbox)),
            "ffmpeg_available": ffmpeg_available(),
        },
        "storage": {
            "database_path": str(database),
            "database_exists": database.exists(),
        },
    }
