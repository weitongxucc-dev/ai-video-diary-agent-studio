"""Persisted creation/edit value contracts; importing them starts no services."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from pathlib import Path
from app.database_migrations import CONTENT_VISIBILITY_DEFAULT
from app.materials.contracts import ContentVisibility
from app.models import utc_now

JobRunStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
]


@dataclass(frozen=True)
class TimelineVersion:
    id: str
    workspace_id: str
    task_id: str
    parent_version_id: str | None
    sequence: int
    snapshot: dict[str, object]
    created_by_operation_id: str | None
    created_at: datetime = field(default_factory=utc_now)


SubtitleTrackStatus = Literal["valid", "failed", "unavailable", "superseded"]


@dataclass(frozen=True)
class SubtitleCue:
    id: str
    source_cue_id: str | None
    text: str
    start_seconds: float
    end_seconds: float
    speaker: str | None = None
    line_breaks: list[int] = field(default_factory=list)
    emphasis: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubtitleTrack:
    id: str
    workspace_id: str
    task_id: str
    timeline_version_id: str
    language: Literal["zh-CN", "en-US"]
    source_language: Literal["zh-CN", "en-US"] | None
    style: dict[str, object]
    cues: list[SubtitleCue]
    generation_method: Literal["timeline", "translation"]
    model_id: str | None
    artifact_ref: str | None
    transform_id: str | None
    status: SubtitleTrackStatus
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class CoverAsset:
    id: str
    workspace_id: str
    task_id: str
    timeline_version_id: str
    source_video_ref: str
    frame_time_seconds: float
    title: str
    safe_area: dict[str, float]
    layout: dict[str, object]
    generation_method: Literal["video_frame", "image_model"]
    model_or_engine: str
    artifact_ref: str | None
    transform_id: str | None
    status: SubtitleTrackStatus
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class JobRun:
    id: str
    workspace_id: str
    task_id: str
    kind: str
    idempotency_key: str
    input_digest: str
    status: JobRunStatus = "queued"
    progress: int = 0
    attempt: int = 0
    output_refs: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class TaskAuthorization:
    id: str
    workspace_id: str
    task_id: str
    identity: dict[str, str | None]
    goal_revision: str
    allowed_operations: tuple[str, ...]
    resource_scope: dict[str, list[str]]
    issued_at: datetime | None
    verified_at: datetime | None
    expires_at: datetime | None
    revision: int = 1
    revoked_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_verdict: str = "unknown"


class TaskAuthorizationConflict(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    workspace_id: str
    task_id: str
    kind: str
    logical_ref: str
    sha256: str
    size_bytes: int
    producer_job_id: str
    input_refs: list[str]
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class TaskEvent:
    id: str
    workspace_id: str
    task_id: str
    job_id: str | None
    event_type: str
    payload: dict[str, object]
    created_at: datetime = field(default_factory=utc_now)


TransformRunStatus = Literal["running", "succeeded", "failed"]


@dataclass(frozen=True)
class TransformRun:
    id: str
    workspace_id: str
    task_id: str
    job_id: str
    transform_type: str
    input_digest: str
    input_refs: list[str]
    output_refs: list[str]
    engine: str
    parameters: dict[str, object]
    status: TransformRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    failure_reason: str | None = None
    remediation: str | None = None


@dataclass(frozen=True)
class DependencyEdge:
    id: str
    workspace_id: str
    task_id: str
    upstream_ref: str
    downstream_ref: str
    created_by_transform_id: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EditIntent:
    id: str
    workspace_id: str
    task_id: str
    raw_command: str
    context_stage: str
    selected_object_refs: list[str]
    playhead_ms: int | None
    intent_type: str
    scope: dict[str, object]
    constraints: dict[str, object]
    confidence: float
    status: str
    created_at: datetime = field(default_factory=utc_now)


EditProposalStatus = Literal["proposed", "confirmed", "rejected", "expired"]


@dataclass(frozen=True)
class EditProposal:
    id: str
    workspace_id: str
    task_id: str
    intent_id: str
    summary: str
    target_refs: list[str]
    operation: dict[str, object]
    invalidated_refs: list[str]
    preview: dict[str, object]
    external_effects: list[object]
    status: EditProposalStatus = "proposed"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


EditOperationStatus = Literal["running", "applied", "failed"]
EditOperationType = Literal[
    "ReviseScriptSection",
    "RegenerateStoryboard",
    "ChangePresenterShare",
    "EditAudioTimeline",
    "EditCompoundTimeline",
        "RemoveSilence",
        "RemoveTimelineRanges",
    "AdjustVisualEffects",
    "ChangeCrop",
    "ChangeCanvas",
        "SetClipTransition",
        "EditGraphicOverlay",
    "ReplaceClip",
    "TrimClip",
        "SplitClip",
        "ChangeClipSpeed",
        "ChangeLinkedSpeed",
        "DeleteClip",
        "DuplicateClip",
    "InsertClip",
    "ReorderClip",
    "ReorderTimelineItems",
    "SetClipLock",
    "ConfirmClip",
    "UndoTimelineVersion",
    "RedoTimelineVersion",
    "RegenerateNarrationSegment",
    "UpdateSubtitleCue",
    "AlignSubtitleTiming",
    "ReplaceSubtitleText",
    "SplitSubtitleCue",
    "MergeSubtitleCue",
    "DeleteSubtitleCue",
    "ConfigureSubtitleTrack",
    "GenerateCoverFromFrame",
    "ChangePersonSegment",
    "RegeneratePersonRender",
]
SUPPORTED_EDIT_OPERATION_TYPES = {
    "ReviseScriptSection",
    "RegenerateStoryboard",
    "ChangePresenterShare",
    "EditAudioTimeline",
    "EditCompoundTimeline",
        "RemoveSilence",
        "RemoveTimelineRanges",
    "AdjustVisualEffects",
    "ChangeCrop",
    "ChangeCanvas",
        "SetClipTransition",
        "EditGraphicOverlay",
    "ReplaceClip",
    "TrimClip",
        "SplitClip",
        "ChangeClipSpeed",
        "ChangeLinkedSpeed",
        "DeleteClip",
        "DuplicateClip",
    "InsertClip",
    "ReorderClip",
    "ReorderTimelineItems",
    "SetClipLock",
    "ConfirmClip",
    "UndoTimelineVersion",
    "RedoTimelineVersion",
    "RegenerateNarrationSegment",
    "UpdateSubtitleCue",
    "AlignSubtitleTiming",
    "ReplaceSubtitleText",
    "SplitSubtitleCue",
    "MergeSubtitleCue",
    "DeleteSubtitleCue",
    "ConfigureSubtitleTrack",
    "GenerateCoverFromFrame",
    "ChangePersonSegment",
    "RegeneratePersonRender",
}


@dataclass(frozen=True)
class EditOperation:
    id: str
    workspace_id: str
    task_id: str
    proposal_id: str
    operation_type: EditOperationType
    payload: dict[str, object]
    base_version_id: str
    result_version_id: str | None
    status: EditOperationStatus
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    applied_at: datetime | None = None


@dataclass(frozen=True)
class EditCommand:
    workspace_id: str
    task_id: str
    actor_identity: dict
    base_version_id: str
    operation_type: str
    values: dict
    source: dict | None = None
    input_source_facts: list | None = None
    affected_clip_ids: tuple[str, ...] | None = None
    affected_source_facts: list | None = None
    affected_clip_facts: list | None = None


@dataclass(frozen=True)
class ClipSourceSelection:
    start_seconds: float
    end_seconds: float
    is_speech: bool
    asset_id: str | None = None
    segment_id: str | None = None
    reference: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class CreationPlanVoiceReference:
    profile_id: str
    version: str
    person_profile_id: str
    person_version: str
    person_authorization_id: str
    person_authorization_version: str
    authorization_id: str
    authorization_version: str
    language: str
    model_id: str


@dataclass(frozen=True)
class CreationPlanPersonReference:
    profile_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.version.strip():
            raise ValueError("人物选择必须固定档案ID与版本")


NarrationSegmentStatus = Literal["pending", "generating", "ready", "failed"]


@dataclass(frozen=True)
class NarrationSegment:
    id: str
    workspace_id: str
    task_id: str
    script_id: str
    source_section_id: str
    text: str
    audio_reference: str | None
    duration_seconds: float | None
    sample_rate: int | None
    model_id: str | None
    voice_profile_id: str | None
    voice_version: str | None
    language: str
    pronunciation_version: str
    status: NarrationSegmentStatus
    source_kind: Literal["generated", "recorded"] = "generated"
    source_artifact_ref: str | None = None
    source_sha256: str | None = None
    source_script_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class CreationTask:
    id: str
    workspace_id: str
    plan_id: str
    name: str
    scenario: str
    stage: Literal["topic", "script", "narration", "montage", "review"] = "topic"
    stage_status: Literal[
        "not_started", "running", "waiting_for_user", "completed", "failed"
    ] = "not_started"
    status: Literal["created", "running", "waiting_for_user", "completed", "failed"] = "created"
    progress: int = 0
    recovery_point: str = "topic"
    current_timeline_version_id: str | None = None
    selected_topic_id: str | None = None
    confirmed_script_id: str | None = None
    narration_cache_key: str | None = None
    montage_plan_id: str | None = None
    output_ready: bool = False
    stage_reason: str | None = None
    stage_impact: str | None = None
    stage_repair_action: str | None = None
    stage_retryable: bool | None = None
    visibility: ContentVisibility = CONTENT_VISIBILITY_DEFAULT
    owner_user_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def needs_script_duration_repair(self) -> bool:
        return (
            self.stage == "narration" and self.stage_status == "failed"
            and self.stage_reason == "narration_generation_failed"
            and "压缩" in (self.stage_repair_action or "")
            and "文案" in (self.stage_repair_action or "")
        )

    @property
    def can_revise_script(self) -> bool:
        return bool(
            self.confirmed_script_id and not self.narration_cache_key
            and not self.montage_plan_id and not self.current_timeline_version_id
            and ((self.stage == "script" and self.stage_status == "failed")
                 or (self.stage == "narration" and self.stage_status == "not_started")
                 or self.needs_script_duration_repair)
        )


def validate_logical_reference(reference: str, label: str) -> None:
    if not reference.strip():
        raise ValueError(f"{label}不能为空")
    if Path(reference).is_absolute() or reference.startswith("file://"):
        raise ValueError(f"{label}不能是绝对路径")
