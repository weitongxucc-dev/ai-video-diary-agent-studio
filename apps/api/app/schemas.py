from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    components = [parsed.netloc, *parsed.path.lstrip("/").split("/")]
    if any(not component for component in components):
        raise ValueError(f"{field} must contain a relative path")
    for component in components:
        decoded = unquote(component)
        if decoded in {".", ".."} or "/" in decoded or "\\" in decoded or ":" in decoded:
            raise ValueError(f"{field} contains an invalid path component")
    return f"{scheme}://{parsed.netloc}{parsed.path}"


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


class ScriptGenerateRequest(BaseModel):
    target_duration_seconds: int | None = Field(default=None, ge=5, le=1800)


class ScriptRegenerateRequest(BaseModel):
    reason: str | None = None
    target_duration_seconds: int | None = Field(default=None, ge=5, le=1800)


class ScriptSectionRegenerateRequest(BaseModel):
    reason: str | None = None


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


class EpisodeVoiceSelectionRequest(BaseModel):
    voice_profile_id: str | None = Field(default=None, max_length=120)
    voice_version: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_complete_versioned_voice_identity(self) -> "EpisodeVoiceSelectionRequest":
        if (self.voice_profile_id is None) != (self.voice_version is None):
            raise ValueError("声音选择必须同时提供 voice_profile_id 和 voice_version")
        return self


class EpisodeVoiceSelectionResponse(BaseModel):
    episode_id: str
    voice_profile_id: str | None
    voice_version: str | None
    language: Literal["zh", "en", "zh-en"] | None
    authorization_id: str | None
    authorization_version: str | None
    pronunciation_entry_ids: list[str]
    source: Literal["episode", "none"]


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


class MaterialAssetResponse(BaseModel):
    id: str
    workspace_id: str
    file_name: str
    path: str
    workspace_reference: str | None = None
    size_bytes: int = Field(ge=0)
    modified_at: datetime
    duration_seconds: float | None = Field(default=None, ge=0)
    usage_role: Literal["unassigned", "a_roll", "b_roll", "exclude"]
    suggested_usage_role: Literal["unassigned", "a_roll", "b_roll"]
    usage_role_confidence: float = Field(ge=0, le=1)
    usage_role_reason: str
    review_status: Literal["raw", "approved", "rejected"]
    rights_confirmed: bool
    brand_safe: bool
    analysis_status: Literal["pending", "ready", "blocked", "failed"]
    analysis_message: str
    segments: list[MaterialSegmentResponse]
    created_at: datetime
    updated_at: datetime


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
    segment_count: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    usage_count: int = Field(ge=0)


class MaterialLibraryInsightsResponse(BaseModel):
    asset_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    tags: list[MaterialTagStatResponse]
    recommended_gaps: list[str]


class MaterialReviewRequest(BaseModel):
    review_status: Literal["raw", "approved", "rejected"]
    usage_role: Literal["unassigned", "a_roll", "b_roll", "exclude"] = "unassigned"
    rights_confirmed: bool = False
    brand_safe: bool = False


class MaterialSegmentUpdateRequest(BaseModel):
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


class NarrationArtifactResponse(BaseModel):
    episode_id: str
    audio_reference: str
    duration_seconds: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    model_id: str
    voice_profile_id: str | None = None
    cache_key: str = ""


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


class CreationPlanDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=120)
    target_count: int = Field(default=1, ge=1, le=50)
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"] = "zh-CN"
    aspect_ratio: Literal["9:16"] = "9:16"
    duration_target_seconds: int = Field(default=60, ge=15, le=600)

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


class CreationPlanPreviewRequest(CreationPlanDefaultsRequest):
    pass


class CreationPlanConfirmRequest(CreationPlanDefaultsRequest):
    preflight_fingerprint: str = Field(min_length=64, max_length=64)


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


class CreationTaskResponse(BaseModel):
    id: str
    workspace_id: str
    plan_id: str
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


class TopicRouteSnapshotResponse(BaseModel):
    provider_kind: str
    execution_location: Literal["cloud", "local"]
    model_id: str
    endpoint_identity: str
    connection_version: str
    availability: Literal["unconfigured", "unverified", "failed", "succeeded"]
    checked_at: datetime


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
    route_snapshot: TopicRouteSnapshotResponse
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


class CreationTaskScriptSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    visual_prompt: str = Field(min_length=1, max_length=1000)
    evidence_reference_ids: list[str] = Field(min_length=1, max_length=12)


class CreationTaskScriptConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    sections: list[CreationTaskScriptSectionRequest] = Field(min_length=1, max_length=20)


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
    id: str
    task_id: str
    run_id: str
    topic_id: str
    title: str
    language: Literal["zh-CN"]
    sections: list[CreationTaskScriptSectionResponse]
    selection_status: Literal["draft", "confirmed"]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None


class CreationTaskScriptsResponse(BaseModel):
    task: CreationTaskResponse
    scripts: list[CreationTaskScriptResponse]
    latest_run: CreationTaskScriptGenerationRunResponse | None
    route_preflight: TopicRouteSnapshotResponse


class CreationTaskNarrationResponse(BaseModel):
    task: CreationTaskResponse
    script_id: str
    audio_reference: str
    duration_seconds: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    model_id: str
    voice_profile_id: str
    language: Literal["zh"]
    pronunciation_version: str


class CreationTaskNarrationVoiceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_profile_id: str = Field(min_length=1, max_length=120)
    voice_version: str = Field(min_length=1, max_length=120)


class CreationTaskVoiceOptionResponse(BaseModel):
    profile_id: str
    version: str
    person_display_name: str
    language: Literal["zh", "en", "zh-en"]
    model_id: str
    runnable: bool
    runnable_reason: str | None


class CreationTaskMontageClipResponse(BaseModel):
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


class CreationTaskNarrationCueResponse(BaseModel):
    id: str
    source_section_id: str
    text: str
    duration_seconds: float = Field(gt=0)


class CreationTaskMontagePlanResponse(BaseModel):
    id: str
    task_id: str
    script_id: str
    script_title: str
    status: Literal["waiting_for_user", "ready", "rendering", "rendered", "failed"]
    cues: list[CreationTaskNarrationCueResponse]
    clips: list[CreationTaskMontageClipResponse]
    duration_seconds: float = Field(gt=0)
    low_confidence_clip_count: int = Field(ge=0)
    narration_reference: str
    output_url: str | None
    subtitle_url: str | None
    message: str
    created_at: datetime
    updated_at: datetime


class CreationTaskMontageRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreationTaskMontageClipUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = Field(default=None, min_length=1, max_length=120)
    segment_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_start_seconds: float | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0, le=99)
    locked: bool | None = None
    confirmed: bool | None = None


class CreationTaskSubtitleCueUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=48)


class CreationPlanPreviewResponse(BaseModel):
    goal: str
    name: str
    target_count: int = Field(ge=1)
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    aspect_ratio: Literal["9:16"]
    duration_target_seconds: int = Field(ge=15)
    knowledge_references: list[CreationPlanKnowledgeReferenceResponse]
    source_references: list[CreationPlanSourceReferenceResponse]
    selected_voice: CreationPlanVoiceReferenceResponse | None
    pronunciation_references: list[CreationPlanPronunciationReferenceResponse]
    approved_asset_count: int = Field(ge=0)
    approved_clip_count: int = Field(ge=0)
    material_rule_summary: str
    model_snapshot: list[dict[str, object]]
    confirmation_points: list[str]
    blockers: list[CreationPlanBlockerResponse]
    confirmable: bool
    preflight_fingerprint: str


class CreationPlanResponse(CreationPlanPreviewResponse):
    id: str
    workspace_id: str
    status: Literal["confirmed"]
    creation_task: CreationTaskResponse
    created_at: datetime
    confirmed_at: datetime
    updated_at: datetime


class CreationTaskWorkspaceResponse(BaseModel):
    task: CreationTaskResponse
    plan: CreationPlanPreviewResponse
    selected_topic: CreationTaskTopicCandidateResponse | None
    topic_evidence: list[TopicEvidenceReferenceResponse]
    scripts: list[CreationTaskScriptResponse]
    available_voices: list[CreationTaskVoiceOptionResponse]
    narration: CreationTaskNarrationResponse | None
    montage: CreationTaskMontagePlanResponse | None


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


class MontagePlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1, max_length=80)


class MontagePlanConfirmRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=100)


class MontageClipUpdateRequest(BaseModel):
    asset_id: str | None = Field(default=None, min_length=1, max_length=120)
    segment_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_start_seconds: float | None = Field(default=None, ge=0)
    locked: bool | None = None
    confirmed: bool | None = None


class MontageClipOrderRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=100)


class MontageClipCandidateResponse(BaseModel):
    asset_id: str
    segment_id: str
    file_name: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    description: str
    score: float = Field(ge=0, le=1)
    reasons: list[str]


class MontageRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class SaveSourceItemToKnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownership: str = Field(min_length=1, max_length=300)
    authorization: str = Field(min_length=1, max_length=2000)
    version: str = Field(default="1", min_length=1, max_length=100)


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
    created_at: datetime


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


class PersonProfileResponse(BaseModel):
    id: str
    workspace_id: str
    display_name: str
    version: str
    avatar_reference: str
    background_capabilities: list[str]
    shot_capabilities: list[str]
    authorization: AuthorizationResponse
    current_authorization: AuthorizationResponse
    runnable: bool
    runnable_reason: str | None


class PersonProfileUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=120)
    avatar_reference: str = Field(min_length=1, max_length=4000)
    background_capabilities: list[str] = Field(default_factory=list, max_length=40)
    shot_capabilities: list[str] = Field(default_factory=list, max_length=40)
    authorization: AuthorizationUpsertRequest

    @field_validator("avatar_reference")
    @classmethod
    def validate_avatar_reference(cls, value: str) -> str:
        return validate_logical_media_reference(
            value,
            field="avatar reference",
            allowed_schemes={"asset", "workspace"},
        )


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
    authorization: AuthorizationResponse
    current_authorization: AuthorizationResponse
    runnable: bool
    runnable_reason: str | None


class VersionedVoiceProfileUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    person_profile_id: str = Field(min_length=1, max_length=120)
    person_version: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    language: Literal["zh", "en", "zh-en"]
    reference_audio_path: str = Field(min_length=1, max_length=4000)
    reference_text: str = Field(min_length=1, max_length=4000)
    model_id: str = Field(min_length=1, max_length=300)
    authorization: AuthorizationUpsertRequest

    @field_validator("reference_audio_path")
    @classmethod
    def validate_versioned_reference_audio_path(cls, value: str) -> str:
        return validate_logical_media_reference(
            value,
            field="reference audio path",
            allowed_schemes={"workspace"},
        )


class VersionedVoiceProfilesResponse(BaseModel):
    items: list[VersionedVoiceProfileResponse]
    total_count: int


class VoiceReferenceImportResponse(BaseModel):
    reference: str
    file_name: str
    size_bytes: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)


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
    authorization_id: str = Field(min_length=1, max_length=120)
    authorization_version: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=300)
    retry: bool = False


class MediaPreviewRunResponse(BaseModel):
    id: str
    workspace_id: str
    preview_type: Literal["voice", "avatar_lipsync"]
    identity: str
    attempt: int = Field(ge=1)
    status: Literal["running", "succeeded", "unavailable", "blocked", "failed"]
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


class RuntimeSettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
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
    platform: str | None = None
    play_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    notes: str | None = None


class PublishMetricsUpdateRequest(BaseModel):
    publish_url: str | None = None
    platform: str | None = None
    play_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    notes: str | None = None


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


class ArchiveSourcesRequest(BaseModel):
    file_names: list[str] | None = None


class ArchiveSourcesResponse(BaseModel):
    episode_id: str
    archived_count: int
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


class ProductionModeRequest(BaseModel):
    mode: str  # one of: bilibili, youtube, shorts, douyin, xiaohongshu


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
    common_expressions: list[str]
    banned_words: list[str]
    style_preferences: dict[str, str]
    created_at: str
    updated_at: str


class CreatorProfileUpdate(BaseModel):
    common_expressions: list[str] | None = None
    banned_words: list[str] | None = None
    style_preferences: dict[str, str] | None = None


class CreatorProfileLearnResult(BaseModel):
    profile: CreatorProfileResponse
    summary: dict[str, object]
