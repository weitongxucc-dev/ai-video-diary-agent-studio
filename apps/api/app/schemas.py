from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, computed_field, model_validator

from app.graphic_overlays import TextOverlay, ImageOverlay, ArrowOverlay
from app.database_migrations import CONTENT_VISIBILITY_DEFAULT, CONTENT_VISIBILITY_LEGACY
from app.visual_effects import VisualEffects
from app.audio_timeline import AudioTimeline, TimelineAudioTrack, TimelineAudioClip, AudioGainPoint, visible_gain_points
from app.visual_understanding import OCR_RETRIEVAL_MIN_CONFIDENCE
from app.source_types import ContentUseState
from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT

from app.models import (
    EpisodeIntakeMode,
    EpisodeStatus,
    FeedbackValue,
    ProductionChainStage,
    ScriptVersionSource,
    SourceFileState,
    VideoJobStatus,
)


def validate_logical_media_reference(
    value: str,
    *,
    field: str,
    allowed_schemes: set[str],
) -> str:
    reference = value.strip()
    if not reference:
        raise ValueError(f"{field} must not be blank")
    if "\\" in reference:
        raise ValueError(f"{field} must use a logical reference without backslashes")
    parsed = urlsplit(reference)
    scheme = parsed.scheme.casefold()
    if scheme not in allowed_schemes or not parsed.netloc:
        allowed = ", ".join(f"{item}://" for item in sorted(allowed_schemes))
        raise ValueError(f"{field} must use {allowed} logical reference")
    if parsed.query or parsed.fragment or "@" in parsed.netloc or ":" in parsed.netloc:
        raise ValueError(f"{field} must not contain authority, query, or fragment")
    components = [parsed.netloc, *(parsed.path.lstrip("/").split("/") if parsed.path else [])]
    if any(not component for component in components):
        raise ValueError(f"{field} must contain a relative path")
    for component in components:
        decoded = unquote(component)
        if decoded in {".", ".."} or "/" in decoded or "\\" in decoded or ":" in decoded:
            raise ValueError(f"{field} contains an invalid path component")
    return f"{scheme}://{parsed.netloc}{parsed.path}"


class DevicePreparationShapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    frame_rate: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    clip_count: int = Field(gt=0)
    audio_input_count: int = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    source_bytes: int | None = Field(default=None, gt=0)


class NarrationPreparationPronunciationReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class NarrationPreparationShapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    voice_profile_id: str = Field(min_length=1)
    voice_version: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: Literal["zh", "en", "zh-en"]
    output_duration_seconds: float = Field(gt=0)
    pronunciation_references: list[NarrationPreparationPronunciationReference] = Field(default_factory=list)
    task_id: str | None = Field(default=None, min_length=1)


class DevicePreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    kind: Literal["cpu", "narration"] = "cpu"
    shape: DevicePreparationShapeRequest | NarrationPreparationShapeRequest

    @model_validator(mode="after")
    def shape_matches_kind(self):
        expected = DevicePreparationShapeRequest if self.kind == "cpu" else NarrationPreparationShapeRequest
        if not isinstance(self.shape, expected):
            raise ValueError("准备类型与输入形状不一致")
        return self


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
    target_type: Literal["topic", "script_section"]
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
    platform: str | None
    play_count: int | None
    like_count: int | None
    comment_count: int | None
    notes: str | None
    published_at: datetime | None
    feedback_events: list[FeedbackEventResponse]
    video_production_chain: list[ProductionChainStepResponse]
    source_file_states: dict[str, SourceFileState]
    created_at: datetime
    updated_at: datetime




















class LLMStatusResponse(BaseModel):
    provider: str
    configured: bool
    model: str | None
    base_url: str


class ObsidianStatusResponse(BaseModel):
    configured: bool
    exists: bool
    path: str | None
    markdown_count: int | None


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


class ProductionProfileResponse(BaseModel):
    workspace_id: str
    workspace_name: str
    brand_name: str
    primary_platform: Literal["douyin"]
    aspect_ratio: Literal["9:16"]
    target_duration_min_seconds: int = Field(ge=5, le=1800)
    target_duration_max_seconds: int = Field(ge=5, le=1800)
    brand_template_name: str
    brand_colors: list[str]
    subtitle_style: str
    intro_text: str
    outro_text: str
    background_music_path: str | None
    voice_name: str
    voice_reference_path: str | None
    voice_reference_text: str
    voice_consent_confirmed: bool
    voice_model_status: Literal["pending", "ready", "blocked", "failed"]
    voice_model_message: str
    source_urls: list[str]
    use_obsidian: bool
    use_hermes: bool
    factuality_gate_enabled: bool
    cloud_enhancement_enabled: bool
    default_voice_profile_id: str | None = None
    default_voice_version: str | None = None
    updated_at: datetime


class ProductionProfileUpdateRequest(BaseModel):
    workspace_name: str = Field(min_length=1, max_length=80)
    brand_name: str = Field(max_length=80)
    brand_template_name: str = Field(max_length=80)
    brand_colors: list[str] = Field(default_factory=list, max_length=8)
    subtitle_style: str = Field(default="", max_length=240)
    intro_text: str = Field(default="", max_length=240)
    outro_text: str = Field(default="", max_length=240)
    background_music_path: str | None = None
    target_duration_min_seconds: int | None = Field(default=None, ge=5, le=1800)
    target_duration_max_seconds: int | None = Field(default=None, ge=5, le=1800)
    voice_name: str = Field(default="", max_length=80)
    voice_reference_path: str | None = None
    voice_reference_text: str = Field(default="", max_length=1000)
    voice_consent_confirmed: bool = False
    source_urls: list[str] = Field(default_factory=list, max_length=40)
    use_obsidian: bool = True
    use_hermes: bool = True
    factuality_gate_enabled: bool = True
    cloud_enhancement_enabled: bool = False

    @field_validator("voice_reference_path")
    @classmethod
    def validate_voice_reference_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_logical_media_reference(
            value,
            field="voice reference",
            allowed_schemes={"workspace"},
        )


class VoiceProfileResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    reference_audio_path: str
    reference_text: str
    consent_confirmed: bool
    is_default: bool
    model_id: str
    status: Literal["pending", "ready", "blocked", "failed"]
    message: str
    created_at: datetime
    updated_at: datetime


class VoiceProfilesResponse(BaseModel):
    voices: list[VoiceProfileResponse]
    total_count: int


class VoiceProfileUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    reference_audio_path: str = Field(min_length=1, max_length=2000)
    reference_text: str = Field(min_length=1, max_length=2000)
    consent_confirmed: bool = False
    is_default: bool = False

    @field_validator("reference_audio_path")
    @classmethod
    def validate_reference_audio_path(cls, value: str) -> str:
        return validate_logical_media_reference(
            value,
            field="voice reference",
            allowed_schemes={"workspace"},
        )




class EpisodeVoiceSelectionResponse(BaseModel):
    episode_id: str
    voice_profile_id: str | None
    voice_version: str | None
    language: Literal["zh", "en", "zh-en"] | None
    authorization_id: str | None
    authorization_version: str | None
    pronunciation_entry_ids: list[str]
    source: Literal["episode", "none"]


class VisualFactResponse(BaseModel):
    kind: Literal["object", "action", "scene"]
    text: str
    label: str | None = Field(default=None, max_length=32)
    sample_times: list[float]


class OCRTextResponse(BaseModel):
    text: str
    confidence: float
    box: list[float]

    @computed_field
    @property
    def retrieval_eligible(self) -> bool:
        return self.confidence >= OCR_RETRIEVAL_MIN_CONFIDENCE


class OCRFrameResponse(BaseModel):
    id: str
    timestamp_seconds: float
    width: int
    height: int
    observations: list[OCRTextResponse]


class SegmentUnderstandingResponse(BaseModel):
    status: Literal["ready", "blocked", "failed"]
    fingerprint: str
    model_id: str
    sample_times: list[float]
    summary: str
    facts: list[VisualFactResponse]
    unknowns: list[str]
    ocr_status: Literal["ready", "blocked", "failed"]
    ocr_model_id: str
    ocr_frames: list[OCRFrameResponse]
    message: str


