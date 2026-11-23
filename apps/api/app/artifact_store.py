from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from uuid import uuid4
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


@dataclass(frozen=True)
class PublishedArtifact:
    logical_ref: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class StorageRebindEntry:
    """One logical artifact whose bytes must be present at the new root."""

    logical_ref: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class StorageRebindPlan:
    """Validated, still-uncommitted root rebind."""

    source_root: Path
    target_root: Path
    source_identity: tuple[int, int]
    target_identity: tuple[int, int]
    entries: tuple[StorageRebindEntry, ...]


@dataclass(frozen=True)
class StorageRebindReceipt:
    """The durable binding that the caller should expose in its audit receipt."""

    previous_root: Path
    active_root: Path
    active_identity: tuple[int, int]
    entries: tuple[StorageRebindEntry, ...]


class StorageRebindError(ValueError):
    """A root rebind was not safe to prepare or commit."""


class ArtifactStore:
    """Publishes immutable files below one controlled storage root."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        root_stat = self._root.stat()
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)

    @property
    def storage_root(self) -> Path:
        """Return the one root currently used for reads and writes."""
        return self._root

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

    def _adopt_storage_root(
        self, root: Path, *, expected_identity: tuple[int, int] | None = None,
    ) -> None:
        """Adopt a root after WorkspaceStorageRebind committed its binding.

        Callers must not invoke this directly.  Keeping the root identity beside
        the path makes every later publication/read continue to reject a removed
        or replaced volume.
        """
        resolved = root.expanduser().resolve()
        current = resolved.stat()
        identity = (current.st_dev, current.st_ino)
        if not stat.S_ISDIR(current.st_mode):
            raise StorageRebindError("新的产物根目录不是目录")
        if expected_identity is not None and identity != expected_identity:
            raise StorageRebindError("新的产物根目录在提交期间被替换，未切换当前进程")
        self._root = resolved
        self._root_identity = identity

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


class WorkspaceStorageRebind:
    """Prepare and commit a verified ArtifactStore root rebind.

    Files are staged by the caller before ``prepare``.  This class never copies
    user data or deletes the previous root.  It validates both roots and every
    logical artifact by byte digest and size, then lets the caller persist the
    unique root setting before switching the live store.  A missing, replaced,
    symlinked, or read-only root is rejected and the current store remains active.
    """

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @classmethod
    def verify_persisted_binding(
        cls, raw_root: str | Path, expected_identity: tuple[int, int],
    ) -> Path:
        """Reject a persisted path whose volume identity no longer matches."""
        target = cls._require_target_root(raw_root)
        if not cls._same_root_identity(target, expected_identity):
            raise StorageRebindError("已保存的产物根目录身份已变化，拒绝启动")
        return target

    def prepare(
        self,
        target_root: str | Path,
        entries: tuple[StorageRebindEntry, ...] | list[StorageRebindEntry],
    ) -> StorageRebindPlan:
        source_root = self._store.storage_root
        try:
            source_stat = source_root.stat()
        except OSError as exc:
            raise StorageRebindError("当前产物根目录不可用") from exc
        source_identity = (source_stat.st_dev, source_stat.st_ino)
        if not self._same_root_identity(source_root, source_identity):
            raise StorageRebindError("当前产物根目录已断开或被替换")

        requested_entries = tuple(entries)
        if not requested_entries and any(path.is_file() for path in source_root.rglob("*")):
            raise StorageRebindError("当前产物根目录仍有文件，迁移清单不能为空")
        target = self._require_target_root(target_root)
        target_stat = target.stat()
        target_identity = (target_stat.st_dev, target_stat.st_ino)
        checked = self._validate_entries(target, requested_entries)
        return StorageRebindPlan(
            source_root=source_root,
            target_root=target,
            source_identity=source_identity,
            target_identity=target_identity,
            entries=checked,
        )

    def commit(
        self,
        plan: StorageRebindPlan,
        persist_binding: Callable[[Path, tuple[int, int]], None],
    ) -> StorageRebindReceipt:
        """Persist the new binding, then switch the live store once.

        ``persist_binding`` must be an idempotent transaction owned by the
        settings/repository layer.  It must persist the path and identity in one
        binding record.  It runs only after the files are rechecked.  If it
        raises, the old root remains active.  A process restart must reject a
        persisted binding whose path no longer has the recorded identity.
        """
        if plan.source_root != self._store.storage_root:
            raise StorageRebindError("迁移计划不属于当前产物根目录")
        if not self._same_root_identity(plan.source_root, plan.source_identity):
            raise StorageRebindError("原产物根目录已断开或被替换")
        if not self._same_root_identity(plan.target_root, plan.target_identity):
            raise StorageRebindError("新的产物根目录已被替换")
        self._validate_entries(plan.target_root, plan.entries)
        persist_binding(plan.target_root, plan.target_identity)
        self._store._adopt_storage_root(plan.target_root, expected_identity=plan.target_identity)
        return StorageRebindReceipt(
            previous_root=plan.source_root,
            active_root=plan.target_root,
            active_identity=plan.target_identity,
            entries=plan.entries,
        )

    def _validate_entries(
        self,
        target_root: Path,
        entries: tuple[StorageRebindEntry, ...] | list[StorageRebindEntry],
    ) -> tuple[StorageRebindEntry, ...]:
        unique: dict[str, StorageRebindEntry] = {}
        for entry in entries:
            if not isinstance(entry, StorageRebindEntry):
                raise StorageRebindError("迁移清单包含无效项")
            if entry.logical_ref in unique:
                raise StorageRebindError("迁移清单包含重复的逻辑引用")
            if entry.size_bytes < 0 or not entry.sha256.strip():
                raise StorageRebindError("迁移清单缺少有效的内容摘要")
            source = self._store.resolve_file(entry.logical_ref)
            target = self._target(target_root, entry.logical_ref)
            self._verify_file(source, entry, "原产物")
            self._verify_file(target, entry, "迁移后的产物")
            unique[entry.logical_ref] = entry
        return tuple(unique.values())

    @staticmethod
    def _target(root: Path, logical_ref: str) -> Path:
        if not logical_ref or "\\" in logical_ref:
            raise StorageRebindError("迁移清单中的逻辑引用无效")
        relative = PurePosixPath(logical_ref)
        if (relative.is_absolute() or relative.as_posix() != logical_ref
                or any(part in {"", ".", ".."} for part in relative.parts)):
            raise StorageRebindError("迁移清单中的逻辑引用必须是受控相对路径")
        target = root.joinpath(*relative.parts)
        if not target.resolve(strict=False).is_relative_to(root):
            raise StorageRebindError("迁移清单中的逻辑引用越过受控目录")
        current = root
        for index, part in enumerate(relative.parts):
            current = current / part
            if current.is_symlink():
                raise StorageRebindError("迁移产物路径不能包含符号链接")
            if index < len(relative.parts) - 1 and current.exists() and not current.is_dir():
                raise StorageRebindError("迁移产物路径的父级不是目录")
        return target

    @staticmethod
    def _verify_file(path: Path, entry: StorageRebindEntry, label: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise StorageRebindError(f"{label}不存在或不是普通文件：{entry.logical_ref}")
        publication = ArtifactStore._describe(entry.logical_ref, path)
        if (publication.sha256, publication.size_bytes) != (entry.sha256, entry.size_bytes):
            raise StorageRebindError(f"{label}内容摘要或大小不匹配：{entry.logical_ref}")

    @staticmethod
    def _same_root_identity(root: Path, identity: tuple[int, int]) -> bool:
        try:
            current = root.stat()
        except OSError:
            return False
        return stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == identity

    @classmethod
    def _require_target_root(cls, raw_root: str | Path) -> Path:
        target = Path(raw_root).expanduser()
        if not target.is_absolute() or target.is_symlink():
            raise StorageRebindError("新的产物根目录必须是绝对且非符号链接目录")
        target = target.resolve()
        if not target.is_dir():
            raise StorageRebindError("新的产物根目录不存在或不是目录")
        probe = target / f".storage-rebind-{os.getpid()}-{uuid4().hex}"
        try:
            probe.touch(exist_ok=False)
            probe.unlink()
        except OSError as exc:
            probe.unlink(missing_ok=True)
            raise StorageRebindError("新的产物根目录不可写") from exc
        return target
