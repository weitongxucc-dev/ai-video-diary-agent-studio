"""Scheduled directory observations and grant-checked source hashing."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Callable, Literal

from app.device_scheduler import Grant
from app.material_hash import (
    MaterialHashCandidate, MaterialHashResult,
    material_hash_candidate as _material_hash_candidate,
)
from app.video import SUPPORTED_VIDEO_EXTENSIONS


class MaterialHashIntake:
    def __init__(self, connection_factory, *, records, library_root, library_available,
                 read_binding, mark_disk_missing, upsert_asset, asset_by_path, mark_asset_blocked):
        self._connect = connection_factory
        self._records = records
        self._library_root = library_root
        self._library_available = library_available
        self._read_binding = read_binding
        self._mark_disk_missing = mark_disk_missing
        self._upsert_asset = upsert_asset
        self._asset_by_path = asset_by_path
        self._mark_asset_blocked = mark_asset_blocked

    def discover_material_hash_candidates(self, *, stable_seconds: float = 10.0) -> list[MaterialHashCandidate]:
        """Observe stable files for the opt-in scheduler path without reading their content."""
        import time

        if stable_seconds < 0:
            raise ValueError("素材稳定等待时间无效")
        binding = self._read_binding()
        if binding is None:
            return []
        workspace_id, bound_path = binding
        root = self._library_root()
        if not self._library_available() or str(root.resolve()) != bound_path:
            self._mark_disk_missing(workspace_id)
            return []
        self._records.require_observation_schema()

        now = time.time()
        candidates: list[MaterialHashCandidate] = []
        for path in sorted(root.rglob("*")):
            if (path.is_symlink() or not path.is_file()
                    or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS
                    or "_已处理" in path.parts):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                continue
            try:
                stat = resolved.stat()
            except FileNotFoundError:
                continue
            asset_id = "asset_" + hashlib.sha256(
                f"{workspace_id}\0{resolved}".encode("utf-8")
            ).hexdigest()[:16]
            candidate = _material_hash_candidate(workspace_id, bound_path, resolved, stat, asset_id)
            observation = self._records.observe_revision(
                workspace_id, str(resolved), stat, candidate, now, stable_seconds,
            )
            if observation is None:
                continue
            changed, stable = observation
            if stable:
                candidates.append(candidate)
            if changed:
                try:
                    self._upsert_asset(workspace_id, resolved)
                except FileNotFoundError:
                    continue
                self._mark_asset_blocked(
                    workspace_id, asset_id, "文件仍在复制或变化；写入稳定后自动安排校验。"
                )
            elif candidate in candidates:
                # Repair a crash between recording the observation and creating its asset.
                try:
                    self._asset_by_path(workspace_id, str(resolved))
                except KeyError:
                    try:
                        self._upsert_asset(workspace_id, resolved)
                    except FileNotFoundError:
                        candidates.remove(candidate)
                        continue
                    self._mark_asset_blocked(
                        workspace_id, asset_id, "文件已稳定，等待设备许可后校验。"
                    )
        return candidates

    def material_hash_verdict(
        self, workspace_id: str, asset_id: str, input_digest: str,
        unit_key: str, conn: sqlite3.Connection,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        binding = self._records.binding(conn)
        if binding is None or binding["workspace_id"] != workspace_id:
            return "unauthorized"
        if not self._library_available():
            return "dependency"
        bound_path = str(self._library_root().resolve())
        if binding["library_path"] != bound_path:
            return "stale"
        asset_row = self._records.asset_source(conn, workspace_id, asset_id)
        if asset_row is None:
            return "stale"
        path = Path(str(asset_row["file_path"]))
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(Path(bound_path)):
                return "stale"
        except OSError:
            return "unknown"
        observation = self._records.observation(conn, workspace_id, str(path))
        if observation is None:
            return "stale"
        if observation["state"] != "waiting" or observation["attempted_digest"] is not None:
            return "stale"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return "dependency"
        except OSError:
            return "unknown"
        candidate = _material_hash_candidate(workspace_id, bound_path, path, stat, asset_id)
        if (observation["provisional_revision"] != candidate.provisional_revision
                or input_digest != candidate.provisional_revision
                or unit_key != candidate.unit_key):
            return "stale"
        return "ready"

    def material_hash_candidate_for_grant(self, grant: Grant) -> MaterialHashCandidate:
        with self._connect() as conn:
            row = self._records.request_identity(conn, grant.request_id)
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"], row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                raise PermissionError("素材哈希许可与执行对象不匹配")
            asset_id = str(row["owner_id"])
            verdict = self.material_hash_verdict(
                grant.workspace_id, asset_id, grant.input_digest, grant.unit_key, conn
            )
            if verdict != "ready":
                raise ValueError(f"素材哈希输入不可执行：{verdict}")
            asset = self._records.asset_source(conn, grant.workspace_id, asset_id)
        path = Path(str(asset["file_path"]))
        candidate = _material_hash_candidate(
            grant.workspace_id, str(self._library_root().resolve()), path,
            path.stat(), asset_id,
        )
        if (candidate.provisional_revision, candidate.unit_key) != (
            grant.input_digest, grant.unit_key
        ):
            raise ValueError("素材哈希源修订已变化")
        return candidate

    def material_hash_verdict_for_grant(
        self, grant: Grant,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        with self._connect() as conn:
            row = self._records.request_identity(conn, grant.request_id)
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"], row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                return "unauthorized"
            return self.material_hash_verdict(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            )

    def hash_granted_material(
        self, grant: Grant, assert_grant: Callable[[Grant], None],
    ) -> MaterialHashResult:
        """Read content only under an active grant, checking it between chunks."""
        candidate = self.material_hash_candidate_for_grant(grant)
        path = Path(candidate.file_path)
        if path.is_symlink() or not path.resolve().is_relative_to(self._library_root().resolve()):
            raise ValueError("素材路径不在已绑定目录内")
        before = path.stat()
        digest = hashlib.sha256()
        assert_grant(grant)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns, opened.st_ino)
                    != (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)):
                raise ValueError("素材文件在打开时已变化")
            while True:
                assert_grant(grant)
                chunk = source.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)):
            raise ValueError("哈希期间源文件发生变化")
        current = _material_hash_candidate(
            candidate.workspace_id, str(self._library_root().resolve()),
            path, after, candidate.asset_id,
        )
        if current != candidate:
            raise ValueError("哈希期间素材绑定或源修订发生变化")
        return MaterialHashResult(candidate, digest.hexdigest())
