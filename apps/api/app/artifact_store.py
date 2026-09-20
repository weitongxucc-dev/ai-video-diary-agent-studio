from __future__ import annotations

import hashlib
import os
import shutil
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
        self, logical_ref: str, source: str | Path
    ) -> PublishedArtifact:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        def copy(target: BinaryIO) -> None:
            with source_path.open("rb") as source_file:
                shutil.copyfileobj(source_file, target)

        return self.publish(logical_ref, copy)

    def publish(
        self,
        logical_ref: str,
        write: Callable[[BinaryIO], object],
    ) -> PublishedArtifact:
        target = self._target(logical_ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(self._root):
            raise ValueError("产物逻辑引用必须位于受控目录内")

        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                write(temporary_file)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            self._require_storage_root()
            publication = self._describe(logical_ref, temporary)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink():
                    raise ValueError("产物逻辑引用不能指向符号链接")
                existing = self._describe(logical_ref, target)
                if existing != publication:
                    raise FileExistsError("产物逻辑引用已绑定到不同内容")
                return existing
            return publication
        finally:
            # A replacement disk/directory must not receive even cleanup writes.
            if self._storage_root_matches():
                temporary.unlink(missing_ok=True)

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
    def _describe(logical_ref: str, path: Path) -> PublishedArtifact:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as artifact_file:
            while chunk := artifact_file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return PublishedArtifact(
            logical_ref=logical_ref,
            sha256=digest.hexdigest(),
            size_bytes=size,
        )
