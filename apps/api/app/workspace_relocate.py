"""Stop-the-world relocation of the local workspace onto an external volume.

Operator command (``python -m app.workspace_relocate``).  It preflights the
source and target, seals the target volume identity in the operation receipt,
backs up the database, copies and byte-verifies every root, rebinds the
material library in the copied database, and then rewrites the dotenv paths.
Source data is never deleted and the old database is never rebound, so an
interrupted run leaves exactly one active configuration and can be resumed.

The command refuses to run while the application holds the device execution
lock, when the target is not a mounted external volume, when source and target
overlap, when the target already holds unrelated content, or when any copied
byte does not match its source.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app import device_paths, volume_identity
from app.env_files import (
    commit_prepared_dotenv_values,
    discard_prepared_dotenv_values,
    prepare_dotenv_values,
    runtime_env_file_path,
)
from app.materials.source_files import MaterialSourceFileAccess

RECEIPT_FORMAT = "yaohe-workspace-relocation/v1"
RECEIPT_FILENAME = "workspace-relocation.json"
_OPERATION_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)
_PART_PREFIX = ".yaohe-relocate-"
_FREE_SPACE_MARGIN_BYTES = 32 * 1024 * 1024
_FREE_SPACE_MARGIN_RATIO = 0.02


class RelocationError(RuntimeError):
    """A relocation precondition or integrity check failed."""


@dataclass(frozen=True)
class StorageRoot:
    name: str
    env_key: str
    kind: Literal["file", "directory"]


STORAGE_ROOTS: tuple[StorageRoot, ...] = (
    StorageRoot("database", "DATABASE_PATH", "file"),
    # VIDEO_INBOX_PATH is both the intake directory and the bound material
    # library (ProductionRepository receives it as video_library_path).
    StorageRoot("materials", "VIDEO_INBOX_PATH", "directory"),
    StorageRoot("outputs", "VIDEO_OUTPUT_PATH", "directory"),
    StorageRoot("drafts", "DRAFT_OUTPUT_PATH", "directory"),
    StorageRoot("temporary", "TEMPORARY_ROOT_PATH", "directory"),
)


@dataclass(frozen=True)
class RelocationRequest:
    source: Mapping[str, Path]
    target: Mapping[str, Path]
    env_path: Path
    operation_id: str | None = None
    receipt_path: Path | None = None
    record_path: Path | None = None
    lock_path: Path | None = None
    is_external: Callable[[Path], bool] | None = None
    probe: Callable[[Path], volume_identity.VolumeIdentity] | None = None
    free_bytes: Callable[[Path], int] | None = None


@dataclass(frozen=True)
class RelocationPlan:
    operation_id: str
    source: dict[str, Path]
    target: dict[str, Path]
    target_volume: Path
    target_identity: volume_identity.VolumeIdentity
    total_bytes: int
    file_counts: dict[str, int]
    resuming: bool
    env_path: Path
    receipt_path: Path
    record_path: Path


def _is_external_mount(path: Path) -> bool:
    volume = volume_identity.volume_root(path)
    return volume is not None and os.path.ismount(volume)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_operation_id(value: str) -> str:
    if not value or len(value) > 128 or any(char not in _OPERATION_CHARS for char in value):
        raise RelocationError("迁移操作编号无效")
    return value


def _default_operation_id(source: Mapping[str, Path], target: Mapping[str, Path]) -> str:
    canonical = json.dumps(
        {"source": {k: str(v) for k, v in sorted(source.items())},
         "target": {k: str(v) for k, v in sorted(target.items())}},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "relocate-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database_path), timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _walk_files(root: Path) -> Iterable[Path]:
    """Yield regular files below ``root``; symlinks and special files are refused."""
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink():
                    raise RelocationError(f"源目录包含符号链接，拒绝迁移：{path}")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path
                else:
                    raise RelocationError(f"源目录包含非普通文件，拒绝迁移：{path}")


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load_receipt(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RelocationError(f"迁移回执无法读取：{path}") from exc
    if not isinstance(payload, dict) or payload.get("format") != RECEIPT_FORMAT:
        raise RelocationError(f"迁移回执格式无效：{path}")
    return payload


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _acquire_execution_lock(lock_path: Path | None) -> int:
    path = Path(lock_path).expanduser() if lock_path is not None else device_paths.device_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise RelocationError("应用仍在运行并持有执行锁；请完全退出应用后再迁移。") from exc
    return descriptor


def _release_execution_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _same_paths(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return set(left) == set(right) and all(
        str(Path(str(left[key])).expanduser()) == str(Path(str(right[key])).expanduser())
        for key in left
    )


def _active_execution(database_path: Path) -> dict[str, int]:
    """Count execution rows that still claim to be pending or running."""
    owners = {
        "job_runs": "status IN ('queued', 'running')",
        "transform_runs": "status = 'running'",
        "material_analysis_runs": "status = 'running'",
        "person_renders": "status IN ('pending', 'running')",
    }
    active: dict[str, int] = {}
    with _connect(database_path) as connection:
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table, predicate in owners.items():
            if table not in tables:
                continue
            count = int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {predicate}"
            ).fetchone()[0])
            if count:
                active[table] = count
    return active


def _observe_target_volume(request: RelocationRequest, volume: Path) -> volume_identity.VolumeIdentity:
    is_mounted = request.is_external or _is_external_mount
    if not is_mounted(volume):
        raise RelocationError(f"目标不是已挂载的外接盘：{volume}。不会迁移到内置盘。")
    observed = (request.probe or volume_identity.probe_volume_identity)(volume)
    if not observed.uuid.strip() or Path(observed.mount_point) != volume.resolve():
        raise RelocationError(f"无法确认目标外接盘身份：{volume}。")
    if not is_mounted(volume):
        raise RelocationError(f"目标外接盘已断开：{volume}。请连接原数据盘后重试。")
    return observed


def _require_target_uuid(volume: Path, actual: str, expected: str) -> None:
    if actual != expected:
        raise RelocationError(f"目标外接盘身份不匹配：{volume}。请连接原数据盘后重试。")


def _plan_target_identity(
    request: RelocationRequest, volume: Path, record_path: Path, receipt: dict[str, object] | None,
) -> volume_identity.VolumeIdentity:
    observed = _observe_target_volume(request, volume)
    records = volume_identity.load_volume_identity_record(record_path) or {}
    recorded = records.get(str(volume.resolve()))
    if recorded is not None:
        _require_target_uuid(volume, observed.uuid, recorded.get("uuid", ""))
    if receipt is None:
        return observed

    sealed = receipt.get("volume_identity")
    if sealed is None and receipt.get("status") == "applied":
        # An older successful receipt may rely on the installed identity record.
        sealed = recorded
    if not isinstance(sealed, dict) or not isinstance(sealed.get("uuid"), str) or not sealed["uuid"].strip():
        raise RelocationError("原迁移回执缺少可信目标卷身份，请核对原数据盘与备份；已保留回执。")
    _require_target_uuid(volume, observed.uuid, sealed["uuid"])
    return observed


def plan_relocation(request: RelocationRequest) -> RelocationPlan:
    """Validate everything the copy depends on without writing user data."""
    source = {name: Path(value).expanduser() for name, value in request.source.items()}
    target = {name: Path(value).expanduser() for name, value in request.target.items()}
    expected_names = {spec.name for spec in STORAGE_ROOTS}
    if set(source) != set(target) or not set(source) <= expected_names:
        raise RelocationError("迁移源和目标的存储根集合不一致")
    if "database" not in source or "materials" not in source:
        raise RelocationError("迁移必须包含数据库和素材目录")
    operation_id = _valid_operation_id(
        request.operation_id or _default_operation_id(source, target)
    )
    env_path = Path(request.env_path).expanduser()
    receipt_path = (
        Path(request.receipt_path).expanduser()
        if request.receipt_path is not None
        else env_path.parent / RECEIPT_FILENAME
    )
    record_path = (
        Path(request.record_path).expanduser()
        if request.record_path is not None
        else volume_identity.record_path_for_env(env_path)
    )
    for name, path in source.items():
        spec = next(item for item in STORAGE_ROOTS if item.name == name)
        if path.is_symlink():
            raise RelocationError(f"源存储位置不能是符号链接：{path}")
        if spec.kind == "file":
            if not path.is_file():
                raise RelocationError(f"源数据库不存在：{path}")
            if path.stat().st_size <= 0:
                raise RelocationError(f"源数据库为空：{path}")
        elif not path.is_dir():
            raise RelocationError(f"源存储目录不存在：{path}")

    active = _active_execution(source["database"])
    if active:
        raise RelocationError(
            "检测到未结束的执行记录："
            + "、".join(f"{table} {count}" for table, count in sorted(active.items()))
            + "。请先启动应用完成恢复并正常退出，再执行迁移。"
        )

    for path in target.values():
        if not path.is_absolute() or path.is_symlink():
            raise RelocationError(f"目标存储位置必须是绝对且非符号链接路径：{path}")
    if len({path.name for path in target.values()}) != len(target):
        raise RelocationError("目标存储位置名称重复")

    for source_path in source.values():
        for target_path in target.values():
            if target_path == source_path or target_path.is_relative_to(source_path) or source_path.is_relative_to(target_path):
                raise RelocationError(f"迁移源和目标不能重叠：{source_path} / {target_path}")

    volumes = {volume_identity.volume_root(path) for path in target.values()}
    if len(volumes) != 1 or None in volumes:
        raise RelocationError("所有目标存储位置必须位于同一个外接盘")
    target_volume = next(iter(volumes))
    assert target_volume is not None
    if any(path.resolve().is_relative_to(target_volume.resolve()) for path in (receipt_path, record_path)):
        raise RelocationError("迁移回执和卷身份记录必须保存在目标卷之外。")
    receipt = _load_receipt(receipt_path)
    if receipt is not None:
        if str(receipt.get("operation_id")) != operation_id:
            raise RelocationError("迁移回执属于另一操作，请使用原操作编号或新的回执路径。")
        if not _same_paths(receipt.get("source", {}), source) or not _same_paths(receipt.get("target", {}), target):
            raise RelocationError("迁移回执的源或目标与本次请求不一致。")
    target_identity = _plan_target_identity(request, target_volume, record_path, receipt)
    resuming = receipt is not None

    total_bytes = 0
    file_counts: dict[str, int] = {}
    for name, path in source.items():
        spec = next(item for item in STORAGE_ROOTS if item.name == name)
        if spec.kind == "file":
            total_bytes += path.stat().st_size
            file_counts[name] = 1
            continue
        count = 0
        for item in _walk_files(path):
            total_bytes += item.stat().st_size
            count += 1
        file_counts[name] = count

    free_bytes = (request.free_bytes or _free_bytes)(target_volume)
    required = total_bytes + int(total_bytes * _FREE_SPACE_MARGIN_RATIO) + _FREE_SPACE_MARGIN_BYTES
    if free_bytes < required:
        raise RelocationError(
            f"目标外接盘剩余空间不足：需要约 {required} 字节，可用 {free_bytes} 字节。"
        )

    for name, path in target.items():
        if not path.exists():
            continue
        spec = next(item for item in STORAGE_ROOTS if item.name == name)
        if spec.kind == "file":
            if not path.is_file():
                raise RelocationError(f"目标数据库位置不是普通文件：{path}")
            if not resuming:
                raise RelocationError(f"目标数据库已存在，拒绝覆盖：{path}")
        else:
            if not path.is_dir():
                raise RelocationError(f"目标存储位置不是目录：{path}")
            if resuming:
                continue
            foreign = [
                item for item in path.iterdir()
                if not item.name.startswith(_PART_PREFIX)
            ]
            if foreign:
                raise RelocationError(f"目标目录已有其他内容，拒绝混合两个工作区：{path}")

    return RelocationPlan(
        operation_id=operation_id, source=source, target=target,
        target_volume=target_volume, target_identity=target_identity, total_bytes=total_bytes,
        file_counts=file_counts, resuming=resuming,
        env_path=env_path, receipt_path=receipt_path, record_path=record_path,
    )


def _copy_file(source: Path, target: Path, *, resuming: bool) -> dict[str, object]:
    before = source.stat()
    if source.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise RelocationError(f"源文件不是普通文件：{source}")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise RelocationError(f"目标路径不是普通文件：{target}")
        source_sha = _file_sha256(source)
        if target.stat().st_size == before.st_size and _file_sha256(target) == source_sha:
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
            return {"size_bytes": before.st_size, "sha256": source_sha, "skipped": True}
        if not resuming:
            raise RelocationError(f"目标已有不同内容，拒绝覆盖：{target}")
        replaced = True
    else:
        replaced = False

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f"{_PART_PREFIX}{os.getpid()}-{uuid4().hex}.part"
    digest = hashlib.sha256()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as target_stream:
            while True:
                chunk = source_stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                target_stream.write(chunk)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        os.chmod(temporary, stat.S_IMODE(before.st_mode))
        os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = source.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns
        ):
            raise RelocationError(f"源文件在校验期间发生变化：{source}")
        if os.path.getsize(temporary) != before.st_size:
            raise RelocationError(f"复制大小不一致：{source}")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    target_sha = _file_sha256(target)
    if target_sha != digest.hexdigest():
        raise RelocationError(f"复制后摘要不一致：{target}")
    _fsync_directory(target.parent)
    return {
        "size_bytes": before.st_size, "sha256": target_sha,
        "skipped": False, "replaced": replaced,
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_tree(source: Path, target: Path, *, resuming: bool) -> dict[str, object]:
    if target.exists() and not target.is_dir():
        raise RelocationError(f"目标不是目录：{target}")
    target.mkdir(parents=True, exist_ok=True)
    files = sorted(_walk_files(source), key=lambda item: item.relative_to(source).as_posix())
    relative_paths = {item.relative_to(source).as_posix() for item in files}
    manifest = hashlib.sha256()
    copied = skipped = replaced = 0
    total_bytes = 0
    for item in files:
        relative = item.relative_to(source).as_posix()
        destination = target.joinpath(*relative.split("/"))
        result = _copy_file(item, destination, resuming=resuming)
        copied += 0 if result["skipped"] else 1
        skipped += 1 if result["skipped"] else 0
        replaced += 1 if result.get("replaced") else 0
        total_bytes += int(result["size_bytes"])
        manifest.update(
            f"{relative}\0{result['size_bytes']}\0{result['sha256']}\n".encode("utf-8")
        )
    foreign: list[str] = []
    for path in _walk_files(target):
        relative = path.relative_to(target).as_posix()
        if relative in relative_paths:
            continue
        if path.name.startswith(_PART_PREFIX):
            path.unlink(missing_ok=True)
            continue
        foreign.append(relative)
    if foreign:
        raise RelocationError(f"目标目录包含源中不存在的文件，拒绝继续：{foreign[:5]}")
    return {
        "file_count": len(files), "total_bytes": total_bytes,
        "copied": copied, "skipped": skipped, "replaced": replaced,
        "manifest_sha256": manifest.hexdigest(),
    }


def _sqlite_integrity(database_path: Path) -> str:
    with _connect(database_path) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row is not None else "missing"


def _database_counts(database_path: Path) -> dict[str, int]:
    with _connect(database_path) as connection:
        tables = [
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }


def _verify_database_copy(source: Path, target: Path) -> None:
    if _sqlite_integrity(source) != "ok":
        raise RelocationError(f"源数据库完整性检查未通过：{source}")
    if _sqlite_integrity(target) != "ok":
        raise RelocationError(f"目标数据库完整性检查未通过：{target}")
    source_counts = _database_counts(source)
    target_counts = _database_counts(target)
    if source_counts != target_counts:
        changed = sorted(
            table for table in source_counts.keys() | target_counts.keys()
            if source_counts.get(table) != target_counts.get(table)
        )
        raise RelocationError(f"数据库复制后行数不一致：{changed[:5]}")


def _backup_database(plan: RelocationPlan, receipt: dict[str, object] | None) -> dict[str, object]:
    source = plan.source["database"]
    if receipt is not None and isinstance(receipt.get("backup"), dict):
        recorded = Path(str(receipt["backup"].get("path", ""))).expanduser()
        if recorded.is_file() and _sqlite_integrity(recorded) == "ok":
            return {
                "path": str(recorded), "sha256": _file_sha256(recorded),
                "size_bytes": recorded.stat().st_size, "reused": True,
            }
    backup_directory = source.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_directory / f"{source.stem}.pre-relocation-{plan.operation_id}-{stamp}.sqlite3"
    temporary = backup.with_name(f".{backup.name}.{uuid4().hex}.tmp")
    origin = _connect(source)
    destination = sqlite3.connect(str(temporary))
    try:
        origin.backup(destination)
    finally:
        destination.close()
        origin.close()
    try:
        if _sqlite_integrity(temporary) != "ok":
            raise RelocationError(f"数据库备份完整性检查未通过：{temporary}")
        _fsync_directory(backup_directory)
        os.replace(temporary, backup)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "path": str(backup), "sha256": _file_sha256(backup),
        "size_bytes": backup.stat().st_size, "reused": False,
    }


def _install_database(
    source: Path, target: Path, backup: Path, *, resuming: bool,
) -> dict[str, object]:
    if not backup.is_file():
        raise RelocationError(f"数据库备份缺失：{backup}")
    result = _copy_file(backup, target, resuming=resuming)
    if _file_sha256(target) != _file_sha256(backup):
        raise RelocationError(f"目标数据库与备份摘要不一致：{target}")
    if _sqlite_integrity(target) != "ok":
        raise RelocationError(f"目标数据库完整性检查未通过：{target}")
    return {"source": str(source), "backup": str(backup), **result}


def _rebind_material_library(plan: RelocationPlan, *, verify_target: Callable[[], None]) -> dict[str, object]:
    database_path = plan.target["database"]
    old_library = plan.source["materials"].resolve()
    new_library = plan.target["materials"].resolve()
    with _connect(database_path) as connection:
        binding = connection.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()
    if binding is None:
        return {"status": "skipped", "reason": "素材目录尚未绑定工作区"}
    workspace_id = str(binding["workspace_id"])
    bound_library = Path(str(binding["library_path"])).expanduser().resolve()
    access = MaterialSourceFileAccess(lambda: _connect(database_path), new_library)
    if bound_library == new_library:
        # A previous run already committed this rebind; the dotenv write or the
        # final check did not finish.  Reuse the receipt instead of refusing.
        try:
            saved = access.material_rebind_receipt(workspace_id, plan.operation_id)
        except KeyError as exc:
            raise RelocationError("素材目录已指向目标位置，但缺少本操作的迁移回执。") from exc
        return {
            "status": str(saved["status"]), "workspace_id": workspace_id,
            "entry_count": int(saved.get("entry_count", 0)),
            "manifest_sha256": str(saved["manifest_sha256"]),
        }
    if bound_library != old_library:
        raise RelocationError(
            f"素材目录绑定与配置的素材根不一致：{bound_library} / {old_library}。"
            "请先核对设置，未迁移任何绑定。"
        )
    verify_target()
    prepared = access.prepare_material_rebind(
        workspace_id, old_directory=str(old_library), new_directory=str(new_library),
        operation_id=plan.operation_id,
    )
    if prepared["status"] == "applied":
        result = prepared
    else:
        verify_target()
        result = access.commit_material_rebind(
            workspace_id, operation_id=plan.operation_id,
            manifest_sha256=str(prepared["manifest_sha256"]),
        )
    return {
        "status": str(result["status"]), "workspace_id": workspace_id,
        "entry_count": int(result.get("entry_count", len(prepared.get("entries", [])))),
        "manifest_sha256": str(result["manifest_sha256"]),
    }


def _update_dotenv(plan: RelocationPlan, *, verify_target: Callable[[], None]) -> dict[str, object]:
    updates = {
        spec.env_key: str(plan.target[spec.name])
        for spec in STORAGE_ROOTS if spec.name in plan.target
    }
    current = _env_values(plan.env_path)
    if all(current.get(key) == value for key, value in updates.items()):
        return {"updated": False, "keys": sorted(updates)}
    prepared = prepare_dotenv_values(plan.env_path, updates)
    try:
        verify_target()
        commit_prepared_dotenv_values(prepared)
    finally:
        discard_prepared_dotenv_values(prepared)
    if any(_env_values(plan.env_path).get(key) != value for key, value in updates.items()):
        raise RelocationError("配置写入后回读不一致")
    return {"updated": True, "keys": sorted(updates)}


def _read_binding(database_path: Path) -> tuple[str, str] | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()
    return (str(row["workspace_id"]), str(row["library_path"])) if row is not None else None


def _already_applied(plan: RelocationPlan) -> bool:
    values = _env_values(plan.env_path)
    if any(values.get(spec.env_key) != str(plan.target[spec.name])
           for spec in STORAGE_ROOTS if spec.name in plan.target):
        return False
    if not plan.target["database"].is_file() or _sqlite_integrity(plan.target["database"]) != "ok":
        raise RelocationError("配置已指向目标位置，但目标数据库不可用。")
    binding = _read_binding(plan.target["database"])
    if binding is not None:
        if Path(binding[1]).expanduser().resolve() != plan.target["materials"].resolve():
            raise RelocationError("配置已指向目标位置，但素材目录绑定仍在旧位置。")
        # The applied receipt is not proof that the copied library is still
        # intact.  A damaged file makes this a resumable repair instead of a
        # false "already applied".
        with _connect(plan.target["database"]) as connection:
            rows = connection.execute(
                "SELECT file_path, size_bytes FROM material_ingestion_files WHERE workspace_id=?",
                (binding[0],),
            ).fetchall()
        for row in rows:
            path = Path(str(row["file_path"])).expanduser()
            if path.is_symlink() or not path.is_file() or path.stat().st_size != int(row["size_bytes"]):
                return False
    return True


def _verify_final(plan: RelocationPlan) -> None:
    records = volume_identity.load_volume_identity_record(plan.record_path) or {}
    record = records.get(str(plan.target_volume.resolve()), {})
    if record.get("uuid") != plan.target_identity.uuid:
        raise RelocationError("目标卷身份尚未安装到启动配置，请核对原回执后重试。")
    values = _env_values(plan.env_path)
    for spec in STORAGE_ROOTS:
        if spec.name in plan.target and values.get(spec.env_key) != str(plan.target[spec.name]):
            raise RelocationError(f"配置未指向目标位置：{spec.env_key}")
    database_path = plan.target["database"]
    if _sqlite_integrity(database_path) != "ok":
        raise RelocationError("目标数据库完整性检查未通过")
    binding = _read_binding(database_path)
    if binding is None:
        return
    workspace_id, library_path = binding
    if Path(library_path).expanduser().resolve() != plan.target["materials"].resolve():
        raise RelocationError("素材目录绑定未指向目标素材根")
    old_root = str(plan.source["materials"].resolve())
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT file_path FROM material_ingestion_files WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall()
    for row in rows:
        file_path = str(row["file_path"])
        if file_path == old_root or file_path.startswith(old_root + os.sep):
            raise RelocationError(f"素材登记仍指向旧素材根：{file_path}")
        path = Path(file_path).expanduser()
        if path.is_symlink() or not path.is_file():
            raise RelocationError(f"迁移后素材文件不可用：{file_path}")
    for path in plan.target.values():
        if not path.exists():
            raise RelocationError(f"目标存储位置缺失：{path}")


def run_relocation(request: RelocationRequest) -> dict[str, object]:
    """Run or resume a relocation and return its durable receipt payload."""
    initial = plan_relocation(request)
    descriptor = _acquire_execution_lock(request.lock_path)
    try:
        # Recompute under the execution lock so the copy cannot race a startup.
        plan = plan_relocation(request)
        _require_target_uuid(plan.target_volume, plan.target_identity.uuid, initial.target_identity.uuid)

        def verify_target() -> None:
            observed = _observe_target_volume(request, plan.target_volume)
            _require_target_uuid(plan.target_volume, observed.uuid, plan.target_identity.uuid)

        verify_target()
        previous = _load_receipt(plan.receipt_path)
        if previous is not None and previous.get("status") in {"identity", "applied"}:
            if _already_applied(plan):
                _verify_final(plan)
                verify_target()
                if previous["status"] == "applied":
                    return {**previous, "reapplied": True}
                previous.update(status="applied", applied_at=_now(), updated_at=_now())
                _write_receipt(plan.receipt_path, previous)
                return previous
            if previous.get("volume_identity") is None:
                raise RelocationError("旧迁移结果无法通过复核，原回执未绑定目标卷身份，已保留回执。")

        receipt: dict[str, object] = {
            "format": RECEIPT_FORMAT, "operation_id": plan.operation_id,
            "status": "started", "started_at": (previous or {}).get("started_at", _now()),
            "source": {k: str(v) for k, v in plan.source.items()},
            "target": {k: str(v) for k, v in plan.target.items()},
            "target_volume": str(plan.target_volume),
            "volume_identity": plan.target_identity.as_record(),
            "total_bytes": plan.total_bytes, "file_counts": plan.file_counts,
        }
        _write_receipt(plan.receipt_path, receipt)

        receipt["backup"] = _backup_database(plan, previous)
        receipt["status"] = "backup"
        receipt["updated_at"] = _now()
        verify_target()
        _write_receipt(plan.receipt_path, receipt)

        copies: dict[str, object] = {}
        for spec in STORAGE_ROOTS:
            if spec.name not in plan.source:
                continue
            verify_target()
            if spec.kind == "file":
                copies[spec.name] = _install_database(
                    plan.source[spec.name], plan.target[spec.name],
                    Path(str(receipt["backup"]["path"])), resuming=plan.resuming,
                )
            else:
                copies[spec.name] = _copy_tree(
                    plan.source[spec.name], plan.target[spec.name], resuming=plan.resuming,
                )
        receipt["copies"] = copies
        receipt["status"] = "copied"
        receipt["updated_at"] = _now()
        verify_target()
        _write_receipt(plan.receipt_path, receipt)
        _verify_database_copy(plan.source["database"], plan.target["database"])
        verify_target()
        receipt["rebind"] = _rebind_material_library(plan, verify_target=verify_target)
        receipt["status"] = "rebound"
        receipt["updated_at"] = _now()
        verify_target()
        _write_receipt(plan.receipt_path, receipt)

        volume_identity.save_volume_identity_record(plan.record_path, [plan.target_identity])
        receipt["status"] = "identity"
        receipt["updated_at"] = _now()
        verify_target()
        _write_receipt(plan.receipt_path, receipt)

        receipt["dotenv"] = _update_dotenv(plan, verify_target=verify_target)
        verify_target()
        _verify_final(plan)
        verify_target()
        receipt["status"] = "applied"
        receipt["applied_at"] = _now()
        receipt["updated_at"] = receipt["applied_at"]
        _write_receipt(plan.receipt_path, receipt)
        return receipt
    finally:
        _release_execution_lock(descriptor)


def _request_from_args(args: argparse.Namespace) -> RelocationRequest:
    from app.config import Settings

    settings = Settings.from_env()
    env_path = runtime_env_file_path()
    source: dict[str, Path] = {
        "database": Path(settings.database_path).expanduser(),
        "materials": Path(settings.video_inbox_path).expanduser(),
        "outputs": Path(settings.video_output_path).expanduser(),
        "drafts": Path(settings.draft_output_path).expanduser(),
    }
    if settings.temporary_root_path:
        source["temporary"] = Path(settings.temporary_root_path).expanduser()
    target_root = Path(args.target_root).expanduser()
    target = {name: target_root / path.name for name, path in source.items()}
    overrides = {
        "database": args.database_target,
        "materials": args.materials_target,
        "outputs": args.outputs_target,
        "drafts": args.drafts_target,
        "temporary": args.temporary_target,
    }
    for name, value in overrides.items():
        if value:
            if name not in source:
                raise RelocationError(f"当前未配置 {name} 根目录，不能指定其目标。")
            target[name] = Path(value).expanduser()
    if all(source[name] == target[name] for name in source):
        # The configuration already points at the target.  Re-running the
        # command must report the applied receipt instead of refusing an
        # overlap, so recover the original source from that receipt.
        receipt_path = (
            Path(args.receipt).expanduser() if args.receipt
            else env_path.parent / RECEIPT_FILENAME
        )
        stored = _load_receipt(receipt_path)
        recorded = stored.get("source") if stored is not None else None
        if (
            stored is None or stored.get("status") not in {"identity", "applied"}
            or not isinstance(recorded, dict) or set(recorded) != set(target)
        ):
            raise RelocationError(
                "配置已指向目标位置，但缺少可用的原迁移回执；请勿重复迁移。"
            )
        source = {name: Path(str(recorded[name])).expanduser() for name in target}
    return RelocationRequest(
        source=source, target=target, env_path=env_path,
        operation_id=args.operation_id,
        receipt_path=Path(args.receipt).expanduser() if args.receipt else None,
        record_path=Path(args.volume_record).expanduser() if args.volume_record else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把本机工作区迁移到外接盘：停机、备份、逐字节校验、受控重绑定。",
    )
    parser.add_argument("--target-root", required=True, help="外接盘上的目标根目录")
    parser.add_argument("--database-target", help="覆盖目标数据库文件路径")
    parser.add_argument("--materials-target", help="覆盖目标素材/待处理目录")
    parser.add_argument("--outputs-target", help="覆盖目标成片输出目录")
    parser.add_argument("--drafts-target", help="覆盖目标剪映草稿目录")
    parser.add_argument("--temporary-target", help="覆盖目标临时目录")
    parser.add_argument("--operation-id", help="可恢复操作的稳定编号")
    parser.add_argument("--receipt", help="迁移回执路径（默认在 .env 同目录）")
    parser.add_argument("--volume-record", help="卷身份记录路径（默认在 .env 同目录）")
    parser.add_argument("--dry-run", action="store_true", help="只执行预检，不写入任何文件")
    args = parser.parse_args(argv)

    try:
        request = _request_from_args(args)
        if args.dry_run:
            plan = plan_relocation(request)
            print(json.dumps({
                "format": "yaohe-workspace-relocation-plan/v1",
                "operation_id": plan.operation_id,
                "source": {k: str(v) for k, v in plan.source.items()},
                "target": {k: str(v) for k, v in plan.target.items()},
                "target_volume": str(plan.target_volume),
                "volume_identity": plan.target_identity.as_record(),
                "total_bytes": plan.total_bytes,
                "file_counts": plan.file_counts,
                "resuming": plan.resuming,
            }, ensure_ascii=False, indent=2))
            return 0
        receipt = run_relocation(request)
    except (RelocationError, volume_identity.VolumeIdentityError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
