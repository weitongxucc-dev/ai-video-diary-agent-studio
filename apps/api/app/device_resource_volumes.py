"""Current filesystem-volume facts for host-owned write locations."""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
import os
import tempfile

import psutil


def narration_write_paths(*, database_path, temporary_root, model_path):
    """Locations actually used by the Qwen provider, not inferred from DB root."""
    return {'database': Path(database_path).resolve().parent,
            'temporary': Path(temporary_root).expanduser().resolve(),
            'model_cache': Path(model_path).expanduser().resolve().parent / '.cache'}


def existing_volume(path: str | Path) -> tuple[int, Path]:
    """Resolve absent output directories against their actual existing ancestor."""
    current = Path(path).expanduser().resolve()
    while not current.exists():
        if current == current.parent:
            raise OSError("Write volume is unavailable")
        current = current.parent
    if not current.is_dir():
        raise ValueError("Write location must be a directory")
    return current.stat().st_dev, current


def sample_write_volumes(paths: Mapping[str, str | Path], reservations: Mapping[str, int], *,
                         reserve_bytes: int, verify_writable: bool = False) -> dict[str, dict]:
    """Reservations are additional live bytes, summed only on the same volume.

    reserve_bytes is retained once per volume. Sampling never persists free space
    as capacity. Preparing a profile may additionally exercise an actual file
    write in each configured directory; read-only admission does not write.
    """
    if set(paths) != set(reservations) or type(reserve_bytes) is not int or reserve_bytes < 0:
        raise ValueError("Disk reservation roles must match the configured write paths")
    volumes = {}
    for role, path in paths.items():
        amount = reservations[role]
        if type(amount) is not int or amount < 0:
            raise ValueError("Disk reservations must be nonnegative byte counts")
        volume, ancestor = existing_volume(path)
        item = volumes.setdefault(volume, {"roles": [], "path": ancestor, "required": reserve_bytes})
        item["roles"].append(role)
        item["required"] += amount
        if verify_writable:
            target = Path(path).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=target) as probe:
                probe.write(b"device-resource-write-probe")
                probe.flush()
                os.fsync(probe.fileno())
            if existing_volume(target)[0] != volume:
                raise OSError("Write volume changed during preparation")
    checks = {}
    for volume, item in volumes.items():
        usage = psutil.disk_usage(str(item["path"]))
        if type(usage.free) is not int or usage.free < 0:
            raise OSError("Write volume available space is unknown")
        for role in item["roles"]:
            checks[role] = {"volume_id": volume, "roles": sorted(item["roles"]),
                "actual": usage.free, "minimum": item["required"],
                "passed": usage.free >= item["required"]}
    return checks
