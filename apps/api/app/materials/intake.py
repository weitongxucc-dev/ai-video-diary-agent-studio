"""Material discovery claims and explicit ingestion state transitions."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from app.materials.analysis import _material_analysis_public_error
from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MaterialAsset, material_media_type_for
from app.materials.source_files import MaterialSourceFileAccess
from app.materials.workspace_references import WorkspaceMediaReferenceAccess


class MaterialIntakeUseCase:
    def __init__(
        self, assets: MaterialAssetStore, source_files: MaterialSourceFileAccess, *,
        connection_factory: Callable[[], sqlite3.Connection],
        workspace_references: WorkspaceMediaReferenceAccess,
        upsert_discovered_asset: Callable[[str, Path], MaterialAsset],
    ) -> None:
        self._connect = connection_factory
        self.workspace_references = workspace_references
        self.assets = assets
        self.source_files = source_files
        self._upsert_discovered_asset = upsert_discovered_asset

    @property
    def video_library_path(self) -> Path:
        return self.source_files.video_library_path

    def receive_desktop_image(self, workspace_id, *, relative_path, expected_revision, owner_user_id):
        from PIL import Image

        if not owner_user_id:
            raise ValueError("导入图片需要当前工作区成员身份")
        relative = Path(relative_path)
        if relative.name != relative_path:
            raise ValueError("请选择素材目录内刚复制的图片文件")
        raw = self.video_library_path / relative
        if raw.is_symlink():
            raise ValueError("不能确认符号链接素材")
        path = self.source_files.validated_library_file(str(raw))
        stat = path.stat()
        if self.source_files.material_copy_revision(stat) != expected_revision:
            raise ValueError("图片文件修订已变化，请重新导入")
        try:
            with Image.open(path) as image:
                if image.format != {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}.get(path.suffix.lower()):
                    raise ValueError("图片内容与文件格式不符")
                image.verify()
        except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError("无法读取所选图片，请重新选择完整的 JPEG 或 PNG 文件") from exc
        asset_id = "asset_" + hashlib.sha256(f"{workspace_id}\0{path}".encode()).hexdigest()[:16]
        asset = MaterialAsset(id=asset_id, workspace_id=workspace_id, owner_user_id=owner_user_id,
            visibility="private", file_name=path.name, path=str(path), size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc), media_type="image",
            analysis_status="blocked", analysis_message="图片已入库，可用作人物参考。")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.source_files.confirm_material_copy_complete(workspace_id, relative_path=relative_path,
                expected_revision=expected_revision, confirmation_source="app_import",
                verified_owner_user_id=owner_user_id, connection=conn)
            saved = self.assets.save_received_asset(conn, asset, workspace_id=workspace_id, owner_user_id=owner_user_id)
            self.workspace_references.claim_asset_reference(conn, workspace_id, saved.id)

    def scan_library(self, workspace_id: str) -> list[MaterialAsset]:
        if (not self.source_files.material_library_available() or self.source_files.get_material_ingestion_binding()
                != (workspace_id, str(self.video_library_path.resolve()))):
            return []
        if not self.video_library_path.exists() or not self.video_library_path.is_dir():
            return []
        discovered: list[MaterialAsset] = []
        for path in sorted(self.video_library_path.rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or material_media_type_for(path) != "video"
                or "_已处理" in path.parts
            ):
                continue
            if not path.resolve().is_relative_to(self.video_library_path.resolve()):
                continue
            try:
                self.source_files.require_material_copy_confirmation(workspace_id, path.resolve())
            except ValueError:
                continue
            discovered.append(self._upsert_discovered_asset(workspace_id, path))
        return discovered

    def scan_material_ingestion(self, *, stable_seconds: float = 10.0) -> list[tuple[str, str]]:
        """Discover pending copies, then claim only explicitly confirmed revisions."""
        import time

        binding = self.source_files.get_material_ingestion_binding()
        if binding is None:
            return []
        workspace_id, bound_path = binding
        root = self.video_library_path
        if not self.source_files.material_library_available() or str(root.resolve()) != bound_path:
            self.mark_disk_missing(workspace_id)
            return []
        now = time.time()
        claimed: list[tuple[str, str]] = []
        for path in sorted(root.rglob("*")):
            if (path.is_symlink() or not path.is_file()
                    or material_media_type_for(path) != "video" or "_已处理" in path.parts):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                continue
            try:
                stat = resolved.stat()
            except FileNotFoundError:
                continue
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM material_ingestion_files WHERE workspace_id = ? AND file_path = ?",
                    (workspace_id, str(resolved)),
                ).fetchone()
                changed = row is None or (
                    row["size_bytes"], row["mtime_ns"], row["ctime_ns"], row["inode"]
                ) != (
                    stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
                )
                if changed:
                    conn.execute(
                        "INSERT INTO material_ingestion_files (workspace_id, file_path, size_bytes, mtime_ns, ctime_ns, inode, observed_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, 'observing') "
                        "ON CONFLICT(workspace_id, file_path) DO UPDATE SET size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, ctime_ns=excluded.ctime_ns, inode=excluded.inode, observed_at=excluded.observed_at, state='observing'",
                        (workspace_id, str(resolved), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, now),
                    )
                elif row["state"] in {"ready", "failed"}:
                    # Size, mtime, ctime and inode are the incremental boundary.
                    # Full content is hashed only on change, recovery, or retry.
                    continue
                elif row["confirmed_revision"] != json.dumps(self.source_files.material_copy_revision(stat), sort_keys=True):
                    continue
            if changed:
                try:
                    asset = self.assets.get_asset_by_path(workspace_id, str(resolved))
                except KeyError:
                    continue
                asset.analysis_status = "blocked"
                asset.analysis_message = "文件已变化；请确认复制完成后重新接收。"
                self.assets.save_asset(asset)
                continue
            try:
                with resolved.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                final_stat = resolved.stat()
                if (final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ctime_ns, final_stat.st_ino) != (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino):
                    continue
            except OSError:
                continue
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM material_ingestion_files WHERE workspace_id = ? AND file_path = ?",
                    (workspace_id, str(resolved)),
                ).fetchone()
                if row is None or (
                    row["size_bytes"], row["mtime_ns"], row["ctime_ns"], row["inode"]
                ) != (
                    stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
                ):
                    continue
                if row["confirmed_revision"] != json.dumps(self.source_files.material_copy_revision(final_stat), sort_keys=True):
                    continue
                if row["completed_digest"] == digest:
                    if row["state"] != "ready":
                        conn.execute("UPDATE material_ingestion_files SET state='ready' WHERE workspace_id=? AND file_path=?", (workspace_id, str(resolved)))
                    completed = True
                    failed_before = False
                elif row["state"] == "failed" and row["attempted_digest"] == digest:
                    completed = False
                    failed_before = True
                else:
                    completed = False
                    failed_before = False
                    # An interrupted process leaves 'running'; the next process can reclaim it.
                    conn.execute(
                        "UPDATE material_ingestion_files SET attempted_digest=?, state='running' WHERE workspace_id=? AND file_path=?",
                        (digest, workspace_id, str(resolved)),
                    )
            if completed:
                try:
                    asset = self.assets.get_asset_by_path(workspace_id, str(resolved))
                except KeyError:
                    continue
                if asset.analysis_status == "blocked":
                    asset.analysis_status = "ready"
                    asset.analysis_message = "分析已完成；外接盘已恢复。"
                    self.assets.save_asset(asset)
                continue
            if failed_before:
                continue
            try:
                asset = self._upsert_discovered_asset(workspace_id, resolved)
            except FileNotFoundError:
                continue
            if asset.segments or asset.analysis_status in {"ready", "failed"}:
                self.assets.reset_changed_material_asset(asset)
            else:
                asset.analysis_status = "pending"
                asset.analysis_message = "复制完成确认已保存，正在分析；确认不代表文件通过校验。"
                self.assets.save_asset(asset)
            claimed.append((asset.id, digest))
        return claimed

    def finish_material_ingestion(self, workspace_id: str, asset_id: str, digest: str, *, failed: bool) -> None:
        asset = self.assets.get_asset(workspace_id, asset_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE material_ingestion_files SET state=?, completed_digest=CASE WHEN ? THEN completed_digest ELSE ? END "
                "WHERE workspace_id=? AND file_path=? AND attempted_digest=?",
                ("failed" if failed else "ready", int(failed), digest, workspace_id, asset.path, digest),
            )

    def fail_material_ingestion(self, workspace_id: str, asset_id: str, digest: str, reason: Exception) -> None:
        asset = self.assets.get_asset(workspace_id, asset_id)
        asset.analysis_status = "failed"
        asset.analysis_message = f"素材分析失败：{_material_analysis_public_error(reason)}。检查后可手动重试。"
        self.assets.save_asset(asset)
        self.finish_material_ingestion(workspace_id, asset_id, digest, failed=True)

    def retry_material_ingestion(self, workspace_id: str, asset_id: str) -> None:
        asset = self.assets.get_asset(workspace_id, asset_id)
        binding = self.source_files.get_material_ingestion_binding()
        if binding is None or binding[0] != workspace_id:
            raise ValueError("当前工作区未绑定素材目录")
        self.source_files.require_material_copy_confirmation(workspace_id, self.source_files.validated_library_file(asset.path))
        with self._connect() as conn:
            conn.execute("UPDATE material_ingestion_files SET state='observing', attempted_digest=NULL, completed_digest=NULL WHERE workspace_id=? AND file_path=?", (workspace_id, asset.path))
        asset.analysis_status = "pending"
        asset.analysis_message = "已请求重试，将按已确认的文件修订重新分析。"
        self.assets.save_asset(asset)

    def mark_disk_missing(self, workspace_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE material_ingestion_files SET state='blocked_disk' WHERE workspace_id=? AND state='ready'",
                (workspace_id,),
            )
            rows = self.assets._disk_missing_asset_rows(conn, workspace_id)
        self.assets.mark_disk_missing_assets(rows)
