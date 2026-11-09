"""Immutable material hash values and the shared pure stat identity factory."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MaterialHashCandidate:
    workspace_id: str
    asset_id: str
    file_path: str
    provisional_revision: str
    stage_input_digest: str
    unit_key: str


@dataclass(frozen=True)
class MaterialHashResult:
    candidate: MaterialHashCandidate
    source_sha256: str


def material_hash_candidate(
    workspace_id: str, bound_path: str, path: Path, stat: os.stat_result, asset_id: str
) -> MaterialHashCandidate:
    revision_input = {
        "workspace_id": workspace_id,
        "bound_path": bound_path,
        "file_path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "inode": stat.st_ino,
    }
    provisional = hashlib.sha256(
        json.dumps(revision_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stage_input = hashlib.sha256(
        json.dumps(
            {"stage": "hash:v1", "algorithm": "sha256", "provisional_revision": provisional},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return MaterialHashCandidate(
        workspace_id, asset_id, str(path), provisional, stage_input,
        f"hash:v1:{stage_input}",
    )
