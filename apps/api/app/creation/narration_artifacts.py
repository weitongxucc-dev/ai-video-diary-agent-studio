"""Canonical narration cache records, date codec and authorized logical references."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal
from urllib.parse import urlsplit

from app.materials.contracts import MediaAuthorityError as _MediaAuthorityError
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime


@dataclass
class NarrationArtifactRecord:
    id: str
    workspace_id: str
    script_id: str
    voice_profile_id: str | None
    cache_key: str
    audio_path: str
    duration_seconds: float
    sample_rate: int | None
    model_id: str | None
    episode_id: str | None = None
    task_id: str | None = None
    language: str = "zh"
    pronunciation_version: str = ""
    source_kind: Literal["generated", "recorded"] = "generated"
    source_artifact_ref: str | None = None
    source_sha256: str | None = None
    source_script_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)


def narration_artifact_from_payload(
    data: dict[str, object],
) -> NarrationArtifactRecord:
    data = _with_datetime(data, "created_at")
    return NarrationArtifactRecord(**data)


class NarrationArtifactStore:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        recorded_source_is_current: Callable[..., bool],
        resolve_workspace_media_reference: Callable[..., Path],
    ) -> None:
        self._connect = connection_factory
        self.recorded_source_is_current = recorded_source_is_current
        self.resolve_workspace_media_reference = resolve_workspace_media_reference

    @staticmethod
    def narration_artifact_reference(cache_key: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cache_key):
            raise ValueError("旁白产物标识无效")
        return f"narration://artifact/{cache_key}"

    def resolve_authorized_narration_audio_reference(
        self,
        workspace_id: str,
        reference: str,
        *, connection: sqlite3.Connection | None = None,
    ) -> Path:
        value = reference.strip()
        if value.startswith("workspace://"):
            return self.resolve_workspace_media_reference(
                workspace_id,
                value,
                require_registered=True,
                connection=connection,
            )
        parsed = urlsplit(value)
        if (
            parsed.scheme != "narration"
            or parsed.netloc != "artifact"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[a-zA-Z0-9_-]{1,128}", parsed.path)
        ):
            raise _MediaAuthorityError(
                reason="logical_media_reference_required",
                impact="任意本机路径不能作为旁白或渲染输入。",
                repair_action="从当前工作区选择 workspace:// 媒体，或使用已有 narration://artifact 资源。",
            )
        artifact = self.get_narration_artifact(
            workspace_id,
            parsed.path.removeprefix("/"),
            connection=connection,
        )
        if artifact is None:
            raise _MediaAuthorityError(
                reason="narration_audio_unavailable",
                impact="当前工作区不存在可用的旁白产物，未调用渲染器。",
                repair_action="重新生成旁白，或选择当前工作区已授权的媒体引用。",
            )
        return Path(artifact.audio_path)

    def get_narration_artifact(
        self,
        workspace_id: str,
        cache_key: str,
        *, connection: sqlite3.Connection | None = None,
    ) -> NarrationArtifactRecord | None:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM narration_artifacts
                WHERE workspace_id = ? AND cache_key = ?
                """,
                (workspace_id, cache_key),
            ).fetchone()
        if row is None:
            return None
        record = narration_artifact_from_payload(json.loads(str(row["payload"])))
        if not Path(record.audio_path).is_file():
            return None
        if record.source_kind == "recorded" and not self.recorded_source_is_current(record, connection=connection):
            return None
        return record

    def save_narration_artifact(
        self,
        artifact: NarrationArtifactRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> NarrationArtifactRecord:
        if connection is None:
            with self._connect() as conn:
                return self.save_narration_artifact(artifact, connection=conn)
        connection.execute(
            """
            INSERT INTO narration_artifacts (id, workspace_id, cache_key, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                id = excluded.id,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                artifact.id,
                artifact.workspace_id,
                artifact.cache_key,
                _json_payload(artifact),
                artifact.created_at.isoformat(),
            ),
        )
        return artifact
