"""Bounded immutable original bytes under the configured application data root.

The source repository owns authorization and receipts. This component never
opens a database or accepts a renderer-supplied filesystem reference.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

MAX_ORIGINAL_FILE_BYTES = 30_000_000  # Existing 40 MB base64 upload request bound.


class KnowledgeOriginalUnavailable(FileNotFoundError):
    """The selected version has no original, or its recorded file is absent."""


class KnowledgeOriginalIntegrityError(ValueError):
    """The recorded immutable original no longer matches its receipt."""


class KnowledgeOriginalStorageError(OSError):
    """The configured disk/root is unavailable; never choose another root."""


class KnowledgeFileStore:
    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).expanduser().absolute()

    @staticmethod
    def scope(workspace_id: str, owner_user_id: str) -> str:
        if not workspace_id.strip() or not owner_user_id.strip():
            raise ValueError("保存原件需要已验证的成员和工作区")
        return hashlib.sha256(json.dumps([workspace_id, owner_user_id], ensure_ascii=False).encode()).hexdigest()

    def _base(self) -> Path:
        try:
            base = self.data_root.resolve(strict=True)
            for path in (self.data_root, base):
                if path.parts[:2] == ("/", "Volumes") and len(path.parts) > 2:
                    if not os.path.ismount(Path(*path.parts[:3])):
                        raise KnowledgeOriginalStorageError("资料原件所在磁盘未挂载，请连接原磁盘后重试")
            if not base.is_dir():
                raise KnowledgeOriginalStorageError("原工作区存储目录不可用，请修复原存储设置")
            return base
        except FileNotFoundError as exc:
            raise KnowledgeOriginalStorageError("原工作区存储目录不存在，请连接原磁盘后重试") from exc

    def _directory(self, workspace_id: str, owner_user_id: str, *, create: bool) -> Path:
        parent = self._base()
        for component in ("originals", "knowledge", self.scope(workspace_id, owner_user_id)):
            parent = parent / component
            if create:
                # The already verified data root must exist. Do not recursively
                # recreate a missing external volume or a caller-selected path.
                try:
                    parent.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            if parent.is_symlink():
                raise KnowledgeOriginalStorageError("资料原件存储位置被符号链接替换，请修复原存储位置")
            if not parent.is_dir():
                if create:
                    raise KnowledgeOriginalStorageError("资料原件存储目录不可用")
                raise KnowledgeOriginalUnavailable("此版本原件文件缺失，请连接原磁盘或检查备份")
        return parent

    @staticmethod
    def _check_digest(digest: str) -> None:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise KnowledgeOriginalIntegrityError("原件内容指纹无效，未读取其他文件")

    @staticmethod
    def _read_file(path: Path, digest: str, size_bytes: int) -> bytes:
        if not 0 < size_bytes <= MAX_ORIGINAL_FILE_BYTES:
            raise KnowledgeOriginalIntegrityError("原件记录大小无效，未读取文件")
        if path.is_symlink():
            raise KnowledgeOriginalStorageError("资料原件被符号链接替换，请修复原存储位置")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError as exc:
            raise KnowledgeOriginalUnavailable("此版本原件文件缺失，请连接原磁盘或检查备份") from exc
        except OSError as exc:
            raise KnowledgeOriginalStorageError("资料原件无法读取，请检查原存储位置和权限") from exc
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size_bytes:
                raise KnowledgeOriginalIntegrityError("原件大小或文件类型已变化，未返回不匹配内容")
            content = source.read(size_bytes + 1)
            after = os.fstat(source.fileno())
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ) or len(content) != size_bytes or hashlib.sha256(content).hexdigest() != digest:
                raise KnowledgeOriginalIntegrityError("原件内容已变化，未返回不匹配内容")
        return content

    def read(self, workspace_id: str, owner_user_id: str, digest: str, size_bytes: int) -> bytes:
        self._check_digest(digest)
        directory = self._directory(workspace_id, owner_user_id, create=False)
        return self._read_file(directory / digest, digest, size_bytes)

    def put(self, workspace_id: str, owner_user_id: str, content: bytes, digest: str) -> None:
        self._check_digest(digest)
        if not 0 < len(content) <= MAX_ORIGINAL_FILE_BYTES:
            raise ValueError("资料原件须为非空文件，且不超过 30 MB")
        if hashlib.sha256(content).hexdigest() != digest:
            raise KnowledgeOriginalIntegrityError("上传原件与解析内容指纹不一致，未保存")
        directory = self._directory(workspace_id, owner_user_id, create=True)
        destination = directory / digest
        if destination.exists() or destination.is_symlink():
            self._read_file(destination, digest, len(content))
            return
        temporary = None
        try:
            with NamedTemporaryFile(dir=directory, prefix=".upload-", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if self._directory(workspace_id, owner_user_id, create=False) != directory:
                raise KnowledgeOriginalStorageError("资料原件存储位置已变化，未登记原件")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                self._read_file(destination, digest, len(content))
            if os.name != "nt":
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
