"""Whole-file mobile intake: verified bytes first, one owner transaction on completion.

Identity and storage are explicit trusted dependencies. Receiving is not analysis
or creation. Interrupted content restarts from byte zero using the same request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID, uuid4

import psutil

from app.materials.contracts import MATERIAL_MEDIA_LABEL, MaterialAsset, material_media_type_for
from app.materials.source_files import MaterialSourceFileAccess
from app.media_link_import import DEFAULT_MAX_BYTES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MobileTransferActor:
    """Constructed by the formal authentication dependency, never from JSON."""
    workspace_id: str
    member_id: str
    owner_user_id: str


class MobileTransferError(ValueError):
    def __init__(self, code, message, *, status=409, retryable=False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        super().__init__(message)

    def payload(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


_MIME_SUFFIXES = {
    ".mp4": {"video/mp4"}, ".m4v": {"video/mp4", "video/x-m4v"}, ".mov": {"video/quicktime"},
    ".webm": {"video/webm"}, ".mp3": {"audio/mpeg"}, ".m4a": {"audio/mp4", "audio/x-m4a"},
    ".aac": {"audio/aac"}, ".wav": {"audio/wav", "audio/x-wav"},
    ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"}, ".png": {"image/png"},
    ".webp": {"image/webp"}, ".gif": {"image/gif"},
}


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _revision(path):
    return MaterialSourceFileAccess.material_copy_revision(path.stat())


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class MobileTransfers:
    def __init__(self, connection_factory, *, assets, workspace_references, target_for,
                 confirm_copy_in_transaction, min_free_disk_bytes: int, max_bytes: int = DEFAULT_MAX_BYTES):
        if type(min_free_disk_bytes) is not int or min_free_disk_bytes < 0 or type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("接收上限与既有磁盘余量必须明确提供")
        self._connect, self.assets, self.references = connection_factory, assets, workspace_references
        self.target_for, self.confirm_copy = target_for, confirm_copy_in_transaction
        self.min_free_disk_bytes, self.max_bytes = min_free_disk_bytes, max_bytes
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS mobile_transfers (
                workspace_id TEXT NOT NULL, member_id TEXT NOT NULL, request_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL, manifest_json TEXT NOT NULL, state TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 0, writer_json TEXT, temporary_name TEXT,
                receipt_json TEXT, asset_id TEXT, error_json TEXT,
                root_path TEXT NOT NULL, root_device INTEGER NOT NULL, root_inode INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,member_id,request_id))""")

    @staticmethod
    def _key(actor, request_id):
        if not isinstance(actor, MobileTransferActor) or not all((actor.workspace_id, actor.member_id, actor.owner_user_id)):
            raise MobileTransferError("identity_required", "需要当前已验证的主机成员和工作区。", status=403)
        try:
            request_id = str(UUID(str(request_id)))
        except ValueError:
            raise MobileTransferError("invalid_request_id", "接收请求必须使用稳定 UUID。", status=422) from None
        return actor.workspace_id, actor.member_id, request_id

    def _manifest(self, filename, mime_type, byte_length, sha256, description):
        if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
                or filename in {".", ".."} or any(value in filename for value in ("/", "\\", "\x00", "\r", "\n"))):
            raise MobileTransferError("invalid_filename", "文件名只能是单个可显示的文件名。", status=422)
        suffix = Path(filename).suffix.lower()
        if mime_type not in _MIME_SUFFIXES.get(suffix, set()) or material_media_type_for(Path(filename)) is None:
            raise MobileTransferError("unsupported_media", "文件类型、后缀与声明 MIME 不一致或尚不支持。", status=422)
        if type(byte_length) is not int or not 0 < byte_length <= self.max_bytes:
            raise MobileTransferError("invalid_length", "文件长度为空或超出已有素材接收上限。", status=413)
        if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
            raise MobileTransferError("invalid_sha256", "需要完整文件的 SHA-256。", status=422)
        if not isinstance(description, str):
            raise MobileTransferError("invalid_description", "素材说明必须是文字。", status=422)
        return {"filename": filename, "mime_type": mime_type, "byte_length": byte_length,
                "sha256": sha256.lower(), "description": description.strip()}

    def _target(self, workspace_id):
        try:
            path = Path(self.target_for(workspace_id)).resolve(strict=True)
            if not path.is_dir():
                raise OSError("target is not a directory")
            return path
        except (OSError, ValueError) as exc:
            raise MobileTransferError("storage_unavailable", "原工作区素材目录不可用，请先恢复原目录。", status=503, retryable=True) from exc

    def _root(self, row):
        root = self._target(row["workspace_id"])
        stat = root.stat()
        if (str(root), stat.st_dev, stat.st_ino) != (row["root_path"], row["root_device"], row["root_inode"]):
            raise MobileTransferError("storage_changed", "接收目录已变化，未切换到其他位置。", status=503, retryable=True)
        return root

    def _stage(self, row):
        root = self._root(row)
        stage = root / ".mobile-transfers"
        if stage.is_symlink():
            raise MobileTransferError("storage_changed", "接收暂存目录已被替换。", status=503)
        stage.mkdir(mode=0o700, exist_ok=True)
        return stage

    def _row(self, conn, actor, request_id):
        key = self._key(actor, request_id)
        row = conn.execute("SELECT * FROM mobile_transfers WHERE workspace_id=? AND member_id=? AND request_id=?", key).fetchone()
        if row is None or row["owner_user_id"] != actor.owner_user_id:
            raise MobileTransferError("not_found", "当前成员没有此接收请求。", status=404)
        return dict(row)

    @staticmethod
    def _view(row):
        manifest = json.loads(row["manifest_json"])
        received = manifest["byte_length"] if row["state"] in {"received", "completed"} else 0
        return {"request_id": row["request_id"], "workspace_id": row["workspace_id"], "member_id": row["member_id"],
            **manifest, "state": row["state"], "received_bytes": received,
            "restart_from_zero": row["state"] in {"declared", "receiving", "failed"},
            "asset_id": row["asset_id"],
            "asset_ref": json.loads(row["receipt_json"]).get("asset_ref") if row["state"] == "completed" else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None}

    def declare(self, actor, request_id, *, filename, mime_type, byte_length, sha256, description=""):
        key = self._key(actor, request_id)
        manifest = self._manifest(filename, mime_type, byte_length, sha256, description)
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM mobile_transfers WHERE workspace_id=? AND member_id=? AND request_id=?", key).fetchone()
            if existing is not None:
                row = self._row(conn, actor, request_id)
                if json.loads(row["manifest_json"]) != manifest:
                    raise MobileTransferError("request_conflict", "原请求的文件清单不可更改，请为新文件创建新 UUID。")
                return self._view(row)
        root = self._target(actor.workspace_id)
        stat = root.stat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT OR IGNORE INTO mobile_transfers
                (workspace_id,member_id,request_id,owner_user_id,manifest_json,state,root_path,root_device,root_inode,updated_at)
                VALUES (?,?,?,?,?,'declared',?,?,?,?)""", (*key, actor.owner_user_id,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True), str(root), stat.st_dev, stat.st_ino, _stamp()))
            row = self._row(conn, actor, request_id)
            if json.loads(row["manifest_json"]) != manifest:
                raise MobileTransferError("request_conflict", "原请求清单不可更改。")
            return self._view(row)

    @staticmethod
    def _writer():
        process = psutil.Process(os.getpid())
        return {"pid": process.pid, "start": f"{process.create_time():.6f}", "boot": f"{psutil.boot_time():.6f}"}

    @staticmethod
    def _writer_alive(marker):
        try:
            if marker["boot"] != f"{psutil.boot_time():.6f}":
                return False
            return f"{psutil.Process(marker['pid']).create_time():.6f}" == marker["start"]
        except psutil.NoSuchProcess:
            return False
        except (psutil.Error, KeyError, TypeError):
            return None

    def query(self, actor, request_id):
        with self._connect() as conn:
            row = self._row(conn, actor, request_id)
        if row["state"] == "receiving" and self._writer_alive(json.loads(row["writer_json"])) is False:
            error = MobileTransferError("transfer_interrupted", "接收进程已结束，请沿原请求从零重发。", retryable=True)
            self._fail(actor, request_id, row["generation"], error)
            self._cleanup(row)
            with self._connect() as conn:
                row = self._row(conn, actor, request_id)
        if row["state"] in {"failed", "cancelled"}:
            self._cleanup(row)
        return self._view(row)

    def _fail(self, actor, request_id, generation, error):
        with self._connect() as conn:
            conn.execute("""UPDATE mobile_transfers SET state='failed',error_json=?,writer_json=NULL,updated_at=?
                WHERE workspace_id=? AND member_id=? AND request_id=? AND generation=? AND state='receiving'""",
                (json.dumps(error.payload(), ensure_ascii=False), _stamp(), *self._key(actor, request_id), generation))

    def _cleanup(self, row):
        if not row["temporary_name"]:
            return
        try:
            path = self._stage(row) / row["temporary_name"]
            if path.is_symlink():
                raise OSError("temporary path replaced")
            path.unlink(missing_ok=True)
        except (OSError, MobileTransferError):
            logger.warning("Mobile transfer temporary cleanup pending: %s", row["request_id"])

    async def receive_content(self, actor, request_id, chunks, *, content_length=None):
        self.query(actor, request_id)  # Recover only a proven-dead writer, never steal a live request.
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, actor, request_id)
            if row["state"] in {"received", "completed"}:
                return self._view(row)
            if row["state"] in {"receiving", "cancelled"}:
                raise MobileTransferError("transfer_busy" if row["state"] == "receiving" else "cancelled", "请求仍在接收或已取消，请先查询原状态。")
            manifest = json.loads(row["manifest_json"])
            if content_length is not None and content_length != manifest["byte_length"]:
                raise MobileTransferError("length_mismatch", "请求长度与原文件清单不一致。", status=422, retryable=True)
            generation = row["generation"] + 1
            temporary_name = f"{row['request_id']}-{generation}-{uuid4().hex}.part"
            conn.execute("""UPDATE mobile_transfers SET state='receiving',generation=?,writer_json=?,temporary_name=?,
                receipt_json=NULL,error_json=NULL,updated_at=? WHERE workspace_id=? AND member_id=? AND request_id=?""",
                (generation, json.dumps(self._writer()), temporary_name, _stamp(), *self._key(actor, request_id)))
            row.update(generation=generation, temporary_name=temporary_name)
        received = False
        try:
            stage = self._stage(row)
            if shutil.disk_usage(stage).free < manifest["byte_length"] + self.min_free_disk_bytes:
                raise MobileTransferError("disk_space_insufficient", "原素材盘余量不足，未接收文件。", status=503, retryable=True)
            path = stage / temporary_name
            digest, written = hashlib.sha256(), 0
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                async for chunk in chunks:
                    with self._connect() as reader:
                        current = self._row(reader, actor, request_id)
                    if current["state"] != "receiving" or current["generation"] != generation:
                        raise MobileTransferError("cancelled", "接收已取消或被新的明确重发取代。")
                    written += len(chunk)
                    if written > manifest["byte_length"] or written > self.max_bytes:
                        raise MobileTransferError("length_mismatch", "实际字节数超过文件清单。", status=422, retryable=True)
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if written != manifest["byte_length"] or digest.hexdigest() != manifest["sha256"]:
                raise MobileTransferError("content_mismatch", "完整文件长度或 SHA-256 校验失败，请从零重发。", status=422, retryable=True)
            self._root(row)
            _sync_directory(stage)
            receipt = {"sha256": digest.hexdigest(), "byte_length": written, "revision": _revision(path)}
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self._row(conn, actor, request_id)
                if current["state"] != "receiving" or current["generation"] != generation:
                    raise MobileTransferError("cancelled", "接收已取消，未封存文件。")
                conn.execute("""UPDATE mobile_transfers SET state='received',writer_json=NULL,receipt_json=?,updated_at=?
                    WHERE workspace_id=? AND member_id=? AND request_id=?""",
                    (json.dumps(receipt), _stamp(), *self._key(actor, request_id)))
            received = True
            return self.query(actor, request_id)
        except MobileTransferError as exc:
            self._fail(actor, request_id, generation, exc)
            raise
        except OSError as exc:
            error = MobileTransferError("storage_unavailable", "接收未完成，请检查原素材盘后从零重发。", status=503, retryable=True)
            self._fail(actor, request_id, generation, error)
            raise error from exc
        except BaseException:
            error = MobileTransferError("transfer_interrupted", "连接已中断，请查询原请求并从零重发。", status=503, retryable=True)
            self._fail(actor, request_id, generation, error)
            raise
        finally:
            if not received:
                self._cleanup(row)

    def complete(self, actor, request_id):
        with self._connect() as conn:
            row = self._row(conn, actor, request_id)
        if row["state"] == "completed":
            return self._view(row)
        if row["state"] != "received":
            raise MobileTransferError("content_not_received", "完整文件尚未校验接收，不能登记素材。")
        root, manifest = self._root(row), json.loads(row["manifest_json"])
        source = self._stage(row) / row["temporary_name"]
        receipt = json.loads(row["receipt_json"])
        scope = hashlib.sha256(f"{actor.workspace_id}\0{actor.owner_user_id}".encode()).hexdigest()[:16]
        destination = root / f"discovery-{scope}-{receipt['sha256']}{Path(manifest['filename']).suffix.lower()}"
        # Publication may have finished before a process exit or DB rollback.
        # Reconcile the same fully verified file; never infer receipt from size.
        source_revision = self._verified_revision(source, receipt) if source.exists() or source.is_symlink() else None
        existing_revision = self._verified_revision(destination, receipt) if destination.exists() or destination.is_symlink() else None
        if source_revision is None and existing_revision is None:
            raise MobileTransferError("content_missing", "原接收文件已丢失，请取消后以新请求重发。")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._row(conn, actor, request_id)
                if current["state"] == "completed":
                    return self._view(current)
                if current["state"] != "received" or current["generation"] != row["generation"]:
                    raise MobileTransferError("transfer_changed", "接收状态已变化，未登记素材。")
                self._root(current)
                if source_revision is not None and (source.is_symlink() or _revision(source) != source_revision):
                    raise MobileTransferError("content_changed", "接收文件已变化，未登记素材。")
                if existing_revision is None:
                    os.link(source, destination)
                elif destination.is_symlink() or _revision(destination) != existing_revision:
                    raise MobileTransferError("destination_conflict", "已有文件修订发生变化，未覆盖。")
                _sync_directory(root)
                # Remove the staging link before recording the final ctime.
                # A later failure retains the complete destination for replay.
                if source_revision is not None:
                    source.unlink()
                    _sync_directory(source.parent)
                try:
                    self.confirm_copy(conn, actor.workspace_id, relative_path=destination.name,
                        expected_revision=_revision(destination), confirmation_source="app_import",
                        verified_owner_user_id=actor.owner_user_id)
                except ValueError as exc:
                    raise MobileTransferError("copy_confirmation_refused", str(exc), retryable=True) from exc
                info = destination.stat()
                media_type = material_media_type_for(destination)
                asset = MaterialAsset(
                    id="asset_" + hashlib.sha256(f"{actor.workspace_id}\0{destination}".encode()).hexdigest()[:16],
                    workspace_id=actor.workspace_id, owner_user_id=actor.owner_user_id,
                    file_name=manifest["filename"], path=str(destination), size_bytes=info.st_size,
                    modified_at=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc), media_type=media_type,
                    user_description=manifest["description"])
                if media_type != "video":
                    asset.analysis_status = "blocked"
                    asset.analysis_message = f"这条{MATERIAL_MEDIA_LABEL[media_type]}已入库；首批只支持视频的镜头与画面分析，内容分析尚未接入。"
                try:
                    asset = self.assets.save_received_asset(conn, asset, workspace_id=actor.workspace_id,
                        owner_user_id=actor.owner_user_id)
                except ValueError as exc:
                    raise MobileTransferError("asset_conflict", str(exc)) from exc
                self.references.claim_asset_reference(conn, actor.workspace_id, asset.id)
                receipt["asset_ref"] = self.references.workspace_media_reference_for_asset(actor.workspace_id, asset.id, connection=conn)
                conn.execute("""UPDATE mobile_transfers SET state='completed',asset_id=?,receipt_json=?,error_json=NULL,updated_at=?
                    WHERE workspace_id=? AND member_id=? AND request_id=?""",
                    (asset.id, json.dumps(receipt), _stamp(), *self._key(actor, request_id)))
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
        return self.query(actor, request_id)

    @staticmethod
    def _verified_revision(path, receipt):
        if path.is_symlink() or not path.is_file():
            raise MobileTransferError("content_changed", "接收位置已被替换，未覆盖。")
        revision = _revision(path)
        with path.open("rb") as original:
            digest = hashlib.file_digest(original, "sha256").hexdigest()
        if (revision != _revision(path) or int(revision["size_bytes"]) != receipt["byte_length"]
                or digest != receipt["sha256"]):
            raise MobileTransferError("content_changed", "已接收文件长度或摘要发生变化，未登记素材。")
        return revision

    def cancel(self, actor, request_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, actor, request_id)
            if row["state"] == "completed":
                raise MobileTransferError("already_completed", "原请求已入库；取消不会删除已有素材。")
            conn.execute("""UPDATE mobile_transfers SET state='cancelled',writer_json=NULL,error_json=NULL,
                generation=generation+1,updated_at=? WHERE workspace_id=? AND member_id=? AND request_id=?""",
                (_stamp(), *self._key(actor, request_id)))
        if row["state"] != "receiving":
            self._cleanup(row)
        return self.query(actor, request_id)
