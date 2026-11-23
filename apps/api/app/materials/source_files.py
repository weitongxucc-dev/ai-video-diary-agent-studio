"""Material-library binding, copy confirmations and source-file access."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from app.materials.contracts import (
    MATERIAL_MEDIA_BY_SUFFIX,
    MaterialAsset,
    MaterialSegment,
    material_usage_eligibility,
)
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


def material_segment_eligible(
    asset: MaterialAsset, segment: MaterialSegment, workspace_id: str, *, automation_only: bool = False,
) -> bool:
    """Check metadata and source identity before decoding under a device grant."""
    purpose = "automatic" if automation_only else "manual"
    if (asset.workspace_id != workspace_id
            or not material_usage_eligibility(asset, segment, purpose=purpose).eligible
            or asset.media_type != "video"
            or asset.duration_seconds is None or not math.isfinite(asset.duration_seconds)
            or not (math.isfinite(segment.start_seconds) and math.isfinite(segment.end_seconds)
                    and 0 <= segment.start_seconds < segment.end_seconds <= asset.duration_seconds)):
        return False
    path = Path(asset.path)
    try:
        stat = path.stat()
        return (path.is_file() and stat.st_size > 0 and stat.st_size == asset.size_bytes
                and abs(stat.st_mtime - asset.modified_at.timestamp()) < 0.001)
    except OSError:
        return False


def automatic_segment_eligible(asset: MaterialAsset, segment: MaterialSegment, workspace_id: str) -> bool:
    return material_segment_eligible(asset, segment, workspace_id, automation_only=True)


class MaterialSourceFileAccess:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], video_library_path: Path) -> None:
        self._connect = connection_factory
        self.video_library_path = video_library_path

    def get_material_ingestion_binding(self) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id = 1").fetchone()
        return (str(row["workspace_id"]), str(row["library_path"])) if row else None

    @staticmethod
    def _rebind_digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    @staticmethod
    def _rebind_revision(path: Path) -> dict[str, int]:
        stat = path.stat()
        return {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "inode": stat.st_ino,
            "device": stat.st_dev,
        }

    @staticmethod
    def _assert_rebind_root(root: Path, *, label: str, writable: bool = True) -> Path:
        root = root.expanduser()
        if not root.is_absolute() or root.is_symlink():
            raise ValueError(f"{label}必须是绝对路径且不能是符号链接")
        if len(root.parts) >= 3 and root.parts[1] == "Volumes":
            volume = Path(*root.parts[:3])
            if not volume.is_mount():
                raise ValueError(f"{label}所在外接盘未挂载")
        if not root.exists() or not root.is_dir():
            raise ValueError(f"{label}不存在或不是目录")
        if writable and not directory_accepts_writes(root):
            raise ValueError(f"{label}不可写")
        return root.resolve()

    def _build_rebind_manifest(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        old_root: str | Path,
        new_root: str | Path,
    ) -> dict[str, object]:
        old = self._assert_rebind_root(Path(old_root), label="原素材目录", writable=False)
        new = self._assert_rebind_root(Path(new_root), label="新素材目录")
        if old == new:
            raise ValueError("新旧素材目录不能相同")
        binding = conn.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()
        if binding is None or (str(binding["workspace_id"]), str(binding["library_path"])) != (workspace_id, str(old)):
            raise ValueError("原素材目录未绑定到当前工作区")

        rows: dict[str, dict[str, object]] = {}
        assets = conn.execute(
            "SELECT id, file_path, payload FROM material_assets WHERE workspace_id=? ORDER BY file_path",
            (workspace_id,),
        ).fetchall()
        ingestion = conn.execute(
            "SELECT file_path, size_bytes, mtime_ns, ctime_ns, inode FROM material_ingestion_files WHERE workspace_id=? ORDER BY file_path",
            (workspace_id,),
        ).fetchall()
        for row in [*assets, *ingestion]:
            raw = Path(str(row["file_path"])).expanduser().resolve()
            try:
                relative = raw.relative_to(old)
            except ValueError as exc:
                raise ValueError("素材登记路径不在原素材目录内") from exc
            key = relative.as_posix()
            item = rows.setdefault(key, {"relative_path": key, "asset_ids": []})
            if "id" in row.keys():
                item["asset_ids"].append(str(row["id"]))

        entries: list[dict[str, object]] = []
        for key in sorted(rows):
            item = rows[key]
            source = old.joinpath(*key.split("/"))
            target = new.joinpath(*key.split("/"))
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"原素材文件缺失：{key}")
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"新素材文件缺失：{key}")
            try:
                source_revision = self._rebind_revision(source)
                target_revision = self._rebind_revision(target)
                source_sha256 = self._file_sha256(source)
                target_sha256 = self._file_sha256(target)
                if (self._rebind_revision(source) != source_revision
                        or self._rebind_revision(target) != target_revision):
                    raise ValueError(f"素材在校验期间发生变化：{key}")
            except OSError as exc:
                raise ValueError(f"素材在校验期间不可用：{key}") from exc
            if source_revision["size_bytes"] != target_revision["size_bytes"] or source_sha256 != target_sha256:
                raise ValueError(f"素材摘要不一致：{key}")
            entries.append({
                **item,
                "old_path": str(source),
                "new_path": str(target),
                "size_bytes": source_revision["size_bytes"],
                "sha256": source_sha256,
                "old_revision": source_revision,
                "new_revision": target_revision,
            })
        return {
            "format": "material-rebind/v1",
            "workspace_id": workspace_id,
            "old_root": str(old),
            "new_root": str(new),
            "entries": entries,
        }

    def prepare_material_rebind(
        self,
        workspace_id: str,
        *,
        old_directory: str,
        new_directory: str,
        operation_id: str,
    ) -> dict[str, object]:
        if not operation_id or len(operation_id) > 128 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in operation_id):
            raise ValueError("迁移操作编号无效")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT manifest_sha256, manifest_json, workspace_id, status FROM material_rebind_receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing is not None and str(existing["workspace_id"]) != workspace_id:
                raise ValueError("迁移操作编号已用于另一工作区")
            if existing is not None and str(existing["status"]) == "applied":
                saved = json.loads(str(existing["manifest_json"]))
                return {"operation_id": operation_id, "manifest_sha256": str(existing["manifest_sha256"]), **saved, "status": "applied"}
            manifest = self._build_rebind_manifest(conn, workspace_id, old_directory, new_directory)
            digest = self._rebind_digest(manifest)
            if existing is not None:
                if str(existing["manifest_sha256"]) != digest:
                    raise ValueError("迁移操作编号已用于另一份清单")
                return {"operation_id": operation_id, "manifest_sha256": digest, **manifest, "status": str(existing["status"])}
            conn.execute(
                "INSERT INTO material_rebind_receipts(operation_id,workspace_id,old_root,new_root,manifest_sha256,manifest_json,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (operation_id, workspace_id, manifest["old_root"], manifest["new_root"], digest,
                 json.dumps(manifest, ensure_ascii=False, sort_keys=True), "prepared", utc_now().isoformat()),
            )
            return {"operation_id": operation_id, "manifest_sha256": digest, **manifest, "status": "prepared"}

    @staticmethod
    def _rewrite_payload(value: object, path_map: dict[str, tuple[str, dict[str, int], dict[str, int]]], asset_map: dict[str, tuple[dict[str, int], dict[str, int]]]) -> object:
        if isinstance(value, dict):
            asset_id = value.get("asset_id")
            if isinstance(asset_id, str) and asset_id in asset_map and isinstance(value.get("file_signature"), dict):
                value["file_signature"] = {
                    key: str(asset_map[asset_id][1][key])
                    for key in ("size_bytes", "mtime_ns", "ctime_ns", "inode")
                }
            for key, child in list(value.items()):
                if isinstance(child, str) and child in path_map:
                    value[key] = path_map[child][0]
                else:
                    value[key] = MaterialSourceFileAccess._rewrite_payload(child, path_map, asset_map)
            return value
        if isinstance(value, list):
            return [MaterialSourceFileAccess._rewrite_payload(item, path_map, asset_map) for item in value]
        return value

    def commit_material_rebind(self, workspace_id: str, *, operation_id: str, manifest_sha256: str) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute(
                "SELECT * FROM material_rebind_receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if receipt is None or str(receipt["workspace_id"]) != workspace_id:
                raise ValueError("迁移准备清单不存在")
            if str(receipt["manifest_sha256"]) != manifest_sha256:
                raise ValueError("迁移清单摘要不匹配")
            if receipt["status"] == "applied":
                return json.loads(str(receipt["manifest_json"])) | {
                    "operation_id": operation_id, "manifest_sha256": manifest_sha256, "status": "applied",
                }
            saved_manifest = json.loads(str(receipt["manifest_json"]))
            current_manifest = self._build_rebind_manifest(
                conn, workspace_id, saved_manifest["old_root"], saved_manifest["new_root"]
            )
            if self._rebind_digest(current_manifest) != manifest_sha256:
                raise ValueError("迁移源文件或目标文件在提交前已变化")

            path_map = {
                str(item["old_path"]): (str(item["new_path"]), item["old_revision"], item["new_revision"])
                for item in current_manifest["entries"]
            }
            asset_map: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
            for item in current_manifest["entries"]:
                for asset_id in item.get("asset_ids", []):
                    asset_map[str(asset_id)] = (item["old_revision"], item["new_revision"])

            # Reject target rows that would collide with an unrelated existing record.
            target_paths = [str(item["new_path"]) for item in current_manifest["entries"]]
            if target_paths:
                placeholders = ",".join("?" for _ in target_paths)
                for table in ("material_assets", "material_ingestion_files"):
                    rows = conn.execute(
                        f"SELECT file_path FROM {table} WHERE workspace_id=? AND file_path IN ({placeholders})",
                        (workspace_id, *target_paths),
                    ).fetchall()
                    allowed = set(target_paths)
                    old_paths = {str(item["old_path"]) for item in current_manifest["entries"]}
                    if any(str(row["file_path"]) in allowed and str(row["file_path"]) not in old_paths for row in rows):
                        raise ValueError("新素材目录已有冲突登记")

            for item in current_manifest["entries"]:
                old_path, new_path = str(item["old_path"]), str(item["new_path"])
                asset_rows = conn.execute(
                    "SELECT id, payload FROM material_assets WHERE workspace_id=? AND file_path=?",
                    (workspace_id, old_path),
                ).fetchall()
                for row in asset_rows:
                    payload = json.loads(str(row["payload"]))
                    payload = self._rewrite_payload(payload, {old_path: (new_path, item["old_revision"], item["new_revision"])}, {
                        str(row["id"]): (item["old_revision"], item["new_revision"]),
                    })
                    payload["path"] = new_path
                    payload["modified_at"] = datetime.fromtimestamp(item["new_revision"]["mtime_ns"] / 1e9, tz=timezone.utc).isoformat()
                    conn.execute(
                        "UPDATE material_assets SET file_path=?, payload=? WHERE workspace_id=? AND id=?",
                        (new_path, json.dumps(payload, ensure_ascii=False), workspace_id, row["id"]),
                    )
                columns = {str(column[1]) for column in conn.execute("PRAGMA table_info(material_ingestion_files)")}
                updates = ["file_path=?", "size_bytes=?", "mtime_ns=?", "ctime_ns=?", "inode=?"]
                values: list[object] = [new_path, item["new_revision"]["size_bytes"], item["new_revision"]["mtime_ns"], item["new_revision"]["ctime_ns"], item["new_revision"]["inode"]]
                if "confirmed_revision" in columns:
                    updates.append("confirmed_revision=?")
                    values.append(json.dumps(self.material_copy_revision(Path(new_path).stat()), sort_keys=True))
                if "provisional_revision" in columns:
                    updates.extend(["provisional_revision=NULL", "attempted_digest=NULL"])
                conn.execute(
                    f"UPDATE material_ingestion_files SET {', '.join(updates)} WHERE workspace_id=? AND file_path=?",
                    (*values, workspace_id, old_path),
                )

            # Current creation plans carry the source signature used by resumed
            # source-video work. Historical TimelineVersion rows remain immutable
            # history, while mutable plans follow the verified destination inode.
            for table in ("creation_plans", "creation_tasks"):
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists is None:
                    continue
                rows = conn.execute(f"SELECT rowid, payload FROM {table} WHERE workspace_id=?", (workspace_id,)).fetchall()
                for row in rows:
                    payload = json.loads(str(row["payload"]))
                    before_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    rewritten = self._rewrite_payload(payload, path_map, asset_map)
                    if json.dumps(rewritten, ensure_ascii=False, sort_keys=True) != before_payload:
                        conn.execute(f"UPDATE {table} SET payload=? WHERE rowid=?", (json.dumps(rewritten, ensure_ascii=False), row["rowid"]))

            binding_update = conn.execute(
                "UPDATE material_ingestion_binding SET library_path=? WHERE id=1 AND workspace_id=? AND library_path=?",
                (current_manifest["new_root"], workspace_id, current_manifest["old_root"]),
            )
            if binding_update.rowcount != 1:
                raise ValueError("素材目录绑定在提交前已变化")
            result = {
                "format": current_manifest["format"], "workspace_id": workspace_id,
                "old_root": current_manifest["old_root"], "new_root": current_manifest["new_root"],
                "entry_count": len(current_manifest["entries"]), "manifest_sha256": manifest_sha256,
                "operation_id": operation_id, "status": "applied", "applied_at": utc_now().isoformat(),
            }
            conn.execute(
                "UPDATE material_rebind_receipts SET status='applied', applied_at=?, manifest_json=? WHERE operation_id=?",
                (result["applied_at"], json.dumps(result, ensure_ascii=False, sort_keys=True), operation_id),
            )
            return result

    def material_rebind_receipt(self, workspace_id: str, operation_id: str) -> dict[str, object]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM material_rebind_receipts WHERE workspace_id=? AND operation_id=?",
                (workspace_id, operation_id),
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return json.loads(str(row["manifest_json"])) | {
            "operation_id": operation_id, "manifest_sha256": str(row["manifest_sha256"]),
            "status": str(row["status"]), "applied_at": row["applied_at"],
        }

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
