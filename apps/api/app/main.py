from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import quote, urlparse
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.assistant_pi import AssistantUnavailable, run_plan_turn, run_task_turn

from app.subtitle_alignment import align_subtitle_track, apply_aligned_timings, recognize_subtitle_timing, synchronize_translated_subtitles
from app.compound_edits import compile_compound_edit, compile_compound_selection, apply_compound_subtitles, is_indexed_subtitle_command
from app.graphic_overlays import validate_overlays, compile_overlay_command
from app.graphic_sources import import_graphic_source, graphic_sources, resolve_graphic_source
from app.transitions import dissolve_frames, extend_dissolve_sources
from app.visual_effects import clip_playback_rate, reusable_color_settings
from app.speech_timing import estimate_speech_seconds
from app.linked_timeline import splice_clip, retime_clip, remove_time_ranges
from app.audio_timeline import render_stretched_audio
from app.canvas import canvas_size
from app.subtitle_style import validate_style_patch, compile_subtitle_style_command, publish_subtitle_style_preview
from app.subtitle_edits import replace_subtitle_text, restructure_subtitle, normalize_subtitle_text_and_breaks, update_subtitle_cue
from app.subtitle_schedule import subtitle_schedule, retime_subtitle_tracks
from app.avatar_lipsync import (
    compile_person_edit_command,
    Authorization,
    AvatarAndLipSyncService,
    AvatarPreviewService,
    PersonProfile,
    PronunciationEntry,
    VoiceProfile as VersionedVoiceProfile,
    avatar_lipsync_cache_key,
    avatar_lipsync_provider_from_settings,
)
from app.auth import (
    ForbiddenError,
    OnboardingStateError,
    SupabaseAuthContext,
    UnauthorizedError,
)
from app.device_accounts import DeviceAccountError, DeviceAccountService, DeviceCredential
from app.artifact_store import ArtifactStore
from app.audio_waveform import AudioWaveformError, audio_waveform
from app.audio_timeline import AudioEditRequest, AudioListenRequest, AudioTimeline, apply_audio_edit, audio_edit_preview_range, compile_audio_command, narration_audio_timeline, probe_audio_source, render_audio_mix, rebind_narration_audio
from app.task_audio import TaskAudio
from app.visual_effects import VisualEditRequest, VisualEffects, publish_color_preview, compile_visual_command, fade_filters, with_visual_effects
from app.local_portrait import LocalPortrait
from app.local_members import LocalMembersError, LocalMemberService
from app.material_speech import LocalMaterialSpeechAnalyzer
from app.visual_understanding import LocalVisualUnderstanding
from app.cover_generation import (
    CoverGenerationRequest,
    CoverGenerationService,
    CoverSafeArea,
    unconfigured_image_generation_capability,
)
from app.config import (
    Settings,
    configure_process_temporary_directory,
    runtime_env_file_path,
)
from app.content_templates import list_content_templates
from app.creation_execution import (
    CpuRenderGrantContext, CreationExecution, CreationExecutionActor,
    CreationJobUnitExecutor, ExecutionStartRequest,
    revision_digest, submit_creation_stage,
)
from app.creator_profile import (
    get_or_create_profile,
    upsert_creator_profile,
)
from app.drafts import DraftWorkspace
from app.edit_plan import (
    build_edit_plan_workspace,
    safe_processed_video_path,
)
from app.external_sources import ExternalSourceResponseError, HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.llm import (
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
from app.montage import (
    CreationTaskMontagePlan,
    MontageClip,
    MontagePlanner,
    MontageRenderer,
    MontageRepository,
    NarrationCue,
    creation_task_montage_plan_from_payload,
    creation_task_montage_plan_to_payload,
    select_presenter_sections,
    _validate_rendered_media,
)
from app.narration import NarrationArtifact, NarrationGenerator, VoicePreviewService, compile_narration_command
from app.preference_memory import PreferenceState, build_preference_memory
from app.preflight import setup_preflight
from app.product_hunt import ProductHuntClient
from app.scheduled_tasks import (
    ScheduledTaskCreateRequest,
    ScheduledTaskEnableRequest,
    ScheduledTaskResponse,
    ScheduledTasksResponse,
    ScheduledTaskService,
    ScheduledTaskUpdateRequest,
)
from app.production import (
    ArtifactRecord,
    CreationPlan,
    VideoTemplate,
    CreationPlanBlocker,
    CreationPlanKnowledgeReference,
    CreationPlanPronunciationReference,
    CreationPlanSourceReference,
    CreationPlanVoiceReference,
    CreationTask,
    CreationTaskScript,
    CreationTaskScriptSection,
    CoverAsset,
    DependencyEdge,
    EditIntent,
    EditOperation,
    EditProposal,
    JobRun,
    MediaAuthorityError,
    MaterialCollection,
    NarrationArtifactRecord,
    NarrationSegment,
    PersonRender,
    ProductionBatch,
    ProductionProfile,
    ProductionRepository,
    ScriptGenerationRun,
    TopicCandidate as CreationTaskTopicCandidate,
    TopicEvidenceReference,
    TopicGenerationRun,
    TaskEvent,
    TimelineVersion,
    SubtitleCue,
    SubtitleTrack,
    TransformRun,
    effective_segment_usage_role,
    material_allows_footage,
)
from app.task_runtime import JobFailure, JobResult
from app.subtitle_tracks import (
    SubtitleCue as SubtitleExecutionCue,
    SubtitleStyle as SubtitleExecutionStyle,
    SubtitleTrack as SubtitleExecutionTrack,
    SubtitleTranslationService,
    render_srt,
    subtitle_translation_provider_from_settings,
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
    AuthorizationResponse,
    AuthorizationsResponse,
    AuthorizationUpsertRequest,
    AvatarLipSyncPreviewRequest,
    ContentTemplatesResponse,
    CreationPlanConfirmRequest,
    CreationPlanPreviewRequest,
    SaveVideoTemplateRequest,
    CreationPlanPreviewResponse,
    CreationPlanResponse,
    CreationTaskTopicConfirmRequest,
    CreationTaskTopicGenerateRequest,
    CreationTaskTopicsResponse,
    CreationTaskScriptConfirmRequest,
    CreationTaskScriptResponse,
    CreationTaskScriptGenerateRequest,
    CreationTaskScriptsResponse,
    CreationTaskNarrationResponse,
    CreationTaskNarrationVoiceSelectionRequest,
    CreationTaskPersonRenderRequest,
    CreationTaskPersonRenderResponse,
    CreationTaskPersonRendersResponse,
    CreationTaskMontagePlanResponse,
    CreationTaskMontageClipUpdateRequest,
    CreationTaskMontageRenderRequest,
    CreationTaskEditConfirmResponse,
    CreationTaskEditPreviewRequest,
    CreationTaskAssistantTurnRequest,
    CreationTaskAssistantTurnResponse,
    CreationTaskAssistantHistoryResponse,
    WorkspaceAssistantPlanTurnRequest,
    WorkspaceAssistantPlanTurnResponse,
    CompoundEditSelectionRequest,
    CreationTaskEditPreviewResponse,
    CreationTaskExactEditRequest,
    CreationTaskSubtitleCueUpdateRequest,
    CreationTaskTimelineUndoRequest,
    CreationTaskWorkspaceResponse,
    CreationTasksResponse,
    CreatorProfileLearnResult,
    CreatorProfileResponse,
    CreatorProfileUpdate,
    DraftAssetResponse,
    DraftEnvironmentResponse,
    DraftStatusResponse,
    EditPlanResponse,
    EpisodeHistoryItemResponse,
    EpisodeHistoryResponse,
    EpisodeListResponse,
    EpisodeResponse,
    EpisodeSummaryResponse,
    EpisodeVoiceSelectionResponse,
    FeedbackEventResponse,
    FeedbackSummaryResponse,
    KnowledgeNoteResponse,
    KnowledgeItemResponse,
    KnowledgeItemsResponse,
    ManualSourceItemRequest,
    ManualLinkRequest,
    MaterialAssetResponse,
    MaterialAssetTagsRequest,
    MaterialIntakeBindRequest,
    MaterialIntakeStatusResponse,
    MaterialBatchTagsRequest,
    MaterialBatchTagsResponse,
    MaterialCollectionCreateRequest,
    MaterialCollectionResponse,
    MaterialCollectionsResponse,
    MaterialLibraryInsightsResponse,
    MaterialLibraryResponse,
    MaterialReviewRequest,
    MaterialUseStateRequest,
    MaterialSegmentMatchResponse,
    MaterialSegmentUpdateRequest,
    MediaCapabilityResponse,
    MediaPreviewRunResponse,
    MediaPreviewRunsResponse,
    ModelCatalogResponse,
    ModelInstallJobResponse,
    ModelInstallRequest,
    MontageClipCandidateResponse,
    MontageClipResponse,
    MontagePlanResponse,
    MontagePlansResponse,
    NarrationCueResponse,
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
    ProcessVideoRequest,
    ProductionBatchesResponse,
    ProductionBatchResponse,
    ProductionChainResponse,
    ProductionFoundationResponse,
    ProductionProfileResponse,
    ProductionProfileUpdateRequest,
    PersonProfileResponse,
    PersonProfilesResponse,
    PersonProfileUpsertRequest,
    PronunciationEntriesResponse,
    PronunciationEntryResponse,
    PronunciationEntryUpsertRequest,
    RetakeReportResponse,
    RuntimeHealthResponse,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
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
    VoicePreviewRequest,
    VoiceProfileResponse,
    VoiceReferenceImportResponse,
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
    CreationTaskGeneratedScript,
    CreationTaskScriptGenerationError,
    CreationTaskScriptInput,
    CreationTaskTopicGenerationError,
    CreationTaskTopicInput,
    CreationTaskTopicSource,
    OpenAICompatibleCreationTaskTopicGenerator,
    OpenAICompatibleCreationTaskScriptGenerator,
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
CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION = "creation-task-montage-render/v20"
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?",
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
    understanding_runtime_python_path=settings.understanding_runtime_python_path,
    understanding_worker_path=settings.understanding_worker_path,
)
model_installer = ModelInstaller(
    model_catalog,
    on_complete=lambda: _reload_runtime_dependencies(),
)
visual_semantic_provider = visual_semantic_provider_from_catalog(model_catalog)
voice_clone_provider = voice_clone_provider_from_catalog(model_catalog)
avatar_lipsync_provider = avatar_lipsync_provider_from_settings(settings)
latentsync_provider = avatar_lipsync_provider_from_settings(settings, quality="high")


def _lipsync_provider(quality: str):
    return latentsync_provider if quality == "high" else avatar_lipsync_provider
production_repo = ProductionRepository(
    database_path=settings.database_path,
    video_library_path=settings.video_inbox_path,
    visual_provider=visual_semantic_provider,
    speech_analyzer=LocalMaterialSpeechAnalyzer(settings),
    visual_understanding=LocalVisualUnderstanding(model_catalog, settings.effective_temporary_root_path, settings.native_ocr_worker_path),
)
montage_repo = MontageRepository(settings.database_path)
local_members_service = LocalMemberService(settings.database_path)
montage_planner = MontagePlanner(
    semantic_provider=visual_semantic_provider,
    visual_feature_loader=production_repo.get_visual_feature,
)
montage_renderer = MontageRenderer(settings.video_output_path)
artifact_store = ArtifactStore(settings.video_output_path)
narration_generator = NarrationGenerator(settings.video_output_path, voice_clone_provider)
voice_preview_service = VoicePreviewService(settings.video_output_path, voice_clone_provider)
avatar_preview_service = AvatarPreviewService(
    settings.video_output_path,
    avatar_lipsync_provider,
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
video_job_runner = VideoJobRunner(production_repo)
# A process that disappeared while a durable job was running must leave an
# explicit recoverable state; it must never be interpreted as a success.
video_job_runner.recover_after_restart()
production_repo.interrupt_running_person_renders()
scheduled_task_service = ScheduledTaskService(settings.database_path)
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

# The single identity-only entry: a first-time invitee accepts a device invite
# before holding any workspace membership. This exact path (POST only) is
# authenticated by identity without workspace resolution (see the middleware).
DEVICE_INVITE_ACCEPT_PATH = "/api/device-invites/accept"

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
    is_device_accept = request.method == "POST" and path == DEVICE_INVITE_ACCEPT_PATH
    if (
        path in PUBLIC_AUTH_PATHS
        or request.method == "OPTIONS"
        or (not auth_context.enabled and not is_device_accept)
    ):
        return await call_next(request)
    try:
        if is_device_accept:
            # Identity-only entry: a first-time invitee has no workspace yet,
            # so membership resolution, the onboarding gate and the workspace
            # context are all skipped. Any token failure is a 401.
            request.state.principal = auth_context.authenticate_identity(request)
            return await call_next(request)
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


DEVICE_ACCOUNT_ERROR_STATUS: dict[str, int] = {
    "forbidden": 403,
    "conflict": 409,
    "invalid": 400,
    "unavailable": 503,
}


def trusted_device_credential() -> DeviceCredential:
    """Return the pre-provisioned device credential from the trusted runtime.

    The Electron main process spawns this API subprocess and injects the device
    identity through environment variables — the same trusted channel as
    ``distributionAuthEnv()`` for the Supabase credentials. The renderer can
    never observe this process environment, so it cannot forge a credential. A
    missing or malformed credential means the native runtime did not provision
    this instance: an operational outage (503), not a client error.
    """
    device_id = os.environ.get("DEVICE_ID")
    proof = os.environ.get("DEVICE_PROOF")
    if not device_id or not proof:
        raise DeviceAccountError("unavailable", "trusted device credential is not provisioned")
    try:
        return DeviceCredential(device_id, proof)
    except ValueError as exc:
        raise DeviceAccountError("unavailable", "trusted device credential is malformed") from exc


class DeviceInviteAcceptRequest(BaseModel):
    invite_token: str


class DeviceInviteAcceptResponse(BaseModel):
    target_workspace_id: str
    member_count: int
    already_accepted: bool


@app.post("/api/device-invites/accept")
async def accept_device_invite(request: Request, payload: DeviceInviteAcceptRequest):
    """Accept a device invite for the verified identity (no workspace required).

    The middleware already authenticated the request's identity only
    (``authenticate_identity``) and attached a workspaceless principal. The
    trusted native runtime supplies the DeviceCredential; the Supabase RPC
    re-checks ``auth.uid()``, the device proof and the invite token.
    """
    principal = request.state.principal
    try:
        service = DeviceAccountService(settings, trusted_device_credential())
        acceptance = service.accept_invite(principal, payload.invite_token)
    except DeviceAccountError as exc:
        status = DEVICE_ACCOUNT_ERROR_STATUS.get(exc.kind, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.kind, "detail": str(exc)},
        )
    return DeviceInviteAcceptResponse(
        target_workspace_id=acceptance.target_workspace_id,
        member_count=acceptance.member_count,
        already_accepted=acceptance.already_accepted,
    )


LOCAL_MEMBERS_ERROR_STATUS: dict[str, int] = {
    "invalid": 400,
    "not_found": 404,
    "conflict": 409,
}


class MemberResponse(BaseModel):
    member_id: str
    name: str
    role: str
    created_at: str


class MemberListResponse(BaseModel):
    members: list[MemberResponse]


class AddMemberRequest(BaseModel):
    name: str
    role: str = "member"


class SetMemberRoleRequest(BaseModel):
    role: str


class JoinMemberRequest(BaseModel):
    code: str
    name: str


class InviteCodeResponse(BaseModel):
    invite_code: str


class MemberWithTokenResponse(MemberResponse):
    """Join/whoami response: the only places a member's bearer token is exposed."""

    member_token: str


LOCAL_MEMBER_TOKEN_HEADER = "X-Local-Member-Token"


def _local_member_token(request: Request) -> str:
    return request.headers.get(LOCAL_MEMBER_TOKEN_HEADER, "").strip()


def _member_response(member) -> MemberResponse:
    return MemberResponse(
        member_id=member.member_id,
        name=member.name,
        role=member.role,
        created_at=member.created_at,
    )


def _member_with_token_response(member, token: str) -> MemberWithTokenResponse:
    return MemberWithTokenResponse(
        member_id=member.member_id,
        name=member.name,
        role=member.role,
        created_at=member.created_at,
        member_token=token,
    )


def _local_members_error_response(exc: LocalMembersError) -> JSONResponse:
    status = LOCAL_MEMBERS_ERROR_STATUS.get(exc.kind, 500)
    return JSONResponse(
        status_code=status,
        content={"error": exc.kind, "detail": str(exc)},
    )


def _local_member_write_guard(request: Request) -> JSONResponse | None:
    """Owner check for member-management write routes.

    方案 A transitional rule: anonymous callers (no header) pass through
    unchanged — the single-user zero-config compatibility state whose bypass
    face is a documented, accepted boundary. Callers presenting a valid member
    token must be owners; an unknown token is a 401.
    """
    token = _local_member_token(request)
    if not token:
        return None
    try:
        member = local_members_service.resolve_member(token)
    except LocalMembersError as exc:
        # An unknown token is a credential failure, not a malformed request:
        # 401 regardless of the underlying "invalid" kind.
        return JSONResponse(
            status_code=401,
            content={"error": exc.kind, "detail": str(exc)},
        )
    assert member is not None  # a present, valid token always resolves
    if member.role != "owner":
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "detail": "仅所有者可执行此操作"},
        )
    return None


def _content_include_private(request: Request) -> bool:
    """Member visibility for content list routes.

    Anonymous and owner callers see everything; a valid member token sees only
    shared content. An unknown token is a 401 in the content routes' own
    HTTPException style.
    """
    token = _local_member_token(request)
    if not token:
        return True
    try:
        member = local_members_service.resolve_member(token)
    except LocalMembersError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    assert member is not None  # a present, valid token always resolves
    return member.role == "owner"


@app.get("/api/members")
def list_local_members() -> MemberListResponse:
    return MemberListResponse(
        members=[_member_response(m) for m in local_members_service.list_members()]
    )


@app.post("/api/members")
def add_local_member(request: Request, payload: AddMemberRequest):
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    try:
        member = local_members_service.add_member(payload.name, payload.role)
    except LocalMembersError as exc:
        return _local_members_error_response(exc)
    return _member_response(member)


@app.delete("/api/members/{member_id}")
def remove_local_member(member_id: str, request: Request):
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    try:
        local_members_service.remove_member(member_id)
    except LocalMembersError as exc:
        return _local_members_error_response(exc)
    return {"deleted": member_id}


@app.patch("/api/members/{member_id}")
def set_local_member_role(member_id: str, request: Request, payload: SetMemberRoleRequest):
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    try:
        member = local_members_service.set_role(member_id, payload.role)
    except LocalMembersError as exc:
        return _local_members_error_response(exc)
    return _member_response(member)


@app.post("/api/members/invite-code")
def create_local_invite_code(request: Request) -> InviteCodeResponse:
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    return InviteCodeResponse(invite_code=local_members_service.create_invite_code())


@app.post("/api/members/join")
def join_local_member(payload: JoinMemberRequest):
    try:
        member, token = local_members_service.join(payload.code, payload.name)
    except LocalMembersError as exc:
        return _local_members_error_response(exc)
    return _member_with_token_response(member, token)


@app.get("/api/members/whoami")
def whoami_local_member(request: Request):
    try:
        member, token = local_members_service.whoami(
            _local_member_token(request) or None
        )
    except LocalMembersError as exc:
        return _local_members_error_response(exc)
    return _member_with_token_response(member, token)


def get_episode_or_404(episode_id: str) -> Episode:
    try:
        return repo.get(episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc


def episode_response(episode: Episode) -> EpisodeResponse:
    return EpisodeResponse.model_validate(
        episode,
        from_attributes=True,
    )




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


@app.get("/api/scheduled-tasks", response_model=ScheduledTasksResponse)
def list_scheduled_tasks(request: Request) -> ScheduledTasksResponse:
    tasks = scheduled_task_service.list_tasks(current_workspace_id(request))
    return ScheduledTasksResponse(tasks=tasks, total_count=len(tasks))


@app.post(
    "/api/scheduled-tasks",
    response_model=ScheduledTaskResponse,
    status_code=201,
)
def create_scheduled_task(request: Request, payload: ScheduledTaskCreateRequest) -> ScheduledTaskResponse:
    try:
        return scheduled_task_service.create_task(current_workspace_id(request), payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
def update_scheduled_task(request: Request, task_id: str, payload: ScheduledTaskUpdateRequest) -> ScheduledTaskResponse:
    try:
        return scheduled_task_service.update_task(current_workspace_id(request), task_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="定时任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/scheduled-tasks/{task_id}/enabled", response_model=ScheduledTaskResponse)
def set_scheduled_task_enabled(request: Request, task_id: str, payload: ScheduledTaskEnableRequest) -> ScheduledTaskResponse:
    try:
        return scheduled_task_service.set_enabled(current_workspace_id(request), task_id, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="定时任务不存在") from exc


@app.delete("/api/scheduled-tasks/{task_id}", status_code=204)
def delete_scheduled_task(request: Request, task_id: str) -> None:
    try:
        scheduled_task_service.delete_task(current_workspace_id(request), task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="定时任务不存在") from exc


@app.get("/api/content-templates", response_model=ContentTemplatesResponse)
def get_content_templates() -> ContentTemplatesResponse:
    return ContentTemplatesResponse.model_validate(
        {"templates": list_content_templates()},
        from_attributes=True,
    )


def _builtin_video_templates(workspace_id: str) -> list[VideoTemplate]:
    return [VideoTemplate(
        id=f"builtin_{item.template_id}", workspace_id=workspace_id, version=1,
        name=item.name, origin="built_in",
        settings={"language": "zh-CN", "aspect_ratio": "9:16", "duration_target_seconds": 60,
                  "script_structure": item.script_structure, "style_instruction": item.scenario,
                  "fit_mode": "fill", "subtitle_style": _default_subtitle_style(), "voice": None},
    ) for item in list_content_templates()]


def _video_template_or_404(workspace_id: str, template_id: str, version: int) -> VideoTemplate:
    for item in _builtin_video_templates(workspace_id):
        if (item.id, item.version) == (template_id, version):
            return item
    try:
        return production_repo.get_video_template(workspace_id, template_id, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="当前工作区找不到这个模板版本。") from exc


@app.get("/api/video-templates")
def list_video_templates(request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    return {"items": [asdict(item) for item in [*_builtin_video_templates(workspace_id), *production_repo.list_video_templates(workspace_id)]]}


@app.post("/api/creation-tasks/{task_id}/templates")
def save_creation_task_template(task_id: str, request: Request, payload: SaveVideoTemplateRequest) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    try:
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="先生成并检查一条成片，再保存模板。") from exc
    if montage.status != "rendered" or not montage.output_path or not task.current_timeline_version_id:
        raise HTTPException(status_code=409, detail="先生成并检查一条成片，再保存模板。")
    try:
        output = _creation_task_output_path(workspace_id, task_id, subtitle=False)
        if output.stat().st_size == 0:
            raise OSError("empty output")
    except (HTTPException, OSError) as exc:
        raise HTTPException(status_code=409, detail="当前成片文件不可用，请恢复文件或重新渲染后再保存模板。") from exc
    if (payload.template_id is None) != (payload.expected_version is None):
        raise HTTPException(status_code=422, detail="更新模板时需要同时指定模板和当前版本。")
    if payload.template_id:
        previous = _video_template_or_404(workspace_id, payload.template_id, payload.expected_version)
        if previous.origin != "user":
            raise HTTPException(status_code=409, detail="内置模板不可覆盖，请另存为我的模板。")
    tracks = _current_subtitle_tracks(workspace_id, task, montage=montage)
    source_track = next((item for item in tracks if item.source_language is None), None)
    scripts = production_repo.list_creation_task_scripts(workspace_id, task_id)
    script = next((item for item in scripts if item.id == task.confirmed_script_id), None)
    color_settings = [reusable_color_settings(clip.visual_effects) for clip in montage.clips]
    common_color = color_settings[0] if color_settings and all(
        item == color_settings[0] for item in color_settings
    ) else None
    template = VideoTemplate(
        id=payload.template_id or f"template_{uuid4().hex[:12]}", workspace_id=workspace_id,
        version=(payload.expected_version or 0) + 1, name=payload.name.strip(), origin="user",
        source_task_id=task_id, source_timeline_version_id=task.current_timeline_version_id,
        settings={"language": plan.language, "aspect_ratio": montage.aspect_ratio,
                  "duration_target_seconds": plan.duration_target_seconds,
                  "presenter_share_target": plan.presenter_share_target,
                  "lipsync_quality": plan.lipsync_quality,
                  "voice": {"profile_id": plan.selected_voice.profile_id, "version": plan.selected_voice.version} if plan.selected_voice else None,
                  "script_section_count": len(script.sections) if script else None,
                  "style_instruction": payload.style_instruction.strip(),
                  "fit_mode": "fit" if all(clip.fit_mode == "fit" for clip in montage.clips) else "fill",
                  "color_style": common_color,
                  "subtitle_style": dict(source_track.style) if source_track else _default_subtitle_style()},
    )
    try:
        return asdict(production_repo.save_video_template(template))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
            for batch in production_repo.list_batches(workspace_id)[:8]
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
        production_repo.get_voice_profile(workspace_id, voice_profile_id)
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


@app.post(
    "/api/media/voice-references/import",
    response_model=VoiceReferenceImportResponse,
    status_code=201,
)
async def import_media_voice_reference(
    request: Request,
    audio: UploadFile = File(...),  # noqa: B008
) -> VoiceReferenceImportResponse:
    if not audio.filename or Path(audio.filename).suffix.lower() != ".wav":
        raise HTTPException(status_code=422, detail="请选择 WAV 声音参考文件")
    try:
        content = await audio.read(50 * 1024 * 1024 + 1)
        reference, size_bytes, duration_seconds = (
            production_repo.import_voice_reference_wav(
                current_workspace_id(request),
                content,
            )
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VoiceReferenceImportResponse(
        reference=reference,
        file_name="已导入声音参考.wav",
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
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
def get_avatar_lipsync_capability(quality: Literal["high", "fast"] = "fast") -> MediaCapabilityResponse:
    provider = _lipsync_provider(quality)
    if not provider.ready:
        raise _capability_unavailable(
            "avatar_lipsync",
            message=provider.message,
            impact="人物与口型步骤未完成，不能生成或返回人物视频。",
            repair_action="配置明确的本地人物口型运行环境后重新查询。",
        )
    return MediaCapabilityResponse(
        capability_id="avatar_lipsync",
        available=True,
        model_id=provider.model_id,
        message=provider.message,
        impact=None,
        repair_action=None,
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
    template = None
    if payload.template_id is not None:
        if payload.template_version is None:
            raise HTTPException(status_code=422, detail="请选择明确的模板版本。")
        template = _video_template_or_404(workspace_id, payload.template_id, payload.template_version)
        inherited = {key: template.settings[key] for key in ("language", "aspect_ratio", "duration_target_seconds")}
        if "presenter_share_target" not in payload.model_fields_set and "presenter_share_target" in template.settings:
            inherited["presenter_share_target"] = template.settings["presenter_share_target"]
        if "lipsync_quality" not in payload.model_fields_set and "lipsync_quality" in template.settings:
            inherited["lipsync_quality"] = template.settings["lipsync_quality"]
        elif "lipsync_quality" not in payload.model_fields_set:
            inherited["lipsync_quality"] = "fast"
        payload = payload.model_copy(update=inherited)
    elif payload.template_version is not None:
        raise HTTPException(status_code=422, detail="模板版本缺少对应模板。")
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
        and material_allows_footage(asset)
        and any(
            segment.review_status == "approved"
            and effective_segment_usage_role(asset, segment) in {"a_roll", "b_roll"}
            for segment in asset.segments
        )
    ]
    approved_clips = [
        segment
        for asset in approved_assets
        for segment in asset.segments
        if segment.review_status == "approved"
        and effective_segment_usage_role(asset, segment) == "b_roll"
    ]
    material_matches = production_repo.search_material_segments(
        workspace_id,
        query=payload.goal,
        usage_roles=["a_roll", "b_roll"],
        limit=8,
    )
    if not material_matches and template is None:
        material_matches = production_repo.search_material_segments(
            workspace_id,
            usage_roles=["a_roll", "b_roll"],
            limit=8,
        )
    language_to_voice_language = {
        "zh-CN": "zh",
        "en-US": "en",
        "zh-CN/en-US": "zh-en",
    }
    requested_voice_language = language_to_voice_language[payload.language]
    active_voices = []
    required_voice = template.settings.get("voice") if template else None
    voices = (
        production_repo.list_versioned_voice_profile_versions(workspace_id, required_voice["profile_id"])
        if required_voice else production_repo.list_versioned_voice_profiles(workspace_id)
    )
    for voice in voices:
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
            and (template is None or not template.settings.get("voice") or
                 (voice.id, voice.version) == (template.settings["voice"]["profile_id"], template.settings["voice"]["version"]))
        ),
        None,
    )
    selected_voice = None
    pronunciation_references: list[CreationPlanPronunciationReference] = []
    person_authorization_failure: str | None = None
    voice_media_failure: str | None = None
    if selected_voice_pair is not None:
        voice, authorization = selected_voice_pair
        try:
            reference_audio = production_repo.resolve_workspace_media_reference(
                workspace_id,
                voice.reference_audio_path,
                require_registered=True,
            )
            if (
                reference_audio.suffix.lower() != ".wav"
                or not reference_audio.is_file()
                or reference_audio.stat().st_size == 0
            ):
                voice_media_failure = "voice_reference_wav_unavailable"
        except (MediaAuthorityError, OSError):
            voice_media_failure = "voice_reference_wav_unavailable"
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
    selected_lipsync_provider = _lipsync_provider(payload.lipsync_quality)
    model_snapshot.append({
        "id": selected_lipsync_provider.model_id,
        "capability": "avatar_lipsync",
        "state": "ready" if selected_lipsync_provider.ready else "unavailable",
        "ready": selected_lipsync_provider.ready,
        "message": selected_lipsync_provider.message,
    })
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
    required_template_voice = template.settings.get("voice") if template else None
    if selected_voice is None and required_template_voice:
        blockers.append(CreationPlanBlocker(
            reason="template_voice_unavailable",
            impact=f"模板指定声音 {required_template_voice['profile_id']} · {required_template_voice['version']} 不可用，未替换为其他声音。",
            repair_action="在人物与声音中恢复这个声音版本及授权，或返回选择其他模板版本。",
            stage="narration", blocks_confirmation=True,
        ))
    elif selected_voice is None:
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
        if voice_media_failure is not None:
            blockers.append(
                CreationPlanBlocker(
                    reason=voice_media_failure,
                    impact="当前声音版本没有可读取的已授权 WAV，配音阶段会停止。",
                    repair_action="在人物与声音中重新选择当前工作区已批准的 WAV 参考素材。",
                    stage="narration",
                    blocks_confirmation=False,
                )
            )
        if person_authorization_failure is not None and payload.presenter_share_target > 0:
            blockers.append(
                CreationPlanBlocker(
                    reason=person_authorization_failure,
                    impact="当前方案不能进入人物口型阶段，但可先创建任务并进行选题。",
                    repair_action="更新人物形象的有效授权后，再从人物与口型阶段继续。",
                    stage="avatar_lipsync",
                    blocks_confirmation=False,
                )
            )
    if payload.presenter_share_target > 0 and not selected_lipsync_provider.ready:
        blockers.append(CreationPlanBlocker(
            reason="person_render_unavailable",
            impact=f"所选{('高精度' if payload.lipsync_quality == 'high' else '快速')}口型尚不可用，当前露脸比例无法进入成片。",
            repair_action="检查所选档位的本地口型环境，或主动选择另一档／将露脸占比调到 0%。",
            stage="avatar_lipsync", blocks_confirmation=False,
        ))
    if not approved_clips and payload.presenter_share_target < 100:
        blockers.append(
            CreationPlanBlocker(
                reason="approved_material_clips_missing",
                impact="当前方案不能进入自动选片和视频剪辑阶段。",
                repair_action="审核并标记至少一个当前工作区可用的 B-roll 镜头后继续。",
                stage="video_editing",
                blocks_confirmation=False,
            )
        )
    if template is not None and not material_matches and payload.presenter_share_target < 100:
        blockers.append(CreationPlanBlocker(
            reason="template_material_matches_missing",
            impact="已有素材没有匹配本次新主题，未沿用原任务镜头。",
            repair_action="前往素材库补充或审核与新主题相关的镜头，再重新检查。",
            stage="video_editing", blocks_confirmation=True,
        ))
    name = payload.name or payload.goal[:48]
    fingerprint_payload = {
        "template_id": template.id if template else None,
        "template_version": template.version if template else None,
        "template_settings": template.settings if template else {},
        "goal": payload.goal,
        "name": name,
        "target_count": payload.target_count,
        "language": payload.language,
        "aspect_ratio": payload.aspect_ratio,
        "duration_target_seconds": payload.duration_target_seconds,
        "presenter_share_target": payload.presenter_share_target,
        "lipsync_quality": payload.lipsync_quality,
        "knowledge_references": [item.__dict__ for item in knowledge_references],
        "source_references": [item.__dict__ for item in source_references],
        "selected_voice": selected_voice.__dict__ if selected_voice else None,
        "pronunciation_references": [
            item.__dict__ for item in pronunciation_references
        ],
        "approved_asset_count": len(approved_assets),
        "approved_clip_count": len(approved_clips),
        "material_rule_summary": "使用当前工作区已审核的人物主镜头与补充画面；用户给入的内容默认可用于创作。",
        "material_matches": [asdict(item) for item in material_matches],
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
        presenter_share_target=payload.presenter_share_target,
        lipsync_quality=payload.lipsync_quality,
        knowledge_references=knowledge_references,
        source_references=source_references,
        selected_voice=selected_voice,
        pronunciation_references=pronunciation_references,
        approved_asset_count=len(approved_assets),
        approved_clip_count=len(approved_clips),
        material_rule_summary=str(fingerprint_payload["material_rule_summary"]),
        material_matches=material_matches,
        model_snapshot=model_snapshot,
        confirmation_points=list(fingerprint_payload["confirmation_points"]),
        blockers=blockers,
        preflight_fingerprint=fingerprint,
        template_id=template.id if template else None,
        template_version=template.version if template else None,
        template_settings=template.settings if template else {},
    )


def _creation_plan_preview_response(plan: CreationPlan) -> CreationPlanPreviewResponse:
    return CreationPlanPreviewResponse.model_validate(
        {
            "template_id": plan.template_id,
            "template_version": plan.template_version,
            "template_settings": plan.template_settings,
            "goal": plan.goal,
            "name": plan.name,
            "target_count": plan.target_count,
            "language": plan.language,
            "aspect_ratio": plan.aspect_ratio,
            "duration_target_seconds": plan.duration_target_seconds,
            "presenter_share_target": plan.presenter_share_target,
            "lipsync_quality": plan.lipsync_quality,
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
            "material_matches": [asdict(item) for item in plan.material_matches],
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


def _creation_task_script_generator_from_settings() -> OpenAICompatibleCreationTaskScriptGenerator:
    return OpenAICompatibleCreationTaskScriptGenerator(
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


def _creation_task_scripts_response(
    workspace_id: str,
    task: CreationTask,
) -> CreationTaskScriptsResponse:
    latest_run = production_repo.latest_script_generation_run(workspace_id, task.id)
    route_snapshot = _projected_topic_route_snapshot(_topic_route_snapshot(), latest_run)
    return CreationTaskScriptsResponse.model_validate(
        {
            "task": task.__dict__,
            "scripts": [asdict(script) for script in production_repo.list_creation_task_scripts(workspace_id, task.id)],
            "latest_run": latest_run.__dict__ if latest_run is not None else None,
            "route_preflight": route_snapshot,
        }
    )


def _creation_task_narration_response(
    task: CreationTask,
    artifact: NarrationArtifactRecord,
) -> CreationTaskNarrationResponse:
    if artifact.task_id != task.id or artifact.voice_profile_id is None:
        raise ValueError("配音产物与当前创作任务不一致")
    segments = production_repo.list_narration_segments(
        task.workspace_id, task.id, artifact.script_id
    )
    return CreationTaskNarrationResponse.model_validate(
        {
            "task": task.__dict__,
            "script_id": artifact.script_id,
            "audio_reference": production_repo.narration_artifact_reference(artifact.cache_key),
            "duration_seconds": artifact.duration_seconds,
            "sample_rate": artifact.sample_rate,
            "model_id": artifact.model_id,
            "voice_profile_id": artifact.voice_profile_id,
            "language": artifact.language,
            "pronunciation_version": artifact.pronunciation_version,
            "segments": [
                {
                    **asdict(segment),
                    "audio_url": (
                        f"/api/creation-tasks/{task.id}/narration/segments/{segment.id}/audio"
                        if segment.status == "ready" and segment.audio_reference
                        else None
                    ),
                }
                for segment in segments
            ],
        }
    )


def _portrait_processor(workspace_id: str) -> LocalPortrait:
    return LocalPortrait(settings.native_portrait_worker_path, settings.effective_temporary_root_path / "portrait-cache" / workspace_id)


def _task_audio() -> TaskAudio:
    return TaskAudio(production_repo, artifact_store, settings.effective_temporary_root_path)


def _effective_audio_timeline(plan: CreationTaskMontagePlan) -> AudioTimeline:
    return plan.audio_timeline or narration_audio_timeline(plan.narration_audio_path or "", plan.duration_seconds)


@app.post("/api/creation-tasks/{task_id}/audio/import")
def import_creation_task_audio(task_id: str, request: Request, audio: UploadFile = File(...)):
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".flac"}:
        raise HTTPException(status_code=422, detail="请选择 WAV、MP3、M4A、AAC 或 FLAC 音频")
    def produce(path):
        total = 0
        with path.open("wb") as output:
            while chunk := audio.file.read(1024 * 1024):
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise ValueError("单个导入音频不能超过 512 MB，请先裁短")
                output.write(chunk)
        if not total:
            raise ValueError("音频文件为空")
    try:
        record = _task_audio().publish(workspace_id, task_id, kind="audio_source", parameters={"name": Path(audio.filename or "音频").name[:120]},
                                      input_refs=[], produce=produce, suffix=suffix)
        duration = probe_audio_source(_task_audio().source(workspace_id, task_id, record.logical_ref))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail="音频导入失败，请检查文件格式、时长及外接盘。") from exc
    return {"source_ref": record.logical_ref, "name": Path(audio.filename or "音频").name[:120], "duration_ms": round(duration*1000)}


@app.get("/api/creation-tasks/{task_id}/audio/recordings")
def list_creation_task_recordings(task_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not task.confirmed_script_id or not task.montage_plan_id:
        return {"items": []}
    service = _task_audio()
    plan = creation_task_montage_plan_from_payload(
        production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    current_narration_refs = {clip.source_ref for track in _effective_audio_timeline(plan).tracks
                              if track.role == "narration" for clip in track.clips}
    items = []
    for record in reversed(production_repo.list_artifact_records(workspace_id, task_id)):
        if record.kind != "task_recording":
            continue
        try:
            _, duration_ms = service.recording(workspace_id, task_id, record.logical_ref)
        except (KeyError, ValueError, FileNotFoundError):
            continue  # Failed or obsolete uploads are never offered as current narration.
        items.append({"source_ref": record.logical_ref, "artifact_id": record.id,
                      "audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{record.id}",
                      "duration_ms": duration_ms, "created_at": record.created_at.isoformat(),
                      "applied": record.logical_ref in current_narration_refs})
    return {"items": items}


@app.post("/api/creation-tasks/{task_id}/audio/recordings")
def save_creation_task_recording(task_id: str, request: Request, audio: UploadFile = File(...)):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if creation_execution_mode == "scheduled":
        raise HTTPException(status_code=503, detail="电脑录音校验尚未接入整机许可")
    if task.stage not in {"montage", "review"} or not task.confirmed_script_id or not task.montage_plan_id:
        raise HTTPException(status_code=409, detail="当前任务尚无可替换旁白的故事板")
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in {".webm", ".mp4", ".m4a", ".ogg", ".wav"}:
        raise HTTPException(status_code=422, detail="当前录音格式不可用，请使用 WebM、MP4、Ogg 或 WAV")

    def produce(path: Path) -> None:
        total = 0
        with path.open("wb") as output:
            while chunk := audio.file.read(1024 * 1024):
                total += len(chunk)
                if total > 50 * 1024 * 1024:
                    raise ValueError("电脑录音不能超过 50 MB")
                output.write(chunk)
        if total == 0:
            raise ValueError("电脑录音为空")

    try:
        service = _task_audio()
        record = service.publish(
            workspace_id, task_id, kind="task_recording",
            parameters={"script_id": task.confirmed_script_id, "montage_plan_id": task.montage_plan_id},
            input_refs=[], produce=produce, suffix=suffix,
        )
        _, duration_ms = service.recording(workspace_id, task_id, record.logical_ref)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"电脑录音未保存：{exc}") from exc
    except (KeyError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail="电脑录音未保存，请检查录音格式和本地存储") from exc
    return {"source_ref": record.logical_ref, "artifact_id": record.id,
            "audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{record.id}",
            "duration_ms": duration_ms, "applied": False}


@app.get("/api/creation-tasks/{task_id}/audio/artifacts/{artifact_id}")
def get_creation_task_audio_artifact(task_id: str, artifact_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    try:
        record = production_repo.get_artifact_record(workspace_id, artifact_id)
        if record.task_id != task_id:
            raise KeyError(artifact_id)
        path = _task_audio().source(workspace_id, task_id, record.logical_ref)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="当前任务的音频不存在") from exc
    audio_media_types = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg",
                         ".m4a": "audio/mp4", ".mp4": "audio/mp4"}
    return FileResponse(path, media_type=audio_media_types.get(path.suffix))


@app.get("/api/creation-tasks/{task_id}/audio/waveform/{clip_id}")
def get_creation_task_audio_waveform(task_id: str, clip_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    try:
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        clip = next(clip for track in _effective_audio_timeline(plan).tracks for clip in track.clips if clip.id == clip_id)
        source = _task_audio().source(workspace_id, task_id, clip.source_ref)
        return audio_waveform(source, settings.effective_temporary_root_path / "audio-waveforms" / workspace_id)
    except (KeyError, StopIteration, ValueError, AudioWaveformError) as exc:
        raise HTTPException(status_code=422, detail="无法读取所选音频的波形，请检查源文件") from exc


@app.post("/api/creation-tasks/{task_id}/audio/listen")
def listen_creation_task_audio(task_id: str, request: Request, payload: AudioListenRequest):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.current_timeline_version_id != payload.base_version_id:
        raise HTTPException(status_code=409, detail="时间线已经变化，请刷新后试听")
    try:
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        graph = _effective_audio_timeline(plan)
        service = _task_audio()
        record = service.publish(workspace_id, task_id, kind="audio_preview",
            parameters={"audio_timeline": graph.model_dump(), "start_seconds": payload.start_seconds,
                        "end_seconds": payload.end_seconds, "solo_track_ids": sorted(payload.solo_track_ids)},
            input_refs=list(dict.fromkeys(clip.source_ref for track in graph.tracks for clip in track.clips)),
            produce=lambda path: render_audio_mix(graph, plan.duration_seconds,
                lambda ref: service.source(workspace_id, task_id, ref), path, start_seconds=payload.start_seconds,
                end_seconds=payload.end_seconds, solo_track_ids=payload.solo_track_ids))
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "试听生成失败，请检查音频和外接盘") from exc
    return {"audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{record.id}"}


def _preview_clip_original_audio(task_id: str, request: Request, payload: CreationTaskEditPreviewRequest):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    refs = payload.selected_object_refs
    if len(refs) != 1 or not refs[0].startswith("clip:"):
        raise HTTPException(status_code=422, detail="请先选择一个镜头，再提取原声。")
    plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    clip = next((item for item in plan.clips if item.id == refs[0][5:]), None)
    if clip is None:
        raise HTTPException(status_code=404, detail="所选镜头不存在。")
    if clip.person_render_id:
        raise HTTPException(status_code=409, detail="生成口型片段使用的是配音；提取真实原声请先选择原视频镜头。")
    graph = _effective_audio_timeline(plan)
    tracks = [track for track in graph.tracks if track.role == "original"]
    if len(tracks) != 1:
        raise HTTPException(status_code=409, detail="需要唯一的原声音轨才能添加镜头原声。")
    track = tracks[0]
    if clip.locked or track.locked:
        raise HTTPException(status_code=409, detail="镜头或原声音轨已锁定，请先解锁。")
    if track.muted:
        raise HTTPException(status_code=409, detail="原声音轨已静音，请先取消静音再预览。")
    start, end = round(clip.timeline_start_seconds * 1000), round(clip.timeline_end_seconds * 1000)
    if any(item.timeline_start_ms < end and item.timeline_start_ms + item.duration_ms > start for item in track.clips):
        raise HTTPException(status_code=409, detail="该范围已有原声，请编辑现有音频片段，避免重复叠加。")
    service = _task_audio()
    try:
        source = production_repo.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
        reference = production_repo.workspace_media_reference_for_asset(workspace_id, clip.asset_id)
        source_start, source_end = round(clip.source_start_seconds * 1000), round(clip.source_end_seconds * 1000)
        rate = (source_end-source_start)/(end-start)
        record = service.publish(workspace_id, task_id, kind="audio_source",
            parameters={"action":"extract_clip_original", "clip_id":clip.id, "source_start_ms":source_start,
                "source_end_ms":source_end, "rate":rate, "duration_ms":end-start}, input_refs=[reference],
            produce=lambda output: render_stretched_audio(source, source_start, source_end, rate, end-start, output))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "无法提取原声，请检查素材是否包含音轨、FFmpeg和外接盘。") from exc
    return preview_creation_task_audio(task_id, request, AudioEditRequest(
        base_version_id=task.current_timeline_version_id, action="add", target_id=track.id,
        raw_command=payload.raw_command, values={"source_ref":record.logical_ref, "name":"镜头原声",
            "source_start_ms":0, "source_end_ms":end-start, "timeline_start_ms":start}))


@app.post("/api/creation-tasks/{task_id}/audio/preview", response_model=CreationTaskEditPreviewResponse)
def preview_creation_task_audio(task_id: str, request: Request, payload: AudioEditRequest):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.current_timeline_version_id != payload.base_version_id:
        raise HTTPException(status_code=409, detail="时间线已经变化，请刷新后重新预览音频修改")
    try:
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        before = _effective_audio_timeline(plan)
        service = _task_audio()
        edit = payload
        if payload.action == "replace_narration":
            if creation_execution_mode == "scheduled":
                raise HTTPException(status_code=503, detail="电脑录音校验尚未接入整机许可")
            if set(payload.values) != {"source_ref"} or any(clip.person_render_id for clip in plan.clips):
                raise ValueError("人物口型或附加参数不能随整条旁白录音替换保留")
            source_ref = payload.values["source_ref"]
            if not isinstance(source_ref, str):
                raise ValueError("请选择当前任务已保存的电脑录音")
            recording, measured_ms = service.recording(workspace_id, task_id, source_ref, verify_digest=True)
            duration_ms = round(plan.duration_seconds * 1000)
            if not duration_ms - 20 <= measured_ms <= duration_ms + 250:
                raise ValueError("录音时长与当前画面不符，请重录到接近整片时长")
            edit = payload.model_copy(update={"values": {"source_ref": source_ref, "source_end_ms": duration_ms}})
            after = apply_audio_edit(before, edit, duration_ms)
            original_ref = next(clip.source_ref for track in before.tracks if track.role == "narration" for clip in track.clips)
            service.source(workspace_id, task_id, original_ref)
            if original_ref.startswith("narration://artifact/"):
                if original_ref != production_repo.narration_artifact_reference(task.narration_cache_key or ""):
                    raise ValueError("当前旁白引用与任务版本不一致，请刷新后重试")
                before_url = f"/api/creation-tasks/{task_id}/narration/audio"
            else:
                original = next(record for record in production_repo.list_artifact_records(workspace_id, task_id)
                                if record.logical_ref == original_ref)
                before_url = f"/api/creation-tasks/{task_id}/audio/artifacts/{original.id}"
            preview_media = {
                "before": {"ref": original_ref, "label": "当前旁白", "detail": "当前版本的完整旁白",
                           "audio_url": before_url},
                "after": {"ref": source_ref, "label": "本次电脑录音", "detail": "确认后替换整条旁白；画面时长不变",
                          "audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{recording.id}"},
            }
            start = 0.0
        else:
            after = apply_audio_edit(before, payload, round(plan.duration_seconds*1000))
            service.validate(workspace_id, task_id, after, plan.duration_seconds)
            start, end = audio_edit_preview_range(before, after, payload, plan.duration_seconds)
            previews = []
            for graph in (before, after):
                record = service.publish(workspace_id, task_id, kind="audio_preview",
                    parameters={"audio_timeline": graph.model_dump(), "start_seconds": start, "end_seconds": end},
                    input_refs=list(dict.fromkeys(clip.source_ref for track in graph.tracks for clip in track.clips)),
                    produce=lambda path, graph=graph: render_audio_mix(graph, plan.duration_seconds,
                        lambda ref: service.source(workspace_id, task_id, ref), path, start_seconds=start, end_seconds=end))
                previews.append(record)
            preview_media = {side: {"ref": record.logical_ref, "label": "修改前" if side == "before" else "音轨修改后",
                "detail": f"试听 {start:.1f}–{end:.1f} 秒；完整音轨在确认后生效",
                "audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{record.id}"}
                for side, record in zip(("before", "after"), previews)}
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else "本地音频预览失败，请检查源文件、FFmpeg 与外接盘"
        raise HTTPException(status_code=422, detail=detail) from exc
    if production_repo.get_creation_task(workspace_id, task_id).current_timeline_version_id != payload.base_version_id:
        raise HTTPException(status_code=409, detail="试听生成期间时间线发生变化，请重新预览")
    base = _ensure_montage_base_version(workspace_id, task, plan)
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}", workspace_id=workspace_id, task_id=task_id,
        raw_command=payload.raw_command, context_stage="montage", selected_object_refs=[f"audio:{payload.target_id}"],
        playhead_ms=round(start*1000), intent_type="edit_audio", scope={"kind": "audio", "target_id": payload.target_id},
        constraints={"preserve_video": True}, confidence=1, status="interpreted"))
    labels = {"add": "添加音频片段", "update": "修改音频片段", "delete": "删除音频片段（保留时间空隙）", "split": "分割音频片段", "duplicate": "复制音频片段", "track": "修改音轨设置", "replace_narration": "用本机录音替换整条旁白"}
    summary = labels[payload.action]
    if payload.action == "add" and any(track.id == payload.target_id and track.role == "original" for track in before.tracks):
        summary = "添加原声，与现有音轨混合；画面和配音不变"
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, intent_id=intent.id, summary=summary, target_refs=[f"audio:{payload.target_id}"],
        operation={"operation_type": "EditAudioTimeline", "payload": {"base_version_id": base.id, "audio_timeline": after.model_dump(),
            "edit": edit.model_dump(), "expected_audio_timeline": before.model_dump(),
            **({"recording_duration_ms": measured_ms, "recording_sha256": recording.sha256}
               if payload.action == "replace_narration" else {})}},
        invalidated_refs=_logical_montage_output_refs(plan),
        preview=preview_media,
        external_effects=(["新录音使用原画面与字幕时轴；确认后旧成片失效，需核对字幕与重新渲染。"]
                          if payload.action == "replace_narration" else [])))
    return _edit_preview_response(intent, proposal)


@app.post("/api/creation-tasks/{task_id}/graphics/import")
def import_creation_task_graphic(task_id: str, request: Request, image: UploadFile = File(...)):
    from PIL import Image
    workspace_id=current_workspace_id(request)
    _creation_task_or_404(workspace_id,task_id)
    try:
        return import_graphic_source(production_repo,artifact_store,settings.effective_temporary_root_path,workspace_id,task_id,image)
    except (ValueError,OSError,Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else "图片导入失败，请检查格式、尺寸和外接盘") from exc


@app.get("/api/creation-tasks/{task_id}/graphics/sources")
def get_creation_task_graphic_sources(task_id: str, request: Request):
    workspace_id=current_workspace_id(request)
    _creation_task_or_404(workspace_id,task_id)
    return graphic_sources(production_repo,workspace_id,task_id)


@app.get("/api/creation-tasks/{task_id}/graphics/source/{artifact_id}")
def get_creation_task_graphic_source(task_id: str,artifact_id: str,request: Request):
    workspace_id=current_workspace_id(request)
    _creation_task_or_404(workspace_id,task_id)
    try:
        record=production_repo.get_artifact_record(workspace_id,artifact_id)
        if record.task_id!=task_id or record.kind!='graphic_source':
            raise KeyError(artifact_id)
        path=resolve_graphic_source(production_repo,artifact_store,workspace_id,task_id,record.logical_ref)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise HTTPException(status_code=404,detail="当前任务图片不存在") from exc
    return FileResponse(path,media_type="image/png")


@app.get("/api/creation-tasks/{task_id}/visual/artifacts/{artifact_id}")
def get_creation_task_visual_preview(task_id: str, artifact_id: str, request: Request):
    workspace_id=current_workspace_id(request)
    _creation_task_or_404(workspace_id,task_id)
    try:
        record=production_repo.get_artifact_record(workspace_id,artifact_id)
        if record.task_id != task_id or record.kind not in {"color_preview", "speed_preview", "canvas_preview", "subtitle_style_preview", "transition_preview", "graphic_preview", "compound_preview", "silence_preview"}:
            raise KeyError(artifact_id)
        path=artifact_store.resolve_file(record.logical_ref)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise HTTPException(status_code=404,detail="当前任务的画面预览不存在") from exc
    return FileResponse(path,media_type="image/png" if record.kind == "subtitle_style_preview" else "video/mp4")


@app.post("/api/creation-tasks/{task_id}/visual/preview",response_model=CreationTaskEditPreviewResponse)
def preview_creation_task_visual(task_id: str, request: Request, payload: VisualEditRequest):
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    if task.current_timeline_version_id != payload.base_version_id:
        raise HTTPException(status_code=409,detail="时间线已经变化，请刷新后重新预览")
    plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
    clips=[clip for clip in plan.clips if clip.id in payload.clip_ids]
    if len(clips) != len(set(payload.clip_ids)):
        raise HTTPException(status_code=404,detail="所选镜头不存在")
    if any(clip.locked for clip in clips):
        raise HTTPException(status_code=409,detail="所选范围包含锁定镜头，请先解锁或缩小范围")
    if all(VisualEffects.model_validate(clip.visual_effects)==payload.effects for clip in clips):
        raise HTTPException(status_code=422,detail="画面参数没有变化")
    try:
        for target in clips:
            fade_filters(with_visual_effects(target, payload.effects.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    # Show a representative selected clip; all targets and the exact shared parameters remain explicit.
    clip=clips[0]
    preview_limit = None if (payload.effects.fade_out_seconds or clip.visual_effects.get("fade_out_seconds")) else 5
    def resolve_target(target):
        preview_target=replace(target)
        if target.person_render_id:
            person=production_repo.get_person_render(workspace_id,task_id,target.person_render_id)
            source=_person_render_media_path(person)
            cue=next(cue for cue in plan.cues if cue.id==target.cue_id)
            if source is None or person.narration_segment_id != cue.narration_segment_id:
                raise ValueError("当前人物口型产物已失效，请先重新生成")
        else:
            source=production_repo.resolve_clip_media_reference(workspace_id,target.asset_id,target.source_path)
        return preview_target,source
    try:
        preview_clip,source=resolve_target(clip)
        if payload.effects.beauty_strength or payload.effects.skin_exposure:
            for target in clips:
                resolved,path=resolve_target(target)
                _portrait_processor(workspace_id).process(path,resolved.source_start_seconds,resolved.source_end_seconds,
                    payload.effects.beauty_strength,payload.effects.skin_exposure)
        records=[]
        for effects in (clip.visual_effects,payload.effects.model_dump()):
            preview_clip=with_visual_effects(preview_clip,effects)
            records.append(publish_color_preview(production_repo,artifact_store,settings.effective_temporary_root_path,
                workspace_id,task_id,preview_clip,source,_portrait_processor(workspace_id),aspect_ratio=plan.aspect_ratio,max_seconds=preview_limit))
    except (ValueError,OSError,subprocess.SubprocessError,StopIteration) as exc:
        raise HTTPException(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else "本地画面预览失败，请检查所选源素材和 FFmpeg") from exc
    if production_repo.get_creation_task(workspace_id,task_id).current_timeline_version_id != payload.base_version_id:
        raise HTTPException(status_code=409,detail="预览期间时间线发生变化，请重新预览")
    base=_ensure_montage_base_version(workspace_id,task,plan)
    refs=[f"clip:{clip.id}" for clip in clips]
    intent=production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        raw_command=payload.raw_command,context_stage="montage",selected_object_refs=refs,playhead_ms=round(clip.timeline_start_seconds*1000),intent_type="visual_adjustment",
        scope={"kind":"clips","clip_ids":[clip.id for clip in clips]},constraints={"preserve_timing":True,"preserve_audio":True,"preserve_face_geometry":True},confidence=1,status="interpreted"))
    detail=(f"曝光 {payload.effects.exposure:+.1f} EV · 对比度 {payload.effects.contrast:.2f} · "
            f"饱和度 {payload.effects.saturation:.2f} · 冷暖 {payload.effects.temperature:+.2f} · "
            f"磨皮 {payload.effects.beauty_strength:.0%} · 肤色曝光 {payload.effects.skin_exposure:+.2f} EV · "
            f"缩放 {payload.effects.zoom:g} 倍 · 旋转 {payload.effects.rotation:g}° · "
            f"位置 {payload.effects.offset_x:+.0%} / {payload.effects.offset_y:+.0%} · "
            f"黑场淡入 {payload.effects.fade_in_seconds:g} 秒 / 淡出 {payload.effects.fade_out_seconds:g} 秒")
    proposal=production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        intent_id=intent.id,summary=f"调整 {len(clips)} 个镜头的画面效果",target_refs=refs,
        operation={"operation_type":"AdjustVisualEffects","payload":{"base_version_id":base.id,
            "effects":payload.effects.model_dump(),"expected_clips":{clip.id:asdict(clip) for clip in clips}}},
        invalidated_refs=_logical_montage_output_refs(plan),external_effects=[],
        preview={side:{"ref":record.logical_ref,"label":"当前画面" if side=="before" else detail,
            "detail":f"预览镜头 {plan.clips.index(clip)+1} 的{'完整区间（包含结尾淡出）' if preview_limit is None else '开头（最多 5 秒）'}；确认将应用到 {len(clips)} 个指定镜头，不改变声音和时长。",
            "video_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"}
            for side,record in zip(("before","after"),records)}))
    return _edit_preview_response(intent,proposal)


def _creation_task_montage_response(
    plan: CreationTaskMontagePlan,
) -> CreationTaskMontagePlanResponse:
    return CreationTaskMontagePlanResponse.model_validate(
        {
            "id": plan.id,
            "aspect_ratio": plan.aspect_ratio,
            "presenter_share_target": plan.presenter_share_target,
            "presenter_share_actual": round(
                100 * sum(clip.timeline_end_seconds - clip.timeline_start_seconds
                          for clip in plan.clips if clip.person_render_id) / plan.duration_seconds, 1
            ) if plan.duration_seconds else 0,
            "task_id": plan.task_id,
            "script_id": plan.script_id,
            "script_title": plan.script_title,
            "status": plan.status,
            "cues": [asdict(cue) for cue in plan.cues],
            "clips": [
                {
                    **asdict(clip),
                    "source_reference": clip.source_path,
                }
                for clip in plan.clips
            ],
            "duration_seconds": plan.duration_seconds,
            "low_confidence_clip_count": plan.low_confidence_clip_count,
            "narration_reference": plan.narration_audio_path,
            "graphic_overlays": plan.graphic_overlays,
            "audio_timeline": _effective_audio_timeline(plan).model_dump(),
            "output_url": (
                f"/api/creation-tasks/{plan.task_id}/output"
                if plan.status == "rendered" and plan.output_path
                else None
            ),
            "subtitle_url": (
                f"/api/creation-tasks/{plan.task_id}/output/subtitles"
                if plan.status == "rendered" and plan.subtitle_path
                else None
            ),
            "message": plan.message,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }
    )


def _resolve_creation_task_voice_selection(
    *,
    workspace_id: str,
    task: CreationTask,
    plan: CreationPlan,
    voice_profile_id: str,
    voice_version: str,
) -> tuple[CreationPlanVoiceReference, list[CreationPlanPronunciationReference]]:
    try:
        inputs = production_repo.resolve_creation_task_narration_inputs(
            workspace_id=workspace_id,
            task_id=task.id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
    except KeyError as exc:
        raise MediaAuthorityError(
            reason="voice_identity_missing",
            impact="当前任务没有绑定声音，未调用声音模型。",
            repair_action="选择当前工作区中存在的声音版本。",
        ) from exc
    voice = inputs.voice
    expected_language = {
        "zh-CN": "zh",
        "en-US": "en",
        "zh-CN/en-US": "zh-en",
    }[plan.language]
    if voice.language not in {expected_language, "zh-en"}:
        raise MediaAuthorityError(
            reason="voice_language_unavailable",
            impact="所选声音不支持当前任务语言，未调用声音模型。",
            repair_action="选择中文声音版本，或新建与任务语言一致的授权声音。",
        )
    authorization_failure = voice.authorization.failure_for(
        content_type="voice",
        usage_scope="narration",
    )
    if authorization_failure is not None:
        raise MediaAuthorityError(
            reason=authorization_failure,
            impact="所选声音授权当前不可用于配音，未调用声音模型。",
            repair_action="更新声音授权状态和配音使用范围后再选择。",
        )
    try:
        reference_audio = production_repo.resolve_workspace_media_reference(
            workspace_id,
            voice.reference_audio_path,
            require_registered=True,
        )
    except (MediaAuthorityError, OSError) as exc:
        raise MediaAuthorityError(
            reason="voice_reference_wav_unavailable",
            impact="所选声音没有可读取的工作区 WAV，未调用声音模型。",
            repair_action="重新导入 1–120 秒的已授权 WAV，再登记声音新版本。",
        ) from exc
    if (
        reference_audio.suffix.lower() != ".wav"
        or not reference_audio.is_file()
        or reference_audio.stat().st_size == 0
    ):
        raise MediaAuthorityError(
            reason="voice_reference_wav_unavailable",
            impact="所选声音没有有效 WAV，未调用声音模型。",
            repair_action="重新导入 1–120 秒的已授权 WAV，再登记声音新版本。",
        )
    model_status = next(
        (status for status in model_catalog.list_statuses() if status.model.id == voice.model_id),
        None,
    )
    if model_status is None or not model_status.ready:
        raise MediaAuthorityError(
            reason="voice_model_unavailable",
            impact="所选声音要求的本地模型尚未就绪，未调用声音模型。",
            repair_action=(
                model_status.message
                if model_status is not None
                else "为声音档案选择当前已安装的本地声音模型。"
            ),
        )
    try:
        person = production_repo.get_person_profile(
            workspace_id,
            voice.person_profile_id,
            voice.person_version,
        )
        person_authorization = production_repo.get_authorization(
            workspace_id,
            person.authorization.id,
        )
    except KeyError as exc:
        raise MediaAuthorityError(
            reason="voice_person_identity_missing",
            impact="声音版本关联的人物身份不完整，未调用声音模型。",
            repair_action="重新登记与现有人物版本关联的授权声音。",
        ) from exc
    return (
        CreationPlanVoiceReference(
            profile_id=voice.id,
            version=voice.version,
            person_profile_id=voice.person_profile_id,
            person_version=voice.person_version,
            person_authorization_id=person_authorization.id,
            person_authorization_version=person_authorization.version,
            authorization_id=voice.authorization.id,
            authorization_version=voice.authorization.version,
            language=voice.language,
            model_id=voice.model_id,
        ),
        [
            CreationPlanPronunciationReference(
                id=entry.id,
                version=entry.version,
                scope=entry.scope,
                scope_reference=entry.scope_reference,
                language=entry.language,
            )
            for entry in inputs.pronunciation_entries
        ],
    )


def _creation_task_voice_options(
    workspace_id: str,
    task: CreationTask,
    plan: CreationPlan,
) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    for voice in production_repo.list_versioned_voice_profiles(workspace_id):
        try:
            person = production_repo.get_person_profile(
                workspace_id,
                voice.person_profile_id,
                voice.person_version,
            )
        except KeyError:
            person = None
        reason = None
        try:
            _resolve_creation_task_voice_selection(
                workspace_id=workspace_id,
                task=task,
                plan=plan,
                voice_profile_id=voice.id,
                voice_version=voice.version,
            )
        except MediaAuthorityError as exc:
            reason = exc.reason
        options.append(
            {
                "profile_id": voice.id,
                "version": voice.version,
                "person_display_name": (
                    person.display_name if person is not None else "人物档案缺失"
                ),
                "language": voice.language,
                "model_id": voice.model_id,
                "runnable": reason is None,
                "runnable_reason": reason,
            }
        )
    return options


def _default_subtitle_style() -> dict[str, object]:
    return {
        "name": "default",
        "font_family": "PingFang SC",
        "font_size": 40,
        "primary_color": "#FFFFFF",
        "emphasis_color": "#FFD54F",
        "position": "bottom",
        "bilingual_order": "source_then_translation",
    }


def _normalize_subtitle_text_and_breaks(
    text: str,
    line_breaks: list[int] | None,
) -> tuple[str, list[int]]:
    try:
        return normalize_subtitle_text_and_breaks(text,line_breaks)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


def _subtitle_track_from_montage(
    workspace_id: str,
    task: CreationTask,
    version: TimelineVersion,
    montage: CreationTaskMontagePlan,
) -> SubtitleTrack | None:
    if montage.status != "rendered" or not montage.subtitle_path:
        return None
    transform_id = version.snapshot.get("transform_run_id")
    if not isinstance(transform_id, str) or not transform_id:
        transform_id = version.snapshot.get("reused_job_id")
        if isinstance(transform_id, str) and transform_id:
            transform_id = f"transform_{transform_id}"
    if not isinstance(transform_id, str) or not transform_id:
        return None
    cues: list[SubtitleCue] = []
    cursor = 0.0
    for cue in montage.cues:
        start = cursor
        end = min(start + cue.duration_seconds, montage.duration_seconds)
        normalized_text, line_breaks = _normalize_subtitle_text_and_breaks(
            cue.text,
            None,
        )
        cues.append(
            SubtitleCue(
                id=cue.id,
                source_cue_id=None,
                text=normalized_text,
                start_seconds=start,
                end_seconds=end,
                line_breaks=line_breaks,
            )
        )
        cursor = end
    source_language = production_repo.get_creation_plan(workspace_id, task.plan_id).language
    track_language = "en-US" if source_language == "en-US" else "zh-CN"
    return SubtitleTrack(
        id=f"subtitle_{version.id}_{'en' if track_language == 'en-US' else 'zh'}",
        workspace_id=workspace_id,
        task_id=task.id,
        timeline_version_id=version.id,
        language=track_language,
        source_language=None,
        style=dict(montage.cues[0].subtitle_style) if montage.cues and montage.cues[0].subtitle_style else _default_subtitle_style(),
        cues=cues,
        generation_method="timeline",
        model_id=None,
        artifact_ref=montage.subtitle_path,
        transform_id=transform_id,
        status="valid",
    )


def _current_subtitle_tracks(
    workspace_id: str,
    task: CreationTask,
    *,
    current_version: TimelineVersion | None = None,
    montage: CreationTaskMontagePlan | None = None,
) -> list[SubtitleTrack]:
    if current_version is None and task.current_timeline_version_id:
        try:
            current_version = production_repo.get_timeline_version(
                workspace_id, task.current_timeline_version_id
            )
        except KeyError:
            current_version = None
    if current_version is None:
        return []
    tracks = production_repo.list_subtitle_tracks(
        workspace_id, task.id, timeline_version_id=current_version.id
    )
    if tracks:
        return tracks
    if current_version.snapshot.get("subtitle_track_ids") == []:
        return []
    if montage is None:
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
        except KeyError:
            return []
    projected = _subtitle_track_from_montage(
        workspace_id, task, current_version, montage
    )
    return [projected] if projected is not None else []


def _creation_task_workspace_response(
    workspace_id: str,
    task: CreationTask,
) -> CreationTaskWorkspaceResponse:
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    selected_topic = next(
        (
            candidate
            for candidate in production_repo.list_topic_candidates(workspace_id, task.id)
            if candidate.id == task.selected_topic_id
        ),
        None,
    )
    topic_evidence = [
        reference
        for reference in production_repo.list_topic_evidence_references(workspace_id, task.id)
        if selected_topic is not None and reference.candidate_id == selected_topic.id
    ]
    artifact = (
        production_repo.get_narration_artifact(workspace_id, task.narration_cache_key)
        if task.narration_cache_key
        else None
    )
    try:
        montage = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task.id)
        )
    except KeyError:
        montage = None
    timeline_versions = production_repo.list_timeline_versions(workspace_id, task.id)
    current_version = next(
        (
            version
            for version in timeline_versions
            if version.id == task.current_timeline_version_id
        ),
        None,
    )
    can_undo = _undo_target_versions(workspace_id, task)[0] is not None
    person_provider = _lipsync_provider(plan.lipsync_quality)
    person_render_available = person_provider.ready
    subtitle_tracks = _current_subtitle_tracks(
        workspace_id, task, current_version=current_version, montage=montage
    )
    cover_assets = (
        production_repo.list_cover_assets(
            workspace_id,
            task.id,
            timeline_version_id=current_version.id,
        )
        if current_version is not None
        else []
    )
    translation_provider = (
        subtitle_translation_provider_from_settings(settings)
        if isinstance(settings, Settings)
        else None
    )
    last_translation = next((
        event for event in reversed(production_repo.list_task_events(workspace_id, task.id))
        if event.event_type == "subtitle_translation_returned"
        and translation_provider is not None
        and event.payload.get("model_id") == translation_provider.model_id
        and event.payload.get("provider_name") == translation_provider.provider_name
    ), None)
    translation_failure = (
        last_translation.payload
        if last_translation and last_translation.payload.get("status") != "succeeded"
        else None
    )
    cover_frame_available = bool(
        montage is not None
        and montage.status == "rendered"
        and montage.output_path
        and shutil.which("ffmpeg")
        and shutil.which("ffprobe")
    )
    image_capability = unconfigured_image_generation_capability()
    return CreationTaskWorkspaceResponse.model_validate(
        {
            "task": {
                **task.__dict__,
                "timeline_sequence": current_version.sequence if current_version else 0,
                "can_undo": can_undo,
                "can_redo": _redo_target_versions(workspace_id, task)[0] is not None,
                "can_revise_script": task.can_revise_script,
            },
            "plan": _creation_plan_preview_response(plan).model_dump(),
            "selected_topic": (
                {
                    "id": selected_topic.id,
                    "run_id": selected_topic.run_id,
                    "title": selected_topic.title,
                    "angle": selected_topic.angle,
                    "audience": selected_topic.audience,
                    "outline": selected_topic.outline,
                    "score": selected_topic.score,
                    "selection_status": selected_topic.selection_status,
                    "created_at": selected_topic.created_at,
                }
                if selected_topic is not None
                else None
            ),
            "topic_evidence": [
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
                for reference in topic_evidence
            ],
            "scripts": [
                asdict(script)
                for script in production_repo.list_creation_task_scripts(workspace_id, task.id)
            ],
            "available_voices": _creation_task_voice_options(workspace_id, task, plan),
            "narration": (
                _creation_task_narration_response(task, artifact).model_dump()
                if artifact is not None
                else None
            ),
            "portrait_available": _portrait_processor(workspace_id).available,
            "person_render_capability": {
                "available": person_render_available,
                "model_id": person_provider.model_id,
                "message": person_provider.message,
                "repair_action": (
                    None
                    if person_render_available
                    else "配置本地人物口型运行环境后重试。"
                ),
            },
            "person_renders": [
                _person_render_response(render).model_dump()
                for render in production_repo.list_person_renders(workspace_id, task.id)
            ],
            "montage": (
                _creation_task_montage_response(montage).model_dump()
                if montage is not None
                else None
            ),
            "subtitle_tracks": [asdict(track) for track in subtitle_tracks],
            "cover_asset": asdict(cover_assets[-1]) if cover_assets else None,
            "subtitle_translation_capability": {
                "available": bool(translation_provider and translation_provider.ready),
                "model_id": translation_provider.model_id if translation_provider else None,
                "message": (
                    f"上次翻译未完成：{translation_failure['failure_reason']}"
                    if translation_failure
                    else
                    translation_provider.message
                    if translation_provider
                    else "当前测试运行时未配置文字模型翻译能力。"
                ),
                "repair_action": (
                    translation_failure.get("repair_action")
                    if translation_failure
                    else
                    None
                    if translation_provider and translation_provider.ready
                    else translation_provider.message
                    if translation_provider
                    else "配置完整运行时设置后重试。"
                ),
            },
            "cover_frame_capability": {
                "available": cover_frame_available,
                "model_id": "ffmpeg+Pillow" if cover_frame_available else None,
                "message": (
                    "可以从当前成片选择真实画面生成封面。"
                    if cover_frame_available
                    else "先生成可播放成片，并确认本机 FFmpeg 可用。"
                ),
                "repair_action": (
                    None
                    if cover_frame_available
                    else "先完成成片渲染或修复 FFmpeg/FFprobe 环境。"
                ),
            },
            "image_generation_capability": {
                "available": False,
                "model_id": None,
                "message": image_capability.impact,
                "repair_action": image_capability.remediation,
            },
        }
    )
@app.post(
    "/api/assistant/plan/turn",
    response_model=WorkspaceAssistantPlanTurnResponse,
)
def workspace_assistant_plan_turn(
    request: Request,
    payload: WorkspaceAssistantPlanTurnRequest,
) -> WorkspaceAssistantPlanTurnResponse:
    try:
        reply, preview = run_plan_turn(
            settings,
            workspace_id=current_workspace_id(request),
            conversation_id=str(payload.conversation_id),
            message=payload.message.strip(),
            authorization=request.headers.get("authorization"),
            cookie=request.headers.get("cookie"),
        )
    except AssistantUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    plan_preview = CreationPlanPreviewResponse.model_validate(preview) if preview is not None else None
    return WorkspaceAssistantPlanTurnResponse(
        reply=reply,
        provider=settings.effective_llm_provider,
        model=settings.effective_llm_model,
        plan_preview=plan_preview,
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
    tasks = production_repo.list_creation_tasks(
        current_workspace_id(request),
        include_private=_content_include_private(request),
    )
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
                style_instruction=str(plan.template_settings.get("style_instruction", "")),
                script_structure=tuple(plan.template_settings.get("script_structure", [])),
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
    "/api/creation-tasks/{task_id}",
    response_model=CreationTaskWorkspaceResponse,
)
def get_creation_task_workspace(
    task_id: str,
    request: Request,
) -> CreationTaskWorkspaceResponse:
    workspace_id = current_workspace_id(request)
    return _creation_task_workspace_response(
        workspace_id,
        _creation_task_or_404(workspace_id, task_id),
    )


def _assistant_script_inputs(workspace_id: str, task_id: str):
    task = _creation_task_or_404(workspace_id, task_id)
    if task.stage != "script" or task.confirmed_script_id:
        raise HTTPException(status_code=409, detail="当前任务不在待确认文案阶段")
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    topic = next((item for item in production_repo.list_topic_candidates(workspace_id, task_id)
                  if item.id == task.selected_topic_id), None)
    if topic is None:
        raise HTTPException(status_code=409, detail="请先确认本次选题")
    try:
        sources, input_references, _ = _creation_task_topic_inputs(
            workspace_id, task, plan, cloud_egress=True,
        )
    except _CreationTaskTopicPreflightError as exc:
        raise HTTPException(status_code=409, detail={
            "reason": exc.reason, "impact": exc.impact, "repair_action": exc.repair_action,
        }) from exc
    return task, plan, topic, sources, input_references


@app.get("/api/creation-tasks/{task_id}/assistant/script-context")
def get_assistant_script_context(task_id: str, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    task, plan, topic, sources, _ = _assistant_script_inputs(workspace_id, task_id)
    scripts = production_repo.list_creation_task_scripts(workspace_id, task.id)
    draft = scripts[0] if scripts and scripts[0].selection_status == "draft" else None
    return {
        "task_id": task.id,
        "goal": plan.goal,
        "language": plan.language,
        "duration_target_seconds": plan.duration_target_seconds,
        "topic": {"title": topic.title, "angle": topic.angle, "audience": topic.audience,
                  "outline": topic.outline},
        "sources": [{"reference_id": source.reference_id, "title": source.title,
                     "text": source.text} for source in sources],
        "draft": ({"id": draft.id, "title": draft.title,
                   "sections": [asdict(section) for section in draft.sections]}
                  if draft is not None else None),
    }


@app.post(
    "/api/creation-tasks/{task_id}/assistant/script-draft",
    response_model=CreationTaskScriptResponse,
)
def save_assistant_script_draft(
    task_id: str, request: Request, payload: CreationTaskScriptConfirmRequest,
) -> CreationTaskScriptResponse:
    workspace_id = current_workspace_id(request)
    task, plan, topic, _, input_references = _assistant_script_inputs(workspace_id, task_id)
    sections = [CreationTaskScriptSection(**section.model_dump()) for section in payload.sections]
    allowed = {str(item["reference_id"]) for item in input_references}
    if any(not set(section.evidence_reference_ids).issubset(allowed) for section in sections):
        raise HTTPException(status_code=422, detail="文案引用了本次任务以外的资料")
    scripts = production_repo.list_creation_task_scripts(workspace_id, task_id)
    if scripts and scripts[0].selection_status == "draft" and task.stage_status == "waiting_for_user":
        try:
            draft = production_repo.update_assistant_script_draft(
                workspace_id, task_id, scripts[0].id, title=payload.title, sections=sections,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        run = ScriptGenerationRun(
            id=f"script_run_{uuid4().hex[:12]}", workspace_id=workspace_id,
            task_id=task_id, plan_id=plan.id, topic_id=topic.id, status="running",
            route_snapshot=_topic_route_snapshot(),
            prompt_schema_version="assistant-script-draft/v1",
            input_references=input_references,
        )
        try:
            production_repo.start_script_generation(run)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        draft = CreationTaskScript(
            id=f"script_{uuid4().hex[:12]}", workspace_id=workspace_id,
            task_id=task_id, run_id=run.id, topic_id=topic.id,
            title=payload.title, language=plan.language, sections=sections,
        )
        try:
            _, _, draft = production_repo.complete_script_generation(run.id, draft)
        except ValueError as exc:
            production_repo.fail_script_generation(
                run.id, reason="assistant_script_invalid",
                impact="本轮文案未保存，原有文案不变。",
                repair_action="检查文案内容和来源后重试。",
                retryable=True, provider_attempted=True,
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CreationTaskScriptResponse.model_validate(asdict(draft))


@app.get(
    "/api/creation-tasks/{task_id}/assistant/history",
    response_model=CreationTaskAssistantHistoryResponse,
)
def get_creation_task_assistant_history(task_id: str, request: Request) -> CreationTaskAssistantHistoryResponse:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    return CreationTaskAssistantHistoryResponse.model_validate({"items": [
        {"role": event.payload.get("role"), "message": event.payload.get("message"),
         "stage": event.payload.get("stage"), "script_id": event.payload.get("script_id"),
         "at": event.created_at}
        for event in production_repo.list_task_events(workspace_id, task_id)
        if event.event_type == "assistant_turn" and event.payload.get("role") in {"user", "assistant"}
    ]})


def _assistant_text_without_credentials(value: str) -> str:
    safe = value
    for known in (settings.effective_llm_api_key, settings.tencentcloud_secret_id,
                  settings.tencentcloud_secret_key):
        if known and len(known) >= 8:
            safe = safe.replace(known, "[已隐藏凭据]")
    for pattern in (
        r"(?i)(\bauthorization\s*:\s*bearer\s+)\S+",
        r"(?i)(\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*)\S+",
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
    ):
        safe = re.sub(pattern, lambda match: (match.group(1) if match.lastindex else "") + "[已隐藏凭据]", safe)
    return safe


def _assistant_turn_scope(
    workspace_id: str, task: CreationTask, payload: CreationTaskAssistantTurnRequest,
) -> tuple[str, list[str]]:
    """Authorize the requested view and selection from persisted task objects before Pi runs."""
    stage = payload.context_stage
    if task.stage == "script" or task.confirmed_script_id is None:
        if stage != "script":
            raise HTTPException(status_code=409, detail="当前任务尚未完成文案确认，不能使用剪辑对话")
        _assistant_script_inputs(workspace_id, task.id)
        if payload.selected_object_refs or payload.playhead_ms is not None:
            raise HTTPException(status_code=422, detail="文案对话不能选择剪辑对象")
        return "script", []
    if stage in {"topic", "script"} or task.stage not in {"narration", "montage", "review"}:
        raise HTTPException(status_code=409, detail="当前任务不能在所选阶段使用对话")
    if task.stage == "narration" and stage != "narration":
        raise HTTPException(status_code=409, detail="当前任务尚未生成可修改的故事板")

    narration = (production_repo.get_narration_artifact(workspace_id, task.narration_cache_key)
                 if task.narration_cache_key else None)
    if stage == "narration":
        if narration is None or narration.task_id != task.id or narration.script_id != task.confirmed_script_id:
            raise HTTPException(status_code=409, detail="当前任务没有可修改的配音")
        refs = {f"narration:{segment.id}" for segment in production_repo.list_narration_segments(
            workspace_id, task.id, task.confirmed_script_id,
        ) if segment.status == "ready"}
    else:
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="当前任务没有可修改的故事板") from exc
        if (montage.task_id != task.id or montage.script_id != task.confirmed_script_id
                or task.montage_plan_id != montage.id):
            raise HTTPException(status_code=409, detail="当前故事板不属于已确认文案")
        version = None
        if task.current_timeline_version_id:
            try:
                version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
            except KeyError as exc:
                raise HTTPException(status_code=409, detail="当前任务的时间线版本不可用") from exc
            if version.task_id != task.id:
                raise HTTPException(status_code=409, detail="当前时间线不属于此任务")
        if stage == "review" and (montage.status != "rendered" or not montage.output_path):
            raise HTTPException(status_code=409, detail="当前任务没有可查看的成片")
        refs = {f"clip:{clip.id}" for clip in montage.clips}
        if stage in {"montage", "review"}:
            refs.update(f"overlay:{overlay.id}" for overlay in montage.graphic_overlays)
            audio = _effective_audio_timeline(montage)
            refs.update(f"audio:{track.id}" for track in audio.tracks)
            refs.update(f"audio:{clip.id}" for track in audio.tracks for clip in track.clips)
            if narration is not None and narration.task_id == task.id and narration.script_id == task.confirmed_script_id:
                refs.update(f"narration:{segment.id}" for segment in production_repo.list_narration_segments(
                    workspace_id, task.id, task.confirmed_script_id,
                ) if segment.status == "ready")
        else:
            if version is None:
                raise HTTPException(status_code=409, detail="当前任务没有可修改的字幕时间线")
            tracks = [track for track in _current_subtitle_tracks(
                workspace_id, task, current_version=version, montage=montage,
            ) if track.status == "valid" and track.task_id == task.id
                      and track.timeline_version_id == version.id]
            if not tracks:
                raise HTTPException(status_code=409, detail="当前任务没有可修改的字幕")
            refs.update(f"subtitle-track:{track.id}" for track in tracks)
            refs.update(f"cue:{cue.id}" for track in tracks for cue in track.cues)
            covers = production_repo.list_cover_assets(
                workspace_id, task.id, timeline_version_id=version.id,
            )
            refs.update(f"cover:{cover.id}" for cover in covers)
            if montage.status == "rendered" and montage.output_path:
                refs.add(f"cover:{task.id}")
        if payload.playhead_ms is not None and payload.playhead_ms > round(montage.duration_seconds * 1000):
            raise HTTPException(status_code=422, detail="播放位置超出当前任务的时间线")
    if any(ref not in refs for ref in payload.selected_object_refs):
        raise HTTPException(status_code=422, detail="选中对象不属于当前任务或阶段")
    return stage, list(payload.selected_object_refs)


@app.post(
    "/api/creation-tasks/{task_id}/assistant/turn",
    response_model=CreationTaskAssistantTurnResponse,
)
def creation_task_assistant_turn(
    task_id: str,
    request: Request,
    payload: CreationTaskAssistantTurnRequest,
) -> CreationTaskAssistantTurnResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    trusted_stage, trusted_refs = _assistant_turn_scope(workspace_id, task, payload)
    message = payload.message.strip()
    if _assistant_text_without_credentials(message) != message:
        raise HTTPException(status_code=422, detail="请勿在创作要求中粘贴密钥或口令。")
    production_repo.append_task_event(TaskEvent(
        id=f"event_{uuid4().hex}", workspace_id=workspace_id, task_id=task_id,
        job_id=None, event_type="assistant_turn",
        payload={"role": "user", "message": message, "stage": trusted_stage},
        created_at=utc_now(),
    ))
    try:
        reply, preview = run_task_turn(
            settings,
            workspace_id=workspace_id,
            task_id=task_id,
            message=message,
            stage=trusted_stage,
            selected_refs=trusted_refs,
            playhead_ms=payload.playhead_ms,
            authorization=request.headers.get("authorization"),
            cookie=request.headers.get("cookie"),
        )
    except AssistantUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    reply = _assistant_text_without_credentials(reply)
    script_draft = None
    if preview is not None and trusted_stage == "script":
        script_id = preview.get("script_id") if isinstance(preview, dict) else None
        if not isinstance(script_id, str):
            raise HTTPException(status_code=502, detail="对话没有返回可核实的文案草稿。")
        try:
            candidate = production_repo.get_creation_task_script(workspace_id, script_id)
        except KeyError as exc:
            raise HTTPException(status_code=502, detail="对话返回的文案草稿不存在。") from exc
        if candidate.task_id != task_id or candidate.selection_status != "draft":
            raise HTTPException(status_code=502, detail="对话返回的文案草稿不属于当前任务。")
        script_draft = CreationTaskScriptResponse.model_validate(asdict(candidate))
        edit_preview = None
    elif preview is not None:
        edit_preview = CreationTaskEditPreviewResponse.model_validate(preview)
        if edit_preview.proposal.task_id != task_id or edit_preview.proposal.workspace_id != workspace_id:
            raise HTTPException(status_code=502, detail="对话工具返回了不属于当前任务的编辑提案。")
    else:
        edit_preview = None
    production_repo.append_task_event(TaskEvent(
        id=f"event_{uuid4().hex}", workspace_id=workspace_id, task_id=task_id,
        job_id=None, event_type="assistant_turn",
        payload={"role": "assistant", "message": reply, "stage": trusted_stage,
                 "script_id": script_draft.id if script_draft else None},
        created_at=utc_now(),
    ))
    return CreationTaskAssistantTurnResponse(
        reply=reply,
        provider=settings.effective_llm_provider,
        model=settings.effective_llm_model,
        edit_preview=edit_preview,
        script_draft=script_draft,
    )


@app.get(
    "/api/creation-tasks/{task_id}/scripts",
    response_model=CreationTaskScriptsResponse,
)
def get_creation_task_scripts(
    task_id: str,
    request: Request,
) -> CreationTaskScriptsResponse:
    workspace_id = current_workspace_id(request)
    return _creation_task_scripts_response(
        workspace_id,
        _creation_task_or_404(workspace_id, task_id),
    )


@app.post(
    "/api/creation-tasks/{task_id}/scripts/generate",
    response_model=CreationTaskScriptsResponse,
)
def generate_creation_task_script(
    task_id: str,
    request: Request,
    _payload: CreationTaskScriptGenerateRequest,
) -> CreationTaskScriptsResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    selected_topic = next(
        (
            candidate
            for candidate in production_repo.list_topic_candidates(workspace_id, task.id)
            if candidate.id == task.selected_topic_id
        ),
        None,
    )
    if selected_topic is None:
        raise _topic_failure_response(
            status_code=409,
            error="confirmed_topic_missing",
            reason="当前创作任务还没有已确认选题。",
            impact="未调用文字模型，未生成文案。",
            repair_action="先生成并确认一个选题。",
            retryable=False,
        )
    route_snapshot = _topic_route_snapshot()
    provider_sources: tuple[CreationTaskTopicSource, ...] = ()
    input_references: list[dict[str, object]] = []
    preflight_error: _CreationTaskTopicPreflightError | None = None
    try:
        provider_sources, input_references, _ = _creation_task_topic_inputs(
            workspace_id,
            task,
            plan,
            cloud_egress=route_snapshot["execution_location"] == "cloud",
        )
        if route_snapshot["availability"] == "unconfigured":
            raise _CreationTaskTopicPreflightError(
                reason="capability_unavailable",
                impact="当前没有可调用的文字模型连接，未生成文案。",
                repair_action="在本机运行时设置中配置 OpenAI-compatible 文字模型后重试。",
                retryable=True,
            )
    except _CreationTaskTopicPreflightError as exc:
        preflight_error = exc
    run = ScriptGenerationRun(
        id=f"script_run_{uuid4().hex[:12]}",
        workspace_id=workspace_id,
        task_id=task.id,
        plan_id=plan.id,
        topic_id=selected_topic.id,
        status="running",
        route_snapshot=route_snapshot,
        prompt_schema_version="creation-task-script/v1",
        input_references=input_references,
    )
    try:
        task, run = production_repo.start_script_generation(run)
    except (KeyError, ValueError) as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_script_stage_conflict",
            reason="当前创作任务不能开始生成文案。",
            impact="没有创建重复文案或重复模型调用。",
            repair_action=str(exc),
            retryable=task.stage_status == "failed",
        ) from exc
    if preflight_error is not None:
        production_repo.fail_script_generation(
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
        generated: CreationTaskGeneratedScript = (
            _creation_task_script_generator_from_settings().generate(
                CreationTaskScriptInput(
                    goal=plan.goal,
                    language=plan.language,
                    duration_target_seconds=plan.duration_target_seconds,
                    topic_title=selected_topic.title,
                    topic_angle=selected_topic.angle,
                    topic_audience=selected_topic.audience,
                    topic_outline=tuple(selected_topic.outline),
                    sources=provider_sources,
                    style_instruction=str(plan.template_settings.get("style_instruction", "")),
                    script_structure=tuple(plan.template_settings.get("script_structure", [])),
                    script_section_count=(plan.template_settings.get("script_section_count")
                                          or (5 if 0 < plan.presenter_share_target < 100 else None)),
                )
            )
        )
    except CreationTaskScriptGenerationError as exc:
        production_repo.fail_script_generation(
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
    script = CreationTaskScript(
        id=f"script_{uuid4().hex[:12]}",
        workspace_id=workspace_id,
        task_id=task.id,
        run_id=run.id,
        topic_id=selected_topic.id,
        title=generated.title,
        language=generated.language,
        sections=[
            CreationTaskScriptSection(
                id=f"section_{index:02d}_{uuid4().hex[:6]}",
                title=section.title,
                content=section.content,
                visual_prompt=section.visual_prompt,
                evidence_reference_ids=list(section.evidence_reference_ids),
            )
            for index, section in enumerate(generated.sections, start=1)
        ],
    )
    try:
        task, _, _ = production_repo.complete_script_generation(run.id, script)
    except ValueError as exc:
        production_repo.fail_script_generation(
            run.id,
            reason="provider_output_invalid",
            impact="文字模型输出未通过文案与证据持久化校验，未保存文案。",
            repair_action="检查模型 JSON 输出与来源引用后重试。",
            retryable=True,
            provider_attempted=True,
        )
        raise _topic_failure_response(
            status_code=502,
            error="provider_output_invalid",
            reason="文案输出未通过持久化校验。",
            impact="未保存无效文案。",
            repair_action="检查模型 JSON 输出与来源引用后重试。",
            retryable=True,
        ) from exc
    return _creation_task_scripts_response(workspace_id, task)


@app.post(
    "/api/creation-tasks/{task_id}/scripts/{script_id}/confirm",
    response_model=CreationTaskScriptsResponse,
)
def confirm_creation_task_script(
    task_id: str,
    script_id: str,
    request: Request,
    payload: CreationTaskScriptConfirmRequest,
) -> CreationTaskScriptsResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    estimated_seconds = estimate_speech_seconds("\n".join(section.content for section in payload.sections))
    if estimated_seconds > plan.duration_target_seconds:
        raise HTTPException(status_code=422, detail=(
            f"文案预计约 {estimated_seconds:.1f} 秒，超过方案 {plan.duration_target_seconds} 秒上限；"
            "请压缩正文后再确认。"
        ))
    try:
        task, _ = production_repo.confirm_creation_task_script(
            workspace_id,
            task_id,
            script_id,
            title=payload.title,
            sections=[
                CreationTaskScriptSection(**section.model_dump())
                for section in payload.sections
            ],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task script not found") from exc
    except ValueError as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_script_confirmation_conflict",
            reason="当前文案不能被确认。",
            impact="没有改变任务阶段或文案版本。",
            repair_action=str(exc),
            retryable=False,
        ) from exc
    return _creation_task_scripts_response(workspace_id, task)


@app.post(
    "/api/creation-tasks/{task_id}/narration/voice",
    response_model=CreationTaskWorkspaceResponse,
)
def select_creation_task_narration_voice(
    task_id: str,
    request: Request,
    payload: CreationTaskNarrationVoiceSelectionRequest,
) -> CreationTaskWorkspaceResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    try:
        selected_voice, pronunciation_references = _resolve_creation_task_voice_selection(
            workspace_id=workspace_id,
            task=task,
            plan=plan,
            voice_profile_id=payload.voice_profile_id,
            voice_version=payload.voice_version,
        )
        _, task = production_repo.select_creation_task_narration_voice(
            workspace_id=workspace_id,
            task_id=task.id,
            selected_voice=selected_voice,
            pronunciation_references=pronunciation_references,
        )
    except MediaAuthorityError as exc:
        raise _topic_failure_response(
            status_code=409,
            error=exc.reason,
            reason=exc.reason,
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=True,
        ) from exc
    except ValueError as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_voice_selection_conflict",
            reason="当前任务不能选择这个声音版本。",
            impact="没有改变任务阶段，也没有调用声音模型。",
            repair_action=str(exc),
            retryable=False,
        ) from exc
    return _creation_task_workspace_response(workspace_id, task)


@app.post(
    "/api/creation-tasks/{task_id}/narration/generate",
    response_model=CreationTaskNarrationResponse,
)
def generate_creation_task_narration(
    task_id: str,
    request: Request,
) -> CreationTaskNarrationResponse:
    return _generate_creation_task_narration_for_workspace(current_workspace_id(request), task_id)


def _generate_creation_task_narration_for_workspace(workspace_id: str, task_id: str) -> CreationTaskNarrationResponse:
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    if task.confirmed_script_id is None:
        raise HTTPException(status_code=409, detail="confirmed creation task script missing")
    try:
        script = production_repo.get_creation_task_script(
            workspace_id, task.confirmed_script_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="confirmed creation task script missing") from exc
    try:
        task = production_repo.start_creation_task_narration(workspace_id, task.id)
    except ValueError as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_narration_conflict",
            reason="当前任务还不能生成配音。",
            impact="没有调用声音模型，也没有生成音频文件。",
            repair_action=str(exc),
            retryable=False,
        ) from exc
    try:
        if plan.selected_voice is None:
            raise MediaAuthorityError(
                reason="voice_identity_missing",
                impact="当前创作任务未生成配音，未调用声音模型。",
                repair_action="重新预检并选择已授权且支持目标语言的声音版本。",
            )
        inputs = production_repo.resolve_creation_task_narration_inputs(
            workspace_id=workspace_id,
            task_id=task.id,
            voice_profile_id=plan.selected_voice.profile_id,
            voice_version=plan.selected_voice.version,
        )
        reference_path = production_repo.resolve_workspace_media_reference(
            workspace_id,
            inputs.voice.reference_audio_path,
            require_registered=True,
        )
        segment_artifacts: list[NarrationArtifact] = []
        narration_segments: list[NarrationSegment] = []
        for section in script.sections:
            segment_id = f"narration_segment_{hashlib.sha256(f'{task.id}:{script.id}:{section.id}'.encode('utf-8')).hexdigest()[:20]}"
            segment_artifact = narration_generator.generate_for_creation_task(
                workspace_id=workspace_id,
                task_id=task.id,
                script_id=script.id,
                narration_text=section.content,
                language={"zh-CN": "zh", "en-US": "en", "zh-CN/en-US": "zh-en"}[script.language],
                max_duration_seconds=plan.duration_target_seconds,
                voice_identity=inputs.voice,
                pronunciation_entries=inputs.pronunciation_entries,
                resolved_reference_audio_path=reference_path,
                segment_id=segment_id,
            )
            segment_record = production_repo.save_narration_artifact(
                NarrationArtifactRecord(
                    id=f"narration_{segment_artifact.cache_key[:16]}",
                    workspace_id=workspace_id,
                    task_id=task.id,
                    script_id=script.id,
                    voice_profile_id=segment_artifact.voice_profile_id,
                    cache_key=segment_artifact.cache_key,
                    audio_path=segment_artifact.audio_path,
                    duration_seconds=segment_artifact.duration_seconds,
                    sample_rate=segment_artifact.sample_rate,
                    model_id=segment_artifact.model_id,
                    language=segment_artifact.language,
                    pronunciation_version=segment_artifact.pronunciation_version,
                )
            )
            segment_artifacts.append(segment_artifact)
            narration_segments.append(
                NarrationSegment(
                    id=segment_id,
                    workspace_id=workspace_id,
                    task_id=task.id,
                    script_id=script.id,
                    source_section_id=section.id,
                    text=section.content,
                    audio_reference=production_repo.narration_artifact_reference(
                        segment_record.cache_key
                    ),
                    duration_seconds=segment_record.duration_seconds,
                    sample_rate=segment_record.sample_rate,
                    model_id=segment_record.model_id,
                    voice_profile_id=inputs.voice.id,
                    voice_version=inputs.voice.version,
                    language=segment_artifact.language,
                    pronunciation_version=segment_record.pronunciation_version,
                    status="ready",
                )
            )
        artifact = narration_generator.combine_creation_task_segments(
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            segments=segment_artifacts,
            max_duration_seconds=plan.duration_target_seconds,
        )
        task, record = production_repo.complete_creation_task_narration(
            NarrationArtifactRecord(
                id=f"narration_{artifact.cache_key[:16]}",
                workspace_id=workspace_id,
                task_id=task.id,
                script_id=script.id,
                voice_profile_id=artifact.voice_profile_id,
                cache_key=artifact.cache_key,
                audio_path=artifact.audio_path,
                duration_seconds=artifact.duration_seconds,
                sample_rate=artifact.sample_rate,
                model_id=artifact.model_id,
                language=artifact.language,
                pronunciation_version=artifact.pronunciation_version,
            ),
            segments=narration_segments,
        )
    except (MediaAuthorityError, VoiceCloneError, OSError, ValueError) as exc:
        if isinstance(exc, MediaAuthorityError):
            reason, impact, repair_action, retryable = (
                exc.reason,
                exc.impact,
                exc.repair_action,
                True,
            )
        elif isinstance(exc, VoiceCloneError):
            reason, impact, repair_action, retryable = (
                exc.failure.reason,
                exc.failure.impact,
                exc.failure.repair_action,
                exc.failure.retryable,
            )
        else:
            reason, impact, repair_action, retryable = (
                "narration_generation_failed",
                "未生成可用的声音克隆 WAV。",
                str(exc),
                True,
            )
        production_repo.fail_creation_task_stage(
            workspace_id,
            task.id,
            stage="narration",
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )
        raise _topic_failure_response(
            status_code=503,
            error=reason,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        ) from exc
    return _creation_task_narration_response(task, record)


@app.get("/api/creation-tasks/{task_id}/narration/audio")
def get_creation_task_narration_audio(task_id: str, request: Request) -> FileResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    artifact = (
        production_repo.get_narration_artifact(workspace_id, task.narration_cache_key)
        if task.narration_cache_key
        else None
    )
    if artifact is None or artifact.task_id != task.id:
        raise HTTPException(status_code=404, detail="creation task narration not found")
    return FileResponse(artifact.audio_path, media_type="audio/wav", filename="narration.wav")


@app.get("/api/creation-tasks/{task_id}/narration/waveform")
def get_creation_task_narration_waveform(task_id: str, request: Request) -> dict:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    artifact = (
        production_repo.get_narration_artifact(workspace_id, task.narration_cache_key)
        if task.narration_cache_key else None
    )
    if artifact is None or artifact.task_id != task.id:
        raise HTTPException(status_code=404, detail="当前任务还没有可读取的配音。")
    try:
        return audio_waveform(
            Path(artifact.audio_path),
            settings.effective_temporary_root_path / "audio-waveforms" / workspace_id,
        )
    except AudioWaveformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/creation-tasks/{task_id}/narration/segments/{segment_id}/audio")
def get_creation_task_narration_segment_audio(
    task_id: str, segment_id: str, request: Request
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    try:
        segment = production_repo.get_narration_segment(
            workspace_id, task_id, segment_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task narration segment not found") from exc
    if segment.status != "ready" or not segment.audio_reference:
        raise HTTPException(status_code=409, detail="creation task narration segment is not ready")
    path = production_repo.resolve_authorized_narration_audio_reference(
        workspace_id, segment.audio_reference
    )
    return FileResponse(path, media_type="audio/wav", filename=f"{segment.id}.wav")


def _person_source_range(workspace_id, task, montage, clip, segment):
    from app.audio_timeline import narration_source_range
    if clip.locked:
        raise ValueError("所选镜头已锁定，请先解锁。")
    segments = production_repo.list_narration_segments(workspace_id, task.id, segment.script_id)
    script = production_repo.get_creation_task_script(workspace_id, segment.script_id)
    by_section = {item.source_section_id: item for item in segments}
    offset = 0
    for section in script.sections:
        item = by_section.get(section.id)
        if item is None or not item.duration_seconds:
            raise ValueError("配音分段不完整，无法定位人物片段。")
        if item.id == segment.id:
            break
        offset += round(item.duration_seconds * 1000)
    else:
        raise ValueError("所选配音不属于当前文案。")
    refs = {montage.narration_audio_path: offset, segment.audio_reference: 0}
    return narration_source_range(_effective_audio_timeline(montage),
        round(clip.timeline_start_seconds * 1000), round(clip.timeline_end_seconds * 1000),
        refs, round((segment.duration_seconds or 0) * 1000))


def _person_render_matches_narration(
    render: PersonRender, segment: NarrationSegment, transforms: list[TransformRun]
) -> bool:
    return bool(render.artifact_ref and segment.audio_reference and any(
        item.transform_type == "person_render"
        and item.status == "succeeded"
        and item.input_digest == render.cache_key
        and render.artifact_ref in item.output_refs
        and segment.audio_reference in item.input_refs
        and item.parameters.get("language") == segment.language
        for item in transforms
    ))


def _person_render_media_path(render: PersonRender) -> Path | None:
    if render.status != "ready":
        return None
    if render.artifact_ref:
        try:
            return artifact_store.resolve_file(render.artifact_ref)
        except (FileNotFoundError, ValueError):
            return None
    if not render.output_path:
        return None
    root = Path(settings.video_output_path).expanduser().resolve()
    output = Path(render.output_path).expanduser().resolve()
    try:
        output.relative_to(root)
    except ValueError:
        return None
    if not output.is_file() or output.stat().st_size == 0:
        return None
    return output


def _person_render_response(render: PersonRender) -> CreationTaskPersonRenderResponse:
    payload = asdict(render)
    payload.pop("cache_key", None)
    payload.pop("output_path", None)
    payload["media_url"] = (
        f"/api/creation-tasks/{render.task_id}/person-renders/{render.id}/media"
        if _person_render_media_path(render) is not None
        else None
    )
    return CreationTaskPersonRenderResponse.model_validate(payload)


@app.get(
    "/api/creation-tasks/{task_id}/person-renders",
    response_model=CreationTaskPersonRendersResponse,
)
def list_creation_task_person_renders(
    task_id: str, request: Request
) -> CreationTaskPersonRendersResponse:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    items = production_repo.list_person_renders(workspace_id, task_id)
    return CreationTaskPersonRendersResponse(
        items=[_person_render_response(item) for item in items],
        total_count=len(items),
    )


def _generate_creation_task_person_render_record(
    workspace_id: str,
    task_id: str,
    payload: CreationTaskPersonRenderRequest,
    *,
    regeneration_token: str | None = None,
) -> PersonRender:
    task = _creation_task_or_404(workspace_id, task_id)
    quality = payload.lipsync_quality or production_repo.get_creation_plan(workspace_id, task.plan_id).lipsync_quality
    provider = _lipsync_provider(quality)
    if not provider.ready:
        raise _capability_unavailable(
            "avatar_lipsync",
            message=provider.message,
            impact="人物片段未生成，当前任务继续使用原始素材。",
            repair_action="配置本地人物口型运行环境后重试。",
        )
    try:
        segment = production_repo.get_narration_segment(
            workspace_id, task_id, payload.narration_segment_id
        )
        if segment.status != "ready" or not segment.audio_reference:
            raise ValueError("人物片段只能使用已完成的配音分段")
        person = production_repo.get_person_profile(
            workspace_id,
            payload.person_profile_id,
            payload.person_version,
        )
        audio_path = production_repo.resolve_authorized_narration_audio_reference(
            workspace_id, segment.audio_reference
        )
        render_request = production_repo.resolve_avatar_lipsync_request(
            workspace_id=workspace_id,
            person_id=person.id,
            person_version=person.version,
            voice_profile_id=segment.voice_profile_id,
            voice_version=segment.voice_version,
            audio_path=str(audio_path),
            audio_cache_key=segment.audio_reference,
            language=segment.language,
            motion=payload.motion,
            expression=payload.expression,
            authorization_id=person.authorization.id,
            authorization_version=person.authorization.version,
            model_id=provider.model_id,
        )
        base_cache_key = avatar_lipsync_cache_key(render_request)
        cache_key = (
            hashlib.sha256(
                f"{base_cache_key}\nregenerate:{regeneration_token}".encode("utf-8")
            ).hexdigest()
            if regeneration_token
            else base_cache_key
        )
        render = production_repo.create_person_render(
            PersonRender(
                id=f"person_render_{cache_key[:24]}",
                workspace_id=workspace_id,
                task_id=task_id,
                person_profile_id=person.id,
                person_version=person.version,
                voice_profile_id=segment.voice_profile_id,
                voice_version=segment.voice_version,
                narration_segment_id=segment.id,
                visual_reference=person.avatar_reference,
                model_id=provider.model_id,
                motion=payload.motion,
                expression=payload.expression,
                cache_key=cache_key,
                status="pending",
            )
        )
        if render.status == "ready":
            if _person_render_media_path(render) is None:
                raise ValueError("已记录的人物片段输出缺失，请重新生成")
        if render.status == "running":
            return render
        input_refs = list(
            dict.fromkeys(
                [
                    f"person-profile://{person.id}/{person.version}",
                    f"voice-profile://{segment.voice_profile_id}/{segment.voice_version}",
                    f"narration-segment://{segment.id}",
                    segment.audio_reference,
                    person.avatar_reference,
                ]
            )
        )
        base_idempotency_key = f"person-render:{task_id}:{cache_key}"
        matching_jobs = [
            item
            for item in production_repo.list_job_runs(workspace_id, task_id)
            if item.kind == "person_render" and item.input_digest == cache_key
        ]
        latest_matching_job = matching_jobs[-1] if matching_jobs else None
        idempotency_key = (
            latest_matching_job.idempotency_key
            if latest_matching_job is not None
            and latest_matching_job.status in {"interrupted", "succeeded"}
            else f"{base_idempotency_key}:retry:{len(matching_jobs)}"
            if matching_jobs
            else base_idempotency_key
        )
        job = JobRun(
            id=f"job_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            kind="person_render",
            idempotency_key=idempotency_key,
            input_digest=cache_key,
        )

        def person_render_job(context) -> JobResult:
            current = production_repo.get_person_render(
                context.workspace_id, task_id, render.id
            )
            source = _person_render_media_path(current)
            duration_seconds = current.duration_seconds
            alignment_evidence = current.alignment_evidence
            claimed = current.status != "ready"
            try:
                if source is None:
                    production_repo.claim_person_render(
                        context.workspace_id, task_id, current.id
                    )
                    result = AvatarAndLipSyncService(
                        settings.video_output_path,
                        provider,
                    ).render(render_request, output_cache_key=cache_key)
                    if result.status != "succeeded" or not result.artifact_path:
                        failed = production_repo.finish_person_render(
                            context.workspace_id,
                            task_id,
                            current.id,
                            status="failed",
                            failure_reason=result.failure_code or "person_render_failed",
                            impact=result.failure_impact
                            or "人物片段未生成，当前任务继续使用原始素材。",
                            repair_action=result.remediation_action
                            or "检查人物口型运行环境后重试。",
                        )
                        raise JobFailure(
                            failed.failure_reason or "person_render_failed",
                            failed.impact or "人物片段未生成，当前任务继续使用原始素材。",
                            failed.repair_action or "检查人物口型运行环境后重试。",
                        )
                    source = Path(result.artifact_path)
                    duration_seconds = result.duration_seconds
                    alignment_evidence = result.alignment_evidence
                logical_ref = (
                    f"artifacts/{task_id}/{context.job_id}/person-render.mp4"
                )
                try:
                    published = artifact_store.describe(logical_ref)
                except FileNotFoundError:
                    published = artifact_store.publish_file(logical_ref, source)
                artifact_id = f"artifact_{context.job_id}_person_render"
                try:
                    production_repo.get_artifact_record(
                        context.workspace_id, artifact_id
                    )
                except KeyError:
                    production_repo.create_artifact_record(
                        ArtifactRecord(
                            id=artifact_id,
                            workspace_id=context.workspace_id,
                            task_id=task_id,
                            kind="person_render",
                            logical_ref=logical_ref,
                            sha256=published.sha256,
                            size_bytes=published.size_bytes,
                            producer_job_id=context.job_id,
                            input_refs=input_refs,
                        )
                    )
                transform_id = f"transform_{context.job_id}"
                try:
                    transform = production_repo.get_transform_run(
                        context.workspace_id, transform_id
                    )
                except KeyError:
                    running_job = production_repo.get_job_run(
                        context.workspace_id, context.job_id
                    )
                    transform = production_repo.create_transform_run(
                        TransformRun(
                            id=transform_id,
                            workspace_id=context.workspace_id,
                            task_id=task_id,
                            job_id=context.job_id,
                            transform_type="person_render",
                            input_digest=cache_key,
                            input_refs=input_refs,
                            output_refs=[logical_ref],
                            engine=provider.model_id,
                            parameters={
                                "motion": payload.motion,
                                "expression": payload.expression,
                                "language": segment.language,
                            },
                            status="succeeded",
                            started_at=running_job.started_at or utc_now(),
                            finished_at=utc_now(),
                        )
                    )
                for upstream_ref in input_refs:
                    edge_digest = hashlib.sha256(
                        f"{upstream_ref}\n{logical_ref}".encode("utf-8")
                    ).hexdigest()[:24]
                    edge_id = f"edge_{context.job_id}_{edge_digest}"
                    try:
                        production_repo.get_dependency_edge(
                            context.workspace_id, edge_id
                        )
                    except KeyError:
                        production_repo.create_dependency_edge(
                            DependencyEdge(
                                id=edge_id,
                                workspace_id=context.workspace_id,
                                task_id=task_id,
                                upstream_ref=upstream_ref,
                                downstream_ref=logical_ref,
                                created_by_transform_id=transform.id,
                            )
                        )
                if claimed:
                    current = production_repo.finish_person_render(
                        context.workspace_id,
                        task_id,
                        current.id,
                        status="ready",
                        output_path=str(artifact_store.resolve_file(logical_ref)),
                        duration_seconds=duration_seconds,
                        alignment_evidence=alignment_evidence,
                    )
                production_repo.bind_person_render_artifact(
                    context.workspace_id, task_id, current.id, logical_ref
                )
                return JobResult((logical_ref,))
            except JobFailure:
                raise
            except (FileNotFoundError, MediaAuthorityError, OSError, ValueError) as exc:
                if claimed:
                    try:
                        production_repo.finish_person_render(
                            context.workspace_id,
                            task_id,
                            current.id,
                            status="failed",
                            failure_reason="person_render_lineage_failed",
                            impact="人物片段未进入可信产物记录，当前时间线保持不变。",
                            repair_action=str(exc),
                        )
                    except ValueError:
                        pass
                raise JobFailure(
                    "person_render_lineage_failed",
                    "人物片段未进入可信产物记录，当前时间线保持不变。",
                    str(exc),
                ) from exc

        completed_job = video_job_runner.run(job, person_render_job)
        completed = production_repo.get_person_render(workspace_id, task_id, render.id)
        if completed_job.status != "succeeded":
            raise _topic_failure_response(
                status_code=503,
                error=completed_job.failure_reason or "person_render_failed",
                reason=completed_job.failure_reason or "人物口型片段没有生成可信输出。",
                impact=completed_job.impact or "当前时间线和成片保持不变。",
                repair_action=completed_job.repair_action or "检查人物口型运行环境后重试。",
                retryable=True,
            )
        return completed
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="person render input not found") from exc
    except MediaAuthorityError as exc:
        raise _topic_failure_response(
            status_code=409,
            error=exc.reason,
            reason=exc.reason,
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=False,
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/creation-tasks/{task_id}/person-renders/generate",
    response_model=CreationTaskPersonRenderResponse,
)
def generate_creation_task_person_render(
    task_id: str,
    payload: CreationTaskPersonRenderRequest,
    request: Request,
) -> CreationTaskPersonRenderResponse:
    render = _generate_creation_task_person_render_record(
        current_workspace_id(request),
        task_id,
        payload,
    )
    return _person_render_response(render)


@app.get("/api/creation-tasks/{task_id}/person-renders/{render_id}/media")
def get_creation_task_person_render_media(
    task_id: str, render_id: str, request: Request
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    try:
        render = production_repo.get_person_render(workspace_id, task_id, render_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="person render not found") from exc
    output = _person_render_media_path(render)
    if output is None:
        raise HTTPException(status_code=409, detail="person render is not ready")
    return FileResponse(output, media_type="video/mp4", filename=f"{render.id}.mp4")


@app.post(
    "/api/creation-tasks/{task_id}/montage/plan",
    response_model=CreationTaskMontagePlanResponse,
)
def create_creation_task_montage_plan(
    task_id: str,
    request: Request,
) -> CreationTaskMontagePlanResponse:
    return _create_creation_task_montage_plan_for_workspace(current_workspace_id(request), task_id)


def _create_creation_task_montage_plan_for_workspace(workspace_id: str, task_id: str) -> CreationTaskMontagePlanResponse:
    task = _creation_task_or_404(workspace_id, task_id)
    creation_plan = _creation_plan_for_task_or_404(workspace_id, task)
    if task.confirmed_script_id is None or task.narration_cache_key is None:
        raise HTTPException(status_code=409, detail="confirmed script or narration missing")
    try:
        task = production_repo.start_creation_task_montage(workspace_id, task.id)
    except ValueError as exc:
        raise _topic_failure_response(
            status_code=409,
            error="creation_task_montage_conflict",
            reason="当前任务还不能生成故事板。",
            impact="没有读取或改写剪辑计划。",
            repair_action=str(exc),
            retryable=False,
        ) from exc
    try:
        script = production_repo.get_creation_task_script(
            workspace_id, task.confirmed_script_id
        )
        narration = production_repo.get_narration_artifact(
            workspace_id, task.narration_cache_key
        )
        if narration is None or narration.task_id != task.id:
            raise ValueError("当前创作任务的配音产物不可用")
        narration_segments = production_repo.list_narration_segments(
            workspace_id, task.id, script.id
        )
        if not narration_segments or any(
            segment.status != "ready"
            or segment.audio_reference is None
            or segment.duration_seconds is None
            for segment in narration_segments
        ):
            raise ValueError("当前创作任务缺少可用的分段配音事实")
        segments_by_section = {segment.source_section_id: segment for segment in narration_segments}
        selected_sections = select_presenter_sections(
            [(section.id, float(segments_by_section[section.id].duration_seconds or 0))
             for section in script.sections],
            creation_plan.presenter_share_target,
        )
        if selected_sections and creation_plan.selected_voice is None:
            raise ValueError("露脸段落缺少当前任务已授权的人物和声音")
        montage = montage_planner.build_for_creation_task(
            workspace_id=workspace_id,
            task_id=task.id,
            script=script,
            profile=production_repo.get_profile(workspace_id),
            assets=production_repo.list_assets(workspace_id),
            media_reference_for_asset=lambda asset_id: production_repo.workspace_media_reference_for_asset(
                workspace_id, asset_id
            ),
            narration_audio_path=production_repo.narration_artifact_reference(
                narration.cache_key
            ),
            narration_duration_seconds=narration.duration_seconds,
            narration_segment_ids_by_section={
                segment.source_section_id: segment.id
                for segment in narration_segments
            },
            narration_segment_durations_by_section={
                segment.source_section_id: float(segment.duration_seconds or 0)
                for segment in narration_segments
            },
            person_render_by_section={section_id: (f"pending:{section_id}", float(segments_by_section[section_id].duration_seconds or 0))
                                      for section_id in selected_sections},
            presenter_share_target=creation_plan.presenter_share_target,
        )
        for section_id in selected_sections:
            segment = segments_by_section[section_id]
            selected_voice = creation_plan.selected_voice
            assert selected_voice is not None
            render = _generate_creation_task_person_render_record(
                workspace_id, task.id,
                CreationTaskPersonRenderRequest(
                    narration_segment_id=segment.id,
                    person_profile_id=selected_voice.person_profile_id,
                    person_version=selected_voice.person_version,
                ),
            )
            if render.status != "ready" or _person_render_media_path(render) is None:
                raise ValueError("露脸段落的口型视频没有完成，请重试故事板")
            for clip in montage.clips:
                cue = next(item for item in montage.cues if item.id == clip.cue_id)
                if cue.source_section_id == section_id:
                    if clip.source_end_seconds > (render.duration_seconds or 0) + 1 / 30:
                        raise ValueError("人物口型输出短于露脸段落，请重试或降低比例")
                    clip.person_render_id = render.id
                    clip.asset_id = f"person:{render.id}"
                    clip.source_path = f"person-render://{render.id}"
                    clip.source_end_seconds = min(clip.source_end_seconds, render.duration_seconds or 0)
        actual = round(100 * sum(clip.timeline_end_seconds - clip.timeline_start_seconds
                                 for clip in montage.clips if clip.person_render_id) / montage.duration_seconds, 1)
        montage.message += f" 露脸目标 {montage.presenter_share_target}%，实际 {actual}%；仅为露脸段落制作口型。"
        montage.aspect_ratio = creation_plan.aspect_ratio
        for clip in montage.clips:
            clip.fit_mode = creation_plan.template_settings.get("fit_mode", "fill")
            if creation_plan.template_settings.get("color_style") is not None:
                clip.visual_effects.update(reusable_color_settings(creation_plan.template_settings["color_style"]))
        for cue in montage.cues:
            cue.subtitle_style = dict(creation_plan.template_settings.get("subtitle_style", {}))
        task, _ = production_repo.save_creation_task_montage_plan(
            workspace_id,
            task.id,
            montage.id,
            creation_task_montage_plan_to_payload(montage),
        )
        _ensure_montage_base_version(workspace_id, task, montage)
    except (KeyError, MediaAuthorityError, ValueError, HTTPException) as exc:
        task = production_repo.fail_creation_task_stage(
            workspace_id,
            task.id,
            stage="montage",
            reason="montage_plan_failed",
            impact="未生成可供审核的故事板和剪辑计划。",
            repair_action=str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
            retryable=True,
        )
        raise _topic_failure_response(
            status_code=409,
            error="montage_plan_failed",
            reason="素材或配音不满足剪辑计划要求。",
            impact=task.stage_impact or "未生成剪辑计划。",
            repair_action=task.stage_repair_action or "审核素材后重试。",
            retryable=True,
        ) from exc
    return _creation_task_montage_response(montage)


def _creation_task_montage_candidates(
    workspace_id: str,
    plan: CreationTaskMontagePlan,
    clip: MontageClip,
) -> list[MontageClipCandidateResponse]:
    cue = next((item for item in plan.cues if item.id == clip.cue_id), None)
    cue_tokens = set((cue.text if cue else "").lower())
    candidates: list[MontageClipCandidateResponse] = []
    for asset in production_repo.list_assets(workspace_id):
        if asset.review_status != "approved" or not material_allows_footage(asset):
            continue
        for segment in asset.segments:
            if (
                segment.review_status != "approved"
                or effective_segment_usage_role(asset, segment)
                not in {"a_roll", "b_roll"}
            ):
                continue
            available = segment.end_seconds - segment.start_seconds
            slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
            if available + 1e-6 < slot_duration:
                continue
            description = (segment.description or segment.transcript or asset.file_name).strip()
            overlap = len(cue_tokens & set(description.lower())) / max(1, len(cue_tokens))
            score = max(
                0.0,
                min(
                    1.0,
                    overlap * 0.7
                    + min(1.0, available / max(slot_duration, 0.1)) * 0.3,
                ),
            )
            candidates.append(
                MontageClipCandidateResponse(
                    asset_id=asset.id,
                    segment_id=segment.id,
                    file_name=asset.file_name,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    description=description,
                    score=round(score, 3),
                    reasons=["当前工作区可用素材", "时长满足当前旁白时间槽"],
                )
            )
    candidates.sort(
        key=lambda item: (
            item.asset_id == clip.asset_id and item.segment_id == clip.segment_id,
            item.score,
        ),
        reverse=True,
    )
    return candidates[:24]


@app.get(
    "/api/creation-tasks/{task_id}/montage/clips/{clip_id}/candidates",
    response_model=list[MontageClipCandidateResponse],
)
def get_creation_task_montage_clip_candidates(
    task_id: str,
    clip_id: str,
    request: Request,
) -> list[MontageClipCandidateResponse]:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    try:
        plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
        clip = next(item for item in plan.clips if item.id == clip_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="creation task montage clip not found") from exc
    return _creation_task_montage_candidates(workspace_id, plan, clip)


def _edit_preview_response(
    intent: EditIntent,
    proposal: EditProposal,
) -> CreationTaskEditPreviewResponse:
    return CreationTaskEditPreviewResponse.model_validate(
        {"intent": asdict(intent), "proposal": asdict(proposal)}
    )


def _logical_montage_output_refs(plan: CreationTaskMontagePlan) -> list[str]:
    references = [
        reference
        for reference in (plan.output_path, plan.subtitle_path)
        if reference
    ]
    if any(
        Path(reference).is_absolute()
        or not reference.strip()
        or ".." in Path(reference).parts
        for reference in references
    ):
        raise _topic_failure_response(
            status_code=409,
            error="edit_base_not_migrated",
            reason="当前成片仍使用迁移前的文件引用，不能建立可撤销修改。",
            impact="当前镜头和成片保持不变。",
            repair_action="先用当前生产链路重新渲染一次成片，再继续修改。",
            retryable=True,
        )
    return references


def _current_narration_snapshot(workspace_id: str, task: CreationTask) -> dict[str, object]:
    if not task.confirmed_script_id:
        return {}
    segments = production_repo.list_narration_segments(
        workspace_id, task.id, task.confirmed_script_id
    )
    return {
        "narration_segments": [
            json.loads(
                json.dumps(
                    asdict(segment),
                    ensure_ascii=False,
                    default=lambda value: value.isoformat()
                    if isinstance(value, datetime)
                    else str(value),
                )
            )
            for segment in segments
        ],
        "narration_cache_key": task.narration_cache_key,
    }


def _narration_segments_from_snapshot(
    snapshot: dict[str, object],
) -> list[NarrationSegment] | None:
    raw_segments = snapshot.get("narration_segments")
    if not isinstance(raw_segments, list):
        return None
    segments: list[NarrationSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            raise ValueError("时间线中的配音片段快照无效")
        payload = dict(item)
        for key in ("created_at", "updated_at"):
            if isinstance(payload.get(key), str):
                payload[key] = datetime.fromisoformat(str(payload[key]))
        segments.append(NarrationSegment(**payload))
    return segments


def _ensure_montage_base_version(
    workspace_id: str,
    task: CreationTask,
    plan: CreationTaskMontagePlan,
) -> TimelineVersion:
    if task.current_timeline_version_id is not None:
        return production_repo.get_timeline_version(
            workspace_id, task.current_timeline_version_id
        )
    versions = production_repo.list_timeline_versions(workspace_id, task.id)
    if versions:
        return versions[-1]
    version = TimelineVersion(
        id=f"timeline_{uuid4().hex}",
        workspace_id=workspace_id,
        task_id=task.id,
        parent_version_id=None,
        sequence=1,
        snapshot={
            "montage": creation_task_montage_plan_to_payload(plan),
            **_current_narration_snapshot(workspace_id, task),
        },
        created_by_operation_id=None,
    )
    return production_repo.create_timeline_version(version)


@app.post("/api/creation-tasks/{task_id}/timeline/initialize")
def initialize_creation_task_timeline(task_id: str, request: Request) -> dict[str, str]:
    """Explicitly snapshot a legacy storyboard before the user starts editing it."""
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.stage not in {"montage", "review"}:
        raise HTTPException(status_code=409, detail="当前任务尚无可编辑的故事板")
    plan = creation_task_montage_plan_from_payload(
        production_repo.get_creation_task_montage_plan(workspace_id, task_id)
    )
    _logical_montage_output_refs(plan)
    if task.current_timeline_version_id is None:
        versions = production_repo.list_timeline_versions(workspace_id, task_id)
        if versions:
            current_snapshot = {
                "montage": creation_task_montage_plan_to_payload(plan),
                **_current_narration_snapshot(workspace_id, task),
            }
            if any(versions[-1].snapshot.get(key) != value for key, value in current_snapshot.items()):
                raise _topic_failure_response(
                    status_code=409,
                    error="timeline_version_conflict",
                    reason="当前故事板与已有时间线版本内容不同，不能把旧版本当作当前版本。",
                    impact="当前故事板和历史版本均已保留，没有写入或替换。",
                    repair_action="请先核对当前故事板与版本历史，再联系支持修复版本关联。",
                    retryable=False,
                )
    version = _ensure_montage_base_version(workspace_id, task, plan)
    if task.current_timeline_version_id is None:
        version = production_repo.create_timeline_version(version)
    return {"version_id": version.id}


def _prepare_changed_montage(
    plan: CreationTaskMontagePlan,
    *,
    message: str,
) -> tuple[CreationTaskMontagePlan, dict[str, object]]:
    _refresh_montage_review_state(plan)
    plan.message = message if plan.status == "ready" else f"{message} 仍有 {plan.low_confidence_clip_count} 个镜头等待确认。"
    return plan, creation_task_montage_plan_to_payload(plan)


def _persist_typed_montage_edit(
    *,
    workspace_id: str,
    task: CreationTask,
    proposal: EditProposal,
    base_version: TimelineVersion,
    updated_plan: CreationTaskMontagePlan,
    operation_type: str,
    operation_payload: dict[str, object],
    narration_segments: list[NarrationSegment] | None = None,
    narration_artifact: NarrationArtifactRecord | None = None,
    subtitle_source_version: TimelineVersion | None = None,
    edited_subtitle_tracks: list[SubtitleTrack] | None = None,
) -> CreationTaskEditConfirmResponse:
    try:
        dissolve_frames(updated_plan.clips)
        validate_overlays(updated_plan.graphic_overlays,updated_plan.duration_seconds)
        for clip in updated_plan.clips:
            fade_filters(clip)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    montage_payload = creation_task_montage_plan_to_payload(updated_plan)
    applied_at = utc_now()
    operation = EditOperation(
        id=f"operation_{uuid4().hex}",
        workspace_id=workspace_id,
        task_id=task.id,
        proposal_id=proposal.id,
        operation_type=operation_type,
        payload=operation_payload,
        base_version_id=base_version.id,
        result_version_id=f"timeline_{uuid4().hex}",
        status="applied",
        created_at=applied_at,
        applied_at=applied_at,
    )
    source_version = subtitle_source_version or base_version
    source_plan = creation_task_montage_plan_from_payload(source_version.snapshot["montage"])
    carried_tracks = [_clone_subtitle_track(track, timeline_version_id=operation.result_version_id)
        for track in (edited_subtitle_tracks if edited_subtitle_tracks is not None else
            _current_subtitle_tracks(workspace_id, task, current_version=source_version, montage=source_plan))]
    audio_edit = operation_payload.get("edit") if operation_type == "EditAudioTimeline" else None
    if isinstance(audio_edit, dict) and audio_edit.get("action") == "replace_narration":
        carried_tracks = [replace(track, status="unavailable", artifact_ref=None, transform_id=None,
            failure_reason="整条旁白已更换，旧字幕时间尚未复核。",
            impact="原字幕文字已保留，但旧字幕轨不能作为新旁白的有效字幕输出。",
            repair_action="逐条检查字幕文字和时间，确认后重新生成字幕与成片。")
            for track in carried_tracks]
    if operation_type in {"RegenerateNarrationSegment", "DeleteClip", "DuplicateClip", "ChangeLinkedSpeed", "EditCompoundTimeline", "RemoveSilence", "AlignSubtitleTiming"}:
        try:
            if operation_type == "RegenerateNarrationSegment":
                carried_tracks = retime_subtitle_tracks(carried_tracks, source_plan.cues, updated_plan.cues)
            for index, track in enumerate(carried_tracks):
                if track.artifact_ref is not None:
                    continue
                artifact_ref, transform_id = _publish_subtitle_text(
                    workspace_id=workspace_id, task_id=task.id, proposal_id=proposal.id,
                    kind="subtitle_compound" if operation_type=="EditCompoundTimeline" else "subtitle_retime",
                    input_refs=[f"timeline:{source_version.id}", updated_plan.narration_audio_path,
                        *([operation_payload["source_audio_ref"]] if operation_type == "AlignSubtitleTiming" else [])],
                    content=render_srt(_execution_subtitle_track(track)),
                    engine="subtitle-srt/v1" if operation_type in {"EditCompoundTimeline", "AlignSubtitleTiming"} else "timeline-clock-v1",
                    parameters={"language": track.language, "operation": operation_type},
                )
                carried_tracks[index] = _clone_subtitle_track(track,
                    timeline_version_id=operation.result_version_id,
                    artifact_ref=artifact_ref, transform_id=transform_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"字幕重定时未保存，当前版本保持不变：{exc}") from exc
    source_cover = _cover_asset_for_version(workspace_id, task.id, source_version)
    carried_cover = (_clone_cover_asset(source_cover, timeline_version_id=operation.result_version_id)
        if source_cover is not None else None)
    result_version = TimelineVersion(
        id=operation.result_version_id or "",
        workspace_id=workspace_id,
        task_id=task.id,
        parent_version_id=base_version.id,
        sequence=base_version.sequence + 1,
        snapshot={
            **source_version.snapshot,
            **({"subtitle_track_ids": [track.id for track in carried_tracks]}
               if edited_subtitle_tracks is not None or carried_tracks or "subtitle_track_ids" in source_version.snapshot else {}),
            "cover_asset_id": carried_cover.id if carried_cover is not None else None,
            "montage": montage_payload,
            **(
                {"narration_segments": [asdict(segment) for segment in narration_segments]}
                if narration_segments is not None
                else {}
            ),
            **(
                {"narration_cache_key": narration_artifact.cache_key}
                if narration_artifact is not None
                else {}
            ),
            "invalidated_refs": proposal.invalidated_refs,
            "edit_operation_id": operation.id,
        },
        created_by_operation_id=operation.id,
        created_at=applied_at,
    )
    try:
        _confirmed, persisted_operation, _version, _task = (
            production_repo.confirm_edit_proposal(
                workspace_id,
                task.id,
                proposal.id,
                operation=operation,
                result_version=result_version,
                montage_plan_id=updated_plan.id,
                montage_payload=montage_payload,
                narration_segments=narration_segments,
                narration_artifact=narration_artifact,
                subtitle_tracks=carried_tracks,
                cover_asset=carried_cover,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    persisted_plan = creation_task_montage_plan_from_payload(
        production_repo.get_creation_task_montage_plan(workspace_id, task.id)
    )
    current_task = production_repo.get_creation_task(workspace_id, task.id)
    current_narration = (
        production_repo.get_narration_artifact(
            workspace_id, current_task.narration_cache_key
        )
        if current_task.narration_cache_key
        else None
    )
    return CreationTaskEditConfirmResponse.model_validate(
        {
            "operation": asdict(persisted_operation),
            "montage": _creation_task_montage_response(persisted_plan).model_dump(),
            "narration": (
                _creation_task_narration_response(current_task, current_narration).model_dump()
                if current_narration is not None
                else None
            ),
        }
    )


def _subtitle_display_selection(tracks: list[SubtitleTrack], mode: str) -> dict[str, bool]:
    languages = {"none": set(), "zh": {"zh-CN"}, "en": {"en-US"}, "zh-en": {"zh-CN", "en-US"}}
    if mode not in languages:
        raise HTTPException(status_code=422, detail="字幕显示模式无效")
    wanted = languages[mode]
    if not wanted.issubset({track.language for track in tracks if track.status == "valid"}):
        raise HTTPException(status_code=409, detail="所选语言的字幕轨尚未生成；本次切换不会调用翻译模型。")
    return {track.id: track.language in wanted for track in tracks}


def _execution_subtitle_track(track: SubtitleTrack) -> SubtitleExecutionTrack:
    return SubtitleExecutionTrack(
        id=track.id,
        workspace_id=track.workspace_id,
        task_id=track.task_id,
        timeline_version_id=track.timeline_version_id,
        language=track.language,
        source_language=track.source_language or track.language,
        style=SubtitleExecutionStyle(**track.style),
        cues=tuple(
            SubtitleExecutionCue(
                id=cue.id,
                source_cue_id=cue.source_cue_id,
                text=cue.text,
                start_seconds=cue.start_seconds,
                end_seconds=cue.end_seconds,
                speaker=cue.speaker,
                line_breaks=tuple(cue.line_breaks),
                emphasis=tuple(cue.emphasis),
            )
            for cue in track.cues
        ),
        generation_method=track.generation_method,
        status=track.status,
        model_id=track.model_id,
        artifact_ref=track.artifact_ref,
        transform_id=track.transform_id,
    )


def _clone_subtitle_track(
    track: SubtitleTrack,
    *,
    timeline_version_id: str,
    track_id: str | None = None,
    artifact_ref: str | None = None,
    transform_id: str | None = None,
    cues: list[SubtitleCue] | None = None,
    style: dict[str, object] | None = None,
    model_id: str | None = None,
) -> SubtitleTrack:
    now = utc_now()
    return SubtitleTrack(
        id=track_id or f"subtitle_{timeline_version_id}_{track.language.lower()}",
        workspace_id=track.workspace_id,
        task_id=track.task_id,
        timeline_version_id=timeline_version_id,
        language=track.language,
        source_language=track.source_language,
        style=style or track.style,
        cues=cues or track.cues,
        generation_method=track.generation_method,
        model_id=model_id if model_id is not None else track.model_id,
        artifact_ref=artifact_ref or track.artifact_ref,
        transform_id=transform_id or track.transform_id,
        status="valid",
        created_at=now,
        updated_at=now,
    )


def _cover_asset_for_version(
    workspace_id: str,
    task_id: str,
    version: TimelineVersion,
) -> CoverAsset | None:
    cover_id = version.snapshot.get("cover_asset_id")
    if not isinstance(cover_id, str) or not cover_id:
        return None
    try:
        return production_repo.get_cover_asset(workspace_id, task_id, cover_id)
    except KeyError:
        return None


def _clone_cover_asset(
    cover: CoverAsset,
    *,
    timeline_version_id: str,
) -> CoverAsset:
    now = utc_now()
    return replace(
        cover,
        id=f"cover_{uuid4().hex}",
        timeline_version_id=timeline_version_id,
        created_at=now,
        updated_at=now,
    )


def _publish_subtitle_text(
    *,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
    kind: str,
    input_refs: list[str],
    content: str,
    engine: str,
    parameters: dict[str, object],
) -> tuple[str, str]:
    input_digest = hashlib.sha256(
        json.dumps(
            {"inputs": input_refs, "content": content, "parameters": parameters},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    job = production_repo.create_job_run(
        JobRun(
            id=f"job_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            kind=kind,
            idempotency_key=f"{kind}:{proposal_id}:{input_digest}",
            input_digest=input_digest,
        )
    )
    running = production_repo.claim_job_run(workspace_id, job.id)
    logical_ref = f"artifacts/{task_id}/{job.id}/subtitles.srt"
    staging_root = Path(settings.video_output_path).expanduser() / ".job-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=f"{job.id}-", dir=staging_root) as staging:
            staged = Path(staging) / "subtitles.srt"
            staged.write_text(content, encoding="utf-8")
            publication = artifact_store.publish_file(logical_ref, staged)
        artifact = production_repo.create_artifact_record(
            ArtifactRecord(
                id=f"artifact_{job.id}_subtitles",
                workspace_id=workspace_id,
                task_id=task_id,
                kind="subtitle_track",
                logical_ref=publication.logical_ref,
                sha256=publication.sha256,
                size_bytes=publication.size_bytes,
                producer_job_id=job.id,
                input_refs=input_refs,
            )
        )
        transform = production_repo.create_transform_run(
            TransformRun(
                id=f"transform_{job.id}",
                workspace_id=workspace_id,
                task_id=task_id,
                job_id=job.id,
                transform_type=kind,
                input_digest=input_digest,
                input_refs=input_refs,
                output_refs=[artifact.logical_ref],
                engine=engine,
                parameters=parameters,
                status="succeeded",
                started_at=running.started_at or utc_now(),
                finished_at=utc_now(),
            )
        )
        for upstream in input_refs:
            production_repo.create_dependency_edge(
                DependencyEdge(
                    id=f"edge_{job.id}_{hashlib.sha256(upstream.encode()).hexdigest()[:20]}",
                    workspace_id=workspace_id,
                    task_id=task_id,
                    upstream_ref=upstream,
                    downstream_ref=artifact.logical_ref,
                    created_by_transform_id=transform.id,
                )
            )
        production_repo.finish_job_run(
            workspace_id, job.id, status="succeeded", output_refs=[artifact.logical_ref]
        )
        return artifact.logical_ref, transform.id
    except Exception as exc:
        try:
            production_repo.finish_job_run(
                workspace_id,
                job.id,
                status="failed",
                failure_reason=str(exc) or type(exc).__name__,
                impact="没有保存新的字幕轨，当前版本保持不变。",
                repair_action="检查字幕内容与本机产物目录后重试。",
            )
        except (KeyError, ValueError):
            pass
        raise


def _persist_subtitle_cover_edit(
    *,
    workspace_id: str,
    task: CreationTask,
    proposal: EditProposal,
    base_version: TimelineVersion,
    result_version_id: str,
    operation_type: str,
    operation_payload: dict[str, object],
    subtitle_tracks: list[SubtitleTrack],
    cover_asset: CoverAsset | None = None,
    updated_plan: CreationTaskMontagePlan | None = None,
    snapshot_source: dict[str, object] | None = None,
    narration_segments: list[NarrationSegment] | None = None,
    narration_artifact: NarrationArtifactRecord | None = None,
) -> CreationTaskEditConfirmResponse:
    now = utc_now()
    operation = EditOperation(
        id=f"operation_{uuid4().hex}",
        workspace_id=workspace_id,
        task_id=task.id,
        proposal_id=proposal.id,
        operation_type=operation_type,
        payload=operation_payload,
        base_version_id=base_version.id,
        result_version_id=result_version_id,
        status="applied",
        created_at=now,
        applied_at=now,
    )
    montage_payload = (
        creation_task_montage_plan_to_payload(updated_plan)
        if updated_plan is not None
        else None
    )
    snapshot = {
        **(snapshot_source if snapshot_source is not None else base_version.snapshot),
        **({"montage": montage_payload} if montage_payload is not None else {}),
        "subtitle_track_ids": [track.id for track in subtitle_tracks],
        "invalidated_refs": proposal.invalidated_refs,
        "edit_operation_id": operation.id,
        **({"narration_segments": [asdict(segment) for segment in narration_segments]}
           if narration_segments is not None else {}),
    }
    if cover_asset is not None:
        snapshot["cover_asset_id"] = cover_asset.id
    else:
        snapshot.pop("cover_asset_id", None)
    result_version = TimelineVersion(
        id=result_version_id,
        workspace_id=workspace_id,
        task_id=task.id,
        parent_version_id=base_version.id,
        sequence=base_version.sequence + 1,
        snapshot=snapshot,
        created_by_operation_id=operation.id,
        created_at=now,
    )
    try:
        _proposal, persisted_operation, _version, current_task = production_repo.confirm_edit_proposal(
            workspace_id,
            task.id,
            proposal.id,
            operation=operation,
            result_version=result_version,
            montage_plan_id=updated_plan.id if updated_plan is not None else None,
            montage_payload=montage_payload,
            subtitle_tracks=subtitle_tracks,
            cover_asset=cover_asset,
            narration_segments=narration_segments,
            narration_artifact=narration_artifact,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    persisted_plan = creation_task_montage_plan_from_payload(
        production_repo.get_creation_task_montage_plan(workspace_id, task.id)
    )
    narration = (
        production_repo.get_narration_artifact(workspace_id, current_task.narration_cache_key)
        if current_task.narration_cache_key
        else None
    )
    return CreationTaskEditConfirmResponse.model_validate(
        {
            "operation": asdict(persisted_operation),
            "montage": _creation_task_montage_response(persisted_plan).model_dump(),
            "narration": (
                _creation_task_narration_response(current_task, narration).model_dump()
                if narration is not None
                else None
            ),
        }
    )


def _history_edit_head(workspace_id: str, version_id: str | None, *, for_redo: bool):
    # Render-only versions have no edit. Control versions point to the exact
    # restored snapshot, which lets repeated undo/redo walk the existing history.
    while version_id:
        version = production_repo.get_timeline_version(workspace_id, version_id)
        if version.created_by_operation_id is None:
            version_id = version.parent_version_id
            continue
        operation = production_repo.get_edit_operation(workspace_id, version.created_by_operation_id)
        follow_restore = operation.operation_type == "RedoTimelineVersion" or (
            not for_redo and operation.operation_type == "UndoTimelineVersion")
        if follow_restore:
            version_id = operation.payload.get("restore_version_id")
            continue
        return version, operation
    return None, None


def _undo_target_versions(workspace_id: str, task: CreationTask) -> tuple[TimelineVersion | None, TimelineVersion | None]:
    try:
        changed, _ = _history_edit_head(workspace_id, task.current_timeline_version_id, for_redo=False)
        if changed is None or changed.parent_version_id is None:
            return None, None
        return changed, production_repo.get_timeline_version(workspace_id, changed.parent_version_id)
    except KeyError:
        return None, None


def _redo_target_versions(workspace_id: str, task: CreationTask) -> tuple[TimelineVersion | None, TimelineVersion | None]:
    try:
        current, operation = _history_edit_head(workspace_id, task.current_timeline_version_id, for_redo=True)
        if current is None or operation.operation_type != "UndoTimelineVersion":
            return None, None
        target_id = operation.payload.get("base_version_id")
        if not isinstance(target_id, str):
            return None, None
        return current, production_repo.get_timeline_version(workspace_id, target_id)
    except KeyError:
        return None, None


def _edit_material_constraint(command: str) -> str:
    value = command.casefold()
    for phrase in (
        "保留当前配音",
        "保留原配音",
        "保留配音",
        "配音不变",
        "把这个镜头",
        "把当前镜头",
        "换成另一个镜头",
        "换成其他镜头",
        "换成另一个画面",
        "替换成另一个镜头",
        "replace this clip",
        "with another clip",
        "改用",
        "替换成",
        "替换",
        "换成",
        "请",
    ):
        value = value.replace(phrase, " ")
    return value.strip(" ，。！？,.!?;；:：")


def _subtitle_text_from_command(command: str) -> str | None:
    for marker in ("改成", "改为", "换成", "replace with"):
        if marker in command.casefold():
            value = command[command.casefold().index(marker) + len(marker) :].strip()
            value = value.lstrip(" ：:").strip()
            for opening, closing in (("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")):
                if len(value) >= 2 and value.startswith(opening) and value.endswith(closing):
                    value = value[1:-1].strip()
                    break
            return value or None
    return None


def _retime_montage_for_narration_segments(
    workspace_id: str,
    plan: CreationTaskMontagePlan,
    segments: list[NarrationSegment],
    narration_reference: str,
) -> CreationTaskMontagePlan:
    updated = creation_task_montage_plan_from_payload(
        creation_task_montage_plan_to_payload(plan)
    )
    duration_by_segment = {
        segment.id: float(segment.duration_seconds or 0)
        for segment in segments
        if segment.status == "ready"
    }
    cues_by_segment: dict[str, list[NarrationCue]] = {}
    for cue in updated.cues:
        cues_by_segment.setdefault(cue.narration_segment_id, []).append(cue)
    for segment_id, cues in cues_by_segment.items():
        segment_duration = duration_by_segment.get(segment_id)
        if segment_duration is None or segment_duration <= 0:
            raise ValueError("时间线引用了不可用的配音片段")
        old_total = sum(cue.duration_seconds for cue in cues)
        cursor = allocated = 0.0
        for cue in cues:
            cursor += cue.duration_seconds
            end = round(segment_duration * cursor / old_total, 3)
            cue.duration_seconds = round(end - allocated, 3)
            allocated = end
    cue_by_id = {cue.id: cue for cue in updated.cues}
    old_duration_by_cue: dict[str, float] = {}
    for clip in plan.clips:
        old_duration_by_cue[clip.cue_id] = old_duration_by_cue.get(clip.cue_id, 0.0) + clip.timeline_end_seconds - clip.timeline_start_seconds
    allocated_by_cue: dict[str, float] = {}
    used_by_cue: dict[str, float] = {}
    cursor = 0.0
    for index, clip in enumerate(updated.clips):
        cue = cue_by_id.get(clip.cue_id)
        if cue is None:
            raise ValueError("镜头缺少对应的字幕与配音时间段")
        old_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
        playback_rate = clip_playback_rate(clip)
        used_by_cue[cue.id] = used_by_cue.get(cue.id, 0.0) + old_duration
        allocated_end = round(cue.duration_seconds * used_by_cue[cue.id] / old_duration_by_cue[cue.id], 3)
        duration = round(allocated_end - allocated_by_cue.get(cue.id, 0.0), 3)
        allocated_by_cue[cue.id] = allocated_end
        if clip.locked and (abs(cursor - clip.timeline_start_seconds) > .001 or abs(duration - old_duration) > .001):
            raise ValueError("配音时长变化会修改已锁定镜头，请先解锁受影响镜头")
        if index:
            previous = plan.clips[index - 1]
            original = plan.clips[index]
            if ((previous.asset_id, previous.segment_id, previous.cue_id, previous.person_render_id)
                == (original.asset_id, original.segment_id, original.cue_id, original.person_render_id)
                and abs(previous.source_end_seconds - original.source_start_seconds) < .001):
                clip.source_start_seconds = updated.clips[index - 1].source_end_seconds
        if clip.person_render_id:
            render = production_repo.get_person_render(workspace_id, plan.task_id, clip.person_render_id)
            source_end = render.duration_seconds if render.status == "ready" else None
        else:
            asset = production_repo.get_asset(workspace_id, clip.asset_id)
            source_segment = next((item for item in asset.segments if item.id == clip.segment_id), None)
            source_end = source_segment.end_seconds if source_segment else None
        if source_end is None or clip.source_start_seconds + duration * playback_rate > source_end + 1e-6:
            raise ValueError(
                "调整后的配音时长超过当前镜头可用范围，请先替换或放宽该镜头。"
            )
        clip.source_end_seconds = round(clip.source_start_seconds + duration * playback_rate, 3)
        clip.timeline_start_seconds = round(cursor, 3)
        cursor += duration
        clip.timeline_end_seconds = round(cursor, 3)
    updated.duration_seconds = round(cursor, 3)
    if updated.audio_timeline is not None:
        updated.audio_timeline = rebind_narration_audio(updated.audio_timeline, plan.narration_audio_path or "", narration_reference,
            round(plan.duration_seconds*1000), round(updated.duration_seconds*1000))
    updated.narration_audio_path = narration_reference
    updated.output_path = None
    updated.subtitle_path = None
    updated.status = "ready"
    updated.message = "所选配音片段已更新；字幕按原相对位置重定时，镜头范围已同步；请预览并重新渲染。"
    return updated


def _regenerate_creation_task_narration_segment(
    *,
    workspace_id: str,
    task: CreationTask,
    segment_id: str,
    delivery_instruction: str | None,
    generation_identity: str,
) -> tuple[list[NarrationSegment], NarrationArtifactRecord, CreationTaskMontagePlan]:
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    current_montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
    if current_montage.audio_timeline is not None:
        reference = current_montage.narration_audio_path or ""
        duration_ms = round(current_montage.duration_seconds*1000)
        rebind_narration_audio(current_montage.audio_timeline, reference, reference, duration_ms, duration_ms)
    if task.confirmed_script_id is None:
        raise ValueError("当前任务没有已确认文案")
    script = production_repo.get_creation_task_script(
        workspace_id, task.confirmed_script_id
    )
    target = production_repo.get_narration_segment(
        workspace_id, task.id, segment_id
    )
    if target.script_id != script.id:
        raise ValueError("配音片段不属于当前确认文案")
    if plan.selected_voice is None:
        raise ValueError("当前任务没有可执行的声音版本")
    inputs = production_repo.resolve_creation_task_narration_inputs(
        workspace_id=workspace_id,
        task_id=task.id,
        voice_profile_id=plan.selected_voice.profile_id,
        voice_version=plan.selected_voice.version,
    )
    reference_path = production_repo.resolve_workspace_media_reference(
        workspace_id, inputs.voice.reference_audio_path, require_registered=True
    )
    generated = narration_generator.generate_for_creation_task(
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        narration_text=target.text,
        language=target.language,
        max_duration_seconds=plan.duration_target_seconds,
        voice_identity=inputs.voice,
        pronunciation_entries=inputs.pronunciation_entries,
        resolved_reference_audio_path=reference_path,
        segment_id=target.id,
        delivery_instruction=delivery_instruction,
        generation_identity=generation_identity,
    )
    generated_record = production_repo.save_narration_artifact(
        NarrationArtifactRecord(
            id=f"narration_{generated.cache_key[:16]}",
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            voice_profile_id=generated.voice_profile_id,
            cache_key=generated.cache_key,
            audio_path=generated.audio_path,
            duration_seconds=generated.duration_seconds,
            sample_rate=generated.sample_rate,
            model_id=generated.model_id,
            language=generated.language,
            pronunciation_version=generated.pronunciation_version,
        )
    )
    updated_target = replace(
        target,
        audio_reference=production_repo.narration_artifact_reference(
            generated_record.cache_key
        ),
        duration_seconds=generated_record.duration_seconds,
        sample_rate=generated_record.sample_rate,
        model_id=generated_record.model_id,
        voice_profile_id=inputs.voice.id,
        voice_version=inputs.voice.version,
        pronunciation_version=generated_record.pronunciation_version,
        status="ready",
        updated_at=utc_now(),
    )
    segments = [
        updated_target if item.id == target.id else item
        for item in production_repo.list_narration_segments(
            workspace_id, task.id, script.id
        )
    ]
    segment_artifacts: list[NarrationArtifact] = []
    for segment in segments:
        if not segment.audio_reference or segment.duration_seconds is None or segment.sample_rate is None:
            raise ValueError("存在尚未生成的配音片段")
        cache_key = segment.audio_reference.removeprefix("narration://artifact/")
        record = production_repo.get_narration_artifact(workspace_id, cache_key)
        if record is None:
            raise ValueError("配音片段音频已丢失，请重新生成该片段")
        segment_artifacts.append(
            NarrationArtifact(
                task_id=task.id,
                script_id=script.id,
                audio_path=record.audio_path,
                duration_seconds=record.duration_seconds,
                sample_rate=record.sample_rate,
                model_id=record.model_id,
                voice_profile_id=record.voice_profile_id,
                cache_key=record.cache_key,
                language=record.language,
                pronunciation_version=record.pronunciation_version,
            )
        )
    combined = narration_generator.combine_creation_task_segments(
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        segments=segment_artifacts,
        max_duration_seconds=plan.duration_target_seconds,
    )
    combined_record = NarrationArtifactRecord(
        id=f"narration_{combined.cache_key[:16]}",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        voice_profile_id=combined.voice_profile_id,
        cache_key=combined.cache_key,
        audio_path=combined.audio_path,
        duration_seconds=combined.duration_seconds,
        sample_rate=combined.sample_rate,
        model_id=combined.model_id,
        language=combined.language,
        pronunciation_version=combined.pronunciation_version,
    )
    montage = creation_task_montage_plan_from_payload(
        production_repo.get_creation_task_montage_plan(workspace_id, task.id)
    )
    updated_montage = _retime_montage_for_narration_segments(
        workspace_id,
        montage,
        segments,
        production_repo.narration_artifact_reference(combined.cache_key),
    )
    return segments, combined_record, updated_montage


def _presenter_share_selection(plan: CreationTaskMontagePlan, target: int) -> tuple[set[str], float]:
    durations: dict[str, float] = {}
    for cue in plan.cues:
        durations[cue.source_section_id] = durations.get(cue.source_section_id, 0) + cue.duration_seconds
    selected = set(select_presenter_sections(list(durations.items()), target))
    actual = round(100 * sum(
        clip.timeline_end_seconds - clip.timeline_start_seconds
        for clip in plan.clips
        if next(cue for cue in plan.cues if cue.id == clip.cue_id).source_section_id in selected
    ) / plan.duration_seconds, 1)
    return selected, actual


def _presenter_share_broll_candidate(workspace_id: str, plan: CreationTaskMontagePlan, clip: MontageClip):
    for candidate in _creation_task_montage_candidates(workspace_id, plan, clip):
        asset = production_repo.get_asset(workspace_id, candidate.asset_id)
        segment = next(item for item in asset.segments if item.id == candidate.segment_id)
        if effective_segment_usage_role(asset, segment) == "b_roll":
            return candidate
    return None


def _presenter_share_changed_clips(plan: CreationTaskMontagePlan, selected: set[str]) -> list[MontageClip]:
    cues = {cue.id: cue for cue in plan.cues}
    new_render_sections = {cues[clip.cue_id].source_section_id for clip in plan.clips
        if not clip.person_render_id and cues[clip.cue_id].source_section_id in selected}
    return [clip for clip in plan.clips if
        bool(clip.person_render_id) != (cues[clip.cue_id].source_section_id in selected)
        or cues[clip.cue_id].source_section_id in new_render_sections]


def _preview_presenter_share(task: CreationTask, request: Request, payload: CreationTaskEditPreviewRequest, target: int) -> CreationTaskEditPreviewResponse:
    workspace_id = current_workspace_id(request)
    plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
    base = _ensure_montage_base_version(workspace_id, task, plan)
    selected, actual = _presenter_share_selection(plan, target)
    cues = {cue.id: cue for cue in plan.cues}
    changed = _presenter_share_changed_clips(plan, selected)
    if not changed and plan.presenter_share_target == target:
        raise HTTPException(status_code=409, detail="露脸比例与当前版本相同")
    if any(clip.locked for clip in changed):
        raise HTTPException(status_code=409, detail="要调整的范围有锁定镜头，请先解锁")
    plan_record = production_repo.get_creation_plan(workspace_id, task.plan_id)
    if any(cues[clip.cue_id].source_section_id in selected for clip in changed):
        if plan_record.selected_voice is None or not _lipsync_provider(plan_record.lipsync_quality).ready:
            raise HTTPException(status_code=409, detail="当前人物口型能力不可用，无法增加露脸镜头")
    for clip in changed:
        if cues[clip.cue_id].source_section_id in selected:
            continue
        if _presenter_share_broll_candidate(workspace_id, plan, clip) is None:
            raise HTTPException(status_code=409, detail="没有足够长的可用素材镜头覆盖减少的露脸区间")
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(
        id=f"intent_{uuid4().hex}", workspace_id=workspace_id, task_id=task.id,
        raw_command=payload.raw_command, context_stage=payload.context_stage,
        selected_object_refs=[], playhead_ms=None, intent_type="presenter_share",
        scope={"kind": "whole_video"}, constraints={"preserve_narration": True},
        confidence=1, status="interpreted", created_at=now,
    ))
    current = round(100 * sum(clip.timeline_end_seconds - clip.timeline_start_seconds for clip in plan.clips if clip.person_render_id) / plan.duration_seconds, 1)
    proposal = production_repo.create_edit_proposal(EditProposal(
        id=f"proposal_{uuid4().hex}", workspace_id=workspace_id, task_id=task.id,
        intent_id=intent.id, summary=f"露脸目标改为 {target}%，按当前段落预计实际 {actual}%；涉及 {len(changed)} 个镜头。",
        target_refs=[f"clip:{clip.id}" for clip in changed],
        operation={"operation_type": "ChangePresenterShare", "payload": {"target": target, "base_version_id": base.id}},
        invalidated_refs=_logical_montage_output_refs(plan),
        preview={"before": {"ref": f"timeline:{base.id}", "label": f"露脸 {current}%", "detail": f"{len(plan.clips)} 个镜头"},
                 "after": {"ref": f"target:{target}", "label": f"目标 {target}% · 预计 {actual}%", "detail": f"将调整 {len(changed)} 个镜头"}},
        external_effects=["确认后只为新增露脸段制作本地口型，配音和字幕不变；可撤销。"],
        created_at=now, updated_at=now,
    ))
    return _edit_preview_response(intent, proposal)


@app.post(
    "/api/creation-tasks/{task_id}/edits/preview",
    response_model=CreationTaskEditPreviewResponse,
)
def preview_creation_task_edit(
    task_id: str,
    request: Request,
    payload: CreationTaskEditPreviewRequest,
) -> CreationTaskEditPreviewResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    command = payload.raw_command.strip()
    share_command = re.fullmatch(r"(?:把)?(?:这条视频的?|整片的?)?(?:露脸|口播出镜)(?:占比|比例)?(?:改成|改为|调到|设为)\s*(0|20|40|60|80|100)\s*%[。！]?", command)
    if share_command and payload.context_stage in {"montage", "review"}:
        return _preview_presenter_share(task, request, payload, int(share_command.group(1)))
    alignment_command = re.fullmatch(r"(?:按配音|根据配音)(?:校准|对齐)(中文|英文|双语|中英双语|中英)?字幕[。！]?", command)
    if alignment_command and payload.context_stage in {"montage", "review", "subtitle_cover"}:
        return _preview_subtitle_alignment(task, request, payload, alignment_command[1])
    if payload.context_stage in {"montage", "review"} and re.fullmatch(
        r"(?:提取|添加|保留)(?:(?:当前|这个|所选)镜头的?)?原声[。！]?", command
    ):
        return _preview_clip_original_audio(task_id, request, payload)
    if payload.context_stage in {"montage", "review"} and re.fullmatch(r"(?:剪掉|删掉|删除)(?:当前镜头(?:的)?)?(?:停顿|静音)[。！]?", command):
        return _preview_remove_silence(task, request, payload)
    if payload.context_stage in {"montage","review"} or (payload.context_stage == "subtitle_cover" and is_indexed_subtitle_command(command)):
        combined=_preview_compound_edit(task,request,payload)
        if combined is not None:
            return combined
    canvas_commands = {"改横版":"16:9", "改成横版":"16:9", "改成16:9":"16:9", "改成竖版":"9:16", "改竖版":"9:16", "改成9:16":"9:16", "改成方形":"1:1", "改成1:1":"1:1"}
    canvas_command = command.rstrip("。！")
    keep_picture = canvas_command.endswith("，别裁掉人物") or canvas_command.endswith("，保留完整画面")
    if keep_picture:
        canvas_command = canvas_command.split("，")[0]
    if payload.context_stage in {"montage", "review"} and canvas_command in canvas_commands:
        return _preview_canvas(task_id, request, CreationTaskExactEditRequest(operation_type="ChangeCanvas",
            aspect_ratio=canvas_commands[canvas_command], **({"fit_mode":"fit"} if keep_picture else {})))
    display_commands = {"关闭字幕": "none", "不显示字幕": "none", "只显示中文字幕": "zh", "只显示英文字幕": "en", "显示中英双语字幕": "zh-en"}
    if payload.context_stage in {"montage", "review", "subtitle_cover"} and command.rstrip("。！") in display_commands:
        return preview_creation_task_exact_edit(task_id, request, CreationTaskExactEditRequest(
            operation_type="ConfigureSubtitleTrack", subtitle_display=display_commands[command.rstrip("。！")]))

    if payload.context_stage in {"montage", "review", "subtitle_cover"}:
        try:
            style_patch = compile_subtitle_style_command(command)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if style_patch is not None:
            return _preview_subtitle_style(task_id, request, CreationTaskExactEditRequest(
                operation_type="ConfigureSubtitleTrack", style=style_patch))

    if payload.context_stage in {"montage","review"} and any(word in command for word in ("标题","图片","标识")):
        try:
            overlay_plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
            edit=compile_overlay_command(command,overlay_plan.graphic_overlays,graphic_sources(production_repo,workspace_id,task_id) if any(word in command for word in ("图片","标识")) else (),payload.selected_object_refs)
        except (ValueError,KeyError) as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        if edit:
            action,value,identity=edit
            return _preview_graphic_overlay(task_id,request,CreationTaskExactEditRequest(operation_type="EditGraphicOverlay",
                overlay_action=action,graphic_overlay=value,overlay_id=identity))
    if "/" in command or "\\" in command:
        raise _topic_failure_response(
            status_code=409,
            error="edit_path_input_forbidden",
            reason="修改要求中不能指定本机或网络文件路径。",
            impact="系统没有读取该路径，任务内容保持不变。",
            repair_action="先将内容导入当前工作区，再选中具体对象提出修改要求。",
            retryable=True,
        )
    selected_ref = (
        payload.selected_object_refs[0]
        if len(payload.selected_object_refs) == 1
        else ""
    )
    if payload.context_stage == "subtitle_cover" and len(payload.selected_object_refs) == 2:
        cues = [ref for ref in payload.selected_object_refs if ref.startswith("cue:")]
        tracks = [ref for ref in payload.selected_object_refs if ref.startswith("subtitle-track:")]
        if len(cues) == len(tracks) == 1:
            selected_ref = cues[0]
    if selected_ref.startswith("clip:"):
        transition=re.fullmatch(r"(?:(?:当前|这个)?镜头)?(?:与前一个镜头)?(?:添加|改为|设置)?叠化(?:转场)?\s*(\d+(?:\.\d+)?)\s*秒[。！]?",command)
        remove_transition=command.rstrip("。！") in {"取消叠化","改为硬切"}
        if transition or remove_transition:
            return _preview_transition(task_id,request,CreationTaskExactEditRequest(operation_type="SetClipTransition",
                clip_id=selected_ref.removeprefix("clip:"),transition_seconds=0 if remove_transition else float(transition[1])))
        try:
            visual_plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
            selected_clip=next(clip for clip in visual_plan.clips if clip.id==selected_ref.removeprefix("clip:"))
            effects=compile_visual_command(command,selected_clip.visual_effects)
        except (ValueError,KeyError,StopIteration) as exc:
            raise HTTPException(status_code=422,detail=str(exc) or "当前镜头不可用") from exc
        if effects is not None:
            return preview_creation_task_visual(task_id,request,VisualEditRequest(base_version_id=task.current_timeline_version_id,
                clip_ids=[selected_clip.id],effects=effects,raw_command=command))
    if selected_ref.startswith("audio:"):
        try:
            montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
            audio_edit = compile_audio_command(command, selected_ref.removeprefix("audio:"), _effective_audio_timeline(montage),
                task.current_timeline_version_id, payload.playhead_ms)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return preview_creation_task_audio(task_id, request, audio_edit)
    if selected_ref.startswith("narration:"):
        if payload.context_stage not in {"narration", "montage", "review"}:
            raise _topic_failure_response(
                status_code=409,
                error="edit_scope_unavailable",
                reason="当前步骤不能修改配音片段。",
                impact="任务内容没有发生变化。",
                repair_action="进入配音、视频剪辑或检查成片后选择一段配音。",
                retryable=True,
            )
        try:
            delivery_tempo = compile_narration_command(command)
        except ValueError as exc:
            raise _topic_failure_response(
                status_code=409,
                error="edit_intent_unsupported",
                reason=str(exc),
                impact="任务内容没有发生变化。",
                repair_action="输入“这句重新配音”或“这句读慢一点”。",
                retryable=True,
            )
        segment_id = selected_ref.removeprefix("narration:")
        try:
            segment = production_repo.get_narration_segment(
                workspace_id, task.id, segment_id
            )
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="creation task narration edit target not found") from exc
        if montage.status != "rendered":
            raise _topic_failure_response(
                status_code=409,
                error="rendered_version_required",
                reason="当前任务还没有可比较的成片版本。",
                impact="无法准确展示分段配音修改的失效范围。",
                repair_action="先渲染当前成片，再修改配音片段。",
                retryable=True,
            )
        invalidated_refs = [
            *( [segment.audio_reference] if segment.audio_reference else [] ),
            *[
                f"subtitle-cue:{cue.id}"
                for cue in montage.cues
                if cue.narration_segment_id == segment.id
            ],
            *_logical_montage_output_refs(montage),
        ]
        base_version = _ensure_montage_base_version(workspace_id, task, montage)
        now = utc_now()
        intent = production_repo.create_edit_intent(
            EditIntent(
                id=f"intent_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task.id,
                raw_command=command,
                context_stage=payload.context_stage,
                selected_object_refs=[selected_ref],
                playhead_ms=payload.playhead_ms,
                intent_type="regenerate_narration_segment",
                scope={"kind": "narration_segment", "segment_id": segment.id},
                constraints={"delivery_instruction": command},
                confidence=0.98,
                status="interpreted",
                created_at=now,
            )
        )
        proposal = production_repo.create_edit_proposal(
            EditProposal(
                id=f"proposal_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task.id,
                intent_id=intent.id,
                summary=("只重新生成所选配音片段"
                         + (f"，按 {delivery_tempo:g} 倍速处理" if delivery_tempo != 1.0 else "")
                         + "，并同步它对应的字幕时间与镜头范围。"),
                target_refs=[selected_ref],
                operation={
                    "operation_type": "RegenerateNarrationSegment",
                    "payload": {
                        "narration_segment_id": segment.id,
                        "expected_audio_reference": segment.audio_reference,
                        "delivery_instruction": command,
                        "base_version_id": base_version.id,
                    },
                },
                invalidated_refs=list(dict.fromkeys(invalidated_refs)),
                preview={
                    "before": {
                        "ref": selected_ref,
                        "label": "当前配音片段",
                        "detail": f"{segment.duration_seconds or 0:.1f} 秒 · {segment.text}",
                    },
                    "after": {
                        "ref": selected_ref,
                        "label": "重新生成所选片段",
                        "detail": command,
                    },
                },
                external_effects=["确认后会调用一次真实声音模型；不会重做其他配音片段。"],
                created_at=now,
                updated_at=now,
            )
        )
        return _edit_preview_response(intent, proposal)
    if selected_ref.startswith("cover:"):
        if payload.context_stage != "subtitle_cover":
            raise _topic_failure_response(
                status_code=409,
                error="edit_scope_unavailable",
                reason="当前步骤不能修改封面。",
                impact="任务内容没有发生变化。",
                repair_action="进入字幕与封面步骤后选择封面。",
                retryable=True,
            )
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="creation task montage plan not found") from exc
        title_match = re.search(
            r"标题(?:写|改成|改为|为)?[：:]?\s*[“\"']?(.+?)[”\"']?(?:[，,。]|$)",
            command,
        )
        title = title_match.group(1).strip(" “”。\"'") if title_match else None
        if not title:
            raise _topic_failure_response(
                status_code=409,
                error="cover_title_missing",
                reason="没有识别出要显示的封面标题。",
                impact="没有生成封面，当前任务保持不变。",
                repair_action="输入“标题写……”并保留完整标题。",
                retryable=True,
            )
        seconds_match = re.search(r"(\d+(?:\.\d+)?)\s*秒", command)
        frame_time = float(seconds_match.group(1)) if seconds_match else None
        ordinal_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        ordinal_match = re.search(r"第([一二三四五六七八九十]|\d+)个?镜头", command)
        if frame_time is None and ordinal_match:
            raw_index = ordinal_match.group(1)
            index = int(raw_index) if raw_index.isdigit() else ordinal_map.get(raw_index, 0)
            if 1 <= index <= len(montage.clips):
                clip = montage.clips[index - 1]
                frame_time = min(
                    clip.timeline_end_seconds - 0.05,
                    clip.timeline_start_seconds + 0.2,
                )
        if frame_time is None:
            frame_time = payload.playhead_ms / 1000 if payload.playhead_ms is not None else 0.0
        return preview_creation_task_exact_edit(
            task_id,
            request,
            CreationTaskExactEditRequest(
                operation_type="GenerateCoverFromFrame",
                frame_time_seconds=frame_time,
                title=title,
            ),
        )
    if selected_ref.startswith("clip:") and payload.context_stage in {"montage", "review", "subtitle_cover"}:
        linked_speed = re.fullmatch(r"(?:把)?(?:这个|当前)?镜头(?:改成|改为|调到)\s*(\d+(?:\.\d+)?)\s*倍速[，, ]*(?:声音和字幕一起|同步声音和字幕|连同声音字幕)[。！]?", command)
        if linked_speed:
            value = float(linked_speed.group(1))
            if not .25 <= value <= 4:
                raise HTTPException(status_code=422, detail="关联倍速须在0.25–4之间")
            return _preview_linked_speed(task_id, request, CreationTaskExactEditRequest(
                operation_type="ChangeLinkedSpeed", clip_id=selected_ref.removeprefix("clip:"), playback_rate=value))
        speed_match = re.fullmatch(r"(?:把)?(?:这个|当前)?镜头(?:画面)?(?:改成|改为|调到|设为)\s*(\d+(?:\.\d+)?)\s*倍速[，, ]*(?:保留配音|声音不变|保持时长)[。！]?", command)
        if speed_match:
            if not .25 <= float(speed_match.group(1)) <= 4:
                raise HTTPException(status_code=422, detail="画面倍速须在0.25–4之间")
            return preview_creation_task_exact_edit(task_id, request,
                CreationTaskExactEditRequest(operation_type="ChangeClipSpeed",
                    clip_id=selected_ref.removeprefix("clip:"), playback_rate=float(speed_match.group(1))))
        linked_commands = {"删除这个镜头": "DeleteClip", "删除当前镜头": "DeleteClip",
            "删掉这个镜头，把后面接上": "DeleteClip", "复制这个镜头": "DuplicateClip", "复制当前镜头": "DuplicateClip"}
        linked_type = linked_commands.get(command.rstrip("。！"))
        if linked_type:
            return _preview_linked_clip_edit(task_id, request,
                CreationTaskExactEditRequest(operation_type=linked_type, clip_id=selected_ref.removeprefix("clip:")))
        split_match = re.fullmatch(r"(?:把)?(?:这个|当前)?镜头(?:在|从)\s*(\d+(?:\.\d+)?)\s*秒(?:处)?(?:分割|切开)[。！]?", command)
        split_here = command.rstrip("。！") in {"从这里分割镜头", "在播放位置分割镜头", "从这里切开", "分割当前镜头"}
        if split_match or split_here:
            if split_here and payload.playhead_ms is None:
                raise HTTPException(status_code=422, detail="请先在播放器定位分割位置，或指定成片秒数。")
            return preview_creation_task_exact_edit(task_id, request, CreationTaskExactEditRequest(
                operation_type="SplitClip", clip_id=selected_ref.removeprefix("clip:"),
                split_time_seconds=float(split_match.group(1)) if split_match else payload.playhead_ms / 1000))
    batch_match = re.fullmatch(r'全文把[“"](.+?)[”"](?:替换成|替换为|改成|改为)[“"](.*)[”"][。！]?', command)
    if batch_match and payload.context_stage == "subtitle_cover":
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        tracks = _current_subtitle_tracks(workspace_id, task, montage=montage)
        track_ref = next((ref.removeprefix("subtitle-track:") for ref in payload.selected_object_refs if ref.startswith("subtitle-track:")), None)
        matching = [track for track in tracks if track.id == track_ref] if track_ref else [track for track in tracks
            if selected_ref == f"subtitle-track:{track.id}" or any(selected_ref == f"cue:{cue.id}" for cue in track.cues)]
        if len(matching) != 1:
            raise HTTPException(status_code=409, detail="请先选择要全文替换的字幕语言轨")
        if len(batch_match.group(1)) > 96 or len(batch_match.group(2)) > 96:
            raise HTTPException(status_code=422, detail="单次查找和替换文字最多96字")
        return _preview_subtitle_replace(task_id, request, CreationTaskExactEditRequest(operation_type="ReplaceSubtitleText",
            track_id=matching[0].id, find_text=batch_match.group(1), replacement_text=batch_match.group(2)))
    structure_command = re.fullmatch(r"(?:把)?(?:当前|这条)字幕(?:在第(\d+)字后(?:、|，|,)?(?:在)?([0-9.]+)秒拆分|与下一条合并|删除)[。！]?", command)
    delete_command = command.rstrip("。！") in {"删除当前字幕", "删除这条字幕"}
    if (structure_command or delete_command) and payload.context_stage == "subtitle_cover":
        track_ref = next((ref.removeprefix("subtitle-track:") for ref in payload.selected_object_refs if ref.startswith("subtitle-track:")), None)
        if not selected_ref.startswith("cue:") or not track_ref:
            raise HTTPException(status_code=409, detail="请先选择当前语言轨中的字幕")
        operation = "DeleteSubtitleCue" if delete_command or "删除" in command else "MergeSubtitleCue"
        extra = {}
        if structure_command and structure_command.group(1):
            operation = "SplitSubtitleCue"
            try:
                extra = {"split_offset":int(structure_command.group(1)), "split_time_seconds":float(structure_command.group(2))}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="拆分时间须为有效秒数") from exc
        return preview_creation_task_exact_edit(task_id, request, CreationTaskExactEditRequest(operation_type=operation,
            track_id=track_ref, cue_id=selected_ref.removeprefix("cue:"), **extra))
    if selected_ref.startswith("cue:"):
        if payload.context_stage not in {"montage", "review", "subtitle_cover"}:
            raise _topic_failure_response(
                status_code=409,
                error="edit_scope_unavailable",
                reason="当前步骤不能修改字幕。",
                impact="任务内容没有发生变化。",
                repair_action="进入视频剪辑或检查成片后选择一条字幕。",
                retryable=True,
            )
        shift = re.fullmatch(r"(?:把)?(?:这条|这句|当前)?字幕(?:整体)?(?:向前|往前|提前|向后|往后|延后|推迟)\s*(\d+(?:\.\d+)?)\s*(毫秒|秒)[。！]?", command)
        if shift:
            montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
            tracks = _current_subtitle_tracks(workspace_id, task, montage=montage)
            track_ref = next((ref.removeprefix("subtitle-track:") for ref in payload.selected_object_refs if ref.startswith("subtitle-track:")), None)
            candidates = [(track, cue) for track in tracks if track_ref is None or track.id == track_ref
                for cue in track.cues if selected_ref == f"cue:{cue.id}"]
            if len(candidates) != 1:
                raise HTTPException(status_code=409, detail="请明确选择一条字幕及其语言轨。")
            track, cue = candidates[0]
            offset = float(shift.group(1)) / (1000 if shift.group(2) == "毫秒" else 1)
            if any(word in command for word in ("向前", "往前", "提前")):
                offset = -offset
            try:
                edit = CreationTaskExactEditRequest(
                    operation_type="UpdateSubtitleCue", cue_id=cue.id, track_id=track.id,
                    start_seconds=round(cue.start_seconds + offset, 3), end_seconds=round(cue.end_seconds + offset, 3))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="字幕移动后的时间必须有效，且不能早于成片开始。") from exc
            return preview_creation_task_exact_edit(task_id, request, edit)
        timing = re.fullmatch(r"(?:把)?(?:这条|这句|当前)?字幕(?:的)?(?:时间)?(?:调整为|设为|改为|改成|从)?\s*(\d+(?:\.\d+)?)\s*秒?\s*(?:到|至|–|-)\s*(\d+(?:\.\d+)?)\s*秒[。！]?", command)
        if timing:
            return preview_creation_task_exact_edit(task_id, request, CreationTaskExactEditRequest(
                operation_type="UpdateSubtitleCue", cue_id=selected_ref.removeprefix("cue:"),
                track_id=next((ref.removeprefix("subtitle-track:") for ref in payload.selected_object_refs if ref.startswith("subtitle-track:")), None),
                start_seconds=float(timing.group(1)), end_seconds=float(timing.group(2))))
        new_text = _subtitle_text_from_command(command)
        if new_text is None:
            raise _topic_failure_response(
                status_code=409,
                error="edit_intent_unsupported",
                reason="没有识别出新的字幕文字。",
                impact="字幕和成片保持不变。",
                repair_action="输入“把这句字幕改成……”并保留要显示的完整文字。",
                retryable=True,
            )
        cue_id = selected_ref.removeprefix("cue:")
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
            tracks = _current_subtitle_tracks(workspace_id, task, montage=montage)
            track_ref = next((ref.removeprefix("subtitle-track:") for ref in payload.selected_object_refs if ref.startswith("subtitle-track:")), None)
            selected_track = next((track for track in tracks if (track_ref is None or track.id == track_ref)
                and any(cue.id == cue_id for cue in track.cues)), None)
            if track_ref is not None and selected_track is None:
                raise HTTPException(status_code=409, detail="所选字幕不属于当前语言轨，请重新选择")
            if selected_track is not None:
                return preview_creation_task_exact_edit(task_id, request, CreationTaskExactEditRequest(
                    operation_type="UpdateSubtitleCue", track_id=selected_track.id,
                    cue_id=cue_id, text=new_text))
            cue = next(item for item in montage.cues if item.id == cue_id)
        except (KeyError, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="creation task subtitle edit target not found") from exc
        if new_text == cue.text:
            raise _topic_failure_response(
                status_code=409,
                error="edit_no_change",
                reason="新字幕与当前字幕相同。",
                impact="没有创建新的时间线版本。",
                repair_action="输入不同的字幕文字后再试。",
                retryable=True,
            )
        base_version = _ensure_montage_base_version(workspace_id, task, montage)
        task = production_repo.get_creation_task(workspace_id, task.id)
        subtitle_track = next(
            (
                track
                for track in _current_subtitle_tracks(
                    workspace_id, task, current_version=base_version, montage=montage
                )
                if any(track_cue.id == cue.id for track_cue in track.cues)
            ),
            None,
        )
        now = utc_now()
        intent = production_repo.create_edit_intent(
            EditIntent(
                id=f"intent_{uuid4().hex}", workspace_id=workspace_id,
                task_id=task.id, raw_command=command,
                context_stage=payload.context_stage,
                selected_object_refs=[selected_ref], playhead_ms=payload.playhead_ms,
                intent_type="update_subtitle_cue",
                scope={"kind": "subtitle_cue", "cue_id": cue.id},
                constraints={"preserve_narration": True, "preserve_clips": True},
                confidence=0.99, status="interpreted", created_at=now,
            )
        )
        proposal = production_repo.create_edit_proposal(
            EditProposal(
                id=f"proposal_{uuid4().hex}", workspace_id=workspace_id,
                task_id=task.id, intent_id=intent.id,
                summary="只修改所选字幕；配音和镜头保持不变。",
                target_refs=[selected_ref],
                operation={"operation_type": "UpdateSubtitleCue", "payload": {
                    "cue_id": cue.id, "text": new_text,
                    "expected_text": cue.text, "base_version_id": base_version.id,
                    **(
                        {
                            "track_id": subtitle_track.id,
                            "line_breaks": next(
                                item.line_breaks
                                for item in subtitle_track.cues
                                if item.id == cue.id
                            ),
                            "emphasis": next(
                                item.emphasis
                                for item in subtitle_track.cues
                                if item.id == cue.id
                            ),
                            "style": subtitle_track.style,
                        }
                        if subtitle_track is not None
                        else {}
                    ),
                }},
                invalidated_refs=_logical_montage_output_refs(montage),
                preview={
                    "before": {"ref": selected_ref, "label": "当前字幕", "detail": cue.text},
                    "after": {"ref": selected_ref, "label": "修改后字幕", "detail": new_text},
                },
                external_effects=["确认后只更新字幕版本；重新渲染前不会执行媒体进程。"],
                created_at=now, updated_at=now,
            )
        )
        return _edit_preview_response(intent, proposal)
    if selected_ref.startswith("clip:") and any(
        token in command.casefold()
        for token in (
            "出镜",
            "口型",
            "人物",
            "表情",
            "avatar",
            "lip sync",
            "lipsync",
            "expression",
        )
    ):
        if payload.context_stage not in {"montage", "review"}:
            raise _topic_failure_response(
                status_code=409,
                error="edit_scope_unavailable",
                reason="当前步骤还不能把人物口型片段写入成片。",
                impact="任务内容没有发生变化。",
                repair_action="进入视频剪辑或检查成片后选择一个镜头。",
                retryable=True,
            )
        clip_id = selected_ref.removeprefix("clip:")
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
            clip = next(item for item in montage.clips if item.id == clip_id)
            cue = next(item for item in montage.cues if item.id == clip.cue_id)
            segment = production_repo.get_narration_segment(
                workspace_id, task.id, cue.narration_segment_id
            )
            creation_plan = production_repo.get_creation_plan(workspace_id, task.plan_id)
            person_provider = _lipsync_provider(creation_plan.lipsync_quality)
            selected_voice = creation_plan.selected_voice
            if selected_voice is None:
                raise ValueError("当前任务没有绑定人物声音版本")
            person = production_repo.get_person_profile(
                workspace_id,
                selected_voice.person_profile_id,
                selected_voice.person_version,
            )
            current_render = (
                production_repo.get_person_render(
                    workspace_id, task.id, clip.person_render_id
                )
                if clip.person_render_id
                else None
            )
            if current_render is not None:
                person = production_repo.get_person_profile(
                    workspace_id,
                    current_render.person_profile_id,
                    current_render.person_version,
                )
        except (KeyError, StopIteration) as exc:
            raise HTTPException(
                status_code=404,
                detail="creation task person edit target not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if re.fullmatch(r"(?:表情|expression).*(?:自然|开心|微笑|natural|happy|smile)", command.casefold()):
            raise _topic_failure_response(
                status_code=409,
                error="person_expression_control_unavailable",
                reason="当前本地人物模型只能沿用原视频中的表情，不能单独改变表情。",
                impact="人物片段、时间线和成片保持不变。",
                repair_action="可以输入“这里只重做口型，其他都不要动”。",
                retryable=False,
            )
        try:
            person_operation = compile_person_edit_command(command)
        except ValueError as exc:
            raise _topic_failure_response(
                status_code=409, error="edit_intent_unsupported", reason=str(exc),
                impact="人物片段、时间线和成片保持不变。",
                repair_action="请明确指定换成人物出镜或重做所选镜头口型。",
                retryable=True,
            ) from exc
        regenerate_requested = person_operation == "RegeneratePersonRender"
        if regenerate_requested and current_render is None:
            raise _topic_failure_response(
                status_code=409,
                error="person_render_required",
                reason="所选镜头还不是人物口型片段，当前没有可以重做的口型。",
                impact="当前镜头和成片保持不变。",
                repair_action="先输入“这一段换成当前人物出镜”。",
                retryable=True,
            )
        if not person_provider.ready:
            raise _topic_failure_response(
                status_code=503,
                error="capability_unavailable",
                reason=person_provider.message,
                impact="人物片段、时间线和成片保持不变。",
                repair_action="配置本地人物口型运行环境后重试。",
                retryable=True,
            )
        if montage.status != "rendered":
            raise _topic_failure_response(
                status_code=409,
                error="rendered_version_required",
                reason="当前任务还没有可比较的成片版本。",
                impact="无法准确展示人物出镜会替换哪一个镜头。",
                repair_action="先渲染当前成片，再选择镜头提出人物出镜要求。",
                retryable=True,
            )
        if segment.status != "ready" or not segment.audio_reference:
            raise _topic_failure_response(
                status_code=409,
                error="narration_segment_unavailable",
                reason="所选镜头没有可用于口型生成的已完成配音。",
                impact="当前镜头和成片保持不变。",
                repair_action="先完成对应配音分段，再重新提交人物出镜要求。",
                retryable=True,
            )
        if segment.language not in person_provider.supported_languages:
            raise _topic_failure_response(
                status_code=503, error="language_unavailable",
                reason="当前本地人物模型未声明支持所选配音的语言。",
                impact="人物片段、时间线和成片保持不变。",
                repair_action="选择已声明支持该配音语言的本地人物模型。",
                retryable=False,
            )
        try:
            person_source_start, person_source_end = _person_source_range(workspace_id, task, montage, clip, segment)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        person_transforms = production_repo.list_transform_runs(workspace_id, task.id)
        existing_render = next(
            (
                item
                for item in reversed(
                    production_repo.list_person_renders(workspace_id, task.id)
                )
                if item.narration_segment_id == segment.id
                and item.person_profile_id == person.id
                and item.person_version == person.version
                and item.voice_profile_id == segment.voice_profile_id
                and item.voice_version == segment.voice_version
                and item.model_id == person_provider.model_id
                and item.motion == "natural"
                and item.expression == "neutral"
                and item.status == "ready"
                and _person_render_matches_narration(item, segment, person_transforms)
                and _person_render_media_path(item) is not None
            ),
            None,
        )
        operation_type = (
            "RegeneratePersonRender" if regenerate_requested else "ChangePersonSegment"
        )
        intent_type = (
            "regenerate_person_render" if regenerate_requested else "change_person_segment"
        )
        base_version = _ensure_montage_base_version(workspace_id, task, montage)
        now = utc_now()
        intent = production_repo.create_edit_intent(
            EditIntent(
                id=f"intent_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task.id,
                raw_command=command,
                context_stage=payload.context_stage,
                selected_object_refs=[selected_ref],
                playhead_ms=payload.playhead_ms,
                intent_type=intent_type,
                scope={
                    "kind": "clip",
                    "clip_id": clip.id,
                    "narration_segment_id": segment.id,
                },
                constraints={
                    "preserve_narration": True,
                    "preserve_subtitles": True,
                    "preserve_other_clips": True,
                },
                confidence=0.98,
                status="interpreted",
                created_at=now,
            )
        )
        proposal = production_repo.create_edit_proposal(
            EditProposal(
                id=f"proposal_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task.id,
                intent_id=intent.id,
                summary=(
                    "只重新生成所选人物片段的口型；配音、字幕和其他镜头保持不变。"
                    if regenerate_requested
                    else (
                        f"把所选镜头换成 {person.display_name} 的口型片段；"
                        "配音、字幕和其他镜头保持不变。"
                    )
                ),
                target_refs=[selected_ref],
                operation={
                    "operation_type": operation_type,
                    "payload": {
                        "clip_id": clip.id,
                        "cue_id": cue.id,
                        "narration_segment_id": segment.id,
                        "expected_audio_reference": segment.audio_reference,
                        "source_start_seconds": person_source_start,
                        "source_end_seconds": person_source_end,
                        "person_profile_id": person.id,
                        "person_version": person.version,
                        "expected_asset_id": clip.asset_id,
                        "expected_segment_id": clip.segment_id,
                        "expected_person_render_id": clip.person_render_id,
                        "motion": "natural",
                        "expression": "neutral",
                        "lipsync_quality": creation_plan.lipsync_quality,
                        "base_version_id": base_version.id,
                        **(
                            {"reuse_person_render_id": existing_render.id}
                            if existing_render is not None
                            and not regenerate_requested
                            else {}
                        ),
                    },
                },
                invalidated_refs=_logical_montage_output_refs(montage),
                preview={
                    "before": {
                        "ref": selected_ref,
                        "label": "当前镜头",
                        "detail": (
                            f"{clip.timeline_start_seconds:.1f}–"
                            f"{clip.timeline_end_seconds:.1f} 秒"
                        ),
                    },
                    "after": {
                        "ref": f"person:{person.id}:{person.version}",
                        "label": (
                            f"重新生成 {person.display_name} 的口型"
                            if regenerate_requested
                            else f"{person.display_name} 出镜"
                        ),
                        "detail": (
                            f"使用配音分段第 {person_source_start:.2f}–{person_source_end:.2f} 秒；"
                            "沿用人物原视频动作与表情，配音和字幕不变。"
                        ),
                    },
                },
                external_effects=[
                    (
                        "确认后会生成一个新人物片段；旧片段保留在历史版本中。"
                        if regenerate_requested
                        else (
                            "确认后复用已验证的人物口型片段，只使当前成片输出失效。"
                            if existing_render is not None
                            else "确认后调用一次真实人物口型模型；不会重做文案、配音、字幕或其他镜头。"
                        )
                    )
                ],
                created_at=now,
                updated_at=now,
            )
        )
        return _edit_preview_response(intent, proposal)
    if payload.context_stage not in {"montage", "review"}:
        raise _topic_failure_response(
            status_code=409,
            error="edit_scope_unavailable",
            reason="当前步骤不支持这项镜头修改。",
            impact="任务内容没有发生变化。",
            repair_action="进入视频剪辑或检查成片，选中一个镜头后再输入替换要求。",
            retryable=True,
        )
    if not any(token in command.casefold() for token in ("换", "替换", "改用", "replace")):
        raise _topic_failure_response(
            status_code=409,
            error="edit_intent_unsupported",
            reason="这条镜头指令还无法完整执行。",
            impact="任务内容没有发生变化。",
            repair_action="可指定替换镜头、曝光、冷暖或美颜；其他操作暂时使用右侧已有控件。",
            retryable=True,
        )
    if len(payload.selected_object_refs) != 1 or not payload.selected_object_refs[0].startswith("clip:"):
        raise _topic_failure_response(
            status_code=409,
            error="edit_scope_required",
            reason="还没有确定要替换哪个镜头。",
            impact="系统不会猜测修改范围。",
            repair_action="先在故事板中选中一个镜头，再提交修改要求。",
            retryable=True,
        )
    clip_id = payload.selected_object_refs[0].removeprefix("clip:")
    try:
        plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
        clip = next(item for item in plan.clips if item.id == clip_id)
        current_asset = None if clip.person_render_id else production_repo.get_asset(workspace_id, clip.asset_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="creation task edit target not found") from exc
    if plan.status != "rendered" or not plan.output_path or not plan.subtitle_path:
        raise _topic_failure_response(
            status_code=409,
            error="rendered_version_required",
            reason="当前剪辑还不是一个已验证的成片版本。",
            impact="无法准确展示修改会使哪些产物失效。",
            repair_action="先完成当前成片渲染，再提出镜头替换要求。",
            retryable=True,
        )
    invalidated_refs = _logical_montage_output_refs(plan)
    candidates = [
        candidate
        for candidate in _creation_task_montage_candidates(workspace_id, plan, clip)
        if (candidate.asset_id, candidate.segment_id) != (clip.asset_id, clip.segment_id)
    ]
    material_constraint = _edit_material_constraint(command)
    if material_constraint:
        slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
        matched_refs = production_repo.search_material_segments(
            workspace_id,
            query=material_constraint,
            usage_roles=["a_roll", "b_roll"],
            min_duration_seconds=slot_duration,
            limit=24,
        )
        candidate_by_ref = {
            (f"asset:{candidate.asset_id}", f"segment:{candidate.segment_id}"): candidate
            for candidate in candidates
        }
        candidates = [
            candidate_by_ref[(match.asset_ref, match.segment_ref)]
            for match in matched_refs
            if (match.asset_ref, match.segment_ref) in candidate_by_ref
        ]
    if not candidates:
        raise _topic_failure_response(
            status_code=409,
            error="replacement_clip_unavailable",
            reason=(
                f"当前工作区没有符合“{material_constraint}”且满足时间槽的其他镜头。"
                if material_constraint
                else "当前工作区没有满足这个时间槽的其他可用镜头。"
            ),
            impact="原镜头和当前成片保持不变。",
            repair_action="导入或审核更多素材后重新提出替换要求。",
            retryable=True,
        )
    base_version = _ensure_montage_base_version(workspace_id, task, plan)
    replacement = candidates[0]
    now = utc_now()
    intent = production_repo.create_edit_intent(
        EditIntent(
            id=f"intent_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            raw_command=command,
            context_stage=payload.context_stage,
            selected_object_refs=payload.selected_object_refs,
            playhead_ms=payload.playhead_ms,
            intent_type="replace_visual",
            scope={"kind": "clip", "clip_id": clip.id},
            constraints={
                "preserve_narration": True,
                "preserve_timing": True,
                "material_query": material_constraint or None,
            },
            confidence=0.95,
            status="interpreted",
            created_at=now,
        )
    )
    proposal = production_repo.create_edit_proposal(
        EditProposal(
            id=f"proposal_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            intent_id=intent.id,
            summary=f"把选中的镜头替换为 {replacement.file_name}，保留当前配音和时间位置。",
            target_refs=[f"clip:{clip.id}"],
            operation={
                "operation_type": "ReplaceClip",
                "payload": {
                    "clip_id": clip.id,
                    "asset_id": replacement.asset_id,
                    "segment_id": replacement.segment_id,
                    "source_start_seconds": replacement.start_seconds,
                    "expected_asset_id": clip.asset_id,
                    "expected_segment_id": clip.segment_id,
                    "base_version_id": base_version.id,
                },
            },
            invalidated_refs=invalidated_refs,
            preview={
                "before": {
                    "ref": f"clip:{clip.id}",
                    "label": current_asset.file_name if current_asset else "人物口播",
                    "detail": f"{clip.source_start_seconds:.1f}–{clip.source_end_seconds:.1f} 秒",
                },
                "after": {
                    "ref": f"segment:{replacement.segment_id}",
                    "label": replacement.file_name,
                    "detail": f"{replacement.start_seconds:.1f}–{replacement.start_seconds + clip.timeline_end_seconds - clip.timeline_start_seconds:.1f} 秒 · {replacement.description}",
                },
            },
            external_effects=["确认后只更新任务版本，不会立即调用模型或渲染。"],
            created_at=now,
            updated_at=now,
        )
    )
    return _edit_preview_response(intent, proposal)


@app.post(
    "/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm",
    response_model=CreationTaskEditConfirmResponse,
)
def confirm_creation_task_edit(
    task_id: str,
    proposal_id: str,
    request: Request,
) -> CreationTaskEditConfirmResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    try:
        proposal = production_repo.get_edit_proposal(workspace_id, proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task edit proposal not found") from exc
    if proposal.task_id != task_id or proposal.status != "proposed":
        raise HTTPException(status_code=409, detail="creation task edit proposal is not confirmable")
    operation_draft = proposal.operation
    operation_payload = operation_draft.get("payload")
    operation_type = operation_draft.get("operation_type")
    if operation_type == "ChangePresenterShare" and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="时间线已变化，请重新预览露脸比例")
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        target = operation_payload["target"]
        selected, actual = _presenter_share_selection(plan, target)
        cues = {cue.id: cue for cue in plan.cues}
        plan_record = production_repo.get_creation_plan(workspace_id, task.plan_id)
        section_segments = {cue.source_section_id: cue.narration_segment_id for cue in plan.cues}
        section_starts = {section_id: min(clip.timeline_start_seconds for clip in plan.clips
            if cues[clip.cue_id].source_section_id == section_id) for section_id in section_segments}
        changed_clips = _presenter_share_changed_clips(plan, selected)
        if any(clip.locked for clip in changed_clips):
            raise HTTPException(status_code=409, detail="要调整的范围有锁定镜头，请重新预览")
        broll_candidates = {}
        for clip in changed_clips:
            if cues[clip.cue_id].source_section_id in selected:
                continue
            candidate = _presenter_share_broll_candidate(workspace_id, plan, clip)
            if candidate is None:
                raise HTTPException(status_code=409, detail="可用素材已变化，请重新预览露脸比例")
            broll_candidates[clip.id] = candidate
        renders: dict[str, PersonRender] = {}
        for section_id in selected:
            if not any(cues[clip.cue_id].source_section_id == section_id and not clip.person_render_id for clip in plan.clips):
                continue
            voice = plan_record.selected_voice
            if voice is None:
                raise HTTPException(status_code=409, detail="当前任务缺少已授权人物和声音")
            render = _generate_creation_task_person_render_record(workspace_id, task_id,
                CreationTaskPersonRenderRequest(narration_segment_id=section_segments[section_id],
                    person_profile_id=voice.person_profile_id, person_version=voice.person_version))
            if render.status != "ready" or _person_render_media_path(render) is None:
                raise HTTPException(status_code=503, detail="新增露脸段口型尚未生成，原时间线保持不变")
            renders[section_id] = render
        for clip in plan.clips:
            section_id = cues[clip.cue_id].source_section_id
            wants_face = section_id in selected
            if wants_face == bool(clip.person_render_id) and section_id not in renders:
                continue
            if clip.locked:
                raise HTTPException(status_code=409, detail="要调整的范围有锁定镜头，请重新预览")
            duration = clip.timeline_end_seconds - clip.timeline_start_seconds
            if wants_face:
                render = renders[section_id]
                start = round(clip.timeline_start_seconds - section_starts[section_id], 3)
                if start + duration > (render.duration_seconds or 0) + 1 / 30:
                    raise HTTPException(status_code=409, detail="新口型视频不足以覆盖露脸范围，原时间线保持不变")
                clip.person_render_id = render.id
                clip.asset_id = f"person:{render.id}"
                clip.segment_id = section_segments[section_id]
                clip.source_path = f"person-render://{render.id}"
                clip.source_start_seconds = start
                clip.source_end_seconds = min(round(start + duration, 3), render.duration_seconds or 0)
                clip.role = "a_roll"
                clip.match_reasons = ["按当前配音生成口型", f"露脸目标 {target}%"]
            else:
                candidate = broll_candidates[clip.id]
                clip.person_render_id = None
                clip.asset_id = candidate.asset_id
                clip.segment_id = candidate.segment_id
                clip.source_path = production_repo.workspace_media_reference_for_asset(workspace_id, candidate.asset_id)
                clip.source_start_seconds = candidate.start_seconds
                clip.source_end_seconds = round(candidate.start_seconds + duration, 3)
                clip.role = "b_roll"
                clip.match_reasons = ["按当前配音选择素材镜头", f"露脸目标 {target}%"]
            clip.needs_confirmation = False
        plan.presenter_share_target = target
        _prepare_changed_montage(plan, message=f"露脸目标 {target}%，实际 {actual}%；配音和字幕不变，可重新渲染并撤销。")
        return _persist_typed_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=plan, operation_type=operation_type, operation_payload=operation_payload)
    if operation_type == "AlignSubtitleTiming" and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload["base_version_id"]:
            raise HTTPException(status_code=409, detail="时间线已变化，请重新校准字幕")
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)
        track = next((item for item in tracks if item.id == operation_payload["track_id"]), None)
        if track is None:
            raise HTTPException(status_code=409, detail="字幕轨已变化，请重新校准")
        changed = apply_aligned_timings(track, operation_payload["timings"], montage.duration_seconds)
        changed_tracks = (synchronize_translated_subtitles(tracks, changed, montage.duration_seconds)
            if operation_payload.get("include_translation") else [changed if item.id == track.id else item for item in tracks])
        _prepare_changed_montage(montage, message="字幕时间已校准，可重新渲染；撤销恢复原时间。")
        return _persist_typed_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=montage, operation_type="AlignSubtitleTiming", operation_payload=operation_payload,
            edited_subtitle_tracks=changed_tracks)
    if operation_type == "EditCompoundTimeline" and isinstance(operation_payload,dict):
        if task.current_timeline_version_id!=operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409,detail="时间线已变化，请重新预览全部修改")
        montage=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
        data=creation_task_montage_plan_to_payload(montage)
        data.update(clips=operation_payload["clips"],audio_timeline=operation_payload["audio_timeline"])
        candidate=creation_task_montage_plan_from_payload(data)
        try:
            _task_audio().validate(workspace_id,task_id,candidate.audio_timeline,candidate.duration_seconds)
        except (ValueError,OSError,subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=409,detail="音频来源已变化，请重新预览") from exc
        base=production_repo.get_timeline_version(workspace_id,task.current_timeline_version_id)
        tracks=_current_subtitle_tracks(workspace_id,task,current_version=base,montage=montage)
        changed_tracks=apply_compound_subtitles(tracks,operation_payload["steps"],montage.duration_seconds)
        _prepare_changed_montage(candidate,message="复合修改已应用，可重新渲染；一次撤销恢复全部修改。")
        return _persist_typed_montage_edit(workspace_id=workspace_id,task=task,proposal=proposal,
            base_version=production_repo.get_timeline_version(workspace_id,task.current_timeline_version_id),updated_plan=candidate,
            operation_type="EditCompoundTimeline",operation_payload=operation_payload,edited_subtitle_tracks=changed_tracks)
    if operation_type == "EditGraphicOverlay" and isinstance(operation_payload,dict):
        if task.current_timeline_version_id!=operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409,detail="时间线已变化，请重新预览图文修改")
        montage=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
        montage.graphic_overlays=operation_payload["graphic_overlays"]
        _prepare_changed_montage(montage,message="图文已更新，可以重新渲染。")
        return _persist_typed_montage_edit(workspace_id=workspace_id,task=task,proposal=proposal,
            base_version=production_repo.get_timeline_version(workspace_id,task.current_timeline_version_id),updated_plan=montage,
            operation_type="EditGraphicOverlay",operation_payload=operation_payload)
    if operation_type == "SetClipTransition" and isinstance(operation_payload,dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409,detail="时间线已变化，请重新预览转场")
        montage=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
        targets=[clip for clip in montage.clips if clip.id in operation_payload["expected_clips"]]
        if len(targets)!=2 or any(clip.locked or asdict(clip)!=operation_payload["expected_clips"][clip.id] for clip in targets):
            raise HTTPException(status_code=409,detail="转场相邻镜头已变化，请重新预览")
        next(clip for clip in targets if clip.id==operation_payload["clip_id"]).transition_in_seconds=operation_payload["transition_seconds"]
        _prepare_changed_montage(montage,message="转场已更新，配音与字幕时轴不变。")
        return _persist_typed_montage_edit(workspace_id=workspace_id,task=task,proposal=proposal,
            base_version=production_repo.get_timeline_version(workspace_id,task.current_timeline_version_id),
            updated_plan=montage,operation_type="SetClipTransition",operation_payload=operation_payload)
    if operation_type == "AdjustVisualEffects" and isinstance(operation_payload,dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409,detail="时间线已经变化，不能确认旧的画面提案")
        montage=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
        expected=operation_payload["expected_clips"]
        targets=[clip for clip in montage.clips if clip.id in expected]
        if len(targets)!=len(expected) or any(clip.locked or asdict(clip)!=expected[clip.id] for clip in targets):
            raise HTTPException(status_code=409,detail="所选镜头已变化，请重新预览")
        effects=VisualEffects.model_validate(operation_payload["effects"]).model_dump()
        for clip in targets:
            updated=with_visual_effects(clip,dict(effects))
            clip.visual_effects=updated.visual_effects
            clip.fade_envelope_rate=updated.fade_envelope_rate
            clip.fade_envelope_offset_frames=updated.fade_envelope_offset_frames
            clip.fade_envelope_duration_frames=updated.fade_envelope_duration_frames
        _prepare_changed_montage(montage,message="画面效果已更新，可以重新渲染。")
        base=production_repo.get_timeline_version(workspace_id,task.current_timeline_version_id)
        return _persist_typed_montage_edit(workspace_id=workspace_id,task=task,proposal=proposal,base_version=base,
            updated_plan=montage,operation_type="AdjustVisualEffects",operation_payload=operation_payload)
    if operation_type == "EditAudioTimeline" and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="时间线已经变化，不能确认旧的音频提案")
        try:
            montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
            current_audio = _effective_audio_timeline(montage)
            if current_audio.model_dump() != operation_payload.get("expected_audio_timeline"):
                raise ValueError("原音轨已变化，请重新预览")
            updated_audio = AudioTimeline.model_validate(operation_payload["audio_timeline"])
            edit = AudioEditRequest.model_validate(operation_payload["edit"])
            if edit.action == "replace_narration":
                if creation_execution_mode == "scheduled":
                    raise HTTPException(status_code=503, detail="电脑录音校验尚未接入整机许可")
                if any(clip.person_render_id for clip in montage.clips):
                    raise ValueError("当前人物口型依赖旧配音，请先移除或重做口型")
                source_ref = edit.values.get("source_ref")
                if not isinstance(source_ref, str):
                    raise ValueError("录音引用无效")
                recording, measured_ms = _task_audio().recording(
                    workspace_id, task_id, source_ref, verify_digest=True)
                duration_ms = round(montage.duration_seconds * 1000)
                if (recording.sha256 != operation_payload.get("recording_sha256")
                        or measured_ms != operation_payload.get("recording_duration_ms")
                        or not duration_ms - 20 <= measured_ms <= duration_ms + 250
                        or apply_audio_edit(current_audio, edit, duration_ms).model_dump() != updated_audio.model_dump()):
                    raise ValueError("录音或旁白替换提案已变化，请重新预览")
            else:
                _task_audio().validate(workspace_id, task_id, updated_audio, montage.duration_seconds)
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=409, detail="音频提案不再适用，请检查源文件并重新预览") from exc
        montage.audio_timeline = updated_audio
        _prepare_changed_montage(montage, message="音轨已更新，可以试听或重新渲染成片。")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        return _persist_typed_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base,
            updated_plan=montage, operation_type="EditAudioTimeline", operation_payload=operation_payload)
    if operation_type == "ChangeCanvas" and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="版本已变化，请重新预览画幅")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        montage.aspect_ratio = operation_payload["aspect_ratio"]
        if operation_payload.get("fit_mode"):
            for clip in montage.clips:
                clip.fit_mode = operation_payload["fit_mode"]
        version_id = f"timeline_{uuid4().hex}"
        tracks = [_clone_subtitle_track(track, timeline_version_id=version_id) for track in _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)]
        _prepare_changed_montage(montage, message="画幅已修改，请重新渲染；封面需从新成片重新生成。")
        return _persist_subtitle_cover_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base,
            result_version_id=version_id, operation_type=operation_type, operation_payload=operation_payload,
            subtitle_tracks=tracks, cover_asset=None, updated_plan=montage)
    if operation_type == "ConfigureSubtitleTrack" and isinstance(operation_payload, dict) and "subtitle_display" in operation_payload:
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        base_version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base_version, montage=montage)
        selection = _subtitle_display_selection(tracks, operation_payload["subtitle_display"])
        version_id = f"timeline_{uuid4().hex}"
        updated_tracks = [_clone_subtitle_track(track, timeline_version_id=version_id,
            style={**track.style, "enabled": selection[track.id]}) for track in tracks]
        cover = _cover_asset_for_version(workspace_id, task.id, base_version)
        _prepare_changed_montage(montage, message="字幕显示已更新，请重新渲染成片。")
        return _persist_subtitle_cover_edit(workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base_version, result_version_id=version_id, operation_type=operation_type,
            operation_payload=operation_payload, subtitle_tracks=updated_tracks,
            cover_asset=_clone_cover_asset(cover, timeline_version_id=version_id) if cover else None,
            updated_plan=montage)
    if operation_type == "ConfigureSubtitleTrack" and isinstance(operation_payload, dict) and "style" in operation_payload:
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="字幕版本已变化，请重新预览样式")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)
        patch = validate_style_patch(operation_payload["style"])
        version_id = f"timeline_{uuid4().hex}"
        changed = [_clone_subtitle_track(track, timeline_version_id=version_id,
            style={**track.style, **patch} if track.status == "valid" else track.style) for track in tracks]
        cover = _cover_asset_for_version(workspace_id, task.id, base)
        _prepare_changed_montage(montage, message="字幕样式已保存；重新渲染后写入成片。")
        return _persist_subtitle_cover_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base,
            result_version_id=version_id, operation_type=operation_type, operation_payload=operation_payload,
            subtitle_tracks=changed, cover_asset=_clone_cover_asset(cover, timeline_version_id=version_id) if cover else None,
            updated_plan=montage)
    if operation_type == "ConfigureSubtitleTrack" and isinstance(operation_payload, dict):
        source_track_id = operation_payload.get("source_track_id")
        base_version_id = operation_payload.get("base_version_id")
        if (
            not isinstance(source_track_id, str)
            or not source_track_id
            or not isinstance(base_version_id, str)
            or not base_version_id
            or operation_payload.get("target_language") != "en-US"
        ):
            raise HTTPException(status_code=409, detail="invalid subtitle track configuration")
        if task.current_timeline_version_id != base_version_id:
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        base_version = production_repo.get_timeline_version(workspace_id, base_version_id)
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="subtitle edit base no longer exists") from exc
        current_tracks = _current_subtitle_tracks(
            workspace_id, task, current_version=base_version, montage=montage
        )
        source = next((track for track in current_tracks if track.id == source_track_id), None)
        if source is None or source.language != "zh-CN" or source.status != "valid":
            raise HTTPException(status_code=409, detail="subtitle source track has changed")
        result_version_id = f"timeline_{uuid4().hex}"
        service = SubtitleTranslationService(
            subtitle_translation_provider_from_settings(settings)
        )
        execution = service.translate_zh_to_en(
            _execution_subtitle_track(source),
            translated_track_id=f"subtitle_{result_version_id}_en-us",
            timeline_version_id=result_version_id,
        )
        production_repo.append_task_event(TaskEvent(
            id=f"event_{execution.execution_id}",
            workspace_id=workspace_id,
            task_id=task.id,
            job_id=None,
            event_type="subtitle_translation_returned",
            payload={
                "proposal_id": proposal.id,
                "source_track_id": source.id,
                "provider_name": execution.provider_name,
                "model_id": execution.model_id,
                "status": execution.status,
                "failure_code": execution.failure_code,
                "failure_reason": execution.failure_reason,
                "impact": execution.impact,
                "repair_action": execution.repair_action,
                "retryable": execution.retryable,
            },
        ))
        if execution.status != "succeeded" or execution.translated_track is None:
            raise _topic_failure_response(
                status_code=503,
                error=execution.failure_code or "subtitle_translation_failed",
                reason=execution.failure_reason or "英文字幕没有生成可信结果。",
                impact=execution.impact or "中文字幕和当前成片保持不变。",
                repair_action=execution.repair_action or "检查文字模型后重试。",
                retryable=bool(execution.retryable),
            )
        try:
            english_ref, transform_id = _publish_subtitle_text(
                workspace_id=workspace_id,
                task_id=task.id,
                proposal_id=proposal.id,
                kind="subtitle_translation",
                input_refs=[source.artifact_ref or f"subtitle-track:{source.id}"],
                content=render_srt(execution.translated_track),
                engine=f"{execution.provider_name}:{execution.model_id}",
                parameters={
                    "prompt_schema_version": execution.prompt_schema_version,
                    "provider_execution_id": execution.provider_execution_id,
                    "source_track_id": source.id,
                    "target_language": "en-US",
                },
            )
        except (OSError, ValueError) as exc:
            raise _topic_failure_response(
                status_code=503,
                error="subtitle_artifact_failed",
                reason="英文字幕已返回，但未能保存为可信字幕产物。",
                impact="当前时间线和成片保持不变。",
                repair_action=str(exc),
                retryable=True,
            ) from exc
        translated = execution.translated_track
        english_track = SubtitleTrack(
            id=translated.id,
            workspace_id=workspace_id,
            task_id=task.id,
            timeline_version_id=result_version_id,
            language="en-US",
            source_language="zh-CN",
            style=asdict(translated.style),
            cues=[
                SubtitleCue(
                    id=cue.id,
                    source_cue_id=cue.source_cue_id,
                    text=cue.text,
                    start_seconds=cue.start_seconds,
                    end_seconds=cue.end_seconds,
                    speaker=cue.speaker,
                    line_breaks=list(cue.line_breaks),
                    emphasis=list(cue.emphasis),
                )
                for cue in translated.cues
            ],
            generation_method="translation",
            model_id=execution.model_id,
            artifact_ref=english_ref,
            transform_id=transform_id,
            status="valid",
        )
        chinese_track = _clone_subtitle_track(
            source,
            timeline_version_id=result_version_id,
            track_id=f"subtitle_{result_version_id}_zh-cn",
        )
        current_cover = _cover_asset_for_version(workspace_id, task.id, base_version)
        carried_cover = (
            _clone_cover_asset(current_cover, timeline_version_id=result_version_id)
            if current_cover is not None
            else None
        )
        montage, _ = _prepare_changed_montage(
            montage,
            message="中英字幕已生成并保持原时间轴；请重新渲染以写入成片。",
        )
        return _persist_subtitle_cover_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=base_version,
            result_version_id=result_version_id,
            operation_type="ConfigureSubtitleTrack",
            operation_payload=dict(operation_payload),
            subtitle_tracks=[chinese_track, english_track],
            cover_asset=carried_cover,
            updated_plan=montage,
        )
    if operation_type == "GenerateCoverFromFrame" and isinstance(operation_payload, dict):
        base_version_id = operation_payload.get("base_version_id")
        source_video_ref = operation_payload.get("source_video_ref")
        title = operation_payload.get("title")
        frame_time_seconds = operation_payload.get("frame_time_seconds")
        if (
            not isinstance(base_version_id, str)
            or not isinstance(source_video_ref, str)
            or not isinstance(title, str)
            or not isinstance(frame_time_seconds, (int, float))
        ):
            raise HTTPException(status_code=409, detail="invalid cover generation operation")
        if task.current_timeline_version_id != base_version_id:
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        base_version = production_repo.get_timeline_version(workspace_id, base_version_id)
        job = production_repo.create_job_run(
            JobRun(
                id=f"job_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task.id,
                kind="cover_frame_generation",
                idempotency_key=f"cover:{proposal.id}",
                input_digest=hashlib.sha256(
                    json.dumps(operation_payload, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
            )
        )
        running = production_repo.claim_job_run(workspace_id, job.id)
        cover_ref = f"artifacts/{task.id}/{job.id}/cover.png"
        try:
            generated = CoverGenerationService(settings.video_output_path).generate(
                CoverGenerationRequest(
                    source_video_path=artifact_store.resolve_file(source_video_ref),
                    output_ref=cover_ref,
                    frame_time_seconds=float(frame_time_seconds),
                    title=title,
                    safe_area=CoverSafeArea(),
                    aspect_ratio=operation_payload.get("aspect_ratio", "9:16"),
                )
            )
            evidence = generated.evidence
            artifact = production_repo.create_artifact_record(
                ArtifactRecord(
                    id=f"artifact_{job.id}_cover",
                    workspace_id=workspace_id,
                    task_id=task.id,
                    kind="cover_image",
                    logical_ref=evidence.output_ref,
                    sha256=evidence.output_sha256,
                    size_bytes=evidence.output_size_bytes,
                    producer_job_id=job.id,
                    input_refs=[source_video_ref],
                )
            )
            transform = production_repo.create_transform_run(
                TransformRun(
                    id=f"transform_{job.id}",
                    workspace_id=workspace_id,
                    task_id=task.id,
                    job_id=job.id,
                    transform_type="cover_from_frame",
                    input_digest=job.input_digest,
                    input_refs=[source_video_ref],
                    output_refs=[artifact.logical_ref],
                    engine=f"{evidence.engine}+{evidence.layout_engine}",
                    parameters=evidence.parameters,
                    status="succeeded",
                    started_at=running.started_at or utc_now(),
                    finished_at=utc_now(),
                )
            )
            production_repo.create_dependency_edge(
                DependencyEdge(
                    id=f"edge_{job.id}_cover",
                    workspace_id=workspace_id,
                    task_id=task.id,
                    upstream_ref=source_video_ref,
                    downstream_ref=artifact.logical_ref,
                    created_by_transform_id=transform.id,
                )
            )
            production_repo.finish_job_run(
                workspace_id, job.id, status="succeeded", output_refs=[artifact.logical_ref]
            )
        except Exception as exc:
            try:
                production_repo.finish_job_run(
                    workspace_id,
                    job.id,
                    status="failed",
                    failure_reason=str(exc) or type(exc).__name__,
                    impact="当前时间线和已有成片保持不变。",
                    repair_action="检查成片、选帧时间与 FFmpeg 后重试。",
                )
            except (KeyError, ValueError):
                pass
            raise _topic_failure_response(
                status_code=503,
                error="cover_generation_failed",
                reason="封面没有生成可信图片产物。",
                impact="当前时间线和已有成片保持不变。",
                repair_action=str(exc),
                retryable=True,
            ) from exc
        result_version_id = f"timeline_{uuid4().hex}"
        tracks = [
            _clone_subtitle_track(track, timeline_version_id=result_version_id)
            for track in _current_subtitle_tracks(
                workspace_id, task, current_version=base_version
            )
        ]
        cover = CoverAsset(
            id=f"cover_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task.id,
            timeline_version_id=result_version_id,
            source_video_ref=source_video_ref,
            frame_time_seconds=float(frame_time_seconds),
            title=title,
            safe_area={key: float(value) for key, value in asdict(CoverSafeArea()).items()},
            layout={"title_position": "bottom", "text_align": "center"},
            generation_method="video_frame",
            model_or_engine=f"{generated.evidence.engine}+{generated.evidence.layout_engine}",
            artifact_ref=generated.evidence.output_ref,
            transform_id=transform.id,
            status="valid",
        )
        return _persist_subtitle_cover_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=base_version,
            result_version_id=result_version_id,
            operation_type="GenerateCoverFromFrame",
            operation_payload=dict(operation_payload),
            subtitle_tracks=tracks,
            cover_asset=cover,
        )
    if operation_type in {"ReplaceSubtitleText", "SplitSubtitleCue", "MergeSubtitleCue", "DeleteSubtitleCue"} and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="字幕版本已变化，请重新预览修改")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)
        track = next((track for track in tracks if track.id == operation_payload["track_id"]), None)
        if track is None:
            raise HTTPException(status_code=409, detail="字幕轨已变化，请重新预览")
        try:
            changed, changes = _apply_caption_edit(track, operation_type, operation_payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        version_id = f"timeline_{uuid4().hex}"
        if changed.cues:
            subtitle_ref, transform_id = _publish_subtitle_text(workspace_id=workspace_id, task_id=task.id,
                proposal_id=proposal.id, kind="subtitle_text_edit", input_refs=[track.artifact_ref or f"subtitle-track:{track.id}"],
                content=render_srt(_execution_subtitle_track(changed)), engine="local-subtitle-edit/v1",
                parameters={"track_id":track.id, "changes":changes})
            changed = _clone_subtitle_track(changed, timeline_version_id=version_id, artifact_ref=subtitle_ref, transform_id=transform_id)
        # Match the existing source-correction rule: dependent translations need regeneration.
        retained = [_clone_subtitle_track(other, timeline_version_id=version_id) for other in tracks
            if other.id != track.id and not (operation_type == "ReplaceSubtitleText" and track.source_language is None and other.source_language == track.language)]
        if operation_type == "ReplaceSubtitleText" and track.source_language is None:
            text_by_id = {cue.id:cue.text for cue in changed.cues}
            for cue in montage.cues:
                if cue.id in text_by_id:
                    cue.text = text_by_id[cue.id]
        cover = _cover_asset_for_version(workspace_id, task.id, base)
        _prepare_changed_montage(montage, message="字幕修改已保存，配音未改；请重新渲染。")
        return _persist_subtitle_cover_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base,
            result_version_id=version_id, operation_type=operation_type, operation_payload=operation_payload,
            subtitle_tracks=([changed] if changed.cues else []) + retained, cover_asset=_clone_cover_asset(cover, timeline_version_id=version_id) if cover else None,
            updated_plan=montage)
    if (
        operation_type == "UpdateSubtitleCue"
        and isinstance(operation_payload, dict)
        and isinstance(operation_payload.get("track_id"), str)
    ):
        track_id = str(operation_payload["track_id"])
        cue_id = operation_payload.get("cue_id")
        text = operation_payload.get("text")
        expected_text = operation_payload.get("expected_text")
        base_version_id = operation_payload.get("base_version_id")
        if any(
            not isinstance(value, str) or not value
            for value in (cue_id, text, expected_text, base_version_id)
        ):
            raise HTTPException(status_code=409, detail="invalid subtitle edit operation")
        if task.current_timeline_version_id != base_version_id:
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        base_version = production_repo.get_timeline_version(workspace_id, str(base_version_id))
        montage = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task.id)
        )
        current_tracks = _current_subtitle_tracks(
            workspace_id, task, current_version=base_version, montage=montage
        )
        track = next((item for item in current_tracks if item.id == track_id), None)
        if track is None:
            raise HTTPException(status_code=409, detail="subtitle track has changed")
        cue = next((item for item in track.cues if item.id == cue_id), None)
        if cue is None or cue.text != expected_text:
            raise HTTPException(status_code=409, detail="subtitle cue has changed")
        updated_track=update_subtitle_cue(track,cue.id,text=str(text),start_seconds=operation_payload.get("start_seconds"),
            end_seconds=operation_payload.get("end_seconds"),line_breaks=list(operation_payload.get("line_breaks") or []),emphasis=list(operation_payload.get("emphasis") or []))
        updated_cues=updated_track.cues
        updated_style = operation_payload.get("style")
        if not isinstance(updated_style, dict):
            raise HTTPException(status_code=409, detail="invalid subtitle style")
        try:
            subtitle_schedule([replace(track, status="valid", style={**updated_style, "enabled": True}, cues=updated_cues)], montage.duration_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        result_version_id = f"timeline_{uuid4().hex}"
        provisional = _clone_subtitle_track(
            track,
            timeline_version_id=result_version_id,
            cues=updated_cues,
            style=dict(updated_style),
        )
        subtitle_ref, transform_id = _publish_subtitle_text(
            workspace_id=workspace_id,
            task_id=task.id,
            proposal_id=proposal.id,
            kind="subtitle_cue_update",
            input_refs=[track.artifact_ref or f"subtitle-track:{track.id}"],
            content=render_srt(_execution_subtitle_track(provisional)),
            engine="subtitle-srt/v1",
            parameters={"track_id": track.id, "cue_id": cue.id},
        )
        changed_track = _clone_subtitle_track(
            track,
            timeline_version_id=result_version_id,
            cues=updated_cues,
            style=dict(updated_style),
            artifact_ref=subtitle_ref,
            transform_id=transform_id,
        )
        retained = [
            _clone_subtitle_track(item, timeline_version_id=result_version_id)
            for item in current_tracks
            if item.id != track.id and not (
                track.language == "zh-CN" and item.language == "en-US" and str(text) != expected_text
            )
        ]
        if track.source_language is None:
            montage_cue = next((item for item in montage.cues if item.id == cue.id), None)
            if montage_cue is not None:
                montage_cue.text = str(text)
        current_cover = _cover_asset_for_version(workspace_id, task.id, base_version)
        carried_cover = (
            _clone_cover_asset(current_cover, timeline_version_id=result_version_id)
            if current_cover is not None
            else None
        )
        montage, _ = _prepare_changed_montage(
            montage,
            message="字幕已修改；请重新渲染以写入成片。",
        )
        return _persist_subtitle_cover_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=base_version,
            result_version_id=result_version_id,
            operation_type="UpdateSubtitleCue",
            operation_payload=dict(operation_payload),
            subtitle_tracks=[changed_track, *retained],
            cover_asset=carried_cover,
            updated_plan=montage,
        )
    if operation_type in {"ChangePersonSegment", "RegeneratePersonRender"} and isinstance(
        operation_payload, dict
    ):
        planned_quality = production_repo.get_creation_plan(workspace_id, task.plan_id).lipsync_quality
        chosen_quality = operation_payload.get("lipsync_quality", planned_quality)
        if chosen_quality != planned_quality:
            raise HTTPException(status_code=409, detail="口型档位与当前任务方案不一致，请重新预览")
        person_provider = _lipsync_provider(chosen_quality)
        required_strings = {
            key: operation_payload.get(key)
            for key in (
                "clip_id",
                "cue_id",
                "narration_segment_id",
                "person_profile_id",
                "person_version",
                "expected_asset_id",
                "expected_segment_id",
                "motion",
                "expression",
                "base_version_id",
            )
        }
        if any(
            not isinstance(value, str) or not value
            for value in required_strings.values()
        ):
            raise HTTPException(status_code=409, detail="invalid person segment edit operation")
        if task.current_timeline_version_id != required_strings["base_version_id"]:
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        try:
            base_version = production_repo.get_timeline_version(
                workspace_id, required_strings["base_version_id"]
            )
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
            clip = next(
                item for item in montage.clips if item.id == required_strings["clip_id"]
            )
            cue = next(item for item in montage.cues if item.id == required_strings["cue_id"])
            segment = production_repo.get_narration_segment(
                workspace_id,
                task.id,
                required_strings["narration_segment_id"],
            )
        except (KeyError, StopIteration) as exc:
            raise HTTPException(
                status_code=409,
                detail="person segment edit base no longer exists",
            ) from exc
        if (
            clip.asset_id != required_strings["expected_asset_id"]
            or clip.segment_id != required_strings["expected_segment_id"]
            or clip.person_render_id != operation_payload.get("expected_person_render_id")
            or cue.narration_segment_id != required_strings["narration_segment_id"]
        ):
            raise HTTPException(status_code=409, detail="person segment edit target has changed")
        if operation_type == "RegeneratePersonRender" and clip.person_render_id is None:
            raise HTTPException(status_code=409, detail="person render no longer exists")
        if segment.audio_reference != operation_payload.get("expected_audio_reference"):
            raise HTTPException(status_code=409, detail="配音已改变，请重新预览人物口型修改。")
        try:
            source_start, source_end = _person_source_range(workspace_id, task, montage, clip, segment)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if (source_start, source_end) != (operation_payload.get("source_start_seconds"), operation_payload.get("source_end_seconds")):
            raise HTTPException(status_code=409, detail="配音时轴已改变，请重新预览人物修改。")
        reuse_person_render_id = operation_payload.get("reuse_person_render_id")
        if reuse_person_render_id is not None:
            if (
                operation_type != "ChangePersonSegment"
                or not isinstance(reuse_person_render_id, str)
                or not reuse_person_render_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="invalid person render reuse operation",
                )
            try:
                render = production_repo.get_person_render(
                    workspace_id,
                    task.id,
                    reuse_person_render_id,
                )
            except KeyError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="person render reuse target no longer exists",
                ) from exc
            if (
                render.status != "ready"
                or render.person_profile_id != required_strings["person_profile_id"]
                or render.person_version != required_strings["person_version"]
                or render.narration_segment_id
                != required_strings["narration_segment_id"]
                or render.voice_profile_id != segment.voice_profile_id
                or render.voice_version != segment.voice_version
                or render.model_id != person_provider.model_id
                or not _person_render_matches_narration(
                    render, segment, production_repo.list_transform_runs(workspace_id, task.id)
                )
                or render.motion != required_strings["motion"]
                or render.expression != required_strings["expression"]
                or _person_render_media_path(render) is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail="person render reuse target has changed",
                )
        else:
            render = _generate_creation_task_person_render_record(
                workspace_id,
                task.id,
                CreationTaskPersonRenderRequest(
                    narration_segment_id=required_strings["narration_segment_id"],
                    person_profile_id=required_strings["person_profile_id"],
                    person_version=required_strings["person_version"],
                    motion=required_strings["motion"],
                    expression=required_strings["expression"],
                    lipsync_quality=chosen_quality,
                ),
                regeneration_token=(
                    proposal.id
                    if operation_type == "RegeneratePersonRender"
                    else None
                ),
            )
        if render.status != "ready" or _person_render_media_path(render) is None:
            raise _topic_failure_response(
                status_code=503,
                error=render.failure_reason or "person_render_failed",
                reason=render.failure_reason or "人物口型片段没有生成可信输出。",
                impact=render.impact or "当前时间线和成片保持不变。",
                repair_action=render.repair_action or "检查人物口型运行环境后重试。",
                retryable=True,
            )
        if source_end > (render.duration_seconds or 0) + 1 / 30:
            raise HTTPException(status_code=409, detail="人物口型输出不足以覆盖所选配音范围。")
        clip.source_start_seconds = source_start
        clip.source_end_seconds = min(source_end, render.duration_seconds)
        clip.person_render_id = render.id
        clip.match_reasons = [
            f"人物 {render.person_profile_id} · {render.person_version}",
            (
                "口型已按当前配音分段重新生成"
                if operation_type == "RegeneratePersonRender"
                else "口型使用当前配音分段生成"
            ),
        ]
        clip.role = "a_roll"
        clip.manually_selected = True
        montage, _ = _prepare_changed_montage(
            montage,
            message=(
                "所选人物片段已重新对口型；配音、字幕和其他镜头保持不变，可以重新渲染。"
                if operation_type == "RegeneratePersonRender"
                else "人物口型片段已写入当前版本；配音、字幕和其他镜头保持不变，可以重新渲染。"
            ),
        )
        applied_payload = dict(operation_payload)
        applied_payload["person_render_id"] = render.id
        return _persist_typed_montage_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=base_version,
            updated_plan=montage,
            operation_type=operation_type,
            operation_payload=applied_payload,
        )
    if operation_type == "UpdateSubtitleCue" and isinstance(operation_payload, dict):
        cue_id = operation_payload.get("cue_id")
        text = operation_payload.get("text")
        expected_text = operation_payload.get("expected_text")
        base_version_id = operation_payload.get("base_version_id")
        if any(not isinstance(value, str) or not value for value in (cue_id, text, expected_text, base_version_id)):
            raise HTTPException(status_code=409, detail="invalid subtitle edit operation")
        if task.current_timeline_version_id != base_version_id:
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        base_version = production_repo.get_timeline_version(workspace_id, base_version_id)
        try:
            montage = creation_task_montage_plan_from_payload(
                production_repo.get_creation_task_montage_plan(workspace_id, task.id)
            )
            cue = next(item for item in montage.cues if item.id == cue_id)
        except (KeyError, StopIteration) as exc:
            raise HTTPException(status_code=409, detail="subtitle edit base no longer exists") from exc
        if cue.text != expected_text:
            raise HTTPException(status_code=409, detail="subtitle edit target has changed")
        cue.text = text
        _refresh_montage_review_state(montage)
        montage, _ = _prepare_changed_montage(
            montage, message="字幕已修改；配音和镜头保持不变，可以重新渲染。"
        )
        narration_segments = (
            production_repo.list_narration_segments(
                workspace_id, task.id, task.confirmed_script_id
            )
            if task.confirmed_script_id
            else []
        )
        return _persist_typed_montage_edit(
            workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base_version, updated_plan=montage,
            operation_type="UpdateSubtitleCue", operation_payload=dict(operation_payload),
            narration_segments=narration_segments,
        )
    if operation_type == "RegenerateNarrationSegment" and isinstance(operation_payload, dict):
        segment_id = operation_payload.get("narration_segment_id")
        expected_audio_reference = operation_payload.get("expected_audio_reference")
        delivery_instruction = operation_payload.get("delivery_instruction")
        base_version_id = operation_payload.get("base_version_id")
        if (
            not isinstance(segment_id, str) or not segment_id
            or not isinstance(base_version_id, str) or not base_version_id
            or (delivery_instruction is not None and not isinstance(delivery_instruction, str))
        ):
            raise HTTPException(status_code=409, detail="invalid narration segment edit operation")
        if task.current_timeline_version_id != base_version_id:
            raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
        current_segment = production_repo.get_narration_segment(
            workspace_id, task.id, segment_id
        )
        if current_segment.audio_reference != expected_audio_reference:
            raise HTTPException(status_code=409, detail="narration segment edit target has changed")
        base_version = production_repo.get_timeline_version(workspace_id, base_version_id)
        try:
            segments, narration_record, montage = _regenerate_creation_task_narration_segment(
                workspace_id=workspace_id, task=task, segment_id=segment_id,
                delivery_instruction=delivery_instruction,
                generation_identity=proposal.id,
            )
        except (KeyError, MediaAuthorityError, VoiceCloneError, OSError, ValueError) as exc:
            raise _topic_failure_response(
                status_code=503,
                error="narration_segment_generation_failed",
                reason="所选配音片段没有生成可用的新音频。",
                impact="当前时间线、配音和成片保持不变。",
                repair_action=str(exc),
                retryable=True,
            ) from exc
        return _persist_typed_montage_edit(
            workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base_version, updated_plan=montage,
            operation_type="RegenerateNarrationSegment",
            operation_payload=dict(operation_payload),
            narration_segments=segments, narration_artifact=narration_record,
        )
    if operation_type == "ChangeLinkedSpeed" and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="时间线已变化，请重新预览关联变速")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        service = _task_audio()
        def existing_audio(clip, rate, duration):
            ref = operation_payload["stretched_audio"].get(_stretch_key(clip, rate, duration))
            if not isinstance(ref, str):
                raise ValueError("变速预览音频已失效，请重新预览")
            service.source(workspace_id, task_id, ref)
            return ref
        try:
            updated, tracks = retime_clip(plan, tracks, _effective_audio_timeline(plan),
                operation_payload["clip_id"], operation_payload["playback_rate"], existing_audio)
            service.validate(workspace_id, task_id, updated.audio_timeline, updated.duration_seconds)
            _prepare_changed_montage(updated, message="关联变速已应用，声音、字幕与后续内容同步；请重新渲染。")
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _persist_typed_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=updated, operation_type=operation_type,
            operation_payload=operation_payload, edited_subtitle_tracks=tracks)
    if operation_type == "RemoveSilence" and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="时间线已变化，请重新预览停顿剪辑。")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        try:
            updated, tracks = remove_time_ranges(plan, tracks, _effective_audio_timeline(plan), operation_payload["removed_ranges_ms"])
            _task_audio().validate(workspace_id, task.id, updated.audio_timeline, updated.duration_seconds)
            _prepare_changed_montage(updated, message="停顿剪辑已应用；声音、字幕与画面同步缩短，请重新渲染。")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _persist_typed_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=updated, operation_type=operation_type,
            operation_payload=operation_payload, edited_subtitle_tracks=tracks)
    if operation_type in {"DeleteClip", "DuplicateClip"} and isinstance(operation_payload, dict):
        if task.current_timeline_version_id != operation_payload.get("base_version_id"):
            raise HTTPException(status_code=409, detail="时间线已变化，请重新预览关联编辑")
        base = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
        try:
            updated, tracks = splice_clip(plan, tracks, _effective_audio_timeline(plan),
                operation_payload["clip_id"], "delete" if operation_type == "DeleteClip" else "duplicate")
            _prepare_changed_montage(updated, message="镜头及关联音轨、字幕已更新，请重新渲染。")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _persist_typed_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal,
            base_version=base, updated_plan=updated, operation_type=operation_type,
            operation_payload=operation_payload, edited_subtitle_tracks=tracks)
    if operation_draft.get("operation_type") not in {"ReplaceClip", "ChangeCrop", "TrimClip", "SplitClip", "ChangeClipSpeed", "ReorderClip", "SetClipLock", "ConfirmClip"} or not isinstance(operation_payload, dict):
        raise HTTPException(status_code=409, detail="unsupported creation task edit operation")
    required_strings = {
        key: operation_payload.get(key)
        for key in (
            "clip_id",
            "expected_asset_id",
            "expected_segment_id",
            "base_version_id",
        )
    }
    if any(not isinstance(value, str) or not value for value in required_strings.values()):
        raise HTTPException(status_code=409, detail="invalid creation task edit operation")
    if task.current_timeline_version_id != required_strings["base_version_id"]:
        raise HTTPException(status_code=409, detail="creation task edit proposal is stale")
    try:
        base_version = production_repo.get_timeline_version(
            workspace_id, required_strings["base_version_id"]
        )
        plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
        current_clip = next(
            item for item in plan.clips if item.id == required_strings["clip_id"]
        )
    except (KeyError, StopIteration) as exc:
        raise HTTPException(status_code=409, detail="creation task edit base no longer exists") from exc
    if (current_clip.asset_id, current_clip.segment_id) != (
        required_strings["expected_asset_id"],
        required_strings["expected_segment_id"],
    ):
        raise HTTPException(status_code=409, detail="creation task edit target has changed")
    operation_type = operation_draft["operation_type"]
    update = CreationTaskMontageClipUpdateRequest(
        split_time_seconds=operation_payload.get("split_time_seconds") if operation_type == "SplitClip" else None,
        playback_rate=operation_payload.get("playback_rate") if operation_type == "ChangeClipSpeed" else None,
        fit_mode=operation_payload.get("fit_mode") if operation_type == "ChangeCrop" else None,
        crop_x=operation_payload.get("crop_x") if operation_type == "ChangeCrop" else None,
        crop_y=operation_payload.get("crop_y") if operation_type == "ChangeCrop" else None,
        asset_id=operation_payload.get("asset_id") if operation_type == "ReplaceClip" else None,
        segment_id=operation_payload.get("segment_id") if operation_type == "ReplaceClip" else None,
        source_start_seconds=operation_payload.get("source_start_seconds") if operation_type in {"ReplaceClip", "TrimClip"} else None,
        position=operation_payload.get("position") if operation_type == "ReorderClip" else None,
        locked=operation_payload.get("locked") if operation_type == "SetClipLock" else None,
        confirmed=True if operation_type in {"ReplaceClip", "ConfirmClip"} else None,
    )
    updated_plan = _apply_creation_task_montage_clip_update(
        workspace_id,
        task_id,
        plan,
        required_strings["clip_id"],
        update,
    )
    updated_plan, _payload = _prepare_changed_montage(
        updated_plan,
        message="镜头设置已更新；文案、配音和其他镜头保持不变，可以重新渲染。",
    )
    return _persist_typed_montage_edit(
        workspace_id=workspace_id,
        task=task,
        proposal=proposal,
        base_version=base_version,
        updated_plan=updated_plan,
        operation_type=operation_draft["operation_type"],
        operation_payload=dict(operation_payload),
    )


@app.post(
    "/api/creation-tasks/{task_id}/edits/exact/preview",
    response_model=CreationTaskEditPreviewResponse,
)
def preview_creation_task_exact_edit(
    task_id: str,
    request: Request,
    payload: CreationTaskExactEditRequest,
) -> CreationTaskEditPreviewResponse:
    if payload.operation_type == "EditGraphicOverlay":
        return _preview_graphic_overlay(task_id,request,payload)
    if payload.operation_type == "SetClipTransition":
        return _preview_transition(task_id, request, payload)
    if payload.operation_type == "ChangeCanvas":
        return _preview_canvas(task_id, request, payload)
    if payload.operation_type == "ConfigureSubtitleTrack" and payload.style is not None and payload.target_language is None:
        return _preview_subtitle_style(task_id, request, payload)
    if payload.operation_type == "ReplaceSubtitleText":
        return _preview_subtitle_replace(task_id, request, payload)
    if payload.operation_type in {"SplitSubtitleCue", "MergeSubtitleCue", "DeleteSubtitleCue"}:
        return _preview_subtitle_structure(task_id, request, payload)
    if payload.operation_type == "ChangeLinkedSpeed":
        return _preview_linked_speed(task_id, request, payload)
    if payload.operation_type in {"DeleteClip", "DuplicateClip"}:
        return _preview_linked_clip_edit(task_id, request, payload)
    if payload.operation_type in {"ReplaceClip", "ChangeCrop", "TrimClip", "SplitClip", "ChangeClipSpeed", "ReorderClip", "SetClipLock", "ConfirmClip"}:
        _task, _base, _plan, intent, proposal = _preview_exact_montage_edit(task_id, request, payload)
        return _edit_preview_response(intent, proposal)
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if payload.operation_type not in {
        "UpdateSubtitleCue",
        "ConfigureSubtitleTrack",
        "GenerateCoverFromFrame",
    }:
        raise HTTPException(
            status_code=422,
            detail="该精确操作尚未迁移到预览确认入口",
        )
    try:
        montage = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task montage plan not found") from exc
    base_version = _ensure_montage_base_version(workspace_id, task, montage)
    task = production_repo.get_creation_task(workspace_id, task_id)
    tracks = _current_subtitle_tracks(
        workspace_id, task, current_version=base_version, montage=montage
    )
    now = utc_now()

    if payload.operation_type == "ConfigureSubtitleTrack" and payload.subtitle_display is not None:
        selection = _subtitle_display_selection(tracks, payload.subtitle_display)
        if all(track.style.get("enabled", True) == selection[track.id] for track in tracks):
            raise HTTPException(status_code=409, detail="字幕显示设置没有变化")
        labels = {"none": "不显示字幕", "zh": "仅中文字幕", "en": "仅英文字幕", "zh-en": "中英双语字幕"}
        summary = f"成片改为{labels[payload.subtitle_display]}，保留字幕文字与时轴。"
        target_refs = [f"subtitle-track:{track.id}" for track in tracks]
        operation_payload = {"subtitle_display": payload.subtitle_display, "base_version_id": base_version.id}
        preview = {"before": {"ref": f"timeline:{base_version.id}", "label": "当前显示", "detail": "、".join(track.language for track in tracks if track.style.get("enabled", True)) or "无字幕"},
                   "after": {"ref": f"subtitle-display:{payload.subtitle_display}", "label": "修改后", "detail": labels[payload.subtitle_display]}}
        effects = ["只修改字幕显示；不调用模型。关闭全部字幕后仍保留可下载的字幕文件。"]
    elif payload.operation_type == "ConfigureSubtitleTrack":
        source = next((track for track in tracks if track.language == "zh-CN"), None)
        if source is None:
            raise _topic_failure_response(
                status_code=409,
                error="subtitle_source_missing",
                reason="当前时间线没有可用的中文字幕轨。",
                impact="没有调用文字模型，当前成片保持不变。",
                repair_action="先生成带中文字幕的成片，再启用双语字幕。",
                retryable=True,
            )
        if any(track.language == "en-US" and track.status == "valid" for track in tracks):
            raise _topic_failure_response(
                status_code=409,
                error="edit_no_change",
                reason="当前时间线已经启用中英双语字幕。",
                impact="没有创建新的时间线版本。",
                repair_action="可直接编辑具体字幕，或先撤销到上一版本。",
                retryable=False,
            )
        provider = subtitle_translation_provider_from_settings(settings)
        if not provider.ready:
            raise _topic_failure_response(
                status_code=409,
                error="subtitle_translation_unavailable",
                reason="文字模型翻译能力不可用。",
                impact="未生成英文字幕，中文字幕和成片保持不变。",
                repair_action=provider.message,
                retryable=True,
            )
        summary = "把当前中文字幕逐条翻译为英文，保持原时间轴，并启用中英双语字幕。"
        target_refs = [f"subtitle-track:{source.id}"]
        operation_payload = {
            "source_track_id": source.id,
            "target_language": "en-US",
            "base_version_id": base_version.id,
        }
        preview = {
            "before": {"ref": target_refs[0], "label": "中文字幕", "detail": f"{len(source.cues)} 条"},
            "after": {"ref": "subtitle-track:en-US", "label": "中英双语字幕", "detail": "逐条对齐原时间轴，确认后调用一次文字模型"},
        }
        effects = ["确认后调用一次当前已配置文字模型；不会改写字幕时间轴。"]
    elif payload.operation_type == "UpdateSubtitleCue":
        if not payload.cue_id:
            raise HTTPException(status_code=422, detail="subtitle edit requires cue_id")
        track = next(
            (
                item
                for item in tracks
                if (payload.track_id is None or item.id == payload.track_id)
                and any(cue.id == payload.cue_id for cue in item.cues)
            ),
            None,
        )
        if track is None:
            raise HTTPException(status_code=404, detail="subtitle track or cue not found")
        cue = next(cue for cue in track.cues if cue.id == payload.cue_id)
        if track.language == "en-US" and payload.style is not None and payload.style != track.style:
            raise HTTPException(status_code=422, detail="双语字幕共用样式，请在中文轨调整；英文轨支持文字、断行和强调词。")
        normalized_text, normalized_line_breaks = _normalize_subtitle_text_and_breaks(
            payload.text if payload.text is not None else cue.text,
            payload.line_breaks if payload.line_breaks is not None else (cue.line_breaks if payload.text is None or payload.text == cue.text else None),
        )
        start = payload.start_seconds if payload.start_seconds is not None else cue.start_seconds
        end = payload.end_seconds if payload.end_seconds is not None else cue.end_seconds
        candidate = replace(cue, start_seconds=start, end_seconds=end)
        try:
            subtitle_schedule([replace(track, status="valid", style={**track.style, "enabled": True},
                cues=[candidate if item.id == cue.id else item for item in track.cues])], montage.duration_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        changed = (
            start != cue.start_seconds or end != cue.end_seconds
            or
            normalized_text != cue.text
            or normalized_line_breaks != cue.line_breaks
            or payload.emphasis is not None
            or payload.style is not None
        )
        if not changed:
            raise _topic_failure_response(
                status_code=409,
                error="edit_no_change",
                reason="字幕文字、时轴和样式没有变化。",
                impact="没有创建新的时间线版本。",
                repair_action="修改文字、断行、强调词或字幕样式后再预览。",
                retryable=False,
            )
        summary = "只修改所选字幕条目；配音、镜头和其他字幕保持不变。"
        target_refs = [f"subtitle-cue:{track.id}:{cue.id}"]
        operation_payload = {
            "track_id": track.id,
            "cue_id": cue.id,
            "text": normalized_text,
            "start_seconds": start,
            "end_seconds": end,
            "line_breaks": normalized_line_breaks,
            "emphasis": payload.emphasis if payload.emphasis is not None else cue.emphasis,
            "style": payload.style if payload.style is not None else track.style,
            "expected_text": cue.text,
            "base_version_id": base_version.id,
        }
        preview = {
            "before": {"ref": target_refs[0], "label": "当前字幕", "detail": f"{cue.start_seconds:.2f}–{cue.end_seconds:.2f} 秒 · {cue.text}"},
            "after": {"ref": target_refs[0], "label": "修改后字幕", "detail": f"{start:.2f}–{end:.2f} 秒 · {normalized_text}"},
        }
        effects = ["确认后只重建字幕产物并使当前视频输出失效；不会重新生成配音或镜头。"]
        if track.language == "zh-CN" and normalized_text != cue.text and any(item.language == "en-US" for item in tracks):
            effects.append("中文内容已改变，旧英文翻译将移除；需要重新翻译后才能输出双语字幕。")
    else:
        if payload.title is None or payload.frame_time_seconds is None:
            raise HTTPException(status_code=422, detail="cover edit requires title and frame_time_seconds")
        if montage.status != "rendered" or not montage.output_path:
            raise _topic_failure_response(
                status_code=409,
                error="rendered_version_required",
                reason="当前任务没有可选帧的已渲染成片。",
                impact="没有生成封面，当前任务保持不变。",
                repair_action="先渲染当前成片，再选择画面和标题生成封面。",
                retryable=True,
            )
        summary = f"从成片 {payload.frame_time_seconds:.1f} 秒选择真实画面，并叠加标题生成封面。"
        target_refs = [montage.output_path]
        operation_payload = {
            "source_video_ref": montage.output_path,
            "aspect_ratio": montage.aspect_ratio,
            "frame_time_seconds": payload.frame_time_seconds,
            "title": payload.title,
            "base_version_id": base_version.id,
        }
        preview = {
            "before": {"ref": montage.output_path, "label": "当前成片画面", "detail": f"{payload.frame_time_seconds:.1f} 秒"},
            "after": {"ref": "cover:preview", "label": "封面标题", "detail": payload.title},
        }
        effects = ["确认后只运行一次本机 FFmpeg 与图片排版；不会调用 AI 生图模型。"]

    intent = production_repo.create_edit_intent(
        EditIntent(
            id=f"intent_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            raw_command=f"精确操作：{summary}",
            context_stage="review" if task.stage == "review" else "montage",
            selected_object_refs=target_refs,
            playhead_ms=(
                round((payload.frame_time_seconds or 0) * 1000)
                if payload.operation_type == "GenerateCoverFromFrame"
                else None
            ),
            intent_type="exact_subtitle_cover_edit",
            scope={"kind": payload.operation_type},
            constraints={"preserve_narration": True, "preserve_clips": True},
            confidence=1.0,
            status="interpreted",
            created_at=now,
        )
    )
    proposal = production_repo.create_edit_proposal(
        EditProposal(
            id=f"proposal_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            intent_id=intent.id,
            summary=summary,
            target_refs=target_refs,
            operation={"operation_type": payload.operation_type, "payload": operation_payload},
            invalidated_refs=(
                _logical_montage_output_refs(montage)
                if payload.operation_type != "GenerateCoverFromFrame"
                else []
            ),
            preview=preview,
            external_effects=effects,
            created_at=now,
            updated_at=now,
        )
    )
    return _edit_preview_response(intent, proposal)


@app.post(
    "/api/creation-tasks/{task_id}/edits/exact",
    response_model=CreationTaskEditConfirmResponse,
)
def apply_creation_task_exact_edit(
    task_id: str,
    request: Request,
    payload: CreationTaskExactEditRequest,
) -> CreationTaskEditConfirmResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if payload.operation_type in {"ConfigureSubtitleTrack", "GenerateCoverFromFrame", "SetClipTransition", "EditGraphicOverlay"} or (
        payload.operation_type == "UpdateSubtitleCue" and payload.track_id is not None
    ):
        preview = preview_creation_task_exact_edit(task_id, request, payload)
        return confirm_creation_task_edit(task_id, preview.proposal.id, request)
    if payload.operation_type in {"ChangePersonSegment", "RegeneratePersonRender"}:
        if not payload.clip_id:
            raise HTTPException(status_code=422, detail="person edit requires clip_id")
        preview = preview_creation_task_edit(
            task_id,
            request,
            CreationTaskEditPreviewRequest(
                raw_command=(
                    "这里只重做口型，其他都不要动"
                    if payload.operation_type == "RegeneratePersonRender"
                    else "这一段换成当前人物出镜并匹配口型"
                ),
                context_stage="review" if task.stage == "review" else "montage",
                selected_object_refs=[f"clip:{payload.clip_id}"],
            ),
        )
        return confirm_creation_task_edit(task_id, preview.proposal.id, request)
    if payload.operation_type == "UpdateSubtitleCue":
        if not payload.cue_id or payload.text is None:
            raise HTTPException(status_code=422, detail="subtitle edit requires cue_id and text")
        preview = preview_creation_task_edit(
            task_id,
            request,
            CreationTaskEditPreviewRequest(
                raw_command=f"把这句字幕改成：{payload.text}",
                context_stage="montage",
                selected_object_refs=[f"cue:{payload.cue_id}"],
            ),
        )
        return confirm_creation_task_edit(task_id, preview.proposal.id, request)
    if payload.operation_type == "RegenerateNarrationSegment":
        if not payload.narration_segment_id:
            raise HTTPException(status_code=422, detail="narration edit requires narration_segment_id")
        preview = preview_creation_task_edit(
            task_id,
            request,
            CreationTaskEditPreviewRequest(
                raw_command=payload.delivery_instruction or "这句重新配音",
                context_stage="narration",
                selected_object_refs=[f"narration:{payload.narration_segment_id}"],
            ),
        )
        return confirm_creation_task_edit(task_id, preview.proposal.id, request)
    task, base_version, plan, _intent, proposal = _preview_exact_montage_edit(task_id, request, payload)
    plan, _montage_payload = _prepare_changed_montage(
        plan,
        message=f"{proposal.summary} 可以重新渲染。",
    )
    return _persist_typed_montage_edit(
        workspace_id=workspace_id,
        task=task,
        proposal=proposal,
        base_version=base_version,
        updated_plan=plan,
        operation_type=payload.operation_type,
        operation_payload=dict(proposal.operation["payload"]),
    )



def _validate_montage_material_use(workspace_id, plan):
    # Validate before cache reuse as well as inside the actual renderer.
    for clip in plan.clips:
        if not clip.person_render_id:
            try:
                asset = production_repo.get_asset(workspace_id, clip.asset_id)
            except KeyError as exc:
                raise ValueError("镜头素材已不存在，请替换后重试") from exc
            if not material_allows_footage(asset):
                raise ValueError("素材使用状态不允许用于画面，请替换镜头或在素材详情修改使用状态")
    audio_service = _task_audio()
    for track in _effective_audio_timeline(plan).tracks:
        if not track.muted:
            for clip in track.clips:
                if not clip.muted:
                    audio_service.validate_material_use(workspace_id, plan.task_id, clip.source_ref)


def _publish_task_picture_preview(workspace_id,task,base,candidate,tracks,*,kind,parameters):
    from copy import deepcopy
    _validate_montage_material_use(workspace_id,candidate)
    preview_plan=deepcopy(candidate)
    preview_plan.status="ready"
    preview_plan.audio_timeline=_effective_audio_timeline(candidate)
    def produce(output):
        _render_montage_with_resolved_clip_sources(workspace_id,preview_plan,output.parent/"timeline-mix.wav",
            job_staging_path=output.parent,subtitle_tracks=tracks)
        (output.parent/"preview.mp4").replace(output)
    return _task_audio().publish(workspace_id,task.id,kind=kind,suffix=".mp4",engine="local-montage-v20",
        parameters={"base_version_id":base.id,**parameters},
        input_refs=list(dict.fromkeys([f"timeline:{base.id}", *[clip.source_path for clip in candidate.clips],
            *sorted({item.source_ref for track in preview_plan.audio_timeline.tracks for item in track.clips}),
            *[item['source_ref'] for item in candidate.graphic_overlays if item.get('kind')=='image'],
            *[track.artifact_ref for track in tracks if track.artifact_ref]])),produce=produce)


@app.post("/api/creation-tasks/{task_id}/edits/{proposal_id}/selection/preview")
def preview_compound_selection(task_id:str,proposal_id:str,request:Request,payload:CompoundEditSelectionRequest):
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    try:
        proposal=production_repo.get_edit_proposal(workspace_id,proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,detail="修改提案不存在") from exc
    if proposal.task_id!=task_id:
        raise HTTPException(status_code=404,detail="修改提案不属于当前任务")
    data=proposal.operation.get("payload",{})
    if proposal.status!="proposed" or proposal.operation.get("operation_type")!="EditCompoundTimeline" or not data.get("selection_enabled"):
        raise HTTPException(status_code=409,detail="当前提案不能重新选择，请重新预览原始要求")
    if task.current_timeline_version_id!=data.get("base_version_id"):
        raise HTTPException(status_code=409,detail="时间线已变化，请重新预览原始要求")
    if (payload.included_step_indices is None)==(payload.raw_command is None):
        raise HTTPException(status_code=422,detail="请提供保留项或调整要求，不能同时提供两种")
    try:
        included=payload.included_step_indices if payload.raw_command is None else compile_compound_selection(payload.raw_command,data["steps"])
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    intent=production_repo.get_edit_intent(workspace_id,proposal.intent_id)
    return _preview_compound_edit(task,request,CreationTaskEditPreviewRequest(raw_command=intent.raw_command,
        context_stage=intent.context_stage,selected_object_refs=intent.scope["context_refs"],playhead_ms=intent.playhead_ms),included,selection_command=payload.raw_command)


def _preview_compound_edit(task,request,payload,included_step_indices=None,selection_command=None):
    workspace_id=current_workspace_id(request)
    # Narrow candidate commands before loading the montage.
    if not is_indexed_subtitle_command(payload.raw_command) and not re.search(r'[，,；;]|同时|并且|然后',payload.raw_command):
        return None
    if not re.search(r'音乐|配音|原声|音效|字幕|英文放在中文',payload.raw_command):
        return None
    try:
        plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task.id))
        audio=_effective_audio_timeline(plan)
        compiled=compile_compound_edit(payload.raw_command,plan,audio,payload.selected_object_refs,task.current_timeline_version_id,payload.playhead_ms,included_step_indices)
        if compiled is None:
            return None
        if plan.status=="rendering":
            raise ValueError("正在渲染，请完成后重新预览")
        candidate,steps=compiled
        dissolve_frames(candidate.clips)
        for clip in candidate.clips:
            fade_filters(clip)
        _task_audio().validate(workspace_id,task.id,candidate.audio_timeline,candidate.duration_seconds)
        base=_ensure_montage_base_version(workspace_id,task,plan)
        tracks=_current_subtitle_tracks(workspace_id,task,current_version=base,montage=plan)
        changed_tracks=apply_compound_subtitles(tracks,steps,plan.duration_seconds)
        if '字幕不变' in payload.raw_command and changed_tracks!=tracks:
            raise ValueError("字幕不变与字幕修改冲突，请明确要保留的内容")
        records=[_publish_task_picture_preview(workspace_id,task,base,item,render_tracks,kind="compound_preview",parameters={"steps":steps,"side":side}) for side,item,render_tracks in (("before",plan,tracks),("after",candidate,changed_tracks))]
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else "复合修改预览失败，请检查来源后重试") from exc
    if production_repo.get_creation_task(workspace_id,task.id).current_timeline_version_id!=base.id:
        raise HTTPException(status_code=409,detail="预览期间版本已变化，请重新预览")
    effects=["中文字幕内容已改变，旧英文翻译将失效；需要重新翻译后输出双语字幕。"] if any(track.language=='en-US' and track.source_language=='zh-CN' for track in tracks) and not any(track.language=='en-US' and track.source_language=='zh-CN' for track in changed_tracks) else []
    if any(step['enabled'] and step['operation_type']=='UpdateSubtitleCue' for step in steps):
        effects.append("字幕文字和显示时间的修改不会重新生成配音。")
    refs=list(dict.fromkeys(step['target_ref'] for step in steps if step['enabled']))
    intent=production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task.id,
        raw_command=payload.raw_command,context_stage=payload.context_stage,selected_object_refs=refs,playhead_ms=payload.playhead_ms,
        intent_type="compound_edit",scope={"target_refs":refs,"context_refs":payload.selected_object_refs,"selection_command":selection_command},constraints={"preserve_video_duration":True},confidence=1,status="interpreted"))
    detail="；".join(step['command'] for step in steps if step['enabled'])
    proposal=production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task.id,intent_id=intent.id,
        summary=detail,target_refs=refs,operation={"operation_type":"EditCompoundTimeline","payload":{"base_version_id":base.id,"steps":steps,"selection_enabled":True,
            "clips":[asdict(clip) for clip in candidate.clips],"audio_timeline":candidate.audio_timeline.model_dump()}},invalidated_refs=_logical_montage_output_refs(plan),external_effects=effects,
        preview={side:{"ref":record.logical_ref,"label":"修改前" if side=="before" else detail,
            "detail":"整片视听预览；全部修改确认后写入同一个版本，可一次撤销。",
            "video_url":f"/api/creation-tasks/{task.id}/visual/artifacts/{record.id}"} for side,record in zip(("before","after"),records)}))
    return _edit_preview_response(intent,proposal)


def _preview_graphic_overlay(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    from copy import deepcopy
    if payload.model_fields_set-{"operation_type","overlay_action","graphic_overlay","overlay_id"}:
        raise HTTPException(status_code=422,detail="图文叠加层编辑不能混入其他操作")
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
    if plan.status=="rendering":
        raise HTTPException(status_code=409,detail="正在渲染，请完成后修改图文叠加层")
    edited=deepcopy(plan)
    action=payload.overlay_action
    value=payload.graphic_overlay
    if action=='add' and value is not None and payload.overlay_id is None:
        edited.graphic_overlays.append(value.model_dump())
        identity=value.id
    elif action in {'update','delete'} and payload.overlay_id:
        index=next((i for i,item in enumerate(edited.graphic_overlays) if item['id']==payload.overlay_id),-1)
        if index<0:
            raise HTTPException(status_code=404,detail="图文叠加层不存在，请刷新列表")
        identity=payload.overlay_id
        if action=='delete' and value is None:
            edited.graphic_overlays.pop(index)
        elif action=='update' and value is not None and value.id==identity:
            edited.graphic_overlays[index]=value.model_dump()
        else:
            raise HTTPException(status_code=422,detail="图文叠加层操作的对象或参数不匹配")
    else:
        raise HTTPException(status_code=422,detail="请选择添加、修改或删除图文叠加层，并提供对应参数")
    if edited.graphic_overlays==plan.graphic_overlays:
        raise HTTPException(status_code=422,detail="图文叠加层没有变化")
    base=_ensure_montage_base_version(workspace_id,task,plan)
    tracks=_current_subtitle_tracks(workspace_id,task,current_version=base,montage=plan)
    try:
        validate_overlays(edited.graphic_overlays,edited.duration_seconds)
        for item in edited.graphic_overlays:
            if item.get('kind')=='image':
                resolve_graphic_source(production_repo,artifact_store,workspace_id,task_id,item['source_ref'])
        records=[_publish_task_picture_preview(workspace_id,task,base,candidate,tracks,kind="graphic_preview",
            parameters={"graphic_overlays":candidate.graphic_overlays}) for candidate in (plan,edited)]
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422,detail=str(exc) if isinstance(exc,(ValueError,OSError)) else "图文叠加层预览失败，请检查本机渲染器") from exc
    if production_repo.get_creation_task(workspace_id,task_id).current_timeline_version_id!=base.id:
        raise HTTPException(status_code=409,detail="预览期间时间线变化，请重新预览图文叠加层")
    summary={"add":"添加图文叠加层","update":"修改图文叠加层","delete":"删除图文叠加层"}[action]
    intent=production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        raw_command=summary,context_stage="montage",selected_object_refs=[f"overlay:{identity}"],playhead_ms=None,
        intent_type="graphic_overlay",scope={"kind":"overlay","id":identity},constraints={"preserve_narration":True},confidence=1,status="interpreted"))
    proposal=production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,intent_id=intent.id,
        summary=summary,target_refs=[f"overlay:{identity}"],operation={"operation_type":"EditGraphicOverlay","payload":{
            "base_version_id":base.id,"graphic_overlays":edited.graphic_overlays}},invalidated_refs=_logical_montage_output_refs(plan),external_effects=[],
        preview={side:{"ref":record.logical_ref,"label":"修改前" if side=="before" else summary,
            "detail":"完整成片视听预览；图文叠加层在指定区间显示，字幕、声音和镜头保持不变。",
            "video_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"} for side,record in zip(("before","after"),records)}))
    return _edit_preview_response(intent,proposal)


def _preview_transition(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    from copy import deepcopy
    if not payload.clip_id or payload.transition_seconds is None or payload.model_fields_set - {"operation_type","clip_id","transition_seconds"}:
        raise HTTPException(status_code=422,detail="转场需要后一个镜头和叠化秒数，不能混入其他操作")
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
    index=next((i for i,clip in enumerate(plan.clips) if clip.id==payload.clip_id),-1)
    if index<=0:
        raise HTTPException(status_code=422,detail="请选择第二个或之后的镜头，修改它与前一个镜头之间的转场")
    targets=plan.clips[index-1:index+1]
    if plan.status=="rendering" or any(clip.locked for clip in targets):
        raise HTTPException(status_code=409,detail="正在渲染或转场镜头已锁定，请先解锁或等待完成")
    if targets[1].transition_in_seconds==payload.transition_seconds:
        raise HTTPException(status_code=422,detail="转场没有变化")
    base=_ensure_montage_base_version(workspace_id,task,plan)
    edited=deepcopy(plan)
    edited.clips[index].transition_in_seconds=payload.transition_seconds
    tracks=_current_subtitle_tracks(workspace_id,task,current_version=base,montage=plan)
    try:
        dissolve_frames(edited.clips)
        records=[_publish_task_picture_preview(workspace_id,task,base,candidate,tracks,kind="transition_preview",
            parameters={"transition_seconds":candidate.clips[index].transition_in_seconds,"clip_id":payload.clip_id}) for candidate in (plan,edited)]
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else "本地转场预览失败，请检查源素材和FFmpeg") from exc
    if production_repo.get_creation_task(workspace_id,task_id).current_timeline_version_id!=base.id:
        raise HTTPException(status_code=409,detail="预览期间时间线变化，请重新预览转场")
    refs=[f"clip:{clip.id}" for clip in targets]
    intent=production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        raw_command=f"叠化{payload.transition_seconds:g}秒",context_stage="montage",selected_object_refs=refs,
        playhead_ms=round(targets[1].timeline_start_seconds*1000),intent_type="transition",scope={"kind":"clips","clip_ids":[clip.id for clip in targets]},
        constraints={"preserve_timing":True,"preserve_audio":True},confidence=1,status="interpreted"))
    proposal=production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,intent_id=intent.id,
        summary=f"镜头 {index} → {index+1}："+(f"叠化 {payload.transition_seconds:g} 秒" if payload.transition_seconds else "硬切"),target_refs=refs,
        operation={"operation_type":"SetClipTransition","payload":{"base_version_id":base.id,"clip_id":payload.clip_id,
            "transition_seconds":payload.transition_seconds,"expected_clips":{clip.id:asdict(clip) for clip in targets}}},
        invalidated_refs=_logical_montage_output_refs(plan),external_effects=[],
        preview={side:{"ref":record.logical_ref,"label":"当前转场" if side=="before" else "修改后转场",
            "detail":f"完整成片视听预览；转场从 {targets[1].timeline_start_seconds:.2f} 秒开始，读取前镜头结尾的真实素材余量，声音与字幕时轴不变。",
            "video_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"} for side,record in zip(("before","after"),records)}))
    return _edit_preview_response(intent,proposal)


def _preview_canvas(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    if payload.aspect_ratio is None or payload.model_fields_set - {"operation_type", "aspect_ratio", "fit_mode"}:
        raise HTTPException(status_code=422, detail="画幅修改需要9:16、16:9或1:1，可同时指定填充或适应")
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    base = _ensure_montage_base_version(workspace_id, task, montage)
    if any(clip.locked for clip in montage.clips):
        raise HTTPException(status_code=422, detail="改变画幅会影响所有镜头，请先解锁镜头")
    if montage.aspect_ratio == payload.aspect_ratio and (payload.fit_mode is None or all(clip.fit_mode == payload.fit_mode for clip in montage.clips)):
        raise HTTPException(status_code=409, detail="画幅与适配方式没有变化")
    clip = montage.clips[0]
    try:
        source = production_repo.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
        records = [publish_color_preview(production_repo, artifact_store, settings.effective_temporary_root_path,
            workspace_id, task_id, target, source, _portrait_processor(workspace_id), kind="canvas_preview", aspect_ratio=ratio)
            for target,ratio in ((clip,montage.aspect_ratio),(replace(clip,fit_mode=payload.fit_mode or clip.fit_mode),payload.aspect_ratio))]
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc,ValueError) else "画幅预览失败，请检查素材与本机FFmpeg") from exc
    if production_repo.get_creation_task(workspace_id, task_id).current_timeline_version_id != base.id:
        raise HTTPException(status_code=409, detail="预览期间版本变化，请重新预览")
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        raw_command=f"画幅改为{payload.aspect_ratio}",context_stage="montage",selected_object_refs=[],playhead_ms=None,
        intent_type="canvas",scope={"kind":"timeline"},constraints={"preserve_narration":True},confidence=1,status="interpreted",created_at=now))
    cover = _cover_asset_for_version(workspace_id,task_id,base)
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,intent_id=intent.id,
        summary=f"整片改为{payload.aspect_ratio}；配音、字幕文字和时轴不变。",target_refs=[f"timeline:{base.id}"],
        operation={"operation_type":"ChangeCanvas","payload":{"base_version_id":base.id,"aspect_ratio":payload.aspect_ratio,"fit_mode":payload.fit_mode}},
        invalidated_refs=[*_logical_montage_output_refs(montage), *([cover.artifact_ref] if cover and cover.artifact_ref else [])],
        preview={side:{"ref":record.logical_ref,"label":ratio,"detail":"第一镜头最多5秒的静音画面预览；字幕将按新画幅重新排版。",
            "video_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"}
            for side,ratio,record in zip(("before","after"),(montage.aspect_ratio,payload.aspect_ratio),records)},
        external_effects=["影响所有镜头，封面需重新生成；撤销可恢复。", "适应模式完整保留画面，可能留黑边；填充模式按裁切位置裁掉超出部分。"],
        status="proposed",created_at=now,updated_at=now))
    return _edit_preview_response(intent,proposal)


def _preview_subtitle_alignment(task, request, payload, language_label):
    workspace_id = current_workspace_id(request)
    plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
    if plan.status == "rendering":
        raise HTTPException(status_code=409, detail="正在渲染，请完成后再校准字幕")
    base = _ensure_montage_base_version(workspace_id, task, plan)
    tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
    bilingual = language_label in {"双语", "中英双语", "中英"}
    language = {"中文":"zh-CN", "英文":"en-US"}.get(language_label)
    candidates = [track for track in tracks if track.status == "valid" and (language is None or track.language == language)
                  and (not bilingual or track.source_language is None)]
    if len(candidates) != 1 or candidates[0].source_language is not None:
        raise HTTPException(status_code=422, detail="请明确要校准的原语言字幕；翻译字幕不能直接与另一语言的配音对齐")
    track = candidates[0]
    if bilingual:
        try:
            synchronize_translated_subtitles(tracks, track, plan.duration_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    graph = _effective_audio_timeline(plan)
    voice_ids = {item.id for item in graph.tracks if item.role in {"narration", "original"}
                 and not item.muted and any(not clip.muted for clip in item.clips)}
    if not voice_ids:
        raise HTTPException(status_code=409, detail="当前没有可用于校准的配音或原声")
    service = _task_audio()
    try:
        analysis = service.publish(workspace_id, task.id, kind="audio_preview",
            parameters={"action":"subtitle_alignment", "base_version_id":base.id},
            input_refs=sorted({clip.source_ref for item in graph.tracks if item.id in voice_ids for clip in item.clips}),
            produce=lambda output: render_audio_mix(graph, plan.duration_seconds,
                lambda ref: service.source(workspace_id, task.id, ref), output, solo_track_ids=voice_ids))
        with TemporaryDirectory(prefix="subtitle-alignment-", dir=settings.effective_temporary_root_path) as directory:
            recognition = recognize_subtitle_timing(settings, artifact_store.resolve_file(analysis.logical_ref), track.language, directory)
        changed = align_subtitle_track(track, recognition, plan.duration_seconds)
        timings = [{"id":cue.id,"start_seconds":cue.start_seconds,"end_seconds":cue.end_seconds} for cue in changed.cues]
        changed = apply_aligned_timings(track, timings, plan.duration_seconds)
        changed_tracks = (synchronize_translated_subtitles(tracks, changed, plan.duration_seconds) if bilingual
            else [changed if item.id == track.id else item for item in tracks])
        if all(after.cues == before.cues for after, before in zip(changed_tracks, tracks, strict=True)):
            raise ValueError("识别时间与现有字幕一致，无需修改")
        records = [_publish_task_picture_preview(workspace_id, task, base, plan, items, kind="compound_preview",
            parameters={"action":"subtitle_alignment", "side":side, "audio_ref":analysis.logical_ref, "timings":timings, "include_translation":bilingual})
            for side, items in (("before", tracks), ("after", changed_tracks))]
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "本地字幕校准失败，当前版本未改变") from exc
    if production_repo.get_creation_task(workspace_id, task.id).current_timeline_version_id != base.id:
        raise HTTPException(status_code=409, detail="预览期间版本已变化，请重新校准")
    refs = [f"subtitle-track:{item.id}" for item in tracks if item.id == track.id or (bilingual and item.status == "valid" and item.source_language == track.language and item.language != track.language)]
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task.id,
        raw_command=payload.raw_command,context_stage=payload.context_stage,selected_object_refs=refs,playhead_ms=payload.playhead_ms,
        intent_type="subtitle_timing",scope={"track_id":track.id},constraints={"preserve_text":True,"preserve_audio":True},confidence=1,status="interpreted"))
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task.id,
        intent_id=intent.id,summary="按当前配音校准双语字幕；译文跟随对应原句，正文、声音和镜头保持不变。" if bilingual else "按当前配音校准字幕时间；正文、声音和镜头保持不变。",target_refs=refs,
        operation={"operation_type":"AlignSubtitleTiming","payload":{"base_version_id":base.id,"track_id":track.id,
            "timings":timings,"include_translation":bilingual,"source_audio_ref":analysis.logical_ref,"recognition_sha256":hashlib.sha256(json.dumps(recognition,sort_keys=True).encode()).hexdigest(),
            "engine":"local-whisper-cpp-token-timing"}},invalidated_refs=_logical_montage_output_refs(plan),
        external_effects=["时间来自本地语音识别，不保证逐帧精确；请试听后确认。", "两种语言的对应条目同步改时间，保留译文与语言开关，可一次撤销。" if bilingual else "确认后创建新版本，可撤销；其他语言字幕保持不变。"],
        preview={side:{"ref":record.logical_ref,"label":"当前字幕" if side=="before" else "按配音校准后",
            "detail":"整片视听预览，字幕正文保留。","video_url":f"/api/creation-tasks/{task.id}/visual/artifacts/{record.id}"}
            for side,record in zip(("before","after"),records)}))
    return _edit_preview_response(intent, proposal)


def _preview_subtitle_style(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    if payload.model_fields_set - {"operation_type", "style"}:
        raise HTTPException(status_code=422, detail="样式应用于全部字幕轨，不能同时翻译或切换显示")
    try:
        patch = validate_style_patch(payload.style)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    base = _ensure_montage_base_version(workspace_id, task, montage)
    tracks = [track for track in _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage) if track.status == "valid"]
    if not tracks:
        raise HTTPException(status_code=409, detail="当前没有可编辑字幕，请先生成字幕")
    defaults = asdict(SubtitleExecutionStyle())
    if all(all(track.style.get(key, defaults[key]) == value for key, value in patch.items()) for track in tracks):
        raise HTTPException(status_code=409, detail="字幕样式没有变化")
    refs = [track.artifact_ref or f"subtitle-track:{track.id}" for track in tracks]
    try:
        before = subtitle_schedule([replace(track, style={**track.style, "enabled":True}) for track in tracks], montage.duration_seconds)
        after = subtitle_schedule([replace(track, style={**track.style, **patch, "enabled":True}) for track in tracks], montage.duration_seconds)
        sample = max(before, key=lambda item:len(item.text))
        updated_sample = next(item for item in after if item.start_seconds <= sample.start_seconds < item.end_seconds)
        # Validate every cue's layout before accepting the style, not just the preview sample.
        from app.montage import _subtitle_font_and_lines
        for caption in after:
            _subtitle_font_and_lines(caption.text, caption.subtitle_style, size=canvas_size(montage.aspect_ratio))
        records = [publish_subtitle_style_preview(production_repo, artifact_store, settings.effective_temporary_root_path,
            workspace_id, task_id, caption, refs, size=canvas_size(montage.aspect_ratio)) for caption in (sample, updated_sample)]
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if production_repo.get_creation_task(workspace_id, task_id).current_timeline_version_id != base.id:
        raise HTTPException(status_code=409, detail="预览期间版本变化，请重新预览")
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        raw_command="调整全部字幕样式",context_stage="subtitle_cover",selected_object_refs=[f"subtitle-track:{track.id}" for track in tracks],
        playhead_ms=None,intent_type="subtitle_style",scope={"kind":"subtitle_tracks"},constraints={"preserve_narration":True},
        confidence=1,status="interpreted",created_at=now))
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task_id,
        intent_id=intent.id,summary="调整全部字幕轨的样式；文字、时间、配音和语言开关保持不变。",
        target_refs=intent.selected_object_refs,operation={"operation_type":"ConfigureSubtitleTrack", "payload":{"base_version_id":base.id,"style":patch}},
        invalidated_refs=_logical_montage_output_refs(montage),preview={side:{"ref":record.logical_ref,"label":label,
            "detail":f"使用真实字幕 {sample.start_seconds:.2f}–{sample.end_seconds:.2f} 秒；透明字幕图，不是成片画面。",
            "image_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"}
            for side,label,record in zip(("before","after"),("当前样式","修改后样式"),records)},
        external_effects=["中英文共用样式；已关闭的语言仍保持关闭。图片预览为便于比较展示全部语言。", "SRT仅保存文本与时间，颜色等样式写入视频。"],
        status="proposed",created_at=now,updated_at=now))
    return _edit_preview_response(intent, proposal)


def _apply_caption_edit(track, operation_type, payload):
    if operation_type == "ReplaceSubtitleText":
        return replace_subtitle_text(track, payload["find_text"], payload["replacement_text"])
    return restructure_subtitle(track, operation_type, payload["cue_id"],
        split_offset=payload.get("split_offset"), split_time_seconds=payload.get("split_time_seconds"))


def _preview_subtitle_structure(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    allowed = {"operation_type", "track_id", "cue_id"}
    if payload.operation_type == "SplitSubtitleCue":
        allowed |= {"split_offset", "split_time_seconds"}
    if not payload.track_id or not payload.cue_id or payload.model_fields_set - allowed:
        raise HTTPException(status_code=422, detail="请指定当前语言轨和字幕，拆分时同时指定文字位置与时间")
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    base = _ensure_montage_base_version(workspace_id, task, montage)
    tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)
    track = next((track for track in tracks if track.id == payload.track_id and track.status == "valid"), None)
    if track is None:
        raise HTTPException(status_code=409, detail="请选择可用字幕轨")
    operation = payload.model_dump(exclude_unset=True, exclude={"operation_type"})
    try:
        changed, changes = _apply_caption_edit(track, payload.operation_type, operation)
        subtitle_schedule([replace(changed, style={**changed.style, "enabled": True})], montage.duration_seconds)
        if changed.cues:
            render_srt(_execution_subtitle_track(changed))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    label = {"SplitSubtitleCue":"拆分字幕", "MergeSubtitleCue":"与下一条字幕合并", "DeleteSubtitleCue":"删除字幕"}[payload.operation_type]
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, raw_command=label, context_stage="subtitle_cover", selected_object_refs=[f"cue:{payload.cue_id}", f"subtitle-track:{track.id}"],
        playhead_ms=None, intent_type="subtitle_structure", scope={"kind":"subtitle_track", "track_id":track.id},
        constraints={"preserve_narration":True}, confidence=1, status="interpreted", created_at=now))
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, intent_id=intent.id, summary=f"{label}，仅修改当前{'中文' if track.language == 'zh-CN' else '英文'}字幕轨；配音和镜头不变。",
        target_refs=[f"subtitle-track:{track.id}",f"cue:{payload.cue_id}"], operation={"operation_type":payload.operation_type,
            "payload":{**operation, "base_version_id":base.id}},
        invalidated_refs=[*_logical_montage_output_refs(montage), *([track.artifact_ref] if track.artifact_ref else [])],
        preview={"before":{"ref":f"subtitle-track:{track.id}","label":"当前字幕", "detail":f"{len(track.cues)} 条"},
            "after":{"ref":f"subtitle-track:{track.id}","label":label, "detail":f"{len(changed.cues)} 条"}, "text_changes":changes},
        external_effects=["其他语言轨保持不变。", {
            "SplitSubtitleCue":"文字拆分点和显示时间按指定值执行，不代表语音自动对齐；跨拆分点的强调词会移除。",
            "MergeSubtitleCue":"合并后的字幕覆盖两条之间的空白时段。",
            "DeleteSubtitleCue":"删除会留下无字幕时段；删除本轨最后一条后可通过撤销恢复。",
        }[payload.operation_type]],
        status="proposed", created_at=now, updated_at=now))
    return _edit_preview_response(intent, proposal)


def _preview_subtitle_replace(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    if not payload.track_id or payload.find_text is None or payload.replacement_text is None or payload.model_fields_set - {"operation_type", "track_id", "find_text", "replacement_text"}:
        raise HTTPException(status_code=422, detail="全文替换需要指定字幕轨、查找文字和替换文字")
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    base = _ensure_montage_base_version(workspace_id, task, montage)
    tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=montage)
    track = next((track for track in tracks if track.id == payload.track_id and track.status == "valid"), None)
    if track is None:
        raise HTTPException(status_code=409, detail="请选择可用字幕轨")
    try:
        changed, changes = replace_subtitle_text(track, payload.find_text, payload.replacement_text)
        render_srt(_execution_subtitle_track(changed))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    translations = [other for other in tracks if track.source_language is None and other.source_language == track.language]
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, raw_command=f"全文把“{payload.find_text}”替换为“{payload.replacement_text}”", context_stage="subtitle_cover",
        selected_object_refs=[f"subtitle-track:{track.id}"], playhead_ms=None, intent_type="subtitle_text_replace",
        scope={"kind":"subtitle_track", "track_id":track.id}, constraints={"preserve_narration":True, "preserve_timing":True},
        confidence=1, status="interpreted", created_at=now))
    count = sum(change["occurrences"] for change in changes)
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, intent_id=intent.id, summary=f"替换当前{track.language}字幕轨的 {len(changes)} 条字幕，共 {count} 处；配音和时间不变。",
        target_refs=[f"subtitle-track:{track.id}"], operation={"operation_type":"ReplaceSubtitleText", "payload":{
            "base_version_id":base.id, "track_id":track.id, "find_text":payload.find_text, "replacement_text":payload.replacement_text}},
        invalidated_refs=[*_logical_montage_output_refs(montage), *(item.artifact_ref for item in [track,*translations] if item.artifact_ref)],
        preview={"before":{"ref":f"subtitle-track:{track.id}","label":"查找文字", "detail":payload.find_text},
            "after":{"ref":f"subtitle-track:{track.id}","label":"替换文字", "detail":payload.replacement_text or "删除命中文字"},
            "text_changes":changes},
        external_effects=(["当前原文的译文将失效，需重新生成；旧译文可通过撤销恢复。"] if translations else []) +
            ["按文字精确匹配，不使用正则表达式；不改文案和配音。替换词内部的手动断行会移除。"],
        status="proposed", created_at=now, updated_at=now))
    return _edit_preview_response(intent, proposal)


def _stretch_key(clip, rate, duration):
    return hashlib.sha256(json.dumps([clip.source_ref, clip.source_start_ms, clip.source_end_ms,
        rate, duration]).encode()).hexdigest()


def _preview_linked_speed(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    if not payload.clip_id or payload.playback_rate is None or payload.model_fields_set - {"operation_type", "clip_id", "playback_rate"}:
        raise HTTPException(status_code=422, detail="关联变速需要镜头和倍速，不混入其他操作")
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    base = _ensure_montage_base_version(workspace_id, task, plan)
    tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
    service = _task_audio()
    stretched = {}
    def stretch(clip, rate, duration):
        key = _stretch_key(clip, rate, duration)
        if key not in stretched:
            source = service.source(workspace_id, task_id, clip.source_ref)
            record = service.publish(workspace_id, task_id, kind="audio_source",
                parameters={"action":"pitch_preserving_tempo", "rate":rate, "source_start_ms":clip.source_start_ms,
                    "source_end_ms":clip.source_end_ms, "duration_ms":duration}, input_refs=[clip.source_ref],
                produce=lambda output: render_stretched_audio(source, clip.source_start_ms, clip.source_end_ms, rate, duration, output))
            stretched[key] = record.logical_ref
        return stretched[key]
    try:
        edited, _ = retime_clip(plan, tracks, _effective_audio_timeline(plan), payload.clip_id, payload.playback_rate, stretch)
        service.validate(workspace_id, task_id, edited.audio_timeline, edited.duration_seconds)
        previews = {}
        for side, candidate in (("before", plan), ("after", edited)):
            clip = next(clip for clip in candidate.clips if clip.id == payload.clip_id)
            if clip.person_render_id:
                person = production_repo.get_person_render(workspace_id, task_id, clip.person_render_id)
                source = _person_render_media_path(person)
                if source is None or clip.source_end_seconds > (person.duration_seconds or 0) + .001:
                    raise ValueError("人物口型源片段不可用，请先重新生成")
            else:
                source = production_repo.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
            video = publish_color_preview(production_repo, artifact_store, settings.effective_temporary_root_path,
                workspace_id, task_id, clip, source, _portrait_processor(workspace_id), kind="speed_preview", aspect_ratio=plan.aspect_ratio)
            timeline = _effective_audio_timeline(candidate)
            audio = service.publish(workspace_id, task_id, kind="audio_preview",
                parameters={"action":"linked_speed_preview", "side":side, "timeline":timeline.model_dump(),
                    "start_seconds":clip.timeline_start_seconds, "end_seconds":clip.timeline_end_seconds},
                input_refs=sorted({item.source_ref for track in timeline.tracks for item in track.clips}),
                produce=lambda output: render_audio_mix(timeline, candidate.duration_seconds,
                    lambda ref: service.source(workspace_id, task_id, ref), output,
                    start_seconds=clip.timeline_start_seconds, end_seconds=clip.timeline_end_seconds))
            previews[side] = {"ref": video.logical_ref, "label":"修改前" if side == "before" else "关联变速后",
                "detail":f"所选段 {clip.timeline_end_seconds-clip.timeline_start_seconds:.3f} 秒 · 整片 {candidate.duration_seconds:.3f} 秒；画面预览最多5秒，音频为整段混音。",
                "video_url":f"/api/creation-tasks/{task_id}/visual/artifacts/{video.id}",
                "audio_url":f"/api/creation-tasks/{task_id}/audio/artifacts/{audio.id}"}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "关联变速预览失败，请检查本地素材和FFmpeg") from exc
    if production_repo.get_creation_task(workspace_id, task_id).current_timeline_version_id != base.id:
        raise HTTPException(status_code=409, detail="预览期间时间线已变化，请重新预览")
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, raw_command=f"关联变速 {payload.playback_rate} 倍", context_stage="montage",
        selected_object_refs=[f"clip:{payload.clip_id}"], playhead_ms=None, intent_type="linked_timeline_edit",
        scope={"kind":"clip", "clip_id":payload.clip_id}, constraints={"linked_tracks":True}, confidence=1,
        status="interpreted", created_at=now))
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}", workspace_id=workspace_id,
        task_id=task_id, intent_id=intent.id, summary=f"所选镜头连同声音、字幕按当前速度再变速 {payload.playback_rate} 倍，后续内容同步移动。",
        target_refs=[f"clip:{payload.clip_id}"], operation={"operation_type":"ChangeLinkedSpeed", "payload":{
            "base_version_id":base.id, "clip_id":payload.clip_id, "playback_rate":payload.playback_rate, "stretched_audio":stretched}},
        invalidated_refs=[*_logical_montage_output_refs(plan), *(track.artifact_ref for track in tracks if track.artifact_ref)],
        preview=previews, external_effects=["本地保持音高变速；请试听音色与节奏。确认后才改变时间线，可撤销。"],
        status="proposed", created_at=now, updated_at=now))
    return _edit_preview_response(intent, proposal)


def _preview_remove_silence(task, request, payload):
    from app.silence_edits import detect_silence_cuts
    workspace_id = current_workspace_id(request)
    if len(payload.selected_object_refs) != 1 or not payload.selected_object_refs[0].startswith("clip:"):
        raise HTTPException(status_code=422, detail="请先选择要剪掉停顿的镜头。")
    plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
    clip = next((c for c in plan.clips if f"clip:{c.id}" == payload.selected_object_refs[0]), None)
    if clip is None:
        raise HTTPException(status_code=404, detail="所选镜头不存在。")
    if clip.locked:
        raise HTTPException(status_code=409, detail="所选镜头已锁定，请先解锁。")
    base = _ensure_montage_base_version(workspace_id, task, plan)
    tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
    graph = _effective_audio_timeline(plan)
    voice_ids = {t.id for t in graph.tracks if t.role in {"narration", "original"} and not t.muted and any(not c.muted for c in t.clips)}
    if not voice_ids:
        raise HTTPException(status_code=409, detail="所选任务没有可检测的配音或原声，请先加入讲话音轨。")
    service = _task_audio()
    try:
        analysis = service.publish(workspace_id, task.id, kind="audio_preview",
            parameters={"action":"silence_analysis", "clip_id":clip.id},
            input_refs=sorted({c.source_ref for t in graph.tracks if t.id in voice_ids for c in t.clips}),
            produce=lambda output: render_audio_mix(graph, plan.duration_seconds,
                lambda ref: service.source(workspace_id, task.id, ref), output,
                start_seconds=clip.timeline_start_seconds, end_seconds=clip.timeline_end_seconds, solo_track_ids=voice_ids))
        cuts = detect_silence_cuts(artifact_store.resolve_file(analysis.logical_ref),
            clip.timeline_end_seconds-clip.timeline_start_seconds, clip.timeline_start_seconds)
        edited, changed_tracks = remove_time_ranges(plan, tracks, graph, cuts)
        service.validate(workspace_id, task.id, edited.audio_timeline, edited.duration_seconds)
        for c in edited.clips:
            fade_filters(c)
        previews = {}
        for side, candidate, captions in (("before",plan,tracks),("after",edited,changed_tracks)):
            record = _publish_task_picture_preview(workspace_id,task,base,candidate,captions,
                kind="silence_preview",parameters={"side":side,"removed_ranges_ms":cuts})
            previews[side] = {"ref":record.logical_ref,"label":"修改前" if side=="before" else "缩短停顿后",
                "detail":f"{candidate.duration_seconds:.3f} 秒", "video_url":f"/api/creation-tasks/{task.id}/visual/artifacts/{record.id}"}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc,ValueError) else "本地停顿预览失败，请检查音轨、素材和外接盘。") from exc
    if production_repo.get_creation_task(workspace_id,task.id).current_timeline_version_id != base.id:
        raise HTTPException(status_code=409,detail="预览期间时间线已变化，请重新预览。")
    removed = sum(b-a for a,b in cuts)/1000
    detail = "、".join(f"{a/1000:.3f}–{b/1000:.3f}秒" for a,b in cuts)
    intent = production_repo.create_edit_intent(EditIntent(id=f"intent_{uuid4().hex}",workspace_id=workspace_id,task_id=task.id,
        raw_command=payload.raw_command,context_stage=payload.context_stage,selected_object_refs=payload.selected_object_refs,
        playhead_ms=payload.playhead_ms,intent_type="remove_silence",scope={"clip_id":clip.id},
        constraints={"linked_tracks":True},confidence=1,status="interpreted"))
    proposal = production_repo.create_edit_proposal(EditProposal(id=f"proposal_{uuid4().hex}",workspace_id=workspace_id,task_id=task.id,
        intent_id=intent.id,summary=f"缩短 {len(cuts)} 处静音候选，共 {removed:.3f} 秒；画面、声音、字幕与叠加同步前移。",
        target_refs=payload.selected_object_refs,operation={"operation_type":"RemoveSilence","payload":{
            "base_version_id":base.id,"clip_id":clip.id,"removed_ranges_ms":cuts,"analysis_ref":analysis.logical_ref}},
        invalidated_refs=_logical_montage_output_refs(plan),preview=previews,
        external_effects=[f"删除范围：{detail}","按低于-40dB且持续0.45秒检测，两端至少保留0.12秒。低声讲话也可能被识别为静音，请试听后确认；不改变保留部分的语速。" ]))
    return _edit_preview_response(intent,proposal)


def _preview_linked_clip_edit(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    if not payload.clip_id or payload.model_fields_set - {"operation_type", "clip_id"}:
        raise HTTPException(status_code=422, detail="关联删除或复制只接受一个镜头，不混入其他修改")
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
    base = _ensure_montage_base_version(workspace_id, task, plan)
    tracks = _current_subtitle_tracks(workspace_id, task, current_version=base, montage=plan)
    action = "delete" if payload.operation_type == "DeleteClip" else "duplicate"
    try:
        edited, _ = splice_clip(plan, tracks, _effective_audio_timeline(plan), payload.clip_id, action)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    clip = next(clip for clip in plan.clips if clip.id == payload.clip_id)
    label = "删除" if action == "delete" else "复制"
    direction = "前移" if action == "delete" else "后移"
    duration = clip.timeline_end_seconds - clip.timeline_start_seconds
    summary = f"{label}当前镜头及同时段配音、音乐、音效和字幕，后续内容{direction} {duration:.3f} 秒。"
    now = utc_now()
    intent = production_repo.create_edit_intent(EditIntent(
        id=f"intent_{uuid4().hex}", workspace_id=workspace_id, task_id=task_id,
        raw_command=f"{label}当前镜头", context_stage="montage", selected_object_refs=[f"clip:{clip.id}"],
        playhead_ms=round(clip.timeline_start_seconds * 1000), intent_type="linked_timeline_edit",
        scope={"kind": "time_range", "start_seconds": clip.timeline_start_seconds, "end_seconds": clip.timeline_end_seconds},
        constraints={"linked_tracks": True}, confidence=1.0, status="interpreted", created_at=now))
    proposal = production_repo.create_edit_proposal(EditProposal(
        id=f"proposal_{uuid4().hex}", workspace_id=workspace_id, task_id=task_id, intent_id=intent.id,
        summary=summary, target_refs=[f"clip:{clip.id}"],
        operation={"operation_type": payload.operation_type, "payload": {"clip_id": clip.id, "base_version_id": base.id}},
        invalidated_refs=[*_logical_montage_output_refs(plan), *(t.artifact_ref for t in tracks if t.artifact_ref)],
        preview={"before": {"ref": f"timeline:{base.id}", "label": "当前时间线", "detail": f"{len(plan.clips)} 个镜头 · {plan.duration_seconds:.3f} 秒"},
                 "after": {"ref": f"clip:{clip.id}", "label": "关联编辑后", "detail": f"{len(edited.clips)} 个镜头 · {edited.duration_seconds:.3f} 秒；{summary}"}},
        external_effects=["原始素材和声音文件保留；确认后可撤销，需要重新渲染。"],
        status="proposed", created_at=now, updated_at=now))
    return CreationTaskEditPreviewResponse.model_validate({"intent": asdict(intent), "proposal": asdict(proposal)})


def _trim_clip_source_url(workspace_id: str, task_id: str, clip: MontageClip, cue: NarrationCue) -> str:
    """Return an authorized source interval; browser playback does no media work."""
    if clip.person_render_id:
        render = production_repo.get_person_render(workspace_id, task_id, clip.person_render_id)
        if render.narration_segment_id != cue.narration_segment_id:
            raise ValueError("人物口型镜头与当前配音段落不匹配")
        synthetic_source = clip.asset_id == f"person:{render.id}" and clip.source_path == f"person-render://{render.id}"
        legacy_source = clip.source_path == render.visual_reference and not clip.asset_id.startswith("person:")
        if legacy_source:
            production_repo.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
        if not (synthetic_source or legacy_source):
            raise ValueError("人物口型镜头来源不匹配")
        path = _person_render_media_path(render)
        if path is None or clip.source_end_seconds > (render.duration_seconds or 0) + .001:
            raise ValueError("人物口型源片段不可用")
        route = f"/api/creation-tasks/{quote(task_id, safe='')}/person-renders/{quote(render.id, safe='')}/media"
    else:
        path = production_repo.resolve_clip_media_reference(workspace_id, clip.asset_id, clip.source_path)
        path.relative_to(Path(settings.video_inbox_path).expanduser().resolve())
        route = f"/api/production/materials/{quote(clip.asset_id, safe='')}/media"
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("镜头源片段不可用")
    return f"{route}#t={clip.source_start_seconds:.3f},{clip.source_end_seconds:.3f}"


def _preview_exact_montage_edit(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not payload.clip_id:
        raise HTTPException(status_code=422, detail="visual edit requires clip_id")
    try:
        persisted_plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
        before_clip = next(item for item in persisted_plan.clips if item.id == payload.clip_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="creation task montage clip not found") from exc
    if payload.operation_type == "ReplaceClip" and (
        payload.asset_id == before_clip.asset_id
        and payload.segment_id == before_clip.segment_id
        and payload.source_start_seconds in {None, before_clip.source_start_seconds}
    ):
        raise _topic_failure_response(
            status_code=409, error="edit_no_change",
            reason="这个候选就是当前镜头。", impact="原镜头和成片保持不变。",
            repair_action="选择另一个片段，或调整裁剪起点。", retryable=True,
        )
    invalidated_refs = _logical_montage_output_refs(persisted_plan)
    if payload.operation_type == "TrimClip" and task.current_timeline_version_id is None:
        raise _topic_failure_response(
            status_code=409,
            error="timeline_version_required",
            reason="当前故事板还没有可撤销的初始版本。",
            impact="裁剪预览和有效时间线均未改变。",
            repair_action="点击“保留当前故事板并建立版本”，再预览裁剪。",
            retryable=True,
        )
    base_version = _ensure_montage_base_version(workspace_id, task, persisted_plan)
    task = production_repo.get_creation_task(workspace_id, task_id)
    plan = creation_task_montage_plan_from_payload(
        creation_task_montage_plan_to_payload(persisted_plan)
    )
    supplied = {
        "fit_mode": payload.fit_mode,
        "crop_x": payload.crop_x,
        "crop_y": payload.crop_y,
        "asset_id": payload.asset_id,
        "segment_id": payload.segment_id,
        "source_start_seconds": payload.source_start_seconds,
        "split_time_seconds": payload.split_time_seconds,
        "playback_rate": payload.playback_rate,
        "position": payload.position,
        "locked": payload.locked,
    }
    required_by_type = {
        "ChangeCrop": {"fit_mode", "crop_x", "crop_y"},
        "ReplaceClip": {"asset_id", "segment_id"},
        "TrimClip": {"source_start_seconds"},
        "SplitClip": {"split_time_seconds"},
        "ChangeClipSpeed": {"playback_rate"},
        "ReorderClip": {"position"},
        "SetClipLock": {"locked"},
        "ConfirmClip": set(),
    }
    required = required_by_type[payload.operation_type]
    if any(supplied[key] is None for key in required):
        raise HTTPException(status_code=422, detail="exact edit is missing required values")
    permitted = required | (
        {"source_start_seconds"} if payload.operation_type == "ReplaceClip" else set()
    )
    if any(value is not None and key not in permitted for key, value in supplied.items()):
        raise HTTPException(status_code=422, detail="exact edit mixes multiple operations")
    update = CreationTaskMontageClipUpdateRequest(
        split_time_seconds=payload.split_time_seconds if payload.operation_type == "SplitClip" else None,
        playback_rate=payload.playback_rate if payload.operation_type == "ChangeClipSpeed" else None,
        fit_mode=payload.fit_mode if payload.operation_type == "ChangeCrop" else None,
        crop_x=payload.crop_x if payload.operation_type == "ChangeCrop" else None,
        crop_y=payload.crop_y if payload.operation_type == "ChangeCrop" else None,
        asset_id=payload.asset_id if payload.operation_type == "ReplaceClip" else None,
        segment_id=payload.segment_id if payload.operation_type == "ReplaceClip" else None,
        source_start_seconds=(
            payload.source_start_seconds
            if payload.operation_type in {"ReplaceClip", "TrimClip"}
            else None
        ),
        position=payload.position if payload.operation_type == "ReorderClip" else None,
        locked=payload.locked if payload.operation_type == "SetClipLock" else None,
        confirmed=True if payload.operation_type == "ConfirmClip" else None,
    )
    plan = _apply_creation_task_montage_clip_update(
        workspace_id, task_id, plan, payload.clip_id, update
    )
    after_clip = next(item for item in plan.clips if item.id == payload.clip_id)
    if creation_task_montage_plan_to_payload(plan)["clips"] == creation_task_montage_plan_to_payload(persisted_plan)["clips"]:
        raise _topic_failure_response(
            status_code=409,
            error="edit_no_change",
            reason="这项设置与当前镜头一致。",
            impact="没有创建新的时间线版本。",
            repair_action="调整镜头、裁剪位置、顺序或锁定状态后再试。",
            retryable=True,
        )
    labels = {
        "ChangeCrop": "调整画面适配",
        "ReplaceClip": "替换镜头",
        "TrimClip": "调整镜头起点",
        "SplitClip": "分割镜头，保留配音与字幕时轴",
        "ChangeClipSpeed": "改变画面速度，保留成片时长、声音和字幕",
        "ReorderClip": "调整镜头顺序",
        "SetClipLock": "更改镜头锁定状态",
        "ConfirmClip": "确认当前镜头",
    }
    operation_payload: dict[str, object] = {
        "clip_id": payload.clip_id,
        "base_version_id": base_version.id,
        "expected_asset_id": before_clip.asset_id,
        "expected_segment_id": before_clip.segment_id,
    }
    for key, value in supplied.items():
        if value is not None:
            operation_payload[key] = value
    if payload.operation_type == "ReplaceClip":
        operation_payload["source_start_seconds"] = after_clip.source_start_seconds
    if payload.operation_type == "ChangeCrop":
        operation_payload.update(asset_id=before_clip.asset_id, segment_id=before_clip.segment_id,
                                 source_start_seconds=before_clip.source_start_seconds)
    def describe_clip(clip, clips):
        if payload.operation_type == "ChangeClipSpeed":
            return f"{clip_playback_rate(clip):.2f} 倍速 · 源素材 {clip.source_start_seconds:.3f}–{clip.source_end_seconds:.3f} 秒"
        if payload.operation_type == "SplitClip":
            count = len(clips) - len(persisted_plan.clips) + 1
            return f"{count} 个片段 · 成片 {before_clip.timeline_start_seconds:.3f}–{before_clip.timeline_end_seconds:.3f} 秒" + (
                f" · 分割点 {payload.split_time_seconds:.3f} 秒" if count == 2 else "")
        if payload.operation_type == "ReorderClip":
            return f"第 {clips.index(clip) + 1} 个镜头"
        if payload.operation_type == "SetClipLock":
            return "已锁定" if clip.locked else "未锁定"
        if payload.operation_type == "ConfirmClip":
            return "待确认" if clip.needs_confirmation else "已确认"
        if payload.operation_type == "ChangeCrop":
            framing = "完整显示" if clip.fit_mode == "fit" else "填满画面"
            return f"{framing} · 水平 {clip.crop_x:.0%} · 垂直 {clip.crop_y:.0%}"
        return f"{clip.source_start_seconds:.1f}–{clip.source_end_seconds:.1f} 秒"

    media_previews = {}
    if payload.operation_type == "ChangeClipSpeed":
        try:
            for side, target in (("before", before_clip), ("after", after_clip)):
                if target.person_render_id:
                    render = production_repo.get_person_render(workspace_id, task_id, target.person_render_id)
                    source = _person_render_media_path(render)
                    if source is None:
                        raise ValueError("人物口型片段不可用，请重新生成")
                else:
                    source = production_repo.resolve_clip_media_reference(workspace_id, target.asset_id, target.source_path)
                record = publish_color_preview(production_repo, artifact_store, settings.effective_temporary_root_path,
                    workspace_id, task_id, target, source, _portrait_processor(workspace_id), kind="speed_preview", aspect_ratio=plan.aspect_ratio)
                media_previews[side] = {"ref": record.logical_ref,
                    "video_url": f"/api/creation-tasks/{task_id}/visual/artifacts/{record.id}"}
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError)
                else "变速预览失败，请检查源素材、FFmpeg和外接盘") from exc
        if production_repo.get_creation_task(workspace_id, task_id).current_timeline_version_id != base_version.id:
            raise HTTPException(status_code=409, detail="预览期间时间线已变化，请重新预览")
    if payload.operation_type == "TrimClip":
        try:
            for side, target in (("before", before_clip), ("after", after_clip)):
                cue = next(cue for cue in plan.cues if cue.id == target.cue_id)
                media_previews[side] = {"video_url": _trim_clip_source_url(workspace_id, task_id, target, cue)}
        except (KeyError, StopIteration, ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail="裁剪源片段不可播放，请检查素材或人物口型产物") from exc
        if production_repo.get_creation_task(workspace_id, task_id).current_timeline_version_id != base_version.id:
            raise HTTPException(status_code=409, detail="预览期间时间线已变化，请重新预览")
    before_label = "人物口播" if before_clip.person_render_id else production_repo.get_asset(workspace_id, before_clip.asset_id).file_name
    after_label = "人物口播" if after_clip.person_render_id else production_repo.get_asset(workspace_id, after_clip.asset_id).file_name
    now = utc_now()
    intent = production_repo.create_edit_intent(
        EditIntent(
            id=f"intent_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            raw_command=f"精确操作：{labels[payload.operation_type]}",
            context_stage="montage",
            selected_object_refs=[f"clip:{payload.clip_id}"],
            playhead_ms=round(after_clip.timeline_start_seconds * 1000),
            intent_type="exact_montage_edit",
            scope={"kind": "clip", "clip_id": payload.clip_id},
            constraints={"preserve_narration": True},
            confidence=1.0,
            status="interpreted",
            created_at=now,
        )
    )
    proposal = production_repo.create_edit_proposal(
        EditProposal(
            id=f"proposal_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            intent_id=intent.id,
            summary=f"{labels[payload.operation_type]}；文案、配音和其他镜头保持不变。",
            target_refs=[f"clip:{payload.clip_id}"],
            operation={
                "operation_type": payload.operation_type,
                "payload": operation_payload,
            },
            invalidated_refs=invalidated_refs,
            preview={
                "before": {
                    "ref": f"clip:{before_clip.id}",
                    "label": before_label,
                    "detail": describe_clip(before_clip, persisted_plan.clips),
                    **media_previews.get("before", {}),
                },
                "after": {
                    "ref": f"clip:{after_clip.id}",
                    "label": after_label,
                    "detail": describe_clip(after_clip, plan.clips),
                    **media_previews.get("after", {}),
                },
            },
            external_effects=(["这里播放的是原视频画面和原声；配音、字幕和画面效果以最终成片为准。当前任务版本未改变。"]
                if payload.operation_type == "TrimClip" else
                ["已在本地生成开头最多5秒的静音画面对比；声音、字幕和任务版本尚未改变。"]
                if media_previews else ["只更新任务版本；重新渲染前不会执行媒体进程。"]),
            created_at=now,
            updated_at=now,
        )
    )

    return task, base_version, plan, intent, proposal

@app.post(
    "/api/creation-tasks/{task_id}/timeline/undo",
    response_model=CreationTaskEditConfirmResponse,
)
def undo_creation_task_timeline(
    task_id: str,
    request: Request,
    _payload: CreationTaskTimelineUndoRequest,
) -> CreationTaskEditConfirmResponse:
    return _restore_creation_task_timeline(task_id, request, redo=False)


@app.post("/api/creation-tasks/{task_id}/timeline/redo", response_model=CreationTaskEditConfirmResponse)
def redo_creation_task_timeline(task_id: str, request: Request, _payload: CreationTaskTimelineUndoRequest) -> CreationTaskEditConfirmResponse:
    return _restore_creation_task_timeline(task_id, request, redo=True)


def _restore_creation_task_timeline(task_id: str, request: Request, *, redo: bool) -> CreationTaskEditConfirmResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    changed_version, target_version = (_redo_target_versions if redo else _undo_target_versions)(workspace_id, task)
    operation_type = "RedoTimelineVersion" if redo else "UndoTimelineVersion"
    action_label = "重做" if redo else "撤销"
    if changed_version is None or target_version is None:
        raise _topic_failure_response(
            status_code=409,
            error="timeline_redo_unavailable" if redo else "timeline_undo_unavailable",
            reason=f"当前没有可{action_label}的内容修改。",
            impact="任务和时间线保持不变。",
            repair_action="先撤销一次修改，再使用重做。" if redo else "先完成一次镜头、配音或字幕修改，再使用撤销。",
            retryable=False,
        )
    try:
        changed_operation = production_repo.get_edit_operation(
            workspace_id, changed_version.created_by_operation_id or ""
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="timeline undo operation is unavailable") from exc
    subtitle_cover_undo = redo or changed_operation.operation_type in {
        "RedoTimelineVersion",
        "ConfigureSubtitleTrack",
        "GenerateCoverFromFrame",
        "UpdateSubtitleCue",
        "ReplaceSubtitleText",
        "SplitSubtitleCue",
        "MergeSubtitleCue",
        "DeleteSubtitleCue",
        "ChangeCanvas",
        "SetClipTransition",
        "EditGraphicOverlay",
        "EditCompoundTimeline",
        "RemoveSilence",
        "AlignSubtitleTiming",
    }
    montage_snapshot = target_version.snapshot.get("montage")
    if not isinstance(montage_snapshot, dict):
        raise HTTPException(status_code=409, detail="timeline undo snapshot is unavailable")
    try:
        current_plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
        restored_plan = creation_task_montage_plan_from_payload(montage_snapshot)
        restored_segments = _narration_segments_from_snapshot(target_version.snapshot)
        if restored_segments is not None:
            restored_at = utc_now()
            restored_segments = [replace(segment, updated_at=restored_at) for segment in restored_segments]
        restored_narration = None
        restored_cache_key = target_version.snapshot.get("narration_cache_key")
        if isinstance(restored_cache_key, str) and restored_cache_key:
            restored_narration = production_repo.get_narration_artifact(
                workspace_id, restored_cache_key
            )
            if restored_narration is None:
                raise ValueError("时间线引用的配音总轨已丢失")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="timeline undo snapshot is invalid") from exc
    current_version = production_repo.get_timeline_version(
        workspace_id, task.current_timeline_version_id or ""
    )
    restored_tracks: list[SubtitleTrack] = []
    restored_cover: CoverAsset | None = None
    result_version_id: str | None = None
    if subtitle_cover_undo:
        result_version_id = f"timeline_{uuid4().hex}"
        target_tracks = _current_subtitle_tracks(
            workspace_id,
            task,
            current_version=target_version,
            montage=restored_plan,
        )
        restored_tracks = [
            _clone_subtitle_track(track, timeline_version_id=result_version_id)
            for track in target_tracks
        ]
        target_cover = _cover_asset_for_version(workspace_id, task.id, target_version)
        restored_cover = (
            _clone_cover_asset(target_cover, timeline_version_id=result_version_id)
            if target_cover is not None
            else None
        )
        current_tracks = _current_subtitle_tracks(
            workspace_id,
            task,
            current_version=current_version,
            montage=current_plan,
        )
        current_cover = _cover_asset_for_version(workspace_id, task.id, current_version)
        restored_refs = {
            *_logical_montage_output_refs(restored_plan),
            *(track.artifact_ref for track in target_tracks if track.artifact_ref),
            *([target_cover.artifact_ref] if target_cover and target_cover.artifact_ref else []),
        }
        current_refs = [
            *_logical_montage_output_refs(current_plan),
            *(track.artifact_ref for track in current_tracks if track.artifact_ref),
            *([current_cover.artifact_ref] if current_cover and current_cover.artifact_ref else []),
        ]
        invalidated_refs = [reference for reference in current_refs if reference not in restored_refs]
    else:
        invalidated_refs = _logical_montage_output_refs(current_plan)
        restored_plan, _montage_payload = _prepare_changed_montage(
            restored_plan,
            message="已撤销上一次镜头修改，可以重新渲染。",
        )
    now = utc_now()
    intent = production_repo.create_edit_intent(
        EditIntent(
            id=f"intent_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            raw_command=f"{action_label}上一次内容修改",
            context_stage="subtitle_cover" if subtitle_cover_undo else "montage",
            selected_object_refs=[],
            playhead_ms=None,
            intent_type="redo_timeline" if redo else "undo_timeline",
            scope={"kind": "timeline", "version_id": changed_version.id},
            constraints={},
            confidence=1.0,
            status="interpreted",
            created_at=now,
        )
    )
    proposal = production_repo.create_edit_proposal(
        EditProposal(
            id=f"proposal_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task_id,
            intent_id=intent.id,
            summary="重新应用刚才撤销的修改。" if redo else "恢复到上一次内容修改之前的版本。",
            target_refs=[f"timeline:{changed_version.id}"],
            operation={
                "operation_type": operation_type,
                "payload": {
                    "changed_version_id": changed_version.id,
                    "restore_version_id": target_version.id,
                    "base_version_id": task.current_timeline_version_id,
                },
            },
            invalidated_refs=invalidated_refs,
            preview={
                "before": {
                    "ref": f"timeline:{task.current_timeline_version_id}",
                    "label": "当前版本",
                    "detail": f"版本 {production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id or '').sequence}",
                },
                "after": {
                    "ref": f"timeline:{target_version.id}",
                    "label": "恢复的任务状态",
                    "detail": f"来源版本 {target_version.sequence}",
                },
            },
            external_effects=[f"{action_label}会创建新的历史版本，不会删除原版本。"],
            created_at=now,
            updated_at=now,
        )
    )
    if subtitle_cover_undo and result_version_id is not None:
        return _persist_subtitle_cover_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=current_version,
            result_version_id=result_version_id,
            operation_type=operation_type,
            operation_payload=dict(proposal.operation["payload"]),
            subtitle_tracks=restored_tracks,
            cover_asset=restored_cover,
            updated_plan=restored_plan,
            snapshot_source=target_version.snapshot,
            narration_segments=restored_segments,
            narration_artifact=restored_narration,
        )
    return _persist_typed_montage_edit(
        workspace_id=workspace_id,
        task=task,
        proposal=proposal,
        base_version=current_version,
        updated_plan=restored_plan,
        operation_type=operation_type,
        operation_payload=dict(proposal.operation["payload"]),
        narration_segments=restored_segments,
        narration_artifact=restored_narration,
        subtitle_source_version=target_version,
    )


def _apply_creation_task_montage_clip_update(
    workspace_id: str,
    task_id: str,
    plan: CreationTaskMontagePlan,
    clip_id: str,
    payload: CreationTaskMontageClipUpdateRequest,
) -> CreationTaskMontagePlan:
    try:
        clip = next(item for item in plan.clips if item.id == clip_id)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="creation task montage clip not found") from exc
    if plan.status == "rendering":
        raise HTTPException(status_code=409, detail="rendering montage cannot be edited")
    if payload.split_time_seconds is not None:
        if clip.locked:
            raise HTTPException(status_code=409, detail="请先解锁镜头，再分割。")
        at = round(payload.split_time_seconds, 3)
        if min(at - clip.timeline_start_seconds, clip.timeline_end_seconds - at) < 0.04:
            raise HTTPException(status_code=422, detail="分割点须在镜头内，两侧至少保留0.04秒。")
        effects = VisualEffects.model_validate(clip.visual_effects)
        source_at = round(clip.source_start_seconds + (at - clip.timeline_start_seconds) * clip_playback_rate(clip), 3)
        second = replace(clip, id=f"clip_{uuid4().hex[:12]}", source_start_seconds=source_at,
            timeline_start_seconds=at, transition_in_seconds=0)
        if effects.fade_in_seconds or effects.fade_out_seconds:
            envelope=clip.fade_envelope_duration_frames
            if envelope is None:
                envelope=round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30)
            second.fade_envelope_duration_frames=clip.fade_envelope_duration_frames=envelope
            second.fade_envelope_offset_frames=clip.fade_envelope_offset_frames+(round(at*30)-round(clip.timeline_start_seconds*30))*clip.fade_envelope_rate
        clip.source_end_seconds = source_at
        clip.timeline_end_seconds = at
        plan.clips.insert(plan.clips.index(clip) + 1, second)
        _refresh_montage_review_state(plan)
        return plan
    if clip.locked and any(value is not None for value in (
        payload.fit_mode, payload.crop_x, payload.crop_y, payload.asset_id,
        payload.segment_id, payload.source_start_seconds, payload.position, payload.playback_rate,
    )):
        raise HTTPException(status_code=409, detail="请先解锁镜头，再修改素材、裁剪或位置。")
    if payload.position is not None:
        proposed_order = list(plan.clips)
        proposed_order.remove(clip)
        proposed_order.insert(min(payload.position, len(proposed_order)), clip)
        cursor = 0.0
        for item in proposed_order:
            duration = item.timeline_end_seconds - item.timeline_start_seconds
            if item.locked and abs(round(cursor, 3) - item.timeline_start_seconds) > .001:
                raise HTTPException(status_code=409, detail="本次排序会移动已锁定镜头，请先解锁受影响的镜头。")
            cursor += duration
    replacing_material = payload.asset_id is not None or payload.segment_id is not None
    if clip.person_render_id and not replacing_material:
        render = production_repo.get_person_render(workspace_id, task_id, clip.person_render_id)
        if render.status != "ready" or render.duration_seconds is None:
            raise HTTPException(status_code=409, detail="人物口型片段不可用，请重新生成")
        source_min, source_max = 0.0, render.duration_seconds
        asset = segment = None
    else:
        asset_id = payload.asset_id or clip.asset_id
        segment_id = payload.segment_id or clip.segment_id
        try:
            asset = production_repo.get_asset(workspace_id, asset_id)
            segment = next(item for item in asset.segments if item.id == segment_id)
        except (KeyError, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="material segment not found") from exc
        if (
            asset.review_status != "approved"
            or not material_allows_footage(asset)
            or segment.review_status != "approved"
            or effective_segment_usage_role(asset, segment) not in {"a_roll", "b_roll"}
        ):
            raise HTTPException(status_code=409, detail="material segment has not passed review")
        source_min, source_max = segment.start_seconds, segment.end_seconds
    slot_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
    source_start = (
        payload.source_start_seconds
        if payload.source_start_seconds is not None
        else max(source_min, clip.source_start_seconds) if not replacing_material else source_min
    )
    rate = payload.playback_rate if payload.playback_rate is not None else clip_playback_rate(clip)
    if abs(rate - 1) > .001 and (clip.person_render_id or (asset is not None and effective_segment_usage_role(asset, segment) == "a_roll")):
        raise HTTPException(status_code=409, detail="人物口型镜头不能只改变画面速度；请调整配音后重新对口型。")
    source_end = source_start + slot_duration * rate
    if source_start < source_min - 1e-6 or source_end > source_max + 1e-6:
        raise HTTPException(status_code=400, detail=f"当前镜头需保留 {slot_duration:.1f} 秒，裁剪起点须在 {source_min:.1f}–{max(source_min, source_max - slot_duration * rate):.1f} 秒之间。")
    if payload.asset_id is not None or payload.segment_id is not None:
        assert asset is not None and segment is not None
        clip.asset_id = asset.id
        clip.segment_id = segment.id
        clip.source_path = production_repo.workspace_media_reference_for_asset(
            workspace_id, asset.id
        )
        clip.role = (
            "a_roll"
            if effective_segment_usage_role(asset, segment) == "a_roll"
            else "b_roll"
        )
        clip.match_reasons = ["由用户替换", "当前工作区可用素材"]
        clip.match_score = 1.0
        clip.manually_selected = True
        clip.person_render_id = None
    clip.source_start_seconds = round(source_start, 3)
    clip.source_end_seconds = round(source_end, 3)
    if payload.locked is not None:
        clip.locked = payload.locked
    if payload.fit_mode is not None:
        clip.fit_mode = payload.fit_mode
    if payload.crop_x is not None:
        clip.crop_x = payload.crop_x
    if payload.crop_y is not None:
        clip.crop_y = payload.crop_y
    if payload.confirmed is True or payload.asset_id is not None or payload.segment_id is not None:
        clip.needs_confirmation = False
    if payload.position is not None:
        current_index = plan.clips.index(clip)
        target_index = min(payload.position, len(plan.clips) - 1)
        if current_index != target_index:
            plan.clips.pop(current_index)
            plan.clips.insert(target_index, clip)
            timeline_cursor = 0.0
            for item in plan.clips:
                duration = item.timeline_end_seconds - item.timeline_start_seconds
                item.timeline_start_seconds = round(timeline_cursor, 3)
                item.timeline_end_seconds = round(timeline_cursor + duration, 3)
                timeline_cursor += duration
    _refresh_montage_review_state(plan)
    return plan


@app.patch(
    "/api/creation-tasks/{task_id}/montage/clips/{clip_id}",
    response_model=CreationTaskMontagePlanResponse,
)
def update_creation_task_montage_clip(
    task_id: str,
    clip_id: str,
    request: Request,
    payload: CreationTaskMontageClipUpdateRequest,
) -> CreationTaskMontagePlanResponse:
    if any(value is not None for value in (payload.fit_mode, payload.crop_x, payload.crop_y, payload.split_time_seconds, payload.playback_rate)):
        raise HTTPException(status_code=409, detail="请通过精确编辑预览确认修改。")
    categories = [
        name
        for name, active in (
            ("ReplaceClip", payload.asset_id is not None or payload.segment_id is not None),
            ("TrimClip", payload.source_start_seconds is not None and payload.asset_id is None and payload.segment_id is None),
            ("ReorderClip", payload.position is not None),
            ("SetClipLock", payload.locked is not None),
            ("ConfirmClip", payload.confirmed is True),
        )
        if active
    ]
    if len(categories) != 1:
        raise HTTPException(
            status_code=409,
            detail="legacy montage PATCH accepts one typed edit at a time",
        )
    result = apply_creation_task_exact_edit(
        task_id,
        request,
        CreationTaskExactEditRequest(
            operation_type=categories[0],
            clip_id=clip_id,
            asset_id=payload.asset_id,
            segment_id=payload.segment_id,
            source_start_seconds=payload.source_start_seconds,
            position=payload.position,
            locked=payload.locked,
        ),
    )
    return result.montage


@app.patch(
    "/api/creation-tasks/{task_id}/montage/cues/{cue_id}",
    response_model=CreationTaskMontagePlanResponse,
)
def update_creation_task_subtitle_cue(
    task_id: str,
    cue_id: str,
    request: Request,
    payload: CreationTaskSubtitleCueUpdateRequest,
) -> CreationTaskMontagePlanResponse:
    result = apply_creation_task_exact_edit(
        task_id,
        request,
        CreationTaskExactEditRequest(
            operation_type="UpdateSubtitleCue",
            cue_id=cue_id,
            text=payload.text,
        ),
    )
    return result.montage


@app.post(
    "/api/creation-tasks/{task_id}/montage/render",
    response_model=CreationTaskMontagePlanResponse,
)
def render_creation_task_montage(
    task_id: str,
    request: Request,
    _payload: CreationTaskMontageRenderRequest,
) -> CreationTaskMontagePlanResponse:
    return _render_creation_task_montage_for_workspace(current_workspace_id(request), task_id)


def _render_creation_task_montage_for_workspace(
    workspace_id: str, task_id: str, *, render_context: CpuRenderGrantContext | None = None,
) -> CreationTaskMontagePlanResponse:
    task = _creation_task_or_404(workspace_id, task_id)
    try:
        plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task montage plan not found") from exc
    if any(clip.needs_confirmation for clip in plan.clips) or plan.status not in {"ready", "rendering", "rendered", "failed"}:
        raise HTTPException(status_code=409, detail="仍有镜头等待确认，请先检查并确认这些镜头。")
    if not plan.narration_audio_path:
        raise HTTPException(status_code=409, detail="creation task narration missing")
    if render_context is not None and any(
        clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure")
        for clip in plan.clips
    ):
        raise HTTPException(status_code=409, detail="本机人像效果尚无受许可执行路径，请先移除该效果")
    try:
        _validate_montage_material_use(workspace_id,plan)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    active_subtitle_tracks = _current_subtitle_tracks(workspace_id, task, montage=plan)
    active_version = (
        production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        if task.current_timeline_version_id
        else None
    )
    explicit_subtitles = active_version is not None and "subtitle_track_ids" in active_version.snapshot
    subtitles_visible = (any(track.status == "valid" and track.style.get("enabled", True) for track in active_subtitle_tracks)
        if active_subtitle_tracks or explicit_subtitles else bool(plan.cues))
    active_cover = (
        _cover_asset_for_version(workspace_id, task.id, active_version)
        if active_version is not None
        else None
    )
    plan_input = creation_task_montage_plan_to_payload(plan)
    for transient_key in (
        "status",
        "message",
        "output_path",
        "subtitle_path",
        "updated_at",
    ):
        plan_input.pop(transient_key, None)
    plan_input["render_contract_version"] = CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION
    plan_input["subtitles_visible"] = subtitles_visible
    if any(clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure") for clip in plan.clips):
        try:
            plan_input["portrait_runtime_fingerprint"]=_portrait_processor(workspace_id).fingerprint()
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
    plan_input["subtitle_tracks"] = [
        {
            "language": track.language,
            "style": track.style,
            "cues": [asdict(cue) for cue in track.cues],
        }
        for track in active_subtitle_tracks
        if track.status == "valid"
    ]
    input_refs = list(
        dict.fromkeys(
            [
                f"montage-plan://{task.id}/{plan.id}",
                plan.narration_audio_path,
                *(clip.source_path for clip in plan.clips),
                *((clip.source_ref for track in plan.audio_timeline.tracks for clip in track.clips) if plan.audio_timeline else ()),
                *(
                    f"person-render://{clip.person_render_id}"
                    for clip in plan.clips
                    if clip.person_render_id
                ),
            ]
        )
    )
    input_digest = hashlib.sha256(
        json.dumps(plan_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if (render_context is not None and active_version is not None
            and active_version.snapshot.get("creation_render_request_id") == render_context.grant.request_id):
        # The published version owns output subtitle tracks. They were not an
        # input to this render, so bind recovery to the input captured before
        # publication instead of hashing those newly generated tracks.
        snapshot = active_version.snapshot
        original_tracks = snapshot.get("render_input_subtitle_tracks")
        original_visible = snapshot.get("render_input_subtitles_visible")
        original_digest = snapshot.get("render_input_digest")
        if not isinstance(original_tracks, list) or not isinstance(original_visible, bool):
            raise ValueError("成片恢复缺少原始字幕输入")
        plan_input["subtitle_tracks"] = original_tracks
        plan_input["subtitles_visible"] = original_visible
        input_digest = hashlib.sha256(
            json.dumps(plan_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        transform_id = snapshot.get("transform_run_id")
        if not isinstance(transform_id, str) or not transform_id.startswith("transform_job_"):
            raise ValueError("成片恢复缺少内层渲染记录")
        inner_id = transform_id.removeprefix("transform_")
        inner_job = production_repo.get_job_run(workspace_id, inner_id)
        transform = production_repo.get_transform_run(workspace_id, transform_id)
        output_refs = [plan.output_path, plan.subtitle_path]
        if (input_digest != original_digest or inner_job.task_id != task.id
                or inner_job.kind != "creation_task_montage_render"
                or inner_job.status != "succeeded" or inner_job.input_digest != input_digest
                or inner_job.output_refs != output_refs
                or transform.task_id != task.id or transform.job_id != inner_id
                or transform.status != "succeeded" or transform.input_digest != input_digest
                or transform.output_refs != output_refs
                or plan.status != "rendered" or not all(isinstance(ref, str) and ref for ref in output_refs)
                or snapshot.get("output_refs") != output_refs):
            raise ValueError("成片恢复输入或已发布产物不匹配")
        render_context.scheduler.assert_grant(render_context.grant)
        subtitle_record = production_repo.get_artifact_record(workspace_id, f"artifact_{inner_id}_subtitles")
        published_subtitle = artifact_store.describe(plan.subtitle_path)
        if (subtitle_record.producer_job_id != inner_id or subtitle_record.logical_ref != plan.subtitle_path
                or subtitle_record.sha256 != published_subtitle.sha256
                or subtitle_record.size_bytes != published_subtitle.size_bytes):
            raise ValueError("成片恢复字幕产物校验失败")
        return _creation_task_montage_response(plan)
    base_idempotency_key = f"montage-render:{task.id}:{input_digest}"
    matching_jobs = [
        existing
        for existing in production_repo.list_job_runs(workspace_id, task.id)
        if existing.kind == "creation_task_montage_render"
        and existing.input_digest == input_digest
    ]
    latest_matching_job = matching_jobs[-1] if matching_jobs else None
    if latest_matching_job is not None and latest_matching_job.status in {
        "queued",
        "running",
    }:
        raise HTTPException(status_code=409, detail="montage render is already running")
    if (
        plan.status == "rendering"
        and (latest_matching_job is None or latest_matching_job.status != "interrupted")
    ):
        raise HTTPException(status_code=409, detail="montage render is not resumable")
    if latest_matching_job is not None and latest_matching_job.status == "succeeded":
        reusable_records = [
            record
            for record in production_repo.list_artifact_records(workspace_id, task.id)
            if record.producer_job_id == latest_matching_job.id
            and record.logical_ref in latest_matching_job.output_refs
        ]
        reusable_by_kind = {record.kind: record for record in reusable_records}
        reusable_video = reusable_by_kind.get("rendered_video")
        reusable_subtitles = reusable_by_kind.get("subtitle_track")
        if reusable_video is not None and reusable_subtitles is not None:
            try:
                artifact_store.describe(reusable_video.logical_ref)
                artifact_store.describe(reusable_subtitles.logical_ref)
            except FileNotFoundError:
                pass
            else:
                plan.status = "rendered"
                plan.output_path = reusable_video.logical_ref
                plan.subtitle_path = reusable_subtitles.logical_ref
                plan.message = "已复用输入一致且通过校验的成片，等待检查。"
                rendered_payload = creation_task_montage_plan_to_payload(plan)
                production_repo.save_creation_task_montage_plan(
                    workspace_id, task.id, plan.id, rendered_payload
                )
                versions = production_repo.list_timeline_versions(workspace_id, task.id)
                current_task = production_repo.get_creation_task(workspace_id, task.id)
                version_id = f"timeline_{uuid4().hex}"
                inherited_cover = (
                    _clone_cover_asset(active_cover, timeline_version_id=version_id)
                    if active_cover is not None else None
                )
                version = production_repo.create_timeline_version(
                    TimelineVersion(
                        id=version_id,
                        workspace_id=workspace_id,
                        task_id=task.id,
                        parent_version_id=current_task.current_timeline_version_id,
                        sequence=(versions[-1].sequence + 1) if versions else 1,
                        snapshot={
                            "montage": rendered_payload,
                            **_current_narration_snapshot(workspace_id, current_task),
                        **({"subtitle_track_ids": []} if explicit_subtitles and not active_subtitle_tracks else {}),
                            "output_refs": [
                                reusable_video.logical_ref,
                                reusable_subtitles.logical_ref,
                            ],
                            "reused_job_id": latest_matching_job.id,
                            **({"cover_asset_id": inherited_cover.id} if inherited_cover else {}),
                        },
                        created_by_operation_id=None,
                    )
                )
                if inherited_cover is not None:
                    production_repo.create_cover_asset(inherited_cover)
                if not production_repo.list_subtitle_tracks(
                    workspace_id, task.id, timeline_version_id=version.id
                ):
                    if active_subtitle_tracks:
                        for active_track in active_subtitle_tracks:
                            production_repo.create_subtitle_track(
                                _clone_subtitle_track(
                                    active_track,
                                    timeline_version_id=version.id,
                                    artifact_ref=(
                                        reusable_subtitles.logical_ref
                                        if active_track.source_language is None
                                        else active_track.artifact_ref
                                    ),
                                    transform_id=(
                                        f"transform_{latest_matching_job.id}"
                                        if active_track.source_language is None
                                        else active_track.transform_id
                                    ),
                                )
                            )
                    elif not explicit_subtitles:
                        initial_track = _subtitle_track_from_montage(
                            workspace_id, task, version, plan
                        )
                        if initial_track is not None:
                            production_repo.create_subtitle_track(initial_track)
                production_repo.append_task_event(
                    TaskEvent(
                        id=f"event_{version.id}_render_reused",
                        workspace_id=workspace_id,
                        task_id=task.id,
                        job_id=latest_matching_job.id,
                        event_type="render_reused",
                        payload={
                            "timeline_version_id": version.id,
                            "output_refs": [
                                reusable_video.logical_ref,
                                reusable_subtitles.logical_ref,
                            ],
                        },
                    )
                )
                return _creation_task_montage_response(plan)
    idempotency_key = (
        latest_matching_job.idempotency_key
        if latest_matching_job is not None
        and latest_matching_job.status == "interrupted"
        else f"{base_idempotency_key}:retry:{len(matching_jobs)}"
        if matching_jobs
        else base_idempotency_key
    )
    job = JobRun(
        id=f"job_{uuid4().hex}",
        workspace_id=workspace_id,
        task_id=task.id,
        kind="creation_task_montage_render",
        idempotency_key=idempotency_key,
        input_digest=input_digest,
    )

    def render_job(context) -> JobResult:
        running_job = production_repo.get_job_run(
            context.workspace_id, context.job_id
        )
        try:
            video_ref = f"artifacts/{task.id}/{context.job_id}/preview.mp4"
            subtitle_ref = f"artifacts/{task.id}/{context.job_id}/subtitles.srt"
            try:
                published_video = artifact_store.describe(video_ref)
                published_subtitles = artifact_store.describe(subtitle_ref)
                rendered = plan
                rendered.status = "rendered"
            except FileNotFoundError:
                narration_path = production_repo.resolve_authorized_narration_audio_reference(
                    workspace_id, plan.narration_audio_path or ""
                )
                rendering_payload = creation_task_montage_plan_to_payload(plan)
                rendering_payload["status"] = "rendering"
                production_repo.save_creation_task_montage_plan(
                    workspace_id, task.id, plan.id, rendering_payload
                )
                plan.status = "ready"
                staging_root = Path(settings.video_output_path).expanduser() / ".job-staging"
                staging_root.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(prefix=f"{context.job_id}-", dir=staging_root) as staging:
                    rendering_plan = creation_task_montage_plan_from_payload(
                        creation_task_montage_plan_to_payload(plan)
                    )
                    rendered_variant = _render_montage_with_resolved_clip_sources(
                        workspace_id,
                        rendering_plan,
                        narration_path,
                        job_staging_path=Path(staging),
                        subtitle_tracks=active_subtitle_tracks if active_subtitle_tracks or explicit_subtitles else None,
                        **({"render_context": render_context} if render_context is not None else {}),
                    )
                    rendered = plan
                    rendered.status = rendered_variant.status
                    staged_video = Path(staging) / "preview.mp4"
                    staged_subtitles = Path(staging) / "subtitles.srt"

                    def publish_or_reuse(logical_ref: str, staged_path: Path):
                        try:
                            return artifact_store.describe(logical_ref)
                        except FileNotFoundError:
                            return artifact_store.publish_file(logical_ref, staged_path)

                    published_video = publish_or_reuse(video_ref, staged_video)
                    published_subtitles = publish_or_reuse(
                        subtitle_ref, staged_subtitles
                    )

            for artifact_id, kind, published in (
                (f"artifact_{context.job_id}_video", "rendered_video", published_video),
                (
                    f"artifact_{context.job_id}_subtitles",
                    "subtitle_track",
                    published_subtitles,
                ),
            ):
                try:
                    production_repo.get_artifact_record(workspace_id, artifact_id)
                except KeyError:
                    production_repo.create_artifact_record(
                        ArtifactRecord(
                            id=artifact_id,
                            workspace_id=workspace_id,
                            task_id=task.id,
                            kind=kind,
                            logical_ref=published.logical_ref,
                            sha256=published.sha256,
                            size_bytes=published.size_bytes,
                            producer_job_id=context.job_id,
                            input_refs=input_refs,
                        )
                    )

            output_refs = [video_ref, subtitle_ref]
            active_transform_id = f"transform_{context.job_id}"
            try:
                transform = production_repo.get_transform_run(
                    workspace_id, active_transform_id
                )
            except KeyError:
                transform = production_repo.create_transform_run(
                    TransformRun(
                        id=active_transform_id,
                        workspace_id=workspace_id,
                        task_id=task.id,
                        job_id=context.job_id,
                        transform_type="montage_render",
                        input_digest=input_digest,
                        input_refs=input_refs,
                        output_refs=output_refs,
                        engine="ffmpeg+ffprobe",
                        parameters={
                            "aspect_ratio": plan.aspect_ratio,
                            "subtitle_burned_in": subtitles_visible,
                            "render_contract_version": CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION,
                        },
                        status="succeeded",
                        started_at=running_job.started_at or utc_now(),
                        finished_at=utc_now(),
                    )
                )
            for upstream_ref in input_refs:
                for downstream_ref in output_refs:
                    edge_digest = hashlib.sha256(
                        f"{upstream_ref}\n{downstream_ref}".encode("utf-8")
                    ).hexdigest()[:24]
                    edge_id = f"edge_{context.job_id}_{edge_digest}"
                    try:
                        production_repo.get_dependency_edge(workspace_id, edge_id)
                    except KeyError:
                        production_repo.create_dependency_edge(
                            DependencyEdge(
                                id=edge_id,
                                workspace_id=workspace_id,
                                task_id=task.id,
                                upstream_ref=upstream_ref,
                                downstream_ref=downstream_ref,
                                created_by_transform_id=transform.id,
                            )
                        )

            rendered.output_path = video_ref
            rendered.subtitle_path = subtitle_ref
            rendered.message = "带字幕的成片已生成，等待检查。"
            rendered_payload = creation_task_montage_plan_to_payload(rendered)
            production_repo.save_creation_task_montage_plan(
                workspace_id, task.id, rendered.id, rendered_payload
            )
            version_id = f"timeline_{context.job_id}"
            try:
                version = production_repo.get_timeline_version(
                    workspace_id, version_id
                )
            except KeyError:
                versions = production_repo.list_timeline_versions(workspace_id, task.id)
                current_task = production_repo.get_creation_task(workspace_id, task.id)
                parent_version_id = current_task.current_timeline_version_id
                if parent_version_id is None and versions:
                    parent_version_id = versions[-1].id
                version = TimelineVersion(
                    id=version_id,
                    workspace_id=workspace_id,
                    task_id=task.id,
                    parent_version_id=parent_version_id,
                    sequence=(versions[-1].sequence + 1) if versions else 1,
                    snapshot={
                        "montage": rendered_payload,
                        **_current_narration_snapshot(workspace_id, current_task),
                        **({"subtitle_track_ids": []} if explicit_subtitles and not active_subtitle_tracks else {}),
                        "output_refs": output_refs,
                        "transform_run_id": transform.id,
                        **({
                            "creation_render_request_id": render_context.grant.request_id,
                            "render_input_digest": input_digest,
                            "render_input_subtitle_tracks": plan_input["subtitle_tracks"],
                            "render_input_subtitles_visible": subtitles_visible,
                        } if render_context is not None else {}),
                        **({"cover_asset_id": f"cover_{version_id}"} if active_cover else {}),
                    },
                    created_by_operation_id=None,
                )
            event_id = f"event_{context.job_id}_render_succeeded"
            try:
                production_repo.get_task_event(workspace_id, event_id)
            except KeyError:
                production_repo.append_task_event(
                    TaskEvent(
                        id=event_id,
                        workspace_id=workspace_id,
                        task_id=task.id,
                        job_id=context.job_id,
                        event_type="render_succeeded",
                        payload={
                            "transform_run_id": transform.id,
                            "timeline_version_id": version.id,
                            "output_refs": output_refs,
                        },
                    )
                )
            production_repo.create_timeline_version(version)
            # The selected cover is an immutable image with its own source reference;
            # rendering a video must not silently discard or retarget that selection.
            if active_cover is not None and not production_repo.list_cover_assets(
                workspace_id, task.id, timeline_version_id=version.id
            ):
                production_repo.create_cover_asset(
                    replace(
                        _clone_cover_asset(active_cover, timeline_version_id=version.id),
                        id=version.snapshot["cover_asset_id"],
                    )
                )
            persisted_tracks = production_repo.list_subtitle_tracks(
                workspace_id, task.id, timeline_version_id=version.id
            )
            if not persisted_tracks:
                if active_subtitle_tracks:
                    for active_track in active_subtitle_tracks:
                        production_repo.create_subtitle_track(
                            _clone_subtitle_track(
                                active_track,
                                timeline_version_id=version.id,
                                artifact_ref=(
                                    subtitle_ref
                                    if active_track.source_language is None
                                    else active_track.artifact_ref
                                ),
                                transform_id=(
                                    transform.id
                                    if active_track.source_language is None
                                    else active_track.transform_id
                                ),
                            )
                        )
                elif not explicit_subtitles:
                    initial_track = _subtitle_track_from_montage(
                        workspace_id, task, version, rendered
                    )
                    if initial_track is not None:
                        production_repo.create_subtitle_track(initial_track)
            context.report_progress(100)
            return JobResult(tuple(output_refs))
        except JobFailure:
            raise
        except MediaAuthorityError as exc:
            raise JobFailure(exc.reason, exc.impact, exc.repair_action) from exc
        except (OSError, ValueError) as exc:
            raise JobFailure(
                str(exc) or type(exc).__name__,
                "当前任务仍没有可播放的可信成片。",
                "检查素材、旁白和 FFmpeg 后重新渲染。",
            ) from exc

    result = video_job_runner.run(job, render_job)
    if result.status != "succeeded":
        transform_id = f"transform_{result.id}"
        try:
            production_repo.get_transform_run(workspace_id, transform_id)
        except KeyError:
            production_repo.create_transform_run(
                TransformRun(
                    id=transform_id,
                    workspace_id=workspace_id,
                    task_id=task.id,
                    job_id=result.id,
                    transform_type="montage_render",
                    input_digest=input_digest,
                    input_refs=input_refs,
                    output_refs=[],
                    engine="ffmpeg+ffprobe",
                    parameters={"aspect_ratio": plan.aspect_ratio, "subtitle_burned_in": subtitles_visible},
                    status="failed",
                    started_at=result.started_at or result.created_at,
                    finished_at=result.finished_at or utc_now(),
                    failure_reason=result.failure_reason,
                    remediation=result.repair_action,
                )
            )
        plan.status = "failed"
        plan.output_path = None
        plan.subtitle_path = None
        plan.message = f"成片渲染失败：{result.failure_reason or '未知错误'}"
        production_repo.save_creation_task_montage_plan(
            workspace_id,
            task.id,
            plan.id,
            creation_task_montage_plan_to_payload(plan),
        )
        raise _topic_failure_response(
            status_code=500,
            error="montage_render_failed",
            reason=result.failure_reason or "FFmpeg 没有生成通过校验的成片。",
            impact=result.impact or "当前任务仍没有可播放 MP4。",
            repair_action=result.repair_action or "检查输入后重新渲染。",
            retryable=result.status in {"failed", "interrupted"},
        )

    rendered = creation_task_montage_plan_from_payload(
        production_repo.get_creation_task_montage_plan(workspace_id, task.id)
    )
    _record_montage_usage(workspace_id, rendered)
    return _creation_task_montage_response(rendered)


def _creation_task_output_path(
    workspace_id: str,
    task_id: str,
    *,
    subtitle: bool,
) -> Path:
    task = _creation_task_or_404(workspace_id, task_id)
    try:
        plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task.id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task output not found") from exc
    raw_path = plan.subtitle_path if subtitle else plan.output_path
    if plan.status != "rendered" or not raw_path:
        raise HTTPException(status_code=404, detail="creation task output not found")
    expected_kind = "subtitle_track" if subtitle else "rendered_video"
    registered = next(
        (
            artifact
            for artifact in production_repo.list_artifact_records(workspace_id, task.id)
            if artifact.logical_ref == raw_path and artifact.kind == expected_kind
        ),
        None,
    )
    if registered is None:
        raise HTTPException(
            status_code=409, detail="creation task output is not a registered artifact"
        )
    try:
        path = artifact_store.resolve_file(raw_path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="creation task output not found")
    return path


@app.get("/api/creation-tasks/{task_id}/output")
def get_creation_task_output(task_id: str, request: Request) -> FileResponse:
    path = _creation_task_output_path(
        current_workspace_id(request), task_id, subtitle=False
    )
    return FileResponse(path, media_type="video/mp4", filename=f"{task_id}.mp4")


@app.get("/api/creation-tasks/{task_id}/output/subtitles")
def get_creation_task_subtitles(task_id: str, request: Request) -> FileResponse:
    path = _creation_task_output_path(
        current_workspace_id(request), task_id, subtitle=True
    )
    return FileResponse(path, media_type="application/x-subrip", filename=f"{task_id}.srt")


@app.get("/api/creation-tasks/{task_id}/covers/{cover_id}/image")
def get_creation_task_cover_image(
    task_id: str, cover_id: str, request: Request
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    try:
        cover = production_repo.get_cover_asset(workspace_id, task_id, cover_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task cover not found") from exc
    if cover.status != "valid" or not cover.artifact_ref:
        raise HTTPException(status_code=404, detail="creation task cover is not available")
    registered = next(
        (
            artifact
            for artifact in production_repo.list_artifact_records(workspace_id, task_id)
            if artifact.logical_ref == cover.artifact_ref and artifact.kind == "cover_image"
        ),
        None,
    )
    if registered is None:
        raise HTTPException(status_code=409, detail="creation task cover is not a registered artifact")
    try:
        path = artifact_store.resolve_file(cover.artifact_ref)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="creation task cover not found") from exc
    media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=media_type, filename=path.name)


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
        include_private=_content_include_private(request),
    )
    return MaterialLibraryResponse(
        assets=[_material_asset_response(asset) for asset in assets],
        total_count=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(assets) < total,
    )


def _material_binding_status(workspace_id: str, repository: ProductionRepository) -> MaterialIntakeStatusResponse:
    directory = repository.material_ingestion_directory_state(workspace_id)
    binding = repository.get_material_ingestion_binding()
    foreign = bool(directory["bound_to_another_workspace"]) or (binding is not None and binding[0] != workspace_id)
    bound = binding is not None and binding[0] == workspace_id
    if foreign:
        state = "unknown"
        message = "素材目录已关联其他工作区；当前工作区不能读取。"
        repair = "切换到原工作区，或先明确解除原绑定。"
    elif directory["configured_path_changed"]:
        state = "changed"
        message = "当前设置的素材目录与本工作区原绑定目录不同。"
        repair = "先检查目录设置并处理原绑定，再重新确认。"
    else:
        state = str(directory["status"])
        message, repair = {
            "ready": (("素材目录可访问；文件稳定后会进入分析。" if bound else
                       "素材目录可访问，等待关联到当前工作区。"), None),
            "missing": ("找不到当前设置的素材目录。", "连接存储盘或在设置中选择现有目录。"),
            "unmounted": ("素材所在的外接盘没有挂载。", "连接原数据盘后重试。"),
            "invalid": ("当前素材目录不可用于自动导入。", "在设置中选择可访问的素材目录。"),
        }.get(state, ("尚未确认素材目录状态。", "检查目录设置后重试。"))
    return MaterialIntakeStatusResponse(
        bound_to_current_workspace=bound,
        binding_available=not foreign,
        configured_directory=None if foreign else str(directory["directory"]),
        bound_directory=binding[1] if bound else None,
        directory_state=state,
        directory_message=message,
        repair_action=repair,
        scan_interval_seconds=60,
        file_counts=(repository.material_ingestion_state_counts(workspace_id) if bound else
                     {"observing": 0, "running": 0, "ready": 0, "failed": 0, "blocked_disk": 0}),
    )


def _material_ingestion_for(repository: ProductionRepository):
    service = material_ingestion
    if material_execution_mode == "scheduled":
        runtime = device_runtime
        if (
            runtime is None
            or not runtime.running
            or runtime.last_error is not None
            or service is None
            or service.repository is not repository
            or getattr(service, "execution_mode", None) != "scheduled"
            or service.scheduler is not runtime.scheduler
            or not isinstance(getattr(runtime.scheduler, "database_path", None), (str, Path))
            or Path(runtime.scheduler.database_path).expanduser().resolve()
            != Path(repository.database_path).expanduser().resolve()
        ):
            raise HTTPException(status_code=503, detail="整机调度尚未就绪，素材重活未启动。")
        return service
    if service is None or service.repository is not repository:
        raise HTTPException(status_code=503, detail="素材扫描服务尚未就绪，请稍后重试。")
    return service


@app.get("/api/production/materials/intake", response_model=MaterialIntakeStatusResponse)
def get_material_intake_status(request: Request) -> MaterialIntakeStatusResponse:
    repository = production_repo
    return _material_binding_status(current_workspace_id(request), repository)


@app.post("/api/production/materials/intake/bind", response_model=MaterialIntakeStatusResponse)
def bind_material_intake(request: Request, payload: MaterialIntakeBindRequest) -> MaterialIntakeStatusResponse:
    workspace_id = current_workspace_id(request)
    repository = production_repo
    service = _material_ingestion_for(repository)
    try:
        repository.bind_material_ingestion(workspace_id, expected_directory=payload.expected_directory)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    service.wake()
    return _material_binding_status(workspace_id, repository)


@app.post("/api/production/materials/intake/scan")
def scan_material_intake(request: Request) -> MaterialLibraryResponse:
    workspace_id = current_workspace_id(request)
    repository = production_repo
    status = _material_binding_status(workspace_id, repository)
    if not status.bound_to_current_workspace:
        raise HTTPException(status_code=409, detail="请先在当前工作区绑定素材目录")
    if status.directory_state != "ready":
        raise HTTPException(status_code=409, detail=status.directory_message)
    _material_ingestion_for(repository).scan_once()
    assets, total = repository.list_assets_page(workspace_id, limit=24, offset=0)
    return MaterialLibraryResponse(
        assets=[_material_asset_response(asset) for asset in assets],
        total_count=total, limit=24, offset=0, has_more=len(assets) < total,
    )


@app.post("/api/production/materials/{asset_id}/intake/retry")
def retry_material_intake(asset_id: str, request: Request) -> MaterialAssetResponse:
    workspace_id = current_workspace_id(request)
    repository = production_repo
    service = _material_ingestion_for(repository)
    if material_execution_mode == "scheduled":
        raise HTTPException(status_code=503, detail="素材手动重试的调度授权尚未接通。")
    status = _material_binding_status(workspace_id, repository)
    if status.directory_state != "ready":
        raise HTTPException(status_code=409, detail=status.directory_message)
    try:
        repository.retry_material_ingestion(workspace_id, asset_id)
        asset = repository.get_asset(workspace_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材不属于当前工作区") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    service.wake()
    return _material_asset_response(asset)


@app.put("/api/production/materials/{asset_id}/tags", response_model=MaterialAssetResponse)
def update_material_asset_tags(asset_id: str, request: Request, payload: MaterialAssetTagsRequest) -> MaterialAssetResponse:
    try:
        asset = production_repo.update_asset_tags(current_workspace_id(request), asset_id, payload.user_tags)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材不属于当前工作区") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _material_asset_response(asset)


@app.post("/api/production/materials/tags/batch", response_model=MaterialBatchTagsResponse)
def update_material_asset_tags_batch(request: Request, payload: MaterialBatchTagsRequest) -> MaterialBatchTagsResponse:
    try:
        assets = production_repo.update_assets_tags_batch(
            current_workspace_id(request), payload.asset_ids,
            add=payload.add, remove=payload.remove,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="所选素材有不属于当前工作区的项目，未做任何修改") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MaterialBatchTagsResponse(assets=[_material_asset_response(asset) for asset in assets])


@app.get("/api/production/materials/{asset_id}/unmatched-corrections")
def get_unmatched_material_corrections(asset_id: str, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    try:
        production_repo.get_asset(workspace_id, asset_id)
        items = production_repo.list_unmatched_material_corrections(workspace_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材不属于当前工作区") from exc
    return {"asset_id": asset_id, "items": items}


@app.get(
    "/api/production/materials/search",
    response_model=list[MaterialSegmentMatchResponse],
)
def search_production_material_segments(
    request: Request,
    q: str = "",
    usage_role: str | None = None,
    tags: str | None = None,
    min_duration_seconds: float | None = None,
    max_duration_seconds: float | None = None,
    limit: int = 20,
) -> list[MaterialSegmentMatchResponse]:
    if usage_role not in {None, "a_roll", "b_roll"}:
        raise HTTPException(status_code=422, detail="invalid material usage role")
    try:
        matches = production_repo.search_material_segments(
            current_workspace_id(request),
            query=q,
            usage_roles=[usage_role] if usage_role else ["a_roll", "b_roll"],
            tags=[item.strip() for item in tags.split(",") if item.strip()]
            if tags
            else None,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [MaterialSegmentMatchResponse.model_validate(asdict(item)) for item in matches]


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
    if material_execution_mode == "scheduled":
        _material_ingestion_for(production_repo).scan_once()
    else:
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
    if material_execution_mode == "scheduled":
        _material_ingestion_for(production_repo)
        raise HTTPException(status_code=503, detail="素材手动分析的调度授权尚未接通。")
    try:
        asset = production_repo.analyze_asset(current_workspace_id(request), asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _material_asset_response(asset)


@app.put("/api/production/materials/{asset_id}/use-state", response_model=MaterialAssetResponse)
def update_material_use_state(asset_id: str, request: Request, payload: MaterialUseStateRequest) -> MaterialAssetResponse:
    try:
        asset = production_repo.update_asset_use_state(current_workspace_id(request), asset_id, payload.use_state)
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
    batches = production_repo.list_batches(current_workspace_id(request))
    return ProductionBatchesResponse(
        batches=[
            _production_batch_response(batch)
            for batch in batches
        ],
        total_count=len(batches),
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
        batch = production_repo.get_batch(current_workspace_id(request), batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="production batch not found") from exc
    return _production_batch_response(batch)
















def _record_montage_usage(workspace_id: str, plan) -> None:
    for clip in plan.clips:
        if clip.person_render_id:
            continue
        production_repo.record_segment_usage(
            workspace_id,
            clip.asset_id,
            clip.segment_id,
            plan_id=plan.id,
            clip_id=clip.id,
        )




def _render_montage_with_resolved_clip_sources(
    workspace_id: str,
    plan,
    narration_audio_path: Path,
    *,
    job_staging_path: Path | None = None,
    subtitle_tracks: list[SubtitleTrack] | None = None,
    render_context: CpuRenderGrantContext | None = None,
):
    if render_context is not None and job_staging_path is None:
        raise ValueError("受许可成片必须写入任务暂存目录")
    original_references = [clip.source_path for clip in plan.clips]
    original_ranges = [
        (clip.source_start_seconds, clip.source_end_seconds) for clip in plan.clips
    ]
    original_narration_reference = plan.narration_audio_path
    original_graphics = getattr(plan,'graphic_overlays',None)
    cues_by_id = {cue.id: cue for cue in plan.cues}
    try:
        if original_graphics is not None:
            plan.graphic_overlays=[{**item,'source_ref':str(resolve_graphic_source(production_repo,artifact_store,workspace_id,plan.task_id,item['source_ref']))}
                if item.get('kind')=='image' else item for item in original_graphics]
        for clip in plan.clips:
            if clip.person_render_id:
                render = production_repo.get_person_render(
                    workspace_id, plan.task_id, clip.person_render_id
                )
                output = _person_render_media_path(render)
                cue = cues_by_id.get(clip.cue_id)
                if (
                    output is None
                    or cue is None
                    or render.narration_segment_id != cue.narration_segment_id
                ):
                    raise MediaAuthorityError(
                        reason="person_render_unavailable",
                        impact="人物口型片段未被传给渲染器，当前成片没有改变。",
                        repair_action="重新生成所选人物口型片段后再渲染。",
                    )
                clip.source_path = str(output)
                if clip.source_end_seconds > (render.duration_seconds or 0.0) + .001:
                    raise ValueError("人物口型源片段不足以覆盖当前镜头")
                continue
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
        extended,_transitions=extend_dissolve_sources(plan.clips)
        for clip,handle_clip in zip(plan.clips,extended,strict=True):
            effects=VisualEffects.model_validate(clip.visual_effects)
            if effects.beauty_strength or effects.skin_exposure:
                if render_context is not None:
                    raise ValueError("本机人像效果尚无受许可执行路径，请先移除该效果")
                processed,metadata=_portrait_processor(workspace_id).process(Path(clip.source_path),clip.source_start_seconds,
                    handle_clip.source_end_seconds,effects.beauty_strength,effects.skin_exposure)
                clip.source_path=str(processed)
                original_duration=clip.source_end_seconds-clip.source_start_seconds
                clip.source_start_seconds=0
                clip.source_end_seconds=original_duration if handle_clip is not clip else metadata["duration_seconds"]
        if job_staging_path is None:
            return montage_renderer.render(plan, narration_audio_path)
        if isinstance(plan, CreationTaskMontagePlan) and plan.audio_timeline is not None:
            mixed_audio = Path(job_staging_path) / "timeline-mix.wav"
            service = _task_audio()
            audio_options = (
                {"scheduler": render_context.scheduler, "grant": render_context.grant,
                 "cancel_event": render_context.cancel_event,
                 "deadline_monotonic": render_context.deadline_monotonic,
                 "max_output_bytes": int(plan.duration_seconds * 192_000) + 4096,
                 "threads": render_context.threads}
                if render_context is not None else {}
            )
            render_audio_mix(plan.audio_timeline, plan.duration_seconds,
                lambda reference: service.source(workspace_id, plan.task_id, reference), mixed_audio,
                **audio_options)
            narration_audio_path = mixed_audio
        render_options = (
            {"scheduler": render_context.scheduler, "grant": render_context.grant,
             "cancel_event": render_context.cancel_event,
             "deadline_monotonic": render_context.deadline_monotonic,
             "max_output_bytes": render_context.max_output_bytes,
             "threads": render_context.threads}
            if render_context is not None else {}
        )
        return montage_renderer.render(
            plan, narration_audio_path, job_staging_path=job_staging_path,
            subtitle_tracks=subtitle_tracks, **render_options,
        )
    finally:
        for clip, reference, source_range in zip(
            plan.clips, original_references, original_ranges, strict=True
        ):
            clip.source_path = reference
            clip.source_start_seconds, clip.source_end_seconds = source_range
        plan.narration_audio_path = original_narration_reference
        if original_graphics is not None:
            plan.graphic_overlays=original_graphics




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
        if asset.review_status != "approved" or not material_allows_footage(asset):
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








@app.get("/api/version")
async def get_version():
    """Return the current API version, build info, and completed phases."""
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
        return OnboardingCompletionResponse(
            workspace_id=LEGACY_WORKSPACE_ID,
            completed=True,
        )

    principal = getattr(request.state, "principal", None)
    if principal is None or not principal.user_id or not principal.workspace_id:
        raise HTTPException(status_code=403, detail="workspace context is required")

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
    global settings, repo, source_repository, model_catalog, model_installer, visual_semantic_provider, voice_clone_provider, avatar_lipsync_provider, latentsync_provider, production_repo, montage_repo, montage_planner, montage_renderer, artifact_store, narration_generator, voice_preview_service, avatar_preview_service, vault, video_inbox, video_processor, video_pipeline, draft_workspace, source_tracker, service, workflow, auth_context, video_job_runner, creation_execution, execution_worker, material_ingestion

    if material_execution_mode == "scheduled" or creation_execution_mode == "scheduled":
        raise RuntimeError("整机调度的安全重载尚未接通，现有驱动保持原状态。")
    _stop_execution_worker()
    _stop_material_ingestion()

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
        understanding_runtime_python_path=settings.understanding_runtime_python_path,
        understanding_worker_path=settings.understanding_worker_path,
    )
    model_installer = ModelInstaller(
        model_catalog,
        on_complete=lambda: _reload_runtime_dependencies(),
    )
    visual_semantic_provider = visual_semantic_provider_from_catalog(model_catalog)
    voice_clone_provider = voice_clone_provider_from_catalog(model_catalog)
    avatar_lipsync_provider = avatar_lipsync_provider_from_settings(settings)
    latentsync_provider = avatar_lipsync_provider_from_settings(settings, quality="high")
    production_repo = ProductionRepository(
        database_path=settings.database_path,
        video_library_path=settings.video_inbox_path,
        visual_provider=visual_semantic_provider,
        speech_analyzer=LocalMaterialSpeechAnalyzer(settings),
        visual_understanding=LocalVisualUnderstanding(model_catalog, settings.effective_temporary_root_path, settings.native_ocr_worker_path),
    )
    video_job_runner = VideoJobRunner(production_repo)
    montage_repo = MontageRepository(settings.database_path)
    montage_planner = MontagePlanner(
        semantic_provider=visual_semantic_provider,
        visual_feature_loader=production_repo.get_visual_feature,
    )
    montage_renderer = MontageRenderer(settings.video_output_path)
    artifact_store = ArtifactStore(settings.video_output_path)
    narration_generator = NarrationGenerator(settings.video_output_path, voice_clone_provider)
    voice_preview_service = VoicePreviewService(settings.video_output_path, voice_clone_provider)
    avatar_preview_service = AvatarPreviewService(
        settings.video_output_path,
        avatar_lipsync_provider,
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
    video_job_runner.recover_after_restart()
    creation_execution = _new_creation_execution()
    _start_execution_worker()
    _start_material_ingestion()
    _recover_creation_executions()


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














@app.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)
def get_episode(episode_id: str, request: Request) -> EpisodeResponse:
    return episode_response(get_episode_or_404(episode_id))




@app.get("/api/episodes/{episode_id}/retake-report", response_model=RetakeReportResponse)
def get_episode_retake_report(episode_id: str) -> RetakeReportResponse:
    return _read_retake_report(get_episode_or_404(episode_id))


@app.get("/api/episodes/{episode_id}/edit-plan", response_model=EditPlanResponse)
def get_episode_edit_plan(episode_id: str) -> EditPlanResponse:
    episode = get_episode_or_404(episode_id)
    return EditPlanResponse.model_validate(
        build_edit_plan_workspace(episode, settings.video_output_path)
    )






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


_LEGACY_WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]


def _creation_render_is_valid(
    workspace_id: str, task_id: str, *, verify_media: bool = True,
    render_context: CpuRenderGrantContext | None = None,
) -> bool:
    try:
        task = production_repo.get_creation_task(workspace_id, task_id)
        if not task.current_timeline_version_id:
            return False
        plan = production_repo.get_creation_task_montage_plan(workspace_id, task_id)
        ref = plan.get("output_path")
        if (plan.get("status") != "rendered" or not isinstance(ref, str)
                or plan.get("script_id") != task.confirmed_script_id):
            return False
        version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        if version.task_id != task_id or ref not in version.snapshot.get("output_refs", []):
            return False
        registered = next(
            (artifact for artifact in production_repo.list_artifact_records(workspace_id, task_id)
             if artifact.logical_ref == ref and artifact.kind == "rendered_video"),
            None,
        )
        if registered is None or production_repo.get_job_run(workspace_id, registered.producer_job_id).status != "succeeded":
            return False
        path = artifact_store.resolve_file(ref)
        if not verify_media:
            return path.stat().st_size == registered.size_bytes
        if render_context is not None:
            render_context.scheduler.assert_grant(render_context.grant)
        published = artifact_store.describe(ref)
        if published.sha256 != registered.sha256 or published.size_bytes != registered.size_bytes:
            return False
        media_options = (
            {"scheduler": render_context.scheduler, "grant": render_context.grant,
             "cancel_event": render_context.cancel_event,
             "deadline_monotonic": render_context.deadline_monotonic}
            if render_context is not None else {}
        )
        _validate_rendered_media(path, expected_size=canvas_size(str(plan["aspect_ratio"])),
                                 expected_duration_seconds=float(plan["duration_seconds"]),
                                 **media_options)
        return True
    except (KeyError, FileNotFoundError, ValueError, OSError):
        return False


def _new_creation_execution() -> CreationExecution:
    return CreationExecution(
        production_repo,
        run_narration=_generate_creation_task_narration_for_workspace,
        run_montage_plan=_create_creation_task_montage_plan_for_workspace,
        run_render=_render_creation_task_montage_for_workspace,
        render_is_valid=_creation_render_is_valid,
    )


def _recover_creation_executions() -> None:
    # The repository owns the cross-workspace pending-job query. A workspace is
    # taken only from each persisted JobRun, never from the current login.
    if creation_execution_mode == "scheduled":
        runtime = _scheduled_creation_runtime()
        # A crash may land after the JobRun cancellation commits but before
        # its device request is cancelled. Reconcile from both durable rows.
        with production_repo._connect() as conn:
            cancelled_requests = conn.execute(
                "SELECT r.id, r.workspace_id, r.member_id, r.owner_id FROM device_work_requests r "
                "JOIN job_runs j ON j.workspace_id=r.workspace_id AND j.id=r.owner_id "
                "WHERE r.owner_type='creation_job' AND j.kind='creation_task_execution' "
                "AND j.status='cancelled' AND r.state IN ('waiting', 'blocked', 'granted')"
            ).fetchall()
        for row in cancelled_requests:
            event = production_repo.get_task_event(row["workspace_id"], f"event_{row['owner_id']}_start")
            if (row["workspace_id"] != LEGACY_WORKSPACE_ID or row["member_id"] != "local-owner"
                    or event.payload.get("member_id") != row["member_id"]):
                raise RuntimeError("已停止制作请求的持久身份无法复核，不能自动取消设备许可")
            runtime.scheduler.cancel(row["id"], CreationExecutionActor(row["workspace_id"], row["member_id"]))
        for job in production_repo.list_pending_creation_executions():
            event = production_repo.get_task_event(job.workspace_id, f"event_{job.id}_start")
            member_id = event.payload.get("member_id")
            if job.workspace_id != LEGACY_WORKSPACE_ID or member_id != "local-owner":
                raise RuntimeError("旧制作作业缺少可复核的本机成员身份，不能自动申请设备许可")
            submit_creation_stage(creation_execution, runtime.scheduler, job,
                                  CreationExecutionActor(job.workspace_id, member_id))
        runtime.wake()
        return
    for job in production_repo.list_pending_creation_executions():
        execution_worker.submit(creation_execution.execute, job.workspace_id, job.id)


def _scheduled_creation_runtime():
    runtime = device_runtime
    if (runtime is None or runtime.last_error is not None
            or "creation_job" not in runtime.registrations):
        raise HTTPException(status_code=503, detail="整机制作许可尚未就绪，当前任务没有启动")
    return runtime


def _scheduled_creation_actor(request: Request, workspace_id: str) -> CreationExecutionActor:
    # The current auth service cannot recheck remote membership inside the
    # scheduler's SQLite admission transaction. Do not mint a durable member
    # identity from a request whose later authority cannot be verified.
    if auth_context.enabled:
        raise HTTPException(status_code=503, detail="多成员制作许可尚未接通持续身份核验")
    if workspace_id != LEGACY_WORKSPACE_ID:
        raise HTTPException(status_code=403, detail="本机制作身份与工作区不匹配")
    return CreationExecutionActor(workspace_id, "local-owner")


_EXECUTION_WAITING_TEXT = {
    "dependency": "等待前一步或受许可执行能力就绪。",
    "owner_unknown": "当前成员或任务归属暂不能核实。",
    "authorization_lost": "当前任务授权已变化，请核对权限。",
    "input_stale": "文案或制作要求已变化，请重新确认。",
    "scheduled_time": "等待指定开始时间。",
    "outside_window": "当前不在选择的执行时段内。",
    "activity_unknown": "暂不能确认设备是否空闲。",
    "device_active": "设备有人正在播放、录音或编辑。",
    "resource_unknown": "暂不能确认设备资源是否足够。",
    "resource_busy": "设备资源正在处理其他工作。",
    "retry_not_before": "等待下次可重试时间。",
    "recovered": "设备恢复后等待重新核验。",
}


def _creation_execution_status(workspace_id: str, task_id: str) -> dict[str, object]:
    task = _creation_task_or_404(workspace_id, task_id)
    job = creation_execution.latest(workspace_id, task_id)
    events = (
        [
            {"stage": event.payload.get("stage"), "status": event.payload.get("status"),
             "reason": event.payload.get("reason"), "at": event.created_at.isoformat()}
            for event in production_repo.list_task_events(workspace_id, task_id)
            if job is not None and event.job_id == job.id
            and event.event_type == "creation_execution_stage"
        ]
        if job else []
    )
    covers = (
        production_repo.list_cover_assets(workspace_id, task_id, timeline_version_id=task.current_timeline_version_id)
        if task.current_timeline_version_id else []
    )
    current_stage = None
    if job and events:
        latest_stage_event = events[-1]
        if (latest_stage_event["stage"] in {"narration", "montage_plan", "render", "cover"}
                and latest_stage_event["status"] in {"running", "failed", "pending"}):
            current_stage = latest_stage_event["stage"]
    waiting_reason = None
    status_updated_at = job.updated_at.isoformat() if job else None
    if (job and job.status in {"queued", "interrupted"}
            and creation_execution_mode == "scheduled" and workspace_id == LEGACY_WORKSPACE_ID):
        start_event = production_repo.get_task_event(workspace_id, f"event_{job.id}_start")
        if start_event.payload.get("member_id") == "local-owner":
            with production_repo._connect() as conn:
                row = conn.execute(
                    "SELECT unit_key, wait_reason, updated_at FROM device_work_requests "
                    "WHERE workspace_id=? AND member_id=? AND owner_type='creation_job' AND owner_id=? "
                    "AND input_digest=? AND state IN ('waiting', 'blocked', 'granted', 'yield_requested') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (workspace_id, "local-owner", job.id, job.input_digest),
                ).fetchone()
            if row is not None:
                queued_stage = row["unit_key"].split(":", 1)[0]
                if queued_stage in {"narration", "montage_plan", "render"}:
                    current_stage = queued_stage
                waiting_reason = _EXECUTION_WAITING_TEXT.get(row["wait_reason"])
                if datetime.fromisoformat(row["updated_at"]) > job.updated_at:
                    status_updated_at = row["updated_at"]
    return {
        "task_id": task_id,
        "job": asdict(job) if job else None,
        "stages": events,
        "current_stage": current_stage,
        "waiting_reason": waiting_reason,
        "status_updated_at": status_updated_at,
        "video_ready": _creation_render_is_valid(
            workspace_id, task_id, verify_media=creation_execution_mode != "scheduled"),
        "cover_status": "ready" if any(cover.status == "valid" for cover in covers) else "pending",
        "cover_message": None if any(cover.status == "valid" for cover in covers) else "尚未生成封面，请单独制作或选择封面。",
    }


@app.get("/api/creation-tasks/{task_id}/execution/preview")
def preview_creation_execution(task_id: str, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not task.confirmed_script_id:
        raise HTTPException(status_code=409, detail="请先确认实际文案")
    script = production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    digest = revision_digest(script, plan)
    try:
        creation_execution.validate_input(workspace_id, task, script.id, digest)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "task_id": task.id,
        "script_id": script.id,
        "script_title": script.title,
        "script_sections": [{"title": section.title, "content": section.content} for section in script.sections],
        "plan_id": plan.id,
        "requirements": {
            "aspect_ratio": plan.aspect_ratio,
            "duration_target_seconds": plan.duration_target_seconds,
            "presenter_share_target": plan.presenter_share_target,
            "lipsync_quality": plan.lipsync_quality,
            "selected_voice": plan.selected_voice.profile_id if plan.selected_voice else None,
            "template_settings": plan.template_settings,
        },
        "input_digest": digest,
    }


@app.get("/api/creation-tasks/{task_id}/execution")
def get_creation_execution(task_id: str, request: Request) -> dict[str, object]:
    return _creation_execution_status(current_workspace_id(request), task_id)


@app.post("/api/creation-tasks/{task_id}/execution/start", status_code=202)
def start_creation_execution(task_id: str, request: Request, payload: ExecutionStartRequest) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    if not payload.requirements_confirmed:
        raise HTTPException(status_code=409, detail="请先确认实际文案与本次制作要求")
    runtime = _scheduled_creation_runtime() if creation_execution_mode == "scheduled" else None
    actor = _scheduled_creation_actor(request, workspace_id) if runtime is not None else None
    try:
        job = creation_execution.start(workspace_id, task_id, payload.script_id, payload.input_digest,
                                       member_id=actor.member_id if actor else None)
        if runtime is not None and actor is not None:
            submit_creation_stage(creation_execution, runtime.scheduler, job, actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="创作任务或确认对象不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if runtime is not None:
        runtime.wake()
    elif job.status in {"queued", "interrupted"}:
        execution_worker.submit(creation_execution.execute, workspace_id, job.id)
    return _creation_execution_status(workspace_id, task_id)


@app.post("/api/creation-tasks/{task_id}/execution/resume", status_code=202)
def resume_creation_execution(task_id: str, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    runtime = _scheduled_creation_runtime() if creation_execution_mode == "scheduled" else None
    actor = _scheduled_creation_actor(request, workspace_id) if runtime is not None else None
    try:
        job = creation_execution.retry(workspace_id, task_id,
                                       member_id=actor.member_id if actor else None)
        if runtime is not None and actor is not None:
            submit_creation_stage(creation_execution, runtime.scheduler, job, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if runtime is not None:
        runtime.wake()
    elif job.status in {"queued", "interrupted"}:
        execution_worker.submit(creation_execution.execute, workspace_id, job.id)
    return _creation_execution_status(workspace_id, task_id)


@app.post("/api/creation-tasks/{task_id}/execution/cancel")
def cancel_creation_execution(task_id: str, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    runtime = _scheduled_creation_runtime() if creation_execution_mode == "scheduled" else None
    actor = _scheduled_creation_actor(request, workspace_id) if runtime is not None else None
    try:
        job = creation_execution.cancel(workspace_id, task_id,
                                        member_id=actor.member_id if actor else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if runtime is not None and actor is not None:
        with production_repo._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
                "AND owner_id=? AND state IN ('waiting', 'blocked', 'granted')",
                (workspace_id, job.id),
            ).fetchall()
        for row in rows:
            runtime.scheduler.cancel(row["id"], actor)
            registration = runtime.registrations.get("creation_job")
            executor = getattr(registration, "executor", None)
            if isinstance(executor, CreationJobUnitExecutor):
                executor.request_cancel(row["id"])
    return _creation_execution_status(workspace_id, task_id)


creation_execution = _new_creation_execution()
execution_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creation-execution")
material_ingestion = None
material_execution_mode: Literal["legacy", "scheduled"] = "legacy"
creation_execution_mode: Literal["legacy", "scheduled"] = "legacy"
device_runtime = None


def _build_scheduled_material_components(
    *,
    repository: ProductionRepository,
    device_lock_path: str | Path,
    device_access,
    resources,
    processes,
):
    """Compose one hash-only runtime candidate without starting production work."""
    from app.device_runtime import OwnerRegistration, build_device_runtime
    from app.material_ingestion import (
        MaterialHashOwnerVerifier, MaterialHashUnitExecutor, MaterialIngestionService,
    )

    executor = MaterialHashUnitExecutor()
    runtime = build_device_runtime(
        database_path=repository.database_path,
        device_lock_path=device_lock_path,
        device_access=device_access,
        registrations={"material_asset": OwnerRegistration(MaterialHashOwnerVerifier(repository), executor)},
        resources=resources,
        processes=processes,
        activity_source_reliable=False,
    )
    service = MaterialIngestionService(
        repository, execution_mode="scheduled", scheduler=runtime.scheduler,
        scan_interval_seconds=60, stable_seconds=10,
    )
    executor.bind(service)
    return runtime, service


def _new_material_ingestion():
    from app.material_ingestion import MaterialIngestionService
    return MaterialIngestionService(production_repo, scan_interval_seconds=60, stable_seconds=10)


def _start_execution_worker() -> None:
    global execution_worker
    if execution_worker is None:
        execution_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creation-execution")


def _stop_execution_worker() -> None:
    global execution_worker
    worker = execution_worker
    execution_worker = None
    if worker is not None:
        worker.shutdown(wait=True)


def _start_material_ingestion() -> None:
    global material_ingestion
    if material_execution_mode == "scheduled":
        raise RuntimeError("整机调度的统一启动尚未接通，不能启动旧素材线程。")
    if material_ingestion is not None:
        material_ingestion.start()
        return
    service = _new_material_ingestion()
    try:
        service.start()
    except Exception:
        service.stop()
        raise
    material_ingestion = service


def _stop_material_ingestion() -> None:
    global material_ingestion
    service = material_ingestion
    material_ingestion = None
    if service is not None:
        service.stop()


@app.on_event("startup")
def resume_persisted_creation_executions() -> None:
    if creation_execution_mode == "scheduled":
        raise RuntimeError("制作单元的统一许可启动尚未接通，不能同时启动旧素材与制作线程。")
    _start_execution_worker()
    try:
        _start_material_ingestion()
        _recover_creation_executions()
    except Exception:
        _stop_material_ingestion()
        _stop_execution_worker()
        raise


@app.on_event("shutdown")
def stop_creation_execution_worker() -> None:
    _stop_material_ingestion()
    _stop_execution_worker()


@app.api_route("/api/episodes", methods=_LEGACY_WRITE_METHODS)
@app.api_route("/api/episodes/{legacy_path:path}", methods=_LEGACY_WRITE_METHODS)
@app.api_route("/api/drafts/{legacy_path:path}", methods=_LEGACY_WRITE_METHODS)
@app.api_route("/api/production/batches", methods=_LEGACY_WRITE_METHODS)
@app.api_route(
    "/api/production/batches/{legacy_path:path}",
    methods=_LEGACY_WRITE_METHODS,
)
@app.api_route(
    "/api/production/montage-plans/{legacy_path:path}",
    methods=_LEGACY_WRITE_METHODS,
)
@app.api_route(
    "/api/production/episodes/{legacy_path:path}",
    methods=_LEGACY_WRITE_METHODS,
)
def reject_legacy_creation_write(legacy_path: str = "") -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_creation_write_retired",
            "message": "旧 Episode、Batch 与草稿写入口已退役。",
            "impact": "本次请求没有写入历史 Episode、Batch 或剪辑草稿。",
            "repair_action": "请通过 CreationTask 接口继续当前创作任务。",
            "retryable": False,
        },
    )






# ── Server entry point (for standalone API server) ──────────────────
if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
