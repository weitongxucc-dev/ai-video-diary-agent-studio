from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import (
    EpisodeIntakeMode,
    EpisodeStatus,
    FeedbackValue,
    ScriptVersionSource,
    VideoJobStatus,
)


class SourceRefResponse(BaseModel):
    source_type: str
    title: str
    ref: str


class TopicCandidateResponse(BaseModel):
    id: str
    title: str
    core_viewpoint: str
    reason: str
    audience: str
    outline: list[str]
    score: int = Field(ge=0, le=100)
    source_refs: list[SourceRefResponse]
    feedback: FeedbackValue | None


class ScriptSectionResponse(BaseModel):
    id: str
    title: str
    estimated_seconds: int
    content: str
    feedback: FeedbackValue | None


class ScriptPackageResponse(BaseModel):
    id: str
    hook: str
    sections: list[ScriptSectionResponse]
    title_options: list[str]
    cover_copy: str
    publish_copy: str
    comment_prompt: str


class ScriptVersionResponse(BaseModel):
    id: str
    source: ScriptVersionSource
    script: ScriptPackageResponse
    changed_section_ids: list[str]
    reason: str | None
    active: bool
    created_at: datetime


class FeedbackEventResponse(BaseModel):
    episode_id: str | None = None
    id: str
    target_type: Literal[
        "topic",
        "script_section",
        "published_episode",
        "published_comment_insight",
    ]
    target_id: str
    value: FeedbackValue
    reason: str | None
    active: bool
    before_snapshot: dict[str, object] | None
    after_snapshot: dict[str, object] | None
    reverted_at: datetime | None
    revert_reason: str | None
    created_at: datetime


class EpisodeResponse(BaseModel):
    id: str
    day_number: int
    episode_date: date
    status: EpisodeStatus
    intake_mode: EpisodeIntakeMode
    content_template_id: str
    topics: list[TopicCandidateResponse]
    selected_topic_id: str | None
    script: ScriptPackageResponse | None
    script_versions: list[ScriptVersionResponse]
    video_source_file_name: str | None
    video_source_file_names: list[str]
    video_processed_file_name: str | None
    video_processed_path: str | None
    video_edit_plan_path: str | None
    video_dedup_report_path: str | None
    video_job_status: VideoJobStatus
    video_job_message: str | None
    video_job_error: str | None
    video_job_started_at: datetime | None
    video_job_finished_at: datetime | None
    draft_package_name: str | None
    draft_package_path: str | None
    publish_url: str | None
    play_count: int | None
    like_count: int | None
    comment_count: int | None
    published_at: datetime | None
    feedback_events: list[FeedbackEventResponse]
    created_at: datetime
    updated_at: datetime


class ApproveTopicRequest(BaseModel):
    topic_id: str


class ContentTemplateSelectRequest(BaseModel):
    template_id: str


class FeedbackRequest(BaseModel):
    target_type: Literal["topic", "script_section"]
    target_id: str
    value: FeedbackValue
    reason: str | None = None


class ScriptUpdateRequest(BaseModel):
    script: ScriptPackageResponse
    reason: str | None = None


class ScriptRegenerateRequest(BaseModel):
    reason: str | None = None


class ScriptSectionRegenerateRequest(BaseModel):
    reason: str | None = None
    rewrite_intent_id: str | None = None
    rewrite_section_title: str | None = None
    rewrite_user_note: str | None = None


class ScriptRewriteIntentResponse(BaseModel):
    id: str
    label: str
    description: str
    prompt_hint: str


class ScriptRewriteIntentsResponse(BaseModel):
    intents: list[ScriptRewriteIntentResponse]


class ScriptVersionRevertRequest(BaseModel):
    version_id: str
    reason: str | None = None


class FeedbackRevertRequest(BaseModel):
    feedback_event_id: str
    reason: str | None = None


class LLMStatusResponse(BaseModel):
    provider: str
    configured: bool
    model: str | None
    base_url: str


class ObsidianStatusResponse(BaseModel):
    configured: bool
    exists: bool
    path: str | None
    markdown_count: int


class ProductHuntStatusResponse(BaseModel):
    configured: bool
    api_url: str


