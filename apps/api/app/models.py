from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


class EpisodeStatus(StrEnum):
    CREATED = "created"
    SOURCES_INGESTING = "sources_ingesting"
    TOPICS_READY = "topics_ready"
    TOPIC_APPROVED = "topic_approved"
    SCRIPT_READY = "script_ready"
    SCRIPT_APPROVED = "script_approved"
    WAITING_VIDEO = "waiting_video"
    VIDEO_PROCESSING = "video_processing"
    DRAFT_READY = "draft_ready"
    PUBLISHED = "published"
    FAILED = "failed"


class VideoJobStatus(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    DRAFTING = "drafting"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class EpisodeIntakeMode(StrEnum):
    PLANNED_SINGLE = "planned_single"
    PLANNED_BATCH = "planned_batch"
    AD_HOC_SINGLE = "ad_hoc_single"
    AD_HOC_BATCH = "ad_hoc_batch"


class SourceFileState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    ARCHIVED = "archived"
    SKIPPED = "skipped"


class ProductionChainStage(StrEnum):
    SOURCE_CLIPS = "source_clips"
    MERGE = "merge"
    SILENCE_CUT = "silence_cut"
    SPEED_PASS = "speed_pass"
    RETAKE_SCAN = "retake_scan"
    RETAKE_REMOVAL = "retake_removal"
    SUBTITLE = "subtitle"
    COVER = "cover"
    DRAFT_PACKAGE = "draft_package"
    JIANYING_IMPORT = "jianying_import"


class FeedbackValue(StrEnum):
    UP = "up"
    DOWN = "down"


class ScriptVersionSource(StrEnum):
    AI_GENERATED = "ai_generated"
    MANUAL_EDIT = "manual_edit"
    FULL_REGENERATION = "full_regeneration"
    SECTION_REGENERATION = "section_regeneration"
    REVERT = "revert"


@dataclass
class SourceRef:
    source_type: str
    title: str
    ref: str


@dataclass
class SourceSyncStatus:
    source_type: str
    configured: bool = False
    last_sync_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    error_message: str | None = None


@dataclass
class TopicCandidate:
    id: str
    title: str
    core_viewpoint: str
    reason: str
    audience: str
    outline: list[str]
    score: int
    source_refs: list[SourceRef]
    feedback: FeedbackValue | None = None


@dataclass
class ScriptSection:
    id: str
    title: str
    estimated_seconds: int
    content: str
    feedback: FeedbackValue | None = None


@dataclass
class ScriptPackage:
    id: str
    hook: str
    sections: list[ScriptSection]
    title_options: list[str]
    cover_copy: str
    publish_copy: str
    comment_prompt: str


@dataclass
class ScriptVersion:
    id: str
    source: ScriptVersionSource
    script: ScriptPackage
    changed_section_ids: list[str] = field(default_factory=list)
    reason: str | None = None
    active: bool = True
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionChainStep:
    stage: ProductionChainStage
    status: Literal["pending", "skipped", "completed", "failed"] = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None


@dataclass
class FeedbackEvent:
    target_type: str
    target_id: str
    value: FeedbackValue
    reason: str | None = None
    id: str = field(default_factory=lambda: new_id("fb"))
    active: bool = True
    before_snapshot: dict[str, object] | None = None
    after_snapshot: dict[str, object] | None = None
    reverted_at: datetime | None = None
    revert_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class Episode:
    id: str
    day_number: int
    episode_date: date
    status: EpisodeStatus
    intake_mode: EpisodeIntakeMode = EpisodeIntakeMode.PLANNED_SINGLE
    content_template_id: str = "video_diary"
    topics: list[TopicCandidate] = field(default_factory=list)
    selected_topic_id: str | None = None
    script: ScriptPackage | None = None
    script_versions: list[ScriptVersion] = field(default_factory=list)
    video_source_file_name: str | None = None
    video_source_file_names: list[str] = field(default_factory=list)
    video_processed_file_name: str | None = None
    video_processed_path: str | None = None
    video_edit_plan_path: str | None = None
    video_dedup_report_path: str | None = None
    video_job_status: VideoJobStatus = VideoJobStatus.IDLE
    video_job_message: str | None = None
    video_job_error: str | None = None
    video_job_started_at: datetime | None = None
    video_job_finished_at: datetime | None = None
    draft_package_name: str | None = None
    draft_package_path: str | None = None
    video_production_chain: list[ProductionChainStep] = field(default_factory=list)
    source_file_states: dict[str, SourceFileState] = field(default_factory=dict)
    publish_url: str | None = None
    platform: str | None = None
    play_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    notes: str | None = None
    published_at: datetime | None = None
    feedback_events: list[FeedbackEvent] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
