from __future__ import annotations
from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
from app.creation.render_stage_use_case import RenderStageUseCase, RenderRejected
from app.creation.montage_stage_use_case import MontageStageUseCase, MontageStageRejected
from app.creation.person_stage_use_case import PersonStageUseCase,PersonStageRejected,PersonEditGrantRequired
from app.creation.narration_stage_use_case import NarrationStageUseCase,NarrationStageRejected

import base64
import hashlib
from functools import cache
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal
from types import SimpleNamespace
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from weakref import WeakSet

if TYPE_CHECKING:
    from app.device_runtime import DeviceRuntime

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

import httpx

from app.assistant_pi import (
    ASSISTANT_BUSINESS_TOOLS, AssistantStopped, AssistantUnavailable, forget_turn_control,
    pi_runtime_checks, register_turn_control,
    request_stop_turn, run_material_turn, run_plan_turn, run_task_turn,
    stop_all_assistant_turns,
)
from app.assistant_goal_continuation import GoalContinuationService, GoalResultProjection, GoalProjectionRejected
from app.conversation_repository import (
    ConversationDraftVersionConflict,
    ConversationReferenceVersionConflict,
    ConversationRepository,
    ConversationTurnAlreadyRunning,
    ConversationTurnRequestConflict,
)

from app.compound_edits import compile_compound_edit, compile_compound_selection, apply_compound_subtitles, is_indexed_subtitle_command, synchronize_linked_subtitle_cue
from app.graphic_overlays import validate_overlays, compile_overlay_command
from app.graphic_sources import import_graphic_source, graphic_sources, resolve_graphic_source, graphic_source_facts
from app.transitions import dissolve_frames, extend_dissolve_sources
from app.visual_effects import clip_playback_rate, reusable_color_settings
from app.speech_timing import estimate_speech_seconds
from app.linked_timeline import reorder_linked_group
from app.canvas import canvas_size
from app.subtitle_style import compile_subtitle_style_command
from app.subtitle_edits import normalize_subtitle_text_and_breaks, update_subtitle_cue
from app.subtitle_schedule import subtitle_schedule, narration_schedule, retime_subtitle_tracks, new_subtitle_gap_intervals
from app.avatar_lipsync import (compile_person_edit_command, AvatarAndLipSyncService, AvatarPreviewService, avatar_lipsync_cache_key, avatar_lipsync_provider_from_settings)
from app.profiles.contracts import (Authorization, PersonProfile, ProfileReference, PronunciationEntry, VoiceProfile as VersionedVoiceProfile, profile_use_failure)
from app.auth import (
    AuthPrincipal,
    ForbiddenError,
    OnboardingStateError,
    SupabaseAuthContext,
    UnauthorizedError,
)
from app.device_accounts import DeviceAccountError, DeviceAccountService, DeviceCredential
from app.auth import WORKSPACE_SELECTION_HEADER
from app.companion.identity import CompanionIdentity, require_account_principal, require_companion_identity
from app.companion.routes import build_companion_router, companion_http_error
from app.companion.pairing import CompanionPairingService
from app.mobile_transfer_routes import build_mobile_transfer_router
from app.mobile_transfers import MobileTransferActor
from app.artifact_store import ArtifactStore
from app.bootstrap import DatabaseOwners, RuntimeDependencies, prepare_runtime_dependencies, bind_device_runtime
from app.creation.audio_ingress_use_case import AudioIngressUseCase, AudioIngressRejected, AUDIO_INGRESS_ACTIONS
from app.audio_timeline import AudioEditRequest, AudioListenRequest, AudioTimeline, apply_audio_edit, audio_edit_preview_range, compile_audio_command, narration_audio_timeline, probe_audio_source, render_audio_mix, rebind_narration_audio, bounded_audio_context
from app.task_audio import TaskAudio, TaskMediaJobs, TASK_MEDIA_CONTEXT, TASK_MEDIA_KIND, TaskMediaRequired, media_digest, audio_mix_resources
from app.creation.clip_preview import ClipPreviewCommand, ClipPreviewUseCase, ClipPreviewConflict, ClipPreviewUnavailable, prepare_clip_edit, needs_full_visual_preview
from app.media.clip_preview import ClipPreviewExecutor
from app.media.picture_preview import PicturePreviewRenderer, PicturePreviewRejected
from app.creation.visual_preview_use_case import VisualPreviewUseCase
from app.creation.editing.metadata import (PICTURE_METADATA_OPERATIONS,
    apply_audio_metadata, prepare_picture_command, prepare_audio_command, metadata_request_identity,
    prepare_clip_command, prepare_subtitle_display_command)
from app.creation.editing.clips import CLIP_SETTING_OPERATIONS, CLIP_METADATA_OPERATIONS, ClipPreviewConflict, apply_clip_patch, reorder_picture
from app.creation.editing.picture import graphic_overlay_candidate, EditConflict
from app.creation.editing.audio_edit_use_case import AudioEditUseCase, AudioEditRejected
from app.creation.output_observation_use_case import OutputObservationUseCase, OutputObservationRejected
from app.creation.editing.timeline_restore_use_case import (TimelineRestoreUseCase, TimelineRestoreCommand,
    TimelineOutputQualification, TimelineRestoreUnavailable)
from app.creation.editing.sources import EditSourceVerifier
from app.creation.editing.linked_timeline_use_case import (LinkedTimelineEditUseCase, LinkedEditApplyCommand, LinkedEditPreviewFailed,
    LINKED_EDIT_OPERATIONS, LinkedEditInputError, InsertMaterialMissing, InsertSourceRangeError)
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, MetadataEditReplay, InvalidMontage, LegacyMontageReference, EditPreviewRejected,
    SubtitleNoChange, SubtitleTargetMissing,
    metadata_edit_command,
    default_subtitle_style as metadata_default_subtitle_style,
    effective_audio_timeline as metadata_effective_audio_timeline,
    execution_subtitle_track as metadata_execution_subtitle_track,
    narration_linkage_spans as metadata_narration_linkage_spans,
    refresh_montage_review_state as metadata_refresh_montage_review_state,
    prepare_changed_montage as metadata_prepare_changed_montage,
    logical_montage_output_refs as metadata_logical_montage_output_refs,
)
from app.creation.editing.compound_use_case import (CompoundEditUseCase, CompoundPreviewCommand,
    CompoundSelectionCommand, CompoundProposalMissing, CompoundSelectionInputError)
from app.creation.editing.subtitle_use_case import (SubtitleEditUseCase, SubtitlePreviewFailed, is_subtitle_edit, subtitle_values)
from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase
from app.creation.editing.command_preview_use_case import CommandPreviewUseCase
from app.creation.editing.command_compile import AudioCommandPreview,CommandParseRejected,indexed_reorder_command,subtitle_text_from_command,edit_material_constraint
from app.creation.editing.narration_preview_use_case import NarrationPreviewUseCase
from app.creation.editing.person_preview_use_case import PersonPreviewUseCase
from app.creation.projection import project_task_summary, undo_target_versions, redo_target_versions
from app.creation.editing.subtitles import subtitle_display_selection
from app.creation.transactions import (prepare_change_set, commit_change_set, commit_direct_change,
    clone_subtitle_track as _clone_subtitle_track, clone_cover_asset as _clone_cover_asset)
from app.media_script import MediaScriptError
from app.media_script_jobs import read_script_toolchain, MediaScriptPreviewUseCase, MEDIA_SCRIPT_ACTIONS
from app.media_script_tools import MediaScriptTools, ScriptToolError, ScriptToolConflict, ScriptToolUnknown
from app.visual_effects import VisualEditRequest, VisualEffects, compile_crop_command, compile_visual_command, fade_filters, with_visual_effects
from app.local_portrait import LocalPortrait
from app.local_members import LocalMember, LocalMembersError, LocalMemberService
from app.material_speech import LocalMaterialSpeechAnalyzer
from app.device_scheduler import SessionHeartbeat, SubmissionIntent, SchedulePolicy, RecoveryBlocked
from app.member_permits import (
    cpu_audio_budget_from_env,
    DeviceHeartbeatRequest, HostBudgetUncalibrated, MemberDeviceAccess,
    build_host_probes, cpu_render_budget_from_env, creation_job_registration, find_member,
    host_budget_from_env, permit_actor, resolve_member_token,
    creation_stage_budgets_from_env, presenter_budgets_from_env,
    observation_budget_from_env,
    clip_preview_budget_from_env,
    quality_budget_from_env,
    cover_budget_from_env,
)
from app.visual_understanding import LocalVisualUnderstanding
from app.creation.cover_use_case import CoverUseCase, CoverUnavailable, CoverUnresolved, COVER_JOB_KIND, COVER_OPERATION
from app.cover_generation import (
    CoverSafeArea,
    unconfigured_image_generation_capability,
)
from app.config import (
    Settings,
    configure_process_temporary_directory,
    prepare_process_temporary_directory,
    publish_process_temporary_directory,
    record_llm_service_verification,
    runtime_env_file_path,
)
from app.content_templates import list_content_templates
from app.creation_execution import (
    presenter_inputs, presenter_input_facts, current_presenter_render,
    CpuRenderGrantContext, CreationStageGrantContext, CreationExecution, CreationExecutionActor,
    CreationJobUnitExecutor, ExecutionStartRequest,
    revision_digest, submit_creation_stage, PERSON_EDIT_KIND, person_edit_digest,
    NARRATION_EDIT_KIND, narration_edit_digest, montage_waiting_for_confirmation,
)
from app.creator_profile import (
    CreatorProfileConflict,
    change_creator_preference,
    get_or_create_profile,
    upsert_creator_profile,
)
from app.drafts import DraftWorkspace
from app.edit_plan import (
    build_edit_plan_workspace,
    safe_processed_video_path,
)
from app.external_sources import ExternalSourceResponseError, HtmlPageSourceClient
from app.job_progress import summarize_job_progress
from app.hermes_radar import HermesRadarSourceClient
from app.llm import (
    script_generator_from_settings,
)
from app.model_installer import ModelInstaller, ModelInstallError
from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT
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
    _validate_rendered_media,
)
from app.narration import NarrationArtifact, NarrationGenerator, VoicePreviewService, compile_narration_command
from app.preference_memory import PreferenceState, build_preference_memory
from app.preflight import setup_preflight, workspace_preflight
from app.profiles.voice_sampling import VoiceSamplingConflict, VoiceSamplingTask, MAX_SAMPLE_BYTES
from app.assistant.references import AssistantReferenceConflict, normalize_selection, validate_timeline_reference, timeline_object_refs
from app.assistant.inputs import AssistantInputConflict
from app.assistant.input_delivery import AssistantInputDelivery
from app.schemas import (VoiceSamplingTaskCreateRequest, MaterialDiscoveryStartRequest,
                         MaterialDiscoveryControlRequest, MaterialDiscoveryReceiveRequest,
                         AssistantConversationInputRequest)
from app.product_hunt import ProductHuntClient
from app.scheduled_tasks import (
    ScheduledTaskCreateRequest,
    ScheduledTaskEnableRequest,
    ScheduledTaskResponse,
    ScheduledTasksResponse,
    ScheduledTaskService,
    ScheduledTaskUpdateRequest,
)
from app.production import (EXACT_EDIT_SOURCE, MaterialVersionConflict, TimelineVersionConflict, ScriptLibraryEntry, content_readable, CreationPlan, VideoTemplate, CreationPlanBlocker, CreationPlanKnowledgeReference, CreationPlanPronunciationReference, CreationPlanSourceReference, CreationTask, CreationTaskScript, CreationTaskScriptSection, MediaAuthorityError, MaterialCollection, MaterialAssetNote, MaterialBusinessObject, MaterialLinkProvenanceSink, NarrationArtifactRecord, NarrationSegment, PersonRender, ProductionBatch, ProductionProfile, ProductionRepository, ScriptGenerationRun, TopicCandidate as CreationTaskTopicCandidate, TopicEvidenceReference, TopicGenerationRun, effective_segment_usage_role, material_allows_footage, material_segment_eligible)
from app.creation.publish_output_qualification_use_case import PublishOutputQualificationUseCase
from app.creation.video_template_use_case import VideoTemplateRejected, VideoTemplateUseCase
from app.creation.plan_preflight_use_case import (CreationPlanPreflightInput,
    CreationPlanPreflightRejected, CreationPlanPreflightUseCase, source_item_identity)
from app.creation.contracts import (EditCommand, ArtifactRecord, CoverAsset, DependencyEdge, EditIntent, EditOperation, EditProposal, JobRun, TaskEvent, TimelineVersion, SubtitleCue, SubtitleTrack, TransformRun)
from app.task_runtime import JobFailure, JobResult
from app.quality_inspection import QualityCheckRepository
from app.creation.quality_inspection_use_case import QualityInspectionUseCase, QUALITY_KIND
from app.creation.quality_execution import quality_check_registration
from app.schemas import CreationTaskObservationRequest, CreationTaskObservationTarget, KnowledgeSearchResponse
from app.media_link_import import (
    DEFAULT_MAX_BYTES,
    MediaLinkImportError,
    MediaLinkImportService,
)
from app.subtitle_tracks import (
    SubtitleCue as SubtitleExecutionCue,
    SubtitleStyle as SubtitleExecutionStyle,
    SubtitleTrack as SubtitleExecutionTrack,
    render_bilingual_srt,
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
from app.runtime_health import runtime_health, model_catalog_from_settings
from app.runtime_settings import (
    RuntimeSettingsSaveError,
    apply_runtime_settings_save,
    device_runtime_settings_values,
    is_local_client_host,
    runtime_settings_updates,
)
from app.workspace_settings import (
    WorkspaceSecretStoreError,
    workspace_settings,
)
from app.source_repository import KnowledgeVersionConflict, SourceRepository
from app.knowledge_file_store import KnowledgeOriginalUnavailable, KnowledgeOriginalIntegrityError, KnowledgeOriginalStorageError
from app.home_aggregation import aggregate_home
from app.schemas import (
    AssistantConversationCreateRequest,
    AssistantConversationCurrentRequest,
    AssistantConversationReferenceRequest,
    AssistantConversationReferenceResponse,
    AssistantConversationDraftRestoreRequest,
    AssistantConversationDraftRestoreResponse,
    AssistantConversationListResponse,
    AssistantConversationResponse,
    AssistantConversationRequestResponse,
    AssistantConversationStopRequest,
    AssistantConversationUpdateRequest,
    JobProgressResponse,
    TaskJobProgressResponse,
    ScriptLibrarySaveRequest, ScriptLibraryEntryResponse, ScriptLibraryListResponse,
    ScriptLibrarySourceResponse, ScriptLibraryReuseRequest, ScriptLibraryReuseResponse,
    AuthorizationResponse,
    AuthorizationsResponse,
    AuthorizationUpsertRequest,
    AvatarLipSyncPreviewRequest,
    ContentTemplatesResponse,
    CreationPlanConfirmRequest,
    CreationPlanPreviewRequest,
    SaveVideoTemplateRequest,
    VideoTemplateVersionRequest,
    CreationPlanPreviewResponse,
    CreationPlanResponse,
    CreationRequestCreateRequest,
    CreationRequestReviseRequest,
    CreationRequestWindowPreviewRequest,
    CreationRequestWindowConfirmRequest,
    CreationRequestWindowReplanRequest,
    CreationRequestResponse,
    CreationRequestItemsResponse,
    CreationTaskTopicConfirmRequest,
    CreationTaskTopicGenerateRequest,
    CreationTaskTopicsResponse,
    CreationTaskScriptConfirmRequest,
    CreationTaskScriptDraftRequest,
    TaskKnowledgeAdoptionRequest,
    TaskKnowledgeAdoptionResponse,
    AssistantReplyScriptTransferRequest,
    CreationTaskScriptResponse,
    CreationTaskScriptGenerateRequest,
    CreationTaskScriptsResponse,
    CreationTaskNarrationResponse,
    CreationTaskRecordingAdoptRequest,
    CreationTaskNarrationVoiceSelectionRequest,
    CreationTaskNarrationVoiceSelectionResponse,
    CreationTaskPersonRenderRequest,
    CreationTaskPersonRenderResponse,
    CreationTaskPersonRendersResponse,
    CreationTaskMontagePlanResponse,
    StoryboardRegenerationRequest,
    AssistantStoryboardRegenerationReceipt,
    AssistantStoryboardRegenerationFailure,
    CreationTaskMontageClipUpdateRequest,
    CreationTaskMontageRenderRequest,
    CreationTaskQualityCheckRequest,
    CreationTaskQualityCheckResponse,
    CreationTaskEditConfirmResponse,
    CreationTaskEditQueuedResponse,
    CreationTaskEditPreviewRequest,
    CreationTaskAssistantTurnRequest,
    CreationTaskAssistantTurnResponse,
    CreationTaskAssistantHistoryResponse,
    AssistantStreamEvent,
    MaterialAssistantTurnRequest,
    MaterialAssistantTurnResponse,
    MaterialAssistantResultResponse,
    WorkspaceAssistantPlanTurnRequest,
    WorkspaceAssistantPlanTurnResponse,
    CompoundEditSelectionRequest,
    CreationTaskEditPreviewResponse,
    CreationTaskExactEditRequest,
    CreationTaskEditApplyRequest, CreationTaskEditJobReplayResponse,
    AssistantEditRequestLocator,
    AssistantScriptToolReceipt,
    MediaScriptPreviewRequest,
    TaskMediaJobResponse,
    MediaScriptToolSaveRequest, MediaScriptToolUpdateRequest, MediaScriptToolRevokeRequest, MediaScriptToolPreviewRequest,
    EXACT_EDIT_FIELDS, EXACT_EDIT_ENVELOPE_FIELDS, EXACT_MONTAGE_REQUIRED_FIELDS,
    CreationTaskSubtitleCueUpdateRequest,
    CreationTaskTimelineUndoRequest,
    CreationTaskWorkspaceResponse,
    CreationTasksResponse,
    CreatorProfileLearnResult,
    CreatorProfileResponse,
    CreatorProfileUpdate,
    CreatorPreferenceChangeRequest,
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
    MaterialAssetNotesRequest,
    MaterialAssetNotesResponse,
    MaterialBusinessObjectCreateRequest,
    MaterialBusinessObjectResponse,
    MaterialBusinessObjectsResponse,
    MaterialAssetBusinessObjectsSaveRequest,
    MaterialAssetTagsRequest,
    MaterialIntakeBindRequest,
    MaterialIntakeStatusResponse,
    MaterialCopyCompleteRequest,
    MaterialCopyCompleteResponse,
    MaterialUsageRecordsResponse,
    MediaLinkCheckRequest,
    MediaLinkCheckResponse,
    MediaLinkImportRequest,
    MediaLinkImportResponse,
    MaterialBatchTagsRequest,
    MaterialBatchTagsResponse,
    MaterialCollectionCreateRequest,
    MaterialCollectionResponse,
    MaterialCollectionsResponse,
    MaterialLibraryInsightsResponse,
    MaterialLibraryResponse,
    HomeAggregationResponse,
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
    FixedProfileReference,
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
    KnowledgeFileImportRequest,
    KnowledgeItemDetailResponse,
    KnowledgeItemVersionResponse,
    ContentVisibilityRequest,
    KnowledgeItemVisibilityRequest,
    KnowledgeItemContentRequest,
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
    CoverAssetResponse,
    SubtitleTrackResponse,
    TimelineVersionArtifactResponse,
    TimelineVersionDetailResponse,
    TimelineVersionListResponse,
    TimelineVersionNarrationSegmentResponse,
    TimelineVersionShotResponse,
    TimelineVersionSummaryResponse,
    validate_logical_media_reference,
)
from app.services import EpisodeService
from app.knowledge_files import KnowledgeFileError, parse_knowledge_file
from app.knowledge_grant_routes import knowledge_grant_router
from app.knowledge_grants import VerifiedMemberGrantTargetResolver
from app.platform_auth.service import PlatformAuthService
from app.platform_publish import (
    build_platform_publish_router,
    platform_publish_registration,
    recover_platform_publish_jobs,
)
from app.sources import ObsidianVault
from app.source_types import KnowledgeItem, SourceConnector, SourceItem
from app.source_policy import content_allows_use
from app.source_sync import SourceBoundaryError, SourceSyncFailedError, sync_source_connector

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
    topic_sources_configured,
)
from app.video import FfmpegVideoProcessor, SourceTracker, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
from app.visual_semantics import visual_semantic_provider_from_catalog
from app.voice import transcribe_audio_bytes
from app.voice_clone import voice_clone_provider_from_catalog
from app.workflow import LangGraphEpisodeWorkflow
from app.automation_routes import build_automation_guarded_publish_router
from contextvars import ContextVar
from contextlib import contextmanager
from app.automation_routes import build_automation_router
from app.automation_runtime import AutomationRuntime

app = FastAPI(title="AI视频日记 Agent Studio API")
logger = logging.getLogger(__name__)
app.state.creation_permit_client = None


# Register before every driver that can verify a creation identity.
@app.on_event("startup")
def start_creation_permit_transport() -> None:
    if app.state.creation_permit_client is None:
        app.state.creation_permit_client = httpx.Client(timeout=5.0)


def stop_creation_permit_transport() -> None:
    client = app.state.creation_permit_client
    if client is not None:
        client.close()
        app.state.creation_permit_client = None


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def exact_edit_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.endswith(("/edits/exact/preview", "/edits/exact")):
        # Invalid NaN/Infinity cannot be echoed into JSON. Keep the actionable
        # field/type/message rather than turning a rejected edit into a 500.
        return JSONResponse(status_code=422, content={"detail": [
            {key: value for key, value in error.items() if key not in {"input", "ctx"}}
            for error in exc.errors()]})
    return await request_validation_exception_handler(request, exc)


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


_runtime_reload_lock = threading.RLock()
_assistant_stream_workers: WeakSet[threading.Thread] = WeakSet()


def _publish_runtime_dependencies(candidate: RuntimeDependencies) -> None:
    global settings, repo, source_repository, model_catalog, visual_semantic_provider, voice_clone_provider
    global avatar_lipsync_provider, latentsync_provider, production_repo, conversation_repo, montage_repo
    global quality_check_repo, local_members_service, platform_auth_service, scheduled_task_service
    global montage_planner, montage_renderer, artifact_store, narration_generator, voice_preview_service
    global avatar_preview_service, vault, video_inbox, video_processor, video_pipeline, draft_workspace
    global source_tracker, service, workflow, auth_context, video_job_runner, runtime_env_path
    settings = candidate.settings
    database, models, media, content = candidate.database, candidate.models, candidate.media, candidate.content
    repo, source_repository, production_repo = database.episodes, database.sources, database.production
    conversation_repo, montage_repo, quality_check_repo = database.conversations, database.montage, database.quality
    local_members_service, platform_auth_service = database.members, database.platform_auth
    scheduled_task_service, video_job_runner = database.scheduled_tasks, database.video_jobs
    model_catalog, visual_semantic_provider, voice_clone_provider = models.catalog, models.visual, models.voice
    avatar_lipsync_provider, latentsync_provider = models.avatar, models.latentsync
    montage_planner, montage_renderer, artifact_store = media.planner, media.renderer, media.artifacts
    narration_generator, voice_preview_service, avatar_preview_service = media.narration, media.voice_preview, media.avatar_preview
    video_inbox, video_processor, video_pipeline = media.inbox, media.processor, media.pipeline
    draft_workspace, source_tracker = media.drafts, media.source_tracker
    vault, service, workflow = content.vault, content.episodes, content.workflow
    auth_context = candidate.auth
    runtime_env_path = runtime_env_file_path()
    set_auth_enabled(settings.auth_enabled)


def _current_database_owners() -> DatabaseOwners:
    return DatabaseOwners(repo, source_repository, production_repo, conversation_repo, montage_repo,
        quality_check_repo, local_members_service, platform_auth_service, scheduled_task_service, video_job_runner)


def _initialize_runtime_dependencies() -> ModelInstaller:
    initial_settings = Settings.from_env()
    configure_process_temporary_directory(initial_settings)
    _publish_runtime_dependencies(prepare_runtime_dependencies(initial_settings))
    return ModelInstaller(model_catalog, on_complete=lambda: _reload_runtime_dependencies())


model_installer: ModelInstaller


@app.on_event("startup")
def recover_model_installations() -> None:
    model_installer.recover_interrupted_jobs()


def _lipsync_provider(quality):
    return PersonPreviewUseCase.provider_for_quality(quality,latentsync_provider,avatar_lipsync_provider)


@app.on_event("startup")
def recover_legacy_video_jobs() -> None:
    video_job_runner.recover_after_restart()
    production_repo.interrupt_running_person_renders()


edit_plan_apply_runner = None

_EXTERNAL_URLS_CONFIGURATION_REQUIRED = "configuration_required"

# Endpoints that are true pre-login / local-readiness probes and therefore
# stay public even when Supabase auth is enabled. Everything else requires a
# verified bearer token.
PUBLIC_AUTH_PATHS: set[str] = {
    "/api/setup/preflight",
    "/api/is_setup",
    "/api/system/status",
    "/api/version",
    "/api/workflow/status",
    "/api/content-templates",
}

# Identity-only entries: a first-time invitee accepts a device invite
# before holding any workspace membership. This exact path (POST only) is
# authenticated by identity without workspace resolution (see the middleware).
DEVICE_INVITE_ACCEPT_PATH = "/api/device-invites/accept"
PAIRING_ACCEPT_PATH = "/api/companion/pairings/accept"

# Authenticated users who have not completed onboarding may only use these
# repair endpoints.  The same trusted Python boundary enforces this rule for
# direct localhost requests, not just Next.js-proxied browser traffic.
ONBOARDING_REPAIR_PATHS: set[str] = {
    # Binding facts only; accepting an invite has not initialized the workspace.
    "/api/companion/session",
    "/api/runtime/health",
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
    is_device_accept = request.method == "POST" and path in {DEVICE_INVITE_ACCEPT_PATH, PAIRING_ACCEPT_PATH}
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
            request.state.principal = await run_in_threadpool(auth_context.authenticate_identity, request)
            return await call_next(request)
        # JWT/JWKS, membership, device and onboarding checks use synchronous I/O.
        # Await them in the existing pool; keep request context in this coroutine.
        request.state.principal = await run_in_threadpool(auth_context.authenticate, request)
        principal = request.state.principal
        if WORKSPACE_SELECTION_HEADER in request.headers:
            request.state.companion_identity = await run_in_threadpool(require_companion_identity,
                request, DeviceAccountService(settings, trusted_device_credential()),
            )
        if (
            not is_onboarding_repair_path(path)
            and not await run_in_threadpool(auth_context.is_onboarding_complete,
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
    except DeviceAccountError as exc:
        return JSONResponse(status_code=DEVICE_ACCOUNT_ERROR_STATUS[exc.kind],
            content={"error": exc.kind, "detail": str(exc)})
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


def companion_identity_for(request: Request) -> CompanionIdentity:
    """Public identity dependency for companion upload, session and media adapters."""
    try:
        require_account_principal(request)
        if not auth_context.enabled:
            raise UnauthorizedError("formal account authentication is not configured")
        cached = getattr(request.state, "companion_identity", None)
        if isinstance(cached, CompanionIdentity):
            return cached
        return require_companion_identity(request, DeviceAccountService(settings, trusted_device_credential()))
    except (DeviceAccountError, UnauthorizedError) as exc:
        raise companion_http_error(exc) from exc


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


def _content_reader(request: Request, workspace_id: str) -> tuple[str | None, bool]:
    """解析这次调用的可信内容读取身份：`(user_id, local_diagnostic)`。

    两种既有身份归一成一个 id 参与判据，不新造第二套：
    - 已验证的 Bearer 身份：`request.state.principal.user_id`；
    - 本地成员令牌：`local_members_service.resolve_member` 的 `member_id`。
    令牌无效按 401 处理，不降级成「没带令牌所以按 owner 看全部」。

    `local_diagnostic` 只在保留的 local 工作区、且请求确实没有任何认证身份时为真：
    单机本地正常使用不受影响，但这不是多人隔离——带了无效身份或已开认证的请求
    不得借此看到别人的私有内容。
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.user_id:
        return str(principal.user_id), False

    token = _local_member_token(request)
    if token:
        try:
            member = local_members_service.resolve_member(token)
        except LocalMembersError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        assert member is not None
        return str(member.member_id), False

    if auth_context.enabled:
        # 已开认证却没有可信身份：没有可读身份，不退回共享或 local。
        return None, False
    # 受控诊断只限保留的 local 工作区：本机单人使用照常可读自己的私有内容。
    # 其他 workspace 没有这个范围——那里的匿名请求没有可信身份，读不到私有内容。
    return None, workspace_id == LEGACY_WORKSPACE_ID


def _assistant_member_id(request: Request, workspace_id: str) -> str:
    member_id, _local_diagnostic = _content_reader(request, workspace_id)
    if not member_id:
        raise HTTPException(status_code=403, detail="请先确认当前成员身份，再使用可恢复的对话会话。")
    _remember_goal_transport(request, workspace_id, member_id)
    return member_id


def _assistant_scope_id(kind: str, *, task_id: str | None = None,
                        selection: dict[str, str] | None = None) -> str:
    if kind == "home":
        return "home"
    if kind == "task" and task_id:
        return task_id
    if kind == "material":
        return "library"
    raise HTTPException(status_code=422, detail="对话范围无效。")


def _validate_assistant_scope(request: Request, kind: str, scope_id: str) -> None:
    workspace_id = current_workspace_id(request)
    if kind == "home" and scope_id == "home":
        return
    if kind == "task":
        task = _creation_task_or_404(workspace_id, scope_id)
        if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
            raise HTTPException(status_code=404, detail="creation task not found")
        return
    if kind == "material" and scope_id in {"library", "browse"}:
        return
    if kind == "material" and scope_id.startswith("asset:"):
        asset_id = scope_id.removeprefix("asset:")
        if asset_id:
            get_production_material(asset_id, request)
            return
    if kind == "material" and scope_id.startswith("segment:"):
        parts = scope_id.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            asset = get_production_material(parts[1], request)
            if any(segment.id == parts[2] and segment.asset_id == asset.id for segment in asset.segments):
                return
            raise HTTPException(status_code=404, detail="所选素材片段不存在")
    raise HTTPException(status_code=422, detail="对话范围与当前可见对象不匹配。")


def _content_visible(
    request: Request, workspace_id: str, visibility: str, owner_user_id: str | None
) -> bool:
    """内容可见范围判据，与仓储层共用同一规则（app.production.content_readable）。"""
    user_id, local_diagnostic = _content_reader(request, workspace_id)
    return content_readable(
        user_id=user_id,
        local_diagnostic=local_diagnostic,
        visibility=visibility,
        owner_user_id=owner_user_id,
    )


# 知识授权路由复用本文件既有的身份与工作区解析，不新造第二套：
# - `workspace_for` / `content_reader` 就是上面两个函数，判据与列表、详情、搜索、
#   助手共用同一套 `_content_reader`；
# - 目标解析用 `VerifiedMemberGrantTargetResolver`：这台机器上没有「已验证的同设备
#   成员 → 空间」事实源，所以生产解析返回 unresolved、本地模式返回 same_workspace，
#   响应里如实标注「仅同空间」与 `cross_workspace_status=UNVERIFIED`。
#   真实跨空间端到端仍未验证。
# `local_mode` 取「未启用 Supabase 认证」这一既有事实：此时 `current_workspace_id`
# 只能落到保留的 local 工作区，全部成员共享同一空间，跨空间授权本就不成立。
def _prepare_runtime_routes(database: DatabaseOwners, auth: SupabaseAuthContext):
    router = APIRouter()

    @cache
    def transfers_for():
        try:
            minimum = int(os.environ.get("DEVICE_HOST_MIN_FREE_DISK_BYTES", ""))
            if minimum <= 0:
                raise ValueError("positive disk reserve required")
        except ValueError as exc:
            raise HTTPException(status_code=503, detail={"code": "storage_budget_unavailable",
                "message": "主机接收磁盘余量尚未准备，请先在主机完成设备配置。", "retryable": True}) from exc
        return database.production.mobile_transfer_service(min_free_disk_bytes=minimum)

    def transfer_actor(request: Request) -> MobileTransferActor:
        identity = companion_identity_for(request)
        return MobileTransferActor(identity.workspace_id, identity.member_id, identity.member_id)

    router.include_router(build_mobile_transfer_router(transfers_for=transfers_for, actor_for=transfer_actor))
    router.include_router(build_companion_router(
        accounts_for=lambda: DeviceAccountService(settings, trusted_device_credential()),
        identity_for=companion_identity_for,
        pairings=CompanionPairingService(database.production.database_path),
    ))
    router.include_router(knowledge_grant_router(
        database.sources, workspace_for=current_workspace_id, content_reader=_content_reader,
        target_resolver=VerifiedMemberGrantTargetResolver(local_mode=not auth.enabled),
    ))
    router.include_router(build_automation_guarded_publish_router(build_platform_publish_router(
        database.production, workspace_for=current_workspace_id, content_reader=_content_reader,
        actor_for=lambda request, workspace_id: _require_permit_actor(request, workspace_id),
        auth_service=database.platform_auth,
        task_for_user=lambda workspace_id, task_id, user_id: _platform_publish_task_for_user(
            workspace_id, task_id, user_id,
        ),
        publishable_output_for=lambda workspace_id, task_id, user_id: _platform_publish_output_for(
            workspace_id, task_id, user_id,
        ),
        runtime_for=lambda: _creation_runtime(),
    ), guard=lambda request, task_id: _require_automation_task_open(request, current_workspace_id(request), task_id)))
    return router.routes


def _publish_runtime_routes(routes) -> None:
    global _bound_runtime_routes
    retained = [route for route in app.router.routes if route not in _bound_runtime_routes]
    app.router.routes = [*retained, *routes]
    _bound_runtime_routes = routes
    app.openapi_schema = None


_runtime_routes_insert_at = len(app.router.routes)
_bound_runtime_routes = []


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
    return VideoTemplateUseCase(production_repo).builtin_templates(workspace_id)


def _video_template_or_404(request: Request, workspace_id: str, template_id: str, version: int) -> VideoTemplate:
    case = VideoTemplateUseCase(production_repo)
    item = case.builtin_template(workspace_id, template_id, version)
    if item is not None:
        return item
    try:
        item = production_repo.get_video_template(workspace_id, template_id, version)
        user_id, local_diagnostic = _content_reader(request, workspace_id)
        case.require_readable(item, template_id, reader_user_id=user_id, local_diagnostic=local_diagnostic)
        return item
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="当前工作区找不到这个模板版本。") from exc


@app.put("/api/video-templates/{template_id}/visibility")
def set_video_template_visibility_route(
    template_id: str,
    request: Request,
    payload: ContentVisibilityRequest,
) -> dict[str, object]:
    """显式共享或收回一个用户模板；返回同一列的回读值。"""
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    workspace_id = current_workspace_id(request)
    user_id, _ = _content_reader(request, workspace_id)
    versions = production_repo.list_video_templates(workspace_id, viewer_user_id=user_id)
    previous = next((item for item in versions if item.id == template_id), None)
    if previous is None:
        raise HTTPException(status_code=404, detail="当前工作区找不到这个模板。")
    if user_id is None or previous.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="只有这个模板的所有者可以修改可见范围。")
    try:
        template = production_repo.set_video_template_visibility(
            workspace_id, template_id, payload.visibility
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="video template not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"item": asdict(template)}


@app.get("/api/video-templates")
def list_video_templates(request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    user_id, local_diagnostic = _content_reader(request, workspace_id)
    return {"items": [
        {**asdict(item), "can_edit":item.origin == "user" and user_id is not None and item.owner_user_id == user_id}
        for item in [
            *_builtin_video_templates(workspace_id),
            *production_repo.list_video_templates(
                workspace_id, viewer_user_id=user_id, local_diagnostic=local_diagnostic
            ),
        ]
    ]}


@app.post("/api/video-templates/{template_id}/versions")
def create_video_template_version(template_id: str, request: Request, payload: VideoTemplateVersionRequest) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    user_id, _diagnostic = _content_reader(request, workspace_id)
    previous = _video_template_or_404(request,workspace_id,template_id,payload.expected_version)
    case = VideoTemplateUseCase(production_repo)
    try:
        saved = case.append_version(previous, expected_version=payload.expected_version,
            name=payload.name, style_instruction=payload.style_instruction, user_id=user_id)
    except VideoTemplateRejected as exc:
        status = {"field_builtin_readonly": 409, "field_not_owner": 403, "persist_conflict": 409}[exc.code]
        failure = HTTPException(status_code=status, detail=exc.message)
        if exc.__cause__ is not None:
            raise failure from exc.__cause__
        raise failure from None
    return {**asdict(saved),"can_edit":True}


@app.post("/api/creation-tasks/{task_id}/templates")
def save_creation_task_template(task_id: str, request: Request, payload: SaveVideoTemplateRequest) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    case = VideoTemplateUseCase(production_repo)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    try:
        montage = case.rendered_source(workspace_id, task_id, task)
        try:
            output = _creation_task_output_path(workspace_id, task_id, subtitle=False)
            case.require_nonempty_output(output)
        except (HTTPException, OSError) as exc:
            raise HTTPException(status_code=409, detail="当前成片文件不可用，请恢复文件或重新渲染后再保存模板。") from exc
        case.check_save_target(payload.template_id, payload.expected_version)
        if payload.template_id:
            previous = _video_template_or_404(request, workspace_id, payload.template_id, payload.expected_version)
            case.check_save_template_origin(previous)
            reader_id, _ = _content_reader(request, workspace_id)
            case.require_save_owner(previous, reader_id=reader_id)
        tracks = _current_subtitle_tracks(workspace_id, task, montage=montage)
        template = case.build_from_task(workspace_id, task_id, task, plan, montage, tracks,
            name=payload.name, style_instruction=payload.style_instruction,
            template_id=payload.template_id, expected_version=payload.expected_version)
        # Keep both trusted reads and their workspace resolution at the original late point.
        _, template_local_diagnostic = _content_reader(request, current_workspace_id(request))
        try:
            return asdict(case.persist_task_template(template,
                verified_owner_user_id=_content_reader(request, current_workspace_id(request))[0],
                local_diagnostic=template_local_diagnostic))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VideoTemplateRejected as exc:
        status = {"task_not_rendered": 409, "target_pair_incomplete": 422,
            "task_builtin_overwrite": 409, "task_not_owner": 403, "persist_conflict": 409}[exc.code]
        failure = HTTPException(status_code=status, detail=exc.message)
        if exc.__cause__ is not None:
            raise failure from exc.__cause__
        raise failure from None


@app.get(
    "/api/production/foundation",
    response_model=ProductionFoundationResponse,
)
def get_production_foundation(request: Request) -> ProductionFoundationResponse:
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    profile = production_profile_with_model_status(workspace_id)
    return ProductionFoundationResponse(
        profile=ProductionProfileResponse.model_validate(
            profile,
            from_attributes=True,
        ),
        readiness=production_repo.readiness(
            workspace_id, profile=profile, viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
            topic_sources_configured=topic_sources_configured(settings, vault),
        ),
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


def _profile_read_facts(profile, request: Request):
    user_id, diagnostic = _content_reader(request, profile.workspace_id)
    current = production_repo.get_person_profile(profile.workspace_id, profile.id) if isinstance(profile, PersonProfile) else production_repo.get_versioned_voice_profile(profile.workspace_id, profile.id)
    if any(not production_repo.profile_readable(item, user_id, diagnostic) for item in (profile, current)):
        raise HTTPException(status_code=404, detail="profile not found")
    reference_reason = None
    try:
        path, _, _ = production_repo._profile_reference_source(profile, viewer_user_id=user_id,
            local_diagnostic=diagnostic, for_generation=False)
    except (ValueError, PermissionError, OSError, KeyError) as exc:
        path, reference_reason = None, str(exc)
    reason = profile_use_failure(current, content_type="person_likeness" if isinstance(profile, PersonProfile) else "voice",
        usage_scope="avatar_lipsync" if isinstance(profile, PersonProfile) else "narration") or reference_reason
    if reason is None:
        try:
            production_repo.profile_reference_input(profile, viewer_user_id=user_id, local_diagnostic=diagnostic)
        except (ValueError, PermissionError, OSError, KeyError) as exc:
            reason = str(exc)
    authorization = production_repo.current_profile_authorization(profile)
    return path, reference_reason, reason, authorization


def _person_profile_response(person: PersonProfile, request: Request) -> PersonProfileResponse:
    path, reference_reason, reason, authorization = _profile_read_facts(person, request)
    return PersonProfileResponse(id=person.id, workspace_id=person.workspace_id, display_name=person.display_name,
        version=person.version, avatar_reference=person.avatar_reference,
        background_capabilities=list(person.background_capabilities), shot_capabilities=list(person.shot_capabilities),
        authorization=_authorization_response(person.authorization) if person.authorization else None,
        current_authorization=_authorization_response(authorization) if authorization else None,
        runnable=reason is None, runnable_reason=reason, body_reference_kind="workspace",
        body_reference_name=path.name if path else None, body_reference_available=path is not None,
        body_reference_reason=reference_reason, body_reference_repair_action="重新定位来源并保存新版本。" if reference_reason else None,
        reference=asdict(person.reference) if person.reference else None, use_state=person.use_state,
        reference_issue=person.reference_issue, reference_url=f"/api/media/person-profiles/{quote(person.id, safe='')}/reference?version={quote(person.version, safe='')}" if path else None)


def _versioned_voice_profile_response(voice: VersionedVoiceProfile, request: Request) -> VersionedVoiceProfileResponse:
    path, reference_reason, reason, authorization = _profile_read_facts(voice, request)
    return VersionedVoiceProfileResponse(id=voice.id, workspace_id=voice.workspace_id,
        display_name=voice.display_name, person_profile_id=voice.person_profile_id, person_version=voice.person_version,
        version=voice.version, language=voice.language, reference_audio_path=voice.reference_audio_path,
        reference_text=voice.reference_text, model_id=voice.model_id,
        authorization=_authorization_response(voice.authorization) if voice.authorization else None,
        current_authorization=_authorization_response(authorization) if authorization else None,
        runnable=reason is None, runnable_reason=reason, reference=asdict(voice.reference) if voice.reference else None,
        use_state=voice.use_state, reference_issue=voice.reference_issue,
        reference_url=f"/api/media/voice-profiles/{quote(voice.id, safe='')}/reference?version={quote(voice.version, safe='')}" if path else None)


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


def _readable_profiles(request: Request, items):
    user_id, diagnostic = _content_reader(request, current_workspace_id(request))
    return [item for item in items if production_repo.profile_readable(item, user_id, diagnostic)]


@app.get("/api/media/default-voice", response_model=VersionedVoiceProfileResponse | None)
def get_default_media_voice(request: Request):
    voice = production_repo.get_default_versioned_voice(current_workspace_id(request))
    user, diagnostic = _content_reader(request, current_workspace_id(request))
    return _versioned_voice_profile_response(voice, request) if voice and production_repo.profile_readable(voice, user, diagnostic) else None


@app.put("/api/media/default-voice", response_model=VersionedVoiceProfileResponse)
def set_default_media_voice(request: Request, payload: FixedProfileReference):
    selected = payload
    ws = current_workspace_id(request)
    user, diagnostic = _content_reader(request, ws)
    try:
        voice = production_repo.set_default_versioned_voice(ws, selected.profile_id, selected.version,
            viewer_user_id=user, local_diagnostic=diagnostic)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _versioned_voice_profile_response(voice, request)


@app.get("/api/media/{profile_kind}/{profile_id}/reference")
def stream_profile_reference(profile_kind: Literal["person-profiles", "voice-profiles"], profile_id: str, request: Request, version: str):
    ws = current_workspace_id(request)
    try:
        profile = (production_repo.get_person_profile(ws, profile_id, version) if profile_kind == "person-profiles"
                   else production_repo.get_versioned_voice_profile(ws, profile_id, version))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile not found") from exc
    path, reason, _, _ = _profile_read_facts(profile, request)
    if path is None:
        raise HTTPException(status_code=409, detail=reason)
    return FileResponse(path)


@app.get("/api/media/person-profiles", response_model=PersonProfilesResponse)
def list_media_person_profiles(request: Request) -> PersonProfilesResponse:
    people = _readable_profiles(request, production_repo.list_person_profiles(current_workspace_id(request)))
    return PersonProfilesResponse(
        items=[_person_profile_response(item, request) for item in people],
        total_count=len(people),
    )


def _profile_save_use_state(workspace_id, payload, *, person):
    if "use_state" in payload.model_fields_set:
        return payload.use_state
    try:
        previous = (production_repo.get_person_profile(workspace_id, payload.id) if person
                    else production_repo.get_versioned_voice_profile(workspace_id, payload.id))
    except KeyError:
        return payload.use_state
    return previous.use_state


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
                version=payload.version or f"v-{uuid4().hex}",
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
                authorization=_authorization_from_request(workspace_id, payload.authorization) if payload.authorization else None,
                reference=ProfileReference(**payload.reference.model_dump()) if payload.reference else ProfileReference(),
                use_state=_profile_save_use_state(workspace_id, payload, person=True),
            ), verified_owner_user_id=_content_reader(request, workspace_id)[0],
            local_diagnostic=_content_reader(request, workspace_id)[1],
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise _media_write_error(exc) from exc
    return _person_profile_response(saved, request)


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
    return _person_profile_response(person, request)


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
    items = _readable_profiles(request, production_repo.list_person_profile_versions(workspace_id, person_id))
    return PersonProfilesResponse(
        items=[_person_profile_response(item, request) for item in items],
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
                content, verified_owner_user_id=_content_reader(request, current_workspace_id(request))[0],
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


def _member_owned_content_scope(request: Request) -> tuple[str, str]:
    workspace_id = current_workspace_id(request)
    owner, _diagnostic = _content_reader(request, workspace_id)
    if not owner:
        raise HTTPException(status_code=403, detail="请先确认当前成员身份，再操作私人素材。")
    return workspace_id, owner


@app.post("/api/media/voice-sampling-tasks", response_model=VoiceSamplingTask, status_code=201)
def create_voice_sampling_task(request: Request, payload: VoiceSamplingTaskCreateRequest):
    workspace_id, owner = _member_owned_content_scope(request)
    try:
        return production_repo.voice_sampling.create(
            workspace_id, owner, request_id=str(payload.request_id),
            display_name=payload.display_name, language=payload.language, purpose=payload.purpose,
        )
    except VoiceSamplingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/media/voice-sampling-tasks")
def list_voice_sampling_tasks(request: Request):
    workspace_id, owner = _member_owned_content_scope(request)
    items = production_repo.voice_sampling.list(workspace_id, owner)
    return {"items": [asdict(item) for item in items], "total_count": len(items)}


@app.get("/api/media/voice-sampling-tasks/{task_id}", response_model=VoiceSamplingTask)
def get_voice_sampling_task(task_id: str, request: Request):
    workspace_id, owner = _member_owned_content_scope(request)
    try:
        return production_repo.voice_sampling.get(workspace_id, owner, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="声音采样任务不存在或当前不可访问。") from exc


@app.post("/api/media/voice-sampling-tasks/{task_id}/sample", response_model=VoiceSamplingTask)
async def receive_voice_sampling_sample(
    task_id: str, request: Request,
    request_id: UUID = Form(...), text_version: str = Form(...),
    purpose: Literal["voice_profile"] = Form(...),
    source_device: Literal["desktop", "import", "mobile"] = Form(...),
    audio: UploadFile = File(...),
):
    if source_device == "mobile":
        companion_identity_for(request)
    workspace_id, owner = _member_owned_content_scope(request)
    try:
        # Resolve ownership before reading an uploaded sample into memory.
        service = production_repo.voice_sampling
        service.get(workspace_id, owner, task_id)
        content = await audio.read(MAX_SAMPLE_BYTES + 1)
        return service.receive(
            workspace_id, owner, task_id, request_id=str(request_id), text_version=text_version,
            purpose=purpose, source_device=source_device, content=content,
            model_id=voice_clone_provider.model_id if voice_clone_provider.ready else "",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="声音采样任务不存在或当前不可访问。") from exc
    except VoiceSamplingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="声音样本未保存，请检查原素材目录和磁盘后重试。") from exc


@app.get("/api/media/voice-profiles", response_model=VersionedVoiceProfilesResponse)
def list_media_voice_profiles(request: Request) -> VersionedVoiceProfilesResponse:
    voices = _readable_profiles(request, production_repo.list_versioned_voice_profiles(current_workspace_id(request)))
    return VersionedVoiceProfilesResponse(
        items=[_versioned_voice_profile_response(item, request) for item in voices],
        total_count=len(voices),
    )


@app.post("/api/media/voice-profiles", response_model=VersionedVoiceProfileResponse)
def save_media_voice_profile(
    request: Request,
    payload: VersionedVoiceProfileUpsertRequest,
) -> VersionedVoiceProfileResponse:
    workspace_id = current_workspace_id(request)
    person_pair = (payload.person_profile_id, payload.person_version)
    if not {"person_profile_id", "person_version"} & payload.model_fields_set:
        try:
            previous = production_repo.get_versioned_voice_profile(workspace_id, payload.id)
            person_pair = (previous.person_profile_id, previous.person_version)
        except KeyError:
            pass  # A new independent voice has no historical person restriction.
    try:
        saved = production_repo.save_versioned_voice_profile(
            VersionedVoiceProfile(
                id=_required_media_text(payload.id, "voice profile id"),
                workspace_id=workspace_id,
                person_profile_id=person_pair[0],
                person_version=person_pair[1],
                version=payload.version or f"v-{uuid4().hex}",
                language=payload.language,
                reference_audio_path=validate_logical_media_reference(
                    payload.reference_audio_path,
                    field="reference audio path",
                    allowed_schemes={"asset", "workspace"},
                ),
                reference_text=payload.reference_text,
                display_name=payload.display_name,
                model_id=payload.model_id,
                authorization=_authorization_from_request(workspace_id, payload.authorization) if payload.authorization else None,
                reference=ProfileReference(**payload.reference.model_dump()) if payload.reference else ProfileReference(),
                use_state=_profile_save_use_state(workspace_id, payload, person=False),
            ), verified_owner_user_id=_content_reader(request, workspace_id)[0],
            local_diagnostic=_content_reader(request, workspace_id)[1],
        )
    except (KeyError, ValueError, PermissionError) as exc:
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail="person profile not found") from exc
        raise _media_write_error(exc) from exc
    return _versioned_voice_profile_response(saved, request)


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
    return _versioned_voice_profile_response(voice, request)


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
    items = _readable_profiles(request, production_repo.list_versioned_voice_profile_versions(
        workspace_id, voice_profile_id
    ))
    return VersionedVoiceProfilesResponse(
        items=[_versioned_voice_profile_response(item, request) for item in items],
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
        preview_text=run.preview_input.get("preview_text") if isinstance(run.preview_input.get("preview_text"), str) else None,
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
def request_voice_preview(request: Request, payload: VoicePreviewRequest) -> MediaPreviewRunResponse:
    ws = current_workspace_id(request)
    user, diagnostic = _content_reader(request, ws)
    actor = _require_permit_actor(request, ws)
    runtime = _creation_runtime()
    try:
        params = payload.model_dump()
        run = voice_preview_service.request(repository=production_repo, workspace_id=ws,
            viewer_user_id=user, local_diagnostic=diagnostic, prepare_only=True, member_identity=actor.identity_payload(), **params)
        if run.status == "queued" and not run.device_request_id:
            from app.profile_preview_execution import submit_profile_preview
            run = submit_profile_preview(runtime, run, actor, params)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="voice profile not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _media_preview_response(run)


@app.post(
    "/api/media/previews/avatar-lipsync",
    response_model=MediaPreviewRunResponse,
)
def request_avatar_lipsync_preview(request: Request, payload: AvatarLipSyncPreviewRequest) -> MediaPreviewRunResponse:
    ws = current_workspace_id(request)
    actor = _require_permit_actor(request, ws)
    runtime = _creation_runtime()
    voice_run = production_repo.get_media_preview_run(ws, "voice", payload.voice_preview_identity)
    if voice_run is None or not _preview_readable(voice_run, request):
        raise HTTPException(status_code=404, detail="voice preview not found")
    audio_path = _preview_output_path(voice_run)
    if voice_run.status != "succeeded" or audio_path is None:
        raise _preview_precondition_error(error="voice_preview_not_succeeded", detail="需要当前成员已成功的声音预览。",
            impact="未开始人物预览。", repair_action="先完成同一固定声音版本的独立试听。")
    user, diagnostic = _content_reader(request, ws)
    params = dict(person_id=payload.person_profile_id, person_version=payload.person_version,
        voice_profile_id=payload.voice_profile_id, voice_version=payload.voice_version,
        audio_path=str(audio_path), audio_cache_key=voice_run.identity, language=voice_run.language,
        motion=payload.motion, expression=payload.expression, authorization_id=payload.authorization_id,
        authorization_version=payload.authorization_version, model_id=payload.model_id, retry=payload.retry)
    try:
        run = avatar_preview_service.request(repository=production_repo, workspace_id=ws,
            viewer_user_id=user, local_diagnostic=diagnostic, prepare_only=True, member_identity=actor.identity_payload(), **params)
        if run.status == "queued" and not run.device_request_id:
            from app.profile_preview_execution import submit_profile_preview
            run = submit_profile_preview(runtime, run, actor, params)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="media preview dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _media_preview_response(run)


def _preview_readable(run, request: Request):
    user, diagnostic = _content_reader(request, run.workspace_id)
    if not run.member_identity:
        return diagnostic
    original = CreationExecutionActor.from_event(run.workspace_id, run.member_identity)
    return user == (original.user_id or original.member_id)


@app.post("/api/media/previews/{preview_type}/{identity}/cancel", response_model=MediaPreviewRunResponse)
def cancel_media_preview(preview_type: str, identity: str, request: Request):
    ws = current_workspace_id(request)
    actor = _require_permit_actor(request, ws)
    run = production_repo.get_media_preview_run(ws, preview_type, identity)
    if run is None or not _preview_readable(run, request):
        raise HTTPException(status_code=404, detail="media preview not found")
    if run.status not in {"queued", "running"} or not run.device_request_id:
        raise HTTPException(status_code=409, detail="预览不在可取消的设备请求状态")
    runtime = _creation_runtime()
    result = runtime.scheduler.cancel(run.device_request_id, actor)
    runtime.registrations["media_preview"].executor.request_cancel(run.device_request_id)
    if result["state"] == "cancelled":
        run = production_repo.save_media_preview_run(replace(run, status="cancelled", finished_at=utc_now(),
            output_path=None, reason="cancelled", impact="预览已取消，参考和输入保留。", repair_action="需要时显式重新预览。"))
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
        if not _preview_readable(run, request):
            continue
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
    runs = [run for run in production_repo.list_media_preview_attempts(workspace_id, preview_type, identity) if _preview_readable(run, request)]
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
    if run is None or not _preview_readable(run, request):
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
        update={
            "workspace_reference": _material_workspace_reference(asset),
            "source_url": asset.source_url or None,
        }
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




def _creation_plan_preflight(
    request: Request,
    workspace_id: str,
    payload: CreationPlanPreviewRequest | CreationPlanConfirmRequest,
    *, automation_scope: dict[str, list[str]] | None = None,
) -> CreationPlan:
    values = CreationPlanPreflightInput(
        goal=payload.goal, name=payload.name, target_count=payload.target_count,
        language=payload.language, aspect_ratio=payload.aspect_ratio,
        duration_target_seconds=payload.duration_target_seconds,
        presenter_share_target=payload.presenter_share_target, lipsync_quality=payload.lipsync_quality,
        template_id=payload.template_id, template_version=payload.template_version,
        selected_knowledge=(payload.selected_knowledge.knowledge_item_id, payload.selected_knowledge.version) if payload.selected_knowledge is not None else None,
        selected_voice=(payload.selected_voice.profile_id, payload.selected_voice.version) if payload.selected_voice is not None else None,
        selected_person=(payload.selected_person.profile_id, payload.selected_person.version) if payload.selected_person is not None else None,
        provided_fields=frozenset(payload.model_fields_set),
    )
    case = CreationPlanPreflightUseCase(production_repo, source_repository, model_catalog,
        lipsync_provider_for=_lipsync_provider)
    try:
        case.check_template_selection(values)
        template = (_video_template_or_404(request, workspace_id, values.template_id, values.template_version)
                    if values.template_id is not None else None)
        values = case.inherit_template(values, template)
        user_id, local_diagnostic = _content_reader(request, workspace_id)
        return case.prepare(workspace_id, values, template=template,
            reader_user_id=user_id, local_diagnostic=local_diagnostic, automation_scope=automation_scope)
    except CreationPlanPreflightRejected as exc:
        status = {"template_version_required": 422, "template_id_required": 422,
            "knowledge_outside_automation_scope": 409, "knowledge_version_unreadable": 404,
            "knowledge_automation_forbidden": 409, "knowledge_reference_forbidden": 409}[exc.code]
        failure = HTTPException(status_code=status, detail=exc.message)
        if exc.__cause__ is not None:
            raise failure from exc.__cause__
        raise failure from None



def _creation_plan_preview_response(plan: CreationPlan) -> CreationPlanPreviewResponse:
    return CreationPlanPreviewResponse.model_validate(
        {
            "creation_intent": plan.creation_intent,
            "adoption": plan.adoption,
            "updated_at": plan.updated_at,
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
            "selected_person": asdict(plan.selected_person) if plan.selected_person else None,
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
            # 回读列上的当前可见范围：写进去的和读出来的必须是同一个值。
            "visibility": plan.visibility,
            "status": plan.status,
            "creation_task": project_task_summary(production_repo, task).summary,
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


def _topic_route_snapshot(workspace_id: str) -> dict[str, object]:
    """按当前工作区的文字服务配置判断可用性。

    读全局设置会把配置正确的工作区判成「未配置」，所以这里只用该工作区的配置。
    """
    settings_for_workspace = _workspace_settings_for(workspace_id)
    provider_kind = settings_for_workspace.effective_llm_provider
    base_url = settings_for_workspace.effective_llm_base_url
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
    availability = "unverified" if settings_for_workspace.llm_configured else "unconfigured"
    connection_version = hashlib.sha256(
        f"{provider_kind}|{endpoint_identity}|{settings_for_workspace.effective_llm_model}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "provider_kind": provider_kind,
        "execution_location": execution_location,
        "model_id": settings_for_workspace.effective_llm_model,
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
            item = source_repository.get_knowledge_reference_readable_by(
                workspace_id, reference.id, reference.version,
                user_id=task.owner_user_id,
                local_diagnostic=task.owner_user_id is None and workspace_id == LEGACY_WORKSPACE_ID,
            )
        except (KeyError, ValueError) as exc:
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
        if (not content_allows_use(item.use_state, "reference")
                or item.use_state == "not_for_automation"):
            raise _CreationTaskTopicPreflightError(
                reason="knowledge_use_restricted",
                impact="知识资料的使用范围不允许用于本次创作参考。",
                repair_action="调整资料使用范围，或从当前方案移除此资料后重新预检。",
                retryable=True,
            )
        text = _topic_text_excerpt(item.body)
        if not text:
            raise _CreationTaskTopicPreflightError(
                reason="knowledge_text_unavailable",
                impact="知识资料没有可发送的文字摘录，未调用文字模型。",
                repair_action="补充可读取的文字内容后重新预检；本机文件路径不会发送给模型。",
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
            # 真实回查证据：网页快照才带原网址和抓取时间；文件与手填条目为空。
            # 不编造链接，也不拿当前版冒充引用当时那一版。
            "source_url": _safe_topic_source_url(item.source_url),
            "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
            "content_hash": item.content_hash,
        }
    for reference in plan.source_references:
        try:
            item = source_repository.get_source_item_for_use(
                workspace_id, reference.id, purpose="reference"
            )
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
            or source_item_identity(item) != reference.identity
        ):
            raise _CreationTaskTopicPreflightError(
                reason="source_reference_stale",
                impact="方案中的信息源身份或许可已变化，未调用文字模型。",
                repair_action="重新执行内容预检并确认最新信息源条目。",
                retryable=True,
            )
        if (not content_allows_use(item.use_state, "reference")
                or item.use_state == "not_for_automation"):
            raise _CreationTaskTopicPreflightError(
                reason="source_use_restricted",
                impact="信息源的使用范围不允许用于本次创作参考。",
                repair_action="调整资料使用范围，或从当前方案移除此来源后重新预检。",
                retryable=True,
            )
        license_value = item.license.strip()
        text = _topic_text_excerpt(item.body or item.subtitles)
        if not text:
            raise _CreationTaskTopicPreflightError(
                reason="source_text_unavailable",
                impact="信息源没有可发送的文字摘录，未调用文字模型。",
                repair_action="补充有可用正文或字幕的来源后重新预检。",
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
                "version_or_identity": source_item_identity(item),
                "title": item.title,
            }
        )
        evidence_by_reference_id[reference_id] = {
            "kind": "source",
            "reference_id": item.id,
            "version_or_identity": source_item_identity(item),
            "title": item.title,
            "authorization_or_license": license_value,
            "source_url": _safe_topic_source_url(item.source_url),
        }
    if not provider_sources:
        raise _CreationTaskTopicPreflightError(
            reason="content_sources_missing",
            impact="当前方案没有可发送的已授权文字资料，未调用文字模型。",
            repair_action="保存有正文且允许本次使用的知识或信息源后重新预检。",
            retryable=True,
        )
    return tuple(provider_sources), input_references, evidence_by_reference_id


def _creation_task_topic_generator_for(settings_for_workspace: Settings) -> OpenAICompatibleCreationTaskTopicGenerator:
    return OpenAICompatibleCreationTaskTopicGenerator(
        base_url=settings_for_workspace.effective_llm_base_url,
        model=settings_for_workspace.effective_llm_model,
        api_key=settings_for_workspace.effective_llm_api_key,
    )


def _creation_task_script_generator_for(settings_for_workspace: Settings) -> OpenAICompatibleCreationTaskScriptGenerator:
    return OpenAICompatibleCreationTaskScriptGenerator(
        base_url=settings_for_workspace.effective_llm_base_url,
        model=settings_for_workspace.effective_llm_model,
        api_key=settings_for_workspace.effective_llm_api_key,
    )


def _workspace_settings_for(workspace_id: str) -> Settings:
    """当前工作区的文字服务配置：快照 + 本工作区钥匙串里的密钥。

    全局 dotenv 里的旧明文密钥不会叠进来，删除过的密钥也不会被旧字段叫回来。
    """
    return workspace_settings(workspace_id, settings).settings


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
    route_snapshot = route_snapshot or _topic_route_snapshot(workspace_id)
    latest_run = production_repo.latest_topic_generation_run(workspace_id, task.id)
    route_snapshot = _projected_topic_route_snapshot(route_snapshot, latest_run)
    candidates = production_repo.list_topic_candidates(workspace_id, task.id)
    evidence_references = production_repo.list_topic_evidence_references(workspace_id, task.id)
    cloud_egress = route_snapshot["execution_location"] == "cloud"
    return CreationTaskTopicsResponse.model_validate(
        {
            "task": project_task_summary(production_repo, task).summary,
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
    if latest_run is not None and latest_run.run_kind != "generation":
        latest_run = None
    route_snapshot = _projected_topic_route_snapshot(_topic_route_snapshot(workspace_id), latest_run)
    return CreationTaskScriptsResponse.model_validate(
        {
            "task": project_task_summary(production_repo, task).summary,
            "scripts": [asdict(script) for script in production_repo.list_creation_task_scripts(workspace_id, task.id)],
            "latest_run": latest_run.__dict__ if latest_run is not None else None,
            "route_preflight": route_snapshot,
        }
    )


def _creation_task_narration_response(
    task: CreationTask,
    artifact: NarrationArtifactRecord,
) -> CreationTaskNarrationResponse:
    if artifact.task_id != task.id:
        raise ValueError("配音产物与当前创作任务不一致")
    segments = production_repo.list_narration_segments(
        task.workspace_id, task.id, artifact.script_id
    )
    return CreationTaskNarrationResponse.model_validate(
        {
            "task": project_task_summary(production_repo, task).summary,
            "script_id": artifact.script_id,
            "audio_reference": production_repo.narration_artifact_reference(artifact.cache_key),
            "duration_seconds": artifact.duration_seconds,
            "sample_rate": artifact.sample_rate,
            "model_id": artifact.model_id,
            "voice_profile_id": artifact.voice_profile_id,
            "source_kind": artifact.source_kind,
            "source_artifact_ref": artifact.source_artifact_ref,
            "source_sha256": artifact.source_sha256,
            "source_script_digest": artifact.source_script_digest,
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
    return _picture_preview_renderer().portrait_processor(workspace_id)




def _exact_edit_access(task_id, request, *, write=False):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    member_id = _assistant_member_id(request, workspace_id)
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="creation task not found")
    if write and task.owner_user_id and task.owner_user_id != member_id:
        raise HTTPException(status_code=403, detail="当前成员不能修改此任务")
    return task, member_id


def _require_assistant_input_origin(request, source):
    if source.get("assistant_turn_id") and (request.headers.get("X-Assistant-Input-ID") or None) != source.get("input_id"):
        raise HTTPException(status_code=409, detail="助手输入已变化，旧请求不能归入新要求；请查询原请求回执")


def _prepare_exact_edit_request(task_id,request,payload):
    if not payload.model_fields_set & EXACT_EDIT_ENVELOPE_FIELDS:
        return payload,None
    task,member_id=_exact_edit_access(task_id,request,write=True)
    case=_exact_preview_use_case()
    try:
        source,version=case.typed_source(task,payload,member_id)
        actor=_request_permit_actor(request,task.workspace_id) if auth_context.enabled else CreationExecutionActor(task.workspace_id,member_id)
        clean,source=case.complete_typed(task,payload,member_id,actor,source,version)
        _require_assistant_input_origin(request,source)
        source['source_facts']=_exact_edit_source_facts(task,clean,request)
        return clean,source
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _exact_edit_source_facts(task,payload,request):
    case=_exact_preview_use_case()
    try:
        if payload.operation_type == 'EditAudioTimeline':
            graph=case.audio_source_graph(task,payload)
            actor=_require_permit_actor(request,task.workspace_id)
            return case.audio_source_facts(task,graph,actor)
        ids=payload.clip_ids if payload.operation_type == 'AdjustVisualEffects' else [payload.clip_id] if payload.clip_id else []
        if not ids:return []
        reader,diagnostic=_content_reader(request,task.workspace_id)
        return case.source_facts(task,payload,{'member_id':reader,'local_diagnostic':diagnostic},None)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except (KeyError,ValueError,OSError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


def _audio_mix_request(payload, base_version_id):
    return AudioEditRequest(base_version_id=base_version_id, action=payload.audio_edit.action,
        target_id=payload.audio_edit.target_id, values=payload.audio_edit.values.model_dump(mode="json", exclude_unset=True))


def _claim_edit_request(task,member_id,request_id,phase,inputs):
    try:return _exact_preview_use_case().claim_request(task,member_id,request_id,phase,inputs)
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _save_edit_request_result(task, event_id, result):
    status = result.status_code if isinstance(result, JSONResponse) else 200
    content = json.loads(result.body) if isinstance(result, JSONResponse) else result.model_dump(mode="json")
    _exact_preview_use_case().record_request_result(task, event_id, status_code=status, response=content)
    _wake_assistant_continuations()


def _read_edit_request_result(task, event_id):
    recorded = _exact_preview_use_case().read_request_result(task, event_id)
    return JSONResponse(status_code=recorded["status_code"], content=recorded["response"]) if recorded is not None else None


@app.get("/api/creation-tasks/{task_id}/assistant/edit-requests/{request_id}")
def get_exact_edit_request_result(task_id: str, request_id: UUID, request: Request, phase: Literal["preview", "apply", "script", "observation"] = "preview"):
    return _goal_request_result(task_id, request_id, request, phase)




# Nested public helpers bubble the media boundary to the original operation,
# preserving its accepted assistant instruction and selection.




def _output_observation_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    audio = TaskAudio(repo, artifact_store, settings.effective_temporary_root_path)
    return OutputObservationUseCase(repo, artifact_store, audio,
        settings.effective_temporary_root_path, settings.native_ocr_worker_path)


def _audio_edit_use_case(repository=None):
    repo=repository if repository is not None else production_repo
    audio=TaskAudio(repo,artifact_store,settings.effective_temporary_root_path)
    metadata=MetadataEditUseCase(repo,artifact_store,output_root=Path(settings.video_output_path),audio_service=audio)
    return AudioEditUseCase(repo,audio,metadata,creation_execution_mode)


def _preview_audio_edit_request(task_id, request, payload, *, original_clip=False, queue_on_required=False):
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    actor=_request_permit_actor(request,workspace_id)
    draft=request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact')
    case=_audio_edit_use_case()
    source=EXACT_EDIT_SOURCE.get()
    try:
        if original_clip:
            result=case.original_clip(workspace_id,task_id,payload.model_dump(mode='json'),actor,draft_only=draft,source=source)
        else:
            result=case.preview(workspace_id,task_id,payload,actor,draft_only=draft,source=source)
        return _edit_preview_response(result.intent,result.proposal)
    except AudioEditRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except TaskMediaRequired as required:
        if not queue_on_required:
            raise
        actor=_require_permit_actor(request,workspace_id)
        values={'payload':payload.model_dump(mode='json',exclude_unset=True),'_media_kind':required.kind,'_draft':draft}
        runtime=_creation_runtime();media=runtime.registrations['creation_job'].owner.task_media
        try:
            if task.montage_plan_id and task.current_timeline_version_id is None:
                media.facts(workspace_id,task_id,'audio_edit_preview',values,actor)
                if media.verify_identity(actor)!='ready':raise PermissionError('媒体处理身份失效或无法复核')
                plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
                base=_ensure_montage_base_version(workspace_id,task,plan)
                values['payload']['base_version_id']=base.id
            job=media.submit(runtime,actor,task_id,'audio_edit_preview',values,'edit_preview')
            receipt=_task_media_job_response(media,job,actor,runtime);receipt['result']=None
            return JSONResponse(status_code=202,content=receipt)
        except PermissionError as exc:
            raise HTTPException(status_code=403,detail=str(exc)) from exc
        except (ValueError,KeyError) as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc


def _audio_ingress_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    audio = TaskAudio(repo, artifact_store, settings.effective_temporary_root_path)
    return AudioIngressUseCase(repo, audio, settings.effective_temporary_root_path / 'audio-waveforms')


def _submit_audio_ingress(task_id, request, operation, result_kind, *, audio=None, arguments=None):
    workspace_id = current_workspace_id(request)
    if audio is None:
        _creation_task_or_404(workspace_id, task_id)
    actor = _require_permit_actor(request, workspace_id)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403, detail='当前成员不能安排此任务的媒体处理')
    if audio is not None and cpu_audio_budget_from_env() is None:
        raise HTTPException(status_code=503, detail='CPU 音频处理预算尚未标定，未接收媒体作业')
    values = {'_media_kind': 'upload' if audio is not None else 'audio_mix' if operation == 'audio_listen' else 'waveform', '_draft': request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'), **(arguments if arguments is not None else {})}
    created_input = None
    try:
        if audio is not None:
            suffix = Path(audio.filename or '').suffix.lower()
            allowed = {'.wav', '.mp3', '.m4a', '.aac', '.flac'} if operation == 'audio_import' else {'.webm', '.mp4', '.m4a', '.ogg', '.wav'}
            if suffix not in allowed:
                raise HTTPException(status_code=422, detail='当前上传音频格式不可用')
            root = settings.effective_temporary_root_path / 'task-media-inputs' / media_digest([workspace_id, actor.identity_payload()])
            root.mkdir(parents=True, exist_ok=True)
            partial = root / (uuid4().hex + '.part')
            limit = (512 if operation == 'audio_import' else 50) * 1024 * 1024
            total, digest = (0, hashlib.sha256())
            try:
                with partial.open('xb') as target:
                    while (chunk := audio.file.read(1024 * 1024)):
                        total += len(chunk)
                        if total > limit:
                            raise HTTPException(status_code=422, detail='上传音频超过本次大小上限')
                        target.write(chunk)
                        digest.update(chunk)
                if total == 0:
                    raise HTTPException(status_code=422, detail='上传音频为空')
                staged = root / (digest.hexdigest() + suffix)
                if staged.is_symlink():
                    raise HTTPException(status_code=422, detail='音频暂存位置不可用')
                try:
                    os.link(partial, staged)
                except FileExistsError:
                    if staged.stat().st_size != total:
                        raise HTTPException(status_code=409, detail='已保存的固定音频输入发生变化，请检查本机存储')
                else:
                    created_input = staged
            finally:
                partial.unlink(missing_ok=True)
            values['audio'] = {'staged_input': str(staged.relative_to(settings.effective_temporary_root_path)), 'filename': Path(audio.filename).name, 'size_bytes': total, 'sha256': digest.hexdigest()}
        else:
            _audio_ingress_use_case().check_public_input(workspace_id, task_id, operation, values, actor)
        runtime = _creation_runtime()
        media = runtime.registrations['creation_job'].owner.task_media
        job = media.submit(runtime, actor, task_id, operation, values, result_kind)
        receipt = _task_media_job_response(media, job, actor, runtime)
        receipt['result'] = None
        return JSONResponse(status_code=202, content=receipt)
    except Exception as exc:
        if created_input is not None:
            relative = str(created_input.relative_to(settings.effective_temporary_root_path))
            with production_repo._connect() as conn:
                referenced = conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND event_type='task_media_submitted' AND json_extract(payload_json,'$.arguments.audio.staged_input')=? LIMIT 1", (workspace_id, relative)).fetchone()
            if referenced is None:
                created_input.unlink(missing_ok=True)
        if isinstance(exc, AudioIngressRejected):
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, (ValueError, KeyError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise







def _visual_preview_facts(workspace_id, task_id, arguments, actor):
    case = _visual_preview_use_case()
    try:
        prepared = case.prepare_source(workspace_id, task_id, arguments, actor)
    except PicturePreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    case.require_picture_plan(prepared)
    budget = case.require_render_budget(cpu_render_budget_from_env())
    if case.uses_presenter(prepared):
        _presenter, presenter_cpu = presenter_budgets_from_env()
        budget = case.require_presenter_budget(presenter_cpu)
    return case.seal_resources(prepared, budget, cpu_audio_budget_from_env(), actor=actor)


def _compound_selection_facts(workspace_id, task_id, arguments, actor):
    case = _compound_edit_use_case()
    prepared = case.prepare_source(workspace_id, task_id, arguments, actor)
    if prepared[1] is None:
        raise ValueError('当前任务没有可预览的故事板')
    budget = cpu_render_budget_from_env()
    if budget is None:
        raise ValueError('CPU 画面预览预算尚未标定，未启动媒体')
    if any(clip.person_render_id for clip in prepared[1].clips):
        _presenter, presenter_cpu = presenter_budgets_from_env()
        if presenter_cpu is None:
            raise ValueError('人物组合 CPU 预览预算尚未标定，未启动媒体')
        budget = presenter_cpu
    return case.seal_resources(prepared, budget, cpu_audio_budget_from_env(), actor=actor)



def _try_exact_preview(task_id,request,payload,*,source,execution,operation):
    workspace_id=current_workspace_id(request)
    if payload.operation_type in {'EditAudioTimeline','AdjustVisualEffects'}:
        _exact_edit_access(task_id,request,write=True)
    if payload.operation_type in {'ChangeLinkedSpeed','DeleteClip','DuplicateClip','InsertClip'}:
        return _preview_linked_core(task_id,request,payload,source=source)
    reader,diagnostic=_content_reader(request,workspace_id)
    if source is not None:reader,diagnostic=_assistant_member_id(request,workspace_id),False
    actor=_request_permit_actor(request,workspace_id) if payload.operation_type == 'EditAudioTimeline' else None
    case=_exact_preview_use_case()
    try:
        outcome=case.preview(workspace_id,task_id,payload,identity={'member_id':reader,'local_diagnostic':diagnostic},
            actor=actor,source=source,execution=execution,reader_identity=None,
            draft_only=request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact'))
        if isinstance(outcome,ClipPreviewCommand):
            return _submit_exact_clip_preview(case,workspace_id,request,outcome,operation)
        return _edit_preview_response(*outcome)
    except (EditPreviewRejected,PicturePreviewRejected,AudioEditRejected) as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except (ClipPreviewUnavailable,HostBudgetUncalibrated) as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc
    except (ClipPreviewConflict,TimelineVersionConflict) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


def _submit_exact_clip_preview(case,workspace_id,request,command,operation):
    actor=_require_permit_actor(request,workspace_id)
    try:
        command=case.prepare_clip_submission(actor,command,original_command=getattr(request.state,'task_media_command',None))
        if command.source is not None:
            _require_assistant_input_origin(request,command.source)
        runtime=_creation_runtime();media=runtime.registrations['creation_job'].owner.task_media
        job=case.submit_clip(runtime,media,actor,command,operation=operation)
        receipt=_task_media_job_response(media,job,actor,runtime);receipt['result']=None
        return JSONResponse(status_code=202,content=receipt)
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except (ClipPreviewUnavailable,HostBudgetUncalibrated) as exc:raise HTTPException(status_code=503,detail=str(exc)) from exc
    except (ClipPreviewConflict,TimelineVersionConflict) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    except PermissionError as exc:raise HTTPException(status_code=403,detail=str(exc)) from exc
    except KeyError as exc:raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (ValueError,OSError) as exc:raise HTTPException(status_code=422,detail=str(exc)) from exc


def _queue_exact_preview(task_id,request,payload,*,source,media_kind):
    workspace_id=current_workspace_id(request)
    actor=_require_permit_actor(request,workspace_id)
    case=_exact_preview_use_case()
    try:
        task,values=case.submission_arguments(workspace_id,task_id,actor,payload,media_kind=media_kind,
            draft_only=request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact'),source=source)
        runtime=_creation_runtime();media=runtime.registrations['creation_job'].owner.task_media
        job=case.submit(runtime,media,actor,task,values)
        return _task_media_job_response(media,job,actor,runtime)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except (ValueError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


def _exact_preview_facts(workspace_id,task_id,arguments,actor):
    case=_exact_preview_use_case()
    prepared=case.prepare_source(workspace_id,task_id,arguments,actor)
    if prepared[1] is None:raise ValueError('当前任务没有可预览的故事板')
    budget=cpu_render_budget_from_env()
    if budget is None:raise ValueError('CPU 画面预览预算尚未标定，未启动媒体')
    if any(clip.person_render_id for clip in prepared[1].clips):
        _,person_budget=presenter_budgets_from_env()
        if person_budget is None:raise ValueError('人物组合 CPU 预览预算尚未标定，未启动媒体')
        budget=person_budget
    resources=case.picture_resources(prepared,budget)
    return case.seal_resources(prepared,resources,cpu_audio_budget_from_env(),actor=actor)


def _task_media_facts(workspace_id, task_id, operation, arguments, actor):
    if operation == 'compound_selection_preview':
        return _compound_selection_facts(workspace_id, task_id, arguments, actor)
    if operation == 'visual_edit_preview' and '_clip_preview' not in arguments:
        return _visual_preview_facts(workspace_id, task_id, arguments, actor)
    if operation in MEDIA_SCRIPT_ACTIONS:
        owner = _media_script_use_case()
        prepared = owner.prepare_source(workspace_id, task_id, operation, arguments, actor)
        toolchain, budget = _script_configuration()
        return owner.seal(prepared, toolchain, budget, arguments.get('_script_toolchain'))
    if operation == 'output_observation':
        return _output_observation_facts(workspace_id, task_id, arguments, actor)
    if AudioEditUseCase.handles(operation, arguments):
        return _audio_edit_use_case().seal(workspace_id, task_id, operation, arguments, actor, cpu_audio_budget_from_env())
    if operation in AUDIO_INGRESS_ACTIONS:
        return _audio_ingress_use_case().seal(workspace_id, task_id, operation, arguments, actor, cpu_audio_budget_from_env())
    if '_clip_preview' in arguments:
        command = ClipPreviewCommand(**arguments['_clip_preview'])
        if command.task_id != task_id or actor.workspace_id != workspace_id:
            raise PermissionError('镜头预览封存输入不属于当前成员和任务')
        return _clip_preview_use_case().seal(actor, command)
    if operation == 'exact_edit_preview':
        return _exact_preview_facts(workspace_id, task_id, arguments, actor)
    if operation == 'command_edit_preview':
        return _command_preview_use_case().facts(workspace_id, task_id, arguments, actor)
    raise ValueError('未知任务媒体处理类型')








def _task_media_outputs_current(job):
    return _output_observation_use_case().published_outputs_current(job)


def _task_media_device_request(job, actor, runtime):
    with production_repo._connect() as conn:
        row = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? "
            "AND owner_type='creation_job' AND owner_id=? AND input_digest=? AND unit_key=? "
            "ORDER BY created_at DESC LIMIT 1",
            (job.workspace_id, job.id, job.input_digest, f"task_media:{job.input_digest}")).fetchone()
    if row is None:
        return None, {}
    view = runtime.scheduler.view_for(row["id"], actor)
    if view.get("id", view.get("request_id")) != row["id"]:
        return None, {}
    return runtime.scheduler._request(row), view


def _task_media_job_response(media, job, actor, runtime):
    from app.schemas import TaskMediaJobResponse
    current, event = media.read(job.workspace_id, job.id)
    if CreationExecutionActor.from_event(job.workspace_id, event) != actor or media.verify_identity(actor) != "ready":
        raise HTTPException(status_code=404, detail="任务媒体作业不存在")
    verdict = media.check(current, event, actor)
    status = "stale" if verdict != "ready" and current.status not in {"failed", "cancelled"} else current.status
    result = None
    result_invalid = status == "succeeded" and not _task_media_outputs_current(current)
    if result_invalid:
        status = "failed"
    if status == "succeeded":
        result = production_repo.get_task_event(job.workspace_id, f"event_{job.id}_result").payload["result"]
    device_request_id = waiting_reason = None
    device_request, view = _task_media_device_request(job, actor, runtime)
    if device_request is not None:
        device_request_id = device_request.id
        waiting_reason = view.get("wait_reason")
    payload = event["arguments"].get("payload", {})
    refs = payload.get("selected_object_refs", [])
    if not refs and payload.get("clip_ids"):
        refs = [f"clip:{item}" for item in payload["clip_ids"]]
    if not refs and payload.get("clip_id"):
        refs = [f"clip:{payload['clip_id']}"]
    if not refs and payload.get("target_id"):
        refs = [f"audio:{payload['target_id']}"]
    if not refs and payload.get("audio_edit"):
        refs = [f"audio:{payload['audio_edit']['target_id']}"]
    if not refs and payload.get("track_id"):
        refs = [f"subtitle-track:{payload['track_id']}"]
    if not refs and event["operation"] == "audio_waveform":
        refs = [f"audio:{event['arguments']['clip_id']}"]
    refs = [item for item in refs if re.fullmatch(r"(?:clip|audio|cue|overlay|subtitle-track):[A-Za-z0-9_-]{1,120}", item)][:8]
    return TaskMediaJobResponse(job_id=job.id, status=status, result_kind=event["result_kind"],
        operation=event["operation"], target_refs=refs, input_digest=job.input_digest, created_at=job.created_at,
        result_url=f"/api/creation-tasks/{job.task_id}/media-jobs/{job.id}", base_version_id=event["input_facts"]["base_version_id"],
        device_request_id=device_request_id, waiting_reason=waiting_reason, result=result,
        resource_wait=media.resource_admission_receipt(current, event, actor, device_request, status),
        failure_reason="result_invalid" if result_invalid else current.failure_reason,
        repair_action="本次媒体文件已丢失或变化；请恢复原文件。系统不会重渲染已提交产物。" if result_invalid else current.repair_action).model_dump(mode="json")


def _observation_version(task, payload):
    return _output_observation_use_case().version(task, payload)


def _prepare_observation_request(task_id, request, payload):
    task, member_id = _exact_edit_access(task_id, request, write=True)
    try:
        version = _observation_version(task, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    actor = _require_permit_actor(request, task.workspace_id)
    source = {"workspace_id": task.workspace_id, "task_id": task_id, "member_id": member_id,
        "expected_timeline_version_id": task.current_timeline_version_id,
        "expected_timeline_sequence": version.sequence if version else 0,
        "actor_identity": actor.identity_payload(), "request_id": str(payload.request_id) if payload.request_id else None}
    if payload.assistant_turn_id is not None:
        try:
            origin = conversation_repo.task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        snapshot = origin["context"].get("target_snapshot", {})
        if (snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence")) != (
                task.current_timeline_version_id, version.sequence if version else 0):
            raise HTTPException(status_code=409, detail="观察请求的助手轮次版本已失效")
        source.update(assistant_turn_id=str(payload.assistant_turn_id), conversation_id=origin["conversation_id"],
                      raw_command=origin["raw_command"],
                      **{key: origin[key] for key in ("input_id", "input_turn_id") if key in origin})
    _require_assistant_input_origin(request, source)
    return source


def _registered_observation_output(workspace_id, task_id, target, actor):
    try:
        return _output_observation_use_case().registered_output(workspace_id, task_id, target, actor)
    except OutputObservationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _output_observation_facts(workspace_id, task_id, arguments, actor):
    case = _output_observation_use_case()
    source = case.prepare_source(workspace_id, task_id, arguments, actor)
    budget = cpu_render_budget_from_env()
    case.require_render_budget(budget)
    observation_budget = observation_budget_from_env()
    return case.seal_resources(source, budget, observation_budget)


def _bounded_quality_evidence(record):
    evidence = record["evidence"]
    findings = evidence.get("findings", [])
    return {"check_id": record["check_id"], "timeline_version_id": record["timeline_version_id"],
        "render_ref": record["render_ref"], "check_state": record["check_state"],
        "result_status": record["result_status"], "checked_at": record["checked_at"],
        "error": record["error"], "validation_scope": "deterministic_media_errors_only",
        "findings": findings[:20], "findings_total": len(findings),
        "findings_truncated": len(findings) > 20, "unavailable": evidence.get("unavailable", [])}


def _task_observation_facts(task_id, request):
    workspace_id = current_workspace_id(request)
    task = _quality_check_task_access(request, workspace_id, task_id)
    current = _creation_task_quality_check_response(workspace_id, task_id).model_dump(mode="json")
    findings = current["findings"]
    current["findings_total"], current["findings_truncated"] = len(findings), len(findings) > 20
    current["findings"] = findings[:20]
    current.pop("clipping_summary", None)
    with production_repo._connect() as conn:
        rows = conn.execute("""SELECT j.id, e.payload_json FROM job_runs j JOIN task_events e
            ON e.workspace_id=j.workspace_id AND e.id='event_' || j.id || '_start'
            WHERE j.workspace_id=? AND j.task_id=? AND j.kind=?
            AND json_extract(e.payload_json,'$.operation')='output_observation'
            AND json_extract(e.payload_json,'$.member_id')=?
            ORDER BY j.created_at DESC,j.id DESC LIMIT 5""",
            (workspace_id, task_id, TASK_MEDIA_KIND, _assistant_member_id(request, workspace_id))).fetchall()
    observations = []
    for row in rows:
        job = production_repo.get_job_run(workspace_id, row["id"])
        start = json.loads(row["payload_json"])
        result = None
        if job.status == "succeeded":
            result = production_repo.get_task_event(workspace_id, f"event_{job.id}_result").payload["result"]
        stale = start["input_facts"]["base_version_id"] != task.current_timeline_version_id
        if not stale:
            try:
                original = CreationTaskObservationRequest.model_validate(start["arguments"]["payload"])
                _path, output, _binding = _registered_observation_output(workspace_id, task_id, original.target,
                    CreationExecutionActor.from_event(workspace_id, start))
                stale = output != start["input_facts"]["output"]
            except (KeyError, OSError, ValueError, PermissionError, HTTPException):
                stale = True
        observations.append({"job_id": job.id, "status": job.status,
            "stale": stale,
            "timeline_version_id": start["input_facts"]["base_version_id"], "result": result,
            "result_url": f"/api/creation-tasks/{task_id}/media-jobs/{job.id}"})
    targets = []
    try:
        _path, output, _binding = _registered_observation_output(workspace_id, task_id,
            CreationTaskObservationTarget(kind="final"), None)
    except (KeyError, OSError, ValueError, PermissionError, HTTPException):
        pass  # Missing/changed final is not an available observation target.
    else:
        targets.append({"target": {"kind": "final"}, "artifact_id": output["artifact_id"], "sha256": output["sha256"],
                        "output_role": "composed_output"})
    with production_repo._connect() as conn:
        rows = conn.execute("""SELECT a.id,a.logical_ref,a.kind,a.sha256,a.producer_job_id FROM artifact_records a
            JOIN job_runs j ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id
            JOIN task_events e ON e.workspace_id=j.workspace_id AND e.id='event_' || j.id || '_start'
            WHERE a.workspace_id=? AND a.task_id=? AND j.kind=? AND j.status='succeeded'
            AND json_extract(e.payload_json,'$.member_id')=?
            AND json_extract(e.payload_json,'$.input_facts.base_version_id') IS ?
            ORDER BY j.created_at DESC,a.id DESC LIMIT 10""",
            (workspace_id, task_id, TASK_MEDIA_KIND, _assistant_member_id(request, workspace_id), task.current_timeline_version_id)).fetchall()
    for row in rows:
        if row["logical_ref"].endswith(".mp4") or row["kind"] == "graphic_source" and row["logical_ref"].endswith(".png"):
            targets.append({"target": {"kind": "media_job", "job_id": row["producer_job_id"], "artifact_id": row["id"]},
                "artifact_id": row["id"], "sha256": row["sha256"],
                "output_role": "graphic_source" if row["kind"] == "graphic_source" else "composed_preview"})
    version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    return {"task_id": task_id, "timeline_version_id": task.current_timeline_version_id,
        "timeline_sequence": version.sequence if version else 0,
        "quality_check": current, "quality_history": [_bounded_quality_evidence(item)
            for item in quality_check_repo.history(workspace_id, task_id)],
        "observations": observations, "targets": targets, "targets_limit": 11,
        "media_policy": "local_measurements_and_text_only"}


@app.get("/api/creation-tasks/{task_id}/observations")
def get_creation_task_observations(task_id: str, request: Request):
    return _task_observation_facts(task_id, request)


@app.post("/api/creation-tasks/{task_id}/observations")
def observe_creation_task_output(task_id: str, request: Request, payload: CreationTaskObservationRequest):
    event_id = None
    if payload.request_id is not None:
        task, member_id = _exact_edit_access(task_id, request, write=True)
        event_id, fresh = _claim_edit_request(task, member_id, payload.request_id, 'observation',
            payload.model_dump(mode='json', exclude_unset=True))
        if not fresh:
            result = _read_edit_request_result(task, event_id)
            if result is not None:
                return result
            raise HTTPException(status_code=409, detail='原观察请求结果尚未确定；请查原请求，不重复执行')
    try:
        source = _prepare_observation_request(task_id, request, payload)
        task, _member_id = _exact_edit_access(task_id, request, write=True)
        try:
            _observation_version(task, payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException as exc:
        if event_id:
            _save_edit_request_result(task, event_id, JSONResponse(status_code=exc.status_code, content={'detail': exc.detail}))
        raise
    workspace_id = current_workspace_id(request)
    actor = _require_permit_actor(request, workspace_id)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403, detail='当前成员不能安排此任务的媒体处理')
    values = {'_media_kind': 'output_observation',
        '_draft': request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'),
        '_typed_source': source, 'payload': payload.model_dump(mode='json', exclude_unset=True)}
    try:
        runtime = _creation_runtime()
        media = runtime.registrations['creation_job'].owner.task_media
        job = media.submit(runtime, actor, task_id, 'output_observation', values, 'observation')
        receipt = _task_media_job_response(media, job, actor, runtime)
        if event_id:
            _save_edit_request_result(task, event_id, JSONResponse(status_code=202, content=receipt))
        receipt['result'] = None
        return JSONResponse(status_code=202, content=receipt)
    except OutputObservationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/creation-tasks/{task_id}/media-jobs")
def list_task_media_jobs(task_id: str, request: Request, cursor: str | None = None, limit: int = 20):
    if not 1 <= limit <= 40:
        raise HTTPException(status_code=422, detail="媒体作业分页大小须为 1–40")
    actor = _require_permit_actor(request, current_workspace_id(request))
    runtime = _creation_runtime()
    media = runtime.registrations["creation_job"].owner.task_media
    _creation_task_or_404(actor.workspace_id, task_id)
    parameters = [actor.workspace_id, task_id, TASK_MEDIA_KIND, actor.member_id]
    before = ""
    if cursor is not None:
        try:
            saved, event = media.read(actor.workspace_id, cursor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="媒体作业分页位置不存在") from exc
        if saved.task_id != task_id or CreationExecutionActor.from_event(actor.workspace_id, event) != actor:
            raise HTTPException(status_code=404, detail="媒体作业分页位置不存在")
        before = " AND (j.created_at < ? OR (j.created_at = ? AND j.id < ?))"
        parameters.extend([saved.created_at.isoformat(), saved.created_at.isoformat(), saved.id])
    parameters.append(limit + 1)
    with production_repo._connect() as conn:
        rows = conn.execute("SELECT j.* FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id "
            "AND e.job_id=j.id AND e.id='event_' || j.id || '_start' "
            "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND json_extract(e.payload_json,'$.member_id')=?" + before +
            " ORDER BY j.created_at DESC,j.id DESC LIMIT ?", parameters).fetchall()
    items = [_task_media_job_response(media, production_repo._job_run_from_row(row), actor, runtime) for row in rows[:limit]]
    return {"items": items, "next_cursor": rows[limit-1]["id"] if len(rows) > limit else None}


@app.get("/api/creation-tasks/{task_id}/media-jobs/{job_id}")
def get_task_media_job(task_id: str, job_id: str, request: Request):
    return _goal_media_result(task_id, job_id, request)


@app.post("/api/creation-tasks/{task_id}/media-jobs/{job_id}/cancel")
def cancel_task_media_job(task_id: str, job_id: str, request: Request):
    actor = _require_permit_actor(request, current_workspace_id(request))
    runtime = _creation_runtime()
    media = runtime.registrations["creation_job"].owner.task_media
    try:
        job, event = media.read(actor.workspace_id, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务媒体作业不存在") from exc
    from app.creation_execution import CreationExecutionActor
    if job.task_id != task_id or CreationExecutionActor.from_event(job.workspace_id, event) != actor:
        raise HTTPException(status_code=404, detail="任务媒体作业不存在")
    if job.status in {"queued", "running", "interrupted"}:
        with production_repo._connect() as conn:
            rows = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_id=? AND owner_type='creation_job' AND state IN ('waiting','blocked','granted','yield_requested')",
                                (job.workspace_id, job.id)).fetchall()
        for row in rows:
            runtime.scheduler.cancel(row["id"], actor)
            runtime.registrations["creation_job"].executor.request_cancel(row["id"])
        production_repo.finish_job_run(job.workspace_id, job.id, status="cancelled", failure_reason="user_cancelled",
            impact="当前时间线保持不变。", repair_action="需要时重新安排媒体处理。")
    return _task_media_job_response(media, job, actor, runtime)


@app.post("/api/creation-tasks/{task_id}/media-jobs/{job_id}/retry")
def retry_task_media_job(task_id: str, job_id: str, request: Request):
    actor = _require_permit_actor(request, current_workspace_id(request))
    runtime = _creation_runtime()
    media = runtime.registrations["creation_job"].owner.task_media
    try:
        job, event = media.read(actor.workspace_id, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务媒体作业不存在") from exc
    if job.task_id != task_id or CreationExecutionActor.from_event(job.workspace_id, event) != actor:
        raise HTTPException(status_code=404, detail="任务媒体作业不存在")
    reason = "result_invalid" if job.status=="succeeded" and not _task_media_outputs_current(job) else job.status
    if reason not in {"failed", "cancelled", "result_invalid"}:
        raise HTTPException(status_code=409, detail="只有失败或已取消的媒体作业可以显式重试")
    with production_repo._connect() as conn:
        saved = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_id=? AND owner_type='creation_job'",
                             (job.workspace_id, job.id)).fetchone()
    if saved is None:
        raise HTTPException(status_code=409, detail="媒体作业没有可核验的原设备请求")
    try:
        runtime.scheduler.retry_existing(saved["id"], actor, reason=reason)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runtime.wake()
    receipt = _task_media_job_response(media, job, actor, runtime)
    receipt["result"] = None
    return JSONResponse(status_code=202, content=receipt)


def _picture_preview_components(repository=None):
    repo = repository if repository is not None else production_repo
    audio = TaskAudio(repo, artifact_store, settings.effective_temporary_root_path)
    metadata = MetadataEditUseCase(repo, artifact_store, output_root=Path(settings.video_output_path), audio_service=audio)
    renderer = PicturePreviewRenderer(repo, artifact_store, audio, metadata, montage_renderer,
        verify_identity=_verify_creation_identity, presenter_model_ids=_presenter_model_ids,
        output_root=Path(settings.video_output_path), portrait_worker_path=settings.native_portrait_worker_path,
        temporary_root=settings.effective_temporary_root_path)
    audio.picture_renderer = renderer.render_picture_input
    metadata.picture_renderer = renderer
    metadata.preview_settings = settings
    return audio, metadata, renderer


def _command_preview_use_case(repository=None):
    repo=production_repo if repository is None else repository
    exact=_exact_preview_use_case(repo)
    metadata=exact.metadata
    return CommandPreviewUseCase(repo,conversation_repo,metadata,exact.subtitles,exact.linked,
        CompoundEditUseCase(repo,metadata,picture_renderer=metadata.picture_renderer),exact,exact.audio_edit,exact.visual,
        NarrationPreviewUseCase(repo,metadata),PersonPreviewUseCase(repo,metadata,latentsync_provider,avatar_lipsync_provider))


def _person_preview_use_case():
    return PersonPreviewUseCase(production_repo,_metadata_edit_use_case(),latentsync_provider,avatar_lipsync_provider)


def _exact_preview_use_case(repository=None):
    repo = production_repo if repository is None else repository
    audio, metadata, renderer = _picture_preview_components(repo)
    return ExactPreviewUseCase(repo, metadata, SubtitleEditUseCase(repo,metadata),
        LinkedTimelineEditUseCase(repo,metadata), AudioEditUseCase(repo,audio,metadata,creation_execution_mode),
        VisualPreviewUseCase(repo,artifact_store,audio,metadata,renderer), _clip_preview_use_case(repo), conversation_repo, settings)


def _visual_preview_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    audio, metadata, renderer = _picture_preview_components(repo)
    return VisualPreviewUseCase(repo, artifact_store, audio, metadata, renderer)


def _picture_preview_renderer():
    return _picture_preview_components()[2]


def _task_audio() -> TaskAudio:
    return _picture_preview_components()[0]


def _clip_preview_use_case(repository=None):
    repo = production_repo if repository is None else repository
    def profile():
        try:
            return clip_preview_budget_from_env()
        except HostBudgetUncalibrated as exc:
            raise ClipPreviewUnavailable(str(exc)) from exc
    def presenter_source(task, clip):
        plan = repo.get_creation_plan(task.workspace_id, task.plan_id)
        script = repo.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
        matches = [current_presenter_render(repo, task, segment, request, _person_render_media_path)
            for segment, request in presenter_inputs(repo, task, script, plan, _presenter_model_ids())]
        if not any(item is not None and item.id == clip.person_render_id for item in matches):
            raise PermissionError("当前人物/声音授权或片段版本已失效")
        return _person_render_media_path(repo.get_person_render(task.workspace_id, task.id, clip.person_render_id))
    return ClipPreviewUseCase(repo, resolve_presenter_source=presenter_source, budget=profile)


def _submit_clip_preview(task_id, request, *, base_version_id, operation_type, clip_ids, values, raw_command):
    """HTTP adapter: trusted identity/source in, explicit persistent command out."""
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    draft = request.query_params.get("draft") == "true" or request.url.path.endswith("/edits/exact")
    if draft:
        command_values = ({'clip_ids':clip_ids, 'effects':values} if operation_type == 'AdjustVisualEffects'
            else {'clip_id':clip_ids[0], **values})
        return _preview_metadata_settings(task, request, operation_type, command_values, base_version_id)
    actor = _require_permit_actor(request, workspace_id)
    source = EXACT_EDIT_SOURCE.get()
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403, detail="当前成员不能修改此任务")
    try:
        if base_version_id is None:
            raise ClipPreviewConflict("当前故事板没有基础版本，请先保留当前故事板并建立版本")
        if task.current_timeline_version_id != base_version_id:
            raise ClipPreviewConflict("时间线已经变化，请刷新后重新预览")
        base = production_repo.get_timeline_version(workspace_id, base_version_id)
        original = getattr(request.state, "task_media_command", None)
        if source is None and original is not None:
            action = CreationTaskExactEditRequest(operation_type=operation_type,
                **({"clip_ids":clip_ids, "effects":values} if operation_type == "AdjustVisualEffects" else {"clip_id":clip_ids[0], **values}))
            source = {"workspace_id":workspace_id, "task_id":task_id, "member_id":actor.user_id or actor.member_id,
                "actor_identity":actor.identity_payload(), "request_id":None, "raw_command":original.raw_command,
                "expected_timeline_version_id":base.id, "expected_timeline_sequence":base.sequence,
                "action":action.model_dump(mode="json", exclude_unset=True),
                "source_facts":_exact_edit_source_facts(task, action, request)}
            if original.assistant_turn_id is not None:
                origin = conversation_repo.task_turn_source(str(original.assistant_turn_id), workspace_id,
                    actor.user_id or actor.member_id, task_id)
                source.update(assistant_turn_id=str(original.assistant_turn_id), conversation_id=origin["conversation_id"],
                              **{key: origin[key] for key in ("input_id", "input_turn_id") if key in origin})
        if source is not None:
            _require_assistant_input_origin(request, source)
        command = ClipPreviewCommand(task_id, base.id, operation_type, clip_ids, values, raw_command, source)
        use_case = _clip_preview_use_case()
        use_case.prepare(actor, command)
        use_case.seal(actor, command)
        runtime = _creation_runtime()
        media = runtime.registrations["creation_job"].owner.task_media
        media.clip_preview = ClipPreviewExecutor(use_case, _task_audio())
        operation = ("exact_edit_preview" if source is not None or operation_type != "AdjustVisualEffects" else "visual_edit_preview")
        arguments = {"_media_kind":"clip_preview", "_draft":False, "_clip_preview":asdict(command)}
        if source is not None:
            arguments["_typed_source"] = source
        # Keep the original action/target projection used by existing receipts.
        arguments["payload"] = {"operation_type":operation_type, "clip_ids":clip_ids,
            "clip_id":clip_ids[0], "base_version_id":base.id,
            **({"effects":values} if operation_type == "AdjustVisualEffects" else values)}
        job = media.submit(runtime, actor, task_id, operation, arguments, "edit_preview")
        receipt = _task_media_job_response(media, job, actor, runtime)
        receipt["result"] = None
        return JSONResponse(status_code=202, content=receipt)
    except (ClipPreviewUnavailable, HostBudgetUncalibrated) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ClipPreviewConflict, TimelineVersionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _effective_audio_timeline(plan: CreationTaskMontagePlan) -> AudioTimeline:
    return metadata_effective_audio_timeline(plan)


@app.post('/api/creation-tasks/{task_id}/audio/import')
def import_creation_task_audio(task_id: str, request: Request, audio: UploadFile=File(...)):
    return _submit_audio_ingress(task_id, request, 'audio_import', 'audio_source', audio=audio)


@app.get("/api/creation-tasks/{task_id}/audio/recordings")
def list_creation_task_recordings(task_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not task.confirmed_script_id:
        return {"items": []}
    service = _task_audio()
    current_narration_refs = set()
    if task.montage_plan_id:
        plan = creation_task_montage_plan_from_payload(
            production_repo.get_creation_task_montage_plan(workspace_id, task_id))
        if plan.script_id == task.confirmed_script_id:
            current_narration_refs = {clip.source_ref for track in _effective_audio_timeline(plan).tracks
                                      if track.role == "narration" for clip in track.clips}
    if task.narration_cache_key:
        current_narration = production_repo.get_narration_artifact(
            workspace_id, task.narration_cache_key,
        )
        if (current_narration is not None and current_narration.task_id == task.id
                and current_narration.script_id == task.confirmed_script_id
                and current_narration.source_kind == "recorded"
                and current_narration.source_artifact_ref):
            current_narration_refs.add(current_narration.source_artifact_ref)
    items = []
    for record in reversed(production_repo.list_artifact_records(workspace_id, task_id)):
        if record.kind != "task_recording":
            continue
        try:
            _, duration_ms = service.recording(
                workspace_id, task_id, record.logical_ref,
            )
            if not _task_media_outputs_current(production_repo.get_job_run(workspace_id, record.producer_job_id)):
                continue
        except (KeyError, ValueError, FileNotFoundError):
            continue  # Failed or obsolete uploads are never offered as current narration.
        items.append({"source_ref": record.logical_ref, "artifact_id": record.id,
                      "audio_url": f"/api/creation-tasks/{task_id}/audio/artifacts/{record.id}",
                      "duration_ms": duration_ms, "created_at": record.created_at.isoformat(),
                      "applied": record.logical_ref in current_narration_refs})
    return {"items": items}


@app.post('/api/creation-tasks/{task_id}/audio/recordings')
def save_creation_task_recording(task_id: str, request: Request, audio: UploadFile=File(...)):
    return _submit_audio_ingress(task_id, request, 'recording_save', 'recording', audio=audio)


@app.post(
    "/api/creation-tasks/{task_id}/audio/recordings/adopt",
    response_model=CreationTaskNarrationResponse,
)
def adopt_creation_task_recording(
    task_id: str,
    request: Request,
    payload: CreationTaskRecordingAdoptRequest,
) -> CreationTaskNarrationResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.stage != "narration" or task.stage_status not in {"not_started", "failed"}:
        raise HTTPException(status_code=409, detail="当前任务不能首次采用录音")
    if task.confirmed_script_id != payload.script_id:
        raise HTTPException(status_code=409, detail="文案版本已变化，请重新选择当前录音")
    try:
        script = production_repo.get_creation_task_script(workspace_id, payload.script_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="当前文案不存在") from exc
    if script.selection_status != "confirmed" or len(script.sections) != 1:
        raise HTTPException(status_code=409, detail="当前版本有多个文案段，暂不能确定单段录音的对应关系")
    section = script.sections[0]
    if payload.source_section_id != section.id:
        raise HTTPException(status_code=409, detail="录音对应的文案段已变化，请重新选择")
    script_digest = production_repo.confirmed_creation_task_script_digest(
        workspace_id, task.id, script.id,
    )
    if script_digest is None:
        raise HTTPException(status_code=409, detail="当前文案未确认或已经变化，不能采用录音")
    try:
        source, duration_ms = _task_audio().recording(
            workspace_id, task.id, payload.source_ref,
        )
        if not _task_media_outputs_current(production_repo.get_job_run(workspace_id, source.producer_job_id)):
            raise ValueError("电脑录音已变化或缺少受许可核验回执，请重新录制")
        audio_path = _task_audio().source(workspace_id, task.id, source.logical_ref)
        cache_seed = f"recorded:{task.id}:{script.id}:{section.id}:{source.logical_ref}:{source.sha256}"
        cache_key = f"recorded_{hashlib.sha256(cache_seed.encode('utf-8')).hexdigest()[:48]}"
        narration_ref = production_repo.narration_artifact_reference(cache_key)
        narration_id = f"narration_segment_{hashlib.sha256(cache_seed.encode('utf-8')).hexdigest()[:20]}"
        language = {"zh-CN": "zh", "en-US": "en", "zh-CN/en-US": "zh-en"}[script.language]
        record = NarrationArtifactRecord(
            id=cache_key,
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            voice_profile_id=None,
            cache_key=cache_key,
            audio_path=str(audio_path),
            duration_seconds=duration_ms / 1000,
            sample_rate=None,
            model_id=None,
            language=language,
            source_kind="recorded",
            source_artifact_ref=source.logical_ref,
            source_sha256=source.sha256,
            source_script_digest=script_digest,
        )
        segment = NarrationSegment(
            id=narration_id,
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            source_section_id=section.id,
            text=section.content,
            audio_reference=narration_ref,
            duration_seconds=duration_ms / 1000,
            sample_rate=None,
            model_id=None,
            voice_profile_id=None,
            voice_version=None,
            language=language,
            pronunciation_version="",
            status="ready",
            source_kind="recorded",
            source_artifact_ref=source.logical_ref,
            source_sha256=source.sha256,
            source_script_digest=script_digest,
        )
        task, record = production_repo.complete_creation_task_narration(record, segments=[segment])
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=f"当前录音未采用：{exc}") from exc
    return _creation_task_narration_response(task, record)


def _require_task_media_artifact(record, request):
    job = production_repo.get_job_run(record.workspace_id, record.producer_job_id)
    if job.kind != TASK_MEDIA_KIND:
        return
    actor = _require_permit_actor(request, record.workspace_id)
    runtime = _creation_runtime()
    media = runtime.registrations["creation_job"].owner.task_media
    original, event = media.read(record.workspace_id, job.id)
    if CreationExecutionActor.from_event(record.workspace_id, event) != actor or media.verify_identity(actor) != "ready":
        raise KeyError(record.id)
    if original.status != "succeeded" or not _task_media_outputs_current(original):
        raise KeyError(record.id)
    if record.kind not in {"audio_source", "task_recording"} and media.check(original, event, actor) != "ready":
        raise KeyError(record.id)


@app.get("/api/creation-tasks/{task_id}/audio/artifacts/{artifact_id}")
def get_creation_task_audio_artifact(task_id: str, artifact_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    try:
        record = production_repo.get_artifact_record(workspace_id, artifact_id)
        if record.task_id != task_id:
            raise KeyError(artifact_id)
        _require_task_media_artifact(record, request)
        path = _task_audio().source(workspace_id, task_id, record.logical_ref, actor=_require_permit_actor(request, workspace_id))
    except (KeyError, ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="当前任务的音频不存在") from exc
    audio_media_types = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg",
                         ".m4a": "audio/mp4", ".mp4": "audio/mp4"}
    return FileResponse(path, media_type=audio_media_types.get(path.suffix))


@app.get('/api/creation-tasks/{task_id}/audio/waveform/{clip_id}')
def get_creation_task_audio_waveform(task_id: str, clip_id: str, request: Request):
    return _submit_audio_ingress(task_id, request, 'audio_waveform', 'waveform', arguments={'clip_id': clip_id})


@app.post('/api/creation-tasks/{task_id}/audio/listen')
def listen_creation_task_audio(task_id: str, request: Request, payload: AudioListenRequest):
    return _submit_audio_ingress(task_id, request, 'audio_listen', 'audio_listen', arguments={'payload': payload.model_dump(mode='json', exclude_unset=True)})


def _preview_clip_original_audio(task_id: str, request: Request, payload: CreationTaskEditPreviewRequest):
    return _preview_audio_edit_request(task_id, request, payload, original_clip=True, queue_on_required=False)


@app.post('/api/creation-tasks/{task_id}/audio/preview', response_model=CreationTaskEditPreviewResponse)
def preview_creation_task_audio(task_id: str, request: Request, payload: AudioEditRequest):
    return _preview_audio_edit_request(task_id, request, payload, queue_on_required=True)


@app.post("/api/creation-tasks/{task_id}/graphics/import")
def import_creation_task_graphic(task_id: str, request: Request, image: UploadFile = File(...)):
    from PIL import Image
    workspace_id=current_workspace_id(request)
    _creation_task_or_404(workspace_id,task_id)
    try:
        return import_graphic_source(production_repo,artifact_store,settings.effective_temporary_root_path,workspace_id,task_id,image)
    except (ValueError,OSError,Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422,detail=str(exc) if isinstance(exc,ValueError) else "图片导入失败，请检查格式、尺寸和外接盘") from exc


def _script_configuration():
    from app.member_permits import media_script_budget_from_env
    toolchain = read_script_toolchain(Path(settings.model_root_path).expanduser())
    try:
        budget = media_script_budget_from_env()
    except (ValueError, HostBudgetUncalibrated) as exc:
        raise MediaScriptError("媒体脚本专用预算不完整或不可核验") from exc
    if budget is None:
        raise MediaScriptError("媒体脚本专用预算尚未标定")
    return toolchain, budget


def _completed_source_receipt(task, request, turn_id, slot, *, contract, input_refs):
    actor = _require_permit_actor(request, task.workspace_id)
    goal = conversation_repo.get_task_goal(turn_id)
    bound = conversation_repo.completed_goal_source(turn_id, requested_outputs=[contract], input_refs=input_refs)
    version = production_repo.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    if (bound is None or bound["job_id"] != slot["job_id"] or
            (goal["target_snapshot"].get("timeline_version_id"), goal["target_snapshot"].get("timeline_sequence")) != (
                task.current_timeline_version_id, version.sequence if version else 0)):
        raise HTTPException(status_code=409, detail="已完成来源的原目标版本链已失效")
    try:
        actual = _goal_script_source_slot(goal, slot["job_id"], actor)
        if any(actual[key] != slot[key] for key in ("source_ref", "artifact_id", "sha256", "size_bytes", "width", "height", "name", "input_refs")):
            raise ValueError("已完成来源登记事实发生变化")
        if actual["size_bytes"] > contract["max_bytes"] or actual["width"] > contract["width"] or actual["height"] > contract["height"]:
            raise ValueError("已完成来源不符合本次输出尺寸或大小上限")
        for item in input_refs:
            facts = graphic_source_facts(production_repo, artifact_store, task.workspace_id, task.id, item["source_ref"], actor=actor)
            if facts["artifact_id"] != item["expected_artifact_id"] or facts["sha256"] != item["expected_sha256"]:
                raise ValueError("原固定输入登记修订已变化")
        job = production_repo.get_job_run(task.workspace_id, slot["job_id"])
        start = production_repo.get_task_event(task.workspace_id, f"event_{job.id}_start").payload
        result = production_repo.get_task_event(task.workspace_id, f"event_{job.id}_result").payload["result"]
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TaskMediaJobResponse(operation=start["operation"], target_refs=[],
        input_digest=job.input_digest, created_at=job.created_at, job_id=job.id, status=job.status,
        result_kind="graphic_source", result_url=f"/api/creation-tasks/{task.id}/media-jobs/{job.id}",
        base_version_id=start["input_facts"]["base_version_id"], result={**result, "reused_completed_source": True}).model_dump(mode="json")


def _prepare_script_request(task_id, request, payload):
    task, member_id = _exact_edit_access(task_id, request, write=True)
    version = production_repo.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
        raise HTTPException(status_code=409, detail="脚本目标版本已变化")
    source = {"workspace_id": task.workspace_id, "task_id": task.id, "member_id": member_id,
        "request_id": str(payload.request_id), "expected_timeline_version_id": payload.expected_timeline_version_id,
        "expected_timeline_sequence": payload.expected_timeline_sequence}
    if payload.assistant_turn_id is not None:
        try:
            origin = conversation_repo.task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task.id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        snapshot = origin["context"].get("target_snapshot", {})
        if (snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence")) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise HTTPException(status_code=409, detail="原助手目标版本已失效")
        source.update(assistant_turn_id=str(payload.assistant_turn_id), conversation_id=origin["conversation_id"], raw_command=origin["raw_command"],
                      **{key: origin[key] for key in ("input_id", "input_turn_id") if key in origin})
    _require_assistant_input_origin(request, source)
    actor = _request_permit_actor(request, task.workspace_id) if auth_context.enabled else CreationExecutionActor(task.workspace_id, member_id)
    if actor is not None:
        source["actor_identity"] = actor.identity_payload()
    if payload.assistant_turn_id is not None:
        slot = conversation_repo.completed_goal_source(str(payload.assistant_turn_id),
            requested_outputs=[item.model_dump(mode="json") for item in payload.requested_outputs],
            input_refs=[item.model_dump(mode="json") for item in payload.input_refs])
        if slot is not None:
            source["reused_completed_source"] = _completed_source_receipt(task, request, str(payload.assistant_turn_id), slot,
                contract=payload.requested_outputs[0].model_dump(mode="json"),
                input_refs=[item.model_dump(mode="json") for item in payload.input_refs])
    return source


@app.get("/api/creation-tasks/{task_id}/assistant/media-script-capabilities")
def get_media_script_capabilities(task_id: str, request: Request):
    task, member_id = _exact_edit_access(task_id, request)
    reason, runtime, limits = None, None, None
    try:
        toolchain, budget = _script_configuration()
        runtime = {"language": "python", "libraries": ["Pillow"], "version": toolchain.version, "digest": toolchain.digest}
        limits = {key: value for key, value in asdict(budget).items() if key.startswith("max_")}
        if task.owner_user_id is not None and task.owner_user_id != member_id:
            reason = "当前成员没有该任务制作写权限"
    except MediaScriptError as exc:
        reason = str(exc)
    return {"task_id": task.id, "schema": MediaScriptPreviewRequest.model_json_schema(),
        "available": reason is None, "reason": reason, "runtime": runtime, "limits": limits,
        "purpose": "preview", "input_kinds": ["task_graphic_png"], "output_kinds": ["graphic_source"],
        "output_count": 1, "note": "生成PNG来源，尚未应用图层或生成成片；视频和音频脚本未接入。"}


@app.post("/api/creation-tasks/{task_id}/media-scripts/preview", response_model=TaskMediaJobResponse)
def preview_media_script(task_id: str, request: Request, payload: MediaScriptPreviewRequest):
    return _submit_media_script(task_id, request, payload)


def _media_script_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    return MediaScriptPreviewUseCase(repo, artifact_store, settings.effective_temporary_root_path, _media_script_tools(repo))


def _submit_media_script(task_id, request, payload, *, tool_id=None):
    operation = 'media_script_tool_preview' if tool_id is not None else 'media_script_preview'
    edit_request_event = None
    if payload.request_id is not None:
        task, member_id = _exact_edit_access(task_id, request, write=True)
        edit_request_event, fresh = _claim_edit_request(task, member_id, payload.request_id, 'script',
            {**payload.model_dump(mode='json', exclude_unset=True), **({'tool_id': tool_id} if tool_id is not None else {})})
        if not fresh:
            result = _read_edit_request_result(task, edit_request_event)
            if result is not None:
                return result
            raise HTTPException(status_code=409, detail='原预览请求结果尚未确定，请查询原请求，不重复执行')
    try:
        script_payload = payload
        if tool_id is not None:
            actor = _require_permit_actor(request, current_workspace_id(request))
            try:
                script_payload, _ref = _instantiate_script_tool(actor, tool_id, payload)
            except (KeyError, PermissionError, ScriptToolError) as exc:
                _script_tool_error(exc)
        typed_source = _prepare_script_request(task_id, request, script_payload)
        if typed_source.get('reused_completed_source') is not None:
            result = JSONResponse(status_code=200, content=typed_source['reused_completed_source'])
            if edit_request_event:
                _save_edit_request_result(task, edit_request_event, result)
            return result
    except HTTPException as exc:
        if edit_request_event:
            _save_edit_request_result(task, edit_request_event, JSONResponse(status_code=exc.status_code, content={'detail': exc.detail}))
        raise
    try:
        if tool_id is not None:
            actor = _require_permit_actor(request, current_workspace_id(request))
            try:
                _expanded, _ref = _instantiate_script_tool(actor, tool_id, payload)
            except (KeyError, PermissionError, ScriptToolError) as exc:
                _script_tool_error(exc)
        try:
            _script_configuration()
        except MediaScriptError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException as exc:
        if edit_request_event:
            _save_edit_request_result(task, edit_request_event, JSONResponse(status_code=exc.status_code, content={'detail': exc.detail}))
        raise
    workspace_id = current_workspace_id(request)
    actor = _require_permit_actor(request, workspace_id)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403, detail='当前成员不能安排此任务的媒体处理')
    values = {'_media_kind': 'media_script', '_draft': request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'),
              '_typed_source': typed_source}
    toolchain, _budget = _script_configuration()
    values['_script_toolchain'] = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(toolchain).items()}
    if tool_id is not None:
        _expanded, values['_recipe_ref'] = _instantiate_script_tool(actor, tool_id, payload)
    try:
        if tool_id is not None:
            values['tool_id'] = tool_id
        values['payload'] = payload.model_dump(mode='json', exclude_unset=True)
        runtime = _creation_runtime()
        media = runtime.registrations['creation_job'].owner.task_media
        job = media.submit(runtime, actor, task_id, operation, values, 'graphic_source')
        receipt = _task_media_job_response(media, job, actor, runtime)
        if edit_request_event:
            _save_edit_request_result(task, edit_request_event, JSONResponse(status_code=202, content=receipt))
        receipt['result'] = None
        return JSONResponse(status_code=202, content=receipt)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _media_script_tools(repository=None):
    repo = repository if repository is not None else production_repo
    def authorize(actor, task):
        user_id = actor.user_id or actor.member_id
        return (actor.workspace_id == task.workspace_id and (task.owner_user_id is None or task.owner_user_id == user_id)
            and content_readable(user_id=user_id, local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id))
    return MediaScriptTools(repo, artifact_store, verify_identity=_verify_creation_identity,
                            authorize_task_write=authorize)


def _script_tool_error(exc):
    status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, KeyError) else 409 if isinstance(exc, (ScriptToolConflict, ScriptToolUnknown)) else 422
    raise HTTPException(status_code=status, detail="配方不存在或不可访问" if isinstance(exc, KeyError) else str(exc)) from exc


def _instantiate_script_tool(actor, tool_id, payload):
    return _media_script_use_case().expand_recipe(actor, tool_id, payload)


@app.post("/api/creation-tasks/{task_id}/media-script-tools")
def save_media_script_tool(task_id: str, request: Request, payload: MediaScriptToolSaveRequest):
    task, member_id = _exact_edit_access(task_id, request, write=True)
    actor = _require_permit_actor(request, task.workspace_id)
    if payload.assistant_turn_id is not None:
        try:
            source = conversation_repo.task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _require_assistant_input_origin(request, {**source, "assistant_turn_id": str(payload.assistant_turn_id)})
        version = production_repo.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        snapshot = source["context"].get("target_snapshot", {})
        if (snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence")) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise HTTPException(status_code=409, detail="保存配方的原任务目标已变化")
    try:
        producer = production_repo.get_job_run(task.workspace_id, payload.producer_job_id)
        if producer.task_id != task_id:
            raise KeyError(payload.producer_job_id)
        return _media_script_tools().create_from_job(actor, producer.id, request_id=payload.request_id,
            name=payload.name, description=payload.description, parameter_schema=payload.parameter_schema)
    except (KeyError, PermissionError, ScriptToolError) as exc:
        _script_tool_error(exc)


@app.get("/api/media-script-tools/requests/{request_id}")
def get_media_script_tool_creation(request_id: UUID, request: Request):
    actor = _require_permit_actor(request, current_workspace_id(request))
    try:
        return _media_script_tools().get_created_for_request(actor, request_id)
    except (KeyError, PermissionError, ScriptToolError) as exc:
        _script_tool_error(exc)


@app.get("/api/media-script-tools/capabilities")
def media_script_tool_capabilities(request: Request):
    _require_permit_actor(request, current_workspace_id(request))
    return {"schemas": {"save": MediaScriptToolSaveRequest.model_json_schema(),
        "update": MediaScriptToolUpdateRequest.model_json_schema(), "revoke": MediaScriptToolRevokeRequest.model_json_schema(),
        "invoke": MediaScriptToolPreviewRequest.model_json_schema()},
        "parameter_schema_subset": "有界object与scalar子集；实际参数由共同Leaf validator校验，不加载工具代码到host。",
        "invocation_phase": "script", "result_kind": "graphic_source"}


@app.get("/api/media-script-tools")
def list_media_script_tools(request: Request, include_inactive: bool = False):
    actor = _require_permit_actor(request, current_workspace_id(request))
    try:
        values = _media_script_tools().list(actor, include_inactive=include_inactive, limit=25)
        return {"items": values[:24], "truncated": len(values) > 24}
    except (KeyError, PermissionError, ScriptToolError) as exc:
        _script_tool_error(exc)


@app.get("/api/media-script-tools/{tool_id}")
def get_media_script_tool(tool_id: str, request: Request, version: int | None = None):
    actor = _require_permit_actor(request, current_workspace_id(request))
    try:
        return _media_script_tools().get(actor, tool_id, version=version)
    except (KeyError, PermissionError, ScriptToolError) as exc:
        _script_tool_error(exc)


@app.patch("/api/media-script-tools/{tool_id}")
def update_media_script_tool(tool_id: str, request: Request, payload: MediaScriptToolUpdateRequest):
    actor = _require_permit_actor(request, current_workspace_id(request))
    try:
        return _media_script_tools().update_from_job(actor, tool_id, expected_version=payload.expected_version,
            job_id=payload.producer_job_id, name=payload.name, description=payload.description, parameter_schema=payload.parameter_schema)
    except (KeyError, PermissionError, ScriptToolError) as exc:
        _script_tool_error(exc)


@app.post("/api/media-script-tools/{tool_id}/revoke")
def revoke_media_script_tool(tool_id: str, request: Request, payload: MediaScriptToolRevokeRequest):
    actor = _require_permit_actor(request, current_workspace_id(request))
    try:
        return _media_script_tools().revoke(actor, tool_id, expected_version=payload.expected_version)
    except (KeyError, PermissionError, ScriptToolError) as exc:
        _script_tool_error(exc)


@app.post("/api/creation-tasks/{task_id}/media-script-tools/{tool_id}/preview", response_model=TaskMediaJobResponse)
def preview_media_script_tool(task_id: str, tool_id: str, request: Request, payload: MediaScriptToolPreviewRequest):
    return _submit_media_script(task_id, request, payload, tool_id=tool_id)


@app.get("/api/creation-tasks/{task_id}/graphics/sources")
def get_creation_task_graphic_sources(task_id: str, request: Request):
    workspace_id=current_workspace_id(request)
    _exact_edit_access(task_id,request)
    return graphic_sources(production_repo,workspace_id,task_id,artifact_store)


@app.get("/api/creation-tasks/{task_id}/graphics/source/{artifact_id}")
def get_creation_task_graphic_source(task_id: str,artifact_id: str,request: Request):
    workspace_id=current_workspace_id(request)
    _exact_edit_access(task_id,request)
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
    return FileResponse(path,media_type="image/png" if path.suffix == ".png" else "video/mp4")


def _visual_preview_projection(plan, clip_ids, patch, tracks=()):
    return _visual_preview_use_case().project(plan, clip_ids, patch, tracks)


@app.post("/api/creation-tasks/{task_id}/visual/preview",response_model=CreationTaskEditPreviewResponse)
def preview_creation_task_visual(task_id: str, request: Request, payload: VisualEditRequest):
    try:
        return _preview_visual_request(task_id, request, payload)
    except TaskMediaRequired as required:
        media_kind = required.kind
    workspace_id = current_workspace_id(request)
    actor = _require_permit_actor(request, workspace_id)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403, detail='当前成员不能安排此任务的媒体处理')
    values = {'_media_kind':media_kind,'_draft':request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact'),
        'payload':payload.model_dump(mode='json',exclude_unset=True)}
    if values['payload'].get('base_version_id') is None:
        values['payload']['base_version_id'] = task.current_timeline_version_id
    try:
        runtime = _creation_runtime()
        media = runtime.registrations['creation_job'].owner.task_media
        if task.montage_plan_id and task.current_timeline_version_id is None:
            media.facts(workspace_id,task_id,'visual_edit_preview',values,actor)
            if media.verify_identity(actor)!='ready':
                raise PermissionError('媒体处理身份失效或无法复核')
            plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
            values['payload']['base_version_id']=_ensure_montage_base_version(workspace_id,task,plan).id
        job=media.submit(runtime,actor,task_id,'visual_edit_preview',values,'edit_preview')
        receipt=_task_media_job_response(media,job,actor,runtime)
        receipt['result']=None
        return JSONResponse(status_code=202,content=receipt)
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except (ValueError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


def _preview_visual_request(task_id, request, payload, *, execution=None, source=None):
    draft_only = request.query_params.get("draft") == "true" or request.url.path.endswith("/edits/exact")
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    case = _visual_preview_use_case()
    try:
        prepared = case.prepare(task, payload)
        plan, patch, clips, changed_clips = prepared
        if not needs_full_visual_preview(clips[0], changed_clips[clips[0].id]):
            return _submit_clip_preview(task_id, request, base_version_id=payload.base_version_id,
                operation_type="AdjustVisualEffects", clip_ids=payload.clip_ids, values=patch, raw_command=payload.raw_command)
        return _edit_preview_response(*case.preview_full(task, payload, prepared,
            draft_only=draft_only,source=source,execution=execution))
    except PicturePreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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


def _resolve_creation_task_voice_selection(*, workspace_id: str, task: CreationTask, plan: CreationPlan,
                                         voice_profile_id: str, voice_version: str):
    try:
        reference = production_repo.creation_plan_voice_reference(workspace_id, voice_profile_id, voice_version,
            viewer_user_id=task.owner_user_id)
        entries = production_repo.list_applicable_pronunciation_entries(workspace_id=workspace_id,
            person_profile_id=reference.person_profile_id, episode_id=task.id)
    except (KeyError, ValueError, PermissionError, OSError) as exc:
        raise MediaAuthorityError(reason="voice_reference_unavailable", impact="声音引用未更新，未调用模型。",
                                  repair_action=str(exc)) from exc
    if reference.language not in {{"zh-CN":"zh", "en-US":"en", "zh-CN/en-US":"zh-en"}[plan.language], "zh-en"}:
        raise MediaAuthorityError(reason="voice_language_unavailable", impact="声音语言不匹配。", repair_action="选择匹配方案语言的声音。")
    return reference, [CreationPlanPronunciationReference(id=e.id, version=e.version, scope=e.scope,
        scope_reference=e.scope_reference, language=e.language) for e in entries]


def _creation_task_voice_options(
    workspace_id: str,
    task: CreationTask,
    plan: CreationPlan,
) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    for voice in production_repo.list_versioned_voice_profiles(workspace_id):
        if not production_repo.profile_readable(voice, task.owner_user_id):
            continue
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
                    person.display_name if person is not None else voice.display_name or "未命名声音"
                ),
                "language": voice.language,
                "model_id": voice.model_id,
                "runnable": reason is None,
                "runnable_reason": reason,
            }
        )
    return options


def _default_subtitle_style() -> dict[str, object]:
    return metadata_default_subtitle_style()


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
    try:
        return _metadata_edit_use_case().subtitle_track_from_montage(workspace_id, task, version, montage)
    except InvalidMontage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _current_subtitle_tracks(
    workspace_id: str,
    task: CreationTask,
    *,
    current_version: TimelineVersion | None = None,
    montage: CreationTaskMontagePlan | None = None,
) -> list[SubtitleTrack]:
    try:
        return _metadata_edit_use_case().current_subtitle_tracks(workspace_id, task, current_version=current_version, montage=montage)
    except InvalidMontage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    task_read = project_task_summary(production_repo, task)
    current_version = task_read.current_version
    person_provider = _lipsync_provider(plan.lipsync_quality) if plan.presenter_share_target > 0 else None
    person_render_available = bool(person_provider and person_provider.ready)
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
            "task": task_read.summary,
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
                "model_id": person_provider.model_id if person_provider else "",
                "message": person_provider.message if person_provider else "0%人物，本任务不需要口型能力。",
                "repair_action": (
                    None
                    if person_render_available or person_provider is None
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
def _assistant_profile_snapshot(request: Request, kind: str, profile_id: str, version: str) -> dict:
    workspace_id = current_workspace_id(request)
    user_id, diagnostic = _content_reader(request, workspace_id)
    try:
        profile = (production_repo.get_person_profile(workspace_id, profile_id, version) if kind == "person"
                   else production_repo.get_versioned_voice_profile(workspace_id, profile_id, version))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="所引用档案版本不存在或当前不可访问。") from exc
    if not production_repo.profile_readable(profile, user_id, diagnostic):
        raise HTTPException(status_code=404, detail="所引用档案版本不存在或当前不可访问。")
    response = (_person_profile_response(profile, request) if kind == "person"
                else _versioned_voice_profile_response(profile, request))
    facts = response.model_dump(mode="json")
    safe_fields = {key: facts[key] for key in ("display_name", "version", "runnable", "runnable_reason", "use_state")}
    if kind == "voice":
        safe_fields.update(language=facts["language"], model_id=facts["model_id"],
                           person_profile_id=facts["person_profile_id"], person_version=facts["person_version"])
    else:
        safe_fields.update(background_capabilities=facts["background_capabilities"], shot_capabilities=facts["shot_capabilities"])
    return {"kind": kind, "profile_id": profile_id, **safe_fields,
            "instruction": "固定版本档案事实，只读引用；不表示已用于任务，也不授权编辑、生成或发布。"}


@app.get("/api/assistant/profile-references/{kind}/{profile_id}")
def assistant_profile_reference(kind: Literal["person", "voice"], profile_id: str, request: Request, version: str):
    _assistant_member_id(request, current_workspace_id(request))
    return _assistant_profile_snapshot(request, kind, profile_id, version)


def _assistant_knowledge_snapshot(request: Request, knowledge_item_id: str, version: str, locator: str | None = None, *, include_content: bool = False) -> dict:
    workspace_id = current_workspace_id(request)
    user_id, diagnostic = _content_reader(request, workspace_id)
    try:
        item = source_repository.get_knowledge_reference_readable_by(
            workspace_id, knowledge_item_id, version, user_id=user_id, local_diagnostic=diagnostic,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="所引用知识版本不存在或当前不可访问。") from exc
    sections = [section for section in item.sections if locator is None or section.locator == locator]
    parameters = [parameter for parameter in item.parameters if locator is None or parameter.locator == locator]
    if locator is not None and not sections and not parameters:
        raise HTTPException(status_code=404, detail="所引用定位不在这个知识版本中，请重新查看该版正文。")
    facts = {"knowledge_item_id": item.id, "version": item.version, "locator": locator,
             "title": item.title, "content_hash": item.content_hash, "use_state": item.use_state,
             "instruction": "固定版本知识事实，只读引用；不授权编辑、用于任务或发布。"}
    if include_content:
        facts.update(body=item.body if locator is None else None,
                     sections=[{"locator": section.locator, "title": section.title, "text": section.text} for section in sections],
                     parameters=[{"locator": parameter.locator, "key": parameter.key, "value": parameter.value, "unit": parameter.unit} for parameter in parameters])
    return facts


@app.get("/api/assistant/knowledge-references/{knowledge_item_id}")
def assistant_knowledge_reference(knowledge_item_id: str, request: Request, version: str, locator: str | None = None):
    _assistant_member_id(request, current_workspace_id(request))
    return _assistant_knowledge_snapshot(request, knowledge_item_id, version, locator, include_content=True)


def _assistant_creation_request_for_turn(request: Request, turn: dict) -> dict | None:
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, turn["conversation_id"])
    from app.assistant.inputs import active_input, latest_consumed_input
    current_turn = conversation_repo.get_turn(turn["id"])
    latest = latest_consumed_input(current_turn["events"])
    if latest and latest["status"] == "interrupted":
        return None
    active = active_input(current_turn["events"])
    request_key = active["input_id"] if active else turn["id"]
    owner, diagnostic = _content_reader(request, workspace_id)
    if not owner:
        return None
    with production_repo._connect() as conn:
        saved = conn.execute("SELECT id FROM creation_requests WHERE workspace_id=? AND owner_user_id=? AND request_key=?",
                             (workspace_id, owner, request_key)).fetchone()
    if saved is None:
        return None
    return production_repo.get_creation_request(workspace_id, saved["id"],
        viewer_user_id=owner, local_diagnostic=diagnostic)


def _assistant_conversation_response(row: dict, request: Request) -> dict:
    turns = conversation_repo.list_turns(row["id"])
    for turn in turns:
        if turn["status"] != "completed" or (turn.get("result") or {}).get("kind") != "conversation":
            continue
        saved = _assistant_creation_request_for_turn(request, turn)
        if saved is not None:
            turn["result"] = {**turn["result"], "kind": "creation_request", "creation_request_id": saved["id"]}
    return {**row, "turns": turns}


def _assistant_conversation_for_request(request: Request, conversation_id: str) -> tuple[str, str, dict]:
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    row = conversation_repo.get(conversation_id, workspace_id, member_id)
    if row is None:
        raise HTTPException(status_code=404, detail="对话不存在或当前成员无权访问。")
    return workspace_id, member_id, row


@app.post("/api/assistant/conversations/current", response_model=AssistantConversationResponse)
def current_assistant_conversation(request: Request, payload: AssistantConversationCurrentRequest):
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    _validate_assistant_scope(request, payload.scope_kind, payload.scope_id)
    row = conversation_repo.current_or_create(workspace_id, member_id, payload.scope_kind, payload.scope_id)
    return AssistantConversationResponse.model_validate(_assistant_conversation_response(row, request))


@app.post("/api/assistant/conversations", response_model=AssistantConversationResponse)
def create_assistant_conversation(request: Request, payload: AssistantConversationCreateRequest):
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    if _assistant_text_without_credentials(payload.title, workspace_id) != payload.title:
        raise HTTPException(status_code=422, detail="会话名称不能包含密钥或口令。")
    _validate_assistant_scope(request, payload.scope_kind, payload.scope_id)
    row = conversation_repo.create(workspace_id, member_id, payload.scope_kind, payload.scope_id, payload.title)
    return AssistantConversationResponse.model_validate(_assistant_conversation_response(row, request))


@app.get("/api/assistant/conversations", response_model=AssistantConversationListResponse)
def list_assistant_conversations(request: Request, scope_kind: str, scope_id: str):
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    _validate_assistant_scope(request, scope_kind, scope_id)
    rows = conversation_repo.list_for_scope(workspace_id, member_id, scope_kind, scope_id)
    if scope_kind == "material" and scope_id == "library":
        visible_rows = []
        for row in rows:
            try:
                _validate_assistant_scope(request, row["scope_kind"], row["scope_id"])
            except HTTPException as exc:
                if exc.status_code not in {404, 422}:
                    raise
                continue
            visible_rows.append(row)
        rows = visible_rows
    return AssistantConversationListResponse.model_validate({
        "items": [_assistant_conversation_response(row, request) for row in rows],
    })


@app.get("/api/assistant/conversations/{conversation_id}", response_model=AssistantConversationResponse)
def get_assistant_conversation(conversation_id: str, request: Request):
    _workspace_id, _member_id, row = _assistant_conversation_for_request(request, conversation_id)
    return AssistantConversationResponse.model_validate(_assistant_conversation_response(row, request))


@app.get("/api/assistant/conversations/{conversation_id}/requests/{client_request_id}", response_model=AssistantConversationRequestResponse)
def get_assistant_conversation_request(conversation_id: str, client_request_id: UUID, request: Request):
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    receipt = conversation_repo.get_request_receipt(str(client_request_id), conversation_id, workspace_id, member_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="当前对话中没有这次请求的保存回执。")
    return AssistantConversationRequestResponse.model_validate(receipt)


@app.post("/api/assistant/conversations/{conversation_id}/resume", response_model=AssistantConversationResponse)
def resume_assistant_conversation(conversation_id: str, request: Request):
    workspace_id, member_id, row = _assistant_conversation_for_request(request, conversation_id)
    selected = conversation_repo.select_current(conversation_id, workspace_id, member_id, workspace_session=True)
    assert selected is not None
    return AssistantConversationResponse.model_validate(_assistant_conversation_response(selected, request))


@app.post(
    "/api/assistant/conversations/{conversation_id}/turns/{turn_id}/plan-preview",
    response_model=CreationPlanPreviewResponse,
)
def update_assistant_plan_preview(
    conversation_id: str, turn_id: UUID, request: Request, payload: CreationPlanPreviewRequest,
):
    workspace_id, _member_id, conversation = _assistant_conversation_for_request(request, conversation_id)
    turn = conversation_repo.get_turn(str(turn_id))
    if (turn is None or turn["conversation_id"] != conversation_id or turn["status"] != "completed"
        or not isinstance(turn["result"], dict) or turn["result"].get("kind") != "plan_preview"):
        raise HTTPException(status_code=409, detail="所选对话轮次没有可更新的制作方案预览。")
    plan = _creation_plan_preflight(request, workspace_id, payload)
    response = _creation_plan_preview_response(plan)
    if not conversation_repo.update_completed_plan_preview(str(turn_id), response.model_dump(mode="json")):
        raise HTTPException(status_code=409, detail="方案轮次已变化，请重新读取这段对话后再调整。")
    return response


def _validate_saved_assistant_reference(request: Request, reference: dict | None) -> dict | None:
    if reference is None:
        return None
    if reference["kind"] == "selection":
        try:
            return normalize_selection({"kind": "selection", "items": [
                _validate_saved_assistant_reference(request, item) for item in reference["items"]
            ]})
        except AssistantReferenceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    workspace_id = current_workspace_id(request)
    if _assistant_text_without_credentials(reference["label"], workspace_id) != reference["label"]:
        raise HTTPException(status_code=422, detail="引用名称不能包含密钥或口令。")
    if reference["kind"] == "material":
        selection, _asset = _assistant_material_selection_snapshot(request, reference["selection"])
        # Store identity and fixed revisions; actual segment ranges remain server-owned.
        reference = {**reference, "selection":{key:value for key,value in selection.items() if key not in ("start_seconds","end_seconds")}}
    elif reference["kind"] == "knowledge":
        _assistant_knowledge_snapshot(request, reference["knowledge_item_id"], reference["version"], reference.get("locator"))
    elif reference["kind"] in {"shot", "narration", "audio", "subtitle", "effect"}:
        task, _member_id = _exact_edit_access(reference["task_id"], request)
        version = _timeline_version_or_404(workspace_id, task.id, reference["version"])
        try:
            return validate_timeline_reference(reference, task=task, timeline=version,
                subtitle_tracks=production_repo.list_subtitle_tracks(workspace_id, task.id, timeline_version_id=version.id))
        except AssistantReferenceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        _assistant_profile_snapshot(request, reference["kind"], reference["profile_id"], reference["version"])
    return reference


def _assistant_reference_response(request: Request, row: dict) -> dict:
    reference = row["selected_reference"]
    reason = None
    try:
        _validate_saved_assistant_reference(request, reference)
    except HTTPException as exc:
        if exc.status_code not in (404,409):
            raise
        reason = str(exc.detail)
    return {"selected_reference":reference,"reference_version":row["reference_version"],
            "status":"unavailable" if reason else "ready","reason":reason}


@app.get("/api/assistant/conversations/{conversation_id}/reference", response_model=AssistantConversationReferenceResponse)
def get_assistant_conversation_reference(conversation_id: str, request: Request):
    _workspace_id, _member_id, row = _assistant_conversation_for_request(request, conversation_id)
    return _assistant_reference_response(request, row)


@app.patch("/api/assistant/conversations/{conversation_id}/reference", response_model=AssistantConversationReferenceResponse)
def save_assistant_conversation_reference(conversation_id: str, request: Request, payload: AssistantConversationReferenceRequest):
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    reference = _validate_saved_assistant_reference(request, payload.selected_reference.model_dump(mode="json",exclude_none=True) if payload.selected_reference else None)
    try:
        saved = conversation_repo.save_reference(conversation_id, workspace_id, member_id, reference, payload.expected_reference_version)
    except ConversationReferenceVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _assistant_reference_response(request, saved)


@app.patch("/api/assistant/conversations/{conversation_id}", response_model=AssistantConversationResponse)
def update_assistant_conversation(
    conversation_id: str, request: Request, payload: AssistantConversationUpdateRequest,
):
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    if payload.title is not None and _assistant_text_without_credentials(payload.title, workspace_id) != payload.title:
        raise HTTPException(status_code=422, detail="会话名称不能包含密钥或口令。")
    if payload.draft_text is not None and _assistant_text_without_credentials(payload.draft_text, workspace_id) != payload.draft_text:
        raise HTTPException(status_code=422, detail="对话草稿不能包含密钥或口令。")
    if payload.draft_text is not None:
        try:
            updated = conversation_repo.save_draft(
                conversation_id, workspace_id, member_id, payload.draft_text,
                payload.expected_draft_version,
            )
        except ConversationDraftVersionConflict as exc:
            raise HTTPException(status_code=409, detail={
                "error": "assistant_draft_conflict",
                "message": "草稿已在其他位置更新；当前较新的草稿已保留，请重新读取后继续。",
                "current_draft_version": exc.current["draft_version"],
            }) from exc
    else:
        updated = conversation_repo.get(conversation_id, workspace_id, member_id)
    if updated is not None and payload.title is not None:
        updated = conversation_repo.update_title(conversation_id, workspace_id, member_id, payload.title)
    assert updated is not None
    return AssistantConversationResponse.model_validate(_assistant_conversation_response(updated, request))


@app.post(
    "/api/assistant/conversations/{conversation_id}/restore-unpersisted-turn",
    response_model=AssistantConversationDraftRestoreResponse,
)
def restore_unpersisted_assistant_turn_draft(
    conversation_id: str, request: Request, payload: AssistantConversationDraftRestoreRequest,
):
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    if _assistant_text_without_credentials(payload.user_message, workspace_id) != payload.user_message:
        raise HTTPException(status_code=422, detail="未发送草稿不能包含密钥或口令。")
    try:
        conversation, status = conversation_repo.restore_unpersisted_turn_draft(
            conversation_id, workspace_id, member_id, str(payload.client_request_id),
            payload.user_message, payload.expected_draft_version,
        )
    except ConversationDraftVersionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "error": "assistant_draft_conflict",
            "message": "草稿版本已经变化；较新的草稿已保留，未恢复旧句子。",
            "current_draft_version": exc.current["draft_version"],
        }) from exc
    except ConversationTurnRequestConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="对话不存在或当前成员无权访问。")
    return AssistantConversationDraftRestoreResponse.model_validate({
        "status": status,
        "conversation": _assistant_conversation_response(conversation, request),
    })


@app.post("/api/assistant/conversations/{conversation_id}/stop")
def stop_assistant_conversation_turn(
    conversation_id: str, request: Request, payload: AssistantConversationStopRequest,
):
    _workspace_id, _member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    turn = conversation_repo.get_turn(str(payload.turn_id))
    if turn is None or turn["conversation_id"] != conversation_id:
        raise HTTPException(status_code=404, detail="对话轮次不存在。")
    if turn["status"] != "running":
        raise HTTPException(status_code=409, detail="这一轮已经结束，未重复执行停止。")
    if not request_stop_turn(str(payload.turn_id)):
        raise HTTPException(status_code=409, detail="这一轮已结束或没有可停止的助手进程。")
    _goal_service().invalidate(str(payload.turn_id), "user_stopped")
    return {"turn_id": str(payload.turn_id), "status": "stopping"}


@app.get("/api/assistant/conversations/{conversation_id}/inputs")
def list_assistant_inputs(conversation_id: str, request: Request):
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    return {"items": conversation_repo.list_inputs(conversation_id, workspace_id, member_id)}


@app.post("/api/assistant/conversations/{conversation_id}/inputs")
def enqueue_assistant_input(conversation_id: str, request: Request, payload: AssistantConversationInputRequest):
    workspace_id, member_id, conversation = _assistant_conversation_for_request(request, conversation_id)
    if _assistant_text_without_credentials(payload.message, workspace_id) != payload.message:
        raise HTTPException(status_code=422, detail="请勿在对话中粘贴密钥或口令。")
    context = conversation_repo.consumed_turn_request_context(
        str(payload.target_turn_id), conversation_id, workspace_id, member_id)
    if not context:
        raise HTTPException(status_code=404, detail="目标轮次不存在或当前不可访问。")
    previous = next((item for item in conversation_repo.list_inputs(conversation_id, workspace_id, member_id)
                     if item["input_id"] == str(payload.client_request_id)), None)
    if previous is not None:
        context = previous["request_context"]
    else:
        if conversation["reference_version"] != payload.expected_reference_version:
            raise HTTPException(status_code=409, detail="选中引用已变化，请重新读取后发送。")
        reference = _validate_saved_assistant_reference(request, conversation["selected_reference"])
        context = _assistant_input_context(request, context, reference)
        context["expected_reference_version"] = payload.expected_reference_version
    try:
        item, _created = conversation_repo.enqueue_input(
            conversation_id, workspace_id, member_id,
            target_turn_id=str(payload.target_turn_id), client_request_id=str(payload.client_request_id),
            message=payload.message, mode=payload.mode, expected_draft_version=payload.expected_draft_version,
            expected_reference_version=payload.expected_reference_version, request_context=context,
        )
    except AssistantInputConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="目标会话或轮次不存在。") from exc
    return {"input": item, "draft_version": payload.expected_draft_version + 1}


@app.post("/api/assistant/conversations/{conversation_id}/inputs/{input_id}/withdraw")
def withdraw_assistant_input(conversation_id: str, input_id: str, request: Request):
    workspace_id, member_id, _row = _assistant_conversation_for_request(request, conversation_id)
    try:
        return conversation_repo.transition_input(conversation_id, workspace_id, member_id, input_id, "withdrawn")
    except AssistantInputConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="追加输入不存在。") from exc


@app.get("/api/assistant/capabilities")
def assistant_capabilities(request: Request):
    workspace_id = current_workspace_id(request)
    _assistant_member_id(request, workspace_id)
    pi_check = next(check for check in pi_runtime_checks() if check["id"] == "pi")
    return {
        "application_commands": ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
        "runtime": {"owner": "pi-coding-agent-sdk", "version": pi_check["version"],
                    "status": pi_check["status"], "reason": pi_check["reason"]},
        "pi_tui_commands": {"available": False, "commands": [],
                            "reason": "请使用本产品的会话命令；终端专用命令不在此运行。"},
        "extension_commands": {"available": False, "commands": [],
                               "reason": "创作工具由助手按任务调用。"},
        "skills": {"available": pi_check["status"] == "ready",
                   "names": ["source-speech", "narration-materials", "local-edit"],
                   "reason": "创作技能在任务需要时加载；具体制作能力仍依赖已连接模型和媒体工具。"},
        "prompt_templates": {"available": pi_check["status"] == "ready", "names": ["review-result"],
                             "reason": "结果回读模板由创作流程按需使用。"},
        "business_tools": {scope: list(tools) for scope, tools in ASSISTANT_BUSINESS_TOOLS.items()},
    }


def _begin_assistant_turn(
    request: Request, conversation_id: str, scope_kind: str, scope_id: str | None,
    message: str, client_request_id: UUID, expected_draft_version: int,
    request_context: dict[str, object], on_event, *, expected_reference_version: int | None = None,
) -> tuple[str, bool, dict]:
    workspace_id, member_id, conversation = _assistant_conversation_for_request(request, conversation_id)
    # Scope is historical metadata. The destination route has already verified
    # this turn's business object; conversation ownership grants no object access.
    if _assistant_text_without_credentials(message, workspace_id) != message:
        raise HTTPException(status_code=422, detail="请勿在对话中粘贴密钥或口令。")
    previous = conversation_repo.request_context(str(client_request_id), conversation_id, workspace_id, member_id)
    if expected_reference_version is None:
        if any(request_context.get(key) for key in ("selection", "profile_reference", "knowledge_reference", "selected_object_refs")):
            raise HTTPException(status_code=409, detail="请重新读取当前选中引用后再发送。")
        reference = None
    elif previous is not None:
        reference = previous.get("selected_reference")
    else:
        if conversation["reference_version"] != expected_reference_version:
            raise HTTPException(status_code=409, detail="选中引用已变化，请重新读取后再发送。")
        reference = _validate_saved_assistant_reference(request, conversation["selected_reference"])
    if expected_reference_version is not None:
        request_context = {**request_context, "selected_reference": reference,
            "expected_reference_version": expected_reference_version}
        target = dict(request_context.get("target_snapshot") or {})
        target["selected_references"] = reference["items"] if reference and reference["kind"] == "selection" else [reference] if reference else []
        request_context["target_snapshot"] = target
        if previous is not None:
            request_context["target_snapshot"] = previous.get("target_snapshot")
            if request_context.get("kind") == "task":
                request_context["selected_object_refs"] = previous.get("selected_object_refs", [])
        elif request_context.get("kind") == "task":
            frozen = _assistant_input_context(request, request_context, reference)
            request_context["selected_object_refs"] = frozen["selected_object_refs"]
            request_context["target_snapshot"] = {**target,
                "material_selection": frozen["target_snapshot"].get("material_selection")}
    try:
        turn, created = conversation_repo.create_turn_idempotent(
            conversation_id, workspace_id, member_id, message, str(client_request_id),
            expected_draft_version, request_context,
            expected_reference_version=expected_reference_version,
        )
    except ConversationReferenceVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConversationTurnAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConversationDraftVersionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "error": "assistant_draft_conflict",
            "message": "发送前草稿版本或正文已变化；较新的草稿已保留，请重新读取后再发送。",
            "current_draft_version": exc.current["draft_version"],
        }) from exc
    except ConversationTurnRequestConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if turn is None:
        raise HTTPException(status_code=404, detail="对话不存在或当前成员无权访问。")
    if created:
        register_turn_control(turn["id"])
        if conversation["title"] == "新对话":
            conversation_repo.update_title(conversation_id, workspace_id, member_id, message[:36])
    if on_event is not None:
        on_event({"type": "turn_started", "turn_id": turn["id"], "turn_state": turn["status"],
                  "draft_version": expected_draft_version + 1})
        if not created and turn["status"] != "running":
            on_event({"type": "turn_status", "turn_id": turn["id"], "turn_state": turn["status"]})
    return turn["id"], created, turn


def _assistant_input_context(request: Request, context: dict, reference: dict | None) -> dict:
    items = reference["items"] if reference and reference["kind"] == "selection" else [reference] if reference else []
    workspace_id = current_workspace_id(request)
    snapshot = {"selected_references": items}
    context = {**context, "selected_reference": reference}
    profiles = [item for item in items if item["kind"] in {"person", "voice"}]
    knowledge = [item for item in items if item["kind"] == "knowledge"]
    profile = profiles[0] if len(profiles) == 1 else None
    knowledge_item = knowledge[0] if len(knowledge) == 1 else None
    context["profile_reference"] = ({key: profile[key] for key in ("kind", "profile_id", "version")} if profile else None)
    context["knowledge_reference"] = ({key: knowledge_item[key] for key in ("knowledge_item_id", "version", "locator") if key in knowledge_item} if knowledge_item else None)
    snapshot["profile_reference"] = (_assistant_profile_snapshot(request, profile["kind"], profile["profile_id"], profile["version"]) if profile else None)
    snapshot["knowledge_reference"] = (_assistant_knowledge_snapshot(request, knowledge_item["knowledge_item_id"], knowledge_item["version"], knowledge_item.get("locator")) if knowledge_item else None)
    if context["kind"] == "task":
        task, _member_id = _exact_edit_access(context["task_id"], request)
        tracks = production_repo.list_subtitle_tracks(workspace_id, task.id)
        try:
            refs = timeline_object_refs(items, task.id, subtitle_tracks=tracks)
        except AssistantReferenceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        stage, refs = _assistant_turn_scope(workspace_id, task, SimpleNamespace(
            context_stage=context["stage"], selected_object_refs=refs, playhead_ms=None))
        version = (_timeline_version_or_404(workspace_id, task.id, task.current_timeline_version_id)
                   if task.current_timeline_version_id else None)
        context.update(stage=stage, selected_object_refs=refs, playhead_ms=None,
                       expected_timeline_version_id=task.current_timeline_version_id,
                       expected_timeline_sequence=version.sequence if version else 0)
        snapshot.update(timeline_version_id=task.current_timeline_version_id,
                        timeline_sequence=version.sequence if version else 0,
                        material_selection=_assistant_task_clip_material_selection(request, task, refs))
    elif context["kind"] == "material":
        materials = [item for item in items if item["kind"] == "material"]
        selection = materials[0]["selection"] if len(materials) == 1 else None
        context["selection"] = selection
        if selection:
            selection, _asset = _assistant_material_selection_snapshot(request, selection)
            snapshot.update(selection)
    context["target_snapshot"] = snapshot
    return context


def _assistant_input_delivery(request: Request, conversation_id: str, turn_id: str):
    workspace_id, member_id, _conversation = _assistant_conversation_for_request(request, conversation_id)

    def verify(item):
        try:
            current_workspace, current_member, _current = _assistant_conversation_for_request(request, conversation_id)
            if (current_workspace, current_member) != (workspace_id, member_id):
                raise PermissionError("当前成员或工作区已变化，请重新发送。")
            frozen = item["request_context"]
            reference = _validate_saved_assistant_reference(request, frozen.get("selected_reference"))
            fresh = _assistant_input_context(request, frozen, reference)
            snapshot = fresh["target_snapshot"]
            if frozen["kind"] == "task" and any(snapshot.get(key) != frozen["target_snapshot"].get(key)
                    for key in ("timeline_version_id", "timeline_sequence")):
                raise ValueError("接收追加输入后任务版本发生变化，请重新查看对象后发送；未改绑新版。")
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        material = snapshot.get("material_selection") if fresh["kind"] == "task" else fresh.get("selection")
        delta = {
            "INPUT_ID": item["input_id"], "INPUT_TURN_ID": turn_id,
            "USER_INSTRUCTION": item["message"], "SELECTED_REFS": json.dumps(fresh.get("selected_object_refs", [])),
            "SELECTED_REFERENCES": json.dumps(snapshot.get("selected_references", []), ensure_ascii=False),
            "PLAYHEAD_MS": "", "MATERIAL_SELECTION": json.dumps(material) if material else "",
            "PROFILE_REFERENCE": json.dumps(snapshot["profile_reference"]) if snapshot.get("profile_reference") else "",
            "KNOWLEDGE_REFERENCE": json.dumps(snapshot["knowledge_reference"]) if snapshot.get("knowledge_reference") else "",
            "TASK_VERSION": json.dumps({key: snapshot[key] for key in ("timeline_version_id", "timeline_sequence")}) if fresh["kind"] == "task" else "",
            "TASK_READ_READY": "", "SCRIPT_DRAFT_UPDATED_AT": "", "EDIT_CAPABILITIES": "",
            "MEDIA_SCRIPT_CAPABILITIES": "", "SCRIPT_TOOL_CAPABILITIES": "",
        }
        return {"environment": {f"AI_VIDEO_DIARY_AGENT_{key}": value for key, value in delta.items()},
                "frozen_context": {"kind": fresh["kind"], "task_id": fresh.get("task_id"),
                    "selected_object_refs": fresh.get("selected_object_refs", []), "target_snapshot": snapshot}}

    return AssistantInputDelivery(conversation_repo, conversation_id=conversation_id, workspace_id=workspace_id,
        member_id=member_id, turn_id=turn_id, verify=verify)


def _frozen_assistant_references(request: Request, conversation_id: str, turn_id: str) -> list[dict]:
    workspace_id, member_id = _member_owned_content_scope(request)
    context = conversation_repo.consumed_turn_request_context(
        turn_id, conversation_id, workspace_id, member_id)
    return (context.get("target_snapshot") or {}).get("selected_references", [])


def _replay_assistant_turn(turn_id: str, response_model, *, provider: str, model: str,
                           draft_version: int):
    turn = conversation_repo.get_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="对话轮次不存在。")
    if turn["status"] == "failed":
        failure = turn.get("result") or {}
        raise HTTPException(status_code=503, detail=failure.get("message", "上一轮已失败，请检查后明确重试。"))
    if turn["status"] in {"completed", "stopped"}:
        stored = turn.get("result")
        response = stored.get("response") if isinstance(stored, dict) else None
        if not isinstance(response, dict):
            raise HTTPException(status_code=500, detail="已持久化轮次缺少原始结果，未重复执行工具。")
        return response_model.model_validate(response)
    return response_model(
        reply=turn["assistant_text"], provider=provider, model=model,
        turn_id=turn_id, turn_status="running", draft_version=draft_version,
    )


@app.post(
    "/api/assistant/plan/turn",
    response_model=WorkspaceAssistantPlanTurnResponse,
)
def workspace_assistant_plan_turn(
    request: Request,
    payload: WorkspaceAssistantPlanTurnRequest,
) -> WorkspaceAssistantPlanTurnResponse:
    if "text/event-stream" in request.headers.get("accept", "").lower():
        workspace_id = current_workspace_id(request)
        return _assistant_stream_response(
            lambda on_event: _workspace_assistant_plan_turn(request, payload, on_event),
            workspace_id=workspace_id,
        )
    return _workspace_assistant_plan_turn(request, payload, None)


def _workspace_assistant_plan_turn(
    request: Request,
    payload: WorkspaceAssistantPlanTurnRequest,
    on_event,
    *, material_selection: dict | None = None,
) -> WorkspaceAssistantPlanTurnResponse:
    workspace_id = current_workspace_id(request)
    workspace_settings_for_turn = _workspace_settings_for(workspace_id)
    caller_user_id, local_diagnostic = _content_reader(request, workspace_id)
    profile_reference = (_assistant_profile_snapshot(request, payload.selected_profile_reference.kind,
                         payload.selected_profile_reference.profile_id, payload.selected_profile_reference.version)
                         if payload.selected_profile_reference is not None else None)
    knowledge_reference = (_assistant_knowledge_snapshot(request, payload.selected_knowledge_reference.knowledge_item_id,
                           payload.selected_knowledge_reference.version, payload.selected_knowledge_reference.locator)
                           if payload.selected_knowledge_reference is not None else None)
    turn_id, created, turn = _begin_assistant_turn(
        request, str(payload.conversation_id), "home", "home", payload.message.strip(),
        payload.client_request_id, payload.expected_draft_version, {"kind": "home", "profile_reference": payload.selected_profile_reference.model_dump() if payload.selected_profile_reference else None, "knowledge_reference": payload.selected_knowledge_reference.model_dump() if payload.selected_knowledge_reference else None, "target_snapshot": {"profile_reference": profile_reference, "knowledge_reference": knowledge_reference, "material_selection": material_selection}}, on_event,
        expected_reference_version=payload.expected_reference_version,
    )
    if not created:
        settings_for_replay = _workspace_settings_for(workspace_id)
        response = _replay_assistant_turn(
            turn_id, WorkspaceAssistantPlanTurnResponse,
            provider=settings_for_replay.effective_llm_provider,
            model=settings_for_replay.effective_llm_model,
            draft_version=payload.expected_draft_version + 1,
        )
        _register_adopted_creation_goals(request, turn_id, response.creation_request)
        return response
    preview = None
    try:
        reply, preview = run_plan_turn(
            workspace_settings_for_turn,
            workspace_id=workspace_id,
            local_member_token=_local_member_token(request) or None,
            caller_user_id=caller_user_id if not local_diagnostic else None,
            conversation_id=str(payload.conversation_id),
            message=payload.message.strip(),
            authorization=request.headers.get("authorization"),
            cookie=request.headers.get("cookie"),
            on_event=on_event,
            turn_id=turn_id,
            selected_references=_frozen_assistant_references(request, str(payload.conversation_id), turn_id),
            input_handler=_assistant_input_delivery(request, str(payload.conversation_id), turn_id),
            profile_reference=profile_reference,
            knowledge_reference=knowledge_reference,
            keep_turn_control=True, material_selection=material_selection,
        )
        if isinstance(preview, dict) and preview.get("kind") == "creation_request":
            saved_request = preview["request"]
            save_only = re.search(r"只(?:先)?保存|仅保存|不(?:要|用)(?:准备|规划|出)(?:方案|计划)", payload.message)
            if saved_request["status"] == "awaiting_plan" and not saved_request["clarification_questions"] and not save_only:
                latest = _creation_request_or_404(request, saved_request["id"])
                try:
                    next_reply, next_preview = run_plan_turn(
                        workspace_settings_for_turn, workspace_id=workspace_id,
                        local_member_token=_local_member_token(request) or None,
                        caller_user_id=caller_user_id if not local_diagnostic else None,
                        conversation_id=str(payload.conversation_id), message=payload.message.strip(),
                        authorization=request.headers.get("authorization"), cookie=request.headers.get("cookie"),
                        on_event=on_event, turn_id=turn_id, profile_reference=profile_reference,
                        selected_references=_frozen_assistant_references(request, str(payload.conversation_id), turn_id),
                        input_handler=_assistant_input_delivery(request, str(payload.conversation_id), turn_id),
                        knowledge_reference=knowledge_reference,
                        creation_request=_creation_request_response(latest).model_dump(mode="json"),
                        keep_turn_control=True, material_selection=material_selection,
                    )
                    if isinstance(next_preview, dict) and next_preview.get("kind") == "creation_request":
                        reply, preview = next_reply, next_preview
                except AssistantUnavailable as exc:
                    reply = "制作目标已保存，当前这组方案尚未完成。" + _assistant_stream_safe_text(str(exc), workspace_id)
    except AssistantStopped:
        saved = conversation_repo.get_turn(turn_id)
        saved_goal = preview.get("request") if isinstance(preview, dict) and preview.get("kind") == "creation_request" else None
        response = WorkspaceAssistantPlanTurnResponse(
            reply=saved["assistant_text"] if saved else "", provider=workspace_settings_for_turn.effective_llm_provider,
            model=workspace_settings_for_turn.effective_llm_model, turn_id=turn_id, turn_status="stopped",
            draft_version=payload.expected_draft_version + 1,
            creation_request=CreationRequestResponse.model_validate(saved_goal) if saved_goal else None,
        )
        forget_turn_control(turn_id)
        conversation_repo.finish_turn(turn_id, "stopped", result={"kind": "creation_request" if saved_goal else "conversation",
            "creation_request_id": saved_goal["id"] if saved_goal else None, "response": response.model_dump(mode="json")})
        return response
    except AssistantUnavailable as exc:
        forget_turn_control(turn_id)
        conversation_repo.finish_turn(turn_id, "failed", result={
            "kind": "failure", "message": _assistant_stream_safe_text(str(exc), workspace_id),
        })
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    material_result = None
    if isinstance(preview, dict) and preview.get("kind") == "material_result":
        try:
            material_result = MaterialAssistantResultResponse.model_validate(preview.get("material_result"))
        except ValidationError as exc:
            forget_turn_control(turn_id)
            conversation_repo.finish_turn(turn_id, "failed", result={"kind": "failure", "message": "素材结果格式不可核验，未宣称保存。"})
            raise HTTPException(status_code=502, detail="素材结果格式不可核验，未宣称保存。") from exc
    if material_result is not None:
        materials = [item["selection"] for item in _frozen_assistant_references(request, str(payload.conversation_id), turn_id)
                     if item["kind"] == "material"]
        bound = materials[0] if len(materials) == 1 else material_selection if not materials else None
        if (bound is None or material_result.scope != bound["scope"]
                or material_result.asset_ref != f"asset:{bound['asset_id']}"
                or material_result.segment_ref != (f"segment:{bound['segment_id']}" if bound["scope"] == "segment" else None)):
            forget_turn_control(turn_id)
            conversation_repo.finish_turn(turn_id, "failed", result={"kind": "failure", "message": "素材结果没有通过当前选择核对，未宣称保存。"})
            raise HTTPException(status_code=502, detail="素材结果没有通过当前选择核对，未宣称保存。")
        preview = None
    request_result = preview.get("request") if isinstance(preview, dict) and preview.get("kind") == "creation_request" else None
    if request_result is None and material_result is None:
        request_result = _assistant_creation_request_for_turn(request, conversation_repo.get_turn(turn_id))
        if request_result is not None:
            preview = None
    creation_request = CreationRequestResponse.model_validate(request_result) if request_result is not None else None
    plan_preview = CreationPlanPreviewResponse.model_validate(preview) if preview is not None and creation_request is None else None
    forget_turn_control(turn_id)
    safe_reply = _assistant_text_without_credentials(reply, workspace_id)
    response = WorkspaceAssistantPlanTurnResponse(
        reply=safe_reply,
        provider=workspace_settings_for_turn.effective_llm_provider,
        model=workspace_settings_for_turn.effective_llm_model,
        plan_preview=plan_preview,
        creation_request=creation_request, material_result=material_result,
        turn_id=turn_id,
        draft_version=payload.expected_draft_version + 1,
    )
    conversation_repo.finish_turn(turn_id, "completed", safe_reply, {
        "kind": "creation_request" if creation_request else "plan_preview" if plan_preview else "material_result" if material_result else "conversation",
        "creation_request_id": creation_request.id if creation_request else None,
        "plan_preview": plan_preview.model_dump(mode="json") if plan_preview else None,
        "response": response.model_dump(mode="json"),
    })
    _register_adopted_creation_goals(request, turn_id, creation_request)
    return response


@app.post("/api/assistant/material/turn", response_model=MaterialAssistantTurnResponse)
def material_assistant_turn(
    request: Request,
    payload: MaterialAssistantTurnRequest,
) -> MaterialAssistantTurnResponse:
    if "text/event-stream" in request.headers.get("accept", "").lower():
        workspace_id = current_workspace_id(request)
        return _assistant_stream_response(
            lambda on_event: _material_assistant_turn(request, payload, on_event),
            workspace_id=workspace_id,
        )
    return _material_assistant_turn(request, payload, None)


def _assistant_material_selection_snapshot(request: Request, selection: dict, *, replay: bool = False) -> tuple[dict, object]:
    selection = dict(selection)
    asset = get_production_material(selection["asset_id"], request)
    expected = selection.get("expected_updated_at")
    if not replay and expected is not None and datetime.fromisoformat(str(expected).replace("Z","+00:00")) != asset.updated_at:
        raise HTTPException(status_code=409, detail="所选素材已更新，请重新查看后发送。")
    selection["expected_updated_at"] = asset.updated_at.isoformat()
    if selection["scope"] == "segment":
        segment = next((item for item in asset.segments if item.id == selection["segment_id"] and item.asset_id == asset.id), None)
        if segment is None:
            raise HTTPException(status_code=404, detail="所选片段不属于当前可见素材")
        expected_segment = selection.get("expected_segment_updated_at")
        if not replay and expected_segment is not None and datetime.fromisoformat(str(expected_segment).replace("Z","+00:00")) != segment.updated_at:
            raise HTTPException(status_code=409, detail="所选片段已更新，请重新查看后发送。")
        selection.update(expected_segment_updated_at=segment.updated_at.isoformat(),start_seconds=segment.start_seconds,end_seconds=segment.end_seconds)
    return selection, asset


def _assistant_task_clip_material_selection(request: Request, task, selected_refs: list[str]) -> dict | None:
    if len(selected_refs) != 1 or not selected_refs[0].startswith("clip:"):
        return None
    workspace_id = current_workspace_id(request)
    try:
        plan = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task.id))
        clip = next((item for item in plan.clips if f"clip:{item.id}" == selected_refs[0]), None)
        # Generated presenter output is not a library segment, even when legacy
        # clips happen to retain an asset identity. Never fall back to that asset.
        if clip is None or clip.person_render_id or clip.asset_id.startswith("person:") or clip.source_path.startswith("person-render://"):
            return None
        asset = get_production_material(clip.asset_id, request)
        segment = next((item for item in asset.segments if item.id == clip.segment_id and item.asset_id == asset.id), None)
        if segment is None:
            return None
        selection, _asset = _assistant_material_selection_snapshot(request, {
            "scope": "segment", "asset_id": asset.id, "segment_id": segment.id,
            "expected_updated_at": asset.updated_at.isoformat(),
            "expected_segment_updated_at": segment.updated_at.isoformat(),
        })
        return selection
    except KeyError:
        return None
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _material_assistant_turn(
    request: Request,
    payload: MaterialAssistantTurnRequest,
    on_event,
) -> MaterialAssistantTurnResponse:
    selection = None
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    previous = conversation_repo.request_context(str(payload.client_request_id), str(payload.conversation_id), workspace_id, member_id)
    if payload.selection is not None:
        selection, _asset = _assistant_material_selection_snapshot(
            request, payload.selection.model_dump(mode="json", exclude_none=True), replay=previous is not None)
        if previous is not None:
            selection = previous["target_snapshot"].get("material_selection")
    response = _workspace_assistant_plan_turn(request, WorkspaceAssistantPlanTurnRequest(
        **payload.model_dump(exclude={"selection"})), on_event, material_selection=selection)
    return MaterialAssistantTurnResponse.model_validate(response.model_dump())


def _creation_request_response(data: dict) -> CreationRequestResponse:
    current = data.get("current_window")
    if current is not None:
        current = {**current, "entries": [_creation_request_item_response(item) for item in current["entries"]]}
    return CreationRequestResponse.model_validate({**data, "current_window": current,
        "intent": data["requirements"].get("creation_intent"),
        "requirements": {key: value for key, value in data["requirements"].items() if key != "creation_intent"}})


def _creation_request_item_response(item: dict) -> dict:
    plan = item.get("plan")
    task_payload = item.get("task")
    task = production_repo._creation_task_from_payload(task_payload) if task_payload else None
    return {**item, "plan": _creation_plan_preview_response(
        production_repo._creation_plan_from_payload(plan)
    ).model_dump(mode="json") if plan else None,
        "task": project_task_summary(production_repo, task).summary if task else None}


def _creation_request_or_404(request: Request, request_id: str) -> dict:
    workspace_id = current_workspace_id(request)
    owner, diagnostic = _content_reader(request, workspace_id)
    if not owner:
        raise HTTPException(status_code=403, detail="请先确认当前成员，再读取制作目标。")
    try:
        return production_repo.get_creation_request(
            workspace_id, request_id, viewer_user_id=owner, local_diagnostic=diagnostic,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到当前成员的制作目标。") from exc


def _creation_request_questions(target_count: int, relation: str | None, scope: str) -> list[str]:
    questions = []
    if target_count > 1 and relation not in {"different_topics", "variants", "series"}:
        questions.append("这些视频是不同主题、同一主题的不同版本，还是系列分集？")
    if scope == "today_received":
        questions.append("请明确本次要用的今日接收素材，或选择使用现有素材库。")
    return questions


def _creation_number(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = section = number = 0
    for character in value:
        if character.isascii() and character.isdigit():
            number = number * 10 + int(character)
        elif character in digits:
            number = number * 10 + digits[character]
        elif character == "万":
            total += (section + number) * 10000
            section = number = 0
        else:
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[character]
            number = 0
    return total + section + number


def _explicit_creation_constraints(goal: str) -> tuple[int | None, int | None]:
    from app.creation.intent_use_case import CreationIntentUseCase
    number = r"[0-9零〇一二两三四五六七八九十百千万]+"
    count = re.search(rf"({number})\s*(?:条|个|支)(?:短)?视频|(?:做|制作|生成|剪成)\s*({number})\s*(?:条|个|支)(?!镜头)", goal)
    duration = re.search(rf"(?:每条|每个|每支)(?:视频)?(?:为|做成|时长|要|控制在|长|约|大约|是|的|\s)*({number})\s*(分钟|秒钟|秒)", goal)
    if duration is None:
        duration = re.search(rf"(?:做成|制作|剪成)(?:一(?:个|条))?\s*({number})\s*(分钟|秒钟|秒)(?:的)?(?:短)?视频", goal)
    count_value = _creation_number(next(value for value in count.groups() if value)) if count else None
    if count_value is None and CreationIntentUseCase.is_bilingual_variant_goal(goal):
        count_value = 2
    duration_value = _creation_number(duration[1]) * (60 if duration[2] == "分钟" else 1) if duration else None
    return count_value, duration_value


def _creation_request_input(request: Request, turn_id: str):
    from app.assistant.inputs import active_input, latest_consumed_input
    turn = conversation_repo.get_turn(turn_id)
    if turn is None or turn["status"] != "running":
        raise HTTPException(status_code=409, detail="当前创作回合已变化，没有保存或采用制作目标。")
    _assistant_conversation_for_request(request, turn["conversation_id"])
    latest = latest_consumed_input(turn["events"])
    if latest and latest["status"] == "interrupted":
        raise HTTPException(status_code=409, detail="当前输入已中断，不能恢复先前制作授权。")
    active = active_input(turn["events"])
    if request.headers.get("x-assistant-input-id") != (active["input_id"] if active else None):
        raise HTTPException(status_code=409, detail="制作目标不属于当前已消费输入，没有沿旧输入保存或采用。")
    return turn, active["message"] if active else turn["user_message"], active


@app.post("/api/creation-requests", response_model=CreationRequestResponse, status_code=201)
def create_creation_request(request: Request, payload: CreationRequestCreateRequest):
    workspace_id = current_workspace_id(request)
    owner, diagnostic = _content_reader(request, workspace_id)
    if not owner:
        raise HTTPException(status_code=403, detail="请先确认当前成员，再保存制作目标。")
    goal = payload.goal
    source_input = None
    if payload.assistant_turn_id is not None:
        turn, goal, source_input = _creation_request_input(request, str(payload.assistant_turn_id))
        if str(payload.request_key) != (source_input["input_id"] if source_input else turn["id"]):
            raise HTTPException(status_code=409, detail="制作目标必须沿本轮固定身份保存，不能重复建立。")
    stated_count, stated_duration = _explicit_creation_constraints(goal)
    if stated_count is not None and stated_count != payload.target_count:
        raise HTTPException(status_code=409, detail="总数量与用户原句不一致，没有保存错误的制作目标。")
    if stated_duration is not None and stated_duration != payload.duration_target_seconds:
        raise HTTPException(status_code=409, detail="目标时长与用户原句不一致，没有保存错误的制作要求。")
    # A model enum cannot impose a date restriction absent from the user's goal.
    scope = "today_received" if re.search(r"(?:今天|今日)(?:我)?(?:接收|收到|发给|传给|导入|上传|发来|传来)", goal) else ("workspace_library" if payload.assistant_turn_id else payload.material_scope)
    if re.search(r"(?:不限制|不限|不按|无需).{0,4}(?:今天|今日)", goal):
        scope = "workspace_library"
    relation = "single" if payload.target_count == 1 else payload.output_relation
    if payload.assistant_turn_id is not None and payload.target_count > 1:
        from app.creation.intent_use_case import CreationIntentUseCase
        explicit_relation = ("series" if re.search(r"系列|分集", goal) else
            "variants" if CreationIntentUseCase.is_bilingual_variant_goal(goal) or re.search(r"同(?:一|一个)?主题.{0,12}(?:版本|风格)|不同版本", goal) else
            "different_topics" if re.search(r"不同主题|各自主题|多个主题", goal) else None)
        if explicit_relation is not None and relation not in {None, explicit_relation}:
            raise HTTPException(status_code=409, detail="多条视频的关系与用户原句不一致，没有保存错误的方案。")
        relation = explicit_relation
    values = payload.model_dump(mode="json", exclude={"request_key", "output_relation", "material_scope", "assistant_turn_id", "intent"})
    values.update(goal=goal, target_count=1)
    if payload.intent is not None:
        from app.creation.intent_use_case import CreationIntentUseCase
        intent = payload.intent.model_dump(mode="json")
        final = intent.get("final_script")
        if final and final.get("reply_origin"):
            origin = final["reply_origin"]
            reference, reply_text, reply_owner = _verified_assistant_reply(request, origin["conversation_id"], origin["turn_id"], None)
            if reply_owner != owner or final["text"] != reply_text:
                raise HTTPException(status_code=409, detail="定稿与指定回复的实际正文不一致，没有采用改写的版本。")
            final["source_reply_reference"] = reference
        source = ({"kind": "conversation_input", "member_id": owner, "request_id": str(payload.request_key),
            "conversation_id": turn["conversation_id"], "turn_id": turn["id"],
            "message_digest": hashlib.sha256(goal.encode()).hexdigest(),
            **({"input_id": source_input["input_id"], "input_turn_id": turn["id"]} if source_input else {})} if payload.assistant_turn_id is not None else
            {"kind": "user_action", "member_id": owner, "request_id": str(payload.request_key)})
        try:
            values["creation_intent"] = CreationIntentUseCase.receive_intent(intent, source=source,
                target_count=payload.target_count, original_message=goal if payload.assistant_turn_id else None)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    fingerprint = hashlib.sha256(json.dumps({
        "goal": goal, "target_count": payload.target_count, "output_relation": relation,
        "requirements": values, "material_scope": scope,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    try:
        data = production_repo.create_creation_request(
            workspace_id, request_key=str(payload.request_key), input_fingerprint=fingerprint,
            goal=goal, target_count=payload.target_count, output_relation=relation,
            requirements=values, material_scope={"kind": scope},
            clarification_questions=_creation_request_questions(payload.target_count, relation, scope),
            verified_owner_user_id=owner, local_diagnostic=diagnostic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _creation_request_response(data)


@app.get("/api/creation-requests/{request_id}", response_model=CreationRequestResponse)
def get_creation_request(request_id: str, request: Request):
    return _creation_request_response(_creation_request_or_404(request, request_id))


@app.patch("/api/creation-requests/{request_id}", response_model=CreationRequestResponse)
def revise_creation_request(request_id: str, request: Request, payload: CreationRequestReviseRequest):
    current = _creation_request_or_404(request, request_id)
    workspace_id = current_workspace_id(request)
    owner, diagnostic = _content_reader(request, workspace_id)
    relation = payload.output_relation or current["output_relation"]
    scope = payload.material_scope or current["material_scope"]["kind"]
    requirements = payload.requirements.model_dump(mode="json") if payload.requirements else current["requirements"]
    if payload.requirements and current["requirements"].get("creation_intent"):
        requirements["creation_intent"] = current["requirements"]["creation_intent"]
    if requirements["target_count"] != 1:
        raise HTTPException(status_code=422, detail="每条方案只制作一条视频；总数量保存在制作目标中。")
    try:
        data = production_repo.revise_creation_request(
            workspace_id, request_id, expected_revision=payload.expected_revision,
            output_relation=relation, requirements=requirements, material_scope={"kind": scope},
            clarification_questions=_creation_request_questions(current["target_count"], relation, scope),
            verified_owner_user_id=owner, local_diagnostic=diagnostic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _creation_request_response(data)


@app.post("/api/creation-requests/{request_id}/windows", response_model=CreationRequestResponse)
def preview_creation_request_window(request_id: str, request: Request, payload: CreationRequestWindowPreviewRequest):
    return _prepare_creation_request_window(request_id, request, payload)


def _prepare_creation_request_window(request_id: str, request: Request, payload: CreationRequestWindowPreviewRequest,
    *, replace_window_id: str | None = None):
    from app.creation.intent_use_case import CreationIntentUseCase
    current = _creation_request_or_404(request, request_id)
    if current["clarification_questions"]:
        raise HTTPException(status_code=409, detail="先确认制作目标中尚未明确的条件，再准备逐条方案。")
    workspace_id = current_workspace_id(request)
    owner, diagnostic = _content_reader(request, workspace_id)
    entries = []
    deliveries = [CreationPlanPreviewRequest.model_validate(CreationIntentUseCase.delivery_inputs(
        current["requirements"], goal=item.goal, name=item.name,
        overrides=item.requirements.model_dump(mode="json", exclude_unset=True) if item.requirements else {}))
        for item in payload.goals]
    try:
        CreationIntentUseCase.check_distinct_deliveries([item.model_dump(mode="json") for item in deliveries])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for inputs in deliveries:
        plan = _creation_plan_preflight(request, workspace_id, inputs)
        try:
            plan = CreationIntentUseCase.use_provided_copy(plan, current["requirements"].get("creation_intent"))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        plan.id = f"plan_{uuid4().hex[:12]}"
        task = CreationTask(id=f"task_{uuid4().hex[:12]}", workspace_id=workspace_id,
            plan_id=plan.id, name=plan.name, scenario=plan.goal)
        entries.append((plan, task))
    fingerprint = hashlib.sha256(json.dumps({
        "revision": payload.expected_revision, "goals": [item.model_dump() for item in payload.goals],
        "preflights": [plan.preflight_fingerprint for plan, _task in entries],
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    try:
        data = production_repo.save_creation_request_window(
            workspace_id, request_id, expected_revision=payload.expected_revision,
            input_fingerprint=fingerprint, entries=entries, verified_owner_user_id=owner, local_diagnostic=diagnostic,
            replace_window_id=replace_window_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _creation_request_response(data)


@app.post("/api/creation-requests/{request_id}/windows/{window_id}/replan", response_model=CreationRequestResponse)
def replan_creation_request_window(request_id: str, window_id: str, request: Request, payload: CreationRequestWindowReplanRequest):
    from app.creation.intent_use_case import CreationIntentUseCase
    current = _creation_request_or_404(request, request_id)
    window = current["current_window"]
    if window is None or window["id"] != window_id or window["status"] != "proposed":
        raise HTTPException(status_code=409, detail="这组方案已处理，请重新读取当前制作目标。")
    inputs = CreationRequestWindowPreviewRequest.model_validate({"expected_revision": payload.expected_revision,
        "goals": [{"goal": entry["plan"]["goal"], "name": entry["plan"]["name"],
            "requirements": {key: value for key, value in CreationIntentUseCase.frozen_delivery_inputs(
                current["requirements"], entry["plan"]).items()
                if key in {*CreationIntentUseCase.DELIVERY_FIELDS, "selected_voice", "selected_person"}}}
            for entry in window["entries"]]})
    return _prepare_creation_request_window(request_id, request, inputs, replace_window_id=window_id)


@app.post("/api/creation-requests/{request_id}/windows/{window_id}/confirm", response_model=CreationRequestResponse)
def confirm_creation_request_window(request_id: str, window_id: str, request: Request, payload: CreationRequestWindowConfirmRequest):
    from app.creation.intent_use_case import CreationIntentUseCase
    if payload.adoption_kind == "delegated_system" and payload.assistant_turn_id is not None:
        _turn, message, _input = _creation_request_input(request, str(payload.assistant_turn_id))
        try:
            CreationIntentUseCase.reject_discussion_only_input(message)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    current = _creation_request_or_404(request, request_id)
    window = current["current_window"]
    if window is None or window["id"] != window_id:
        raise HTTPException(status_code=409, detail="当前待确认方案已变化，请重新读取制作目标。")
    if window["status"] == "proposed":
        for entry in window["entries"]:
            frozen = entry["plan"]
            inputs = CreationPlanPreviewRequest.model_validate(
                CreationIntentUseCase.frozen_delivery_inputs(current["requirements"], frozen))
            try:
                live = CreationIntentUseCase.use_provided_copy(_creation_plan_preflight(request, current_workspace_id(request), inputs),
                    current["requirements"].get("creation_intent"))
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if live.preflight_fingerprint != frozen["preflight_fingerprint"]:
                raise HTTPException(status_code=409, detail="方案使用的素材、资料或制作条件已变化，请重新准备这组方案。")
    owner, diagnostic = _content_reader(request, current_workspace_id(request))
    try:
        data = production_repo.confirm_creation_request_window(
            current_workspace_id(request), request_id, window_id, expected_revision=payload.expected_revision,
            input_fingerprint=payload.input_fingerprint, verified_owner_user_id=owner, local_diagnostic=diagnostic,
            adoption_kind=payload.adoption_kind,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _creation_request_response(data)


@app.get("/api/creation-requests/{request_id}/items", response_model=CreationRequestItemsResponse)
def list_creation_request_items(request_id: str, request: Request, after_ordinal: int = 0, limit: int = 20):
    _creation_request_or_404(request, request_id)
    if after_ordinal < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="分页范围无效。")
    owner, diagnostic = _content_reader(request, current_workspace_id(request))
    data = production_repo.list_creation_request_items(current_workspace_id(request), request_id,
        after_ordinal=after_ordinal, limit=limit, viewer_user_id=owner, local_diagnostic=diagnostic)
    return CreationRequestItemsResponse.model_validate({**data,
        "items": [_creation_request_item_response(item) for item in data["items"]]})


@app.post(
    "/api/creation-plans/preview",
    response_model=CreationPlanPreviewResponse,
)
def preview_creation_plan(
    request: Request,
    payload: CreationPlanPreviewRequest,
) -> CreationPlanPreviewResponse:
    return _creation_plan_preview_response(
        _creation_plan_preflight(request, current_workspace_id(request), payload)
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
    if payload.target_count != 1:
        raise HTTPException(status_code=409, detail="多条视频需要分别准备方案，请从新建视频的总目标入口分组创建；没有创建单条任务冒充多条。")
    workspace_id = current_workspace_id(request)
    reply_reference = None
    reply_request_id = None
    reply_owner_id = None
    local_diagnostic = False
    if payload.reply_origin is not None:
        origin = payload.reply_origin
        reply_reference, _reply_text, reply_owner_id = _verified_assistant_reply(
            request, str(origin.conversation_id), str(origin.turn_id), None,
        )
        request_owner_id, local_diagnostic = _content_reader(request, workspace_id)
        if not request_owner_id or request_owner_id != reply_owner_id:
            raise HTTPException(status_code=403, detail="对话成员身份已变化，请重新打开当前工作区。")
        reply_request_id = str(origin.request_id)
    plan = _creation_plan_preflight(request, workspace_id, payload)
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
        # 方案与任务的归属只认这次请求的可信身份，调用方自填的值不参与判断。
        saved_plan, saved_task = production_repo.confirm_creation_plan(
            plan, task,
            verified_owner_user_id=reply_owner_id or _content_reader(request, workspace_id)[0],
            local_diagnostic=local_diagnostic if reply_reference is not None else _content_reader(request, workspace_id)[1],
            reply_reference=reply_reference,
            request_id=reply_request_id,
        )
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


@app.put(
    "/api/creation-plans/{plan_id}/visibility",
    response_model=CreationPlanResponse,
)
def set_creation_plan_visibility_route(
    plan_id: str,
    request: Request,
    payload: ContentVisibilityRequest,
) -> CreationPlanResponse:
    """显式共享或收回一个创作方案；响应回读同一列。"""
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    workspace_id = current_workspace_id(request)
    try:
        production_repo.set_creation_plan_visibility(workspace_id, plan_id, payload.visibility)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation plan not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plan = production_repo.get_creation_plan(workspace_id, plan_id)
    if plan.creation_task_id is None:
        raise HTTPException(status_code=404, detail="creation plan not found")
    task = production_repo.get_creation_task(workspace_id, plan.creation_task_id)
    return _creation_plan_response(plan, task)


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


def _script_library_task_access(request: Request, workspace_id: str, task_id: str) -> CreationTask:
    """读文案库引用的来源任务：可见范围按可信身份判，读不到按不存在处理。"""
    try:
        task = production_repo.get_creation_task(workspace_id, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文案来源任务不可读") from exc
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="文案来源任务不可读")
    return task


def _attach_cited_version_facts(
    state: ScriptLibrarySourceResponse,
    source_repository: SourceRepository,
    workspace_id: str,
    item_id: str,
    version: str,
) -> None:
    """回填引用绑定那一版的真实快照事实，供前端回查当时内容。

    只读版本快照里已经存下的来源链接、抓取时刻和指纹；引用本身只记到条目与
    版本，没有段落定位，所以 `locator` 留空，不拿第一段冒充引用定位。
    """
    try:
        versions = source_repository.list_knowledge_item_versions(workspace_id, item_id)
    except KeyError:
        return
    snapshot = next(
        (value for value in versions if value.version == version),
        None,
    )
    if snapshot is None:
        return
    state.cited_version = snapshot.version
    state.cited_source_url = snapshot.source_url
    state.cited_fetched_at = snapshot.fetched_at
    state.cited_content_hash = snapshot.content_hash


def _script_library_sources(
    request: Request, workspace_id: str, entry: ScriptLibraryEntry,
) -> tuple[list[ScriptLibrarySourceResponse], list[CreationPlanKnowledgeReference], list[CreationPlanSourceReference]]:
    task = _script_library_task_access(request, workspace_id, entry.source_task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    knowledge = {reference.id: reference for reference in plan.knowledge_references}
    sources = {reference.id: reference for reference in plan.source_references}
    resolved_knowledge: list[CreationPlanKnowledgeReference] = []
    resolved_sources: list[CreationPlanSourceReference] = []
    result: list[ScriptLibrarySourceResponse] = []
    references = dict.fromkeys(ref for section in entry.sections for ref in section.reference_ids)
    for ref in references:
        state = ScriptLibrarySourceResponse(reference_id=ref, status="missing")
        if ref.startswith("knowledge:"):
            parts = ref.removeprefix("knowledge:").rsplit(":", 1)
            if len(parts) == 2:
                item_id, version = parts
                user_id, local_diagnostic = _content_reader(request, workspace_id)
                try:
                    # 与知识域读取同一个判据：私有只对上传者本人，读不到当不存在。
                    item = source_repository.get_knowledge_item_readable_by(
                        workspace_id, item_id,
                        user_id=user_id, local_diagnostic=local_diagnostic,
                    )
                except (KeyError, ValueError):
                    item = None
                original = knowledge.get(item_id)
                if item is not None and original is not None:
                    # 有权读到这一版（无论是当前版还是历史版）才回填快照事实；
                    # 读不到就不给来源链接、抓取时刻和指纹。
                    _attach_cited_version_facts(
                        state, source_repository, item.workspace_id, item_id, version
                    )
                    if (item.version != version or item.version != original.version
                            or not content_allows_use(item.use_state, "reference")):
                        state.status = "version_changed"
                    else:
                        state.status = "readable"
                        state.title = item.title
                        state.version_or_identity = item.version
                        resolved_knowledge.append(original)
        elif ref.startswith("source:"):
            item_id = ref.removeprefix("source:")
            try:
                item = source_repository.get_source_item(workspace_id, item_id)
            except KeyError:
                item = None
            original = sources.get(item_id)
            if item is not None and original is not None:
                if (not original.identity or source_item_identity(item) != original.identity
                        or item.connector_id != original.connector_id or item.license != original.license):
                    state.status = "version_changed"
                elif content_allows_use(item.use_state, "reference"):
                    state.status = "readable"
                    state.title = item.title
                    state.source_url = item.source_url
                    state.version_or_identity = original.identity
                    resolved_sources.append(original)
        result.append(state)
    return result, resolved_knowledge, resolved_sources


def _readable_script_library_entry(request: Request, workspace_id: str, entry_id: str) -> tuple[ScriptLibraryEntry, list[CreationPlanKnowledgeReference], list[CreationPlanSourceReference]]:
    try:
        entry = production_repo.get_script_library_entry(workspace_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文案库条目不存在") from exc
    states, knowledge, sources = _script_library_sources(request, workspace_id, entry)
    if any(source.status != "readable" for source in states):
        raise HTTPException(status_code=409, detail="文案引用的来源已不可读或版本已变化，请核对原任务来源")
    return entry, knowledge, sources


@app.post("/api/production/script-library", response_model=ScriptLibraryEntryResponse)
def save_script_library(request: Request, payload: ScriptLibrarySaveRequest) -> ScriptLibraryEntryResponse:
    workspace_id = current_workspace_id(request)
    try:
        script = production_repo.get_creation_task_script(workspace_id, payload.source_script_id)
        # Saving an owned confirmed script does not authorize reuse of its sources.
        _script_library_task_access(request, workspace_id, script.task_id)
        entry = production_repo.save_script_library_entry(
            workspace_id, source_script_id=script.id, name=payload.name, category=payload.category,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文案不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ScriptLibraryEntryResponse.model_validate(asdict(entry))


@app.get("/api/production/script-library", response_model=ScriptLibraryListResponse)
def list_script_library(request: Request, q: str = "", category: str = "") -> ScriptLibraryListResponse:
    workspace_id = current_workspace_id(request)
    items = []
    for entry in production_repo.list_script_library_entries(workspace_id, query=q, category=category):
        try:
            _script_library_task_access(request, workspace_id, entry.source_task_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            continue
        items.append(ScriptLibraryEntryResponse.model_validate(asdict(entry)))
    return ScriptLibraryListResponse(items=items, total_count=len(items))


@app.get("/api/production/script-library/{entry_id}/sources", response_model=list[ScriptLibrarySourceResponse])
def script_library_sources(entry_id: str, request: Request) -> list[ScriptLibrarySourceResponse]:
    workspace_id = current_workspace_id(request)
    try:
        entry = production_repo.get_script_library_entry(workspace_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文案库条目不存在") from exc
    states, _, _ = _script_library_sources(request, workspace_id, entry)
    return states


@app.post("/api/production/script-library/{entry_id}/reuse", response_model=ScriptLibraryReuseResponse)
def reuse_script_library(entry_id: str, request: Request, payload: ScriptLibraryReuseRequest) -> ScriptLibraryReuseResponse:
    workspace_id = current_workspace_id(request)
    if not payload.requirements_confirmed:
        raise HTTPException(status_code=409, detail="请明确确认新任务要求")
    entry, knowledge, sources = _readable_script_library_entry(request, workspace_id, entry_id)
    plan = CreationPlan(
        id=f"plan_{uuid4().hex[:12]}", workspace_id=workspace_id,
        name=payload.name.strip(), goal=f"复用文案：{entry.name}（版本{entry.version}），待重新确认正文",
        target_count=1, language=entry.language, aspect_ratio=payload.aspect_ratio,
        duration_target_seconds=payload.duration_target_seconds,
        knowledge_references=knowledge, source_references=sources,
        selected_voice=None, pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="复用稿尚未选材，确认正文后沿当前任务继续", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint=f"script-library:{entry.id}",
    )
    if not plan.name:
        raise HTTPException(status_code=422, detail="请输入新任务名称")
    task, draft = production_repo.reuse_script_library_entry(workspace_id, entry.id, plan=plan)
    return ScriptLibraryReuseResponse(task_id=task.id, script=CreationTaskScriptResponse.model_validate(asdict(draft)))


@app.get("/api/creation-tasks", response_model=CreationTasksResponse)
def list_creation_tasks(request: Request) -> CreationTasksResponse:
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    tasks = production_repo.list_creation_tasks(
        workspace_id,
        include_private=_content_include_private(request),
        viewer_user_id=viewer_user_id,
        local_diagnostic=local_diagnostic,
    )
    return CreationTasksResponse(
        items=[project_task_summary(production_repo, item).summary for item in tasks],
        total_count=len(tasks),
    )


def _creation_task_or_404(workspace_id: str, task_id: str) -> CreationTask:
    try:
        return production_repo.get_creation_task(workspace_id, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc


def _readable_creation_task(request: Request, task_id: str) -> CreationTask:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="creation task not found")
    return task


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
    reason_code: str | None = None,
    section_index: int | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": error,
            "reason": reason,
            "impact": impact,
            "repair_action": repair_action,
            "retryable": retryable,
            **({"reason_code": reason_code} if reason_code is not None else {}),
            **({"section_index": section_index} if section_index is not None else {}),
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
    route_snapshot = _topic_route_snapshot(workspace_id)
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
    _require_automation_task_open(request,workspace_id,task_id)
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    route_snapshot = _topic_route_snapshot(workspace_id)
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
        generated_topics = _creation_task_topic_generator_for(_workspace_settings_for(workspace_id)).generate(
            CreationTaskTopicInput(
                goal=plan.goal,
                language=plan.language,
                sources=provider_sources,
                style_instruction=str(plan.template_settings.get("style_instruction", "")),
                script_structure=tuple(plan.template_settings.get("script_structure", [])),
            )
        )
    except CreationTaskTopicGenerationError as exc:
        if exc.code not in {"input_invalid", "route_configuration_invalid"}:
            record_llm_service_verification(
                _workspace_settings_for(workspace_id), ok=False, cause=exc.code,
                detail=exc.reason, repair_action=exc.repair_action,
                workspace_id=workspace_id,
            )
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

    record_llm_service_verification(
        _workspace_settings_for(workspace_id), ok=True, cause="verified",
        workspace_id=workspace_id,
    )
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
    payload: CreationTaskTopicConfirmRequest,
) -> CreationTaskTopicsResponse:
    workspace_id = current_workspace_id(request)
    if payload.adoption_kind == "delegated_system" and payload.assistant_turn_id is not None:
        _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id))
    try:
        task, _ = production_repo.confirm_topic_candidate(
            workspace_id,
            task_id,
            topic_id,
            title=payload.title,
            adoption_kind=payload.adoption_kind, verified_owner_user_id=_content_reader(request, workspace_id)[0],
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


@app.put(
    "/api/creation-tasks/{task_id}/visibility",
    response_model=CreationTaskWorkspaceResponse,
)
def set_creation_task_visibility_route(
    task_id: str,
    request: Request,
    payload: ContentVisibilityRequest,
) -> CreationTaskWorkspaceResponse:
    """显式共享或收回一条创作任务；响应回读同一列。"""
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    workspace_id = current_workspace_id(request)
    try:
        production_repo.set_creation_task_visibility(workspace_id, task_id, payload.visibility)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _creation_task_workspace_response(
        workspace_id,
        _creation_task_or_404(workspace_id, task_id),
    )


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
        _readable_creation_task(request, task_id),
    )


@app.post("/api/creation-tasks/{task_id}/knowledge-adoptions", response_model=TaskKnowledgeAdoptionResponse)
def adopt_creation_task_knowledge(
    task_id: str, request: Request, payload: TaskKnowledgeAdoptionRequest,
) -> TaskKnowledgeAdoptionResponse:
    from app.creation.knowledge_adoption_use_case import KnowledgeAdoptionCommand, TaskKnowledgeAdoptionUseCase
    task, member_id = _exact_edit_access(task_id, request, write=True)
    if payload.assistant_turn_id is not None:
        _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id), draft_only=True)
    command = KnowledgeAdoptionCommand(str(payload.request_id), payload.expected_plan_updated_at,
        payload.expected_task_updated_at, tuple((item.knowledge_item_id, item.version) for item in payload.knowledge_references))
    try:
        receipt = TaskKnowledgeAdoptionUseCase(production_repo, source_repository).adopt(task.workspace_id, task_id, member_id, command)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到可引用的固定资料版本或当前任务。") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TaskKnowledgeAdoptionResponse.model_validate(receipt)


@app.get("/api/creation-tasks/{task_id}/knowledge-adoptions/{request_id}", response_model=TaskKnowledgeAdoptionResponse)
def get_creation_task_knowledge_adoption(
    task_id: str, request_id: UUID, request: Request,
) -> TaskKnowledgeAdoptionResponse:
    task, member_id = _exact_edit_access(task_id, request)
    try:
        receipt = production_repo.get_task_knowledge_adoption(task.workspace_id, task_id, str(request_id), member_id=member_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到此成员在当前任务中的原资料采用回执。") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return TaskKnowledgeAdoptionResponse.model_validate(receipt)


def _assistant_creation_adoption_scope(task_id: str, request: Request, turn_id: str, *, draft_only: bool = False):
    from app.creation.intent_use_case import CreationIntentUseCase
    workspace_id = current_workspace_id(request)
    owner = _content_reader(request, workspace_id)[0]
    try:
        origin = conversation_repo.task_turn_source(turn_id, workspace_id, owner, task_id)
        if origin.get("input_id") != request.headers.get("x-assistant-input-id"):
            raise ValueError("采用动作不属于当前已消费输入，没有沿旧输入采用。")
        if not draft_only:
            CreationIntentUseCase.reject_discussion_only_input(origin["raw_command"])
            if re.search(r"(?:不要|先别|先不|暂不|不)(?:采用|覆盖原稿|替换原稿|覆盖已采用稿|替换已采用稿)", origin["raw_command"]):
                raise ValueError("本轮未授权采用或替换，原稿保持不变；请明确采用修订后再替换。")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return origin


def _script_topic_origin(workspace_id: str, plan: CreationPlan, topic: CreationTaskTopicCandidate) -> Literal["generated", "provided"]:
    intent = plan.creation_intent
    if not intent or not (intent.get("topic_title") or intent.get("final_script")):
        return "generated"
    run = production_repo.get_topic_generation_run(workspace_id, topic.run_id)
    return "provided" if run.task_id == topic.task_id and run.route_snapshot.get("operation") == "provided_topic" else "generated"


def _assistant_script_inputs(workspace_id: str, task_id: str):
    task = _creation_task_or_404(workspace_id, task_id)
    if task.confirmed_script_id and task.can_revise_script:
        confirmed = production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)
        if confirmed.locked_body_digest:
            raise HTTPException(status_code=409, detail="当前定稿正文已锁定，不能自动改写；请明确采用新的定稿。")
    elif task.stage != "script" or task.confirmed_script_id:
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
    if task.can_revise_script and task.confirmed_script_id:
        draft = production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)
    retained_draft = None
    if draft and not production_repo.script_knowledge_inputs_current(workspace_id, task.id, draft.id):
        retained_draft, draft = draft, None
    reply_event = next((event for event in production_repo.list_task_events(workspace_id, task.id)
                        if event.event_type == "assistant_reply_plan_confirmed"
                        and event.payload.get("plan_id") == plan.id), None)
    reply_reference = reply_event.payload.get("reply_reference") if reply_event else None
    reply_origin = ({
        "conversation_id": reply_reference["conversation_id"],
        "turn_id": reply_reference["turn_id"],
        "request_id": reply_event.payload["request_id"],
    } if isinstance(reply_reference, dict)
        and isinstance(reply_reference.get("conversation_id"), str)
        and isinstance(reply_reference.get("turn_id"), str)
        and isinstance(reply_event.payload.get("request_id"), str) else None)
    return {
        "task_id": task.id,
        "goal": plan.goal,
        "creation_intent": plan.creation_intent,
        "language": plan.language,
        "duration_target_seconds": plan.duration_target_seconds,
        "presenter_share_target": plan.presenter_share_target,
        "style_instruction": str(plan.template_settings.get("style_instruction", "")),
        "script_structure": list(plan.template_settings.get("script_structure", [])),
        "script_section_count": (plan.template_settings.get("script_section_count")
                                 or (5 if 0 < plan.presenter_share_target < 100 else None)),
        "topic": {"title": topic.title, "angle": topic.angle, "audience": topic.audience,
                  "outline": topic.outline},
        "sources": [{"reference_id": source.reference_id, "title": source.title,
                     "text": source.text} for source in sources],
        "draft": ({"id": draft.id, "title": draft.title,
                   "updated_at": draft.updated_at,
                   "sections": [asdict(section) for section in draft.sections]}
                  if draft is not None else None),
        "reply_origin": reply_origin,
        "retained_draft": ({"id": retained_draft.id, "title": retained_draft.title,
            "updated_at": retained_draft.updated_at, "reason": "资料已追加，旧稿保留；新一轮文案将使用新引用。"} if retained_draft else None),
    }


def _verified_assistant_reply(request: Request, conversation_id: str, turn_id: str, task_id: str | None) -> tuple[dict[str, str], str, str]:
    workspace_id, member_id, conversation = _assistant_conversation_for_request(request, conversation_id)
    turn = conversation_repo.get_turn(turn_id)
    result = turn.get("result") if turn else None
    response = result.get("response") if isinstance(result, dict) else None
    assistant_text = str(turn.get("assistant_text") or "") if turn else ""
    if (not turn or turn.get("conversation_id") != conversation_id
        or turn.get("status") != "completed" or not assistant_text.strip()
        or not isinstance(response, dict) or response.get("reply") != assistant_text):
        raise HTTPException(status_code=409, detail="这条回复尚未完整保存或内容已变化，请重新读取对话。")
    reference = {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "response_digest": hashlib.sha256(assistant_text.encode("utf-8")).hexdigest(),
    }
    return reference, assistant_text, member_id


def _require_current_script_inputs(
    request: Request, workspace_id: str, plan: CreationPlan,
    input_references: list[dict[str, object]],
) -> tuple[str | None, bool]:
    reader_id, local_diagnostic = _content_reader(request, workspace_id)
    knowledge_by_id = {item.id: item for item in plan.knowledge_references}
    source_by_id = {item.id: item for item in plan.source_references}
    try:
        for reference in input_references:
            kind = reference.get("kind")
            object_id = str(reference.get("object_id") or "")
            identity = str(reference.get("version_or_identity") or "")
            if kind == "knowledge":
                item = source_repository.get_knowledge_reference_readable_by(
                    workspace_id, object_id, identity, user_id=reader_id,
                    local_diagnostic=local_diagnostic,
                )
                planned = knowledge_by_id.get(object_id)
                if (planned is None or item.version != planned.version
                    or item.title != planned.title
                    or item.authorization != planned.authorization
                    or item.ownership != planned.ownership
                    or not content_allows_use(item.use_state, "reference")
                    or item.use_state == "not_for_automation"):
                    raise ValueError("知识资料的当前版本、许可或使用范围已变化")
            elif kind == "source":
                item = source_repository.get_source_item_for_use(workspace_id, object_id, purpose="reference")
                planned = source_by_id.get(object_id)
                if (planned is None or source_item_identity(item) != planned.identity
                    or item.connector_id != planned.connector_id or item.title != planned.title
                    or item.license != planned.license
                    or not content_allows_use(item.use_state, "reference")
                    or item.use_state == "not_for_automation"):
                    raise ValueError("信息源身份、许可或使用范围已变化")
            else:
                raise ValueError("文案引用来源类型无效")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail={
            "error": "assistant_reply_source_changed",
            "message": "当前任务资料已不可用或使用范围已变化，文案没有写入。",
            "repair_action": "重新核对任务资料、固定版本和用途后再试。",
        }) from exc
    return reader_id, local_diagnostic


@app.post(
    "/api/creation-tasks/{task_id}/assistant/reply-transfer",
    response_model=CreationTaskScriptsResponse,
)
def transfer_assistant_reply_to_script(
    task_id: str, request: Request, payload: AssistantReplyScriptTransferRequest,
) -> CreationTaskScriptsResponse:
    workspace_id = current_workspace_id(request)
    reference, reply_text, member_id = _verified_assistant_reply(
        request, str(payload.conversation_id), str(payload.turn_id), task_id,
    )
    task, plan, topic, _sources, input_references = _assistant_script_inputs(workspace_id, task_id)
    reader_id, _local_diagnostic = _require_current_script_inputs(
        request, workspace_id, plan, input_references,
    )
    if reader_id != member_id:
        raise HTTPException(status_code=403, detail="对话成员身份已变化，请重新打开当前工作区。")
    scripts = production_repo.list_creation_task_scripts(workspace_id, task_id)
    latest = scripts[0] if scripts else None
    script_id = latest.id if latest and payload.replace_existing_script_id == latest.id else f"script_{uuid4().hex[:12]}"
    run_id = f"script_run_{uuid4().hex[:12]}"
    title = latest.title if latest and script_id == latest.id else f"对话回复草稿 · {topic.title[:80]}"
    section = CreationTaskScriptSection(
        id=f"reply_section_{payload.request_id.hex}", title=topic.title[:160] or "对话回复",
        content=reply_text, visual_prompt="", evidence_reference_ids=[],
    )
    try:
        task, _run, _script = production_repo.transfer_completed_assistant_reply_to_script(
            workspace_id, task_id, request_id=str(payload.request_id),
            reply_reference=reference, reply_text=reply_text,
            plan_conversion_request_id=(str(payload.plan_conversion_request_id) if payload.plan_conversion_request_id else None),
            expected_plan_id=plan.id, expected_plan_updated_at=payload.expected_plan_updated_at,
            expected_topic_id=topic.id, expected_task_updated_at=payload.expected_task_updated_at,
            input_references=input_references, script_id=script_id, run_id=run_id,
            title=title, language=plan.language, sections=[section],
            replace_existing_script_id=payload.replace_existing_script_id,
            expected_script_updated_at=payload.expected_script_updated_at,
            verified_owner_user_id=reader_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="当前任务或方案不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "assistant_reply_transfer_conflict",
            "message": "当前任务或草稿已变化，回复没有写入文案。",
            "repair_action": str(exc),
        }) from exc
    return _creation_task_scripts_response(workspace_id, task)


@app.post(
    "/api/creation-tasks/{task_id}/assistant/script-draft",
    response_model=CreationTaskScriptResponse,
)
def save_assistant_script_draft(
    task_id: str, request: Request, payload: CreationTaskScriptDraftRequest,
) -> CreationTaskScriptResponse:
    from app.topics import CreationTaskGeneratedScriptSection

    workspace_id = current_workspace_id(request)
    instruction_origin = None
    if payload.assistant_turn_id is not None:
        instruction_origin = _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id), draft_only=True)
    task, plan, topic, sources, input_references = _assistant_script_inputs(workspace_id, task_id)
    reader = _require_current_script_inputs(request, workspace_id, plan, input_references)
    sections = [CreationTaskScriptSection(**section.model_dump()) for section in payload.sections]
    allowed = {str(item["reference_id"]) for item in input_references}
    if any(not set(section.evidence_reference_ids).issubset(allowed) for section in sections):
        raise HTTPException(status_code=422, detail="文案引用了本次任务以外的资料")
    scripts = production_repo.list_creation_task_scripts(workspace_id, task_id)
    revising_confirmed = bool(task.confirmed_script_id and task.can_revise_script)
    if revising_confirmed:
        scripts = [production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)]
    updating = bool(scripts and scripts[0].selection_status == "draft" and task.stage_status == "waiting_for_user")
    if updating or revising_confirmed:
        if payload.expected_updated_at is None:
            raise HTTPException(status_code=422, detail="请先读取当前草稿，再提交修订。")
        if payload.expected_updated_at != scripts[0].updated_at:
            raise HTTPException(status_code=409, detail="草稿已被修改，请重新读取后再提交。")
    elif payload.expected_updated_at is not None:
        raise HTTPException(status_code=409, detail="草稿版本已变化，请重新读取后再保存")
    binding = (asdict(plan), asdict(topic), tuple(sources), input_references)
    topic_origin = _script_topic_origin(workspace_id, plan, topic)
    script_input = CreationTaskScriptInput(
        goal=plan.goal, language=plan.language, presenter_share_target=plan.presenter_share_target,
        duration_target_seconds=plan.duration_target_seconds, topic_title=topic.title,
        topic_angle=topic.angle if topic_origin == "generated" else "", topic_audience=topic.audience, topic_outline=tuple(topic.outline),
        topic_origin=topic_origin,
        current_instruction=instruction_origin["raw_command"] if instruction_origin else "",
        sources=tuple(sources), style_instruction=str(plan.template_settings.get("style_instruction", "")),
        script_structure=tuple(plan.template_settings.get("script_structure", [])),
        script_section_count=(plan.template_settings.get("script_section_count")
                              or (5 if 0 < plan.presenter_share_target < 100 else None)),
    )
    candidate = CreationTaskGeneratedScript(title=payload.title, language=plan.language, sections=tuple(
        CreationTaskGeneratedScriptSection(title=section.title, content=section.content,
            visual_prompt=section.visual_prompt, evidence_reference_ids=tuple(section.evidence_reference_ids))
        for section in sections))
    try:
        _creation_task_script_generator_for(_workspace_settings_for(workspace_id)).check_draft(script_input, candidate)
    except CreationTaskScriptGenerationError as exc:
        prevalidation = exc.code in {"input_invalid", "language_unavailable", "route_configuration_invalid", "response_schema_invalid"}
        content_failure = exc.code in {"script_fact_unsupported", "script_goal_conflict"}
        if not prevalidation:
            reachable = content_failure or exc.code == "script_review_invalid"
            record_llm_service_verification(_workspace_settings_for(workspace_id), ok=reachable,
                cause="verified" if reachable else exc.code,
                detail="" if reachable else exc.reason,
                repair_action="" if reachable else exc.repair_action, workspace_id=workspace_id)
        raise _topic_failure_response(status_code=422 if content_failure or prevalidation else 502,
            error=exc.code, reason=exc.reason, impact="本轮修订未保存，原有草稿和任务保持原样。",
            repair_action=("请核对文案段落、资料引用和方案时长，修改后再提交。" if prevalidation else exc.repair_action),
            retryable=exc.retryable, reason_code=exc.reason_code, section_index=exc.section_index) from exc
    record_llm_service_verification(_workspace_settings_for(workspace_id), ok=True, cause="verified", workspace_id=workspace_id)
    # The model call can outlive source permissions or the current plan/topic.
    # Re-read those bindings; the repository still owns the final draft CAS.
    current_task, current_plan, current_topic, current_sources, current_refs = _assistant_script_inputs(workspace_id, task_id)
    current_reader = _require_current_script_inputs(request, workspace_id, current_plan, current_refs)
    if binding != (asdict(current_plan), asdict(current_topic), tuple(current_sources), current_refs) or reader != current_reader:
        raise HTTPException(status_code=409, detail="方案、选题或资料已变化，修订没有写入，请重新读取当前任务。")
    if instruction_origin is not None and instruction_origin != _assistant_creation_adoption_scope(
            task_id, request, str(payload.assistant_turn_id), draft_only=True):
        raise HTTPException(status_code=409, detail="本轮要求已变化，旧要求核查的文案没有保存，请读取当前输入。")
    if revising_confirmed:
        if instruction_origin is not None:
            # Revising an adopted body is itself adoption. A discussion-only
            # agent request must never be converted into a manual adoption.
            _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id))
        delegated = bool(instruction_origin and plan.creation_intent and plan.creation_intent["kind"] == "goal_delegation")
        try:
            task, draft = production_repo.confirm_creation_task_script(workspace_id, task_id, scripts[0].id,
                title=payload.title, sections=sections, expected_updated_at=payload.expected_updated_at,
                adoption_kind="delegated_system" if delegated else "manual", verified_owner_user_id=reader[0])
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if delegated:
            _goal_service().record_script_adoption(str(payload.assistant_turn_id), workspace_id, reader[0],
                task, plan, draft, instruction_origin)
    elif updating:
        try:
            draft = production_repo.update_assistant_script_draft(
                workspace_id, task_id, scripts[0].id, title=payload.title, sections=sections,
                expected_updated_at=payload.expected_updated_at,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        run = ScriptGenerationRun(
            id=f"script_run_{uuid4().hex[:12]}", workspace_id=workspace_id,
            task_id=task_id, plan_id=plan.id, topic_id=topic.id, status="running",
            route_snapshot=_topic_route_snapshot(workspace_id),
            prompt_schema_version="assistant-script-draft/v2",
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


def _assistant_text_without_credentials(value: str, workspace_id: str) -> str:
    safe = value
    for known in (_workspace_settings_for(workspace_id).effective_llm_api_key,
                  settings.tencentcloud_secret_id,
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


def _assistant_stream_safe_text(value: str, workspace_id: str) -> str:
    safe = _assistant_text_without_credentials(value, workspace_id)
    return re.sub(r"(?:file://)?(?:/Users/|/Volumes/|/private/|/tmp/|/var/folders/)[^\s,;\"']+", "[本机路径]", safe)


class _AssistantStreamTextSanitizer:
    """Hold only suffixes that could complete a known secret or sensitive marker."""

    _markers = (
        "authorization: bearer ", "api_key=", "api_key: ", "api-key=", "api-key: ",
        "api key=", "api key: ", "token=", "token: ", "password=", "password: ",
        "secret=", "secret: ", "sk-", "file://", "/users/", "/private/", "/tmp/", "/var/folders/",
    )
    _sensitive_patterns = (
        re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"),
        re.compile(r"(?i)\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"(?:file://)?(?:/Users/|/private/|/tmp/|/var/folders/)[^\s,;\"']+", re.IGNORECASE),
    )

    def __init__(self, workspace_id: str) -> None:
        self._workspace_id = workspace_id
        self._pending = ""
        key = _workspace_settings_for(workspace_id).effective_llm_api_key
        self._known_secrets = tuple(secret for secret in (
            key, settings.tencentcloud_secret_id, settings.tencentcloud_secret_key,
        ) if secret and len(secret) >= 8)

    def feed(self, delta: str, *, final: bool = False) -> str:
        combined = self._pending + delta
        if final:
            self._pending = ""
            return _assistant_stream_safe_text(combined, self._workspace_id)

        safe_end = len(combined)
        lowered = combined.lower()
        for marker in self._markers:
            max_length = min(len(marker), len(lowered))
            for length in range(max_length, 0, -1):
                if lowered.endswith(marker[:length]):
                    safe_end = min(safe_end, len(combined) - length)
                    break

        for secret in self._known_secrets:
            lowered_secret = secret.lower()
            max_length = min(len(lowered_secret), len(lowered))
            for length in range(max_length, 0, -1):
                if lowered.endswith(lowered_secret[:length]):
                    safe_end = min(safe_end, len(combined) - length)
                    break
            offset = combined.find(secret)
            while offset >= 0:
                end = offset + len(secret)
                if offset < safe_end < end:
                    safe_end = offset
                offset = combined.find(secret, offset + 1)

        for pattern in self._sensitive_patterns:
            for match in pattern.finditer(combined):
                if match.start() < safe_end and (match.end() == len(combined) or match.start() < safe_end < match.end()):
                    safe_end = match.start()

        safe_text = combined[:safe_end]
        self._pending = combined[safe_end:]
        return _assistant_stream_safe_text(safe_text, self._workspace_id) if safe_text else ""


def _assistant_stream_response(
    execute,
    *,
    workspace_id: str,
    turn_id: str | None = None,
) -> StreamingResponse:
    events: queue.Queue[dict[str, object] | None] = queue.Queue()
    disconnected = threading.Event()
    text_sanitizer = _AssistantStreamTextSanitizer(workspace_id)
    turn_ref = {"id": turn_id}

    def publish(raw_event: dict[str, object]) -> None:
        event_type = raw_event.get("type")
        if event_type == "text_delta" and isinstance(raw_event.get("delta"), str):
            delta = text_sanitizer.feed(str(raw_event["delta"]))
            if not delta:
                return
            event = {"type": "text_delta", "delta": delta}
        elif event_type == "tool_start":
            event = {
                "type": "tool_start", "tool_id": raw_event["tool_id"],
                "label": _assistant_stream_safe_text(str(raw_event.get("label", "助手操作")), workspace_id),
            }
        elif event_type == "tool_end":
            event = {
                "type": "tool_end", "tool_id": raw_event["tool_id"],
                "label": _assistant_stream_safe_text(str(raw_event.get("label", "助手操作")), workspace_id),
                "ok": bool(raw_event.get("ok")),
                "summary": _assistant_stream_safe_text(str(raw_event.get("summary", "")), workspace_id),
            }
            if isinstance(raw_event.get("failure"), dict):
                event["failure"] = {key: _assistant_stream_safe_text(value[:500], workspace_id)
                    for key, value in raw_event["failure"].items() if key in {"reason", "repair_action"} and isinstance(value, str)}
        elif event_type == "turn_started":
            event = {key: raw_event[key] for key in ("type", "turn_id", "turn_state", "draft_version") if key in raw_event}
            turn_ref["id"] = str(raw_event["turn_id"])
        elif event_type == "turn_status":
            event = {key: raw_event[key] for key in ("type", "turn_id", "turn_state") if key in raw_event}
        else:
            return
        validated = AssistantStreamEvent.model_validate(event)
        safe_event = validated.model_dump(mode="json", exclude_none=True)
        if turn_ref["id"] and event_type in {"text_delta", "tool_start", "tool_end"}:
            stream_conversation_repo.append_event(turn_ref["id"], safe_event)
        if not disconnected.is_set():
            events.put(safe_event)

    def work() -> None:
        terminal_event: dict[str, object] | None = None
        try:
            result = execute(publish)
            if turn_ref["id"]:
                publish({"type": "turn_status", "turn_id": turn_ref["id"], "turn_state": getattr(result, "turn_status", "completed")})
            if not disconnected.is_set():
                terminal_event = AssistantStreamEvent(
                    type="result", result=result.model_dump(mode="json"),
                ).model_dump(mode="json", exclude_none=True)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "对话未能完成，请查看当前任务状态。"
            if turn_ref["id"]:
                forget_turn_control(turn_ref["id"])
                stream_conversation_repo.finish_turn(turn_ref["id"], "failed", result={
                    "kind": "failure", "message": _assistant_stream_safe_text(detail, workspace_id),
                })
            if not disconnected.is_set():
                terminal_event = AssistantStreamEvent(
                    type="error", status=exc.status_code,
                    message=_assistant_stream_safe_text(detail, workspace_id),
                ).model_dump(mode="json", exclude_none=True)
        except Exception as exc:
            if turn_ref["id"]:
                forget_turn_control(turn_ref["id"])
                stream_conversation_repo.finish_turn(turn_ref["id"], "failed", result={
                    "kind": "failure", "message": "对话服务未能完整返回结果，请查看当前任务状态。",
                })
            logger.error("assistant stream worker failed (%s)", type(exc).__name__)
            if not disconnected.is_set():
                terminal_event = AssistantStreamEvent(
                    type="error", status=500,
                    message="对话服务未能完整返回结果，请查看当前任务状态。",
                ).model_dump(exclude_none=True)
        finally:
            tail = text_sanitizer.feed("", final=True)
            if tail:
                publish({"type": "text_delta", "delta": tail})
            if terminal_event is not None and not disconnected.is_set():
                events.put(terminal_event)
            events.put(None)
            with _runtime_reload_lock:
                _assistant_stream_workers.discard(thread)

    with _runtime_reload_lock:
        stream_conversation_repo = conversation_repo
        thread = threading.Thread(target=work, name="assistant-stream-response", daemon=True)
        _assistant_stream_workers.add(thread)

    def body():
        if turn_id:
            started = AssistantStreamEvent(type="turn_started", turn_id=turn_id, turn_state="running")
            yield f"data: {json.dumps(started.model_dump(mode='json', exclude_none=True), ensure_ascii=False, separators=(',', ':'))}\n\n"
        thread.start()
        result_seen = False
        error_seen = False
        try:
            while True:
                event = events.get()
                if event is None:
                    if not result_seen and not error_seen:
                        incomplete = AssistantStreamEvent(
                            type="error", message="连接中断，未收到完整的对话结果。",
                        ).model_dump(mode="json", exclude_none=True)
                        yield f"data: {json.dumps(incomplete, ensure_ascii=False)}\n\n"
                    break
                result_seen = result_seen or event.get("type") == "result"
                error_seen = error_seen or event.get("type") == "error"
                yield f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
        finally:
            disconnected.set()

    return StreamingResponse(body(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


def _assistant_turn_scope(
    workspace_id: str, task: CreationTask, payload: CreationTaskAssistantTurnRequest,
) -> tuple[str, list[str]]:
    """Authorize the requested view and selection from persisted task objects before Pi runs."""
    stage = payload.context_stage
    if task.stage == "topic":
        if stage != "topic":
            raise HTTPException(status_code=409, detail="当前任务只能在选题阶段使用对话")
        if payload.selected_object_refs or payload.playhead_ms is not None:
            raise HTTPException(status_code=422, detail="选题对话不能选择剪辑对象")
        return "topic", []
    if task.stage == "script" or task.confirmed_script_id is None or stage == "script" and task.can_revise_script:
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

    # The task-scoped conversation can read confirmed-task facts and invoke
    # the explicit execution actions before any narration artifact exists.
    # Object edits still pass through the strict selected-ref checks below;
    # preview_edit's existing endpoint remains the authority for edit scope.
    if not payload.selected_object_refs and payload.playhead_ms is None:
        return stage, []

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
            refs.update(f"overlay:{overlay['id']}" for overlay in montage.graphic_overlays)
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
    if "text/event-stream" in request.headers.get("accept", "").lower():
        workspace_id = current_workspace_id(request)
        return _assistant_stream_response(
            lambda on_event: _creation_task_assistant_turn(task_id, request, payload, on_event),
            workspace_id=workspace_id,
        )
    return _creation_task_assistant_turn(task_id, request, payload, None)


def _assistant_result_binding(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    try:
        return conversation_repo.task_result_source(turn_id, workspace_id, member_id, task_id,
            input_id=details.get("input_id"), input_turn_id=details.get("input_turn_id"))
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _resolve_assistant_edit_request(task_id, request, turn_id, details):
    try:
        request_id = UUID(str(details["request_id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="编辑请求定位缺少有效请求身份") from exc
    phase = details.get("phase", "preview")
    if phase not in {"preview", "apply", "script", "observation"}:
        raise HTTPException(status_code=502, detail="编辑请求定位phase不可核验")
    query_url = f"/api/creation-tasks/{quote(task_id, safe='')}/assistant/edit-requests/{request_id}?phase={phase}"
    if details.get("task_id", task_id) != task_id or details.get("query_url") != query_url:
        raise HTTPException(status_code=502, detail="编辑请求定位不属于当前任务")
    try:
        actual = get_exact_edit_request_result(task_id, request_id, request, phase)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        call_id = details.get("tool_call_id")
        if (details.get("assistant_turn_id") != turn_id or not isinstance(call_id, str)
                or not 1 <= len(call_id) <= 128 or request_id != uuid5(NAMESPACE_URL,
                    json.dumps([task_id, turn_id, call_id], ensure_ascii=False, separators=(",", ":")))):
            raise HTTPException(status_code=502, detail="原请求尚无服务端记录，工具定位来源不可核验") from exc
        status = details.get("status")
        status = "unknown" if status in {"unknown", "result_unknown"} else status
        if status not in {"unknown", "rejected", "not_submitted"}:
            raise HTTPException(status_code=502, detail="未提交请求不能声称已有结果")
        actual = {"status": status, "input_digest": None, "result_status_code": None, "result": None, "submitted_input": None}
    if actual["status"] == "recorded" and actual["result_status_code"] >= 400:
        actual["status"] = "rejected"
    locator = AssistantEditRequestLocator(task_id=task_id, request_id=request_id, phase=phase, query_url=query_url,
        status=actual["status"], input_digest=actual["input_digest"],
        submitted_input=actual["submitted_input"], result_status_code=actual["result_status_code"], result=actual["result"])
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    source = None
    previous = next((event for event in production_repo.list_task_events(workspace_id, task_id)
        if event.event_type == "assistant_edit_request_located"
        and event.payload["source"]["assistant_turn_id"] == turn_id
        and event.payload["locator"]["request_id"] == str(request_id)), None)
    if previous is not None:
        source = previous.payload["source"]
    actual_result = locator.result or {}
    if actual_result.get("job_id"):
        start = production_repo.get_task_event(workspace_id, f"event_{actual_result['job_id']}_start")
        original = start.payload["arguments"].get("_typed_source")
        if original is not None:
            source = original
    elif isinstance(actual_result.get("proposal"), dict):
        proposal = production_repo.get_edit_proposal(workspace_id, actual_result["proposal"]["id"])
        original = production_repo.get_edit_intent(workspace_id, proposal.intent_id).constraints.get("request_source")
        if original is not None:
            source = original
    if phase == "apply" and "operation_type" in (locator.submitted_input or {}):
        recorded_operation = (locator.result or {}).get("operation")
        if locator.result_status_code == 200 and isinstance(recorded_operation, dict):
            operation = production_repo.get_edit_operation(workspace_id, recorded_operation["id"])
            proposal = production_repo.get_edit_proposal(workspace_id, operation.proposal_id)
            intent = production_repo.get_edit_intent(workspace_id, proposal.intent_id)
            original = intent.constraints.get("request_source")
            if (operation.task_id != task_id or not original
                    or (original.get("workspace_id"), original.get("task_id"), original.get("member_id"), original.get("request_id"))
                        != (workspace_id, task_id, member_id, str(request_id))):
                raise HTTPException(status_code=502, detail="原编辑操作与请求来源不一致")
            source = original
    if source is None:
        binding = _assistant_result_binding(task_id, request, turn_id, details)
        snapshot = binding["context"]["target_snapshot"]
        actor = _request_permit_actor(request, workspace_id) if auth_context.enabled else CreationExecutionActor(workspace_id, member_id)
        source = {"workspace_id": workspace_id, "task_id": task_id, "member_id": member_id,
            "conversation_id": binding["conversation_id"], "assistant_turn_id": turn_id,
            **{key: binding[key] for key in ("input_id", "input_turn_id") if key in binding},
            "expected_timeline_version_id": snapshot["timeline_version_id"], "expected_timeline_sequence": snapshot["timeline_sequence"],
            "actor_identity": actor.identity_payload() if actor else None}
        if phase == "apply" and "operation_type" in (locator.submitted_input or {}):
            submitted = locator.submitted_input
            source.update(assistant_turn_id=submitted.get("assistant_turn_id"),
                          expected_timeline_version_id=submitted.get("expected_timeline_version_id"),
                          expected_timeline_sequence=submitted.get("expected_timeline_sequence"))
    if (source.get("workspace_id"), source.get("task_id"), source.get("member_id"), source.get("assistant_turn_id")) != (
            workspace_id, task_id, member_id, turn_id):
        raise HTTPException(status_code=502, detail="原编辑回执来源与当前查询身份不一致")
    production_repo.append_task_event(TaskEvent(id=f"event_edit_locator_{turn_id}_{request_id}", workspace_id=workspace_id,
        task_id=task_id, job_id=None, event_type="assistant_edit_request_located",
        payload={"source": source, "locator": locator.model_dump(mode="json"),
                 "request_event_id": "event_edit_request_" + media_digest([workspace_id, member_id, str(request_id), phase])},
        created_at=utc_now()))
    return locator


def _script_tool_metadata(value):
    return {key: item for key, item in value.items() if key != "definition"}


def _check_script_tool_save_result(workspace_id, task_id, tool, submitted_input):
    producer = production_repo.get_job_run(workspace_id, tool["source_job_id"])
    if producer.task_id != task_id or tool["version"] != 1:
        raise HTTPException(status_code=502, detail="工具登记结果不属于原任务的v1")
    if submitted_input is not None and any(tool[key] != submitted_input[field] for key, field in (
            ("source_job_id", "producer_job_id"), ("name", "name"),
            ("description", "description"), ("parameter_schema", "parameter_schema"))):
        raise HTTPException(status_code=502, detail="工具登记结果与原提交输入不一致")


def _resolve_assistant_script_tool(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    actor = _require_permit_actor(request, workspace_id)
    if (actor.user_id or actor.member_id) != member_id:
        raise HTTPException(status_code=502, detail="工具回执不属于当前对话成员")
    action = details.get("action")
    recorded = details["kind"] == "media_script_tool_result"
    status = "recorded" if recorded else details.get("status")
    if action not in {"save", "query", "update", "revoke"} or status not in {"recorded", "unknown", "rejected", "not_submitted"}:
        raise HTTPException(status_code=502, detail="工具回执动作或状态不可核验")
    submitted, expected, request_id, tool = None, None, None, None
    if action in {"save", "query"}:
        try:
            request_id = UUID(str(details["request_id"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="工具登记缺少有效原请求身份") from exc
        query_url = f"/api/media-script-tools/requests/{request_id}"
        if action == "save" and details.get("query_url") != query_url:
            raise HTTPException(status_code=502, detail="工具登记查询定位不可核验")
        if not recorded:
            call_id = details.get("tool_call_id")
            if (action != "save" or details.get("task_id") != task_id or details.get("assistant_turn_id") != turn_id
                    or not isinstance(call_id, str) or not 1 <= len(call_id) <= 128
                    or request_id != uuid5(NAMESPACE_URL, json.dumps([task_id, turn_id, call_id], ensure_ascii=False, separators=(",", ":")))):
                raise HTTPException(status_code=502, detail="未知工具登记的原轮次和调用来源不可核验")
            raw = details.get("submitted_input")
            if not isinstance(raw, dict) or set(raw) - {"producer_job_id", "name", "description", "parameter_schema"}:
                raise HTTPException(status_code=502, detail="工具登记原输入不可核验")
            try:
                submitted = MediaScriptToolSaveRequest.model_validate({**raw, "request_id": request_id}).model_dump(
                    mode="json", exclude={"request_id", "assistant_turn_id"})
            except ValidationError as exc:
                raise HTTPException(status_code=502, detail="工具登记原输入不符合实际API契约") from exc
            if len(json.dumps(submitted, ensure_ascii=False).encode()) > 65536:
                raise HTTPException(status_code=502, detail="工具登记原输入超过有界元数据限制")
        try:
            tool = get_media_script_tool_creation(request_id, request)
        except HTTPException as exc:
            if recorded or exc.status_code not in {404, 409}:
                raise
        if tool is not None:
            if action == "save":
                _check_script_tool_save_result(workspace_id, task_id, tool, submitted)
            status = "recorded"
        elif status == "unknown":
            binding = _assistant_result_binding(task_id, request, turn_id, details)
            snapshot = binding["context"]["target_snapshot"]
            source = {"workspace_id": workspace_id, "task_id": task_id, "member_id": member_id,
                "conversation_id": binding["conversation_id"], "assistant_turn_id": turn_id,
                **{key: binding[key] for key in ("input_id", "input_turn_id") if key in binding},
                "expected_timeline_version_id": snapshot["timeline_version_id"], "expected_timeline_sequence": snapshot["timeline_sequence"],
                "actor_identity": actor.identity_payload()}
            # The Leaf owns UUID/principal scoping; reuse its existing registration
            # identity only to select completed receipts for the GET-only driver.
            registration_id = _media_script_tools()._creation_identity(actor, request_id)[1]
            production_repo.append_task_event(TaskEvent(id=f"event_script_tool_locator_{turn_id}_{request_id}",
                workspace_id=workspace_id, task_id=task_id, job_id=None, event_type="assistant_script_tool_located",
                payload={"source": source, "request_id": str(request_id), "query_url": query_url,
                         "submitted_input": submitted, "registration_id": registration_id}, created_at=utc_now()))
    else:
        reported = details.get("tool") if recorded else details
        tool_id = reported.get("tool_id") if isinstance(reported, dict) else None
        if not isinstance(tool_id, str) or not 1 <= len(tool_id) <= 120:
            raise HTTPException(status_code=502, detail="工具回执身份不可核验")
        query_url = f"/api/media-script-tools/{quote(tool_id, safe='')}"
        if not recorded and details.get("query_url") != query_url:
            raise HTTPException(status_code=502, detail="未知工具写入查询定位不可核验")
        try:
            expected = MediaScriptToolRevokeRequest.model_validate({"expected_version": details.get("expected_version")}).expected_version
            schema = MediaScriptToolUpdateRequest if action == "update" else MediaScriptToolRevokeRequest
            submitted = schema.model_validate(details.get("submitted_input")).model_dump(mode="json")
            if submitted["expected_version"] != expected:
                raise ValueError("expected version mismatch")
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="工具写入原版本和输入不可核验") from exc
        if recorded:
            version = reported.get("version")
            if type(version) is not int or version < 1:
                raise HTTPException(status_code=502, detail="工具回执版本不可核验")
            tool = get_media_script_tool(tool_id, request, version)
            if ((action == "revoke" and tool["active"] is not False)
                    or (expected is not None and version != expected + (1 if action == "update" else 0))):
                raise HTTPException(status_code=502, detail="工具版本未证实本次回执")
            if action == "update" and submitted is not None and any(tool[key] != submitted[field] for key, field in (
                    ("source_job_id", "producer_job_id"), ("name", "name"),
                    ("description", "description"), ("parameter_schema", "parameter_schema"))):
                raise HTTPException(status_code=502, detail="工具更新版本与原输入不一致")
        else:
            tool = get_media_script_tool(tool_id, request)
    receipt = AssistantScriptToolReceipt(action=action, status=status, request_id=request_id,
        tool_id=tool["tool_id"] if tool else None, tool_version=tool["version"] if tool else None,
        expected_version=expected, query_url=query_url, submitted_input=submitted,
        tool=_script_tool_metadata(tool) if tool else None)
    if status == "recorded":
        messages = {"save": "工具元数据已登记，已回读原v1。", "query": "已回读原登记请求的实际v1。",
            "update": "已回读工具的实际更新版本。", "revoke": "已核验工具停用状态。"}
        message = messages[action] + "回读工具元数据不会生成图片或改变当前草稿、成片。"
    elif action in {"update", "revoke"}:
        message = f"本次工具写入{('结果未知' if status == 'unknown' else '已拒绝')}；已读取当前HEAD v{tool['version']}，不能将HEAD变化归因于本次请求，未重复写入。"
    else:
        state_text = {"unknown": "结果未知", "rejected": "请求已拒绝", "not_submitted": "请求未提交"}[status]
        message = f"工具登记{state_text}；原UUID、输入和查询定位已保留，只回读原请求，不重复保存。"
    return receipt, message


def _assistant_related_media_job(task_id, request, turn_id, conversation_id, reported):
    related = TaskMediaJobResponse.model_validate(reported)
    workspace_id = current_workspace_id(request)
    original, submitted = _creation_runtime().registrations["creation_job"].owner.task_media.read(workspace_id, related.job_id)
    source = submitted["arguments"].get("_typed_source", {})
    if original.task_id != task_id or source.get("assistant_turn_id") != turn_id or source.get("conversation_id") != conversation_id:
        raise HTTPException(status_code=502, detail="先前媒体回执不属于本轮真实目标")
    return TaskMediaJobResponse.model_validate(get_task_media_job(task_id, original.id, request))


def _creation_task_assistant_turn(
    task_id: str,
    request: Request,
    payload: CreationTaskAssistantTurnRequest,
    on_event,
) -> CreationTaskAssistantTurnResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    # 先按可信身份确认这条任务读得到，再让助手碰它：读不到的任务不应被助手读到。
    caller_user_id, local_diagnostic = _content_reader(request, workspace_id)
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="creation task not found")
    member_id = _assistant_member_id(request, workspace_id)
    _remember_goal_transport(request, workspace_id, member_id)
    previous_context = conversation_repo.request_context(str(payload.client_request_id), str(payload.conversation_id), workspace_id, member_id)
    current_version = None
    if task.current_timeline_version_id is not None:
        try:
            current_version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="当前任务的时间线版本不可用") from exc
        if current_version.task_id != task.id:
            raise HTTPException(status_code=409, detail="当前时间线不属于此任务")
    task_version = {"timeline_version_id": task.current_timeline_version_id,
                    "timeline_sequence": current_version.sequence if current_version is not None else 0}
    if previous_context is None and payload.expected_timeline_sequence is not None and (payload.expected_timeline_sequence != task_version["timeline_sequence"]
            or payload.expected_timeline_version_id != task.current_timeline_version_id):
        raise HTTPException(status_code=409, detail={"error": "creation_task_timeline_version_conflict",
            "message": "当前任务版本已变化，请重新查看选中对象后发送。"})
    trusted_stage, trusted_refs = _assistant_turn_scope(workspace_id, task, payload)
    material_selection = (previous_context.get("target_snapshot", {}).get("material_selection")
                          if previous_context is not None else _assistant_task_clip_material_selection(request, task, trusted_refs))
    profile_reference = (_assistant_profile_snapshot(request, payload.selected_profile_reference.kind,
                         payload.selected_profile_reference.profile_id, payload.selected_profile_reference.version)
                         if payload.selected_profile_reference is not None else None)
    message = payload.message.strip()
    if _assistant_text_without_credentials(message, workspace_id) != message:
        raise HTTPException(status_code=422, detail="请勿在创作要求中粘贴密钥或口令。")
    knowledge_reference = (_assistant_knowledge_snapshot(request, payload.selected_knowledge_reference.knowledge_item_id,
                           payload.selected_knowledge_reference.version, payload.selected_knowledge_reference.locator)
                           if payload.selected_knowledge_reference is not None else None)
    turn_id, created, _turn = _begin_assistant_turn(
        request, str(payload.conversation_id), "task", task_id, message,
        payload.client_request_id, payload.expected_draft_version,
        {"kind": "task", "task_id": task_id, "stage": trusted_stage,
         "selected_object_refs": sorted(trusted_refs), "playhead_ms": payload.playhead_ms,
         "expected_timeline_version_id": payload.expected_timeline_version_id, "expected_timeline_sequence": payload.expected_timeline_sequence,
         "profile_reference": payload.selected_profile_reference.model_dump() if payload.selected_profile_reference else None, "knowledge_reference": payload.selected_knowledge_reference.model_dump() if payload.selected_knowledge_reference else None,
         "target_snapshot": {**task_version, "profile_reference": profile_reference, "knowledge_reference": knowledge_reference,
                             **({"material_selection": material_selection} if len(trusted_refs) == 1 and trusted_refs[0].startswith("clip:") else {})}}, on_event,
        expected_reference_version=payload.expected_reference_version,
    )
    if not created:
        replay_settings = _workspace_settings_for(workspace_id)
        return _replay_assistant_turn(
            turn_id, CreationTaskAssistantTurnResponse,
            provider=replay_settings.effective_llm_provider,
            model=replay_settings.effective_llm_model,
            draft_version=payload.expected_draft_version + 1,
        )
    frozen_context = conversation_repo.consumed_turn_request_context(
        turn_id, str(payload.conversation_id), workspace_id, member_id)
    trusted_refs = frozen_context.get("selected_object_refs", trusted_refs)
    material_selection = (frozen_context.get("target_snapshot") or {}).get("material_selection")
    try:
        reply, preview = run_task_turn(
            _workspace_settings_for(workspace_id),
            workspace_id=workspace_id,
            local_member_token=_local_member_token(request) or None,
            caller_user_id=caller_user_id if not local_diagnostic else None,
            task_id=task_id,
            conversation_id=str(payload.conversation_id),
            message=message,
            stage=trusted_stage,
            selected_refs=trusted_refs,
            playhead_ms=payload.playhead_ms,
            authorization=request.headers.get("authorization"),
            cookie=request.headers.get("cookie"),
            on_event=on_event,
            turn_id=turn_id,
            selected_references=_frozen_assistant_references(request, str(payload.conversation_id), turn_id),
            input_handler=_assistant_input_delivery(request, str(payload.conversation_id), turn_id),
            profile_reference=profile_reference,
            knowledge_reference=knowledge_reference,
            task_version=task_version,
            material_selection=material_selection,
        )
    except AssistantStopped:
        saved = conversation_repo.get_turn(turn_id)
        response = CreationTaskAssistantTurnResponse(
            reply=saved["assistant_text"] if saved else "",
            provider=_workspace_settings_for(workspace_id).effective_llm_provider,
            model=_workspace_settings_for(workspace_id).effective_llm_model,
            turn_id=turn_id,
            turn_status="stopped",
            draft_version=payload.expected_draft_version + 1,
        )
        conversation_repo.finish_turn(turn_id, "stopped", result={"kind": "task_turn", "response": response.model_dump(mode="json")})
        return response
    except AssistantUnavailable as exc:
        forget_turn_control(turn_id)
        conversation_repo.finish_turn(turn_id, "failed", result={
            "kind": "failure", "message": _assistant_stream_safe_text(str(exc), workspace_id),
        })
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _finish_task_assistant_turn(task_id, request, payload, turn_id, reply, preview, trusted_stage)


def _assistant_applied_edit_receipt(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    edit_request = _resolve_assistant_edit_request(task_id, request, turn_id, details)
    if edit_request.phase != "apply" or edit_request.status != "recorded" or edit_request.result_status_code != 200:
        raise HTTPException(status_code=502, detail="直接编辑尚无可核验的完成回执")
    confirmed = CreationTaskEditConfirmResponse.model_validate(edit_request.result)
    actual = production_repo.get_edit_operation(workspace_id, confirmed.operation.id)
    source_input = edit_request.submitted_input or {}
    if (actual.task_id != task_id or actual.status != "applied" or not actual.result_version_id
            or source_input.get("assistant_turn_id") != turn_id
            or source_input.get("operation_type") != actual.operation_type
            or source_input.get("expected_timeline_version_id") != actual.base_version_id
            or confirmed.operation.result_version_id != actual.result_version_id):
        raise HTTPException(status_code=502, detail="直接编辑回执与当前输入及实际操作不一致")
    return edit_request


def _resolve_assistant_storyboard_regeneration(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    origin = _assistant_result_binding(task_id, request, turn_id, details)
    member_id = _assistant_member_id(request, workspace_id)
    try:
        current = conversation_repo.task_turn_source(turn_id, workspace_id, member_id, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="故事板回执不属于当前有效输入") from exc
    if (current.get("input_id"), current.get("input_turn_id")) != (origin.get("input_id"), origin.get("input_turn_id")):
        raise HTTPException(status_code=502, detail="故事板回执属于先前输入，未归入当前要求")
    kind, action = details.get("kind"), details.get("action")
    reported = details.get("receipt") if kind == "storyboard_regeneration_receipt" else None
    try:
        request_id = UUID(str(reported["request_id"] if reported is not None else details["request_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="故事板回执缺少有效的原请求身份") from exc
    if action not in {"submit", "query"} or action == "query" and reported is None:
        raise HTTPException(status_code=502, detail="故事板回执动作不可核验")
    query_url = f"/api/creation-tasks/{quote(task_id, safe='')}/montage/plan/requests/{request_id}"
    if reported is not None and reported.get("task_id") != task_id or reported is None and details.get("query_url") != query_url:
        raise HTTPException(status_code=502, detail="故事板原请求定位不属于当前任务")
    if action == "submit":
        call_id = details.get("tool_call_id")
        if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 128 or request_id != uuid5(NAMESPACE_URL,
                json.dumps([task_id, turn_id, call_id], ensure_ascii=False, separators=(",", ":")))):
            raise HTTPException(status_code=502, detail="故事板原请求与本轮实际工具调用不一致")
    _exact_edit_access(task_id, request)
    actor = _require_permit_actor(request, workspace_id)
    try:
        actual = _creation_execution_control().read_storyboard_regeneration(workspace_id, task_id, str(request_id), actor)
    except ExecutionControlRejected as exc:
        if exc.status_code != 404 or reported is not None:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        try:
            return AssistantStoryboardRegenerationFailure.model_validate({
                "kind": kind, "action": action, "request_id": request_id, "query_url": query_url,
                "failure": {key: _assistant_stream_safe_text(details["failure"][key], workspace_id)[:500]
                    for key in ("reason", "repair_action")}})
        except (KeyError, ValidationError, TypeError) as invalid:
            raise HTTPException(status_code=502, detail="故事板未知结果定位不可核验") from invalid
    if reported is not None and reported.get("job_id") != actual["job_id"]:
        raise HTTPException(status_code=502, detail="故事板回执与原请求的实际作业不一致")
    if action == "submit":
        original = production_repo.get_task_event(workspace_id, f"event_{actual['job_id']}_start").payload["request_inputs"]
        if original.get("assistant_turn_id") != str(turn_id):
            raise HTTPException(status_code=502, detail="故事板原请求不属于本轮提交")
        snapshot = origin["context"]["target_snapshot"]
        base = (actual["base_timeline_version_id"], actual["base_timeline_sequence"])
        if base != (snapshot["timeline_version_id"], snapshot["timeline_sequence"]):
            task = production_repo.get_creation_task(workspace_id, task_id)
            version = production_repo.get_timeline_version(workspace_id, base[0])
            successor = _assistant_edited_execution_version(task, member_id, turn_id, origin, snapshot, version, request)
            if successor is None or base != (successor["timeline_version_id"], successor["timeline_sequence"]):
                raise HTTPException(status_code=502, detail="故事板原请求与本轮目标版本不一致")
    return AssistantStoryboardRegenerationReceipt(kind="storyboard_regeneration_receipt", action=action, receipt=actual)


def _finish_task_assistant_turn(task_id, request, payload, turn_id, reply, preview, trusted_stage):
    workspace_id = current_workspace_id(request)
    reply = _assistant_text_without_credentials(reply, workspace_id)
    script_draft = None
    execution_action = None
    execution_status = None
    topic_candidates = None
    storyboard_regeneration = None
    timeline_action = None
    media_job, edit_request, script_tool = None, None, None
    if isinstance(preview, dict) and preview.get("kind") in {"execution_action", "execution_result"} and preview.get("applied_edit") is not None:
        edit_request = _assistant_applied_edit_receipt(task_id, request, turn_id, preview["applied_edit"])
        timeline_action = "apply"
    if isinstance(preview, dict) and preview.get("kind") in {"storyboard_regeneration_receipt", "storyboard_regeneration_unknown", "storyboard_regeneration_rejected"}:
        from app.assistant.result_projection import storyboard_regeneration_message
        storyboard_regeneration = _resolve_assistant_storyboard_regeneration(task_id, request, turn_id, preview)
        reply = storyboard_regeneration_message(storyboard_regeneration.model_dump(mode="json"))
        edit_preview = None
    elif isinstance(preview, dict) and preview.get("kind") in {"media_script_tool_result", "media_script_tool_status"}:
        script_tool, reply = _resolve_assistant_script_tool(task_id, request, turn_id, preview)
        if preview.get("related_media_job") is not None:
            media_job = _assistant_related_media_job(task_id, request, turn_id, str(payload.conversation_id), preview["related_media_job"])
        edit_preview = None
    elif isinstance(preview, dict) and preview.get("kind") == "edit_applied":
        edit_request = _assistant_applied_edit_receipt(task_id, request, turn_id, preview)
        timeline_action = "apply"
        edit_preview = None
        reply = "修改已保存，可撤销；重新生成后可播放更新的成片。"
    elif isinstance(preview, dict) and preview.get("kind") == "edit_request_status":
        edit_request = _resolve_assistant_edit_request(task_id, request, turn_id, preview)
        if preview.get("related_media_job") is not None:
            media_job = _assistant_related_media_job(task_id, request, turn_id, str(payload.conversation_id), preview["related_media_job"])
        edit_preview = None
        reply = "原编辑请求定位已保存；尚未确认修改完成。" if edit_request.status == "unknown" else "原编辑请求已回读，请查看实际状态；没有重复提交。"
    elif isinstance(preview, dict) and preview.get("kind") == "media_job":
        candidate = TaskMediaJobResponse.model_validate(preview.get("media_job"))
        # Only the stable job identity is consumed from the tool receipt.
        # Re-read state, ownership and result URLs from the production owner.
        if (candidate.result or {}).get("reused_completed_source") is True:
            goal = conversation_repo.get_task_goal(turn_id)
            slot = next((item for item in (goal or {}).get("completed_source_slots", []) if item["job_id"] == candidate.job_id), None)
            if slot is None:
                raise HTTPException(status_code=409, detail="复用来源不属于当前原目标的已完成输出槽")
            task, _member_id = _exact_edit_access(task_id, request, write=True)
            media_job = TaskMediaJobResponse.model_validate(_completed_source_receipt(task, request, turn_id, slot,
                contract=slot["requested_output"], input_refs=slot["input_refs"]))
        else:
            media_job = TaskMediaJobResponse.model_validate(get_task_media_job(task_id, candidate.job_id, request))
        edit_preview = None
    elif isinstance(preview, dict) and "script_id" in preview and "proposal" not in preview:
        script_id = preview.get("script_id") if isinstance(preview, dict) else None
        if not isinstance(script_id, str):
            raise HTTPException(status_code=502, detail="对话没有返回可核实的文案草稿。")
        try:
            candidate = production_repo.get_creation_task_script(workspace_id, script_id)
        except KeyError as exc:
            raise HTTPException(status_code=502, detail="对话返回的文案草稿不存在。") from exc
        adopted = (candidate.selection_status == "confirmed" and candidate.adoption
            and production_repo.get_creation_task(workspace_id, task_id).confirmed_script_id == candidate.id)
        if candidate.task_id != task_id or candidate.selection_status != "draft" and not adopted:
            raise HTTPException(status_code=502, detail="对话返回的文案草稿不属于当前任务。")
        script_draft = CreationTaskScriptResponse.model_validate(asdict(candidate))
        edit_preview = None
    elif isinstance(preview, dict) and preview.get("kind") == "topic_generation":
        try:
            topics = CreationTaskTopicsResponse.model_validate(preview.get("topics"))
        except ValidationError as exc:
            raise HTTPException(status_code=502, detail="对话没有返回可核实的选题候选。") from exc
        if topics.task.id != task_id:
            raise HTTPException(status_code=502, detail="对话生成的选题不属于当前任务。")
        topic_candidates = topics
        edit_preview = None
    elif isinstance(preview, dict) and preview.get("kind") == "execution_result":
        status = preview.get("status")
        if not isinstance(status, dict) or status.get("task_id") != task_id or not isinstance(status.get("job"), dict):
            raise HTTPException(status_code=502, detail="没有返回可核验的原制作作业")
        try:
            execution_status = _read_bound_execution_result(task_id, request, status["job"]["id"])
            if preview.get("input_id") and execution_status["job"]["status"] in {"queued", "running"}:
                execution_status = _bind_assistant_execution_observation(task_id, request, turn_id, preview, execution_status)
        except HTTPException as exc:
            if edit_request is None:
                raise
            execution_status = None
            reply = "修改已保存，可撤销；原制作作业回读未通过核验。" + _assistant_stream_safe_text(str(exc.detail), workspace_id)
        edit_preview = None
    elif isinstance(preview, dict) and preview.get("kind") == "execution_action":
        action = preview.get("action")
        status = preview.get("status")
        if (action not in {"start", "resume", "cancel"} or not isinstance(status, dict)
                or status.get("task_id") != task_id):
            raise HTTPException(status_code=502, detail="对话没有返回可核实的制作作业状态。")
        execution_action = action
        try:
            execution_status = (_read_bound_execution_result(task_id, request, status["job"]["id"])
                                if action == "cancel" else _bind_assistant_execution_result(task_id, request, turn_id, status))
        except HTTPException as exc:
            if edit_request is None:
                raise
            execution_action, execution_status = None, None
            reply = "修改已保存，可撤销；后续制作回执未通过核验。" + _assistant_stream_safe_text(str(exc.detail), workspace_id)
        edit_preview = None
    elif isinstance(preview, dict) and preview.get("kind") == "edit_control":
        action = preview.get("action")
        if action not in {"undo", "redo", "reject"} or preview.get("task_id") != task_id:
            raise HTTPException(status_code=502, detail="没有返回可核验的当前编辑控制结果。")
        timeline_action = action
        edit_preview = None
    elif preview is not None:
        edit_preview = CreationTaskEditPreviewResponse.model_validate(preview)
        if edit_preview.proposal.task_id != task_id or edit_preview.proposal.workspace_id != workspace_id:
            raise HTTPException(status_code=502, detail="对话工具返回了不属于当前任务的编辑提案。")
        try:
            actual_proposal = production_repo.get_edit_proposal(workspace_id, edit_preview.proposal.id)
            actual_intent = production_repo.get_edit_intent(workspace_id, actual_proposal.intent_id)
        except KeyError as exc:
            raise HTTPException(status_code=502, detail="工具提案没有真实持久结果") from exc
        if actual_proposal.task_id != task_id:
            raise HTTPException(status_code=502, detail="工具提案不属于当前任务")
        edit_preview = _edit_preview_response(actual_intent, actual_proposal)
    else:
        edit_preview = None
    response = CreationTaskAssistantTurnResponse(
        storyboard_regeneration=storyboard_regeneration,
        script_tool=script_tool,
        edit_request=edit_request,
        reply=reply,
        provider=_workspace_settings_for(workspace_id).effective_llm_provider,
        model=_workspace_settings_for(workspace_id).effective_llm_model,
        edit_preview=edit_preview,
        script_draft=script_draft,
        execution_action=execution_action,
        execution_status=execution_status,
        topic_candidates=topic_candidates,
        timeline_action=timeline_action,
        media_job=media_job,
        turn_id=turn_id,
        draft_version=payload.expected_draft_version + 1,
    )
    forget_turn_control(turn_id)
    conversation_repo.finish_turn(turn_id, "completed", reply, {
        "kind": "task_turn",
        "goal_business_actions": _assistant_goal_action_proofs(workspace_id, task_id, turn_id,
            media_job=media_job, edit_preview=edit_preview, edit_request=edit_request, execution_status=execution_status),
        "edit_preview": bool(edit_preview),
        "script_draft": script_draft.id if script_draft else None,
        "execution_action": execution_action,
        "execution_status": execution_status.get("job", {}).get("status")
            if isinstance(execution_status, dict) and isinstance(execution_status.get("job"), dict) else None,
        "topic_candidates": bool(topic_candidates),
        "timeline_action": timeline_action,
        "storyboard_regeneration": storyboard_regeneration.model_dump(mode="json") if storyboard_regeneration else None,
        "response": response.model_dump(mode="json"),
    })
    _goal_service().register_turn(turn_id)
    _wake_assistant_continuations()
    return response


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
        _readable_creation_task(request, task_id),
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
    _require_automation_task_open(request,workspace_id,task_id)
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
    route_snapshot = _topic_route_snapshot(workspace_id)
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
        prompt_schema_version="creation-task-script/v2",
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
            _creation_task_script_generator_for(_workspace_settings_for(workspace_id)).generate(
                CreationTaskScriptInput(
                    goal=plan.goal,
                    language=plan.language,
                    presenter_share_target=plan.presenter_share_target,
                    duration_target_seconds=plan.duration_target_seconds,
                    topic_title=selected_topic.title,
                    topic_origin=_script_topic_origin(workspace_id, plan, selected_topic),
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
        content_failure = exc.code in {"script_fact_unsupported", "script_goal_conflict"}
        if content_failure or exc.code == "script_review_invalid":
            # Both provider HTTP calls succeeded. Content/review rejection is
            # not evidence that the user's text service connection is broken.
            record_llm_service_verification(
                _workspace_settings_for(workspace_id), ok=True, cause="verified",
                workspace_id=workspace_id,
            )
        elif exc.code not in {"input_invalid", "route_configuration_invalid", "language_unavailable"}:
            record_llm_service_verification(
                _workspace_settings_for(workspace_id), ok=False, cause=exc.code,
                detail=exc.reason, repair_action=exc.repair_action,
                workspace_id=workspace_id,
            )
        production_repo.fail_script_generation(
            run.id,
            reason=exc.reason,
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=exc.retryable,
            provider_attempted=True,
            provider_failed=not (content_failure or exc.code == "script_review_invalid"),
        )
        raise _topic_failure_response(
            status_code=422 if content_failure else 502,
            error=exc.code,
            reason=exc.reason,
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=exc.retryable,
            reason_code=exc.reason_code,
            section_index=exc.section_index,
        ) from exc
    record_llm_service_verification(
        _workspace_settings_for(workspace_id), ok=True, cause="verified",
        workspace_id=workspace_id,
    )
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
    instruction_origin = None
    if payload.adoption_kind == "delegated_system" and payload.assistant_turn_id is not None:
        instruction_origin = _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id))
    task = _creation_task_or_404(workspace_id, task_id)
    plan = _creation_plan_for_task_or_404(workspace_id, task)
    try:
        current_script = production_repo.get_creation_task_script(workspace_id, script_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task script not found") from exc
    if current_script.task_id != task.id:
        raise HTTPException(status_code=404, detail="creation task script not found")
    if current_script.reused_from_entry_id:
        _readable_script_library_entry(request, workspace_id, current_script.reused_from_entry_id)
    elif current_script.run_id:
        try:
            generation_run = production_repo.get_script_generation_run(workspace_id, current_script.run_id)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="文案来源记录已不可用，请重新读取任务。") from exc
        _require_current_script_inputs(request, workspace_id, plan, generation_run.input_references)
    estimated_seconds = estimate_speech_seconds("\n".join(section.content for section in payload.sections))
    if estimated_seconds > plan.duration_target_seconds:
        raise HTTPException(status_code=422, detail=(
            f"文案预计约 {estimated_seconds:.1f} 秒，超过方案 {plan.duration_target_seconds} 秒上限；"
            "请压缩正文后再确认。"
        ))
    try:
        task, adopted_script = production_repo.confirm_creation_task_script(
            workspace_id,
            task_id,
            script_id,
            title=payload.title,
            sections=[
                CreationTaskScriptSection(**section.model_dump())
                for section in payload.sections
            ],
            expected_updated_at=payload.expected_updated_at,
            adoption_kind=payload.adoption_kind,
            verified_owner_user_id=_content_reader(request, workspace_id)[0],
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
    if instruction_origin is not None:
        _goal_service().record_script_adoption(str(payload.assistant_turn_id), workspace_id,
            _content_reader(request, workspace_id)[0], task, plan, adopted_script, instruction_origin)
    return _creation_task_scripts_response(workspace_id, task)


@app.post(
    "/api/creation-tasks/{task_id}/narration/voice",
    response_model=CreationTaskNarrationVoiceSelectionResponse,
)
def select_creation_task_narration_voice(
    task_id: str,
    request: Request,
    payload: CreationTaskNarrationVoiceSelectionRequest,
) -> CreationTaskNarrationVoiceSelectionResponse:
    task, member_id = _exact_edit_access(task_id, request, write=True)
    if payload.assistant_turn_id is not None:
        _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id))
    try:
        receipt = production_repo.select_creation_task_narration_voice(
            workspace_id=task.workspace_id, task_id=task.id, member_id=member_id, request_id=str(payload.request_id),
            voice_profile_id=payload.voice_profile_id,
            voice_version=payload.voice_version,
            expected_plan_updated_at=payload.expected_plan_updated_at,
            expected_task_updated_at=payload.expected_task_updated_at,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到当前任务或配音声音。") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
    return CreationTaskNarrationVoiceSelectionResponse.model_validate(receipt)


@app.get("/api/creation-tasks/{task_id}/narration/voice/requests/{request_id}",
    response_model=CreationTaskNarrationVoiceSelectionResponse)
def get_creation_task_narration_voice_selection(task_id: str, request_id: UUID, request: Request):
    task, member_id = _exact_edit_access(task_id, request)
    try:
        receipt = production_repo.get_creation_task_narration_voice_selection(
            workspace_id=task.workspace_id, task_id=task.id, request_id=str(request_id), member_id=member_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到当前成员的原声音采用请求。") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return CreationTaskNarrationVoiceSelectionResponse.model_validate(receipt)


@app.post(
    "/api/creation-tasks/{task_id}/narration/generate",
    response_model=CreationTaskNarrationResponse,
)
def generate_creation_task_narration(
    task_id: str,
    request: Request,
) -> CreationTaskNarrationResponse:
    raise HTTPException(status_code=409, detail="请确认文案和制作要求后使用开始/继续制作，配音必须进入设备队列")


def _generate_creation_task_narration_for_workspace(workspace_id: str,task_id: str,*,grant_context=None):
    try:
        task,record=_narration_stage_use_case().generate(workspace_id,task_id,grant_context=grant_context)
    except NarrationStageRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    return _creation_task_narration_response(task,record)



@app.get("/api/creation-tasks/{task_id}/narration/audio")
def get_creation_task_narration_audio(task_id: str, request: Request) -> FileResponse:
    workspace_id = current_workspace_id(request)
    task = _readable_creation_task(request, task_id)
    artifact = (
        production_repo.get_narration_artifact(workspace_id, task.narration_cache_key)
        if task.narration_cache_key
        else None
    )
    if artifact is None or artifact.task_id != task.id:
        raise HTTPException(status_code=404, detail="creation task narration not found")
    media_type = {
        ".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg",
        ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    }.get(Path(artifact.audio_path).suffix.lower(), "application/octet-stream")
    return FileResponse(artifact.audio_path, media_type=media_type, filename=f"narration{Path(artifact.audio_path).suffix}")


@app.get('/api/creation-tasks/{task_id}/narration/waveform')
def get_creation_task_narration_waveform(task_id: str, request: Request) -> dict:
    return _submit_audio_ingress(task_id, request, 'narration_waveform', 'waveform')


@app.get("/api/creation-tasks/{task_id}/narration/segments/{segment_id}/audio")
def get_creation_task_narration_segment_audio(
    task_id: str, segment_id: str, request: Request
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    _readable_creation_task(request, task_id)
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
    media_type = {
        ".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg",
        ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=f"{segment.id}{path.suffix}")


def _person_source_range(workspace_id,task,montage,clip,segment):
    return _person_preview_use_case().source_range(workspace_id,task,montage,clip,segment)


def _narration_linkage_spans(
    plan: CreationTaskMontagePlan,
) -> list[tuple[str, int, int]]:
    return metadata_narration_linkage_spans(plan)


def _apply_reorder_narration_linkage(
    workspace_id: str,
    task: CreationTask,
    plan: CreationTaskMontagePlan,
) -> tuple[list[dict[str, object]], list[SubtitleTrack]]:
    try:
        return _metadata_edit_use_case().reorder_narration_linkage(workspace_id, task, plan)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _linkage_lines(linkage):
    return _metadata_edit_use_case().linkage_lines(linkage)


def _person_render_matches_narration(render,segment,transforms):
    return _person_preview_use_case().render_matches_narration(render,segment,transforms)


def _person_render_media_path(render: PersonRender) -> Path | None:
    return _picture_preview_renderer().person_path(render)


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


def _generate_creation_task_person_render_record(workspace_id,task_id,payload,*,regeneration_token=None,grant_context=None):
    try:
        return _person_stage_use_case().generate_record(workspace_id,task_id,payload,regeneration_token=regeneration_token,grant_context=grant_context)
    except PersonStageRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc



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
    _readable_creation_task(request, task_id)
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
    status_code=202,
)
def create_creation_task_montage_plan(
    task_id: str,
    request: Request,
    payload: StoryboardRegenerationRequest,
) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _exact_edit_access(task_id, request, write=True)
    if payload.assistant_turn_id is not None:
        _assistant_creation_adoption_scope(task_id, request, str(payload.assistant_turn_id), draft_only=True)
    actor = _require_permit_actor(request, workspace_id)
    runtime = _creation_runtime()
    try:
        return _creation_execution_control().regenerate_storyboard(workspace_id, task_id, payload, actor, runtime)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/api/creation-tasks/{task_id}/montage/plan/requests/{request_id}")
def read_storyboard_regeneration(task_id: str, request_id: UUID, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _exact_edit_access(task_id, request)
    actor = _require_permit_actor(request, workspace_id)
    try:
        return _creation_execution_control().read_storyboard_regeneration(workspace_id, task_id, str(request_id), actor)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _create_creation_task_montage_plan_for_workspace(workspace_id: str, task_id: str,
    *, grant_context: CreationStageGrantContext | None = None) -> CreationTaskMontagePlanResponse:
    try:
        montage = _montage_stage_use_case().generate(workspace_id, task_id, grant_context=grant_context)
    except MontageStageRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _creation_task_montage_response(montage)


def _creation_task_montage_candidates(workspace_id,plan,clip,*,automation_only=False,viewer_user_id=None,local_diagnostic=False):
    return _metadata_edit_use_case().montage_candidates(workspace_id,plan,clip,automation_only=automation_only,viewer_user_id=viewer_user_id,local_diagnostic=local_diagnostic)


@app.get(
    "/api/creation-tasks/{task_id}/montage/clips/{clip_id}/candidates",
    response_model=list[MontageClipCandidateResponse],
)
def get_creation_task_montage_clip_candidates(
    task_id: str,
    clip_id: str,
    request: Request,
    for_automation: bool = False,
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
    return _creation_task_montage_candidates(
        workspace_id, plan, clip, automation_only=for_automation,
        viewer_user_id=_content_reader(request, workspace_id)[0],
        local_diagnostic=_content_reader(request, workspace_id)[1],
    )


def _edit_preview_response(
    intent: EditIntent,
    proposal: EditProposal,
) -> CreationTaskEditPreviewResponse:
    return CreationTaskEditPreviewResponse.model_validate(
        {"intent": asdict(intent), "proposal": asdict(proposal)}
    )


def _render_stage_use_case(repository=None):
    repository = repository if repository is not None else production_repo
    metadata = _picture_preview_components(repository)[1]
    preview = PersonPreviewUseCase(repository, metadata, latentsync_provider, avatar_lipsync_provider)
    return RenderStageUseCase(repository, metadata, preview, video_job_runner, settings)


def _montage_stage_use_case(repository=None):
    repository = repository if repository is not None else production_repo
    person_stage = _person_stage_use_case(repository)
    return MontageStageUseCase(repository, person_stage.metadata, person_stage, montage_planner)


def _person_stage_use_case(repository=None):
    repository=repository if repository is not None else production_repo
    metadata=_picture_preview_components(repository)[1]
    preview=PersonPreviewUseCase(repository,metadata,latentsync_provider,avatar_lipsync_provider)
    return PersonStageUseCase(repository,metadata,preview,video_job_runner,settings)


def _narration_stage_use_case(repository=None,*,narration_budget=None):
    repository=repository if repository is not None else production_repo
    return NarrationStageUseCase(repository,narration_generator,_picture_preview_components(repository)[1],narration_budget)


def _cover_use_case(repository=None, *, budget=None):
    repo = production_repo if repository is None else repository
    return CoverUseCase(repo, artifact_store, _picture_preview_components(repo)[1],
        verify_identity=_verify_creation_identity, output_root=settings.video_output_path,
        budget=cover_budget_from_env() if budget is None else budget,
        on_settled=_wake_assistant_continuations)


def _cover_confirm_response(result):
    montage = creation_task_montage_plan_from_payload(result.version.snapshot["montage"])
    return CreationTaskEditConfirmResponse(operation=asdict(result.operation),
        montage=_creation_task_montage_response(montage), narration=None)


def _confirm_cover_response(task, proposal, request, *, request_id=None):
    _require_automation_task_open(request, task.workspace_id, task.id)
    actor = _require_permit_actor(request, task.workspace_id)
    try:
        owner = _cover_use_case()
        previous = owner.original_result(actor, task.id, proposal.id)
        if previous is not None:
            return _cover_confirm_response(previous)
        owner.require_budget()
        source = production_repo.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get('request_source')
        if source is not None and source['source_facts'] != _exact_edit_source_facts(task, CreationTaskExactEditRequest.model_validate(source['action']), request):
            raise ValueError('预览后的源文件或片段修订已变化，请重新预览')
        runtime = _creation_runtime()
        registered = runtime.registrations["creation_job"].owner.cover
        return _cover_confirm_response(registered.submit(runtime, actor, task.id, proposal.id,
                                                         request_id=request_id))
    except CoverUnavailable as exc:
        raise _topic_failure_response(status_code=503, error='cover_budget_unavailable', reason=str(exc),
            impact='当前时间线和已有成片保持不变。', repair_action='先完成独立封面资源标定。', retryable=False) from exc
    except CoverUnresolved as exc:
        raise HTTPException(status_code=503, detail={'error': 'cover_generation_unknown',
            'reason': str(exc), 'job_id': exc.job.id, 'status': exc.job.status,
            'request_id': request_id, 'impact': '当前时间线保持不变，未伪造采用或释放。',
            'repair_action': '查询原封面请求与设备许可，续交原固定回执，不重复生成。', 'retryable': False}) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _metadata_edit_use_case():
    return _picture_preview_components()[1]


def _linked_timeline_use_case():
    return LinkedTimelineEditUseCase(production_repo, _metadata_edit_use_case())


def _linked_timeline_values(payload):
    try:
        return _exact_preview_use_case().linked_values(payload)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _linked_timeline_command(task,request,operation_type,values,*,source=None):
    member_id,diagnostic=_content_reader(request,task.workspace_id)
    if source is not None:
        member_id,diagnostic=_assistant_member_id(request,task.workspace_id),False
    identity={'member_id':member_id,'local_diagnostic':diagnostic}
    reader_identity=None
    if operation_type == 'InsertClip':
        reader,reader_diagnostic=_content_reader(request,task.workspace_id)
        reader_identity={'member_id':reader,'local_diagnostic':reader_diagnostic}
    return _exact_preview_use_case().linked_command(task,identity,operation_type,values,
        source=source,reader_identity=reader_identity)


def _linked_timeline_apply_command(task, proposal, request, payload=None):
    source = production_repo.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source")
    command = _linked_timeline_command(task, request, proposal.operation["operation_type"], proposal.operation["payload"], source=source)
    requesting = payload is not None and payload.request_id is not None
    return LinkedEditApplyCommand(task.workspace_id, task.id, proposal.id, command.actor_identity,
        _assistant_member_id(request, task.workspace_id), source=source,
        request_id=str(payload.request_id) if requesting else None,
        expected_base_version_id=payload.expected_base_version_id if requesting else None,
        base_version_submitted=requesting and "expected_base_version_id" in payload.model_fields_set,
        expected_input_digest=payload.expected_input_digest if requesting else None,
        request_fields=payload.model_dump(mode="json", exclude_unset=True) if requesting else {})


def _confirm_linked_timeline_response(task, proposal, request, payload=None, *, wake=False):
    try:
        outcome = _linked_timeline_use_case().confirm(_linked_timeline_apply_command(task, proposal, request, payload))
    except InsertMaterialMissing as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except InsertSourceRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="编辑提案不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = _metadata_edit_response(outcome.outcome)
    if outcome.record_projection:
        if outcome.newly_applied:
            _save_edit_request_result(task, outcome.event_id, response)
        else:
            production_repo.save_edit_request_result(task, outcome.event_id,
                status_code=200, response=response.model_dump(mode="json"))
    if wake and outcome.newly_applied:
        _wake_assistant_continuations()
    return response


def _metadata_edit_response(outcome):
    if isinstance(outcome, MetadataEditReplay):
        if outcome.recorded_projection is not None:
            recorded = outcome.recorded_projection
            return JSONResponse(status_code=recorded['status_code'], content=recorded['response'])
        outcome = outcome.result
    return CreationTaskEditConfirmResponse(operation=asdict(outcome.operation),
        montage=_creation_task_montage_response(outcome.montage),
        narration=_creation_task_narration_response(outcome.task, outcome.narration)
            if outcome.narration is not None else None)


def _metadata_edit_command(task, request, operation_type, values, *, source=None):
    user_id, diagnostic = _content_reader(request, task.workspace_id)
    if source is not None:
        user_id, diagnostic = _assistant_member_id(request, task.workspace_id), False
    return metadata_edit_command(task, {"member_id":user_id, "local_diagnostic":diagnostic}, operation_type, values, source=source)


def _validate_edit_graphic_sources(workspace_id, task_id, overlays):
    return _metadata_edit_use_case().validate_graphic_sources(workspace_id, task_id, overlays)


def _bind_affected_source_command(verifier,command,before,after):
    try:return _metadata_edit_use_case().bind_affected_source_command(verifier,command,before,after)
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _edit_clip_source_bounds(task, request, clip):
    identity, diagnostic = _content_reader(request, task.workspace_id)
    selection = production_repo.resolve_edit_clip_source(task.workspace_id, task.id, clip, {},
        user_id=identity, local_diagnostic=diagnostic)
    return selection.start_seconds, selection.end_seconds, selection.is_speech


def _resolve_edit_clip_source(task,request,clip,values):
    reader,diagnostic=_content_reader(request,task.workspace_id)
    return _metadata_edit_use_case().resolve_edit_clip_source(task,{'member_id':reader,'local_diagnostic':diagnostic},clip,values)


def _link_reordered_edit(task, candidate):
    return _metadata_edit_use_case().link_reordered_edit(task, candidate)


def _preview_metadata_settings(task, request, operation_type, values, base_version_id):
    reader, diagnostic = _content_reader(request, task.workspace_id)
    source = EXACT_EDIT_SOURCE.get()
    if source is not None:
        reader, diagnostic = _assistant_member_id(request, task.workspace_id), False
    try:
        return _edit_preview_response(*_metadata_edit_use_case().preview_metadata_settings(task, operation_type, values, base_version_id,
            identity={'member_id':reader,'local_diagnostic':diagnostic}, source=source))
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _save_direct_metadata_edit(task_id, request, payload):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    case = _metadata_edit_use_case()
    event_id = None
    try:
        event_id, replay = case.begin(task,
            _exact_edit_access(task_id, request, write=True)[1] if payload.request_id is not None else None,
            payload.request_id, payload.model_dump(mode='json', exclude_unset=True), phase='apply')
        if replay is not None:
            result = _metadata_edit_response(replay)
            if replay.recorded_projection is None:
                case.record_result(task, event_id, status_code=200, response=result.model_dump(mode='json'))
                _wake_assistant_continuations()
            return result
        clean, source = _prepare_exact_edit_request(task_id, request, payload)
        values = clean.action_values()
        values.pop('operation_type')
        if set(values) - EXACT_EDIT_FIELDS[clean.operation_type]:
            raise HTTPException(status_code=422, detail='精确编辑不能混入其他操作')
        required = ({'cue_id','text'} if clean.operation_type == 'UpdateSubtitleCue' else
            EXACT_MONTAGE_REQUIRED_FIELDS.get(clean.operation_type, set()) | ({'clip_id'} if clean.operation_type in CLIP_METADATA_OPERATIONS else set()))
        if any(values.get(key) is None for key in required):
            raise HTTPException(status_code=422, detail='exact edit is missing required values')
        command = _metadata_edit_command(task, request, clean.operation_type,
            {'base_version_id':task.current_timeline_version_id, **values}, source=source)
        outcome = case.save(task, command,
            audio_edit=_audio_mix_request(clean, task.current_timeline_version_id) if clean.operation_type == 'EditAudioTimeline' else None,
            audio_actor=_require_permit_actor(request, workspace_id) if clean.operation_type == 'EditAudioTimeline' else None)
        result = _metadata_edit_response(outcome)
        case.record_result(task, event_id, status_code=200, response=result.model_dump(mode='json'))
    except (HTTPException, LegacyMontageReference, SubtitleNoChange, SubtitleTargetMissing,
            InvalidMontage, PermissionError, EditConflict, ValueError, KeyError, OSError) as exc:
        if isinstance(exc, HTTPException):
            failure = exc
        elif isinstance(exc, (LegacyMontageReference, SubtitleNoChange)):
            failure = _topic_failure_response(**exc.details)
        else:
            code = (404 if isinstance(exc, SubtitleTargetMissing) else
                    422 if isinstance(exc, InvalidMontage) else
                    403 if isinstance(exc, PermissionError) else 409)
            failure = HTTPException(status_code=code,
                detail=exc.args[0] if isinstance(exc, SubtitleTargetMissing) else str(exc))
        if event_id is not None:
            case.record_result(task, event_id, status_code=failure.status_code, response={"detail": failure.detail})
        raise failure from exc
    _wake_assistant_continuations()
    return result


def _logical_montage_output_refs(plan: CreationTaskMontagePlan) -> list[str]:
    try:
        return metadata_logical_montage_output_refs(plan)
    except LegacyMontageReference as exc:
        raise _topic_failure_response(**exc.details) from exc


def _current_narration_snapshot(workspace_id: str, task: CreationTask) -> dict[str, object]:
    return _metadata_edit_use_case().current_narration_snapshot(workspace_id, task)


def _narration_segments_from_snapshot(
    snapshot: dict[str, object],
) -> list[NarrationSegment] | None:
    return MetadataEditUseCase.narration_segments_from_snapshot(snapshot)


def _ensure_montage_base_version(
    workspace_id: str,
    task: CreationTask,
    plan: CreationTaskMontagePlan,
) -> TimelineVersion:
    return _metadata_edit_use_case().ensure_montage_base(workspace_id, task, plan)


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
    return metadata_prepare_changed_montage(plan, message=message)


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
    connection=None,
    direct_command=None,
    direct_intent=None,
    direct_source_verifier=None,
    proposal_source_command=None,
    proposal_source_verifier=None,
) -> CreationTaskEditConfirmResponse:
    try:
        result = _metadata_edit_use_case().persist_montage_edit(workspace_id=workspace_id, task=task, proposal=proposal, base_version=base_version, updated_plan=updated_plan, operation_type=operation_type, operation_payload=operation_payload, narration_segments=narration_segments, narration_artifact=narration_artifact, subtitle_source_version=subtitle_source_version, edited_subtitle_tracks=edited_subtitle_tracks, connection=connection, direct_command=direct_command, direct_intent=direct_intent, direct_source_verifier=direct_source_verifier, proposal_source_command=proposal_source_command, proposal_source_verifier=proposal_source_verifier)
    except LegacyMontageReference as exc:
        raise _topic_failure_response(**exc.details) from exc
    except InvalidMontage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result if connection is not None else _metadata_edit_response(result)


def _subtitle_display_selection(tracks, mode):
    try:
        return subtitle_display_selection(tracks, mode)
    except EditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

def _execution_subtitle_track(track: SubtitleTrack) -> SubtitleExecutionTrack:
    return metadata_execution_subtitle_track(track)


def _cover_asset_for_version(
    workspace_id: str,
    task_id: str,
    version: TimelineVersion,
) -> CoverAsset | None:
    return _metadata_edit_use_case().cover_asset_for_version(workspace_id, task_id, version)


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
    return _metadata_edit_use_case().publish_subtitle_text(workspace_id=workspace_id, task_id=task_id, proposal_id=proposal_id, kind=kind, input_refs=input_refs, content=content, engine=engine, parameters=parameters)


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
    try:
        outcome = _metadata_edit_use_case().persist_subtitle_edit(workspace_id=workspace_id, task=task,
            proposal=proposal, base_version=base_version, result_version_id=result_version_id,
            operation_type=operation_type, operation_payload=operation_payload, subtitle_tracks=subtitle_tracks,
            cover_asset=cover_asset, updated_plan=updated_plan, snapshot_source=snapshot_source,
            narration_segments=narration_segments, narration_artifact=narration_artifact)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _metadata_edit_response(outcome)










def _presenter_share_selection(plan,target):
    return _person_preview_use_case().share_selection(plan,target)


def _presenter_share_broll_candidate(workspace_id: str, plan: CreationTaskMontagePlan, clip: MontageClip):
    # This one picks by itself — the first usable B-roll becomes the replacement
    # — so material the user kept out of automation must not appear here.
    for candidate in _creation_task_montage_candidates(workspace_id, plan, clip, automation_only=True):
        asset = production_repo.get_asset(workspace_id, candidate.asset_id)
        segment = next(item for item in asset.segments if item.id == candidate.segment_id)
        if effective_segment_usage_role(asset, segment) == "b_roll":
            return candidate
    return None


def _presenter_share_changed_clips(plan,selected):
    return _person_preview_use_case().share_changed_clips(plan,selected)






def _bind_assistant_edit_instruction(request,task,payload):
    if payload.assistant_turn_id is None:return payload
    turn=conversation_repo.get_turn(str(payload.assistant_turn_id))
    if turn is None:raise HTTPException(status_code=404,detail='本轮对话不存在或不可访问。')
    workspace_id,member_id,_conversation=_assistant_conversation_for_request(request,turn['conversation_id'])
    try:
        origin=conversation_repo.task_turn_source(turn['id'],workspace_id,member_id,task.id)
        _require_assistant_input_origin(request, {**origin, 'assistant_turn_id': turn['id']})
        return _command_preview_use_case().bind_after_access(task,payload,turn,workspace_id,member_id)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc





def _reorder_montage_plan(workspace_id,task,plan,new_order,*,link_narration):
    try:return _metadata_edit_use_case().reorder_montage_plan(workspace_id,task,plan,new_order,link_narration=link_narration)
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _timeline_reorder_narration_scope(plan,new_order,target_ids):
    try:return _metadata_edit_use_case().reorder_narration_scope(plan,new_order,target_ids)
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc







@app.post('/api/creation-tasks/{task_id}/edits/preview',response_model=CreationTaskEditPreviewResponse)
def preview_creation_task_edit(task_id:str,request:Request,payload:CreationTaskEditPreviewRequest):
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    payload=_bind_assistant_edit_instruction(request,task,payload)
    base_version_id=task.current_timeline_version_id
    try:return _preview_command_edit_request(task_id,request,payload,base_version_id=base_version_id)
    except TaskMediaRequired as required:media_kind=required.kind
    actor=_require_permit_actor(request,workspace_id)
    task=_creation_task_or_404(workspace_id,task_id)
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403,detail='当前成员不能安排此任务的媒体处理')
    values={'_media_kind':media_kind,'_draft':request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact'),
        'payload':payload.model_dump(mode='json',exclude_unset=True)}
    try:
        runtime=_creation_runtime();media=runtime.registrations['creation_job'].owner.task_media
        if task.montage_plan_id and task.current_timeline_version_id is None:
            media.facts(workspace_id,task_id,'command_edit_preview',values,actor)
            if media.verify_identity(actor)!='ready':raise PermissionError('媒体处理身份失效或无法复核')
            plan=creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id,task_id))
            base=_ensure_montage_base_version(workspace_id,task,plan)
            if 'base_version_id' in values['payload']:values['payload']['base_version_id']=base.id
        job=media.submit(runtime,actor,task_id,'command_edit_preview',values,'edit_preview')
        receipt=_task_media_job_response(media,job,actor,runtime);receipt['result']=None
        return JSONResponse(status_code=202,content=receipt)
    except PermissionError as exc:raise HTTPException(status_code=403,detail=str(exc)) from exc
    except (ValueError,KeyError) as exc:raise HTTPException(status_code=422,detail=str(exc)) from exc


def _preview_command_edit_request(task_id,request,payload,*,base_version_id=False):
    workspace_id=current_workspace_id(request)
    task=_creation_task_or_404(workspace_id,task_id)
    if base_version_id is False:base_version_id=task.current_timeline_version_id
    payload=_bind_assistant_edit_instruction(request,task,payload)
    reader,diagnostic=_content_reader(request,workspace_id)
    identity={'member_id':reader,'local_diagnostic':diagnostic}
    request_source=EXACT_EDIT_SOURCE.get()
    if request_source is not None:identity={'member_id':_assistant_member_id(request,workspace_id),'local_diagnostic':False}
    case=_command_preview_use_case()
    draft=request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact')
    options={'identity':identity,'reader_identity':{'member_id':reader,'local_diagnostic':diagnostic},
        'request_source':request_source,'draft_only':draft,'execution':None,'actor':None}
    try:
        outcome=case.preview(task,payload,base_version_id=base_version_id,**options)
        outcome=case.resolve_action(task,outcome,**options)
        if isinstance(outcome,AudioCommandPreview):
            actor=_request_permit_actor(request,workspace_id)
            outcome=case.preview_audio(task,outcome,actor,request_source=request_source,draft_only=draft,execution=None)
        if isinstance(outcome,ClipPreviewCommand):
            actor=_require_permit_actor(request,workspace_id)
            command=case.exact.prepare_clip_submission(actor,outcome,original_command=payload)
            if command.source is not None:
                _require_assistant_input_origin(request,command.source)
            runtime=_creation_runtime();media=runtime.registrations['creation_job'].owner.task_media
            job=case.exact.submit_clip(runtime,media,actor,command,operation='command_edit_preview')
            receipt=_task_media_job_response(media,job,actor,runtime);receipt['result']=None
            return JSONResponse(status_code=202,content=receipt)
        return _edit_preview_response(*outcome) if outcome is not None else None
    except (EditPreviewRejected,PicturePreviewRejected,AudioEditRejected,CommandParseRejected) as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except (ClipPreviewUnavailable,HostBudgetUncalibrated) as exc:raise HTTPException(status_code=503,detail=str(exc)) from exc
    except (ClipPreviewConflict,TimelineVersionConflict) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc





def _submit_person_edit(workspace_id, task, proposal, request):
    actor = _require_permit_actor(request, workspace_id)
    runtime = _creation_runtime()
    owner = runtime.registrations["creation_job"].owner
    script = production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)
    plan = production_repo.get_creation_plan(workspace_id, task.plan_id)
    digest = person_edit_digest(proposal.operation)
    # Repeated exact requests compile fresh proposals, but bind the same target
    # and base version to the same durable job rather than generating twice.
    candidate = JobRun(id=f"job_{uuid4().hex}", workspace_id=workspace_id, task_id=task.id,
        kind=PERSON_EDIT_KIND, input_digest=digest,
        idempotency_key=f"person-edit:{task.id}:{actor.member_id}:{digest}:{proposal.id}")
    event_payload = {**actor.identity_payload(), "script_id": script.id, "plan_id": plan.id,
        "revision_digest": revision_digest(script, plan), "input_digest": digest,
        "edit_proposal_id": proposal.id}
    job = None
    try:
        key, _ = owner.edit_facts(candidate, task, event_payload, plan)
        # Hold the existing SQLite write transaction across active dedup and
        # job/event creation. A fresh proposal may retry a finished failure;
        # the same old proposal cannot resurrect it or attach to a new job.
        with production_repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND idempotency_key=?",
                (workspace_id, candidate.idempotency_key)).fetchone()
            if prior is not None and prior["status"] not in {"queued", "running", "interrupted"}:
                raise ValueError("这次人物编辑已结束，请查看作业结果后重新预览")
            active = conn.execute(
                "SELECT j.* FROM job_runs j JOIN task_events e ON e.job_id=j.id AND e.workspace_id=j.workspace_id "
                "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND j.input_digest=? "
                "AND j.status IN ('queued','running','interrupted') AND e.event_type='person_edit_requested' "
                "AND json_extract(e.payload_json,'$.member_id')=? ORDER BY j.created_at DESC LIMIT 1",
                (workspace_id, task.id, PERSON_EDIT_KIND, digest, actor.member_id)).fetchone()
            if active is not None:
                job = production_repo._job_run_from_row(active)
            else:
                job = production_repo.create_job_run(candidate, connection=conn)
                production_repo.append_task_event(TaskEvent(
                    id=f"event_{job.id}_start", workspace_id=workspace_id, task_id=task.id,
                    job_id=job.id, event_type="person_edit_requested", payload=event_payload,
                    created_at=job.created_at), connection=conn)
        queued = runtime.scheduler.submit(SubmissionIntent(workspace_id, "creation_job", job.id,
            digest, key, SchedulePolicy()), actor)
    except (PermissionError, KeyError, StopIteration, ValueError) as exc:
        if job is not None:
            with production_repo._connect() as conn:
                conn.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,"
                    "finished_at=?,updated_at=? WHERE workspace_id=? AND id=? AND status='queued'",
                    (str(exc), "人物编辑未安排成功，当前版本保持不变。", "修复原因后重新预览人物修改。",
                     utc_now().isoformat(), utc_now().isoformat(), workspace_id, job.id))
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 409, detail=str(exc)) from exc
    runtime.wake()
    return JSONResponse(status_code=202, content=CreationTaskEditQueuedResponse(
        job_id=job.id, request_id=queued.id).model_dump())


def _submit_narration_edit(workspace_id, task, proposal, request):
    actor = _require_permit_actor(request, workspace_id)
    runtime = _creation_runtime()
    owner = runtime.registrations["creation_job"].owner
    script = production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)
    plan = production_repo.get_creation_plan(workspace_id, task.plan_id)
    digest = narration_edit_digest(proposal.operation)
    candidate = JobRun(id=f"job_{uuid4().hex}", workspace_id=workspace_id, task_id=task.id,
        kind=NARRATION_EDIT_KIND, input_digest=digest,
        idempotency_key=f"narration-edit:{task.id}:{actor.member_id}:{digest}:{proposal.id}")
    event_payload = {**actor.identity_payload(), "script_id": script.id, "plan_id": plan.id,
        "revision_digest": revision_digest(script, plan), "input_digest": digest,
        "edit_proposal_id": proposal.id}
    job = None
    try:
        key, _ = owner.narration_edit_facts(candidate, task, event_payload, plan)
        with production_repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND idempotency_key=?",
                (workspace_id, candidate.idempotency_key)).fetchone()
            if prior is not None and prior["status"] not in {"queued", "running", "interrupted"}:
                raise ValueError("这次配音编辑已结束，请查看作业结果后重新预览")
            active = conn.execute(
                "SELECT j.* FROM job_runs j JOIN task_events e ON e.job_id=j.id AND e.workspace_id=j.workspace_id "
                "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND j.input_digest=? "
                "AND j.status IN ('queued','running','interrupted') AND e.event_type='narration_edit_requested' "
                "AND json_extract(e.payload_json,'$.member_id')=? ORDER BY j.created_at DESC LIMIT 1",
                (workspace_id, task.id, NARRATION_EDIT_KIND, digest, actor.member_id)).fetchone()
            if active is not None:
                job = production_repo._job_run_from_row(active)
            else:
                job = production_repo.create_job_run(candidate, connection=conn)
                production_repo.append_task_event(TaskEvent(
                    id=f"event_{job.id}_start", workspace_id=workspace_id, task_id=task.id,
                    job_id=job.id, event_type="narration_edit_requested", payload=event_payload,
                    created_at=job.created_at), connection=conn)
        queued = runtime.scheduler.submit(SubmissionIntent(workspace_id, "creation_job", job.id,
            digest, key, SchedulePolicy()), actor)
    except (PermissionError, KeyError, StopIteration, ValueError) as exc:
        if job is not None:
            with production_repo._connect() as conn:
                conn.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,"
                    "finished_at=?,updated_at=? WHERE workspace_id=? AND id=? AND status='queued'",
                    (str(exc), "配音编辑未安排成功，当前版本保持不变。", "修复原因后重新预览配音修改。",
                     utc_now().isoformat(), utc_now().isoformat(), workspace_id, job.id))
        raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 409, detail=str(exc)) from exc
    runtime.wake()
    return JSONResponse(status_code=202, content=CreationTaskEditQueuedResponse(
        job_id=job.id, request_id=queued.id).model_dump())






def _apply_person_segment_edit(workspace_id,task,proposal,request=None):
    try:
        result=_person_stage_use_case().apply_segment_edit(workspace_id,task,proposal)
    except PersonEditGrantRequired:
        return _submit_person_edit(workspace_id,task,proposal,request)
    except PersonStageRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    return _metadata_edit_response(result)



@app.get("/api/creation-tasks/{task_id}/edits/{proposal_id}", response_model=CreationTaskEditPreviewResponse)
def get_creation_task_edit_preview(task_id: str, proposal_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="没有找到当前任务。")
    try:
        proposal = production_repo.get_edit_proposal(workspace_id, proposal_id)
        intent = production_repo.get_edit_intent(workspace_id, proposal.intent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到所选修改方案。") from exc
    if proposal.task_id != task_id or proposal.status != "proposed":
        raise HTTPException(status_code=409, detail="这份修改已处理，请查看当前任务版本。")
    if proposal.operation["payload"].get("base_version_id") != task.current_timeline_version_id:
        raise HTTPException(status_code=409, detail="任务版本已变化，旧修改不能直接应用，请重新生成方案。")
    return _edit_preview_response(intent, proposal)


@app.get("/api/creation-tasks/{task_id}/edit-controls")
def get_creation_task_edit_controls(task_id: str, request: Request):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="没有找到当前任务。")
    summary = project_task_summary(production_repo, task).summary
    proposals = production_repo.list_edit_proposals(workspace_id, task_id, base_version_id=task.current_timeline_version_id, limit=8)
    return {"task_id": task_id, "timeline_version_id": summary["current_timeline_version_id"],
        "timeline_sequence": summary["timeline_sequence"], "can_undo": summary["can_undo"], "can_redo": summary["can_redo"],
        "proposals": [{"id": item.id, "summary": item.summary, "operation_type": item.operation["operation_type"],
            "steps": item.operation["payload"].get("steps", [])} for item in proposals]}


def _assistant_edit_control_instruction(task: CreationTask, request: Request, payload: CreationTaskTimelineUndoRequest, action: str) -> str:
    if payload.assistant_turn_id is None:
        raise HTTPException(status_code=409, detail="当前修改要求不可核验，未改变任务。")
    turn = conversation_repo.get_turn(str(payload.assistant_turn_id))
    if turn is None:
        raise HTTPException(status_code=404, detail="没有找到当前对话要求。")
    workspace_id, member_id, _chat = _assistant_conversation_for_request(request, turn["conversation_id"])
    try:
        origin = conversation_repo.task_turn_source(turn["id"], workspace_id, member_id, task.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    context = origin["context"]
    bound = _bind_assistant_edit_instruction(request, task, CreationTaskEditPreviewRequest(
        assistant_turn_id=payload.assistant_turn_id, raw_command=origin["raw_command"],
        context_stage=context.get("stage", "montage"), selected_object_refs=context.get("selected_object_refs", []),
        playhead_ms=context.get("playhead_ms"),
    ))
    if (payload.expected_timeline_version_id != task.current_timeline_version_id
        or payload.expected_timeline_sequence != context.get("target_snapshot", {}).get("timeline_sequence")):
        raise HTTPException(status_code=409, detail="修改所依据的任务版本已变化，未改变任务。")
    command = bound.raw_command
    if re.search(r"不要|不用|别|不(?:撤销|重做|取消|放弃)", command):
        raise HTTPException(status_code=409, detail="当前要求没有授权这一动作，任务保持不变。")
    prefix = r"(?:请|麻烦|现在|先|帮我|请帮我|可以帮我|能不能帮我)?"
    if action == "reject":
        proposal_scope = r"(?:这(?:个|份|次)|当前|刚才的)?(?:修改)?(?:提案|方案|修改(?:建议)?)"
        allowed = re.fullmatch(prefix + rf"(?:取消|放弃|丢弃)(?:一下)?{proposal_scope}(?:一下|吧|吗|好吗)?[。！!？?]?", command.strip())
    else:
        scope = r"(?:上一步(?:修改|编辑|操作)?|上一次(?:内容)?(?:修改|编辑|操作)?|刚才(?:撤销(?:的)?|的)?(?:那)?(?:一步|一次|修改|编辑|操作)?|最后一次(?:修改|编辑|操作)?)"
        verb = r"撤销|回退" if action == "undo" else r"重做|恢复"
        allowed = re.fullmatch(prefix + rf"(?:整体)?(?:{verb})(?:一下)?{scope}?(?:一下|吧|吗|好吗)?[。！!？?]?", command.strip())
        if re.search(r"调色|颜色|字幕|音量|音乐|配音|口型|镜头|只撤销.+项|仅撤销.+项", command):
            raise HTTPException(status_code=409, detail="当前只能整体撤销或重做上一项编辑，尚不能选择性撤销复合编辑中的一项；未改变任务。")
    if not allowed:
        raise HTTPException(status_code=409, detail="需要明确的撤销、重做或取消修改方案要求；未改变任务。")
    return command


@app.post("/api/creation-tasks/{task_id}/edits/{proposal_id}/reject")
def reject_creation_task_edit_proposal(task_id: str, proposal_id: str, request: Request, payload: CreationTaskTimelineUndoRequest):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    _assistant_edit_control_instruction(task, request, payload, "reject")
    try:
        proposal = production_repo.get_edit_proposal(workspace_id, proposal_id)
        if production_repo.get_edit_intent(workspace_id, proposal.intent_id).constraints.get("request_source") is not None:
            _edit_proposal_binding(task_id, proposal_id, request)
        if proposal.task_id != task_id or proposal.operation["payload"].get("base_version_id") != task.current_timeline_version_id:
            raise HTTPException(status_code=409, detail="这份修改的任务或基础版本已变化。")
        if proposal.status != "rejected":
            proposal = production_repo.transition_edit_proposal(workspace_id, task_id, proposal_id, status="rejected")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这份修改方案。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _wake_assistant_continuations()
    return {"task_id": task_id, "proposal_id": proposal.id, "status": proposal.status,
        "timeline_version_id": task.current_timeline_version_id}


def _edit_proposal_binding(task_id, proposal_id, request):
    task, member_id = _exact_edit_access(task_id, request, write=True)
    try:
        proposal = production_repo.get_edit_proposal(task.workspace_id, proposal_id)
        intent = production_repo.get_edit_intent(task.workspace_id, proposal.intent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="编辑提案不存在") from exc
    if proposal.task_id != task_id:
        raise HTTPException(status_code=404, detail="编辑提案不存在")
    origin = intent.constraints.get("request_source")
    if origin is not None and origin["member_id"] != member_id:
        raise HTTPException(status_code=403, detail="提案不属于当前请求成员")
    digest = (_linked_timeline_use_case().input_digest(task, proposal, origin)
        if proposal.operation.get("operation_type") in LINKED_EDIT_OPERATIONS else media_digest([task.id, proposal.id, proposal.operation, proposal.target_refs,
                           origin.get("source_facts") if origin else None]))
    return task, member_id, proposal, digest


def _original_edit_result(task, proposal):
    operations = [item for item in production_repo.list_edit_operations(task.workspace_id, task.id) if item.proposal_id == proposal.id]
    if operations:
        operation = operations[0]
        version = production_repo.get_timeline_version(task.workspace_id, operation.result_version_id)
        montage = creation_task_montage_plan_from_payload(version.snapshot["montage"])
        return CreationTaskEditConfirmResponse(operation=asdict(operation), montage=_creation_task_montage_response(montage), narration=None)
    with production_repo._connect() as conn:
        row = conn.execute(
            "SELECT j.* FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id AND e.job_id=j.id "
            "WHERE j.workspace_id=? AND j.task_id=? AND json_extract(e.payload_json,'$.edit_proposal_id')=? "
            "ORDER BY j.created_at LIMIT 1", (task.workspace_id, task.id, proposal.id),
        ).fetchone()
        if row is None:
            return None
        request_row = conn.execute("SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_id=? ORDER BY created_at LIMIT 1",
                                   (task.workspace_id, row["id"])).fetchone()
    result = CreationTaskEditJobReplayResponse(status=row["status"], job_id=row["id"],
        request_id=request_row["id"] if request_row else None, failure_reason=row["failure_reason"])
    return JSONResponse(status_code=202 if row["status"] in {"queued", "running", "interrupted"} else 200, content=result.model_dump(mode="json"))


@app.get("/api/creation-tasks/{task_id}/edits/{proposal_id}/result")
def get_creation_task_edit_result(task_id: str, proposal_id: str, request: Request):
    task, _member_id, proposal, digest = _edit_proposal_binding(task_id, proposal_id, request)
    if proposal.operation.get("operation_type") in LINKED_EDIT_OPERATIONS:
        try:
            saved = _linked_timeline_use_case().read_result(_linked_timeline_apply_command(task, proposal, request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"proposal_id": saved.proposal.id, "base_version_id": saved.proposal.operation["payload"].get("base_version_id"),
            "input_digest": saved.input_digest, "status": saved.proposal.status,
            "result": _metadata_edit_response(saved.outcome).model_dump(mode="json") if saved.outcome is not None else None}
    if is_subtitle_edit(proposal.operation.get("operation_type"), proposal.operation.get("payload", {})):
        try:
            outcome = _subtitle_edit_use_case().read_result(_subtitle_proposal_command(task, proposal, request), proposal.id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"proposal_id": proposal.id, "base_version_id": proposal.operation["payload"].get("base_version_id"),
            "input_digest": digest, "status": proposal.status,
            "result": _metadata_edit_response(outcome).model_dump(mode="json") if outcome is not None else None}
    if proposal.operation.get("operation_type") == "EditCompoundTimeline":
        try:
            outcome = _compound_edit_use_case().read_result(_compound_proposal_command(task, proposal, request), proposal.id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"proposal_id": proposal.id, "base_version_id": proposal.operation["payload"].get("base_version_id"),
            "input_digest": digest, "status": proposal.status,
            "result": _metadata_edit_response(outcome).model_dump(mode="json") if outcome is not None else None}
    result = _original_edit_result(task, proposal)
    return {"proposal_id": proposal.id, "base_version_id": proposal.operation.get("payload", {}).get("base_version_id"),
            "input_digest": digest, "status": proposal.status,
            "result": json.loads(result.body) if isinstance(result, JSONResponse) else result.model_dump(mode="json") if result else None}


@app.post("/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm",
          response_model=CreationTaskEditConfirmResponse | CreationTaskEditQueuedResponse | CreationTaskEditJobReplayResponse)
def confirm_creation_task_edit(task_id: str, proposal_id: str, request: Request,
                              payload: CreationTaskEditApplyRequest | None = None):
    if payload is None or payload.request_id is None:
        try:
            proposal = production_repo.get_edit_proposal(current_workspace_id(request), proposal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="creation task edit proposal not found") from exc
        intent = production_repo.get_edit_intent(proposal.workspace_id, proposal.intent_id)
        if intent.constraints.get("request_source") is not None:
            _edit_proposal_binding(task_id, proposal_id, request)
        if proposal.operation.get("operation_type") in LINKED_EDIT_OPERATIONS:
            task = _creation_task_or_404(current_workspace_id(request), task_id)
            return _confirm_linked_timeline_response(task, proposal, request, payload, wake=True)
        result = _confirm_creation_task_edit(task_id, proposal_id, request)
        _wake_assistant_continuations()
        return result
    task, member_id, proposal, digest = _edit_proposal_binding(task_id, proposal_id, request)
    if proposal.operation.get("operation_type") in LINKED_EDIT_OPERATIONS:
        return _confirm_linked_timeline_response(task, proposal, request, payload, wake=True)
    base = proposal.operation.get("payload", {}).get("base_version_id")
    if (("expected_base_version_id" in payload.model_fields_set and payload.expected_base_version_id != base)
            or (payload.expected_input_digest is not None and payload.expected_input_digest != digest)):
        raise HTTPException(status_code=409, detail="请求与原提案基础版本或输入摘要不一致")
    event_id, fresh = _claim_edit_request(task, member_id, payload.request_id, "apply",
        {"proposal_id": proposal.id, "base_version_id": base, "input_digest": digest,
         "request": payload.model_dump(mode="json", exclude_unset=True)})
    previous = _read_edit_request_result(task, event_id)
    if proposal.operation.get("operation_type") == COVER_OPERATION:
        result = _confirm_cover_response(task, proposal, request, request_id=payload.request_id)
        _save_edit_request_result(task, event_id, result)
        _wake_assistant_continuations()
        return result
    if is_subtitle_edit(proposal.operation.get("operation_type"), proposal.operation.get("payload", {})):
        try:
            outcome = _subtitle_edit_use_case().read_result(_subtitle_proposal_command(task, proposal, request), proposal.id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        previous = (previous if previous is not None else _metadata_edit_response(outcome)) if outcome is not None else None
    elif proposal.operation.get("operation_type") == "EditCompoundTimeline":
        try:
            outcome = _compound_edit_use_case().read_result(_compound_proposal_command(task, proposal, request), proposal.id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        previous = (previous if previous is not None else _metadata_edit_response(outcome)) if outcome is not None else None
    else:
        if previous is not None and "job_id" not in json.loads(previous.body):
            return previous
        previous = _original_edit_result(task, proposal)
    if previous is not None:
        if _read_edit_request_result(task, event_id) is None:
            _save_edit_request_result(task, event_id, previous)
        return previous
    if not fresh:
        raise HTTPException(status_code=409, detail="原应用请求结果尚未确定，请查询原提案结果，不重复执行")
    result = _confirm_creation_task_edit(task_id, proposal_id, request)
    _save_edit_request_result(task, event_id, result)
    _wake_assistant_continuations()
    return result


def _confirm_creation_task_edit(task_id: str, proposal_id: str, request: Request) -> CreationTaskEditConfirmResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    try:
        proposal = production_repo.get_edit_proposal(workspace_id, proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='creation task edit proposal not found') from exc
    if proposal.task_id == task_id and proposal.operation.get('operation_type') == COVER_OPERATION:
        return _confirm_cover_response(task, proposal, request)
    if proposal.task_id != task_id or proposal.status != 'proposed':
        raise HTTPException(status_code=409, detail='creation task edit proposal is not confirmable')
    if proposal.operation.get('operation_type') in LINKED_EDIT_OPERATIONS:
        return _confirm_linked_timeline_response(task, proposal, request)
    if is_subtitle_edit(proposal.operation.get('operation_type'), proposal.operation.get('payload', {})):
        return _confirm_subtitle_response(task, proposal, request)
    if proposal.operation.get('operation_type') == 'EditCompoundTimeline':
        return _confirm_compound_response(task, proposal, request)
    source = production_repo.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('request_source')
    if source is not None and source['source_facts'] != _exact_edit_source_facts(task, CreationTaskExactEditRequest.model_validate(source['action']), request):
        raise HTTPException(status_code=409, detail='预览后的源文件或片段修订已变化，请重新预览')
    operation_draft = proposal.operation
    operation_payload = operation_draft.get('payload')
    operation_type = operation_draft.get('operation_type')
    if operation_type in ({'ReorderTimelineItems', 'ChangePresenterShare', 'ChangeCanvas'} | CLIP_METADATA_OPERATIONS | PICTURE_METADATA_OPERATIONS) and isinstance(operation_payload, dict):
        reader, diagnostic = _content_reader(request, workspace_id)
        reader_identity = {'member_id': reader, 'local_diagnostic': diagnostic}
        identity = ({'member_id': _assistant_member_id(request, workspace_id), 'local_diagnostic': False}
                    if source is not None else reader_identity)
        try:
            outcome = _metadata_edit_use_case().confirm(task, proposal, identity=identity,
                reader_identity=reader_identity, source=source)
        except EditPreviewRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except LegacyMontageReference as exc:
            raise _topic_failure_response(**exc.details) from exc
        except InvalidMontage as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _metadata_edit_response(outcome)
    if operation_type in {'AlignSubtitleTiming', 'ConfigureSubtitleTrack', 'UpdateSubtitleCue'} and isinstance(operation_payload, dict):
        try:
            outcome = _subtitle_edit_use_case().confirm_legacy(task, proposal, settings=settings)
        except EditPreviewRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except LegacyMontageReference as exc:
            raise _topic_failure_response(**exc.details) from exc
        except InvalidMontage as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _metadata_edit_response(outcome)
    if operation_type == 'EditAudioTimeline' and isinstance(operation_payload, dict):
        actor = _require_permit_actor(request, workspace_id)
        try:
            outcome = _audio_edit_use_case().confirm(task, proposal, actor, source=source)
        except AudioEditRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _metadata_edit_response(outcome)
    if operation_type in {'ChangePersonSegment', 'RegeneratePersonRender'} and isinstance(operation_payload, dict):
        _require_automation_task_open(request, workspace_id, task_id)
        return _apply_person_segment_edit(workspace_id, task, proposal, request)
    if operation_type == 'RegenerateNarrationSegment' and isinstance(operation_payload, dict):
        _require_automation_task_open(request, workspace_id, task_id)
        return _submit_narration_edit(workspace_id, task, proposal, request)
    if operation_type == 'RemoveSilence' and isinstance(operation_payload, dict):
        try:
            outcome = _linked_timeline_use_case().confirm_remove_silence(task, proposal)
        except EditPreviewRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except LegacyMontageReference as exc:
            raise _topic_failure_response(**exc.details) from exc
        except InvalidMontage as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _metadata_edit_response(outcome)
    raise HTTPException(status_code=409, detail='unsupported creation task edit operation')


@app.get("/api/creation-tasks/{task_id}/assistant/edit-capabilities")
def get_creation_task_edit_capabilities(task_id: str, request: Request):
    task, _member_id = _exact_edit_access(task_id, request)
    payload = production_repo.get_creation_task_montage_plan(task.workspace_id, task.id) if task.montage_plan_id else None
    clips = payload.get("clips", []) if payload else []
    tracks = production_repo.list_subtitle_tracks(task.workspace_id, task.id, timeline_version_id=task.current_timeline_version_id) if task.current_timeline_version_id else []
    version = production_repo.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    operations = []
    for operation, fields in EXACT_EDIT_FIELDS.items():
        reason = None
        if payload is None:
            reason = "当前任务没有故事板"
        elif version is None:
            reason = "先保留当前故事板并建立可撤销版本"
        elif "clip_id" in fields and not clips:
            reason = "当前任务没有可编辑镜头"
        elif operation == "AdjustVisualEffects" and not clips:
            reason = "当前任务没有可调整画面的镜头"
        elif operation == "EditAudioTimeline" and not any(track.clips for track in
                _effective_audio_timeline(creation_task_montage_plan_from_payload(payload)).tracks):
            reason = "当前任务没有可修改的真实音轨片段"
        elif operation in {"UpdateSubtitleCue", "ReplaceSubtitleText", "SplitSubtitleCue", "MergeSubtitleCue", "DeleteSubtitleCue", "ConfigureSubtitleTrack"} and not tracks:
            reason = "当前版本没有字幕轨"
        operations.append({"operation_type": operation, "available": reason is None, "reason": reason,
            "parameter_fields": sorted(fields), "required_fields": sorted(EXACT_MONTAGE_REQUIRED_FIELDS.get(operation, set()) | ({"clip_id"} if "clip_id" in fields else set())),
            "requires_target_validation": True})
    return {"task_id": task.id, "timeline_version_id": task.current_timeline_version_id,
        "timeline_sequence": version.sequence if version else 0,
        "schema": CreationTaskExactEditRequest.model_json_schema(),
        "supported_operations": list(EXACT_EDIT_FIELDS), "operations": operations,
        "direct_apply": {
            "endpoint": f"/api/creation-tasks/{quote(task.id, safe='')}/edits/exact",
            "operations": [
                {**item, "parameter_fields": (
                    ["cue_id", "text"] if item["operation_type"] == "UpdateSubtitleCue" else
                    ["subtitle_display"] if item["operation_type"] == "ConfigureSubtitleTrack" else
                    item["parameter_fields"]),
                 "required_fields": (
                    ["cue_id", "text"] if item["operation_type"] == "UpdateSubtitleCue" else
                    ["subtitle_display"] if item["operation_type"] == "ConfigureSubtitleTrack" else
                    item["required_fields"])}
                for item in operations if item["operation_type"] != "GenerateCoverFromFrame"
            ],
        },
        "units": {"source_start_seconds": "源片段起点，秒；TrimClip 保留镜头时长与旁白",
                  "start_seconds": "时间轴秒", "end_seconds": "时间轴秒", "split_time_seconds": "时间轴秒",
                  "playback_rate": "0.25–4，语义按operation区分。ChangeClipSpeed：绝对画面速度，每1秒timeline消耗rate秒source；source_span=timeline_span*rate。0.7减速、2加速；2.5秒槽@0.7消耗2.5*0.7=1.75秒源片段，保留时间槽、旁白和字幕。ChangeLinkedSpeed：相对当前速度倍率，联动改变时轴，不是绝对速度。",
                  "crop_x": "0–1，裁剪窗口水平位置", "crop_y": "0–1，裁剪窗口垂直位置",
            "audio_edit.action": "update修改音频片段；track修改音轨。目标必须是当前版本的真实音频ID，不使用视频clip ID。",
            "audio_edit.target_id": "当前音轨或音频片段ID；与action对应。",
            "audio_edit.values.gain_db": "-60至12 dB，绝对目标音量；轨道与片段音量叠加。减6dB须读取原值后计算目标。",
            "audio_edit.values.muted": "布尔值；true静音，false恢复。",
            "audio_edit.values.fade_in_ms": "音频片段淡入毫秒，非负整数；淡入+淡出不能超过片段包络长度。",
            "audio_edit.values.fade_out_ms": "音频片段淡出毫秒，非负整数；不是时间线结束位置。",
            "audio_edit.values.denoise_db": "0至24 dB，片段降噪强度，0关闭。",
            "audio_edit.values.loudness_target_lufs": "-30至-10 LUFS，片段响度目标；显式null清除。",
            "audio_edit.values.duck_under_voice": "仅music轨布尔值；按当前真实旁白压低背景音乐。",
            "effects.exposure": "-2至2 EV，绝对曝光设置。", "effects.contrast": "0.5至1.5，1为默认。",
            "effects.saturation": "0至2，1为默认。", "effects.temperature": "-1至1，负值冷、正值暖。",
            "effects.zoom": "0.1至4倍，1为默认。", "effects.offset_x": "-1至1，归一化水平位置；正值右移。",
            "effects.offset_y": "-1至1，归一化垂直位置；正值下移。", "effects.rotation": "-180至180度，绝对角度。",
            "effects.fade_in_seconds": "0至5秒黑场淡入；非零至少两输出帧，与淡出总长不超过镜头。",
            "effects.fade_out_seconds": "0至5秒黑场淡出；独立于音频淡出。",
            "effects.beauty_strength": "0至1；非零本机人像效果尚无受许可执行路径。",
            "effects.skin_exposure": "-0.5至0.5 EV；非零本机人像效果尚无受许可执行路径。",
            "patch": "只修改明确提交字段，遗漏保持各对象现值；重置须明确提交默认值。不接音频关键点、独立变速、solo持久化或来源替换。"},
        "preview_modes": {"draft": "只生成参数差异，不生成媒体", "media": "需现有设备许可和标定预算，返回实际预览或异步作业"}}


@app.post('/api/creation-tasks/{task_id}/edits/exact/preview',response_model=CreationTaskEditPreviewResponse)
def preview_creation_task_exact_edit(task_id:str,request:Request,payload:CreationTaskExactEditRequest):
    original=payload
    edit_request_event=None
    if original.request_id is not None:
        task,member_id=_exact_edit_access(task_id,request,write=True)
        edit_request_event,fresh=_claim_edit_request(task,member_id,original.request_id,'preview',original.model_dump(mode='json',exclude_unset=True))
        if not fresh:
            result=_read_edit_request_result(task,edit_request_event)
            if result is not None:return result
            raise HTTPException(status_code=409,detail='原预览请求结果尚未确定，请查询原请求，不重复执行')
    try:
        payload,source=_prepare_exact_edit_request(task_id,request,original)
    except HTTPException as exc:
        if edit_request_event:_save_edit_request_result(task,edit_request_event,JSONResponse(status_code=exc.status_code,content={'detail':exc.detail}))
        raise
    try:
        result=_try_exact_preview(task_id,request,payload,source=source,execution=None,operation='exact_edit_preview')
        if edit_request_event:_save_edit_request_result(task,edit_request_event,result)
        return result
    except HTTPException as exc:
        if edit_request_event:_save_edit_request_result(task,edit_request_event,JSONResponse(status_code=exc.status_code,content={'detail':exc.detail}))
        raise
    except TaskMediaRequired as required:
        media_kind=required.kind
    receipt=_queue_exact_preview(task_id,request,payload,source=source,media_kind=media_kind)
    if edit_request_event:_save_edit_request_result(task,edit_request_event,JSONResponse(status_code=202,content=receipt))
    receipt['result']=None
    return JSONResponse(status_code=202,content=receipt)


def _preview_exact_edit_request(task_id,request,payload):
    return _try_exact_preview(task_id,request,payload,source=EXACT_EDIT_SOURCE.get(),
        execution=None,operation='exact_edit_preview')


@app.post(
    "/api/creation-tasks/{task_id}/edits/exact",
    response_model=CreationTaskEditConfirmResponse | CreationTaskEditQueuedResponse,
)
def apply_creation_task_exact_edit(
    task_id: str,
    request: Request,
    payload: CreationTaskExactEditRequest,
) -> CreationTaskEditConfirmResponse:
    if payload.assistant_turn_id is not None and payload.request_id is None:
        raise HTTPException(status_code=422, detail="助手直接编辑需要原请求身份")
    if (payload.operation_type in PICTURE_METADATA_OPERATIONS | CLIP_METADATA_OPERATIONS or (payload.operation_type == 'EditAudioTimeline' and payload.audio_edit.action in {'update', 'track'}) or (payload.operation_type == 'ConfigureSubtitleTrack' and payload.subtitle_display is not None)) or (payload.operation_type == 'UpdateSubtitleCue' and payload.track_id is None):
        return _save_direct_metadata_edit(task_id, request, payload)
    # The same exact-edit endpoint binds deterministic edits to their original
    # assistant request. Domain use cases still own all timeline mutations.
    if payload.request_id is None and payload.assistant_turn_id is None:
        return _apply_exact_edit_body(task_id, request, payload, source=EXACT_EDIT_SOURCE.get())
    task, member_id = _exact_edit_access(task_id, request, write=True)
    case = _metadata_edit_use_case()
    event_id = None
    try:
        event_id, replay = case.begin(task, member_id, payload.request_id,
            payload.model_dump(mode="json", exclude_unset=True), phase="apply")
        if replay is not None:
            result = _metadata_edit_response(replay)
            if replay.recorded_projection is None:
                _save_edit_request_result(task, event_id, result)
            return result
        clean, source = _prepare_exact_edit_request(task_id, request, payload)
        fields = clean.model_dump(mode="json", exclude_unset=True)
        if set(fields) - {"operation_type"} - EXACT_EDIT_FIELDS.get(clean.operation_type, set()):
            raise HTTPException(status_code=422, detail="精确编辑不能混入其他操作字段")
        result = _apply_exact_edit_body(task_id, request, clean, source=source)
        if event_id is not None:
            _save_edit_request_result(task, event_id, result)
        return result
    except HTTPException as exc:
        if event_id is not None:
            case.record_result(task, event_id, status_code=exc.status_code, response={"detail": exc.detail})
        raise
    except (EditConflict, ValueError, KeyError, OSError) as exc:
        failure = HTTPException(status_code=409, detail=str(exc))
        if event_id is not None:
            case.record_result(task, event_id, status_code=409, response={"detail": failure.detail})
        raise failure from exc


def _apply_exact_edit_body(task_id, request, payload, *, source):
    if payload.operation_type in LINKED_EDIT_OPERATIONS:
        task = _creation_task_or_404(current_workspace_id(request), task_id)
        command = _linked_timeline_command(task, request, payload.operation_type,
            {"base_version_id": task.current_timeline_version_id, **_linked_timeline_values(payload)}, source=source)
        try:
            return _metadata_edit_response(_linked_timeline_use_case().apply(command))
        except LinkedEditInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except InsertMaterialMissing as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except InsertSourceRangeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (EditConflict, KeyError, TimelineVersionConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LinkedEditPreviewFailed as exc:
            raise HTTPException(status_code=422, detail=str(exc.original) if isinstance(exc.original, ValueError) else "关联变速预览失败，请检查本地素材和FFmpeg") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_subtitle_edit(payload.operation_type, payload.model_dump(exclude_unset=True)):
        values = _subtitle_values(payload)
        task = _creation_task_or_404(current_workspace_id(request), task_id)
        command = _metadata_edit_command(task, request, payload.operation_type,
            {"base_version_id": task.current_timeline_version_id, **values}, source=source)
        try:
            return _metadata_edit_response(_subtitle_edit_use_case().apply(command))
        except SubtitleNoChange as exc:
            raise _topic_failure_response(**exc.details) from exc
        except SubtitleTargetMissing as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SubtitlePreviewFailed as exc:
            raise HTTPException(status_code=422, detail=str(exc.original)) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if payload.operation_type in {"ChangePersonSegment", "RegeneratePersonRender"}:
        if not payload.clip_id:
            raise HTTPException(status_code=422, detail="person edit requires clip_id")
        preview = _preview_command_edit_request(
            task_id,
            request,
            CreationTaskEditPreviewRequest(
                raw_command=(
                    "这里只重做口型，其他都不要动"
                    if payload.operation_type == "RegeneratePersonRender"
                    else "这一段换成当前人物出镜"
                ),
                context_stage="review" if task.stage == "review" else "montage",
                selected_object_refs=[f"clip:{payload.clip_id}"],
            ),
        )
        return _confirm_creation_task_edit(task_id, preview.proposal.id, request)
    if payload.operation_type == "RegenerateNarrationSegment":
        if not payload.narration_segment_id:
            raise HTTPException(status_code=422, detail="narration edit requires narration_segment_id")
        preview = _preview_command_edit_request(
            task_id,
            request,
            CreationTaskEditPreviewRequest(
                raw_command=payload.delivery_instruction or "这句重新配音",
                context_stage="narration",
                selected_object_refs=[f"narration:{payload.narration_segment_id}"],
            ),
        )
        return _confirm_creation_task_edit(task_id, preview.proposal.id, request)
    preview = _try_exact_preview(task_id, request, payload, source=source, execution=None, operation="exact_edit_apply")
    return _confirm_creation_task_edit(task_id, preview.proposal.id, request)



def _validate_montage_material_use(workspace_id, plan):
    return _picture_preview_renderer().validate_material_use(workspace_id, plan)


def _render_task_picture_input(workspace_id, task_id, media_input, output, context):
    return _picture_preview_renderer().render_picture_input(workspace_id, task_id, media_input, output, context)




@app.post("/api/creation-tasks/{task_id}/edits/{proposal_id}/selection/preview")
def preview_compound_selection(task_id:str,proposal_id:str,request:Request,payload:CompoundEditSelectionRequest):
    task = _creation_task_or_404(current_workspace_id(request), task_id)
    actor = _metadata_edit_command(task, request, "EditCompoundTimeline", {"base_version_id": task.current_timeline_version_id}).actor_identity
    control = CompoundSelectionCommand(task.workspace_id, task.id, proposal_id, actor,
        payload.included_step_indices, payload.raw_command)
    draft_only = request.query_params.get("draft") == "true" or request.url.path.endswith("/edits/exact")
    try:
        outcome = _compound_edit_use_case().preview_selection(control, draft_only=draft_only, execution=None)
        return _edit_preview_response(*outcome) if outcome is not None else None
    except PicturePreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except TaskMediaRequired as required:
        media_kind = required.kind
    workspace_id = current_workspace_id(request)
    actor = _require_permit_actor(request, workspace_id)
    task = _creation_task_or_404(workspace_id, task_id)
    if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
        raise HTTPException(status_code=403, detail='当前成员不能安排此任务的媒体处理')
    values = {'_media_kind': media_kind, '_draft': draft_only, 'proposal_id': proposal_id,
        'payload': payload.model_dump(mode='json', exclude_unset=True)}
    try:
        runtime = _creation_runtime()
        media = runtime.registrations['creation_job'].owner.task_media
        if task.montage_plan_id and task.current_timeline_version_id is None:
            media.facts(workspace_id, task_id, 'compound_selection_preview', values, actor)
            if media.verify_identity(actor) != 'ready':
                raise PermissionError('媒体处理身份失效或无法复核')
            montage = creation_task_montage_plan_from_payload(production_repo.get_creation_task_montage_plan(workspace_id, task_id))
            _ensure_montage_base_version(workspace_id, task, montage)
        job = media.submit(runtime, actor, task_id, 'compound_selection_preview', values, 'edit_preview')
        receipt = _task_media_job_response(media, job, actor, runtime)
        receipt['result'] = None
        return JSONResponse(status_code=202, content=receipt)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _compound_edit_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    _audio, metadata, renderer = _picture_preview_components(repo)
    return CompoundEditUseCase(repo, metadata, picture_renderer=renderer)


def _compound_proposal_command(task, proposal, request):
    source = production_repo.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source")
    return _metadata_edit_command(task, request, "EditCompoundTimeline", proposal.operation["payload"], source=source)


def _confirm_compound_response(task, proposal, request):
    try:
        outcome = _compound_edit_use_case().confirm(_compound_proposal_command(task, proposal, request), proposal.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _metadata_edit_response(outcome)







def _graphic_overlay_candidate(plan,payload):
    return _metadata_edit_use_case().graphic_overlay_candidate(plan,payload)

def _preview_graphic_overlay(task_id, request, payload):
    workspace_id = current_workspace_id(request)
    reader, diagnostic = _content_reader(request, workspace_id)
    identity = {'member_id':reader, 'local_diagnostic':diagnostic}
    try:
        outcome = _metadata_edit_use_case().preview_graphic_overlay(workspace_id, task_id, payload,
            identity=identity, source=EXACT_EDIT_SOURCE.get(), execution=None,
            draft_only=request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'))
        return _edit_preview_response(*outcome)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _preview_transition(task_id, request, payload):
    workspace_id = current_workspace_id(request)
    reader, diagnostic = _content_reader(request, workspace_id)
    identity = {'member_id':reader, 'local_diagnostic':diagnostic}
    try:
        outcome = _metadata_edit_use_case().preview_transition(workspace_id, task_id, payload,
            identity=identity, source=EXACT_EDIT_SOURCE.get(), execution=None,
            draft_only=request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'))
        return _edit_preview_response(*outcome)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _preview_canvas(task_id, request, payload):
    workspace_id = current_workspace_id(request)
    reader, diagnostic = _content_reader(request, workspace_id)
    identity = {'member_id':reader, 'local_diagnostic':diagnostic}
    try:
        outcome = _metadata_edit_use_case().preview_canvas(workspace_id, task_id, payload,
            identity=identity, source=EXACT_EDIT_SOURCE.get(), execution=None,
            draft_only=request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'))
        return _edit_preview_response(*outcome)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc





def _subtitle_edit_use_case():
    return SubtitleEditUseCase(production_repo, _metadata_edit_use_case())


def _subtitle_values(payload):
    try:return subtitle_values(payload)
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _subtitle_proposal_command(task, proposal, request):
    source = production_repo.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source")
    return _metadata_edit_command(task, request, proposal.operation["operation_type"], proposal.operation["payload"], source=source)


def _confirm_subtitle_response(task, proposal, request):
    try:
        outcome = _subtitle_edit_use_case().confirm(_subtitle_proposal_command(task, proposal, request), proposal.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _metadata_edit_response(outcome)


def _preview_subtitle_edit(task_id, request, payload):
    workspace_id = current_workspace_id(request)
    reader, diagnostic = _content_reader(request, workspace_id)
    source = EXACT_EDIT_SOURCE.get()
    if source is not None:
        reader, diagnostic = _assistant_member_id(request, workspace_id), False
    try:
        return _edit_preview_response(*_subtitle_edit_use_case().preview(workspace_id, task_id, payload,
            identity={'member_id':reader,'local_diagnostic':diagnostic}, source=source,
            execution=None,
            draft_only=request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact')))
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _preview_subtitle_style(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    return _preview_subtitle_edit(task_id, request, payload)


def _preview_subtitle_structure(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    return _preview_subtitle_edit(task_id, request, payload)


def _preview_subtitle_replace(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    return _preview_subtitle_edit(task_id, request, payload)


def _preview_subtitle_cue(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    return _preview_subtitle_edit(task_id, request, payload)


def _preview_linked_timeline(task_id,request,payload):
    return _preview_linked_core(task_id,request,payload,source=EXACT_EDIT_SOURCE.get())


def _preview_linked_core(task_id,request,payload,*,source):
    case=_exact_preview_use_case()
    try:
        task,values=case.prepare_linked(current_workspace_id(request),task_id,payload)
        command=_linked_timeline_command(task,request,payload.operation_type,
            {'base_version_id':task.current_timeline_version_id,**values},source=source)
        return _edit_preview_response(*case.preview_linked(command,payload))
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc





def _preview_linked_clip_edit(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    return _preview_linked_timeline(task_id, request, payload)


def _preview_insert(task_id: str, request: Request, payload: CreationTaskExactEditRequest):
    return _preview_linked_timeline(task_id, request, payload)


def _trim_clip_source_url(workspace_id,task_id,clip,cue):
    return _metadata_edit_use_case().trim_clip_source_url(workspace_id,task_id,clip,cue)


def _preview_exact_montage_edit(task_id, request, payload):
    workspace_id = current_workspace_id(request)
    reader, diagnostic = _content_reader(request, workspace_id)
    identity = {'member_id':reader, 'local_diagnostic':diagnostic}
    try:
        outcome = _metadata_edit_use_case().preview_exact_montage(workspace_id, task_id, payload,
            identity=identity, source=EXACT_EDIT_SOURCE.get(), execution=None,
            draft_only=request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'))
        return outcome
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

@app.post(
    "/api/creation-tasks/{task_id}/timeline/undo",
    response_model=CreationTaskEditConfirmResponse,
)
def undo_creation_task_timeline(
    task_id: str,
    request: Request,
    _payload: CreationTaskTimelineUndoRequest,
) -> CreationTaskEditConfirmResponse:
    return _restore_creation_task_timeline(task_id, request, redo=False, control=_payload)


@app.post("/api/creation-tasks/{task_id}/timeline/redo", response_model=CreationTaskEditConfirmResponse)
def redo_creation_task_timeline(task_id: str, request: Request, _payload: CreationTaskTimelineUndoRequest) -> CreationTaskEditConfirmResponse:
    return _restore_creation_task_timeline(task_id, request, redo=True, control=_payload)


def _restore_creation_task_timeline(task_id: str, request: Request, *, redo: bool, control: CreationTaskTimelineUndoRequest | None = None) -> CreationTaskEditConfirmResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if control is not None and control.assistant_turn_id is not None:
        _assistant_edit_control_instruction(task, request, control, "redo" if redo else "undo")
    elif control is not None and control.model_fields_set & {"expected_timeline_version_id", "expected_timeline_sequence"}:
        if not {"expected_timeline_version_id", "expected_timeline_sequence"} <= control.model_fields_set or control.expected_timeline_sequence is None:
            raise HTTPException(status_code=422, detail="历史恢复需要同时提交期望版本与序号")
        task, _member_id = _exact_edit_access(task_id, request, write=True)
        version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if (control.expected_timeline_version_id, control.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise HTTPException(status_code=409, detail="历史恢复所依据的时间线已变化，请重新读取当前版本")
    case = _timeline_restore_use_case()
    try:
        prepared = case.prepare_restore(TimelineRestoreCommand(workspace_id, task, redo))
        restore_output = _creation_render_is_valid(
            workspace_id, task_id, verify_media=False, timeline_version=prepared.target_version,
        )
        outcome = case.apply_restore(prepared, TimelineOutputQualification(prepared, restore_output))
    except (TimelineRestoreUnavailable, LegacyMontageReference) as exc:
        raise _topic_failure_response(**exc.details) from exc
    except InvalidMontage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _metadata_edit_response(outcome)


def _timeline_restore_use_case():
    return TimelineRestoreUseCase(production_repo, _metadata_edit_use_case())


def _apply_creation_task_montage_clip_update(workspace_id,task_id,plan,clip_id,payload,*,request):
    reader,diagnostic=_content_reader(request,workspace_id)
    try:return _metadata_edit_use_case().apply_clip_update(workspace_id,task_id,plan,clip_id,payload,
        reader_identity={'member_id':reader,'local_diagnostic':diagnostic})
    except EditPreviewRejected as exc:raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


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
    result = _save_direct_metadata_edit(
        task_id,
        request,
        CreationTaskExactEditRequest(
            operation_type=categories[0],
            clip_id=clip_id,
            **{key: value for key, value in payload.model_dump(exclude_unset=True).items()
               if key in EXACT_EDIT_FIELDS[categories[0]]},
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
    result = _save_direct_metadata_edit(
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
    raise HTTPException(status_code=409, detail="请使用开始/继续制作，成片必须进入设备队列")








def _render_creation_task_montage_for_workspace(
    workspace_id: str, task_id: str, *, render_context: CpuRenderGrantContext | None = None,
) -> CreationTaskMontagePlanResponse:
    try:
        plan = _render_stage_use_case().render(workspace_id, task_id, render_context=render_context)
    except RenderRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _quality_inspection_use_case().start_for_render(device_runtime, workspace_id, task_id,
        render_context.grant.owner_id if render_context is not None else None)
    return _creation_task_montage_response(plan)


def _creation_task_output_path(workspace_id: str, task_id: str, *, subtitle: bool) -> Path:
    try:
        return _output_observation_use_case().output_path(workspace_id, task_id, subtitle=subtitle)
    except OutputObservationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _platform_publish_task_for_user(workspace_id: str, task_id: str, user_id: str) -> CreationTask:
    return PublishOutputQualificationUseCase(production_repo).task_for_user(workspace_id, task_id, user_id)


def _platform_publish_output_for(workspace_id: str, task_id: str, user_id: str) -> dict[str, object]:
    """Resolve only the current, authorized and already-validated task output."""
    case = PublishOutputQualificationUseCase(production_repo)
    task, version = case.source_version(workspace_id, task_id, user_id)
    render_valid = _creation_render_is_valid(
        workspace_id, task_id, verify_media=False, timeline_version=version,
    )
    case.require_render_valid(render_valid)
    ref = case.output_reference(version)
    path = _creation_task_output_path(workspace_id, task_id, subtitle=False)
    return case.qualified_output(workspace_id, task_id, task, version, ref, path)


def _quality_check_task_access(request: Request, workspace_id: str, task_id: str) -> CreationTask:
    user_id, _ = _content_reader(request, workspace_id)
    if user_id is None:
        raise HTTPException(status_code=401, detail="请使用可信成员身份检查成片")
    if (_local_member_token(request) and workspace_id != LEGACY_WORKSPACE_ID
            and getattr(request.state, "principal", None) is None):
        raise HTTPException(status_code=403, detail="本机成员与工作区不匹配")
    task = _creation_task_or_404(workspace_id, task_id)
    if not _content_visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="creation task not found")
    return task


def _quality_check_source(workspace_id: str, task_id: str):
    try:
        return _output_observation_use_case().quality_source_metadata(workspace_id, task_id)
    except OutputObservationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _quality_inspection_use_case(repository=None):
    budget, error = None, None
    try:
        budget = quality_budget_from_env()
    except HostBudgetUncalibrated as exc:
        error = str(exc)
    repo = production_repo if repository is None else repository
    return QualityInspectionUseCase(repo, quality_check_repo, _output_observation_use_case(repo),
        budget=budget, budget_error=error, verify_identity=_verify_creation_identity,
        temporary_root=settings.effective_temporary_root_path / "quality-checks")


def _creation_task_quality_check_response(workspace_id: str, task_id: str) -> CreationTaskQualityCheckResponse:
    _creation_task_or_404(workspace_id, task_id)
    return CreationTaskQualityCheckResponse.model_validate(_quality_inspection_use_case().projection(workspace_id, task_id))


def _start_creation_task_quality_check(workspace_id: str, task_id: str, *,
                                     actor: CreationExecutionActor, force: bool = False) -> None:
    _quality_inspection_use_case().start(device_runtime, actor, workspace_id, task_id, force=force)


@app.get("/api/creation-tasks/{task_id}/quality-check", response_model=CreationTaskQualityCheckResponse)
def get_creation_task_quality_check(task_id: str, request: Request) -> CreationTaskQualityCheckResponse:
    workspace_id = current_workspace_id(request)
    _quality_check_task_access(request, workspace_id, task_id)
    return _creation_task_quality_check_response(workspace_id, task_id)


def recover_interrupted_quality_checks(runtime) -> None:
    _quality_inspection_use_case().recover(runtime.scheduler if runtime is not None else None)


@app.post("/api/creation-tasks/{task_id}/quality-check", response_model=CreationTaskQualityCheckResponse)
def check_creation_task_quality(task_id: str, request: Request,
                                _payload: CreationTaskQualityCheckRequest) -> CreationTaskQualityCheckResponse:
    workspace_id = current_workspace_id(request)
    _quality_check_task_access(request, workspace_id, task_id)
    _start_creation_task_quality_check(workspace_id, task_id,
        actor=_require_permit_actor(request, workspace_id), force=True)
    return _creation_task_quality_check_response(workspace_id, task_id)


@app.get("/api/creation-tasks/{task_id}/output")
def get_creation_task_output(task_id: str, request: Request, expected_version_id: str | None = None) -> FileResponse:
    _readable_creation_task(request, task_id)
    return _creation_task_download_response(task_id, request, expected_version_id, subtitle=False)


@app.get("/api/creation-tasks/{task_id}/output/subtitles")
def get_creation_task_subtitles(task_id: str, request: Request, expected_version_id: str | None = None) -> FileResponse:
    _readable_creation_task(request, task_id)
    return _creation_task_download_response(task_id, request, expected_version_id, subtitle=True)


def _creation_task_download_response(task_id: str, request: Request, expected_version_id: str | None, *, subtitle: bool) -> FileResponse:
    try:
        result = _output_observation_use_case().download(current_workspace_id(request), task_id,
            expected_version_id=expected_version_id, subtitle=subtitle)
    except OutputObservationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    headers = {"Cache-Control": "no-store"}
    if result.version_id is not None:
        headers.update({"X-Timeline-Version-Id": result.version_id, "X-Timeline-Sequence": str(result.sequence)})
    return FileResponse(result.path, media_type="application/x-subrip" if subtitle else "video/mp4",
        filename=f"{task_id}.srt" if subtitle else f"{task_id}.mp4", headers=headers)


def _timeline_version_or_404(
    workspace_id: str, task_id: str, version_id: str
) -> TimelineVersion:
    """Read one historical version only when it belongs to the requested task.

    ``get_timeline_version`` is workspace-scoped, so this task check is what
    keeps another task's version from being read through this route.
    """
    try:
        version = production_repo.get_timeline_version(workspace_id, version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="时间线版本不存在。") from exc
    if version.task_id != task_id:
        raise HTTPException(status_code=404, detail="时间线版本不存在。")
    return version


def _history_montage(version: TimelineVersion) -> dict[str, object]:
    montage = version.snapshot.get("montage")
    return montage if isinstance(montage, dict) else {}


def _history_subtitle_tracks(workspace_id: str, task_id: str, version: TimelineVersion):
    return production_repo.list_subtitle_tracks(
        workspace_id, task_id, timeline_version_id=version.id
    )


def _history_summary(
    task: CreationTask,
    version: TimelineVersion,
    workspace_id: str,
) -> TimelineVersionSummaryResponse:
    montage = _history_montage(version)
    clips = montage.get("clips")
    segments = version.snapshot.get("narration_segments")
    return TimelineVersionSummaryResponse(
        id=version.id,
        sequence=version.sequence,
        parent_version_id=version.parent_version_id,
        created_at=version.created_at,
        is_current=task.current_timeline_version_id == version.id,
        shot_count=len(clips) if isinstance(clips, list) else 0,
        subtitle_track_count=len(_history_subtitle_tracks(workspace_id, task.id, version)),
        narration_segment_count=len(segments) if isinstance(segments, list) else 0,
    )


def _history_artifact_refs(
    version: TimelineVersion,
    workspace_id: str,
    task_id: str,
) -> dict[str, str | None]:
    """Collect this version's own artifact refs, never the task's current output."""
    montage = _history_montage(version)
    tracks = _history_subtitle_tracks(workspace_id, task_id, version)
    subtitle_ref = next(
        (
            track.artifact_ref
            for track in tracks
            if track.status == "valid" and track.artifact_ref
        ),
        None,
    ) or montage.get("subtitle_path")
    cover_ref = None
    cover = _cover_asset_for_version(workspace_id, task_id, version)
    if cover is not None and cover.status == "valid":
        cover_ref = cover.artifact_ref
    return {
        "video": montage.get("output_path") if isinstance(montage.get("output_path"), str) else None,
        "subtitles": subtitle_ref if isinstance(subtitle_ref, str) else None,
        "cover": cover_ref,
    }


def _history_artifact_state(
    logical_ref: str | None,
) -> Literal["available", "not_generated", "missing"]:
    if not logical_ref:
        return "not_generated"
    try:
        artifact_store.resolve_file(logical_ref)
    except (FileNotFoundError, ValueError):
        return "missing"
    return "available"


_HISTORY_ARTIFACT_MEDIA_TYPES = {
    "video": "video/mp4",
    "subtitles": "application/x-subrip",
}

_HISTORY_ARTIFACT_NOT_GENERATED = {
    "video": "该版本没有记录成片；这一版还没渲染过视频。",
    "subtitles": "该版本没有记录字幕文件；这一版没有生成字幕产物。",
    "cover": "该版本没有封面；这一版没有生成封面。",
}

_HISTORY_ARTIFACT_MISSING = {
    "video": "该版本记录的成片已不在受控产物目录内。",
    "subtitles": "该版本记录的字幕文件已不在受控产物目录内。",
    "cover": "该版本记录的封面已不在受控产物目录内。",
}


def _history_artifact_url(task_id: str, version: TimelineVersion, kind: str) -> str:
    return (
        f"/api/creation-tasks/{quote(task_id, safe='')}/timeline/versions/"
        f"{quote(version.id, safe='')}/artifacts/{kind}"
    )


def _history_artifacts(
    task_id: str,
    version: TimelineVersion,
    refs: dict[str, str | None],
) -> list[TimelineVersionArtifactResponse]:
    return [
        TimelineVersionArtifactResponse(
            kind=kind,
            status=status,
            url=_history_artifact_url(task_id, version, kind) if status == "available" else None,
            reason=(
                None
                if status == "available"
                else _HISTORY_ARTIFACT_NOT_GENERATED[kind]
                if status == "not_generated"
                else _HISTORY_ARTIFACT_MISSING[kind]
            ),
        )
        for kind, status in (
            (kind, _history_artifact_state(ref)) for kind, ref in refs.items()
        )
    ]


@app.get(
    "/api/creation-tasks/{task_id}/timeline/versions",
    response_model=TimelineVersionListResponse,
)
def list_creation_task_timeline_versions(
    task_id: str, request: Request
) -> TimelineVersionListResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    return TimelineVersionListResponse(
        task_id=task.id,
        current_version_id=task.current_timeline_version_id,
        items=[
            _history_summary(task, version, workspace_id)
            for version in production_repo.list_timeline_versions(workspace_id, task_id)
        ],
    )


@app.get(
    "/api/creation-tasks/{task_id}/timeline/versions/{version_id}",
    response_model=TimelineVersionDetailResponse,
)
def get_creation_task_timeline_version(
    task_id: str, version_id: str, request: Request
) -> TimelineVersionDetailResponse:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    version = _timeline_version_or_404(workspace_id, task_id, version_id)
    montage = _history_montage(version)
    clips = montage.get("clips")
    cue_list = montage.get("cues") if isinstance(montage.get("cues"), list) else []
    cues_by_id = {
        cue["id"]: cue for cue in cue_list if isinstance(cue, dict) and "id" in cue
    }
    shots = [
        TimelineVersionShotResponse(
            index=index + 1,
            id=str(clip.get("id", "")),
            narration_text=cues_by_id[clip["cue_id"]].get("text")
            if isinstance(cues_by_id.get(clip.get("cue_id")), dict)
            else None,
            start_seconds=float(clip.get("timeline_start_seconds") or 0.0),
            end_seconds=float(clip.get("timeline_end_seconds") or 0.0),
            description="；".join(str(reason) for reason in clip["match_reasons"])
            if isinstance(clip.get("match_reasons"), list) and clip["match_reasons"]
            else None,
        )
        for index, clip in enumerate(clips if isinstance(clips, list) else [])
        if isinstance(clip, dict)
    ]
    segments = version.snapshot.get("narration_segments")
    narration_segments = [
        TimelineVersionNarrationSegmentResponse(
            index=index + 1,
            id=str(segment.get("id", "")),
            text=str(segment.get("text", "")),
            duration_seconds=segment.get("duration_seconds")
            if isinstance(segment.get("duration_seconds"), (int, float))
            else None,
            status=segment.get("status") if isinstance(segment.get("status"), str) else None,
        )
        for index, segment in enumerate(segments if isinstance(segments, list) else [])
        if isinstance(segment, dict)
    ]
    tracks = _history_subtitle_tracks(workspace_id, task_id, version)
    cover = _cover_asset_for_version(workspace_id, task_id, version)
    return TimelineVersionDetailResponse(
        task_id=task.id,
        version=_history_summary(task, version, workspace_id),
        shots=shots,
        narration_segments=narration_segments,
        subtitle_tracks=[
            SubtitleTrackResponse.model_validate(asdict(track)) for track in tracks
        ],
        cover_asset=CoverAssetResponse.model_validate(asdict(cover)) if cover is not None else None,
        artifacts=_history_artifacts(
            task_id,
            version,
            _history_artifact_refs(version, workspace_id, task_id),
        ),
    )


@app.get("/api/creation-tasks/{task_id}/timeline/versions/{version_id}/artifacts/{kind}")
def get_creation_task_timeline_version_artifact(
    task_id: str,
    version_id: str,
    kind: Literal["video", "subtitles", "cover"],
    request: Request,
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    _readable_creation_task(request, task_id)
    version = _timeline_version_or_404(workspace_id, task_id, version_id)
    logical_ref = _history_artifact_refs(version, workspace_id, task_id)[kind]
    if logical_ref is None:
        raise HTTPException(status_code=404, detail=_HISTORY_ARTIFACT_NOT_GENERATED[kind])
    if _history_artifact_state(logical_ref) == "missing":
        raise HTTPException(status_code=404, detail=_HISTORY_ARTIFACT_MISSING[kind])
    path = artifact_store.resolve_file(logical_ref)
    if kind == "cover":
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    else:
        media_type = _HISTORY_ARTIFACT_MEDIA_TYPES[kind]
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/creation-tasks/{task_id}/output/selected-subtitles")
def get_creation_task_selected_subtitles(
    task_id: str,
    request: Request,
    expected_version_id: str,
    mode: Literal["zh", "en", "zh-en"],
    zh_track_id: str | None = None,
    en_track_id: str | None = None,
) -> Response:
    workspace_id = current_workspace_id(request)
    task = _readable_creation_task(request, task_id)
    if not task.current_timeline_version_id or task.current_timeline_version_id != expected_version_id:
        raise HTTPException(status_code=409, detail="当前字幕版本已变化，请重新选择后保存。")
    required = {"zh": ("zh-CN",), "en": ("en-US",), "zh-en": ("zh-CN", "en-US")}[mode]
    requested = {"zh-CN": zh_track_id, "en-US": en_track_id}
    if any(not requested[language] for language in required):
        raise HTTPException(status_code=422, detail="请选择要保存的字幕轨。")
    if any(requested[language] for language in {"zh-CN", "en-US"} - set(required)):
        raise HTTPException(status_code=422, detail="字幕语言与所选保存方式不一致。")
    tracks = _current_subtitle_tracks(workspace_id, task)
    selected = {}
    for language in required:
        track = next((item for item in tracks if item.id == requested[language]), None)
        if track is None or track.language != language or track.task_id != task_id or track.timeline_version_id != expected_version_id:
            raise HTTPException(status_code=409, detail="所选字幕轨已变化，请重新选择后保存。")
        if track.status != "valid" or not track.cues:
            raise HTTPException(status_code=409, detail="所选字幕轨目前不可保存，请先检查或重新生成字幕。")
        selected[language] = _execution_subtitle_track(track)
    try:
        if mode == "zh-en":
            chinese = selected["zh-CN"]
            if selected["en-US"].source_language != "zh-CN":
                raise ValueError("英文字幕与当前中文字幕没有来源关联")
            chinese = replace(chinese, style=replace(chinese.style, bilingual_order="source_then_translation"))
            content = render_bilingual_srt(chinese, selected["en-US"])
        else:
            content = render_srt(selected[required[0]])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"字幕内容或时间无法保存：{exc}") from exc
    if not content.strip():
        raise HTTPException(status_code=409, detail="当前字幕文件为空，请检查字幕后重试。")
    return Response(content=content, media_type="application/x-subrip; charset=utf-8")


@app.get("/api/creation-tasks/{task_id}/covers/{cover_id}/image")
def get_creation_task_cover_image(
    task_id: str, cover_id: str, request: Request
) -> FileResponse:
    workspace_id = current_workspace_id(request)
    _readable_creation_task(request, task_id)
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
    "/api/production/home",
    response_model=HomeAggregationResponse,
)
def get_production_home(
    request: Request,
    days: int = 7,
) -> HomeAggregationResponse:
    """首页一次取齐：当前身份可见的统计、真实日趋势、可进入的队列/待办/成果、
    整机匿名接收数量和真实存储事实。

    身份解析完全沿用既有读过滤（`_content_reader` 与 `_content_include_private`），
    聚合模块不另写可见性判据。`days` 只影响日趋势跨度，不改变其他统计口径。
    """
    if days not in {7, 30}:
        raise HTTPException(status_code=422, detail="首页统计只支持 days=7 或 days=30")
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    return aggregate_home(
        workspace_id=workspace_id,
        repository=production_repo,
        source_repository=source_repository,
        days=days,
        viewer_user_id=viewer_user_id,
        local_diagnostic=local_diagnostic,
        include_private=_content_include_private(request),
    )


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
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    return MaterialLibraryInsightsResponse.model_validate(
        production_repo.library_insights(
            workspace_id,
            viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
        )
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
    tag: str | None = None,
    review_status: str | None = None,
    media_type: str | None = None,
) -> MaterialLibraryResponse:
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="invalid material pagination")
    if review_status not in {None, "raw", "approved", "rejected"}:
        raise HTTPException(status_code=422, detail="invalid material review status")
    if media_type not in {None, "video", "audio", "image"}:
        raise HTTPException(status_code=422, detail="invalid material media type")
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    assets, total = production_repo.list_assets_page(
        workspace_id,
        limit=limit,
        offset=offset,
        query=q,
        tag=tag,
        review_status=review_status,
        media_type=media_type,
        include_private=local_diagnostic,
        viewer_user_id=viewer_user_id,
        local_diagnostic=local_diagnostic,
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
            "ready": (("素材目录可访问；确认复制完成后才接收并分析。" if bound else
                       "素材目录可访问，等待关联到当前工作区。"), None),
            "missing": ("找不到当前设置的素材目录。", "连接存储盘或在设置中选择现有目录。"),
            "unmounted": ("素材所在的外接盘没有挂载。", "连接原数据盘后重试。"),
            "read_only": ("素材目录当前不可写，素材无法进入分析。", "检查磁盘是否以只读方式挂载或目录权限是否被改动，再重新检查。"),
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
        pending_files=repository.pending_material_copies(workspace_id) if bound else [],
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
    workspace_id = current_workspace_id(request)
    _content_reader(request, workspace_id)
    return _material_binding_status(workspace_id, repository)


@app.post("/api/production/materials/intake/bind", response_model=MaterialIntakeStatusResponse)
def bind_material_intake(request: Request, payload: MaterialIntakeBindRequest) -> MaterialIntakeStatusResponse:
    workspace_id = current_workspace_id(request)
    repository = production_repo
    service = _material_ingestion_for(repository)
    try:
        repository.bind_material_ingestion(
            workspace_id,
            expected_directory=payload.expected_directory,
            verified_owner_user_id=_content_reader(request, workspace_id)[0],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    service.wake()
    return _material_binding_status(workspace_id, repository)


@app.post("/api/production/materials/intake/copy-complete", response_model=MaterialCopyCompleteResponse)
def confirm_material_copy_complete(request: Request, payload: MaterialCopyCompleteRequest) -> MaterialCopyCompleteResponse:
    workspace_id = current_workspace_id(request)
    repository = production_repo
    service = _material_ingestion_for(repository)
    try:
        service.confirm_copy_complete(
            workspace_id, relative_path=payload.relative_path,
            expected_revision=payload.expected_revision.model_dump(),
            confirmation_source=payload.confirmation_source,
            verified_owner_user_id=_content_reader(request, workspace_id)[0],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="文件已移除，请重新检查素材目录。") from exc
    return MaterialCopyCompleteResponse(
        confirmed=True, intake=_material_binding_status(workspace_id, repository),
    )


@app.post("/api/production/materials/intake/scan")
def scan_material_intake(request: Request) -> MaterialLibraryResponse:
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    repository = production_repo
    status = _material_binding_status(workspace_id, repository)
    if not status.bound_to_current_workspace:
        raise HTTPException(status_code=409, detail="请先在当前工作区绑定素材目录")
    if status.directory_state != "ready":
        raise HTTPException(status_code=409, detail=status.directory_message)
    _material_ingestion_for(repository).scan_once()
    assets, total = repository.list_assets_page(
        workspace_id, limit=24, offset=0,
        include_private=local_diagnostic,
        viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic,
    )
    return MaterialLibraryResponse(
        assets=[_material_asset_response(asset) for asset in assets],
        total_count=total, limit=24, offset=0, has_more=len(assets) < total,
    )


def _media_link_service(
    workspace_id: str, verified_owner_user_id: str | None = None,
    local_diagnostic: bool = False,
) -> MediaLinkImportService:
    """Build the direct-link importer on top of the existing intake binding."""
    repository = production_repo
    try:
        intake_root = repository.material_link_target(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MediaLinkImportService(
        # trust_env=False：不读环境里的代理设置，导入只能连到已判定的公开地址。
        client_factory=lambda: httpx.Client(
            follow_redirects=False, timeout=60.0, trust_env=False
        ),
        intake_root=intake_root,
        provenance=MaterialLinkProvenanceSink(
            repository, workspace_id, verified_owner_user_id, local_diagnostic,
        ),
        max_bytes=DEFAULT_MAX_BYTES,
    )


# 只有真正的边界拒绝才算客户端可修的问题；其余照实报告来源返回的状态。
_MEDIA_LINK_STATUS = {
    "invalid_url": 422,
    "unsupported_scheme": 422,
    "credentials_in_url": 422,
    "social_page": 422,
    "private_address": 422,
    "host_unresolved": 422,
    "web_page_not_media": 422,
    "unsupported_media_type": 422,
    "too_large": 413,
    "credentials_required": 415,
    "not_found": 404,
}


def _media_link_failure(exc: MediaLinkImportError) -> HTTPException:
    return HTTPException(
        status_code=_MEDIA_LINK_STATUS.get(exc.code, 502),
        detail={"code": exc.code, "detail": exc.message},
    )


@app.post("/api/production/materials/link/check", response_model=MediaLinkCheckResponse)
def check_production_material_link(
    request: Request,
    payload: MediaLinkCheckRequest,
) -> MediaLinkCheckResponse:
    workspace_id = current_workspace_id(request)
    service = _media_link_service(workspace_id)
    try:
        result = service.check(payload.url)
    except MediaLinkImportError as exc:
        raise _media_link_failure(exc) from exc
    return MediaLinkCheckResponse.model_validate(asdict(result))


@app.post("/api/production/materials/link/import", response_model=MediaLinkImportResponse)
def import_production_material_link(
    request: Request,
    payload: MediaLinkImportRequest,
) -> MediaLinkImportResponse:
    workspace_id = current_workspace_id(request)
    # 导入的素材归属这次请求的可信身份，不采信任何自填值。
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    service = _media_link_service(workspace_id, viewer_user_id, local_diagnostic)
    try:
        result = service.import_link(payload.url)
        asset = production_repo.get_asset(workspace_id, result.asset_id)
    except MediaLinkImportError as exc:
        raise _media_link_failure(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    return MediaLinkImportResponse(
        url=result.url,
        media_type=asset.media_type,
        file_name=asset.file_name,
        size_bytes=asset.size_bytes,
        # 以已登记素材为准：同一原网址重复提交时，响应描述的仍是那条素材。
        file_path=asset.path,
        asset_id=asset.id,
        provenance_id=result.provenance_id,
        reused_existing_file=result.reused_existing_file,
        analysis_status=asset.analysis_status,
        analysis_message=asset.analysis_message,
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
    get_production_material(asset_id, request)
    try:
        asset = production_repo.update_asset_tags(current_workspace_id(request), asset_id, payload.user_tags, expected_asset_updated_at=payload.expected_asset_updated_at)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材不属于当前工作区") from exc
    except MaterialVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


@app.put("/api/production/materials/notes", response_model=MaterialAssetNotesResponse)
def update_material_asset_notes(request: Request, payload: MaterialAssetNotesRequest) -> MaterialAssetNotesResponse:
    """Save each named material's own text and the batch background it arrived with.

    Only the materials named in the request are touched, and the repository
    refuses the whole batch if any one of them is not this workspace's, so the
    user is never told a note is stored that was not written.
    """
    try:
        assets = production_repo.update_asset_notes(
            current_workspace_id(request),
            [
                MaterialAssetNote(
                    asset_id=item.asset_id,
                    user_description=item.user_description,
                    upload_context=item.upload_context,
                )
                for item in payload.items
            ],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="所选素材有不属于当前工作区的项目，未做任何修改") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MaterialAssetNotesResponse(assets=[_material_asset_response(asset) for asset in assets])


def _material_business_object_response(entry: MaterialBusinessObject) -> MaterialBusinessObjectResponse:
    return MaterialBusinessObjectResponse(id=entry.id, kind=entry.kind, name=entry.name)


@app.get("/api/production/business-objects", response_model=MaterialBusinessObjectsResponse)
def list_material_business_objects(request: Request) -> MaterialBusinessObjectsResponse:
    """List this workspace's product/dish/person name entries.

    Entries are scoped to the calling workspace, and creating one attaches it to
    nothing: the user still chooses which materials carry it.
    """
    entries = production_repo.list_business_objects(current_workspace_id(request))
    return MaterialBusinessObjectsResponse(
        items=[_material_business_object_response(entry) for entry in entries],
        total_count=len(entries),
    )


@app.post("/api/production/business-objects", response_model=MaterialBusinessObjectResponse, status_code=201)
def create_material_business_object(
    request: Request, payload: MaterialBusinessObjectCreateRequest
) -> MaterialBusinessObjectResponse:
    """Add one name entry without associating it to any material yet."""
    try:
        entry = production_repo.create_business_object(current_workspace_id(request), payload.kind, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _material_business_object_response(entry)


@app.get("/api/production/materials/{asset_id}/business-objects", response_model=MaterialBusinessObjectsResponse)
def list_material_asset_business_objects(request: Request, asset_id: str) -> MaterialBusinessObjectsResponse:
    """Read back the name entries attached to one material."""
    try:
        entries = production_repo.list_asset_business_objects(current_workspace_id(request), asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材不存在或不属于当前工作区") from exc
    return MaterialBusinessObjectsResponse(
        items=[_material_business_object_response(entry) for entry in entries],
        total_count=len(entries),
    )


@app.put("/api/production/materials/{asset_id}/business-objects", response_model=MaterialBusinessObjectsResponse)
def save_material_asset_business_objects(
    request: Request, asset_id: str, payload: MaterialAssetBusinessObjectsSaveRequest
) -> MaterialBusinessObjectsResponse:
    """Save exactly the name entries the user selected for this one material.

    Only the material named in the path is touched, and the repository refuses
    the whole selection if any entry belongs to another workspace, so the user is
    never shown associations that were not written. An empty list clears them.
    """
    try:
        entries = production_repo.set_asset_business_objects(
            current_workspace_id(request), asset_id, list(payload.business_object_ids)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材或名称条目不属于当前工作区，未做任何修改") from exc
    return MaterialBusinessObjectsResponse(
        items=[_material_business_object_response(entry) for entry in entries],
        total_count=len(entries),
    )


@app.get(
    "/api/production/materials/{asset_id}/usage-records",
    response_model=MaterialUsageRecordsResponse,
)
def list_material_segment_usage_records(
    request: Request, asset_id: str, segment_id: str
) -> MaterialUsageRecordsResponse:
    """Show what used one segment of one material, read from real render rows."""
    workspace_id = current_workspace_id(request)
    try:
        asset = production_repo.get_asset(workspace_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材或片段不存在或不属于当前工作区") from exc
    if not _content_visible(request, workspace_id, asset.visibility, asset.owner_user_id):
        raise HTTPException(status_code=404, detail="素材或片段不存在或不属于当前工作区")
    try:
        records = production_repo.list_segment_usage_records(workspace_id, asset_id, segment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="素材或片段不存在或不属于当前工作区") from exc
    return MaterialUsageRecordsResponse(items=records, total_count=len(records))


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
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    try:
        matches = production_repo.search_material_segments(
            workspace_id,
            query=q,
            usage_roles=[usage_role] if usage_role else ["a_roll", "b_roll"],
            tags=[item.strip() for item in tags.split(",") if item.strip()]
            if tags
            else None,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            limit=limit,
            viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [MaterialSegmentMatchResponse.model_validate(asdict(item)) for item in matches]


@app.put(
    "/api/production/materials/{asset_id}/visibility",
    response_model=MaterialAssetResponse,
)
def set_production_material_visibility(
    asset_id: str,
    request: Request,
    payload: ContentVisibilityRequest,
) -> MaterialAssetResponse:
    """显式共享或收回一条素材；响应回读同一列。"""
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    try:
        asset = production_repo.set_asset_visibility(
            current_workspace_id(request), asset_id, payload.visibility
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _material_asset_response(asset)


@contextmanager
def _material_discovery_errors():
    try:
        yield
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="扫描任务或候选素材不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="无法读取所选位置，请检查目录权限。") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="媒体目录暂不可用，请连接原磁盘后重试。") from exc


@app.get("/api/production/materials/discovery/roots")
def material_discovery_roots(request: Request):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        discovery = production_repo.material_discovery
        return {"roots": discovery.roots(), "exclusions": discovery.store.exclusions(workspace_id, owner)}


@app.get("/api/production/materials/discovery/latest")
def latest_material_discovery(request: Request):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        return production_repo.material_discovery.latest(workspace_id, owner)


@app.post("/api/production/materials/discovery", status_code=201)
def start_material_discovery(request: Request, payload: MaterialDiscoveryStartRequest):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        return production_repo.material_discovery.start(workspace_id, owner,
            root_ids=payload.root_ids, exclusions=payload.exclusions)


@app.get("/api/production/materials/discovery/{session_id}")
def get_material_discovery(session_id: str, request: Request, offset: int = 0):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        return production_repo.material_discovery.snapshot(session_id, workspace_id, owner, offset=offset)


@app.post("/api/production/materials/discovery/{session_id}/advance")
def advance_material_discovery(session_id: str, request: Request):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        return production_repo.material_discovery.advance(session_id, workspace_id, owner)


@app.post("/api/production/materials/discovery/{session_id}/control")
def control_material_discovery(session_id: str, request: Request, payload: MaterialDiscoveryControlRequest):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        return production_repo.material_discovery.control(session_id, workspace_id, owner,
            payload.action, exclusions=payload.exclusions)


@app.post("/api/production/materials/discovery/{session_id}/receive")
def receive_discovered_materials(session_id: str, request: Request, payload: MaterialDiscoveryReceiveRequest):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        repository = production_repo
        result = repository.material_discovery.receive(session_id, workspace_id, owner, payload.candidate_ids)
        # Reception is durable even when the independent analysis driver is unavailable.
        if material_ingestion is not None and material_ingestion.repository is repository:
            material_ingestion.wake()
        return result


@app.get("/api/production/materials/discovery/{session_id}/candidates/{candidate_id}/preview")
def preview_discovered_material(session_id: str, candidate_id: str, request: Request):
    workspace_id, owner = _member_owned_content_scope(request)
    with _material_discovery_errors():
        path = production_repo.material_discovery.preview_path(session_id, candidate_id, workspace_id, owner)
        return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.get(
    "/api/production/materials/{asset_id}",
    response_model=MaterialAssetResponse,
)
def get_production_material(
    asset_id: str,
    request: Request,
) -> MaterialAssetResponse:
    workspace_id = current_workspace_id(request)
    try:
        asset = production_repo.get_asset(workspace_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    if not _content_visible(request, workspace_id, asset.visibility, asset.owner_user_id):
        raise HTTPException(status_code=404, detail="material asset not found")
    return _material_asset_response(asset)


@app.get("/api/production/materials/{asset_id}/media")
def stream_production_material(asset_id: str, request: Request) -> FileResponse:
    """Stream a workspace-scoped library asset for the desktop review player."""
    workspace_id = current_workspace_id(request)
    try:
        asset = production_repo.get_asset(workspace_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="material asset not found") from exc
    if not _content_visible(request, workspace_id, asset.visibility, asset.owner_user_id):
        raise HTTPException(status_code=404, detail="material asset not found")
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
        ".webm": "video/webm",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".wav": "audio/wav",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@app.post(
    "/api/production/materials/scan",
    response_model=MaterialLibraryResponse,
)
def scan_production_materials(request: Request) -> MaterialLibraryResponse:
    workspace_id = current_workspace_id(request)
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    if material_execution_mode == "scheduled":
        _material_ingestion_for(production_repo).scan_once()
    else:
        production_repo.scan_library(workspace_id)
    assets, total = production_repo.list_assets_page(
        workspace_id, limit=24, offset=0, include_private=local_diagnostic,
        viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic,
    )
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
    get_production_material(asset_id, request)
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
            expected_asset_updated_at=payload.expected_asset_updated_at,
            expected_segment_updated_at=payload.expected_segment_updated_at,
        )
    except MaterialVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    _render_stage_use_case().record_usage(workspace_id, plan)






def _verify_silence_audio_candidate(workspace_id, plan, context, resources, facts, actor):
    try:
        return _picture_preview_renderer().verify_silence_audio_candidate(workspace_id, plan, context, resources, facts, actor)
    except PicturePreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _granted_audio_mix_sources(workspace_id, plan, context):
    try:
        return _picture_preview_renderer().audio_mix_sources(workspace_id, plan, context)
    except PicturePreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc






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
    return metadata_refresh_montage_review_state(plan)


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
    viewer_user_id, local_diagnostic = _content_reader(request, workspace_id)
    for asset in production_repo.list_assets(
        workspace_id, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic
    ):
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
def get_runtime_health(request: Request) -> RuntimeHealthResponse:
    workspace_id = current_workspace_id(request)
    return RuntimeHealthResponse.model_validate(runtime_health(_workspace_settings_for(workspace_id), workspace_id))


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
    """Initialize the trusted workspace after its minimum storage check."""
    principal = getattr(request.state, "principal", None)
    if auth_context.enabled and (
        principal is None or not principal.user_id or not principal.workspace_id
    ):
        raise HTTPException(status_code=403, detail="workspace context is required")

    preflight = workspace_preflight(settings)
    if preflight.get("overall") != "ready":
        raise HTTPException(
            status_code=409,
            detail="workspace storage is not ready",
        )

    if not auth_context.enabled:
        return OnboardingCompletionResponse(
            workspace_id=LEGACY_WORKSPACE_ID,
            completed=True,
        )

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
def get_runtime_settings(request: Request) -> RuntimeSettingsResponse:
    return RuntimeSettingsResponse.model_validate(
        _workspace_settings_for(current_workspace_id(request)).safe_runtime_settings(
            current_workspace_id(request)
        )
    )


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
    workspace_id = current_workspace_id(request)
    submitted = payload.model_dump()
    device_values = device_runtime_settings_values(submitted)
    try:
        with _runtime_reload_lock:
            _assert_runtime_reload_allowed()
            prospective_environment = {**os.environ, **runtime_settings_updates(device_values)}
            prospective_settings = Settings.from_env(prospective_environment)
            candidate_catalog = model_catalog_from_settings(prospective_settings)
            with model_installer.guard_catalog_update(candidate_catalog):
                prepared = _prepare_runtime_reload(prospective_settings)
                with _quiesced_runtime_drivers() as drivers:
                    changed = apply_runtime_settings_save(workspace_id, runtime_env_path, submitted)
                    if changed:
                        try:
                            _activate_runtime_reload(prepared, drivers)
                        except Exception as exc:
                            raise HTTPException(status_code=503, detail="配置已保存，运行重载失败；请检查运行状态。") from exc
    except RuntimeSettingsSaveError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WorkspaceSecretStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelInstallError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RuntimeSettingsResponse.model_validate(
        _workspace_settings_for(workspace_id).safe_runtime_settings(workspace_id)
    )



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


def _assert_runtime_reload_allowed() -> None:
    if material_execution_mode == "scheduled" or creation_execution_mode == "scheduled":
        raise HTTPException(status_code=503, detail="整机调度的安全重载尚未接通，现有驱动保持原状态。")


@dataclass(frozen=True)
class PreparedRuntimeReload:
    dependencies: RuntimeDependencies
    temporary_root: Path | None
    routes: list
    creation: CreationExecution


@dataclass
class QuiescedRuntimeDrivers:
    device: DeviceRuntime | None = None
    automation: AutomationRuntime | None = None
    continuation: AssistantContinuationDriver | None = None
    committed: bool = False


@contextmanager
def _quiesced_runtime_drivers():
    drivers = QuiescedRuntimeDrivers()
    primary_failure: BaseException | None = None
    try:
        if device_runtime is not None:
            if not device_runtime.pause(30.0):
                raise HTTPException(503, "整机运行单元尚未到达安全边界，配置未保存。")
            drivers.device = device_runtime
        if automation_runtime is not None:
            if not automation_runtime.pause(10.0):
                raise HTTPException(503, "自动任务仍在处理本轮输入，配置未保存。")
            drivers.automation = automation_runtime
        if assistant_continuation_driver is not None:
            if not assistant_continuation_driver.pause(5.0):
                raise HTTPException(503, "对话结果仍在回填，配置未保存。")
            drivers.continuation = assistant_continuation_driver
        yield drivers
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        if not drivers.committed:
            failed = []
            for name, owner in (("continuation", drivers.continuation),
                    ("automation", drivers.automation), ("device", drivers.device)):
                if owner is not None:
                    try:
                        owner.resume()
                    except RuntimeError as exc:
                        failed.append((name, exc))
            if failed:
                if primary_failure is not None:
                    for name, failure in failed:
                        primary_failure.add_note(
                            f"旧运行恢复失败，未创建替代协调者（{name}）：{type(failure).__name__}: {failure}"
                        )
                else:
                    raise RuntimeError("旧运行恢复失败，未创建替代协调者：" + ", ".join(name for name, _ in failed)) from failed[0][1]


def _prepare_runtime_reload(prospective_settings: Settings) -> PreparedRuntimeReload:
    _assert_runtime_reload_allowed()
    if _assistant_stream_workers:
        raise HTTPException(status_code=503, detail="对话仍在运行，请等待当前结果返回后重载设置。")
    temporary_root = prepare_process_temporary_directory(prospective_settings)
    dependencies = prepare_runtime_dependencies(prospective_settings, current_database=_current_database_owners())
    routes = _prepare_runtime_routes(dependencies.database, dependencies.auth)
    return PreparedRuntimeReload(dependencies, temporary_root, routes,
        _new_creation_execution(dependencies.database.production))


def _activate_runtime_reload(prepared: PreparedRuntimeReload, drivers: QuiescedRuntimeDrivers) -> None:
    global creation_execution, automation_runtime
    old_platform_auth = platform_auth_service
    database_changed = (
        Path(production_repo.database_path).expanduser().resolve()
        != Path(prepared.dependencies.database.production.database_path).expanduser().resolve()
    )
    restart_device = device_runtime is not None and device_runtime.running
    restart_automation = automation_runtime is not None and automation_runtime.thread is not None and automation_runtime.thread.is_alive()
    restart_assistant = assistant_continuation_driver is not None

    # A false device stop keeps its original reference and grant. No replacement
    # coordinator or new globals can be published over a still-running unit.
    _stop_device_runtime()
    if automation_runtime is not None:
        automation_runtime.stop()
        if automation_runtime.thread is not None and automation_runtime.thread.is_alive():
            raise RuntimeError("自动任务驱动尚未退出，原运行绑定保持。")
    _stop_execution_worker()
    _stop_material_ingestion()
    if database_changed:
        old_platform_auth.sessions.close_all()

    model_installer.rebind_catalog(prepared.dependencies.models.catalog)
    publish_process_temporary_directory(prepared.temporary_root)
    _publish_runtime_dependencies(prepared.dependencies)
    _publish_runtime_routes(prepared.routes)
    creation_execution = prepared.creation
    automation_runtime = None
    _start_execution_worker()
    _start_material_ingestion()
    _start_device_runtime(start=restart_device)
    if restart_automation:
        _automation_runtime().start()
    if restart_assistant:
        assistant_continuation_driver.resume()
    drivers.committed = True


def _reload_runtime_dependencies(prepared: PreparedRuntimeReload | None = None) -> None:
    with _runtime_reload_lock:
        _assert_runtime_reload_allowed()
        if prepared is None:
            prospective_settings = Settings.from_env()
            candidate_catalog = model_catalog_from_settings(prospective_settings)
        else:
            candidate_catalog = prepared.dependencies.models.catalog
        with model_installer.guard_catalog_update(candidate_catalog):
            if prepared is None:
                prepared = _prepare_runtime_reload(prospective_settings)
            with _quiesced_runtime_drivers() as drivers:
                _activate_runtime_reload(prepared, drivers)


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


def _knowledge_item_response(item: KnowledgeItem, visibility: str) -> KnowledgeItemResponse:
    response = KnowledgeItemResponse.model_validate(item, from_attributes=True)
    response.visibility = visibility
    return response


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
                    use_state=payload.use_state,
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
    workspace_id = current_workspace_id(request)
    try:
        item = source_repository.save_source_item_to_knowledge(
            workspace_id,
            source_item_id,
            verified_owner_user_id=_content_reader(request, workspace_id)[0],
        )
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="source item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = _knowledge_item_response(
        item, source_repository.get_knowledge_item_visibility(workspace_id, item.id)
    )
    response.saved_source_item_id = source_item_id
    # 重复结果只来自解析链：内容指纹命中已有历史版本时给出对应版本号。
    if item.content_hash is not None and response.duplicate_of_version is None:
        source = source_repository.get_source_item(workspace_id, source_item_id)
        if source.content_hash != item.content_hash:
            stored = source_repository.find_version_by_content_hash(
                workspace_id,
                source.content_hash,
                knowledge_item_id=item.id if source.source_label == "web_snapshot" else None,
                verified_owner_user_id=_content_reader(request, workspace_id)[0],
            )
            response.duplicate_of_version = stored.version if stored else None
    return response


@app.post("/api/source-items/file", response_model=SourceItemDetailResponse)
def import_knowledge_file(
    request: Request,
    payload: KnowledgeFileImportRequest,
) -> SourceItemDetailResponse:
    """导入 Markdown/文本/PDF/Word/Excel 文件：保存全文、真实出处和显式参数。

    解析失败（不支持格式、扫描件无文本层）直接报错，不写入伪造正文。
    """
    workspace_id = current_workspace_id(request)
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="文件内容不是合法的 base64") from exc
    try:
        parsed = parse_knowledge_file(payload.filename.strip(), data)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    connector = _compat_connector(
        workspace_id,
        "knowledge_files",
        platform="knowledge_files",
        protocol="local_file",
        account_or_rule="知识文件导入",
    )
    try:
        item = source_repository.save_source_file(
            workspace_id,
                SourceItem(
                    id=f"source_{uuid4().hex}",
                    workspace_id=workspace_id,
                    connector_id=connector.id,
                    source_label="file_import",
                    source_url=None,
                    content_type=parsed.content_type,
                    author=None,
                    published_at=None,
                    body=parsed.full_text,
                    subtitles=None,
                    media_references=(),
                    license="user_provided",
                    title=parsed.title,
                    file_reference=parsed.filename,
                    content_hash=parsed.content_hash,
                    sections=parsed.sections,
                    parameters=parsed.parameters,
                    use_state=payload.use_state,
                ), data, verified_owner_user_id=_content_reader(request, workspace_id)[0],
        )
    except KnowledgeOriginalIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KnowledgeOriginalStorageError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _source_item_response(item)


@app.get("/api/knowledge-items/{knowledge_item_id}/versions/{version}/original")
def download_knowledge_original(knowledge_item_id: str, version: str, request: Request):
    workspace_id = current_workspace_id(request)
    owner, diagnostic = _content_reader(request, workspace_id)
    try:
        original = source_repository.read_knowledge_original(workspace_id, knowledge_item_id, version,
            user_id=owner, local_diagnostic=diagnostic)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到可访问的资料原件或固定版本。") from exc
    except KnowledgeOriginalUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeOriginalIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KnowledgeOriginalStorageError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return Response(original.content, media_type=original.content_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(original.filename, safe='')}",
        "ETag": f'"{original.content_hash}"', "X-Content-Type-Options": "nosniff"})


@app.get("/api/knowledge-items/search", response_model=KnowledgeSearchResponse)
def search_knowledge_items(request: Request, query: str, limit: int = 5, offset: int = 0):
    if not 1 <= len(query.strip()) <= 120 or not 1 <= limit <= 20 or offset < 0:
        raise HTTPException(status_code=422, detail="资料检索须为 1–120 字，分页大小 1–20，偏移非负")
    workspace_id = current_workspace_id(request)
    user_id, diagnostic = _content_reader(request, workspace_id)
    try:
        items, total = source_repository.search_knowledge_items(workspace_id, query=query,
            limit=limit, offset=offset, user_id=user_id, local_diagnostic=diagnostic)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return KnowledgeSearchResponse(items=items, total_count=total, limit=limit, offset=offset,
                                   next_offset=offset + len(items) if offset + len(items) < total else None)


@app.get("/api/knowledge-items/{knowledge_item_id}", response_model=KnowledgeItemDetailResponse)
def get_knowledge_item_detail(
    knowledge_item_id: str,
    request: Request,
) -> KnowledgeItemDetailResponse:
    workspace_id = current_workspace_id(request)
    user_id, local_diagnostic = _content_reader(request, workspace_id)
    try:
        item = source_repository.get_knowledge_item_readable_by(
            workspace_id, knowledge_item_id, user_id=user_id, local_diagnostic=local_diagnostic,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    visibility = source_repository.get_knowledge_item_visibility(item.workspace_id, item.id)
    versions = source_repository.list_knowledge_item_versions(item.workspace_id, item.id)
    detail = KnowledgeItemDetailResponse.model_validate(item, from_attributes=True)
    detail.visibility = visibility
    detail.versions = [
        KnowledgeItemVersionResponse.model_validate(version, from_attributes=True)
        for version in versions
    ]
    return detail


@app.put("/api/knowledge-items/{knowledge_item_id}/content", response_model=KnowledgeItemDetailResponse)
def edit_knowledge_item_content(
    knowledge_item_id: str, request: Request, payload: KnowledgeItemContentRequest,
) -> KnowledgeItemDetailResponse:
    workspace_id = current_workspace_id(request)
    user_id, local_diagnostic = _content_reader(request, workspace_id)
    try:
        source_repository.edit_knowledge_item(
            workspace_id, knowledge_item_id, expected_version=payload.expected_version,
            title=payload.title, body=payload.body, verified_user_id=user_id,
            local_diagnostic=local_diagnostic,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="资料不存在或不属于当前工作区") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KnowledgeVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_knowledge_item_detail(knowledge_item_id, request)


@app.put(
    "/api/knowledge-items/{knowledge_item_id}/visibility",
    response_model=KnowledgeItemResponse,
)
def set_knowledge_item_visibility_route(
    knowledge_item_id: str,
    request: Request,
    payload: KnowledgeItemVisibilityRequest,
) -> KnowledgeItemResponse:
    """显式共享或收回：把私有内容放进/移出团队共享范围。"""
    denied = _local_member_write_guard(request)
    if denied is not None:
        return denied
    workspace_id = current_workspace_id(request)
    try:
        visibility = source_repository.set_knowledge_item_visibility(
            workspace_id, knowledge_item_id, payload.visibility
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = source_repository.get_knowledge_item(workspace_id, knowledge_item_id)
    return _knowledge_item_response(item, visibility)


@app.get("/api/knowledge-items", response_model=KnowledgeItemsResponse)
def list_knowledge_items(request: Request) -> KnowledgeItemsResponse:
    workspace_id = current_workspace_id(request)
    user_id, local_diagnostic = _content_reader(request, workspace_id)
    try:
        items = source_repository.list_knowledge_items_readable_by(
            workspace_id, user_id=user_id, local_diagnostic=local_diagnostic
        )
    except ValueError as exc:
        # 已开认证却没有可信身份：没有可读身份就是没有可读内容，不退回只看共享。
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return KnowledgeItemsResponse(
        items=[
            _knowledge_item_response(
                item, source_repository.get_knowledge_item_visibility(item.workspace_id, item.id)
            )
            for item in items
        ],
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
        [replace(item.to_source_item(workspace_id, connector.id), use_state=payload.use_state)
         for item in topic_items],
    )
    return _legacy_source_items_response(
        workspace_id,
        source_repository.list_source_items(workspace_id, connector.id),
    )


@app.post("/api/source-items/link", response_model=SourceItemDetailResponse)
def capture_web_snapshot(
    request: Request,
    payload: ManualLinkRequest,
) -> SourceItemDetailResponse:
    """抓一个普通网址的完整正文快照，保存用户输入的原网址和抓取时间。

    原网址就是用户填的那个地址，并按它串版本；重定向到的地址作为附加事实记录，
    不替换原网址。相同正文不新增版本（保存到知识库时由内容指纹判定）；网址内容
    更新时另存新版本，旧版本及其快照保持不变。登录、不可达或没有正文按真实失败。
    """
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    workspace_id = current_workspace_id(request)
    connector = _compat_connector(
        workspace_id,
        "web_snapshot",
        platform="web_snapshot",
        protocol="https",
        account_or_rule="网页快照",
    )
    try:
        fetched = HtmlPageSourceClient([url]).fetch_page(url)
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
    snapshot = fetched.snapshot
    item = source_repository.save_source_items(
        workspace_id,
        [
            SourceItem(
                id=f"source_{uuid4().hex}",
                workspace_id=workspace_id,
                connector_id=connector.id,
                source_label="web_snapshot",
                source_url=url,
                content_type="article",
                author=None,
                published_at=None,
                body=snapshot.full_text,
                subtitles=None,
                media_references=(),
                license="user_provided",
                title=snapshot.title,
                # 版本链按用户填的原网址串，不按重定向后的地址，也不按随机条目 id。
                file_reference=url,
                content_hash=snapshot.content_hash,
                sections=snapshot.sections,
                parameters=(),
                fetched_at=fetched.fetched_at,
                use_state=payload.use_state,
            )
        ],
    )[0]
    return _source_item_response(item)


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
    local_only: bool = Form(False),  # noqa: B008
) -> VoiceTranscriptionResponse:
    """Transcribe uploaded audio; local_only selects LocalWhisper for this request."""
    try:
        content = await audio.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {exc}") from exc

    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file")

    try:
        if local_only:
            result = transcribe_audio_bytes(content, settings, local_only=True)
        else:
            result = transcribe_audio_bytes(content, settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VoiceTranscriptionResponse(
        transcript=result["transcript"],
        provider=result["provider"],
        segments=result["segments"],
    )


# ── Creator Profile ──────────────────────────────────────────────


def _creator_profile_response(profile) -> CreatorProfileResponse:
    return CreatorProfileResponse(
        id=profile.id, revision=profile.revision,
        common_expressions=profile.common_expressions,
        banned_words=profile.banned_words,
        style_preferences=profile.style_preferences,
        created_at=profile.created_at, updated_at=profile.updated_at,
    )


def _creator_profile_scope(request: Request) -> tuple[str, str | None]:
    workspace_id = current_workspace_id(request)
    owner_id, diagnostic = _content_reader(request, workspace_id)
    if owner_id is None and not diagnostic:
        raise HTTPException(status_code=403, detail="请先确认当前成员身份，再读取创作偏好。")
    return workspace_id, owner_id


@app.get("/api/creator-profile", response_model=CreatorProfileResponse)
def get_creator_profile_endpoint(request: Request) -> CreatorProfileResponse:
    workspace_id, owner_id = _creator_profile_scope(request)
    return _creator_profile_response(get_or_create_profile(
        settings.database_path, workspace_id=workspace_id, owner_user_id=owner_id,
    ))


@app.patch("/api/creator-profile", response_model=CreatorProfileResponse)
def update_creator_profile_endpoint(body: CreatorProfileUpdate, request: Request) -> CreatorProfileResponse:
    workspace_id, owner_id = _creator_profile_scope(request)
    profile = get_or_create_profile(settings.database_path, workspace_id=workspace_id, owner_user_id=owner_id)
    if body.common_expressions is not None:
        profile.common_expressions = body.common_expressions
    if body.banned_words is not None:
        profile.banned_words = body.banned_words
    if body.style_preferences is not None:
        profile.style_preferences = body.style_preferences
    try:
        saved = upsert_creator_profile(profile, settings.database_path, workspace_id=workspace_id,
                                       owner_user_id=owner_id, expected_revision=body.expected_revision)
    except CreatorProfileConflict as exc:
        raise HTTPException(status_code=409, detail="创作偏好已更新，请重新读取并核对；你的修改没有覆盖已保存内容。") from exc
    return _creator_profile_response(saved)


@app.get("/api/assistant/memory", response_model=CreatorProfileResponse)
def read_assistant_memory(request: Request) -> CreatorProfileResponse:
    workspace_id = current_workspace_id(request)
    owner_id = _assistant_member_id(request, workspace_id)
    return _creator_profile_response(get_or_create_profile(
        settings.database_path, workspace_id=workspace_id, owner_user_id=owner_id,
    ))


@app.get("/api/assistant/memory/user.md")
def export_assistant_memory(request: Request) -> Response:
    profile = read_assistant_memory(request)
    lines = ["# 我的创作偏好", "", "这些是我明确保存的偏好；当前任务要求优先，不授予工具或外发权限。", ""]
    for title, values in (("常用表达", profile.common_expressions), ("避免用词", profile.banned_words)):
        lines.extend([f"## {title}", ""])
        if not values:
            lines.append("未设置。")
        for value in values:
            lines.extend("> " + part for part in value.splitlines())
            lines.append("")
        lines.append("")
    lines.extend(["## 创作偏好", ""])
    if not profile.style_preferences:
        lines.append("未设置。")
    for key, value in profile.style_preferences.items():
        lines.append("> " + " ".join(key.splitlines()) + "：")
        lines.extend("> " + part for part in value.splitlines())
        lines.append("")
    return Response("\n".join(lines) + "\n", media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="user.md"'})


def _explicit_memory_action(message: str, action: str, key: str) -> bool:
    # This is the write boundary, not an intent planner: inferred habits and a
    # one-off production request must not silently become long-term preferences.
    if action == "remember":
        if re.search(r"(?:不要|别|不用|无需|不必).{0,4}(?:记住|保存|记录)", message):
            return False
        return bool(re.search(r"记住|\bremember\b|(?:保存|记录).{0,8}(?:偏好|习惯)|(?:以后|今后).{0,12}(?:默认|都用)", message, re.IGNORECASE))
    return bool(re.search(r"忘记|\bforget\b", message, re.IGNORECASE)
                or (re.search(r"删除|移除|不再使用", message) and (key in message or "偏好" in message)))


@app.patch("/api/assistant/memory", response_model=CreatorProfileResponse)
def update_assistant_memory(body: CreatorPreferenceChangeRequest, request: Request) -> CreatorProfileResponse:
    workspace_id = current_workspace_id(request)
    owner_id = _assistant_member_id(request, workspace_id)
    turn = conversation_repo.get_turn(body.assistant_turn_id)
    if turn is None or conversation_repo.get(turn["conversation_id"], workspace_id, owner_id) is None:
        raise HTTPException(status_code=404, detail="当前对话轮次不存在或不可访问。")
    if turn["status"] != "running":
        raise HTTPException(status_code=409, detail="这轮对话已结束，未再次修改长期偏好。")
    quote = body.source_quote.strip()
    message = turn["user_message"]
    if not quote or quote not in message or not _explicit_memory_action(message, body.action, body.key):
        raise HTTPException(status_code=409, detail="本轮没有明确要求长期保存或忘记这条偏好，未修改记忆。")
    key = body.key.strip()
    value = body.value.strip() if body.value is not None else None
    if not key or (body.action == "remember" and not value) or (body.action == "forget" and body.value is not None):
        raise HTTPException(status_code=422, detail="请明确要记住的偏好内容，或要忘记的偏好名称。")
    try:
        profile = change_creator_preference(
            settings.database_path, workspace_id=workspace_id, owner_user_id=owner_id,
            key=key, value=value, expected_revision=body.expected_revision,
            source_turn_id=turn["id"], source_quote=quote,
        )
    except CreatorProfileConflict as exc:
        raise HTTPException(status_code=409, detail="创作偏好已更新，请先读取最新内容；没有覆盖新的偏好。") from exc
    return _creator_profile_response(profile)


@app.post("/api/creator-profile/learn", response_model=CreatorProfileLearnResult)
def learn_creator_profile_endpoint(request: Request) -> CreatorProfileLearnResult:
    _creator_profile_scope(request)
    raise HTTPException(status_code=410, detail="旧的历史自动学习入口已停用。请明确保存创作偏好；助手推测需要先由你采纳。")


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
    timeline_version: TimelineVersion | None = None,
) -> bool:
    try:
        return _render_stage_use_case().valid(workspace_id, task_id, verify_media=verify_media,
            render_context=render_context, timeline_version=timeline_version)
    except RenderRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _creation_execution_control():
    return CreationExecutionControl(production_repo, creation_execution, _render_stage_use_case(), _verify_creation_identity)


def _new_creation_execution(repository: ProductionRepository) -> CreationExecution:
    return CreationExecution(
        repository,
        run_narration=_generate_creation_task_narration_for_workspace,
        run_montage_plan=_create_creation_task_montage_plan_for_workspace,
        run_render=_render_creation_task_montage_for_workspace,
        render_is_valid=_creation_render_is_valid,
    )


def _recover_creation_executions() -> None:
    _creation_execution_control().recover(device_runtime)


def _creation_runtime():
    """交互式制作许可的这一个整机运行时，缺什么就明确说缺什么。

    ``creation_execution_mode == "scheduled"`` 只管定时触发，不影响这里：
    交互式许可已获批，不能因为定时生产通道关闭就一起被挡在门外。
    """
    from app.device_runtime import RuntimeUnavailable

    global device_runtime
    runtime = device_runtime
    if runtime is None:
        try:
            runtime, _ = _build_device_components(repository=production_repo)
        except HostBudgetUncalibrated as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (RuntimeUnavailable, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"整机制作许可尚未就绪：{exc}") from exc
        device_runtime = runtime
    if runtime.last_error is not None or "creation_job" not in runtime.registrations:
        raise HTTPException(status_code=503, detail="整机制作许可已停止，须核查许可后才能继续")
    if not runtime.running:
        try:
            recover_interrupted_quality_checks(runtime)
        except RecoveryBlocked as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        runtime.start()
    return runtime


def _request_member(request: Request) -> LocalMember | None:
    """本次请求的可信本机成员。

    令牌无效是凭据失败（401），不降级成「没带令牌所以按 owner 看全部」；已开
    认证又没有令牌时没有可核验身份，返回 ``None``，不凭空造一个成员。
    """
    try:
        return resolve_member_token(
            members=local_members_service,
            token=_local_member_token(request),
            auth_enabled=auth_context.enabled,
        )
    except LocalMembersError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _request_permit_actor(request: Request, workspace_id: str) -> CreationExecutionActor | None:
    active = TASK_MEDIA_CONTEXT.get()
    if active is not None and active[1].workspace_id == workspace_id:
        return active[3]
    """Interactive identity comes from a verified JWT or the local roster."""
    if auth_context.enabled:
        principal = getattr(request.state, "principal", None)
        if (principal is None or not principal.authenticated
                or principal.workspace_id != workspace_id or workspace_id == LEGACY_WORKSPACE_ID
                or request.headers.get("X-Local-Member-Token")):
            raise HTTPException(status_code=403, detail="账号设备许可身份与工作区不匹配")
        try:
            device = trusted_device_credential()
            return CreationExecutionActor.account(workspace_id, device.device_id, principal.user_id)
        except ValueError:
            raise HTTPException(status_code=403, detail="账号设备许可身份无效") from None
    if workspace_id != LEGACY_WORKSPACE_ID:
        raise HTTPException(status_code=403, detail="本机制作身份与工作区不匹配")
    return permit_actor(_request_member(request), workspace_id)


def _verify_creation_identity(actor: CreationExecutionActor) -> str:
    if actor.device_id is not None:
        client = app.state.creation_permit_client
        if client is None:
            return "unknown"
        try:
            service = DeviceAccountService(settings, trusted_device_credential(), creation_permit_client=client)
        except (DeviceAccountError, HTTPException):
            return "unknown"
        return service.creation_permit(actor.device_id, actor.user_id, actor.workspace_id)
    if auth_context.enabled or actor.workspace_id != LEGACY_WORKSPACE_ID:
        return "unauthorized"
    return "ready" if find_member(local_members_service, actor.member_id) is not None else "unauthorized"


def _require_permit_actor(request: Request, workspace_id: str) -> CreationExecutionActor:
    """Verify permission before writing any JobRun, session or queue intent."""
    actor = _request_permit_actor(request, workspace_id)
    if actor is None:
        raise HTTPException(status_code=503, detail="多成员制作许可尚未接通持续身份核验")
    identity = _verify_creation_identity(actor)
    if identity == "unknown":
        raise HTTPException(status_code=503, detail="设备成员许可暂不可核验，请恢复连接后重试")
    if identity != "ready":
        raise HTTPException(status_code=403, detail="设备成员或工作区制作许可已失效")
    return actor




def _creation_execution_status(workspace_id: str, task_id: str) -> dict[str, object]:
    try:
        return _creation_execution_control().status(workspace_id, task_id, verify_media=device_runtime is None)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/api/creation-tasks/{task_id}/execution/preview")
def preview_creation_execution(task_id: str, request: Request) -> dict[str, object]:
    _readable_creation_task(request, task_id)
    try:
        return _creation_execution_control().preview(current_workspace_id(request), task_id)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/api/creation-tasks/{task_id}/execution")
def get_creation_execution(task_id: str, request: Request, job_id: str | None = None) -> dict[str, object]:
    _readable_creation_task(request, task_id)
    if job_id is not None:
        try:
            return _read_bound_execution_result(task_id, request, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="原制作作业不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _creation_execution_status(current_workspace_id(request), task_id)


@app.get(
    "/api/creation-tasks/{task_id}/job-runs",
    response_model=TaskJobProgressResponse,
)
def get_task_job_progress(task_id: str, request: Request) -> TaskJobProgressResponse:
    """任务作业进度：只读后台已经落库的作业行。

    进度、已耗时、更新时间和失败原因都来自这些记录；重开应用读到的是同一批
    作业，页面关闭不改变它们。等待与处理估时分开，没有可信样本就是未知。
    """
    workspace_id = current_workspace_id(request)
    _readable_creation_task(request, task_id)
    try:
        runs = production_repo.list_job_runs(workspace_id, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc
    items, current = summarize_job_progress(runs, now=datetime.now(UTC))
    return TaskJobProgressResponse(
        task_id=task_id,
        current=JobProgressResponse.model_validate(current, from_attributes=True)
        if current
        else None,
        items=[JobProgressResponse.model_validate(item, from_attributes=True) for item in items],
        total_count=len(items),
    )


@app.post("/api/creation-tasks/{task_id}/execution/start", status_code=202)
def start_creation_execution(task_id: str, request: Request, payload: ExecutionStartRequest,
                             assistant_turn_id: UUID | None = None) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _require_automation_task_open(request,workspace_id,task_id)
    if not payload.requirements_confirmed:
        raise HTTPException(status_code=409, detail="请先确认实际文案与本次制作要求")
    source, replay = _assistant_execution_source(task_id, request, assistant_turn_id, "start", payload.input_digest)
    if replay is not None:
        return replay
    runtime = _creation_runtime()
    actor = _require_permit_actor(request, workspace_id)
    try:
        result = _creation_execution_control().start(workspace_id, task_id, payload.script_id, payload.input_digest, actor, runtime, verify_media=device_runtime is None)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _bind_assistant_execution_result(task_id, request, str(assistant_turn_id), result, source=source) if source else result


@app.post("/api/creation-tasks/{task_id}/execution/resume", status_code=202)
def resume_creation_execution(task_id: str, request: Request, assistant_turn_id: UUID | None = None) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _require_automation_task_open(request,workspace_id,task_id)
    _creation_task_or_404(workspace_id, task_id)
    source, replay = _assistant_execution_source(task_id, request, assistant_turn_id, "resume")
    if replay is not None:
        return replay
    runtime = _creation_runtime()
    actor = _require_permit_actor(request, workspace_id)
    try:
        result = _creation_execution_control().resume(workspace_id, task_id, actor, runtime, verify_media=device_runtime is None)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _bind_assistant_execution_result(task_id, request, str(assistant_turn_id), result, source=source) if source else result


@app.post("/api/creation-tasks/{task_id}/execution/cancel")
def cancel_creation_execution(task_id: str, request: Request) -> dict[str, object]:
    workspace_id = current_workspace_id(request)
    _creation_task_or_404(workspace_id, task_id)
    # 取消不要求整机运行时在场：没有许可通道时也必须能把已经发起的作业停下来。
    runtime = device_runtime
    actor = _request_permit_actor(request, workspace_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="缺少可核查的原制作身份，不能取消")
    try:
        return _creation_execution_control().cancel(workspace_id, task_id, actor, runtime, verify_media=device_runtime is None)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _creation_job_for_request(workspace_id: str, request_id: str):
    return _creation_execution_control().job_for_request(workspace_id, request_id)


@app.post("/api/device/session/heartbeat")
def report_device_session(payload: DeviceHeartbeatRequest, request: Request) -> dict[str, object]:
    """上报一次真实会话活动。

    身份与工作区都由服务端从本机名册判定，客户端自报的字段里没有这两项；
    成员被移除后，下一次心跳就会因为名册里查不到而失败，旧会话不能续用。
    """
    workspace_id = current_workspace_id(request)
    actor = _require_permit_actor(request, workspace_id)
    runtime = _creation_runtime()
    try:
        runtime.scheduler.heartbeat_session(
            SessionHeartbeat(
                session_id=payload.session_id, workspace_id=workspace_id,
                playing=payload.playing, recording=payload.recording,
                editing=payload.editing, interaction=payload.interaction,
            ),
            actor,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"session_id": payload.session_id, "workspace_id": workspace_id,
            "member_id": actor.member_id}


@app.get("/api/device/queue")
def read_device_queue(request: Request) -> dict[str, object]:
    """本机整机队列：自己的执行单元加上匿名的忙闲汇总。"""
    workspace_id = current_workspace_id(request)
    actor = _require_permit_actor(request, workspace_id)
    runtime = _creation_runtime()
    try:
        return runtime.scheduler.list_queue_for(actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/device/queue/{request_id}/cancel")
def cancel_device_request(request_id: str, request: Request) -> dict[str, object]:
    """释放一条执行单元的整机许可，并把背后的制作作业一并停下。

    权限由调度器按作业归属判定：不是自己的单元就拒绝，取消方向不要求
    成员仍在名册内——撤权必须总是可行。
    """
    workspace_id = current_workspace_id(request)
    actor = _request_permit_actor(request, workspace_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="缺少可核查的原制作身份，不能取消")
    runtime = _creation_runtime()
    try:
        detail = runtime.scheduler.cancel(request_id, actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="该执行单元不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    quality = runtime.registrations.get(QUALITY_KIND)
    if quality is not None:
        quality.executor.request_cancel(request_id, actor=actor)
    registration = runtime.registrations.get("creation_job")
    executor = getattr(registration, "executor", None)
    if isinstance(executor, CreationJobUnitExecutor):
        job = _creation_job_for_request(workspace_id, request_id)
        if job is not None:
            try:
                if job.kind == COVER_JOB_KIND:
                    if detail["state"] == "cancelled" and job.status in {"queued", "interrupted"}:
                        production_repo.finish_job_run(workspace_id, job.id, status="cancelled",
                            failure_reason="user_cancelled", impact="当前时间线保持不变。",
                            repair_action="查询原封面请求后重新发起。")
                elif job.kind in {PERSON_EDIT_KIND, NARRATION_EDIT_KIND, TASK_MEDIA_KIND}:
                    if job.status in {"queued", "interrupted", "running"}:
                        production_repo.finish_job_run(workspace_id, job.id, status="cancelled",
                            failure_reason="user_cancelled", impact="目标片段和当前版本保持不变。",
                            repair_action="需要时重新预览该片段修改。")
                else:
                    creation_execution.cancel(workspace_id, job.task_id, member_id=actor.member_id)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        executor.request_cancel(request_id)
    return detail


creation_execution: CreationExecution
execution_worker: ThreadPoolExecutor | None = None
material_ingestion = None
material_execution_mode: Literal["legacy", "scheduled"] = "legacy"
creation_execution_mode: Literal["legacy", "scheduled"] = "legacy"
device_runtime = None


#: 整机锁文件名。锁放在生产库同目录：换一台机、换一个库，就是另一把锁。


class RenderStageRejected(RuntimeError):
    """成片阶段被业务规则拒绝。转成可读原因，不把 HTTP 语义带进后台线程。"""


def _render_creation_stage_with_grant(workspace_id: str, task_id: str, grant: CpuRenderGrantContext):
    try:
        plan = _render_stage_use_case().render(workspace_id, task_id, render_context=grant)
        _quality_inspection_use_case().start_for_render(device_runtime, workspace_id, task_id, grant.grant.owner_id)
        return plan
    except (RenderRejected, HTTPException) as exc:
        raise RenderStageRejected(str(exc.detail)) from exc




def _presenter_model_ids():
    return {quality: _lipsync_provider(quality).model_id for quality in ("fast", "high")}





def _creation_stage_valid_with_grant(workspace_id: str, task_id: str, grant: CpuRenderGrantContext) -> bool:
    try:
        return _render_stage_use_case().valid_with_grant(workspace_id, task_id, grant)
    except RenderRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc




def _build_device_components(*, repository: ProductionRepository):
    """装出这台机器唯一的整机运行时：一把锁、一张队列、一个驱动线程。

    ``creation_job`` 始终在这里注册——交互式许可已经获批，不能因为定时素材
    通道关闭就一起消失。``material_asset`` 只在它自己的定时模式确实是 scheduled
    时才注册，避免造出第二条永远等不到许可的队列。探针和预算都是真实来源：
    没标定就抛 ``HostBudgetUncalibrated``，由调用方明确报不可用。
    """
    from app.device_runtime import OwnerRegistration, build_device_runtime
    from app.material_ingestion import (
        MaterialHashOwnerVerifier, MaterialHashUnitExecutor, MaterialIngestionService,
    )
    from app.member_permits import memory_policy_from_env, material_visual_query_budget_from_env

    cpu_render_budget = (cpu_render_budget_from_env() if any(
        value.strip() for name, value in os.environ.items() if name.startswith("DEVICE_CPU_RENDER_")) else None)
    budget = host_budget_from_env(cpu_render=cpu_render_budget,
        memory_policy=memory_policy_from_env(), material_visual_query=material_visual_query_budget_from_env())
    narration_budget, montage_budget = creation_stage_budgets_from_env()
    presenter_budget, presenter_cpu_render_budget = presenter_budgets_from_env()
    budget = replace(budget, narration=narration_budget, montage=montage_budget,
                     presenter=presenter_budget, presenter_cpu_render=presenter_cpu_render_budget)
    audio_budget = cpu_audio_budget_from_env()
    from app.member_permits import media_script_budget_from_env
    budget = replace(budget, cpu_audio=audio_budget, clip_preview=clip_preview_budget_from_env(), media_script=media_script_budget_from_env(),
                     observation=observation_budget_from_env())
    quality = _quality_inspection_use_case(repository)
    cover = _cover_use_case(repository)
    budget = replace(budget, quality=quality.budget, cover=cover.budget)
    task_media = TaskMediaJobs(repository, facts=_task_media_facts,
        verify_identity=_verify_creation_identity, on_settled=_wake_assistant_continuations,
        audio_ingress=_audio_ingress_use_case(repository),
        audio_edit=_audio_edit_use_case(repository),
        output_observation=_output_observation_use_case(repository),
        media_script=_media_script_use_case(repository),
        visual_preview=_visual_preview_use_case(repository),
        compound_selection=_compound_edit_use_case(repository),
        exact_preview=_exact_preview_use_case(repository),
        command_preview=_command_preview_use_case(repository),
        clip_preview=ClipPreviewExecutor(_clip_preview_use_case(repository),
            TaskAudio(repository, artifact_store, settings.effective_temporary_root_path)))
    registrations = {
        QUALITY_KIND: quality_check_registration(quality),
        "creation_job": creation_job_registration(
            repository,
            task_media=task_media,
            cover=cover,
            person_stage=_person_stage_use_case(repository),
            montage_stage=_montage_stage_use_case(repository),
            narration_stage=_narration_stage_use_case(repository,narration_budget=narration_budget),
            graphic_source_facts=lambda workspace_id, task_id, reference, *, actor: graphic_source_facts(
                repository, artifact_store, workspace_id, task_id, reference, actor=actor),
            audio_mix_facts=lambda workspace_id, task_id, montage, *, actor: _task_audio().mix_facts(
                workspace_id, task_id, montage.audio_timeline, montage.duration_seconds, actor=actor,
                normalize_voice_sources=montage.narration_linkage_created_audio_timeline, budget=audio_budget),
            render_action=_render_creation_stage_with_grant,
            render_is_valid=_creation_stage_valid_with_grant,
            cpu_render_budget=cpu_render_budget,
            verify_identity=_verify_creation_identity,
            narration_budget=narration_budget, montage_budget=montage_budget,
            presenter_budget=presenter_budget, presenter_cpu_render_budget=presenter_cpu_render_budget,
            presenter_models=_presenter_model_ids(), resolve_presenter_output=_person_render_media_path,
        ),
    }
    from app.automation_runtime import restrict_automation_registration
    registrations["creation_job"] = restrict_automation_registration(registrations["creation_job"])
    from app.profile_preview_execution import profile_preview_registration
    registrations["media_preview"] = profile_preview_registration(repository,
        voice_service=voice_preview_service, avatar_service=avatar_preview_service,
        narration_budget=narration_budget, presenter_budget=presenter_budget,
        verify_identity=_verify_creation_identity)
    registrations["platform_publish"] = platform_publish_registration(
        repository, platform_auth_service,
        publishable_output_for=_platform_publish_output_for,
        verify_identity=_verify_creation_identity,
        actor_from_event=lambda identity: CreationExecutionActor.from_event(
            identity["workspace_id"], identity
        ),
    )
    registrations["platform_publish"] = restrict_automation_registration(registrations["platform_publish"],publishing=True)
    material_service = None
    material_executor = None
    if material_execution_mode == "scheduled":
        material_executor = MaterialHashUnitExecutor()
        registrations["material_asset"] = OwnerRegistration(
            MaterialHashOwnerVerifier(repository), material_executor,
        )
    runtime, material_service = bind_device_runtime(
        repository=repository, settings=settings, members=local_members_service,
        registrations=registrations, budget=budget, material_executor=material_executor,
        verify_identity=_verify_creation_identity, record_admission=task_media.record_admission,
    )
    return runtime, material_service


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
        # 定时素材已经在整机运行时那条队列上装配好了；这里不能再并行起一条
        # 旧素材线程去抢同一把锁。
        if material_ingestion is None:
            raise RuntimeError("定时素材尚未接入整机许可运行时，不能启动旧素材线程。")
        return
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


def _start_device_runtime(*, start: bool = True) -> bool:
    """启动这一个整机驱动线程；预算没标定时明确说明，不静默降级。

    启动失败不影响其它路由——整机许可是可选能力，交互式制作相关路由会自己
    返回 503 并写清缺什么，而不是让整个服务起不来。
    """
    global device_runtime, material_ingestion
    runtime, material_service = device_runtime, None
    if runtime is not None and runtime.running:
        return True
    if runtime is None:
        try:
            runtime, material_service = _build_device_components(repository=production_repo)
        except HostBudgetUncalibrated as exc:
            logger.warning("整机许可预算尚未标定，交互式制作许可不可用：%s", exc)
            return False
        except Exception as exc:
            logger.warning("整机许可运行时未能启动，交互式制作许可不可用：%s", exc)
            return False
        device_runtime = runtime
        if material_service is not None:
            material_ingestion = material_service
    try:
        recover_interrupted_quality_checks(runtime)
    except RecoveryBlocked as exc:
        logger.warning("设备释放核验尚未完成，原检查与许可保持：%s", exc)
        return False
    if material_service is not None:
        if start:
            material_service.start()
    if start:
        runtime.start()
    return True


def _stop_device_runtime() -> None:
    global device_runtime
    runtime = device_runtime
    if runtime is not None:
        quality = runtime.registrations.get(QUALITY_KIND)
        if quality is not None:
            quality.executor.request_stop()
        if not runtime.stop(timeout_seconds=30.0):
            raise RuntimeError("整机运行单元尚未退出，原许可与运行绑定保持。")
        device_runtime = None


# Automation only scans due local facts; media work remains with DeviceRuntime.

automation_runtime: AutomationRuntime | None = None


def _automation_runtime() -> AutomationRuntime:
    global automation_runtime
    if automation_runtime is None:
        automation_runtime = AutomationRuntime(
            production_repo,source_repository,verify_identity=_verify_creation_identity,
            list_templates=lambda actor:[*_builtin_video_templates(actor.workspace_id),
                *production_repo.list_video_templates(actor.workspace_id,
                    viewer_user_id=actor.user_id or actor.member_id,local_diagnostic=False)],
            preflight=_creation_plan_preflight,preview_response=_creation_plan_preview_response,
            plan_response=_creation_plan_response,execution_status=_creation_execution_status,
            sync_connector=_sync_connector,inspect_topic_route=_topic_route_snapshot,
            execution_available=lambda:device_runtime is not None,
        )
    return automation_runtime


def _require_automation_task_open(request:Request, workspace_id:str, task_id:str) -> None:
    from app.automation_routes import guard_automation_task
    guard_automation_task(_automation_runtime(),workspace_id,task_id,
        lambda:_require_permit_actor(request,workspace_id))


app.include_router(build_automation_router(_automation_runtime,
    actor_for=lambda request:_require_permit_actor(request,current_workspace_id(request))))


@app.on_event("startup")
def start_automation_due_driver() -> None:
    _automation_runtime().start()


@app.on_event("shutdown")
def stop_automation_due_driver() -> None:
    global automation_runtime
    if automation_runtime is not None:
        automation_runtime.stop()
        automation_runtime = None


@app.on_event("startup")
def recover_interrupted_assistant_turns() -> None:
    conversation_repo.initialize()
    conversation_repo.fail_interrupted_turns()


assistant_continuation_driver = None
_assistant_goal_transports = {}


def _remember_goal_transport(request, workspace_id, member_id):
    """Only retain an already verified interactive credential in host memory."""
    principal = getattr(request.state, "principal", None)
    if not auth_context.enabled or not isinstance(principal, AuthPrincipal) or principal.user_id != member_id or principal.workspace_id != workspace_id:
        return
    authorization = request.headers.get("authorization")
    if not authorization and principal.access_token:
        authorization = "Bearer " + principal.access_token
    if authorization:
        _assistant_goal_transports[(workspace_id, member_id)] = {
            "authorization": authorization, "cookie": request.headers.get("cookie"), "blocked": False}


def _goal_request(goal):
    """Reauthenticate; a device permit alone never authorizes content reads."""
    workspace_id, member_id = goal["workspace_id"], goal["member_id"]
    headers, state = [], {}
    if auth_context.enabled:
        saved = _assistant_goal_transports.get((workspace_id, member_id))
        if saved is None or saved["blocked"]:
            return None
        scheme, _, token = saved["authorization"].partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        try:
            claims = auth_context.verify_token(token)
            if claims["sub"] != member_id or auth_context.resolve_workspace_id(member_id, token) != workspace_id:
                saved["blocked"] = True
                return None
        except (UnauthorizedError, ForbiddenError):
            saved["blocked"] = True
            return None
        state["principal"] = AuthPrincipal(member_id, workspace_id, access_token=token)
        headers.append((b"authorization", saved["authorization"].encode()))
        if saved["cookie"]:
            headers.append((b"cookie", saved["cookie"].encode()))
    else:
        if workspace_id != LEGACY_WORKSPACE_ID:
            return None
        # Read the original member's existing credential; never seed an owner,
        # issue a replacement credential or persist a token in conversation data.
        import sqlite3
        with sqlite3.connect(local_members_service._database_path) as conn:
            row = conn.execute("SELECT token FROM local_members WHERE member_id=?", (member_id,)).fetchone()
        if row is None or not row[0]:
            return None
        try:
            member = local_members_service.resolve_member(row[0])
        except LocalMembersError:
            return None
        if member is None or member.member_id != member_id:
            return None
        headers.append((b"x-local-member-token", str(row[0]).encode()))
    return Request({"type": "http", "method": "GET", "path": f"/api/creation-tasks/{goal['task_id']}",
                    "headers": headers, "query_string": b"", "state": state})


def _goal_script_source_slot(goal, job_id, actor):
    return _goal_projection().script_source_slot(goal, job_id, actor, artifact_store, _output_observation_use_case())


def _goal_completed_sources(goal, actor):
    return _goal_projection().completed_sources(goal, actor, artifact_store, _output_observation_use_case())


def _goal_pending_preview(goal, timeline_version_id):
    return _goal_projection().pending_preview(goal, timeline_version_id)


def _goal_selection_context(goal, task, version, request, removed_refs):
    """Rebuild only derived continuation references over this goal's proved chain."""
    original = goal["request_context"]["target_snapshot"].get("selected_references", [])
    timeline_kinds = {"shot", "narration", "audio", "subtitle", "effect"}
    if not any(item["kind"] in timeline_kinds for item in original):
        return {}, None
    chain = _goal_projection().selection_version_chain(goal, version)
    if chain is None:
        raise AssistantReferenceConflict("接续引用没有同一输入的已核验版本链")
    prefixes = {"shot": "clip", "narration": "narration", "audio": "audio", "subtitle": "cue", "effect": "overlay"}
    items = []
    for item in original:
        if item["kind"] in timeline_kinds:
            if f"{prefixes[item['kind']]}:{item['object_id']}" in removed_refs:
                continue
            if item["task_id"] != task.id or item["version"] not in chain:
                raise AssistantReferenceConflict("接续引用不属于原目标的版本链")
            item = {**item, "version": version.id}
        items.append(item)
    # Materialize current subtitle identities through their existing owner;
    # derive track refs from the same selected cue, never a name/language guess.
    tracks = _current_subtitle_tracks(task.workspace_id, task, current_version=version) if any(item["kind"] == "subtitle" for item in items) else []
    items = [validate_timeline_reference(item, task=task, timeline=version, subtitle_tracks=tracks)
             if item["kind"] in timeline_kinds else _validate_saved_assistant_reference(request, item) for item in items]
    reference = normalize_selection({"kind": "selection", "items": items}) if items else None
    items = reference["items"] if reference else []
    refs = timeline_object_refs(items, task.id, subtitle_tracks=tracks)
    return {"selected_references": items}, {"selected_reference": reference, "selected_object_refs": refs}


def _verify_current_goal(goal, event):
    owner = _goal_projection()
    request = _goal_request(goal)
    if request is None:
        return owner.unavailable_fact("identity")
    workspace_id, task_id = goal["workspace_id"], goal["task_id"]
    try:
        task, member_id = _exact_edit_access(task_id, request, write=True)
        actor = _require_permit_actor(request, workspace_id)
        version = production_repo.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    except (KeyError, HTTPException, PermissionError):
        return owner.unavailable_fact("access")
    decision = owner.verify_version(goal, event, task)
    if decision is not None:
        return decision
    if event.get("phase") == "script_adoption":
        if task.confirmed_script_id != event["script_id"]:
            return {"status": "invalidated", "reason": "adopted_script_changed"}
        script = production_repo.get_creation_task_script(workspace_id, event["script_id"])
        if script.updated_at.isoformat() != event["script_version"]:
            return {"status": "invalidated", "reason": "adopted_script_changed"}
    try:
        completed_sources = _goal_completed_sources(goal, actor)
    except (KeyError, ValueError, PermissionError, OSError):
        return owner.unavailable_fact("completed_sources")
    pending = _goal_pending_preview(goal, task.current_timeline_version_id)
    if pending is not None:
        return {**pending, "completed_source_slots": completed_sources}
    facts = {"business_event": event, "task": {"id": task.id, "stage": task.stage, "output_ready": task.output_ready},
        "completed_source_slots": completed_sources}
    no_side_effect = False
    removed_refs = owner.removed_references(goal)
    if event.get("phase") == "request":
        decision, located = owner.request_locator(event)
        if decision is not None:
            return decision
        actual = _goal_request_result(task_id, UUID(located["request_id"]), request, located["phase"])
        facts["original_request"] = actual
        decision, no_side_effect = owner.request_verification(goal, event, actual)
        if decision is not None:
            return decision
    if event.get("phase") == "execution":
        try:
            facts["execution_status"] = _read_bound_execution_result(task_id, request, event["job_id"])
        except (KeyError, ValueError, HTTPException):
            return owner.unavailable_fact("execution")
        decision = owner.execution_verification(facts["execution_status"])
        if decision is not None:
            return decision
    elif owner.needs_media_receipt(event):
        try:
            actual_job = _goal_media_result(task_id, event["job_id"], request)
        except HTTPException:
            return owner.unavailable_fact("media")
        decision = owner.media_verification(actual_job)
        if decision is not None:
            return decision
        facts["media_job"] = actual_job
    decision, adopted, removed_refs = owner.adopted_operation(goal, event, task, removed_refs)
    if decision is not None:
        return decision
    if adopted is not None:
        facts["adopted_operation"] = adopted
    deferred = owner.deferred_edit_execution(goal, event)
    if deferred is not None:
        return deferred
    facts["observations"] = _task_observation_facts(task_id, request)
    context = goal["request_context"]
    for key in ("profile_reference", "knowledge_reference"):
        reference = context.get(key)
        if reference:
            try:
                facts[key] = (_assistant_profile_snapshot(request, reference["kind"], reference["profile_id"], reference["version"])
                    if key == "profile_reference" else _assistant_knowledge_snapshot(request, reference["knowledge_item_id"],
                        reference["version"], reference.get("locator")))
            except HTTPException:
                return owner.unavailable_fact("reference")
    checkpoint = _goal_output_checkpoint(goal, event, facts, task, version, actor, request)
    try:
        reference_snapshot, selection_context = _goal_selection_context(goal, task, version, request, removed_refs)
    except (AssistantReferenceConflict, HTTPException):
        return owner.unavailable_fact("reference")
    fresh = owner.ready(task, version, actor, facts, no_side_effect, completed_sources, removed_refs, checkpoint)
    fresh["task_version"].update(reference_snapshot)
    return {**fresh, "selection_context": selection_context, "request": request}


def _goal_output_checkpoint(goal, event, facts, task, version, actor, request):
    owner = _goal_projection()
    observation = owner.output_observation(goal, event, facts, task, version)
    if observation is None:
        return None
    try:
        _path, output, _binding = _registered_observation_output(task.workspace_id, task.id,
            CreationTaskObservationTarget(kind="final"), actor)
    except (KeyError, OSError, ValueError, PermissionError, HTTPException):
        return None
    sources = owner.output_sources(goal, task, version, observation, output)
    if sources is None:
        return None
    for row in sources["unknown_requests"]:
        located = owner.unknown_request_for_goal(goal, row)
        if located is not None:
            original = _goal_request_result(task.id, UUID(located["request_id"]), request, located["phase"])
            if owner.request_still_unknown(original):
                return None
    source = owner.output_execution_source(goal, version, sources)
    if source is None:
        return None
    actual = _read_bound_execution_result(task.id, request, source["job_id"])
    checkpoint = owner.completed_output(observation, output, version, source, actual)
    if checkpoint is not None:
        checkpoint["unavailable"] = [{"scope": item["scope"], "reason": _assistant_stream_safe_text(item["reason"], task.workspace_id)}
            for item in checkpoint["unavailable"]]
    return checkpoint


def _run_goal_turn(claim, fresh):
    goal, context, request = claim["goal"], claim["request_context"], fresh["request"]
    task_id, workspace_id, turn_id = goal["task_id"], goal["workspace_id"], claim["turn_id"]
    task = _creation_task_or_404(workspace_id, task_id)
    # A verified adoption may legitimately move review back to montage. Keep
    # the original selection and enforce the same current object validator.
    stage = context["stage"]
    if stage in {"topic", "script"} and task.confirmed_script_id:
        from app.creation.intent_use_case import CreationIntentUseCase
        adopted_script = production_repo.get_creation_task_script(workspace_id, task.confirmed_script_id)
        adopted_plan = _creation_plan_for_task_or_404(workspace_id, task)
        if CreationIntentUseCase.adopted_for_execution(adopted_plan, adopted_script):
            stage = task.stage
    if (claim["event"].get("status") == "confirmed" and "adopted_operation" in fresh["facts"]
            or stage == "review" and task.stage == "montage"):
        stage = task.stage
    payload = CreationTaskAssistantTurnRequest(conversation_id=goal["conversation_id"], message=goal["original_instruction"],
        client_request_id=turn_id, expected_draft_version=0, context_stage=context["stage"],
        selected_object_refs=[ref for ref in context.get("selected_object_refs", [])
            if ref not in fresh["task_version"].get("removed_selected_refs", [])], playhead_ms=context.get("playhead_ms"))
    payload.context_stage = stage
    stage, refs = _assistant_turn_scope(workspace_id, task, payload)
    material_selection = _assistant_task_clip_material_selection(request, task, refs)
    register_turn_control(turn_id)
    if assistant_continuation_driver is not None and assistant_continuation_driver.stop_event.is_set():
        conversation_repo.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})
        forget_turn_control(turn_id)
        return
    sanitizer = _AssistantStreamTextSanitizer(workspace_id)
    def on_event(event):
        if event.get("type") == "text_delta":
            delta = sanitizer.feed(str(event.get("delta", "")))
            if delta:
                conversation_repo.append_event(turn_id, {"type": "text_delta", "delta": delta})
        elif event.get("type") in {"tool_start", "tool_end"}:
            safe = {key: value for key, value in event.items() if key in {"type", "tool_id", "label", "ok", "summary"}}
            for key in ("label", "summary"):
                if key in safe:
                    safe[key] = _assistant_stream_safe_text(str(safe[key]), workspace_id)
            if isinstance(event.get("failure"), dict):
                safe["failure"] = {key: _assistant_stream_safe_text(value[:500], workspace_id)
                    for key, value in event["failure"].items() if key in {"reason", "repair_action"} and isinstance(value, str)}
            conversation_repo.append_event(turn_id, safe)
    try:
        reply, preview = run_task_turn(_workspace_settings_for(workspace_id), workspace_id=workspace_id,
            local_member_token=_local_member_token(request) or None, caller_user_id=goal["member_id"],
            task_id=task_id, conversation_id=goal["conversation_id"], message=goal["original_instruction"], stage=stage,
            selected_refs=refs, playhead_ms=context.get("playhead_ms"), authorization=request.headers.get("authorization"),
            cookie=request.headers.get("cookie"), on_event=on_event, turn_id=turn_id,
            profile_reference=fresh["facts"].get("profile_reference"), knowledge_reference=fresh["facts"].get("knowledge_reference"),
            task_version=fresh["task_version"], material_selection=material_selection,
            selected_references=_frozen_assistant_references(request, goal["conversation_id"], turn_id),
            input_handler=_assistant_input_delivery(request, goal["conversation_id"], turn_id),
            continuation_context={"original_turn_id": goal["original_turn_id"], "business_event": claim["event"],
                                  "source_input_id": goal.get("source_input_id"), "source_input_turn_id": goal.get("source_input_turn_id"),
                                  "current_facts": fresh["facts"], "round_count": goal["round_count"],
                                  "goal_checkpoint": {key: goal[key] for key in ("target_snapshot", "unfinished_steps", "handled_event_ids")},
                                  "completed_source_slots": goal.get("completed_source_slots", []),
                                  "completed_output_checkpoint": goal.get("completed_output_checkpoint"),
                                  "unfinished_steps": goal["unfinished_steps"], "next_checkpoint": goal.get("next_checkpoint")})
        tail = sanitizer.feed("", final=True)
        if tail:
            conversation_repo.append_event(turn_id, {"type": "text_delta", "delta": tail})
        _finish_task_assistant_turn(task_id, request, payload, turn_id, reply, preview, stage)
    except AssistantStopped:
        if assistant_continuation_driver is not None and assistant_continuation_driver.stop_event.is_set():
            conversation_repo.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})
        else:
            conversation_repo.finish_turn(turn_id, "stopped", result={"kind": "task_turn", "response": {"turn_status": "stopped"}})
    finally:
        forget_turn_control(turn_id)


def _goal_projection():
    return GoalResultProjection(production_repo, conversation_repo)


def _goal_request_result(task_id, request_id, request, phase):
    # Transport projection of the original journal, using the same canonical reader.
    task, member_id = _exact_edit_access(task_id, request, write=True)
    try:
        event_id, event = _goal_projection().request_claim(task, member_id, str(request_id), phase)
    except GoalProjectionRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc.__cause__
    inputs = event.payload["inputs"]
    if phase in {"preview", "script", "observation"} or (phase == "apply" and "operation_type" in inputs):
        fields = (CreationTaskExactEditRequest.model_fields if phase in {"preview", "apply"} else
                  CreationTaskObservationRequest.model_fields if phase == "observation" else
                  MediaScriptToolPreviewRequest.model_fields if "tool_id" in inputs else MediaScriptPreviewRequest.model_fields)
        submitted_input = {key: value for key, value in inputs.items() if key in fields}
        if phase == "script" and "tool_id" in inputs:
            submitted_input["tool_id"] = inputs["tool_id"]
    else:
        submitted_input = {key: inputs[key] for key in ("proposal_id", "base_version_id", "input_digest")}
        submitted_input["request"] = {key: value for key, value in inputs["request"].items() if key in CreationTaskEditApplyRequest.model_fields}
    if len(json.dumps(submitted_input, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise HTTPException(status_code=409, detail="原请求字段超过有界查询范围，不能省略字段或换绑新版重放")
    result = _read_edit_request_result(task, event_id)
    return {"request_id": str(request_id), "phase": phase, "input_digest": event.payload["input_digest"],
            "task_id": task_id, "status": "recorded" if result else "unknown", "submitted_input": submitted_input,
            "result_status_code": result.status_code if result else None, "result": json.loads(result.body) if result else None}


def _goal_media_result(task_id, job_id, request):
    actor = _require_permit_actor(request, current_workspace_id(request))
    runtime = _creation_runtime()
    media = runtime.registrations["creation_job"].owner.task_media
    try:
        job, event = media.read(actor.workspace_id, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务媒体作业不存在") from exc
    try:
        _goal_projection().require_media_task(task_id, job)
    except GoalProjectionRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return _task_media_job_response(media, job, actor, runtime)


def _goal_service():
    return GoalContinuationService(conversation_repo, verify_current_goal=_verify_current_goal, run_goal_turn=_run_goal_turn)


def _register_adopted_creation_goals(request, turn_id, creation_request):
    if creation_request is None:
        return
    workspace_id = current_workspace_id(request)
    member_id = _content_reader(request, workspace_id)[0]
    # Never bind the model's returned task IDs. Read the owning repository's
    # actual window/tasks/adoption again through the same member boundary.
    current = _creation_request_response(_creation_request_or_404(request, creation_request.id)).model_dump(mode="json")
    registered = _goal_service().register_creation_request(turn_id, workspace_id, member_id, current)
    if registered:
        _remember_goal_transport(request, workspace_id, member_id)
        _wake_assistant_continuations()


def _read_bound_execution_result(task_id, request, job_id):
    task, _member_id = _exact_edit_access(task_id, request, write=True)
    actor = _require_permit_actor(request, task.workspace_id)
    try:
        result = _creation_execution_control().read_bound(task.workspace_id, task_id, job_id, actor,
            verify_media=device_runtime is None)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return jsonable_encoder(result)


def _assistant_goal_action_proofs(workspace_id, task_id, turn_id, *, media_job, edit_preview, edit_request, execution_status):
    proofs = []

    def append(kind, identity, source):
        if source.get("assistant_turn_id") == turn_id:
            proofs.append({"kind": kind, "id": identity,
                           **({"input_id": source["input_id"]} if source.get("input_id") else {})})

    if media_job:
        start = production_repo.get_task_event(workspace_id, f"event_{media_job.job_id}_start").payload
        append("media_job", media_job.job_id, start["arguments"].get("_typed_source", {}))
    if edit_preview:
        intent = production_repo.get_edit_intent(workspace_id, edit_preview.proposal.intent_id)
        append("edit_preview", edit_preview.proposal.id, intent.constraints.get("request_source", {}))
    if edit_request or execution_status:
        for event in production_repo.list_task_events(workspace_id, task_id):
            if (edit_request and event.event_type == "assistant_edit_request_located"
                    and event.payload["locator"]["request_id"] == str(edit_request.request_id)):
                append("edit_request", str(edit_request.request_id), event.payload["source"])
            if (execution_status and event.event_type == "assistant_execution_located"
                    and event.job_id == execution_status["job"]["id"]):
                append("execution_status", event.job_id, event.payload["source"])
    return proofs


def _assistant_execution_locator_id(turn_id, input_id=None):
    return f"event_assistant_execution_locator_{turn_id}" + (f"_{input_id}" if input_id else "")


def _bind_assistant_execution_observation(task_id, request, turn_id, details, result):
    origin = _assistant_result_binding(task_id, request, turn_id, details)
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    current = conversation_repo.task_turn_source(turn_id, workspace_id, member_id, task_id)
    if (current.get("input_id"), current.get("input_turn_id")) != (origin.get("input_id"), origin.get("input_turn_id")):
        return result  # A late read remains historical; it cannot bind a new goal.
    task = production_repo.get_creation_task(workspace_id, task_id)
    snapshot = origin["context"]["target_snapshot"]
    actor = _require_permit_actor(request, workspace_id)
    source = {"workspace_id": workspace_id, "task_id": task_id, "member_id": member_id,
        "conversation_id": origin["conversation_id"], "assistant_turn_id": turn_id,
        "input_id": origin["input_id"], "input_turn_id": origin["input_turn_id"],
        "expected_timeline_version_id": snapshot["timeline_version_id"],
        "expected_timeline_sequence": snapshot["timeline_sequence"],
        "actor_identity": actor.identity_payload(), "action": "observe"}
    if not _execution_source_version_current(task, source, result["job"]["id"], result["job"]["status"]):
        return result
    return _bind_assistant_execution_result(task_id, request, turn_id, result, source=source)


def _assistant_edited_execution_version(task, member_id, turn_id, origin, snapshot, version, request):
    """Derive one authorized successor from this input's committed edit journal."""
    if (version is None or version.parent_version_id != snapshot.get("timeline_version_id")
            or version.sequence != snapshot.get("timeline_sequence", 0) + 1):
        return None
    operations = [item for item in production_repo.list_edit_operations(task.workspace_id, task.id)
                  if item.status == "applied" and item.result_version_id == version.id
                  and item.base_version_id == snapshot["timeline_version_id"]]
    if len(operations) != 1:
        return None
    operation = operations[0]
    proposal = production_repo.get_edit_proposal(task.workspace_id, operation.proposal_id)
    source = production_repo.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source") or {}
    if any(source.get(key) != value for key, value in {
            "workspace_id": task.workspace_id, "task_id": task.id, "member_id": member_id,
            "conversation_id": origin["conversation_id"], "assistant_turn_id": str(turn_id),
            "input_id": origin.get("input_id"), "input_turn_id": origin.get("input_turn_id"),
            "expected_timeline_version_id": snapshot["timeline_version_id"],
            "expected_timeline_sequence": snapshot["timeline_sequence"]}.items()):
        return None
    request_id = source.get("request_id")
    if not request_id:
        return None
    receipt = get_exact_edit_request_result(task.id, UUID(request_id), request, "apply")
    submitted, result = receipt["submitted_input"], receipt["result"]
    if (receipt["status"] != "recorded" or receipt["result_status_code"] != 200 or not result
            or submitted.get("assistant_turn_id") != str(turn_id)
            or submitted.get("expected_timeline_version_id") != operation.base_version_id
            or submitted.get("expected_timeline_sequence") != snapshot["timeline_sequence"]
            or result.get("operation", {}).get("id") != operation.id
            or result["operation"].get("result_version_id") != version.id):
        return None
    _require_assistant_input_origin(request, source)
    return {**snapshot, "timeline_version_id": version.id, "timeline_sequence": version.sequence}


def _assistant_execution_source(task_id, request, turn_id, action, input_digest=None):
    if turn_id is None:
        return None, None  # Existing explicit GUI/CLI starts keep their protocol.
    task, member_id = _exact_edit_access(task_id, request, write=True)
    try:
        origin = conversation_repo.task_turn_source(str(turn_id), task.workspace_id, member_id, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with conversation_repo._connect() as conn:
        latest = conn.execute("SELECT id FROM assistant_conversation_turns WHERE conversation_id=? "
            "AND user_message!='' ORDER BY sequence DESC LIMIT 1", (origin["conversation_id"],)).fetchone()
    original_turn_id = origin["context"].get("original_turn_id", str(turn_id))
    if latest is None or latest["id"] != original_turn_id:
        raise HTTPException(status_code=409, detail="原助手制作要求已被新的真人要求替代")
    snapshot = origin["context"].get("target_snapshot", {})
    version = production_repo.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    if not origin["raw_command"].strip():
        raise HTTPException(status_code=409, detail="原助手制作要求的阶段或目标版本已失效")
    if origin["context"].get("stage") in {"topic", "script"}:
        from app.creation.intent_use_case import CreationIntentUseCase
        plan = _creation_plan_for_task_or_404(task.workspace_id, task)
        script = (production_repo.get_creation_task_script(task.workspace_id, task.confirmed_script_id)
            if task.confirmed_script_id else None)
        if not CreationIntentUseCase.adopted_for_execution(plan, script):
            raise HTTPException(status_code=409, detail="原回合尚无已授权采用的实际文案版本，不能开始制作。")
        try:
            CreationIntentUseCase.reject_discussion_only_input(origin["raw_command"])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if (snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence")) != (
            task.current_timeline_version_id, version.sequence if version else 0):
        successor = (_assistant_edited_execution_version(task, member_id, turn_id, origin, snapshot, version, request)
                     if action in {"start", "resume"} else None)
        if successor is None:
            raise HTTPException(status_code=409, detail="原助手制作要求的目标版本已失效，且没有同一输入的已提交编辑回执")
        snapshot = successor
    goal = conversation_repo.get_task_goal(str(turn_id))
    if goal is not None and goal.get("source_input_id") == origin.get("input_id") and (goal["status"] in {"invalidated", "no_progress", "limit_reached"}
            or _goal_pending_preview(goal, task.current_timeline_version_id) is not None):
        raise HTTPException(status_code=409, detail="原要求已停止或仍等待真实修改采用，未开始制作")
    actor = _require_permit_actor(request, task.workspace_id)
    source = {"workspace_id": task.workspace_id, "task_id": task_id, "member_id": member_id,
        "assistant_turn_id": str(turn_id), "conversation_id": origin["conversation_id"],
        **{key: origin[key] for key in ("input_id", "input_turn_id") if key in origin},
        "expected_timeline_version_id": snapshot["timeline_version_id"],
        "expected_timeline_sequence": snapshot["timeline_sequence"], "actor_identity": actor.identity_payload(), "action": action}
    _require_assistant_input_origin(request, source)
    try:
        located = production_repo.get_task_event(task.workspace_id, _assistant_execution_locator_id(turn_id, source.get("input_id")))
    except KeyError:
        return source, None
    if (located.payload["source"] != source
            or input_digest is not None and located.payload["input_digest"] != input_digest):
        raise HTTPException(status_code=409, detail="本轮已绑定不同的制作请求；请只查询原作业")
    return source, _read_bound_execution_result(task_id, request, located.payload["job_id"])


def _bind_assistant_execution_result(task_id, request, turn_id, candidate, *, source=None):
    if not isinstance(candidate.get("job"), dict) or not isinstance(candidate["job"].get("id"), str):
        raise HTTPException(status_code=502, detail="制作回执缺少真实作业身份")
    try:
        result = _read_bound_execution_result(task_id, request, candidate["job"]["id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    workspace_id = current_workspace_id(request)
    member_id = _assistant_member_id(request, workspace_id)
    if source is None:
        # A model may finish after consuming another input. Re-read the job's
        # original host binding before consulting the now-current input.
        previous = next((event for event in production_repo.list_task_events(workspace_id, task_id)
            if event.event_type == "assistant_execution_located" and event.job_id == result["job"]["id"]
            and event.payload["source"]["assistant_turn_id"] == turn_id), None)
        if previous is None:
            raise HTTPException(status_code=409, detail="制作结果没有本轮的原始提交绑定，请只查询原作业")
        source = previous.payload["source"]
    event_id = _assistant_execution_locator_id(turn_id, source.get("input_id"))
    try:
        previous = production_repo.get_task_event(workspace_id, event_id)
    except KeyError:
        production_repo.append_task_event(TaskEvent(id=event_id, workspace_id=workspace_id, task_id=task_id,
            job_id=result["job"]["id"], event_type="assistant_execution_located",
            payload={"job_id": result["job"]["id"], "input_digest": result["job"]["input_digest"], "source": source}))
    else:
        if previous.job_id != result["job"]["id"]:
            raise HTTPException(status_code=409, detail="本轮制作来源已绑定原作业，不能改绑")
    task = production_repo.get_creation_task(workspace_id, task_id)
    current = _execution_source_version_current(task, source, result["job"]["id"], result["job"]["status"])
    conversation_repo.record_task_execution(turn_id, workspace_id, member_id, task_id, result, current_source=current, input_id=source.get("input_id"))
    _wake_assistant_continuations()
    return result


def _execution_source_version_current(task, source, job_id, status):
    return _goal_projection().execution_source_current(task, source, job_id, status)


def _project_assistant_execution_results():
    owner = _goal_projection()
    projected = 0
    for row in production_repo.assistant_execution_projection_rows():
        context = owner.execution_candidate(row)
        if context is None:
            continue
        request = _goal_request(context.goal)
        if request is None:
            continue
        try:
            actual = _read_bound_execution_result(row["task_id"], request, context.job_id)
        except (KeyError, ValueError, HTTPException):
            owner.invalidate_execution(context)
            continue
        event = owner.project_execution(context, actual)
        if event is not None:
            _goal_service().observe_business_result(context.source["assistant_turn_id"], event)
            projected += 1
    return projected


def _wake_assistant_continuations():
    if assistant_continuation_driver is not None:
        assistant_continuation_driver.wake.set()


def _mark_assistant_projection(candidate):
    _goal_projection().mark(candidate)


def _assistant_business_turn_can_continue(binding, turn_id):
    return _goal_projection().can_continue(binding, turn_id)


def _project_assistant_business_candidate(candidate):
    if candidate["kind"] == "request":
        return _project_assistant_request_candidate(candidate)
    owner = _goal_projection()
    context = owner.business_candidate(candidate,
        media_available=device_runtime is not None and "creation_job" in device_runtime.registrations)
    if isinstance(context, bool):
        return context
    if not owner.authorized(context, _verify_creation_identity(context.actor)):
        return True
    media = device_runtime.registrations["creation_job"].owner.task_media if context.job is not None else None
    projection = owner.business_projection(context, media=media, outputs=_output_observation_use_case())
    if isinstance(projection, bool):
        return projection
    if projection.preview is not None:
        projection.response_patch["edit_preview"] = _edit_preview_response(*projection.preview).model_dump(mode="json")
    if projection.media is not None:
        from app.schemas import TaskMediaJobResponse
        data = projection.media
        job, start, actor = data["job"], data["start"], data["actor"]
        device_request, _view = _task_media_device_request(job, actor, device_runtime)
        projection.response_patch["media_job"] = TaskMediaJobResponse(job_id=job.id,
            status=data["response_status"], operation=start["operation"], input_digest=job.input_digest, created_at=job.created_at,
            result_kind=start["result_kind"], base_version_id=projection.event["base_version_id"],
            failure_reason=job.failure_reason, result=data["result"],
            resource_wait=media.resource_admission_receipt(job, start, actor, device_request, data["status"]),
            result_url=f"/api/creation-tasks/{candidate['task_id']}/media-jobs/{job.id}").model_dump(mode="json")
    if owner.commit(context, projection):
        if _goal_service().observe_business_result(context.source["assistant_turn_id"], projection.event):
            _wake_assistant_continuations()
        return True
    return False


def _project_assistant_request_candidate(candidate):
    owner = _goal_projection()
    context = owner.request_candidate(candidate)
    if isinstance(context, bool):
        return context
    if not owner.authorized(context, _verify_creation_identity(context.actor)):
        return True
    recorded = _read_edit_request_result(context.task, owner.request_event_id(context))
    receipt = {"status_code": recorded.status_code, "response": json.loads(recorded.body)} if recorded is not None else None
    result = owner.request_result(context, receipt, runtime_available=device_runtime is not None)
    if isinstance(result, bool):
        return result
    patch = {"edit_request": result.locator}
    if result.preview is not None:
        patch["edit_preview"] = _edit_preview_response(*result.preview).model_dump(mode="json")
    elif result.job is not None:
        media = device_runtime.registrations["creation_job"].owner.task_media
        patch["media_job"] = _task_media_job_response(media, result.job, context.actor, device_runtime)
    projection = owner.request_projection(context, result, patch)
    if owner.commit(context, projection):
        if _goal_service().observe_business_result(context.source["assistant_turn_id"], projection.event):
            _wake_assistant_continuations()
        return True
    return False


def _assistant_script_tool_candidates():
    return _goal_projection().script_tool_candidates()


def _project_assistant_script_tool_candidate(candidate):
    owner = _goal_projection()
    context = owner.script_tool_candidate(candidate)
    if isinstance(context, bool):
        return context
    if not owner.authorized(context, _verify_creation_identity(context.actor)):
        return True
    located = candidate["located"]
    tool = _media_script_tools().get_created_for_request(context.actor, UUID(located["request_id"]))
    _check_script_tool_save_result(candidate["workspace_id"], candidate["task_id"], tool, located["submitted_input"])
    receipt = AssistantScriptToolReceipt(action="save", status="recorded", request_id=located["request_id"],
        tool_id=tool["tool_id"], tool_version=tool["version"], query_url=located["query_url"],
        submitted_input=located["submitted_input"], tool=_script_tool_metadata(tool)).model_dump(mode="json")
    projection = owner.script_tool_projection(context, receipt)
    if owner.commit(context, projection):
        if _goal_service().observe_business_result(context.source["assistant_turn_id"], projection.event):
            _wake_assistant_continuations()
        return True
    return False


def _reconcile_assistant_business_results():
    projected = sum(_project_assistant_business_candidate(candidate)
                    for candidate in production_repo.assistant_business_candidates())
    return projected + sum(_project_assistant_script_tool_candidate(candidate)
                           for candidate in _assistant_script_tool_candidates()) + _project_assistant_execution_results()


class AssistantContinuationDriver:
    """Low-load projection of durable business events; never a media executor."""
    def __init__(self):
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self._paused = threading.Event()
        self._tick_lock = threading.Lock()
        self.thread = None

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        with self._tick_lock:
            _reconcile_assistant_business_results()
        self.thread = threading.Thread(target=self._run, name="assistant-business-results", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            self.wake.wait(1)
            self.wake.clear()
            if self.stop_event.is_set():
                break
            try:
                with self._tick_lock:
                    if not self.stop_event.is_set() and not self._paused.is_set():
                        _reconcile_assistant_business_results()
                        _goal_service().reconcile()
            except Exception:
                logger.exception("对话业务结果回填失败；持久来源保留，未重复执行业务动作")

    def pause(self, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("暂停等待时间不能为负数")
        if not self._tick_lock.acquire(timeout=timeout_seconds):
            return False
        try:
            self._paused.set()
        finally:
            self._tick_lock.release()
        return True

    def resume(self) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("对话结果驱动已终止，不能恢复原循环")
        self._paused.clear()
        self.wake.set()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        # The model may be waiting inside this driver's active reconcile call.
        # Signal it before joining; shutdown is not a user's cancellation.
        active_turns = stop_all_assistant_turns()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("对话结果驱动尚未退出")

        # A process shutdown stops model controls, but is not a user decision
        # to invalidate the original goal or replay an in-flight operation.
        for turn_id in active_turns:
            conversation_repo.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})


def start_assistant_continuation_driver():
    global assistant_continuation_driver
    if assistant_continuation_driver is None:
        driver = AssistantContinuationDriver()
        driver.start()
        assistant_continuation_driver = driver


@app.on_event("shutdown")
def stop_assistant_continuation_driver():
    global assistant_continuation_driver
    if assistant_continuation_driver is not None:
        assistant_continuation_driver.stop()
        assistant_continuation_driver = None
    _assistant_goal_transports.clear()


@app.on_event("shutdown")
def stop_active_assistant_turns() -> None:
    for turn_id in stop_all_assistant_turns():
        conversation_repo.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})


@app.on_event("startup")
def resume_persisted_creation_executions() -> None:
    _start_execution_worker()
    try:
        # Register the runtime first, recover all persisted owner jobs while its
        # dispatch loop is still stopped, then allow any queued unit to run.
        ready = _start_device_runtime(start=False)
        if device_runtime is not None and not ready:
            return  # Keep the stopped runtime and original work until release is provable.
        _start_material_ingestion()
        _recover_creation_executions()
        runtime = device_runtime
        if runtime is not None and "platform_publish" in runtime.registrations:
            recover_platform_publish_jobs(
                production_repo, runtime,
                actor_from_event=lambda identity: CreationExecutionActor.from_event(
                    identity["workspace_id"], identity
                ),
                verify_identity=_verify_creation_identity,
            )
        if runtime is not None:
            runtime.start()
    except Exception:
        _stop_device_runtime()
        _stop_material_ingestion()
        _stop_execution_worker()
        raise


@app.on_event("shutdown")
def stop_creation_execution_worker() -> None:
    _stop_device_runtime()
    _stop_material_ingestion()
    _stop_execution_worker()
    platform_auth_service.sessions.close_all()


app.on_event("startup")(start_assistant_continuation_driver)
# Shutdown hooks run in registration order; every transport user stops first.
app.on_event("shutdown")(stop_creation_permit_transport)


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






def create_app() -> FastAPI:
    """Compose this process's application once, before its ASGI lifespan."""
    global model_installer, _bound_runtime_routes, creation_execution
    model_installer = _initialize_runtime_dependencies()
    _bound_runtime_routes = _prepare_runtime_routes(_current_database_owners(), auth_context)
    app.router.routes[_runtime_routes_insert_at:_runtime_routes_insert_at] = _bound_runtime_routes
    app.openapi_schema = None
    creation_execution = _new_creation_execution(production_repo)
    _start_execution_worker()
    return app


# ── Server entry point (for standalone API server) ──────────────────
if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
