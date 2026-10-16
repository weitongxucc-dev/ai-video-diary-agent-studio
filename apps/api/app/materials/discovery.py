"""Opt-in local discovery, candidate preview and selected-file reception.

Routes supply a verified member and call bounded advance() steps while the user
has an active scan. Discovery does not start a watcher, inspect media content,
or create assets. accept_copy is the existing intake use case supplied by the
composition root; it must idempotently register this complete published file.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import string
import tempfile
import threading
from collections.abc import Callable
from uuid import uuid4

from app.materials.contracts import MATERIAL_MEDIA_BY_SUFFIX
from app.materials.discovery_store import DiscoveryStore
from app.models import utc_now

# Software/system artifacts are excluded independently of user preferences.
SKIP_NAMES = frozenset({
    "system", "library", "applications", "windows", "program files", "program files (x86)",
    "programdata", "appdata", "$recycle.bin", "system volume information", "recovery",
    "bin", "sbin", "usr", "etc", "dev", "proc", "sys", "private", "var", "tmp",
    "node_modules", "vendor", "site-packages", "__pycache__", "venv", ".venv", ".git",
    "cache", "caches", "build", "dist", "target", ".next", ".npm", ".cache",
})


def _identity(path: Path) -> list[int]:
    s = path.stat()
    return [s.st_dev, s.st_ino]


def _revision(s: os.stat_result) -> list[int]:
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def system_roots() -> list[Path]:
    """Local host inventory only, never a renderer-provided path."""
    if os.name == "nt":
        return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    roots = [Path("/")]
    for mount_dir in (Path("/Volumes"), Path("/media"), Path("/mnt")):
        if mount_dir.is_dir():
            for item in mount_dir.iterdir():
                if item.is_dir() and not item.is_symlink():
                    roots.append(item)
    return roots


class MaterialDiscoveryService:
    def __init__(
        self, store: DiscoveryStore, *,
        root_provider: Callable[[], list[Path]] = system_roots,
        excluded_roots: Callable[[], list[Path]],
        target_for: Callable[[str], Path],
        accept_copy: Callable[[str, str, Path, str, str, str], str],
    ):
        self.store = store
        self._roots = root_provider
        self._excluded_roots = excluded_roots
        self._target_for = target_for
        self._accept_copy = accept_copy
        self._lock = threading.RLock()
        # One copy publisher, with independent pause/cancel/exclusion operations.
        self._copy_lock = threading.Lock()

    def roots(self) -> list[dict]:
        return [{"id": _key(str(path.absolute())), "label": "本机" if str(path) == "/" else path.name or str(path), "display_path": str(path), "available": path.is_dir() and self._mount_ready(path)} for path in self._roots()]

    @staticmethod
    def _mount_ready(path: Path) -> bool:
        if path.parts[:2] == ("/", "Volumes") and len(path.parts) >= 3:
            return os.path.ismount(Path(*path.parts[:3]))
        return True

    def start(self, workspace_id: str, owner_user_id: str, *, root_ids: list[str], exclusions: list[str] | None = None) -> dict:
        if not workspace_id or not owner_user_id:
            raise ValueError("扫描需要已验证的成员和工作区")
        available = {_key(str(p.absolute())): p for p in self._roots()}
        if not root_ids or any(item not in available for item in root_ids):
            raise ValueError("扫描位置已变化，请重新选择本机或外接盘")
        roots = []
        for root_id in dict.fromkeys(root_ids):
            path = available[root_id].resolve(strict=True)
            if not self._mount_ready(path):
                raise ValueError("扫描磁盘未挂载，请连接原磁盘后重新选择")
            roots.append({"path": str(path), "identity": _identity(path)})
        excluded = self.store.exclusions(workspace_id, owner_user_id) if exclusions is None else self._normalize_exclusions(exclusions)
        now = utc_now().isoformat()
        session = {"id": uuid4().hex, "workspace_id": workspace_id, "owner_user_id": owner_user_id,
                   "state": "scanning", "roots": roots, "exclusions": excluded,
                   "pending": [{"path": r["path"], "after": ""} for r in roots],
                   "visited_count": 0, "skipped_count": 0, "issues": [], "updated_at": now}
        with self._lock:
            previous = self.store.latest(workspace_id, owner_user_id)
            if previous and previous["state"] in {"scanning", "paused"}:
                raise ValueError("请先恢复或取消上一次扫描")
            self.store.save_exclusions(workspace_id, owner_user_id, excluded)
            self.store.save_session(session)
        return self.snapshot(session["id"], workspace_id, owner_user_id)

    @staticmethod
    def _normalize_exclusions(exclusions: list[str]) -> list[str]:
        # These inputs can only remove access, never authorize a path read.
        if any(not Path(p).expanduser().is_absolute() for p in exclusions):
            raise ValueError("排除目录必须是完整路径")
        return sorted({str(Path(p).expanduser().resolve()) for p in exclusions})

    def _allowed(self, session: dict, path: Path) -> bool:
        resolved = path.resolve()
        roots = [r for r in session["roots"] if resolved.is_relative_to(Path(r["path"]))]
        if not roots:
            return False
        root = max(roots, key=lambda r: len(r["path"]))
        if not self._mount_ready(Path(root["path"])) or _identity(Path(root["path"])) != root["identity"]:
            raise ValueError("扫描磁盘已断开或替换，请重新扫描")
        if path.is_symlink() or resolved != path:
            return False
        if any(part.lower() in SKIP_NAMES or part.lower().endswith((".app", ".photoslibrary")) for part in path.relative_to(Path(root["path"])).parts):
            return False
        exclusions = [Path(p) for p in session["exclusions"]] + [p.resolve() for p in self._excluded_roots()]
        return not any(resolved.is_relative_to(p) for p in exclusions)

    def snapshot(self, session_id: str, workspace_id: str, owner_user_id: str, *, offset: int = 0, limit: int = 100) -> dict:
        with self._lock:
            session = self.store.session(session_id, workspace_id, owner_user_id)
            candidates, total = self.store.candidates(session_id, offset=max(0, offset), limit=min(100, max(1, limit)))
            visible = []
            for candidate in candidates:
                item = {k: v for k, v in candidate.items() if k not in {"revision", "source_path", "temporary_path", "sha256"}}
                item["excluded"] = any(Path(candidate["source_path"]).is_relative_to(Path(p)) for p in session["exclusions"])
                visible.append(item)
            return {k: session[k] for k in ("id", "state", "exclusions", "visited_count", "skipped_count", "issues", "updated_at")} | {"candidates": visible, "total_count": total, "offset": offset, "has_more": offset + len(candidates) < total}

    def latest(self, workspace_id: str, owner_user_id: str) -> dict | None:
        session = self.store.latest(workspace_id, owner_user_id)
        return self.snapshot(session["id"], workspace_id, owner_user_id) if session else None

    def control(self, session_id: str, workspace_id: str, owner_user_id: str, action: str, *, exclusions: list[str] | None = None) -> dict:
        with self._lock:
            session = self.store.session(session_id, workspace_id, owner_user_id)
            if action == "exclude":
                session["exclusions"] = self._normalize_exclusions(exclusions or [])
                self.store.save_exclusions(workspace_id, owner_user_id, session["exclusions"])
            elif action == "pause" and session["state"] == "scanning":
                session["state"] = "paused"
            elif action == "resume" and session["state"] == "paused":
                session["state"] = "scanning"
            elif action == "cancel":
                session["state"] = "cancelled"
            else:
                raise ValueError("当前扫描状态不支持此操作")
            session["updated_at"] = utc_now().isoformat()
            self.store.save_session(session)
        return self.snapshot(session_id, workspace_id, owner_user_id)

    @staticmethod
    def _issue(session: dict, path: Path, message: str) -> None:
        session["skipped_count"] += 1
        if len(session["issues"]) < 100:
            session["issues"].append({"location": str(path), "message": message})

    def advance(self, session_id: str, workspace_id: str, owner_user_id: str, *, budget: int = 256) -> dict:
        with self._lock:
            session = self.store.session(session_id, workspace_id, owner_user_id)
            if session["state"] != "scanning":
                return self.snapshot(session_id, workspace_id, owner_user_id)
            candidates = []
            remaining = budget
            while session["pending"] and remaining > 0:
                cursor = session["pending"][0]
                directory = Path(cursor["path"])
                try:
                    if not self._allowed(session, directory):
                        session["pending"].pop(0)
                        continue
                    entries = sorted(directory.iterdir(), key=lambda p: p.name)
                except (OSError, ValueError):
                    self._issue(session, directory, "未扫描：目录权限不足、磁盘断开或位置已变化")
                    session["pending"].pop(0)
                    remaining -= 1
                    continue
                rest = [p for p in entries if p.name > cursor["after"]]
                for path in rest[:remaining]:
                    cursor["after"] = path.name
                    remaining -= 1
                    session["visited_count"] += 1
                    try:
                        # Mounted volumes have their own explicit selectable root.
                        if directory == Path("/") and path.name in {"Volumes", "media", "mnt"}:
                            continue
                        if not self._allowed(session, path):
                            continue
                        info = path.stat()
                        if stat.S_ISDIR(info.st_mode):
                            session["pending"].append({"path": str(path), "after": ""})
                        elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in MATERIAL_MEDIA_BY_SUFFIX:
                            revision = _revision(info)
                            identity = _key(str(path) + repr(revision))
                            candidates.append({"id": identity, "source_path": str(path), "revision": revision,
                                "file_name": path.name, "location": str(path.parent), "media_type": MATERIAL_MEDIA_BY_SUFFIX[path.suffix.lower()],
                                "size_bytes": info.st_size, "duration_seconds": None,
                                "state": "candidate", "asset_id": None, "error": None})
                    except (OSError, ValueError):
                        self._issue(session, path, "未读取：权限不足或文件已变化")
                if not rest or cursor["after"] == rest[-1].name:
                    session["pending"].pop(0)
            if not session["pending"]:
                session["state"] = "completed"
            session["updated_at"] = utc_now().isoformat()
            self.store.save_session(session, candidates)
        return self.snapshot(session_id, workspace_id, owner_user_id)

    def preview_path(self, session_id: str, candidate_id: str, workspace_id: str, owner_user_id: str) -> Path:
        with self._lock:
            session = self.store.session(session_id, workspace_id, owner_user_id)
            candidate = self.store.candidate(session_id, candidate_id)
            path = Path(candidate["source_path"])
            if not self._allowed(session, path) or _revision(path.stat()) != candidate["revision"]:
                raise ValueError("候选已排除、磁盘断开或文件变化，请重新扫描")
            return path

    def _copy_permitted(self, session_id: str, candidate: dict, workspace_id: str, owner_user_id: str) -> None:
        session = self.store.session(session_id, workspace_id, owner_user_id)
        if session["state"] in {"paused", "cancelled"} or not self._allowed(session, Path(candidate["source_path"])):
            raise ValueError("扫描已暂停、取消或该目录已排除，接收已停止")

    def receive(self, session_id: str, workspace_id: str, owner_user_id: str, candidate_ids: list[str]) -> dict:
        if not candidate_ids or len(candidate_ids) > 100:
            raise ValueError("请勾选 1 至 100 个候选素材")
        # Validate every identity before any copy starts.
        with self._lock:
            self.store.session(session_id, workspace_id, owner_user_id)
            selected = [self.store.candidate(session_id, item) for item in dict.fromkeys(candidate_ids)]
        with self._copy_lock:
            for candidate in selected:
                try:
                    self._receive_one(session_id, workspace_id, owner_user_id, candidate)
                except (OSError, ValueError) as exc:
                    candidate.update(state="failed", error=str(exc))
                    self.store.save_candidate(session_id, candidate)
        return self.snapshot(session_id, workspace_id, owner_user_id)

    def _receive_one(self, session_id: str, workspace_id: str, owner_user_id: str, candidate: dict) -> None:
        self._copy_permitted(session_id, candidate, workspace_id, owner_user_id)
        source = self.preview_path(session_id, candidate["id"], workspace_id, owner_user_id)
        target = self._target_for(workspace_id).resolve(strict=True)
        target_identity = _identity(target)
        if candidate.get("temporary_path"):
            interrupted = Path(candidate["temporary_path"])
            if interrupted.parent == target and interrupted.name.startswith(".discovery-") and interrupted.suffix == ".part":
                interrupted.unlink(missing_ok=True)
        if candidate.get("sha256"):
            existing = self.store.receipt(workspace_id, owner_user_id, candidate["sha256"])
            if existing:
                self._finish_receipt(session_id, workspace_id, owner_user_id, candidate, target, candidate["sha256"], existing)
                return
        candidate.update(state="receiving", error=None)
        self.store.save_candidate(session_id, candidate)
        temporary = None
        try:
            descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as original:
                if _revision(os.fstat(original.fileno())) != candidate["revision"]:
                    raise ValueError("源文件已变化，请重新扫描")
                digest = hashlib.sha256()
                with tempfile.NamedTemporaryFile(dir=target, prefix=".discovery-", suffix=".part", delete=False) as copied:
                    temporary = Path(copied.name)
                    candidate["temporary_path"] = str(temporary)
                    self.store.save_candidate(session_id, candidate)
                    while chunk := original.read(1024 * 1024):
                        self._copy_permitted(session_id, candidate, workspace_id, owner_user_id)
                        if _identity(target) != target_identity:
                            raise ValueError("接收磁盘已变化，未登记素材")
                        copied.write(chunk)
                        digest.update(chunk)
                    copied.flush()
                    os.fsync(copied.fileno())
                if _revision(os.fstat(original.fileno())) != candidate["revision"] or _revision(source.stat()) != candidate["revision"]:
                    raise ValueError("复制期间源文件发生变化，未登记素材")
            if temporary.stat().st_size == 0:
                raise ValueError("素材文件为空，未登记")
            self._copy_permitted(session_id, candidate, workspace_id, owner_user_id)
            checksum = digest.hexdigest()
            candidate["sha256"] = checksum
            self.store.save_candidate(session_id, candidate)
            # Digest is scoped by member/workspace so private files never share
            # a receipt merely because their bytes happen to be identical.
            identity = _key(workspace_id + "\0" + owner_user_id)[:16]
            destination = target / f"discovery-{identity}-{checksum}{source.suffix.lower()}"
            receipt = self.store.receipt(workspace_id, owner_user_id, checksum)
            if receipt:
                destination = Path(receipt["path"])
                if destination.parent != target or not destination.is_file() or destination.is_symlink() or _revision(destination.stat()) != receipt["revision"]:
                    raise ValueError("原接收文件已变化或丢失，请在素材库修复，不会覆盖原记录")
            else:
                if destination.exists():
                    # Recover publication before receipt commit. Verify bytes;
                    # never adopt an unrelated file based on its name alone.
                    if destination.is_symlink():
                        raise ValueError("接收位置已被替换")
                    with destination.open("rb") as existing:
                        existing_digest = hashlib.file_digest(existing, "sha256").hexdigest()
                    if existing_digest != checksum:
                        raise ValueError("接收位置存在不同文件，未覆盖")
                else:
                    if _identity(target) != target_identity or self._target_for(workspace_id).resolve(strict=True) != target:
                        raise ValueError("接收目录已变化，未登记素材")
                    os.link(temporary, destination)
                    temporary.unlink()
                    temporary = None
                receipt = {"path": str(destination), "revision": _revision(destination.stat()), "asset_id": None}
                self.store.save_receipt(workspace_id, owner_user_id, checksum, receipt)
            self._finish_receipt(session_id, workspace_id, owner_user_id, candidate, target, checksum, receipt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _finish_receipt(self, session_id: str, workspace_id: str, owner_user_id: str, candidate: dict, target: Path, checksum: str, receipt: dict) -> None:
        with self._lock:
            self._copy_permitted(session_id, candidate, workspace_id, owner_user_id)
            destination = Path(receipt["path"])
            if destination.parent != target or destination.is_symlink() or _revision(destination.stat()) != receipt["revision"]:
                raise ValueError("原接收文件已变化或丢失，请在素材库修复")
            # A replay queries the same aggregate without copying/analysing again.
            asset_id = self._accept_copy(workspace_id, owner_user_id, destination, candidate["media_type"], candidate["source_path"], checksum)
            receipt["asset_id"] = asset_id
            self.store.save_receipt(workspace_id, owner_user_id, checksum, receipt)
            candidate.update(state="received", asset_id=asset_id, error=None, temporary_path=None)
            self.store.save_candidate(session_id, candidate)
