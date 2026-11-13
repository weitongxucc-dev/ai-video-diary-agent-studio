"""Reference-video study and bounded structural comparison.

The study reuses an existing governed MediaAsset/Clip set.  Comparison keeps
the evidence honest: shot count, duration and cadence are measurable here;
visual/audio similarity remains explicitly unavailable until a bounded worker
has produced those observations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import sqlite3
from typing import Callable, Literal
from uuid import uuid4

from app.materials.contracts import MaterialAsset, material_usage_eligibility
from app.models import utc_now


@dataclass(frozen=True)
class ReferenceVideoShot:
    id: str
    asset_id: str
    segment_id: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    evidence_ref_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReferenceVideoStudy:
    id: str
    workspace_id: str
    asset_id: str
    source_revision: dict[str, str]
    source_digest: str | None
    analysis_run_id: str | None
    status: Literal["ready", "stale"]
    shots: tuple[ReferenceVideoShot, ...]
    metrics: dict[str, object]
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ReferenceVideoCompareReport:
    id: str
    workspace_id: str
    reference_study_id: str
    task_id: str
    timeline_version_id: str
    reference_evidence_ref_ids: tuple[str, ...]
    metrics: dict[str, object]
    visual_observation: dict[str, object]
    audio_observation: dict[str, object]
    acceptance: Literal["requires_bounded_visual_review", "structural_match"]
    created_at: datetime = field(default_factory=utc_now)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReferenceVideoStudyStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def save_study(self, study: ReferenceVideoStudy) -> ReferenceVideoStudy:
        payload = {**asdict(study), "created_at": study.created_at.isoformat(), "updated_at": study.updated_at.isoformat()}
        payload["shots"] = [asdict(item) for item in study.shots]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reference_video_studies
                    (id, workspace_id, asset_id, source_revision_json, source_digest,
                     analysis_run_id, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                    payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (study.id, study.workspace_id, study.asset_id, _json(study.source_revision),
                 study.source_digest, study.analysis_run_id, study.status, _json(payload),
                 study.created_at.isoformat(), study.updated_at.isoformat()),
            )
        return study

    def get_study(self, workspace_id: str, study_id: str) -> ReferenceVideoStudy:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM reference_video_studies WHERE workspace_id=? AND id=?",
                (workspace_id, study_id),
            ).fetchone()
        if row is None:
            raise KeyError(study_id)
        return self._from_payload(json.loads(str(row["payload"])))

    def save_compare(self, report: ReferenceVideoCompareReport) -> ReferenceVideoCompareReport:
        payload = {**asdict(report), "created_at": report.created_at.isoformat()}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reference_video_compare_reports
                    (id, workspace_id, reference_study_id, task_id,
                     timeline_version_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (report.id, report.workspace_id, report.reference_study_id, report.task_id,
                 report.timeline_version_id, _json(payload), report.created_at.isoformat()),
            )
        return report

    @staticmethod
    def _from_payload(payload: dict[str, object]) -> ReferenceVideoStudy:
        shots = tuple(
            ReferenceVideoShot(
                id=str(item["id"]), asset_id=str(item["asset_id"]), segment_id=str(item["segment_id"]),
                start_seconds=float(item["start_seconds"]), end_seconds=float(item["end_seconds"]),
                duration_seconds=float(item["duration_seconds"]),
                evidence_ref_ids=tuple(str(value) for value in item.get("evidence_ref_ids", [])),
            )
            for item in payload.get("shots", []) if isinstance(item, dict)
        )
        return ReferenceVideoStudy(
            id=str(payload["id"]), workspace_id=str(payload["workspace_id"]), asset_id=str(payload["asset_id"]),
            source_revision=dict(payload.get("source_revision", {})), source_digest=payload.get("source_digest"),
            analysis_run_id=payload.get("analysis_run_id"), status=str(payload["status"]), shots=shots,
            metrics=dict(payload.get("metrics", {})),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )


def build_reference_video_study(
    *,
    study_id: str,
    workspace_id: str,
    asset: MaterialAsset,
    source_revision: dict[str, str],
    source_digest: str | None,
    analysis_run_id: str | None,
    evidence_ids_for: Callable[[str, str], list[str]],
) -> ReferenceVideoStudy:
    eligibility = material_usage_eligibility(asset, purpose="reference")
    if not eligibility.eligible:
        raise ValueError(f"参考视频不可用：{eligibility.reason}")
    shots = tuple(
        ReferenceVideoShot(
            id=f"shot_{uuid4().hex[:12]}", asset_id=asset.id, segment_id=segment.id,
            start_seconds=segment.start_seconds, end_seconds=segment.end_seconds,
            duration_seconds=round(segment.end_seconds - segment.start_seconds, 3),
            evidence_ref_ids=tuple(evidence_ids_for(asset.id, segment.id)),
        )
        for segment in asset.segments
    )
    duration = round(sum(item.duration_seconds for item in shots), 3)
    metrics = {
        "duration_seconds": duration,
        "shot_count": len(shots),
        "average_shot_duration_seconds": round(duration / len(shots), 3) if shots else 0.0,
        "analysis_scope": "governed_asset_segments",
        "visual_similarity": {"status": "unavailable", "reason": "study 阶段只固化镜头与证据，未运行视觉 compare"},
        "audio_similarity": {"status": "unavailable", "reason": "study 阶段未运行音频观察"},
    }
    now = utc_now()
    return ReferenceVideoStudy(
        id=study_id, workspace_id=workspace_id, asset_id=asset.id,
        source_revision=dict(source_revision), source_digest=source_digest,
        analysis_run_id=analysis_run_id, status="ready", shots=shots, metrics=metrics,
        created_at=now, updated_at=now,
    )


def compare_study_to_timeline(
    *,
    report_id: str,
    study: ReferenceVideoStudy,
    workspace_id: str,
    task_id: str,
    timeline_version_id: str,
    montage: dict[str, object],
) -> ReferenceVideoCompareReport:
    target_clips = [item for item in montage.get("clips", []) if isinstance(item, dict)]
    reference_duration = float(study.metrics.get("duration_seconds", 0.0))
    target_duration = float(montage.get("duration_seconds", 0.0))
    reference_count = len(study.shots)
    target_count = len(target_clips)
    reference_average = float(study.metrics.get("average_shot_duration_seconds", 0.0))
    target_average = target_duration / target_count if target_count else 0.0
    metrics = {
        "reference_duration_seconds": reference_duration,
        "target_duration_seconds": round(target_duration, 3),
        "duration_delta_seconds": round(target_duration - reference_duration, 3),
        "reference_shot_count": reference_count,
        "target_shot_count": target_count,
        "shot_count_delta": target_count - reference_count,
        "reference_average_shot_duration_seconds": reference_average,
        "target_average_shot_duration_seconds": round(target_average, 3),
        "average_shot_duration_delta_seconds": round(target_average - reference_average, 3),
        "comparison_scope": "bounded_structural_timeline",
    }
    return ReferenceVideoCompareReport(
        id=report_id, workspace_id=workspace_id, reference_study_id=study.id,
        task_id=task_id, timeline_version_id=timeline_version_id,
        reference_evidence_ref_ids=tuple(ref for shot in study.shots for ref in shot.evidence_ref_ids),
        metrics=metrics,
        visual_observation={"status": "unavailable", "reason": "需要实际输出的有界帧观察；结构指标不替代画面验收"},
        audio_observation={"status": "unavailable", "reason": "需要实际输出与参考音轨的有界音频观察"},
        acceptance="requires_bounded_visual_review",
    )