class ExternalSourcesStatusResponse(BaseModel):
    configured: bool
    urls: list[str]


class HermesRadarStatusResponse(BaseModel):
    configured: bool
    exists: bool
    path: str | None
    latest_report: str | None
    total_count: int
    product_hunt_count: int
    watcha_count: int


class VideoStatusResponse(BaseModel):
    inbox_path: str
    output_path: str
    inbox_exists: bool
    output_exists: bool
    pending_count: int
    ffmpeg_available: bool


class StorageStatusResponse(BaseModel):
    database_path: str
    database_exists: bool


class ASRStatusResponse(BaseModel):
    provider: str
    configured: bool
    region: str
    engine_model_type: str
    max_local_audio_mb: float
    local_whisper_configured: bool
    local_whisper_python_path: str | None
    local_whisper_model_path: str | None


class SystemStatusResponse(BaseModel):
    llm: LLMStatusResponse
    obsidian: ObsidianStatusResponse
    product_hunt: ProductHuntStatusResponse
    external_sources: ExternalSourcesStatusResponse
    hermes_radar: HermesRadarStatusResponse
    video: VideoStatusResponse
    storage: StorageStatusResponse
    asr: ASRStatusResponse


class WorkflowStatusResponse(BaseModel):
    framework: str
    node_names: list[str]
    human_review_nodes: list[str]


class ContentTemplateResponse(BaseModel):
    template_id: str
    name: str
    scenario: str
    recommended_duration: str
    topic_structure: list[str]
    script_structure: list[str]
    material_requirements: list[str]
    jianying_style: str
    subtitle_style: str
    title_rules: list[str]
    cover_copy_rules: list[str]
    publish_copy_rules: list[str]
    default_feedback_dimensions: list[str]


class ContentTemplatesResponse(BaseModel):
    templates: list[ContentTemplateResponse]


class RuntimeHealthSummaryResponse(BaseModel):
    total: int
    ready: int
    missing: int
    degraded: int
    invalid: int


class RuntimeHealthItemResponse(BaseModel):
    id: str
    label: str
    status: Literal["ready", "missing", "degraded", "invalid"]
    reason: str
    impact: str
    repair_action: str
    last_checked_at: datetime


class RuntimeHealthResponse(BaseModel):
    summary: RuntimeHealthSummaryResponse
    items: list[RuntimeHealthItemResponse]


class DiagnosticsSummaryResponse(BaseModel):
    health_total: int
    health_ready: int
    health_missing: int
    health_degraded: int
    health_invalid: int
    recent_error_count: int
    report_file_count: int


class DiagnosticsLogCategoryResponse(BaseModel):
    id: str
    label: str
    status: Literal["available", "empty", "degraded"]
    entry_count: int
    latest_message: str


class DiagnosticsRecentErrorResponse(BaseModel):
    source: str
    episode_id: str | None
    message: str
    created_at: datetime | None


class DiagnosticsStatusResponse(BaseModel):
    generated_at: datetime
    summary: DiagnosticsSummaryResponse
    log_categories: list[DiagnosticsLogCategoryResponse]
    recent_errors: list[DiagnosticsRecentErrorResponse]
    settings_snapshot: dict[str, dict[str, object]]
    report_files: list[str]
    excluded_items: list[str]


class PendingVideoResponse(BaseModel):
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime


class ProcessVideoRequest(BaseModel):
    file_name: str | None = None


class ProcessVideoBatchRequest(BaseModel):
    file_names: list[str] = Field(min_length=2)


class ProcessedVideoResponse(BaseModel):
    file_name: str
    output_path: str
    source_file_name: str
    edit_plan_path: str | None = None
    source_file_names: list[str] = Field(default_factory=list)
    dedup_report_path: str | None = None


class RetakeRangeResponse(BaseModel):
    start: float
    end: float


class RetakeCandidateResponse(BaseModel):
    remove: RetakeRangeResponse
    keep: RetakeRangeResponse
    confidence: float
    reason: str


class RetakeReportResponse(BaseModel):
    status: Literal["applied", "no_changes", "skipped", "failed"]
    source_video: str
    output_video: str | None
    auto_removed: list[RetakeCandidateResponse]
    review_only: list[RetakeCandidateResponse]
    message: str | None = None


