from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.avatar_lipsync import (
    Authorization,
    AvatarPreviewService,
    PersonProfile,
    PronunciationEntry,
    UnavailableAvatarAndLipSyncProvider,
    VoiceProfile as VersionedVoiceProfile,
)
from app.auth import (
    ForbiddenError,
    OnboardingStateError,
    SupabaseAuthContext,
    UnauthorizedError,
)
from app.config import (
    Settings,
    configure_process_temporary_directory,
    runtime_env_file_path,
)
from app.content_templates import get_content_template, list_content_templates
from app.creator_profile import (
    get_or_create_profile,
    upsert_creator_profile,
)
from app.drafts import DraftWorkspace
from app.edit_plan import (
    apply_confirmed_edit_plan,
    build_edit_plan_workspace,
    revert_edit_plan_segment_decision,
    safe_processed_video_path,
    update_edit_plan_segment_decision,
)
from app.external_sources import ExternalSourceResponseError, HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.llm import (
    LLMConfigurationError,
    LLMResponseError,
    script_generator_from_settings,
)
from app.model_installer import ModelInstaller, ModelInstallError
from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT, ModelCatalog
from app.models import (
    Episode,
    EpisodeIntakeMode,
    EpisodeStatus,
    FeedbackEvent,
    ScriptPackage,
    ScriptSection,
    VideoJobStatus,
    utc_now,
)
from app.montage import MontagePlanner, MontageRenderer, MontageRepository
from app.narration import NarrationArtifact, NarrationGenerator, VoicePreviewService
from app.preference_memory import PreferenceState, build_preference_memory
from app.preflight import setup_preflight
from app.product_hunt import ProductHuntClient
from app.production import (
    CreationPlan,
    CreationPlanBlocker,
    CreationPlanKnowledgeReference,
    CreationPlanPronunciationReference,
    CreationPlanSourceReference,
    CreationPlanVoiceReference,
    CreationTask,
    MediaAuthorityError,
    MaterialCollection,
    NarrationArtifactRecord,
    ProductionBatch,
    ProductionBatchItem,
    ProductionProfile,
    ProductionRepository,
    TopicCandidate as CreationTaskTopicCandidate,
    TopicEvidenceReference,
    TopicGenerationRun,
    VoiceProfile,
)
from app.voice_clone import VoiceCloneError
from app.repository import (
    LEGACY_WORKSPACE_ID,
    EpisodeRepository,
    active_workspace_id,
    load_preference_state_payload,
    reset_workspace_context,
    save_preference_state_payload,
    set_auth_enabled,
    set_workspace_context,
)
from app.runtime_health import runtime_health
from app.runtime_settings import apply_runtime_settings_update, is_local_client_host
from app.source_repository import SourceRepository
from app.schemas import (
    ApproveTopicRequest,
    AuthorizationResponse,
    AuthorizationsResponse,
    AuthorizationUpsertRequest,
    ArchiveSourcesRequest,
    ArchiveSourcesResponse,
    AvatarLipSyncPreviewRequest,
    ContentTemplateSelectRequest,
    ContentTemplatesResponse,
    CreationPlanConfirmRequest,
    CreationPlanPreviewRequest,
    CreationPlanPreviewResponse,
    CreationPlanResponse,
    CreationTaskTopicCandidateResponse,
    CreationTaskTopicConfirmRequest,
    CreationTaskTopicGenerateRequest,
    CreationTaskTopicsResponse,
    CreationTasksResponse,
    CreatorProfileLearnResult,
    CreatorProfileResponse,
    CreatorProfileUpdate,
    DraftAssetResponse,
    DraftEnvironmentResponse,
    DraftImportRequest,
    DraftImportResponse,
    DraftPackageRequest,
    DraftPackageResponse,
    DraftStatusResponse,
    EditPlanResponse,
    EditPlanSegmentDecisionRequest,
    EpisodeHistoryItemResponse,
    EpisodeHistoryResponse,
    EpisodeListResponse,
    EpisodeResponse,
    EpisodeSummaryResponse,
    EpisodeVoiceSelectionRequest,
    EpisodeVoiceSelectionResponse,
    FeedbackEventResponse,
    FeedbackRequest,
    FeedbackRevertRequest,
    FeedbackSummaryResponse,
    KnowledgeNoteResponse,
    KnowledgeItemResponse,
    KnowledgeItemsResponse,
    ManualSourceItemRequest,
    ManualLinkRequest,
    MarkPublishedRequest,
    MaterialAssetResponse,
    MaterialCollectionCreateRequest,
    MaterialCollectionResponse,
    MaterialCollectionsResponse,
    MaterialLibraryInsightsResponse,
    MaterialLibraryResponse,
    MaterialReviewRequest,
    MaterialSegmentUpdateRequest,
    MediaCapabilityResponse,
    MediaPreviewRunResponse,
    MediaPreviewRunsResponse,
    ModelCatalogResponse,
    ModelInstallJobResponse,
    ModelInstallRequest,
    MontageClipCandidateResponse,
    MontageClipOrderRequest,
    MontageClipResponse,
    MontageClipUpdateRequest,
    MontagePlanConfirmRequest,
    MontagePlanCreateRequest,
    MontagePlanResponse,
    MontagePlansResponse,
    MontageRenderRequest,
    NarrationCueResponse,
    NarrationArtifactResponse,
    OnboardingCompletionResponse,
    PendingVideoResponse,
    PreferenceCardResponse,
    PreferenceEvidenceItemResponse,
    PreferenceEvidenceResponse,
    PreferenceItemResponse,
    PreferencesCardsResponse,
    PreferencesResponse,
    PreferenceToggleRequest,
    ProcessedVideoResponse,
    ProcessVideoBatchRequest,
    ProcessVideoRequest,
    ProductionBatchesResponse,
    ProductionBatchResponse,
    ProductionChainResponse,
    ProductionFoundationResponse,
    ProductionModeRequest,
    ProductionProfileResponse,
    ProductionProfileUpdateRequest,
    PersonProfileResponse,
    PersonProfilesResponse,
    PersonProfileUpsertRequest,
    PronunciationEntriesResponse,
    PronunciationEntryResponse,
    PronunciationEntryUpsertRequest,
    PublishMetricsUpdateRequest,
    RetakeReportResponse,
    RuntimeHealthResponse,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
    ScriptGenerateRequest,
    ScriptRegenerateRequest,
    ScriptSectionRegenerateRequest,
    ScriptUpdateRequest,
    ScriptVersionRevertRequest,
    SetupPreflightResponse,
    SourceFileStatesResponse,
    SourceConnectorCreateRequest,
    SourceConnectorResponse,
    SourceConnectorsResponse,
    SourceItemDetailResponse,
    SourceItemDetailsResponse,
    SourceItemResponse,
    SourceRunEvidenceResponse,
    SourceRunEvidencesResponse,
    SourceSyncResponse,
    SourceItemsResponse,
    SaveSourceItemToKnowledgeRequest,
    SourceSyncRetryResponse,
    SourceSyncStatusesResponse,
    SourceSyncStatusResponse,
    SystemStatusResponse,
    TopicEvidenceItemResponse,
    TopicEvidenceResponse,
    TopicEgressPreflightResponse,
    TopicEvidenceReferenceResponse,
    TopicGenerationRunResponse,
    TopicRouteSnapshotResponse,
    UploadVideoRequest,
    VoicePreviewRequest,
    VoiceProfileResponse,
    VoiceProfilesResponse,
    VoiceProfileUpsertRequest,
    VersionedVoiceProfileResponse,
    VersionedVoiceProfilesResponse,
    VersionedVoiceProfileUpsertRequest,
    VoiceTranscriptionResponse,
    WorkflowStatusResponse,
    validate_logical_media_reference,
)
from app.services import EpisodeService
from app.sources import (
    KnowledgeItem,
    ObsidianVault,
    SourceBoundaryError,
    SourceConnector,
    SourceItem,
    SourceSyncFailedError,
    sync_source_connector,
)
from app.topics import (
    CreationTaskGeneratedTopic,
    CreationTaskTopicGenerationError,
    CreationTaskTopicInput,
    CreationTaskTopicSource,
    OpenAICompatibleCreationTaskTopicGenerator,
    TopicGenerationConfigurationError,
    TopicGenerationResponseError,
    topic_generator_from_settings,
)
from app.video import FfmpegVideoProcessor, SourceTracker, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
from app.visual_semantics import visual_semantic_provider_from_catalog
from app.voice import transcribe_audio_bytes
from app.voice_clone import voice_clone_provider_from_catalog
from app.workflow import LangGraphEpisodeWorkflow

app = FastAPI(title="AI视频日记 Agent Studio API")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail if isinstance(exc.detail, str) else type(exc).__name__,
            "detail": str(exc.detail),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    status_map: dict[type[Exception], int] = {
        ValueError: 400,
        FileNotFoundError: 404,
        PermissionError: 403,
        RuntimeError: 500,
        NotImplementedError: 501,
    }
    status_code = 500
    for exc_type, code in status_map.items():
        if isinstance(exc, exc_type):
            status_code = code
            break
    logger.exception(
        "unhandled exception %s: %s", type(exc).__name__, exc
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "error": type(exc).__name__,
            "detail": str(exc),
        },
    )


settings = Settings.from_env()
configure_process_temporary_directory(settings)
set_auth_enabled(settings.auth_enabled)
repo = EpisodeRepository(database_path=settings.database_path)
source_repository = SourceRepository(settings.database_path)
model_catalog = ModelCatalog(
    settings.model_root_path,
    settings.model_runtime_python_path,
    settings.visual_model_worker_path,
    settings.voice_model_worker_path,
)
model_installer = ModelInstaller(
    model_catalog,
    on_complete=lambda: _reload_runtime_dependencies(),
)
visual_semantic_provider = visual_semantic_provider_from_catalog(model_catalog)
voice_clone_provider = voice_clone_provider_from_catalog(model_catalog)
production_repo = ProductionRepository(
    database_path=settings.database_path,
    video_library_path=settings.video_inbox_path,
    visual_provider=visual_semantic_provider,
)
montage_repo = MontageRepository(settings.database_path)
montage_planner = MontagePlanner(
    semantic_provider=visual_semantic_provider,
    visual_feature_loader=production_repo.get_visual_feature,
)
montage_renderer = MontageRenderer(settings.video_output_path)
narration_generator = NarrationGenerator(settings.video_output_path, voice_clone_provider)
voice_preview_service = VoicePreviewService(settings.video_output_path, voice_clone_provider)
avatar_preview_service = AvatarPreviewService(
    settings.video_output_path,
    UnavailableAvatarAndLipSyncProvider(),
)
vault = ObsidianVault(settings.obsidian_vault_path)
video_inbox = VideoInbox(settings.video_inbox_path)
video_processor = FfmpegVideoProcessor(settings)
video_pipeline = video_processing_pipeline_from_settings(settings, processor=video_processor)
draft_workspace = DraftWorkspace(settings)
runtime_env_path = runtime_env_file_path()
service = EpisodeService(
    script_generator=script_generator_from_settings(settings),
    topic_generator=topic_generator_from_settings(settings, vault),
)
workflow = LangGraphEpisodeWorkflow(service)
video_job_runner = VideoJobRunner()
batch_job_runner = VideoJobRunner()
edit_plan_apply_runner = None
source_tracker = SourceTracker(video_inbox)
auth_context = SupabaseAuthContext(settings)

_EXTERNAL_URLS_CONFIGURATION_REQUIRED = "configuration_required"

# Endpoints that are true pre-login / local-readiness probes and therefore
# stay public even when Supabase auth is enabled. Everything else requires a
# verified bearer token.
PUBLIC_AUTH_PATHS: set[str] = {
    "/api/runtime/health",
    "/api/setup/preflight",
    "/api/is_setup",
    "/api/system/status",
    "/api/version",
    "/api/workflow/status",
    "/api/content-templates",
}

# Authenticated users who have not completed onboarding may only use these
# repair endpoints.  The same trusted Python boundary enforces this rule for
# direct localhost requests, not just Next.js-proxied browser traffic.
ONBOARDING_REPAIR_PATHS: set[str] = {
    "/api/onboarding/complete",
    "/api/settings/runtime",
    "/api/models",
}


def is_onboarding_repair_path(path: str) -> bool:
    return (
        path in ONBOARDING_REPAIR_PATHS
        or path.startswith(("/api/models/", "/api/model-install-jobs/"))
    )


