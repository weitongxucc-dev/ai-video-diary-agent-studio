"""Stable identity of the external volume that holds a workspace.

A mount-point check alone cannot tell the original data disk apart from another
disk mounted under the same name.  The relocation command records the volume's
UUID here; startup compares the configured paths against that record and
refuses to run when the volume is absent, replaced, or cannot be identified.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

IDENTITY_RECORD_FORMAT = "yaohe-volume-identity/v1"
IDENTITY_RECORD_FILENAME = "external-volume-identity.json"


class VolumeIdentityError(RuntimeError):
    """The external volume cannot be proven to be the recorded data disk."""


@dataclass(frozen=True)
class VolumeIdentity:
    mount_point: str
    uuid: str
    volume_name: str = ""
    filesystem: str = ""

    def as_record(self) -> dict[str, str]:
        return {
            "uuid": self.uuid,
            "volume_name": self.volume_name,
            "filesystem": self.filesystem,
        }


def volume_root(path: str | Path) -> Path | None:
    """Return the ``/Volumes/<name>`` mount root for an external path."""
    parts = Path(path).expanduser().parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return Path("/Volumes") / parts[2]
    return None


def record_path_for_env(env_path: str | Path) -> Path:
    """The identity record lives beside the dotenv file, not on the external disk."""
    return Path(env_path).expanduser().parent / IDENTITY_RECORD_FILENAME


def probe_volume_identity(volume: str | Path) -> VolumeIdentity:
    """Read the volume UUID through DiskArbitration (``diskutil`` on macOS).

    Only an identity the platform can actually prove is accepted.  A volume
    without a UUID is rejected instead of being compared by its display name,
    because the name is exactly what a replacement disk can reuse.
    """
    mount = Path(volume).expanduser()
    if sys.platform != "darwin":
        raise VolumeIdentityError("当前平台不支持外接盘卷身份核对")
    try:
        result = subprocess.run(
            ["diskutil", "info", "-plist", str(mount)],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VolumeIdentityError(f"无法读取外接盘身份：{mount}") from exc
    if result.returncode != 0:
        raise VolumeIdentityError(f"无法读取外接盘身份：{mount}")
    try:
        info = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as exc:
        raise VolumeIdentityError(f"外接盘身份响应无效：{mount}") from exc
    if not isinstance(info, dict):
        raise VolumeIdentityError(f"外接盘身份响应无效：{mount}")
    uuid = ""
    for key in ("VolumeUUID", "APFSVolumeUUID", "MediaUUID"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            uuid = value.strip()
            break
    if not uuid:
        raise VolumeIdentityError(f"外接盘缺少稳定卷标识：{mount}")
    return VolumeIdentity(
        mount_point=str(mount.resolve()),
        uuid=uuid,
        volume_name=str(info.get("VolumeName") or ""),
        filesystem=str(info.get("FilesystemType") or info.get("FilesystemName") or ""),
    )


def load_volume_identity_record(path: str | Path) -> dict[str, dict[str, str]] | None:
    target = Path(path).expanduser()
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VolumeIdentityError(f"卷身份记录无法读取：{target}") from exc
    if not isinstance(payload, dict) or payload.get("format") != IDENTITY_RECORD_FORMAT:
        raise VolumeIdentityError(f"卷身份记录格式无效：{target}")
    volumes = payload.get("volumes")
    if not isinstance(volumes, dict) or any(
        not isinstance(key, str) or not isinstance(value, dict)
        for key, value in volumes.items()
    ):
        raise VolumeIdentityError(f"卷身份记录格式无效：{target}")
    return {str(key): {str(name): str(item) for name, item in value.items()} for key, value in volumes.items()}


def save_volume_identity_record(
    path: str | Path, identities: Iterable[VolumeIdentity],
) -> dict[str, dict[str, str]]:
    """Merge identities into the record atomically, preserving other volumes."""
    target = Path(path).expanduser()
    existing = load_volume_identity_record(target) or {}
    merged = dict(existing)
    for identity in identities:
        if not isinstance(identity, VolumeIdentity) or not identity.uuid.strip():
            raise VolumeIdentityError("卷身份缺少有效标识")
        merged[str(Path(identity.mount_point).expanduser())] = identity.as_record()
    payload = json.dumps(
        {"format": IDENTITY_RECORD_FORMAT, "volumes": merged},
        ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return merged


def verify_recorded_volumes(
    volumes: Iterable[Path],
    *,
    record_path: str | Path,
    probe=probe_volume_identity,
) -> dict[str, VolumeIdentity]:
    """Fail closed when a recorded volume is now a different disk.

    Volumes absent from the record are not yet protected: they have no recorded
    identity to compare against.  Every recorded volume must still be mounted
    and must report the recorded UUID; an unreadable identity is a failure, not
    a reason to continue.
    """
    record = load_volume_identity_record(record_path)
    if record is None:
        return {}
    verified: dict[str, VolumeIdentity] = {}
    for volume in volumes:
        mount = Path(volume).expanduser()
        key = str(mount.resolve())
        expected = record.get(key)
        if expected is None:
            continue
        expected_uuid = str(expected.get("uuid") or "").strip()
        if not expected_uuid:
            raise VolumeIdentityError(f"卷身份记录缺少磁盘标识：{mount}")
        if not mount.is_mount():
            raise VolumeIdentityError(f"外接存储未挂载：{mount}。请连接原数据盘后重试。")
        actual = probe(mount)
        if actual.uuid != expected_uuid:
            raise VolumeIdentityError(
                f"外接盘身份不匹配：{mount}。当前磁盘不是上次使用的数据盘，已停止读取和写入。"
            )
        verified[key] = actual
    return verified