class EditPlanVideoResponse(BaseModel):
    file_name: str
    path: str
    url: str


class EditPlanSegmentDecisionRequest(BaseModel):
    action: Literal["keep", "remove", "review"]
    reason: str | None = None


class EditPlanUserDecisionResponse(BaseModel):
    action: Literal["keep", "remove", "review"]
    reason: str | None
    created_at: datetime


class EditPlanSegmentResponse(BaseModel):
    id: str
    label: str
    start: float
    end: float
    duration: float
    action: Literal["keep", "cut_silence", "remove_retake", "manual_review"]
    status: Literal["kept", "auto_removed", "manual_review"]
    reason: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str
    user_decision: EditPlanUserDecisionResponse | None = None


class EditPlanDecisionResponse(BaseModel):
    segment_id: str
    title: str
    command: Literal["keep_segment", "cut_silence", "remove_retake", "manual_review"]
    range: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str


class EditPlanMarkerResponse(BaseModel):
    segment_id: str
    label: str
    time: str
    memo: str


class EditPlanJianyingResponse(BaseModel):
    status: Literal["not_ready", "waiting_confirmation", "draft_ready"]
    package_name: str | None
    package_path: str | None
    auto_imported: bool
    message: str


class EditPlanResponse(BaseModel):
    episode_id: str
    available: bool
    status: Literal["missing", "processing", "failed", "ready"]
    message: str
    strategy: str | None
    duration_seconds: float
    source_files: list[str]
    source_video: EditPlanVideoResponse | None
    output_video: EditPlanVideoResponse | None
    segments: list[EditPlanSegmentResponse]
    decisions: list[EditPlanDecisionResponse]
    markers: list[EditPlanMarkerResponse]
    jianying: EditPlanJianyingResponse


class KnowledgeNoteResponse(BaseModel):
    title: str
    ref: str
    excerpt: str
    modified_at: datetime
    tags: list[str] = []
    wikilinks: list[str] = []
    outlinks: list[str] = []


class SourceItemResponse(BaseModel):
    source_type: str
    title: str
    ref: str
    excerpt: str


class SourceItemsResponse(BaseModel):
    items: list[SourceItemResponse]
    total_count: int
    source_counts: dict[str, int]


class ManualLinkRequest(BaseModel):
    url: str


class FeedbackSummaryResponse(BaseModel):
    total_count: int
    up_count: int
    down_count: int
    recent_events: list[FeedbackEventResponse]


class PreferenceItemResponse(BaseModel):
    label: str
    signal: str
    evidence_count: int
    confidence: float = Field(ge=0, le=1)


class PreferenceCandidateResponse(BaseModel):
    kind: Literal["prefer", "avoid"]
    label: str
    content: str
    source_event_ids: list[str]
    evidence_count: int
    confidence: float = Field(ge=0, le=1)
    scope: str
    confirmed: bool


class LongTermPreferenceResponse(BaseModel):
    id: str
    kind: Literal["prefer", "avoid"]
    label: str
    content: str
    source_event_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    scope: str
    enabled: bool
    deleted: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


class PreferencesResponse(BaseModel):
    total_feedback_count: int
    preferences: list[PreferenceItemResponse]
    candidates: list[PreferenceCandidateResponse] = Field(default_factory=list)
    long_term_preferences: list[LongTermPreferenceResponse] = Field(default_factory=list)


class PreferenceConfirmRequest(BaseModel):
    kind: Literal["prefer", "avoid"]
    content: str = Field(min_length=1)
    source_event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    scope: str = "general"


class CommentInsightCandidateResponse(BaseModel):
    kind: Literal["prefer", "avoid"]
    label: str
    content: str
    source_event_ids: list[str]
    evidence_count: int
    confidence: float = Field(ge=0, le=1)
    scope: str = "publish"
    confirmed: bool = False


class CommentInsightsRequest(BaseModel):
    comments_text: str = ""


class CommentInsightsResponse(BaseModel):
    episode: EpisodeResponse
    created_candidates: list[CommentInsightCandidateResponse]


