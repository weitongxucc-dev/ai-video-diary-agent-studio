from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


class GenerationJobStatus(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class EpisodeIntakeMode(StrEnum):
    PLANNED = "planned"
    AD_HOC_VIDEO = "ad_hoc_video"
    AD_HOC_VIDEO_BATCH = "ad_hoc_video_batch"


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
    intake_mode: EpisodeIntakeMode = EpisodeIntakeMode.PLANNED
    content_template_id: str = "video_diary"
    cover_template_id: str = "pixel_diary"
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
    topic_job_status: GenerationJobStatus = GenerationJobStatus.IDLE
    topic_job_message: str | None = None
    topic_job_error: str | None = None
    topic_job_started_at: datetime | None = None
    topic_job_finished_at: datetime | None = None
    draft_package_name: str | None = None
    draft_package_path: str | None = None
    publish_url: str | None = None
    play_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    published_at: datetime | None = None
    feedback_events: list[FeedbackEvent] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
