"""Persistent evidence, assertions and index freshness for material data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import sqlite3
from typing import Literal
from uuid import uuid4

from app.materials.contracts import MaterialAsset
from app.models import utc_now


EvidenceStatus = Literal["valid", "stale", "unresolved"]
AssertionOrigin = Literal[
    "visual_observation",
    "audio_transcript",
    "ocr_observation",
    "business_fact",
    "user_note",
    "user_confirmation",
    "model_inference",
]
AssertionStatus = Literal["candidate", "confirmed", "rejected", "unresolved"]
IndexStatus = Literal["building", "ready", "stale", "failed"]
FieldValueType = Literal[
    "text", "integer", "number", "boolean", "enum", "datetime", "range", "object_ref", "list"
]
FieldDefinitionStatus = Literal["active", "deprecated"]
KnowledgeClaimOrigin = Literal[
    "business_fact", "model_inference", "user_note", "user_confirmation"
]
KnowledgeClaimStatus = Literal["candidate", "confirmed", "rejected", "unresolved"]
SegmentLineageRelation = Literal["preserved", "split", "merge", "resegmented"]
ClipReferenceMigrationStatus = Literal["current", "migrated", "needs_review"]
QualityProfileStatus = Literal["observed", "partial", "unavailable", "unknown"]
QualityMetricStatus = Literal["observed", "unavailable", "unknown"]
IndexRebuildJobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


def _migration_status_for_relation(relation: str) -> ClipReferenceMigrationStatus:
    """Derive the status for rows written before the explicit status column."""
    if relation == "preserved":
        return "current"
    if relation == "resegmented":
        return "migrated"
    return "needs_review"


QUALITY_METRIC_NAMES = (
    "clarity", "exposure", "stability", "composition", "safe_crop",
    "audio", "ocr_readability", "action_completeness",
)


@dataclass(frozen=True)
class UnitConversionRule:
    family: str
    unit: str
    canonical_unit: str
    factor: float
    offset: float = 0.0


@dataclass(frozen=True)
class MaterialQualityProfile:
    id: str
    workspace_id: str
    asset_id: str
    source_revision: dict[str, str]
    source_digest: str | None
    analysis_run_id: str | None
    profile_version: str
    status: QualityProfileStatus
    metrics: dict[str, dict[str, object]]
    unavailable_reasons: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialIndexRebuildJob:
    id: str
    workspace_id: str
    asset_id: str
    index_type: str
    source_revision: dict[str, str]
    source_digest: str | None
    index_version: str
    status: IndexRebuildJobStatus = "queued"
    attempt_count: int = 0
    error_message: str | None = None
    requested_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialClipReferenceMigration:
    id: str
    workspace_id: str
    asset_id: str
    old_segment_id: str
    old_segment_version: int
    new_segment_id: str | None
    new_segment_version: int | None
    relation: Literal["preserved", "split", "merge", "resegmented", "unresolved"]
    status: ClipReferenceMigrationStatus
    explanation: str
    source_revision: dict[str, str]
    created_at: datetime = field(default_factory=utc_now)


_UNIT_RULES: tuple[UnitConversionRule, ...] = (
    UnitConversionRule("time", "ms", "s", 0.001),
    UnitConversionRule("time", "s", "s", 1.0),
    UnitConversionRule("time", "min", "s", 60.0),
    UnitConversionRule("length", "mm", "mm", 1.0),
    UnitConversionRule("length", "cm", "mm", 10.0),
    UnitConversionRule("length", "m", "mm", 1000.0),
    UnitConversionRule("mass", "g", "g", 1.0),
    UnitConversionRule("mass", "kg", "g", 1000.0),
    UnitConversionRule("rate", "unit/min", "unit/min", 1.0),
    UnitConversionRule("rate", "unit/hour", "unit/min", 1.0 / 60.0),
)


def convert_unit(value: int | float, *, family: str, from_unit: str, to_unit: str) -> float:
    """Convert a numeric field only inside one declared unit family."""
    source = next((rule for rule in _UNIT_RULES if rule.family == family and rule.unit == from_unit), None)
    target = next((rule for rule in _UNIT_RULES if rule.family == family and rule.unit == to_unit), None)
    if source is None or target is None:
        raise ValueError("没有适用的单位换算规则")
    canonical = float(value) * source.factor + source.offset
    return (canonical - target.offset) / target.factor


def normalize_field_value(value: object, *, value_type: FieldValueType, unit_family: str | None = None,
                          unit: str | None = None, canonical_unit: str | None = None) -> object:
    if value_type in {"integer", "number", "range"}:
        if value_type == "range":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError("范围字段必须包含两个数值")
            converted = [normalize_field_value(item, value_type="number", unit_family=unit_family,
                                                unit=unit, canonical_unit=canonical_unit) for item in value]
            return converted
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("数值字段必须是数字")
        if value_type == "integer" and int(value) != value:
            raise ValueError("整数型字段不能包含小数")
        if unit_family and unit and canonical_unit:
            value = convert_unit(value, family=unit_family, from_unit=unit, to_unit=canonical_unit)
        return int(value) if value_type == "integer" else float(value)
    return value


@dataclass(frozen=True)
class MaterialEvidenceRef:
    id: str
    workspace_id: str
    asset_id: str
    source_ref: str
    source_revision: dict[str, str]
    source_time_range: tuple[float, float] | None = None
    segment_id: str | None = None
    locator_type: str = "file"
    locator: dict[str, object] = field(default_factory=dict)
    modality: str = "unknown"
    model_or_rule: str = ""
    analysis_version: str = ""
    sampling_coverage: dict[str, object] = field(default_factory=dict)
    status: EvidenceStatus = "valid"
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialAssetAssertion:
    id: str
    workspace_id: str
    asset_id: str
    field_ref: str
    value: object
    unit: str | None = None
    qualifiers: dict[str, object] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    segment_id: str | None = None
    origin: AssertionOrigin = "model_inference"
    confidence: float | None = None
    status: AssertionStatus = "candidate"
    asserted_by: str = ""
    supersedes_ref: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialIndexRecord:
    id: str
    workspace_id: str
    asset_id: str
    index_type: str
    source_revision: dict[str, str]
    source_digest: str | None
    model_or_rule: str
    index_version: str
    coverage: dict[str, object]
    status: IndexStatus
    invalid_reason: str | None = None
    built_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class SemanticFieldDefinition:
    id: str
    workspace_id: str
    canonical_id: str
    display_name: str
    value_type: FieldValueType
    unit_family: str | None = None
    allowed_units: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = ()
    version: int = 1
    status: FieldDefinitionStatus = "active"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class KnowledgeClaim:
    id: str
    workspace_id: str
    knowledge_item_id: str
    knowledge_version: str
    field_ref: str
    value: object
    unit: str | None = None
    qualifiers: dict[str, object] = field(default_factory=dict)
    evidence_refs: tuple[dict[str, object], ...] = ()
    origin: KnowledgeClaimOrigin = "business_fact"
    confidence: float | None = None
    status: KnowledgeClaimStatus = "candidate"
    asserted_by: str = ""
    supersedes_ref: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialSegmentLineage:
    workspace_id: str
    asset_id: str
    parent_segment_id: str
    child_segment_id: str
    relation: SegmentLineageRelation
    source_revision: dict[str, str]
    created_at: datetime = field(default_factory=utc_now)


class MaterialDataGovernanceStore:
    """Own durable data-governance rows for material analysis outputs."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def upsert_field_definition(self, item: SemanticFieldDefinition) -> SemanticFieldDefinition:
        if not item.workspace_id.strip() or not item.canonical_id.strip() or not item.display_name.strip():
            raise ValueError("字段定义必须包含工作区、规范标识和显示名称")
        if item.version < 1 or item.value_type not in {
            "text", "integer", "number", "boolean", "enum", "datetime", "range", "object_ref", "list"
        }:
            raise ValueError("字段定义的类型或版本无效")
        if len(set(item.allowed_units)) != len(item.allowed_units):
            raise ValueError("字段允许单位不能重复")
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO semantic_field_definitions
                    (id, workspace_id, canonical_id, display_name, value_type, unit_family,
                     allowed_units_json, aliases_json, applies_to_json, version, status,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, canonical_id, version) DO UPDATE SET
                    display_name=excluded.display_name,
                    value_type=excluded.value_type,
                    unit_family=excluded.unit_family,
                    allowed_units_json=excluded.allowed_units_json,
                    aliases_json=excluded.aliases_json,
                    applies_to_json=excluded.applies_to_json,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    item.id, item.workspace_id, item.canonical_id.strip(), item.display_name.strip(),
                    item.value_type, item.unit_family, _json(item.allowed_units), _json(item.aliases),
                    _json(item.applies_to), item.version, item.status, item.created_at.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.get_field_definition(item.workspace_id, item.canonical_id, version=item.version)

    def get_field_definition(
        self, workspace_id: str, canonical_id: str, *, version: int | None = None
    ) -> SemanticFieldDefinition:
        query = "SELECT * FROM semantic_field_definitions WHERE workspace_id=? AND canonical_id=?"
        params: list[object] = [workspace_id, canonical_id]
        if version is None:
            query += " ORDER BY version DESC LIMIT 1"
        else:
            query += " AND version=?"
            params.append(version)
        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        if row is None:
            raise KeyError(canonical_id)
        return self._field_from_row(row)

    def list_field_definitions(
        self, workspace_id: str, *, include_deprecated: bool = False
    ) -> list[SemanticFieldDefinition]:
        query = "SELECT * FROM semantic_field_definitions WHERE workspace_id=?"
        params: list[object] = [workspace_id]
        if not include_deprecated:
            query += " AND status='active'"
        query += " ORDER BY canonical_id, version DESC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._field_from_row(row) for row in rows]

    def normalize_claim_value(self, workspace_id: str, field_ref: str, value: object, *, unit: str | None = None) -> object:
        definition = self.get_field_definition(workspace_id, field_ref)
        if unit is not None and definition.allowed_units and unit not in definition.allowed_units:
            raise ValueError("断言单位不在字段允许范围内")
        canonical_unit = definition.allowed_units[0] if definition.allowed_units else None
        return normalize_field_value(
            value, value_type=definition.value_type, unit_family=definition.unit_family,
            unit=unit, canonical_unit=canonical_unit,
        )

    def record_quality_profile(self, item: MaterialQualityProfile) -> MaterialQualityProfile:
        if item.status not in {"observed", "partial", "unavailable", "unknown"}:
            raise ValueError("质量档案状态无效")
        if set(item.metrics) - set(QUALITY_METRIC_NAMES):
            raise ValueError("质量档案包含未知指标")
        for name, metric in item.metrics.items():
            if not isinstance(metric, dict) or metric.get("status") not in {"observed", "unavailable", "unknown"}:
                raise ValueError(f"质量指标 {name} 必须明确状态")
            if metric.get("status") != "observed" and metric.get("value") is not None:
                raise ValueError(f"不可用质量指标 {name} 不能伪造分数")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO material_quality_profiles
                    (id, workspace_id, asset_id, source_revision_json, source_digest,
                     analysis_run_id, profile_version, status, metrics_json,
                     unavailable_reasons_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, asset_id, source_revision_json, profile_version)
                DO UPDATE SET source_digest=excluded.source_digest,
                    analysis_run_id=excluded.analysis_run_id, status=excluded.status,
                    metrics_json=excluded.metrics_json,
                    unavailable_reasons_json=excluded.unavailable_reasons_json,
                    updated_at=excluded.updated_at
                """,
                (item.id, item.workspace_id, item.asset_id, _json(item.source_revision), item.source_digest,
                 item.analysis_run_id, item.profile_version, item.status, _json(item.metrics),
                 _json(item.unavailable_reasons), item.created_at.isoformat(), item.updated_at.isoformat()),
            )
        return self.list_quality_profiles(item.workspace_id, item.asset_id, limit=1)[0]

    def list_quality_profiles(self, workspace_id: str, asset_id: str, *, limit: int = 20) -> list[MaterialQualityProfile]:
        if limit < 1 or limit > 100:
            raise ValueError("质量档案查询数量必须在 1 到 100 之间")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_quality_profiles WHERE workspace_id=? AND asset_id=? ORDER BY updated_at DESC, id DESC LIMIT ?",
                (workspace_id, asset_id, limit),
            ).fetchall()
        return [self._quality_from_row(row) for row in rows]

    def queue_index_rebuild(self, workspace_id: str, asset_id: str, *, index_type: str,
                            source_revision: dict[str, str], source_digest: str | None,
                            index_version: str) -> MaterialIndexRebuildJob:
        item = MaterialIndexRebuildJob(
            id=f"idxjob_{uuid4().hex}", workspace_id=workspace_id, asset_id=asset_id,
            index_type=index_type, source_revision=dict(source_revision), source_digest=source_digest,
            index_version=index_version,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO material_index_rebuild_jobs
                    (id, workspace_id, asset_id, index_type, source_revision_json, source_digest,
                     index_version, status, attempt_count, error_message, requested_at,
                     started_at, finished_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, NULL, ?, NULL, NULL, ?)
                ON CONFLICT(workspace_id, asset_id, index_type, source_revision_json, index_version)
                DO UPDATE SET status='queued', error_message=NULL, finished_at=NULL, updated_at=excluded.updated_at
                """,
                (item.id, workspace_id, asset_id, index_type, _json(source_revision), source_digest,
                 index_version, item.requested_at.isoformat(), item.updated_at.isoformat()),
            )
        return self.list_index_rebuild_jobs(workspace_id, asset_id, limit=1)[0]

    def list_index_rebuild_jobs(self, workspace_id: str, asset_id: str, *, limit: int = 20) -> list[MaterialIndexRebuildJob]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_index_rebuild_jobs WHERE workspace_id=? AND asset_id=? ORDER BY requested_at DESC, id DESC LIMIT ?",
                (workspace_id, asset_id, limit),
            ).fetchall()
        return [self._rebuild_job_from_row(row) for row in rows]

    def start_index_rebuild(self, workspace_id: str, job_id: str) -> MaterialIndexRebuildJob:
        return self._transition_rebuild_job(workspace_id, job_id, status="running")

    def finish_index_rebuild(self, workspace_id: str, job_id: str, *, success: bool,
                             error_message: str | None = None) -> MaterialIndexRebuildJob:
        if success and error_message:
            raise ValueError("成功的索引任务不能包含失败原因")
        return self._transition_rebuild_job(
            workspace_id, job_id, status="succeeded" if success else "failed", error_message=error_message,
        )

    def record_clip_reference_migrations(self, items: list[MaterialClipReferenceMigration]) -> None:
        if not items:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO material_clip_reference_migrations
                    (id, workspace_id, asset_id, old_segment_id, old_segment_version,
                     new_segment_id, new_segment_version, relation, status, explanation,
                     source_revision_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(item.id, item.workspace_id, item.asset_id, item.old_segment_id, item.old_segment_version,
                  item.new_segment_id, item.new_segment_version, item.relation, item.status, item.explanation,
                  _json(item.source_revision), item.created_at.isoformat()) for item in items],
            )

    def list_clip_reference_migrations(self, workspace_id: str, asset_id: str) -> list[MaterialClipReferenceMigration]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_clip_reference_migrations WHERE workspace_id=? AND asset_id=? ORDER BY created_at, id",
                (workspace_id, asset_id),
            ).fetchall()
        return [self._clip_migration_from_row(row) for row in rows]

    def record_knowledge_claim(self, item: KnowledgeClaim) -> KnowledgeClaim:
        if not item.workspace_id.strip() or not item.knowledge_item_id.strip() or not item.knowledge_version.strip():
            raise ValueError("知识断言必须包含工作区、资料和版本")
        if not item.field_ref.strip() or not item.asserted_by.strip():
            raise ValueError("知识断言必须包含字段和断言者")
        if item.confidence is not None and not 0.0 <= item.confidence <= 1.0:
            raise ValueError("知识断言置信度必须在 0 到 1 之间")
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_claims
                    (id, workspace_id, knowledge_item_id, knowledge_version, field_ref, value_json,
                     unit, qualifiers_json, evidence_refs_json, origin, confidence, status,
                     asserted_by, supersedes_ref, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, item.workspace_id, item.knowledge_item_id, item.knowledge_version,
                    item.field_ref.strip(), _json(item.value), item.unit, _json(item.qualifiers),
                    _json(item.evidence_refs), item.origin, item.confidence, item.status,
                    item.asserted_by, item.supersedes_ref, item.created_at.isoformat(), now.isoformat(),
                ),
            )
        return self.get_knowledge_claim(item.workspace_id, item.id)

    def get_knowledge_claim(self, workspace_id: str, claim_id: str) -> KnowledgeClaim:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_claims WHERE workspace_id=? AND id=?",
                (workspace_id, claim_id),
            ).fetchone()
        if row is None:
            raise KeyError(claim_id)
        return self._claim_from_row(row)

    def list_knowledge_claims(
        self, workspace_id: str, knowledge_item_id: str, *, knowledge_version: str | None = None
    ) -> list[KnowledgeClaim]:
        query = "SELECT * FROM knowledge_claims WHERE workspace_id=? AND knowledge_item_id=?"
        params: list[object] = [workspace_id, knowledge_item_id]
        if knowledge_version is not None:
            query += " AND knowledge_version=?"
            params.append(knowledge_version)
        query += " ORDER BY created_at, id"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._claim_from_row(row) for row in rows]

    def transition_knowledge_claim(
        self, workspace_id: str, claim_id: str, *, status: KnowledgeClaimStatus,
        expected_status: KnowledgeClaimStatus | None = None,
    ) -> KnowledgeClaim:
        if status not in {"candidate", "confirmed", "rejected", "unresolved"}:
            raise ValueError("知识断言状态无效")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM knowledge_claims WHERE workspace_id=? AND id=?",
                (workspace_id, claim_id),
            ).fetchone()
            if row is None:
                raise KeyError(claim_id)
            if expected_status is not None and row["status"] != expected_status:
                raise ValueError("知识断言状态已变化，请重新读取后确认")
            conn.execute(
                "UPDATE knowledge_claims SET status=?, updated_at=? WHERE workspace_id=? AND id=?",
                (status, utc_now().isoformat(), workspace_id, claim_id),
            )
        return self.get_knowledge_claim(workspace_id, claim_id)

    def record_segment_lineage(self, items: list[MaterialSegmentLineage]) -> None:
        if not items:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO material_segment_lineage
                    (workspace_id, asset_id, parent_segment_id, child_segment_id, relation,
                     source_revision_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.workspace_id, item.asset_id, item.parent_segment_id, item.child_segment_id,
                        item.relation, _json(item.source_revision), item.created_at.isoformat(),
                    )
                    for item in items
                ],
            )

    def list_segment_lineage(self, workspace_id: str, asset_id: str) -> list[MaterialSegmentLineage]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_segment_lineage WHERE workspace_id=? AND asset_id=? ORDER BY created_at, parent_segment_id, child_segment_id",
                (workspace_id, asset_id),
            ).fetchall()
        return [
            MaterialSegmentLineage(
                workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
                parent_segment_id=str(row["parent_segment_id"]), child_segment_id=str(row["child_segment_id"]),
                relation=str(row["relation"]), source_revision=json.loads(str(row["source_revision_json"])),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        ]

    def record_analysis_outputs(
        self,
        workspace_id: str,
        asset: MaterialAsset,
        *,
        analysis_version: str,
        source_revision: dict[str, str],
        source_digest: str | None,
        model_or_rule: str,
        visual_index_status: IndexStatus,
        visual_index_version: str,
        visual_index_coverage: dict[str, object],
    ) -> tuple[list[MaterialEvidenceRef], list[MaterialAssetAssertion]]:
        """Persist one analysis's evidence and candidate assertions atomically."""
        if asset.workspace_id != workspace_id:
            raise ValueError("素材证据工作区不一致")
        now = utc_now()
        evidence: list[MaterialEvidenceRef] = [
            MaterialEvidenceRef(
                id=f"evi_{uuid4().hex}",
                workspace_id=workspace_id,
                asset_id=asset.id,
                source_ref=asset.path,
                source_revision=dict(source_revision),
                locator={"path": asset.path},
                modality="media_probe",
                model_or_rule=model_or_rule,
                analysis_version=analysis_version,
                sampling_coverage={"segment_count": len(asset.segments)},
                created_at=now,
            )
        ]
        assertions: list[MaterialAssetAssertion] = []
        for segment in asset.segments:
            segment_evidence = MaterialEvidenceRef(
                id=f"evi_{uuid4().hex}",
                workspace_id=workspace_id,
                asset_id=asset.id,
                segment_id=segment.id,
                source_ref=asset.path,
                source_revision=dict(source_revision),
                source_time_range=(segment.start_seconds, segment.end_seconds),
                locator_type="source_time_range",
                locator={
                    "segment_id": segment.id,
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                },
                modality="segment_observation",
                model_or_rule=model_or_rule,
                analysis_version=analysis_version,
                sampling_coverage={"segment_duration_seconds": segment.end_seconds - segment.start_seconds},
                created_at=now,
            )
            evidence.append(segment_evidence)
            understanding = segment.understanding
            facts = understanding.facts if understanding is not None else []
            if not isinstance(facts, list):
                continue
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                kind = str(fact.get("kind") or "").strip()
                text = str(fact.get("text") or "").strip()
                if not kind or not text:
                    continue
                assertions.append(
                    MaterialAssetAssertion(
                        id=f"asr_{uuid4().hex}",
                        workspace_id=workspace_id,
                        asset_id=asset.id,
                        segment_id=segment.id,
                        field_ref=kind,
                        value=text,
                        evidence_refs=(segment_evidence.id,),
                        origin="model_inference",
                        confidence=(
                            float(fact["confidence"])
                            if isinstance(fact.get("confidence"), (int, float))
                            else None
                        ),
                        status="candidate",
                        asserted_by=model_or_rule,
                        created_at=now,
                    )
                )

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in evidence:
                self._insert_evidence(conn, item)
            for item in assertions:
                self._insert_assertion(conn, item)
            self._upsert_index(
                conn,
                MaterialIndexRecord(
                    id=f"idx_{uuid4().hex}",
                    workspace_id=workspace_id,
                    asset_id=asset.id,
                    index_type="visual_features",
                    source_revision=dict(source_revision),
                    source_digest=source_digest,
                    model_or_rule=model_or_rule,
                    index_version=visual_index_version,
                    coverage=dict(visual_index_coverage),
                    status=visual_index_status,
                    invalid_reason=None if visual_index_status == "ready" else "画面特征未完整生成",
                    built_at=now if visual_index_status == "ready" else None,
                    updated_at=now,
                ),
            )
            self._upsert_index(
                conn,
                MaterialIndexRecord(
                    id=f"idx_{uuid4().hex}",
                    workspace_id=workspace_id,
                    asset_id=asset.id,
                    index_type="material_search",
                    source_revision=dict(source_revision),
                    source_digest=source_digest,
                    model_or_rule="material_payload_v1",
                    index_version="material-search-v1",
                    coverage={"segment_count": len(asset.segments)},
                    status="ready",
                    built_at=now,
                    updated_at=now,
                ),
            )
            unavailable_reason = "当前素材分析器未提供可验证的质量测量结果"
            metrics = {
                name: {"status": "unavailable", "value": None, "reason": unavailable_reason}
                for name in QUALITY_METRIC_NAMES
            }
            conn.execute(
                """
                INSERT INTO material_quality_profiles
                    (id, workspace_id, asset_id, source_revision_json, source_digest,
                     analysis_run_id, profile_version, status, metrics_json,
                     unavailable_reasons_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'unavailable', ?, ?, ?, ?)
                ON CONFLICT(workspace_id, asset_id, source_revision_json, profile_version)
                DO UPDATE SET source_digest=excluded.source_digest,
                    analysis_run_id=excluded.analysis_run_id, status=excluded.status,
                    metrics_json=excluded.metrics_json,
                    unavailable_reasons_json=excluded.unavailable_reasons_json,
                    updated_at=excluded.updated_at
                """,
                (f"quality_{uuid4().hex}", workspace_id, asset.id, _json(source_revision), source_digest,
                 analysis_version, "quality-v1", _json(metrics), _json([unavailable_reason]),
                 now.isoformat(), now.isoformat()),
            )
        return evidence, assertions

    def mark_indexes_stale(
        self,
        workspace_id: str,
        asset_id: str,
        source_revision: dict[str, str],
        source_digest: str | None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE material_index_records
                SET status='stale', invalid_reason=?, updated_at=?
                WHERE workspace_id=? AND asset_id=? AND status='ready'
                  AND (source_revision_json != ? OR COALESCE(source_digest, '') != COALESCE(?, ''))
                """,
                (
                    "源文件修订已变化，需要重新构建索引",
                    utc_now().isoformat(),
                    workspace_id,
                    asset_id,
                    _json(source_revision),
                    source_digest,
                ),
            )
        return cursor.rowcount

    def list_evidence(self, workspace_id: str, asset_id: str, *, limit: int = 200) -> list[MaterialEvidenceRef]:
        if limit < 1 or limit > 500:
            raise ValueError("证据查询数量必须在 1 到 500 之间")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_evidence_refs WHERE workspace_id=? AND asset_id=? ORDER BY created_at, id LIMIT ?",
                (workspace_id, asset_id, limit),
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def list_evidence_for_segment(
        self, workspace_id: str, asset_id: str, segment_id: str, *, limit: int = 50,
    ) -> list[MaterialEvidenceRef]:
        """Return current evidence for one segment, falling back to asset evidence.

        Candidate ranking is allowed to explain a segment with the segment-level
        observation.  Old analyses may only have an asset-level observation, so
        that evidence remains usable as a clearly broader fallback.
        """
        if limit < 1 or limit > 200:
            raise ValueError("片段证据查询数量必须在 1 到 200 之间")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM material_evidence_refs
                WHERE workspace_id=? AND asset_id=? AND status='valid'
                  AND (segment_id=? OR segment_id IS NULL)
                ORDER BY CASE WHEN segment_id=? THEN 0 ELSE 1 END, created_at DESC, id DESC
                LIMIT ?
                """,
                (workspace_id, asset_id, segment_id, segment_id, limit),
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def list_assertions(self, workspace_id: str, asset_id: str, *, limit: int = 200) -> list[MaterialAssetAssertion]:
        if limit < 1 or limit > 500:
            raise ValueError("素材断言查询数量必须在 1 到 500 之间")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_asset_assertions WHERE workspace_id=? AND asset_id=? ORDER BY created_at, id LIMIT ?",
                (workspace_id, asset_id, limit),
            ).fetchall()
        return [self._assertion_from_row(row) for row in rows]

    def record_assertion(self, item: MaterialAssetAssertion) -> MaterialAssetAssertion:
        if not item.workspace_id.strip() or not item.asset_id.strip() or not item.field_ref.strip():
            raise ValueError("素材断言必须包含工作区、素材和字段")
        if not item.asserted_by.strip():
            raise ValueError("素材断言必须包含断言者")
        if item.confidence is not None and not 0.0 <= item.confidence <= 1.0:
            raise ValueError("素材断言置信度必须在 0 到 1 之间")
        with self._connect() as conn:
            self._insert_assertion(conn, item)
        saved_id = item.id
        return next(saved for saved in self.list_assertions(item.workspace_id, item.asset_id) if saved.id == saved_id)

    def transition_assertion(
        self, workspace_id: str, asset_id: str, assertion_id: str, *,
        status: AssertionStatus, expected_status: AssertionStatus | None = None,
    ) -> MaterialAssetAssertion:
        if status not in {"candidate", "confirmed", "rejected", "unresolved"}:
            raise ValueError("素材断言状态无效")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM material_asset_assertions WHERE workspace_id=? AND asset_id=? AND id=?",
                (workspace_id, asset_id, assertion_id),
            ).fetchone()
            if row is None:
                raise KeyError(assertion_id)
            if expected_status is not None and row["status"] != expected_status:
                raise ValueError("素材断言状态已变化，请重新读取后确认")
            conn.execute(
                "UPDATE material_asset_assertions SET status=? WHERE workspace_id=? AND asset_id=? AND id=?",
                (status, workspace_id, asset_id, assertion_id),
            )
        for assertion in self.list_assertions(workspace_id, asset_id):
            if assertion.id == assertion_id:
                return assertion
        raise KeyError(assertion_id)

    def list_indexes(self, workspace_id: str, asset_id: str) -> list[MaterialIndexRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM material_index_records WHERE workspace_id=? AND asset_id=? ORDER BY updated_at DESC, id DESC",
                (workspace_id, asset_id),
            ).fetchall()
        return [self._index_from_row(row) for row in rows]

    def latest_index_status(self, workspace_id: str, asset_id: str, index_type: str) -> IndexStatus | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status FROM material_index_records
                WHERE workspace_id=? AND asset_id=? AND index_type=?
                ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (workspace_id, asset_id, index_type),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def _transition_rebuild_job(self, workspace_id: str, job_id: str, *, status: IndexRebuildJobStatus,
                                error_message: str | None = None) -> MaterialIndexRebuildJob:
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM material_index_rebuild_jobs WHERE workspace_id=? AND id=?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = str(row["status"])
            allowed = {
                "queued": {"running", "cancelled"},
                "running": {"succeeded", "failed", "cancelled"},
                "failed": {"queued"},
                "succeeded": set(), "cancelled": set(),
            }
            if status not in allowed.get(current, set()):
                raise ValueError("索引重建任务状态迁移无效")
            started = row["started_at"] or (now.isoformat() if status == "running" else None)
            finished = now.isoformat() if status in {"succeeded", "failed", "cancelled"} else None
            attempts = int(row["attempt_count"]) + (1 if status == "running" else 0)
            conn.execute(
                "UPDATE material_index_rebuild_jobs SET status=?, attempt_count=?, error_message=?, started_at=?, finished_at=?, updated_at=? WHERE workspace_id=? AND id=?",
                (status, attempts, error_message, started, finished, now.isoformat(), workspace_id, job_id),
            )
            row = conn.execute(
                "SELECT * FROM material_index_rebuild_jobs WHERE workspace_id=? AND id=?",
                (workspace_id, job_id),
            ).fetchone()
        assert row is not None
        return self._rebuild_job_from_row(row)

    @staticmethod
    def _insert_evidence(conn: sqlite3.Connection, item: MaterialEvidenceRef) -> None:
        conn.execute(
            """
            INSERT INTO material_evidence_refs
                (id, workspace_id, asset_id, segment_id, source_ref, source_revision_json,
                 source_time_range_json, locator_type, locator_json, modality, model_or_rule,
                 analysis_version, sampling_coverage_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id, item.workspace_id, item.asset_id, item.segment_id, item.source_ref,
                _json(item.source_revision), _json(item.source_time_range), item.locator_type,
                _json(item.locator), item.modality, item.model_or_rule, item.analysis_version,
                _json(item.sampling_coverage), item.status, item.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_assertion(conn: sqlite3.Connection, item: MaterialAssetAssertion) -> None:
        if item.confidence is not None and not 0.0 <= item.confidence <= 1.0:
            raise ValueError("素材断言置信度必须在 0 到 1 之间")
        conn.execute(
            """
            INSERT INTO material_asset_assertions
                (id, workspace_id, asset_id, segment_id, field_ref, value_json, unit,
                 qualifiers_json, evidence_refs_json, origin, confidence, status,
                 asserted_by, supersedes_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id, item.workspace_id, item.asset_id, item.segment_id, item.field_ref,
                _json(item.value), item.unit, _json(item.qualifiers), _json(item.evidence_refs),
                item.origin, item.confidence, item.status, item.asserted_by,
                item.supersedes_ref, item.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _upsert_index(conn: sqlite3.Connection, item: MaterialIndexRecord) -> None:
        conn.execute(
            """
            INSERT INTO material_index_records
                (id, workspace_id, asset_id, index_type, source_revision_json, source_digest,
                 model_or_rule, index_version, coverage_json, status, invalid_reason, built_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, asset_id, index_type, index_version)
            DO UPDATE SET
                source_revision_json=excluded.source_revision_json,
                source_digest=excluded.source_digest,
                model_or_rule=excluded.model_or_rule,
                coverage_json=excluded.coverage_json,
                status=excluded.status,
                invalid_reason=excluded.invalid_reason,
                built_at=excluded.built_at,
                updated_at=excluded.updated_at
            """,
            (
                item.id, item.workspace_id, item.asset_id, item.index_type,
                _json(item.source_revision), item.source_digest, item.model_or_rule,
                item.index_version, _json(item.coverage), item.status, item.invalid_reason,
                item.built_at.isoformat() if item.built_at else None, item.updated_at.isoformat(),
            ),
        )

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> MaterialEvidenceRef:
        source_range = json.loads(str(row["source_time_range_json"]))
        return MaterialEvidenceRef(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
            segment_id=str(row["segment_id"]) if row["segment_id"] is not None else None,
            source_ref=str(row["source_ref"]), source_revision=json.loads(str(row["source_revision_json"])),
            source_time_range=tuple(source_range) if isinstance(source_range, list) else None,
            locator_type=str(row["locator_type"]), locator=json.loads(str(row["locator_json"])),
            modality=str(row["modality"]), model_or_rule=str(row["model_or_rule"]),
            analysis_version=str(row["analysis_version"]),
            sampling_coverage=json.loads(str(row["sampling_coverage_json"])), status=str(row["status"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _quality_from_row(row: sqlite3.Row) -> MaterialQualityProfile:
        return MaterialQualityProfile(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
            source_revision=json.loads(str(row["source_revision_json"])), source_digest=row["source_digest"],
            analysis_run_id=row["analysis_run_id"], profile_version=str(row["profile_version"]),
            status=str(row["status"]), metrics=json.loads(str(row["metrics_json"])),
            unavailable_reasons=tuple(json.loads(str(row["unavailable_reasons_json"]))),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _rebuild_job_from_row(row: sqlite3.Row) -> MaterialIndexRebuildJob:
        return MaterialIndexRebuildJob(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
            index_type=str(row["index_type"]), source_revision=json.loads(str(row["source_revision_json"])),
            source_digest=row["source_digest"], index_version=str(row["index_version"]),
            status=str(row["status"]), attempt_count=int(row["attempt_count"]),
            error_message=row["error_message"], requested_at=datetime.fromisoformat(str(row["requested_at"])),
            started_at=datetime.fromisoformat(str(row["started_at"])) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(str(row["finished_at"])) if row["finished_at"] else None,
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _clip_migration_from_row(row: sqlite3.Row) -> MaterialClipReferenceMigration:
        relation = str(row["relation"])
        return MaterialClipReferenceMigration(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
            old_segment_id=str(row["old_segment_id"]), old_segment_version=int(row["old_segment_version"]),
            new_segment_id=row["new_segment_id"],
            new_segment_version=int(row["new_segment_version"]) if row["new_segment_version"] is not None else None,
            relation=relation,
            status=(str(row["status"]) if "status" in row.keys() and row["status"] else _migration_status_for_relation(relation)),
            explanation=str(row["explanation"]),
            source_revision=json.loads(str(row["source_revision_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _assertion_from_row(row: sqlite3.Row) -> MaterialAssetAssertion:
        confidence = row["confidence"]
        return MaterialAssetAssertion(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
            segment_id=str(row["segment_id"]) if row["segment_id"] is not None else None,
            field_ref=str(row["field_ref"]), value=json.loads(str(row["value_json"])),
            unit=str(row["unit"]) if row["unit"] is not None else None,
            qualifiers=json.loads(str(row["qualifiers_json"])),
            evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))), origin=str(row["origin"]),
            confidence=float(confidence) if confidence is not None else None,
            status=str(row["status"]), asserted_by=str(row["asserted_by"]),
            supersedes_ref=str(row["supersedes_ref"]) if row["supersedes_ref"] is not None else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _index_from_row(row: sqlite3.Row) -> MaterialIndexRecord:
        return MaterialIndexRecord(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]), asset_id=str(row["asset_id"]),
            index_type=str(row["index_type"]), source_revision=json.loads(str(row["source_revision_json"])),
            source_digest=str(row["source_digest"]) if row["source_digest"] is not None else None,
            model_or_rule=str(row["model_or_rule"]), index_version=str(row["index_version"]),
            coverage=json.loads(str(row["coverage_json"])), status=str(row["status"]),
            invalid_reason=str(row["invalid_reason"]) if row["invalid_reason"] is not None else None,
            built_at=datetime.fromisoformat(str(row["built_at"])) if row["built_at"] else None,
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _field_from_row(row: sqlite3.Row) -> SemanticFieldDefinition:
        return SemanticFieldDefinition(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]),
            canonical_id=str(row["canonical_id"]), display_name=str(row["display_name"]),
            value_type=str(row["value_type"]), unit_family=str(row["unit_family"]) if row["unit_family"] else None,
            allowed_units=tuple(json.loads(str(row["allowed_units_json"]))),
            aliases=tuple(json.loads(str(row["aliases_json"]))),
            applies_to=tuple(json.loads(str(row["applies_to_json"]))), version=int(row["version"]),
            status=str(row["status"]), created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> KnowledgeClaim:
        confidence = row["confidence"]
        return KnowledgeClaim(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]),
            knowledge_item_id=str(row["knowledge_item_id"]), knowledge_version=str(row["knowledge_version"]),
            field_ref=str(row["field_ref"]), value=json.loads(str(row["value_json"])),
            unit=str(row["unit"]) if row["unit"] is not None else None,
            qualifiers=json.loads(str(row["qualifiers_json"])),
            evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))), origin=str(row["origin"]),
            confidence=float(confidence) if confidence is not None else None,
            status=str(row["status"]), asserted_by=str(row["asserted_by"]),
            supersedes_ref=str(row["supersedes_ref"]) if row["supersedes_ref"] else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