class CreatorProfileResponse(BaseModel):
    identity: str
    content_domains: list[str]
    tone_keywords: list[str]
    preferred_phrases: list[str]
    banned_phrases: list[str]
    good_examples: list[str]
    bad_examples: list[str]
    prompt_payload: dict[str, object]
    updated_at: datetime


class CreatorProfileUpdateRequest(BaseModel):
    identity: str | None = None
    content_domains: list[str] | None = None
    tone_keywords: list[str] | None = None
    preferred_phrases: list[str] | None = None
    banned_phrases: list[str] | None = None
    good_examples: list[str] | None = None
    bad_examples: list[str] | None = None


class RuntimeSettingItemResponse(BaseModel):
    name: str
    configured: bool
    value: str | None
    secret: bool


class RuntimeSettingsResponse(BaseModel):
    settings: list[RuntimeSettingItemResponse]
    status: SystemStatusResponse


class RuntimeSettingsUpdateRequest(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None
    product_hunt_token: str | None = None
    tencentcloud_secret_id: str | None = None
    tencentcloud_secret_key: str | None = None
    tencentcloud_asr_region: str | None = None
    tencentcloud_asr_engine_model_type: str | None = None
    local_whisper_python_path: str | None = None
    local_whisper_model_path: str | None = None
    local_whisper_language: str | None = None
    obsidian_vault_path: str | None = None
    hermes_radar_path: str | None = None
    video_inbox_path: str | None = None
    video_output_path: str | None = None
    draft_output_path: str | None = None
    jianying_draft_root_path: str | None = None
    database_path: str | None = None
    topic_source_urls: list[str] | None = None


class DraftToolStatusResponse(BaseModel):
    available: bool
    tool_name: str | None
    checked_modules: list[str]


class DraftAssetResponse(BaseModel):
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime
    import_status: str | None = None
    import_message: str | None = None
    draft_path: str | None = None
    template_id: str | None = None
    template_name: str | None = None
    track_count: int | None = None
    effect_count: int | None = None
    track_preview: list[str] = Field(default_factory=list)
    effect_preview: list[str] = Field(default_factory=list)


class DraftEnvironmentResponse(BaseModel):
    output_path: str
    output_exists: bool
    processed_video_count: int
    package_count: int
    draft_tool: DraftToolStatusResponse
    jianying_root_path: str
    jianying_root_exists: bool
    jianying_10_7_detected: bool
    editable_draft_supported: bool
    jianying_blocker: str | None


class DraftStatusResponse(BaseModel):
    environment: DraftEnvironmentResponse
    processed_videos: list[DraftAssetResponse]
    packages: list[DraftAssetResponse]


class DraftPackageRequest(BaseModel):
    video_file_name: str | None = None


class DraftPackageResponse(BaseModel):
    package_name: str
    path: str
    manifest_path: str
    video_path: str
    subtitle_path: str | None
    cover_path: str | None
    status: str


class DraftImportRequest(BaseModel):
    package_name: str | None = None


class DraftImportResponse(BaseModel):
    package_name: str
    status: str
    message: str
    draft_path: str | None
    manifest_path: str


class UploadVideoRequest(BaseModel):
    file_name: str


class MarkPublishedRequest(BaseModel):
    publish_url: str | None = None
    play_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    feedback_value: FeedbackValue | None = None
    feedback_reason: str | None = None


class VoiceCommandRequest(BaseModel):
    transcript: str = Field(min_length=1)


class VoiceTranscriptionSegmentResponse(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class VoiceTranscriptionResponse(BaseModel):
    provider: str
    transcript: str
    segments: list[VoiceTranscriptionSegmentResponse]


class VoiceCommandOperationResponse(BaseModel):
    kind: str
    label: str
    params: dict[str, object] = Field(default_factory=dict)


class VoiceCommandResponse(BaseModel):
    status: Literal["ready", "blocked", "unknown", "executed"]
    intent: str
    summary: str
    can_execute: bool
    next_url: str | None = None
    operations: list[VoiceCommandOperationResponse]
    result_episode: EpisodeResponse | None = None
    result_edit_plan: EditPlanResponse | None = None
