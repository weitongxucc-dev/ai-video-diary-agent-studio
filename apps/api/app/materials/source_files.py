"""Material-library path and exact copy-revision access, without provisioning."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sqlite3

from app.video import SUPPORTED_VIDEO_EXTENSIONS


class MaterialSourceFileAccess:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], video_library_path: Path) -> None:
        self._connect = connection_factory
        self.video_library_path = video_library_path

    def get_material_ingestion_binding(self) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id = 1").fetchone()
        return (str(row["workspace_id"]), str(row["library_path"])) if row else None

    @staticmethod
    def material_copy_revision(stat: os.stat_result) -> dict[str, str]:
        """Decimal strings preserve nanosecond precision in desktop/browser JSON."""
        return {
            "size_bytes": str(stat.st_size), "mtime_ns": str(stat.st_mtime_ns),
            "ctime_ns": str(stat.st_ctime_ns), "inode": str(stat.st_ino),
        }

    def require_material_copy_confirmation(self, workspace_id: str, path: Path) -> None:
        if self.get_material_ingestion_binding() != (workspace_id, str(self.video_library_path.resolve())):
            raise ValueError("当前工作区未绑定此素材目录")
        revision = json.dumps(self.material_copy_revision(path.stat()), sort_keys=True)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT confirmed_revision FROM material_ingestion_files WHERE workspace_id=? AND file_path=?",
                (workspace_id, str(path)),
            ).fetchone()
        if row is None or row["confirmed_revision"] != revision:
            raise ValueError("请先确认此文件已复制完成，再开始接收或重试")

    def validated_library_file(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        try:
            path.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError("素材文件不存在或格式不支持")
        return path