def current_workspace_id(request: Request) -> str:
    """Resolve the workspace id scoping the current request.

    With Supabase auth enabled the middleware always attaches a verified
    principal, so this fails loudly (via active_workspace_id) instead of
    silently scoping an authenticated request to the reserved legacy
    workspace. With auth disabled it falls back to the reserved local
    workspace so the local-first path keeps working unchanged.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.workspace_id:
        return principal.workspace_id
    return active_workspace_id()


def production_profile_with_model_status(workspace_id: str) -> ProductionProfile:
    profile = production_repo.get_profile(workspace_id)
    status = model_catalog.status(QWEN3_TTS_06B_BASE_8BIT)
    model_status = "ready" if status.ready else (
        "failed" if status.state == "invalid" else "blocked"
    )
    return replace(
        profile,
        voice_model_status=model_status,
        voice_model_message=status.message,
    )


@app.get("/api/models", response_model=ModelCatalogResponse)
def list_local_models() -> ModelCatalogResponse:
    return ModelCatalogResponse(
        models=[status.as_dict() for status in model_catalog.list_statuses()]
    )


@app.post(
    "/api/models/{model_id}/install",
    response_model=ModelInstallJobResponse,
    status_code=202,
)
def install_local_model(
    request: Request,
    model_id: str,
    payload: ModelInstallRequest,
) -> ModelInstallJobResponse:
    client_host = request.client.host if request.client else None
    if not is_local_client_host(client_host):
        raise HTTPException(
            status_code=403,
            detail="local models can only be installed from a local client",
        )
    try:
        job = model_installer.start(
            model_id,
            license_accepted=payload.license_accepted,
        )
    except ModelInstallError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelInstallJobResponse.model_validate(job)


@app.get(
    "/api/model-install-jobs/{job_id}",
    response_model=ModelInstallJobResponse,
)
def get_model_install_job(job_id: str) -> ModelInstallJobResponse:
    return ModelInstallJobResponse.model_validate(model_installer.get_job(job_id))


@app.middleware("http")
async def auth_boundary_middleware(request: Request, call_next):
    """Enforce the bearer-token trust boundary for product-data endpoints.

    Public readiness endpoints are exempt. When auth is enabled, a missing or
    invalid token yields 401 and a valid token without an authorized workspace
    yields 403. The resolved principal is attached to request.state and the
    workspace context is set for the duration of the request so repository
    queries stay inside the caller's partition.
    """
    path = request.url.path
    if (
        path in PUBLIC_AUTH_PATHS
        or request.method == "OPTIONS"
        or not auth_context.enabled
    ):
        return await call_next(request)
    try:
        request.state.principal = auth_context.authenticate(request)
        principal = request.state.principal
        if (
            not is_onboarding_repair_path(path)
            and not auth_context.is_onboarding_complete(
                principal.user_id,
                principal.workspace_id or LEGACY_WORKSPACE_ID,
                principal.access_token or "",
            )
        ):
            raise ForbiddenError("onboarding completion required")
    except UnauthorizedError as exc:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "detail": str(exc)},
        )
    except ForbiddenError as exc:
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "detail": str(exc)},
        )
    workspace_id = request.state.principal.workspace_id or LEGACY_WORKSPACE_ID
    token = set_workspace_context(workspace_id)
    try:
        return await call_next(request)
    finally:
        reset_workspace_context(token)


def get_episode_or_404(episode_id: str) -> Episode:
    try:
        return repo.get(episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc


def episode_response(episode: Episode) -> EpisodeResponse:
    return EpisodeResponse.model_validate(
        recover_interrupted_video_job_if_needed(episode),
        from_attributes=True,
    )


def recover_interrupted_video_job_if_needed(episode: Episode) -> Episode:
    active_statuses = {VideoJobStatus.PROCESSING, VideoJobStatus.DRAFTING}
    job_status = _video_job_status(episode.video_job_status)
    if (
        episode.status == EpisodeStatus.VIDEO_PROCESSING
        and job_status in active_statuses
        and not video_job_runner.is_active(episode.id)
    ):
        episode.status = EpisodeStatus.FAILED
        episode.video_job_status = VideoJobStatus.INTERRUPTED
        episode.video_job_message = "后台任务已中断"
        episode.video_job_error = (
            "API restarted or the worker stopped before the video job finished."
        )
        episode.video_job_finished_at = utc_now()
        return repo.save(episode)
    return episode


def _video_job_status(value: object) -> VideoJobStatus:
    try:
        return value if isinstance(value, VideoJobStatus) else VideoJobStatus(str(value))
    except ValueError:
        return VideoJobStatus.IDLE


def feedback_history_for(episode_id: str) -> list[FeedbackEvent]:
    return [
        event
        for episode in repo.all()
        if episode.id != episode_id
        for event in episode.feedback_events
        if event.active
    ][-30:]


def _script_package_from_response(script) -> ScriptPackage:
    return ScriptPackage(
        id=script.id,
        hook=script.hook,
        sections=[
            ScriptSection(
                id=section.id,
                title=section.title,
                estimated_seconds=section.estimated_seconds,
                content=section.content,
                feedback=section.feedback,
            )
            for section in script.sections
        ],
        title_options=[*script.title_options],
        cover_copy=script.cover_copy,
        publish_copy=script.publish_copy,
        comment_prompt=script.comment_prompt,
    )


@app.get("/api/system/status", response_model=SystemStatusResponse)
def get_system_status() -> SystemStatusResponse:
    return SystemStatusResponse.model_validate(settings.public_status())


@app.get("/api/workflow/status", response_model=WorkflowStatusResponse)
def get_workflow_status() -> WorkflowStatusResponse:
    return WorkflowStatusResponse(
        framework="LangGraph",
        node_names=workflow.node_names(),
        human_review_nodes=workflow.human_review_nodes(),
    )


@app.get("/api/content-templates", response_model=ContentTemplatesResponse)
def get_content_templates() -> ContentTemplatesResponse:
    return ContentTemplatesResponse.model_validate(
        {"templates": list_content_templates()},
        from_attributes=True,
    )


@app.get(
    "/api/production/foundation",
    response_model=ProductionFoundationResponse,
)
def get_production_foundation(request: Request) -> ProductionFoundationResponse:
    workspace_id = current_workspace_id(request)
    profile = production_profile_with_model_status(workspace_id)
    return ProductionFoundationResponse(
        profile=ProductionProfileResponse.model_validate(
            profile,
            from_attributes=True,
        ),
        readiness=production_repo.readiness(workspace_id, profile=profile),
        recent_batches=[
            _production_batch_response(batch)
            for batch in (
                _recover_production_batch_if_needed(item)
                for item in production_repo.list_batches(workspace_id)[:8]
            )
        ],
        recent_montage_plans=[
            _montage_plan_response(plan)
            for plan in montage_repo.recent(workspace_id, limit=8)
        ],
        eligible_episodes=[
            {
                "id": episode.id,
                "title": (
                    episode.script.title_options[0]
                    if episode.script and episode.script.title_options
                    else f"Episode {episode.day_number}"
                ),
                "status": episode.status,
            }
            for episode in repo.all(workspace_id=workspace_id)
            if episode.script is not None
            and episode.status
            in {
                EpisodeStatus.SCRIPT_APPROVED,
                EpisodeStatus.WAITING_VIDEO,
                EpisodeStatus.VIDEO_PROCESSING,
                EpisodeStatus.DRAFT_READY,
                EpisodeStatus.PUBLISHED,
            }
        ][:50],
    )


@app.put(
    "/api/production/profile",
    response_model=ProductionProfileResponse,
)
def update_production_profile(
    request: Request,
    payload: ProductionProfileUpdateRequest,
) -> ProductionProfileResponse:
    workspace_id = current_workspace_id(request)
    current = production_repo.get_profile(workspace_id)
    updates = payload.model_dump()
    legacy_voice_fields = {
        "voice_name": str(updates["voice_name"]).strip(),
        "voice_reference_path": updates["voice_reference_path"],
        "voice_reference_text": str(updates["voice_reference_text"]).strip(),
        "voice_consent_confirmed": bool(updates["voice_consent_confirmed"]),
    }
    current_legacy_voice_fields = {
        "voice_name": current.voice_name,
        "voice_reference_path": current.voice_reference_path,
        "voice_reference_text": current.voice_reference_text,
        "voice_consent_confirmed": current.voice_consent_confirmed,
    }
    submitted_legacy_voice_fields = payload.model_fields_set.intersection(
        legacy_voice_fields
    )
    if any(
        legacy_voice_fields[field] != current_legacy_voice_fields[field]
        for field in submitted_legacy_voice_fields
    ):
        raise HTTPException(
            status_code=410,
            detail={
                "error": "legacy_profile_voice_write_retired",
                "impact": "生产档案中的旧声音字段不会再用于创作任务或新声音登记。",
                "repair_action": "使用 /api/media/voice-profiles 保存带人物、授权和版本的声音档案。",
                "retryable": False,
            },
        )
    duration_min = (
        int(updates["target_duration_min_seconds"])
        if updates["target_duration_min_seconds"] is not None
        else current.target_duration_min_seconds
    )
    duration_max = (
        int(updates["target_duration_max_seconds"])
        if updates["target_duration_max_seconds"] is not None
        else current.target_duration_max_seconds
    )
    if duration_min > duration_max:
        raise HTTPException(status_code=400, detail="目标时长下限不能大于上限")
    voice_runtime_status = model_catalog.status(QWEN3_TTS_06B_BASE_8BIT)
    profile = ProductionProfile(
        workspace_id=workspace_id,
        workspace_name=str(updates["workspace_name"]).strip(),
        brand_name=str(updates["brand_name"]).strip(),
        primary_platform="douyin",
        aspect_ratio="9:16",
        target_duration_min_seconds=duration_min,
        target_duration_max_seconds=duration_max,
        brand_template_name=str(updates["brand_template_name"]).strip(),
        brand_colors=[str(item).strip() for item in updates["brand_colors"] if str(item).strip()],
        subtitle_style=str(updates["subtitle_style"]).strip(),
        intro_text=str(updates["intro_text"]).strip(),
        outro_text=str(updates["outro_text"]).strip(),
        background_music_path=updates["background_music_path"],
        voice_name=current.voice_name,
        voice_reference_path=current.voice_reference_path,
        voice_reference_text=current.voice_reference_text,
        voice_consent_confirmed=current.voice_consent_confirmed,
        # Model readiness is maintained by the local model lifecycle, never by
        # an untrusted profile update from the browser.
        voice_model_status=(
            "ready"
            if voice_runtime_status.ready
            else (
                "failed"
                if voice_runtime_status.state == "invalid"
                else "blocked"
            )
        ),
        voice_model_message=voice_runtime_status.message,
        source_urls=[str(item).strip() for item in updates["source_urls"] if str(item).strip()],
        use_obsidian=bool(updates["use_obsidian"]),
        use_hermes=bool(updates["use_hermes"]),
        factuality_gate_enabled=bool(updates["factuality_gate_enabled"]),
        cloud_enhancement_enabled=bool(updates["cloud_enhancement_enabled"]),
    )
    saved = production_repo.save_profile(profile)
    return ProductionProfileResponse.model_validate(saved, from_attributes=True)


@app.get("/api/production/voices", response_model=VoiceProfilesResponse)
def list_production_voices(request: Request) -> VoiceProfilesResponse:
    """Read historical voice rows only; new writes use versioned media profiles."""
    voices = production_repo.list_voice_profiles(current_workspace_id(request))
    return VoiceProfilesResponse(
        voices=[
            VoiceProfileResponse.model_validate(voice, from_attributes=True)
            for voice in voices
        ],
        total_count=len(voices),
    )


@app.post("/api/production/voices", response_model=VoiceProfileResponse)
def create_production_voice(
    request: Request,
    payload: VoiceProfileUpsertRequest,
) -> VoiceProfileResponse:
    del request, payload
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_voice_profile_write_retired",
            "impact": "旧声音档案不会再作为创作任务的配音事实源。",
            "repair_action": "使用 /api/media/voice-profiles 保存带人物、授权和版本的声音档案。",
            "retryable": False,
        },
    )


@app.put(
    "/api/production/voices/{voice_profile_id}",
    response_model=VoiceProfileResponse,
)
def update_production_voice(
    voice_profile_id: str,
    request: Request,
    payload: VoiceProfileUpsertRequest,
) -> VoiceProfileResponse:
    del voice_profile_id, request, payload
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_voice_profile_write_retired",
            "impact": "旧声音档案不会再作为创作任务的配音事实源。",
            "repair_action": "使用 /api/media/voice-profiles 创建新的声音版本。",
            "retryable": False,
        },
    )


@app.get("/api/production/voices/{voice_profile_id}/media")
def get_production_voice_media(
    voice_profile_id: str,
    request: Request,
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    try:
        voice = production_repo.get_voice_profile(workspace_id, voice_profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="voice profile not found") from exc
    raise _capability_unavailable(
        "voice_reference_mapping",
        message="声音参考仅保存为工作区逻辑引用，当前 API 未配置已授权媒体根映射。",
        impact="不能直接读取或返回声音参考媒体。",
        repair_action="配置工作区已授权媒体根映射后重试。",
    )


def _episode_voice_selection_response(
    workspace_id: str,
    episode_id: str,
) -> EpisodeVoiceSelectionResponse:
    identity = production_repo.selected_episode_versioned_voice_identity(
        workspace_id,
        episode_id,
    )
    if identity is None:
        return EpisodeVoiceSelectionResponse(
            episode_id=episode_id,
            voice_profile_id=None,
            voice_version=None,
            language=None,
            authorization_id=None,
            authorization_version=None,
            pronunciation_entry_ids=[],
            source="none",
        )
    inputs = production_repo.resolve_episode_narration_inputs(workspace_id, episode_id)
    voice = inputs.voice
    return EpisodeVoiceSelectionResponse(
        episode_id=episode_id,
        voice_profile_id=voice.id,
        voice_version=voice.version,
        language=voice.language,
        authorization_id=voice.authorization.id,
        authorization_version=voice.authorization.version,
        pronunciation_entry_ids=[entry.id for entry in inputs.pronunciation_entries],
        source="episode",
    )


@app.get(
    "/api/production/episodes/{episode_id}/voice",
    response_model=EpisodeVoiceSelectionResponse,
)
def get_episode_voice_selection(
    episode_id: str,
    request: Request,
) -> EpisodeVoiceSelectionResponse:
    workspace_id = current_workspace_id(request)
    try:
        repo.get(episode_id, workspace_id=workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
    try:
        return _episode_voice_selection_response(workspace_id, episode_id)
    except MediaAuthorityError as exc:
        raise _media_authority_http_error(exc) from exc


@app.put(
    "/api/production/episodes/{episode_id}/voice",
    response_model=EpisodeVoiceSelectionResponse,
)
def update_episode_voice_selection(
    episode_id: str,
    request: Request,
    payload: EpisodeVoiceSelectionRequest,
) -> EpisodeVoiceSelectionResponse:
    workspace_id = current_workspace_id(request)
    try:
        repo.get(episode_id, workspace_id=workspace_id)
        production_repo.select_episode_versioned_voice(
            workspace_id,
            episode_id,
            payload.voice_profile_id,
            payload.voice_version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode or versioned voice profile not found") from exc
    except MediaAuthorityError as exc:
        raise _media_authority_http_error(exc) from exc
    try:
        return _episode_voice_selection_response(workspace_id, episode_id)
    except MediaAuthorityError as exc:
        raise _media_authority_http_error(exc) from exc


def _required_media_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized


def _media_text_values(values: list[str], field: str) -> tuple[str, ...]:
    normalized = tuple(_required_media_text(value, field) for value in values)
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _optional_media_text_values(values: list[str], field: str) -> tuple[str, ...]:
    return tuple(_required_media_text(value, field) for value in values)


def _authorization_from_request(
    workspace_id: str,
    payload: AuthorizationUpsertRequest,
) -> Authorization:
    return Authorization(
        id=_required_media_text(payload.id, "authorization id"),
        workspace_id=workspace_id,
        version=_required_media_text(payload.version, "authorization version"),
        rights_holder=_required_media_text(payload.rights_holder, "rights holder"),
        content_types=_media_text_values(payload.content_types, "content types"),
        usage_scopes=_media_text_values(payload.usage_scopes, "usage scopes"),
        effective_from=payload.effective_from,
        expires_at=payload.expires_at,
        evidence_reference=_required_media_text(
            payload.evidence_reference, "evidence reference"
        ),
        status=payload.status,
    )


def _media_write_error(exc: ValueError) -> HTTPException:
    status_code = 409 if "语义变更必须使用新版本" in str(exc) else 422
    return HTTPException(status_code=status_code, detail=str(exc))


def _media_authority_http_error(exc: MediaAuthorityError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": exc.reason,
            "impact": exc.impact,
            "repair_action": exc.repair_action,
            "retryable": False,
        },
    )


def _voice_clone_http_error(exc: VoiceCloneError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": exc.failure.reason,
            "impact": exc.failure.impact,
            "repair_action": exc.failure.repair_action,
            "retryable": exc.failure.retryable,
        },
    )


def _authorization_response(authorization: Authorization) -> AuthorizationResponse:
    return AuthorizationResponse.model_validate(authorization, from_attributes=True)


def _current_authorization(workspace_id: str, authorization: Authorization) -> Authorization:
    return production_repo.get_authorization(workspace_id, authorization.id)


def _person_profile_response(person: PersonProfile) -> PersonProfileResponse:
    current_authorization = _current_authorization(person.workspace_id, person.authorization)
    reason = current_authorization.failure_for(
        content_type="person_likeness",
        usage_scope="avatar_lipsync",
    )
    return PersonProfileResponse(
        id=person.id,
        workspace_id=person.workspace_id,
        display_name=person.display_name,
        version=person.version,
        avatar_reference=person.avatar_reference,
        background_capabilities=list(person.background_capabilities),
        shot_capabilities=list(person.shot_capabilities),
        authorization=_authorization_response(person.authorization),
        current_authorization=_authorization_response(current_authorization),
        runnable=reason is None,
        runnable_reason=reason,
    )


def _versioned_voice_profile_response(
    voice: VersionedVoiceProfile,
) -> VersionedVoiceProfileResponse:
    current_authorization = _current_authorization(voice.workspace_id, voice.authorization)
    reason = current_authorization.failure_for(
        content_type="voice",
        usage_scope="narration",
    )
    return VersionedVoiceProfileResponse(
        id=voice.id,
        workspace_id=voice.workspace_id,
        person_profile_id=voice.person_profile_id,
        person_version=voice.person_version,
        version=voice.version,
        language=voice.language,
        reference_audio_path=voice.reference_audio_path,
        reference_text=voice.reference_text,
        model_id=voice.model_id,
        authorization=_authorization_response(voice.authorization),
        current_authorization=_authorization_response(current_authorization),
        runnable=reason is None,
        runnable_reason=reason,
    )


def _pronunciation_entry_response(entry: PronunciationEntry) -> PronunciationEntryResponse:
    return PronunciationEntryResponse.model_validate(entry, from_attributes=True)


@app.get("/api/media/authorizations", response_model=AuthorizationsResponse)
def list_media_authorizations(request: Request) -> AuthorizationsResponse:
    authorizations = production_repo.list_authorizations(current_workspace_id(request))
    return AuthorizationsResponse(
        items=[_authorization_response(item) for item in authorizations],
        total_count=len(authorizations),
    )


@app.post("/api/media/authorizations", response_model=AuthorizationResponse)
def save_media_authorization(
    request: Request,
    payload: AuthorizationUpsertRequest,
) -> AuthorizationResponse:
    try:
        saved = production_repo.save_authorization(
            _authorization_from_request(current_workspace_id(request), payload)
        )
    except ValueError as exc:
        raise _media_write_error(exc) from exc
    return _authorization_response(saved)


@app.get("/api/media/authorizations/{authorization_id}", response_model=AuthorizationResponse)
def get_media_authorization(
    authorization_id: str,
    request: Request,
    version: str | None = None,
) -> AuthorizationResponse:
    try:
        authorization = production_repo.get_authorization(
            current_workspace_id(request), authorization_id, version
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="authorization not found") from exc
    return _authorization_response(authorization)


@app.get(
    "/api/media/authorizations/{authorization_id}/versions",
    response_model=AuthorizationsResponse,
)
def list_media_authorization_versions(
    authorization_id: str,
    request: Request,
) -> AuthorizationsResponse:
    workspace_id = current_workspace_id(request)
    try:
        production_repo.get_authorization(workspace_id, authorization_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="authorization not found") from exc
    items = production_repo.list_authorization_versions(workspace_id, authorization_id)
    return AuthorizationsResponse(
        items=[_authorization_response(item) for item in items],
        total_count=len(items),
    )


@app.get("/api/media/person-profiles", response_model=PersonProfilesResponse)
def list_media_person_profiles(request: Request) -> PersonProfilesResponse:
    people = production_repo.list_person_profiles(current_workspace_id(request))
    return PersonProfilesResponse(
        items=[_person_profile_response(item) for item in people],
        total_count=len(people),
    )


@app.post("/api/media/person-profiles", response_model=PersonProfileResponse)
def save_media_person_profile(
    request: Request,
    payload: PersonProfileUpsertRequest,
) -> PersonProfileResponse:
    workspace_id = current_workspace_id(request)
    try:
        saved = production_repo.save_person_profile(
            PersonProfile(
                id=_required_media_text(payload.id, "person id"),
                workspace_id=workspace_id,
                display_name=_required_media_text(payload.display_name, "display name"),
                version=_required_media_text(payload.version, "person version"),
                avatar_reference=validate_logical_media_reference(
                    payload.avatar_reference,
                    field="avatar reference",
                    allowed_schemes={"asset", "workspace"},
                ),
                background_capabilities=_optional_media_text_values(
                    payload.background_capabilities, "background capabilities"
                ),
                shot_capabilities=_optional_media_text_values(
                    payload.shot_capabilities, "shot capabilities"
                ),
                authorization=_authorization_from_request(workspace_id, payload.authorization),
            )
        )
    except ValueError as exc:
        raise _media_write_error(exc) from exc
    return _person_profile_response(saved)


@app.get(
    "/api/media/person-profiles/{person_id}",
    response_model=PersonProfileResponse,
)
def get_media_person_profile(
    person_id: str,
    request: Request,
    version: str | None = None,
) -> PersonProfileResponse:
    try:
        person = production_repo.get_person_profile(
            current_workspace_id(request), person_id, version
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="person profile not found") from exc
    return _person_profile_response(person)


@app.get(
    "/api/media/person-profiles/{person_id}/versions",
    response_model=PersonProfilesResponse,
)
def list_media_person_profile_versions(
    person_id: str,
    request: Request,
) -> PersonProfilesResponse:
    workspace_id = current_workspace_id(request)
    try:
        production_repo.get_person_profile(workspace_id, person_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="person profile not found") from exc
    items = production_repo.list_person_profile_versions(workspace_id, person_id)
    return PersonProfilesResponse(
        items=[_person_profile_response(item) for item in items],
        total_count=len(items),
    )


@app.get("/api/media/voice-profiles", response_model=VersionedVoiceProfilesResponse)
def list_media_voice_profiles(request: Request) -> VersionedVoiceProfilesResponse:
    voices = production_repo.list_versioned_voice_profiles(current_workspace_id(request))
    return VersionedVoiceProfilesResponse(
        items=[_versioned_voice_profile_response(item) for item in voices],
        total_count=len(voices),
    )


@app.post("/api/media/voice-profiles", response_model=VersionedVoiceProfileResponse)
def save_media_voice_profile(
    request: Request,
    payload: VersionedVoiceProfileUpsertRequest,
) -> VersionedVoiceProfileResponse:
    workspace_id = current_workspace_id(request)
    try:
        saved = production_repo.save_versioned_voice_profile(
            VersionedVoiceProfile(
                id=_required_media_text(payload.id, "voice profile id"),
                workspace_id=workspace_id,
                person_profile_id=_required_media_text(
                    payload.person_profile_id, "person profile id"
                ),
                person_version=_required_media_text(
                    payload.person_version, "person version"
                ),
                version=_required_media_text(payload.version, "voice version"),
                language=payload.language,
                reference_audio_path=validate_logical_media_reference(
                    payload.reference_audio_path,
                    field="reference audio path",
                    allowed_schemes={"workspace"},
                ),
                reference_text=_required_media_text(
                    payload.reference_text, "reference text"
                ),
                model_id=_required_media_text(payload.model_id, "model id"),
                authorization=_authorization_from_request(workspace_id, payload.authorization),
            )
        )
    except (KeyError, ValueError) as exc:
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail="person profile not found") from exc
        raise _media_write_error(exc) from exc
    return _versioned_voice_profile_response(saved)


@app.get(
    "/api/media/voice-profiles/{voice_profile_id}",
    response_model=VersionedVoiceProfileResponse,
)
def get_media_voice_profile(
    voice_profile_id: str,
    request: Request,
    version: str | None = None,
) -> VersionedVoiceProfileResponse:
    try:
        voice = production_repo.get_versioned_voice_profile(
            current_workspace_id(request), voice_profile_id, version
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="voice profile not found") from exc
    return _versioned_voice_profile_response(voice)


@app.get(
    "/api/media/voice-profiles/{voice_profile_id}/versions",
    response_model=VersionedVoiceProfilesResponse,
)
def list_media_voice_profile_versions(
    voice_profile_id: str,
    request: Request,
) -> VersionedVoiceProfilesResponse:
    workspace_id = current_workspace_id(request)
    try:
        production_repo.get_versioned_voice_profile(workspace_id, voice_profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="voice profile not found") from exc
    items = production_repo.list_versioned_voice_profile_versions(
        workspace_id, voice_profile_id
    )
    return VersionedVoiceProfilesResponse(
        items=[_versioned_voice_profile_response(item) for item in items],
        total_count=len(items),
    )


@app.get(
    "/api/media/pronunciation-entries",
    response_model=PronunciationEntriesResponse,
)
def list_media_pronunciation_entries(request: Request) -> PronunciationEntriesResponse:
    entries = production_repo.list_pronunciation_entries(current_workspace_id(request))
    return PronunciationEntriesResponse(
        items=[_pronunciation_entry_response(item) for item in entries],
        total_count=len(entries),
    )


@app.post(
    "/api/media/pronunciation-entries",
    response_model=PronunciationEntryResponse,
)
def save_media_pronunciation_entry(
    request: Request,
    payload: PronunciationEntryUpsertRequest,
) -> PronunciationEntryResponse:
    workspace_id = current_workspace_id(request)
    try:
        scope_reference = (
            _required_media_text(payload.scope_reference, "scope reference")
            if payload.scope_reference is not None
            else None
        )
        saved = production_repo.save_pronunciation_entry(
            PronunciationEntry(
                id=_required_media_text(payload.id, "pronunciation entry id"),
                workspace_id=workspace_id,
                term=_required_media_text(payload.term, "term"),
                language=payload.language,
                target_pronunciation=_required_media_text(
                    payload.target_pronunciation, "target pronunciation"
                ),
                scope=payload.scope,
                scope_reference=scope_reference,
                version=_required_media_text(payload.version, "pronunciation version"),
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="person profile not found") from exc
    except ValueError as exc:
        raise _media_write_error(exc) from exc
    return _pronunciation_entry_response(saved)


@app.get(
    "/api/media/pronunciation-entries/{entry_id}",
    response_model=PronunciationEntryResponse,
)
def get_media_pronunciation_entry(
    entry_id: str,
    request: Request,
    version: str | None = None,
) -> PronunciationEntryResponse:
    try:
        entry = production_repo.get_pronunciation_entry(
            current_workspace_id(request), entry_id, version
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="pronunciation entry not found") from exc
    return _pronunciation_entry_response(entry)


@app.get(
    "/api/media/pronunciation-entries/{entry_id}/versions",
    response_model=PronunciationEntriesResponse,
)
def list_media_pronunciation_entry_versions(
    entry_id: str,
    request: Request,
) -> PronunciationEntriesResponse:
    workspace_id = current_workspace_id(request)
    try:
        production_repo.get_pronunciation_entry(workspace_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="pronunciation entry not found") from exc
    items = production_repo.list_pronunciation_entry_versions(workspace_id, entry_id)
    return PronunciationEntriesResponse(
        items=[_pronunciation_entry_response(item) for item in items],
        total_count=len(items),
    )


def _capability_unavailable(
    capability_id: str,
    *,
    message: str,
    impact: str,
    repair_action: str,
) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "error": "capability_unavailable",
            "capability_id": capability_id,
            "detail": message,
            "impact": impact,
            "repair_action": repair_action,
            "retryable": False,
        },
    )


@app.get(
    "/api/media/capabilities/voice-clone",
    response_model=MediaCapabilityResponse,
)
def get_voice_clone_capability() -> MediaCapabilityResponse:
    if not voice_clone_provider.ready:
        raise _capability_unavailable(
            "voice_clone",
            message=voice_clone_provider.message,
            impact="声音复制未执行，不能产出或返回配音媒体。",
            repair_action="安装并配置本地声音复制运行环境后重新查询。",
        )
    return MediaCapabilityResponse(
        capability_id="voice_clone",
        available=True,
        model_id=voice_clone_provider.model_id,
        message=voice_clone_provider.message,
        impact=None,
        repair_action=None,
    )


@app.get(
    "/api/media/capabilities/avatar-lipsync",
    response_model=MediaCapabilityResponse,
)
def get_avatar_lipsync_capability() -> MediaCapabilityResponse:
    raise _capability_unavailable(
        "avatar_lipsync",
        message="人物口型能力尚未配置。",
        impact="人物与口型步骤未完成，不能生成或返回人物视频。",
        repair_action="配置明确的本地人物口型运行环境后重新查询。",
    )


def _preview_output_path(run) -> Path | None:
    if run.status != "succeeded" or not run.output_path:
        return None
    root = Path(settings.video_output_path).expanduser().resolve()
    path = Path(run.output_path).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return path


def _media_preview_response(run) -> MediaPreviewRunResponse:
    output_url = (
        f"/api/media/previews/{run.preview_type}/{run.identity}/media"
        if _preview_output_path(run) is not None
        else None
    )
    return MediaPreviewRunResponse(
        id=run.id,
        workspace_id=run.workspace_id,
        preview_type=run.preview_type,
        identity=run.identity,
        attempt=run.attempt,
        status=run.status,
        capability_id=run.capability_id,
        model_id=run.model_id,
        language=run.language,
        person_profile_id=run.person_profile_id,
        person_version=run.person_version,
        voice_profile_id=run.voice_profile_id,
        voice_version=run.voice_version,
        authorization_id=run.authorization_id,
        authorization_version=run.authorization_version,
        output_url=output_url,
        reason=run.reason,
        impact=run.impact,
        repair_action=run.repair_action,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _preview_precondition_error(
    *,
    error: str,
    detail: str,
    impact: str,
    repair_action: str,
    status_code: int = 409,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": error,
            "detail": detail,
            "impact": impact,
            "repair_action": repair_action,
            "retryable": False,
        },
    )


@app.post("/api/media/previews/voice", response_model=MediaPreviewRunResponse)
def request_voice_preview(
    request: Request,
    payload: VoicePreviewRequest,
) -> MediaPreviewRunResponse:
    try:
        run = voice_preview_service.request(
            repository=production_repo,
            workspace_id=current_workspace_id(request),
            voice_profile_id=payload.voice_profile_id,
            voice_version=payload.voice_version,
            preview_text=payload.preview_text,
            retry=payload.retry,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="voice profile not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _media_preview_response(run)


@app.post(
    "/api/media/previews/avatar-lipsync",
    response_model=MediaPreviewRunResponse,
)
def request_avatar_lipsync_preview(
    request: Request,
    payload: AvatarLipSyncPreviewRequest,
) -> MediaPreviewRunResponse:
    workspace_id = current_workspace_id(request)
    voice_run = production_repo.get_media_preview_run(
        workspace_id,
        "voice",
        payload.voice_preview_identity,
    )
    if voice_run is None:
        raise HTTPException(status_code=404, detail="voice preview not found")
    audio_path = _preview_output_path(voice_run)
    if voice_run.status != "succeeded" or audio_path is None:
        raise _preview_precondition_error(
            error="voice_preview_not_succeeded",
            detail="avatar preview requires a succeeded voice preview with a safe output.",
            impact="人物口型预览未开始，不能生成或返回人物视频。",
            repair_action="完成当前工作区的声音预览后重试。",
        )
    try:
        run = avatar_preview_service.request(
            repository=production_repo,
            workspace_id=workspace_id,
            person_id=payload.person_profile_id,
            person_version=payload.person_version,
            voice_profile_id=payload.voice_profile_id,
            voice_version=payload.voice_version,
            audio_path=str(audio_path),
            audio_cache_key=voice_run.identity,
            language=voice_run.language,
            motion=payload.motion,
            expression=payload.expression,
            authorization_id=payload.authorization_id,
            authorization_version=payload.authorization_version,
            model_id=payload.model_id,
            retry=payload.retry,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="media preview dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _media_preview_response(run)


@app.get("/api/media/previews", response_model=MediaPreviewRunsResponse)
def list_media_previews(
    request: Request,
    preview_type: str | None = None,
) -> MediaPreviewRunsResponse:
    if preview_type not in {None, "voice", "avatar_lipsync"}:
        raise HTTPException(status_code=422, detail="invalid preview type")
    workspace_id = current_workspace_id(request)
    latest_by_identity = {}
    for run in production_repo.list_media_preview_runs(workspace_id, preview_type):
        current = latest_by_identity.get(run.identity)
        if current is None or run.attempt > current.attempt:
            latest_by_identity[run.identity] = run
    runs = sorted(
        latest_by_identity.values(),
        key=lambda run: (run.finished_at or run.started_at, run.attempt),
        reverse=True,
    )
    return MediaPreviewRunsResponse(
        items=[_media_preview_response(run) for run in runs],
        total_count=len(runs),
    )


@app.get(
    "/api/media/previews/{preview_type}/{identity}/attempts",
    response_model=MediaPreviewRunsResponse,
)
def list_media_preview_attempts(
    preview_type: str,
    identity: str,
    request: Request,
) -> MediaPreviewRunsResponse:
    if preview_type not in {"voice", "avatar_lipsync"}:
        raise HTTPException(status_code=422, detail="invalid preview type")
    workspace_id = current_workspace_id(request)
    if production_repo.get_media_preview_run(workspace_id, preview_type, identity) is None:
        raise HTTPException(status_code=404, detail="media preview not found")
    runs = production_repo.list_media_preview_attempts(workspace_id, preview_type, identity)
    return MediaPreviewRunsResponse(
        items=[_media_preview_response(run) for run in runs],
        total_count=len(runs),
    )


@app.get("/api/media/previews/{preview_type}/{identity}/media")
def stream_media_preview_output(
    preview_type: str,
    identity: str,
    request: Request,
) -> FileResponse:
    if preview_type not in {"voice", "avatar_lipsync"}:
        raise HTTPException(status_code=422, detail="invalid preview type")
    run = production_repo.get_media_preview_run(
        current_workspace_id(request), preview_type, identity
    )
    if run is None:
        raise HTTPException(status_code=404, detail="media preview not found")
    output_path = _preview_output_path(run)
    if output_path is None:
        raise HTTPException(status_code=404, detail="media preview output not found")
    media_type = "audio/wav" if output_path.suffix.lower() == ".wav" else "video/mp4"
    return FileResponse(output_path, media_type=media_type, filename=output_path.name)


@app.get(
    "/api/production/material-collections",
    response_model=MaterialCollectionsResponse,
)
def list_material_collections(request: Request) -> MaterialCollectionsResponse:
    collections = production_repo.list_collections(current_workspace_id(request))
    return MaterialCollectionsResponse(
        collections=[
            MaterialCollectionResponse.model_validate(item, from_attributes=True)
            for item in collections
        ],
        total_count=len(collections),
    )


@app.post(
    "/api/production/material-collections",
    response_model=MaterialCollectionResponse,
)
def create_material_collection(
    request: Request,
    payload: MaterialCollectionCreateRequest,
) -> MaterialCollectionResponse:
    workspace_id = current_workspace_id(request)
    collection = MaterialCollection(
        id=f"collection_{uuid4().hex[:10]}",
        workspace_id=workspace_id,
        name=payload.name.strip(),
        parent_id=payload.parent_id,
    )
    try:
        saved = production_repo.save_collection(collection)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="parent collection not found") from exc
    return MaterialCollectionResponse.model_validate(saved, from_attributes=True)


def _material_workspace_reference(asset) -> str | None:
    root = Path(settings.video_inbox_path).expanduser().resolve()
    try:
        relative_path = Path(asset.path).expanduser().resolve().relative_to(root)
    except ValueError:
        return None
    if not relative_path.parts:
        return None
    return f"workspace://{relative_path.as_posix()}"


def _material_asset_response(asset) -> MaterialAssetResponse:
    response = MaterialAssetResponse.model_validate(asset, from_attributes=True)
    return response.model_copy(
        update={"workspace_reference": _material_workspace_reference(asset)}
    )


def _safe_montage_media_path(plan, field: str) -> Path | None:
    if plan.status != "rendered":
        return None
    stored_path = getattr(plan, field)
    if not stored_path:
        return None
    root = Path(settings.video_output_path).expanduser().resolve()
    path = Path(stored_path).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return path


def _montage_narration_status(workspace_id: str, narration_reference: str | None) -> str:
    if not narration_reference:
        return "unavailable"
    try:
        path = production_repo.resolve_authorized_narration_audio_reference(
            workspace_id,
            narration_reference,
        )
    except MediaAuthorityError:
        return "unavailable"
    return "ready" if path.is_file() and path.stat().st_size > 0 else "unavailable"


def _montage_clip_response(workspace_id: str, clip) -> MontageClipResponse:
    response = MontageClipResponse.model_validate(clip, from_attributes=True)
    try:
        source_reference = production_repo.workspace_media_reference_for_asset(
            workspace_id,
            clip.asset_id,
        )
    except (KeyError, MediaAuthorityError):
        source_reference = None
    return response.model_copy(update={"source_path": source_reference})


def _montage_plan_response(plan) -> MontagePlanResponse:
    output_path = _safe_montage_media_path(plan, "output_path")
    subtitle_path = _safe_montage_media_path(plan, "subtitle_path")
    subtitle_status = (
        "ready"
        if subtitle_path is not None
        else "not_generated"
        if not plan.subtitle_path
        else "unavailable"
    )
    return MontagePlanResponse.model_validate(
        {
            "id": plan.id,
            "workspace_id": plan.workspace_id,
            "batch_id": plan.batch_id,
            "episode_id": plan.episode_id,
            "script_title": plan.script_title,
            "status": plan.status,
            "cues": [
                NarrationCueResponse.model_validate(cue, from_attributes=True)
                for cue in plan.cues
            ],
            "clips": [
                _montage_clip_response(plan.workspace_id, clip)
                for clip in plan.clips
            ],
            "duration_seconds": plan.duration_seconds,
            "low_confidence_clip_count": plan.low_confidence_clip_count,
            "aspect_ratio": "9:16",
            "narration_status": _montage_narration_status(
                plan.workspace_id, plan.narration_audio_path
            ),
            "subtitle_status": subtitle_status,
            "output_url": (
                f"/api/production/montage-plans/{plan.id}/output"
                if output_path is not None
                else None
            ),
            "subtitle_url": (
                f"/api/production/montage-plans/{plan.id}/subtitle"
                if subtitle_path is not None
                else None
            ),
            "message": plan.message,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }
    )


def _production_batch_response(batch: ProductionBatch) -> ProductionBatchResponse:
    items = []
    for item in batch.items:
        plan = None
        if item.plan_id:
            try:
                plan = montage_repo.get(batch.workspace_id, item.plan_id)
            except KeyError:
                pass
        output_path = _safe_montage_media_path(plan, "output_path") if plan else None
        subtitle_path = _safe_montage_media_path(plan, "subtitle_path") if plan else None
        items.append(
            {
                "episode_id": item.episode_id,
                "status": item.status,
                "plan_id": item.plan_id,
                "aspect_ratio": "9:16",
                "narration_status": _montage_narration_status(
                    batch.workspace_id,
                    plan.narration_audio_path if plan else None,
                ),
                "subtitle_status": (
                    "ready"
                    if subtitle_path is not None
                    else "not_generated"
                    if plan is None or not plan.subtitle_path
                    else "unavailable"
                ),
                "output_url": (
                    f"/api/production/montage-plans/{plan.id}/output"
                    if output_path is not None
                    else None
                ),
                "subtitle_url": (
                    f"/api/production/montage-plans/{plan.id}/subtitle"
                    if subtitle_path is not None
                    else None
                ),
                "message": item.message,
                "attempts": item.attempts,
                "updated_at": item.updated_at,
            }
        )
    return ProductionBatchResponse.model_validate(
        {
            "id": batch.id,
            "workspace_id": batch.workspace_id,
            "name": batch.name,
            "target_count": batch.target_count,
            "status": batch.status,
            "episode_ids": batch.episode_ids,
            "items": items,
            "blocker_codes": batch.blocker_codes,
            "message": batch.message,
            "rendered_count": batch.rendered_count,
            "waiting_review_count": batch.waiting_review_count,
            "failed_count": batch.failed_count,
            "started_at": batch.started_at,
            "finished_at": batch.finished_at,
            "created_at": batch.created_at,
            "updated_at": batch.updated_at,
        }
    )


def _source_item_identity(item: SourceItem) -> str:
    """Return a non-secret identity for the exact SourceItem snapshot in a Plan."""
    return hashlib.sha256(
        json.dumps(
            {
                "id": item.id,
                "connector_id": item.connector_id,
                "source_label": item.source_label,
                "source_url": item.source_url,
                "content_type": item.content_type,
                "author": item.author,
                "published_at": item.published_at.isoformat()
                if item.published_at is not None
                else None,
                "body": item.body,
                "subtitles": item.subtitles,
                "media_references": list(item.media_references),
                "license": item.license,
                "title": item.title,
                "file_reference": item.file_reference,
                "created_at": item.created_at.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _creation_plan_preflight(
    workspace_id: str,
    payload: CreationPlanPreviewRequest | CreationPlanConfirmRequest,
) -> CreationPlan:
    knowledge_references = [
        CreationPlanKnowledgeReference(
            id=item.id,
            version=item.version,
            title=item.title,
            authorization=item.authorization,
            ownership=item.ownership,
        )
        for item in source_repository.list_knowledge_items(workspace_id)
    ]
    source_references = [
        CreationPlanSourceReference(
            id=item.id,
            connector_id=item.connector_id,
            source_label=item.source_label,
            title=item.title,
            license=item.license,
            identity=_source_item_identity(item),
        )
        for item in source_repository.list_source_items(workspace_id)
    ]
    approved_assets = [
        asset
        for asset in production_repo.list_assets(workspace_id)
        if asset.review_status == "approved"
        and asset.rights_confirmed
        and asset.brand_safe
        and asset.usage_role in {"a_roll", "b_roll"}
    ]
    approved_clips = [
        segment
        for asset in approved_assets
        for segment in asset.segments
        if segment.review_status == "approved"
        and segment.usage_role == "b_roll"
    ]
    language_to_voice_language = {
        "zh-CN": "zh",
        "en-US": "en",
        "zh-CN/en-US": "zh-en",
    }
    requested_voice_language = language_to_voice_language[payload.language]
    active_voices = []
    for voice in production_repo.list_versioned_voice_profiles(workspace_id):
        authorization = production_repo.get_authorization(
            workspace_id,
            voice.authorization.id,
        )
        if authorization.failure_for(content_type="voice", usage_scope="narration") is None:
            active_voices.append((voice, authorization))
    selected_voice_pair = next(
        (
            (voice, authorization)
            for voice, authorization in active_voices
            if voice.language in {requested_voice_language, "zh-en"}
        ),
        None,
    )
    selected_voice = None
    pronunciation_references: list[CreationPlanPronunciationReference] = []
    person_authorization_failure: str | None = None
    if selected_voice_pair is not None:
        voice, authorization = selected_voice_pair
        person = production_repo.get_person_profile(
            workspace_id,
            voice.person_profile_id,
            voice.person_version,
        )
        person_authorization = production_repo.get_authorization(
            workspace_id,
            person.authorization.id,
        )
        person_authorization_failure = person_authorization.failure_for(
            content_type="person_likeness",
            usage_scope="avatar_lipsync",
        )
        selected_voice = CreationPlanVoiceReference(
            profile_id=voice.id,
            version=voice.version,
            person_profile_id=voice.person_profile_id,
            person_version=voice.person_version,
            person_authorization_id=person_authorization.id,
            person_authorization_version=person_authorization.version,
            authorization_id=authorization.id,
            authorization_version=authorization.version,
            language=voice.language,
            model_id=voice.model_id,
        )
        pronunciation_references = [
            CreationPlanPronunciationReference(
                id=entry.id,
                version=entry.version,
                scope=entry.scope,
                scope_reference=entry.scope_reference,
                language=entry.language,
            )
            for entry in production_repo.list_applicable_pronunciation_entries(
                workspace_id=workspace_id,
                person_profile_id=voice.person_profile_id,
                episode_id="",
            )
        ]
    model_snapshot = [
        {
            "id": status.model.id,
            "capability": status.model.capability,
            "state": status.state,
            "ready": status.ready,
            "message": status.message,
        }
        for status in model_catalog.list_statuses()
    ]
    model_by_id = {str(item["id"]): item for item in model_snapshot}
    blockers: list[CreationPlanBlocker] = []
    if not knowledge_references and not source_references:
        blockers.append(
            CreationPlanBlocker(
                reason="content_sources_missing",
                impact="没有已保存的知识库内容或信息源条目，不能可靠进入选题阶段。",
                repair_action="先保存用户知识或完成一个可用信息源同步，再重新预检。",
                stage="topic",
                blocks_confirmation=True,
            )
        )
    if selected_voice is None:
        reason = (
            "voice_language_unavailable"
            if active_voices
            else "voice_authorization_missing"
        )
        blockers.append(
            CreationPlanBlocker(
                reason=reason,
                impact="当前方案不能进入需要该语言授权声音的配音阶段。",
                repair_action="登记当前工作区可用语言的授权声音版本，或调整方案语言。",
                stage="narration",
                blocks_confirmation=False,
            )
        )
    else:
        model = model_by_id.get(selected_voice.model_id)
        if model is None or not bool(model["ready"]):
            blockers.append(
                CreationPlanBlocker(
                    reason="voice_model_unavailable",
                    impact="当前方案不能进入配音阶段，但可先创建任务并进行选题。",
                    repair_action="安装或修复所选声音版本要求的模型后，再从配音阶段继续。",
                    stage="narration",
                    blocks_confirmation=False,
                )
            )
        if person_authorization_failure is not None:
            blockers.append(
                CreationPlanBlocker(
                    reason=person_authorization_failure,
                    impact="当前方案不能进入人物口型阶段，但可先创建任务并进行选题。",
                    repair_action="更新人物形象的有效授权后，再从人物与口型阶段继续。",
                    stage="avatar_lipsync",
                    blocks_confirmation=False,
                )
            )
    if not approved_clips:
        blockers.append(
            CreationPlanBlocker(
                reason="approved_material_clips_missing",
                impact="当前方案不能进入自动选片和视频剪辑阶段。",
                repair_action="审核并标记至少一个当前工作区可用的 B-roll 镜头后继续。",
                stage="video_editing",
                blocks_confirmation=False,
            )
        )
    name = payload.name or f"创作任务：{payload.goal[:48]}"
    fingerprint_payload = {
        "goal": payload.goal,
        "name": name,
        "target_count": payload.target_count,
        "language": payload.language,
        "aspect_ratio": payload.aspect_ratio,
        "duration_target_seconds": payload.duration_target_seconds,
        "knowledge_references": [item.__dict__ for item in knowledge_references],
        "source_references": [item.__dict__ for item in source_references],
        "selected_voice": selected_voice.__dict__ if selected_voice else None,
        "pronunciation_references": [
            item.__dict__ for item in pronunciation_references
        ],
        "approved_asset_count": len(approved_assets),
        "approved_clip_count": len(approved_clips),
        "material_rule_summary": "仅计入已审核、已确认授权且品牌安全的 B-roll 镜头。",
        "model_snapshot": model_snapshot,
        "confirmation_points": ["确认资料与来源事实", "确认选题", "审核成片与发布"],
        "blockers": [item.__dict__ for item in blockers],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return CreationPlan(
        id="",
        workspace_id=workspace_id,
        goal=payload.goal,
        name=name,
        target_count=payload.target_count,
        language=payload.language,
        aspect_ratio=payload.aspect_ratio,
        duration_target_seconds=payload.duration_target_seconds,
        knowledge_references=knowledge_references,
        source_references=source_references,
        selected_voice=selected_voice,
        pronunciation_references=pronunciation_references,
        approved_asset_count=len(approved_assets),
        approved_clip_count=len(approved_clips),
        material_rule_summary=str(fingerprint_payload["material_rule_summary"]),
        model_snapshot=model_snapshot,
        confirmation_points=list(fingerprint_payload["confirmation_points"]),
        blockers=blockers,
        preflight_fingerprint=fingerprint,
    )


def _creation_plan_preview_response(plan: CreationPlan) -> CreationPlanPreviewResponse:
    return CreationPlanPreviewResponse.model_validate(
        {
            "goal": plan.goal,
            "name": plan.name,
            "target_count": plan.target_count,
            "language": plan.language,
            "aspect_ratio": plan.aspect_ratio,
            "duration_target_seconds": plan.duration_target_seconds,
            "knowledge_references": [item.__dict__ for item in plan.knowledge_references],
            "source_references": [item.__dict__ for item in plan.source_references],
            "selected_voice": (
                plan.selected_voice.__dict__ if plan.selected_voice is not None else None
            ),
            "pronunciation_references": [
                item.__dict__ for item in plan.pronunciation_references
            ],
            "approved_asset_count": plan.approved_asset_count,
            "approved_clip_count": plan.approved_clip_count,
            "material_rule_summary": plan.material_rule_summary,
            "model_snapshot": plan.model_snapshot,
            "confirmation_points": plan.confirmation_points,
            "blockers": [item.__dict__ for item in plan.blockers],
            "confirmable": not any(
                blocker.blocks_confirmation for blocker in plan.blockers
            ),
            "preflight_fingerprint": plan.preflight_fingerprint,
        }
    )


def _creation_plan_response(
    plan: CreationPlan,
    task: CreationTask,
) -> CreationPlanResponse:
    payload = _creation_plan_preview_response(plan).model_dump()
    payload.update(
        {
            "id": plan.id,
            "workspace_id": plan.workspace_id,
            "status": plan.status,
            "creation_task": task.__dict__,
            "created_at": plan.created_at,
            "confirmed_at": plan.confirmed_at,
            "updated_at": plan.updated_at,
        }
    )
    return CreationPlanResponse.model_validate(payload)


class _CreationTaskTopicPreflightError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> None:
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action
        self.retryable = retryable
        super().__init__(reason)


def _topic_route_snapshot() -> dict[str, object]:
    provider_kind = settings.effective_llm_provider
    base_url = settings.effective_llm_base_url
    parsed = urlparse(base_url)
    host = parsed.hostname or "unconfigured"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    endpoint_identity = (
        f"{parsed.scheme or 'unknown'}://{host}{port}"
        if host != "unconfigured"
        else "unconfigured"
    )
    execution_location = (
        "local"
        if provider_kind == "ollama" or host in {"localhost", "127.0.0.1", "::1"}
        else "cloud"
    )
    availability = "unverified" if settings.llm_configured else "unconfigured"
    connection_version = hashlib.sha256(
        f"{provider_kind}|{endpoint_identity}|{settings.effective_llm_model}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "provider_kind": provider_kind,
        "execution_location": execution_location,
        "model_id": settings.effective_llm_model,
        "endpoint_identity": endpoint_identity,
        "connection_version": connection_version,
        "availability": availability,
        "checked_at": utc_now(),
    }


def _safe_topic_source_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _topic_text_excerpt(value: str | None) -> str:
    return " ".join((value or "").split())[:8_000]


def _creation_task_topic_inputs(
    workspace_id: str,
    task: CreationTask,
    plan: CreationPlan,
    *,
    cloud_egress: bool,
) -> tuple[
    tuple[CreationTaskTopicSource, ...],
    list[dict[str, object]],
    dict[str, dict[str, object]],
]:
    if plan.workspace_id != workspace_id or task.plan_id != plan.id:
        raise _CreationTaskTopicPreflightError(
            reason="creation_plan_relation_invalid",
            impact="无法确认本次选题使用的已确认方案，未调用文字模型。",
            repair_action="重新从当前创作任务进入内容预检并确认方案。",
            retryable=False,
        )
    provider_sources: list[CreationTaskTopicSource] = []
    input_references: list[dict[str, object]] = []
    evidence_by_reference_id: dict[str, dict[str, object]] = {}
    for reference in plan.knowledge_references:
        try:
            item = source_repository.get_knowledge_item(workspace_id, reference.id)
        except KeyError as exc:
            raise _CreationTaskTopicPreflightError(
                reason="knowledge_reference_missing",
                impact="方案中的知识库资料已不可用，未调用文字模型。",
                repair_action="重新执行内容预检，确认仍可用的知识资料后重试。",
                retryable=True,
            ) from exc
        if (
            item.version != reference.version
            or item.ownership != reference.ownership
            or item.authorization != reference.authorization
        ):
            raise _CreationTaskTopicPreflightError(
                reason="knowledge_reference_stale",
                impact="方案中的知识版本已变化，未调用文字模型。",
                repair_action="重新执行内容预检并确认最新知识版本。",
                retryable=True,
            )
        if not item.ownership.strip() or not item.authorization.strip():
            raise _CreationTaskTopicPreflightError(
                reason="knowledge_authorization_invalid",
                impact="知识资料缺少可验证的拥有权或授权，不能发送给文字模型。",
                repair_action="补齐该知识条目的拥有权、授权和版本后重新预检。",
                retryable=True,
            )
        text = _topic_text_excerpt(item.body)
        if not text:
            raise _CreationTaskTopicPreflightError(
                reason="knowledge_text_unavailable",
                impact="知识资料没有可发送的文字摘录，未调用文字模型。",
                repair_action="保存已授权的文字内容后重新预检；本机文件路径不会发送给模型。",
                retryable=True,
            )
        reference_id = f"knowledge:{item.id}:{item.version}"
        provider_sources.append(
            CreationTaskTopicSource(
                reference_id=reference_id,
                kind="knowledge",
                title=item.title,
                text=text,
            )
        )
        input_references.append(
            {
                "reference_id": reference_id,
                "kind": "knowledge",
                "object_id": item.id,
                "version_or_identity": item.version,
                "title": item.title,
            }
        )
        evidence_by_reference_id[reference_id] = {
            "kind": "knowledge",
            "reference_id": item.id,
            "version_or_identity": item.version,
            "title": item.title,
            "authorization_or_license": item.authorization,
            "source_url": None,
        }
    for reference in plan.source_references:
        try:
            item = source_repository.get_source_item(workspace_id, reference.id)
        except KeyError as exc:
            raise _CreationTaskTopicPreflightError(
                reason="source_reference_missing",
                impact="方案中的信息源条目已不可用，未调用文字模型。",
                repair_action="重新同步或重新选择信息源后执行内容预检。",
                retryable=True,
            ) from exc
        if (
            item.connector_id != reference.connector_id
            or item.title != reference.title
            or item.license != reference.license
            or not reference.identity
            or _source_item_identity(item) != reference.identity
        ):
            raise _CreationTaskTopicPreflightError(
                reason="source_reference_stale",
                impact="方案中的信息源身份或许可已变化，未调用文字模型。",
                repair_action="重新执行内容预检并确认最新信息源条目。",
                retryable=True,
            )
        license_value = item.license.strip()
        if cloud_egress and (
            not license_value
            or license_value.casefold() in {"unknown", "unverified"}
        ):
            raise _CreationTaskTopicPreflightError(
                reason="source_license_unverified",
                impact="信息源许可未知或未验证，不能发送到云端文字模型。",
                repair_action="补齐该信息源条目的许可后重新预检。",
                retryable=True,
            )
        text = _topic_text_excerpt(item.body or item.subtitles)
        if not text:
            raise _CreationTaskTopicPreflightError(
                reason="source_text_unavailable",
                impact="信息源没有可发送的文字摘录，未调用文字模型。",
                repair_action="保存该来源的已授权正文或字幕后重新预检。",
                retryable=True,
            )
        reference_id = f"source:{item.id}"
        provider_sources.append(
            CreationTaskTopicSource(
                reference_id=reference_id,
                kind="source",
                title=item.title,
                text=text,
            )
        )
        input_references.append(
            {
                "reference_id": reference_id,
                "kind": "source",
                "object_id": item.id,
                "version_or_identity": _source_item_identity(item),
                "title": item.title,
            }
        )
        evidence_by_reference_id[reference_id] = {
            "kind": "source",
            "reference_id": item.id,
            "version_or_identity": _source_item_identity(item),
            "title": item.title,
            "authorization_or_license": license_value,
            "source_url": _safe_topic_source_url(item.source_url),
        }
    if not provider_sources:
        raise _CreationTaskTopicPreflightError(
            reason="content_sources_missing",
            impact="当前方案没有可发送的已授权文字资料，未调用文字模型。",
            repair_action="保存知识或同步已授权的信息源后重新预检。",
            retryable=True,
        )
    return tuple(provider_sources), input_references, evidence_by_reference_id


def _creation_task_topic_generator_from_settings() -> OpenAICompatibleCreationTaskTopicGenerator:
    return OpenAICompatibleCreationTaskTopicGenerator(
        base_url=settings.effective_llm_base_url,
        model=settings.effective_llm_model,
        api_key=settings.effective_llm_api_key,
    )


def _projected_topic_route_snapshot(
    current_snapshot: dict[str, object],
    latest_run: TopicGenerationRun | None,
) -> dict[str, object]:
    if latest_run is None:
        return current_snapshot
    previous = latest_run.route_snapshot
    if previous.get("connection_version") != current_snapshot["connection_version"]:
        return current_snapshot
    availability = previous.get("availability")
    if availability not in {"succeeded", "failed"}:
        return current_snapshot
    return {
        **current_snapshot,
        "availability": availability,
        "checked_at": previous.get("checked_at", current_snapshot["checked_at"]),
    }


def _creation_task_topic_response(
    workspace_id: str,
    task: CreationTask,
    *,
    route_snapshot: dict[str, object] | None = None,
    preflight_error: _CreationTaskTopicPreflightError | None = None,
    preflight_input_count: int = 0,
) -> CreationTaskTopicsResponse:
    route_snapshot = route_snapshot or _topic_route_snapshot()
    latest_run = production_repo.latest_topic_generation_run(workspace_id, task.id)
    route_snapshot = _projected_topic_route_snapshot(route_snapshot, latest_run)
    candidates = production_repo.list_topic_candidates(workspace_id, task.id)
    evidence_references = production_repo.list_topic_evidence_references(workspace_id, task.id)
    cloud_egress = route_snapshot["execution_location"] == "cloud"
    return CreationTaskTopicsResponse.model_validate(
        {
            "task": task.__dict__,
            "candidates": [
                {
                    "id": candidate.id,
                    "run_id": candidate.run_id,
                    "title": candidate.title,
                    "angle": candidate.angle,
                    "audience": candidate.audience,
                    "outline": candidate.outline,
                    "score": candidate.score,
                    "selection_status": candidate.selection_status,
                    "created_at": candidate.created_at,
                }
                for candidate in candidates
            ],
            "evidence_references": [
                {
                    "id": reference.id,
                    "candidate_id": reference.candidate_id,
                    "kind": reference.kind,
                    "reference_id": reference.reference_id,
                    "version_or_identity": reference.version_or_identity,
                    "title": reference.title,
                    "authorization_or_license": reference.authorization_or_license,
                    "source_url": reference.source_url,
                    "created_at": reference.created_at,
                }
                for reference in evidence_references
            ],
            "selected_topic_id": task.selected_topic_id,
            "latest_run": latest_run.__dict__ if latest_run is not None else None,
            "route_preflight": route_snapshot,
            "egress_preflight": {
                "cloud_egress": cloud_egress,
                "source_count": len(latest_run.input_references)
                if latest_run is not None
                else preflight_input_count,
                "text_excerpt_count": len(latest_run.input_references)
                if latest_run is not None
                else preflight_input_count,
                "reason": preflight_error.reason if preflight_error else None,
                "impact": preflight_error.impact if preflight_error else None,
                "repair_action": preflight_error.repair_action if preflight_error else None,
            },
        }
    )
@app.post(
    "/api/creation-plans/preview",
    response_model=CreationPlanPreviewResponse,
)
def preview_creation_plan(
    request: Request,
    payload: CreationPlanPreviewRequest,
) -> CreationPlanPreviewResponse:
    return _creation_plan_preview_response(
        _creation_plan_preflight(current_workspace_id(request), payload)
    )


@app.post(
    "/api/creation-plans/confirm",
    response_model=CreationPlanResponse,
    status_code=201,
)
def confirm_creation_plan(
    request: Request,
    payload: CreationPlanConfirmRequest,
) -> CreationPlanResponse:
    workspace_id = current_workspace_id(request)
    plan = _creation_plan_preflight(workspace_id, payload)
    if payload.preflight_fingerprint != plan.preflight_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "creation_plan_preflight_stale",
                "impact": "预检期间的资料、授权、素材或模型状态已变化，未创建任务。",
                "repair_action": "重新执行内容预检并确认最新方案。",
                "retryable": True,
            },
        )
    blocking = next((item for item in plan.blockers if item.blocks_confirmation), None)
    if blocking is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": blocking.reason,
                "impact": blocking.impact,
                "repair_action": blocking.repair_action,
                "retryable": True,
            },
        )
    now = utc_now()
    plan.id = f"plan_{uuid4().hex[:12]}"
    plan.created_at = now
    plan.confirmed_at = now
    task = CreationTask(
        id=f"task_{uuid4().hex[:12]}",
        workspace_id=workspace_id,
        plan_id=plan.id,
        name=plan.name,
        scenario=plan.goal,
        created_at=now,
        updated_at=now,
    )
    try:
        saved_plan, saved_task = production_repo.confirm_creation_plan(plan, task)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "creation_plan_confirmation_rejected",
                "impact": "没有创建部分方案或部分创作任务。",
                "repair_action": str(exc),
                "retryable": True,
            },
        ) from exc
    return _creation_plan_response(saved_plan, saved_task)


@app.get(
    "/api/creation-plans/{plan_id}",
    response_model=CreationPlanResponse,
)
def get_creation_plan(plan_id: str, request: Request) -> CreationPlanResponse:
    workspace_id = current_workspace_id(request)
    try:
        plan = production_repo.get_creation_plan(workspace_id, plan_id)
        if plan.creation_task_id is None:
            raise KeyError(plan_id)
        task = production_repo.get_creation_task(workspace_id, plan.creation_task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation plan not found") from exc
    return _creation_plan_response(plan, task)


@app.get("/api/creation-tasks", response_model=CreationTasksResponse)
def list_creation_tasks(request: Request) -> CreationTasksResponse:
    tasks = production_repo.list_creation_tasks(current_workspace_id(request))
    return CreationTasksResponse(
        items=[item.__dict__ for item in tasks],
        total_count=len(tasks),
    )


def _creation_task_or_404(workspace_id: str, task_id: str) -> CreationTask:
    try:
        return production_repo.get_creation_task(workspace_id, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc


def _creation_plan_for_task_or_404(
    workspace_id: str,
    task: CreationTask,
) -> CreationPlan:
    try:
        plan = production_repo.get_creation_plan(workspace_id, task.plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc
    if plan.creation_task_id != task.id:
        raise HTTPException(status_code=404, detail="creation task not found")
    return plan


def _topic_failure_response(
    *,
    status_code: int,
    error: str,
    reason: str,
    impact: str,
    repair_action: str,
    retryable: bool,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": error,
            "reason": reason,
            "impact": impact,
            "repair_action": repair_action,
            "retryable": retryable,
        },
    )


@app.get(
    "/api/creation-tasks/{task_id}/topics",
    response_model=CreationTaskTopicsResponse,
)
def get_creation_task_topics(
    task_id: str,
    request: Request,
) -> CreationTaskTopicsResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    route_snapshot = _topic_route_snapshot()
    preflight_error: _CreationTaskTopicPreflightError | None = None
    input_count = 0
    try:
        sources, _, _ = _creation_task_topic_inputs(
            workspace_id,
            task,
            plan,
            cloud_egress=route_snapshot["execution_location"] == "cloud",
        )
        input_count = len(sources)
        if route_snapshot["availability"] == "unconfigured":
            preflight_error = _CreationTaskTopicPreflightError(
                reason="capability_unavailable",
                impact="当前没有可调用的文字模型连接，不能开始选题。",
                repair_action="在本机运行时设置中配置 OpenAI-compatible 文字模型后重试。",
                retryable=True,
            )
    except _CreationTaskTopicPreflightError as exc:
        preflight_error = exc
    return _creation_task_topic_response(
        workspace_id,
        task,
        route_snapshot=route_snapshot,
        preflight_error=preflight_error,
        preflight_input_count=input_count,
    )


@app.post(
    "/api/creation-tasks/{task_id}/topics/generate",
    response_model=CreationTaskTopicsResponse,
)
def generate_creation_task_topics(
    task_id: str,
    request: Request,
    _payload: CreationTaskTopicGenerateRequest,
) -> CreationTaskTopicsResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    route_snapshot = _topic_route_snapshot()
    provider_sources: tuple[CreationTaskTopicSource, ...] = ()
    input_references: list[dict[str, object]] = []
    evidence_by_reference_id: dict[str, dict[str, object]] = {}
    preflight_error: _CreationTaskTopicPreflightError | None = None
    try:
        (
            provider_sources,
            input_references,
            evidence_by_reference_id,
        ) = _creation_task_topic_inputs(
            workspace_id,
            task,
            plan,
            cloud_egress=route_snapshot["execution_location"] == "cloud",
        )
        if route_snapshot["availability"] == "unconfigured":
            raise _CreationTaskTopicPreflightError(
                reason="capability_unavailable",
                impact="当前没有可调用的文字模型连接，未生成选题候选。",
                repair_action="在本机运行时设置中配置 OpenAI-compatible 文字模型后重试。",
                retryable=True,
            )
    except _CreationTaskTopicPreflightError as exc:
        preflight_error = exc

    run = TopicGenerationRun(
        id=f"topic_run_{uuid4().hex[:12]}",
        workspace_id=workspace_id,
        task_id=task.id,
        plan_id=plan.id,
        status="running",
        route_snapshot=route_snapshot,
        prompt_schema_version="creation-task-topic/v1",
        input_references=input_references,
    )
    try:
        task, run = production_repo.start_topic_generation(run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc
    except ValueError as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_topic_stage_conflict",
            reason="当前创作任务不能重复开始选题。",
            impact="没有创建新的选题候选或重复运行记录。",
            repair_action=str(exc),
            retryable=task.stage_status == "failed",
        ) from exc

    if preflight_error is not None:
        task, _ = production_repo.fail_topic_generation(
            run.id,
            reason=preflight_error.reason,
            impact=preflight_error.impact,
            repair_action=preflight_error.repair_action,
            retryable=preflight_error.retryable,
        )
        raise _topic_failure_response(
            status_code=503,
            error="capability_unavailable",
            reason=preflight_error.reason,
            impact=preflight_error.impact,
            repair_action=preflight_error.repair_action,
            retryable=preflight_error.retryable,
        )

    try:
        generated_topics = _creation_task_topic_generator_from_settings().generate(
            CreationTaskTopicInput(
                goal=plan.goal,
                language=plan.language,
                sources=provider_sources,
            )
        )
    except CreationTaskTopicGenerationError as exc:
        production_repo.fail_topic_generation(
            run.id,
            reason=exc.reason,
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=exc.retryable,
            provider_attempted=True,
        )
        raise _topic_failure_response(
            status_code=502,
            error=exc.code,
            reason=exc.reason,
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=exc.retryable,
        ) from exc

    candidates: list[CreationTaskTopicCandidate] = []
    evidence_references: list[TopicEvidenceReference] = []
    try:
        for generated in generated_topics:
            candidate = CreationTaskTopicCandidate(
                id=f"topic_{uuid4().hex[:12]}",
                workspace_id=workspace_id,
                task_id=task.id,
                run_id=run.id,
                title=generated.title,
                angle=generated.angle,
                audience=generated.audience,
                outline=list(generated.outline),
                score=round(generated.score),
            )
            candidates.append(candidate)
            for reference_id in generated.evidence_reference_ids:
                source = evidence_by_reference_id.get(reference_id)
                if source is None:
                    raise ValueError("文字模型引用了未授权的证据")
                evidence_references.append(
                    TopicEvidenceReference(
                        id=f"topic_evidence_{uuid4().hex[:12]}",
                        workspace_id=workspace_id,
                        task_id=task.id,
                        candidate_id=candidate.id,
                        kind=str(source["kind"]),  # type: ignore[arg-type]
                        reference_id=str(source["reference_id"]),
                        version_or_identity=str(source["version_or_identity"]),
                        title=str(source["title"]),
                        authorization_or_license=str(source["authorization_or_license"]),
                        source_url=(
                            str(source["source_url"])
                            if source["source_url"] is not None
                            else None
                        ),
                    )
                )
        task, run, _, _ = production_repo.complete_topic_generation(
            run.id,
            candidates,
            evidence_references,
        )
    except ValueError as exc:
        production_repo.fail_topic_generation(
            run.id,
            reason="provider_output_invalid",
            impact="文字模型输出未通过选题和证据持久化校验，未生成候选。",
            repair_action="检查模型 JSON 输出与来源引用后重试。",
            retryable=True,
            provider_attempted=True,
        )
        raise _topic_failure_response(
            status_code=502,
            error="provider_output_invalid",
            reason="provider_output_invalid",
            impact="文字模型输出未通过选题和证据持久化校验，未生成候选。",
            repair_action="检查模型 JSON 输出与来源引用后重试。",
            retryable=True,
        ) from exc
    return _creation_task_topic_response(
        workspace_id,
        task,
        route_snapshot=route_snapshot,
        preflight_input_count=len(provider_sources),
    )


@app.post(
    "/api/creation-tasks/{task_id}/topics/{topic_id}/confirm",
    response_model=CreationTaskTopicsResponse,
)
def confirm_creation_task_topic(
    task_id: str,
    topic_id: str,
    request: Request,
    _payload: CreationTaskTopicConfirmRequest,
) -> CreationTaskTopicsResponse:
    workspace_id = current_workspace_id(request)
    try:
        task, _ = production_repo.confirm_topic_candidate(
            workspace_id,
            task_id,
            topic_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task topic not found") from exc
    except ValueError as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_topic_confirmation_conflict",
            reason="当前选题不能被确认。",
            impact="没有改变已确认的选题或任务阶段。",
            repair_action=str(exc),
            retryable=False,
        ) from exc
    return _creation_task_topic_response(workspace_id, task)


@app.get(
    "/api/production/materials/insights",
    response_model=MaterialLibraryInsightsResponse,
)
def get_material_library_insights(
    request: Request,
) -> MaterialLibraryInsightsResponse:
    workspace_id = current_workspace_id(request)
    for plan in montage_repo.recent(workspace_id, limit=50):
        if plan.status == "rendered":
            _record_montage_usage(workspace_id, plan)
    return MaterialLibraryInsightsResponse.model_validate(
        production_repo.library_insights(workspace_id)
    )


@app.get(
    "/api/production/materials",
    response_model=MaterialLibraryResponse,
)
def list_production_materials(
    request: Request,
    limit: int = 24,
    offset: int = 0,
    q: str | None = None,
    review_status: str | None = None,
) -> MaterialLibraryResponse:
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="invalid material pagination")
    if review_status not in {None, "raw", "approved", "rejected"}:
        raise HTTPException(status_code=422, detail="invalid material review status")
    assets, total = production_repo.list_assets_page(
        current_workspace_id(request),
        limit=limit,
        offset=offset,
        query=q,
        review_status=review_status,
    )
    return MaterialLibraryResponse(
        assets=[
            _material_asset_response(asset)
            for asset in assets
        ],
        total_count=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(assets) < total,
    )


@app.get(
    "/api/production/materials/{asset_id}",
    response_model=MaterialAssetResponse,
)
def get_production_material(
    asset_id: str,
    request: Request,
) -> MaterialAssetResponse:
    try:
        asset = production_repo.get_asset(current_workspace_id(request), asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    return _material_asset_response(asset)


@app.get("/api/production/materials/{asset_id}/media")
def stream_production_material(asset_id: str, request: Request) -> FileResponse:
    """Stream a workspace-scoped library asset for the desktop review player."""
    try:
        asset = production_repo.get_asset(current_workspace_id(request), asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    root = Path(settings.video_inbox_path).expanduser().resolve()
    path = Path(asset.path).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="material asset is outside the library") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="material file not found")
    media_type = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@app.post(
    "/api/production/materials/scan",
    response_model=MaterialLibraryResponse,
)
def scan_production_materials(request: Request) -> MaterialLibraryResponse:
    workspace_id = current_workspace_id(request)
    production_repo.scan_library(workspace_id)
    assets, total = production_repo.list_assets_page(workspace_id, limit=24, offset=0)
    return MaterialLibraryResponse(
        assets=[
            _material_asset_response(asset)
            for asset in assets
        ],
        total_count=total,
        limit=24,
        offset=0,
        has_more=len(assets) < total,
    )


@app.post(
    "/api/production/materials/{asset_id}/analyze",
    response_model=MaterialAssetResponse,
)
def analyze_production_material(
    asset_id: str,
    request: Request,
) -> MaterialAssetResponse:
    try:
        asset = production_repo.analyze_asset(current_workspace_id(request), asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    return _material_asset_response(asset)


@app.put(
    "/api/production/materials/{asset_id}/review",
    response_model=MaterialAssetResponse,
)
def review_production_material(
    asset_id: str,
    request: Request,
    payload: MaterialReviewRequest,
) -> MaterialAssetResponse:
    try:
        asset = production_repo.update_asset_review(
            current_workspace_id(request),
            asset_id,
            review_status=payload.review_status,
            rights_confirmed=payload.rights_confirmed,
            brand_safe=payload.brand_safe,
            usage_role=payload.usage_role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    return _material_asset_response(asset)


@app.put(
    "/api/production/materials/{asset_id}/segments/{segment_id}",
    response_model=MaterialAssetResponse,
)
def update_production_material_segment(
    asset_id: str,
    segment_id: str,
    request: Request,
    payload: MaterialSegmentUpdateRequest,
) -> MaterialAssetResponse:
    try:
        asset = production_repo.update_segment(
            current_workspace_id(request),
            asset_id,
            segment_id,
            description=payload.description,
            transcript=payload.transcript,
            review_status=payload.review_status,
            usage_role=payload.usage_role,
            user_tags=payload.user_tags,
            collection_ids=payload.collection_ids,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material segment not found") from exc
    return _material_asset_response(asset)


@app.get(
    "/api/production/batches",
    response_model=ProductionBatchesResponse,
)
def list_production_batches(request: Request) -> ProductionBatchesResponse:
    batches = [
        _recover_production_batch_if_needed(batch)
        for batch in production_repo.list_batches(current_workspace_id(request))
    ]
    return ProductionBatchesResponse(
        batches=[
            _production_batch_response(batch)
            for batch in batches
        ],
        total_count=len(batches),
    )


@app.post(
    "/api/production/batches",
)
def create_production_batch() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_batch_creation_retired",
            "message": "旧的生产批次创建入口已退役，不会创建创作任务。",
            "impact": "本次请求没有写入批次、创作方案或创作任务。",
            "repair_action": "从创作任务进入“检查可用内容”，再完成“确认并创建任务”流程。",
            "retryable": False,
        },
    )


@app.get(
    "/api/production/batches/{batch_id}",
    response_model=ProductionBatchResponse,
)
def get_production_batch(
    batch_id: str,
    request: Request,
) -> ProductionBatchResponse:
    try:
        batch = _recover_production_batch_if_needed(
            production_repo.get_batch(current_workspace_id(request), batch_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="production batch not found") from exc
    return _production_batch_response(batch)


@app.post(
    "/api/production/batches/{batch_id}/run",
    response_model=ProductionBatchResponse,
    status_code=202,
)
def run_production_batch(
    batch_id: str,
    request: Request,
) -> ProductionBatchResponse:
    workspace_id = current_workspace_id(request)
    try:
        batch = _recover_production_batch_if_needed(
            production_repo.get_batch(workspace_id, batch_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="production batch not found") from exc
    if batch_job_runner.is_active(batch.id):
        return _production_batch_response(batch)
    if batch.status in {"ready", "reviewed"}:
        return _production_batch_response(batch)

    profile = production_profile_with_model_status(workspace_id)
    readiness = production_repo.readiness(workspace_id, profile=profile)
    blockers = [str(item["code"]) for item in readiness["blockers"]]
    if blockers:
        batch.status = "blocked"
        batch.blocker_codes = blockers
        batch.message = "生产基础尚未就绪，修复阻断后再继续。"
        production_repo.save_batch(batch)
        raise HTTPException(status_code=409, detail=batch.message)

    batch.status = "running"
    batch.blocker_codes = []
    batch.started_at = batch.started_at or utc_now()
    batch.finished_at = None
    batch.message = "批次已进入串行队列，正在准备第一条内容。"
    production_repo.save_batch(batch)
    submitted = batch_job_runner.submit(
        batch.id,
        lambda: _complete_production_batch(workspace_id, batch.id),
        workspace_id=workspace_id,
    )
    if not submitted:
        batch = production_repo.get_batch(workspace_id, batch.id)
    return _production_batch_response(batch)


def _recover_production_batch_if_needed(batch: ProductionBatch) -> ProductionBatch:
    if (
        batch.status != "running"
        or batch.started_at is None
        or batch_job_runner.is_active(batch.id)
    ):
        return batch
    for item in batch.items:
        if item.status == "running":
            item.status = "interrupted"
            item.message = "应用重启导致任务中断，可从当前条目继续。"
            item.updated_at = utc_now()
    batch.status = "interrupted"
    batch.finished_at = utc_now()
    batch.message = "上次批量任务已中断，点击继续后会保留已生成成片。"
    return production_repo.save_batch(batch)


def _eligible_batch_episodes(workspace_id: str) -> list[Episode]:
    allowed = {
        EpisodeStatus.SCRIPT_APPROVED,
        EpisodeStatus.WAITING_VIDEO,
        EpisodeStatus.VIDEO_PROCESSING,
        EpisodeStatus.DRAFT_READY,
        EpisodeStatus.PUBLISHED,
    }
    return [
        episode
        for episode in repo.all(workspace_id=workspace_id)
        if episode.script is not None and episode.status in allowed
    ]


def _complete_production_batch(workspace_id: str, batch_id: str) -> None:
    try:
        batch = production_repo.get_batch(workspace_id, batch_id)
        eligible = _eligible_batch_episodes(workspace_id)
        known_ids = {item.episode_id for item in batch.items}
        for episode in eligible:
            if len(batch.items) >= batch.target_count:
                break
            if episode.id not in known_ids:
                batch.items.append(ProductionBatchItem(episode_id=episode.id))
                known_ids.add(episode.id)
        production_repo.save_batch(batch)

        for item in batch.items[: batch.target_count]:
            if item.status == "rendered":
                continue
            item.status = "running"
            item.attempts += 1
            item.updated_at = utc_now()
            item.message = "正在生成旁白与混剪时间线。"
            batch.status = "running"
            batch.message = (
                f"正在处理 {item.episode_id}；"
                f"已完成 {batch.rendered_count}/{batch.target_count} 条。"
            )
            production_repo.save_batch(batch)
            try:
                _process_production_batch_item(workspace_id, batch, item)
            except Exception as exc:
                item.status = "failed"
                item.message = str(exc)
                item.updated_at = utc_now()
                logger.exception(
                    "production batch item failed: batch=%s episode=%s",
                    batch.id,
                    item.episode_id,
                )
            production_repo.save_batch(batch)
        _finalize_production_batch(workspace_id, batch_id, len(eligible))
    except Exception:
        logger.exception("production batch failed: %s", batch_id)
        try:
            batch = production_repo.get_batch(workspace_id, batch_id)
            batch.status = "failed"
            batch.finished_at = utc_now()
            batch.message = "批量执行器异常停止，已完成条目会保留，可重试。"
            production_repo.save_batch(batch)
        except Exception:
            logger.exception("unable to persist failed production batch: %s", batch_id)


def _process_production_batch_item(
    workspace_id: str,
    batch: ProductionBatch,
    item: ProductionBatchItem,
) -> None:
    episode = repo.get(item.episode_id, workspace_id=workspace_id)
    if episode.id not in batch.episode_ids:
        batch.episode_ids.append(episode.id)
    existing = next(
        (
            plan
            for plan in montage_repo.list_for_batch(workspace_id, batch.id)
            if plan.episode_id == episode.id and plan.status != "failed"
        ),
        None,
    )
    if existing is not None and existing.status == "rendered":
        item.status = "rendered"
        item.plan_id = existing.id
        item.narration_audio_path = existing.narration_audio_path
        item.output_path = existing.output_path
        item.message = "已复用完成的初版成片。"
        item.updated_at = utc_now()
        return
    if existing is not None and existing.status == "waiting_for_user":
        item.status = "waiting_for_user"
        item.plan_id = existing.id
        item.narration_audio_path = existing.narration_audio_path
        item.message = existing.message
        item.updated_at = utc_now()
        return

    plan = existing
    if plan is None or not plan.narration_audio_path:
        profile = production_profile_with_model_status(workspace_id)
        narration = _narration_for_episode(workspace_id, episode, profile)
        narration_reference = production_repo.narration_artifact_reference(
            narration.cache_key
        )
        rebuilt = montage_planner.build(
            workspace_id=workspace_id,
            batch=batch,
            episode=episode,
            profile=profile,
            assets=production_repo.list_assets(workspace_id),
            narration_audio_path=narration_reference,
            narration_duration_seconds=narration.duration_seconds,
        )
        _normalize_montage_clip_references(workspace_id, rebuilt)
        if plan is not None:
            rebuilt.id = plan.id
            rebuilt.created_at = plan.created_at
        plan = montage_repo.save(rebuilt)
        if episode.id not in batch.episode_ids:
            batch.episode_ids.append(episode.id)

    item.plan_id = plan.id
    item.narration_audio_path = plan.narration_audio_path
    if plan.status == "waiting_for_user":
        item.status = "waiting_for_user"
        item.message = plan.message
        item.updated_at = utc_now()
        return
    if not plan.narration_audio_path:
        raise ValueError("混剪计划没有可用的旁白音频。")
    narration_reference = plan.narration_audio_path
    resolved_narration_audio_path = (
        production_repo.resolve_authorized_narration_audio_reference(
            workspace_id,
            narration_reference,
        )
    )
    if (
        not resolved_narration_audio_path.is_file()
        or resolved_narration_audio_path.stat().st_size == 0
    ):
        raise MediaAuthorityError(
            reason="narration_audio_unavailable",
            impact="混剪渲染未使用不存在或为空的旁白音频。",
            repair_action="重新生成当前工作区的旁白后再渲染。",
        )
    rendered = _render_montage_with_resolved_clip_sources(
        workspace_id,
        plan,
        resolved_narration_audio_path,
    )
    rendered.narration_audio_path = narration_reference
    montage_repo.save(rendered)
    _record_montage_usage(workspace_id, rendered)
    item.status = "rendered"
    item.output_path = rendered.output_path
    item.message = rendered.message
    item.updated_at = utc_now()


def _finalize_production_batch(
    workspace_id: str,
    batch_id: str,
    eligible_count: int,
) -> ProductionBatch:
    batch = production_repo.get_batch(workspace_id, batch_id)
    batch.finished_at = utc_now()
    if batch.rendered_count >= batch.target_count:
        batch.status = "ready"
        batch.message = "批次初版成片已全部生成，等待最终审核。"
    elif batch.waiting_review_count:
        batch.status = "waiting_for_user"
        batch.message = (
            f"已生成 {batch.rendered_count}/{batch.target_count} 条；"
            f"有 {batch.waiting_review_count} 条时间线等待镜头确认。"
        )
    elif batch.failed_count and batch.rendered_count == 0:
        batch.status = "failed"
        batch.message = (
            f"本轮 {batch.failed_count} 条失败，修复原因后可继续，"
            "已完成结果不会重做。"
        )
    else:
        batch.status = "created"
        failure_message = (
            f"本轮 {batch.failed_count} 条失败，可修复后继续；"
            if batch.failed_count
            else ""
        )
        batch.message = (
            f"已生成 {batch.rendered_count}/{batch.target_count} 条；"
            f"{failure_message}当前有 {eligible_count} 条已确认文案。"
        )
    return production_repo.save_batch(batch)


def _narration_for_episode(
    workspace_id: str,
    episode: Episode,
    profile: ProductionProfile | None = None,
) -> NarrationArtifact:
    profile = profile or production_profile_with_model_status(workspace_id)
    inputs = production_repo.resolve_episode_narration_inputs(
        workspace_id,
        episode.id,
    )
    cache_key = narration_generator.cache_key(
        episode=episode,
        profile=profile,
        voice_identity=inputs.voice,
        pronunciation_entries=inputs.pronunciation_entries,
    )
    cached = production_repo.get_narration_artifact(workspace_id, cache_key)
    if cached is not None:
        return NarrationArtifact(
            episode_id=cached.episode_id,
            audio_path=cached.audio_path,
            duration_seconds=cached.duration_seconds,
            sample_rate=cached.sample_rate,
            model_id=cached.model_id,
            voice_profile_id=cached.voice_profile_id,
            cache_key=cached.cache_key,
        )
    resolved_reference_audio_path = production_repo.resolve_workspace_media_reference(
        workspace_id,
        inputs.voice.reference_audio_path,
        require_registered=True,
    )
    if (
        not resolved_reference_audio_path.is_file()
        or resolved_reference_audio_path.stat().st_size == 0
    ):
        raise MediaAuthorityError(
            reason="voice_reference_unavailable",
            impact="声音参考不可用，未调用声音模型或复用旧配音。",
            repair_action="确认当前工作区已授权媒体根内的参考音频存在且非空。",
        )
    artifact = narration_generator.generate(
        workspace_id=workspace_id,
        episode=episode,
        profile=profile,
        voice_identity=inputs.voice,
        pronunciation_entries=inputs.pronunciation_entries,
        resolved_reference_audio_path=resolved_reference_audio_path,
    )
    production_repo.save_narration_artifact(
        NarrationArtifactRecord(
            id=f"narration_{artifact.cache_key[:16]}",
            workspace_id=workspace_id,
            episode_id=episode.id,
            script_id=episode.script.id if episode.script is not None else "",
            voice_profile_id=artifact.voice_profile_id,
            cache_key=artifact.cache_key,
            audio_path=artifact.audio_path,
            duration_seconds=artifact.duration_seconds,
            sample_rate=artifact.sample_rate,
            model_id=artifact.model_id,
        )
    )
    return artifact


def _record_montage_usage(workspace_id: str, plan) -> None:
    for clip in plan.clips:
        production_repo.record_segment_usage(
            workspace_id,
            clip.asset_id,
            clip.segment_id,
            plan_id=plan.id,
            clip_id=clip.id,
        )


def _normalize_montage_clip_references(workspace_id: str, plan):
    for clip in plan.clips:
        clip.source_path = production_repo.workspace_media_reference_for_asset(
            workspace_id,
            clip.asset_id,
        )
    return plan


def _render_montage_with_resolved_clip_sources(
    workspace_id: str,
    plan,
    narration_audio_path: Path,
):
    original_references = [clip.source_path for clip in plan.clips]
    try:
        for clip in plan.clips:
            resolved_source_path = production_repo.resolve_clip_media_reference(
                workspace_id,
                clip.asset_id,
                clip.source_path,
            )
            if not resolved_source_path.is_file() or resolved_source_path.stat().st_size == 0:
                raise MediaAuthorityError(
                    reason="clip_media_unavailable",
                    impact="缺失或为空的素材未被传给渲染器。",
                    repair_action="重新扫描当前工作区素材并更新剪辑计划。",
                )
            clip.source_path = str(resolved_source_path)
        return montage_renderer.render(plan, narration_audio_path)
    finally:
        for clip, reference in zip(plan.clips, original_references, strict=True):
            clip.source_path = reference


@app.post(
    "/api/production/episodes/{episode_id}/narration",
    response_model=NarrationArtifactResponse,
)
def generate_production_narration(
    episode_id: str,
    request: Request,
) -> NarrationArtifactResponse:
    workspace_id = current_workspace_id(request)
    try:
        episode = repo.get(episode_id, workspace_id=workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
    try:
        artifact = _narration_for_episode(workspace_id, episode)
    except MediaAuthorityError as exc:
        raise _media_authority_http_error(exc) from exc
    except VoiceCloneError as exc:
        raise _voice_clone_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return NarrationArtifactResponse(
        episode_id=artifact.episode_id,
        audio_reference=production_repo.narration_artifact_reference(artifact.cache_key),
        duration_seconds=artifact.duration_seconds,
        sample_rate=artifact.sample_rate,
        model_id=artifact.model_id,
        voice_profile_id=artifact.voice_profile_id,
        cache_key=artifact.cache_key,
    )


@app.get(
    "/api/production/batches/{batch_id}/montage-plans",
    response_model=MontagePlansResponse,
)
def list_production_montage_plans(
    batch_id: str,
    request: Request,
) -> MontagePlansResponse:
    workspace_id = current_workspace_id(request)
    try:
        production_repo.get_batch(workspace_id, batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="production batch not found") from exc
    plans = montage_repo.list_for_batch(workspace_id, batch_id)
    return MontagePlansResponse(
        plans=[
            _montage_plan_response(plan)
            for plan in plans
        ],
        total_count=len(plans),
    )


@app.post(
    "/api/production/batches/{batch_id}/montage-plans",
    response_model=MontagePlanResponse,
)
def create_production_montage_plan(
    batch_id: str,
    request: Request,
    payload: MontagePlanCreateRequest,
) -> MontagePlanResponse:
    workspace_id = current_workspace_id(request)
    try:
        batch = production_repo.get_batch(workspace_id, batch_id)
        episode = repo.get(payload.episode_id, workspace_id=workspace_id)
        existing_plan = next(
            (
                item
                for item in montage_repo.list_for_batch(workspace_id, batch_id)
                if item.episode_id == episode.id and item.status != "failed"
            ),
            None,
        )
        if existing_plan is not None:
            raise ValueError("该 Episode 已存在可恢复的混剪计划")
        profile = production_profile_with_model_status(workspace_id)
        narration = _narration_for_episode(workspace_id, episode, profile)
        narration_audio_path = production_repo.narration_artifact_reference(
            narration.cache_key
        )
        audio_duration = narration.duration_seconds
        plan = montage_planner.build(
            workspace_id=workspace_id,
            batch=batch,
            episode=episode,
            profile=profile,
            assets=production_repo.list_assets(workspace_id),
            narration_audio_path=narration_audio_path,
            narration_duration_seconds=audio_duration,
        )
        _normalize_montage_clip_references(workspace_id, plan)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="batch or episode not found") from exc
    except MediaAuthorityError as exc:
        raise _media_authority_http_error(exc) from exc
    except VoiceCloneError as exc:
        raise _voice_clone_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    montage_repo.save(plan)
    if episode.id not in batch.episode_ids:
        batch.episode_ids.append(episode.id)
    batch_item = next(
        (item for item in batch.items if item.episode_id == episode.id),
        None,
    )
    if batch_item is None:
        batch_item = ProductionBatchItem(episode_id=episode.id)
        batch.items.append(batch_item)
    batch_item.plan_id = plan.id
    batch_item.narration_audio_path = plan.narration_audio_path
    batch_item.status = "waiting_for_user" if plan.status == "waiting_for_user" else "queued"
    batch_item.message = plan.message
    batch_item.updated_at = utc_now()
    batch.status = "waiting_for_user" if plan.status == "waiting_for_user" else "created"
    batch.message = (
        "混剪时间线等待镜头确认。"
        if plan.status == "waiting_for_user"
        else "混剪时间线已就绪，等待渲染。"
    )
    production_repo.save_batch(batch)
    return _montage_plan_response(plan)


@app.post(
    "/api/production/montage-plans/{plan_id}/confirm",
    response_model=MontagePlanResponse,
)
def confirm_production_montage_clips(
    plan_id: str,
    request: Request,
    payload: MontagePlanConfirmRequest,
) -> MontagePlanResponse:
    workspace_id = current_workspace_id(request)
    try:
        plan = montage_repo.get(workspace_id, plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="montage plan not found") from exc
    requested = set(payload.clip_ids)
    known = {clip.id for clip in plan.clips}
    if not requested <= known:
        raise HTTPException(status_code=400, detail="montage clip not found")
    for clip in plan.clips:
        if clip.id in requested and clip.needs_confirmation:
            clip.needs_confirmation = False
            clip.match_reasons.append("已由用户确认")
    plan.low_confidence_clip_count = sum(clip.needs_confirmation for clip in plan.clips)
    if plan.low_confidence_clip_count == 0:
        plan.status = "ready"
        plan.message = "所有低置信镜头已确认，可以进入渲染。"
    else:
        plan.message = f"仍有 {plan.low_confidence_clip_count} 个镜头等待确认。"
    montage_repo.save(plan)
    batch = production_repo.get_batch(workspace_id, plan.batch_id)
    batch_item = next(
        (item for item in batch.items if item.episode_id == plan.episode_id),
        None,
    )
    if batch_item is None:
        batch_item = ProductionBatchItem(episode_id=plan.episode_id)
        batch.items.append(batch_item)
    batch_item.plan_id = plan.id
    batch_item.status = "queued" if plan.status == "ready" else "waiting_for_user"
    batch_item.message = plan.message
    batch_item.updated_at = utc_now()
    batch.status = "created" if plan.status == "ready" else "waiting_for_user"
    batch.message = (
        "混剪时间线已就绪，等待渲染。"
        if plan.status == "ready"
        else "混剪时间线仍有镜头等待确认。"
    )
    production_repo.save_batch(batch)
    return _montage_plan_response(plan)


@app.get(
    "/api/production/montage-plans/{plan_id}",
    response_model=MontagePlanResponse,
)
def get_production_montage_plan(
    plan_id: str,
    request: Request,
) -> MontagePlanResponse:
    try:
        plan = montage_repo.get(current_workspace_id(request), plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="montage plan not found") from exc
    return _montage_plan_response(plan)


def _stream_montage_plan_media(
    plan_id: str,
    request: Request,
    *,
    field: str,
    media_type: str,
) -> FileResponse:
    try:
        plan = montage_repo.get(current_workspace_id(request), plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="montage plan not found") from exc
    path = _safe_montage_media_path(plan, field)
    if path is None:
        raise HTTPException(status_code=404, detail="montage media not found")
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@app.get("/api/production/montage-plans/{plan_id}/output")
def stream_production_montage_output(plan_id: str, request: Request) -> FileResponse:
    return _stream_montage_plan_media(
        plan_id,
        request,
        field="output_path",
        media_type="video/mp4",
    )


@app.get("/api/production/montage-plans/{plan_id}/subtitle")
def stream_production_montage_subtitle(plan_id: str, request: Request) -> FileResponse:
    return _stream_montage_plan_media(
        plan_id,
        request,
        field="subtitle_path",
        media_type="application/x-subrip",
    )


def _refresh_montage_review_state(plan) -> None:
    plan.low_confidence_clip_count = sum(clip.needs_confirmation for clip in plan.clips)
    plan.status = "ready" if plan.low_confidence_clip_count == 0 else "waiting_for_user"
    plan.output_path = None
    plan.subtitle_path = None
    plan.message = (
        "镜头审核已完成，可以重新渲染。"
        if plan.status == "ready"
        else f"仍有 {plan.low_confidence_clip_count} 个镜头等待确认。"
    )


@app.get(
    "/api/production/montage-plans/{plan_id}/clips/{clip_id}/candidates",
    response_model=list[MontageClipCandidateResponse],
)
def list_production_montage_clip_candidates(
    plan_id: str,
    clip_id: str,
    request: Request,
) -> list[MontageClipCandidateResponse]:
    workspace_id = current_workspace_id(request)
    try:
        plan = montage_repo.get(workspace_id, plan_id)
        clip = next(item for item in plan.clips if item.id == clip_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="montage clip not found") from exc
    cue = next((item for item in plan.cues if item.id == clip.cue_id), None)
    cue_tokens = set((cue.text if cue else "").lower())
    candidates: list[MontageClipCandidateResponse] = []
    for asset in production_repo.list_assets(workspace_id):
        if asset.review_status != "approved" or not asset.rights_confirmed or not asset.brand_safe:
            continue
        for segment in asset.segments:
            if segment.review_status != "approved" or segment.usage_role not in {"a_roll", "b_roll"}:
                continue
            available = segment.end_seconds - segment.start_seconds
            slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
            if available + 1e-6 < slot_duration:
                continue
            description = (segment.description or segment.transcript or asset.file_name).strip()
            overlap = len(cue_tokens & set(description.lower())) / max(1, len(cue_tokens))
            score = max(0.0, min(1.0, overlap * 0.7 + min(1.0, available / max(slot_duration, 0.1)) * 0.3))
            candidates.append(MontageClipCandidateResponse(
                asset_id=asset.id,
                segment_id=segment.id,
                file_name=asset.file_name,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                description=description,
                score=round(score, 3),
                reasons=["已通过版权和品牌审核", "时长满足当前旁白时间槽"],
            ))
    candidates.sort(key=lambda item: (item.asset_id == clip.asset_id and item.segment_id == clip.segment_id, item.score), reverse=True)
    return candidates[:24]


@app.patch(
    "/api/production/montage-plans/{plan_id}/clips/{clip_id}",
    response_model=MontagePlanResponse,
)
def update_production_montage_clip(
    plan_id: str,
    clip_id: str,
    request: Request,
    payload: MontageClipUpdateRequest,
) -> MontagePlanResponse:
    workspace_id = current_workspace_id(request)
    try:
        plan = montage_repo.get(workspace_id, plan_id)
        clip = next(item for item in plan.clips if item.id == clip_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="montage clip not found") from exc
    if plan.status == "rendering":
        raise HTTPException(status_code=409, detail="montage plan is rendering")
    asset_id = payload.asset_id or clip.asset_id
    segment_id = payload.segment_id or clip.segment_id
    if payload.asset_id is not None or payload.segment_id is not None:
        try:
            asset = production_repo.get_asset(workspace_id, asset_id)
            segment = next(item for item in asset.segments if item.id == segment_id)
        except (KeyError, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="material segment not found") from exc
        if asset.review_status != "approved" or not asset.rights_confirmed or not asset.brand_safe or segment.review_status != "approved":
            raise HTTPException(status_code=409, detail="material segment has not passed review")
        clip.asset_id = asset.id
        clip.segment_id = segment.id
        clip.source_path = production_repo.workspace_media_reference_for_asset(
            workspace_id,
            asset.id,
        )
        clip.role = "a_roll" if segment.usage_role == "a_roll" else "b_roll"
        clip.match_reasons = ["由用户替换", "已通过版权和品牌审核"]
        clip.match_score = 1.0
        clip.manually_selected = True
    else:
        asset = production_repo.get_asset(workspace_id, clip.asset_id)
        segment = next((item for item in asset.segments if item.id == clip.segment_id), None)
        if segment is None:
            raise HTTPException(status_code=404, detail="material segment not found")
    slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
    source_start = payload.source_start_seconds if payload.source_start_seconds is not None else max(segment.start_seconds, clip.source_start_seconds)
    source_end = source_start + slot_duration
    if source_start < segment.start_seconds - 1e-6 or source_end > segment.end_seconds + 1e-6:
        raise HTTPException(status_code=400, detail="trim range exceeds the reviewed segment")
    clip.source_start_seconds = round(source_start, 3)
    clip.source_end_seconds = round(source_end, 3)
    if payload.locked is not None:
        clip.locked = payload.locked
    if payload.confirmed is True or payload.asset_id is not None or payload.segment_id is not None:
        clip.needs_confirmation = False
    _refresh_montage_review_state(plan)
    montage_repo.save(plan)
    return _montage_plan_response(plan)


@app.put(
    "/api/production/montage-plans/{plan_id}/clips/order",
    response_model=MontagePlanResponse,
)
def reorder_production_montage_clips(
    plan_id: str,
    request: Request,
    payload: MontageClipOrderRequest,
) -> MontagePlanResponse:
    workspace_id = current_workspace_id(request)
    try:
        plan = montage_repo.get(workspace_id, plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="montage plan not found") from exc
    if set(payload.clip_ids) != {clip.id for clip in plan.clips} or len(payload.clip_ids) != len(plan.clips):
        raise HTTPException(status_code=400, detail="clip order must contain every clip exactly once")
    by_id = {clip.id: clip for clip in plan.clips}
    for index, current in enumerate(plan.clips):
        if current.locked and payload.clip_ids[index] != current.id:
            raise HTTPException(status_code=409, detail="locked clips cannot be moved")
    sources = [
        {
            "asset_id": by_id[clip_id].asset_id,
            "segment_id": by_id[clip_id].segment_id,
            "source_path": by_id[clip_id].source_path,
            "source_start_seconds": by_id[clip_id].source_start_seconds,
            "source_end_seconds": by_id[clip_id].source_end_seconds,
            "match_score": by_id[clip_id].match_score,
            "match_reasons": list(by_id[clip_id].match_reasons),
            "needs_confirmation": by_id[clip_id].needs_confirmation,
            "role": by_id[clip_id].role,
        }
        for clip_id in payload.clip_ids
    ]
    for target, source in zip(plan.clips, sources, strict=True):
        if target.locked:
            continue
        target.asset_id = str(source["asset_id"])
        target.segment_id = str(source["segment_id"])
        target.source_path = str(source["source_path"])
        target.source_start_seconds = float(source["source_start_seconds"])
        target.source_end_seconds = float(source["source_end_seconds"])
        target.match_score = float(source["match_score"])
        target.match_reasons = [*source["match_reasons"], "由用户调整视觉顺序"]
        target.needs_confirmation = bool(source["needs_confirmation"])
        target.role = source["role"]
        target.manually_selected = True
    _refresh_montage_review_state(plan)
    montage_repo.save(plan)
    return _montage_plan_response(plan)


@app.post(
    "/api/production/montage-plans/{plan_id}/render",
    response_model=MontagePlanResponse,
)
def render_production_montage_plan(
    plan_id: str,
    request: Request,
    payload: MontageRenderRequest,
) -> MontagePlanResponse:
    workspace_id = current_workspace_id(request)
    try:
        plan = montage_repo.get(workspace_id, plan_id)
        narration_reference = plan.narration_audio_path
        if not narration_reference:
            raise MediaAuthorityError(
                reason="narration_audio_unavailable",
                impact="混剪计划没有已持久化的旁白引用，未调用渲染器。",
                repair_action="重新创建混剪计划以由服务端绑定旁白。",
            )
        resolved_narration_audio_path = production_repo.resolve_authorized_narration_audio_reference(
            workspace_id,
            narration_reference,
        )
        if (
            not resolved_narration_audio_path.is_file()
            or resolved_narration_audio_path.stat().st_size == 0
        ):
            raise MediaAuthorityError(
                reason="narration_audio_unavailable",
                impact="混剪渲染未使用不存在或为空的旁白音频。",
                repair_action="选择当前工作区已授权媒体根内的非空旁白音频。",
            )
        audio_duration = montage_renderer.audio_duration(resolved_narration_audio_path)
        if abs(audio_duration - plan.duration_seconds) > 0.5:
            batch = production_repo.get_batch(workspace_id, plan.batch_id)
            episode = repo.get(plan.episode_id, workspace_id=workspace_id)
            rebuilt = montage_planner.build(
                workspace_id=workspace_id,
                batch=batch,
                episode=episode,
                profile=production_repo.get_profile(workspace_id),
                assets=production_repo.list_assets(workspace_id),
                narration_audio_path=narration_reference,
                narration_duration_seconds=audio_duration,
            )
            _normalize_montage_clip_references(workspace_id, rebuilt)
            rebuilt.id = plan.id
            rebuilt.created_at = plan.created_at
            plan = montage_repo.save(rebuilt)
            if plan.status != "ready":
                batch_item = next(
                    (item for item in batch.items if item.episode_id == plan.episode_id),
                    None,
                )
                if batch_item is None:
                    batch_item = ProductionBatchItem(episode_id=plan.episode_id)
                    batch.items.append(batch_item)
                batch_item.plan_id = plan.id
                batch_item.status = "waiting_for_user"
                batch_item.narration_audio_path = plan.narration_audio_path
                batch_item.message = plan.message
                batch_item.updated_at = utc_now()
                batch.status = "waiting_for_user"
                batch.message = "已按真实旁白时长重排时间线，等待镜头确认。"
                production_repo.save_batch(batch)
                raise HTTPException(
                    status_code=409,
                    detail="旁白时长与原计划不同，已重新生成时间线；请确认低置信镜头后再渲染",
                )
        rendered = _render_montage_with_resolved_clip_sources(
            workspace_id,
            plan,
            resolved_narration_audio_path,
        )
        # The renderer needs the resolved local path, but persisted/API state must
        # retain the server-bound logical narration reference.
        rendered.narration_audio_path = narration_reference
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="montage plan not found") from exc
    except MediaAuthorityError as exc:
        raise _media_authority_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        montage_repo.save(plan)
        raise HTTPException(status_code=500, detail=plan.message) from exc

    montage_repo.save(rendered)
    _record_montage_usage(workspace_id, rendered)
    batch = production_repo.get_batch(workspace_id, rendered.batch_id)
    batch_item = next(
        (item for item in batch.items if item.episode_id == rendered.episode_id),
        None,
    )
    if batch_item is None:
        batch_item = ProductionBatchItem(episode_id=rendered.episode_id)
        batch.items.append(batch_item)
    batch_item.plan_id = rendered.id
    batch_item.status = "rendered"
    batch_item.narration_audio_path = rendered.narration_audio_path
    batch_item.output_path = rendered.output_path
    batch_item.message = rendered.message
    batch_item.updated_at = utc_now()
    rendered_count = batch.rendered_count
    batch.status = "ready" if rendered_count >= batch.target_count else "created"
    batch.message = (
        "批次初版成片已全部生成，等待最终审核。"
        if batch.status == "ready"
        else f"已生成 {rendered_count}/{batch.target_count} 条初版成片，可继续批量生产。"
    )
    production_repo.save_batch(batch)
    return _montage_plan_response(rendered)


@app.get("/api/version")
async def get_version():
    """Return the current API version, build info, and completed phases."""
    import subprocess
    from pathlib import Path as _Path

    # Read version from pyproject.toml
    version = "0.1.0"
    pyproject_path = _Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                version = stripped.split("=", 1)[1].strip().strip('"')
                break

    # Read git commit and date
    git_commit = ""
    build_date = ""
    try:
        result = subprocess.run(  # noqa: ASYNC221
            ["git", "log", "-1", "--format=%H,%aI"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=_Path(__file__).resolve().parents[3],
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",", 1)
            git_commit = parts[0]
            build_date = parts[1] if len(parts) > 1 else ""
    except (OSError, subprocess.SubprocessError):
        pass

    return {
        "version": version,
        "build_date": build_date,
        "git_commit": git_commit,
        "phases_completed": [
            "phase-a-script-control-feedback-foundations",
            "phase-b-production-modes-chain-source-lifecycle",
            "phase-c-publish-feedback-episode-history",
            "phase-d-source-sync-preference-cards",
            "phase-0-emergency-hardening",
        ],
    }


def _require_feature(feature: str) -> None:
    """Raise 503 if a required feature gate is not available."""
    import shutil as _shutil

    ffmpeg_available = _shutil.which("ffmpeg") is not None
    gates: dict[str, tuple[bool, str]] = {
        "generate-topics": (True, ""),
        "generate-script": (True, ""),
        "regenerate-section": (True, ""),
        "regenerate-script": (True, ""),
        "process-video": (ffmpeg_available, "ffmpeg 未安装，请运行 brew install ffmpeg"),
    }
    available, reason = gates.get(feature, (True, ""))
    if not available:
        raise HTTPException(
            status_code=503,
            detail={"error": reason, "action": reason},
        )


@app.get("/api/runtime/health", response_model=RuntimeHealthResponse)
def get_runtime_health() -> RuntimeHealthResponse:
    return RuntimeHealthResponse.model_validate(runtime_health(settings))


@app.get("/api/setup/preflight", response_model=SetupPreflightResponse)
def get_setup_preflight() -> SetupPreflightResponse:
    """Run a native setup preflight and return a stable consumable structure.

    Returns overall ready|blocked with per-check details including
    status, summary, impact, repair_action, and optional settings_target.
    Never leaks secrets or absolute sensitive content.
    """
    return SetupPreflightResponse.model_validate(setup_preflight(settings))


@app.post(
    "/api/onboarding/complete",
    response_model=OnboardingCompletionResponse,
)
def complete_onboarding(request: Request) -> OnboardingCompletionResponse:
    """Complete onboarding only after a fresh trusted preflight succeeds."""
    preflight = setup_preflight(settings)
    if preflight.get("overall") != "ready":
        raise HTTPException(
            status_code=409,
            detail="setup preflight is still blocked",
        )

    if not auth_context.enabled:
        repo.today(workspace_id=LEGACY_WORKSPACE_ID)
        return OnboardingCompletionResponse(
            workspace_id=LEGACY_WORKSPACE_ID,
            completed=True,
        )

    principal = getattr(request.state, "principal", None)
    if principal is None or not principal.user_id or not principal.workspace_id:
        raise HTTPException(status_code=403, detail="workspace context is required")

    # The first workbench visit expects today's Episode to exist. Create it
    # before recording the corresponding onboarding step; repo.today() is
    # idempotent for the workspace/date pair, so retries stay safe.
    repo.today(workspace_id=principal.workspace_id)

    try:
        auth_context.complete_onboarding(
            principal.user_id,
            principal.workspace_id,
            principal.access_token or "",
        )
    except OnboardingStateError as exc:
        raise HTTPException(
            status_code=503,
            detail="unable to persist onboarding state",
        ) from exc

    return OnboardingCompletionResponse(
        workspace_id=principal.workspace_id,
        completed=True,
    )


@app.get("/api/settings/runtime", response_model=RuntimeSettingsResponse)
def get_runtime_settings() -> RuntimeSettingsResponse:
    return RuntimeSettingsResponse.model_validate(settings.safe_runtime_settings())


@app.post("/api/settings/runtime", response_model=RuntimeSettingsResponse)
def update_runtime_settings(
    request: Request,
    payload: RuntimeSettingsUpdateRequest,
) -> RuntimeSettingsResponse:
    client_host = request.client.host if request.client else None
    if not is_local_client_host(client_host):
        raise HTTPException(
            status_code=403,
            detail="runtime settings can only be updated from a local client",
        )
    apply_runtime_settings_update(
        runtime_env_path,
        payload.model_dump(),
        _reload_runtime_dependencies,
    )
    return RuntimeSettingsResponse.model_validate(settings.safe_runtime_settings())



@app.get("/api/is_setup")
async def is_setup():
    """Check whether the minimum backend configuration is ready."""
    current_settings = Settings.from_env()
    preflight = setup_preflight(current_settings)
    missing = [
        str(check["id"])
        for check in preflight["checks"]
        if check["status"] in {"missing", "invalid"}
        and check["required"]
    ]
    configured = preflight["overall"] == "ready"
    return {
        "configured": configured,
        "missing": missing,
        "next_step": "/welcome" if missing else "",
    }


def _reload_runtime_dependencies() -> None:
    global settings, repo, source_repository, model_catalog, model_installer, visual_semantic_provider, voice_clone_provider, production_repo, montage_repo, montage_planner, montage_renderer, narration_generator, voice_preview_service, avatar_preview_service, vault, video_inbox, video_processor, video_pipeline, draft_workspace, source_tracker, service, workflow, auth_context

    settings = Settings.from_env()
    configure_process_temporary_directory(settings)
    set_auth_enabled(settings.auth_enabled)
    auth_context = SupabaseAuthContext(settings)
    repo = EpisodeRepository(database_path=settings.database_path)
    source_repository = SourceRepository(settings.database_path)
    model_catalog = ModelCatalog(
        settings.model_root_path,
        settings.model_runtime_python_path,
        settings.visual_model_worker_path,
        settings.voice_model_worker_path,
    )
    model_installer = ModelInstaller(
        model_catalog,
        on_complete=lambda: _reload_runtime_dependencies(),
    )
    visual_semantic_provider = visual_semantic_provider_from_catalog(model_catalog)
    voice_clone_provider = voice_clone_provider_from_catalog(model_catalog)
    production_repo = ProductionRepository(
        database_path=settings.database_path,
        video_library_path=settings.video_inbox_path,
        visual_provider=visual_semantic_provider,
    )
    montage_repo = MontageRepository(settings.database_path)
    montage_planner = MontagePlanner(
        semantic_provider=visual_semantic_provider,
        visual_feature_loader=production_repo.get_visual_feature,
    )
    montage_renderer = MontageRenderer(settings.video_output_path)
    narration_generator = NarrationGenerator(settings.video_output_path, voice_clone_provider)
    voice_preview_service = VoicePreviewService(settings.video_output_path, voice_clone_provider)
    avatar_preview_service = AvatarPreviewService(
        settings.video_output_path,
        UnavailableAvatarAndLipSyncProvider(),
    )
    vault = ObsidianVault(settings.obsidian_vault_path)
    video_inbox = VideoInbox(settings.video_inbox_path)
    video_processor = FfmpegVideoProcessor(settings)
    video_pipeline = video_processing_pipeline_from_settings(
        settings,
        processor=video_processor,
    )
    draft_workspace = DraftWorkspace(settings)
    source_tracker = SourceTracker(video_inbox)
    service = EpisodeService(
        script_generator=script_generator_from_settings(settings),
        topic_generator=topic_generator_from_settings(settings, vault),
    )
    workflow = LangGraphEpisodeWorkflow(service)


@app.get("/api/knowledge/notes", response_model=list[KnowledgeNoteResponse])
def get_knowledge_notes(q: str | None = None, limit: int = 40) -> list[KnowledgeNoteResponse]:
    notes = vault.recent_notes(limit=200)
    if q:
        query = q.lower()
        notes = [
            note
            for note in notes
            if query in note.title.lower()
            or query in note.ref.lower()
            or query in note.excerpt.lower()
        ]
    return [
        KnowledgeNoteResponse.model_validate(note, from_attributes=True)
        for note in notes[: max(1, min(limit, 100))]
    ]


def _source_connector_response(connector: SourceConnector) -> SourceConnectorResponse:
    return SourceConnectorResponse.model_validate(connector, from_attributes=True)


def _source_item_response(item: SourceItem) -> SourceItemDetailResponse:
    return SourceItemDetailResponse.model_validate(item, from_attributes=True)


def _knowledge_item_response(item: KnowledgeItem) -> KnowledgeItemResponse:
    return KnowledgeItemResponse.model_validate(item, from_attributes=True)


def _source_run_response(run) -> SourceRunEvidenceResponse:
    return SourceRunEvidenceResponse.model_validate(run, from_attributes=True)


def _manual_connector(workspace_id: str) -> SourceConnector:
    connector_id = f"manual:{workspace_id}"
    try:
        return source_repository.get_connector(workspace_id, connector_id)
    except KeyError:
        return source_repository.upsert_connector(
            SourceConnector(
                id=connector_id,
                workspace_id=workspace_id,
                platform="manual",
                protocol="local",
                account_or_rule="user-provided content",
                frequency="on_demand",
                permissions=("write:source_items",),
            )
        )


def _compat_connector(
    workspace_id: str,
    key: str,
    *,
    platform: str,
    protocol: str,
    account_or_rule: str,
) -> SourceConnector:
    connector_id = f"compat:{key}:{workspace_id}"
    try:
        existing = source_repository.get_connector(workspace_id, connector_id)
        if existing.account_or_rule != account_or_rule:
            return source_repository.upsert_connector(
                replace(existing, account_or_rule=account_or_rule)
            )
        return existing
    except KeyError:
        return source_repository.upsert_connector(
            SourceConnector(
                id=connector_id,
                workspace_id=workspace_id,
                platform=platform,
                protocol=protocol,
                account_or_rule=account_or_rule,
                frequency="on_demand",
                permissions=("read:source_items",),
            )
        )


def _connector_urls(connector: SourceConnector) -> list[str]:
    return [
        value.strip()
        for value in connector.account_or_rule.replace(",", "\n").splitlines()
        if value.strip() and value.strip() != _EXTERNAL_URLS_CONFIGURATION_REQUIRED
    ]


def _source_fetcher(
    connector: SourceConnector,
    workspace_id: str,
):
    platform = connector.platform.casefold().replace(" ", "_")

    def fetch(cursor: str | None) -> tuple[list[SourceItem], str | None]:
        if platform in {"hermes_radar", "hermes"}:
            topic_items = HermesRadarSourceClient(settings.hermes_radar_path).fetch_items()
            return (
                [item.to_source_item(workspace_id, connector.id) for item in topic_items],
                cursor,
            )
        if platform in {"external_urls", "external_url", "web"}:
            urls = _connector_urls(connector)
            if not urls:
                raise SourceBoundaryError(
                    "configuration_required",
                    "external URL connector has no URLs configured",
                    repair_action="Add one or more allowed HTTP(S) URLs to the connector rule.",
                )
            topic_items = HtmlPageSourceClient(urls).fetch_items()
            return (
                [item.to_source_item(workspace_id, connector.id) for item in topic_items],
                cursor,
            )
        if platform in {"product_hunt", "producthunt"}:
            topic_items = ProductHuntClient(settings).fetch_source_items()
            return (
                [item.to_source_item(workspace_id, connector.id) for item in topic_items],
                cursor,
            )
        if platform == "manual":
            return source_repository.list_source_items(workspace_id, connector.id), cursor
        raise SourceBoundaryError(
            "capability_unavailable",
            f"{connector.platform} connector is configured but not implemented",
            impact="No external items were imported.",
            repair_action="Use a supported connector or wait for this platform capability.",
        )

    return fetch


def _source_failure_http_exception(failure: SourceSyncFailedError) -> HTTPException:
    run = failure.run
    connector = source_repository.get_connector(run.workspace_id, run.connector_id)
    status_code = {
        "authentication_required": 401,
        "access_denied": 403,
        "rate_limited": 429,
        "capability_unavailable": 501,
        "configuration_required": 409,
    }.get(
        connector.connection_status,
        502,
    )
    return HTTPException(
        status_code=status_code,
        detail={
            "error": connector.connection_status,
            "detail": run.reason,
            "impact": run.impact,
            "repair_action": run.repair_action,
            "retryable": run.retryable,
            "run": _source_run_response(run).model_dump(mode="json"),
        },
    )


def _sync_connector(
    workspace_id: str,
    connector: SourceConnector,
):
    try:
        return sync_source_connector(
            source_repository,
            workspace_id,
            connector.id,
            _source_fetcher(connector, workspace_id),
        )
    except SourceSyncFailedError as exc:
        raise _source_failure_http_exception(exc) from exc


def _legacy_source_items_response(
    workspace_id: str,
    items: list[SourceItem],
) -> SourceItemsResponse:
    source_items = [
        SourceItemResponse(
            source_type=item.source_label,
            title=item.title,
            ref=item.source_url or item.file_reference or item.id,
            excerpt=item.body or item.subtitles or "",
        )
        for item in items
    ]
    return SourceItemsResponse(
        items=source_items,
        total_count=len(source_items),
        source_counts=dict(Counter(item.source_type for item in source_items)),
    )


@app.get("/api/source-connectors", response_model=SourceConnectorsResponse)
def list_source_connectors(request: Request) -> SourceConnectorsResponse:
    connectors = source_repository.list_connectors(current_workspace_id(request))
    return SourceConnectorsResponse(
        items=[_source_connector_response(item) for item in connectors],
        total_count=len(connectors),
    )


@app.post("/api/source-connectors", response_model=SourceConnectorResponse)
def create_source_connector(
    request: Request,
    payload: SourceConnectorCreateRequest,
) -> SourceConnectorResponse:
    workspace_id = current_workspace_id(request)
    try:
        connector = source_repository.upsert_connector(
            SourceConnector(
                id=f"connector_{uuid4().hex}",
                workspace_id=workspace_id,
                platform=payload.platform,
                protocol=payload.protocol,
                account_or_rule=payload.account_or_rule,
                frequency=payload.frequency,
                permissions=tuple(item.strip() for item in payload.permissions if item.strip()),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _source_connector_response(connector)


@app.get("/api/source-connectors/{connector_id}", response_model=SourceConnectorResponse)
def get_source_connector(
    connector_id: str,
    request: Request,
) -> SourceConnectorResponse:
    try:
        connector = source_repository.get_connector(
            current_workspace_id(request), connector_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source connector not found") from exc
    return _source_connector_response(connector)


@app.get(
    "/api/source-connectors/{connector_id}/runs",
    response_model=SourceRunEvidencesResponse,
)
def list_source_connector_runs(
    connector_id: str,
    request: Request,
) -> SourceRunEvidencesResponse:
    workspace_id = current_workspace_id(request)
    try:
        source_repository.get_connector(workspace_id, connector_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source connector not found") from exc
    runs = source_repository.list_runs(workspace_id, connector_id)
    return SourceRunEvidencesResponse(
        items=[_source_run_response(run) for run in runs],
        total_count=len(runs),
    )


@app.get(
    "/api/source-connectors/{connector_id}/runs/latest",
    response_model=SourceRunEvidenceResponse,
)
def get_latest_source_connector_run(
    connector_id: str,
    request: Request,
) -> SourceRunEvidenceResponse:
    workspace_id = current_workspace_id(request)
    try:
        source_repository.get_connector(workspace_id, connector_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source connector not found") from exc
    runs = source_repository.list_runs(workspace_id, connector_id)
    if not runs:
        raise HTTPException(status_code=404, detail="source run not found")
    return _source_run_response(runs[0])


@app.get(
    "/api/source-connectors/{connector_id}/runs/{run_id}",
    response_model=SourceRunEvidenceResponse,
)
def get_source_connector_run(
    connector_id: str,
    run_id: str,
    request: Request,
) -> SourceRunEvidenceResponse:
    workspace_id = current_workspace_id(request)
    try:
        source_repository.get_connector(workspace_id, connector_id)
        run = source_repository.get_run(workspace_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source run not found") from exc
    if run.connector_id != connector_id:
        raise HTTPException(status_code=404, detail="source run not found")
    return _source_run_response(run)


@app.post(
    "/api/source-connectors/{connector_id}/sync",
    response_model=SourceSyncResponse,
)
def sync_source_connector_endpoint(
    connector_id: str,
    request: Request,
) -> SourceSyncResponse:
    workspace_id = current_workspace_id(request)
    try:
        connector = source_repository.get_connector(workspace_id, connector_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source connector not found") from exc
    run = _sync_connector(workspace_id, connector)
    return SourceSyncResponse(
        connector=_source_connector_response(
            source_repository.get_connector(workspace_id, connector_id)
        ),
        run=_source_run_response(run),
    )


@app.get("/api/source-items", response_model=SourceItemDetailsResponse)
def list_persisted_source_items(
    request: Request,
    connector_id: str | None = None,
) -> SourceItemDetailsResponse:
    items = source_repository.list_source_items(
        current_workspace_id(request), connector_id
    )
    return SourceItemDetailsResponse(
        items=[_source_item_response(item) for item in items],
        total_count=len(items),
    )


@app.post("/api/source-items/manual", response_model=SourceItemDetailResponse)
def create_manual_source_item(
    request: Request,
    payload: ManualSourceItemRequest,
) -> SourceItemDetailResponse:
    workspace_id = current_workspace_id(request)
    connector = _manual_connector(workspace_id)
    source_url = payload.source_url.strip() if payload.source_url else None
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(
                status_code=422,
                detail="manual source_url must use http or https",
            )
    try:
        item = source_repository.save_source_items(
            workspace_id,
            [
                SourceItem(
                    id=f"source_{uuid4().hex}",
                    workspace_id=workspace_id,
                    connector_id=connector.id,
                    source_label="manual",
                    source_url=source_url,
                    content_type=payload.content_type.strip(),
                    author=payload.author.strip() if payload.author else None,
                    published_at=payload.published_at,
                    body=payload.body,
                    subtitles=payload.subtitles,
                    media_references=tuple(payload.media_references),
                    license=payload.license.strip(),
                    title=payload.title.strip(),
                    file_reference=(
                        payload.file_reference.strip()
                        if payload.file_reference
                        else None
                    ),
                )
            ],
        )[0]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _source_item_response(item)


@app.post(
    "/api/source-items/{source_item_id}/save-to-knowledge",
    response_model=KnowledgeItemResponse,
)
def save_source_item_to_knowledge(
    source_item_id: str,
    request: Request,
    payload: SaveSourceItemToKnowledgeRequest,
) -> KnowledgeItemResponse:
    try:
        item = source_repository.save_source_item_to_knowledge(
            current_workspace_id(request),
            source_item_id,
            ownership=payload.ownership.strip(),
            authorization=payload.authorization.strip(),
            version=payload.version.strip(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _knowledge_item_response(item)


@app.get("/api/knowledge-items", response_model=KnowledgeItemsResponse)
def list_knowledge_items(request: Request) -> KnowledgeItemsResponse:
    items = source_repository.list_knowledge_items(current_workspace_id(request))
    return KnowledgeItemsResponse(
        items=[_knowledge_item_response(item) for item in items],
        total_count=len(items),
    )


@app.get("/api/sources/items", response_model=SourceItemsResponse)
def get_source_items(request: Request) -> SourceItemsResponse:
    workspace_id = current_workspace_id(request)
    return _legacy_source_items_response(
        workspace_id, source_repository.list_source_items(workspace_id)
    )


@app.post("/api/sources/sync", response_model=SourceItemsResponse)
def sync_source_items(request: Request) -> SourceItemsResponse:
    workspace_id = current_workspace_id(request)
    connectors = [
        _compat_connector(
            workspace_id,
            "hermes_radar",
            platform="hermes_radar",
            protocol="local_file",
            account_or_rule=str(settings.hermes_radar_path or ""),
        )
    ]
    if settings.topic_source_urls:
        connectors.append(
            _compat_connector(
                workspace_id,
                "external_urls",
                platform="external_urls",
                protocol="https",
                account_or_rule="\n".join(settings.topic_source_urls),
            )
        )
    for connector in connectors:
        _sync_connector(workspace_id, connector)
    return _legacy_source_items_response(
        workspace_id, source_repository.list_source_items(workspace_id)
    )


@app.post("/api/sources/links", response_model=SourceItemsResponse)
def ingest_manual_link(
    request: Request,
    payload: ManualLinkRequest,
) -> SourceItemsResponse:
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    workspace_id = current_workspace_id(request)
    connector = _manual_connector(workspace_id)
    try:
        topic_items = HtmlPageSourceClient([url]).fetch_items()
    except ExternalSourceResponseError as exc:
        failure = SourceSyncFailedError(
            source_repository.record_sync_failure(
                workspace_id,
                connector.id,
                reason=str(exc),
                impact=exc.impact,
                repair_action=exc.repair_action,
                retryable=exc.retryable,
                connection_status=exc.kind,
            )
        )
        raise _source_failure_http_exception(failure) from exc
    source_repository.save_source_items(
        workspace_id,
        [item.to_source_item(workspace_id, connector.id) for item in topic_items],
    )
    return _legacy_source_items_response(
        workspace_id,
        source_repository.list_source_items(workspace_id, connector.id),
    )


@app.get(
    "/api/sources/sync-status",
    response_model=SourceSyncStatusesResponse,
)
def get_source_sync_status(request: Request) -> SourceSyncStatusesResponse:
    workspace_id = current_workspace_id(request)
    statuses = []
    for connector in source_repository.list_connectors(workspace_id):
        runs = source_repository.list_runs(workspace_id, connector.id)
        latest = runs[0] if runs else None
        latest_success = next((run for run in runs if run.status == "succeeded"), None)
        latest_failure = next((run for run in runs if run.status == "failed"), None)
        statuses.append(
            {
                "source_type": connector.platform,
                "configured": connector.connection_status != "capability_unavailable",
                "last_sync_at": latest.finished_at if latest else None,
                "last_success_at": latest_success.finished_at if latest_success else None,
                "last_failure_at": latest_failure.finished_at if latest_failure else None,
                "error_message": latest_failure.reason if latest_failure else None,
            }
        )
    return SourceSyncStatusesResponse(
        statuses=[
            SourceSyncStatusResponse(**status)
            for status in statuses
        ],
    )


@app.get("/api/feedback/summary", response_model=FeedbackSummaryResponse)
def get_feedback_summary(request: Request) -> FeedbackSummaryResponse:
    ws = current_workspace_id(request)
    all_event_pairs = [
        (episode.id, event)
        for episode in repo.all(workspace_id=ws)
        for event in episode.feedback_events
    ]
    all_event_pairs = sorted(
        all_event_pairs,
        key=lambda item: item[1].created_at,
        reverse=True,
    )
    active_events = [event for _, event in all_event_pairs if event.active]
    counts = Counter(event.value for event in active_events)
    return FeedbackSummaryResponse(
        total_count=len(active_events),
        up_count=counts.get("up", 0),
        down_count=counts.get("down", 0),
        recent_events=[
            FeedbackEventResponse.model_validate(
                {**event.__dict__, "episode_id": episode_id}
            )
            for episode_id, event in all_event_pairs[:20]
        ],
    )


@app.get("/api/preferences", response_model=PreferencesResponse)
def get_preferences(request: Request) -> PreferencesResponse:
    ws = current_workspace_id(request)
    events = [
        event
        for episode in repo.all(workspace_id=ws)
        for event in episode.feedback_events
        if event.reason and event.active
    ]
    pref_state = PreferenceState.from_payload(
        load_preference_state_payload(workspace_id=ws)
    )
    memory = build_preference_memory(events, limit=20, preference_state=pref_state)
    preferences = [
        PreferenceItemResponse(
            label=label,
            signal=signal.signal,
            evidence_count=signal.evidence_count,
            confidence=signal.confidence,
            enabled=signal.enabled,
        )
        for label, signals in (
            ("偏好", memory.prefer),
            ("避开", memory.avoid),
        )
        for signal in signals
    ]
    return PreferencesResponse(
        total_feedback_count=len(events),
        preferences=preferences[:20],
    )


@app.patch(
    "/api/preferences/disable",
    response_model=PreferencesResponse,
)
def disable_preference(
    request: Request,
    body: PreferenceToggleRequest,
) -> PreferencesResponse:
    ws = current_workspace_id(request)
    pref_state = PreferenceState.from_payload(
        load_preference_state_payload(workspace_id=ws)
    )
    pref_state.disable(body.signal)
    save_preference_state_payload(pref_state.to_payload(), workspace_id=ws)
    return _rebuilt_preferences_response(pref_state, workspace_id=ws)


@app.patch(
    "/api/preferences/restore",
    response_model=PreferencesResponse,
)
def restore_preference(
    request: Request,
    body: PreferenceToggleRequest,
) -> PreferencesResponse:
    ws = current_workspace_id(request)
    pref_state = PreferenceState.from_payload(
        load_preference_state_payload(workspace_id=ws)
    )
    pref_state.restore(body.signal)
    save_preference_state_payload(pref_state.to_payload(), workspace_id=ws)
    return _rebuilt_preferences_response(pref_state, workspace_id=ws)


def _rebuilt_preferences_response(
    pref_state: PreferenceState,
    workspace_id: str,
) -> PreferencesResponse:
    events = [
        event
        for episode in repo.all(workspace_id=workspace_id)
        for event in episode.feedback_events
        if event.reason and event.active
    ]
    memory = build_preference_memory(events, limit=20, preference_state=pref_state)
    preferences = [
        PreferenceItemResponse(
            label=label,
            signal=signal.signal,
            evidence_count=signal.evidence_count,
            confidence=signal.confidence,
            enabled=signal.enabled,
        )
        for label, signals in (
            ("偏好", memory.prefer),
            ("避开", memory.avoid),
        )
        for signal in signals
    ]
    return PreferencesResponse(
        total_feedback_count=len(events),
        preferences=preferences[:20],
    )


@app.get("/api/videos/pending", response_model=list[PendingVideoResponse])
def get_pending_videos() -> list[PendingVideoResponse]:
    return [
        PendingVideoResponse.model_validate(video, from_attributes=True)
        for video in video_inbox.pending_videos()
    ]


@app.post("/api/videos/process", response_model=ProcessedVideoResponse)
def process_video(request: ProcessVideoRequest) -> ProcessedVideoResponse:
    _require_feature("process-video")
    try:
        if not request.file_name:
            raise ValueError("file_name is required")
        processed = video_pipeline.process(request.file_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProcessedVideoResponse.model_validate(processed, from_attributes=True)


@app.get("/api/videos/processed/{file_name}")
def get_processed_video(file_name: str) -> FileResponse:
    try:
        path = safe_processed_video_path(file_name, settings.video_output_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="processed video not found") from exc
    return FileResponse(path)


@app.get("/api/drafts/status", response_model=DraftStatusResponse)
def get_draft_status() -> DraftStatusResponse:
    return DraftStatusResponse(
        environment=DraftEnvironmentResponse.model_validate(
            draft_workspace.environment(),
            from_attributes=True,
        ),
        processed_videos=[
            DraftAssetResponse.model_validate(
                video,
                from_attributes=True,
            )
            for video in draft_workspace.processed_videos()
        ],
        packages=[
            DraftAssetResponse.model_validate(
                package,
                from_attributes=True,
            )
            for package in draft_workspace.packages()
        ],
    )


@app.post("/api/drafts/package", response_model=DraftPackageResponse)
def create_draft_package(
    request: Request,
    body: DraftPackageRequest,
) -> DraftPackageResponse:
    ws = current_workspace_id(request)
    try:
        package = draft_workspace.create_package_and_import_to_jianying(
            video_file_name=body.video_file_name,
            episode=repo.today(workspace_id=ws),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DraftPackageResponse.model_validate(package, from_attributes=True)


@app.post("/api/drafts/import", response_model=DraftImportResponse)
def import_draft_package(request: DraftImportRequest) -> DraftImportResponse:
    try:
        result = draft_workspace.import_package_to_jianying(request.package_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DraftImportResponse.model_validate(result, from_attributes=True)


def _pending_video_by_name(file_name: str):
    for video in video_inbox.pending_videos():
        if video.file_name == file_name:
            return video
    raise ValueError("pending video not found")


def _pending_videos_by_name(file_names: list[str]):
    pending_by_name = {
        video.file_name: video
        for video in video_inbox.pending_videos()
    }
    try:
        return [pending_by_name[file_name] for file_name in file_names]
    except KeyError as exc:
        raise ValueError("pending video not found") from exc


@app.get("/api/episodes/today", response_model=EpisodeResponse)
def get_today_episode(request: Request) -> EpisodeResponse:
    return episode_response(repo.today(workspace_id=current_workspace_id(request)))


@app.post("/api/episodes", response_model=EpisodeResponse)
def create_episode(request: Request) -> EpisodeResponse:
    return episode_response(
        repo.create(workspace_id=current_workspace_id(request))
    )


@app.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)
def get_episode(episode_id: str, request: Request) -> EpisodeResponse:
    return episode_response(get_episode_or_404(episode_id))


@app.post("/api/episodes/{episode_id}/content-template", response_model=EpisodeResponse)
def select_episode_content_template(
    episode_id: str,
    request: ContentTemplateSelectRequest,
) -> EpisodeResponse:
    try:
        get_content_template(request.template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="content template not found") from exc
    episode = get_episode_or_404(episode_id)
    episode.content_template_id = request.template_id
    return episode_response(repo.save(episode))


@app.get("/api/episodes/{episode_id}/retake-report", response_model=RetakeReportResponse)
def get_episode_retake_report(episode_id: str) -> RetakeReportResponse:
    return _read_retake_report(get_episode_or_404(episode_id))


@app.get("/api/episodes/{episode_id}/edit-plan", response_model=EditPlanResponse)
def get_episode_edit_plan(episode_id: str) -> EditPlanResponse:
    episode = recover_interrupted_video_job_if_needed(get_episode_or_404(episode_id))
    return EditPlanResponse.model_validate(
        build_edit_plan_workspace(episode, settings.video_output_path)
    )


@app.post(
    "/api/episodes/{episode_id}/edit-plan/segments/{segment_id}/decision",
    response_model=EditPlanResponse,
)
def update_episode_edit_plan_segment_decision(
    episode_id: str,
    segment_id: str,
    request: EditPlanSegmentDecisionRequest,
) -> EditPlanResponse:
    episode = recover_interrupted_video_job_if_needed(get_episode_or_404(episode_id))
    try:
        workspace = update_edit_plan_segment_decision(
            episode,
            settings.video_output_path,
            segment_id=segment_id,
            action=request.action,
            reason=request.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EditPlanResponse.model_validate(workspace)


@app.post(
    "/api/episodes/{episode_id}/edit-plan/segments/{segment_id}/revert",
    response_model=EditPlanResponse,
)
def revert_episode_edit_plan_segment_decision(
    episode_id: str,
    segment_id: str,
) -> EditPlanResponse:
    episode = recover_interrupted_video_job_if_needed(get_episode_or_404(episode_id))
    try:
        workspace = revert_edit_plan_segment_decision(
            episode,
            settings.video_output_path,
            segment_id=segment_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EditPlanResponse.model_validate(workspace)


@app.get("/api/retake-reports/latest", response_model=RetakeReportResponse)
def get_latest_retake_report() -> RetakeReportResponse:
    episodes_with_reports = sorted(
        (episode for episode in repo.all() if episode.video_dedup_report_path),
        key=lambda episode: episode.updated_at,
        reverse=True,
    )
    for episode in episodes_with_reports:
        try:
            return _read_retake_report(episode)
        except HTTPException:
            continue
    raise HTTPException(status_code=404, detail="retake report not found")


def _read_retake_report(episode: Episode) -> RetakeReportResponse:
    if not episode.video_dedup_report_path:
        raise HTTPException(status_code=404, detail="retake report not found")
    try:
        report_root = Path(settings.video_output_path).expanduser().resolve()
        report_path = Path(episode.video_dedup_report_path).expanduser().resolve()
        if not report_path.is_relative_to(report_root) or not report_path.is_file():
            raise HTTPException(status_code=404, detail="retake report not found")
        return RetakeReportResponse.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except HTTPException:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="retake report not found") from exc


@app.post("/api/episodes/{episode_id}/generate-topics", response_model=EpisodeResponse)
def generate_topics(episode_id: str) -> EpisodeResponse:
    try:
        episode = get_episode_or_404(episode_id)
        _require_feature("generate-topics")
        episode = workflow.run(
            "generate_topics",
            episode,
            feedback_history=feedback_history_for(episode_id),
        )
    except TopicGenerationConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TopicGenerationResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/regenerate-topics", response_model=EpisodeResponse)
def regenerate_topics(episode_id: str) -> EpisodeResponse:
    try:
        episode = get_episode_or_404(episode_id)
        _require_feature("generate-topics")
        episode = workflow.run(
            "regenerate_topics",
            episode,
            feedback_history=feedback_history_for(episode_id),
        )
    except TopicGenerationConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TopicGenerationResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/approve-topic", response_model=EpisodeResponse)
def approve_topic(episode_id: str, request: ApproveTopicRequest) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "approve_topic",
            get_episode_or_404(episode_id),
            topic_id=request.topic_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/generate-script", response_model=EpisodeResponse)
def generate_script(
    episode_id: str,
    request: ScriptGenerateRequest | None = None,
) -> EpisodeResponse:
    episode = get_episode_or_404(episode_id)
    if episode.selected_topic_id is None:
        raise HTTPException(status_code=400, detail="approve a topic first")
    try:
        episode = workflow.run(
            "generate_script",
            episode,
            feedback_history=feedback_history_for(episode_id),
            target_duration_seconds=(
                request.target_duration_seconds if request is not None else None
            ),
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/script", response_model=EpisodeResponse)
def update_episode_script(
    episode_id: str,
    request: ScriptUpdateRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "update_script",
            get_episode_or_404(episode_id),
            script=_script_package_from_response(request.script),
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/script/regenerate", response_model=EpisodeResponse)
def regenerate_episode_script(
    episode_id: str,
    request: ScriptRegenerateRequest,
) -> EpisodeResponse:
    try:
        get_episode_or_404(episode_id)
        _require_feature("regenerate-script")
        episode = workflow.run(
            "regenerate_script",
            get_episode_or_404(episode_id),
            feedback_history=feedback_history_for(episode_id),
            reason=request.reason,
            target_duration_seconds=request.target_duration_seconds,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post(
    "/api/episodes/{episode_id}/script/sections/{section_id}/regenerate",
    response_model=EpisodeResponse,
)
def regenerate_episode_script_section(
    episode_id: str,
    section_id: str,
    request: ScriptSectionRegenerateRequest,
) -> EpisodeResponse:
    try:
        get_episode_or_404(episode_id)
        _require_feature("regenerate-section")
        episode = workflow.run(
            "regenerate_script_section",
            get_episode_or_404(episode_id),
            section_id=section_id,
            feedback_history=feedback_history_for(episode_id),
            reason=request.reason,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/script/revert", response_model=EpisodeResponse)
def revert_episode_script_version(
    episode_id: str,
    request: ScriptVersionRevertRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "revert_script_version",
            get_episode_or_404(episode_id),
            version_id=request.version_id,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


def _legacy_voice_command_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_voice_command_retired",
            "reason": "旧的 Episode 对话指令已退役，不能再生成选题、脚本或旧剪辑草稿。",
            "impact": "本次请求不会修改创作内容、剪辑文件或任务状态。",
            "repair_action": "请在创作任务页手动创建任务；目标自动搭建制作方案将在新能力接入后开放。",
        },
    )


@app.post("/api/episodes/{episode_id}/voice-command/preview")
def preview_voice_command(episode_id: str) -> None:
    _legacy_voice_command_retired()


@app.post("/api/episodes/{episode_id}/voice-command/execute")
def execute_voice_command(episode_id: str) -> None:
    _legacy_voice_command_retired()


@app.post("/api/voice/transcribe", response_model=VoiceTranscriptionResponse)
async def voice_transcribe(
    audio: UploadFile = File(...),  # noqa: B008
) -> VoiceTranscriptionResponse:
    """Upload an audio file (webm) and transcribe via ASR pipeline."""
    try:
        content = await audio.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {exc}") from exc

    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file")

    try:
        result = transcribe_audio_bytes(content, settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VoiceTranscriptionResponse(
        transcript=result["transcript"],
        provider=result["provider"],
        segments=result["segments"],
    )


# ── Creator Profile ──────────────────────────────────────────────


@app.get("/api/creator-profile", response_model=CreatorProfileResponse)
def get_creator_profile_endpoint(request: Request) -> CreatorProfileResponse:
    """Return the current creator profile, creating a default one if needed."""
    profile = get_or_create_profile(
        settings.database_path,
        workspace_id=current_workspace_id(request),
    )
    return CreatorProfileResponse(
        id=profile.id,
        common_expressions=profile.common_expressions,
        banned_words=profile.banned_words,
        style_preferences=profile.style_preferences,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@app.patch("/api/creator-profile", response_model=CreatorProfileResponse)
def update_creator_profile_endpoint(
    body: CreatorProfileUpdate,
    request: Request,
) -> CreatorProfileResponse:
    """Merge update the creator profile. Only provided fields are changed."""
    ws = current_workspace_id(request)
    profile = get_or_create_profile(settings.database_path, workspace_id=ws)

    if body.common_expressions is not None:
        profile.common_expressions = body.common_expressions
    if body.banned_words is not None:
        profile.banned_words = body.banned_words
    if body.style_preferences is not None:
        profile.style_preferences = body.style_preferences

    profile = upsert_creator_profile(profile, settings.database_path, workspace_id=ws)
    return CreatorProfileResponse(
        id=profile.id,
        common_expressions=profile.common_expressions,
        banned_words=profile.banned_words,
        style_preferences=profile.style_preferences,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@app.post("/api/creator-profile/learn", response_model=CreatorProfileLearnResult)
def learn_creator_profile_endpoint(request: Request) -> CreatorProfileLearnResult:
    """Analyze historical episode scripts and feedback to learn creator habits.

    Extracts frequently-used expressions from script content, identifies
    negatively-received patterns from feedback, and infers style preferences.
    """
    ws = current_workspace_id(request)
    profile = get_or_create_profile(settings.database_path, workspace_id=ws)
    episodes = repo.all(workspace_id=ws)

    # ── Collect text from active script versions ──
    all_script_text: list[str] = []
    all_hooks: list[str] = []
    for ep in episodes:
        for sv in ep.script_versions:
            if not sv.active:
                continue
            for section in sv.script.sections:
                all_script_text.append(section.content)
            all_hooks.append(sv.script.hook)

    # ── Extract candidate expressions via n-gram frequency ──
    new_expressions: list[str] = []
    if all_script_text:
        combined = "。".join(all_script_text)
        new_expressions = _extract_frequent_phrases(
            combined,
            existing=set(profile.common_expressions),
            min_len=2,
            max_len=5,
            min_count=3,
        )

    # ── Infer banned words from persistent negative feedback ──
    new_banned: list[str] = []
    down_reasons: list[str] = []
    for ep in episodes:
        for fe in ep.feedback_events:
            if fe.value and fe.value.value == "down" and fe.reason:
                down_reasons.append(fe.reason)
    if down_reasons:
        new_banned = _extract_frequent_phrases(
            "。".join(down_reasons),
            existing=set(profile.banned_words),
            min_len=2,
            max_len=4,
            min_count=2,
        )

    # ── Infer style preferences from hooks ──
    style_changes: dict[str, str] = {}
    if all_hooks:
        question_hooks = sum(1 for h in all_hooks if "？" in h or "?" in h)
        exclaim_hooks = sum(1 for h in all_hooks if "！" in h or "!" in h)
        total = len(all_hooks)
        if question_hooks / total >= 0.4:
            style_changes["hook_style"] = "question"
        elif exclaim_hooks / total >= 0.3:
            style_changes["hook_style"] = "exclamation"
        else:
            style_changes["hook_style"] = "statement"

    # ── Update profile ──
    if new_expressions:
        existing_expr = set(profile.common_expressions)
        for expr in new_expressions:
            if expr not in existing_expr:
                profile.common_expressions.append(expr)

    if new_banned:
        existing_banned = set(profile.banned_words)
        for word in new_banned:
            if word not in existing_banned:
                profile.banned_words.append(word)

    if style_changes:
        profile.style_preferences.update(style_changes)

    profile = upsert_creator_profile(
        profile,
        settings.database_path,
        workspace_id=ws,
    )

    return CreatorProfileLearnResult(
        profile=CreatorProfileResponse(
            id=profile.id,
            common_expressions=profile.common_expressions,
            banned_words=profile.banned_words,
            style_preferences=profile.style_preferences,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        ),
        summary={
            "new_expressions": new_expressions,
            "new_banned_words": new_banned,
            "style_changes": style_changes,
        },
    )


def _extract_frequent_phrases(
    text: str,
    *,
    existing: set[str],
    min_len: int = 2,
    max_len: int = 5,
    min_count: int = 3,
) -> list[str]:
    """Extract frequent Chinese character n-grams from text.

    Uses substring slicing (not tokenization) for CJK text.
    Filters very short or already-known phrases.
    """
    from collections import Counter

    # Keep only Chinese characters and common punctuation for n-gram extraction
    cleaned: list[str] = []
    for ch in text:
        if "一" <= ch <= "鿿" or "　" <= ch <= "〿" or "＀" <= ch <= "￯":
            cleaned.append(ch)

    if not cleaned:
        return []

    counter: Counter[str] = Counter()
    chars = "".join(cleaned)
    for n in range(min_len, max_len + 1):
        for i in range(len(chars) - n + 1):
            ngram = chars[i : i + n]
            # Skip phrases that are just punctuation
            if all("一" <= c <= "鿿" for c in ngram):
                counter[ngram] += 1

    result: list[str] = []
    for phrase, count in counter.most_common(50):
        if count < min_count:
            continue
        if phrase in existing:
            continue
        # Deduplicate: skip if a longer phrase contains this one
        if any(phrase != other and phrase in other for other in result):
            continue
        result.append(phrase)
        if len(result) >= 10:
            break

    return result


@app.post("/api/episodes/{episode_id}/approve-script", response_model=EpisodeResponse)
def approve_script(episode_id: str) -> EpisodeResponse:
    try:
        episode = workflow.run("approve_script", get_episode_or_404(episode_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/upload-video", response_model=EpisodeResponse)
def upload_video(episode_id: str, request: UploadVideoRequest) -> EpisodeResponse:
    try:
        video = _pending_video_by_name(request.file_name)
        episode = workflow.run(
            "bind_video",
            get_episode_or_404(episode_id),
            video=video,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/process-video", response_model=EpisodeResponse)
def process_episode_video(
    episode_id: str,
    request: ProcessVideoRequest,
) -> EpisodeResponse:
    episode = get_episode_or_404(episode_id)
    _require_feature("process-video")
    file_name = request.file_name or episode.video_source_file_name
    if not file_name:
        raise HTTPException(status_code=400, detail="bind or provide a video first")
    try:
        processed = video_pipeline.process(file_name)
        episode = workflow.run("bind_processed_video", episode, video=processed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/process-ad-hoc-video", response_model=EpisodeResponse)
def process_ad_hoc_episode_video(
    episode_id: str,
    request: Request,
    body: ProcessVideoRequest,
) -> EpisodeResponse:
    if not body.file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    if video_job_runner.is_active(episode_id):
        return episode_response(get_episode_or_404(episode_id))
    ws = current_workspace_id(request)
    try:
        episode = workflow.run(
            "bind_ad_hoc_video",
            get_episode_or_404(episode_id),
            video=_pending_video_by_name(body.file_name),
        )
        _start_video_job(episode)
        episode = repo.save(episode)
        video_job_runner.submit(
            episode_id,
            lambda: _complete_ad_hoc_episode_video(episode_id, body.file_name),
            workspace_id=ws,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.get(episode_id))


@app.post(
    "/api/episodes/{episode_id}/process-ad-hoc-video-batch",
    response_model=EpisodeResponse,
)
def process_ad_hoc_episode_video_batch(
    episode_id: str,
    request: Request,
    body: ProcessVideoBatchRequest,
) -> EpisodeResponse:
    if video_job_runner.is_active(episode_id):
        return episode_response(get_episode_or_404(episode_id))
    ws = current_workspace_id(request)
    try:
        videos = _pending_videos_by_name(body.file_names)
        episode = workflow.run(
            "bind_ad_hoc_video_batch",
            get_episode_or_404(episode_id),
            videos=videos,
        )
        _start_video_job(episode)
        episode = repo.save(episode)
        video_job_runner.submit(
            episode_id,
            lambda: _complete_ad_hoc_episode_video_batch(
                episode_id,
                body.file_names,
            ),
            workspace_id=ws,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.get(episode_id))


def _complete_ad_hoc_episode_video(episode_id: str, file_name: str) -> None:
    try:
        _mark_video_job_stage(
            episode_id,
            VideoJobStatus.PROCESSING,
            "正在生成精剪视频",
        )
        processed = video_pipeline.process(file_name)
        _mark_video_job_stage(
            episode_id,
            VideoJobStatus.DRAFTING,
            "正在生成草稿素材包",
        )
        _bind_processed_video_and_draft(episode_id, processed)
        _mark_video_job_completed(episode_id)
    except Exception as exc:
        _mark_video_job_failed(episode_id, exc)
        logger.exception("ad hoc video processing failed for %s", episode_id)


def _complete_ad_hoc_episode_video_batch(
    episode_id: str,
    file_names: list[str],
) -> None:
    try:
        _mark_video_job_stage(
            episode_id,
            VideoJobStatus.PROCESSING,
            "正在合并并精剪视频",
        )
        processed = video_pipeline.process_batch(file_names)
        _mark_video_job_stage(
            episode_id,
            VideoJobStatus.DRAFTING,
            "正在生成草稿素材包",
        )
        _bind_processed_video_and_draft(episode_id, processed)
        _mark_video_job_completed(episode_id)
    except Exception as exc:
        _mark_video_job_failed(episode_id, exc)
        logger.exception("ad hoc video batch processing failed for %s", episode_id)


def _bind_processed_video_and_draft(episode_id: str, processed) -> None:
    episode = workflow.run(
        "bind_processed_video",
        get_episode_or_404(episode_id),
        video=processed,
    )
    package = draft_workspace.create_package_and_import_to_jianying(
        video_file_name=processed.file_name,
        episode=episode,
    )
    episode = workflow.run(
        "bind_draft_package",
        episode,
        package_name=package.package_name,
        package_path=package.path,
    )
    repo.save(episode)


def _start_video_job(episode: Episode) -> None:
    episode.status = EpisodeStatus.VIDEO_PROCESSING
    episode.video_job_status = VideoJobStatus.PROCESSING
    episode.video_job_message = "后台正在处理视频"
    episode.video_job_error = None
    episode.video_job_started_at = utc_now()
    episode.video_job_finished_at = None


def _mark_video_job_stage(
    episode_id: str,
    status: VideoJobStatus,
    message: str,
) -> None:
    episode = get_episode_or_404(episode_id)
    episode.video_job_status = status
    episode.video_job_message = message
    episode.video_job_error = None
    if episode.video_job_started_at is None:
        episode.video_job_started_at = utc_now()
    repo.save(episode)


def _mark_video_job_completed(episode_id: str) -> None:
    episode = get_episode_or_404(episode_id)
    episode.video_job_status = VideoJobStatus.COMPLETED
    episode.video_job_message = "视频已处理，并已自动尝试导入剪映草稿箱"
    episode.video_job_error = None
    episode.video_job_finished_at = utc_now()
    repo.save(episode)


def _mark_video_job_failed(episode_id: str, error: Exception) -> None:
    episode = get_episode_or_404(episode_id)
    episode.status = EpisodeStatus.FAILED
    episode.video_job_status = VideoJobStatus.FAILED
    episode.video_job_message = "视频处理失败"
    episode.video_job_error = f"{type(error).__name__}: {error}"
    episode.video_job_finished_at = utc_now()
    repo.save(episode)


@app.post("/api/episodes/{episode_id}/create-draft", response_model=EpisodeResponse)
def create_episode_draft(episode_id: str) -> EpisodeResponse:
    episode = get_episode_or_404(episode_id)
    if not episode.video_processed_file_name:
        raise HTTPException(status_code=400, detail="process a video first")
    try:
        confirmed_video = apply_confirmed_edit_plan(
            episode,
            draft_workspace.video_output_path,
            runner=edit_plan_apply_runner,
        )
        if confirmed_video is not None:
            episode.video_processed_file_name = confirmed_video["file_name"]
            episode.video_processed_path = confirmed_video["path"]
            episode = repo.save(episode)
        package = draft_workspace.create_package_and_import_to_jianying(
            video_file_name=episode.video_processed_file_name,
            episode=episode,
        )
        episode = workflow.run(
            "bind_draft_package",
            episode,
            package_name=package.package_name,
            package_path=package.path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/mark-published", response_model=EpisodeResponse)
def mark_episode_published(
    episode_id: str,
    request: MarkPublishedRequest,
) -> EpisodeResponse:
    episode = workflow.run(
        "mark_published",
        get_episode_or_404(episode_id),
        publish_url=request.publish_url,
        platform=request.platform,
        play_count=request.play_count,
        like_count=request.like_count,
        comment_count=request.comment_count,
        notes=request.notes,
    )
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/feedback", response_model=EpisodeResponse)
def apply_feedback(episode_id: str, request: FeedbackRequest) -> EpisodeResponse:
    try:
        episode = service.apply_feedback(
            get_episode_or_404(episode_id),
            request.target_type,
            request.target_id,
            request.value,
            request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/feedback/revert", response_model=EpisodeResponse)
def revert_episode_feedback(
    episode_id: str,
    request: FeedbackRevertRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "revert_feedback",
            get_episode_or_404(episode_id),
            feedback_event_id=request.feedback_event_id,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


# ── Server entry point (for pyinstaller onefile binary) ─────────────
if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


@app.get(
    "/api/episodes/{episode_id}/production-chain",
    response_model=ProductionChainResponse,
)
def get_episode_production_chain(episode_id: str) -> ProductionChainResponse:
    episode = get_episode_or_404(episode_id)
    return ProductionChainResponse(
        episode_id=episode.id,
        intake_mode=episode.intake_mode,
        chain=[
            {
                "stage": step.stage,
                "status": step.status,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "detail": step.detail,
            }
            for step in episode.video_production_chain
        ],
    )


@app.get(
    "/api/episodes/{episode_id}/source-file-states",
    response_model=SourceFileStatesResponse,
)
def get_episode_source_file_states(episode_id: str) -> SourceFileStatesResponse:
    episode = get_episode_or_404(episode_id)
    return SourceFileStatesResponse(
        episode_id=episode.id,
        files=[
            {"file_name": name, "state": state}
            for name, state in episode.source_file_states.items()
        ],
    )


@app.post(
    "/api/episodes/{episode_id}/archive-sources",
    response_model=ArchiveSourcesResponse,
)
def archive_episode_sources(
    episode_id: str,
    request: ArchiveSourcesRequest,
) -> ArchiveSourcesResponse:
    episode = get_episode_or_404(episode_id)
    try:
        episode = service.archive_source_files(episode, source_tracker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.save(episode)
    files = [
        {"file_name": name, "state": state}
        for name, state in episode.source_file_states.items()
    ]
    archived_count = sum(
        1 for f in files if f["state"] == "archived"
    )
    return ArchiveSourcesResponse(
        episode_id=episode.id,
        archived_count=archived_count,
        files=files,
    )


@app.get("/api/episodes", response_model=EpisodeListResponse)
def list_episodes(
    status: EpisodeStatus | None = None,
    intake_mode: EpisodeIntakeMode | None = None,
    limit: int = 50,
    offset: int = 0,
) -> EpisodeListResponse:
    episodes = repo.all()
    filtered = [
        ep for ep in episodes
        if (status is None or ep.status == status)
        and (intake_mode is None or ep.intake_mode == intake_mode)
    ]
    page = filtered[offset : offset + limit]
    return EpisodeListResponse(
        episodes=[
            EpisodeSummaryResponse(
                id=ep.id,
                day_number=ep.day_number,
                episode_date=ep.episode_date,
                status=ep.status,
                intake_mode=ep.intake_mode,
                content_template_id=ep.content_template_id,
                selected_topic_id=ep.selected_topic_id,
                video_source_file_name=ep.video_source_file_name,
                video_job_status=ep.video_job_status,
                video_job_message=ep.video_job_message,
                draft_package_name=ep.draft_package_name,
                publish_url=ep.publish_url,
                platform=ep.platform,
                published_at=ep.published_at,
                created_at=ep.created_at,
                updated_at=ep.updated_at,
            )
            for ep in page
        ],
        total=len(filtered),
    )


@app.get("/api/episodes/history", response_model=EpisodeHistoryResponse)
def list_episode_history(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
) -> EpisodeHistoryResponse:
    episodes = repo.all()
    sorted_episodes = sorted(episodes, key=lambda ep: ep.created_at, reverse=True)

    # Apply status filter if provided
    if status:
        try:
            status_enum = EpisodeStatus(status)
            filtered = [ep for ep in sorted_episodes if ep.status == status_enum]
        except ValueError:
            filtered = sorted_episodes
    else:
        filtered = sorted_episodes

    total_all = len(sorted_episodes)
    page = filtered[offset : offset + limit]

    def _make_title(ep: Episode) -> str:
        selected = next((t for t in ep.topics if t.id == ep.selected_topic_id), None)
        if selected:
            return selected.title
        return f"第{ep.day_number}期 {ep.episode_date}"

    def _topic_title(ep: Episode) -> str | None:
        selected = next((t for t in ep.topics if t.id == ep.selected_topic_id), None)
        return selected.title if selected else None

    def _script_title(ep: Episode) -> str | None:
        if ep.script:
            return ep.script.hook or ep.script.title_options[0] if ep.script.title_options else None
        return None

    items = [
        EpisodeHistoryItemResponse(
            id=ep.id,
            day_number=ep.day_number,
            episode_date=ep.episode_date,
            title=_make_title(ep),
            status=ep.status,
            created_at=ep.created_at,
            updated_at=ep.updated_at,
            platform=ep.platform,
            notes=ep.notes,
            topic_title=_topic_title(ep),
            script_title=_script_title(ep),
            intake_mode=ep.intake_mode,
            publish_url=ep.publish_url,
            play_count=ep.play_count,
            like_count=ep.like_count,
            comment_count=ep.comment_count,
            draft_package_path=ep.draft_package_name,
            video_dedup_report_path=ep.video_dedup_report_path,
            published_at=ep.published_at,
        )
        for ep in page
    ]
    return EpisodeHistoryResponse(
        items=items,
        total_count=total_all,
        filtered_count=len(filtered),
    )


@app.post("/api/sources/{key}/retry", response_model=SourceSyncRetryResponse)
def retry_source_sync(key: str, request: Request) -> SourceSyncRetryResponse:
    """Compatibility retry route backed by the same persistent connectors."""
    workspace_id = current_workspace_id(request)
    if key == "manual":
        connector = _manual_connector(workspace_id)
    elif key == "hermes_radar":
        connector = _compat_connector(
            workspace_id,
            key,
            platform="hermes_radar",
            protocol="local_file",
            account_or_rule=str(settings.hermes_radar_path or ""),
        )
    elif key == "external_urls":
        connector = _compat_connector(
            workspace_id,
            key,
            platform="external_urls",
            protocol="https",
            account_or_rule=(
                "\n".join(settings.topic_source_urls)
                if settings.topic_source_urls
                else _EXTERNAL_URLS_CONFIGURATION_REQUIRED
            ),
        )
    else:
        raise HTTPException(status_code=404, detail=f"Unknown source key: {key}")

    run = _sync_connector(workspace_id, connector)
    return SourceSyncRetryResponse(
        source_type=key,
        status="success",
        message=f"{connector.platform} sync completed: {run.item_count} items",
        item_count=run.item_count,
    )


@app.get("/api/preferences/cards", response_model=PreferencesCardsResponse)
def get_preferences_cards() -> PreferencesCardsResponse:
    events = [
        event
        for episode in repo.all()
        for event in episode.feedback_events
        if event.reason and event.active
    ]
    pref_state = PreferenceState.from_payload(load_preference_state_payload())
    memory = build_preference_memory(events, limit=20, preference_state=pref_state)
    all_cards = [
        PreferenceCardResponse(
            id=f"{label}::{signal.signal}",
            label=label,
            signal=signal.signal,
            category=label,
            evidence_count=signal.evidence_count,
            confidence=signal.confidence,
            enabled=signal.enabled,
            latest_evidence_at=None,
        )
        for label, signals in (
            ("偏好", memory.prefer),
            ("避开", memory.avoid),
        )
        for signal in signals
    ]
    cards = all_cards[:20]
    return PreferencesCardsResponse(
        total_count=len(cards),
        enabled_count=sum(1 for c in cards if c.enabled),
        disabled_count=sum(1 for c in cards if not c.enabled),
        cards=cards,
    )


@app.get(
    "/api/preferences/cards/{card_id}/evidence",
    response_model=PreferenceEvidenceResponse,
)
def get_preference_card_evidence(card_id: str) -> PreferenceEvidenceResponse:
    """Return evidence items for a preference signal."""
    episodes = repo.all()
    items = [
        PreferenceEvidenceItemResponse(
            feedback_event_id=ev.id,
            episode_id=ep.id,
            episode_date=ep.episode_date,
            target_type=ev.target_type,
            target_id=ev.target_id,
            value=ev.value.value,
            reason=ev.reason or "",
            created_at=ev.created_at,
        )
        for ep in episodes[:50]
        for ev in ep.feedback_events
        if ev.active and ev.reason and card_id in ev.reason
    ][:10]
    return PreferenceEvidenceResponse(
        preference_id=card_id,
        total=len(items),
        items=items,
    )


@app.get(
    "/api/episodes/{episode_id}/topic-evidence",
    response_model=TopicEvidenceResponse,
)
def get_episode_topic_evidence(episode_id: str) -> TopicEvidenceResponse:
    """Return topic evidence (source refs) from the selected topic."""
    episode = get_episode_or_404(episode_id)
    selected = next((t for t in episode.topics if t.id == episode.selected_topic_id), None)
    items = [
        TopicEvidenceItemResponse(
            source_key=sf.ref or sf.title,
            source_type=sf.source_type,
            title=sf.title,
            ref=sf.ref,
        )
        for sf in selected.source_refs
    ] if selected else []
    return TopicEvidenceResponse(
        episode_id=episode_id,
        total=len(items),
        items=items,
    )


@app.post(
    "/api/episodes/{episode_id}/production-mode",
    response_model=EpisodeResponse,
)
def set_episode_production_mode(
    episode_id: str,
    request: ProductionModeRequest,
) -> EpisodeResponse:
    """Stub: accept production mode (bilibili/youtube/shorts) and return episode."""
    known_modes = {"bilibili", "youtube", "shorts", "douyin", "xiaohongshu"}
    if request.mode not in known_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown production mode: {request.mode}. Known: {', '.join(sorted(known_modes))}",
        )
    episode = get_episode_or_404(episode_id)
    episode.platform = request.mode
    repo.save(episode)
    return episode_response(episode)


@app.put(
    "/api/episodes/{episode_id}/publish-metrics",
    response_model=EpisodeResponse,
)
def update_episode_publish_metrics(
    episode_id: str,
    request: PublishMetricsUpdateRequest,
) -> EpisodeResponse:
    episode = get_episode_or_404(episode_id)
    episode = service.update_publish_metrics(
        episode,
        publish_url=request.publish_url,
        platform=request.platform,
        play_count=request.play_count,
        like_count=request.like_count,
        comment_count=request.comment_count,
        notes=request.notes,
    )
    return episode_response(repo.save(episode))


# ── Server entry point (for standalone API server) ──────────────────
if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
