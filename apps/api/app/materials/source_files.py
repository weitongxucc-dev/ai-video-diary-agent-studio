"""Material-library binding, copy confirmations and source-file access."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from app.materials.contracts import MaterialAsset, MATERIAL_MEDIA_BY_SUFFIX
from app.media_link_import import _probe_file_media_type

from app.models import utc_now


def directory_accepts_writes(root: Path) -> bool:
    """Prove the directory accepts writes, because existence alone never promised that.

    A read-only mount still lists as a normal directory, so binding to one
    registers an intake that can never accept a file and hides the cause until
    a later import fails, and a first-run preflight would report the local video
    pipeline as ready. Creating and removing a hidden uniquely named file is the
    same proof the desktop import path already relies on, and every scanner that
    walks these directories skips anything without a supported media suffix.
    """
    probe = root / f".chatcut_write_probe_{uuid4().hex}"
    try:
        probe.touch(exist_ok=False)
        probe.unlink()
    except OSError:
        return False
    return True


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

    def bind_material_ingestion(
        self,
        workspace_id: str,
        *,
        expected_directory: str,
        verified_owner_user_id: str | None = None,
    ) -> None:
        """Bind the configured inbox to one authenticated workspace, never to the current login."""
        if not workspace_id.strip():
            raise ValueError("必须先选择已验证的工作区")
        if not expected_directory.strip() or not Path(expected_directory).expanduser().is_absolute():
            raise ValueError("请先确认当前素材目录")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            status = self.material_library_status()
            if status == "read_only":
                raise ValueError("素材目录当前不可写；请检查磁盘挂载方式或目录权限，或在设置中改选一个可写目录")
            if status != "ready":
                raise ValueError("素材目录不可用；请连接外接盘并重新选择目录")
            root_path = str(self.video_library_path.resolve())
            expected_path = str(Path(expected_directory).expanduser().resolve())
            if expected_path != root_path:
                raise ValueError("素材目录配置已变化，请刷新后重新确认")
            row = conn.execute(
                "SELECT workspace_id, library_path, intake_owner_user_id FROM material_ingestion_binding WHERE id = 1"
            ).fetchone()
            if row is not None and (row["workspace_id"], row["library_path"]) != (workspace_id, root_path):
                raise ValueError("素材目录已绑定另一工作区或目录；请先明确解除原绑定")
            # 已绑定同一目录时保留当时绑定者的身份，不因再次确认而改写归属。
            intake_owner = (row["intake_owner_user_id"] if row is not None
                            else str(verified_owner_user_id or "").strip() or None)
            conn.execute(
                "INSERT OR REPLACE INTO material_ingestion_binding "
                "(id, workspace_id, library_path, intake_owner_user_id) VALUES (1, ?, ?, ?)",
                (workspace_id, root_path, intake_owner),
            )

    def get_material_ingestion_owner(self) -> str | None:
        """历史目录绑定者；不用于推断文件上传者。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT intake_owner_user_id FROM material_ingestion_binding WHERE id = 1"
            ).fetchone()
        if row is None or "intake_owner_user_id" not in row.keys():
            return None
        return str(row["intake_owner_user_id"] or "").strip() or None

    def confirm_material_copy_complete(
        self, workspace_id: str, *, relative_path: str,
        expected_revision: dict[str, str], confirmation_source: Literal["manual", "app_import"],
        verified_owner_user_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Persist a copy declaration for this bound file revision, not media validity."""
        import time

        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or "_已处理" in relative.parts:
            raise ValueError("请确认当前绑定目录内的素材文件")
        raw_path = self.video_library_path / relative
        if raw_path.is_symlink():
            raise ValueError("不能确认符号链接素材")
        if confirmation_source not in {"manual", "app_import"}:
            raise ValueError("复制完成确认来源无效")
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            elif not conn.in_transaction:
                raise ValueError("接收确认必须加入调用方已开始的事务")
            binding = conn.execute("SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1").fetchone()
            if (binding is None or (binding["workspace_id"], binding["library_path"])
                    != (workspace_id, str(self.video_library_path.resolve()))):
                raise ValueError("当前工作区未绑定此素材目录")
            if not self.material_library_available():
                raise ValueError("素材目录当前不可用，请连接原目录后重试")
            path = self.validated_library_file(str(raw_path))
            stat = path.stat()
            revision = self.material_copy_revision(stat)
            if revision != expected_revision:
                raise ValueError("文件修订已变化，请重新检查并确认复制完成")
            conn.execute(
                """INSERT INTO material_ingestion_files
                   (workspace_id, file_path, size_bytes, mtime_ns, ctime_ns, inode,
                    observed_at, state, confirmed_revision, confirmation_source, copy_confirmed_at,
                    copy_uploader_user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'observing', ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                     size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
                     ctime_ns=excluded.ctime_ns, inode=excluded.inode, observed_at=excluded.observed_at,
                     state=CASE WHEN confirmed_revision=excluded.confirmed_revision THEN state ELSE 'observing' END,
                     copy_uploader_user_id=CASE WHEN confirmed_revision=excluded.confirmed_revision
                       THEN copy_uploader_user_id ELSE excluded.copy_uploader_user_id END,
                     confirmed_revision=excluded.confirmed_revision,
                     confirmation_source=excluded.confirmation_source,
                     copy_confirmed_at=excluded.copy_confirmed_at""",
                (workspace_id, str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                 stat.st_ino, time.time(), json.dumps(revision, sort_keys=True),
                 confirmation_source, utc_now().isoformat(),
                 str(verified_owner_user_id or "").strip() or None),
            )

    def pending_material_copies(self, workspace_id: str) -> list[dict[str, object]]:
        if (not self.material_library_available() or self.get_material_ingestion_binding()
                != (workspace_id, str(self.video_library_path.resolve()))):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, confirmed_revision FROM material_ingestion_files WHERE workspace_id=? ORDER BY file_path",
                (workspace_id,),
            ).fetchall()
        pending = []
        for row in rows:
            path = Path(row["file_path"])
            if (not path.resolve().is_relative_to(self.video_library_path.resolve())
                    or not path.is_file() or path.is_symlink()):
                continue
            revision = self.material_copy_revision(path.stat())
            if row["confirmed_revision"] != json.dumps(revision, sort_keys=True):
                pending.append({"relative_path": str(path.relative_to(self.video_library_path.resolve())),
                                "expected_revision": revision})
        return pending

    def material_ingestion_directory_state(self, workspace_id: str) -> dict[str, object]:
        """Describe the configured directory without revealing another workspace's binding."""
        if not workspace_id.strip():
            raise ValueError("必须先选择已验证的工作区")
        binding = self.get_material_ingestion_binding()
        current_path = str(self.video_library_path)
        resolved_path = str(self.video_library_path.resolve())
        foreign_binding = binding is not None and binding[0] != workspace_id
        return {
            "directory": None if foreign_binding else current_path,
            "status": self.material_library_status(),
            "bound_to_current_workspace": binding == (workspace_id, resolved_path),
            "bound_to_another_workspace": foreign_binding,
            "configured_path_changed": binding is not None and binding[1] != resolved_path,
        }

    def material_ingestion_state_counts(self, workspace_id: str) -> dict[str, int]:
        """Return intake totals for one workspace without exposing file paths."""
        counts = {state: 0 for state in ("observing", "running", "ready", "failed", "blocked_disk")}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS total FROM material_ingestion_files "
                "WHERE workspace_id=? GROUP BY state",
                (workspace_id,),
            ).fetchall()
        for row in rows:
            state = str(row["state"])
            if state in counts:
                counts[state] = int(row["total"])
        return counts

    def material_library_available(self) -> bool:
        return self.material_library_status() == "ready"

    def material_library_status(self) -> Literal["missing", "unmounted", "invalid", "read_only", "ready"]:
        root = self.video_library_path
        if not root.is_absolute() or root.is_symlink():
            return "invalid"
        # A leftover /Volumes/<name> directory is not evidence that its disk is mounted.
        if len(root.parts) >= 3 and root.parts[1] == "Volumes":
            volume = Path(*root.parts[:3])
            if not volume.is_mount():
                return "unmounted"
        if not root.exists():
            return "missing"
        if not root.is_dir():
            return "invalid"
        if not directory_accepts_writes(root):
            return "read_only"
        return "ready"

    def material_link_target(self, workspace_id: str) -> Path:
        """Return the intake directory a direct link may write into, or refuse.

        Direct-link import reuses the existing binding and workspace checks; it
        never creates its own directory or its own authorization path.
        """
        state = self.material_ingestion_directory_state(workspace_id)
        if state["bound_to_another_workspace"]:
            raise ValueError("素材目录已关联其他工作区；当前工作区不能写入。")
        if state["configured_path_changed"]:
            raise ValueError("当前设置的素材目录与本工作区原绑定目录不同；请先处理原绑定。")
        if not state["bound_to_current_workspace"]:
            raise ValueError("请先把素材目录关联到当前工作区，再导入链接。")
        if state["status"] != "ready":
            raise ValueError("素材目录当前不可写；请检查磁盘挂载方式或目录权限。")
        return self.video_library_path
