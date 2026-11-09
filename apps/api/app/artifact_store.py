from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


@dataclass(frozen=True)
class PublishedArtifact:
    logical_ref: str
    sha256: str
    size_bytes: int


class ArtifactStore:
    """Publishes immutable files below one controlled storage root."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        root_stat = self._root.stat()
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)

    def publish_bytes(self, logical_ref: str, content: bytes) -> PublishedArtifact:
        return self.publish(logical_ref, lambda target: target.write(content))

    def publish_file(
        self, logical_ref: str, source: str | Path, *,
        temporary_path: str | Path | None = None,
        check: Callable[[], None] | None = None,
        retain_failed_temporary: bool = False,
    ) -> PublishedArtifact:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        def copy(target: BinaryIO) -> None:
            with source_path.open("rb") as source_file:
                while True:
                    self._check_publication(check)
                    chunk = source_file.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)

        return self.publish(logical_ref, copy, temporary_path=temporary_path, check=check,
                            retain_failed_temporary=retain_failed_temporary)

    def publish(
        self,
        logical_ref: str,
        write: Callable[[BinaryIO], object],
        *,
        temporary_path: str | Path | None = None,
        check: Callable[[], None] | None = None,
        retain_failed_temporary: bool = False,
    ) -> PublishedArtifact:
        if retain_failed_temporary and temporary_path is None:
            raise ValueError("保留失败复制文件需要明确的计量路径")
        target = self._target(logical_ref)
        self._check_publication(check)
        if temporary_path is not None:
            self._require_plain_directory(target.parent, create=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(self._root):
            raise ValueError("产物逻辑引用必须位于受控目录内")

        self._check_publication(check)
        target_parent_stat = target.parent.stat()
        target_parent_identity = (target_parent_stat.st_dev, target_parent_stat.st_ino)
        temporary_parent_identity = None
        temporary_identity = None
        completed = False
        if temporary_path is None:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp",
            )
            temporary = Path(temporary_name)
        else:
            temporary = Path(temporary_path)
            if (not temporary.is_absolute() or ".." in temporary.parts
                    or not temporary.is_relative_to(self._root) or temporary == target):
                raise ValueError("显式临时文件必须是受控目录内独立的绝对路径")
            self._require_plain_directory(temporary.parent)
            self._require_plain_directory(target.parent)
            if temporary.parent.stat().st_dev != target.parent.stat().st_dev:
                raise ValueError("临时文件必须与最终产物位于同一实际卷")
            descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            temporary_parent_stat = temporary.parent.stat()
            temporary_parent_identity = (temporary_parent_stat.st_dev, temporary_parent_stat.st_ino)
            identity = os.fstat(descriptor)
            temporary_identity = (identity.st_dev, identity.st_ino)
            temporary_file = os.fdopen(descriptor, "w+b")
            descriptor = None  # The file object now owns closing the descriptor.
            with temporary_file:
                self._check_publication(check)
                write(temporary_file)
                self._check_publication(check)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_file.seek(0)
                publication = self._describe_stream(
                    logical_ref, temporary_file, lambda: self._check_publication(check),
                )
            self._check_publication(check)
            if not self._file_matches(temporary, temporary_identity):
                raise OSError("发布临时文件已被替换")
            if temporary_path is not None:
                self._require_plain_directory(temporary.parent)
                self._require_plain_directory(target.parent)
            if (not self._directory_matches(temporary.parent, temporary_parent_identity)
                    or not self._directory_matches(target.parent, target_parent_identity)):
                raise OSError("发布目录已被替换")
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                if target.is_symlink():
                    raise ValueError("产物逻辑引用不能指向符号链接")
                existing = self._describe(logical_ref, target, lambda: self._check_publication(check))
                if existing != publication:
                    raise FileExistsError("产物逻辑引用已绑定到不同内容")
                self._check_publication(check)
                completed = True
                return existing
            completed = True
            return publication
        finally:
            if descriptor is not None:
                os.close(descriptor)
            # A replacement disk/directory must not receive even cleanup writes.
            # Metered media owners need the exact partial copy to settle the
            # original attempt. Generic publications retain their normal cleanup.
            if ((completed or not retain_failed_temporary)
                    and temporary_parent_identity is not None and temporary_identity is not None
                    and self._storage_root_matches()
                    and self._directory_matches(temporary.parent, temporary_parent_identity)
                    and (temporary_path is None or temporary.parent.resolve() == temporary.parent)
                    and self._file_matches(temporary, temporary_identity)):
                temporary.unlink(missing_ok=True)

    def _check_publication(self, check: Callable[[], None] | None) -> None:
        self._require_storage_root()
        if check is not None:
            check()
        self._require_storage_root()

    def _require_plain_directory(self, directory: Path, *, create: bool = False) -> None:
        """Explicit sealed paths cannot redirect through even an in-root symlink."""
        relative = directory.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current = current / part
            if create:
                current.mkdir(exist_ok=True)
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise ValueError("发布路径目录不能包含符号链接或非目录")

    @staticmethod
    def _directory_matches(path: Path, identity: tuple[int, int]) -> bool:
        try:
            current = path.stat()
        except FileNotFoundError:
            return False
        return stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == identity

    @staticmethod
    def _file_matches(path: Path, identity: tuple[int, int]) -> bool:
        try:
            current = path.lstat()
        except FileNotFoundError:
            return False
        return stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity

    def resolve_file(self, logical_ref: str) -> Path:
        """Resolve a published logical reference inside the controlled root."""
        target = self._target(logical_ref)
        if target.is_symlink() or not target.is_file() or target.stat().st_size == 0:
            raise FileNotFoundError(logical_ref)
        return target

    def describe(self, logical_ref: str) -> PublishedArtifact:
        """Describe an already published immutable artifact."""
        return self._describe(logical_ref, self.resolve_file(logical_ref))

    def _target(self, logical_ref: str) -> Path:
        self._require_storage_root()
        if not logical_ref or "\\" in logical_ref:
            raise ValueError("产物逻辑引用必须是非空 POSIX 相对路径")
        relative = PurePosixPath(logical_ref)
        if (
            relative.is_absolute()
            or relative.as_posix() != logical_ref
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("产物逻辑引用必须是受控目录内的相对路径")
        target = self._root.joinpath(*relative.parts)
        if not target.resolve(strict=False).is_relative_to(self._root):
            raise ValueError("产物逻辑引用必须位于受控目录内")
        return target

    def _storage_root_matches(self) -> bool:
        try:
            current = self._root.stat()
        except FileNotFoundError:
            return False
        return (current.st_dev, current.st_ino) == self._root_identity

    def _require_storage_root(self) -> None:
        if not self._storage_root_matches():
            raise OSError("产物存储目录已断开或被替换，请恢复原存储位置后重试；更换磁盘后需重新启动应用。")

    @staticmethod
    def _describe(
        logical_ref: str, path: Path, check: Callable[[], None] | None = None,
    ) -> PublishedArtifact:
        with path.open("rb") as artifact_file:
            return ArtifactStore._describe_stream(logical_ref, artifact_file, check)

    @staticmethod
    def _describe_stream(
        logical_ref: str, artifact_file: BinaryIO, check: Callable[[], None] | None = None,
    ) -> PublishedArtifact:
        digest = hashlib.sha256()
        size = 0
        while True:
            if check is not None:
                check()
            chunk = artifact_file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        return PublishedArtifact(
            logical_ref=logical_ref,
            sha256=digest.hexdigest(),
            size_bytes=size,
        )
