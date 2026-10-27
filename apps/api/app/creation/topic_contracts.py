"""Persisted topic attempts, candidates and evidence values."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.models import utc_now


TopicGenerationRunStatus = Literal["running", "succeeded", "failed"]


TopicSelectionStatus = Literal["unselected", "confirmed"]


TopicEvidenceKind = Literal["knowledge", "source"]


@dataclass
class TopicGenerationRun:
    id: str
    workspace_id: str
    task_id: str
    plan_id: str
    status: TopicGenerationRunStatus
    route_snapshot: dict[str, object]
    prompt_schema_version: str
    input_references: list[dict[str, object]]
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None


@dataclass
class TopicCandidate:
    id: str
    workspace_id: str
    task_id: str
    run_id: str
    title: str
    angle: str
    audience: str
    outline: list[str]
    score: int
    selection_status: TopicSelectionStatus = "unselected"
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class TopicEvidenceReference:
    id: str
    workspace_id: str
    task_id: str
    candidate_id: str
    kind: TopicEvidenceKind
    reference_id: str
    version_or_identity: str
    title: str
    authorization_or_license: str
    source_url: str | None
    created_at: datetime = field(default_factory=utc_now)
