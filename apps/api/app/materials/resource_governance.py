"""Versioned resource manifests and provenance for production inputs/outputs.

This store is deliberately small and orthogonal to the material evidence store:
material analysis remains the owner of media facts, while this module records
which version of a resource was available, adopted, locked into a task and
observed in an output.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import sqlite3
from typing import Literal
from uuid import uuid4

from app.models import utc_now


ResourceKind = Literal["material", "template", "font", "model", "service"]
ResourceState = Literal["candidate", "catalog_only", "planned", "connected"]
LicenseLayer = Literal["asset", "font", "model", "service"]
ResourceStage = Literal[
    "candidate", "installed", "imported", "adopted", "task_input",
    "timeline", "render", "output_observation",
]

_RESOURCE_STATES = {"candidate", "catalog_only", "planned", "connected"}
_LICENSE_LAYERS = {"asset", "font", "model", "service"}
_STAGES = {"candidate", "installed", "imported", "adopted", "task_input", "timeline", "render", "output_observation"}
_EXECUTABLE_STAGES = {"adopted", "task_input", "timeline", "render", "output_observation"}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def manifest_digest(*, resource_id: str, resource_version: str, source_revision: dict[str, str], metadata: dict[str, object]) -> str:
    """Return the stable digest used to lock a manifest version."""
    payload = {"resource_id": resource_id, "resource_version": resource_version,
               "source_revision": source_revision, "metadata": metadata}
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResourceLicense:
    id: str
    layer: LicenseLayer
    identifier: str
    source_ref: str = ""
    terms_ref: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceManifest:
    id: str
    workspace_id: str
    resource_id: str
    resource_kind: ResourceKind
    resource_version: str
    state: ResourceState
    source_revision: dict[str, str]
    digest: str
    licenses: tuple[ResourceLicense, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ResourceUsageRecord:
    id: str
    workspace_id: str
    manifest_id: str
    resource_version: str
    stage: ResourceStage
    task_id: str | None = None
    timeline_version_id: str | None = None
    artifact_ref: str | None = None
    observation_ref: str | None = None
    actor_ref: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


class ResourceManifestConflict(ValueError):
    """A locked resource version no longer matches the requested manifest."""


class ResourceGovernanceStore:
    """Durable owner for resource manifests, layered licenses and usage trace."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def save_manifest(self, manifest: ResourceManifest) -> ResourceManifest:
        self._validate_manifest(manifest)
        now = utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, digest, state FROM resource_manifests WHERE workspace_id=? AND resource_id=? AND resource_version=?",
                (manifest.workspace_id, manifest.resource_id, manifest.resource_version),
            ).fetchone()
            if existing is not None and (str(existing["id"]) != manifest.id or str(existing["digest"]) != manifest.digest):
                raise ResourceManifestConflict("同一资源版本的 manifest 已锁定，不能覆盖")
            conn.execute(
                """
                INSERT INTO resource_manifests
                    (id, workspace_id, resource_id, resource_kind, resource_version, state,
                     source_revision_json, digest, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state=excluded.state,
                    metadata_json=excluded.metadata_json, updated_at=excluded.updated_at
                """,
                (manifest.id, manifest.workspace_id, manifest.resource_id, manifest.resource_kind,
                 manifest.resource_version, manifest.state, _json(manifest.source_revision),
                 manifest.digest, _json(manifest.metadata), manifest.created_at.isoformat(), now.isoformat()),
            )
            for license_item in manifest.licenses:
                self._validate_license(license_item)
                conn.execute(
                    """
                    INSERT INTO resource_manifest_licenses
                        (id, workspace_id, manifest_id, layer, identifier, source_ref, terms_ref, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET identifier=excluded.identifier,
                        source_ref=excluded.source_ref, terms_ref=excluded.terms_ref,
                        metadata_json=excluded.metadata_json
                    """,
                    (license_item.id, manifest.workspace_id, manifest.id, license_item.layer,
                     license_item.identifier, license_item.source_ref, license_item.terms_ref,
                     _json(license_item.metadata)),
                )
        return self.get_manifest(manifest.workspace_id, manifest.id)

    def get_manifest(self, workspace_id: str, manifest_id: str) -> ResourceManifest:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_manifests WHERE workspace_id=? AND id=?",
                (workspace_id, manifest_id),
            ).fetchone()
            if row is None:
                raise KeyError(manifest_id)
            licenses = conn.execute(
                "SELECT * FROM resource_manifest_licenses WHERE workspace_id=? AND manifest_id=? ORDER BY layer, id",
                (workspace_id, manifest_id),
            ).fetchall()
        return self._manifest_from_rows(row, licenses)

    def list_manifests(self, workspace_id: str, *, resource_id: str | None = None) -> list[ResourceManifest]:
        query = "SELECT * FROM resource_manifests WHERE workspace_id=?"
        params: list[object] = [workspace_id]
        if resource_id is not None:
            query += " AND resource_id=?"
            params.append(resource_id)
        query += " ORDER BY resource_id, resource_version, id"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            license_rows = conn.execute(
                "SELECT * FROM resource_manifest_licenses WHERE workspace_id=? ORDER BY manifest_id, layer, id",
                (workspace_id,),
            ).fetchall()
        by_manifest: dict[str, list[sqlite3.Row]] = {}
        for row in license_rows:
            by_manifest.setdefault(str(row["manifest_id"]), []).append(row)
        return [self._manifest_from_rows(row, by_manifest.get(str(row["id"]), [])) for row in rows]

    def record_usage(self, record: ResourceUsageRecord) -> ResourceUsageRecord:
        if record.stage not in _STAGES:
            raise ValueError("资源使用阶段无效")
        with self._connect() as conn:
            manifest_row = conn.execute(
                "SELECT * FROM resource_manifests WHERE workspace_id=? AND id=?",
                (record.workspace_id, record.manifest_id),
            ).fetchone()
            if manifest_row is None:
                raise KeyError(record.manifest_id)
            if str(manifest_row["resource_version"]) != record.resource_version:
                raise ResourceManifestConflict("任务引用的资源版本与 manifest 不一致")
            if record.stage in _EXECUTABLE_STAGES and str(manifest_row["state"]) != "connected":
                raise ValueError("candidate/catalog_only/planned 资源不能进入任务、时间线或产物")
            if record.stage in {"task_input", "timeline", "render", "output_observation"} and not record.task_id:
                raise ValueError("任务资源引用必须绑定 task_id")
            if record.stage in {"timeline", "render", "output_observation"} and not record.timeline_version_id:
                raise ValueError("时间线、渲染和输出观察必须绑定 timeline_version_id")
            if record.stage in {"render", "output_observation"} and not record.artifact_ref:
                raise ValueError("渲染和输出观察必须绑定 artifact_ref")
            if record.stage == "output_observation" and not record.observation_ref:
                raise ValueError("输出观察必须绑定 observation_ref")
            conn.execute(
                """
                INSERT OR IGNORE INTO resource_usage_records
                    (id, workspace_id, manifest_id, resource_version, stage, task_id,
                     timeline_version_id, artifact_ref, observation_ref, actor_ref, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record.id, record.workspace_id, record.manifest_id, record.resource_version,
                 record.stage, record.task_id, record.timeline_version_id, record.artifact_ref,
                 record.observation_ref, record.actor_ref, _json(record.metadata), record.created_at.isoformat()),
            )
        return record

    def list_usage(self, workspace_id: str, *, task_id: str | None = None,
                   manifest_id: str | None = None, artifact_ref: str | None = None) -> list[ResourceUsageRecord]:
        query = "SELECT * FROM resource_usage_records WHERE workspace_id=?"
        params: list[object] = [workspace_id]
        for column, value in (("task_id", task_id), ("manifest_id", manifest_id), ("artifact_ref", artifact_ref)):
            if value is not None:
                query += f" AND {column}=?"
                params.append(value)
        query += " ORDER BY created_at, id"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._usage_from_row(row) for row in rows]

    def provenance_for_task(self, workspace_id: str, task_id: str, *, timeline_version_id: str | None = None) -> list[dict[str, object]]:
        records = self.list_usage(workspace_id, task_id=task_id)
        if timeline_version_id is not None:
            records = [item for item in records if item.timeline_version_id in {None, timeline_version_id}]
        manifests = {item.manifest_id: self.get_manifest(workspace_id, item.manifest_id) for item in records}
        return [{"manifest": self._manifest_payload(manifests[item.manifest_id]), "usage": self._usage_payload(item)} for item in records]

    @staticmethod
    def _validate_manifest(manifest: ResourceManifest) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (manifest.id, manifest.workspace_id, manifest.resource_id, manifest.resource_version, manifest.digest)):
            raise ValueError("资源 manifest 缺少稳定身份、版本或摘要")
        if manifest.resource_kind not in {"material", "template", "font", "model", "service"}:
            raise ValueError("资源类型无效")
        if manifest.state not in _RESOURCE_STATES:
            raise ValueError("资源目录状态无效")
        if not isinstance(manifest.source_revision, dict):
            raise ValueError("资源 manifest 必须保存来源修订")
        if manifest.digest != manifest_digest(
            resource_id=manifest.resource_id, resource_version=manifest.resource_version,
            source_revision=manifest.source_revision, metadata=manifest.metadata,
        ):
            raise ValueError("资源 manifest 摘要与版本内容不一致")
        if len(set(item.id for item in manifest.licenses)) != len(manifest.licenses):
            raise ValueError("资源许可记录 ID 不能重复")

    @staticmethod
    def _validate_license(item: ResourceLicense) -> None:
        if item.layer not in _LICENSE_LAYERS or not item.identifier.strip():
            raise ValueError("资源许可必须包含分层和许可标识")

    @classmethod
    def _manifest_from_rows(cls, row: sqlite3.Row, licenses: Iterable[sqlite3.Row]) -> ResourceManifest:
        return ResourceManifest(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), resource_id=str(row["resource_id"]),
            resource_kind=str(row["resource_kind"]), resource_version=str(row["resource_version"]),
            state=str(row["state"]), source_revision=json.loads(str(row["source_revision_json"])),
            digest=str(row["digest"]), licenses=tuple(ResourceLicense(
                id=str(item["id"]), layer=str(item["layer"]), identifier=str(item["identifier"]),
                source_ref=str(item["source_ref"] or ""), terms_ref=str(item["terms_ref"] or ""),
                metadata=json.loads(str(item["metadata_json"])),
            ) for item in licenses), metadata=json.loads(str(row["metadata_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])), updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _usage_from_row(row: sqlite3.Row) -> ResourceUsageRecord:
        return ResourceUsageRecord(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), manifest_id=str(row["manifest_id"]),
            resource_version=str(row["resource_version"]), stage=str(row["stage"]),
            task_id=str(row["task_id"]) if row["task_id"] else None,
            timeline_version_id=str(row["timeline_version_id"]) if row["timeline_version_id"] else None,
            artifact_ref=str(row["artifact_ref"]) if row["artifact_ref"] else None,
            observation_ref=str(row["observation_ref"]) if row["observation_ref"] else None,
            actor_ref=str(row["actor_ref"]) if row["actor_ref"] else None,
            metadata=json.loads(str(row["metadata_json"])), created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _manifest_payload(item: ResourceManifest) -> dict[str, object]:
        return {"id": item.id, "resource_id": item.resource_id, "resource_kind": item.resource_kind,
                "resource_version": item.resource_version, "state": item.state, "source_revision": item.source_revision,
                "digest": item.digest, "licenses": [{"id": license_item.id, "layer": license_item.layer,
                "identifier": license_item.identifier, "source_ref": license_item.source_ref, "terms_ref": license_item.terms_ref,
                "metadata": license_item.metadata} for license_item in item.licenses], "metadata": item.metadata}

    @staticmethod
    def _usage_payload(item: ResourceUsageRecord) -> dict[str, object]:
        return {"id": item.id, "stage": item.stage, "resource_version": item.resource_version,
                "task_id": item.task_id, "timeline_version_id": item.timeline_version_id,
                "artifact_ref": item.artifact_ref, "observation_ref": item.observation_ref,
                "actor_ref": item.actor_ref, "metadata": item.metadata, "created_at": item.created_at.isoformat()}

