"""Material-library path and exact copy-revision access, without provisioning."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sqlite3

from app.materials.contracts import MaterialAsset, MATERIAL_MEDIA_BY_SUFFIX
from app.media_link_import import _probe_file_media_type


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
        if not path.is_file() or path.suffix.lower() not in MATERIAL_MEDIA_BY_SUFFIX:
            raise ValueError("素材文件不存在或格式不支持")
        return path

    def material_file_intact(self, asset: MaterialAsset) -> bool:
        """原文件是否仍完整可读——大小相等只是其中一条。

        登记时非空，这里同样要求非空；还要是普通文件，并且真实格式仍能被本地
        工具读出来、且与登记时的类型一致。只做格式判定，不做内容分析。
        """
        path = Path(asset.path)
        try:
            if path.is_symlink() or not path.is_file():
                return False
            size = path.stat().st_size
        except OSError:
            return False
        if size == 0 or size != asset.size_bytes:
            return False
        try:
            return _probe_file_media_type(path) == asset.media_type
        except OSError:
            return False
