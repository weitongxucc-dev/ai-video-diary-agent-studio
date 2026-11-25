"""Live disk accounting for the private files of one granted MP4 cut."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from app.device_resource_volumes import existing_volume


class CutDiskProgress:
    def __init__(self, root: Path, *, source_bytes: int, output_bytes: int):
        self.root = root
        self.volume, _ = existing_volume(root)
        self.source_bytes = source_bytes
        self.output_bytes = output_bytes
        self._files: dict[str, tuple[Path, tuple[int, int]]] = {}
        self._verified_bytes: int | None = None
        self._published = False

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino

    def _observe(self, path: Path):
        if (not path.is_relative_to(self.root) or path.parent.resolve() != path.parent
                or existing_volume(self.root)[0] != self.volume):
            raise ValueError("截取文件的许可目录或实际卷已变化")
        info = path.lstat()
        if info.st_dev != self.volume or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("截取文件缺少原卷上的独占常规文件身份")
        return info

    def bind(self, role: str, path: Path, handle):
        """Bind the exact descriptor just created exclusively by the cut owner."""
        opened = os.fstat(handle.fileno())
        current = self._observe(path)
        if role in self._files or opened.st_size != 0 or self._identity(opened) != self._identity(current):
            raise ValueError("截取文件不是原作业刚创建的写入文件")
        self._files[role] = (path, self._identity(opened))

    def _used(self, role: str, cap: int, *, complete: bool = False) -> int:
        if role not in self._files:
            return 0
        path, identity = self._files[role]
        info = self._observe(path)
        if self._identity(info) != identity or info.st_size > cap or complete and info.st_size != cap:
            raise ValueError("截取文件身份或写入大小与许可不符")
        # Sparse or preallocated regions are accounted by actual allocated blocks,
        # with at most the original allowance credited to this grant.
        return min(cap, info.st_blocks * 512)

    def verified(self, staged: Path, size: int):
        """Move accounting to the verified file after the source copy is removed."""
        source, _ = self._files["source"]
        try:
            source.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("截取完成后源副本仍占用暂存空间")
        _, identity = self._files["staging"]
        info = self._observe(staged)
        if self._identity(info) != identity or info.st_size != size or not 0 < size <= self.output_bytes:
            raise ValueError("核验后的截取暂存文件与许可不符")
        self._files["staging"] = (staged, identity)
        self._verified_bytes = size

    def published(self):
        # Called only after ArtifactStore has successfully finalized the checked copy.
        self._published = True

    def remaining_bytes(self) -> int:
        if existing_volume(self.root)[0] != self.volume:
            raise ValueError("截取产物卷已变化")
        if self._published:
            return 0
        if self._verified_bytes is not None:
            self._used("staging", self._verified_bytes, complete=True)
            return self._verified_bytes - self._used("publication", self._verified_bytes)
        return (self.source_bytes - self._used("source", self.source_bytes)
                + self.output_bytes - self._used("staging", self.output_bytes)
                + self.output_bytes)