class MaterialSegmentResponse(BaseModel):
    id: str
    asset_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    description: str
    transcript: str
    visual_status: Literal["pending", "ready", "blocked", "failed"]
    visual_model_id: str
    visual_sample_count: int = Field(ge=0, le=3)
    understanding: SegmentUnderstandingResponse | None = None
    usage_role: Literal["unassigned", "a_roll", "b_roll", "exclude"]
    suggested_usage_role: Literal["unassigned", "a_roll", "b_roll"]
    usage_role_confidence: float = Field(ge=0, le=1)
    usage_role_reason: str
    review_status: Literal["raw", "approved", "rejected"]
    system_tags: list[str]
    user_tags: list[str]
    collection_ids: list[str]
    usage_count: int = Field(ge=0)
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MaterialSpeechObservationResponse(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class MaterialSpeechAnalysisResponse(BaseModel):
    status: Literal["ready", "blocked", "failed"]
    source_fingerprint: str
    model_id: str
    observations: list[MaterialSpeechObservationResponse]
    message: str


class MaterialAssetResponse(BaseModel):
    id: str
    workspace_id: str
    # 当前可见范围：private / shared。回读同一列，与显式共享/收回写入的值一致。
    visibility: str = CONTENT_VISIBILITY_DEFAULT
    file_name: str
    path: str
    workspace_reference: str | None = None
    size_bytes: int = Field(ge=0)
    modified_at: datetime
    media_type: Literal["video", "audio", "image"] = "video"
    source_url: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    usage_role: Literal["unassigned", "a_roll", "b_roll", "exclude"]
    suggested_usage_role: Literal["unassigned", "a_roll", "b_roll"]
    usage_role_confidence: float = Field(ge=0, le=1)
    usage_role_reason: str
    review_status: Literal["raw", "approved", "rejected"]
    use_state: ContentUseState
    analysis_status: Literal["pending", "ready", "blocked", "failed"]
    analysis_message: str
    user_tags: list[str] = Field(default_factory=list)
    # The user's own words about this material and the batch it arrived with.
    # Kept beside the tags but never merged into them: a note is not a fact the
    # system may search, filter or score on.
    user_description: str = ""
    upload_context: str = ""
    speech_analysis: MaterialSpeechAnalysisResponse | None = None
    segments: list[MaterialSegmentResponse]
    created_at: datetime
    updated_at: datetime


class MediaLinkCheckRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class MediaLinkCheckResponse(BaseModel):
    url: str
    supported: bool
    media_type: Literal["video", "audio", "image"] | None = None
    file_name: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    message: str


class MediaLinkImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class MediaLinkImportResponse(BaseModel):
    url: str
    media_type: Literal["video", "audio", "image"]
    file_name: str
    size_bytes: int = Field(ge=0)
    file_path: str
    asset_id: str
    provenance_id: str
    reused_existing_file: bool
    analysis_status: Literal["pending", "ready", "blocked", "failed"]
    analysis_message: str


class MaterialLibraryResponse(BaseModel):
    assets: list[MaterialAssetResponse]
    total_count: int
    limit: int = Field(default=24, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    has_more: bool = False


class MaterialCollectionResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    parent_id: str | None
    created_at: datetime
    updated_at: datetime


class MaterialCollectionsResponse(BaseModel):
    collections: list[MaterialCollectionResponse]
    total_count: int


class MaterialCollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=120)


class MaterialTagStatResponse(BaseModel):
    tag: str
    # Materials carrying this tag, counted once each. The segment count below
    # keeps its own meaning and is not a second material count.
    asset_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    usage_count: int = Field(ge=0)


class MaterialUsageRecordTaskResponse(BaseModel):
    id: str | None
    name: str | None
    stage: str | None
    status: str | None
    current_timeline_version_id: str | None


class MaterialUsageRecordResponse(BaseModel):
    plan_id: str
    clip_id: str
    used_at: datetime
    task: MaterialUsageRecordTaskResponse | None
    role: str | None
    manually_selected: bool | None
    timeline_version_id: str | None
    resolution: Literal["current", "unknown"]


class MaterialUsageRecordsResponse(BaseModel):
    items: list[MaterialUsageRecordResponse]
    total_count: int = Field(ge=0)


class MaterialLibraryInsightsResponse(BaseModel):
    asset_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    tags: list[MaterialTagStatResponse]
    recommended_gaps: list[str]


class MaterialReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_status: Literal["raw", "approved", "rejected"]
    usage_role: Literal["unassigned", "a_roll", "b_roll", "exclude"] = "unassigned"


class MaterialAssetTagsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_asset_updated_at: datetime | None = None
    user_tags: list[str] = Field(max_length=50)


class MaterialIntakeBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_directory: str = Field(min_length=1)


class MaterialFileRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size_bytes: str = Field(pattern=r"^[0-9]+$")
    mtime_ns: str = Field(pattern=r"^[0-9]+$")
    ctime_ns: str = Field(pattern=r"^[0-9]+$")
    inode: str = Field(pattern=r"^[0-9]+$")


class MaterialPendingCopyResponse(BaseModel):
    relative_path: str
    expected_revision: MaterialFileRevision


class MaterialCopyCompleteRequest(MaterialPendingCopyResponse):
    model_config = ConfigDict(extra="forbid")
    confirmation_source: Literal["manual", "app_import"]


class MaterialIntakeStatusResponse(BaseModel):
    bound_to_current_workspace: bool
    binding_available: bool
    configured_directory: str | None
    bound_directory: str | None
    directory_state: Literal["ready", "missing", "unmounted", "invalid", "read_only", "changed", "unknown"]
    directory_message: str
    repair_action: str | None
    scan_interval_seconds: int
    file_counts: dict[str, int]
    pending_files: list[MaterialPendingCopyResponse]


class MaterialCopyCompleteResponse(BaseModel):
    confirmed: Literal[True]
    intake: MaterialIntakeStatusResponse


class MaterialBatchTagsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_ids: list[str] = Field(min_length=1, max_length=100)
    add: list[str] = Field(default_factory=list, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)


class MaterialBatchTagsResponse(BaseModel):
    assets: list[MaterialAssetResponse]


class MaterialAssetNoteRequest(BaseModel):
    """One material's own text. Empty strings are a real value, not an omission."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1)
    user_description: str = Field(default="", max_length=2000)
    upload_context: str = Field(default="", max_length=2000)


class MaterialAssetNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MaterialAssetNoteRequest] = Field(min_length=1, max_length=100)


class MaterialAssetNotesResponse(BaseModel):
    assets: list[MaterialAssetResponse]


class MaterialBusinessObjectResponse(BaseModel):
    id: str
    kind: Literal["product", "dish", "person"]
    name: str


class MaterialBusinessObjectsResponse(BaseModel):
    items: list[MaterialBusinessObjectResponse]
    total_count: int = Field(ge=0)


class MaterialBusinessObjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["product", "dish", "person"]
    name: str = Field(min_length=1, max_length=80)


class MaterialAssetBusinessObjectsSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_object_ids: list[str] = Field(default_factory=list, max_length=50)


class MaterialUseStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_state: ContentUseState


class MaterialSegmentUpdateRequest(BaseModel):
    expected_asset_updated_at: datetime | None = None
    expected_segment_updated_at: datetime | None = None
    description: str = Field(default="", max_length=1000)
    transcript: str = Field(default="", max_length=10000)
    review_status: Literal["raw", "approved", "rejected"] = "raw"
    usage_role: Literal["unassigned", "a_roll", "b_roll", "exclude"] | None = None
    user_tags: list[str] | None = Field(default=None, max_length=50)
    collection_ids: list[str] | None = Field(default=None, max_length=20)


class ModelSpecResponse(BaseModel):
    id: str
    capability: str
    display_name: str
    repository: str
    revision: str
    license_name: str
    license_url: str
    approximate_download_bytes: int = Field(ge=0)


class ModelRuntimeStatusResponse(BaseModel):
    model: ModelSpecResponse
    state: Literal[
        "missing_runtime",
        "missing_worker",
        "not_installed",
        "invalid",
        "ready",
    ]
    ready: bool
    message: str
    model_path: str
    runtime_python_path: str | None
    worker_path: str | None


class ModelCatalogResponse(BaseModel):
    models: list[ModelRuntimeStatusResponse]


class ModelInstallRequest(BaseModel):
    license_accepted: bool = False


class ModelInstallJobResponse(BaseModel):
    id: str
    model_id: str
    display_name: str
    status: Literal["queued", "running", "ready", "failed", "interrupted"]
    phase: str
    message: str
    bytes_downloaded: int = Field(ge=0)
    bytes_total: int = Field(ge=0)
    progress: float = Field(ge=0, le=1)
    license_name: str
    license_url: str
    license_accepted_at: datetime
    created_at: datetime
    updated_at: datetime
    model_root_path: str
    external_volume: str | None
    free_bytes: int = Field(ge=0)
    required_bytes: int = Field(ge=0)




class ProductionBlockerResponse(BaseModel):
    code: str
    message: str


class ProductionReadinessResponse(BaseModel):
    ready: bool
    blockers: list[ProductionBlockerResponse]
    asset_count: int
    approved_asset_count: int
    approved_segment_count: int
    total_duration_seconds: float


class ProductionBatchItemResponse(BaseModel):
    episode_id: str
    status: Literal[
        "queued",
        "running",
        "waiting_for_user",
        "rendered",
        "failed",
        "interrupted",
    ]
    plan_id: str | None
    aspect_ratio: Literal["9:16"]
    narration_status: Literal["ready", "unavailable"]
    subtitle_status: Literal["ready", "not_generated", "unavailable"]
    output_url: str | None
    subtitle_url: str | None
    message: str
    attempts: int = Field(ge=0)
    updated_at: datetime


class ProductionBatchResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    target_count: int
    status: Literal[
        "created",
        "blocked",
        "running",
        "waiting_for_user",
        "interrupted",
        "ready",
        "reviewed",
        "failed",
    ]
    episode_ids: list[str]
    items: list[ProductionBatchItemResponse]
    blocker_codes: list[str]
    message: str
    rendered_count: int = Field(ge=0)
    waiting_review_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductionBatchesResponse(BaseModel):
    batches: list[ProductionBatchResponse]
    total_count: int


class PlanProfileReference(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)


class PlanKnowledgeReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_item_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=100)


class CreationPlanDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=120)
    target_count: int = Field(default=1, ge=1, le=50)
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"] = "zh-CN"
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    duration_target_seconds: int = Field(default=60, ge=15, le=600)
    duration_mode: Literal["exact", "approximate"] = "exact"
    duration_range_seconds: tuple[float, float] | None = None
    presenter_share_target: Literal[0, 20, 40, 60, 80, 100] = 0
    lipsync_quality: Literal["high", "fast"] = "high"
    selected_voice: PlanProfileReference | None = None
    selected_person: PlanProfileReference | None = None
    selected_knowledge: PlanKnowledgeReference | None = None
    template_id: str | None = Field(default=None, min_length=1, max_length=120)
    template_version: int | None = Field(default=None, ge=1)

    @field_validator("goal")
    @classmethod
    def _goal_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("goal must not be blank")
        return normalized

    @field_validator("name")
    @classmethod
    def _name_must_not_be_blank_when_set(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank when set")
        return normalized


    @model_validator(mode="after")
    def delivery_duration(self):
        from app.creation.duration_requirement import DurationRequirement
        DurationRequirement(self.duration_target_seconds, self.duration_mode, self.duration_range_seconds)
        return self

class CreationPlanPreviewRequest(CreationPlanDefaultsRequest):
    pass


class AssistantReplyPlanOriginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    turn_id: UUID
    request_id: UUID


class CreationPlanConfirmRequest(CreationPlanDefaultsRequest):
    preflight_fingerprint: str = Field(min_length=64, max_length=64)
    reply_origin: AssistantReplyPlanOriginRequest | None = None


class CreationPlanKnowledgeReferenceResponse(BaseModel):
    id: str
    version: str
    title: str
    authorization: str
    ownership: str


class CreationPlanSourceReferenceResponse(BaseModel):
    id: str
    connector_id: str
    source_label: str
    title: str
    license: str
    identity: str = ""


class CreationPlanVoiceReferenceResponse(BaseModel):
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


class CreationPlanPronunciationReferenceResponse(BaseModel):
    id: str
    version: str
    scope: Literal["workspace", "person", "task"]
    scope_reference: str | None
    language: Literal["zh", "en", "zh-en"]


class CreationPlanBlockerResponse(BaseModel):
    reason: str
    impact: str
    repair_action: str
    stage: str
    blocks_confirmation: bool


class MaterialSegmentMatchResponse(BaseModel):
    """一条候选素材及其可核对的依据。

    前四个字段是原有形状。后面这些是「凭什么说这条候选对」的证据，缺一个
    用户就只能盲选。字段名与 production.MaterialSegmentMatch 一一对应。

    几乎全部可选：检索会从「文字整段命中」降级到「分词命中」再到「仅按结构化
    条件返回」，降级路径产出的证据天然更少。要求它们必填会把一条本来能正常
    返回的候选变成 422 —— 那比少几个字段糟得多。
    """

    asset_ref: str
    segment_ref: str
    # 不加上界：production 侧对 score 没有截断，多个标签条件（每条 +0.15）叠加
    # 多条文字命中（+0.45 + 每条理由 +0.1）可以合法地超过 1.0。写 le=1.0 会把
    # 合法结果挡成 422。
    score: float = Field(ge=0)
    reasons: list[str]
    rank: int = 0
    match_tier: str = "structural"
    evidence_excerpt: str = ""
    needs_confirmation: bool = True
    matched_terms: list[str] = Field(default_factory=list)
    matched_conditions: list[str] = Field(default_factory=list)
    asset_file_name: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    duration_seconds: float = 0.0
    time_range: str = ""
    tags: list[str] = Field(default_factory=list)


class MaterialSegmentSearchResponse(BaseModel):
    matches: list[MaterialSegmentMatchResponse]
    qualification_message: str
    total_count: int
    truncated: bool


class CreationTaskResponse(BaseModel):
    id: str
    workspace_id: str
    plan_id: str
    # 当前可见范围：private / shared。回读同一列，与显式共享/收回写入的值一致。
    visibility: str = CONTENT_VISIBILITY_DEFAULT
    name: str
    scenario: str
    stage: Literal["topic", "script", "narration", "montage", "review"]
    stage_status: Literal[
        "not_started", "running", "waiting_for_user", "completed", "failed"
    ]
    status: Literal["created", "running", "waiting_for_user", "completed", "failed"]
    progress: int = Field(ge=0, le=100)
    recovery_point: str
    selected_topic_id: str | None
    confirmed_script_id: str | None
    narration_cache_key: str | None
    montage_plan_id: str | None
    current_timeline_version_id: str | None = None
    timeline_sequence: int = Field(default=0, ge=0)
    can_undo: bool = False
    can_redo: bool = False
    can_revise_script: bool = False
    output_ready: bool
    stage_reason: str | None
    stage_impact: str | None
    stage_repair_action: str | None
    stage_retryable: bool | None
    created_at: datetime
    updated_at: datetime


class CreationTasksResponse(BaseModel):
    items: list[CreationTaskResponse]
    total_count: int = Field(ge=0)


class CreationTaskTopicGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreationTaskTopicConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adoption_kind: Literal["manual", "delegated_system"] = "manual"
    assistant_turn_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("选题标题不能为空")
        return value.strip() if value is not None else None


class TopicRouteSnapshotResponse(BaseModel):
    provider_kind: str
    execution_location: Literal["cloud", "local"]
    model_id: str
    endpoint_identity: str
    connection_version: str
    availability: Literal["unconfigured", "unverified", "failed", "succeeded"]
    checked_at: datetime


class ProvidedTopicRouteSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["provided_topic"]
    provider: Literal["none"]


class TopicEgressPreflightResponse(BaseModel):
    cloud_egress: bool
    source_count: int = Field(ge=0)
    text_excerpt_count: int = Field(ge=0)
    reason: str | None
    impact: str | None
    repair_action: str | None


class TopicGenerationRunResponse(BaseModel):
    id: str
    workspace_id: str
    task_id: str
    plan_id: str
    status: Literal["running", "succeeded", "failed"]
    route_snapshot: TopicRouteSnapshotResponse | ProvidedTopicRouteSnapshotResponse
    prompt_schema_version: str
    input_references: list[dict[str, object]]
    reason: str | None
    impact: str | None
    repair_action: str | None
    retryable: bool | None
    started_at: datetime
    finished_at: datetime | None


class TopicEvidenceReferenceResponse(BaseModel):
    id: str
    candidate_id: str
    kind: Literal["knowledge", "source"]
    reference_id: str
    version_or_identity: str
    title: str
    authorization_or_license: str
    source_url: str | None
    created_at: datetime


class CreationTaskTopicCandidateResponse(BaseModel):
    id: str
    run_id: str
    title: str
    angle: str
    audience: str
    outline: list[str]
    score: int = Field(ge=0, le=100)
    selection_status: Literal["unselected", "confirmed"]
    created_at: datetime


class CreationTaskTopicsResponse(BaseModel):
    task: CreationTaskResponse
    candidates: list[CreationTaskTopicCandidateResponse]
    evidence_references: list[TopicEvidenceReferenceResponse]
    selected_topic_id: str | None
    latest_run: TopicGenerationRunResponse | None
    route_preflight: TopicRouteSnapshotResponse
    egress_preflight: TopicEgressPreflightResponse


class CreationTaskScriptGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskKnowledgeAdoptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_plan_updated_at: datetime
    expected_task_updated_at: datetime
    knowledge_references: list[PlanKnowledgeReference] = Field(min_length=1, max_length=64)
    assistant_turn_id: UUID | None = None


class TaskKnowledgeAdoptionResponse(BaseModel):
    request_id: UUID
    task_id: str
    plan_id: str
    status: Literal["adopted", "unchanged"]
    recorded_at: datetime
    added_references: list[CreationPlanKnowledgeReferenceResponse]
    previous_references: list[CreationPlanKnowledgeReferenceResponse]
    knowledge_references: list[CreationPlanKnowledgeReferenceResponse]
    plan_updated_at: datetime
    task_updated_at: datetime
    retained_script_count: int
    previous_stage_status: str
    next_step: Literal["generate_new_script", "continue_topic", "unchanged"]


class CreationTaskScriptSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    visual_prompt: str = Field(min_length=1, max_length=1000)
    evidence_reference_ids: list[str] = Field(max_length=12)


class CreationTaskScriptConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    sections: list[CreationTaskScriptSectionRequest] = Field(min_length=1, max_length=20)

    expected_updated_at: datetime
    adoption_kind: Literal["manual", "delegated_system"] = "manual"
    assistant_turn_id: UUID | None = None
    edit_mode: Literal["replace_all"] | None = None


class CreationTaskScriptDraftSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    visual_prompt: str = Field(default="", max_length=1000)
    evidence_reference_ids: list[str] = Field(default_factory=list, max_length=12)


class CreationTaskScriptDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    sections: list[CreationTaskScriptDraftSectionRequest] = Field(min_length=1, max_length=20)
    expected_updated_at: datetime | None = None
    assistant_turn_id: UUID | None = None
    edit_mode: Literal["replace_all"] | None = None


class CreationTaskScriptWindowStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    title: str = Field(min_length=1, max_length=160)
    sections: list[CreationTaskScriptDraftSectionRequest] = Field(
        min_length=1, max_length=SCRIPT_SECTION_WINDOW_LIMIT)
    assistant_turn_id: UUID | None = None


class CreationTaskScriptSectionsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_updated_at: datetime
    sections: list[CreationTaskScriptDraftSectionRequest] = Field(
        min_length=1, max_length=SCRIPT_SECTION_WINDOW_LIMIT)
    insert_after_section_id: str | None = Field(default=None, min_length=1, max_length=120)
    assistant_turn_id: UUID | None = None


class CreationTaskScriptConfirmSavedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_updated_at: datetime
    adoption_kind: Literal["manual", "delegated_system"] = "manual"
    assistant_turn_id: UUID | None = None


class AssistantReplyScriptTransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    turn_id: UUID
    request_id: UUID
    plan_conversion_request_id: UUID | None = None
    expected_plan_updated_at: datetime
    expected_task_updated_at: datetime
    replace_existing_script_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_script_updated_at: datetime | None = None


class CreationTaskScriptSectionResponse(BaseModel):
    id: str
    title: str
    content: str
    visual_prompt: str
    evidence_reference_ids: list[str]


class CreationTaskScriptGenerationRunResponse(BaseModel):
    id: str
    status: Literal["running", "succeeded", "failed"]
    route_snapshot: TopicRouteSnapshotResponse
    reason: str | None
    impact: str | None
    repair_action: str | None
    retryable: bool | None
    started_at: datetime
    finished_at: datetime | None


class CreationTaskScriptResponse(BaseModel):
    draft_complete: bool = True
    id: str
    task_id: str
    run_id: str
    topic_id: str
    title: str
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    sections: list[CreationTaskScriptSectionResponse] | None
    sections_loaded: bool = True
    sections_total_count: int | None = Field(default=None, ge=0)
    selection_status: Literal["draft", "confirmed"]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    reused_from_entry_id: str | None = None
    source_reply_reference: dict[str, str] | None = None
    adoption: dict[str, object] | None = None
    locked_body_digest: str | None = None

    @model_validator(mode="after")
    def complete_body_or_explicit_header(self):
        if self.sections is None:
            if self.sections_loaded or self.sections_total_count is None:
                raise ValueError("未加载正文必须明确标记并提供真实总段数")
        else:
            if not self.sections_loaded:
                raise ValueError("部分正文只能通过文案窗口返回，不能冒充完整稿件")
            if self.sections_total_count is None:
                self.sections_total_count = len(self.sections)
            elif self.sections_total_count != len(self.sections):
                raise ValueError("完整正文段数与声明总数不符")
        return self


class CreationTaskScriptsResponse(BaseModel):
    task: CreationTaskResponse
    scripts: list[CreationTaskScriptResponse]
    scripts_scope: Literal["active", "history"] = "history"
    latest_run: CreationTaskScriptGenerationRunResponse | None
    route_preflight: TopicRouteSnapshotResponse


class NativeInputReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class NativeWorkingContextRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input_refs: list[NativeInputReference] = Field(default_factory=list,max_length=30)
    available_offset: int = Field(default=0,ge=0)
    available_limit: int = Field(default=30,ge=1,le=100)


class NativeFileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['read','access','write','mkdir']
    path: str = Field(min_length=1)
    offset: int = Field(default=0,ge=0)
    limit: int = Field(default=65536,ge=1,le=65536)
    line_offset: int = Field(default=1,ge=1)
    line_limit: int | None = Field(default=None,ge=1)
    content_base64: str | None = Field(default=None,max_length=349528)
    expected_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class NativeCommandRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1)
    command: str = Field(min_length=1,max_length=262144)
    cwd: str = '/task/work'
    timeout_seconds: float | None = Field(default=None,gt=0,allow_inf_nan=False)
    declared_outputs: list[dict[str,str]] = Field(default_factory=list,max_length=30)


class NativeCommandReadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str | None = None
    job_id: str | None = None
    offset: int = Field(default=0,ge=0)
    limit: int = Field(default=65536,ge=1,le=65536)


class NativeArtifactFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1)
    role: str = Field(min_length=1)
    expected_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class NativeArtifactRegisterRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1)
    execution_job_id: str = Field(min_length=1)
    files: list[NativeArtifactFile] = Field(min_length=1,max_length=30)


class NarrationProtectedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["exact_text", "number", "qualified_claim"]
    source_text: str = Field(min_length=1)
    required_text: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)


class NarrationRevisionRequest(BaseModel):
    """A concrete rewrite chosen by Pi; a semantic receipt is resolved by the trusted host."""
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    script_id: str = Field(min_length=1)
    expected_segment_version: str = Field(min_length=1)
    dependency_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_version_id: str | None = None
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement_text: str = Field(min_length=1)
    protected_rules: list[NarrationProtectedRule]
    semantic_review_ref: str = Field(min_length=1)
    adoption_mode: Literal["apply", "candidate"]


class TaskBudgetQuantities(BaseModel):
    execution_seconds: float | None = Field(ge=0)
    generated_bytes: int | None = Field(ge=0)


class TaskBudgetScopeUsage(BaseModel):
    execution_seconds: float = Field(ge=0)
    generated_bytes: int = Field(ge=0)
    reserved_seconds: float = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    unknown_unit_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)


class TaskBudgetScopeAuthorization(BaseModel):
    model_config = ConfigDict(extra="allow")
    scope_id: str
    revision: int = Field(ge=0)
    limits: TaskBudgetQuantities
    policy_kind: Literal["local_goal", "explicit_total"]
    revoked: bool
    target_task_ids: list[str]
    source_ref: str


class TaskBudgetScopeRead(BaseModel):
    authorization: TaskBudgetScopeAuthorization
    usage: TaskBudgetScopeUsage
    remaining: TaskBudgetQuantities
    status: Literal["available", "revoked", "usage_unknown", "exhausted"]
    coverage: Literal["tts_pcm_only"]


class TaskBudgetReadResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    status: str
    resources: dict[str, object]
    actual_cost: dict[str, object]
    estimates: dict[str, object]
    in_flight: list[dict[str, object]]
    scopes: list[TaskBudgetScopeRead]
    authorization_coverage: Literal["tts_pcm_only", "scope_unbound"]


class TaskBudgetResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    expected_job_id: str = Field(min_length=1)


class NarrationSemanticFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str = Field(min_length=1)
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    assessment: Literal["preserved", "uncertain", "conflict"]
    reason: str = Field(min_length=1)


class NarrationSemanticReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    input_id: str | None = Field(default=None, min_length=1)
    section_id: str = Field(min_length=1)
    script_id: str = Field(min_length=1)
    expected_segment_version: str = Field(min_length=1)
    dependency_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_version_id: str | None = None
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_rules: list[NarrationProtectedRule]
    conclusion: Literal["preserved", "uncertain", "conflict"]
    findings: list[NarrationSemanticFinding] = Field(min_length=1)
    authorized_locked_section_ids: list[str] = Field(default_factory=list)


class CreationTaskNarrationSegmentResponse(BaseModel):
    id: str
    source_section_id: str
    text: str
    audio_reference: str | None
    audio_url: str | None
    duration_seconds: float | None = Field(default=None, gt=0)
    sample_rate: int | None = Field(default=None, gt=0)
    model_id: str | None
    voice_profile_id: str | None
    voice_version: str | None
    language: Literal["zh", "en", "zh-en"]
    status: Literal["pending", "ready", "generating", "failed"]
    source_kind: Literal["generated", "recorded"] = "generated"
    source_artifact_ref: str | None = None
    source_sha256: str | None = None
    source_script_digest: str | None = None


class CreationTaskNarrationResponse(BaseModel):
    task: CreationTaskResponse
    script_id: str
    audio_reference: str
    duration_seconds: float = Field(gt=0)
    sample_rate: int | None = Field(default=None, gt=0)
    model_id: str | None
    voice_profile_id: str | None
    source_kind: Literal["generated", "recorded"] = "generated"
    source_artifact_ref: str | None = None
    source_sha256: str | None = None
    source_script_digest: str | None = None
    language: Literal["zh", "en", "zh-en"]
    pronunciation_version: str
    segments: list[CreationTaskNarrationSegmentResponse]


class CreationTaskNarrationVoiceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_plan_updated_at: datetime
    expected_task_updated_at: datetime
    voice_profile_id: str = Field(min_length=1, max_length=120)
    voice_version: str = Field(min_length=1, max_length=120)
    assistant_turn_id: UUID | None = None


class CreationTaskNarrationVoiceSelectionResponse(BaseModel):
    request_id: UUID
    workspace_id: str
    member_id: str
    task_id: str
    plan_id: str
    status: Literal["selected"]
    recorded_at: datetime
    expected_plan_updated_at: datetime
    expected_task_updated_at: datetime
    plan_updated_at: datetime
    task_updated_at: datetime
    previous_voice: CreationPlanVoiceReferenceResponse | None
    selected_voice: CreationPlanVoiceReferenceResponse
    pronunciation_references: list[CreationPlanPronunciationReferenceResponse]
    confirmed_script_id: str | None


class CreationTaskRecordingAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1, max_length=120)
    source_section_id: str = Field(min_length=1, max_length=120)
    source_ref: str = Field(min_length=1, max_length=240)


class CreationTaskPersonRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration_segment_id: str = Field(min_length=1, max_length=160)
    person_profile_id: str = Field(min_length=1, max_length=120)
    person_version: str = Field(min_length=1, max_length=120)
    motion: Literal["natural"] = "natural"
    expression: Literal["neutral"] = "neutral"
    lipsync_quality: Literal["high", "fast"] | None = None


class CreationTaskPersonRenderResponse(BaseModel):
    id: str
    task_id: str
    person_profile_id: str
    person_version: str
    voice_profile_id: str
    voice_version: str
    narration_segment_id: str
    visual_reference: str
    model_id: str
    motion: Literal["natural"]
    expression: Literal["neutral"]
    status: Literal["pending", "running", "ready", "failed"]
    duration_seconds: float | None = Field(default=None, gt=0)
    alignment_evidence: str | None
    media_url: str | None
    failure_reason: str | None
    impact: str | None
    repair_action: str | None
    created_at: datetime
    updated_at: datetime


class CreationTaskPersonRendersResponse(BaseModel):
    items: list[CreationTaskPersonRenderResponse]
    total_count: int = Field(ge=0)


class CreationTaskVoiceOptionResponse(BaseModel):
    profile_id: str
    version: str
    person_display_name: str
    language: Literal["zh", "en", "zh-en"]
    model_id: str
    runnable: bool
    runnable_reason: str | None


class CreationTaskMontageClipResponse(BaseModel):
    transition_in_seconds: float = 0
    fade_envelope_rate: float = 1
    fade_envelope_offset_frames: float = 0
    fade_envelope_duration_frames: float | None = None
    visual_effects: VisualEffects = Field(default_factory=VisualEffects)
    fit_mode: Literal["fill", "fit"] = "fill"
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    id: str
    cue_id: str
    asset_id: str
    segment_id: str
    source_reference: str
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    timeline_start_seconds: float = Field(ge=0)
    timeline_end_seconds: float = Field(gt=0)
    match_score: float
    match_reasons: list[str]
    needs_confirmation: bool
    role: Literal["a_roll", "b_roll"]
    locked: bool
    manually_selected: bool
    person_render_id: str | None = None


class CreationTaskNarrationCueResponse(BaseModel):
    id: str
    source_section_id: str
    narration_segment_id: str = ""
    text: str
    duration_seconds: float = Field(gt=0)


class TimelineAudioClipResponse(TimelineAudioClip):
    editor_gain_points: list[AudioGainPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def project_editor_gain_points(self):
        self.editor_gain_points = visible_gain_points(self)
        return self


class TimelineAudioTrackResponse(TimelineAudioTrack):
    clips: list[TimelineAudioClipResponse] = Field(default_factory=list)


class AudioTimelineResponse(AudioTimeline):
    tracks: list[TimelineAudioTrackResponse] = Field(default_factory=list)


class CreationTaskQualityFindingResponse(BaseModel):
    kind: Literal["black_frame", "truncation", "clipping", "timestamp_offset", "delivery_duration"]
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    detail: str
    clip_ids: list[str]


class CreationTaskQualityUnavailableResponse(BaseModel):
    kind: str
    reason: str
    status: Literal["unavailable"]


class CreationTaskClippingEventResponse(BaseModel):
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class CreationTaskClippingSummaryResponse(BaseModel):
    event_count: int
    total_clipped_seconds: float
    first_start_seconds: float
    last_end_seconds: float
    full_scale_duty: float | None
    event_list_limit: int
    events: list[CreationTaskClippingEventResponse]
    events_truncated: bool
    truncated_event_count: int
    findings_event_limit: int
    findings_truncated: bool
    findings_truncated_count: int


class CreationTaskBlackFrameSummaryResponse(BaseModel):
    event_count: int
    total_black_seconds: float
    first_start_seconds: float
    last_end_seconds: float
    event_list_limit: int
    events: list[CreationTaskClippingEventResponse]
    events_truncated: bool
    truncated_event_count: int
    findings_event_limit: int
    findings_truncated: bool
    findings_truncated_count: int


class QualityContinuationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    failed_request_id: str | None
    phase: str
    completed_phases: list[str]
    can_request: bool
    reason_code: Literal["actor_unavailable", "runtime_unavailable", "original_actor_required",
                         "source_changed", "policy_unavailable", "algorithm_changed",
                         "failed_request_unavailable", "request_in_flight"] | None
    release_check: Literal["on_submit"]


class QualityContinuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1)
    failed_request_id: str = Field(min_length=1)


class QualityContinuationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str
    job_id: str
    request_id: str


class CreationTaskQualityCheckResponse(BaseModel):
    check_state: Literal["none", "checking", "failed", "done"]
    result_status: Literal["clean", "issues_found", "unavailable"] | None
    stale: bool
    check_id: str | None
    timeline_version_id: str | None
    render_ref: str | None
    checked_at: str | None
    error: str | None
    findings: list[CreationTaskQualityFindingResponse]
    unavailable: list[CreationTaskQualityUnavailableResponse]
    clipping_summary: CreationTaskClippingSummaryResponse | None = None
    black_frame_summary: CreationTaskBlackFrameSummaryResponse | None = None
    continuation: QualityContinuationResponse | None = None
    validation_scope: Literal["deterministic_media_errors_only"]


class CreationTaskQualityCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreationTaskObservationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["final", "media_job"]
    job_id: str | None = Field(default=None, min_length=1, max_length=120)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_target(self):
        if self.kind == "final" and (self.job_id is not None or self.artifact_id is not None):
            raise ValueError("当前成片观察不得带其他作业或产物标识")
        if self.kind == "media_job" and (self.job_id is None or self.artifact_id is None):
            raise ValueError("媒体作业观察须指定真实作业和输出产物")
        return self


class CreationTaskObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_timeline_version_id: str | None
    expected_timeline_sequence: int = Field(ge=0)
    target: CreationTaskObservationTarget
    sample_times_seconds: list[float] = Field(default_factory=list, max_length=3)
    assistant_turn_id: UUID | None = None
    request_id: UUID | None = None

    @field_validator("sample_times_seconds")
    @classmethod
    def validate_sample_times(cls, values):
        import math
        if len(set(values)) != len(values) or any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("采样须为不重复的非负有限时间")
        return values


class KnowledgeSearchItemResponse(BaseModel):
    id: str
    title: str
    version: str
    content_hash: str | None
    excerpt: str
    use_state: ContentUseState
    relevance: float
    matched_terms: list[str]


class KnowledgeSearchResponse(BaseModel):
    items: list[KnowledgeSearchItemResponse]
    total_count: int
    offset: int
    limit: int
    next_offset: int | None


class CreationTaskMontagePlanResponse(BaseModel):
    graphic_overlays: list[TextOverlay | ImageOverlay | ArrowOverlay] = Field(default_factory=list)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    presenter_share_target: Literal[0, 20, 40, 60, 80, 100] = 0
    presenter_share_actual: float = Field(default=0, ge=0, le=100)
    id: str
    task_id: str
    script_id: str
    script_title: str
    status: Literal["waiting_for_user", "ready", "rendering", "rendered", "failed"]
    cues: list[CreationTaskNarrationCueResponse]
    clips: list[CreationTaskMontageClipResponse]
    duration_seconds: float = Field(gt=0)
    low_confidence_clip_count: int = Field(ge=0)
    audio_timeline: AudioTimelineResponse | None = None
    narration_reference: str
    output_url: str | None
    subtitle_url: str | None
    message: str
    created_at: datetime
    updated_at: datetime


class CreationTaskMontageRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreationTaskMontageClipUpdateRequest(BaseModel):
    fit_mode: Literal["fill", "fit"] | None = None
    crop_x: float | None = Field(default=None, ge=0, le=1)
    crop_y: float | None = Field(default=None, ge=0, le=1)
    split_time_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    playback_rate: float | None = Field(default=None, ge=0.25, le=4, allow_inf_nan=False)
    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = Field(default=None, min_length=1, max_length=120)
    segment_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_start_seconds: float | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0, le=99)
    locked: bool | None = None
    confirmed: bool | None = None


class AudioMixPatch(BaseModel):
    """Only explicitly supplied existing-domain controls enter the audio patch."""
    model_config = ConfigDict(extra="forbid")
    gain_db: float = deepcopy(TimelineAudioClip.model_fields["gain_db"])
    muted: bool = deepcopy(TimelineAudioClip.model_fields["muted"])
    fade_in_ms: int = deepcopy(TimelineAudioClip.model_fields["fade_in_ms"])
    fade_out_ms: int = deepcopy(TimelineAudioClip.model_fields["fade_out_ms"])
    denoise_db: float = deepcopy(TimelineAudioClip.model_fields["denoise_db"])
    loudness_target_lufs: float | None = deepcopy(TimelineAudioClip.model_fields["loudness_target_lufs"])
    duck_under_voice: bool = deepcopy(TimelineAudioTrack.model_fields["duck_under_voice"])

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not self.model_fields_set:
            raise ValueError("音频混音修改须指定至少一个参数")
        return self


class AudioMixEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["update", "track"]
    target_id: str = Field(min_length=1, max_length=120)
    values: AudioMixPatch

    @model_validator(mode="after")
    def target_controls(self):
        allowed = ({"gain_db", "muted", "duck_under_voice"} if self.action == "track" else
            {"gain_db", "muted", "fade_in_ms", "fade_out_ms", "denoise_db", "loudness_target_lufs"})
        if not self.values.model_fields_set <= allowed:
            raise ValueError("该混音参数不适用于所选音轨或片段动作")
        return self


EXACT_EDIT_FIELDS = {
    "EditAudioTimeline": {"audio_edit"},
    "AdjustVisualEffects": {"clip_ids", "effects"},
    "ReplaceClip": {"clip_id", "asset_id", "segment_id", "source_start_seconds"},
    "TrimClip": {"clip_id", "source_start_seconds"},
    "ChangeClipSpeed": {"clip_id", "playback_rate"},
    "ChangeCrop": {"clip_id", "fit_mode", "crop_x", "crop_y"},
    "SplitClip": {"clip_id", "split_time_seconds"},
    "ReorderClip": {"clip_id", "position"},
    "SetClipLock": {"clip_id", "locked"}, "ConfirmClip": {"clip_id"},
    "DeleteClip": {"clip_id"}, "DuplicateClip": {"clip_id"},
    "InsertClip": {"clip_id", "asset_id", "segment_id", "source_start_seconds"},
    "ChangeLinkedSpeed": {"clip_id", "playback_rate"},
    "ChangeCanvas": {"aspect_ratio", "fit_mode"},
    "SetClipTransition": {"clip_id", "transition_seconds"},
    "EditGraphicOverlay": {"overlay_action", "graphic_overlay", "overlay_id"},
    "UpdateSubtitleCue": {"track_id", "cue_id", "text", "start_seconds", "end_seconds", "line_breaks", "emphasis", "style"},
    "ReplaceSubtitleText": {"track_id", "find_text", "replacement_text"},
    "SplitSubtitleCue": {"track_id", "cue_id", "split_offset", "split_time_seconds"},
    "MergeSubtitleCue": {"track_id", "cue_id"}, "DeleteSubtitleCue": {"track_id", "cue_id"},
    "ConfigureSubtitleTrack": {"track_id", "style", "subtitle_display", "target_language"},
    "GenerateCoverFromFrame": {"frame_time_seconds", "title", "title_position"},
}
EXACT_MONTAGE_REQUIRED_FIELDS = {
    "EditAudioTimeline": {"audio_edit"}, "AdjustVisualEffects": {"clip_ids", "effects"},
    "ChangeCrop": {"fit_mode", "crop_x", "crop_y"}, "ReplaceClip": {"asset_id", "segment_id"},
    "TrimClip": {"source_start_seconds"}, "SplitClip": {"split_time_seconds"},
    "ChangeClipSpeed": {"playback_rate"}, "ReorderClip": {"position"},
    "SetClipLock": {"locked"}, "ConfirmClip": set(),
}
EXACT_EDIT_ENVELOPE_FIELDS = {"assistant_turn_id", "request_id", "expected_timeline_version_id", "expected_timeline_sequence"}


class StoryboardRegenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    script_id: str = Field(min_length=1, max_length=120)
    input_digest: str = Field(min_length=64, max_length=64)
    expected_timeline_version_id: str = Field(min_length=1, max_length=120)
    expected_timeline_sequence: int = Field(ge=1)
    assistant_turn_id: UUID | None = None


class CreationTaskExactEditRequest(BaseModel):
    audio_edit: AudioMixEditRequest | None = None
    clip_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    effects: VisualEffects | None = Field(default=None,
        description="只修改明确提交的字段；遗漏字段保留每个镜头既有效果。重置字段须显式提交其默认值。")
    assistant_turn_id: UUID | None = None
    request_id: UUID | None = None
    expected_timeline_version_id: str | None = None
    expected_timeline_sequence: int | None = Field(default=None, ge=0)
    overlay_action: Literal["add","update","delete"] | None = None
    graphic_overlay: TextOverlay | ImageOverlay | ArrowOverlay | None = None
    overlay_id: str | None = None
    transition_seconds: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    fit_mode: Literal["fill", "fit"] | None = None
    crop_x: float | None = Field(default=None, ge=0, le=1)
    crop_y: float | None = Field(default=None, ge=0, le=1)
    split_time_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    playback_rate: float | None = Field(default=None, ge=0.25, le=4, allow_inf_nan=False,
        description="ChangeClipSpeed：绝对画面速度，每1秒timeline消耗rate秒source，source_span=timeline_span*rate；0.7减速、2加速，2.5秒槽@0.7消耗1.75秒源片段，保留时间槽/旁白/字幕。ChangeLinkedSpeed：相对当前速度倍率，联动改变时轴，不是绝对速度。")
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    operation_type: Literal[
        "EditAudioTimeline",
        "AdjustVisualEffects",
        "ReplaceClip",
        "ChangeCanvas",
        "SetClipTransition",
        "EditGraphicOverlay",
    "ChangeCrop",
        "TrimClip",
        "SplitClip",
        "ChangeClipSpeed",
        "ChangeLinkedSpeed",
        "DeleteClip",
        "DuplicateClip",
        "InsertClip",
        "ReorderClip",
        "SetClipLock",
        "ConfirmClip",
        "RegenerateNarrationSegment",
        "UpdateSubtitleCue",
        "ReplaceSubtitleText",
        "SplitSubtitleCue",
        "MergeSubtitleCue",
        "DeleteSubtitleCue",
        "ConfigureSubtitleTrack",
        "GenerateCoverFromFrame",
        "ChangePersonSegment",
        "RegeneratePersonRender",
    ]
    aspect_ratio: Literal["9:16", "16:9", "1:1"] | None = None
    split_offset: int | None = Field(default=None, ge=1)
    find_text: str | None = Field(default=None, min_length=1, max_length=96)
    replacement_text: str | None = Field(default=None, max_length=96)
    track_id: str | None = Field(default=None, min_length=1, max_length=120)
    clip_id: str | None = Field(default=None, min_length=1, max_length=120)
    narration_segment_id: str | None = Field(default=None, min_length=1, max_length=120)
    cue_id: str | None = Field(default=None, min_length=1, max_length=120)
    start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    text: str | None = Field(default=None, min_length=1, max_length=96)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    title_position: Literal["center", "bottom"] = "bottom"
    subtitle_display: Literal["none", "zh", "en", "zh-en"] | None = None
    target_language: Literal["en-US"] | None = None
    line_breaks: list[int] | None = Field(default=None, max_length=8)
    emphasis: list[str] | None = Field(default=None, max_length=8)
    style: dict[str, object] | None = None
    frame_time_seconds: float | None = Field(default=None, ge=0)
    delivery_instruction: str | None = Field(default=None, min_length=1, max_length=120)
    asset_id: str | None = Field(default=None, min_length=1, max_length=120)
    segment_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_start_seconds: float | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0, le=99)
    locked: bool | None = None

    def action_values(self) -> dict[str, object]:
        """Validated execution values, separate from the explicit request digest."""
        values = self.model_dump(mode="json", exclude_unset=True, exclude=EXACT_EDIT_ENVELOPE_FIELDS)
        if self.operation_type == "EditGraphicOverlay" and self.graphic_overlay is not None:
            values["graphic_overlay"] = self.graphic_overlay.model_dump(mode="json")
        return values

    @model_validator(mode="after")
    def exact_action_fields(self):
        allowed = EXACT_EDIT_FIELDS.get(self.operation_type)
        if allowed is not None and self.model_fields_set - allowed - EXACT_EDIT_ENVELOPE_FIELDS - {"operation_type"}:
            raise ValueError("精确编辑包含与当前动作无关的字段")
        if self.operation_type == "EditAudioTimeline" and self.audio_edit is None:
            raise ValueError("音频混音动作须指定audio_edit")
        if self.operation_type == "AdjustVisualEffects":
            if not self.clip_ids or len(self.clip_ids) != len(set(self.clip_ids)) or any(not ref or len(ref) > 120 for ref in self.clip_ids):
                raise ValueError("画面效果动作须指定不重复的当前镜头ID")
            if self.effects is None or not self.effects.model_fields_set:
                raise ValueError("画面效果动作须指定至少一个效果参数")
        if self.operation_type == "ConfigureSubtitleTrack":
            modes = {"style", "subtitle_display", "target_language"} & self.model_fields_set
            if len(modes) > 1:
                raise ValueError("字幕样式、显示和翻译是不同动作，不混合提交")
        return self


class CreationTaskEditApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID | None = None
    expected_base_version_id: str | None = None
    expected_input_digest: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def replay_identity(self):
        if self.model_fields_set and self.request_id is None:
            raise ValueError("应用请求绑定字段需要稳定request_id")
        return self


class CreationTaskTimelineUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistant_turn_id: UUID | None = None
    expected_timeline_version_id: str | None = None
    expected_timeline_sequence: int | None = Field(default=None, ge=0)


class CreationTaskTimelineRestoreRequest(CreationTaskTimelineUndoRequest):
    request_id: str = Field(min_length=1, max_length=128)
    target_operation_id: str | None = Field(default=None, min_length=1, max_length=160)


class CreationTaskSubtitleCueUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=48)


class CompoundEditSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    included_step_indices: list[int] | None = Field(default=None,min_length=1,max_length=100)
    raw_command: str | None = Field(default=None,min_length=2,max_length=200)


class CreationTaskEditPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_turn_id: UUID | None = None
    raw_command: str = Field(min_length=2, max_length=500)
    context_stage: Literal[
        "topic", "script", "narration", "montage", "review", "subtitle_cover"
    ]
    selected_object_refs: list[str] = Field(default_factory=list, max_length=8)
    playhead_ms: int | None = Field(default=None, ge=0)

    replacement_asset_id: str | None = Field(default=None, min_length=1, max_length=120)
    replacement_segment_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def replacement_pair(self):
        if (self.replacement_asset_id is None) != (self.replacement_segment_id is None):
            raise ValueError("替换候选须同时包含素材与片段")
        return self


class EditIntentResponse(BaseModel):
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
    confidence: float = Field(ge=0, le=1)
    status: str
    created_at: datetime


class TypedEditOperationDraftResponse(BaseModel):
    operation_type: Literal[
        "ChangePresenterShare",
        "EditAudioTimeline",
    "EditCompoundTimeline",
        "RemoveSilence",
        "AdjustVisualEffects",
        "ChangeCanvas",
        "SetClipTransition",
        "EditGraphicOverlay",
        "ChangeCrop",
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
        "UndoTimelineVersion",
    "RedoTimelineVersion",
    ]
    payload: dict[str, object]


class EditPreviewItemResponse(BaseModel):
    image_url: str | None = None
    video_url: str | None = None
    audio_url: str | None = None
    ref: str
    label: str
    detail: str


class SubtitleTextChangeResponse(BaseModel):
    cue_id: str
    before: str
    after: str
    occurrences: int


class EditChangePreviewResponse(BaseModel):
    before: EditPreviewItemResponse
    after: EditPreviewItemResponse
    text_changes: list[SubtitleTextChangeResponse] = Field(default_factory=list)


class EditProposalResponse(BaseModel):
    id: str
    workspace_id: str
    task_id: str
    intent_id: str
    summary: str
    target_refs: list[str]
    operation: TypedEditOperationDraftResponse
    invalidated_refs: list[str]
    preview: EditChangePreviewResponse
    external_effects: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


class CreationTaskEditPreviewResponse(BaseModel):
    intent: EditIntentResponse
    proposal: EditProposalResponse


class MaterialAssistantSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["asset", "segment"]
    asset_id: str = Field(min_length=1, max_length=120)
    segment_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_updated_at: datetime | None = None
    expected_segment_updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.scope == "segment" and self.segment_id is None:
            raise ValueError("片段范围需要明确片段")
        if self.scope == "asset" and self.segment_id is not None:
            raise ValueError("整条素材范围不能带片段")
        return self


class AssistantSavedMaterialReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["material"]
    label: str = Field(min_length=1, max_length=240)
    selection: MaterialAssistantSelectionRequest


class AssistantSavedProfileReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["person", "voice"]
    label: str = Field(min_length=1, max_length=240)
    profile_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)


class AssistantSavedKnowledgeReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["knowledge"]
    label: str = Field(min_length=1, max_length=240)
    knowledge_item_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    locator: str | None = Field(default=None, min_length=1, max_length=500)


class AssistantSavedTimelineReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shot", "narration", "audio", "subtitle", "effect"]
    object_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_range(self):
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("选中时间范围需要同时提供开始和结束")
        if self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("选中时间范围的结束必须晚于开始")
        return self


AssistantSavedReferenceItem = Annotated[
    AssistantSavedMaterialReference | AssistantSavedProfileReference
    | AssistantSavedKnowledgeReference | AssistantSavedTimelineReference,
    Field(discriminator="kind"),
]


class AssistantSavedSelectionReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["selection"]
    items: list[AssistantSavedReferenceItem] = Field(min_length=1, max_length=32)


AssistantSavedReference = Annotated[
    AssistantSavedMaterialReference | AssistantSavedProfileReference
    | AssistantSavedKnowledgeReference | AssistantSavedTimelineReference
    | AssistantSavedSelectionReference,
    Field(discriminator="kind"),
]


class AssistantConversationReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_reference: AssistantSavedReference | None
    expected_reference_version: int = Field(ge=0)


class AssistantConversationReferenceResponse(BaseModel):
    selected_reference: AssistantSavedReference | None = None
    reference_version: int = Field(default=0, ge=0)
    status: Literal["ready", "unavailable"] = "ready"
    reason: str | None = None


class AssistantConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_kind: Literal["home", "material", "task"]
    scope_id: str = Field(min_length=1, max_length=240)
    title: str = Field(default="新对话", min_length=1, max_length=80)


class AssistantConversationCurrentRequest(AssistantConversationCreateRequest):
    pass


class AssistantConversationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=80)
    draft_text: str | None = Field(default=None, max_length=4000)
    expected_draft_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_draft_version(self):
        if self.draft_text is not None and self.expected_draft_version is None:
            raise ValueError("保存草稿必须提供expected_draft_version")
        return self


class AssistantConversationDraftRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    user_message: str = Field(min_length=2, max_length=4000)
    expected_draft_version: int = Field(ge=0)


class AssistantConversationTurnResponse(BaseModel):
    id: UUID
    sequence: int
    user_message: str
    assistant_text: str
    status: Literal["running", "completed", "failed", "stopped"]
    events: list[dict[str, object]]
    result: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime


class AssistantConversationRequestResponse(BaseModel):
    client_request_id: UUID
    conversation_id: UUID
    user_message: str
    request_context: dict[str, object]
    expected_draft_version: int
    expected_reference_version: int | None
    outcome: Literal["consumed", "recovered"]
    turn_id: UUID | None
    turn_status: Literal["running", "completed", "failed", "stopped"] | None


class AssistantConversationResponse(BaseModel):
    selected_reference: AssistantSavedReference | None = None
    reference_version: int = Field(default=0, ge=0)
    id: UUID
    workspace_id: str
    scope_kind: Literal["home", "material", "task"]
    scope_id: str
    title: str
    draft_text: str
    draft_version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    turns: list[AssistantConversationTurnResponse] = Field(default_factory=list)


class AssistantConversationDraftRestoreResponse(BaseModel):
    status: Literal["restored", "already_recovered", "already_persisted"]
    conversation: AssistantConversationResponse


class AssistantConversationListResponse(BaseModel):
    items: list[AssistantConversationResponse]


class AssistantConversationStopRequest(BaseModel):
    turn_id: UUID


class AssistantConversationInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    target_turn_id: UUID
    mode: Literal["steer", "follow_up"]
    message: str = Field(min_length=2, max_length=4000)
    expected_draft_version: int = Field(ge=0)
    expected_reference_version: int = Field(ge=0)


class AssistantProfileReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["person", "voice"]
    profile_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)


class AssistantKnowledgeReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_item_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    locator: str | None = Field(default=None, min_length=1, max_length=500)


class CreationTaskAssistantTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message: str = Field(min_length=2, max_length=1000)
    client_request_id: UUID
    expected_draft_version: int = Field(ge=0)
    expected_reference_version: int | None = Field(default=None, ge=0)
    expected_timeline_version_id: str | None = None
    expected_timeline_sequence: int | None = Field(default=None, ge=0)
    selected_profile_reference: AssistantProfileReferenceRequest | None = None
    selected_knowledge_reference: AssistantKnowledgeReferenceRequest | None = None
    context_stage: Literal["topic", "script", "narration", "montage", "review", "subtitle_cover"]
    selected_object_refs: list[str] = Field(default_factory=list, max_length=8)
    playhead_ms: int | None = Field(default=None, ge=0)


class ResourceAdmissionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["cpu_percent", "available_memory_bytes", "swap_used_bytes", "disk_free_bytes", "disk_io_bytes_per_second"]
    unit: Literal["percent", "bytes", "bytes_per_second"]
    actual: int | float | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    passed: bool | None = None

    @field_validator("actual", "minimum", "maximum", mode="before")
    @classmethod
    def finite_measurement(cls, value):
        import math
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
            raise ValueError("资源检查数值必须为非负有限数或未知")
        return value

    @model_validator(mode="after")
    def measurement_unit(self):
        expected = "percent" if self.metric == "cpu_percent" else "bytes_per_second" if self.metric == "disk_io_bytes_per_second" else "bytes"
        if self.unit != expected:
            raise ValueError("资源检查单位与指标不一致")
        return self


class ResourceAdmissionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    device_request_id: str
    task_id: str
    job_id: str
    input_digest: str
    base_version_id: str | None
    attempt_number: int = Field(ge=1)
    last_grant_generation: int | None
    phase: Literal["admission"]
    observation_kind: Literal["first_unknown", "first_refused"]
    checked_at: datetime
    observed_interval_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    context: Literal["pending_request", "history"]
    admitted: Literal[False] | None
    checks: list[ResourceAdmissionCheck] = Field(max_length=5)
    summary: str = Field(max_length=480)
    repair_action: str = Field(max_length=160)

    @model_validator(mode="after")
    def observation_scope(self):
        offset = self.checked_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("资源检查时点必须是UTC")
        if (self.observation_kind == "first_unknown") != (self.admitted is None):
            raise ValueError("资源检查类别与准入结果不一致")
        return self


class TaskMediaJobResponse(BaseModel):
    operation: str
    target_refs: list[str] = Field(default_factory=list, max_length=8)
    input_digest: str
    created_at: datetime
    job_id: str
    status: Literal["queued", "running", "interrupted", "succeeded", "failed", "cancelled", "stale"]
    result_kind: Literal["edit_preview", "audio_source", "recording", "audio_listen", "waveform", "graphic_source", "observation"]
    result_url: str
    device_request_id: str | None = None
    waiting_reason: str | None = None
    base_version_id: str | None = None
    result: dict[str, object] | None = None
    failure_reason: str | None = None
    repair_action: str | None = None
    resource_wait: ResourceAdmissionReceipt | None = None


class MediaScriptInputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+\.png$")
    source_ref: str = Field(min_length=1, max_length=300)
    expected_artifact_id: str = Field(min_length=1, max_length=120)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MediaScriptPngOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+\.png$")
    max_bytes: int = Field(gt=0, strict=True)
    width: int = Field(gt=0, strict=True, description="PNG输出宽度上限，像素")
    height: int = Field(gt=0, strict=True, description="PNG输出高度上限，像素")


class MediaScriptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: UUID
    assistant_turn_id: UUID | None = None
    expected_timeline_version_id: str | None
    expected_timeline_sequence: int = Field(ge=0, strict=True)
    script_source: str = Field(min_length=1, max_length=65536)
    language: Literal["python"] = "python"
    purpose: Literal["preview"] = "preview"
    parameters: dict[str, object] = Field(default_factory=dict)
    input_refs: list[MediaScriptInputReference] = Field(default_factory=list, max_length=8)
    requested_outputs: list[MediaScriptPngOutput] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def finite_parameters(self):
        import json
        try:
            encoded = json.dumps(self.parameters, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("脚本参数须为有限JSON值") from exc
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("脚本参数超过有界大小")
        return self


class MediaScriptToolSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    assistant_turn_id: UUID | None = None
    producer_job_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    parameter_schema: dict[str, object]


class MediaScriptToolUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    producer_job_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    parameter_schema: dict[str, object]


class MediaScriptToolRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)


class MediaScriptToolPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: UUID
    assistant_turn_id: UUID | None = None
    tool_version: int = Field(ge=1, strict=True)
    expected_timeline_version_id: str | None
    expected_timeline_sequence: int = Field(ge=0, strict=True)
    parameters: dict[str, object] = Field(default_factory=dict)
    input_refs: list[MediaScriptInputReference] = Field(default_factory=list, max_length=8)


class AssistantEditRequestLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=120)
    request_id: UUID
    phase: Literal["preview", "apply", "script", "observation"] = "preview"
    status: Literal["recorded", "unknown", "rejected", "not_submitted"]
    query_url: str
    input_digest: str | None = None
    submitted_input: dict[str, object] | None = None
    result_status_code: int | None = None
    result: dict[str, object] | None = None


class AssistantScriptToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["save", "query", "update", "revoke"]
    status: Literal["recorded", "unknown", "rejected", "not_submitted"]
    request_id: UUID | None = None
    tool_id: str | None = None
    tool_version: int | None = None
    expected_version: int | None = Field(default=None, ge=1, strict=True)
    query_url: str
    submitted_input: dict[str, object] | None = None
    tool: dict[str, object] | None = None


class StoryboardRegenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    montage_plan_id: str
    timeline_version_id: str
    timeline_sequence: int = Field(ge=1, strict=True)
    operation_id: str


class StoryboardRegenerationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    task_id: str
    job_id: str
    status: Literal["queued", "running", "interrupted", "succeeded", "failed", "cancelled"]
    stage: Literal["montage_plan"]
    script_id: str
    narration_cache_key: str
    base_timeline_version_id: str
    base_timeline_sequence: int = Field(ge=1, strict=True)
    result: StoryboardRegenerationResult | None
    failure_reason: str | None
    result_url: str


class AssistantStoryboardRegenerationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["storyboard_regeneration_receipt"]
    action: Literal["submit", "query"]
    receipt: StoryboardRegenerationReceipt


class StoryboardRegenerationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=500)
    repair_action: str = Field(max_length=500)


class AssistantStoryboardRegenerationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["storyboard_regeneration_unknown", "storyboard_regeneration_rejected"]
    action: Literal["submit"]
    request_id: UUID
    query_url: str
    failure: StoryboardRegenerationFailure


class AssistantSegmentRevisionResponse(BaseModel):
    kind: Literal['segment_revision'] = 'segment_revision'
    action: Literal['submit', 'query', 'resume']
    request_id: str
    revision: dict[str, object] | None = None
    failure: dict[str, object] | None = None


class AssistantTimelineRestoreResponse(BaseModel):
    kind: Literal['timeline_restore_request'] = 'timeline_restore_request'
    action: Literal['undo', 'redo', 'query']
    request_id: str
    receipt: dict[str, object] | None = None
    failure: dict[str, object] | None = None


class AssistantScriptWindowRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["script_window_request"] = "script_window_request"
    operation: Literal["start", "patch", "confirm"]
    task_id: str
    request_id: UUID
    script_id: str | None = None
    status: Literal["saved", "confirmed", "unknown", "failed", "rejected", "not_submitted"]
    updated_at: datetime | None = None
    total_count: int | None = Field(default=None, ge=0)
    affected_section_ids: list[str] | None = None
    submission_effect: Literal["not_submitted", "rejected", "unknown"] | None = None
    failure: dict[str, object] | None = None
    input_id: str | None = None
    input_turn_id: str | None = None


class CreationTaskAssistantTurnResponse(BaseModel):
    script_window_request: AssistantScriptWindowRequestResponse | None = None
    timeline_restore: AssistantTimelineRestoreResponse | None = None
    segment_revision: AssistantSegmentRevisionResponse | None = None
    storyboard_regeneration: AssistantStoryboardRegenerationReceipt | AssistantStoryboardRegenerationFailure | None = None
    script_tool: AssistantScriptToolReceipt | None = None
    edit_request: AssistantEditRequestLocator | None = None
    media_job: TaskMediaJobResponse | None = None
    reply: str
    provider: str
    model: str
    edit_preview: CreationTaskEditPreviewResponse | None = None
    script_draft: CreationTaskScriptResponse | None = None
    execution_action: Literal["start", "resume", "cancel"] | None = None
    execution_status: dict[str, object] | None = None
    topic_candidates: CreationTaskTopicsResponse | None = None
    timeline_action: Literal["apply", "undo", "redo", "reject"] | None = None
    turn_id: UUID | None = None
    turn_status: Literal["running", "completed", "stopped"] = "completed"
    draft_version: int | None = Field(default=None, ge=0)


class CreationTaskAssistantHistoryEntry(BaseModel):
    role: Literal["user", "assistant"]
    message: str
    stage: str
    script_id: str | None = None
    at: datetime


class CreationTaskAssistantHistoryResponse(BaseModel):
    items: list[CreationTaskAssistantHistoryEntry]


class EditOperationResponse(BaseModel):
    id: str
    workspace_id: str
    task_id: str
    proposal_id: str
    operation_type: Literal[
        "ChangePresenterShare",
        "EditAudioTimeline",
    "EditCompoundTimeline",
        "RemoveSilence",
        "AdjustVisualEffects",
        "ChangeCanvas",
        "SetClipTransition",
        "EditGraphicOverlay",
        "ChangeCrop",
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
        "RegenerateNarrationSegment",
        "ReviseScriptSection",
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
        "UndoTimelineVersion",
    "RedoTimelineVersion",
    ]
    payload: dict[str, object]
    base_version_id: str | None
    result_version_id: str | None
    status: str
    failure_reason: str | None
    created_at: datetime
    applied_at: datetime | None


class CreationTaskEditConfirmResponse(BaseModel):
    operation: EditOperationResponse
    montage: CreationTaskMontagePlanResponse | None
    narration: CreationTaskNarrationResponse | None = None
    restore_request: dict[str, object] | None = None
    execution_readiness: dict[str, object] | None = None


class CreationTaskEditQueuedResponse(BaseModel):
    status: Literal["queued"] = "queued"
    job_id: str
    request_id: str


class CreationTaskEditJobReplayResponse(BaseModel):
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    job_id: str
    request_id: str | None
    failure_reason: str | None = None


class CreationPlanPreviewResponse(BaseModel):
    updated_at: datetime | None = None
    creation_intent: dict[str, object] | None = None
    adoption: dict[str, object] | None = None
    template_id: str | None = None
    template_version: int | None = None
    template_settings: dict[str, object] = Field(default_factory=dict)
    goal: str
    name: str
    target_count: int = Field(ge=1)
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    duration_target_seconds: int = Field(ge=15)
    duration_mode: Literal["exact", "approximate"] = "exact"
    duration_range_seconds: tuple[float, float] | None = None
    presenter_share_target: Literal[0, 20, 40, 60, 80, 100] = 0
    lipsync_quality: Literal["high", "fast"] = "fast"
    knowledge_references: list[CreationPlanKnowledgeReferenceResponse]
    source_references: list[CreationPlanSourceReferenceResponse]
    selected_person: PlanProfileReference | None = None
    selected_voice: CreationPlanVoiceReferenceResponse | None
    pronunciation_references: list[CreationPlanPronunciationReferenceResponse]
    approved_asset_count: int = Field(ge=0)
    approved_clip_count: int = Field(ge=0)
    material_rule_summary: str
    material_matches: list[MaterialSegmentMatchResponse]
    model_snapshot: list[dict[str, object]]
    confirmation_points: list[str]
    blockers: list[CreationPlanBlockerResponse]
    confirmable: bool
    preflight_fingerprint: str


class CreationFinalScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=80000)
    reply_origin: AssistantReplyPlanOriginRequest | None = None


class CreationIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["goal_delegation", "discussion", "final_script"]
    topic_title: str | None = Field(default=None, min_length=1, max_length=80)
    final_script: CreationFinalScriptRequest | None = None


class CreationRequestCreateRequest(CreationPlanDefaultsRequest):
    target_count: int = Field(default=1, ge=1, le=9_007_199_254_740_991)
    request_key: UUID
    output_relation: Literal["single", "different_topics", "variants", "series"] | None = None
    material_scope: Literal["workspace_library", "today_received"] = "workspace_library"
    assistant_turn_id: UUID | None = None
    intent: CreationIntentRequest | None = None


class CreationRequestReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    output_relation: Literal["single", "different_topics", "variants", "series"] | None = None
    material_scope: Literal["workspace_library", "today_received"] | None = None
    requirements: CreationPlanDefaultsRequest | None = None


class CreationRequestDeliveryRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["zh-CN", "en-US", "zh-CN/en-US"] = "zh-CN"
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    duration_target_seconds: int = Field(default=60, ge=15, le=600)
    presenter_share_target: Literal[0, 20, 40, 60, 80, 100] = 0
    lipsync_quality: Literal["high", "fast"] = "high"
    selected_voice: PlanProfileReference | None = None
    selected_person: PlanProfileReference | None = None


class CreationRequestWindowGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    requirements: CreationRequestDeliveryRequirements | None = None

    @field_validator("goal")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("逐条目标不能为空")
        return value.strip()


class CreationRequestWindowPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    goals: list[CreationRequestWindowGoal] = Field(min_length=1, max_length=8)


class CreationRequestWindowConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    adoption_kind: Literal["manual", "delegated_system"] = "manual"
    assistant_turn_id: UUID | None = None


class CreationRequestWindowReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    goals: list[CreationRequestWindowGoal] | None = Field(default=None, min_length=1, max_length=8)


class CreationRequestItemResponse(BaseModel):
    ordinal: int = Field(ge=1)
    window_id: str | None = None
    plan_id: str
    task_id: str
    plan: CreationPlanPreviewResponse | None = None
    task: CreationTaskResponse | None = None


class CreationRequestWindowResponse(BaseModel):
    id: str
    request_id: str
    ordinal_start: int = Field(ge=1)
    status: Literal["proposed", "confirmed"]
    input_fingerprint: str
    entries: list[CreationRequestItemResponse] = Field(max_length=8)
    created_at: datetime
    confirmed_at: datetime | None = None


class CreationRequestResponse(BaseModel):
    id: str
    goal: str
    target_count: int = Field(ge=1)
    output_relation: Literal["single", "different_topics", "variants", "series"] | None = None
    requirements: dict[str, object]
    intent: dict[str, object] | None = None
    material_scope: dict[str, object]
    clarification_questions: list[str]
    revision: int = Field(ge=1)
    status: Literal["awaiting_clarification", "awaiting_plan", "awaiting_confirmation", "active"]
    expanded_count: int = Field(ge=0)
    confirmed_count: int = Field(ge=0)
    task_status_counts: dict[str, int]
    current_window: CreationRequestWindowResponse | None = None


class CreationRequestItemsResponse(BaseModel):
    items: list[CreationRequestItemResponse]
    next_after_ordinal: int | None
    has_more: bool


class WorkspaceAssistantPlanTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=2, max_length=1000)
    conversation_id: UUID
    client_request_id: UUID
    expected_draft_version: int = Field(ge=0)
    expected_reference_version: int | None = Field(default=None, ge=0)
    selected_profile_reference: AssistantProfileReferenceRequest | None = None
    selected_knowledge_reference: AssistantKnowledgeReferenceRequest | None = None


class MaterialAssistantResultResponse(BaseModel):
    scope: Literal["asset", "segment"]
    asset_ref: str
    segment_ref: str | None = None
    user_tags: list[str]
    system_tags: list[str] | None = None
    action: Literal["replace", "add", "remove"] | None = None
    changed: bool | None = None
    previous_user_tags: list[str] | None = None



class WorkspaceAssistantPlanTurnResponse(BaseModel):
    material_result: MaterialAssistantResultResponse | None = None
    reply: str
    provider: str
    model: str
    plan_preview: CreationPlanPreviewResponse | None = None
    creation_request: CreationRequestResponse | None = None
    turn_id: UUID | None = None
    turn_status: Literal["running", "completed", "stopped"] = "completed"
    draft_version: int | None = Field(default=None, ge=0)




class MaterialAssistantTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=2, max_length=1000)
    conversation_id: UUID
    client_request_id: UUID
    expected_draft_version: int = Field(ge=0)
    expected_reference_version: int | None = Field(default=None, ge=0)
    selection: MaterialAssistantSelectionRequest | None = None




class MaterialAssistantTurnResponse(WorkspaceAssistantPlanTurnResponse):
    turn_status: Literal["running", "completed", "stopped", "failed"] = "completed"


class AssistantStreamEvent(BaseModel):
    """UI-safe projection of Pi text/tool events and the existing final response."""

    type: Literal["turn_started", "turn_status", "text_delta", "tool_start", "tool_end", "result", "error"]
    turn_id: UUID | None = None
    turn_state: Literal["running", "completed", "failed", "stopped"] | None = None
    draft_version: int | None = Field(default=None, ge=0)
    delta: str | None = None
    tool_id: int | None = None
    label: str | None = None
    ok: bool | None = None
    summary: str | None = None
    failure: dict[str, str | int | bool | None | list[dict[str, str | int | bool | None]]] | None = None
    result: dict[str, object] | None = None
    message: str | None = None
    status: int | None = None


class CreationPlanResponse(CreationPlanPreviewResponse):
    id: str
    workspace_id: str
    # 当前可见范围：private / shared。回读同一列，与显式共享/收回写入的值一致。
    visibility: str = CONTENT_VISIBILITY_DEFAULT
    status: Literal["confirmed"]
    creation_task: CreationTaskResponse
    created_at: datetime
    confirmed_at: datetime
    updated_at: datetime


class VideoTemplateVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1,max_length=80)
    style_instruction: str = Field(default="",max_length=500)

    @field_validator("name", "style_instruction", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value,str) else value


class SaveVideoTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    style_instruction: str = Field(default="", max_length=500)
    template_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_version: int | None = Field(default=None, ge=1)


class PersonRenderCapabilityResponse(BaseModel):
    available: bool
    model_id: str | None
    message: str
    repair_action: str | None


class SubtitleCueTrackResponse(BaseModel):
    id: str
    source_cue_id: str | None
    text: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    speaker: str | None
    line_breaks: list[int]
    emphasis: list[str]


class SubtitleTrackResponse(BaseModel):
    id: str
    workspace_id: str
    task_id: str
    timeline_version_id: str
    language: Literal["zh-CN", "en-US"]
    source_language: Literal["zh-CN", "en-US"] | None
    style: dict[str, object]
    cues: list[SubtitleCueTrackResponse]
    generation_method: Literal["timeline", "translation"]
    model_id: str | None
    artifact_ref: str | None
    transform_id: str | None
    status: Literal["valid", "failed", "unavailable", "superseded"]
    failure_reason: str | None
    impact: str | None
    repair_action: str | None
    created_at: datetime
    updated_at: datetime


class CoverAssetResponse(BaseModel):
    id: str
    workspace_id: str
    task_id: str
    timeline_version_id: str
    source_video_ref: str
    frame_time_seconds: float = Field(ge=0)
    title: str
    safe_area: dict[str, float]
    layout: dict[str, object]
    generation_method: Literal["video_frame", "image_model"]
    model_or_engine: str
    artifact_ref: str | None
    transform_id: str | None
    status: Literal["valid", "failed", "unavailable", "superseded"]
    failure_reason: str | None
    impact: str | None
    repair_action: str | None
    created_at: datetime
    updated_at: datetime


class TaskCapabilityResponse(BaseModel):
    available: bool
    model_id: str | None
    message: str
    repair_action: str | None


class CreationTaskWorkspaceResponse(BaseModel):
    task: CreationTaskResponse
    plan: CreationPlanPreviewResponse
    selected_topic: CreationTaskTopicCandidateResponse | None
    topic_evidence: list[TopicEvidenceReferenceResponse]
    scripts: list[CreationTaskScriptResponse]
    scripts_scope: Literal["active"] = "active"
    available_voices: list[CreationTaskVoiceOptionResponse]
    narration: CreationTaskNarrationResponse | None
    portrait_available: bool = False
    person_render_capability: PersonRenderCapabilityResponse
    person_renders: list[CreationTaskPersonRenderResponse] = Field(default_factory=list)
    montage: CreationTaskMontagePlanResponse | None
    subtitle_tracks: list[SubtitleTrackResponse] = Field(default_factory=list)
    cover_asset: CoverAssetResponse | None = None
    subtitle_translation_capability: TaskCapabilityResponse
    cover_frame_capability: TaskCapabilityResponse
    image_generation_capability: TaskCapabilityResponse


class NarrationCueResponse(BaseModel):
    id: str
    source_section_id: str
    text: str
    duration_seconds: float = Field(gt=0)


class MontageClipResponse(BaseModel):
    id: str
    cue_id: str
    asset_id: str
    segment_id: str
    source_path: str | None
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    timeline_start_seconds: float = Field(ge=0)
    timeline_end_seconds: float = Field(gt=0)
    match_score: float
    match_reasons: list[str]
    needs_confirmation: bool
    role: Literal["a_roll", "b_roll"] = "b_roll"
    locked: bool = False
    manually_selected: bool = False


class MontagePlanResponse(BaseModel):
    id: str
    workspace_id: str
    batch_id: str
    episode_id: str
    script_title: str
    status: Literal["waiting_for_user", "ready", "rendering", "rendered", "failed"]
    cues: list[NarrationCueResponse]
    clips: list[MontageClipResponse]
    duration_seconds: float = Field(ge=0)
    low_confidence_clip_count: int = Field(ge=0)
    aspect_ratio: Literal["9:16"]
    narration_status: Literal["ready", "unavailable"]
    subtitle_status: Literal["ready", "not_generated", "unavailable"]
    output_url: str | None
    subtitle_url: str | None
    message: str
    created_at: datetime
    updated_at: datetime


class MontagePlansResponse(BaseModel):
    plans: list[MontagePlanResponse]
    total_count: int










class MontageClipCandidateResponse(BaseModel):
    asset_id: str
    segment_id: str
    file_name: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    description: str
    score: float = Field(ge=0, le=1)
    reasons: list[str]




class ProductionEpisodeOptionResponse(BaseModel):
    id: str
    title: str
    status: EpisodeStatus


class ProductionFoundationResponse(BaseModel):
    profile: ProductionProfileResponse
    readiness: ProductionReadinessResponse
    recent_batches: list[ProductionBatchResponse]
    recent_montage_plans: list[MontagePlanResponse] = Field(default_factory=list)
    eligible_episodes: list[ProductionEpisodeOptionResponse] = Field(default_factory=list)


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
    fix_command: str | None = None
    last_checked_at: datetime


class FeatureGateResponse(BaseModel):
    feature: str
    available: bool
    reason: str


class RuntimeHealthResponse(BaseModel):
    summary: RuntimeHealthSummaryResponse
    items: list[RuntimeHealthItemResponse]
    feature_gates: list[FeatureGateResponse]


# ── Setup preflight ────────────────────────────────────────────────────

class PreflightCheckItem(BaseModel):
    """A single preflight check result."""
    id: str
    label: str
    status: Literal["ready", "missing", "invalid", "degraded", "not_applicable"]
    summary: str
    impact: str
    repair_action: str
    settings_target: str | None = None
    required: bool


class SetupPreflightResponse(BaseModel):
    """GET /api/setup/preflight response contract."""
    overall: Literal["ready", "blocked"]
    checks: list[PreflightCheckItem]
    generated_at: datetime


class OnboardingCompletionResponse(BaseModel):
    workspace_id: str
    completed: bool


class PendingVideoResponse(BaseModel):
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime


class ProcessVideoRequest(BaseModel):
    file_name: str | None = None




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
    model_config = ConfigDict(extra="forbid")

    url: str
    use_state: ContentUseState = "available"


class SourceConnectorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=80)
    protocol: str = Field(min_length=1, max_length=80)
    account_or_rule: str = Field(min_length=1, max_length=2000)
    frequency: str = Field(min_length=1, max_length=80)
    permissions: list[str] = Field(default_factory=list, max_length=40)


class SourceRunEvidenceResponse(BaseModel):
    id: str
    workspace_id: str
    connector_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    cursor_before: str | None
    cursor_after: str | None
    item_count: int
    reason: str | None
    impact: str | None
    repair_action: str | None
    retryable: bool | None


class SourceConnectorResponse(BaseModel):
    id: str
    workspace_id: str
    platform: str
    protocol: str
    account_or_rule: str
    frequency: str
    permissions: list[str]
    cursor: str | None
    connection_status: str
    latest_run_id: str | None
    created_at: datetime


class SourceConnectorsResponse(BaseModel):
    items: list[SourceConnectorResponse]
    total_count: int


class KnowledgeSectionResponse(BaseModel):
    locator: str
    title: str
    text: str


class KnowledgeParameterResponse(BaseModel):
    key: str
    value: str
    unit: str | None = None
    locator: str


class SourceItemDetailResponse(BaseModel):
    id: str
    workspace_id: str
    connector_id: str
    source_label: str
    source_url: str | None
    content_type: str
    author: str | None
    published_at: datetime | None
    body: str | None
    subtitles: str | None
    media_references: list[str]
    license: str
    title: str
    file_reference: str | None
    content_hash: str | None = None
    sections: list[KnowledgeSectionResponse] = Field(default_factory=list)
    parameters: list[KnowledgeParameterResponse] = Field(default_factory=list)
    use_state: ContentUseState = "available"
    # 网页快照的抓取时间；不是网页抓取时为空。
    fetched_at: datetime | None = None
    created_at: datetime


class SourceItemDetailsResponse(BaseModel):
    items: list[SourceItemDetailResponse]
    total_count: int


class ManualSourceItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    content_type: str = Field(min_length=1, max_length=80)
    source_url: str | None = Field(default=None, max_length=4000)
    body: str | None = Field(default=None, max_length=200_000)
    subtitles: str | None = Field(default=None, max_length=200_000)
    media_references: list[str] = Field(default_factory=list, max_length=100)
    file_reference: str | None = Field(default=None, max_length=4000)
    author: str | None = Field(default=None, max_length=300)
    published_at: datetime | None = None
    license: str = Field(default="unknown", min_length=1, max_length=300)
    use_state: ContentUseState = "available"


class SaveSourceItemToKnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeFileImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=400)
    content_base64: str = Field(min_length=1, max_length=40_000_000)
    use_state: ContentUseState = "available"


class KnowledgeItemResponse(BaseModel):
    id: str
    workspace_id: str
    content_type: str
    title: str
    body: str | None
    file_reference: str | None
    authorization: str
    ownership: str
    source_item_id: str | None
    version: str
    use_state: ContentUseState = "available"
    # 当前可见范围：shared / private。回读同一列，与保存时写入的值一致。
    visibility: str = CONTENT_VISIBILITY_LEGACY
    content_hash: str | None = None
    sections: list[KnowledgeSectionResponse] = Field(default_factory=list)
    parameters: list[KnowledgeParameterResponse] = Field(default_factory=list)
    # 当前版本的抓取时间和它实际读到的网址。
    fetched_at: datetime | None = None
    source_url: str | None = None
    created_at: datetime
    # 保存时命中已有内容指纹时给出重复结果（对应已有版本号），不新建条目。
    duplicate_of_version: str | None = None
    # 仅保存接口用于确认此次请求绑定的来源；不改变知识条目的原始来源关系。
    saved_source_item_id: str | None = None


class ContentVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: Literal["shared", "private"]


class KnowledgeItemVisibilityRequest(ContentVisibilityRequest):
    """知识条目的可见范围请求；任务、方案、模板与素材共用同一形状。"""


class KnowledgeItemContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=200000)


class KnowledgeGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_workspace_id: str = Field(min_length=1)


class KnowledgeGrantTargetResponse(BaseModel):
    workspace_id: str
    name: str


class KnowledgeGrantResponse(BaseModel):
    id: str
    knowledge_item_id: str
    source_workspace_id: str
    target_workspace_id: str
    granted_by: str
    granted_at: datetime
    revoked_by: str | None = None
    revoked_at: datetime | None = None


class KnowledgeGrantStateResponse(BaseModel):
    can_manage: bool
    source_workspace_id: str
    access_via_grant: bool = False
    target_resolution: Literal["resolved", "same_workspace", "unresolved"]
    target_resolution_message: str
    cross_workspace_status: Literal["AVAILABLE", "UNVERIFIED"]
    targets: list[KnowledgeGrantTargetResponse] = Field(default_factory=list)
    grants: list[KnowledgeGrantResponse] = Field(default_factory=list)


class KnowledgeItemVersionResponse(BaseModel):
    version: str
    content_hash: str | None = None
    source_item_id: str | None = None
    original_source_item_id: str | None = None
    title: str
    body: str | None
    file_reference: str | None
    sections: list[KnowledgeSectionResponse] = Field(default_factory=list)
    parameters: list[KnowledgeParameterResponse] = Field(default_factory=list)
    # 这一版内容是什么时候、从哪个网址取到的：回查旧版时能回到当时的内容。
    fetched_at: datetime | None = None
    source_url: str | None = None
    saved_at: datetime


class KnowledgeItemDetailResponse(KnowledgeItemResponse):
    versions: list[KnowledgeItemVersionResponse] = Field(default_factory=list)


class KnowledgeItemsResponse(BaseModel):
    items: list[KnowledgeItemResponse]
    total_count: int


class SourceSyncResponse(BaseModel):
    connector: SourceConnectorResponse
    run: SourceRunEvidenceResponse


class SourceRunEvidencesResponse(BaseModel):
    items: list[SourceRunEvidenceResponse]
    total_count: int


class AuthorizationResponse(BaseModel):
    id: str
    workspace_id: str
    version: str
    rights_holder: str
    content_types: list[str]
    usage_scopes: list[str]
    effective_from: datetime
    expires_at: datetime | None
    evidence_reference: str
    status: Literal["active", "pending", "expired", "revoked"]


class AuthorizationUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    rights_holder: str = Field(min_length=1, max_length=300)
    content_types: list[str] = Field(min_length=1, max_length=20)
    usage_scopes: list[str] = Field(min_length=1, max_length=20)
    effective_from: datetime
    expires_at: datetime | None = None
    evidence_reference: str = Field(min_length=1, max_length=4000)
    status: Literal["active", "pending", "expired", "revoked"]


class AuthorizationsResponse(BaseModel):
    items: list[AuthorizationResponse]
    total_count: int


class ProfileReferenceResponse(BaseModel):
    asset_id: str | None = None
    segment_id: str | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)


class FixedProfileReference(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)


class PersonProfileResponse(BaseModel):
    id: str
    workspace_id: str
    display_name: str
    version: str
    avatar_reference: str
    background_capabilities: list[str]
    shot_capabilities: list[str]
    authorization: AuthorizationResponse | None
    current_authorization: AuthorizationResponse | None
    runnable: bool
    runnable_reason: str | None
    body_reference_kind: str
    body_reference_name: str | None
    body_reference_available: bool
    body_reference_reason: str | None
    body_reference_repair_action: str | None

    reference: ProfileReferenceResponse | None = None
    use_state: str = "available"
    reference_issue: str | None = None
    reference_url: str | None = None


class PersonProfileUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=300)
    version: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_reference: str = Field(min_length=1, max_length=4000)
    background_capabilities: list[str] = Field(default_factory=list, max_length=40)
    shot_capabilities: list[str] = Field(default_factory=list, max_length=40)
    authorization: AuthorizationUpsertRequest | None = None

    @field_validator("avatar_reference")
    @classmethod
    def validate_avatar_reference(cls, value: str) -> str:
        return validate_logical_media_reference(
            value,
            field="avatar reference",
            allowed_schemes={"asset", "workspace"},
        )

    reference: ProfileReferenceResponse | None = None
    use_state: Literal["available", "reference_only", "excluded", "not_for_automation", "not_for_publish"] = "available"


class PersonProfilesResponse(BaseModel):
    items: list[PersonProfileResponse]
    total_count: int


class VersionedVoiceProfileResponse(BaseModel):
    id: str
    workspace_id: str
    person_profile_id: str
    person_version: str
    version: str
    language: Literal["zh", "en", "zh-en"]
    reference_audio_path: str
    reference_text: str
    model_id: str
    authorization: AuthorizationResponse | None
    current_authorization: AuthorizationResponse | None
    runnable: bool
    runnable_reason: str | None

    display_name: str = ""

    reference: ProfileReferenceResponse | None = None
    use_state: str = "available"
    reference_issue: str | None = None
    reference_url: str | None = None


class VersionedVoiceProfileUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    person_profile_id: str = Field(default="", max_length=120)
    person_version: str = Field(default="", max_length=120)
    version: str | None = Field(default=None, min_length=1, max_length=120)
    language: Literal["zh", "en", "zh-en"]
    reference_audio_path: str = Field(min_length=1, max_length=4000)
    reference_text: str = Field(default="", max_length=4000)
    model_id: str = Field(default="", max_length=300)
    authorization: AuthorizationUpsertRequest | None = None

    @field_validator("reference_audio_path")
    @classmethod
    def validate_versioned_reference_audio_path(cls, value: str) -> str:
        return validate_logical_media_reference(
            value,
            field="reference audio path",
            allowed_schemes={"asset", "workspace"},
        )

    reference: ProfileReferenceResponse | None = None
    use_state: Literal["available", "reference_only", "excluded", "not_for_automation", "not_for_publish"] = "available"
    display_name: str = Field(min_length=1, max_length=300)


class VersionedVoiceProfilesResponse(BaseModel):
    items: list[VersionedVoiceProfileResponse]
    total_count: int


class VoiceReferenceImportResponse(BaseModel):
    reference: str
    file_name: str
    size_bytes: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)


class VoiceSamplingTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    display_name: str = Field(min_length=1, max_length=300)
    language: Literal["zh", "en", "zh-en"] = "zh"
    purpose: Literal["voice_profile"] = "voice_profile"


class MaterialDiscoveryStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_ids: list[str] = Field(min_length=1, max_length=64)
    exclusions: list[str] | None = Field(default=None, max_length=256)


class MaterialDiscoveryControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume", "cancel", "exclude"]
    exclusions: list[str] | None = Field(default=None, max_length=256)


class MaterialDiscoveryReceiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(min_length=1, max_length=100)


class PronunciationEntryResponse(BaseModel):
    id: str
    workspace_id: str
    term: str
    language: Literal["zh", "en", "zh-en"]
    target_pronunciation: str
    scope: Literal["workspace", "person", "task"]
    scope_reference: str | None
    version: str


class PronunciationEntryUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    term: str = Field(min_length=1, max_length=300)
    language: Literal["zh", "en", "zh-en"]
    target_pronunciation: str = Field(min_length=1, max_length=1000)
    scope: Literal["workspace", "person", "task"]
    scope_reference: str | None = Field(default=None, max_length=120)
    version: str = Field(min_length=1, max_length=120)


class PronunciationEntriesResponse(BaseModel):
    items: list[PronunciationEntryResponse]
    total_count: int


class MediaCapabilityResponse(BaseModel):
    capability_id: Literal["voice_clone", "avatar_lipsync"]
    available: bool
    model_id: str | None
    message: str
    impact: str | None
    repair_action: str | None


class VoicePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_profile_id: str = Field(min_length=1, max_length=120)
    voice_version: str = Field(min_length=1, max_length=120)
    preview_text: str = Field(min_length=1, max_length=4000)
    language: Literal["zh", "en"] | None = None
    retry: bool = False


class AvatarLipSyncPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_profile_id: str = Field(min_length=1, max_length=120)
    person_version: str = Field(min_length=1, max_length=120)
    voice_profile_id: str = Field(min_length=1, max_length=120)
    voice_version: str = Field(min_length=1, max_length=120)
    voice_preview_identity: str = Field(min_length=1, max_length=200)
    motion: str = Field(min_length=1, max_length=120)
    expression: str = Field(min_length=1, max_length=120)
    authorization_id: str | None = Field(default=None, min_length=1, max_length=120)
    authorization_version: str | None = Field(default=None, min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=300)
    retry: bool = False


class MediaPreviewRunResponse(BaseModel):
    preview_text: str | None = None
    id: str
    workspace_id: str
    preview_type: Literal["voice", "avatar_lipsync"]
    identity: str
    attempt: int = Field(ge=1)
    status: Literal["queued", "running", "succeeded", "unavailable", "blocked", "failed", "cancelled"]
    capability_id: str
    model_id: str
    language: str
    person_profile_id: str | None
    person_version: str | None
    voice_profile_id: str | None
    voice_version: str | None
    authorization_id: str | None
    authorization_version: str | None
    output_url: str | None
    reason: str | None
    impact: str | None
    repair_action: str | None
    started_at: datetime
    finished_at: datetime | None


class MediaPreviewRunsResponse(BaseModel):
    items: list[MediaPreviewRunResponse]
    total_count: int


class FeedbackSummaryResponse(BaseModel):
    total_count: int
    up_count: int
    down_count: int
    recent_events: list[FeedbackEventResponse]


class SourceSyncStatusResponse(BaseModel):
    source_type: str
    configured: bool
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    error_message: str | None


class SourceSyncStatusesResponse(BaseModel):
    statuses: list[SourceSyncStatusResponse]


class PreferenceItemResponse(BaseModel):
    label: str
    signal: str
    evidence_count: int
    confidence: float = Field(ge=0, le=1)
    enabled: bool = True


class PreferenceToggleRequest(BaseModel):
    signal: str


class PreferencesResponse(BaseModel):
    total_feedback_count: int
    preferences: list[PreferenceItemResponse]


class RuntimeSettingItemResponse(BaseModel):
    name: str
    configured: bool
    value: str | None
    secret: bool


class RuntimeSettingsResponse(BaseModel):
    settings: list[RuntimeSettingItemResponse]
    status: SystemStatusResponse


class JobProgressResponse(BaseModel):
    """一条作业在任务页上的真实读数。估时没有可信依据时就是 null。"""

    id: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    # 只报告作业自己写下的进度，不是阶段序号。
    progress: int
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    elapsed_seconds: float | None
    failure_reason: str | None
    impact: str | None
    repair_action: str | None
    wait_estimate_seconds: float | None
    processing_estimate_seconds: float | None
    estimate_basis: str | None


class TaskJobProgressResponse(BaseModel):
    """任务当前作业和它的全部历史作业；页面关闭后重开读到同一批记录。"""

    task_id: str
    current: JobProgressResponse | None
    items: list[JobProgressResponse]
    total_count: int


class RuntimeSettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_key_action: Literal["delete"] | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None
    product_hunt_token: str | None = None
    asr_provider: Literal["local_whisper", "tencentcloud"] | None = None
    understanding_runtime_python_path: str | None = None
    native_ocr_worker_path: str | None = None
    local_whisper_backend: Literal["openai_whisper", "whisper_cpp"] | None = None
    local_whisper_cli_path: str | None = None
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
    model_root_path: str | None = None
    temporary_root_path: str | None = None
    draft_background_music_path: str | None = None
    topic_source_urls: list[str] | None = None
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_jwt_audience: str | None = None


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
    template_tracks: list[str] | None = None


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
















class EpisodeSummaryResponse(BaseModel):
    id: str
    day_number: int
    episode_date: date
    status: EpisodeStatus
    intake_mode: EpisodeIntakeMode
    content_template_id: str
    selected_topic_id: str | None
    video_source_file_name: str | None
    video_job_status: VideoJobStatus
    video_job_message: str | None
    draft_package_name: str | None
    publish_url: str | None
    platform: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeSummaryResponse]
    total: int


class ProductionChainStepResponse(BaseModel):
    stage: ProductionChainStage
    status: Literal["pending", "skipped", "completed", "failed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None


class ProductionChainResponse(BaseModel):
    episode_id: str
    intake_mode: EpisodeIntakeMode
    chain: list[ProductionChainStepResponse]


class SourceFileStateResponse(BaseModel):
    file_name: str
    state: SourceFileState


class SourceFileStatesResponse(BaseModel):
    episode_id: str
    files: list[SourceFileStateResponse]






class EpisodeHistoryItemResponse(BaseModel):
    id: str
    day_number: int
    episode_date: date
    title: str
    status: EpisodeStatus
    created_at: datetime
    updated_at: datetime | None = None
    platform: str | None = None
    notes: str | None = None
    topic_title: str | None = None
    script_title: str | None = None
    intake_mode: EpisodeIntakeMode | None = None
    publish_url: str | None = None
    play_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    draft_package_path: str | None = None
    video_dedup_report_path: str | None = None
    published_at: datetime | None = None


class EpisodeHistoryResponse(BaseModel):
    items: list[EpisodeHistoryItemResponse]
    total_count: int
    filtered_count: int = 0
    status: EpisodeStatus | None = None


class PreferenceCardResponse(BaseModel):
    id: str
    label: str
    signal: str
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence_count: int
    enabled: bool = True
    latest_evidence_at: datetime | None = None


class PreferencesCardsResponse(BaseModel):
    total_count: int
    enabled_count: int
    disabled_count: int
    cards: list[PreferenceCardResponse]


class PreferenceEvidenceItemResponse(BaseModel):
    feedback_event_id: str
    episode_id: str | None = None
    episode_date: date | None = None
    target_type: str
    target_id: str
    value: str
    reason: str | None = None
    created_at: datetime


class PreferenceEvidenceResponse(BaseModel):
    preference_id: str
    total: int
    items: list[PreferenceEvidenceItemResponse]


class TopicEvidenceItemResponse(BaseModel):
    source_key: str
    source_type: str
    title: str
    ref: str
    excerpt: str | None = None
    freshness: str = "recent"
    freshness_label: str = "近期"
    participated_topics: int = 0
    last_used_at: datetime | None = None


class TopicEvidenceResponse(BaseModel):
    episode_id: str
    total: int
    items: list[TopicEvidenceItemResponse]




class SourceSyncRetryResponse(BaseModel):
    source_type: str
    status: str  # success | skipped | error
    message: str
    item_count: int = 0
    error: str | None = None


class VoiceTranscribeSegment(BaseModel):
    start: float
    end: float
    text: str


class VoiceTranscriptionResponse(BaseModel):
    transcript: str
    provider: str
    segments: list[VoiceTranscribeSegment]
    confidence: float = 0.0


# ── Creator Profile ──────────────────────────────────────────────


class CreatorProfileResponse(BaseModel):
    id: str
    revision: int = 0
    common_expressions: list[str]
    banned_words: list[str]
    style_preferences: dict[str, str]
    created_at: str
    updated_at: str


class CreatorProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    common_expressions: list[str] | None = None
    banned_words: list[str] | None = None
    style_preferences: dict[str, str] | None = None


class CreatorPreferenceChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["remember", "forget"]
    key: str = Field(min_length=1, max_length=60)
    value: str | None = Field(default=None, max_length=300)
    expected_revision: int = Field(ge=0)
    assistant_turn_id: str = Field(min_length=1, max_length=120)
    source_quote: str = Field(min_length=1, max_length=1000)


class CreatorProfileLearnResult(BaseModel):
    profile: CreatorProfileResponse
    summary: dict[str, object]


class ScriptLibrarySaveRequest(BaseModel):
    request_id: UUID
    expected_source_updated_at: datetime
    source_script_id: str
    name: str = Field(min_length=1)
    category: str = ""


class ScriptLibrarySectionResponse(BaseModel):
    id: str
    title: str
    content: str
    visual_prompt: str
    reference_ids: list[str]


class ScriptLibraryEntryResponse(BaseModel):
    id: str
    name: str
    category: str
    source_script_id: str
    source_task_id: str
    source_run_id: str
    source_topic_id: str
    version: int
    supersedes_entry_id: str | None
    title: str
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    sections: list[ScriptLibrarySectionResponse]
    created_at: datetime
    updated_at: datetime


class ScriptLibraryListResponse(BaseModel):
    items: list[ScriptLibraryEntryResponse]
    total_count: int


class ScriptLibraryEntryHeaderResponse(ScriptLibraryEntryResponse):
    sections: None
    sections_loaded: Literal[False] = False
    sections_total_count: int = Field(ge=0)


class ScriptLibraryCursorResponse(BaseModel):
    after_created_at: datetime
    after_entry_id: str


class ScriptLibraryHeaderListResponse(BaseModel):
    items: list[ScriptLibraryEntryHeaderResponse]
    next: ScriptLibraryCursorResponse | None
    total_count: int | None = None


class ScriptLibrarySectionWindowItemResponse(BaseModel):
    ordinal: int = Field(ge=1)
    section: ScriptLibrarySectionResponse
    digest: str


class ScriptLibrarySectionWindowResponse(BaseModel):
    entry_id: str
    version: int
    updated_at: datetime
    total_count: int = Field(ge=0)
    items: list[ScriptLibrarySectionWindowItemResponse]
    next_after_ordinal: int | None
    has_more: bool


class ScriptLibrarySourceResponse(BaseModel):
    reference_id: str
    status: Literal["readable", "missing", "version_changed"]
    title: str | None = None
    version_or_identity: str | None = None
    source_url: str | None = None
    # 引用绑定那一版（reference_id 里的版本）的真实快照事实，供回查当时内容：
    # 原网页链接、抓取时刻与内容指纹。状态取值沿用既有三条，不新增枚举。
    cited_version: str | None = None
    cited_source_url: str | None = None
    cited_fetched_at: datetime | None = None
    cited_content_hash: str | None = None
    # 引用绑定到具体段落/参数时的定位；引用只记到条目与版本时为 None。
    locator: str | None = None


class ScriptLibraryReuseRequest(BaseModel):
    request_id: UUID
    expected_entry_updated_at: datetime
    name: str = Field(min_length=1)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    duration_target_seconds: int = Field(default=60, ge=1, strict=True)
    requirements_confirmed: bool


class ScriptLibraryReuseResponse(BaseModel):
    request_id: UUID
    entry_id: str
    entry_version: int
    task_id: str
    script_id: str
    updated_at: datetime
    status: Literal["saved"]


class ScriptLibrarySaveResponse(BaseModel):
    request_id: UUID
    status: Literal["saved"]
    entry_id: str
    version: int
    updated_at: datetime
    source_task_id: str
    source_script_id: str
    source_updated_at: datetime
    entry: ScriptLibraryEntryHeaderResponse


class TimelineVersionSummaryResponse(BaseModel):
    id: str
    sequence: int
    parent_version_id: str | None
    created_at: datetime
    is_current: bool
    shot_count: int
    subtitle_track_count: int
    narration_segment_count: int
    operation_id: str | None = None
    operation_type: str | None = None
    target_refs: list[str] = Field(default_factory=list)
    source_request_id: str | None = None


class TimelineVersionListResponse(BaseModel):
    task_id: str
    current_version_id: str | None
    items: list[TimelineVersionSummaryResponse]
    total_count: int
    truncated: bool
    next_before_sequence: int | None


class TimelineVersionShotResponse(BaseModel):
    index: int
    id: str
    narration_text: str | None
    start_seconds: float
    end_seconds: float
    description: str | None


class TimelineVersionNarrationSegmentResponse(BaseModel):
    index: int
    id: str
    text: str
    duration_seconds: float | None
    status: str | None


class TimelineVersionArtifactResponse(BaseModel):
    kind: Literal["video", "subtitles", "cover"]
    status: Literal["available", "not_generated", "missing"]
    url: str | None
    reason: str | None


class TimelineVersionDetailResponse(BaseModel):
    task_id: str
    version: TimelineVersionSummaryResponse
    shots: list[TimelineVersionShotResponse]
    narration_segments: list[TimelineVersionNarrationSegmentResponse]
    subtitle_tracks: list[SubtitleTrackResponse]
    cover_asset: CoverAssetResponse | None
    artifacts: list[TimelineVersionArtifactResponse]


# ── 整机首页聚合 ────────────────────────────────────────────────────────


class HomeFactMeta(BaseModel):
    """首页每块统计的口径、事实来源和读取时点。

    没有这三样，页面上的数字就无法回答「这是谁的数、按什么算的、什么时候的」。
    """

    scope: Literal[
        "caller_visible",
        "local_diagnostic",
        "workspace",
        "device_anonymous",
        "host",
    ]
    source: str
    as_of: datetime


class HomeUnknownFact(BaseModel):
    """如实声明没有真实载体的统计项：不猜、不填 0。"""

    key: str
    reason: str


class HomeCountResponse(BaseModel):
    key: str
    label: str
    value: int = Field(ge=0)
    meta: HomeFactMeta


class HomeTrendPointResponse(BaseModel):
    date: date
    count: int = Field(ge=0)


class HomeTrendResponse(BaseModel):
    days: int = Field(ge=1)
    points: list[HomeTrendPointResponse]
    meta: HomeFactMeta


class HomeDeviceQueueResponse(BaseModel):
    """整机匿名的运行与排队数量。

    ``counts`` 是各真实状态表的记录数（含已成功/失败的历史），
    ``running``/``queued`` 才是在跑和排队的数量——两者不是一回事，
    首页不能拿记录数当队列长度。表读不到时后三项留空并说明原因，
    不用 0 冒充「现在很空闲」。只给计数，不含路径、文件名或归属。
    """

    counts: dict[str, int]
    records_total: int | None = Field(default=None, ge=0)
    running: int | None = Field(default=None, ge=0)
    queued: int | None = Field(default=None, ge=0)
    meta: HomeFactMeta


class HomeStorageCapacityResponse(BaseModel):
    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)


class HomeStorageResponse(BaseModel):
    """真实存储事实。目录路径本身不返回，页面不需要也不该拿到。"""

    directory_state: str
    bound_to_current_workspace: bool
    bound_to_another_workspace: bool
    configured_path_changed: bool
    capacity: HomeStorageCapacityResponse | None
    meta: HomeFactMeta


class HomeQueueEntryResponse(BaseModel):
    task_id: str
    title: str
    stage: str
    status: str
    updated_at: datetime


class HomeTodoEntryResponse(BaseModel):
    task_id: str
    title: str
    stage: str
    reason: str
    repair_action: str | None


class HomeWorkEntryResponse(BaseModel):
    task_id: str
    title: str
    updated_at: datetime


class HomeEntriesResponse(BaseModel):
    """可进入的真实条目：每条都带真实 task_id，页面可直接跳转。"""

    queue: list[HomeQueueEntryResponse]
    todos: list[HomeTodoEntryResponse]
    works: list[HomeWorkEntryResponse]
    meta: HomeFactMeta


class HomeAggregationResponse(BaseModel):
    days: int = Field(ge=1)
    counts: list[HomeCountResponse]
    trend: HomeTrendResponse
    device_queue: HomeDeviceQueueResponse
    storage: HomeStorageResponse
    entries: HomeEntriesResponse
    unknown: list[HomeUnknownFact] = Field(default_factory=list)


class SegmentVisualDecisionAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_clip_ids: list[str] = Field(min_length=1,max_length=100)
